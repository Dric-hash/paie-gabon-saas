"""
devises.py — Taux de change pour prestataires étrangers.

Convertit les montants en devise étrangère vers le franc CFA (XAF) :
    • EUR : parité fixe FCFA BEAC (1 EUR = 655,957 XAF) — jamais via API.
    • USD, MAD : taux du jour récupéré via une API gratuite, mis en cache une
      fois par jour (table `taux_devises`), avec repli sur des valeurs par défaut
      configurables si l'API est indisponible.

Le taux renvoyé est toujours « XAF pour 1 unité de la devise ».
La saisie reste éditable côté interface : le taux proposé peut être ajusté.
"""
import os
import logging
from datetime import date as _date
from decimal import Decimal

logger = logging.getLogger("paiegalon")

# Parité fixe de l'euro (zone CEMAC / BEAC)
PEG_EUR_XAF = 655.957

# Devises gérées : code → (libellé, symbole)
DEVISES = {
    "XAF": ("Franc CFA", "FCFA"),
    "EUR": ("Euro", "€"),
    "USD": ("Dollar US", "$"),
    "MAD": ("Dirham marocain", "DH"),
}

# Repli si l'API est injoignable (ajustable via variables d'environnement).
# Valeurs indicatives — la saisie permet de corriger manuellement le taux.
FALLBACK_XAF = {
    "XAF": 1.0,
    "EUR": PEG_EUR_XAF,
    "USD": float(os.environ.get("TAUX_USD_XAF", 600.0)),
    "MAD": float(os.environ.get("TAUX_MAD_XAF", 60.0)),
}

# API gratuite sans clé (base USD) — renvoie les taux de toutes les devises.
_API_URL = "https://open.er-api.com/v6/latest/USD"
_TIMEOUT = 6


def devises_disponibles():
    """Liste ordonnée des devises pour les menus déroulants."""
    return [(code, lib, sym) for code, (lib, sym) in DEVISES.items()]


def _fetch_api_usd_base():
    """Récupère les taux base USD depuis l'API. Renvoie un dict ou None."""
    try:
        import urllib.request
        import json
        with urllib.request.urlopen(_API_URL, timeout=_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("result") == "success" and "rates" in data:
            return data["rates"]
    except Exception as e:  # réseau coupé, timeout, format inattendu…
        logger.warning(f"[DEVISES] Échec récupération taux API : {e}")
    return None


def _taux_depuis_api(devise):
    """Calcule XAF pour 1 unité de `devise` à partir des taux base USD."""
    rates = _fetch_api_usd_base()
    if not rates:
        return None
    xaf = rates.get("XAF")
    if not xaf:
        return None
    if devise == "USD":
        return float(xaf)
    cible = rates.get(devise)
    if not cible:
        return None
    # XAF pour 1 USD ÷ (devise pour 1 USD) = XAF pour 1 unité de devise
    return round(float(xaf) / float(cible), 6)


def taux_xaf(devise, jour=None, rafraichir=False):
    """
    Renvoie le taux « XAF pour 1 unité de `devise` » pour le jour donné.

    - XAF → 1
    - EUR → parité fixe (PEG)
    - USD/MAD → cache quotidien (table taux_devises) ; sinon API ; sinon repli.

    Tolérant aux pannes : ne lève jamais d'exception, retombe sur le repli.
    """
    devise = (devise or "XAF").upper()
    if devise == "XAF":
        return 1.0
    if devise == "EUR":
        return PEG_EUR_XAF
    if devise not in DEVISES:
        return 1.0

    jour = jour or _date.today()

    # Import tardif pour éviter les imports circulaires au chargement du module.
    try:
        from models import db, TauxDevise
    except Exception:
        return FALLBACK_XAF.get(devise, 1.0)

    if not rafraichir:
        row = TauxDevise.query.filter_by(date_taux=jour, devise=devise).first()
        if row:
            return float(row.taux_xaf)

    # Pas en cache (ou rafraîchissement) → API
    taux = _taux_depuis_api(devise)
    source = "API"
    if taux is None:
        # Repli : dernier taux connu, sinon constante de repli
        dernier = (TauxDevise.query.filter_by(devise=devise)
                   .order_by(TauxDevise.date_taux.desc()).first())
        taux = float(dernier.taux_xaf) if dernier else FALLBACK_XAF.get(devise, 1.0)
        source = "FALLBACK"

    # Mémoriser pour la journée
    try:
        row = TauxDevise.query.filter_by(date_taux=jour, devise=devise).first()
        if row:
            row.taux_xaf = taux
            row.source = source
        else:
            db.session.add(TauxDevise(date_taux=jour, devise=devise,
                                      taux_xaf=taux, source=source))
        db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning(f"[DEVISES] Cache non écrit ({devise}) : {e}")

    return float(taux)


def convertir_en_xaf(montant, devise, jour=None):
    """Convertit `montant` exprimé en `devise` vers XAF (au taux du jour)."""
    try:
        m = float(montant or 0)
    except (TypeError, ValueError):
        m = 0.0
    return round(m * taux_xaf(devise, jour), 2)


def info_taux(devise, jour=None):
    """Dict pratique pour l'interface : code, libellé, symbole, taux, équivalent."""
    devise = (devise or "XAF").upper()
    lib, sym = DEVISES.get(devise, (devise, devise))
    t = taux_xaf(devise, jour)
    return {"devise": devise, "libelle": lib, "symbole": sym,
            "taux_xaf": round(t, 6), "est_xaf": devise == "XAF"}
