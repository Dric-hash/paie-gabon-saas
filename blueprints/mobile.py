# -*- coding: utf-8 -*-
"""
blueprints/mobile.py — PWA de pointage chantier (BTP), hors-ligne d'abord.

Authentification légère par code pointeur + PIN (aucun e-mail, aucun mot de
passe) ; un jeton signé (itsdangerous) est renvoyé et stocké sur le téléphone.
Les pointages arrivent en quarantaine (statut SOUMIS) et ne comptent en paie
qu'après validation du RH. La synchronisation est idempotente par uuid_client :
rejouer la file hors-ligne ne crée jamais de doublon.
"""
import math
import logging
from datetime import datetime, date

from flask import (Blueprint, request, jsonify, render_template,
                   current_app, send_from_directory)
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from models import (db, utcnow, Tenant, Salarie, Journalier, Site,
                    AffectationSite, Pointage, PointeurChantier)

logger = logging.getLogger("paiegalon")

bp = Blueprint("mobile", __name__, url_prefix="/m")

_TOKEN_SALT = "pointeur-mobile-v1"
_TOKEN_MAX_AGE = 60 * 60 * 24 * 30   # 30 jours : le chef reste connecté hors-ligne


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_TOKEN_SALT)


def _emettre_token(pointeur):
    return _serializer().dumps({"pid": pointeur.id})


