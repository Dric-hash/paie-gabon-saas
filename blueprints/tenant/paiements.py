# -*- coding: utf-8 -*-
"""Paiements (Airtel Money, CinetPay), webhooks, abonnement, offres —
extrait de tenant.py (même blueprint « tenant »)."""
from datetime import datetime, date, timedelta
from flask import render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_required, current_user
from blueprints.tenant import bp, logger, _activer_abonnement
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


# ── Airtel Money — Initiation ──────────────────────────────────────────────────
@bp.route("/paiement/airtel/initier", methods=["POST"])
@login_required
def paiement_airtel_initier():
    """
    Lance une demande de paiement STK Push Airtel Money.
    Le client reçoit une notification USSD sur son téléphone.
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    telephone  = request.form.get("telephone", "").strip()
    duree      = int(request.form.get("duree", 1) or 1)
    plan_id    = request.form.get("plan_id", type=int) or (t.plan_id)

    plan = db.session.get(Plan, plan_id) if plan_id else t.plan
    if not plan:
        flash("Plan introuvable.", "error")
        return redirect(url_for("tenant.paiement"))

    if not telephone:
        flash("Veuillez saisir votre numéro Airtel Money.", "error")
        return redirect(url_for("tenant.paiement"))

    montant = float(plan.prix_mensuel) * duree

    # Générer une référence unique
    import uuid
    reference = f"AM-{t.id}-{uuid.uuid4().hex[:10].upper()}"

    # Enregistrer la tentative en base
    p = Paiement(
        tenant_id=t.id,
        moyen="AIRTEL_MONEY",
        montant=montant,
        duree_mois=duree,
        plan_id=plan.id,
        reference_interne=reference,
        telephone=telephone,
        statut="EN_ATTENTE",
    )
    db.session.add(p)
    db.session.commit()

    # Appeler l'API Airtel
    try:
        from airtel_money import initier_paiement, AirtelConfigError
        resultat = initier_paiement(
            reference=reference,
            telephone=telephone,
            montant=montant,
            description=f"Abonnement PaieGabon {plan.nom} — {duree} mois",
        )
        p.reference_externe = resultat.get("transaction_id")
        import json
        p.reponse_raw = json.dumps(resultat.get("raw", {}))

        if resultat["success"]:
            db.session.commit()
            logger.info(f"[Paiement] Airtel initié — ref={reference} tenant={t.id}")
            flash(
                f"Demande de paiement envoyée sur le {telephone}. "
                "Validez sur votre téléphone dans les 2 minutes.",
                "success"
            )
            return redirect(url_for("tenant.paiement_airtel_attente", reference=reference))
        else:
            p.statut = "ECHEC"
            p.notes  = resultat["message"]
            db.session.commit()
            flash(f"Échec : {resultat['message']}", "error")
            return redirect(url_for("tenant.paiement"))

    except Exception as e:
        p.statut = "ECHEC"
        p.notes  = str(e)
        db.session.commit()
        logger.error(f"[Paiement] Erreur Airtel : {e}")
        flash(f"Erreur de connexion Airtel Money. Réessayez ou contactez le support.", "error")
        return redirect(url_for("tenant.paiement"))


# ── Airtel Money — Page d'attente ──────────────────────────────────────────────
@bp.route("/paiement/airtel/attente/<reference>")
@login_required
def paiement_airtel_attente(reference):
    """
    Page d'attente affichée après l'initiation.
    Fait un polling automatique toutes les 5 secondes via AJAX.
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    p = Paiement.query.filter_by(
        reference_interne=reference, tenant_id=t.id
    ).first_or_404()
    return render_template("tenant/paiement_attente.html", paiement=p, tenant=t)


# ── Airtel Money — Vérification statut (AJAX polling) ─────────────────────────
@bp.route("/paiement/airtel/statut/<reference>")
@login_required
def paiement_airtel_statut(reference):
    """
    Endpoint JSON pour le polling côté client.
    Retourne le statut actuel de la transaction.
    """
    t = get_tenant()
    if not t: return jsonify({"statut": "ERREUR", "message": "Non connecté"}), 401

    p = Paiement.query.filter_by(
        reference_interne=reference, tenant_id=t.id
    ).first_or_404()

    # Si déjà confirmé en base, retourner directement
    if p.statut == "SUCCES":
        return jsonify({"statut": "SUCCES", "message": "Paiement confirmé !"})
    if p.statut == "ECHEC":
        return jsonify({"statut": "ECHEC", "message": p.notes or "Paiement refusé."})
    if p.statut == "EXPIRE":
        return jsonify({"statut": "EXPIRE", "message": "Délai dépassé. Recommencez."})

    # Interroger l'API Airtel si on a un transaction_id
    if p.reference_externe:
        try:
            from airtel_money import verifier_statut
            r = verifier_statut(p.reference_externe)
            if r["statut"] == "SUCCESS":
                _activer_abonnement(p)
                return jsonify({"statut": "SUCCES", "message": "Paiement confirmé !"})
            elif r["statut"] in ("FAILED", "EXPIRED"):
                p.statut = "ECHEC" if r["statut"] == "FAILED" else "EXPIRE"
                p.notes  = r.get("message", "")
                db.session.commit()
                return jsonify({"statut": p.statut, "message": p.notes})
        except Exception as e:
            logger.warning(f"[Paiement] Polling Airtel erreur : {e}")

    return jsonify({"statut": "EN_ATTENTE", "message": "En attente de confirmation…"})


