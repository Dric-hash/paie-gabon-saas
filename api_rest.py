"""
api_rest.py — API REST PaieGabon pour grandes entreprises
==========================================================
Permet aux grandes entreprises d'intégrer PaieGabon à leur SIRH.

Authentification supportée :
  1. Token API fixe  : Header "X-API-Key: <token>"
  2. OAuth2 (client_credentials) : POST /api/v1/oauth/token

Endpoints disponibles :
  Lecture :
    GET  /api/v1/salaries              — liste des salariés
    GET  /api/v1/salaries/<id>         — détail d'un salarié
    GET  /api/v1/periodes              — liste des périodes de paie
    GET  /api/v1/bulletins             — bulletins (filtrables)
    GET  /api/v1/bulletins/<id>        — détail d'un bulletin
    GET  /api/v1/stats                 — statistiques de paie
    GET  /api/v1/me                    — infos sur le tenant authentifié

  Écriture :
    POST /api/v1/salaries              — créer un salarié
    PUT  /api/v1/salaries/<id>         — modifier un salarié
    POST /api/v1/periodes              — créer une période
    POST /api/v1/bulletins/calculer    — calculer un bulletin
    POST /api/v1/bulletins/<id>/valider — valider un bulletin

  OAuth2 :
    POST /api/v1/oauth/token           — obtenir un access token
    POST /api/v1/oauth/revoke          — révoquer un token

Toutes les réponses sont en JSON avec la structure :
  { "success": true, "data": {...}, "meta": {"page": 1, ...} }
  { "success": false, "error": "MESSAGE", "code": "ERROR_CODE" }

Rate limiting : 100 requêtes/minute par token (configurable)
"""

import os
import hmac
import hashlib
import logging
import secrets
import functools
from datetime import datetime, timedelta

logger = logging.getLogger("paiegalon.api")

# ── Configuration ──────────────────────────────────────────────────────────────
API_RATE_LIMIT     = int(os.environ.get("API_RATE_LIMIT",     "100"))   # req/min
OAUTH_TOKEN_TTL    = int(os.environ.get("OAUTH_TOKEN_TTL",    "3600"))  # secondes
API_MAX_PAGE_SIZE  = int(os.environ.get("API_MAX_PAGE_SIZE",  "100"))

# Cache simple en mémoire pour les tokens OAuth (remplacé par Redis si dispo)
_oauth_tokens: dict = {}  # token → {tenant_id, expires_at, scopes}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS AUTH
# ═══════════════════════════════════════════════════════════════════════════════

def _get_tenant_from_request(request, Tenant):
    """
    Authentifie la requête API via Token fixe ou OAuth2.
    Retourne le Tenant ou None.

    Priorité :
      1. Header X-API-Key
      2. Header Authorization: Bearer <token>

    Note sécurité : le passage du token via query string (?api_key=) a été
    retiré — il fuiterait dans les logs serveur, l'historique et les en-têtes
    Referer.
    """
    token = (
        request.headers.get("X-API-Key")
        or _extract_bearer(request.headers.get("Authorization", ""))
    )
    if not token:
        return None, "AUTH_MISSING"

    # ── Token API fixe ────────────────────────────────────────────────────────
    tenant = Tenant.query.filter_by(token_api=token, statut="ACTIF").first()
    if tenant:
        return tenant, None

    # ── Token OAuth2 ─────────────────────────────────────────────────────────
    oauth_entry = _oauth_tokens.get(token)
    if oauth_entry:
        if datetime.utcnow() < oauth_entry["expires_at"]:
            tenant = Tenant.query.filter_by(
                id=oauth_entry["tenant_id"], statut="ACTIF"
            ).first()
            if tenant:
                return tenant, None
        else:
            del _oauth_tokens[token]
            return None, "TOKEN_EXPIRED"

    return None, "TOKEN_INVALID"


def _extract_bearer(auth_header: str) -> str:
    """Extrait le token d'un header 'Authorization: Bearer <token>'."""
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return ""


