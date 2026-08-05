#!/usr/bin/env python3
# rappel_abonnement.py
# ═══════════════════════════════════════════════════════════════════════════
#  RAPPEL AUTOMATIQUE D'EXPIRATION D'ABONNEMENT
#  Envoie un email aux clients dont l'abonnement expire dans ~72 heures.
#
#  À lancer une fois par jour via le cron du VPS. Exemple (crontab -e) :
#     0 8 * * *  cd /var/www/paiegabon && /usr/bin/python3 rappel_abonnement.py >> /var/log/paiegabon_rappels.log 2>&1
#  (tous les jours à 8h du matin)
# ═══════════════════════════════════════════════════════════════════════════
import sys
from datetime import timedelta

from app import app, db
from models import Tenant, Utilisateur, utcnow


# Fenêtre de rappel : on prévient quand il reste 3 jours (72h) ou moins,
# mais pas si déjà expiré (jours_restants > 0).
JOURS_RAPPEL = 3


def _html_rappel(prenom, denomination, date_expiration, jours):
    return f"""
<div style="font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:0 auto">
  <div style="background:#0f3d36;padding:28px;border-radius:14px 14px 0 0;text-align:center">
    <h1 style="color:#ffffff;margin:0;font-size:20px">PaieGabon</h1>
    <p style="color:#d99e0b;margin:6px 0 0;font-size:12px;letter-spacing:1px">AMERIACK I.T. SOLUTIONS</p>
  </div>
  <div style="background:#ffffff;padding:32px 28px;border:1px solid #e6e2d6;border-top:none;border-radius:0 0 14px 14px">
    <div style="text-align:center;font-size:40px;margin-bottom:8px">⏰</div>
    <h2 style="color:#0f3d36;margin:0 0 14px;font-size:19px;text-align:center">Votre abonnement expire bientôt</h2>
    <p style="color:#4b5563;line-height:1.65;font-size:15px">Bonjour {prenom},</p>
    <p style="color:#4b5563;line-height:1.65;font-size:15px">
      L'abonnement PaieGabon de <strong style="color:#0f3d36">{denomination}</strong> arrive à échéance
      dans <strong>{jours} jour(s)</strong>, le <strong>{date_expiration.strftime('%d/%m/%Y')}</strong>.
    </p>
    <p style="color:#4b5563;line-height:1.65;font-size:15px">
      Pour continuer à utiliser PaieGabon sans interruption, pensez à renouveler votre abonnement.
    </p>
    <div style="text-align:center;margin:22px 0">
      <a href="https://paiegabon.com/paiement" style="background:#d99e0b;color:#0f3d36;padding:13px 30px;border-radius:10px;font-weight:bold;text-decoration:none;font-size:15px;display:inline-block">
        Renouveler mon abonnement
      </a>
    </div>
    <p style="color:#6b7280;line-height:1.6;font-size:13px;text-align:center">
      Une question ? Écrivez-nous à
      <a href="mailto:infospaiegabon@paiegabon.com" style="color:#0f3d36">infospaiegabon@paiegabon.com</a>
      ou sur WhatsApp au <a href="https://wa.me/24174584772" style="color:#0f3d36">+241 74 58 47 72</a>.
    </p>
  </div>
</div>"""


def envoyer_rappels():
    """Parcourt les tenants actifs et envoie un rappel à ceux qui expirent
    dans JOURS_RAPPEL jours (72h). Retourne le nombre d'emails envoyés."""
    import os
    if not os.environ.get("MAIL_PASSWORD"):
        print("[RAPPEL] MAIL_PASSWORD non configuré — aucun envoi.")
        return 0

    from flask_mail import Message as Msg
    from core import send_email_async

    maintenant = utcnow()
    cible_min = maintenant + timedelta(days=JOURS_RAPPEL - 1)  # borne basse (2j)
    cible_max = maintenant + timedelta(days=JOURS_RAPPEL)      # borne haute (3j)

    envoyes = 0
    tenants = Tenant.query.filter(Tenant.statut.in_(["ACTIF", "ESSAI"])).all()
    for t in tenants:
        if not t.date_expiration:
            continue
        jours_restants = (t.date_expiration.date() - maintenant.date()).days
        # On envoie exactement dans la fenêtre 72h (3 jours restants)
        if jours_restants != JOURS_RAPPEL:
            continue
        admin = Utilisateur.query.filter_by(tenant_id=t.id, role="TENANT_ADMIN").first()
        if not admin or not admin.email:
            continue
        mail = app.extensions["mail"]
        msg = Msg(
            subject=f"⏰ Votre abonnement PaieGabon expire dans {JOURS_RAPPEL} jours",
            recipients=[admin.email],
            html=_html_rappel(admin.prenom or "", t.denomination, t.date_expiration, jours_restants),
            sender=app.config["MAIL_DEFAULT_SENDER"],
        )
        try:
            send_email_async(mail, msg)
            envoyes += 1
            print(f"[RAPPEL] Envoyé à {admin.email} ({t.denomination}) — expire le "
                  f"{t.date_expiration.strftime('%d/%m/%Y')}")
        except Exception as e:
            print(f"[RAPPEL] Échec pour {admin.email} : {e}")

    print(f"[RAPPEL] Terminé — {envoyes} rappel(s) envoyé(s) le "
          f"{maintenant.strftime('%d/%m/%Y %H:%M')}")
    return envoyes


if __name__ == "__main__":
    with app.app_context():
        envoyer_rappels()
