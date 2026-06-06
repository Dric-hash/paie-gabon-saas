"""
core.py — Utilitaires partagés : cache Redis, décorateurs, helpers, email
"""
import os, threading, logging, json as _json
from datetime import datetime, date
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