# ── Airtel Money — Webhook (callback automatique d'Airtel) ────────────────────
@bp.route("/webhook/airtel", methods=["POST"])
def webhook_airtel():
    """
    Reçoit les notifications automatiques d'Airtel après paiement du client.
    Airtel appelle cette URL avec le résultat de la transaction.
    """
    from airtel_money import valider_signature_webhook
    import json

    payload_bytes = request.get_data()
    signature     = request.headers.get("X-Airtel-Signature", "")

    # Vérifier la signature si configurée
    if not valider_signature_webhook(payload_bytes, signature):
        logger.warning("[Webhook Airtel] Signature invalide — requête ignorée.")
        return jsonify({"status": "SIGNATURE_INVALIDE"}), 401

    try:
        data = request.get_json(force=True) or {}
        logger.info(f"[Webhook Airtel] Reçu : {data}")

        # Extraire les infos de la transaction
        txn   = data.get("transaction", {}) or data.get("data", {}).get("transaction", {})
        ref   = txn.get("id") or txn.get("reference") or data.get("reference", "")
        statut_api = (txn.get("status") or data.get("status", {}).get("code", "")).upper()

        if not ref:
            logger.warning("[Webhook Airtel] Référence manquante dans le payload.")
            return jsonify({"status": "REF_MANQUANTE"}), 400

        # Retrouver le paiement
        p = Paiement.query.filter(
            (Paiement.reference_interne == ref) |
            (Paiement.reference_externe == ref)
        ).first()

        if not p:
            logger.warning(f"[Webhook Airtel] Paiement introuvable pour ref={ref}")
            return jsonify({"status": "INTROUVABLE"}), 404

        if p.statut == "SUCCES":
            # Idempotence — déjà traité
            return jsonify({"status": "DEJA_TRAITE"}), 200

        p.reponse_raw = json.dumps(data)

        if statut_api in ("TS", "SUCCESS", "200"):
            # Confirmation autoritative : on ré-interroge Airtel avant d'activer
            from airtel_money import verifier_statut
            verif = verifier_statut(p.reference_externe or ref)
            if verif.get("statut") != "SUCCESS":
                p.statut = "ECHEC"
                p.notes  = f"Webhook OK mais vérification Airtel = {verif.get('statut')}"
                db.session.commit()
                logger.warning(f"[Webhook Airtel] Vérif. divergente — ref={ref}")
                return jsonify({"status": "NON_CONFIRME"}), 200
            _activer_abonnement(p)
            logger.info(f"[Webhook Airtel] Succès vérifié — ref={ref} tenant={p.tenant_id}")
        else:
            p.statut = "ECHEC"
            p.notes  = f"Code Airtel : {statut_api}"
            db.session.commit()
            logger.info(f"[Webhook Airtel] Échec — ref={ref} code={statut_api}")

        return jsonify({"status": "OK"}), 200

    except Exception as e:
        logger.error(f"[Webhook Airtel] Erreur traitement : {e}")
        db.session.rollback()
        return jsonify({"status": "ERREUR_INTERNE"}), 500


# ── Helper : activer l'abonnement après paiement confirmé ─────────────────────


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




