"""
models_sso.py — Modèles du fournisseur SSO de PaieGabon
=======================================================
Ajoute le flux OAuth2 « authorization_code » (le « Se connecter avec
PaieGabon » utilisé par les autres logiciels Ameriack).

- SSOApp          : une application autorisée à utiliser le SSO (ex. Ameriack Ops).
                    Ce n'est PAS lié à un tenant : une même appli sert tous les tenants.
- SSOAuthCode     : code d'autorisation à usage unique et courte durée.
- SSOAccessToken  : jeton d'accès permettant de lire l'identité (/sso/userinfo).

Distinct du modèle OAuthClient existant (qui, lui, gère l'accès API
machine-à-machine par tenant). Les deux coexistent sans se gêner.
"""
from datetime import datetime
from models import db, utcnow, hash_secret, verifier_secret


class SSOApp(db.Model):
    __tablename__ = "sso_apps"

    id                 = db.Column(db.Integer, primary_key=True)
    nom                = db.Column(db.String(120), nullable=False)
    client_id          = db.Column(db.String(64), unique=True, nullable=False)
    client_secret_hash = db.Column(db.String(128), nullable=False)
    # URLs de retour autorisées, séparées par des espaces.
    redirect_uris      = db.Column(db.Text, default="")
    actif              = db.Column(db.Boolean, default=True)
    date_creation      = db.Column(db.DateTime, default=utcnow)

    def verify_secret(self, raw):
        return verifier_secret(raw, self.client_secret_hash)

    def redirect_ok(self, uri):
        raw = (self.redirect_uris or "").replace(",", " ")
        return (uri or "").strip() in raw.split()


class SSOAuthCode(db.Model):
    __tablename__ = "sso_auth_codes"

    id             = db.Column(db.Integer, primary_key=True)
    code           = db.Column(db.String(80), unique=True, nullable=False)
    client_id      = db.Column(db.String(64), nullable=False)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"))
    utilisateur_id = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"), nullable=False)
    redirect_uri   = db.Column(db.Text, nullable=False)
    expires_at     = db.Column(db.DateTime, nullable=False)
    used           = db.Column(db.Boolean, default=False)


class SSOAccessToken(db.Model):
    __tablename__ = "sso_access_tokens"

    id             = db.Column(db.Integer, primary_key=True)
    token          = db.Column(db.String(80), unique=True, nullable=False)
    client_id      = db.Column(db.String(64), nullable=False)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"))
    utilisateur_id = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"), nullable=False)
    expires_at     = db.Column(db.DateTime, nullable=False)
    revoked        = db.Column(db.Boolean, default=False)
