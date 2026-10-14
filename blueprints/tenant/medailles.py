# -*- coding: utf-8 -*-
"""Médaille du travail (gestion de carrière — Phase 2).

Distinction nationale récompensant l'ancienneté (service continu dans la même
entreprise). Seuils PARAMÉTRABLES par entreprise, semés par défaut avec les
valeurs de la Loi N° 021/2010 du 27/07/2010 (art. 11) : bronze 10 ans,
argent 20 ans, or 30 ans.

L'attribution d'une médaille est tracée comme un EvenementCarriere de type
DISTINCTION (le code du palier est stocké dans `nouvelle_valeur`), ce qui la
fait apparaître dans la frise de carrière et évite de ré-alerter une médaille
déjà remise. N'altère jamais la paie.
"""
from datetime import datetime, date

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from blueprints.tenant import bp, _doc_response
from core import get_tenant, _parse_date
from audit import log_action
from models import db, Salarie, EvenementCarriere, PalierMedaille


# ── Helpers partagés (utilisés aussi par le tableau de bord) ──────────────────

def _ensure_paliers_defaut(t):
    """Garantit que le tenant possède ses paliers de médaille. Au premier accès,
    sème les valeurs par défaut (Loi N° 021/2010). Idempotent : ne recrée jamais
    un palier déjà présent (même désactivé)."""
    existants = {p.code for p in PalierMedaille.query.filter_by(tenant_id=t.id).all()}
    if existants:
        return
    for d in PalierMedaille.DEFAUTS:
        db.session.add(PalierMedaille(tenant_id=t.id, code=d["code"], libelle=d["libelle"],
                                      annees_requises=d["annees_requises"], ordre=d["ordre"],
                                      actif=True))
    db.session.commit()


def _anciennete_ans(s, ref=None):
    """Ancienneté en années pleines depuis la date d'embauche (service continu
    dans la même entreprise = depuis date_embauche dans ce tenant)."""
    if not s.date_embauche:
        return 0
    ref = ref or date.today()
    return (ref - s.date_embauche).days // 365


def _medailles_deja_attribuees(t, salarie_ids=None):
    """Ensemble des couples (salarie_id, code_palier) déjà décorés, tracés via
    les EvenementCarriere DISTINCTION (code du palier dans nouvelle_valeur)."""
    q = EvenementCarriere.query.filter_by(tenant_id=t.id, type="DISTINCTION")
    if salarie_ids is not None:
        q = q.filter(EvenementCarriere.salarie_id.in_(salarie_ids))
    out = set()
    for e in q.all():
        code = (e.nouvelle_valeur or "").strip().upper()
        if code:
            out.add((e.salarie_id, code))
    return out


def medailles_eligibles(t, ref=None):
    """Salariés ACTIFS éligibles à une médaille non encore attribuée.

    Retourne une liste de dicts triés par palier décroissant (le plus élevé
    d'abord) : {salarie, palier, anciennete_ans}. Un salarié n'apparaît que
    pour le palier le plus élevé qu'il peut recevoir et qu'il n'a pas déjà.
    """
    paliers = (PalierMedaille.query.filter_by(tenant_id=t.id, actif=True)
               .order_by(PalierMedaille.annees_requises.desc()).all())
    if not paliers:
        return []
    salaries = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").all()
    deja = _medailles_deja_attribuees(t, [s.id for s in salaries])
    out = []
    for s in salaries:
        anc = _anciennete_ans(s, ref)
        # Paliers triés du plus exigeant au moins exigeant : on propose le plus
        # haut non encore reçu que l'ancienneté permet.
        for p in paliers:
            if anc >= p.annees_requises and (s.id, p.code.upper()) not in deja:
                out.append({"salarie": s, "palier": p, "anciennete_ans": anc})
                break
    out.sort(key=lambda x: (-x["palier"].annees_requises, x["salarie"].nom or ""))
    return out


# ── Pages ─────────────────────────────────────────────────────────────────────

@bp.route("/medailles")
@login_required
def medailles():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    _ensure_paliers_defaut(t)
    paliers = (PalierMedaille.query.filter_by(tenant_id=t.id)
               .order_by(PalierMedaille.ordre, PalierMedaille.annees_requises).all())
    eligibles = medailles_eligibles(t)

    # Historique : médailles déjà attribuées (événements DISTINCTION)
    evts = (EvenementCarriere.query.filter_by(tenant_id=t.id, type="DISTINCTION")
            .order_by(EvenementCarriere.date_effet.desc()).all())
    historique = []
    for e in evts:
        s = e.salarie
        historique.append({"evt": e, "salarie": s,
                           "palier_libelle": e.titre or "Médaille"})
    return render_template("tenant/medailles.html", tenant=t, paliers=paliers,
                           eligibles=eligibles, historique=historique,
                           now=datetime.now())