def _pointeur_depuis_token():
    """Retourne le PointeurChantier authentifié par l'en-tête Authorization, ou None."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    if not token:
        token = (request.get_json(silent=True) or {}).get("token")
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    p = PointeurChantier.query.get(data.get("pid"))
    if not p or not p.actif:
        return None
    return p


def _roster(pointeur):
    """Équipe pointable (pour l'offline) : salariés ET journaliers actifs affectés
    au site du pointeur, plus le pointeur lui-même s'il est relié à une fiche."""
    t = pointeur.tenant
    tid = pointeur.tenant_id

    # Salariés
    qs = Salarie.query.filter_by(tenant_id=tid, statut="ACTIF")
    qj = Journalier.query.filter_by(tenant_id=tid, statut="ACTIF")
    if pointeur.site_id:
        affs = AffectationSite.query.filter_by(tenant_id=tid, site_id=pointeur.site_id, actif=True).all()
        sal_ids = [a.salarie_id for a in affs if a.salarie_id]
        jour_ids = [a.journalier_id for a in affs if a.journalier_id]
        # Le pointeur relié est toujours inclus dans sa propre équipe.
        if pointeur.salarie_id:
            sal_ids.append(pointeur.salarie_id)
        if pointeur.journalier_id:
            jour_ids.append(pointeur.journalier_id)
        qs = qs.filter(Salarie.id.in_(sal_ids or [-1]))
        qj = qj.filter(Journalier.id.in_(jour_ids or [-1]))
    salaries = qs.order_by(Salarie.nom, Salarie.prenom).all()
    journaliers = qj.order_by(Journalier.nom, Journalier.prenom).all()
    site = pointeur.site

    equipe = [{"type": "S", "id": s.id, "nom": s.nom, "prenom": s.prenom,
               "matricule": s.matricule, "emploi": s.emploi,
               "est_pointeur": s.id == pointeur.salarie_id} for s in salaries]
    equipe += [{"type": "J", "id": j.id, "nom": j.nom, "prenom": j.prenom,
                "matricule": None, "emploi": j.profession,
                "est_pointeur": j.id == pointeur.journalier_id} for j in journaliers]
    equipe.sort(key=lambda e: (e["nom"] or "", e["prenom"] or ""))

    return {
        "pointeur": {"id": pointeur.id, "nom": pointeur.nom, "code": pointeur.code,
                     "type": ("J" if pointeur.journalier_id else ("S" if pointeur.salarie_id else None)),
                     "worker_id": pointeur.journalier_id or pointeur.salarie_id},
        "entreprise": t.denomination if t else "",
        "site": ({"id": site.id, "nom": site.nom,
                  "latitude": float(site.latitude) if site.latitude is not None else None,
                  "longitude": float(site.longitude) if site.longitude is not None else None,
                  "rayon_m": site.rayon_m or 200} if site else None),
        "convention": (t.convention if t else None),
        "salaries": equipe,   # salariés + journaliers, champ 'type' = S|J
        "serveur": utcnow().isoformat(),
    }


def _distance_m(lat1, lon1, lat2, lon2):
    """Distance approchée (Haversine) en mètres entre deux points GPS."""
    try:
        R = 6371000.0
        p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
        dphi = math.radians(float(lat2) - float(lat1))
        dlmb = math.radians(float(lon2) - float(lon1))
        a = (math.sin(dphi / 2) ** 2
             + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    except (TypeError, ValueError):
        return None


# ──────────────────────────────── Vues PWA ────────────────────────────────

@bp.route("/")
@bp.route("")
def pwa_shell():
    """Coque de l'application (login + pointage), mise en cache par le service worker."""
    return render_template("mobile/app.html")


@bp.route("/manifest.webmanifest")
def manifest():
    return send_from_directory("static/mobile", "manifest.webmanifest",
                               mimetype="application/manifest+json")


@bp.route("/sw.js")
def service_worker():
    # Servi à la racine de /m pour que sa portée couvre toute la PWA.
    resp = send_from_directory("static/mobile", "sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/m"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ──────────────────────────────── API JSON ────────────────────────────────

@bp.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    pin = (data.get("pin") or "").strip()
    if not code or not pin:
        return jsonify({"ok": False, "erreur": "Code et PIN requis."}), 400
    p = PointeurChantier.query.filter_by(code=code, actif=True).first()
    if not p or not p.check_pin(pin):
        return jsonify({"ok": False, "erreur": "Code ou PIN incorrect."}), 401
    p.derniere_connexion = utcnow()
    db.session.commit()
    logger.info(f"[MOBILE] Connexion pointeur {p.code} (tenant {p.tenant_id})")
    return jsonify({"ok": True, "token": _emettre_token(p), **_roster(p)})


@bp.route("/api/roster", methods=["GET", "POST"])
def api_roster():
    p = _pointeur_depuis_token()
    if not p:
        return jsonify({"ok": False, "erreur": "Session expirée."}), 401
    return jsonify({"ok": True, **_roster(p)})


@bp.route("/api/sync", methods=["POST"])
def api_sync():
    """Reçoit la file de pointages du téléphone. Idempotent par uuid_client.

    Entrée : { pointages: [ { uuid, date, salarie_id, present, absent,
               motif_absence, heures_normales, heures_sup_10/30/40/70,
               entree_matin, sortie_matin, entree_apmidi, sortie_apmidi,
               latitude, longitude } ] }
    Sortie : { ok, resultats: [ { uuid, etat: 'cree'|'doublon'|'erreur', ... } ] }
    """
    p = _pointeur_depuis_token()
    if not p:
        return jsonify({"ok": False, "erreur": "Session expirée."}), 401
    data = request.get_json(silent=True) or {}
    items = data.get("pointages") or []
    if not isinstance(items, list):
        return jsonify({"ok": False, "erreur": "Format invalide."}), 400

    # Travailleurs autorisés pour ce pointeur (sécurité : il ne pointe que son
    # équipe) — distingués par type S (salarié) / J (journalier).
    equipe = _roster(p)["salaries"]
    ok_sal = {e["id"] for e in equipe if e["type"] == "S"}
    ok_jour = {e["id"] for e in equipe if e["type"] == "J"}
    site = p.site
    resultats = []

    for it in items:
        uuid = (it.get("uuid") or "").strip()
        if not uuid:
            resultats.append({"uuid": None, "etat": "erreur", "motif": "uuid manquant"})
            continue
        # Idempotence : déjà reçu → on confirme sans recréer.
        existant = Pointage.query.filter_by(tenant_id=p.tenant_id, uuid_client=uuid).first()
        if existant:
            resultats.append({"uuid": uuid, "etat": "doublon", "id": existant.id})
            continue

        # Routage salarié / journalier. On accepte type+worker_id, ou les clés
        # explicites salarie_id / journalier_id (compatibilité).
        kind = (it.get("type") or "").upper()
        wid = it.get("worker_id")
        if it.get("journalier_id") is not None:
            kind, wid = "J", it.get("journalier_id")
        elif it.get("salarie_id") is not None:
            kind, wid = "S", it.get("salarie_id")
        sal_id = wid if kind == "S" else None
        jour_id = wid if kind == "J" else None
        if (kind == "S" and sal_id not in ok_sal) or (kind == "J" and jour_id not in ok_jour) \
                or kind not in ("S", "J"):
            resultats.append({"uuid": uuid, "etat": "erreur", "motif": "travailleur hors équipe"})
            continue
        try:
            d = datetime.strptime(it.get("date"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            resultats.append({"uuid": uuid, "etat": "erreur", "motif": "date invalide"})
            continue

        # Un seul pointage par travailleur/jour (contrainte unique) : si un
        # pointage existe déjà ce jour-là, on ne l'écrase pas — on signale le conflit.
        if kind == "S":
            deja = Pointage.query.filter_by(tenant_id=p.tenant_id, salarie_id=sal_id, date_pointage=d).first()
        else:
            deja = Pointage.query.filter_by(tenant_id=p.tenant_id, journalier_id=jour_id, date_pointage=d).first()
        if deja:
            resultats.append({"uuid": uuid, "etat": "conflit", "id": deja.id,
                              "motif": "pointage déjà existant ce jour"})
            continue

        absent = bool(it.get("absent"))
        lat = it.get("latitude")
        lng = it.get("longitude")
        hors_zone = False
        if (site and site.latitude is not None and site.longitude is not None
                and lat is not None and lng is not None):
            dist = _distance_m(site.latitude, site.longitude, lat, lng)
            if dist is not None and dist > float((site.rayon_m or 200)):
                hors_zone = True

        def _num(k, defaut=0):
            try:
                return float(it.get(k) if it.get(k) is not None else defaut)
            except (TypeError, ValueError):
                return defaut

        pt = Pointage(
            tenant_id=p.tenant_id, salarie_id=sal_id, journalier_id=jour_id,
            date_pointage=d, site_id=p.site_id,
            present=(not absent), absent=absent,
            motif_absence=(it.get("motif_absence") or None) if absent else None,
            heures_normales=_num("heures_normales", 0 if absent else 8),
            heures_sup_10=_num("heures_sup_10"), heures_sup_30=_num("heures_sup_30"),
            heures_sup_40=_num("heures_sup_40"), heures_sup_70=_num("heures_sup_70"),
            entree_matin=(it.get("entree_matin") or None),
            sortie_matin=(it.get("sortie_matin") or None),
            entree_apmidi=(it.get("entree_apmidi") or None),
            sortie_apmidi=(it.get("sortie_apmidi") or None),
            observation=(it.get("observation") or None),
            uuid_client=uuid, source="MOBILE", statut="SOUMIS",
            pointeur_id=p.id,
            latitude=lat, longitude=lng, geo_hors_zone=hors_zone,
            heure_serveur=utcnow(),
        )
        db.session.add(pt)
        try:
            db.session.flush()
            resultats.append({"uuid": uuid, "etat": "cree", "id": pt.id,
                              "hors_zone": hors_zone})
        except Exception as e:                       # pragma: no cover
            db.session.rollback()
            resultats.append({"uuid": uuid, "etat": "erreur", "motif": str(e)[:80]})

    db.session.commit()
    nb_crees = sum(1 for r in resultats if r["etat"] == "cree")
    logger.info(f"[MOBILE] Sync pointeur {p.code} : {nb_crees} créé(s) / {len(items)} reçu(s)")
    return jsonify({"ok": True, "resultats": resultats, "serveur": utcnow().isoformat()})
