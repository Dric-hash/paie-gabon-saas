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
import urllib.request
import urllib.error
from flask import current_app

logger = logging.getLogger("paiegabon.interco")

# Délai maximal d'attente d'une réponse de la caisse (secondes). Court exprès :
# on ne veut pas qu'un paiement « attende » la caisse.
_TIMEOUT = 4


def proposer_ecriture(tenant, *, source_ref, montant, motif,
                      compte_suggere="6611", sens="SORTIE", date_operation=None):
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
