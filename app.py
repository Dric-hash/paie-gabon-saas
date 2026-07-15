"""
app.py — SaaS Paie Gabon — Application factory (refactorisé)
Blueprints :
  auth     → /login  /inscription  /logout  /confirmer-email  /profil
  admin    → /admin/*
  tenant   → /dashboard  /salaries  /bulletins  /conges  /pointage  /sites …
  api_v1   → /api/v1/*
"""
import os, sys, logging, secrets as sec
from datetime import datetime, date, timedelta

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, abort, session, jsonify, g)
from flask_login import LoginManager, logout_user, current_user
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect

from models import (db, utcnow, Plan, Tenant, Utilisateur, CategorieEmploi,
                    RubriquePaie)
from i18n import get_translations, detect_language, is_rtl

# ── Logging structuré ─────────────────────────────────────────────────────────
_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("paiegalon")
if os.environ.get("SQLALCHEMY_ECHO") != "1":
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

# ── Monitoring Sentry (le plus tôt possible) ──────────────────────────────────
from monitoring import init_sentry, set_user_context
init_sentry()

app = Flask(__name__)

# Indicateur de production unique et robuste (présence d'une variable, pas
# égalité stricte sur un nom d'environnement Railway qui peut être renommé).
EST_PRODUCTION = bool(
    os.environ.get("RAILWAY_ENVIRONMENT")
    or os.environ.get("FLASK_ENV") == "production"
)
app.config["EST_PRODUCTION"] = EST_PRODUCTION

# Version applicative (visible en bas de la barre latérale — sert aussi de repère
# pour vérifier quelle version est réellement déployée).
APP_VERSION = "1.5.1 · 2026-06-12"

# ── SECRET_KEY ────────────────────────────────────────────────────────────────
_secret = os.environ.get("SECRET_KEY", "")
if not _secret:
    if EST_PRODUCTION:
        logger.critical("SECRET_KEY non définie — démarrage interrompu.")
        sys.exit(1)
    _secret = sec.token_hex(32)
    logger.warning("[DEV] SECRET_KEY temporaire générée. Définissez-la en production.")
app.config["SECRET_KEY"] = _secret

# ── Base de données ───────────────────────────────────────────────────────────
_db_url = os.environ.get("DATABASE_URL", "sqlite:///saas_paie.db")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"]        = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Les options de pool ne s'appliquent qu'à PostgreSQL/MySQL.
# SQLite (tests, dev local) ne les supporte pas.
if _db_url.startswith("postgresql://") or _db_url.startswith("mysql"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": 5, "max_overflow": 10,
        "pool_timeout": 30, "pool_recycle": 1800, "pool_pre_ping": True,
    }
db.init_app(app)

# Flask-Migrate (Alembic) — active les commandes `flask db ...` pour gérer le
# schéma de façon versionnée. Le démarrage continue d'utiliser create_all() +
# run_migrations() pour ne pas perturber la base de production existante ; la
# bascule vers Alembic est documentée dans migrations/README (flask db stamp).
try:
    from flask_migrate import Migrate
    migrate = Migrate(app, db)
except ImportError:
    migrate = None  # flask-migrate non installé (ex. ancien environnement)

# ── Email (Resend par défaut, configurable via variables d'environnement) ─────
# Resend SMTP : host=smtp.resend.com, user="resend", password=<clé API re_...>.
# L'expéditeur (MAIL_DEFAULT_SENDER) est INDÉPENDANT du nom d'utilisateur SMTP :
# il doit être une adresse valide d'un domaine vérifié dans Resend, ou
# "onboarding@resend.dev" pour les tests (envoi uniquement vers votre propre email).
app.config["MAIL_SERVER"]         = os.environ.get("MAIL_SERVER", "smtp.resend.com")
app.config["MAIL_PORT"]           = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"]        = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"]       = os.environ.get("MAIL_USERNAME", "resend")
app.config["MAIL_PASSWORD"]       = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER", "onboarding@resend.dev")
# On n'envoie réellement que si une clé API (MAIL_PASSWORD) est présente.
app.config["MAIL_SUPPRESS_SEND"]  = not bool(os.environ.get("MAIL_PASSWORD", ""))
mail = Mail(app)

