# -*- coding: utf-8 -*-
"""Abonnement : offres, page de paiement et déclaration de paiement MANUEL
(mobile money sur le numéro de PaieGabon, virement bancaire). Le client
déclare la référence de sa transaction ; le super-admin vérifie puis active
l'abonnement depuis l'administration (espèces : saisie directe par l'admin).

Les intégrations automatiques Airtel Money (API) et CinetPay ont été retirées
le 14/10/2026 : inutilisées, elles exposaient des adresses de notification
publiques sans apporter de service."""
from datetime import datetime, date, timedelta
from flask import render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_required, current_user
from blueprints.tenant import bp, logger
from core import tenant_required, get_tenant
from audit import log_action
from models import db, Paiement, Plan, Utilisateur, Tenant


@bp.route("/offres")
@login_required
def offres():
    """Page 'Nos offres' : le client compare les plans et en choisit un,
    ce qui le mène au paiement avec ce plan présélectionné."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    plans = Plan.query.filter_by(actif=True).order_by(Plan.prix_mensuel).all()
    from paliers_cabinet import PALIERS_CABINET, SUR_DEVIS
    return render_template("tenant/offres.html", tenant=t, plans=plans,
                           paliers_cabinet=PALIERS_CABINET, sur_devis=SUR_DEVIS)


@bp.route("/paiement")
@login_required
def paiement():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    plans = Plan.query.filter_by(actif=True).order_by(Plan.prix_mensuel).all()
    historique = Paiement.query.filter_by(tenant_id=t.id)\
        .order_by(Paiement.date_creation.desc()).limit(10).all()
    from coordonnees_paiement import AIRTEL_MONEY, BANQUE, CONTACT_PAIEMENT
    # Plan présélectionné (?plan=<id>) depuis la page "Nos offres" ; sinon plan actuel
    plan_choisi = request.args.get("plan", type=int) or t.plan_id
    return render_template("tenant/paiement.html", tenant=t, plans=plans,
                           historique=historique, plan_choisi=plan_choisi,
                           airtel=AIRTEL_MONEY, banque=BANQUE, contact=CONTACT_PAIEMENT)



@bp.route("/paiement/confirmer", methods=["POST"])
@login_required
def paiement_confirmer():
    """Paiement manuel : le client déclare avoir payé (Airtel Money ou virement).
    Crée un Paiement EN_ATTENTE que le super-admin validera après vérification."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    moyen     = request.form.get("moyen", "MANUEL").strip().upper()  # AIRTEL_MONEY | VIREMENT
    reference = request.form.get("reference", "").strip()
    duree     = int(request.form.get("duree", 1) or 1)
    plan_id   = request.form.get("plan_id", type=int) or t.plan_id
    if not reference:
        flash("Veuillez indiquer la référence de la transaction.", "error")
        return redirect(url_for("tenant.paiement"))

    plan = db.session.get(Plan, plan_id) if plan_id else t.plan
    # Montant attendu avec remises par durée (3 mois -5%, 6 mois -10%, 12 mois -15%)
    remises = {1: 1.0, 3: 0.95, 6: 0.90, 12: 0.85}
    coef = remises.get(duree, 1.0)
    montant = round(float(plan.prix_mensuel) * duree * coef) if plan else 0

    import uuid
    ref_interne = f"MAN-{t.id}-{uuid.uuid4().hex[:8].upper()}"
    libelle_moyen = {"AIRTEL_MONEY": "Airtel Money", "VIREMENT": "Virement bancaire"}.get(moyen, moyen)
    p = Paiement(
        tenant_id=t.id, moyen=moyen, montant=montant,
        duree_mois=duree, plan_id=plan.id if plan else None,
        reference_interne=ref_interne, reference_externe=reference,
        statut="EN_ATTENTE",
        notes=f"Paiement {libelle_moyen} déclaré par {current_user.email}",
    )
    db.session.add(p)
    t.statut = "PAIEMENT_EN_ATTENTE"
    db.session.commit()
    log_action("CREATE", "paiement", p.id,
               f"Déclaration paiement {libelle_moyen} — réf {reference}, {duree} mois",
               user_id=current_user.id, tenant_id=t.id)
    flash(f"Paiement déclaré (réf. {reference}). Votre abonnement sera activé "
          f"après vérification, généralement sous 24-48h. Merci !", "success")
    return redirect(url_for("tenant.paiement"))
