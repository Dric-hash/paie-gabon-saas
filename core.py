"""
core.py — Utilitaires partagés : cache Redis, décorateurs, helpers, email
"""
import os, threading, logging, json as _json
from datetime import datetime, date, timedelta
from functools import wraps
from flask import redirect, url_for, abort, flash, request, session
from flask_login import current_user, logout_user
from flask_mail import Message

logger = logging.getLogger("paiegalon")

# ══════════════════════════════════════════════════════════════════════════════
# CACHE Redis
# ══════════════════════════════════════════════════════════════════════════════
_redis_client = None
_REDIS_URL = os.environ.get("REDIS_URL", "")
if _REDIS_URL:
    try:
        import redis as _redis_lib
        _redis_client = _redis_lib.from_url(
            _REDIS_URL, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2
        )
        _redis_client.ping()
        logger.info("[CACHE] Redis connecté.")
    except Exception as _e:
        logger.warning(f"[CACHE] Connexion Redis échouée ({_e}). Cache désactivé.")
        _redis_client = None
else:
    logger.info("[CACHE] REDIS_URL non définie. Cache désactivé.")

TTL_KPIS_DASH  = 300
TTL_EVOLUTION  = 600
TTL_CATS_STATS = 600
TTL_TOP_SAL    = 300
TTL_ALERTES    = 120


def cache_get(key: str):
    if not _redis_client:
        return None
    try:
        raw = _redis_client.get(key)
        return _json.loads(raw) if raw else None
    except Exception:
        return None


def cache_set(key: str, value, ttl_seconds: int = 300):
    if not _redis_client:
        return
    try:
        _redis_client.setex(key, ttl_seconds, _json.dumps(value, default=str))
    except Exception:
        pass


def cache_delete(key_prefix: str):
    if not _redis_client:
        return
    try:
        cursor = 0
        while True:
            cursor, keys = _redis_client.scan(cursor, match=f"{key_prefix}*", count=100)
            if keys:
                _redis_client.delete(*keys)
            if cursor == 0:
                break
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# STORE DE TOKENS OAUTH2
# Persiste les access tokens OAuth dans Redis (TTL natif, partagé entre workers
# Gunicorn). À défaut de Redis, repli sur un dict mémoire process — utilisable en
# dev/mono-worker, avec expiration vérifiée à la lecture.
# ══════════════════════════════════════════════════════════════════════════════
_oauth_mem: dict = {}  # repli : token → {tenant_id, client_id, expires_at}
_OAUTH_PREFIX = "oauth:tok:"


def oauth_token_store(token: str, tenant_id: int, client_id: str, ttl_seconds: int):
    payload = {"tenant_id": tenant_id, "client_id": client_id}
    if _redis_client:
        try:
            _redis_client.setex(_OAUTH_PREFIX + token, ttl_seconds, _json.dumps(payload))
            return
        except Exception:
            pass
    _oauth_mem[token] = {**payload,
                         "expires_at": datetime.utcnow() + timedelta(seconds=ttl_seconds)}


def oauth_token_get(token: str):
    """Retourne {tenant_id, client_id} si le token est valide et non expiré, sinon None."""
    if _redis_client:
        try:
            raw = _redis_client.get(_OAUTH_PREFIX + token)
            return _json.loads(raw) if raw else None
        except Exception:
            return None
    entry = _oauth_mem.get(token)
    if not entry:
        return None
    if datetime.utcnow() >= entry["expires_at"]:
        _oauth_mem.pop(token, None)
        return None
    return {"tenant_id": entry["tenant_id"], "client_id": entry["client_id"]}


def oauth_token_delete(token: str):
    if _redis_client:
        try:
            _redis_client.delete(_OAUTH_PREFIX + token)
        except Exception:
            pass
    _oauth_mem.pop(token, None)


# ══════════════════════════════════════════════════════════════════════════════
# SÉCURITÉ DES EXPORTS — anti-injection de formule CSV/Excel
# Un champ texte (nom, matricule, dénomination…) commençant par = + - @ ou un
# caractère de contrôle est interprété comme une formule par Excel/LibreOffice à
# l'ouverture du fichier. On le neutralise en le préfixant d'une apostrophe.
# À n'appliquer qu'aux champs TEXTE issus de la saisie utilisateur (jamais aux
# montants formatés, dont le signe « - » est légitime).
# ══════════════════════════════════════════════════════════════════════════════
def csv_safe(value):
    if value is None:
        return ""
    s = str(value)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


# ══════════════════════════════════════════════════════════════════════════════
# DÉCORATEURS
# ══════════════════════════════════════════════════════════════════════════════

def super_admin_required(f):
    @wraps(f)
    def d(*a, **k):
        if not current_user.is_authenticated or not current_user.is_super_admin:
            abort(403)
        return f(*a, **k)
    return d


def tenant_required(f):
    @wraps(f)
    def d(*a, **k):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_super_admin and (
            not current_user.tenant_id
            or not current_user.tenant
            or current_user.tenant.statut not in ("ACTIF", "ESSAI", "PAIEMENT_EN_ATTENTE")
        ):
            flash("Compte suspendu ou non associé à une entreprise.", "error")
            return redirect(url_for("auth.login"))
        return f(*a, **k)
    return d


