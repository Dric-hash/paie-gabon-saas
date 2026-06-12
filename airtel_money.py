"""
airtel_money.py — Module de paiement Airtel Money pour PaieGabon SaaS
======================================================================
Implémente le flux Collection (STK Push) de l'API Airtel Africa.

Flux complet :
  1. PaieGabon appelle /initier_paiement_airtel → obtient un token OAuth
  2. Envoie une demande de collecte → Airtel envoie une notification USSD au client
  3. Le client valide sur son téléphone
  4. Airtel rappelle notre webhook /webhook/airtel avec le résultat
  5. On active l'abonnement si succès

Documentation API : https://developers.airtel.africa/apis

Variables d'environnement requises :
  AIRTEL_CLIENT_ID      — fourni par Airtel Developer Portal
  AIRTEL_CLIENT_SECRET  — fourni par Airtel Developer Portal
  AIRTEL_ENV            — "sandbox" (test) ou "production"
  APP_BASE_URL          — URL publique de l'app (ex: https://amenack-paie.up.railway.app)
"""

import os
import hmac
import hashlib
import logging
import requests
from datetime import datetime, timedelta

logger = logging.getLogger("paiegalon.airtel")

# ── Configuration ──────────────────────────────────────────────────────────────
AIRTEL_ENV           = os.environ.get("AIRTEL_ENV", "sandbox")
AIRTEL_CLIENT_ID     = os.environ.get("AIRTEL_CLIENT_ID", "")
AIRTEL_CLIENT_SECRET = os.environ.get("AIRTEL_CLIENT_SECRET", "")
AIRTEL_WEBHOOK_SECRET = os.environ.get("AIRTEL_WEBHOOK_SECRET", "")

BASE_URLS = {
    "sandbox":    "https://openapiuat.airtel.africa",
    "production": "https://openapi.airtel.africa",
}
BASE_URL = BASE_URLS.get(AIRTEL_ENV, BASE_URLS["sandbox"])

# Gabon — code pays ISO
COUNTRY_CODE = "GA"
CURRENCY     = "XAF"

# ── Cache token OAuth (évite de redemander un token à chaque appel) ────────────
_token_cache = {"token": None, "expires_at": datetime.utcnow()}


def _get_access_token() -> str:
    """
    Obtient ou renouvelle le token OAuth2 Airtel.
    Le token est mis en cache jusqu'à son expiration.
    """
    global _token_cache
    now = datetime.utcnow()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    if not AIRTEL_CLIENT_ID or not AIRTEL_CLIENT_SECRET:
        raise AirtelConfigError(
            "AIRTEL_CLIENT_ID et AIRTEL_CLIENT_SECRET doivent être définis "
            "dans les variables d'environnement."
        )

    url = f"{BASE_URL}/auth/oauth2/token"
    payload = {
        "client_id":     AIRTEL_CLIENT_ID,
        "client_secret": AIRTEL_CLIENT_SECRET,
        "grant_type":    "client_credentials",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            raise AirtelAPIError(f"Token absent dans la réponse : {data}")
        expires_in = int(data.get("expires_in", 3600))
        _token_cache = {
            "token":      token,
            "expires_at": now + timedelta(seconds=expires_in - 60),
        }
        logger.info("[Airtel] Token OAuth renouvelé.")
        return token
    except requests.RequestException as e:
        raise AirtelAPIError(f"Impossible d'obtenir le token Airtel : {e}") from e


def initier_paiement(
    reference: str,
    telephone: str,
    montant: float,
    description: str = "Abonnement PaieGabon",
) -> dict:
    """
    Lance une demande de paiement STK Push vers le téléphone du client.

    Args:
        reference  : identifiant unique de la transaction (ex: "PAY-2026-001")
        telephone  : numéro Airtel Money sans le "+" (ex: "24107123456")
        montant    : montant en FCFA (entier, pas de centimes)
        description: libellé affiché sur le téléphone du client

    Returns:
        dict avec les clés :
          - success (bool)
          - transaction_id (str) — ID Airtel à stocker
          - message (str)
          - raw (dict) — réponse brute de l'API
    """
    token = _get_access_token()
    telephone = _normaliser_telephone(telephone)
    montant_int = int(round(montant))

    url = f"{BASE_URL}/merchant/v2/payments/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "X-Country":     COUNTRY_CODE,
        "X-Currency":    CURRENCY,
    }
    payload = {
        "reference": description,
        "subscriber": {
            "country": COUNTRY_CODE,
            "currency": CURRENCY,
            "msisdn": telephone,
        },
        "transaction": {
            "amount":    montant_int,
            "country":   COUNTRY_CODE,
            "currency":  CURRENCY,
            "id":        reference,
        },
    }

    logger.info(f"[Airtel] Initiation paiement {reference} — {montant_int} XAF → {telephone}")

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        data = resp.json()
        logger.debug(f"[Airtel] Réponse initiation : {data}")

        # Airtel renvoie status.code = "DP00800001006" pour succès (demande envoyée)
        status = data.get("status", {})
        code   = status.get("code", "")
        msg    = status.get("message", "Erreur inconnue")

        if resp.status_code in (200, 201) and code in ("DP00800001006", "200", "SUCCESS"):
            txn_id = (
                data.get("data", {}).get("transaction", {}).get("id")
                or data.get("transaction", {}).get("id")
                or reference
            )
            logger.info(f"[Airtel] Demande envoyée — transaction_id={txn_id}")
            return {
                "success":        True,
                "transaction_id": txn_id,
                "message":        "Demande de paiement envoyée sur le téléphone du client.",
                "raw":            data,
            }
        else:
            logger.warning(f"[Airtel] Échec initiation : code={code} msg={msg}")
            return {
                "success":        False,
                "transaction_id": None,
                "message":        _traduire_erreur(code, msg),
                "raw":            data,
            }

    except (requests.RequestException, Exception) as e:
        logger.error(f"[Airtel] Erreur réseau : {e}")
        return {
            "success":        False,
            "transaction_id": None,
            "message":        f"Erreur de connexion Airtel : {e}",
            "raw":            {},
        }


