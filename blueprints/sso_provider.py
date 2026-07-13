"""
blueprints/sso_provider.py — Fournisseur SSO « Se connecter avec PaieGabon »
============================================================================
Flux OAuth2 authorization_code :

  1. L'appli cliente (Ameriack Ops) envoie l'utilisateur sur
     GET  /sso/authorize?client_id=...&redirect_uri=...&state=...
     -> si non connecté, PaieGabon affiche sa page de login (login_required),
        puis un écran de consentement.
  2. L'utilisateur autorise -> PaieGabon redirige vers redirect_uri?code=...&state=...
  3. Ameriack Ops échange le code (côté serveur) :
     POST /sso/token  (client_id, client_secret, code, redirect_uri) -> access_token
  4. Ameriack Ops lit l'identité :
     GET  /sso/userinfo  (Authorization: Bearer <token>) -> {email, nom, tenant...}

Deux blueprints :
  - sso_provider  : /authorize (navigateur, protégé CSRF)
  - sso_api       : /token, /userinfo (serveur-à-serveur, exemptés CSRF dans app.py)
"""
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

from flask import (Blueprint, request, redirect, render_template,
                   jsonify, url_for)
from flask_login import login_required, current_user

from models import db, Utilisateur, Tenant
from models_sso import SSOApp, SSOAuthCode, SSOAccessToken

AUTH_CODE_TTL   = 120     # secondes (le code doit être échangé vite)
ACCESS_TOKEN_TTL = 3600   # 1 heure

bp     = Blueprint("sso_provider", __name__, url_prefix="/sso")
sso_api = Blueprint("sso_api", __name__, url_prefix="/sso")


def _now():
    return datetime.utcnow()


# ── 1. Consentement (navigateur) ─────────────────────────────────────────────
@bp.route("/authorize")
@login_required
def authorize():
    client_id    = request.args.get("client_id", "")
    redirect_uri = request.args.get("redirect_uri", "")
    state        = request.args.get("state", "")

    app_ = SSOApp.query.filter_by(client_id=client_id, actif=True).first()
    if not app_ or not app_.redirect_ok(redirect_uri):
        return "Client SSO inconnu ou URL de redirection non autorisée.", 400

    return render_template("sso/consent.html",
                           app=app_, client_id=client_id,
                           redirect_uri=redirect_uri, state=state)


@bp.route("/authorize", methods=["POST"])
@login_required
def authorize_post():
    client_id    = request.form.get("client_id", "")
    redirect_uri = request.form.get("redirect_uri", "")
    state        = request.form.get("state", "")
    decision     = request.form.get("decision", "")

    app_ = SSOApp.query.filter_by(client_id=client_id, actif=True).first()
    if not app_ or not app_.redirect_ok(redirect_uri):
        return "Client SSO inconnu ou URL de redirection non autorisée.", 400

    if decision != "allow":
        return redirect(redirect_uri + "?" + urlencode({"error": "access_denied", "state": state}))

    code = secrets.token_urlsafe(32)
    db.session.add(SSOAuthCode(
        code=code, client_id=client_id,
        tenant_id=current_user.tenant_id, utilisateur_id=current_user.id,
        redirect_uri=redirect_uri,
        expires_at=_now() + timedelta(seconds=AUTH_CODE_TTL)))
    db.session.commit()
    return redirect(redirect_uri + "?" + urlencode({"code": code, "state": state}))


# ── 2. Échange du code contre un jeton (serveur-à-serveur) ───────────────────
@sso_api.route("/token", methods=["POST"])
def token():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    if data.get("grant_type", "") != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400

    client_id     = data.get("client_id", "")
    client_secret = data.get("client_secret", "")
    code          = data.get("code", "")
    redirect_uri  = data.get("redirect_uri", "")

    app_ = SSOApp.query.filter_by(client_id=client_id, actif=True).first()
    if not app_ or not app_.verify_secret(client_secret):
        return jsonify({"error": "invalid_client"}), 401

    ac = SSOAuthCode.query.filter_by(code=code, client_id=client_id, used=False).first()
    if not ac or ac.redirect_uri != redirect_uri or ac.expires_at < _now():
        return jsonify({"error": "invalid_grant"}), 400

    ac.used = True
    tok = secrets.token_urlsafe(40)
    db.session.add(SSOAccessToken(
        token=tok, client_id=client_id,
        tenant_id=ac.tenant_id, utilisateur_id=ac.utilisateur_id,
        expires_at=_now() + timedelta(seconds=ACCESS_TOKEN_TTL)))
    db.session.commit()
    return jsonify({"access_token": tok, "token_type": "Bearer", "expires_in": ACCESS_TOKEN_TTL})


# ── 3. Identité de l'utilisateur (serveur-à-serveur) ─────────────────────────
@sso_api.route("/userinfo")
def userinfo():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "invalid_token"}), 401
    tok = auth[7:].strip()

    at = SSOAccessToken.query.filter_by(token=tok, revoked=False).first()
    if not at or at.expires_at < _now():
        return jsonify({"error": "invalid_token"}), 401

    u = db.session.get(Utilisateur, at.utilisateur_id)
    if not u:
        return jsonify({"error": "user_not_found"}), 404
    t = db.session.get(Tenant, at.tenant_id) if at.tenant_id else None

    return jsonify({
        "sub":        str(u.id),
        "email":      u.email,
        "nom":        u.nom,
        "prenom":     getattr(u, "prenom", ""),
        "role":       u.role,
        "tenant_id":  at.tenant_id,
        "tenant_nom": (getattr(t, "denomination", None) if t else None),
    })
