# -*- coding: utf-8 -*-
"""blueprints/espace.py — Portail des employés (/espace), installable en PWA.

Session propre au portail (clé « espace » de la session Flask), indépendante
de la connexion des utilisateurs de l'entreprise (Flask-Login) : un salarié
connecté au portail n'a accès à AUCUNE page de gestion.
"""
import functools
import logging
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for, flash, session,
                   Response, send_from_directory, current_app, g, abort)

import portail_salaries as ps
from models import db, utcnow, Tenant, ConsultationBulletin

logger = logging.getLogger("paiegalon")

bp = Blueprint("espace", __name__, url_prefix="/espace")
CLE = "espace"

# Anti force brute : par adresse IP (large : opérateurs mobiles derrière une même IP)
# et par couple entreprise/matricule ; le verrouillage de l'accès (5 essais) s'y ajoute.
_LIMITES_IP = "30/minute"
_LIMITES_COMPTE = "10/minute;30/hour"


def _trop_de_tentatives(code_entreprise: str, matricule: str) -> bool:
    from core import get_limiter
    lim = get_limiter()
    if not lim or not getattr(lim, "enabled", True):
        return False
    try:
        from limits import parse_many
        controles = [(_LIMITES_IP, ("espace-ip", request.remote_addr or "?"))]
        if code_entreprise and matricule:
            controles.append((_LIMITES_COMPTE, ("espace-compte", f"{code_entreprise}|{matricule}".lower())))
        for limites, cle in controles:
            for item in parse_many(limites):
                if not lim.limiter.hit(item, *cle):
                    logger.warning(f"[ESPACE] Trop de tentatives ({cle[0]})")
                    return True
    except Exception as e:   # le limiteur ne doit jamais bloquer le service
        logger.warning(f"[ESPACE] Limiteur indisponible : {e}")
    return False


# ── Session du portail ─────────────────────────────────────────────────────

def _ouvrir(acces):
    session[CLE] = {"a": acces.id, "v": acces.version, "t": utcnow().isoformat()}


def _fermer():
    session.pop(CLE, None)


def _courant():
    """(tenant, salarie, acces) de la session, ou None si elle n'est plus valable."""
    from models import AccesSalarie
    d = session.get(CLE)
    if not isinstance(d, dict):
        return None
    try:
        derniere = datetime.fromisoformat(d.get("t"))
    except (TypeError, ValueError):
        return None
    if (utcnow() - derniere).total_seconds() > ps.INACTIVITE_MINUTES * 60:
        return None
    a = db.session.get(AccesSalarie, d.get("a"))
    if a is None or not a.actif or a.version != d.get("v") or not a.pin_hash:
        return None
    s = a.salarie
    t = db.session.get(Tenant, a.tenant_id)
    if s is None or s.tenant_id != a.tenant_id or (s.statut or "ACTIF").upper() != "ACTIF" \
            or not ps.portail_ouvert(t):
        return None
    d["t"] = utcnow().isoformat()
    session[CLE] = d
    return t, s, a


def connexion_requise(f):
    @functools.wraps(f)
    def d(*args, **kwargs):
        c = _courant()
        if c is None:
            expiree = CLE in session
            _fermer()
            if expiree:
                flash("Votre session a expiré. Saisissez à nouveau votre code PIN.", "info")
            return redirect(url_for("espace.accueil"))
        g.esp_tenant, g.esp_salarie, g.esp_acces = c
        return f(*args, **kwargs)
    return d


