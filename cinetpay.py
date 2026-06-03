"""
cinetpay.py — Module de paiement CinetPay pour PaieGabon SaaS
==============================================================
CinetPay est un agrégateur couvrant Airtel Money, Moov Money,
cartes Visa/Mastercard — un seul contrat, tous les opérateurs.

Flux :
  1. PaieGabon initie → CinetPay retourne une URL de paiement
  2. Client redirigé vers la page CinetPay (hébergée par eux)
  3. Client choisit son moyen (Mobile Money ou carte)
  4. CinetPay rappelle notre webhook /webhook/cinetpay
  5. On vérifie et active l'abonnement

Documentation : https://docs.cinetpay.com

Variables d'environnement :
  CINETPAY_API_KEY  — dashboard CinetPay → Mon Compte → API
  CINETPAY_SITE_ID  — dashboard CinetPay → Mon Compte → API
  CINETPAY_ENV      — TEST ou PROD
  APP_BASE_URL      — URL publique Railway (ex: https://amenack-paie.up.railway.app)
"""

import os
import logging
import requests

logger = logging.getLogger("paiegalon.cinetpay")

CINETPAY_API_KEY  = os.environ.get("CINETPAY_API_KEY", "")
CINETPAY_SITE_ID  = os.environ.get("CINETPAY_SITE_ID", "")
CINETPAY_ENV      = os.environ.get("CINETPAY_ENV", "TEST")
APP_BASE_URL      = os.environ.get("APP_BASE_URL", "http://localhost:5000")
CINETPAY_BASE_URL = "https://api-checkout.cinetpay.com/v2"
CURRENCY          = "XAF"


def initier_paiement(reference, montant, description="Abonnement PaieGabon",
                     nom_client="", email_client="", telephone_client=""):
    """
    Crée une session de paiement CinetPay.
    Retourne une payment_url vers laquelle rediriger le client.
    """
    if not CINETPAY_API_KEY or not CINETPAY_SITE_ID:
        raise CinetPayConfigError(
            "CINETPAY_API_KEY et CINETPAY_SITE_ID doivent être définis.")

    montant_int = int(round(montant))
    if montant_int < 100:
        return {"success": False, "payment_url": None, "payment_token": None,
                "message": "Montant minimum : 100 XAF.", "raw": {}}

    payload = {
        "apikey":           CINETPAY_API_KEY,
        "site_id":          CINETPAY_SITE_ID,
        "transaction_id":   reference,
        "amount":           montant_int,
        "currency":         CURRENCY,
        "description":      description,
        "return_url":       f"{APP_BASE_URL}/paiement/cinetpay/retour",
        "notify_url":       f"{APP_BASE_URL}/webhook/cinetpay",
        "cancel_url":       f"{APP_BASE_URL}/paiement",
        "customer_name":    nom_client[:50] if nom_client else "",
        "customer_email":   email_client[:100] if email_client else "",
        "customer_phone_number": _normaliser_telephone(telephone_client) if telephone_client else "",
        "customer_address": "Libreville, Gabon",
        "customer_city":    "Libreville",
        "customer_country": "GA",
        "customer_state":   "GA",
        "customer_zip_code": "BP000",
        "channels":         "ALL",
        "lang":             "fr",
        "metadata":         reference,
    }

    logger.info(f"[CinetPay] Initiation {reference} — {montant_int} XAF")
    try:
        resp = requests.post(f"{CINETPAY_BASE_URL}/payment", json=payload, timeout=20)
        data = resp.json()
        logger.debug(f"[CinetPay] Réponse : {data}")
        code = str(data.get("code", ""))
        if code == "201":
            pd = data.get("data", {})
            logger.info(f"[CinetPay] Session créée — token={pd.get('payment_token','')[:20]}…")
            return {"success": True, "payment_url": pd.get("payment_url", ""),
                    "payment_token": pd.get("payment_token", ""),
                    "message": "Page de paiement prête.", "raw": data}
        return {"success": False, "payment_url": None, "payment_token": None,
                "message": _traduire_erreur(code, data.get("message", "")), "raw": data}
    except Exception as e:
        logger.error(f"[CinetPay] Erreur : {e}")
        return {"success": False, "payment_url": None, "payment_token": None,
                "message": f"Erreur de connexion CinetPay : {e}", "raw": {}}


def verifier_statut(transaction_id):
    """
    Vérifie le statut d'une transaction CinetPay.
    Retourne dict avec statut : ACCEPTED | PENDING | REFUSED | CANCELLED
    """
    payload = {"apikey": CINETPAY_API_KEY, "site_id": CINETPAY_SITE_ID,
               "transaction_id": transaction_id}
    try:
        resp = requests.post(f"{CINETPAY_BASE_URL}/payment/check", json=payload, timeout=15)
        data = resp.json()
        logger.debug(f"[CinetPay] Statut {transaction_id} : {data}")
        statut = (data.get("data", {}).get("status", "") or "").upper()
        mapping = {"ACCEPTED": "ACCEPTED", "PENDING": "PENDING",
                   "REFUSED": "REFUSED", "CANCELLED": "CANCELLED", "FAILED": "REFUSED"}
        s = mapping.get(statut, "PENDING")
        return {"success": s == "ACCEPTED", "statut": s, "transaction_id": transaction_id,
                "montant": data.get("data", {}).get("amount"),
                "operateur": data.get("data", {}).get("payment_method", ""),
                "message": data.get("message", ""), "raw": data}
    except Exception as e:
        logger.error(f"[CinetPay] Erreur statut {transaction_id} : {e}")
        return {"success": False, "statut": "ERREUR", "transaction_id": transaction_id,
                "message": str(e), "raw": {}}


def valider_webhook(data):
    """
    Vérifie que le webhook vient bien de CinetPay
    en comparant le site_id reçu avec notre configuration.
    """
    site_id_recu = str(data.get("cpm_site_id", "") or data.get("site_id", ""))
    if not site_id_recu:
        logger.warning("[CinetPay] Webhook sans site_id — rejeté.")
        return False
    if site_id_recu != str(CINETPAY_SITE_ID):
        logger.warning(f"[CinetPay] site_id invalide : {site_id_recu} ≠ {CINETPAY_SITE_ID}")
        return False
    return True


def _normaliser_telephone(tel):
    tel = "".join(c for c in tel if c.isdigit())
    if tel.startswith("00241"): tel = tel[5:]
    elif tel.startswith("241"): tel = tel[3:]
    return f"+241{tel}"


def _traduire_erreur(code, message):
    t = {
        "400": "Paramètres invalides.",
        "401": "Clé API invalide. Vérifiez CINETPAY_API_KEY.",
        "403": "Accès refusé. Vérifiez votre compte CinetPay.",
        "500": "Erreur serveur CinetPay. Réessayez.",
        "600": "Montant invalide (minimum 100 XAF).",
        "623": "Référence déjà utilisée.",
    }
    return t.get(code, f"Erreur CinetPay [{code}] : {message}")


class CinetPayConfigError(Exception):
    pass

class CinetPayAPIError(Exception):
    pass