# ── Rate Limiting ─────────────────────────────────────────────────────────────
from core import init_limiter
limiter = init_limiter(app)

# ── Sessions ──────────────────────────────────────────────────────────────────
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    minutes=int(os.environ.get("SESSION_TIMEOUT_MINUTES", "60"))
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = EST_PRODUCTION

# ── CSRF ──────────────────────────────────────────────────────────────────────
csrf = CSRFProtect(app)
app.config["WTF_CSRF_CHECK_DEFAULT"] = True
app.config["WTF_CSRF_TIME_LIMIT"]    = 3600

# ── Compression ───────────────────────────────────────────────────────────────
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

# ── Login Manager ─────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view    = "auth.login"
login_manager.login_message = "Veuillez vous connecter."

@login_manager.user_loader
def load_user(uid):
    # Format nouveau : "id.jeton" — on vérifie que le jeton correspond (sinon la
    # session a été invalidée par un changement de mot de passe).
    suid = str(uid)
    if "." in suid:
        sid, tok = suid.split(".", 1)
        try:
            u = db.session.get(Utilisateur, int(sid))
        except (ValueError, TypeError):
            return None
        if u and (u.session_token or "") == tok:
            return u
        return None
    # Sessions héritées (créées avant l'ajout du jeton) : chargement par id.
    # Elles expireront via le timeout d'inactivité.
    try:
        return db.session.get(Utilisateur, int(suid))
    except (ValueError, TypeError):
        return None

# ── Middleware inactivité ─────────────────────────────────────────────────────
@app.before_request
def _generer_csp_nonce():
    """Nonce CSP par requête (préparation à une CSP stricte sans 'unsafe-inline')."""
    g.csp_nonce = sec.token_urlsafe(16)


@app.context_processor
def _injecter_csp_nonce():
    return {"csp_nonce": getattr(g, "csp_nonce", "")}


@app.before_request
def gerer_session_inactivite():
    excluded = ["/login", "/inscription", "/confirmer-email",
                "/mot-de-passe-oublie", "/reinitialiser-mdp",
                "/politique-confidentialite", "/static"]
    if any(request.path.startswith(p) for p in excluded):
        return
    if current_user.is_authenticated:
        now     = utcnow()
        timeout = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "60"))
        derniere = session.get("derniere_activite")
        if derniere:
            try:
                elapsed = (now - datetime.fromisoformat(derniere)).total_seconds() / 60
                if elapsed > timeout:
                    logout_user()
                    session.clear()
                    flash(f"Session expirée après {timeout} min d'inactivité.", "error")
                    return redirect(url_for("auth.login"))
            except Exception:
                pass
        session["derniere_activite"] = now.isoformat()
        session.permanent = True
        # Attacher le contexte tenant/utilisateur à Sentry pour le débogage
        set_user_context(current_user)