@bp.after_request
def _entetes(resp):
    # Données personnelles : jamais en cache partagé, jamais dans l'historique hors-ligne
    if request.endpoint not in ("espace.manifest", "espace.service_worker"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ── PWA ─────────────────────────────────────────────────────────────────────

@bp.route("/manifest.webmanifest")
def manifest():
    return send_from_directory("static/espace", "manifest.webmanifest", mimetype="application/manifest+json")


@bp.route("/sw.js")
def service_worker():
    resp = send_from_directory("static/espace", "sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/espace"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/hors-ligne")
def hors_ligne():
    return render_template("espace/hors_ligne.html")


# ── Connexion ───────────────────────────────────────────────────────────────

@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
def accueil():
    if _courant() is not None:
        return redirect(url_for("espace.mes_bulletins"))
    return render_template("espace/connexion.html", code_entreprise=(request.args.get("e") or "").strip().lower())


@bp.route("/connexion", methods=["POST"])
def connexion():
    f = request.form
    code_e = (f.get("code_entreprise") or "").strip().lower()
    mat = (f.get("matricule") or "").strip()
    pin = (f.get("pin") or "").strip()
    retour = url_for("espace.accueil", e=code_e or None)
    if _trop_de_tentatives(code_e, mat):
        flash("Trop de tentatives. Patientez quelques minutes avant de réessayer.", "error")
        return redirect(retour)
    t, s, a = ps.trouver(code_e, mat)
    if t is not None and not ps.portail_ouvert(t):
        flash("Le portail des employés n'est pas activé par votre employeur.", "error")
        return redirect(retour)
    if ps.verrouille(a):
        flash(f"Trop d'erreurs : accès bloqué {ps.VERROU_MINUTES} minutes. Réessayez plus tard.", "error")
        return redirect(retour)
    ok = ps.verifier_pin(a, pin)
    if not ok or a is None or not a.actif or s is None or (s.statut or "ACTIF").upper() != "ACTIF":
        if a is not None and a.actif:
            ps.echec(a)
            db.session.commit()
        flash("Code entreprise, matricule ou code PIN incorrect, ou accès pas encore activé.", "error")
        return redirect(retour)
    a.echecs = 0
    a.verrou_jusqua = None
    a.derniere_connexion = utcnow()
    db.session.commit()
    _ouvrir(a)
    logger.info(f"[ESPACE] Connexion salarié {s.id} (tenant {t.id})")
    return redirect(url_for("espace.mes_bulletins"))


@bp.route("/activer", methods=["GET", "POST"])
def activer():
    if request.method == "GET":
        return render_template("espace/activer.html", code_entreprise=(request.args.get("e") or "").strip().lower(),
                               matricule=(request.args.get("m") or "").strip())
    f = request.form
    code_e = (f.get("code_entreprise") or "").strip().lower()
    mat = (f.get("matricule") or "").strip()
    code = f.get("code_activation") or ""
    pin, pin2 = (f.get("pin") or "").strip(), (f.get("pin2") or "").strip()
    retour = url_for("espace.activer", e=code_e or None, m=mat or None)
    if _trop_de_tentatives(code_e, mat):
        flash("Trop de tentatives. Patientez quelques minutes avant de réessayer.", "error")
        return redirect(retour)
    t, s, a = ps.trouver(code_e, mat)
    if t is not None and not ps.portail_ouvert(t):
        flash("Le portail des employés n'est pas activé par votre employeur.", "error")
        return redirect(retour)
    if ps.verrouille(a):
        flash(f"Trop d'erreurs : accès bloqué {ps.VERROU_MINUTES} minutes. Réessayez plus tard.", "error")
        return redirect(retour)
    if not ps.verifier_code(a, code) or not a.actif or s is None or (s.statut or "ACTIF").upper() != "ACTIF":
        if a is not None and a.actif:
            ps.echec(a)
            db.session.commit()
        flash("Code d'activation incorrect ou expiré. Demandez un nouveau code à votre service RH.", "error")
        return redirect(retour)
    if pin != pin2:
        flash("Les deux codes PIN saisis sont différents.", "error")
        return redirect(retour)
    motif = ps.pin_refuse(pin, s)
    if motif:
        flash(f"Code PIN refusé : {motif}.", "error")
        return redirect(retour)
    if not f.get("consentement"):
        flash("Vous devez accepter de recevoir vos bulletins sous forme électronique.", "error")
        return redirect(retour)
    ps.definir_pin(a, pin)
    a.consentement_le = a.consentement_le or utcnow()
    a.derniere_connexion = utcnow()
    from audit import log_action
    log_action("UPDATE", "acces_salarie", a.id, f"Portail des employés : accès activé par le salarié {s.matricule}",
               tenant_id=t.id)
    db.session.commit()
    _ouvrir(a)
    flash("Votre accès est activé. Retenez bien votre code PIN.", "success")
    return redirect(url_for("espace.mes_bulletins"))


@bp.route("/deconnexion", methods=["POST"])
def deconnexion():
    _fermer()
    return redirect(url_for("espace.accueil"))


# ── Espace connecté ─────────────────────────────────────────────────────────

@bp.route("/bulletins")
@connexion_requise
def mes_bulletins():
    s = g.esp_salarie
    liste = ps.bulletins(s)
    recap = ps.recapitulatif(liste)
    annee = request.args.get("annee", type=int)
    annees = [r["annee"] for r in recap]
    if annee not in annees:
        annee = annees[0] if annees else None
    vus = {c.bulletin_id for c in ConsultationBulletin.query.filter_by(salarie_id=s.id).all()}
    return render_template("espace/bulletins.html", t=g.esp_tenant, s=s, liste=[b for b in liste if b.periode.annee == annee],
                           recap=next((r for r in recap if r["annee"] == annee), None), annees=annees, annee=annee,
                           vus=vus, libelle=ps.libelle)


@bp.route("/bulletins/<int:bulletin_id>")
@connexion_requise
def bulletin_voir(bulletin_id):
    """Bulletin dans le modèle choisi par l'employeur (le même que celui du RH).
    ?imprimer=1 ouvre directement l'impression (« Enregistrer en PDF » sur le téléphone)."""
    s, t = g.esp_salarie, g.esp_tenant
    b = ps.bulletin(s, bulletin_id)
    if b is None:
        abort(404)
    from blueprints.tenant import rendu_bulletin_html
    page = rendu_bulletin_html(b, t, portail=True, retour_url=url_for("espace.mes_bulletins",
                               annee=b.periode.annee), imprimer=bool(request.args.get("imprimer")))
    # Téléphone : le bulletin est une page A4 ; on l'affiche en entier dans la
    # largeur de l'écran (le salarié peut zoomer). L'impression reste en A4.
    import re as _re
    vp = '<meta name="viewport" content="width=840, user-scalable=yes">'
    page = (_re.sub(r'<meta[^>]+name="viewport"[^>]*>', vp, page, count=1) if 'name="viewport"' in page
            else page.replace("<head>", "<head>\n" + vp, 1))
    db.session.add(ConsultationBulletin(tenant_id=t.id, salarie_id=s.id, bulletin_id=b.id,
                                        ip=(request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                                            or request.remote_addr or "")[:45]))
    db.session.commit()
    return page


@bp.route("/bulletins/<int:bulletin_id>.pdf")
@connexion_requise
def bulletin_pdf(bulletin_id):
    """PDF du bulletin au modèle de l'entreprise (le même PDF que celui du RH)."""
    s, t = g.esp_salarie, g.esp_tenant
    b = ps.bulletin(s, bulletin_id)
    if b is None:
        abort(404)
    from blueprints.tenant import _pdf_bulletin_bytes
    try:
        data = _pdf_bulletin_bytes(b, t)
    except Exception as e:
        logger.error(f"[ESPACE] PDF bulletin {b.id} : {e}")
        flash("Le PDF n'a pas pu être généré. Utilisez « Voir » puis « Imprimer / Enregistrer PDF ».", "error")
        return redirect(url_for("espace.mes_bulletins"))
    db.session.add(ConsultationBulletin(tenant_id=t.id, salarie_id=s.id, bulletin_id=b.id,
                                        ip=(request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                                            or request.remote_addr or "")[:45]))
    db.session.commit()
    nom = f"bulletin_{b.periode.annee}_{b.periode.mois:02d}_{s.matricule}.pdf".replace(" ", "_").replace("/", "-")
    resp = Response(data, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'{"attachment" if request.args.get("dl") else "inline"}; filename="{nom}"'
    return resp


@bp.route("/pin", methods=["GET", "POST"])
@connexion_requise
def changer_pin():
    s, a = g.esp_salarie, g.esp_acces
    if request.method == "POST":
        f = request.form
        if not ps.verifier_pin(a, (f.get("pin_actuel") or "").strip()):
            ps.echec(a)
            db.session.commit()
            if ps.verrouille(a):
                _fermer()
                flash(f"Trop d'erreurs : accès bloqué {ps.VERROU_MINUTES} minutes.", "error")
                return redirect(url_for("espace.accueil"))
            flash("Code PIN actuel incorrect.", "error")
            return redirect(url_for("espace.changer_pin"))
        pin, pin2 = (f.get("pin") or "").strip(), (f.get("pin2") or "").strip()
        if pin != pin2:
            flash("Les deux nouveaux codes PIN sont différents.", "error")
            return redirect(url_for("espace.changer_pin"))
        motif = ps.pin_refuse(pin, s)
        if motif:
            flash(f"Code PIN refusé : {motif}.", "error")
            return redirect(url_for("espace.changer_pin"))
        ps.definir_pin(a, pin)
        db.session.commit()
        _ouvrir(a)           # la version a changé : les autres appareils sont déconnectés
        flash("Code PIN modifié. Vos autres appareils sont déconnectés.", "success")
        return redirect(url_for("espace.mes_bulletins"))
    return render_template("espace/pin.html", t=g.esp_tenant, s=s)


# ── Congés ──────────────────────────────────────────────────────────────────

@bp.route("/conges", methods=["GET", "POST"])
@connexion_requise
def mes_conges():
    t, s = g.esp_tenant, g.esp_salarie
    if request.method == "POST":
        try:
            c, jours = ps.demander_conge(t, s, request.form)
            db.session.flush()
            from audit import log_action
            log_action("CREATE", "conge", c.id,
                       f"Portail des employés : demande de congé {s.matricule} ({jours} j) — "
                       f"{c.date_depart.strftime('%d/%m/%Y')}→{c.date_retour.strftime('%d/%m/%Y')}", tenant_id=t.id)
            db.session.commit()
            flash(f"Demande envoyée ({jours} jour(s)). Votre service RH va la traiter.", "success")
        except ValueError as e:
            db.session.rollback()
            flash(f"Demande impossible : {e}.", "error")
        return redirect(url_for("espace.mes_conges"))
    from conges_avance import solde_annee
    from datetime import date as _date
    annee = _date.today().year
    return render_template("espace/conges.html", t=t, s=s, solde=solde_annee(t, s, annee),
                           conges=ps.conges(s), TYPES=ps.TYPES_CONGE, STATUTS=ps.STATUTS_CONGE,
                           aujourd_hui=_date.today().isoformat())


@bp.route("/conges/<int:conge_id>/annuler", methods=["POST"])
@connexion_requise
def annuler_conge(conge_id):
    from models import Conge
    t, s = g.esp_tenant, g.esp_salarie
    c = Conge.query.filter_by(id=conge_id, tenant_id=t.id, salarie_id=s.id).first_or_404()
    if (c.statut or "").upper() not in ("DEMANDÉ", "DEMANDE") or c.date_depart is None:
        flash("Seule une demande encore en attente peut être annulée.", "error")
        return redirect(url_for("espace.mes_conges"))
    from audit import log_action
    log_action("DELETE", "conge", c.id, f"Portail des employés : demande de congé annulée par {s.matricule}",
               tenant_id=t.id)
    db.session.delete(c)
    db.session.commit()
    flash("Demande annulée.", "success")
    return redirect(url_for("espace.mes_conges"))


# ── Attestations ────────────────────────────────────────────────────────────

@bp.route("/documents", methods=["GET", "POST"])
@connexion_requise
def mes_documents():
    from models import DemandeDocument
    from audit import log_action
    t, s = g.esp_tenant, g.esp_salarie
    if request.method == "POST":
        try:
            d = ps.demander_document(t, s, request.form)
            if t.portail_attestations_auto:
                ps.delivrer(d, t, "Délivrance automatique")
            db.session.flush()
            log_action("CREATE", "demande_document", d.id,
                       f"Portail des employés : {ps.TYPES_DOCUMENT[d.type_doc].lower()} demandée par {s.matricule}"
                       + (" (délivrée automatiquement)" if d.statut == "DELIVRE" else ""), tenant_id=t.id)
            db.session.commit()
            flash("Votre attestation est prête." if d.statut == "DELIVRE"
                  else "Demande envoyée. Vous pourrez télécharger l'attestation dès que le RH l'aura délivrée.",
                  "success")
        except ValueError as e:
            db.session.rollback()
            flash(f"Demande impossible : {e}.", "error")
        return redirect(url_for("espace.mes_documents"))
    demandes = (DemandeDocument.query.filter_by(tenant_id=t.id, salarie_id=s.id)
                .order_by(DemandeDocument.id.desc()).limit(30).all())
    return render_template("espace/documents.html", t=t, s=s, demandes=demandes, TYPES=ps.TYPES_DOCUMENT,
                           STATUTS=ps.STATUTS_DOCUMENT)


@bp.route("/documents/<int:doc_id>.pdf")
@connexion_requise
def document_pdf(doc_id):
    from models import DemandeDocument
    t, s = g.esp_tenant, g.esp_salarie
    d = DemandeDocument.query.filter_by(id=doc_id, tenant_id=t.id, salarie_id=s.id, statut="DELIVRE").first()
    if d is None or not d.pdf:
        abort(404)
    nom = f"{d.type_doc.lower()}_{s.matricule}_{d.date_traitement.strftime('%Y%m%d')}.pdf".replace(" ", "_")
    resp = Response(d.pdf, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'{"attachment" if request.args.get("dl") else "inline"}; filename="{nom}"'
    return resp


# ── Profil ──────────────────────────────────────────────────────────────────

@bp.route("/profil", methods=["GET", "POST"])
@connexion_requise
def mon_profil():
    from models import DemandeProfil
    from audit import log_action
    t, s = g.esp_tenant, g.esp_salarie
    if request.method == "POST":
        try:
            creees = ps.demander_profil(t, s, request.form)
            if not creees:
                flash("Aucune modification à envoyer.", "info")
                return redirect(url_for("espace.mon_profil"))
            log_action("CREATE", "demande_profil", s.id,
                       f"Portail des employés : modification demandée par {s.matricule} — "
                       + ", ".join(ps.CHAMPS_PROFIL[d.champ] for d in creees), tenant_id=t.id)
            db.session.commit()
            flash("Demande envoyée. Votre fiche sera mise à jour après validation par le service RH.", "success")
        except ValueError as e:
            db.session.rollback()
            flash(f"Demande impossible : {e}.", "error")
        return redirect(url_for("espace.mon_profil"))
    demandes = (DemandeProfil.query.filter_by(tenant_id=t.id, salarie_id=s.id)
                .order_by(DemandeProfil.id.desc()).limit(20).all())
    en_attente = {d.champ: d for d in demandes if d.statut == "DEMANDE"}
    return render_template("espace/profil.html", t=t, s=s, demandes=demandes, en_attente=en_attente,
                           CHAMPS=ps.CHAMPS_PROFIL, SITUATIONS=ps.SITUATIONS, STATUTS=ps.STATUTS_PROFIL)
