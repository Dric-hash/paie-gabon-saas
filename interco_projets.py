"""interco_projets.py — PaieGabon → Projets Ameriack : coût de la main-d'œuvre des chantiers.

Chaque site de PaieGabon peut être relié à un chantier de Projets. Chaque mois, le coût de la
main-d'œuvre du site (salariés : coût employeur des bulletins validés ; journaliers : feuilles de
paie) est envoyé à Projets comme dépense du chantier, poste « Main-d'œuvre ». L'envoi est
idempotent (référence stable par mois, site et catégorie) : renvoyer un mois met à jour les
montants ; un montant nul ou un site détaché retire la dépense.

Robustesse : un appel ne lève jamais d'exception ; il renvoie (réponse, erreur).
Configuration (.env) : PROJETS_URL, PROJETS_TOKEN (= INTERCO_TOKEN de Projets). L'entreprise doit
porter la même référence d'interconnexion (external_ref) dans les deux applications.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

logger = logging.getLogger("paiegabon.interco_projets")
_TIMEOUT = 6


def configure(tenant):
    return bool(current_app.config.get("PROJETS_URL") and current_app.config.get("PROJETS_TOKEN")
                and getattr(tenant, "external_ref", None))


def _appel(tenant, chemin, payload=None, params=None):
    """(corps JSON, None) ou (corps|None, message d'erreur). Jamais d'exception."""
    if not configure(tenant):
        return None, "interconnexion avec Projets non configurée"
    url = current_app.config["PROJETS_URL"] + chemin
    entetes = {"X-Interco-Token": current_app.config["PROJETS_TOKEN"], "Accept": "application/json"}
    data = None
    if payload is not None:
        payload = dict(payload, tenant_ref=tenant.external_ref)
        data = json.dumps(payload).encode("utf-8")
        entetes["Content-Type"] = "application/json"
    else:
        url += "?" + urllib.parse.urlencode(dict(params or {}, tenant_ref=tenant.external_ref))
    req = urllib.request.Request(url, data=data, headers=entetes, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8") or "{}"), None
    except urllib.error.HTTPError as e:
        try:
            corps = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            corps = {}
        logger.warning("Projets : HTTP %s sur %s", e.code, chemin)
        return corps, corps.get("erreur") or f"Projets a répondu {e.code}"
    except Exception as e:                       # réseau, délai, JSON illisible
        logger.warning("Projets injoignable (%s) : %s", chemin, e)
        return None, "Projets ne répond pas"


def chantiers(tenant):
    """Liste des chantiers de Projets [{id, nom, statut_label}] ou None si indisponible."""
    corps, err = _appel(tenant, "/api/interco/chantiers", params={"tous": "1"})
    if err or not corps or not corps.get("ok"):
        return None
    return corps.get("chantiers") or []


def envoyer_depense(tenant, ref, chantier_id, montant, jour, libelle):
    """Crée ou met à jour la dépense « ref » ; montant nul ou chantier absent : la retire."""
    if not chantier_id or round(montant or 0) <= 0:
        return _appel(tenant, "/api/interco/depense", {"source_app": "paie", "source_ref": ref, "supprimer": True})
    return _appel(tenant, "/api/interco/depense", {
        "source_app": "paie", "source_ref": ref, "chantier_id": int(chantier_id), "montant": round(montant),
        "date": jour.isoformat(), "poste": "Main-d'œuvre", "libelle": libelle[:200]})