@bp.route("/paiement/cinetpay/initier", methods=["POST"])
@login_required
def paiement_cinetpay_initier():
    """
    Initie un paiement CinetPay.
    Crée une session et redirige le client vers la page de paiement CinetPay
    où il choisit son moyen : Airtel Money, Moov Money ou carte bancaire.
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    duree   = int(request.form.get("duree", 1) or 1)
    plan_id = request.form.get("plan_id", type=int) or t.plan_id
    plan    = db.session.get(Plan, plan_id) if plan_id else t.plan

    if not plan:
        flash("Plan introuvable.", "error")
        return redirect(url_for("tenant.paiement"))

    montant = float(plan.prix_mensuel) * duree

    import uuid
    reference = f"CP-{t.id}-{uuid.uuid4().hex[:10].upper()}"

    # Récupérer l'admin du tenant pour pré-remplir les infos client
    admin = Utilisateur.query.filter_by(tenant_id=t.id, role="TENANT_ADMIN").first()
    nom_client   = admin.nom_complet if admin else t.denomination
    email_client = admin.email if admin else ""

    # Enregistrer la tentative
    p = Paiement(
        tenant_id=t.id,
        moyen="CINETPAY",
        montant=montant,
        duree_mois=duree,
        plan_id=plan.id,
        reference_interne=reference,
        statut="EN_ATTENTE",
    )
    db.session.add(p)
    db.session.commit()

    try:
        from cinetpay import initier_paiement, CinetPayConfigError
        resultat = initier_paiement(
            reference=reference,
            montant=montant,
            description=f"PaieGabon {plan.nom} — {duree} mois — {t.denomination}",
            nom_client=nom_client,
            email_client=email_client,
        )

        import json
        p.reponse_raw = json.dumps(resultat.get("raw", {}))

        if resultat["success"]:
            p.reference_externe = resultat.get("payment_token", "")
            db.session.commit()
            logger.info(f"[CinetPay] Session créée — ref={reference} tenant={t.id}")
            # Rediriger directement vers la page CinetPay
            return redirect(resultat["payment_url"])
        else:
            p.statut = "ECHEC"
            p.notes  = resultat["message"]
            db.session.commit()
            flash(f"Erreur CinetPay : {resultat['message']}", "error")
            return redirect(url_for("tenant.paiement"))

    except Exception as e:
        p.statut = "ECHEC"
        p.notes  = str(e)
        db.session.commit()
        logger.error(f"[CinetPay] Erreur initiation : {e}")
        flash("Erreur de connexion CinetPay. Réessayez ou contactez le support.", "error")
        return redirect(url_for("tenant.paiement"))


@bp.route("/paiement/cinetpay/retour")
@login_required
def paiement_cinetpay_retour():
    """
    Page de retour après la page de paiement CinetPay.
    CinetPay redirige ici après que le client ait terminé (succès ou annulation).
    On affiche un message d'attente pendant que le webhook confirme.
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    transaction_id = request.args.get("transaction_id", "")
    # Chercher le paiement par référence interne ou externe
    p = None
    if transaction_id:
        p = Paiement.query.filter(
            (Paiement.reference_interne == transaction_id) |
            (Paiement.reference_externe == transaction_id),
            Paiement.tenant_id == t.id
        ).first()

    # Vérification immédiate du statut
    if p and p.statut == "EN_ATTENTE" and (p.reference_interne or p.reference_externe):
        try:
            from cinetpay import verifier_statut
            ref = p.reference_interne
            r   = verifier_statut(ref)
            if r["statut"] == "ACCEPTED":
                _activer_abonnement(p)
                flash("Paiement confirmé ! Votre abonnement est actif.", "success")
                return redirect(url_for("tenant.dashboard"))
            elif r["statut"] in ("REFUSED", "CANCELLED"):
                p.statut = "ECHEC"
                p.notes  = r.get("message", "Paiement refusé ou annulé.")
                db.session.commit()
        except Exception as e:
            logger.warning(f"[CinetPay] Vérification retour échouée : {e}")

    if p and p.statut == "SUCCES":
        flash("Paiement confirmé ! Votre abonnement est actif.", "success")
        return redirect(url_for("tenant.dashboard"))

    # Afficher la page d'attente (le webhook va confirmer dans quelques secondes)
    return render_template("tenant/paiement_cinetpay_retour.html",
                           paiement=p, tenant=t,
                           transaction_id=transaction_id)


@bp.route("/paiement/cinetpay/statut/<reference>")
@login_required
def paiement_cinetpay_statut(reference):
    """Endpoint JSON pour le polling côté client sur la page de retour."""
    t = get_tenant()
    if not t: return jsonify({"statut": "ERREUR"}), 401

    p = Paiement.query.filter(
        (Paiement.reference_interne == reference),
        Paiement.tenant_id == t.id
    ).first_or_404()

    if p.statut == "SUCCES":
        return jsonify({"statut": "SUCCES", "message": "Paiement confirmé !"})
    if p.statut == "ECHEC":
        return jsonify({"statut": "ECHEC", "message": p.notes or "Paiement refusé."})

    # Vérification active
    try:
        from cinetpay import verifier_statut
        r = verifier_statut(reference)
        if r["statut"] == "ACCEPTED":
            _activer_abonnement(p)
            return jsonify({"statut": "SUCCES", "message": "Paiement confirmé !"})
        elif r["statut"] in ("REFUSED", "CANCELLED"):
            p.statut = "ECHEC"
            p.notes  = r.get("message", "")
            db.session.commit()
            return jsonify({"statut": "ECHEC", "message": p.notes})
    except Exception as e:
        logger.warning(f"[CinetPay] Polling statut erreur : {e}")

    return jsonify({"statut": "EN_ATTENTE", "message": "Vérification en cours…"})


