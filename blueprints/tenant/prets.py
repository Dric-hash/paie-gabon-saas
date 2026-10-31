# -*- coding: utf-8 -*-
"""Prêts au personnel (sans intérêt) : octroi, échéancier indicatif, suivi des
remboursements (manuels ou prélevés sur bulletin) et reçu PDF.

Impressions :
  /prets/imprimer                      état des prêts regroupé par contractant (sous-totaux),
                                       filtres statut / contractant / site / situation au, détail optionnel
  /prets/contractant/<id>/imprimer     relevé d'un contractant : tous ses prêts, détaillés, à signer
  /prets/<id>/imprimer                 fiche détaillée d'un prêt (échéancier, remboursements, signatures)"""
from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from blueprints.tenant import bp, _doc_response
from core import get_tenant, _parse_date
from audit import log_action
from models import db, Salarie, PretPersonnel, RemboursementPret, Site


def _mois_plus(annee, mois, k):
    """Renvoie (annee, mois) k mois après (annee, mois)."""
    idx = (mois - 1) + k
    return annee + idx // 12, idx % 12 + 1


def _echeancier(pret):
    """Échéancier indicatif : une ligne par mensualité à partir de la date d'octroi."""
    n = int(pret.nb_echeances or 1) or 1
    total = float(pret.montant_total or 0)
    base = round(total / n, 0)
    lignes = []
    cumul = 0.0
    d = pret.date_octroi or date.today()
    for i in range(n):
        an, mo = _mois_plus(d.year, d.month, i + 1)
        montant = base if i < n - 1 else round(total - base * (n - 1), 0)
        cumul += montant
        lignes.append({"num": i + 1, "annee": an, "mois": mo, "montant": montant,
                       "cumul": cumul, "restant": round(total - cumul, 0)})
    return lignes


@bp.route("/prets")
@login_required
def prets():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    statut = request.args.get("statut")  # EN_COURS | SOLDE | ANNULE | None
    q = PretPersonnel.query.filter_by(tenant_id=t.id)
    if statut in ("EN_COURS", "SOLDE", "ANNULE"):
        q = q.filter_by(statut=statut)
    liste = q.order_by(PretPersonnel.statut, PretPersonnel.date_octroi.desc()).all()
    total_encours = sum(p.montant_restant for p in liste if p.statut == "EN_COURS")
    total_prete   = sum(float(p.montant_total or 0) for p in liste if p.statut != "ANNULE")
    salaries = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").order_by(Salarie.nom).all()
    contractants = (Salarie.query.join(PretPersonnel, PretPersonnel.salarie_id == Salarie.id)
                    .filter(PretPersonnel.tenant_id == t.id).distinct().order_by(Salarie.nom, Salarie.prenom).all())
    return render_template("tenant/prets.html",
        tenant=t, liste=liste, salaries=salaries, statut=statut, contractants=contractants,
        sites=Site.query.filter_by(tenant_id=t.id).order_by(Site.nom).all(), aujourdhui=date.today(),
        total_encours=total_encours, total_prete=total_prete,
        nb_encours=len([p for p in liste if p.statut == "EN_COURS"]))


@bp.route("/prets/nouveau", methods=["GET", "POST"])
@login_required
def pret_nouveau():
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    salaries = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").order_by(Salarie.nom).all()
    if request.method == "POST":
        if not current_user.can_edit:
            abort(403)
        sal_id = request.form.get("salarie_id", type=int)
        sal = Salarie.query.filter_by(id=sal_id, tenant_id=t.id).first() if sal_id else None
        try:
            montant = float((request.form.get("montant_total") or "0").replace(" ", "").replace(",", "."))
        except ValueError:
            montant = 0
        nb = request.form.get("nb_echeances", type=int) or 1
        d_octroi = _parse_date(request.form.get("date_octroi")) or date.today()
        if not sal or montant <= 0 or nb < 1:
            flash("Vérifiez le salarié, le montant et le nombre d'échéances.", "error")
            return render_template("tenant/pret_form.html", tenant=t, salaries=salaries, now=datetime.now())
        p = PretPersonnel(
            tenant_id=t.id, salarie_id=sal.id, montant_total=montant,
            date_octroi=d_octroi, nb_echeances=nb,
            motif=(request.form.get("motif") or "").strip() or None,
            statut="EN_COURS", montant_rembourse=0,
            cree_par=current_user.id,
            cree_par_nom=getattr(current_user, "nom_complet", None) or current_user.email,
        )
        db.session.add(p)
        db.session.commit()
        log_action("CREATE", "pret", p.id,
                   f"Prêt {montant:,.0f} F — {sal.nom_complet} sur {nb} mois".replace(",", " "))
        flash(f"Prêt de {montant:,.0f} FCFA enregistré.".replace(",", " "), "success")
        return redirect(url_for("tenant.pret_detail", id=p.id))
    return render_template("tenant/pret_form.html", tenant=t, salaries=salaries, now=datetime.now())


