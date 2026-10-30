"""blueprints/tenant/rappels.py — Rappels de salaire.

  /rappels                    liste (statut tenu à jour d'après le bulletin de versement)
  /rappels/nouveau            calcul mois par mois puis enregistrement
  /rappels/<id>               détail, annulation
  /rappels/<id>/imprimer      état de rappel signable
"""
from datetime import date, datetime

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from blueprints.tenant import bp
from core import tenant_required, get_tenant
from audit import log_action
from models import db, utcnow, Salarie, Contrat, PeriodePaie, BulletinPaie, RappelSalaire
import rappels_salaire as rs


def _auteur():
    return getattr(current_user, "nom_complet", None) or current_user.email


def _montant(nom):
    brut = (request.form.get(nom) or "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(brut) if brut else None
    except ValueError:
        return None


@bp.route("/rappels")
@login_required
@tenant_required
def rappels():
    t = get_tenant()
    rs.synchroniser_statuts(t.id)
    db.session.commit()
    liste = (RappelSalaire.query.filter_by(tenant_id=t.id)
             .order_by(RappelSalaire.date_creation.desc(), RappelSalaire.id.desc()).limit(300).all())
    return render_template("tenant/rappels.html", rappels=liste, statuts=rs.STATUTS,
                           a_verser=sum(r.total for r in liste if r.statut == "A_VERSER"))


@bp.route("/rappels/nouveau", methods=["GET", "POST"])
@login_required
@tenant_required
def rappel_nouveau():
    if not current_user.can_edit:
        abort(403)
    t = get_tenant()
    salaries = Salarie.query.filter_by(tenant_id=t.id).order_by(Salarie.nom, Salarie.prenom).all()
    periodes = (PeriodePaie.query.filter(PeriodePaie.tenant_id == t.id, PeriodePaie.statut.in_(("OUVERT", "OUVERTE")))
                .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()).all())
    f = request.form if request.method == "POST" else request.args
    s = Salarie.query.filter_by(id=f.get("salarie_id", type=int), tenant_id=t.id).first() if f.get("salarie_id") else None
    contrat = (Contrat.query.filter_by(tenant_id=t.id, salarie_id=s.id, actif=True)
               .order_by(Contrat.date_debut.desc()).first()) if s else None
    ctx = dict(salaries=salaries, periodes=periodes, s=s, contrat=contrat, rubriques=rs.RUBRIQUES,
               obligatoires=rs.OBLIGATOIRES, choisies=rs.rubriques_par_defaut(), apercu=None, brouillons=[],
               valeurs={k: f.get(k, "") for k in ("motif", "mois_effet", "ancien", "nouveau", "periode_id")})
    if request.method == "GET":
        return render_template("tenant/rappel_nouveau.html", **ctx)

    # ── Contrôles de saisie ──────────────────────────────────────────────────
    erreurs = []
    motif = (request.form.get("motif") or "").strip()[:300]
    ancien, nouveau = _montant("ancien"), _montant("nouveau")
    choisies = [k for k in rs.RUBRIQUES if request.form.get(f"rub_{k}") or k in rs.OBLIGATOIRES]
    ctx["choisies"] = choisies
    periode = PeriodePaie.query.filter_by(id=request.form.get("periode_id", type=int), tenant_id=t.id).first()
    try:
        effet = datetime.strptime((request.form.get("mois_effet") or "").strip(), "%Y-%m").date()
    except ValueError:
        effet = None
    if s is None:
        erreurs.append("Choisissez le salarié.")
    if not motif:
        erreurs.append("Indiquez le motif (augmentation, reclassement, accord…).")
    if not ancien or ancien <= 0 or not nouveau or nouveau <= 0:
        erreurs.append("Indiquez l'ancien et le nouveau salaire de base mensuels.")
    elif abs(nouveau - ancien) < 0.5:
        erreurs.append("Le nouveau salaire est égal à l'ancien : aucun rappel.")
    if periode is None or periode.statut not in ("OUVERT", "OUVERTE"):
        erreurs.append("Choisissez une période de versement ouverte.")
    if effet is None:
        erreurs.append("Indiquez le mois à partir duquel le nouveau salaire s'applique.")
    elif periode is not None and (effet.year, effet.month) >= (periode.annee, periode.mois):
        erreurs.append("Le mois d'effet doit précéder la période de versement (sinon ce n'est pas un rappel).")
    if s is not None and periode is not None:
        bv = BulletinPaie.query.filter_by(tenant_id=t.id, salarie_id=s.id, periode_id=periode.id).first()
        if bv is not None and bv.statut in rs.STATUTS_VALIDES:
            erreurs.append(f"Le bulletin de {s.nom_complet} pour {periode.libelle_complet} est déjà validé : "
                           f"choisissez une autre période de versement ou annulez sa validation.")
    if erreurs:
        for e in erreurs:
            flash(e, "error")
        return render_template("tenant/rappel_nouveau.html", **ctx)

    lignes, brouillons = rs.preparer(t.id, s.id, effet, periode, ancien, nouveau, choisies)
    ctx.update(apercu=lignes, brouillons=brouillons, effet=effet, periode=periode)
    if not lignes:
        flash("Aucun bulletin validé entre le mois d'effet et la période de versement : rien à rappeler.", "warning")
        return render_template("tenant/rappel_nouveau.html", **ctx)
    if request.form.get("action") != "enregistrer":
        return render_template("tenant/rappel_nouveau.html", **ctx)

    ajustements = {}
    for b, _, _ in lignes:
        aj = _montant(f"ajustement_{b.id}")
        if aj:
            ajustements[b.id] = aj
    if ajustements and not (request.form.get("justif_ajustement") or "").strip():
        flash("Un ajustement manuel doit être justifié.", "error")
        return render_template("tenant/rappel_nouveau.html", **ctx)
    if ajustements:
        motif = f"{motif} — ajustement : {request.form.get('justif_ajustement').strip()}"[:300]
    r = rs.enregistrer(t.id, s, periode, effet, ancien, nouveau, choisies, motif, _auteur(), ajustements)
    db.session.commit()
    log_action("CREATE", "rappel_salaire", r.id,
               f"Rappel de salaire {s.nom_complet} : {r.total:,.0f} FCFA sur {periode.libelle_complet}".replace(",", " "),
               apres={"ancien": ancien, "nouveau": nouveau, "effet": effet.isoformat(), "mois": len(r.lignes)})
    bv = BulletinPaie.query.filter_by(tenant_id=t.id, salarie_id=s.id, periode_id=periode.id).first()
    flash(f"Rappel enregistré : {r.total:,.0f} FCFA à verser sur {periode.libelle_complet}. ".replace(",", " ")
          + ("Ouvrez le brouillon existant de cette période et enregistrez-le pour l'inclure."
             if bv is not None else "Il sera inclus automatiquement dans le bulletin de cette période."), "success")
    return redirect(url_for("tenant.rappel_detail", rid=r.id))