@bp.route("/medailles/parametres", methods=["GET", "POST"])
@login_required
def medailles_parametres():
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    _ensure_paliers_defaut(t)

    if request.method == "POST":
        action = request.form.get("action", "maj")

        if action == "ajouter":
            code = (request.form.get("code") or "").strip().upper()
            libelle = (request.form.get("libelle") or "").strip()
            try:
                annees = int(request.form.get("annees_requises") or 0)
            except ValueError:
                annees = 0
            if not code or not libelle or annees <= 0:
                flash("Code, libellé et années (positives) sont obligatoires.", "error")
                return redirect(url_for("tenant.medailles_parametres"))
            if PalierMedaille.query.filter_by(tenant_id=t.id, code=code).first():
                flash(f"Un palier avec le code « {code} » existe déjà.", "error")
                return redirect(url_for("tenant.medailles_parametres"))
            ordre = (db.session.query(db.func.max(PalierMedaille.ordre))
                     .filter_by(tenant_id=t.id).scalar() or 0) + 1
            db.session.add(PalierMedaille(tenant_id=t.id, code=code, libelle=libelle,
                                          annees_requises=annees, ordre=ordre, actif=True))
            db.session.commit()
            log_action("CREATE", "palier_medaille", None, f"Palier médaille {code} ({annees} ans)")
            flash(f"Palier « {libelle} » ajouté.", "success")
            return redirect(url_for("tenant.medailles_parametres"))

        # Mise à jour en lot des paliers existants
        paliers = PalierMedaille.query.filter_by(tenant_id=t.id).all()
        for p in paliers:
            lib = (request.form.get(f"libelle_{p.id}") or "").strip()
            if lib:
                p.libelle = lib
            val = request.form.get(f"annees_{p.id}")
            if val:
                try:
                    n = int(val)
                    if n > 0:
                        p.annees_requises = n
                except ValueError:
                    pass
            p.actif = request.form.get(f"actif_{p.id}") == "1"
        db.session.commit()
        log_action("UPDATE", "palier_medaille", None, "Paramètres paliers médaille mis à jour")
        flash("Paramètres des médailles enregistrés.", "success")
        return redirect(url_for("tenant.medailles_parametres"))

    paliers = (PalierMedaille.query.filter_by(tenant_id=t.id)
               .order_by(PalierMedaille.ordre, PalierMedaille.annees_requises).all())
    return render_template("tenant/medailles_parametres.html", tenant=t, paliers=paliers)


@bp.route("/medailles/attribuer/<int:salarie_id>/<int:palier_id>", methods=["POST"])
@login_required
def medailles_attribuer(salarie_id, palier_id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    s = Salarie.query.filter_by(id=salarie_id, tenant_id=t.id).first_or_404()
    p = PalierMedaille.query.filter_by(id=palier_id, tenant_id=t.id).first_or_404()

    # Garde-fou : ne pas attribuer deux fois le même palier.
    if (s.id, p.code.upper()) in _medailles_deja_attribuees(t, [s.id]):
        flash(f"{s.nom_complet} a déjà reçu la {p.libelle}.", "error")
        return redirect(url_for("tenant.medailles"))

    date_effet = _parse_date(request.form.get("date_effet")) or date.today()
    anc = _anciennete_ans(s, date_effet)
    evt = EvenementCarriere(
        tenant_id=t.id, salarie_id=s.id, type="DISTINCTION", date_effet=date_effet,
        titre=p.libelle,
        details=f"Médaille du travail — {anc} ans de service continu dans l'entreprise "
                f"(seuil : {p.annees_requises} ans). Loi N° 021/2010.",
        ancienne_valeur=f"{anc} ans d'ancienneté",
        nouvelle_valeur=p.code.upper(),
        cree_par=current_user.id,
        cree_par_nom=getattr(current_user, "nom_complet", None) or current_user.email,
    )
    db.session.add(evt)
    db.session.commit()
    log_action("CREATE", "medaille", evt.id,
               f"{p.libelle} — {s.nom_complet} (effet {date_effet.strftime('%d/%m/%Y')})")
    flash(f"{p.libelle} attribuée à {s.nom_complet}. Vous pouvez éditer l'attestation.", "success")
    return redirect(url_for("tenant.medailles"))


@bp.route("/medailles/evenement/<int:id>/attestation")
@login_required
def medailles_attestation(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    evt = EvenementCarriere.query.filter_by(id=id, tenant_id=t.id, type="DISTINCTION").first_or_404()
    s = evt.salarie
    # Retrouver le palier (par code) pour les années requises ; repli souple.
    palier = PalierMedaille.query.filter_by(
        tenant_id=t.id, code=(evt.nouvelle_valeur or "").strip().upper()).first()
    from documents_rh import generer_attestation_medaille_pdf
    pdf = generer_attestation_medaille_pdf(t, s, evt, palier)
    log_action("EXPORT", "medaille", evt.id, "Attestation de médaille éditée")
    nom = f"attestation_medaille_{(s.nom if s else 'salarie')}_{evt.id}.pdf"
    return _doc_response(pdf, nom)
