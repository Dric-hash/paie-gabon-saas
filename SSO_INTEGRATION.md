# SSO PaieGabon — Intégration du fournisseur (« Se connecter avec PaieGabon »)

Ajoute à PaieGabon le flux OAuth2 `authorization_code` qui permet aux autres
logiciels Ameriack (Ameriack Ops, etc.) de déléguer la connexion à PaieGabon.
Aucun fichier existant n'est écrasé ; on ajoute des fichiers + 4 lignes dans app.py.

## Fichiers fournis (à copier dans le dossier paie-gabon-saas)

```
models_sso.py                          ← racine
create_sso_app.py                      ← racine (script à lancer une fois)
blueprints/sso_provider.py             ← dossier blueprints/
templates/sso/consent.html             ← nouveau dossier templates/sso/
```

## Étape 1 — Enregistrer dans app.py

Dans la section des blueprints (là où il y a déjà `app.register_blueprint(...)`),
ajoutez :

```python
import models_sso
from blueprints.sso_provider import bp as sso_bp, sso_api as sso_api_bp
app.register_blueprint(sso_bp)
app.register_blueprint(sso_api_bp)
csrf.exempt(sso_api_bp)   # /sso/token et /sso/userinfo sont appelés de serveur à serveur
```

## Étape 2 — Créer les tables

Redémarrez PaieGabon (`python app.py`). Les tables `sso_apps`,
`sso_auth_codes`, `sso_access_tokens` sont créées automatiquement
(`db.create_all()` au démarrage).

## Étape 3 — Enregistrer l'application Ameriack Ops

Toujours dans le dossier PaieGabon, environnement activé :

```
python create_sso_app.py
```

Le script affiche un `SSO_CLIENT_ID` et un `SSO_CLIENT_SECRET`.
**Copiez le secret** (affiché une seule fois) : il va dans le `.env`
d'Ameriack Ops.

## En production
Relancez `create_sso_app.py` après avoir ajouté l'URL de retour de production
dans `REDIRECT_URIS` (ex. `https://ops.ameriack.com/callback`). Le SSO doit se
faire en HTTPS en production.

## Points de sécurité
- Le code d'autorisation est à usage unique et expire en 2 minutes.
- Le `redirect_uri` est vérifié contre la liste blanche de l'application.
- `/sso/token` exige le `client_secret` (jamais exposé au navigateur).
- Le SSO cible les utilisateurs rattachés à une entreprise (tenant).