# ── Headers sécurité ─────────────────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    path = request.path
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"]          = "private, no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "SAMEORIGIN"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        # ✅ Content-Security-Policy — protège contre l'injection de scripts.
        #    NB : 'unsafe-inline' reste nécessaire tant que les handlers inline
        #    (oninput, onclick…) ne sont pas migrés vers une CSP à nonce.
        #    En attendant, on durcit les directives qui n'exigent aucune
        #    modification de template (base-uri, object-src, form-action…).
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-hashes' cdn.tailwindcss.com cdnjs.cloudflare.com cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' fonts.googleapis.com cdnjs.cloudflare.com; "
            "font-src 'self' data: fonts.gstatic.com cdnjs.cloudflare.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "object-src 'none'; "          # bloque les plugins/embeds (Flash, etc.)
            "base-uri 'self'; "            # empêche le détournement via <base>
            "form-action 'self'; "         # les formulaires ne postent que vers le site
            "frame-src 'none'; "           # aucune iframe (l'app n'en utilise pas)
            "worker-src 'self'; "          # service worker same-origin uniquement
            "manifest-src 'self'; "        # manifeste PWA same-origin
            "frame-ancestors 'none';"      # pas d'iframe tierce (anti-clickjacking)
        )
        # En production, force la mise à niveau des sous-ressources http → https.
        if EST_PRODUCTION:
            csp += " upgrade-insecure-requests;"
        response.headers["Content-Security-Policy"] = csp

        # ── Phase 1 de la migration vers une CSP stricte (F2) ────────────────
        # Opt-in via CSP_REPORT_ONLY=1 : publie une politique stricte à base de
        # nonce en mode *Report-Only* (n'impose RIEN, ne casse rien) pour collecter
        # les violations sur /csp-report avant de basculer en enforce. Désactivé
        # par défaut pour ne pas générer de bruit tant que la migration des
        # handlers inline n'est pas faite.
        if os.environ.get("CSP_REPORT_ONLY", "").lower() in ("1", "true", "yes"):
            nonce = getattr(g, "csp_nonce", "")
            strict = (
                "default-src 'self'; "
                f"script-src 'self' 'nonce-{nonce}' cdn.tailwindcss.com cdnjs.cloudflare.com cdn.jsdelivr.net; "
                # style-src reste en 'unsafe-inline' : les attributs style="…" inline
                # ne peuvent pas porter de nonce, et le risque d'injection CSS est
                # faible. On concentre l'observation sur script-src (le vrai vecteur).
                "style-src 'self' 'unsafe-inline' fonts.googleapis.com cdnjs.cloudflare.com; "
                "font-src 'self' data: fonts.gstatic.com cdnjs.cloudflare.com; "
                "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; "
                "base-uri 'self'; form-action 'self'; frame-src 'none'; "
                "worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; "
                "report-uri /csp-report;"
            )
            response.headers["Content-Security-Policy-Report-Only"] = strict
        # ✅ HSTS — force HTTPS pendant 1 an (uniquement en production)
        if EST_PRODUCTION:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# ── i18n ─────────────────────────────────────────────────────────────────────
@app.context_processor
def inject_translations():
    lang = detect_language(request, current_user if current_user.is_authenticated else None)
    T    = get_translations(lang)
    return {"T": T, "lang": lang, "rtl": is_rtl(lang)}

# ── Template filters ──────────────────────────────────────────────────────────
@app.template_filter("fcfa")
def fcfa_filter(v):
    try:    return f"{int(float(v)):,}".replace(",", " ") + " FCFA"
    except: return "— FCFA"

@app.template_filter("date_fr")
def date_fr_filter(v):
    if not v: return "—"
    if isinstance(v, str):
        try:    v = datetime.strptime(v[:10], "%Y-%m-%d").date()
        except: return v
    return v.strftime("%d/%m/%Y")

@app.template_filter("datetime_fr")
def datetime_fr_filter(v):
    if not v: return "Jamais"
    if isinstance(v, str):
        try:    v = datetime.fromisoformat(v)
        except: return v
    return v.strftime("%d/%m/%Y à %Hh%M")

@app.context_processor
def inject_globals():
    return {"now": datetime.now(), "enumerate": enumerate, "app_version": APP_VERSION}

# ── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(403)
def forbidden(e):   return render_template("auth/403.html"), 403
@app.errorhandler(404)
def not_found(e):   return render_template("auth/404.html"), 404
@app.errorhandler(500)
def server_error(e):
    logger.error(f"Erreur 500 : {e}")
    try:
        db.session.rollback()   # éviter de laisser une transaction cassée
    except Exception:
        pass
    return render_template("auth/500.html"), 500

# ── Health check (monitoring Railway / uptime) ────────────────────────────────
@app.route("/health")
def health_check():
    """Vérifie que l'app et la base de données répondent."""
    try:
        db.session.execute(db.text("SELECT 1"))
        return {"status": "ok", "database": "ok"}, 200
    except Exception as e:
        logger.error(f"Health check échoué : {e}")
        return {"status": "degraded", "database": "error"}, 503


