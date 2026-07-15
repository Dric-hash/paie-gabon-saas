"""
create_sso_app.py — Enregistre l'application « Ameriack Ops » comme client SSO.
À lancer UNE FOIS, depuis le dossier de PaieGabon, environnement activé :

    python create_sso_app.py

Le CLIENT_SECRET n'est affiché qu'une seule fois : copiez-le, il servira à
configurer Ameriack Ops.
"""
import secrets

from app import app, db          # importe l'app PaieGabon
from models import hash_secret
from models_sso import SSOApp

CLIENT_ID = "ameriack-ops"
# URLs de retour autorisées (local + à compléter en production).
REDIRECT_URIS = "https://ops.ameriack.com/callback http://localhost:5001/callback"

with app.app_context():
    db.create_all()  # au cas où les tables sso_* n'existent pas encore

    secret = secrets.token_urlsafe(32)
    app_row = SSOApp.query.filter_by(client_id=CLIENT_ID).first()
    if app_row:
        app_row.client_secret_hash = hash_secret(secret)
        app_row.redirect_uris = REDIRECT_URIS
        app_row.actif = True
        action = "mise à jour"
    else:
        db.session.add(SSOApp(
            nom="Ameriack Ops",
            client_id=CLIENT_ID,
            client_secret_hash=hash_secret(secret),
            redirect_uris=REDIRECT_URIS,
            actif=True))
        action = "créée"
    db.session.commit()

    print("=" * 56)
    print(f"Application SSO « Ameriack Ops » {action}.")
    print("À reporter dans le fichier .env d'Ameriack Ops :")
    print()
    print(f"  SSO_CLIENT_ID={CLIENT_ID}")
    print(f"  SSO_CLIENT_SECRET={secret}")
    print()
    print("  (Ce secret ne sera plus jamais affiché — copiez-le maintenant.)")
    print("  Redirect autorisée :", REDIRECT_URIS)
    print("=" * 56)
