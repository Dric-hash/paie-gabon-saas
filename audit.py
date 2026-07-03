"""
audit.py — Système d'audit trail PaieGabon
==========================================
Enregistre toutes les actions importantes dans la table audit_logs.

Usage dans app.py :
    from audit import log_action

    # Action simple
    log_action("CREATE", "salarie", s.id, f"Nouveau salarié {s.nom_complet}")

    # Avec données avant/après
    log_action("UPDATE", "salarie", s.id,
               f"Modification salarié {s.nom_complet}",
               avant=avant, apres=apres)

    # Action système (sans utilisateur)
    log_action("SYSTEM", "periode", p.id, "Génération automatique bulletins",
               user_id=None, tenant_id=t.id)

Actions standardisées :
    CREATE    → création d'un objet
    UPDATE    → modification d'un objet
    DELETE    → suppression d'un objet
    VALIDATE  → validation (bulletin, période)
    CANCEL    → annulation de validation
    PAY       → marquage comme payé
    LOGIN     → connexion
    LOGOUT    → déconnexion
    EXPORT    → export de données
    IMPORT    → import de données
    ACCESS    → accès refusé (403)
"""

import json
import logging
from datetime import datetime
from models import utcnow

logger = logging.getLogger("paiegalon.audit")

# Actions dont on veut capturer les données avant/après
ACTIONS_AVEC_DIFF = {"UPDATE", "DELETE"}

# Champs sensibles à masquer dans les logs (mots de passe, tokens)
CHAMPS_SENSIBLES = {
    "mot_de_passe_hash", "reset_token", "token_confirmation",
    "token_changement_email", "client_secret", "token_api",
}


def log_action(
    action: str,
    entite: str = None,
    entite_id: int = None,
    description: str = "",
    avant: dict = None,
    apres: dict = None,
    user_id=None,
    tenant_id=None,
    request=None,
):
    """
    Enregistre une action dans le journal d'audit.

    Args:
        action      : type d'action (CREATE, UPDATE, DELETE, VALIDATE, etc.)
        entite      : type d'objet (salarie, bulletin, conge, etc.)
        entite_id   : ID de l'objet concerné
        description : texte lisible décrivant l'action
        avant       : dict de l'état avant modification
        apres       : dict de l'état après modification
        user_id     : ID de l'utilisateur (auto-détecté si Flask-Login actif)
        tenant_id   : ID du tenant (auto-détecté si disponible)
        request     : objet Flask request (pour IP et user-agent)
    """
    try:
        from models import db, AuditLog

        # Auto-détecter l'utilisateur connecté
        if user_id is None:
            try:
                from flask_login import current_user
                if current_user and current_user.is_authenticated:
                    user_id   = current_user.id
                    if tenant_id is None:
                        tenant_id = getattr(current_user, "tenant_id", None)
            except Exception:
                pass

        # Auto-détecter l'IP et user-agent
        ip_address = None
        user_agent = None
        if request is None:
            try:
                from flask import request as flask_request
                request = flask_request
            except Exception:
                pass

        if request:
            ip_address = (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or request.remote_addr
                or "unknown"
            )
            user_agent = request.headers.get("User-Agent", "")[:300]

        # Sérialiser avant/après en JSON en masquant les champs sensibles
        avant_json = _serialize(avant) if avant else None
        apres_json = _serialize(apres) if apres else None

        log = AuditLog(
            tenant_id   = tenant_id,
            user_id     = user_id,
            action      = action.upper()[:50],
            entite      = (entite or "")[:50],
            entite_id   = entite_id,
            description = (description or "")[:500],
            avant       = avant_json,
            apres       = apres_json,
            ip_address  = ip_address,
            user_agent  = user_agent,
            date_action = utcnow(),
        )
        db.session.add(log)
        # Ne pas committer ici — sera commis avec la transaction principale

        logger.debug(
            f"[Audit] {action} {entite}#{entite_id} — "
            f"user={user_id} tenant={tenant_id} — {description[:80]}"
        )

    except Exception as e:
        # L'audit ne doit jamais bloquer l'application
        logger.error(f"[Audit] Erreur enregistrement : {e}")


def _serialize(data: dict) -> str:
    """Sérialise un dict en JSON en masquant les champs sensibles."""
    if not data:
        return None
    safe = {}
    for k, v in data.items():
        if k in CHAMPS_SENSIBLES:
            safe[k] = "***"
        elif hasattr(v, "__str__") and not isinstance(v, (str, int, float, bool, type(None))):
            safe[k] = str(v)
        else:
            safe[k] = v
    try:
        return json.dumps(safe, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def get_audit_logs(tenant_id, limit=100, offset=0,
                   action=None, entite=None, user_id=None,
                   date_debut=None, date_fin=None, recherche=None):
    """
    Récupère les logs d'audit d'un tenant avec filtres.

    Returns:
        (logs, total) — liste des AuditLog et nombre total
    """
    from models import AuditLog
    from sqlalchemy import desc

    q = AuditLog.query.filter_by(tenant_id=tenant_id)

    if action:
        q = q.filter(AuditLog.action == action.upper())
    if entite:
        q = q.filter(AuditLog.entite == entite)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if date_debut:
        q = q.filter(AuditLog.date_action >= date_debut)
    if date_fin:
        q = q.filter(AuditLog.date_action <= date_fin)
    if recherche:
        like = f"%{recherche.strip()}%"
        q = q.filter(AuditLog.description.ilike(like))

    total = q.count()
    logs  = (q.order_by(desc(AuditLog.date_action))
              .offset(offset).limit(limit).all())
    return logs, total


def get_audit_logs_admin(limit=200, tenant_id=None, action=None):
    """Logs d'audit pour le super admin (tous tenants)."""
    from models import AuditLog
    from sqlalchemy import desc
    q = AuditLog.query
    if tenant_id:
        q = q.filter(AuditLog.tenant_id == tenant_id)
    if action:
        q = q.filter(AuditLog.action == action.upper())
    return q.order_by(desc(AuditLog.date_action)).limit(limit).all()


# ── Décorateur pour auditer automatiquement une route ─────────────────────────

def audit(action: str, entite: str, description_fn=None):
    """
    Décorateur qui enregistre automatiquement une action après l'exécution
    d'une route.

    Usage :
        @audit("CREATE", "salarie", lambda: f"Nouveau salarié créé")
        def salarie_nouveau():
            ...
    """
    import functools
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            result = f(*args, **kwargs)
            try:
                desc = description_fn() if description_fn else f"{action} {entite}"
                log_action(action, entite, description=desc)
            except Exception:
                pass
            return result
        return wrapper
    return decorator
