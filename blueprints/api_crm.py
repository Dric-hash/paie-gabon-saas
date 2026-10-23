# -*- coding: utf-8 -*-
"""blueprints/api_crm.py — API commerciale en LECTURE SEULE pour Ameriack CRM.

GET /api/crm/v1/comptes  → une ligne par entreprise cliente (inscription,
statut, offre, échéance, paiements, activité). Aucune donnée de paie
nominative (aucun salarié, aucun salaire).

Sécurité
  - désactivée tant que CRM_API_KEY n'est pas défini dans le .env (404) ;
  - clé dans l'en-tête X-CRM-Key, comparée à temps constant ;
  - par défaut, accessible UNIQUEMENT depuis le serveur lui-même (le CRM
    appelle http://127.0.0.1:<port> sans passer par nginx) : toute requête
    qui a traversé un proxy (X-Forwarded-For / X-Real-IP) est refusée.
    CRM_API_LOCAL_SEULEMENT=0 lève cette restriction (CRM hébergé ailleurs).
"""
import hmac
import logging
import os

from flask import Blueprint, abort, jsonify, request
from sqlalchemy import func

from models import (db, utcnow, Tenant, Plan, Utilisateur, Salarie, BulletinPaie, PeriodePaie, Paiement,
                    EditionDAS)

logger = logging.getLogger("paiegalon")
bp = Blueprint("api_crm", __name__, url_prefix="/api/crm/v1")
VERSION = 1


def _autoriser():
    cle = os.environ.get("CRM_API_KEY", "").strip()
    if not cle:
        abort(404)
    if os.environ.get("CRM_API_LOCAL_SEULEMENT", "1").strip() not in ("0", "non", "false"):
        local = request.remote_addr in ("127.0.0.1", "::1")
        via_proxy = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP")
        if not local or via_proxy:
            logger.warning(f"[API CRM] Accès refusé (non local) depuis {request.remote_addr}")
            abort(403)
    fournie = request.headers.get("X-CRM-Key", "")
    if not fournie or not hmac.compare_digest(fournie.encode(), cle.encode()):
        logger.warning("[API CRM] Clé invalide")
        abort(401)


def _iso(d):
    return d.isoformat() if d else None


@bp.route("/comptes")
def comptes():
    _autoriser()
    tenants = Tenant.query.order_by(Tenant.id).all()
    plans = {p.id: p for p in Plan.query.all()}

    # Agrégats en quelques requêtes (pas de N+1)
    admins = {}
    for u in (Utilisateur.query.filter(Utilisateur.tenant_id.isnot(None))
              .order_by(Utilisateur.id).all()):
        r = admins.setdefault(u.tenant_id, {"contact": None, "nb": 0, "derniere": None})
        r["nb"] += 1
        if u.is_tenant_admin and r["contact"] is None:
            r["contact"] = u
        if u.derniere_connexion and (r["derniere"] is None or u.derniere_connexion > r["derniere"]):
            r["derniere"] = u.derniere_connexion
    salaries = dict(db.session.query(Salarie.tenant_id, func.count(Salarie.id))
                    .filter(Salarie.statut == "ACTIF").group_by(Salarie.tenant_id).all())
    bulletins = dict(db.session.query(BulletinPaie.tenant_id, func.count(BulletinPaie.id))
                     .group_by(BulletinPaie.tenant_id).all())
    derniere_paie = {}
    for tid, annee, mois in (db.session.query(PeriodePaie.tenant_id, PeriodePaie.annee, PeriodePaie.mois)
                             .join(BulletinPaie, BulletinPaie.periode_id == PeriodePaie.id)
                             .group_by(PeriodePaie.tenant_id, PeriodePaie.annee, PeriodePaie.mois).all()):
        if tid not in derniere_paie or (annee, mois) > derniere_paie[tid]:
            derniere_paie[tid] = (annee, mois)
    paiements = {}
    for p in Paiement.query.order_by(Paiement.date_creation).all():
        r = paiements.setdefault(p.tenant_id, {"total_encaisse": 0.0, "nombre": 0, "dernier_le": None,
                                               "dernier_montant": None, "en_attente": 0})
        if (p.statut or "").upper() == "SUCCES":
            r["total_encaisse"] += float(p.montant or 0)
            r["nombre"] += 1
            r["dernier_le"] = _iso(p.date_confirmation or p.date_creation)
            r["dernier_montant"] = float(p.montant or 0)
        elif (p.statut or "").upper() == "EN_ATTENTE":
            r["en_attente"] += 1
    das = {}
    for e in EditionDAS.query.filter_by(statut="PAYEE").all():
        das.setdefault(e.tenant_id, []).append(e.annee)

    sortie = []
    for t in tenants:
        pl = plans.get(t.plan_id)
        a = admins.get(t.id, {})
        c = a.get("contact")
        dp = derniere_paie.get(t.id)
        type_compte = "CABINET" if t.est_cabinet else ("ENTREPRISE_DE_CABINET" if t.cabinet_id else "ENTREPRISE")
        sortie.append({
            "id": str(t.id),
            "nom": t.denomination,
            "sigle": t.sigle,
            "ville": t.ville,
            "telephone": t.telephone,
            "nom_contact": (f"{c.prenom or ''} {c.nom or ''}".strip() if c else None),
            "email_contact": (c.email if c else None),
            "statut": t.statut,
            "type": type_compte,
            "rattache_a": (str(t.cabinet_id) if t.cabinet_id else None),
            "offre": ({"code": pl.code, "nom": pl.nom, "prix_mensuel": float(pl.prix_mensuel or 0)} if pl else None),
            "date_inscription": _iso(t.date_inscription),
            "date_expiration": _iso(t.date_expiration),
            "derniere_activite": _iso(a.get("derniere")),
            "indicateurs": {
                "utilisateurs": a.get("nb", 0),
                "salaries_actifs": salaries.get(t.id, 0),
                "bulletins": bulletins.get(t.id, 0),
                "derniere_paie": (f"{dp[0]}-{dp[1]:02d}" if dp else None),
            },
            "paiements": paiements.get(t.id, {"total_encaisse": 0.0, "nombre": 0, "dernier_le": None,
                                               "dernier_montant": None, "en_attente": 0}),
            "options": {
                "das_payees": sorted(das.get(t.id, [])),
                "liaison_etats_financiers": bool(getattr(t, "liaison_paie_autorisee", False)),
                "portail_employes": bool(getattr(t, "portail_salaries_actif", False)),
            },
        })
    return jsonify({"application": "PAIEGABON", "version": VERSION, "genere_le": utcnow().isoformat(),
                    "comptes": sortie})
