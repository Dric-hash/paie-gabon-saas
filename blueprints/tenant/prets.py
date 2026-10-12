# -*- coding: utf-8 -*-
"""Prêts au personnel (sans intérêt) : octroi, échéancier indicatif, suivi des
remboursements (manuels ou prélevés sur bulletin) et reçu PDF."""
from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from blueprints.tenant import bp, _doc_response
from core import get_tenant, _parse_date
from audit import log_action
from models import db, Salarie, PretPersonnel, RemboursementPret


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
    return render_template("tenant/prets.html",
        tenant=t, liste=liste, salaries=salaries, statut=statut,
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
