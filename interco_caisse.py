"""interco_caisse.py — Client d'interconnexion PaieGabon → Caisse Ameriack.

Quand un paiement est enregistré (bulletin salarié ou feuille journalier),
PaieGabon envoie une PROPOSITION de sortie de caisse à l'application Caisse via
son API sécurisée. La caisse la reçoit « en attente » et l'utilisateur la valide.

Principe de robustesse fondamental : cet appel ne doit JAMAIS faire échouer un
paiement. Toute erreur (caisse injoignable, timeout, config absente) est
capturée et journalisée sans être propagée. L'interconnexion est un confort,
pas une dépendance de la paie.
"""
import json
import logging
import re
import unicodedata
import urllib.request
import urllib.error
from flask import current_app

logger = logging.getLogger("paiegabon.interco")

# Délai maximal d'attente d'une réponse de la caisse (secondes). Court exprès :
# on ne veut pas qu'un paiement « attende » la caisse.
_TIMEOUT = 4


# Comptes SYSCOHADA proposés à la caisse
COMPTE_ACOMPTE = "421"        # Personnel, avances et acomptes
COMPTE_PRESTATAIRES = "401PREST"  # Avances sur paie des journaliers / prestataires

# Modes de paiement compris par la Caisse (elle propose la caisse, la banque ou le mobile money)
MODES_CAISSE = {"ESPECES", "VIREMENT", "CHEQUE", "MOBILE_MONEY"}


def _normaliser(texte):
    t = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", t)


def compte_fournisseur(prestataire):
    """Compte fournisseur auxiliaire « 401 » + identifiant du prestataire (12 caractères au plus,
    limite de la Caisse) : le sigle d'abord (TOTAL → 401TOTAL), sinon le code s'il a été saisi
    à la main, sinon la raison sociale (PETRO GABON → 401PETROGABO)."""
    code = _normaliser(getattr(prestataire, "code", ""))
    if re.fullmatch(r"PRE\d+", code):          # code attribué automatiquement : sans valeur comptable
        code = ""
    ident = (_normaliser(getattr(prestataire, "sigle", "")) or code
             or _normaliser(getattr(prestataire, "raison_sociale", "")))
    return ("401" + ident)[:12] if ident else COMPTE_PRESTATAIRES


def annuler_ecriture(tenant, source_ref):
    """L'objet a été annulé ou supprimé dans PaieGabon : la Caisse retire la proposition
    encore en attente, ou la signale si elle était déjà validée. Jamais d'exception."""
    try:
        if not current_app.config.get("CAISSE_INTERCO_ACTIF"):
            return False
        url = current_app.config.get("CAISSE_URL", "")
        token = current_app.config.get("CAISSE_TOKEN", "")
        ref = getattr(tenant, "external_ref", None)
        if not url or not token or not ref:
            return False
        req = urllib.request.Request(
            url + "/api/interco/ecriture/annuler", method="POST",
            data=json.dumps({"tenant_ref": ref, "source_app": "paiegabon",
                             "source_ref": str(source_ref)}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Interco-Token": token})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.getcode() in (200, 201)
    except Exception as e:
        logger.warning("Annulation caisse échouée pour %s : %s", source_ref, e)
        return False


def proposer_ecriture(tenant, *, source_ref, montant, motif,
                      compte_suggere="6611", sens="SORTIE", date_operation=None,
                      mode_paiement=None, libelle_compte=None):
    """Propose une sortie de caisse à l'app Caisse. Retourne True si l'appel a
    abouti (créé ou déjà reçu), False sinon. N'élève jamais d'exception.

    Paramètres :
      tenant         : l'objet Tenant (doit porter un external_ref)
      source_ref     : identifiant unique de l'objet source (anti-doublon)
      montant        : montant net à décaisser (float, > 0)
      motif          : libellé lisible (nom + période)
      compte_suggere : numéro SYSCOHADA suggéré (661/6611 pour les salaires)
      sens           : "SORTIE" (défaut) ou "ENTREE"
      date_operation : date du paiement (date ou "YYYY-MM-DD") ; défaut = aujourd'hui
      mode_paiement  : ESPECES | VIREMENT | CHEQUE | MOBILE_MONEY (la caisse propose le bon compte)
      libelle_compte : libellé du compte tiers à créer dans la caisse (ex. « TOTAL GABON » pour 401TOTAL)
    """
    try:
        if not current_app.config.get("CAISSE_INTERCO_ACTIF"):
            return False
        url = current_app.config.get("CAISSE_URL", "")
        token = current_app.config.get("CAISSE_TOKEN", "")
        if not url or not token:
            logger.info("Interco caisse non configurée (URL ou token absent) — ignoré.")
            return False
        ref = getattr(tenant, "external_ref", None)
        if not ref:
            logger.warning("Tenant %s sans external_ref — proposition caisse ignorée.",
                           getattr(tenant, "id", "?"))
            return False
        try:
            montant = float(montant)
        except (TypeError, ValueError):
            return False
        if montant <= 0:
            # Un net nul ou négatif (ex. entièrement absorbé par les avances)
            # n'a pas à générer d'écriture de caisse.
            return False

        payload = {
            "tenant_ref": ref,
            "source_app": "paiegabon",
            "source_ref": str(source_ref),
            "sens": sens,
            "montant": round(montant, 2),
            "motif": (motif or "")[:200],
            "compte_suggere": compte_suggere,
            "devise": "XAF",
        }
        if mode_paiement in MODES_CAISSE:
            payload["mode_paiement"] = mode_paiement
        if libelle_compte:
            payload["libelle_compte"] = str(libelle_compte)[:150]
        # Date de paiement (facultative) : la caisse la reprend telle quelle.
        if date_operation is not None:
            try:
                # Accepte un objet date/datetime ou une chaîne "YYYY-MM-DD"
                if hasattr(date_operation, "strftime"):
                    payload["date_operation"] = date_operation.strftime("%Y-%m-%d")
                else:
                    payload["date_operation"] = str(date_operation)[:10]
            except Exception:
                pass
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url + "/api/interco/ecriture", data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "X-Interco-Token": token})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            code = resp.getcode()
            if code in (200, 201):
                logger.info("Proposition caisse OK (%s) pour %s.", code, source_ref)
                return True
            logger.warning("Réponse caisse inattendue (%s) pour %s.", code, source_ref)
            return False
    except urllib.error.HTTPError as e:
        logger.warning("Interco caisse HTTP %s pour %s : %s",
                       e.code, source_ref, e.reason)
        return False
    except Exception as e:
        # Toute autre erreur (réseau, timeout, DNS…) : on log et on continue.
        logger.warning("Interco caisse échouée pour %s : %s", source_ref, e)
        return False