@app.route("/csp-report", methods=["POST"])
def csp_report():
    """Reçoit les rapports de violation CSP (mode Report-Only). Journalise et
    renvoie 204. Sans authentification ni CSRF (POST émis par le navigateur)."""
    try:
        rapport = request.get_json(force=True, silent=True) or {}
        détail = rapport.get("csp-report", rapport)
        logger.warning(
            "[CSP] violation: directive=%s blocked=%s doc=%s",
            détail.get("violated-directive") or détail.get("effectiveDirective"),
            détail.get("blocked-uri") or détail.get("blockedURL"),
            détail.get("document-uri") or détail.get("documentURL"),
        )
    except Exception:
        pass
    return ("", 204)

# ── Blueprints ────────────────────────────────────────────────────────────────
from blueprints.auth   import bp as auth_bp
from blueprints.admin  import bp as admin_bp
from blueprints.tenant import bp as tenant_bp
from blueprints.api_v1 import bp as api_v1_bp
from blueprints.prestataires import bp as prestataires_bp

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(tenant_bp)
app.register_blueprint(api_v1_bp)
app.register_blueprint(prestataires_bp)
import models_sso
from blueprints.sso_provider import bp as sso_bp, sso_api as sso_api_bp
app.register_blueprint(sso_bp)
app.register_blueprint(sso_api_bp)
csrf.exempt(sso_api_bp)
csrf.exempt(api_v1_bp)   # API REST utilise Bearer tokens, pas CSRF

# ── Rate limiting de l'API REST ───────────────────────────────────────────────
# L'API étant exemptée de CSRF, le rate-limit est la principale barrière contre
# le brute-force (X-API-Key / OAuth) et l'abus. Limite par IP (Flask-Limiter).
if limiter:
    try:
        limiter.limit(os.environ.get("API_RATE_LIMIT_STR", "100/minute"))(api_v1_bp)
    except Exception as _e:
        logger.warning(f"[LIMITER] Limite API non appliquée : {_e}")

# ── Exemption CSRF des routes API internes JSON ───────────────────────────────
# Ces routes sont appelées en POST par le JavaScript (calcul temps réel,
# simulateur, BTP). Elles sont protégées par @login_required + session et ne
# font que des calculs (aucune écriture en base), donc le token CSRF n'est pas
# requis. Sans cette exemption, les fetch JSON sont rejetés (400 CSRF missing).
_CSRF_EXEMPT_ENDPOINTS = [
    "tenant.api_calculer",
    "tenant.api_semaine_btp",
    "tenant.api_jour_ferie",
    "tenant.api_simuler_paie",
    "tenant.api_simuler_scenarios",
    "tenant.api_simuler_net_vers_brut",
    "tenant.api_simuler_augmentation",
    # api_cache_clear retiré de l'exemption : il modifie l'état (vide le cache) et
    # le front envoie déjà un en-tête X-CSRFToken — la protection CSRF s'applique.
    "prestataires.api_calculer_facture",
    "csp_report",   # rapports CSP émis par le navigateur (pas de CSRF)
]
for _ep in _CSRF_EXEMPT_ENDPOINTS:
    _view = app.view_functions.get(_ep)
    if _view:
        csrf.exempt(_view)

# ── PWA ───────────────────────────────────────────────────────────────────────
@app.route("/manifest.json")
def pwa_manifest():
    import json
    with open(os.path.join(app.static_folder, "manifest.json")) as f:
        return jsonify(json.load(f))

@app.route("/sw.js")
def pwa_sw():
    from flask import send_from_directory
    if not app.config.get("EST_PRODUCTION"):
        js = (
            "self.addEventListener('install', () => self.skipWaiting());\n"
            "self.addEventListener('activate', (e) => {\n"
            "  e.waitUntil((async () => {\n"
            "    const keys = await caches.keys();\n"
            "    await Promise.all(keys.map(k => caches.delete(k)));\n"
            "    await self.registration.unregister();\n"
            "    const cs = await self.clients.matchAll();\n"
            "    cs.forEach(c => c.navigate(c.url));\n"
            "  })());\n"
            "});\n"
        )
        return app.response_class(js, mimetype="application/javascript")
    return send_from_directory(app.static_folder, "sw.js",
                               mimetype="application/javascript")

