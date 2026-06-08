"""
monitoring.py — Suivi des erreurs en production avec Sentry

Capture automatiquement toutes les exceptions non gérées et les envoie à
Sentry avec leur contexte (URL, tenant, utilisateur, stack trace). Permet
d'être alerté par email dès qu'un client rencontre un bug, sans attendre
qu'il le signale.

Confidentialité : avant tout envoi, les données sensibles (mots de passe,
tokens, cookies, en-têtes d'authentification) sont supprimées. Aucune donnée
de paie n'est transmise — uniquement le contexte technique de l'erreur.

Activation : définir la variable d'environnement SENTRY_DSN.
Sans elle, le monitoring est simplement désactivé (aucune erreur, aucun envoi).

Variables d'environnement :
    SENTRY_DSN              Clé projet Sentry (obligatoire pour activer)
    SENTRY_ENVIRONMENT      Nom de l'environnement (défaut: production)
    SENTRY_TRACES_RATE      Taux d'échantillonnage performance 0–1 (défaut: 0)
    APP_VERSION             Version de l'app pour suivre les régressions (optionnel)
"""
import os
import logging

logger = logging.getLogger("paiegalon.monitoring")

# Champs sensibles à supprimer avant tout envoi à Sentry
_CHAMPS_SENSIBLES = {
    "password", "mot_de_passe", "mot_de_passe_hash", "nouveau_mdp",
    "csrf_token", "token", "token_api", "reset_token", "secret",
    "secret_key", "b2_app_key", "b2_key_id", "authorization",
    "cookie", "set-cookie", "session",
}


def _scrub(data):
    """Remplace récursivement les valeurs sensibles par [FILTRÉ]."""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if isinstance(k, str) and k.lower() in _CHAMPS_SENSIBLES:
                result[k] = "[FILTRÉ]"
            else:
                result[k] = _scrub(v)
        return result
    if isinstance(data, (list, tuple)):
        return type(data)(_scrub(x) for x in data)
    return data


def _before_send(event, hint):
    """Filtre l'événement avant envoi : supprime les données sensibles."""
    # Nettoyer les données de requête (formulaires, query string, cookies)
    if "request" in event:
        req = event["request"]
        for key in ("data", "cookies", "headers", "query_string"):
            if key in req:
                req[key] = _scrub(req[key])
    # Nettoyer les variables locales des stack traces
    for exc in event.get("exception", {}).get("values", []):
        for frame in exc.get("stacktrace", {}).get("frames", []):
            if "vars" in frame:
                frame["vars"] = _scrub(frame["vars"])
    return event


def init_sentry(app=None):
    """
    Initialise Sentry si SENTRY_DSN est défini.
    Retourne True si activé, False sinon.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("[SENTRY] SENTRY_DSN non défini. Monitoring désactivé.")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("[SENTRY] sentry-sdk non installé. Monitoring désactivé.")
        return False

    # Capture les logs ERROR comme événements Sentry
    logging_integration = LoggingIntegration(
        level=logging.INFO,        # niveau minimal pour les "breadcrumbs"
        event_level=logging.ERROR, # les logs ERROR deviennent des événements
    )

    try:
        traces_rate = float(os.environ.get("SENTRY_TRACES_RATE", "0"))
    except ValueError:
        traces_rate = 0.0

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        release=os.environ.get("APP_VERSION") or None,
        integrations=[FlaskIntegration(), logging_integration],
        traces_sample_rate=traces_rate,
        send_default_pii=False,    # ne pas envoyer d'infos personnelles par défaut
        before_send=_before_send,  # filtrage des données sensibles
        max_request_body_size="small",
    )
    logger.info("[SENTRY] Monitoring activé.")
    return True


def set_user_context(user):
    """
    Attache le contexte utilisateur/tenant à Sentry pour l'erreur courante.
    Appelé à chaque requête authentifiée. N'envoie que des identifiants
    techniques (id, rôle, tenant) — jamais d'email ni de données de paie.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    try:
        if user and getattr(user, "is_authenticated", False):
            sentry_sdk.set_user({
                "id": user.id,
                "role": getattr(user, "role", None),
            })
            tenant = getattr(user, "tenant", None)
            if tenant:
                sentry_sdk.set_tag("tenant_id", tenant.id)
                sentry_sdk.set_tag("tenant_slug", getattr(tenant, "slug", None))
    except Exception:
        # Le monitoring ne doit JAMAIS casser une requête
        pass


def capture_message(message, level="info"):
    """Envoie un message custom à Sentry (utile pour des événements métier)."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.capture_message(message, level=level)
    except Exception:
        pass
