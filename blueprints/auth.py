"""
blueprints/auth.py — Authentification : login, inscription, email, mot de passe
"""
import os, secrets as sec
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, session, current_app)
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message

from models import db, Plan, Tenant, Utilisateur, CategorieEmploi
from audit import log_action
from core import get_tenant, send_email_async, validate_password, get_limiter

def _rate_limit(limit_str):
    """Applique un rate limit si le limiter est configuré, sinon no-op."""
    def decorator(f):
        import functools
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            lim = get_limiter()
            if lim:
                return lim.limit(limit_str)(f)(*args, **kwargs)
            return f(*args, **kwargs)
        return wrapper
    return decorator

bp = Blueprint("auth", __name__)

# Hash factice (mot de passe aléatoire) servant à égaliser le temps de réponse
# du login lorsqu'aucun compte ne correspond (anti-énumération par timing).
from werkzeug.security import generate_password_hash as _gph
_DUMMY_PW_HASH = _gph(sec.token_hex(16))


# ── Index ─────────────────────────────────────────────────────────────────────
@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(
            url_for("admin.admin_dashboard") if current_user.is_super_admin
            else url_for("tenant.dashboard")
        )
    # Visiteurs non connectés : page de présentation
    from models import Plan
    plans = Plan.query.filter_by(actif=True).order_by(Plan.prix_mensuel).all()
    return render_template("public/presentation.html", plans=plans)