@bp.route("/prets/<int:id>")
@login_required
def pret_detail(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    p = PretPersonnel.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    remboursements = sorted(p.remboursements, key=lambda r: (r.annee or 0, r.mois or 0, r.id))
    now = datetime.now()
    return render_template("tenant/pret_detail.html",
        tenant=t, pret=p, echeancier=_echeancier(p),
        remboursements=remboursements, now=now)


@bp.route("/prets/<int:id>/rembourser", methods=["POST"])
@login_required
def pret_rembourser(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    p = PretPersonnel.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if p.statut == "ANNULE":
        flash("Ce prêt est annulé.", "error")
        return redirect(url_for("tenant.pret_detail", id=p.id))
    try:
        montant = float((request.form.get("montant") or "0").replace(" ", "").replace(",", "."))
    except ValueError:
        montant = 0
    if montant <= 0:
        flash("Montant de remboursement invalide.", "error")
        return redirect(url_for("tenant.pret_detail", id=p.id))
    # On ne rembourse jamais plus que le capital restant dû.
    montant = min(montant, p.montant_restant)
    if montant <= 0:
        flash("Ce prêt est déjà soldé.", "error")
        return redirect(url_for("tenant.pret_detail", id=p.id))
    r = RemboursementPret(
        tenant_id=t.id, pret_id=p.id, montant=montant,
        mois=request.form.get("mois", type=int),
        annee=request.form.get("annee", type=int),
        note=(request.form.get("note") or "").strip() or None,
        date_remboursement=_parse_date(request.form.get("date_remboursement")) or date.today(),
    )
    db.session.add(r)
    p.montant_rembourse = float(p.montant_rembourse or 0) + montant
    p.recalculer_statut()
    db.session.commit()
    log_action("UPDATE", "pret", p.id, f"Remboursement {montant:,.0f} F".replace(",", " "))
    flash(f"Remboursement de {montant:,.0f} FCFA enregistré.".replace(",", " "), "success")
    return redirect(url_for("tenant.pret_detail", id=p.id))


@bp.route("/prets/<int:id>/annuler", methods=["POST"])
@login_required
def pret_annuler(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    p = PretPersonnel.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    p.statut = "ANNULE"
    db.session.commit()
    log_action("CANCEL", "pret", p.id, "Prêt annulé")
    flash("Prêt annulé.", "success")
    return redirect(url_for("tenant.pret_detail", id=p.id))


@bp.route("/prets/<int:id>/supprimer", methods=["POST"])
@login_required
def pret_supprimer(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    p = PretPersonnel.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if p.remboursements:
        flash("Impossible de supprimer : ce prêt a des remboursements. Annulez-le plutôt.", "error")
        return redirect(url_for("tenant.pret_detail", id=p.id))
    db.session.delete(p)
    db.session.commit()
    log_action("DELETE", "pret", id, "Prêt supprimé")
    flash("Prêt supprimé.", "success")
    return redirect(url_for("tenant.prets"))


@bp.route("/prets/<int:id>/recu")
@login_required
def pret_recu(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    p = PretPersonnel.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    from documents_rh import generer_recu_pret_pdf
    pdf = generer_recu_pret_pdf(t, p, _echeancier(p))
    log_action("EXPORT", "pret", p.id, "Reçu de prêt édité")
    nom = f"recu_pret_{(p.salarie.nom if p.salarie else 'salarie')}_{p.id}.pdf"
    return _doc_response(pdf, nom)


# ── Impressions ───────────────────────────────────────────────────────────────
MOIS_COURTS = ["", "Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin", "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc."]


def _situation(p, au):
    """Situation d'un prêt à la date « au » : remboursements jusqu'à cette date, restant dû,
    montant attendu d'après l'échéancier (mois échus) et retard éventuel."""
    rembs = sorted([r for r in p.remboursements if r.date_remboursement is None or r.date_remboursement <= au],
                   key=lambda r: (r.date_remboursement or date.min, r.annee or 0, r.mois or 0, r.id))
    rembourse = round(sum(float(r.montant or 0) for r in rembs), 2)
    total = float(p.montant_total or 0)
    ech = _echeancier(p)
    attendu = sum(e["montant"] for e in ech if (e["annee"], e["mois"]) <= (au.year, au.month))
    annule = p.statut == "ANNULE"
    return {"pret": p, "remboursements": rembs, "rembourse": rembourse,
            "restant": 0.0 if annule else max(round(total - rembourse, 2), 0.0),
            "attendu": attendu, "retard": 0.0 if annule else max(round(attendu - rembourse, 2), 0.0),
            "echeancier": ech, "nb_regles": len(rembs)}


def _groupes_contractants(t, prets, au):
    """[{salarie, prets: [situation…], totaux}] trié par nom ; totaux hors prêts annulés."""
    par = {}
    for p in prets:
        par.setdefault(p.salarie_id, []).append(_situation(p, au))
    groupes = []
    for sid, sits in par.items():
        sits.sort(key=lambda x: (x["pret"].date_octroi or date.min, x["pret"].id))
        actifs = [x for x in sits if x["pret"].statut != "ANNULE"]
        groupes.append({"salarie": sits[0]["pret"].salarie, "prets": sits,
                        "prete": sum(float(x["pret"].montant_total or 0) for x in actifs),
                        "rembourse": sum(x["rembourse"] for x in actifs),
                        "restant": sum(x["restant"] for x in actifs),
                        "retard": sum(x["retard"] for x in actifs)})
    groupes.sort(key=lambda g: ((g["salarie"].nom or "").upper(), (g["salarie"].prenom or "").upper()))
    return groupes


def _totaux(groupes):
    return {k: sum(g[k] for g in groupes) for k in ("prete", "rembourse", "restant", "retard")} | {
        "nb": sum(1 for g in groupes for x in g["prets"] if x["pret"].statut != "ANNULE")}


def _rendu_etat(t, groupes, au, titre, detail, filtres, releve=None):
    return render_template("tenant/prets_print.html", tenant=t, groupes=groupes, tot=_totaux(groupes), au=au,
                           titre=titre, detail=detail, filtres=filtres, releve=releve, MOIS=MOIS_COURTS,
                           now=datetime.now())


@bp.route("/prets/imprimer")
@login_required
def prets_imprimer():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    au = _parse_date(request.args.get("au")) or date.today()
    statut = request.args.get("statut", "EN_COURS")
    q = PretPersonnel.query.filter(PretPersonnel.tenant_id == t.id, PretPersonnel.date_octroi <= au)
    filtres = []
    if statut in PretPersonnel.STATUTS:
        q = q.filter(PretPersonnel.statut == statut)
        filtres.append({"EN_COURS": "prêts en cours", "SOLDE": "prêts soldés", "ANNULE": "prêts annulés"}[statut])
    else:
        statut = ""
        filtres.append("tous les prêts")
    sid = request.args.get("salarie_id", type=int)
    if sid:
        sal = Salarie.query.filter_by(id=sid, tenant_id=t.id).first_or_404()
        q = q.filter(PretPersonnel.salarie_id == sal.id)
        filtres.append(f"contractant : {sal.nom_complet}")
    prets = q.all()
    site_id = request.args.get("site_id", type=int)
    if site_id is not None and request.args.get("site_id") != "":
        import impression_sites as imp
        affect = imp.site_de(t, "salarie_id")
        if site_id == 0:
            prets = [p for p in prets if affect.get(p.salarie_id) is None]
            filtres.append(f"site : {imp.SANS_SITE.lower()}")
        else:
            site = Site.query.filter_by(id=site_id, tenant_id=t.id).first_or_404()
            prets = [p for p in prets if affect.get(p.salarie_id) == site.id]
            filtres.append(f"site : {site.nom}")
    detail = request.args.get("detail") == "1"
    log_action("EXPORT", "pret", None, f"État des prêts imprimé ({', '.join(filtres)})")
    return _rendu_etat(t, _groupes_contractants(t, prets, au), au, "État des prêts au personnel", detail, filtres)


@bp.route("/prets/contractant/<int:sid>/imprimer")
@login_required
def pret_contractant_imprimer(sid):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    sal = Salarie.query.filter_by(id=sid, tenant_id=t.id).first_or_404()
    au = _parse_date(request.args.get("au")) or date.today()
    prets = PretPersonnel.query.filter(PretPersonnel.tenant_id == t.id, PretPersonnel.salarie_id == sal.id,
                                       PretPersonnel.date_octroi <= au).all()
    log_action("EXPORT", "pret", None, f"Relevé des prêts de {sal.nom_complet} imprimé")
    return _rendu_etat(t, _groupes_contractants(t, prets, au), au, "Relevé des prêts du contractant", True,
                       [f"contractant : {sal.nom_complet}"], releve=sal)


@bp.route("/prets/<int:id>/imprimer")
@login_required
def pret_imprimer(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    p = PretPersonnel.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    au = _parse_date(request.args.get("au")) or date.today()
    log_action("EXPORT", "pret", p.id, "Fiche de prêt imprimée")
    return render_template("tenant/pret_print.html", tenant=t, x=_situation(p, au), au=au, MOIS=MOIS_COURTS,
                           now=datetime.now())
