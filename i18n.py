"""
i18n.py — Système de traduction PaieGabon (FR / EN)
=====================================================
Architecture légère sans dépendance externe :
  - Fichier translations.json = source de vérité
  - Fonction t(key, lang) pour accéder aux traductions
  - Détection automatique depuis Accept-Language
  - Préférence stockée dans session + colonne Tenant.langue

Usage dans les templates Jinja :
  {{ T.nav.dashboard }}           → "Tableau de bord" ou "Dashboard"
  {{ T.bulletins.validate }}      → "Valider" ou "Validate"
  {{ T.payslip.gross_salary }}    → "SALAIRE BRUT" ou "GROSS SALARY"

Usage dans app.py :
  from i18n import get_translations, detect_language
  lang = detect_language(request, current_user)
  T = get_translations(lang)
"""

import json
import os
import logging

logger = logging.getLogger("paiegalon.i18n")

SUPPORTED_LANGUAGES = ["fr", "en", "ar"]
DEFAULT_LANGUAGE     = "fr"

# Langues RTL (droite à gauche)
RTL_LANGUAGES = {"ar"}

def is_rtl(lang: str) -> bool:
    """Retourne True si la langue s'écrit de droite à gauche."""
    return lang in RTL_LANGUAGES

# Chemin vers le fichier de traductions
_TRANS_FILE = os.path.join(os.path.dirname(__file__), "translations.json")

# Cache en mémoire — chargé une seule fois au démarrage
_translations: dict = {}


def _load():
    """Charge les traductions depuis le fichier JSON."""
    global _translations
    if _translations:
        return
    try:
        with open(_TRANS_FILE, "r", encoding="utf-8") as f:
            _translations = json.load(f)
        logger.info(f"[i18n] Traductions chargées — langues : {list(_translations.keys())}")
    except Exception as e:
        logger.error(f"[i18n] Erreur chargement translations.json : {e}")
        _translations = {"fr": {}, "en": {}}


class TranslationProxy:
    """
    Proxy objet qui permet d'accéder aux traductions via la syntaxe pointée.
    Exemple : T.nav.dashboard → _translations["fr"]["nav"]["dashboard"]
    Retourne la clé elle-même si la traduction est manquante (graceful degradation).
    """
    def __init__(self, data: dict, lang: str, path: str = ""):
        self._data = data
        self._lang = lang
        self._path = path

    def __getattr__(self, key: str):
        if key.startswith("_"):
            raise AttributeError(key)
        val = self._data.get(key)
        if val is None:
            logger.debug(f"[i18n] Clé manquante : {self._path}.{key} ({self._lang})")
            return key  # Retourne la clé comme fallback
        if isinstance(val, dict):
            return TranslationProxy(val, self._lang, f"{self._path}.{key}")
        return val

    def __getitem__(self, key):
        """Permet aussi T["nav"]["dashboard"] et T.months["6"]."""
        val = self._data.get(str(key))
        if val is None:
            return str(key)
        if isinstance(val, dict):
            return TranslationProxy(val, self._lang, f"{self._path}[{key}]")
        return val

    def get(self, key, default=None):
        val = self._data.get(str(key))
        return val if val is not None else default

    def __str__(self):
        return str(self._data)

    def __repr__(self):
        return f"TranslationProxy({self._path}, lang={self._lang})"


def get_translations(lang: str = DEFAULT_LANGUAGE) -> TranslationProxy:
    """
    Retourne l'objet de traduction pour la langue donnée.
    Fallback sur FR si la langue n'est pas supportée.
    """
    _load()
    lang = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    return TranslationProxy(_translations.get(lang, {}), lang)


def detect_language(request, user=None) -> str:
    """
    Détecte la langue à utiliser selon l'ordre de priorité :
      1. Préférence sauvegardée en session
      2. Préférence du tenant (colonne langue dans DB)
      3. Header Accept-Language du navigateur
      4. Français par défaut

    Args:
        request : objet Flask request
        user    : Utilisateur Flask-Login (optionnel)
    """
    # 1. Session
    from flask import session
    lang_session = session.get("lang")
    if lang_session in SUPPORTED_LANGUAGES:
        return lang_session

    # 2. Préférence tenant en base
    if user and hasattr(user, "tenant") and user.tenant:
        lang_tenant = getattr(user.tenant, "langue", None)
        if lang_tenant in SUPPORTED_LANGUAGES:
            session["lang"] = lang_tenant  # Mémoriser en session
            return lang_tenant

    # 3. Accept-Language du navigateur
    accept = request.headers.get("Accept-Language", "")
    for part in accept.split(","):
        code = part.strip().split(";")[0].strip().lower()
        code2 = code[:2]  # "fr-FR" → "fr"
        if code2 in SUPPORTED_LANGUAGES:
            return code2

    return DEFAULT_LANGUAGE


def set_language(lang: str) -> str:
    """
    Sauvegarde la langue choisie en session.
    Retourne la langue effectivement appliquée.
    """
    from flask import session
    lang = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    session["lang"] = lang
    return lang