# ── Login ─────────────────────────────────────────────────────────────────────
@bp.route("/login", methods=["GET", "POST"])
@_rate_limit("20/minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("auth.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        user  = Utilisateur.query.filter_by(email=email, actif=True).first()

        # Anti-énumération : égalise le temps de réponse que le compte existe ou
        # non, en exécutant toujours un hash factice quand l'utilisateur est absent.
        if not user:
            try:
                from werkzeug.security import check_password_hash
                check_password_hash(_DUMMY_PW_HASH, pw or "x")
            except Exception:
                pass

        now          = datetime.utcnow()
        MAX_ECHECS   = int(os.environ.get("LOGIN_MAX_ECHECS",  "5"))
        BLOCAGE_MIN  = int(os.environ.get("LOGIN_BLOCAGE_MIN", "15"))

        if user:
            if user.compte_bloque_jusqu and now < user.compte_bloque_jusqu:
                reste = int((user.compte_bloque_jusqu - now).total_seconds() / 60) + 1
                flash(f"Compte temporairement bloqué. Réessayez dans {reste} min.", "error")
                return render_template("auth/login.html")

            if user.check_password(pw):
                user.nb_echecs_connexion = 0
                user.compte_bloque_jusqu = None
                db.session.commit()

                # ── 2FA par email pour le super-admin ─────────────────────────
                # Si l'email est configuré, on n'ouvre pas la session tout de
                # suite : on envoie un code à 6 chiffres et on demande sa saisie.
                # Soupape d'urgence : DISABLE_SUPERADMIN_2FA=1 dans le .env permet
                # de désactiver temporairement la 2FA si le super-admin est bloqué
                # (ex. problème d'envoi d'email). À retirer une fois le souci réglé.
                _2fa_off = os.environ.get("DISABLE_SUPERADMIN_2FA", "").lower() in ("1", "true", "yes")
                if user.is_super_admin and os.environ.get("MAIL_PASSWORD") and not _2fa_off:
                    code = f"{sec.randbelow(1000000):06d}"
                    user.set_otp(code)
                    db.session.commit()
                    try:
                        mail = current_app.extensions["mail"]
                        msg = Message(
                            subject="🔐 Votre code de connexion — PaieGabon",
                            recipients=[user.email],
                            html=(f"<p>Bonjour {user.prenom},</p>"
                                  f"<p>Votre code de connexion super-admin est :</p>"
                                  f"<p style='font-size:28px;font-weight:bold;letter-spacing:4px'>{code}</p>"
                                  f"<p>Ce code expire dans 10 minutes. "
                                  f"Si vous n'êtes pas à l'origine de cette connexion, "
                                  f"changez votre mot de passe immédiatement.</p>"),
                            sender=current_app.config["MAIL_DEFAULT_SENDER"],
                        )
                        send_email_async(mail, msg)
                    except Exception as e:
                        current_app.logger.error(f"[2FA EMAIL ERROR] {e}")
                    session["2fa_user_id"] = user.id
                    return redirect(url_for("auth.verifier_2fa"))

                # ── Connexion normale (non super-admin) ───────────────────────
                user.derniere_connexion  = now
                db.session.commit()
                log_action("LOGIN", "utilisateur", user.id,
                           f"Connexion de {user.nom_complet} ({user.role_label})",
                           user_id=user.id, tenant_id=user.tenant_id)
                db.session.commit()
                login_user(user)
                return redirect(url_for("auth.index"))
            else:
                user.nb_echecs_connexion = (user.nb_echecs_connexion or 0) + 1
                if user.nb_echecs_connexion >= MAX_ECHECS:
                    user.compte_bloque_jusqu = now + timedelta(minutes=BLOCAGE_MIN)
                    user.nb_echecs_connexion = 0
                    db.session.commit()
                    flash(f"Trop de tentatives. Compte bloqué {BLOCAGE_MIN} minutes.", "error")
                    return render_template("auth/login.html")
                db.session.commit()

        flash("Email ou mot de passe incorrect.", "error")
    return render_template("auth/login.html")


# ── Vérification 2FA (super-admin) ────────────────────────────────────────────
@bp.route("/login/verifier-2fa", methods=["GET", "POST"])
@_rate_limit("10/minute")
def verifier_2fa():
    uid = session.get("2fa_user_id")
    if not uid:
        return redirect(url_for("auth.login"))
    user = Utilisateur.query.filter_by(id=uid, actif=True).first()
    if not user:
        session.pop("2fa_user_id", None)
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        now  = datetime.utcnow()
        MAX_OTP = 5

        # Code expiré ou inexistant
        if not user.otp_code_hash or not user.otp_expiry or now > user.otp_expiry:
            user.clear_otp(); db.session.commit()
            session.pop("2fa_user_id", None)
            flash("Code expiré. Veuillez vous reconnecter.", "error")
            return redirect(url_for("auth.login"))

        if user.check_otp(code):
            user.clear_otp()
            user.derniere_connexion = now
            db.session.commit()
            log_action("LOGIN", "utilisateur", user.id,
                       f"Connexion 2FA de {user.nom_complet} ({user.role_label})",
                       user_id=user.id, tenant_id=user.tenant_id)
            db.session.commit()
            session.pop("2fa_user_id", None)
            login_user(user)
            return redirect(url_for("auth.index"))
        else:
            user.otp_tentatives = (user.otp_tentatives or 0) + 1
            if user.otp_tentatives >= MAX_OTP:
                user.clear_otp(); db.session.commit()
                session.pop("2fa_user_id", None)
                flash("Trop de tentatives. Veuillez vous reconnecter.", "error")
                return redirect(url_for("auth.login"))
            db.session.commit()
            reste = MAX_OTP - user.otp_tentatives
            flash(f"Code incorrect. Il vous reste {reste} tentative(s).", "error")

    return render_template("auth/verifier_2fa.html", email=user.email)


@bp.route("/login/renvoyer-2fa", methods=["POST"])
@_rate_limit("3/minute")
def renvoyer_2fa():
    uid = session.get("2fa_user_id")
    if not uid:
        return redirect(url_for("auth.login"))
    user = Utilisateur.query.filter_by(id=uid, actif=True).first()
    if not user:
        session.pop("2fa_user_id", None)
        return redirect(url_for("auth.login"))
    code = f"{sec.randbelow(1000000):06d}"
    user.set_otp(code)
    db.session.commit()
    try:
        mail = current_app.extensions["mail"]
        msg = Message(
            subject="🔐 Votre nouveau code de connexion — PaieGabon",
            recipients=[user.email],
            html=(f"<p>Bonjour {user.prenom},</p>"
                  f"<p>Votre nouveau code de connexion est :</p>"
                  f"<p style='font-size:28px;font-weight:bold;letter-spacing:4px'>{code}</p>"
                  f"<p>Ce code expire dans 10 minutes.</p>"),
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        send_email_async(mail, msg)
    except Exception as e:
        current_app.logger.error(f"[2FA EMAIL ERROR] {e}")
    flash("Un nouveau code vous a été envoyé.", "success")
    return redirect(url_for("auth.verifier_2fa"))


# ── Inscription ───────────────────────────────────────────────────────────────
@bp.route("/inscription", methods=["GET", "POST"])
@_rate_limit("5/hour")
def inscription():
    plans = Plan.query.filter_by(actif=True).all()
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # ── Validation email ──────────────────────────────────────────────────
        if not email or "@" not in email:
            flash("Adresse email invalide.", "error")
            return render_template("auth/inscription.html", plans=plans)
        if Utilisateur.query.filter_by(email=email).first():
            flash("Email déjà utilisé.", "error")
            return render_template("auth/inscription.html", plans=plans)

        # ── Validation mot de passe ───────────────────────────────────────────
        pw_errors = validate_password(password)
        if pw_errors:
            for err in pw_errors:
                flash(err, "error")
            return render_template("auth/inscription.html", plans=plans)

        plan = (Plan.query.get(request.form.get("plan_id", ""))
                or Plan.query.filter_by(code="STARTER").first())
        denom     = request.form.get("denomination", "").strip()
        slug_base = denom.lower().replace(" ", "_")[:30]
        slug = slug_base
        i = 1
        while Tenant.query.filter_by(slug=slug).first():
            slug = f"{slug_base}_{i}"; i += 1

        t = Tenant(
            slug=slug, denomination=denom.upper(),
            sigle=request.form.get("sigle", "").strip().upper(),
            activite=request.form.get("activite", "").strip(),
            nif=request.form.get("nif", "").strip(),
            telephone=request.form.get("telephone", "").strip(),
            ville=request.form.get("ville", "Libreville"),
            pays="Gabon", plan_id=plan.id if plan else None,
            statut="ESSAI",
            date_expiration=datetime.utcnow() + timedelta(days=30),
        )
        t.generate_token()
        db.session.add(t)
        db.session.flush()

        for code, lib in [("C1","Ouvriers"),("C2","Techniciens"),
                          ("C3","Conducteurs de Travaux"),("C4","Cadres")]:
            db.session.add(CategorieEmploi(tenant_id=t.id, code=code, libelle=lib))

        admin = Utilisateur(
            nom=request.form.get("nom", "").strip().upper(),
            prenom=request.form.get("prenom", "").strip(),
            email=email, role="TENANT_ADMIN", tenant_id=t.id, actif=True,
        )
        admin.set_password(password)

        token_conf = sec.token_urlsafe(32)
        admin.token_confirmation        = token_conf
        admin.token_confirmation_expiry = datetime.utcnow() + timedelta(hours=48)
        admin.email_verifie             = False
        db.session.add(admin)
        db.session.commit()

        lien = url_for("auth.confirmer_email", token=token_conf, _external=True)
        if os.environ.get("MAIL_PASSWORD"):
            from flask_mail import Message as Msg
            mail = current_app.extensions["mail"]
            msg = Msg(
                subject="✅ Confirmez votre inscription — PaieGabon",
                recipients=[email],
                html=f"""
                <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto">
                  <div style="background:#1a2332;padding:1.5rem;border-radius:.75rem .75rem 0 0;text-align:center">
                    <h1 style="color:white;margin:0;font-size:1.25rem">PaieGabon</h1>
                  </div>
                  <div style="background:white;padding:2rem;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 .75rem .75rem">
                    <h2 style="color:#111827;margin:0 0 1rem">Bienvenue, {admin.prenom} !</h2>
                    <p style="color:#6b7280;line-height:1.6">
                      Votre compte <strong>{denom.upper()}</strong> a été créé.<br/>
                      Cliquez ci-dessous pour confirmer votre email.
                    </p>
                    <div style="text-align:center;margin:1.5rem 0">
                      <a href="{lien}" style="background:#1a2332;color:white;padding:.875rem 2rem;
                         border-radius:.75rem;font-weight:700;text-decoration:none">
                        ✅ Confirmer mon email
                      </a>
                    </div>
                    <p style="color:#9ca3af;font-size:.75rem;text-align:center">
                      Ce lien expire dans 48h.
                    </p>
                  </div>
                </div>""",
                sender=current_app.config["MAIL_DEFAULT_SENDER"],
            )
            send_email_async(mail, msg)
            flash(f"Compte créé ! Email de confirmation envoyé à {email}.", "success")
        else:
            admin.email_verifie = True
            db.session.commit()
            flash("Bienvenue ! Essai gratuit de 30 jours activé.", "success")

        login_user(admin)
        return redirect(url_for("tenant.dashboard"))
    return render_template("auth/inscription.html", plans=plans)


# ── Logout ────────────────────────────────────────────────────────────────────
@bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


# ── Confirmation email après inscription ──────────────────────────────────────
@bp.route("/confirmer-email/<token>")
def confirmer_email(token):
    u = Utilisateur.query.filter_by(token_confirmation=token).first()
    if not u:
        flash("Lien de confirmation invalide ou déjà utilisé.", "error")
        return redirect(url_for("auth.login"))
    if u.token_confirmation_expiry and datetime.utcnow() > u.token_confirmation_expiry:
        flash("Ce lien a expiré (48h). Reconnectez-vous pour en demander un nouveau.", "error")
        return redirect(url_for("auth.login"))
    u.email_verifie             = True
    u.token_confirmation        = None
    u.token_confirmation_expiry = None
    db.session.commit()
    flash("✅ Email confirmé ! Votre compte est pleinement activé.", "success")
    if current_user.is_authenticated:
        return redirect(url_for("tenant.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/renvoyer-confirmation")
@login_required
def renvoyer_confirmation():
    u = current_user
    if u.email_verifie:
        flash("Votre email est déjà confirmé.", "info")
        return redirect(url_for("tenant.dashboard"))
    if not os.environ.get("MAIL_PASSWORD"):
        u.email_verifie = True
        db.session.commit()
        flash("Mode développement : email validé automatiquement.", "success")
        return redirect(url_for("tenant.dashboard"))
    token = sec.token_urlsafe(32)
    u.token_confirmation        = token
    u.token_confirmation_expiry = datetime.utcnow() + timedelta(hours=48)
    db.session.commit()
    lien = url_for("auth.confirmer_email", token=token, _external=True)
    mail = current_app.extensions["mail"]
    from flask_mail import Message as Msg
    msg = Msg(
        subject="✅ Confirmez votre email — PaieGabon",
        recipients=[u.email],
        html=f'<p>Cliquez ici pour confirmer : <a href="{lien}">{lien}</a></p>',
        sender=current_app.config["MAIL_DEFAULT_SENDER"],
    )
    send_email_async(mail, msg)
    flash(f"Email de confirmation renvoyé à {u.email}.", "success")
    return redirect(url_for("tenant.dashboard"))


# ── Mot de passe oublié ───────────────────────────────────────────────────────
@bp.route("/mot-de-passe-oublie", methods=["GET", "POST"])
@_rate_limit("5/hour")
def mot_de_passe_oublie():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user  = Utilisateur.query.filter_by(email=email, actif=True).first()
        # Toujours afficher le même message (anti-énumération)
        flash("Si un compte existe avec cet email, un lien de réinitialisation a été envoyé.", "success")
        if user and os.environ.get("MAIL_PASSWORD"):
            token = sec.token_urlsafe(32)
            user.reset_token        = token
            user.reset_token_expiry = datetime.utcnow() + timedelta(hours=2)
            db.session.commit()
            lien = url_for("auth.reinitialiser_mdp", token=token, _external=True)
            mail = current_app.extensions["mail"]
            from flask_mail import Message as Msg
            msg = Msg(
                subject="🔑 Réinitialisation de mot de passe — PaieGabon",
                recipients=[email],
                html=f"""
                <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto">
                  <div style="background:#1a2332;padding:1.5rem;text-align:center;border-radius:.75rem .75rem 0 0">
                    <h1 style="color:white;margin:0">PaieGabon</h1>
                  </div>
                  <div style="background:white;padding:2rem;border:1px solid #e5e7eb;
                       border-top:none;border-radius:0 0 .75rem .75rem">
                    <h2 style="color:#111827">Réinitialisation du mot de passe</h2>
                    <p style="color:#6b7280">
                      Cliquez sur le lien ci-dessous pour choisir un nouveau mot de passe.<br/>
                      Ce lien est valable <strong>2 heures</strong>.
                    </p>
                    <div style="text-align:center;margin:1.5rem 0">
                      <a href="{lien}" style="background:#1a2332;color:white;padding:.875rem 2rem;
                         border-radius:.75rem;font-weight:700;text-decoration:none">
                        🔑 Réinitialiser mon mot de passe
                      </a>
                    </div>
                    <p style="color:#9ca3af;font-size:.75rem;text-align:center">
                      Si vous n'avez pas fait cette demande, ignorez cet email.
                    </p>
                  </div>
                </div>""",
                sender=current_app.config["MAIL_DEFAULT_SENDER"],
            )
            send_email_async(mail, msg)
        return redirect(url_for("auth.login"))
    return render_template("auth/mot_de_passe_oublie.html")


@bp.route("/reinitialiser-mdp/<token>", methods=["GET", "POST"])
def reinitialiser_mdp(token):
    user = Utilisateur.query.filter_by(reset_token=token).first()
    if not user or (user.reset_token_expiry and datetime.utcnow() > user.reset_token_expiry):
        flash("Lien invalide ou expiré. Refaites une demande.", "error")
        return redirect(url_for("auth.mot_de_passe_oublie"))
    if request.method == "POST":
        nouveau_mdp = request.form.get("nouveau_mdp", "").strip()
        pw_errors   = validate_password(nouveau_mdp)
        if pw_errors:
            for err in pw_errors:
                flash(err, "error")
            return render_template("auth/reinitialiser_mdp.html", token=token)
        user.set_password(nouveau_mdp)
        user.reset_token        = None
        user.reset_token_expiry = None
        db.session.commit()
        flash("✅ Mot de passe réinitialisé. Connectez-vous.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reinitialiser_mdp.html", token=token)


# ── Modifier email de connexion ───────────────────────────────────────────────
@bp.route("/profil/changer-email", methods=["GET", "POST"])
@login_required
def changer_email():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if request.method == "POST":
        nouvel_email = request.form.get("nouvel_email", "").strip().lower()
        mot_de_passe = request.form.get("mot_de_passe", "")
        if not nouvel_email or "@" not in nouvel_email:
            flash("Adresse email invalide.", "error")
            return render_template("auth/changer_email.html", tenant=t)
        if not current_user.check_password(mot_de_passe):
            flash("Mot de passe incorrect.", "error")
            return render_template("auth/changer_email.html", tenant=t)
        if Utilisateur.query.filter_by(email=nouvel_email).first():
            flash("Cette adresse email est déjà utilisée.", "error")
            return render_template("auth/changer_email.html", tenant=t)
        token = sec.token_urlsafe(32)
        current_user.nouvel_email_en_attente = nouvel_email
        current_user.token_changement_email  = token
        current_user.token_changement_expiry = datetime.utcnow() + timedelta(hours=24)
        db.session.commit()
        lien = url_for("auth.confirmer_changement_email", token=token, _external=True)
        if os.environ.get("MAIL_PASSWORD"):
            mail = current_app.extensions["mail"]
            from flask_mail import Message as Msg
            msg = Msg(
                subject="📧 Confirmation changement d'email — PaieGabon",
                recipients=[nouvel_email],
                html=f"""
                <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto">
                  <p style="color:#6b7280">
                    Confirmez votre nouvel email :
                    <a href="{lien}">{lien}</a><br/>
                    Lien valable 24h.
                  </p>
                </div>""",
                sender=current_app.config["MAIL_DEFAULT_SENDER"],
            )
            send_email_async(mail, msg)
            flash(f"Lien de confirmation envoyé à {nouvel_email}.", "success")
        else:
            current_user.email = nouvel_email
            current_user.nouvel_email_en_attente = None
            current_user.token_changement_email  = None
            db.session.commit()
            flash(f"Email mis à jour : {nouvel_email}", "success")
        return redirect(url_for("tenant.parametres"))
    return render_template("auth/changer_email.html", tenant=t)


@bp.route("/profil/confirmer-email/<token>")
@login_required
def confirmer_changement_email(token):
    u = Utilisateur.query.filter_by(token_changement_email=token).first()
    if not u:
        flash("Lien invalide ou déjà utilisé.", "error")
        return redirect(url_for("tenant.parametres"))
    if u.token_changement_expiry and datetime.utcnow() > u.token_changement_expiry:
        flash("Ce lien a expiré (24h). Refaites la demande.", "error")
        return redirect(url_for("auth.changer_email"))
    ancien_email = u.email
    u.email                   = u.nouvel_email_en_attente
    u.nouvel_email_en_attente = None
    u.token_changement_email  = None
    u.token_changement_expiry = None
    db.session.commit()
    flash(f"✅ Email mis à jour ({ancien_email} → {u.email}). Reconnectez-vous.", "success")
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


# ── Politique de confidentialité ──────────────────────────────────────────────
@bp.route("/politique-confidentialite")
def politique_confidentialite():
    return render_template("politique_confidentialite.html")


# ── Documents légaux ──────────────────────────────────────────────────────────
@bp.route("/cgu")
def cgu():
    return render_template("cgu.html")


@bp.route("/cgv")
def cgv():
    return render_template("cgv.html")


@bp.route("/mentions-legales")
def mentions_legales():
    return render_template("mentions_legales.html")