def verifier_statut(transaction_id: str) -> dict:
    """
    Interroge le statut d'une transaction Airtel (polling).
    Utilisé si le webhook n'arrive pas ou pour vérification manuelle.

    Returns:
        dict avec statut : "SUCCESS" | "PENDING" | "FAILED" | "EXPIRED"
    """
    token = _get_access_token()
    url = f"{BASE_URL}/standard/v1/payments/{transaction_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Country":     COUNTRY_CODE,
        "X-Currency":    CURRENCY,
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        data = resp.json()
        logger.debug(f"[Airtel] Statut {transaction_id} : {data}")

        statut_api = (
            data.get("data", {}).get("transaction", {}).get("status", "")
            or data.get("status", {}).get("code", "")
        ).upper()

        mapping = {
            "TS":              "SUCCESS",
            "SUCCESS":         "SUCCESS",
            "TF":              "FAILED",
            "FAILED":          "FAILED",
            "TE":              "EXPIRED",
            "EXPIRED":         "EXPIRED",
            "DP00800001006":   "PENDING",
            "PENDING":         "PENDING",
        }
        statut = mapping.get(statut_api, "PENDING")

        return {
            "success":        statut == "SUCCESS",
            "statut":         statut,
            "transaction_id": transaction_id,
            "message":        data.get("status", {}).get("message", ""),
            "raw":            data,
        }
    except requests.RequestException as e:
        logger.error(f"[Airtel] Erreur vérification statut {transaction_id} : {e}")
        return {"success": False, "statut": "ERREUR", "transaction_id": transaction_id,
                "message": str(e), "raw": {}}


def valider_signature_webhook(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Vérifie la signature HMAC-SHA256 du webhook Airtel (header 'X-Airtel-Signature').

    SÉCURITÉ — fail-closed en production :
      • Si le secret est absent ET qu'on est en production → REJET (return False).
      • L'acceptation sans signature n'est tolérée qu'en dev/sandbox, pour les tests.

    Returns True si la signature est valide, False sinon.
    """
    if not AIRTEL_WEBHOOK_SECRET:
        est_production = (
            os.environ.get("RAILWAY_ENVIRONMENT") is not None
            or os.environ.get("AIRTEL_ENV") == "production"
        )
        if est_production:
            logger.error(
                "[Airtel] AIRTEL_WEBHOOK_SECRET absent en PRODUCTION — webhook REJETÉ. "
                "Configurez la variable d'environnement immédiatement."
            )
            return False
        logger.warning("[Airtel] AIRTEL_WEBHOOK_SECRET absent (dev) — signature non vérifiée.")
        return True  # Dev/sandbox uniquement

    if not signature_header:
        return False

    expected = hmac.new(
        AIRTEL_WEBHOOK_SECRET.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _normaliser_telephone(tel: str) -> str:
    """
    Normalise un numéro de téléphone gabonais pour l'API Airtel.
    Exemples :
      "+241 07 12 34 56" → "24107123456"
      "07 12 34 56"      → "24107123456"
      "24107123456"      → "24107123456"
    """
    tel = "".join(c for c in tel if c.isdigit())
    if tel.startswith("00241"):
        tel = tel[5:]
    elif tel.startswith("241"):
        tel = tel[3:]
    # Ajouter indicatif Gabon
    if not tel.startswith("241"):
        tel = "241" + tel
    return tel


def _traduire_erreur(code: str, message: str) -> str:
    """Traduit les codes d'erreur Airtel en messages lisibles."""
    traductions = {
        "DP00800001001": "Solde insuffisant sur le compte Airtel Money.",
        "DP00800001002": "Numéro de téléphone invalide ou non enregistré sur Airtel Money.",
        "DP00800001003": "Transaction refusée par le client (annulation sur le téléphone).",
        "DP00800001004": "Délai d'attente dépassé — le client n'a pas répondu.",
        "DP00800001005": "Limite de transaction atteinte (montant ou fréquence).",
        "ESB000033":     "Service temporairement indisponible. Réessayez dans quelques minutes.",
    }
    return traductions.get(code, f"Erreur Airtel [{code}] : {message}")


# ── Exceptions ─────────────────────────────────────────────────────────────────

class AirtelConfigError(Exception):
    """Variables d'environnement Airtel manquantes."""

class AirtelAPIError(Exception):
    """Erreur de communication avec l'API Airtel."""
