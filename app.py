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
                   flash, abort, session, jsonify)
from flask_login import LoginManager, logout_user, current_user
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect

from models import (db, Plan, Tenant, Utilisateur, CategorieEmploi,
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

# Version applicative (visible en bas de la barre latérale — sert aussi de repère
# pour vérifier quelle version est réellement déployée).
APP_VERSION = "1.5.0 · 2026-06-12"

# ── SECRET_KEY ────────────────────────────────────────────────────────────────
_secret = os.environ.get("SECRET_KEY", "")
if not _secret:
    if os.environ.get("FLASK_ENV") == "production" or os.environ.get("RAILWAY_ENVIRONMENT"):
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

# ── Email ─────────────────────────────────────────────────────────────────────
app.config["MAIL_SERVER"]         = "smtp.gmail.com"
app.config["MAIL_PORT"]           = 587
app.config["MAIL_USE_TLS"]        = True
app.config["MAIL_USERNAME"]       = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"]       = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_USERNAME", "noreply@paiegalon.ga")
app.config["MAIL_SUPPRESS_SEND"]  = not bool(os.environ.get("MAIL_USERNAME", ""))
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
app.config["SESSION_COOKIE_SECURE"]   = (os.environ.get("RAILWAY_ENVIRONMENT") == "production")

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
    return Utilisateur.query.get(int(uid))

# ── Middleware inactivité ─────────────────────────────────────────────────────
@app.before_request
def gerer_session_inactivite():
    excluded = ["/login", "/inscription", "/confirmer-email",
                "/mot-de-passe-oublie", "/reinitialiser-mdp",
                "/politique-confidentialite", "/static"]
    if any(request.path.startswith(p) for p in excluded):
        return
    if current_user.is_authenticated:
        now     = datetime.utcnow()
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
        # ✅ Content-Security-Policy — protège contre l'injection de scripts
        #    externes tout en autorisant les handlers inline (oninput, onclick…)
        #    et les fetch vers les routes internes utilisés par l'app.
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-hashes' cdn.tailwindcss.com cdnjs.cloudflare.com cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' fonts.googleapis.com cdnjs.cloudflare.com; "
            "font-src 'self' data: fonts.gstatic.com cdnjs.cloudflare.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers["Content-Security-Policy"] = csp
        # ✅ HSTS — force HTTPS pendant 1 an (uniquement en production)
        if os.environ.get("RAILWAY_ENVIRONMENT") == "production":
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
def not_found(e):   return render_template("auth/403.html"), 404
@app.errorhandler(500)
def server_error(e):
    logger.error(f"Erreur 500 : {e}")
    try:
        db.session.rollback()   # éviter de laisser une transaction cassée
    except Exception:
        pass
    return render_template("auth/403.html"), 500

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
csrf.exempt(api_v1_bp)   # API REST utilise Bearer tokens, pas CSRF

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
    "tenant.api_cache_clear",
    "prestataires.api_calculer_facture",
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
    return send_from_directory(app.static_folder, "sw.js",
                               mimetype="application/javascript")

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
            sa_password = sec.token_urlsafe(16)
            logger.info(f"[INIT] Super-admin — email: {sa_email} — mdp auto: {sa_password}")
        sa = Utilisateur(nom="ADMIN", prenom="Super", email=sa_email,
                         role="SUPER_ADMIN", actif=True)
        sa.set_password(sa_password)
        db.session.add(sa)
    if not Tenant.query.first():
        db.session.flush()
        plan = Plan.query.filter_by(code="PRO").first()
        t = Tenant(slug="demo", denomination="ENTREPRISE DEMO", sigle="DEMO",
                   activite="À RENSEIGNER", nif="", ville="Libreville", pays="Gabon",
                   plan_id=plan.id if plan else None, statut="ACTIF")
        t.token_api = sec.token_hex(32)
        db.session.add(t); db.session.flush()
        for code, lib in [("C1","Ouvriers"),("C2","Techniciens"),
                          ("C3","Conducteurs"),("C4","Cadres")]:
            db.session.add(CategorieEmploi(tenant_id=t.id, code=code, libelle=lib))
        demo_email    = os.environ.get("DEMO_EMAIL",    "demo@paiegalon.ga")
        demo_password = os.environ.get("DEMO_PASSWORD", "")
        if not demo_password:
            demo_password = sec.token_urlsafe(16)
            logger.info(f"[INIT] Démo — email: {demo_email} — mdp auto: {demo_password}")
        u = Utilisateur(nom="DEMO", prenom="Responsable", email=demo_email,
                        role="TENANT_ADMIN", tenant_id=t.id, actif=True)
        u.set_password(demo_password)
        db.session.add(u)
    db.session.commit()
    logger.info("✅ Base initialisée.")

def run_migrations():
    migrations = [
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
        "UPDATE utilisateurs SET email_verifie = TRUE WHERE email_verifie IS NULL OR email_verifie = FALSE",
        "ALTER TABLE salaries ADD COLUMN IF NOT EXISTS email VARCHAR(200)",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS date_embauche DATE",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS date_debut DATE",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS date_fin DATE",
        "ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS nationalite VARCHAR(60)",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS indem_compensatrice_conge NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS indem_services_rendus NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS indem_compensatrice_preavis NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS indem_licenciement NUMERIC(15,2) DEFAULT 0",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS convention VARCHAR(20) DEFAULT 'AUCUNE'",
    ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.debug(f"Migration skipped ({sql[:50]}…): {e}")
    logger.info("✅ Migrations terminées.")

with app.app_context():
    try:
        db.create_all()
        run_migrations()
        init_db()
    except Exception as e:
        logger.error(f"Erreur au démarrage : {e}")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
