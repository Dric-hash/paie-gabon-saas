"""
alertes_expiration.py — Avertissement « abonnement expire dans 72 h »
=====================================================================
Envoie un e-mail aux administrateurs des tenants dont l'essai ou l'abonnement
arrive à échéance dans les 72 heures, une seule fois par cycle (le champ
Tenant.alerte_expiration_envoyee évite les doublons ; il est remis à NULL au
renouvellement du paiement).

À déclencher une fois par heure (ou par jour) via une tâche planifiée (cron) :

    # Toutes les heures, à la minute 5 :
    5 * * * * cd /var/www/paiegabon && /var/www/paiegabon/venv/bin/flask alertes-expiration >> /var/log/paiegabon-alertes.log 2>&1
"""
from datetime import timedelta


def alerter_expirations_proches(db, mail, models, utcnow, send_email_async, logger=None):
    """Parcourt les tenants et envoie l'alerte 72 h à ceux qui approchent de
    l'échéance sans l'avoir déjà reçue. Retourne le nombre d'e-mails envoyés."""
    from flask_mail import Message as Msg
    Tenant = models.Tenant
    Utilisateur = models.Utilisateur

    maintenant = utcnow()
    limite = maintenant + timedelta(hours=72)

    # Tenants payants (non entreprises gérées : elles héritent du cabinet),
    # dont l'échéance tombe dans les 72 h, non encore alertés pour ce cycle.
    candidats = (Tenant.query
                 .filter(Tenant.cabinet_id.is_(None))
                 .filter(Tenant.date_expiration.isnot(None))
                 .filter(Tenant.date_expiration > maintenant)
                 .filter(Tenant.date_expiration <= limite)
                 .filter(Tenant.alerte_expiration_envoyee.is_(None))
                 .all())

    envoyes = 0
    for t in candidats:
        admin = Utilisateur.query.filter_by(tenant_id=t.id, role="TENANT_ADMIN").first()
        if not admin or not admin.email:
            continue
        echeance = t.date_expiration.strftime("%d/%m/%Y à %Hh%M")
        type_abo = "essai gratuit" if t.statut == "ESSAI" else "abonnement"
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#12211d">
          <div style="background:#0f3d36;color:#fff;padding:22px 26px;border-radius:12px 12px 0 0">
            <h2 style="margin:0;font-size:20px">⏳ Votre {type_abo} expire bientôt</h2>
          </div>
          <div style="border:1px solid #e6e2d6;border-top:none;padding:24px 26px;border-radius:0 0 12px 12px">
            <p>Bonjour,</p>
            <p>Votre {type_abo} PaieGabon pour <strong>{t.denomination}</strong> arrive à
               échéance le <strong>{echeance}</strong>, soit dans moins de 72 heures.</p>
            <p><strong>Passé ce délai, l'accès sera suspendu</strong> jusqu'au règlement.
               Pour éviter toute interruption, renouvelez dès maintenant par
               <strong>Airtel Money</strong> ou <strong>virement bancaire</strong>.</p>
            <p style="text-align:center;margin:26px 0">
              <a href="https://paiegabon.com/paiement"
                 style="background:#d99e0b;color:#0f3d36;font-weight:bold;text-decoration:none;
                        padding:12px 28px;border-radius:8px;display:inline-block">
                Renouveler mon abonnement
              </a>
            </p>
            <p style="color:#6b7280;font-size:13px">Après votre paiement, votre accès est réactivé
               dès validation. Une question ? Écrivez-nous, nous sommes là pour vous aider.</p>
          </div>
        </div>
        """
        try:
            msg = Msg(
                subject=f"⏳ Votre abonnement PaieGabon expire dans moins de 72 h — {t.denomination}",
                recipients=[admin.email],
                html=html,
            )
            send_email_async(mail, msg)
            t.alerte_expiration_envoyee = maintenant
            envoyes += 1
        except Exception as e:
            if logger:
                logger.error(f"[ALERTE EXPIRATION] Échec pour tenant {t.id} : {e}")

    if envoyes:
        db.session.commit()
    if logger:
        logger.info(f"[ALERTE EXPIRATION] {envoyes} alerte(s) 72 h envoyée(s).")
    return envoyes
