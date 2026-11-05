"""blueprints/compte_ameriack.py — « Se connecter avec mon compte Ameriack » (compte.ameriack.com) et page
« Applications liées » de PaieGabon.

Le compte Ameriack est le compte unique de la suite. À la connexion, PaieGabon reçoit l'identité de la
personne et de son entreprise, dont la référence Ameriack : elle devient la référence d'interconnexion de
l'entreprise (Tenant.external_ref) ; la personne est reliée par Utilisateur.external_ref = « ameriack:<sub> ».

Règles propres à la paie (données sensibles) :
  - personne déjà reliée                → connexion (la double vérification PaieGabon s'applique toujours) ;
  - accès PaieGabon avec la même adresse → la personne confirme avec son mot de passe PaieGabon, une fois ;
  - personne sans accès PaieGabon        → AUCUN accès créé automatiquement : l'administrateur PaieGabon de
                                            l'entreprise crée l'accès (et choisit le rôle), puis la personne
                                            se relie ;
  - entreprise inconnue de PaieGabon     → son administrateur Ameriack crée l'espace (essai de 30 jours) ;
  - le super-admin PaieGabon ne se connecte jamais par cette voie.
La connexion par e-mail et mot de passe reste possible.

Configuration (.env) : AMERIACK_URL, AMERIACK_CLIENT_ID, AMERIACK_CLIENT_SECRET, AMERIACK_RETOUR.
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user

from audit import log_action
from models import db, utcnow, Plan, Tenant, Utilisateur, CategorieEmploi

bp = Blueprint("compte_ameriack", __name__)
logger = logging.getLogger("paiegabon.compte_ameriack")
_TIMEOUT = 8
PREFIXE = "ameriack:"


@bp.app_context_processor
def _ctx():
    try:
        return {"AMERIACK_ACTIF": configure()}
    except Exception:
        return {"AMERIACK_ACTIF": False}


def configure():
    c = current_app.config
    return bool(c.get("AMERIACK_URL") and c.get("AMERIACK_CLIENT_ID") and c.get("AMERIACK_CLIENT_SECRET")
                and c.get("AMERIACK_RETOUR"))


def _echanger(code, verificateur):
    c = current_app.config
    donnees = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code, "redirect_uri": c["AMERIACK_RETOUR"],
        "client_id": c["AMERIACK_CLIENT_ID"], "client_secret": c["AMERIACK_CLIENT_SECRET"],
        "code_verifier": verificateur}).encode()
    req = urllib.request.Request(c["AMERIACK_URL"] + "/oauth/token", data=donnees, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            corps = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("Compte Ameriack : échange refusé (HTTP %s).", e.code)
        return None, "le compte Ameriack a refusé la connexion (recommencez)"
    except Exception as e:
        logger.warning("Compte Ameriack injoignable : %s", e)
        return None, "le compte Ameriack ne répond pas"
    ident = corps.get("utilisateur") or {}
    if not ident.get("sub") or not ident.get("email") or not (ident.get("organisation") or {}).get("reference"):
        return None, "réponse incomplète du compte Ameriack"
    return ident, None


def _cle(ident):
    return PREFIXE + ident["sub"]


def _rattacher_entreprise(t, ident, admin):
    ref = ident["organisation"]["reference"]
    if t.external_ref == ref:
        return None
    autre = Tenant.query.filter(Tenant.external_ref == ref, Tenant.id != t.id).first()
    if autre is not None:
        return (f"l'entreprise Ameriack {ident['organisation']['nom']} est déjà reliée à une autre entreprise "
                f"PaieGabon ({autre.denomination})")
    if not admin:
        return (f"{t.denomination} n'est pas encore reliée à l'entreprise Ameriack : demandez à l'administrateur "
                "PaieGabon de se connecter d'abord avec son compte Ameriack")
    ancienne, t.external_ref = t.external_ref, ref
    if ancienne:
        flash(f"Référence d'interconnexion de {t.denomination} : {ancienne} → {ref} (celle de votre compte Ameriack).",
              "warning")
    return None


def _relier(u, ident):
    deja = Utilisateur.query.filter(Utilisateur.external_ref == _cle(ident), Utilisateur.id != u.id).first()
    if deja is not None:
        return f"ce compte Ameriack est déjà relié à l'accès {deja.email}"
    if u.is_super_admin:
        return "le super-administrateur PaieGabon ne se relie pas à un compte Ameriack d'entreprise"
    if u.tenant is not None:
        e = _rattacher_entreprise(u.tenant, ident, u.is_tenant_admin)
        if e:
            return e
    u.external_ref = _cle(ident)
    return None


def _envoyer_code(u):
    """Code de double vérification PaieGabon par e-mail (même message que la connexion classique)."""
    from flask_mail import Message
    from core import send_email_async
    code = f"{secrets.randbelow(1000000):06d}"
    u.set_otp(code)
    db.session.commit()
    try:
        msg = Message(subject="🔐 Votre code de connexion — PaieGabon", recipients=[u.email],
                      html=(f"<p>Bonjour {u.prenom},</p><p>Votre code de connexion est :</p>"
                            f"<p style='font-size:28px;font-weight:bold;letter-spacing:4px'>{code}</p>"
                            "<p>Ce code expire dans 10 minutes. Si vous n'êtes pas à l'origine de cette connexion, "
                            "changez votre mot de passe immédiatement.</p>"),
                      sender=current_app.config["MAIL_DEFAULT_SENDER"])
        send_email_async(current_app.extensions["mail"], msg)
    except Exception as e:
        current_app.logger.error(f"[2FA EMAIL ERROR] {e}")


def _ouvrir(u):
    """Ouvre la session ; la double vérification PaieGabon de la personne s'applique comme d'habitude."""
    session.pop("ameriack_identite", None)
    if getattr(u, "twofa_active", False):
        methode = getattr(u, "twofa_methode", "email") or "email"
        if methode == "email" and os.environ.get("MAIL_PASSWORD"):
            _envoyer_code(u)
        elif methode != "totp" or not getattr(u, "twofa_secret", None):
            methode = None
        if methode:
            db.session.commit()
            session["2fa_user_id"], session["post_login_next"] = u.id, ""
            return redirect(url_for("auth.verifier_2fa"))
    u.derniere_connexion = utcnow()
    u.nb_echecs_connexion, u.compte_bloque_jusqu = 0, None
    db.session.commit()
    log_action("LOGIN", "utilisateur", u.id, f"Connexion par le compte Ameriack de {u.nom_complet} ({u.role_label})",
               user_id=u.id, tenant_id=u.tenant_id)
    db.session.commit()
    login_user(u)
    return redirect(url_for("auth.index"))


# ── Connexion ─────────────────────────────────────────────────────────────────
@bp.route("/connexion/ameriack")
def connexion():
    if not configure():
        flash("La connexion avec le compte Ameriack n'est pas encore configurée.", "warning")
        return redirect(url_for("auth.login"))
    etat, verif = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    session["ameriack_etat"], session["ameriack_verif"] = etat, verif
    defi = base64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).rstrip(b"=").decode()
    c = current_app.config
    return redirect(c["AMERIACK_URL"] + "/oauth/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": c["AMERIACK_CLIENT_ID"], "redirect_uri": c["AMERIACK_RETOUR"],
        "state": etat, "code_challenge": defi, "code_challenge_method": "S256"}))


@bp.route("/connexion/ameriack/retour")
def retour():
    echec = redirect(url_for("compte_ameriack.liees") if current_user.is_authenticated else url_for("auth.login"))
    etat, verif = session.pop("ameriack_etat", None), session.pop("ameriack_verif", None)
    if request.args.get("error"):
        flash("Connexion avec le compte Ameriack annulée.", "warning")
        return echec
    if not etat or not secrets.compare_digest(etat, request.args.get("state", "")):
        flash("Connexion expirée ou invalide : recommencez.", "error")
        return echec
    ident, err = _echanger(request.args.get("code", ""), verif or "")
    if err:
        flash(f"Connexion impossible : {err}.", "error")
        return echec
    org = ident["organisation"]

    if current_user.is_authenticated:                     # liaison depuis « Applications liées »
        e = _relier(current_user, ident)
        if e:
            db.session.rollback()
            flash(f"Liaison impossible : {e}.", "error")
        else:
            db.session.commit()
            flash(f"Votre accès PaieGabon est relié au compte Ameriack de {org['nom']}.", "success")
        return redirect(url_for("compte_ameriack.liees"))

    u = Utilisateur.query.filter_by(external_ref=_cle(ident)).first()
    if u is not None:
        if not u.actif or u.is_super_admin:
            flash("Cet accès PaieGabon est désactivé : voyez avec votre administrateur.", "error")
            return echec
        if u.tenant is not None:
            e = _rattacher_entreprise(u.tenant, ident, u.is_tenant_admin)
            if e:
                db.session.rollback()
                flash(f"Connexion impossible : {e}.", "error")
                return echec
        return _ouvrir(u)

    session["ameriack_identite"] = ident
    if Utilisateur.query.filter_by(email=ident["email"].lower()).first() is not None:
        return redirect(url_for("compte_ameriack.relier"))
    t = Tenant.query.filter_by(external_ref=org["reference"]).first()
    if t is not None or ident.get("role") != "ADMIN":
        session.pop("ameriack_identite", None)
        flash(f"Vous n'avez pas encore d'accès PaieGabon avec l'adresse {ident['email']}. Pour protéger les données de "
              f"paie, l'administrateur PaieGabon de {org['nom']} doit d'abord vous créer un accès (Paramètres → "
              "Utilisateurs) avec cette adresse ; reconnectez-vous ensuite avec votre compte Ameriack.", "warning")
        return echec
    return redirect(url_for("compte_ameriack.creer"))


@bp.route("/connexion/ameriack/relier", methods=["GET", "POST"])
def relier():
    ident = session.get("ameriack_identite")
    u = Utilisateur.query.filter_by(email=ident["email"].lower()).first() if ident else None
    if u is None:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        if request.form.get("annuler"):
            session.pop("ameriack_identite", None)
            return redirect(url_for("auth.login"))
        now = utcnow()
        if u.compte_bloque_jusqu and now < u.compte_bloque_jusqu:
            flash("Compte temporairement bloqué : réessayez plus tard.", "error")
        elif u.actif and u.check_password(request.form.get("password", "")):
            e = _relier(u, ident)
            if e:
                db.session.rollback()
                session.pop("ameriack_identite", None)
                flash(f"Liaison impossible : {e}.", "error")
                return redirect(url_for("auth.login"))
            log_action("UPDATE", "utilisateur", u.id, "Accès relié au compte Ameriack", user_id=u.id, tenant_id=u.tenant_id)
            flash("Votre accès PaieGabon est maintenant relié à votre compte Ameriack : la prochaine fois, un clic suffit.",
                  "success")
            return _ouvrir(u)
        else:
            u.nb_echecs_connexion = (u.nb_echecs_connexion or 0) + 1
            if u.nb_echecs_connexion >= int(os.environ.get("LOGIN_MAX_ECHECS", "5")):
                u.compte_bloque_jusqu = now + timedelta(minutes=int(os.environ.get("LOGIN_BLOCAGE_MIN", "15")))
                u.nb_echecs_connexion = 0
            db.session.commit()
            flash("Mot de passe incorrect.", "error")
        return redirect(url_for("compte_ameriack.relier"))
    return render_template("compte_ameriack/relier.html", ident=ident, u=u)


TYPES = {"ENTREPRISE": "Entreprise (je fais ma propre paie)", "CABINET": "Cabinet (je gère la paie de clients)",
         "MISE_A_DISPOSITION": "Mise à disposition de personnel"}


@bp.route("/connexion/ameriack/creer", methods=["GET", "POST"])
def creer():
    """L'administrateur d'une entreprise Ameriack ouvre PaieGabon pour la première fois (essai de 30 jours,
    comme l'inscription classique)."""
    ident = session.get("ameriack_identite")
    if not ident or ident.get("role") != "ADMIN":
        return redirect(url_for("auth.login"))
    org = ident["organisation"]
    if Tenant.query.filter_by(external_ref=org["reference"]).first() or \
            Utilisateur.query.filter_by(email=ident["email"].lower()).first():
        return redirect(url_for("compte_ameriack.connexion"))
    if request.method == "POST":
        if not request.form.get("cgu"):
            flash("Acceptez les conditions générales d'utilisation.", "error")
            return redirect(url_for("compte_ameriack.creer"))
        tc = request.form.get("type_compte") if request.form.get("type_compte") in TYPES else "ENTREPRISE"
        denom = (org.get("nom") or "").strip().upper()[:200]
        base = denom.lower().replace(" ", "_")[:30] or "entreprise"
        slug, i = base, 1
        while Tenant.query.filter_by(slug=slug).first():
            slug, i = f"{base}_{i}", i + 1
        plan = Plan.query.filter_by(code="STARTER").first()
        t = Tenant(slug=slug, denomination=denom, nif=(org.get("nif") or "").strip(), telephone=(org.get("telephone") or "").strip(),
                   ville=org.get("ville") or "Libreville", pays="Gabon", plan_id=plan.id if plan else None, statut="ESSAI",
                   date_expiration=utcnow() + timedelta(days=30), external_ref=org["reference"])
        t.type_compte, t.est_cabinet = tc, tc == "CABINET"
        t.generate_token()
        db.session.add(t)
        db.session.flush()
        for code, lib in [("C1", "Ouvriers"), ("C2", "Techniciens"), ("C3", "Conducteurs de Travaux"), ("C4", "Cadres")]:
            db.session.add(CategorieEmploi(tenant_id=t.id, code=code, libelle=lib))
        nom = (ident.get("nom") or ident["email"]).strip()
        prenom, _, reste = nom.partition(" ")
        u = Utilisateur(nom=(reste or prenom).upper()[:100], prenom=(prenom if reste else "")[:100] or "-",
                        email=ident["email"].lower(), role="TENANT_ADMIN", tenant_id=t.id, actif=True,
                        email_verifie=bool(ident.get("email_verifie")), external_ref=_cle(ident))
        u.set_password(secrets.token_urlsafe(32))     # pas de mot de passe connu : connexion par le compte Ameriack
        db.session.add(u)
        db.session.commit()
        log_action("CREATE", "tenant", t.id, f"Entreprise créée depuis le compte Ameriack ({t.external_ref})",
                   user_id=u.id, tenant_id=t.id)
        flash(f"Bienvenue ! L'espace PaieGabon de {t.denomination} est créé : essai gratuit de 30 jours.", "success")
        return _ouvrir(u)
    return render_template("compte_ameriack/creer.html", ident=ident, org=org, types=TYPES)


# ── Paramètres : applications liées ───────────────────────────────────────────
@bp.route("/parametres/applications-liees")
@login_required
def liees():
    if current_user.is_super_admin or current_user.tenant is None:
        abort(404)
    t = current_user.tenant
    relies = Utilisateur.query.filter(Utilisateur.tenant_id == t.id, Utilisateur.external_ref.like(PREFIXE + "%")).count()
    return render_template("compte_ameriack/liees.html", t=t, configure=configure(), relies=relies,
                           total=Utilisateur.query.filter_by(tenant_id=t.id).count(),
                           moi_relie=bool((current_user.external_ref or "").startswith(PREFIXE)),
                           url_compte=current_app.config.get("AMERIACK_URL") or "https://compte.ameriack.com")