@bp.route("/webhook/cinetpay", methods=["POST"])
def webhook_cinetpay():
    """
    Reçoit les notifications automatiques de CinetPay.

    SÉCURITÉ : on ne fait JAMAIS confiance au statut envoyé dans le corps de la
    requête (falsifiable — le site_id n'est pas un secret). On ré-interroge
    l'API CinetPay (/payment/check) pour obtenir le statut et le montant
    authentiques, et on vérifie que le montant payé correspond au montant
    attendu avant d'activer l'abonnement.
    """
    import json
    from cinetpay import valider_webhook, verifier_statut

    # 1. Lecture tolérante du corps (CinetPay peut envoyer du JSON ou du form-data)
    data = request.get_json(silent=True) or request.form.to_dict()
    logger.info(f"[Webhook CinetPay] Reçu : {data}")

    # 2. Filtre de premier niveau : le site_id doit correspondre
    if not valider_webhook(data):
        return jsonify({"status": "SITE_ID_INVALIDE"}), 401

    # 3. Extraire la référence de transaction
    ref = (data.get("cpm_trans_id") or data.get("transaction_id")
           or data.get("metadata") or "")
    if not ref:
        logger.warning("[Webhook CinetPay] Référence manquante.")
        return jsonify({"status": "REF_MANQUANTE"}), 400

    # 4. Retrouver le paiement en base
    p = Paiement.query.filter_by(reference_interne=ref).first()
    if not p:
        token = data.get("cpm_payment_config") or data.get("payment_token", "")
        p = Paiement.query.filter_by(reference_externe=token).first() if token else None
    if not p:
        logger.warning(f"[Webhook CinetPay] Paiement introuvable ref={ref}")
        return jsonify({"status": "INTROUVABLE"}), 404

    # 5. Idempotence — déjà traité avec succès
    if p.statut == "SUCCES":
        return jsonify({"status": "DEJA_TRAITE"}), 200

    # 6. VÉRIFICATION AUTORITATIVE côté serveur (ne pas croire le corps)
    try:
        verif = verifier_statut(p.reference_interne)
    except Exception as e:
        logger.error(f"[Webhook CinetPay] Échec vérification API : {e}")
        db.session.rollback()
        return jsonify({"status": "VERIF_ERREUR"}), 502

    p.reponse_raw = json.dumps({"webhook": data, "verification": verif.get("raw", {})})

    if verif.get("statut") != "ACCEPTED":
        p.statut = "ECHEC"
        p.notes  = f"Statut CinetPay vérifié : {verif.get('statut')}"
        db.session.commit()
        logger.info(f"[Webhook CinetPay] Non confirmé — ref={ref} statut={verif.get('statut')}")
        return jsonify({"status": "NON_CONFIRME"}), 200

    # 7. Vérifier que le MONTANT payé correspond au montant attendu
    montant_attendu = int(round(float(p.montant or 0)))
    try:
        montant_paye = int(round(float(verif.get("montant") or 0)))
    except (TypeError, ValueError):
        montant_paye = 0
    if montant_paye and montant_paye < montant_attendu:
        p.statut = "ECHEC"
        p.notes  = f"Montant payé ({montant_paye}) < attendu ({montant_attendu}) — rejeté."
        db.session.commit()
        logger.warning(f"[Webhook CinetPay] Montant insuffisant ref={ref} : "
                       f"{montant_paye} < {montant_attendu}")
        return jsonify({"status": "MONTANT_INVALIDE"}), 200

    # 8. Tout est vérifié → activer l'abonnement
    try:
        _activer_abonnement(p)
        logger.info(f"[Webhook CinetPay] Succès vérifié — ref={ref} tenant={p.tenant_id}")
        return jsonify({"status": "OK"}), 200
    except Exception as e:
        logger.error(f"[Webhook CinetPay] Erreur activation : {e}")
        db.session.rollback()
        return jsonify({"status": "ERREUR_INTERNE"}), 500