def can_edit(f):
    """Décorateur : accès en écriture (RH, Admin, Gestionnaire)."""
    @wraps(f)
    def d(*a, **k):
        if not current_user.can_edit:
            abort(403)
        return f(*a, **k)
    return d


def require_permission(perm: str):
    """Décorateur générique : vérifie une permission spécifique."""
    def decorator(f):
        @wraps(f)
        def d(*a, **k):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if not current_user.has_permission(perm) and not current_user.is_tenant_admin:
                abort(403)
            return f(*a, **k)
        return d
    return decorator


def admin_only(f):
    """Décorateur : réservé à l'admin du tenant uniquement."""
    @wraps(f)
    def d(*a, **k):
        if not current_user.is_tenant_admin:
            abort(403)
        return f(*a, **k)
    return d


def plan_required(*codes):
    """
    Décorateur : réserve une fonctionnalité aux tenants dont le plan figure
    dans `codes` (ex. plan_required("CABINET") pour l'abonnement 100 000 FCFA).
    Le super-admin a toujours accès. Sinon → page d'abonnement avec message.
    """
    codes_norm = {c.upper() for c in codes}
    def decorator(f):
        @wraps(f)
        def d(*a, **k):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.is_super_admin:
                return f(*a, **k)
            t = getattr(current_user, "tenant", None)
            plan_code = (t.plan.code.upper() if t and t.plan and t.plan.code else None)
            if plan_code not in codes_norm:
                flash("Cette fonctionnalité est réservée à l'abonnement Cabinet "
                      "(100 000 FCFA/mois). Mettez à niveau votre abonnement pour y accéder.",
                      "error")
                return redirect(url_for("tenant.paiement"))
            return f(*a, **k)
        return d
    return decorator


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_tenant():
    if current_user.is_super_admin:
        return None
    return current_user.tenant


def parse_date(val):
    if not val:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        return datetime.strptime(str(val).strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def calculer_parts_irpp(situation_matrimoniale: str, nb_enfants: int) -> float:
    situation = (situation_matrimoniale or "").upper().strip()
    nb_enf = int(nb_enfants or 0)
    if "CELIBATAIRE" in situation and "AEAC" in situation:
        parts = 1.5
    elif "CELIBATAIRE" in situation:
        parts = 1.0
    elif "DIVORCE" in situation and "AEAC" in situation:
        parts = 1.5
    elif "DIVORCE" in situation:
        parts = 1.0
    elif "MARIE" in situation or "MARIÉ" in situation:
        parts = 2.0
    elif "VEUF" in situation and "2 ANS" in situation:
        parts = 1.5
    elif "VEUF" in situation:
        parts = 2.0
    else:
        parts = 1.0
    parts += nb_enf * 0.5
    return round(parts, 1)


def validate_password(password: str) -> list[str]:
    """
    Valide la robustesse d'un mot de passe.
    Retourne une liste d'erreurs (vide = mot de passe valide).
    """
    errors = []
    if not password or len(password) < 8:
        errors.append("Le mot de passe doit contenir au moins 8 caractères.")
    if not any(c.isupper() for c in (password or "")):
        errors.append("Le mot de passe doit contenir au moins une majuscule.")
    if not any(c.isdigit() for c in (password or "")):
        errors.append("Le mot de passe doit contenir au moins un chiffre.")
    return errors


# ══════════════════════════════════════════════════════════════════════════════
# EMAIL ASYNCHRONE
# ══════════════════════════════════════════════════════════════════════════════

def send_email_async(mail_instance, msg: Message):
    """Envoie un email dans un thread séparé pour ne pas bloquer Gunicorn."""
    from flask import current_app
    app = current_app._get_current_object()

    def run(app_ctx, message):
        with app_ctx:
            try:
                mail_instance.send(message)
            except Exception as e:
                logger.error(f"[EMAIL ERROR] {e}")

    t = threading.Thread(target=run, args=(app.app_context(), msg))
    t.daemon = True
    t.start()


# ══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER (Flask-Limiter)
# ══════════════════════════════════════════════════════════════════════════════
_limiter = None

def get_limiter():
    """Retourne le limiter Flask-Limiter (initialisé dans app.py)."""
    global _limiter
    return _limiter

def init_limiter(app):
    """Attache Flask-Limiter à l'app Flask."""
    global _limiter
    try:
        from flask_limiter import Limiter
        from flask_limiter.util import get_remote_address
        import os
        storage_uri = os.environ.get("REDIS_URL") or "memory://"
        _limiter = Limiter(
            get_remote_address,
            app=app,
            storage_uri=storage_uri,
            default_limits=[],
            headers_enabled=True,
        )
        logger.info(f"[LIMITER] Rate limiter activé (storage: {storage_uri[:20]}…)")
    except ImportError:
        logger.warning("[LIMITER] flask-limiter non installé. Rate limiting désactivé.")
        _limiter = None
    return _limiter


# ══════════════════════════════════════════════════════════════════════════════
# ALIAS rétro-compatibilité (anciens noms utilisés dans les blueprints)
# ══════════════════════════════════════════════════════════════════════════════
_cache_get    = cache_get
_cache_set    = cache_set
_cache_delete = cache_delete
_parse_date   = parse_date


def _pd(v):
    """Parse une date au format YYYY-MM-DD (alias court)."""
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:
        return None