def api_auth_required(f):
    """Décorateur qui vérifie l'authentification API et injecte le tenant."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        from flask import request, jsonify
        from models import Tenant
        tenant, err = _get_tenant_from_request(request, Tenant)
        if not tenant:
            return jsonify(_err(err or "AUTH_FAILED", "Authentification requise.", 401)), 401
        return f(tenant, *args, **kwargs)
    return wrapper


def _paginate(query, request, default_per_page=25):
    """Applique la pagination à une requête SQLAlchemy."""
    page     = max(1, request.args.get("page",     1,  type=int))
    per_page = min(API_MAX_PAGE_SIZE,
                   max(1, request.args.get("per_page", default_per_page, type=int)))
    result   = query.paginate(page=page, per_page=per_page, error_out=False)
    return result, {
        "page":       page,
        "per_page":   per_page,
        "total":      result.total,
        "pages":      result.pages,
        "has_next":   result.has_next,
        "has_prev":   result.has_prev,
    }


def _ok(data, meta=None, status=200):
    from flask import jsonify
    resp = {"success": True, "data": data}
    if meta:
        resp["meta"] = meta
    return jsonify(resp), status


def _err(code: str, message: str, status=400):
    return {"success": False, "error": message, "code": code}


# ── Sérialisation ──────────────────────────────────────────────────────────────

def _salarie_dict(s, detail=False):
    d = {
        "id":            s.id,
        "matricule":     s.matricule,
        "nom":           s.nom,
        "prenom":        s.prenom,
        "nom_complet":   s.nom_complet,
        "emploi":        s.emploi,
        "categorie":     s.categorie.libelle if s.categorie else None,
        "statut":        s.statut,
        "date_embauche": str(s.date_embauche) if s.date_embauche else None,
    }
    if detail:
        d.update({
            "email":              s.email,
            "telephone":          s.telephone,
            "sexe":               s.sexe,
            "nationalite":        s.nationalite,
            "situation_matrimoniale": s.situation_matrimoniale,
            "nb_enfants":         s.nb_enfants,
            "nombre_parts":       float(s.nombre_parts) if s.nombre_parts else 1.0,
            "numero_cnss":        s.numero_cnss,
            "numero_cnamgs":      s.numero_cnamgs,
            "date_naissance":     str(s.date_naissance) if s.date_naissance else None,
            "date_cessation":     str(s.date_cessation) if s.date_cessation else None,
        })
    return d


def _bulletin_dict(b, detail=False):
    d = {
        "id":             b.id,
        "salarie_id":     b.salarie_id,
        "salarie":        b.salarie.nom_complet if b.salarie else None,
        "matricule":      b.salarie.matricule if b.salarie else None,
        "periode_id":     b.periode_id,
        "periode":        b.periode.libelle_complet if b.periode else None,
        "statut":         b.statut,
        "salaire_brut":   float(b.salaire_brut or 0),
        "net_a_payer":    float(b.net_a_payer or 0),
        "date_creation":  str(b.date_creation) if b.date_creation else None,
    }
    if detail:
        d.update({
            "salaire_base":     float(b.salaire_base or 0),
            "cnss_salarie":     float(b.cnss_salarie or 0),
            "cnss_patronale":   float(b.cnss_patronale or 0),
            "cnamgs_salarie":   float(b.cnamgs_salarie or 0),
            "cnamgs_patronale": float(b.cnamgs_patronale or 0),
            "tcs":              float(b.tcs or 0),
            "irpp":             float(b.irpp or 0),
            "fnh":              float(b.fnh or 0),
            "cfp":              float(b.cfp or 0),
            "acompte":          float(b.acompte or 0),
            "salaire_net":      float(b.salaire_net or 0),
            "date_validation":  str(b.date_validation) if b.date_validation else None,
        })
    return d


def _periode_dict(p):
    return {
        "id":       p.id,
        "annee":    p.annee,
        "mois":     p.mois,
        "libelle":  p.libelle_complet if hasattr(p, "libelle_complet") else f"{p.libelle_mois} {p.annee}",
        "statut":   p.statut,
    }