# ── Favicon servi à la racine (onglet navigateur, toutes pages) ───────────────
@app.route("/favicon.ico")
def favicon_ico():
    from flask import send_from_directory
    return send_from_directory(app.static_folder, "favicon.ico",
                               mimetype="image/vnd.microsoft.icon")

@app.route("/favicon.svg")
def favicon_svg():
    from flask import send_from_directory
    return send_from_directory(app.static_folder, "favicon.svg",
                               mimetype="image/svg+xml")

@app.route("/offline")
def pwa_offline():
    return render_template("tenant/offline.html")

# ── Init DB ───────────────────────────────────────────────────────────────────
def init_db():
    db.create_all()
    if not Plan.query.first():
        for code, nom, prix, ms, mu, desc in [
            ("STARTER", "Starter",  15000,   10,  1, "10 salariés max, 1 utilisateur"),
            ("PRO",     "Pro",      35000,   50,  3, "50 salariés max, 3 utilisateurs"),
            ("CABINET", "Cabinet", 100000, None, 10, "Illimité, 10 utilisateurs"),
        ]:
            db.session.add(Plan(code=code, nom=nom, prix_mensuel=prix,
                                max_salaries=ms, max_utilisateurs=mu, description=desc))
    if not RubriquePaie.query.first():
        for code, lib, typ, ts, tp, plaf in [
            ("CNSS",   "Caisse Nationale Sécurité Sociale",     "COTISATION", 0.05,  0.18,  1500000),
            ("CNAMGS", "Assurance Maladie Garantie Sociale",    "COTISATION", 0.02,  0.041, 2500000),
            ("TCS",    "Taxe Complémentaire Salaires",           "RETENUE",   0.05,  None,  None),
            ("FNH",    "Fonds National Habitat",                 "COTISATION", None, 0.03,  1500000),
            ("CFP",    "Contribution Formation Professionnelle", "COTISATION", None, 0.005, None),
        ]:
            db.session.add(RubriquePaie(code=code, libelle=lib, type=typ,
                                        taux_salarie=ts, taux_patronal=tp,
                                        plafond_mensuel=plaf))
    if not Utilisateur.query.filter_by(role="SUPER_ADMIN").first():
        sa_email    = os.environ.get("SUPER_ADMIN_EMAIL", "superadmin@paiegalon.com")
        sa_password = os.environ.get("SUPER_ADMIN_PASSWORD", "")
        if not sa_password:
            if EST_PRODUCTION:
                logger.critical("SUPER_ADMIN_PASSWORD requis en production — démarrage interrompu.")
                sys.exit(1)
            sa_password = sec.token_urlsafe(16)
            # Ne jamais logguer un mot de passe. En dev, on l'écrit dans un
            # fichier local ignoré par git pour pouvoir se connecter.
            try:
                with open(".dev_credentials.txt", "a") as _f:
                    _f.write(f"SUPER_ADMIN {sa_email} : {sa_password}\n")
            except Exception:
                pass
            logger.info("[DEV] Super-admin créé — identifiants dans .dev_credentials.txt")
        sa = Utilisateur(nom="ADMIN", prenom="Super", email=sa_email,
                         role="SUPER_ADMIN", actif=True)
        sa.set_password(sa_password)
        db.session.add(sa)
    # Le tenant de démonstration n'est créé qu'en dehors de la production,
    # pour ne pas exposer un compte aux identifiants devinables en prod.
    if not Tenant.query.first() and not EST_PRODUCTION:
        db.session.flush()
        plan = Plan.query.filter_by(code="PRO").first()
        t = Tenant(slug="demo", denomination="ENTREPRISE DEMO", sigle="DEMO",
                   activite="À RENSEIGNER", nif="", ville="Libreville", pays="Gabon",
                   plan_id=plan.id if plan else None, statut="ACTIF")
        t.generate_token()
        db.session.add(t); db.session.flush()
        for code, lib in [("C1","Ouvriers"),("C2","Techniciens"),
                          ("C3","Conducteurs"),("C4","Cadres")]:
            db.session.add(CategorieEmploi(tenant_id=t.id, code=code, libelle=lib))
        demo_email    = os.environ.get("DEMO_EMAIL",    "demo@paiegalon.ga")
        demo_password = os.environ.get("DEMO_PASSWORD", "")
        if not demo_password:
            demo_password = sec.token_urlsafe(16)
            try:
                with open(".dev_credentials.txt", "a") as _f:
                    _f.write(f"DEMO {demo_email} : {demo_password}\n")
            except Exception:
                pass
            logger.info("[DEV] Compte démo créé — identifiants dans .dev_credentials.txt")
        u = Utilisateur(nom="DEMO", prenom="Responsable", email=demo_email,
                        role="TENANT_ADMIN", tenant_id=t.id, actif=True)
        u.set_password(demo_password)
        db.session.add(u)
    db.session.commit()
    logger.info("✅ Base initialisée.")