def _rappel(rid):
    t = get_tenant()
    return t, RappelSalaire.query.filter_by(id=rid, tenant_id=t.id).first_or_404()


@bp.route("/rappels/<int:rid>")
@login_required
@tenant_required
def rappel_detail(rid):
    t, r = _rappel(rid)
    rs.synchroniser_statuts(t.id, salarie_id=r.salarie_id)
    db.session.commit()
    bv = BulletinPaie.query.filter_by(tenant_id=t.id, salarie_id=r.salarie_id, periode_id=r.periode_id).first()
    return render_template("tenant/rappel_detail.html", r=r, statuts=rs.STATUTS, rubriques=rs.RUBRIQUES,
                           detail=rs.detail_ligne, bulletin_versement=bv,
                           a_reenregistrer=bool(bv and bv.statut not in rs.STATUTS_VALIDES and rs.a_reenregistrer(bv)))


@bp.route("/rappels/<int:rid>/annuler", methods=["POST"])
@login_required
@tenant_required
def rappel_annuler(rid):
    if not current_user.can_edit:
        abort(403)
    t, r = _rappel(rid)
    if rs.statut_effectif(r) == "VERSE":
        flash("Ce rappel est versé sur un bulletin validé : annulez d'abord la validation de ce bulletin.", "error")
    elif r.statut == "ANNULE":
        flash("Ce rappel est déjà annulé.", "info")
    else:
        motif = (request.form.get("motif") or "").strip()[:300]
        if not motif:
            flash("Indiquez le motif de l'annulation.", "error")
        else:
            r.statut, r.motif_annulation = "ANNULE", motif
            db.session.commit()
            log_action("CANCEL", "rappel_salaire", r.id, f"Rappel annulé : {motif}")
            bv = BulletinPaie.query.filter_by(tenant_id=t.id, salarie_id=r.salarie_id, periode_id=r.periode_id).first()
            flash("Rappel annulé." + (" Ré-enregistrez le brouillon de la période pour le retirer du bulletin."
                                      if bv is not None and rs.annules_inclus(bv) else ""), "success")
    return redirect(url_for("tenant.rappel_detail", rid=r.id))


@bp.route("/rappels/<int:rid>/imprimer")
@login_required
@tenant_required
def rappel_imprimer(rid):
    t, r = _rappel(rid)
    return render_template("tenant/rappel_print.html", r=r, t=t, rubriques=rs.RUBRIQUES, detail=rs.detail_ligne,
                           statuts=rs.STATUTS, now=utcnow())