def run_migrations():
    migrations = [
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS numero VARCHAR(30)",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS numero_seq INTEGER",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS jours_conge_par_mois NUMERIC(3,1) DEFAULT 2.5",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS heures_sup_10 NUMERIC(5,2) DEFAULT 0",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS heures_sup_30 NUMERIC(5,2) DEFAULT 0",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS heures_sup_40 NUMERIC(5,2) DEFAULT 0",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS heures_sup_70 NUMERIC(5,2) DEFAULT 0",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS site_id INTEGER",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS entree_matin VARCHAR(5)",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS sortie_matin VARCHAR(5)",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS entree_apmidi VARCHAR(5)",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS sortie_apmidi VARCHAR(5)",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS entree_sup VARCHAR(5)",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS sortie_sup VARCHAR(5)",
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS type_jour VARCHAR(20) DEFAULT 'NORMAL'",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS modele_bulletin VARCHAR(30) DEFAULT 'classique'",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS logo_url TEXT",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS grille_salaires TEXT",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS notes TEXT",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS secteur VARCHAR(200)",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS langue VARCHAR(5) DEFAULT 'fr'",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS email_verifie BOOLEAN DEFAULT FALSE",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS token_confirmation VARCHAR(200)",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS token_confirmation_expiry TIMESTAMP",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS nouvel_email_en_attente VARCHAR(200)",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS token_changement_email VARCHAR(200)",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS token_changement_expiry TIMESTAMP",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS derniere_activite TIMESTAMP",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS nb_echecs_connexion INTEGER DEFAULT 0",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS compte_bloque_jusqu TIMESTAMP",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS reset_token VARCHAR(200)",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS reset_token_expiry TIMESTAMP",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS otp_code_hash VARCHAR(256)",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS otp_expiry TIMESTAMP",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS otp_tentatives INTEGER DEFAULT 0",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS twofa_active BOOLEAN DEFAULT FALSE",
        "ALTER TABLE bulletin_composants ADD COLUMN IF NOT EXISTS base NUMERIC(15,2)",
        "ALTER TABLE bulletin_composants ADD COLUMN IF NOT EXISTS taux NUMERIC(8,4)",
        # ── Mode de paiement (ESPECES / VIREMENT) sur personnes et paies ──
        "ALTER TABLE salaries ADD COLUMN IF NOT EXISTS mode_paiement VARCHAR(30) DEFAULT 'ESPECES'",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS mode_paiement VARCHAR(30) DEFAULT 'ESPECES'",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS mode_paiement VARCHAR(30) DEFAULT 'ESPECES'",
        "ALTER TABLE feuilles_paie_journalier ADD COLUMN IF NOT EXISTS mode_paiement VARCHAR(30) DEFAULT 'ESPECES'",
        # ── Jeton de session (invalidation au changement de mot de passe) ────────
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS session_token VARCHAR(32)",
        "UPDATE utilisateurs SET session_token = substr(md5(random()::text), 1, 32) WHERE session_token IS NULL",
        # ── Hash du token API (M4) : ne plus stocker le secret en clair ──────────
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS token_api_hash VARCHAR(64)",
        "CREATE INDEX IF NOT EXISTS idx_tenants_token_api_hash ON tenants (token_api_hash)",
        "UPDATE utilisateurs SET email_verifie = TRUE WHERE email_verifie IS NULL OR email_verifie = FALSE",
        "ALTER TABLE salaries ADD COLUMN IF NOT EXISTS email VARCHAR(200)",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS date_embauche DATE",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS date_debut DATE",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS date_fin DATE",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS nationalite VARCHAR(60)",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS type_paie VARCHAR(20) DEFAULT 'JOURNALIER'",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS indem_compensatrice_conge NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS indem_services_rendus NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS indem_compensatrice_preavis NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS indem_licenciement NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS convention VARCHAR(20) DEFAULT 'AUCUNE'",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS seuil_heures_sup_hebdo NUMERIC(4,1) DEFAULT 40.0",
        # ── Convention Pétrole : 5ᵉ case d'heures sup (repos/férié de jour, +30%) ──
        "ALTER TABLE pointages ADD COLUMN IF NOT EXISTS heures_sup_30b NUMERIC(5,2) DEFAULT 0",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS heures_sup_30b NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS base_heures_sup_30b NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS taux_heures_sup_30b VARCHAR(20) DEFAULT ''",
        # ── Prestataires : avances (workflow, site, devises) ─────────────────────
        "ALTER TABLE avances_prestataire ADD COLUMN IF NOT EXISTS site_id INTEGER",
        "ALTER TABLE avances_prestataire ADD COLUMN IF NOT EXISTS devise VARCHAR(5) DEFAULT 'XAF'",
        "ALTER TABLE avances_prestataire ADD COLUMN IF NOT EXISTS taux_change NUMERIC(14,6) DEFAULT 1",
        "ALTER TABLE avances_prestataire ADD COLUMN IF NOT EXISTS montant_xaf NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE avances_prestataire ADD COLUMN IF NOT EXISTS valide_par_nom VARCHAR(150)",
        "ALTER TABLE avances_prestataire ADD COLUMN IF NOT EXISTS valide_par_user_id INTEGER",
        "ALTER TABLE avances_prestataire ADD COLUMN IF NOT EXISTS date_validation TIMESTAMP",
        "UPDATE avances_prestataire SET statut = 'EN_ATTENTE' WHERE statut = 'EN_COURS'",
        # ── Prestataires : factures (site, m², %, devises) ───────────────────────
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS site_id INTEGER",
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS surface_m2 NUMERIC(12,2)",
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS prix_unitaire_m2 NUMERIC(15,2)",
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS pourcentage_realisation NUMERIC(5,2) DEFAULT 0",
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS devise VARCHAR(5) DEFAULT 'XAF'",
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS taux_change NUMERIC(14,6) DEFAULT 1",
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS montant_xaf NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS valide_par_nom VARCHAR(150)",
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS valide_par_user_id INTEGER",
        "ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS date_validation TIMESTAMP",
        # Factures déjà saisies (ancien statut EN_ATTENTE = prêtes) → restent payables
        "UPDATE factures_prestataire SET statut='VALIDEE' WHERE statut='EN_ATTENTE'",
        # ── Prestataires : paiements (% réalisation BTP) ─────────────────────────
        "ALTER TABLE paiements_prestataire ADD COLUMN IF NOT EXISTS pourcentage_realisation NUMERIC(5,2) DEFAULT 0",
        # ── Prestataires : lignes de facture (quantité prévue → % réalisation/ligne) ──
        "ALTER TABLE lignes_facture_prestataire ADD COLUMN IF NOT EXISTS quantite_totale NUMERIC(12,2)",
        # ── Journaliers : avances déduites de la paie de période ──
        "ALTER TABLE feuilles_paie_journalier ADD COLUMN IF NOT EXISTS avance_deduite NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE avances_journalier ADD COLUMN IF NOT EXISTS statut VARCHAR(20) DEFAULT 'EN_ATTENTE'",
        # ── Index de performance sur les tables récentes (multi-tenant) ──────────
        # Idempotents (IF NOT EXISTS). Accélèrent les listes et impressions filtrées
        # par tenant_id / statut / dates / site.
        "CREATE INDEX IF NOT EXISTS idx_journaliers_tenant_statut ON journaliers (tenant_id, statut)",
        "CREATE INDEX IF NOT EXISTS idx_journaliers_tenant_type ON journaliers (tenant_id, type_paie)",
        "CREATE INDEX IF NOT EXISTS idx_fpj_tenant_statut ON feuilles_paie_journalier (tenant_id, statut)",
        "CREATE INDEX IF NOT EXISTS idx_fpj_tenant_dates ON feuilles_paie_journalier (tenant_id, date_debut, date_fin)",
        "CREATE INDEX IF NOT EXISTS idx_fpj_journalier ON feuilles_paie_journalier (journalier_id, date_debut, date_fin)",
        "CREATE INDEX IF NOT EXISTS idx_affect_tenant_site ON affectations_sites (tenant_id, site_id)",
        "CREATE INDEX IF NOT EXISTS idx_affect_journalier ON affectations_sites (journalier_id)",
        "CREATE INDEX IF NOT EXISTS idx_affect_salarie ON affectations_sites (salarie_id)",
        "CREATE INDEX IF NOT EXISTS idx_acomptes_tenant_periode ON acomptes (tenant_id, annee, mois)",
        "CREATE INDEX IF NOT EXISTS idx_acomptes_salarie ON acomptes (salarie_id)",
        "CREATE INDEX IF NOT EXISTS idx_conges_tenant_annee ON conges (tenant_id, annee)",
        "CREATE INDEX IF NOT EXISTS idx_conges_salarie ON conges (salarie_id)",
        "CREATE INDEX IF NOT EXISTS idx_sites_tenant_actif ON sites (tenant_id, actif)",
        "CREATE INDEX IF NOT EXISTS idx_pointages_journalier_date ON pointages (journalier_id, date_pointage)",
        # ── Contrats : interrogés par salarié à chaque paie (seul trou restant) ───
        "CREATE INDEX IF NOT EXISTS idx_contrats_salarie_actif ON contrats (salarie_id, actif)",
        "CREATE INDEX IF NOT EXISTS idx_contrats_tenant_salarie ON contrats (tenant_id, salarie_id)",
        # ── Pointages par chantier (relevé multi-sites) ──────────────────────────
        "CREATE INDEX IF NOT EXISTS idx_pointages_tenant_site ON pointages (tenant_id, site_id)",
    ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.debug(f"Migration skipped ({sql[:50]}…): {e}")

    # ── Backfill M4 : hacher les tokens API encore stockés en clair ───────────────
    # Idempotent : un token déjà migré porte un préfixe « …  » et a un hash ; on
    # ne retraite donc jamais deux fois la même ligne. Les clients API existants
    # continuent de fonctionner (le hash du token qu'ils envoient correspond).
    try:
        from models import Tenant, hash_secret
        a_migrer = (Tenant.query
                    .filter(Tenant.token_api.isnot(None))
                    .filter(Tenant.token_api_hash.is_(None))
                    .all())
        for t in a_migrer:
            raw = t.token_api or ""
            if "…" in raw:           # déjà sous forme de préfixe → ignorer
                continue
            t.token_api_hash = hash_secret(raw)
            t.token_api = (raw[:8] + "…") if len(raw) > 8 else raw
        if a_migrer:
            db.session.commit()
            logger.info(f"[M4] {len(a_migrer)} token(s) API migré(s) vers un stockage haché.")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"[M4] Backfill des tokens API ignoré : {e}")

    logger.info("✅ Migrations terminées.")

# Bootstrap du schéma au démarrage (create_all + migrations idempotentes).
# Peut être désactivé via SKIP_BOOTSTRAP=1 pour les commandes CLI (ex. génération
# d'une migration Alembic, qui doit comparer les modèles à une base vide).
if not os.environ.get("SKIP_BOOTSTRAP"):
    with app.app_context():
        try:
            db.create_all()
            run_migrations()
            init_db()
        except Exception as e:
            logger.error(f"Erreur au démarrage : {e}")

if __name__ == "__main__":
    app.run(debug=False, port=5000)
