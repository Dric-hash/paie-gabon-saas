"""
models.py — Modèles multi-tenant SaaS Paie Gabon
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta, timezone
import secrets
import hashlib
import hmac as _hmac

db = SQLAlchemy()


def utcnow():
    """Horodatage UTC *naïf*, équivalent exact de l'ancien utcnow()
    mais sans la dépréciation de Python 3.12. Conserve le même type (naïf) que
    celui stocké dans les colonnes DateTime, donc aucune comparaison ne change."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_secret(raw: str) -> str:
    """
    Hash déterministe (SHA-256) d'un secret à haute entropie (token API,
    client_secret OAuth). Comme ces secrets sont des aléas de 256 bits, un hash
    non salé suffit (aucune attaque par dictionnaire/rainbow-table possible) et
    reste indépendant de SECRET_KEY. Permet une recherche par égalité de hash.
    """
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def verifier_secret(raw: str, hashe: str) -> bool:
    """Comparaison à temps constant entre un secret fourni et son hash stocké."""
    if not hashe:
        return False
    return _hmac.compare_digest(hash_secret(raw), hashe)

class Plan(db.Model):
    __tablename__ = "plans"
    id               = db.Column(db.Integer, primary_key=True)
    code             = db.Column(db.String(20), nullable=False, unique=True)
    nom              = db.Column(db.String(100), nullable=False)
    prix_mensuel     = db.Column(db.Numeric(12,2), nullable=False)
    max_salaries     = db.Column(db.Integer)
    max_utilisateurs = db.Column(db.Integer)
    description      = db.Column(db.Text)
    actif            = db.Column(db.Boolean, default=True)
    tenants = db.relationship("Tenant", backref="plan", lazy=True)
    def to_dict(self):
        return {c.name: float(getattr(self,c.name)) if hasattr(getattr(self,c.name),"__float__") and getattr(self,c.name) is not None
                else getattr(self,c.name) for c in self.__table__.columns}


class Tenant(db.Model):
    __tablename__ = "tenants"
    id               = db.Column(db.Integer, primary_key=True)
    slug             = db.Column(db.String(100), nullable=False, unique=True)
    denomination     = db.Column(db.String(200), nullable=False)
    sigle            = db.Column(db.String(50))
    activite         = db.Column(db.String(200))
    secteur          = db.Column(db.String(200))
    nif              = db.Column(db.String(50))
    numero_cnss      = db.Column(db.String(50))
    numero_cnamgs    = db.Column(db.String(50))
    adresse          = db.Column(db.String(300))
    boite_postale    = db.Column(db.String(20))
    telephone        = db.Column(db.String(20))
    ville            = db.Column(db.String(100), default="Libreville")
    region           = db.Column(db.String(100))
    pays             = db.Column(db.String(100), default="Gabon")
    plan_id          = db.Column(db.Integer, db.ForeignKey("plans.id"))
    statut           = db.Column(db.String(20), default="ACTIF")
    date_inscription = db.Column(db.DateTime, default=utcnow)
    date_expiration  = db.Column(db.DateTime)
    # Horodatage de l'envoi de l'alerte « expiration dans 72 h » pour le cycle
    # d'abonnement en cours. Remis à NULL à chaque renouvellement.
    alerte_expiration_envoyee = db.Column(db.DateTime)
    # Référence d'interconnexion : identifiant partagé avec les autres apps
    # (Caisse Ameriack, Ops) pour relier ce tenant à son équivalent. Défini une
    # fois, il sert de clé d'accroche lors des appels d'API entre applications.
    external_ref     = db.Column(db.String(64), unique=True)
    token_api        = db.Column(db.String(64), unique=True)
    # Hash SHA-256 du token API (le secret n'est plus stocké en clair ; `token_api`
    # ne conserve qu'un préfixe lisible pour identifier le token côté UI).
    token_api_hash   = db.Column(db.String(64), index=True)
    # Clé de liaison Ameriack États Financiers : LECTURE SEULE de la fiche
    # d'identité (GET /api/v1/identite). Stockée hachée, comme le token API.
    cle_liaison_hash    = db.Column(db.String(64), index=True)
    cle_liaison_prefixe = db.Column(db.String(16))
    cle_liaison_date    = db.Column(db.DateTime)
    # Autorisation expresse (administrateur) : la même clé donne aussi accès aux
    # TOTAUX ANNUELS AGRÉGÉS de la paie (Note 27B de la DSF, rapprochement
    # comptable) — jamais aux données nominatives.
    liaison_paie_autorisee = db.Column(db.Boolean, default=False)
    # Comptes du journal de paie (export comptable), JSON {"comptes": {...}, "journal": "PAI"}.
    # Vide = comptes proposés par défaut (SYSCOHADA révisé), voir plan_comptable_paie.py.
    comptes_paie = db.Column(db.Text)
    # Portail des employés (consultation des bulletins) : activé par l'administrateur
    portail_salaries_actif = db.Column(db.Boolean, default=False)
    # Attestations demandées sur le portail : délivrées sans validation du RH si activé
    portail_attestations_auto = db.Column(db.Boolean, default=False)
    notes            = db.Column(db.Text)
    modele_bulletin  = db.Column(db.String(30), default="classique")
    # Valeurs : "classique" | "moderne" | "minimaliste"
    # ✅ LOGO — colonne Text pour stocker base64 sans limite
    logo_url         = db.Column(db.Text)
    representant_nom      = db.Column(db.String(150))   # nom du représentant légal (signataire)
    representant_fonction = db.Column(db.String(100))   # ex. Gérant, Directeur Général
    langue           = db.Column(db.String(5), default="fr")  # "fr" | "en"
    # Convention collective applicable : "AUCUNE" | "BTP" | "COMMERCE"
    convention       = db.Column(db.String(20), default="AUCUNE")
    # Grille de salaires conventionnelle (JSON) : {"EI": {"1": 101526, ...}, ...}.
    # Éditée et VÉRIFIÉE par le tenant ; sert au pré-remplissage du salaire de base.
    grille_salaires  = db.Column(db.Text)
    # Taux d'acquisition des congés (jours ouvrables / mois, Art. 222 Loi 022/2021).
    # Défaut = minimum légal adulte 2,0. Une entreprise au contrat/à la convention
    # plus favorable le relève dans Paramètres (Hydrocarbures : 2,5, Art. 42).
    # Les moins de 18 ans ont toujours 2,5 (calculé, pas stocké ici).
    jours_conge_par_mois = db.Column(db.Numeric(3, 1), default=2.0)
    # Quotité cessible : % du salaire au-delà duquel on alerte (acompte + prêt). Indicatif.
    quotite_cessible_pct = db.Column(db.Numeric(5, 2), default=50)
    # Seuil hebdomadaire de déclenchement des heures supplémentaires (en heures).
    # La loi fixe le seuil légal à 40h/semaine. Certaines entreprises bénéficient
    # d'une dérogation portant ce seuil à 45h ou 48h : en deçà, les heures restent
    # payées au taux normal. Bornes admises : 40h (légal) → 48h (max contractuel).
    seuil_heures_sup_hebdo = db.Column(db.Numeric(4, 1), default=40.0)
    # Contrôle de la paie avant validation (controle_paie.py) : réglages de l'entreprise
    controle_seuil_variation = db.Column(db.Numeric(5, 2))   # % d'écart du net vs mois précédent (défaut 20)
    controle_salaire_minimum = db.Column(db.Numeric(15, 2))  # minimum mensuel de référence (vide : valeur du code)

    # ── MODE CABINET (multi-entreprises) ──────────────────────────────────
    # est_cabinet : ce tenant est un cabinet comptable qui gère plusieurs
    #   entreprises clientes (au lieu d'être une entreprise unique).
    # cabinet_id  : pour une entreprise gérée par un cabinet, référence le
    #   tenant-cabinet parent. NULL pour un tenant normal ou pour un cabinet.
    # Chaque entreprise reste un tenant à part entière (données cloisonnées) ;
    # le cabinet ne fait que les regrouper et permettre d'y accéder.
    est_cabinet = db.Column(db.Boolean, default=False, nullable=False)
    cabinet_id  = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=True)
    # Profil d'usage choisi à l'inscription (oriente l'interface, n'impose rien) :
    # ENTREPRISE (gère sa paie) | CABINET (gère plusieurs clients) |
    # MISE_A_DISPOSITION (met du personnel à disposition d'autres entreprises).
    type_compte = db.Column(db.String(20), default="ENTREPRISE")
    # Nombre maximum d'entreprises qu'un cabinet a le droit de gérer (palier
    # tarifaire souscrit). NULL/0 = palier par défaut. Voir paliers_cabinet.py.
    limite_entreprises = db.Column(db.Integer, nullable=True)

    # Entreprises rattachées à ce cabinet (relation auto-référente).
    entreprises_gerees = db.relationship(
        "Tenant", backref=db.backref("cabinet", remote_side=[id]),
        lazy=True, foreign_keys=[cabinet_id])

    utilisateurs = db.relationship("Utilisateur", backref="tenant", lazy=True, foreign_keys="Utilisateur.tenant_id")
    salaries     = db.relationship("Salarie", backref="tenant", lazy=True)
    periodes     = db.relationship("PeriodePaie", backref="tenant", lazy=True)
    categories   = db.relationship("CategorieEmploi", backref="tenant", lazy=True)

    TYPES_COMPTE = {
        "ENTREPRISE":        "Entreprise",
        "CABINET":           "Cabinet comptable",
        "MISE_A_DISPOSITION":"Entreprise de mise à disposition",
    }

    @property
    def type_compte_libelle(self):
        return self.TYPES_COMPTE.get(self.type_compte or "ENTREPRISE", "Entreprise")

    @property
    def est_mise_a_disposition(self):
        return (self.type_compte or "") == "MISE_A_DISPOSITION"

    @property
    def est_entreprise_geree(self):
        """True si ce tenant est une entreprise rattachée à un cabinet."""
        return self.cabinet_id is not None

    @property
    def tenant_abonnement(self):
        """Tenant qui porte l'abonnement : le cabinet pour une entreprise qu'il gère
        (statut, forfait et échéance du cabinet), sinon le tenant lui-même."""
        return self.cabinet if self.est_entreprise_geree and self.cabinet else self

    def synchroniser_abonnement_entreprises(self):
        """Cabinet : recopie son abonnement (forfait, échéance, statut) sur chacune des
        entreprises qu'il gère, pour qu'un renouvellement les couvre toutes d'un coup.
        Une suspension reste propre à chaque tenant : une entreprise suspendue à part par
        le super-admin le reste, et la suspension du cabinet n'est pas recopiée (elle
        bloque déjà l'accès par le cabinet). Retourne le nombre d'entreprises modifiées."""
        if not self.est_cabinet or self.id is None:
            return 0
        n = 0
        for e in Tenant.query.filter_by(cabinet_id=self.id).all():
            avant = (e.plan_id, e.date_expiration, e.statut, e.alerte_expiration_envoyee)
            e.plan_id = self.plan_id
            e.date_expiration = self.date_expiration
            e.alerte_expiration_envoyee = self.alerte_expiration_envoyee
            if "SUSPENDU" not in (e.statut, self.statut):
                e.statut = self.statut
            if (e.plan_id, e.date_expiration, e.statut, e.alerte_expiration_envoyee) != avant:
                n += 1
        return n

    @property
    def est_expire(self):
        """True si l'essai ou l'abonnement est arrivé à échéance.
        Un tenant sans date d'expiration n'expire jamais. En mode cabinet,
        une entreprise gérée hérite de l'échéance de son cabinet."""
        cible = self.cabinet if self.est_entreprise_geree and self.cabinet else self
        return bool(cible.date_expiration and cible.date_expiration < utcnow())

    @property
    def heures_avant_expiration(self):
        """Nombre d'heures avant l'échéance (None si pas de date, 0 si dépassée)."""
        cible = self.cabinet if self.est_entreprise_geree and self.cabinet else self
        if not cible.date_expiration:
            return None
        delta = (cible.date_expiration - utcnow()).total_seconds() / 3600.0
        return max(0.0, delta)

    @property
    def expire_dans_72h(self):
        """True si l'échéance est dans les 72 h à venir (pas encore expiré)."""
        h = self.heures_avant_expiration
        return h is not None and 0 < h <= 72

    @property
    def nb_entreprises_gerees(self):
        """Nombre d'entreprises gérées par ce cabinet (0 si non-cabinet)."""
        if not self.est_cabinet:
            return 0
        return Tenant.query.filter_by(cabinet_id=self.id).count()

    @property
    def limite_entreprises_effective(self):
        """Limite d'entreprises applicable à ce cabinet (palier par défaut si
        non définie explicitement)."""
        if self.limite_entreprises and self.limite_entreprises > 0:
            return self.limite_entreprises
        from paliers_cabinet import limite_par_defaut
        return limite_par_defaut()

    @property
    def peut_ajouter_entreprise(self):
        """True si le cabinet peut encore ajouter une entreprise (sous sa limite)."""
        if not self.est_cabinet:
            return False
        return self.nb_entreprises_gerees < self.limite_entreprises_effective

    def generate_token(self):
        """Génère un nouveau token API. Stocke uniquement son hash + un préfixe
        lisible ; renvoie le token en clair à afficher UNE SEULE fois à l'usager."""
        raw = secrets.token_hex(32)
        self.token_api_hash = hash_secret(raw)
        self.token_api = raw[:8] + "…"   # préfixe non secret, pour l'affichage
        return raw

    def generer_cle_liaison(self):
        """Clé de liaison (lecture seule de l'identité de l'entreprise) pour les
        applications Ameriack. Stocke le hash ; renvoie la clé en clair à
        afficher UNE SEULE fois. Régénérer invalide l'ancienne clé."""
        raw = "pgl_" + secrets.token_hex(32)
        self.cle_liaison_hash = hash_secret(raw)
        self.cle_liaison_prefixe = raw[:10] + "…"
        self.cle_liaison_date = utcnow()
        return raw

    def revoquer_cle_liaison(self):
        self.cle_liaison_hash = None
        self.cle_liaison_prefixe = None
        self.cle_liaison_date = None
        self.liaison_paie_autorisee = False

    @staticmethod
    def hash_token(raw: str) -> str:
        return hash_secret(raw)

    @property
    def seuil_hs(self) -> float:
        """Seuil hebdomadaire de déclenchement des heures sup (float, défaut 40h).

        Retourne toujours une valeur bornée entre 40h (minimum légal) et 48h
        (maximum contractuel), même si la colonne est NULL ou hors plage.
        """
        try:
            v = float(self.seuil_heures_sup_hebdo) if self.seuil_heures_sup_hebdo is not None else 40.0
        except (TypeError, ValueError):
            v = 40.0
        return max(40.0, min(v, 48.0))

    @property
    def nb_salaries_actifs(self):
        return Salarie.query.filter_by(tenant_id=self.id, statut="ACTIF").count()

    @property
    def nb_journaliers_actifs(self):
        return Journalier.query.filter_by(tenant_id=self.id, statut="ACTIF").count()

    @property
    def nb_total_employes(self):
        return self.nb_salaries_actifs + self.nb_journaliers_actifs

    @property
    def est_dans_limite(self):
        if not self.plan or not self.plan.max_salaries: return True
        return self.nb_salaries_actifs < self.plan.max_salaries

    @property
    def peut_ajouter_employe(self):
        if not self.plan or not self.plan.max_salaries: return True
        return self.nb_total_employes < self.plan.max_salaries

    @property
    def quota_employes_info(self):
        if not self.plan or not self.plan.max_salaries:
            return {"max": None, "actuel": self.nb_total_employes, "plein": False}
        actuel = self.nb_total_employes
        return {
            "max":         self.plan.max_salaries,
            "actuel":      actuel,
            "salaries":    self.nb_salaries_actifs,
            "journaliers": self.nb_journaliers_actifs,
            "restant":     max(0, self.plan.max_salaries - actuel),
            "plein":       actuel >= self.plan.max_salaries,
            "pct":         min(100, int(actuel / self.plan.max_salaries * 100)),
        }

    def to_dict(self):
        return {c.name: str(getattr(self,c.name)) if isinstance(getattr(self,c.name),(date,datetime))
                else getattr(self,c.name) for c in self.__table__.columns}


class Utilisateur(db.Model, UserMixin):
    __tablename__ = "utilisateurs"
    id                 = db.Column(db.Integer, primary_key=True)
    nom                = db.Column(db.String(100), nullable=False)
    prenom             = db.Column(db.String(100), nullable=False)
    email              = db.Column(db.String(200), nullable=False, unique=True)
    mot_de_passe_hash  = db.Column(db.String(256), nullable=False)
    role               = db.Column(db.String(30), default="GESTIONNAIRE")
    tenant_id          = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=True)
    actif              = db.Column(db.Boolean, default=True)
    derniere_connexion = db.Column(db.DateTime)
    date_creation      = db.Column(db.DateTime, default=utcnow)
    # ✅ Reset mot de passe
    reset_token              = db.Column(db.String(200))
    reset_token_expiry       = db.Column(db.DateTime)
    # ── Confirmation email inscription ────────────────────────────────────
    email_verifie            = db.Column(db.Boolean, default=False)
    token_confirmation       = db.Column(db.String(200))
    token_confirmation_expiry= db.Column(db.DateTime)
    # ── Changement email ─────────────────────────────────────────────────
    nouvel_email_en_attente  = db.Column(db.String(200))
    token_changement_email   = db.Column(db.String(200))
    token_changement_expiry  = db.Column(db.DateTime)
    # ── Suivi connexion ───────────────────────────────────────────────────
    derniere_activite        = db.Column(db.DateTime)
    nb_echecs_connexion      = db.Column(db.Integer, default=0)
    compte_bloque_jusqu      = db.Column(db.DateTime)
    # ── 2FA par email (code à usage unique) ───────────────────────────────────
    otp_code_hash            = db.Column(db.String(256))
    otp_expiry               = db.Column(db.DateTime)
    otp_tentatives           = db.Column(db.Integer, default=0)
    # 2FA optionnelle activée par l'utilisateur (super-admin : toujours active).
    twofa_active             = db.Column(db.Boolean, default=False)
    twofa_methode            = db.Column(db.String(10), default="email")  # email | totp
    twofa_secret             = db.Column(db.String(64))   # secret TOTP (base32)
    backup_codes_hash        = db.Column(db.Text)         # JSON : liste de codes de secours hachés
    # ── Jeton de session ──────────────────────────────────────────────────────
    # Régénéré à chaque changement de mot de passe : invalide toutes les sessions
    # ouvertes (anti-détournement de session après reset / vol de mot de passe).
    session_token            = db.Column(db.String(32))

    def get_id(self):
        """Identité de session = id + jeton. Le changement de mot de passe fait
        tourner le jeton, ce qui invalide les sessions précédentes."""
        return f"{self.id}.{self.session_token or ''}"

    def _rotate_session_token(self):
        self.session_token = secrets.token_hex(16)

    def set_password(self, pw):
        self.mot_de_passe_hash = generate_password_hash(pw)
        self._rotate_session_token()
    def check_password(self, pw): return check_password_hash(self.mot_de_passe_hash, pw)

    def set_otp(self, code):
        """Enregistre le code 2FA (haché) avec une validité de 10 minutes."""
        self.otp_code_hash = generate_password_hash(code)
        self.otp_expiry    = utcnow() + timedelta(minutes=10)
        self.otp_tentatives = 0

    def check_otp(self, code):
        """Vérifie le code 2FA : non expiré et correspondant."""
        if not self.otp_code_hash or not self.otp_expiry:
            return False
        if utcnow() > self.otp_expiry:
            return False
        return check_password_hash(self.otp_code_hash, code)

    def clear_otp(self):
        self.otp_code_hash = None
        self.otp_expiry    = None
        self.otp_tentatives = 0

    # ── TOTP (application d'authentification) ────────────────────────────────
    def generer_totp_secret(self):
        """Génère et enregistre un nouveau secret TOTP (base32). À confirmer ensuite."""
        import pyotp
        self.twofa_secret = pyotp.random_base32()
        return self.twofa_secret

    def totp_uri(self, emetteur="PaieGabon"):
        """URI otpauth:// pour le QR code (à scanner avec Google Authenticator)."""
        import pyotp
        return pyotp.totp.TOTP(self.twofa_secret).provisioning_uri(
            name=self.email, issuer_name=emetteur)

    def verify_totp(self, code):
        """Vérifie un code TOTP (tolérance ±1 fenêtre pour le décalage d'horloge)."""
        import pyotp
        if not self.twofa_secret:
            return False
        try:
            return pyotp.TOTP(self.twofa_secret).verify(str(code).strip(), valid_window=2)
        except Exception:
            return False

    def generer_codes_secours(self, n=8):
        """Génère n codes de secours, stocke leur haché, renvoie les codes en clair (à afficher UNE fois)."""
        import secrets, json
        codes = [f"{secrets.randbelow(10**4):04d}-{secrets.randbelow(10**4):04d}" for _ in range(n)]
        self.backup_codes_hash = json.dumps([generate_password_hash(c) for c in codes])
        return codes

    def verify_code_secours(self, code):
        """Vérifie un code de secours ; s'il est valide, il est consommé (usage unique)."""
        import json
        if not self.backup_codes_hash:
            return False
        try:
            hashes = json.loads(self.backup_codes_hash)
        except Exception:
            return False
        code = str(code).strip()
        for h in hashes:
            if check_password_hash(h, code):
                hashes.remove(h)
                self.backup_codes_hash = json.dumps(hashes)
                return True
        return False


    @property
    def nom_complet(self): return f"{self.prenom} {self.nom}"

    @property
    def role_normalized(self): return (self.role or "").strip().upper()

    @property
    def is_super_admin(self): return self.role_normalized == "SUPER_ADMIN"

    @property
    def is_tenant_admin(self): return self.role_normalized in ("SUPER_ADMIN", "TENANT_ADMIN")

    # ══════════════════════════════════════════════════════════════════════
    # SYSTÈME DE PERMISSIONS GRANULAIRES
    # ══════════════════════════════════════════════════════════════════════
    # Rôles disponibles :
    #   TENANT_ADMIN  → tout faire (paramètres, utilisateurs, données)
    #   RH            → salariés, bulletins, congés, acomptes, pointages
    #   COMPTABLE     → lecture seule + exports (Excel, Sage, CNSS)
    #   DIRECTEUR     → tableau de bord, rapports, statistiques uniquement
    #   GESTIONNAIRE  → ancien rôle — équivalent RH (compatibilité)
    # ══════════════════════════════════════════════════════════════════════

    # Matrice des permissions par rôle
    _PERMISSIONS = {
        "SUPER_ADMIN":  {"all"},
        "TENANT_ADMIN": {"all"},
        "RH":           {"view_dashboard","view_salaries","edit_salaries","view_bulletins",
                         "edit_bulletins","view_conges","edit_conges","view_acomptes",
                         "edit_acomptes","view_periodes","edit_periodes","view_pointage",
                         "edit_pointage","view_journaliers","edit_journaliers",
                         "view_sites","view_rapports","view_declaration","export_excel"},
        "COMPTABLE":    {"view_dashboard","view_salaries","view_bulletins","view_conges",
                         "view_acomptes","view_periodes","view_pointage","view_journaliers",
                         "view_sites","view_rapports","view_declaration",
                         "export_excel","export_sage","export_cnss"},
        "DIRECTEUR":    {"view_dashboard","view_rapports","view_salaries","view_bulletins",
                         "view_declaration","view_journaliers","view_sites"},
        "GESTIONNAIRE": {"view_dashboard","view_salaries","edit_salaries","view_bulletins",
                         "edit_bulletins","view_conges","edit_conges","view_acomptes",
                         "edit_acomptes","view_periodes","edit_periodes","view_pointage",
                         "edit_pointage","view_journaliers","edit_journaliers",
                         "view_sites","view_rapports","view_declaration","export_excel"},
    }

    def has_permission(self, perm: str) -> bool:
        """Vérifie si l'utilisateur a une permission spécifique."""
        perms = self._PERMISSIONS.get(self.role_normalized, set())
        return "all" in perms or perm in perms

    @property
    def can_edit(self):
        """Peut modifier des données (salariés, bulletins, etc.)."""
        return self.has_permission("edit_salaries") or self.is_tenant_admin

    @property
    def can_edit_bulletins(self):
        return self.has_permission("edit_bulletins") or self.is_tenant_admin

    @property
    def can_view_only(self):
        """Accès lecture seule uniquement (Comptable, Directeur)."""
        return self.role_normalized in ("COMPTABLE", "DIRECTEUR")

    @property
    def can_export(self):
        return self.has_permission("export_excel") or self.is_tenant_admin

    @property
    def can_export_sage(self):
        return self.has_permission("export_sage") or self.is_tenant_admin

    @property
    def can_manage_parametres(self):
        """Seul l'admin peut modifier les paramètres de l'entreprise."""
        return self.is_tenant_admin

    @property
    def can_manage_users(self):
        """Seul l'admin peut gérer les utilisateurs."""
        return self.is_tenant_admin

    @property
    def role_label(self):
        """Libellé lisible du rôle."""
        labels = {
            "SUPER_ADMIN":  "Super Admin",
            "TENANT_ADMIN": "Administrateur",
            "RH":           "Responsable RH",
            "COMPTABLE":    "Comptable",
            "DIRECTEUR":    "Directeur",
            "GESTIONNAIRE": "Gestionnaire",
        }
        return labels.get(self.role_normalized, self.role)

    @property
    def role_color(self):
        """Couleur badge du rôle."""
        colors = {
            "SUPER_ADMIN":  "purple",
            "TENANT_ADMIN": "red",
            "RH":           "blue",
            "COMPTABLE":    "green",
            "DIRECTEUR":    "orange",
            "GESTIONNAIRE": "gray",
        }
        return colors.get(self.role_normalized, "gray")

    def to_dict(self):
        return {"id":self.id,"nom":self.nom,"prenom":self.prenom,
                "email":self.email,"role":self.role,"role_label":self.role_label,
                "tenant_id":self.tenant_id,"actif":self.actif}


class CategorieEmploi(db.Model):
    __tablename__ = "categories_emploi"
    id              = db.Column(db.Integer, primary_key=True)
    tenant_id       = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    code            = db.Column(db.String(10), nullable=False)
    libelle         = db.Column(db.String(100))
    salaire_minimum = db.Column(db.Numeric(15,2))
    description     = db.Column(db.Text)
    salaries = db.relationship("Salarie", backref="categorie", lazy=True)
    __table_args__ = (db.UniqueConstraint("tenant_id","code"),)
    def to_dict(self):
        return {c.name: float(getattr(self,c.name)) if hasattr(getattr(self,c.name),"__float__") and getattr(self,c.name) is not None
                else getattr(self,c.name) for c in self.__table__.columns}


class Site(db.Model):
    """Site / chantier / agence d'une entreprise (tenant)."""
    __tablename__ = "sites"

    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    nom         = db.Column(db.String(200), nullable=False)
    code        = db.Column(db.String(30))
    adresse     = db.Column(db.String(300))
    ville       = db.Column(db.String(100))
    responsable = db.Column(db.String(200))
    telephone   = db.Column(db.String(30))
    description = db.Column(db.Text)
    actif       = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=utcnow)
    # ── Géolocalisation du chantier (pour l'alerte géo du pointage mobile) ──
    latitude    = db.Column(db.Numeric(9, 6))
    longitude   = db.Column(db.Numeric(9, 6))
    rayon_m     = db.Column(db.Integer, default=200)   # rayon de tolérance en mètres

    tenant       = db.relationship("Tenant", backref="sites")
    affectations = db.relationship("AffectationSite", backref="site", lazy=True,
                                   cascade="all, delete-orphan")

    @property
    def nb_actifs(self):
        return AffectationSite.query.filter_by(site_id=self.id, actif=True).count()

    def __repr__(self): return f"<Site {self.nom}>"


class AffectationSite(db.Model):
    """Affectation d'un salarié ou journalier à un site, avec historique complet."""
    __tablename__ = "affectations_sites"

    id              = db.Column(db.Integer, primary_key=True)
    tenant_id       = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    site_id         = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=False)

    # Un seul des deux est renseigné
    salarie_id      = db.Column(db.Integer, db.ForeignKey("salaries.id"),  nullable=True)
    journalier_id   = db.Column(db.Integer, db.ForeignKey("journaliers.id"), nullable=True)

    date_debut      = db.Column(db.Date, nullable=False, default=date.today)
    date_fin        = db.Column(db.Date, nullable=True)   # None = affectation en cours
    actif           = db.Column(db.Boolean, default=True) # False = transféré ou sorti
    motif           = db.Column(db.String(300))           # Motif de la permutation
    date_creation   = db.Column(db.DateTime, default=utcnow)
    cree_par        = db.Column(db.String(200))           # Email de l'utilisateur qui a fait l'action

    # Relations
    salarie    = db.relationship("Salarie",    backref="affectations", foreign_keys=[salarie_id])
    journalier = db.relationship("Journalier", backref="affectations", foreign_keys=[journalier_id])

    @property
    def travailleur(self):
        return self.salarie or self.journalier

    @property
    def type_travailleur(self):
        return "MENSUEL" if self.salarie_id else "JOURNALIER"

    @property
    def nom_travailleur(self):
        t = self.travailleur
        return t.nom_complet if t else "—"

    def __repr__(self):
        return f"<Affectation site={self.site_id} sal={self.salarie_id} jour={self.journalier_id}>"


class Salarie(db.Model):
    __tablename__ = "salaries"
    id                     = db.Column(db.Integer, primary_key=True)
    tenant_id              = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    matricule              = db.Column(db.String(50), nullable=False)
    categorie_id           = db.Column(db.Integer, db.ForeignKey("categories_emploi.id"))
    nom                    = db.Column(db.String(100), nullable=False)
    prenom                 = db.Column(db.String(100), nullable=False)
    telephone              = db.Column(db.String(20))
    email                  = db.Column(db.String(200))
    adresse                = db.Column(db.String(300))
    nationalite            = db.Column(db.String(100), default="GABONAISE")
    sexe                   = db.Column(db.String(1))
    date_naissance         = db.Column(db.Date)
    date_embauche          = db.Column(db.Date, nullable=False)
    date_cessation         = db.Column(db.Date)
    date_prochaine_visite_medicale = db.Column(db.Date)   # alerte RH : prochaine visite médicale
    situation_matrimoniale = db.Column(db.String(50))
    nb_enfants             = db.Column(db.Integer, default=0)
    nb_enfants_moins_16ans = db.Column(db.Integer, default=0)
    nombre_parts           = db.Column(db.Numeric(4,1), default=1)
    numero_cnss            = db.Column(db.String(30))
    # ── Champs pour la Déclaration Annuelle des Salaires (DGI) ──────────────
    nif                    = db.Column(db.String(30))   # NIF fiscal du salarié
    niveau                 = db.Column(db.String(10))   # Niveau de classification (DAS)
    code_emploi            = db.Column(db.String(10))   # Code emploi DGI (repli : code catégorie)
    numero_cnamgs          = db.Column(db.String(30))
    emploi                 = db.Column(db.String(200))
    echelon                = db.Column(db.String(20))   # échelon dans la catégorie (texte libre, gestion de carrière)
    # Poste de nuit (21h-6h) : ouvre droit à la prime de nuit conventionnelle
    # (ex. Hôtellerie Art. 39.2 : 20 % du salaire de base).
    travail_de_nuit        = db.Column(db.Boolean, default=False)
    assujetti_cnamgs       = db.Column(db.Boolean, default=True)
    type_rupture           = db.Column(db.String(100))
    statut                 = db.Column(db.String(20), default="ACTIF")
    date_creation          = db.Column(db.DateTime, default=utcnow)
    mode_paiement          = db.Column(db.String(30), default="ESPECES")  # ESPECES | VIREMENT
    # Coordonnées bancaires (ordres de virement des salaires, 29/10/2026)
    banque                 = db.Column(db.String(100))
    rib                    = db.Column(db.String(50))     # RIB ou IBAN, tel que fourni par la banque
    titulaire_compte       = db.Column(db.String(150))    # si différent du salarié
    date_modification      = db.Column(db.DateTime, onupdate=utcnow)

    bulletins = db.relationship("BulletinPaie", backref="salarie", lazy=True)
    contrats  = db.relationship("Contrat", backref="salarie", lazy=True)
    __table_args__ = (
        db.UniqueConstraint("tenant_id","matricule"),
        db.Index("idx_salaries_tenant_statut", "tenant_id", "statut"),
        db.Index("idx_salaries_tenant_embauche", "tenant_id", "date_embauche"),
    )

    @property
    def nom_complet(self): return f"{self.nom} {self.prenom}"

    def to_dict(self):
        d = {}
        for c in self.__table__.columns:
            val = getattr(self, c.name)
            d[c.name] = str(val) if isinstance(val,(date,datetime)) else (float(val) if hasattr(val,"__float__") and val is not None else val)
        d["nom_complet"] = self.nom_complet
        d["categorie_code"] = self.categorie.code if self.categorie else None
        return d


class Contrat(db.Model):
    __tablename__ = "contrats"
    id           = db.Column(db.Integer, primary_key=True)
    tenant_id    = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id   = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    type_contrat = db.Column(db.String(50), default="CDI")
    date_debut   = db.Column(db.Date, nullable=False)
    date_fin     = db.Column(db.Date)
    date_fin_essai = db.Column(db.Date)   # fin de la période d'essai (alerte RH)
    salaire_base = db.Column(db.Numeric(15,2), nullable=False)
    elements_recurrents = db.Column(db.Text)   # JSON {champ: montant} — primes récurrentes pré-remplies à la saisie
    poste        = db.Column(db.String(200))
    categorie_id = db.Column(db.Integer, db.ForeignKey("categories_emploi.id"))
    actif        = db.Column(db.Boolean, default=True)
    __table_args__ = (
        # Le contrat est presque toujours cherché par salarié (paie, fiche, conversion),
        # souvent filtré sur actif → index dédiés (le point non couvert jusqu'ici).
        db.Index("idx_contrats_salarie_actif", "salarie_id", "actif"),
        db.Index("idx_contrats_tenant_salarie", "tenant_id", "salarie_id"),
    )
    def to_dict(self):
        d = {c.name: getattr(self,c.name) for c in self.__table__.columns}
        for k in ["date_debut","date_fin"]:
            if d[k]: d[k] = str(d[k])
        if d["salaire_base"]: d["salaire_base"] = float(d["salaire_base"])
        return d


class PeriodePaie(db.Model):
    __tablename__ = "periodes_paie"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    annee          = db.Column(db.Integer, nullable=False)
    mois           = db.Column(db.Integer, nullable=False)
    libelle_mois   = db.Column(db.String(20), nullable=False)
    trimestre      = db.Column(db.String(10))
    date_ouverture = db.Column(db.DateTime)
    date_cloture   = db.Column(db.DateTime)
    statut         = db.Column(db.String(20), default="OUVERT")
    bulletins = db.relationship("BulletinPaie", backref="periode", lazy=True)
    __table_args__ = (db.UniqueConstraint("tenant_id","annee","mois"),)
    MOIS_NOMS = ["","JANVIER","FÉVRIER","MARS","AVRIL","MAI","JUIN",
                 "JUILLET","AOÛT","SEPTEMBRE","OCTOBRE","NOVEMBRE","DÉCEMBRE"]
    @property
    def libelle_complet(self): return f"{self.libelle_mois} {self.annee}"
    def to_dict(self):
        return {c.name: getattr(self,c.name) for c in self.__table__.columns}


class BulletinPaie(db.Model):
    __tablename__ = "bulletins_paie"
    id                    = db.Column(db.Integer, primary_key=True)
    tenant_id             = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id            = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    periode_id            = db.Column(db.Integer, db.ForeignKey("periodes_paie.id"), nullable=False)
    # Numéro séquentiel immuable attribué à la validation (ex. BP-2026-000042).
    # Unique par tenant : un document de paie officiel ne change jamais de numéro.
    numero                = db.Column(db.String(30))
    numero_seq            = db.Column(db.Integer)
    mode_paiement         = db.Column(db.String(30), default="ESPECES")  # figé au paiement
    date_paiement         = db.Column(db.Date)   # date réelle du versement (saisie au paiement)
    nb_jours_travailles   = db.Column(db.Integer)
    salaire_base          = db.Column(db.Numeric(15,2), nullable=False)
    heures_sup_10         = db.Column(db.Numeric(15,2), default=0)
    heures_sup_30         = db.Column(db.Numeric(15,2), default=0)
    heures_sup_30b        = db.Column(db.Numeric(15,2), default=0)  # repos/férié jour (Pétrole)
    heures_sup_40         = db.Column(db.Numeric(15,2), default=0)
    heures_sup_70         = db.Column(db.Numeric(15,2), default=0)
    heures_sup_fj         = db.Column(db.Numeric(15,2), default=0)  # férié chômé payé — jour
    heures_sup_fn         = db.Column(db.Numeric(15,2), default=0)  # férié chômé payé — nuit
    absences              = db.Column(db.Numeric(15,2), default=0)
    sursalaire            = db.Column(db.Numeric(15,2), default=0)
    prime_caisse          = db.Column(db.Numeric(15,2), default=0)
    carburant             = db.Column(db.Numeric(15,2), default=0)
    prime_anciennete      = db.Column(db.Numeric(15,2), default=0)
    # Prime de nuit conventionnelle (ex. Hôtellerie Art. 39.2 : 20 % du base).
    prime_nuit            = db.Column(db.Numeric(15,2), default=0)
    indem_logement        = db.Column(db.Numeric(15,2), default=0)
    indem_domesticite     = db.Column(db.Numeric(15,2), default=0)
    indem_eau_electricite = db.Column(db.Numeric(15,2), default=0)
    indem_nourriture      = db.Column(db.Numeric(15,2), default=0)
    prime_rendement       = db.Column(db.Numeric(15,2), default=0)
    prime_assiduité       = db.Column(db.Numeric(15,2), default=0)
    prime_qualite         = db.Column(db.Numeric(15,2), default=0)
    prime_performance     = db.Column(db.Numeric(15,2), default=0)
    prime_transport       = db.Column(db.Numeric(15,2), default=0)
    prime_responsabilite  = db.Column(db.Numeric(15,2), default=0)
    allocations_conge          = db.Column(db.Numeric(15,2), default=0)
    # ✅ Nouveaux éléments de salaire
    indem_compensatrice_conge   = db.Column(db.Numeric(15,2), default=0)
    indem_services_rendus       = db.Column(db.Numeric(15,2), default=0)
    indem_compensatrice_preavis = db.Column(db.Numeric(15,2), default=0)
    indem_licenciement          = db.Column(db.Numeric(15,2), default=0)
    salaire_brut          = db.Column(db.Numeric(15,2), nullable=False)
    base_cnss             = db.Column(db.Numeric(15,2))
    cnss_salarie          = db.Column(db.Numeric(15,2))
    cnss_patronale        = db.Column(db.Numeric(15,2))
    base_cnamgs           = db.Column(db.Numeric(15,2))
    cnamgs_salarie        = db.Column(db.Numeric(15,2))
    cnamgs_patronale      = db.Column(db.Numeric(15,2))
    fnh                   = db.Column(db.Numeric(15,2))
    cfp                   = db.Column(db.Numeric(15,2))
    base_tcs              = db.Column(db.Numeric(15,2))
    tcs                   = db.Column(db.Numeric(15,2))
    base_irpp             = db.Column(db.Numeric(15,2))
    irpp                  = db.Column(db.Numeric(15,2))
    net_avant_irpp        = db.Column(db.Numeric(15,2))
    salaire_net           = db.Column(db.Numeric(15,2))
    prime_panier          = db.Column(db.Numeric(15,2), default=0)
    indem_transport       = db.Column(db.Numeric(15,2), default=0)
    indem_representation  = db.Column(db.Numeric(15,2), default=0)
    prime_salisure        = db.Column(db.Numeric(15,2), default=0)
    acompte               = db.Column(db.Numeric(15,2), default=0)
    retenue_pret          = db.Column(db.Numeric(15,2), default=0)  # remboursement prêt du mois
    net_a_payer           = db.Column(db.Numeric(15,2), nullable=False)
    # Ajustement d'arrondi saisi par l'admin pour obtenir un net rond.
    # Le net affiché = net calculé + ajustement_arrondi. Une ligne dédiée
    # apparaît sur le bulletin pour la transparence (valeur probante conservée).
    ajustement_arrondi    = db.Column(db.Numeric(15,2), default=0)

    # Base et Taux saisis manuellement pour chaque rubrique
    base_salaire_base                   = db.Column(db.Numeric(15,2), default=0)
    taux_salaire_base                   = db.Column(db.String(20), default='')
    base_heures_sup_10                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_10                  = db.Column(db.String(20), default='')
    base_heures_sup_30                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_30                  = db.Column(db.String(20), default='')
    base_heures_sup_30b                 = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_30b                 = db.Column(db.String(20), default='')
    base_heures_sup_40                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_40                  = db.Column(db.String(20), default='')
    base_heures_sup_70                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_70                  = db.Column(db.String(20), default='')
    base_heures_sup_fj                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_fj                  = db.Column(db.String(20), default='')
    base_heures_sup_fn                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_fn                  = db.Column(db.String(20), default='')
    base_absences                       = db.Column(db.Numeric(15,2), default=0)
    taux_absences                       = db.Column(db.String(20), default='')
    base_sursalaire                     = db.Column(db.Numeric(15,2), default=0)
    taux_sursalaire                     = db.Column(db.String(20), default='')
    base_prime_caisse                   = db.Column(db.Numeric(15,2), default=0)
    taux_prime_caisse                   = db.Column(db.String(20), default='')
    base_carburant                      = db.Column(db.Numeric(15,2), default=0)
    taux_carburant                      = db.Column(db.String(20), default='')
    base_prime_anciennete               = db.Column(db.Numeric(15,2), default=0)
    taux_prime_anciennete               = db.Column(db.String(20), default='')
    base_indem_logement                 = db.Column(db.Numeric(15,2), default=0)
    taux_indem_logement                 = db.Column(db.String(20), default='')
    base_indem_domesticite              = db.Column(db.Numeric(15,2), default=0)
    taux_indem_domesticite              = db.Column(db.String(20), default='')
    base_indem_eau_electricite          = db.Column(db.Numeric(15,2), default=0)
    taux_indem_eau_electricite          = db.Column(db.String(20), default='')
    base_indem_nourriture               = db.Column(db.Numeric(15,2), default=0)
    taux_indem_nourriture               = db.Column(db.String(20), default='')
    base_prime_transport                = db.Column(db.Numeric(15,2), default=0)
    taux_prime_transport                = db.Column(db.String(20), default='')
    base_prime_responsabilite           = db.Column(db.Numeric(15,2), default=0)
    taux_prime_responsabilite           = db.Column(db.String(20), default='')
    base_prime_rendement                = db.Column(db.Numeric(15,2), default=0)
    taux_prime_rendement                = db.Column(db.String(20), default='')
    base_prime_assiduité                = db.Column(db.Numeric(15,2), default=0)
    taux_prime_assiduité                = db.Column(db.String(20), default='')
    base_prime_qualite                  = db.Column(db.Numeric(15,2), default=0)
    taux_prime_qualite                  = db.Column(db.String(20), default='')
    base_prime_performance              = db.Column(db.Numeric(15,2), default=0)
    taux_prime_performance              = db.Column(db.String(20), default='')
    base_allocations_conge              = db.Column(db.Numeric(15,2), default=0)
    taux_allocations_conge              = db.Column(db.String(20), default='')
    base_indem_compensatrice_conge      = db.Column(db.Numeric(15,2), default=0)
    taux_indem_compensatrice_conge      = db.Column(db.String(20), default='')
    base_indem_services_rendus          = db.Column(db.Numeric(15,2), default=0)
    taux_indem_services_rendus          = db.Column(db.String(20), default='')
    base_indem_compensatrice_preavis    = db.Column(db.Numeric(15,2), default=0)
    taux_indem_compensatrice_preavis    = db.Column(db.String(20), default='')
    base_indem_licenciement             = db.Column(db.Numeric(15,2), default=0)
    taux_indem_licenciement             = db.Column(db.String(20), default='')
    statut                = db.Column(db.String(20), default="BROUILLON")
    date_creation         = db.Column(db.DateTime, default=utcnow)
    date_validation       = db.Column(db.DateTime)
    __table_args__ = (
        db.UniqueConstraint("tenant_id","salarie_id","periode_id"),
        db.UniqueConstraint("tenant_id","numero", name="uq_bulletin_numero"),
        db.Index("idx_bulletins_tenant_periode", "tenant_id", "periode_id"),
        db.Index("idx_bulletins_tenant_statut",  "tenant_id", "statut"),
    )

    def to_dict(self):
        d = {}
        for c in self.__table__.columns:
            val = getattr(self, c.name)
            if isinstance(val,(date,datetime)): d[c.name] = str(val)
            elif hasattr(val,"__float__") and val is not None: d[c.name] = float(val)
            else: d[c.name] = val
        d["salarie_nom"]     = self.salarie.nom_complet if self.salarie else None
        d["periode_libelle"] = self.periode.libelle_complet if self.periode else None
        return d


class RubriquePaie(db.Model):
    __tablename__ = "rubriques_paie"
    id              = db.Column(db.Integer, primary_key=True)
    code            = db.Column(db.String(20), nullable=False, unique=True)
    libelle         = db.Column(db.String(200), nullable=False)
    type            = db.Column(db.String(30))
    taux_salarie    = db.Column(db.Numeric(8,4))
    taux_patronal   = db.Column(db.Numeric(8,4))
    plafond_mensuel = db.Column(db.Numeric(15,2))
    actif           = db.Column(db.Boolean, default=True)
    def to_dict(self):
        d = {c.name: getattr(self,c.name) for c in self.__table__.columns}
        for k in ["taux_salarie","taux_patronal","plafond_mensuel"]:
            if d[k] is not None: d[k] = float(d[k])
        return d


class ComposantPaie(db.Model):
    """Composant de salaire personnalisé, créé par l'entreprise (tenant).

    Permet à chaque entreprise de définir ses propres primes / indemnités (gains)
    ou retenues, qui s'ajouteront au calcul du bulletin. Chaque composant porte
    son traitement social/fiscal : soumis ou non à la CNSS, à la CNAMGS, à l'IRPP.
    Le montant lui-même est saisi ponctuellement sur chaque bulletin.
    """
    __tablename__ = "composants_paie"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    libelle       = db.Column(db.String(120), nullable=False)
    sens          = db.Column(db.String(10), nullable=False, default="GAIN")  # GAIN | RETENUE
    soumis_cnss   = db.Column(db.Boolean, default=True)
    soumis_cnamgs = db.Column(db.Boolean, default=True)
    soumis_irpp   = db.Column(db.Boolean, default=True)
    entre_dans_brut = db.Column(db.Boolean, default=True)      # compte dans le total brut
    position      = db.Column(db.String(4), default="BAS")     # HAUT | BAS (section bulletin)
    actif         = db.Column(db.Boolean, default=True)
    ordre         = db.Column(db.Integer, default=0)
    cree_le       = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (db.Index("idx_composants_tenant", "tenant_id", "actif"),)

    @property
    def est_gain(self):
        return (self.sens or "GAIN").upper() == "GAIN"


class BulletinComposant(db.Model):
    """Valeur d'un composant personnalisé sur un bulletin précis.

    On stocke un INSTANTANÉ (libellé, sens, traitement social/fiscal) afin que les
    bulletins déjà émis ne changent jamais, même si le composant est modifié ou
    supprimé par la suite.
    """
    __tablename__ = "bulletin_composants"
    id            = db.Column(db.Integer, primary_key=True)
    bulletin_id   = db.Column(db.Integer, db.ForeignKey("bulletins_paie.id"), nullable=False)
    composant_id  = db.Column(db.Integer, db.ForeignKey("composants_paie.id"))
    libelle       = db.Column(db.String(120), nullable=False)
    sens          = db.Column(db.String(10), nullable=False, default="GAIN")
    position      = db.Column(db.String(4), default="BAS")   # HAUT | BAS (instantané)
    soumis_cnss   = db.Column(db.Boolean, default=True)
    soumis_cnamgs = db.Column(db.Boolean, default=True)
    soumis_irpp   = db.Column(db.Boolean, default=True)
    base          = db.Column(db.Numeric(15, 2))
    taux          = db.Column(db.Numeric(8, 4))
    montant       = db.Column(db.Numeric(15, 2), nullable=False, default=0)
    rappel_id     = db.Column(db.Integer, db.ForeignKey("rappels_salaire.id"))   # composant issu d'un rappel
    __table_args__ = (db.Index("idx_bulcomp_bulletin", "bulletin_id"),)

    @property
    def est_gain(self):
        return (self.sens or "GAIN").upper() == "GAIN"

    @property
    def montant_signe(self):
        """Montant signé : + pour un gain, - pour une retenue."""
        m = float(self.montant or 0)
        return m if self.est_gain else -m


class Conge(db.Model):
    __tablename__ = "conges"
    id           = db.Column(db.Integer, primary_key=True)
    tenant_id    = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id   = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    annee        = db.Column(db.Integer, nullable=False)
    jours_acquis = db.Column(db.Numeric(6,2), default=0)
    jours_pris   = db.Column(db.Numeric(6,2), default=0)
    date_depart  = db.Column(db.Date)
    date_retour  = db.Column(db.Date)
    type_conge   = db.Column(db.String(50), default="ANNUEL")
    statut       = db.Column(db.String(20), default="DEMANDÉ")
    origine      = db.Column(db.String(10))      # PORTAIL = demandée par le salarié sur son espace
    commentaire  = db.Column(db.String(500))     # message du salarié
    date_demande = db.Column(db.DateTime)
    salarie = db.relationship("Salarie", backref="conges")
    @property
    def jours_restants(self): return float(self.jours_acquis or 0) - float(self.jours_pris or 0)


class PlanningConges(db.Model):
    """Planning prévisionnel des congés d'une année : les périodes souhaitées par les
    salariés (portail) ou saisies par le RH, retenues ou refusées par le RH, puis
    validées par la direction. Une période retenue devient une vraie demande de congé
    (Conge) quand le RH la transforme ; le planning ne touche jamais aux soldes."""
    __tablename__ = "plannings_conges"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    annee         = db.Column(db.Integer, nullable=False)
    statut        = db.Column(db.String(12), default="BROUILLON")   # BROUILLON | VALIDE
    # Alerte : plus de N % de l'effectif d'un même chantier (ou de l'entreprise) absent le même jour
    seuil_absents_pct = db.Column(db.Integer, default=30)
    # Date jusqu'à laquelle les salariés peuvent proposer leurs dates sur le portail (facultatif)
    date_limite_propositions = db.Column(db.Date)
    commentaire   = db.Column(db.String(500))
    valide_par_id = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    date_validation = db.Column(db.DateTime)
    date_creation = db.Column(db.DateTime, default=utcnow)
    valide_par = db.relationship("Utilisateur", foreign_keys=[valide_par_id])
    periodes = db.relationship("PrevisionConge", backref="planning", cascade="all, delete-orphan",
                               order_by="(PrevisionConge.date_debut, PrevisionConge.id)")
    __table_args__ = (
        db.UniqueConstraint("tenant_id", "annee", name="uq_planning_conges_annee"),
    )


class PrevisionConge(db.Model):
    """Une période de congé prévue dans le planning annuel."""
    __tablename__ = "previsions_conges"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    planning_id   = db.Column(db.Integer, db.ForeignKey("plannings_conges.id"), nullable=False)
    salarie_id    = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    date_debut    = db.Column(db.Date, nullable=False)
    date_fin      = db.Column(db.Date, nullable=False)          # inclus
    origine       = db.Column(db.String(10), default="RH")      # RH | PORTAIL
    statut        = db.Column(db.String(12), default="PROPOSEE")  # PROPOSEE | RETENUE | REFUSEE
    commentaire   = db.Column(db.String(500))                   # message du salarié
    motif_refus   = db.Column(db.String(300))
    conge_id      = db.Column(db.Integer, db.ForeignKey("conges.id"))   # demande créée à partir de la période
    cree_par_id   = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    date_creation = db.Column(db.DateTime, default=utcnow)
    salarie = db.relationship("Salarie")
    conge   = db.relationship("Conge")
    __table_args__ = (
        db.Index("idx_previsions_tenant_planning", "tenant_id", "planning_id"),
        db.Index("idx_previsions_salarie", "salarie_id"),
    )

    @property
    def jours(self):
        """Jours calendaires, comme une demande de congé (départ et retour inclus)."""
        return (self.date_fin - self.date_debut).days + 1


class Acompte(db.Model):
    __tablename__ = "acomptes"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id    = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    montant       = db.Column(db.Numeric(15,2), nullable=False)
    date_acompte  = db.Column(db.Date, nullable=False)
    mois          = db.Column(db.Integer, nullable=False)
    annee         = db.Column(db.Integer, nullable=False)
    motif         = db.Column(db.String(200))
    statut        = db.Column(db.String(20), default="EN_ATTENTE")
    date_creation = db.Column(db.DateTime, default=utcnow)
    salarie = db.relationship("Salarie", backref="acomptes")
    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k in ["date_acompte","date_creation"]:
            if d[k]: d[k] = str(d[k])
        if d["montant"]: d["montant"] = float(d["montant"])
        return d


class Journalier(db.Model):
    __tablename__ = "journaliers"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    nom           = db.Column(db.String(100), nullable=False)
    prenom        = db.Column(db.String(100), nullable=False)
    telephone     = db.Column(db.String(20))
    profession    = db.Column(db.String(100))
    taux_horaire  = db.Column(db.Numeric(12,2), nullable=False)
    type_paie     = db.Column(db.String(20), default="JOURNALIER")  # JOURNALIER | MENSUEL
    statut        = db.Column(db.String(20), default="ACTIF")
    mode_paiement = db.Column(db.String(30), default="ESPECES")  # ESPECES | VIREMENT
    date_embauche = db.Column(db.Date)
    date_debut    = db.Column(db.Date)           # début de mission (libre)
    date_fin      = db.Column(db.Date)           # fin de mission (optionnel)
    nationalite   = db.Column(db.String(60))
    sexe          = db.Column(db.String(1))          # M | F (Note 27B de la DSF)
    date_creation = db.Column(db.DateTime, default=utcnow)
    pointages = db.relationship("Pointage", backref="journalier", lazy=True,
                foreign_keys="Pointage.journalier_id")
    @property
    def nom_complet(self): return f"{self.nom} {self.prenom}"
    @property
    def taux_journalier(self):
        """Taux journalier = taux horaire × 8 h (journée standard)."""
        return float(self.taux_horaire or 0) * 8
    @property
    def est_mensuel(self):
        return (self.type_paie or "JOURNALIER") == "MENSUEL"
    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        if d["taux_horaire"]: d["taux_horaire"] = float(d["taux_horaire"])
        if d["date_creation"]: d["date_creation"] = str(d["date_creation"])
        if d.get("date_embauche"): d["date_embauche"] = str(d["date_embauche"])
        d["nom_complet"] = self.nom_complet
        d["taux_journalier"] = self.taux_journalier
        return d


class Pointage(db.Model):
    __tablename__ = "pointages"
    id              = db.Column(db.Integer, primary_key=True)
    tenant_id       = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    date_pointage   = db.Column(db.Date, nullable=False)
    salarie_id      = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=True)
    journalier_id   = db.Column(db.Integer, db.ForeignKey("journaliers.id"), nullable=True)
    present         = db.Column(db.Boolean, default=True)
    heures_normales = db.Column(db.Numeric(5,2), default=8)
    heures_sup      = db.Column(db.Numeric(5,2), default=0)
    heures_sup_10   = db.Column(db.Numeric(5,2), default=0)
    heures_sup_30   = db.Column(db.Numeric(5,2), default=0)
    heures_sup_30b  = db.Column(db.Numeric(5,2), default=0)  # repos/férié jour (Pétrole)
    heures_sup_40   = db.Column(db.Numeric(5,2), default=0)
    heures_sup_70   = db.Column(db.Numeric(5,2), default=0)
    absent          = db.Column(db.Boolean, default=False)
    motif_absence   = db.Column(db.String(100))
    observation     = db.Column(db.String(200))
    # Type de jour : NORMAL | DIMANCHE | FERIE | CHOME_PAYE | CHOME_RECUPERABLE
    type_jour       = db.Column(db.String(20), default="NORMAL")
    # Horaires entrée/sortie (stockés en STRING "HH:MM" pour simplicité)
    entree_matin    = db.Column(db.String(5))   # ex: "08:00"
    sortie_matin    = db.Column(db.String(5))   # ex: "13:00"
    entree_apmidi   = db.Column(db.String(5))   # ex: "14:00"
    sortie_apmidi   = db.Column(db.String(5))   # ex: "17:00"
    entree_sup      = db.Column(db.String(5))   # ex: "17:00"
    sortie_sup      = db.Column(db.String(5))   # ex: "18:00"
    site_id         = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=True)
    # ── Pointage mobile (PWA chantier) ────────────────────────────────────────
    # Les pointages mobiles arrivent en quarantaine (statut SOUMIS) et ne sont
    # JAMAIS comptés en paie/facturation tant que le RH ne les a pas validés.
    uuid_client     = db.Column(db.String(36))   # idempotence sync (généré sur le téléphone)
    source          = db.Column(db.String(10), default="WEB")   # WEB | MOBILE
    statut          = db.Column(db.String(10), default="VALIDE")  # VALIDE | SOUMIS | REJETE
    pointeur_id     = db.Column(db.Integer, db.ForeignKey("pointeurs_chantier.id"), nullable=True)
    latitude        = db.Column(db.Numeric(9, 6))
    longitude       = db.Column(db.Numeric(9, 6))
    geo_hors_zone   = db.Column(db.Boolean, default=False)   # alerte RH, jamais bloquant
    heure_serveur   = db.Column(db.DateTime)   # horodatage serveur de réception (anti-triche)
    signature       = db.Column(db.Text)       # signature tactile du travailleur (data URL PNG), optionnelle
    photo           = db.Column(db.Text)       # photo du travailleur pointé (data URL JPEG compressé), optionnelle
    salarie = db.relationship("Salarie", backref="pointages", foreign_keys=[salarie_id])
    site    = db.relationship("Site", backref="pointages", foreign_keys=[site_id])
    __table_args__ = (
        db.UniqueConstraint("tenant_id","date_pointage","salarie_id"),
        db.UniqueConstraint("tenant_id","date_pointage","journalier_id"),
        db.Index("idx_pointages_tenant_date", "tenant_id", "date_pointage"),
        db.Index("idx_pointages_salarie",     "salarie_id", "date_pointage"),
    )
    @property
    def total_heures(self): return float(self.heures_normales or 0) + float(self.heures_sup or 0)
    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        if d["date_pointage"]: d["date_pointage"] = str(d["date_pointage"])
        for k in ["heures_normales","heures_sup"]:
            if d[k] is not None: d[k] = float(d[k])
        return d


class FeuillePaieJournalier(db.Model):
    __tablename__ = "feuilles_paie_journalier"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    journalier_id = db.Column(db.Integer, db.ForeignKey("journaliers.id"), nullable=False)
    date_debut    = db.Column(db.Date, nullable=False)
    date_fin      = db.Column(db.Date, nullable=False)
    date_paiement = db.Column(db.Date)
    nb_jours      = db.Column(db.Integer, default=0)
    total_heures  = db.Column(db.Numeric(7,2), default=0)
    heures_normales = db.Column(db.Numeric(7,2), default=0)  # détail pour l'impression
    heures_sup      = db.Column(db.Numeric(7,2), default=0)  # détail pour l'impression
    taux_horaire  = db.Column(db.Numeric(12,2), nullable=False)
    montant_brut  = db.Column(db.Numeric(15,2), default=0)
    avance_deduite = db.Column(db.Numeric(15,2), default=0)  # avances déduites (figé au paiement)
    statut        = db.Column(db.String(20), default="EN_ATTENTE")
    mode_paiement = db.Column(db.String(30), default="ESPECES")  # figé au paiement
    observation   = db.Column(db.String(200))
    date_creation = db.Column(db.DateTime, default=utcnow)
    journalier = db.relationship("Journalier", backref="feuilles_paie")

    @property
    def montant_a_payer(self):
        """Montant réellement dû = le brut enregistré, tel quel.

        L'arrondi éventuel (au millier de franc supérieur) est décidé et appliqué
        à la GÉNÉRATION selon le choix de l'utilisateur ; il n'est plus re-forcé
        ici, afin que toute modification manuelle du montant soit respectée.
        """
        return float(self.montant_brut or 0)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k in ["date_debut","date_fin","date_paiement","date_creation"]:
            if d[k]: d[k] = str(d[k])
        for k in ["total_heures","taux_horaire","montant_brut"]:
            if d[k] is not None: d[k] = float(d[k])
        return d


class AvanceJournalier(db.Model):
    """Avance versée à un journalier, déduite ensuite de sa paie de période.

    Montants en XAF. `montant_regularise` est la part déjà déduite des feuilles
    de paie ; `reste_a_regulariser` est ce qu'il reste à récupérer.
    """
    __tablename__ = "avances_journalier"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    journalier_id = db.Column(db.Integer, db.ForeignKey("journaliers.id"), nullable=False)
    site_id       = db.Column(db.Integer, db.ForeignKey("sites.id"))
    montant       = db.Column(db.Numeric(15,2), nullable=False)
    montant_regularise = db.Column(db.Numeric(15,2), default=0)
    statut        = db.Column(db.String(20), default="EN_ATTENTE")  # EN_ATTENTE | VALIDEE
    date_avance   = db.Column(db.Date, nullable=False, default=utcnow)
    mode_paiement = db.Column(db.String(30), default="ESPECES")
    reference     = db.Column(db.String(80))
    motif         = db.Column(db.String(200))
    date_creation = db.Column(db.DateTime, default=utcnow)

    journalier = db.relationship("Journalier", backref="avances")
    site       = db.relationship("Site")

    __table_args__ = (
        db.Index("idx_avances_journalier", "tenant_id", "journalier_id"),
    )

    @property
    def reste_a_regulariser(self):
        return round(float(self.montant or 0) - float(self.montant_regularise or 0), 2)

    @property
    def est_soldee(self):
        return self.reste_a_regulariser <= 0.009

    @property
    def est_validee(self):
        return self.statut == "VALIDEE"

    @property
    def est_modifiable(self):
        """Modifiable/supprimable tant qu'elle n'est ni validée ni déjà déduite."""
        return self.statut == "EN_ATTENTE" and float(self.montant_regularise or 0) == 0

    @property
    def statut_label(self):
        return {"EN_ATTENTE": "En attente", "VALIDEE": "Validée"}.get(self.statut, self.statut)

    def to_dict(self):
        return {"id": self.id, "journalier_id": self.journalier_id,
                "site_id": self.site_id, "montant": float(self.montant or 0),
                "montant_regularise": float(self.montant_regularise or 0),
                "reste_a_regulariser": self.reste_a_regulariser,
                "statut": self.statut, "est_modifiable": self.est_modifiable,
                "date_avance": str(self.date_avance) if self.date_avance else None,
                "mode_paiement": self.mode_paiement, "reference": self.reference,
                "motif": self.motif}


class Paiement(db.Model):
    """
    Historique de tous les paiements d'abonnement.
    Un enregistrement par tentative (succès ou échec).
    """
    __tablename__ = "paiements"

    id               = db.Column(db.Integer, primary_key=True)
    tenant_id        = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)

    # Moyen de paiement
    moyen            = db.Column(db.String(30), nullable=False)
    # AIRTEL_MONEY | MOOV_MONEY | CINETPAY | STRIPE | MANUEL

    # Montant et durée
    montant          = db.Column(db.Numeric(15, 2), nullable=False)
    duree_mois       = db.Column(db.Integer, default=1)
    plan_id          = db.Column(db.Integer, db.ForeignKey("plans.id"), nullable=True)

    # Références
    reference_interne = db.Column(db.String(100), unique=True, nullable=False)
    # Identifiant côté opérateur (transaction_id Airtel, charge_id Stripe…)
    reference_externe = db.Column(db.String(200))

    # Numéro de téléphone utilisé (Mobile Money)
    telephone        = db.Column(db.String(30))

    # Statut du paiement
    statut           = db.Column(db.String(20), default="EN_ATTENTE")
    # EN_ATTENTE | SUCCES | ECHEC | EXPIRE | REMBOURSE

    # Dates
    date_creation    = db.Column(db.DateTime, default=utcnow)
    date_confirmation = db.Column(db.DateTime)

    # Données brutes de la réponse opérateur (JSON)
    reponse_raw      = db.Column(db.Text)

    # Notes admin
    notes            = db.Column(db.Text)

    # Relations
    tenant = db.relationship("Tenant", backref="paiements")
    plan   = db.relationship("Plan")

    __table_args__ = (
        db.Index("idx_paiements_tenant", "tenant_id", "date_creation"),
        db.Index("idx_paiements_statut", "statut"),
    )

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k in ["date_creation", "date_confirmation"]:
            if d[k]:
                d[k] = str(d[k])
        if d["montant"] is not None:
            d["montant"] = float(d["montant"])
        return d


class OAuthClient(db.Model):
    """
    Client OAuth2 pour les grandes entreprises.
    Permet une authentification plus sécurisée que le token API fixe.
    Un tenant peut avoir plusieurs clients OAuth (ex: un par application tierce).
    """
    __tablename__ = "oauth_clients"

    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    nom           = db.Column(db.String(100), nullable=False)
    client_id     = db.Column(db.String(64), unique=True, nullable=False)
    # Hash SHA-256 du client_secret (jamais stocké en clair). Voir hash_secret().
    client_secret = db.Column(db.String(128), nullable=False)
    description   = db.Column(db.String(300))
    actif         = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=utcnow)
    derniere_utilisation = db.Column(db.DateTime)

    tenant = db.relationship("Tenant", backref="oauth_clients")

    __table_args__ = (
        db.Index("idx_oauth_client_id", "client_id"),
        db.Index("idx_oauth_tenant", "tenant_id"),
    )


class AuditLog(db.Model):
    """
    Journal d'audit — enregistre toutes les actions importantes.
    Qui a fait quoi, quand, sur quel objet, depuis quelle IP.
    """
    __tablename__ = "audit_logs"

    id           = db.Column(db.Integer, primary_key=True)
    tenant_id    = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"), nullable=True)

    # Action effectuée
    action       = db.Column(db.String(50),  nullable=False)
    # CREATE | UPDATE | DELETE | VALIDATE | CANCEL | LOGIN | LOGOUT | EXPORT | IMPORT

    # Type d'objet concerné
    entite       = db.Column(db.String(50))
    # salarie | bulletin | conge | acompte | periode | utilisateur | parametres | paiement

    # ID de l'objet concerné (null si action globale)
    entite_id    = db.Column(db.Integer)

    # Description lisible
    description  = db.Column(db.String(500))

    # Données avant/après modification (JSON)
    avant        = db.Column(db.Text)  # état avant (pour UPDATE/DELETE)
    apres        = db.Column(db.Text)  # état après (pour CREATE/UPDATE)

    # Contexte technique
    ip_address   = db.Column(db.String(45))
    user_agent   = db.Column(db.String(300))
    date_action  = db.Column(db.DateTime, default=utcnow, nullable=False)

    # Relations
    tenant = db.relationship("Tenant",      foreign_keys=[tenant_id])
    user   = db.relationship("Utilisateur", foreign_keys=[user_id])

    __table_args__ = (
        db.Index("idx_audit_tenant_date",  "tenant_id", "date_action"),
        db.Index("idx_audit_user",         "user_id",   "date_action"),
        db.Index("idx_audit_entite",       "entite",    "entite_id"),
        db.Index("idx_audit_action",       "action"),
    )

    def to_dict(self):
        return {
            "id":          self.id,
            "action":      self.action,
            "entite":      self.entite,
            "entite_id":   self.entite_id,
            "description": self.description,
            "user":        self.user.nom_complet if self.user else "Système",
            "user_role":   self.user.role_label  if self.user else "—",
            "ip_address":  self.ip_address,
            "date_action": self.date_action.strftime("%d/%m/%Y %H:%M:%S") if self.date_action else "—",
        }


# ══════════════════════════════════════════════════════════════════════════════
# MODULE PRESTATAIRES — gestion des prestataires, contrats, factures, paiements
# ══════════════════════════════════════════════════════════════════════════════
class Prestataire(db.Model):
    """
    Prestataire : freelance, entreprise sous-traitante ou fournisseur.
    Polyvalent — gère personnes physiques et morales.
    """
    __tablename__ = "prestataires"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    code          = db.Column(db.String(50), nullable=False)   # référence interne

    # Type : PHYSIQUE (freelance) ou MORALE (entreprise)
    type_personne = db.Column(db.String(10), default="MORALE")  # PHYSIQUE | MORALE
    # Catégorie : FREELANCE | SOUS_TRAITANT | FOURNISSEUR
    categorie     = db.Column(db.String(20), default="FREELANCE")

    # Identité
    raison_sociale = db.Column(db.String(200), nullable=False)  # nom ou raison sociale
    sigle          = db.Column(db.String(50))
    nom_contact    = db.Column(db.String(200))   # personne à contacter (si morale)

    # Coordonnées
    telephone     = db.Column(db.String(30))
    email         = db.Column(db.String(200))
    adresse       = db.Column(db.String(300))
    ville         = db.Column(db.String(100), default="Libreville")
    pays          = db.Column(db.String(100), default="Gabon")

    # Identifiants légaux
    nif           = db.Column(db.String(50))     # Numéro d'Identification Fiscale
    rccm          = db.Column(db.String(50))     # Registre de Commerce
    cnss_employeur = db.Column(db.String(30))    # si applicable

    # Domaine d'activité
    activite      = db.Column(db.String(200))

    # Coordonnées de paiement
    mode_paiement = db.Column(db.String(30), default="VIREMENT")  # VIREMENT | MOBILE_MONEY | CHEQUE | ESPECES
    rib           = db.Column(db.String(50))
    banque        = db.Column(db.String(100))
    numero_mobile_money = db.Column(db.String(30))

    # Fiscalité
    resident      = db.Column(db.Boolean, default=True)   # résident fiscal Gabon ?
    assujetti_tva = db.Column(db.Boolean, default=True)
    taux_retenue_source = db.Column(db.Numeric(5,2), default=0)  # % retenue à la source
    # Régime fiscal (pilote les taxes par défaut des factures) :
    #   TVA_CSS   → TVA 18 % + CSS 1 %
    #   CSS       → CSS 1 % seule (pas de TVA)
    #   PRECOMPTE → précompte 9,5 % (retenue), pas de TVA, CSS optionnelle
    regime_fiscal = db.Column(db.String(12), default="TVA_CSS")
    assujetti_css = db.Column(db.Boolean, default=True)  # soumis à la CSS (1 %)

    statut        = db.Column(db.String(20), default="ACTIF")  # ACTIF | INACTIF
    notes         = db.Column(db.Text)
    date_creation     = db.Column(db.DateTime, default=utcnow)
    date_modification = db.Column(db.DateTime, onupdate=utcnow)

    contrats = db.relationship("ContratPrestation", backref="prestataire", lazy=True)
    factures = db.relationship("FacturePrestataire", backref="prestataire", lazy=True)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "code"),
        db.Index("idx_prestataires_tenant_statut", "tenant_id", "statut"),
        db.Index("idx_prestataires_tenant_cat", "tenant_id", "categorie"),
    )

    REGIMES_FISCAUX = {
        "TVA_CSS":   "TVA 18 % + CSS 1 %",
        "CSS":       "CSS 1 % seule",
        "PRECOMPTE": "Précompte 9,5 % (+ CSS optionnelle)",
    }

    @property
    def regime_libelle(self):
        return self.REGIMES_FISCAUX.get(self.regime_fiscal or "TVA_CSS", self.regime_fiscal)

    @property
    def nom_affiche(self):
        return self.raison_sociale

    @property
    def categorie_label(self):
        return {"FREELANCE": "Freelance / Indépendant",
                "SOUS_TRAITANT": "Sous-traitant",
                "FOURNISSEUR": "Fournisseur"}.get(self.categorie, self.categorie)

    def to_dict(self):
        d = {}
        for c in self.__table__.columns:
            val = getattr(self, c.name)
            d[c.name] = (str(val) if isinstance(val, (date, datetime))
                         else (float(val) if hasattr(val, "__float__") and val is not None else val))
        d["nom_affiche"] = self.nom_affiche
        d["categorie_label"] = self.categorie_label
        return d


class ContratPrestation(db.Model):
    """Contrat de prestation entre le tenant et un prestataire."""
    __tablename__ = "contrats_prestation"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    prestataire_id = db.Column(db.Integer, db.ForeignKey("prestataires.id"), nullable=False)

    reference      = db.Column(db.String(50))    # numéro de contrat
    objet          = db.Column(db.String(300), nullable=False)  # objet de la mission
    type_remuneration = db.Column(db.String(20), default="FORFAIT")  # FORFAIT | JOURNALIER | HORAIRE | MENSUEL

    montant        = db.Column(db.Numeric(15,2), nullable=False)  # montant ou taux
    devise         = db.Column(db.String(5), default="XAF")

    date_debut     = db.Column(db.Date, nullable=False)
    date_fin       = db.Column(db.Date)
    site_id        = db.Column(db.Integer, db.ForeignKey("sites.id"))

    statut         = db.Column(db.String(20), default="EN_COURS")  # EN_COURS | TERMINE | SUSPENDU | ANNULE
    conditions     = db.Column(db.Text)
    date_creation  = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.Index("idx_contrats_prest_tenant", "tenant_id", "prestataire_id"),
    )

    @property
    def type_remuneration_label(self):
        return {"FORFAIT": "Forfait", "JOURNALIER": "Taux journalier",
                "HORAIRE": "Taux horaire", "MENSUEL": "Mensuel"}.get(
                    self.type_remuneration, self.type_remuneration)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k in ("date_debut", "date_fin", "date_creation"):
            if d.get(k): d[k] = str(d[k])
        if d.get("montant"): d["montant"] = float(d["montant"])
        return d


class FacturePrestataire(db.Model):
    """Facture reçue d'un prestataire, avec calcul TVA et retenue à la source."""
    __tablename__ = "factures_prestataire"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    prestataire_id = db.Column(db.Integer, db.ForeignKey("prestataires.id"), nullable=False)
    contrat_id     = db.Column(db.Integer, db.ForeignKey("contrats_prestation.id"))
    site_id        = db.Column(db.Integer, db.ForeignKey("sites.id"))   # chantier

    numero         = db.Column(db.String(50), nullable=False)  # n° de facture
    date_facture   = db.Column(db.Date, nullable=False)
    date_echeance  = db.Column(db.Date)
    description    = db.Column(db.String(300))

    # BTP : facturation au mètre carré (montant_ht = surface × prix unitaire)
    surface_m2          = db.Column(db.Numeric(12, 2))
    prix_unitaire_m2    = db.Column(db.Numeric(15, 2))
    pourcentage_realisation = db.Column(db.Numeric(5, 2), default=0)  # % d'avancement

    # Montants
    montant_ht     = db.Column(db.Numeric(15,2), nullable=False)
    taux_tva       = db.Column(db.Numeric(5,2), default=18)     # TVA Gabon = 18%
    montant_tva    = db.Column(db.Numeric(15,2), default=0)
    taux_css       = db.Column(db.Numeric(5,2), default=1)      # CSS Gabon = 1%
    montant_css    = db.Column(db.Numeric(15,2), default=0)
    taux_retenue   = db.Column(db.Numeric(5,2), default=0)      # retenue à la source / précompte
    montant_retenue = db.Column(db.Numeric(15,2), default=0)
    montant_ttc    = db.Column(db.Numeric(15,2), default=0)     # HT + TVA + CSS
    montant_net_a_payer = db.Column(db.Numeric(15,2), default=0)  # TTC - retenue

    # Multi-devises
    devise         = db.Column(db.String(5), default="XAF")
    taux_change    = db.Column(db.Numeric(14, 6), default=1)    # XAF pour 1 unité de devise
    montant_xaf    = db.Column(db.Numeric(15, 2), default=0)    # net à payer converti en XAF

    statut         = db.Column(db.String(20), default="BROUILLON")  # BROUILLON | VALIDEE | PARTIELLE | PAYEE | ANNULEE
    montant_paye   = db.Column(db.Numeric(15,2), default=0)
    # Workflow de validation (modifiable tant que BROUILLON ; payable une fois VALIDEE)
    valide_par_nom     = db.Column(db.String(150))
    valide_par_user_id = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    date_validation    = db.Column(db.DateTime)
    date_creation  = db.Column(db.DateTime, default=utcnow)

    paiements = db.relationship("PaiementPrestataire", backref="facture", lazy=True)
    lignes    = db.relationship("LigneFacturePrestataire", backref="facture",
                                lazy=True, cascade="all, delete-orphan",
                                order_by="LigneFacturePrestataire.ordre")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "prestataire_id", "numero"),
        db.Index("idx_factures_tenant_statut", "tenant_id", "statut"),
        db.Index("idx_factures_tenant_date", "tenant_id", "date_facture"),
    )

    def calculer(self):
        """Recalcule HT (lignes ou m²), TVA, retenue, TTC, net à payer et équivalent XAF."""
        # Priorité aux lignes de détail si présentes ; sinon m² ; sinon HT direct.
        if self.lignes:
            self.montant_ht = round(sum(float(l.montant or 0) for l in self.lignes), 2)
        elif self.surface_m2 and self.prix_unitaire_m2:
            self.montant_ht = round(float(self.surface_m2) * float(self.prix_unitaire_m2), 2)
        ht = float(self.montant_ht or 0)
        self.montant_tva = round(ht * float(self.taux_tva or 0) / 100, 2)
        self.montant_css = round(ht * float(self.taux_css or 0) / 100, 2)
        # TTC = HT + TVA + CSS (la CSS est collectée sur la facture, comme la TVA)
        self.montant_ttc = round(ht + float(self.montant_tva) + float(self.montant_css), 2)
        # Précompte / retenue à la source : calculé sur le HT, déduit du net à payer
        self.montant_retenue = round(ht * float(self.taux_retenue or 0) / 100, 2)
        self.montant_net_a_payer = round(float(self.montant_ttc) - float(self.montant_retenue), 2)
        # Équivalent XAF (devise étrangère → FCFA au taux du jour)
        self.montant_xaf = round(float(self.montant_net_a_payer) * float(self.taux_change or 1), 2)
        return self

    @property
    def montant_net_en_xaf(self):
        if self.montant_xaf and float(self.montant_xaf) > 0:
            return round(float(self.montant_xaf), 2)
        return round(float(self.montant_net_a_payer or 0) * float(self.taux_change or 1), 2)

    @property
    def reste_a_payer(self):
        return round(float(self.montant_net_a_payer or 0) - float(self.montant_paye or 0), 2)

    @property
    def est_modifiable(self):
        """Modifiable/supprimable uniquement à l'état brouillon."""
        return self.statut == "BROUILLON"

    @property
    def est_payable(self):
        """Payable seulement après validation (et tant que non soldée/annulée)."""
        return self.statut in ("VALIDEE", "PARTIELLE")

    @property
    def devise_symbole(self):
        return {"XAF": "FCFA", "EUR": "€", "USD": "$", "MAD": "DH"}.get(self.devise or "XAF", self.devise)

    @property
    def statut_label(self):
        return {"BROUILLON": "Brouillon", "VALIDEE": "Validée", "PAYEE": "Payée",
                "PARTIELLE": "Partielle", "ANNULEE": "Annulée",
                "EN_ATTENTE": "Validée"}.get(self.statut, self.statut)

    def to_dict(self):
        d = {}
        for c in self.__table__.columns:
            val = getattr(self, c.name)
            d[c.name] = (str(val) if isinstance(val, (date, datetime))
                         else (float(val) if hasattr(val, "__float__") and val is not None else val))
        d["reste_a_payer"] = self.reste_a_payer
        d["statut_label"] = self.statut_label
        return d


class LigneFacturePrestataire(db.Model):
    """Ligne de détail d'une facture prestataire (désignation, quantité, prix)."""
    __tablename__ = "lignes_facture_prestataire"
    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    facture_id  = db.Column(db.Integer, db.ForeignKey("factures_prestataire.id"), nullable=False)

    designation    = db.Column(db.String(300), nullable=False)
    quantite_totale = db.Column(db.Numeric(12, 2))            # quantité prévue (au marché)
    quantite       = db.Column(db.Numeric(12, 2), default=1)  # quantité réalisée (facturée)
    unite          = db.Column(db.String(20), default="u")   # u | m² | ml | jour | forfait…
    prix_unitaire  = db.Column(db.Numeric(15, 2), default=0)
    montant        = db.Column(db.Numeric(15, 2), default=0)  # = quantite × prix_unitaire
    ordre          = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.Index("idx_lignes_facture", "tenant_id", "facture_id"),
    )

    @property
    def pourcentage_realisation(self):
        """% d'avancement de la ligne = quantité réalisée / quantité prévue.
        Si aucune quantité prévue n'est renseignée, la ligne est considérée
        réalisée à 100 % (quantité facturée = quantité réalisée)."""
        prevue = float(self.quantite_totale or 0)
        realisee = float(self.quantite or 0)
        if prevue <= 0:
            return 100.0 if realisee > 0 else 0.0
        return round(realisee / prevue * 100, 2)

    def calculer(self):
        self.montant = round(float(self.quantite or 0) * float(self.prix_unitaire or 0), 2)
        return self

    def to_dict(self):
        return {"id": self.id, "designation": self.designation,
                "quantite_totale": float(self.quantite_totale) if self.quantite_totale is not None else None,
                "quantite": float(self.quantite or 0), "unite": self.unite,
                "prix_unitaire": float(self.prix_unitaire or 0),
                "montant": float(self.montant or 0), "ordre": self.ordre,
                "pourcentage_realisation": self.pourcentage_realisation}


class PaiementPrestataire(db.Model):
    """Paiement effectué sur une facture de prestataire."""
    __tablename__ = "paiements_prestataire"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    facture_id     = db.Column(db.Integer, db.ForeignKey("factures_prestataire.id"), nullable=False)

    montant        = db.Column(db.Numeric(15,2), nullable=False)
    date_paiement  = db.Column(db.Date, nullable=False)
    mode_paiement  = db.Column(db.String(30), default="VIREMENT")
    pourcentage_realisation = db.Column(db.Numeric(5,2), default=0)  # % d'avancement payé (BTP)
    reference      = db.Column(db.String(100))   # n° de transaction / chèque
    notes          = db.Column(db.String(300))
    date_creation  = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.Index("idx_paiements_prest_tenant", "tenant_id", "facture_id"),
    )

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k in ("date_paiement", "date_creation"):
            if d.get(k): d[k] = str(d[k])
        if d.get("montant"): d["montant"] = float(d["montant"])
        return d


class TauxDevise(db.Model):
    """Cache quotidien des taux de change (XAF pour 1 unité de devise).

    Évite d'appeler l'API de change à chaque saisie : un taux par devise et par
    jour est mémorisé. L'EUR est figé à la parité fixe FCFA BEAC (655,957).
    """
    __tablename__ = "taux_devises"
    id        = db.Column(db.Integer, primary_key=True)
    date_taux = db.Column(db.Date, nullable=False)
    devise    = db.Column(db.String(5), nullable=False)
    taux_xaf  = db.Column(db.Numeric(14, 6), nullable=False)
    source    = db.Column(db.String(30), default="API")   # API | PEG | FALLBACK | MANUEL
    date_creation = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("date_taux", "devise"),
    )


class AvancePrestataire(db.Model):
    """Avance versée à un prestataire / sous-traitant, hors facture.

    Somme remise au prestataire AVANT ou indépendamment d'une facture
    (fréquent avec les sous-traitants). Elle pourra ensuite être régularisée
    (déduite) au moment de la facturation via `montant_regularise`.

    Cycle de vie : EN_ATTENTE (demandée, modifiable/supprimable) → VALIDEE par le
    chef de chantier (figée : ni modifiable ni supprimable) ; ANNULEE possible.
    """
    __tablename__ = "avances_prestataire"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    prestataire_id = db.Column(db.Integer, db.ForeignKey("prestataires.id"), nullable=False)
    contrat_id     = db.Column(db.Integer, db.ForeignKey("contrats_prestation.id"))
    site_id        = db.Column(db.Integer, db.ForeignKey("sites.id"))   # chantier

    montant            = db.Column(db.Numeric(15, 2), nullable=False)
    montant_regularise = db.Column(db.Numeric(15, 2), default=0)
    date_avance        = db.Column(db.Date, nullable=False)
    mode_paiement      = db.Column(db.String(30), default="VIREMENT")
    reference          = db.Column(db.String(100))   # n° transaction / chèque
    motif              = db.Column(db.String(300))

    # Multi-devises : montant saisi dans `devise`, converti en XAF au taux du jour.
    devise         = db.Column(db.String(5), default="XAF")
    taux_change    = db.Column(db.Numeric(14, 6), default=1)   # XAF pour 1 unité de devise
    montant_xaf    = db.Column(db.Numeric(15, 2), default=0)   # = montant × taux_change

    # Workflow de validation (chef de chantier)
    statut             = db.Column(db.String(20), default="EN_ATTENTE")  # EN_ATTENTE | VALIDEE | ANNULEE
    valide_par_nom     = db.Column(db.String(150))   # nom du chef de chantier
    valide_par_user_id = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    date_validation    = db.Column(db.DateTime)

    date_creation      = db.Column(db.DateTime, default=utcnow)

    prestataire = db.relationship("Prestataire", backref="avances")
    site        = db.relationship("Site")

    __table_args__ = (
        db.Index("idx_avances_prest_tenant", "tenant_id", "prestataire_id"),
        db.Index("idx_avances_prest_statut", "tenant_id", "statut"),
    )

    @property
    def reste_a_regulariser(self):
        return round(float(self.montant or 0) - float(self.montant_regularise or 0), 2)

    @property
    def est_validee(self):
        return self.statut == "VALIDEE"

    @property
    def est_modifiable(self):
        """Modifiable/supprimable tant qu'elle n'est ni validée ni annulée."""
        return self.statut == "EN_ATTENTE"

    @property
    def statut_label(self):
        return {"EN_ATTENTE": "En attente", "VALIDEE": "Validée",
                "REGULARISEE": "Régularisée", "ANNULEE": "Annulée",
                "EN_COURS": "En attente"}.get(self.statut, self.statut)

    @property
    def montant_en_xaf(self):
        """Équivalent XAF (recalculé si non stocké)."""
        if self.montant_xaf and float(self.montant_xaf) > 0:
            return round(float(self.montant_xaf), 2)
        return round(float(self.montant or 0) * float(self.taux_change or 1), 2)

    def to_dict(self):
        d = {}
        for c in self.__table__.columns:
            val = getattr(self, c.name)
            d[c.name] = (str(val) if isinstance(val, (date, datetime))
                         else (float(val) if hasattr(val, "__float__") and val is not None else val))
        d["reste_a_regulariser"] = self.reste_a_regulariser
        d["statut_label"] = self.statut_label
        d["montant_en_xaf"] = self.montant_en_xaf
        return d


class MessageSupport(db.Model):
    """Messagerie superadmin ↔ tenant (support intégré).
    Une conversation = un tenant. Chaque message a un expéditeur
    ('SUPERADMIN' ou 'TENANT'). Le champ 'lu' indique si le destinataire
    a lu le message : un message envoyé par TENANT est non lu tant que le
    superadmin ne l'a pas ouvert, et inversement."""
    __tablename__ = "messages_support"

    id           = db.Column(db.Integer, primary_key=True)
    tenant_id    = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    expediteur   = db.Column(db.String(20), nullable=False)   # 'SUPERADMIN' | 'TENANT'
    user_id      = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"), nullable=True)
    corps        = db.Column(db.Text, nullable=False)
    lu           = db.Column(db.Boolean, default=False, nullable=False)
    date_creation = db.Column(db.DateTime, default=utcnow)

    tenant = db.relationship("Tenant", backref=db.backref("messages_support", lazy=True))
    auteur = db.relationship("Utilisateur", foreign_keys=[user_id])


# ── Avis / retours d'expérience utilisateurs ──────────────────────────────────
class Avis(db.Model):
    """Retour d'expérience laissé via la page publique /avis (connecté ou non)."""
    __tablename__ = "avis"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=True)
    entreprise    = db.Column(db.String(200))
    nom           = db.Column(db.String(120))
    fonction      = db.Column(db.String(120))
    outil_avant   = db.Column(db.String(200))   # ce qu'ils utilisaient avant
    gain          = db.Column(db.Text)          # ce que PaieGabon leur a apporté
    frustration   = db.Column(db.Text)          # ce qui manque / frustre
    nps           = db.Column(db.Integer)        # note de recommandation 0..10
    consentement  = db.Column(db.Boolean, default=False)  # autorise citation publique
    mis_en_avant  = db.Column(db.Boolean, default=False)  # affiché sur la page d'accueil
    commentaire   = db.Column(db.Text)
    date_creation = db.Column(db.DateTime, default=utcnow)
    tenant = db.relationship("Tenant", backref=db.backref("avis", lazy=True))


# ── Documents du dossier salarié (contrat signé, CNI, diplômes, etc.) ──────────
class DocumentSalarie(db.Model):
    """Pièce jointe au dossier d'un salarié. Le fichier est stocké en base
    (data URI base64), comme le logo — plafonné à 4 Mo par fichier."""
    __tablename__ = "documents_salarie"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id    = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    type_document = db.Column(db.String(60))    # Contrat, CNI, Diplôme, Certificat médical, Autre
    nom_fichier   = db.Column(db.String(255))
    mime          = db.Column(db.String(100))
    taille        = db.Column(db.Integer)       # octets
    contenu       = db.Column(db.Text)          # data URI base64
    date_creation = db.Column(db.DateTime, default=utcnow)
    salarie = db.relationship("Salarie", backref=db.backref("documents", lazy=True))


# ── Configuration des rubriques fixes « souples » (panier, transport, etc.) ────
class ConfigRubrique(db.Model):
    """Paramétrage par tenant des rubriques fixes configurables du bulletin
    (prime de panier, transport net, représentation, salisure). Défaut : hors
    brut et non soumises, ce qui reproduit le comportement historique."""
    __tablename__ = "config_rubriques"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    cle           = db.Column(db.String(30), nullable=False)   # panier|transport|representation|salisure
    entre_dans_brut = db.Column(db.Boolean, default=False)
    position      = db.Column(db.String(4), default="BAS")     # HAUT | BAS
    soumis_cnss   = db.Column(db.Boolean, default=False)
    soumis_cnamgs = db.Column(db.Boolean, default=False)
    soumis_irpp   = db.Column(db.Boolean, default=False)
    __table_args__ = (db.UniqueConstraint("tenant_id", "cle", name="uq_config_rubrique_tenant_cle"),)


class ModeleContrat(db.Model):
    """Modèle de contrat créé par le tenant : texte libre avec des balises {{champ}}
    remplies automatiquement à la génération. Le contenu juridique relève du tenant."""
    __tablename__ = "modeles_contrat"
    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    nom         = db.Column(db.String(150), nullable=False)   # ex. "CDI cadre", "Contrat de chantier"
    type_contrat = db.Column(db.String(30), default="CDI")    # CDI | CDD | CHANTIER | JOURNALIER | AUTRE
    contenu     = db.Column(db.Text, default="")              # texte avec balises {{...}}
    actif       = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=utcnow)
    tenant = db.relationship("Tenant", backref="modeles_contrat")

class CollaborateurEntreprise(db.Model):
    """Assigne une entreprise gérée à un collaborateur du cabinet.
    Un collaborateur ne voit/gère que les entreprises qui lui sont assignées ;
    l'administrateur du cabinet voit toutes les entreprises."""
    __tablename__ = "collaborateur_entreprise"
    id             = db.Column(db.Integer, primary_key=True)
    utilisateur_id = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"), nullable=False)
    entreprise_id  = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    date_creation  = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (db.UniqueConstraint("utilisateur_id", "entreprise_id",
                                          name="uq_collab_entreprise"),)


class TarifDAS(db.Model):
    """Tarification de l'édition de la DAS, modifiable par le super-admin
    (ligne unique). Remplace les constantes codées en dur."""
    __tablename__ = "tarif_das"
    id              = db.Column(db.Integer, primary_key=True)
    prix_entreprise = db.Column(db.Integer, default=200000)   # FCFA
    prix_cabinet    = db.Column(db.Integer, default=100000)   # FCFA par société
    earlybird_taux  = db.Column(db.Integer, default=20)       # % de réduction
    earlybird_mois  = db.Column(db.Integer, default=3)        # avant le JJ/MM de l'année N+1
    earlybird_jour  = db.Column(db.Integer, default=31)
    date_modification = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    @classmethod
    def get(cls):
        """Renvoie la config (crée la ligne par défaut au besoin)."""
        row = cls.query.first()
        if row is None:
            row = cls()
            db.session.add(row); db.session.commit()
        return row


class StatLanding(db.Model):
    """Statistiques agrégées de la page de présentation publique.
    Aucune donnée personnelle : seulement des compteurs par jour et par source
    (pas d'IP, pas de cookie, pas de user-agent). Hébergé chez nous."""
    __tablename__ = "stat_landing"
    id        = db.Column(db.Integer, primary_key=True)
    jour      = db.Column(db.Date, nullable=False)
    source    = db.Column(db.String(60), default="direct")   # direct | google | facebook | <domaine>
    vues      = db.Column(db.Integer, default=0)             # visites de la landing
    clics_cta = db.Column(db.Integer, default=0)             # clics « Essai gratuit »
    __table_args__ = (db.UniqueConstraint("jour", "source", name="uq_stat_landing_jour_source"),)


class Sanction(db.Model):
    """Registre disciplinaire : sanctions prononcées à l'encontre d'un salarié
    (avertissement, blâme, mise à pied…), consultable sur sa fiche."""
    __tablename__ = "sanctions"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id    = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    type          = db.Column(db.String(30), nullable=False)   # AVERTISSEMENT | BLAME | MISE_A_PIED | MUTATION | AUTRE
    date_sanction = db.Column(db.Date, nullable=False)
    motif         = db.Column(db.String(200))                  # motif court
    description   = db.Column(db.Text)                         # exposé des faits
    duree_jours   = db.Column(db.Integer)                      # pour mise à pied
    cree_par      = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    cree_par_nom  = db.Column(db.String(150))
    date_creation = db.Column(db.DateTime, default=utcnow)
    salarie = db.relationship("Salarie", backref="sanctions")

    LIBELLES = {
        "AVERTISSEMENT": "Avertissement", "BLAME": "Blâme",
        "MISE_A_PIED": "Mise à pied", "MUTATION": "Mutation disciplinaire",
        "AUTRE": "Autre sanction",
    }
    @property
    def type_libelle(self):
        return self.LIBELLES.get(self.type, self.type)


class HistoriqueSalarie(db.Model):
    """Historique des modifications de champs sensibles d'un salarié
    (date d'embauche, fonction) — consultable par le tenant."""
    __tablename__ = "historique_salarie"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id     = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    champ          = db.Column(db.String(30), nullable=False)   # "date_embauche" | "emploi"
    ancienne_valeur = db.Column(db.String(255))
    nouvelle_valeur = db.Column(db.String(255))
    modifie_par    = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    modifie_par_nom = db.Column(db.String(150))                 # nom figé (au cas où l'utilisateur est supprimé)
    date_modification = db.Column(db.DateTime, default=utcnow)
    salarie = db.relationship("Salarie", backref="historique_modifs")

    LIBELLES = {"date_embauche": "Date d'embauche", "emploi": "Fonction"}
    @property
    def champ_libelle(self):
        return self.LIBELLES.get(self.champ, self.champ)


class EditionDAS(db.Model):
    """Suivi du déblocage payant de la Déclaration Annuelle des Salaires par société
    et par année. La préparation/aperçu est gratuite ; l'édition finale est débloquée
    après paiement."""
    __tablename__ = "edition_das"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    annee          = db.Column(db.Integer, nullable=False)          # exercice concerné
    statut         = db.Column(db.String(12), default="PREPARE")    # PREPARE | PAYEE
    montant        = db.Column(db.Numeric(12, 2))                   # montant réglé
    tarif_type     = db.Column(db.String(12))                       # ENTREPRISE | CABINET
    paiement_id    = db.Column(db.Integer, db.ForeignKey("paiements.id"))
    date_paiement  = db.Column(db.DateTime)
    debloque_par   = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))  # si déblocage manuel
    date_creation  = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (db.UniqueConstraint("tenant_id", "annee", name="uq_edition_das_tenant_annee"),)

    @property
    def est_payee(self):
        return self.statut == "PAYEE"


# ═══════════════════════════════════════════════════════════════════════════
# MISE À DISPOSITION DE PERSONNEL
# ───────────────────────────────────────────────────────────────────────────
# Le tenant est l'entreprise PRESTATAIRE : elle emploie, paie et déclare ses
# salariés (rien ne change à la paie). Elle les met à disposition d'ENTREPRISES
# UTILISATRICES (clients) et leur refacture la prestation.
# ═══════════════════════════════════════════════════════════════════════════

class ClientUtilisateur(db.Model):
    """Entreprise utilisatrice : le client chez qui des salariés du tenant sont
    mis à disposition. Ce n'est PAS un tenant — juste un client refacturé."""
    __tablename__ = "clients_utilisateurs"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    nom           = db.Column(db.String(200), nullable=False)   # raison sociale
    nif           = db.Column(db.String(30))                    # NIF de l'utilisatrice
    rccm          = db.Column(db.String(50))
    contact_nom   = db.Column(db.String(150))
    telephone     = db.Column(db.String(30))
    email         = db.Column(db.String(200))
    adresse       = db.Column(db.String(300))
    secteur       = db.Column(db.String(120))                   # BTP, pétrole, etc.
    note          = db.Column(db.Text)
    actif         = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=utcnow)
    tenant = db.relationship("Tenant", backref="clients_utilisateurs")


class AffectationMAD(db.Model):
    """Mise à disposition d'un salarié chez une entreprise utilisatrice, sur une
    période donnée, avec son mode de facturation."""
    __tablename__ = "affectations_mad"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    client_id     = db.Column(db.Integer, db.ForeignKey("clients_utilisateurs.id"), nullable=False)
    salarie_id    = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    poste         = db.Column(db.String(200))                   # poste occupé chez le client
    date_debut    = db.Column(db.Date, nullable=False)
    date_fin      = db.Column(db.Date)                          # NULL = en cours
    mode_facturation = db.Column(db.String(20), default="TAUX_JOUR")
    # FORFAIT_MENSUEL | TAUX_JOUR | TAUX_HEURE | COEFFICIENT
    valeur        = db.Column(db.Numeric(14, 4), default=0)     # montant forfait / taux / coefficient
    actif         = db.Column(db.Boolean, default=True)
    note          = db.Column(db.String(300))
    date_creation = db.Column(db.DateTime, default=utcnow)

    client  = db.relationship("ClientUtilisateur", backref="affectations")
    salarie = db.relationship("Salarie", backref="affectations_mad")

    MODES = {
        "FORFAIT_MENSUEL": "Forfait mensuel",
        "TAUX_JOUR":       "Taux journalier",
        "TAUX_HEURE":      "Taux horaire",
        "COEFFICIENT":     "Coefficient sur coût employeur",
    }
    UNITES = {
        "FORFAIT_MENSUEL": "mois", "TAUX_JOUR": "jour",
        "TAUX_HEURE": "heure", "COEFFICIENT": "×",
    }

    @property
    def mode_libelle(self):
        return self.MODES.get(self.mode_facturation, self.mode_facturation)

    @property
    def unite(self):
        return self.UNITES.get(self.mode_facturation, "")

    def est_active_sur(self, annee, mois):
        """L'affectation couvre-t-elle (au moins en partie) le mois donné ?"""
        from datetime import date as _date
        debut_mois = _date(annee, mois, 1)
        fin_mois = _date(annee + (mois // 12), (mois % 12) + 1, 1)  # 1er du mois suivant
        if self.date_debut and self.date_debut >= fin_mois:
            return False
        if self.date_fin and self.date_fin < debut_mois:
            return False
        return True

    def montant_facture(self, quantite, cout_employeur=0):
        """Montant facturé au client pour ce mois, selon le mode."""
        v = float(self.valeur or 0)
        q = float(quantite or 0)
        if self.mode_facturation == "FORFAIT_MENSUEL":
            return v
        if self.mode_facturation == "COEFFICIENT":
            return float(cout_employeur or 0) * v
        # TAUX_JOUR / TAUX_HEURE
        return v * q


class MiseEnRelation(db.Model):
    """Demande de mise en relation entre deux tenants, initiée par identifiant (NIF).
    Confidentialité : l'existence d'un tenant cible n'est jamais révélée au demandeur
    tant que la cible n'a pas accepté."""
    __tablename__ = "mises_en_relation"
    id                 = db.Column(db.Integer, primary_key=True)
    tenant_demandeur_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    tenant_cible_id    = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=True)
    nif_recherche      = db.Column(db.String(50), nullable=False)   # NIF saisi par le demandeur
    statut             = db.Column(db.String(12), default="EN_ATTENTE")  # EN_ATTENTE | ACCEPTEE | REFUSEE
    message            = db.Column(db.Text)                          # mot du demandeur
    client_cree_id     = db.Column(db.Integer, db.ForeignKey("clients_utilisateurs.id"))  # client créé à l'acceptation
    date_creation      = db.Column(db.DateTime, default=utcnow)
    date_reponse       = db.Column(db.DateTime)

    demandeur = db.relationship("Tenant", foreign_keys=[tenant_demandeur_id])
    cible     = db.relationship("Tenant", foreign_keys=[tenant_cible_id])

    STATUTS = {"EN_ATTENTE": "En attente", "ACCEPTEE": "Acceptée", "REFUSEE": "Refusée"}

    @property
    def statut_libelle(self):
        return self.STATUTS.get(self.statut, self.statut)


class PretPersonnel(db.Model):
    """Prêt au personnel (sans intérêt) : une avance remboursable étalée sur
    plusieurs mois. Les remboursements sont saisis manuellement (ou appliqués
    depuis le bulletin), et le capital restant dû est suivi jusqu'au solde."""
    __tablename__ = "prets_personnel"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id     = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    montant_total  = db.Column(db.Numeric(15, 2), nullable=False)   # capital prêté
    date_octroi    = db.Column(db.Date, nullable=False)
    nb_echeances   = db.Column(db.Integer, nullable=False, default=1)  # nombre de mensualités
    motif          = db.Column(db.String(200))
    statut         = db.Column(db.String(12), default="EN_COURS")    # EN_COURS | SOLDE | ANNULE
    montant_rembourse = db.Column(db.Numeric(15, 2), default=0)
    cree_par       = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    cree_par_nom   = db.Column(db.String(150))
    date_creation  = db.Column(db.DateTime, default=utcnow)

    salarie = db.relationship("Salarie", backref="prets")
    remboursements = db.relationship("RemboursementPret", backref="pret",
                                     cascade="all, delete-orphan", lazy=True)

    STATUTS = {"EN_COURS": "En cours", "SOLDE": "Soldé", "ANNULE": "Annulé"}

    @property
    def statut_libelle(self):
        return self.STATUTS.get(self.statut, self.statut)

    @property
    def montant_echeance(self):
        """Mensualité théorique = capital / nombre d'échéances (arrondi)."""
        n = int(self.nb_echeances or 1) or 1
        return round(float(self.montant_total or 0) / n, 2)

    @property
    def montant_restant(self):
        return max(float(self.montant_total or 0) - float(self.montant_rembourse or 0), 0)

    @property
    def nb_echeances_reglees(self):
        return len([r for r in self.remboursements]) if self.remboursements else 0

    @property
    def progression_pct(self):
        tot = float(self.montant_total or 0)
        if tot <= 0:
            return 0
        return round(min(float(self.montant_rembourse or 0) / tot * 100, 100), 1)

    def recalculer_statut(self):
        """Repasse à SOLDE si tout est remboursé (ne touche pas à ANNULE)."""
        if self.statut == "ANNULE":
            return
        if self.montant_restant <= 0.5:
            self.statut = "SOLDE"
        else:
            self.statut = "EN_COURS"


class RemboursementPret(db.Model):
    """Un remboursement (mensualité) imputé sur un prêt, éventuellement rattaché
    au bulletin du mois où il a été prélevé."""
    __tablename__ = "remboursements_pret"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    pret_id       = db.Column(db.Integer, db.ForeignKey("prets_personnel.id"), nullable=False)
    montant       = db.Column(db.Numeric(15, 2), nullable=False)
    mois          = db.Column(db.Integer)
    annee         = db.Column(db.Integer)
    bulletin_id   = db.Column(db.Integer, db.ForeignKey("bulletins_paie.id"))  # si prélevé sur un bulletin
    note          = db.Column(db.String(200))
    date_remboursement = db.Column(db.Date, default=date.today)
    date_creation = db.Column(db.DateTime, default=utcnow)


class PointeurChantier(db.Model):
    """Compte de pointage léger (sans email) pour un chef de chantier ou une
    pointeuse. Le RH génère un code court + un PIN et le rattache à un site.
    L'authentification de la PWA se fait par code + PIN ; aucun mot de passe ni
    adresse e-mail n'est requis — adapté au personnel de terrain."""
    __tablename__ = "pointeurs_chantier"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    site_id       = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=True)
    nom           = db.Column(db.String(150), nullable=False)   # ex: "Jean (chef de chantier Akanda)"
    code          = db.Column(db.String(20), nullable=False, unique=True)  # code de connexion (unique global)
    pin_hash      = db.Column(db.String(256), nullable=False)   # PIN haché
    actif         = db.Column(db.Boolean, default=True)
    # Le pointeur EST souvent lui-même un travailleur du chantier (salarié ou
    # journalier) : on relie son compte à sa fiche pour qu'il figure dans sa
    # propre équipe et se pointe lui-même. Un seul des deux est renseigné.
    salarie_id    = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=True)
    journalier_id = db.Column(db.Integer, db.ForeignKey("journaliers.id"), nullable=True)
    derniere_connexion = db.Column(db.DateTime)
    cree_par      = db.Column(db.String(200))
    date_creation = db.Column(db.DateTime, default=utcnow)

    tenant     = db.relationship("Tenant", backref="pointeurs")
    site       = db.relationship("Site", backref="pointeurs")
    salarie    = db.relationship("Salarie", foreign_keys=[salarie_id])
    journalier = db.relationship("Journalier", foreign_keys=[journalier_id])

    __table_args__ = (
        db.Index("idx_pointeurs_tenant", "tenant_id"),
    )

    @property
    def travailleur(self):
        """Fiche travailleur reliée (salarié ou journalier), ou None."""
        return self.salarie or self.journalier

    def set_pin(self, pin):
        self.pin_hash = generate_password_hash(str(pin))

    def check_pin(self, pin):
        return check_password_hash(self.pin_hash, str(pin))

    @staticmethod
    def generer_code(prefixe="PT"):
        """Code court lisible, unique : préfixe + 6 caractères alphanumériques
        sans ambiguïté (sans 0/O/1/I/L)."""
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        for _ in range(20):
            code = prefixe + "".join(secrets.choice(alphabet) for _ in range(6))
            if not PointeurChantier.query.filter_by(code=code).first():
                return code
        # extrêmement improbable : rallonge
        return prefixe + secrets.token_hex(5).upper()


class FeuillePresence(db.Model):
    """Feuille de présence d'un chantier pour une date : porte la signature du
    chef de chantier (pointeur) attestant la présence de l'équipe du jour.
    Une feuille par (tenant, pointeur, date, chantier)."""
    __tablename__ = "feuilles_presence"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    pointeur_id    = db.Column(db.Integer, db.ForeignKey("pointeurs_chantier.id"), nullable=True)
    site_id        = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=True)
    date_pointage  = db.Column(db.Date, nullable=False)
    signature_chef = db.Column(db.Text)        # data URL PNG de la signature du chef
    chef_nom       = db.Column(db.String(150)) # libellé du pointeur au moment de la signature
    date_creation  = db.Column(db.DateTime, default=utcnow)

    pointeur = db.relationship("PointeurChantier")
    site     = db.relationship("Site")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "pointeur_id", "date_pointage", "site_id",
                            name="uix_feuille_presence"),
        db.Index("idx_feuille_tenant_date", "tenant_id", "date_pointage"),
    )


class EvenementCarriere(db.Model):
    """Événement de carrière d'un salarié : brique de la frise de carrière
    (promotion, augmentation, changement de poste, mobilité, titularisation,
    distinction, formation, sanction, départ…). Les promotions sont reliées à
    l'avenant de contrat correspondant (contrat_id). N'altère pas la paie :
    le changement de salaire passe par le contrat, pas par cet enregistrement."""
    __tablename__ = "evenements_carriere"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id     = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    type           = db.Column(db.String(20), nullable=False)   # voir TYPES
    date_effet     = db.Column(db.Date, nullable=False)
    titre          = db.Column(db.String(200))
    details        = db.Column(db.Text)
    ancienne_valeur = db.Column(db.String(255))
    nouvelle_valeur = db.Column(db.String(255))
    contrat_id     = db.Column(db.Integer, db.ForeignKey("contrats.id"), nullable=True)  # avenant lié
    cree_par       = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    cree_par_nom   = db.Column(db.String(150))
    date_creation  = db.Column(db.DateTime, default=utcnow)

    salarie = db.relationship("Salarie", backref="evenements_carriere")
    contrat = db.relationship("Contrat")

    __table_args__ = (
        db.Index("idx_evt_carriere_salarie", "tenant_id", "salarie_id"),
    )

    TYPES = {
        "EMBAUCHE":        "Embauche",
        "TITULARISATION":  "Titularisation",
        "PROMOTION":       "Promotion",
        "AUGMENTATION":    "Augmentation de salaire",
        "CHANGEMENT_POSTE":"Changement de poste",
        "MOBILITE":        "Mobilité / changement de site",
        "FORMATION":       "Formation",
        "DISTINCTION":     "Distinction / médaille",
        "SANCTION":        "Sanction",
        "DEPART":          "Départ",
        "AUTRE":           "Autre",
    }
    # Pastille (emoji) par type, pour la frise
    ICONES = {
        "EMBAUCHE": "🎉", "TITULARISATION": "✅", "PROMOTION": "⬆️",
        "AUGMENTATION": "💰", "CHANGEMENT_POSTE": "🔁", "MOBILITE": "📍",
        "FORMATION": "🎓", "DISTINCTION": "🏅", "SANCTION": "⚠️",
        "DEPART": "👋", "AUTRE": "•",
    }

    @property
    def type_libelle(self):
        return self.TYPES.get(self.type, self.type)

    @property
    def icone(self):
        return self.ICONES.get(self.type, "•")


class PalierMedaille(db.Model):
    """Palier de la médaille du travail (distinction nationale, Gabon).

    Paramétrable par entreprise : chaque tenant dispose de ses propres paliers.
    Les valeurs par défaut reprennent la Loi N° 021/2010 du 27/07/2010
    (modifiant le Code du Travail), art. 11 : bronze 10 ans, argent 20 ans,
    or 30 ans — pour un service continu dans la même entreprise. Le tenant peut
    ajuster les seuils, renommer ou désactiver un palier si un décret
    d'application vient préciser les conditions.
    """
    __tablename__ = "paliers_medaille"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    code           = db.Column(db.String(30), nullable=False)   # BRONZE / ARGENT / OR / ...
    libelle        = db.Column(db.String(100), nullable=False)   # "Médaille de bronze"
    annees_requises = db.Column(db.Integer, nullable=False)      # ancienneté continue requise
    ordre          = db.Column(db.Integer, default=0)            # tri d'affichage
    actif          = db.Column(db.Boolean, default=True)
    date_creation  = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "code", name="uq_palier_medaille_tenant_code"),
        db.Index("idx_paliers_medaille_tenant", "tenant_id", "actif"),
    )

    # Valeurs par défaut (Loi N° 021/2010, art. 11). Pastille pour l'affichage.
    DEFAUTS = [
        {"code": "BRONZE", "libelle": "Médaille de bronze", "annees_requises": 10, "ordre": 1},
        {"code": "ARGENT", "libelle": "Médaille d'argent",  "annees_requises": 20, "ordre": 2},
        {"code": "OR",     "libelle": "Médaille d'or",       "annees_requises": 30, "ordre": 3},
    ]
    ICONES = {"BRONZE": "🥉", "ARGENT": "🥈", "OR": "🥇"}

    @property
    def icone(self):
        return self.ICONES.get((self.code or "").upper(), "🏅")


# ══════════════════════════════════════════════════════════════════════════════
# DÉVELOPPEMENT RH (gestion de carrière — Phase 3) : formations, compétences,
# évaluations. Aucune de ces tables n'intervient dans le calcul de la paie.
# ══════════════════════════════════════════════════════════════════════════════

class Formation(db.Model):
    """Action de formation suivie (ou planifiée) par un salarié.

    Alimente la frise de carrière. Si la formation est certifiante avec une
    date d'expiration, elle déclenche l'alerte « formation à renouveler »."""
    __tablename__ = "formations"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id     = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    intitule       = db.Column(db.String(200), nullable=False)
    organisme      = db.Column(db.String(200))
    type           = db.Column(db.String(30), default="EXTERNE")   # INTERNE / EXTERNE / CERTIFIANTE
    date_debut     = db.Column(db.Date)
    date_fin       = db.Column(db.Date)
    duree_heures   = db.Column(db.Numeric(8, 2))
    cout           = db.Column(db.Numeric(15, 2))
    statut         = db.Column(db.String(20), default="PLANIFIEE")  # PLANIFIEE/EN_COURS/TERMINEE/ANNULEE
    certifiante    = db.Column(db.Boolean, default=False)
    date_expiration = db.Column(db.Date)   # validité d'une certification (alerte renouvellement)
    resultat       = db.Column(db.String(200))   # mention / note / "Réussi"
    details        = db.Column(db.Text)
    cree_par       = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    cree_par_nom   = db.Column(db.String(150))
    date_creation  = db.Column(db.DateTime, default=utcnow)

    salarie = db.relationship("Salarie", backref="formations")

    __table_args__ = (
        db.Index("idx_formations_tenant_salarie", "tenant_id", "salarie_id"),
        db.Index("idx_formations_expiration", "tenant_id", "date_expiration"),
    )

    TYPES = {"INTERNE": "Interne", "EXTERNE": "Externe", "CERTIFIANTE": "Certifiante"}
    STATUTS = {"PLANIFIEE": "Planifiée", "EN_COURS": "En cours",
               "TERMINEE": "Terminée", "ANNULEE": "Annulée"}

    @property
    def type_libelle(self):
        return self.TYPES.get(self.type, self.type)

    @property
    def statut_libelle(self):
        return self.STATUTS.get(self.statut, self.statut)


class Competence(db.Model):
    """Compétence du référentiel de l'entreprise (ex. « Soudure TIG », « Anglais »,
    « Conduite d'engins »). Évaluée par salarié via CompetenceSalarie."""
    __tablename__ = "competences"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    libelle       = db.Column(db.String(150), nullable=False)
    categorie     = db.Column(db.String(50))   # Technique / Management / Langue / Sécurité / Bureautique / Autre
    actif         = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "libelle", name="uq_competence_tenant_libelle"),
        db.Index("idx_competences_tenant", "tenant_id", "actif"),
    )

    CATEGORIES = ["Technique", "Management", "Langue", "Sécurité", "Bureautique", "Autre"]


class CompetenceSalarie(db.Model):
    """Niveau d'un salarié sur une compétence (4 niveaux). Une ligne par couple
    (salarié, compétence), mise à jour dans le temps."""
    __tablename__ = "competences_salarie"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id     = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    competence_id  = db.Column(db.Integer, db.ForeignKey("competences.id"), nullable=False)
    niveau         = db.Column(db.Integer, nullable=False)   # 1..4
    date_evaluation = db.Column(db.Date)
    evaluateur_nom = db.Column(db.String(150))
    commentaire    = db.Column(db.String(255))

    competence = db.relationship("Competence")
    salarie    = db.relationship("Salarie", backref="competences_salarie")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "salarie_id", "competence_id",
                            name="uq_comp_sal_tenant_salarie_competence"),
        db.Index("idx_comp_sal_salarie", "tenant_id", "salarie_id"),
    )

    NIVEAUX = {1: "Débutant", 2: "Intermédiaire", 3: "Confirmé", 4: "Expert"}

    @property
    def niveau_libelle(self):
        return self.NIVEAUX.get(self.niveau, "—")


class Evaluation(db.Model):
    """Entretien / évaluation d'un salarié. Note globale en lettres (A–E).
    N'altère jamais la paie."""
    __tablename__ = "evaluations"
    id                = db.Column(db.Integer, primary_key=True)
    tenant_id         = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id        = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    type              = db.Column(db.String(30), default="ANNUELLE")   # ANNUELLE/PERIODE_ESSAI/PROFESSIONNEL/AUTRE
    date_evaluation   = db.Column(db.Date)
    periode           = db.Column(db.String(20))   # ex. "2026", "2026-S1"
    note_globale      = db.Column(db.String(2))    # A/B/C/D/E
    evaluateur_id     = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    evaluateur_nom    = db.Column(db.String(150))
    points_forts      = db.Column(db.Text)
    axes_amelioration = db.Column(db.Text)
    objectifs         = db.Column(db.Text)   # objectifs pour la période suivante
    commentaire_salarie = db.Column(db.Text)
    statut            = db.Column(db.String(20), default="PLANIFIEE")  # PLANIFIEE / REALISEE
    cree_par          = db.Column(db.Integer, db.ForeignKey("utilisateurs.id"))
    cree_par_nom      = db.Column(db.String(150))
    date_creation     = db.Column(db.DateTime, default=utcnow)

    salarie = db.relationship("Salarie", backref="evaluations")

    __table_args__ = (
        db.Index("idx_evaluations_tenant_salarie", "tenant_id", "salarie_id"),
    )

    TYPES = {"ANNUELLE": "Évaluation annuelle", "PERIODE_ESSAI": "Fin de période d'essai",
             "PROFESSIONNEL": "Entretien professionnel", "AUTRE": "Autre"}
    NOTES = {"A": "Excellent", "B": "Très bien", "C": "Bien",
             "D": "À améliorer", "E": "Insuffisant"}

    @property
    def type_libelle(self):
        return self.TYPES.get(self.type, self.type)

    @property
    def note_libelle(self):
        return self.NOTES.get((self.note_globale or "").upper(), "—")


# ═══════════════════════════════════════════════════════════════════════════
# CYCLE DE PAIE — suivi des obligations (déclarations et paiements)
# ═══════════════════════════════════════════════════════════════════════════

class ObligationPaie(db.Model):
    """Déclaration / paiement d'une obligation du cycle de paie, saisi par
    l'entreprise (IRPP, TCS, FNH, CFP mensuels ; CNSS, CNAMGS trimestriels ;
    DAS annuelle ; comptabilisation des écritures de paie du mois).
    periode = mois (1-12), trimestre (1-4) ou 0 pour l'année, selon le code."""
    __tablename__ = "obligations_paie"
    id               = db.Column(db.Integer, primary_key=True)
    tenant_id        = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    code             = db.Column(db.String(20), nullable=False)
    annee            = db.Column(db.Integer, nullable=False)
    periode          = db.Column(db.Integer, nullable=False, default=0)
    statut           = db.Column(db.String(20), nullable=False, default="A_FAIRE")  # A_FAIRE | DECLARE | PAYE
    date_declaration = db.Column(db.Date)
    date_paiement    = db.Column(db.Date)
    montant_paye     = db.Column(db.Numeric(15, 2))
    reference        = db.Column(db.String(100))      # n° de quittance / de dépôt
    commentaire      = db.Column(db.String(500))
    saisi_par        = db.Column(db.String(150))
    date_modification = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (db.UniqueConstraint("tenant_id", "code", "annee", "periode",
                                          name="uq_obligation_paie"),)


class EcheanceLegale(db.Model):
    """Date limite légale d'une obligation, réglée par le super-administrateur.
    Aucune valeur par défaut : tant qu'elle n'est pas renseignée, le cycle de
    paie affiche « échéance à renseigner ».
    Échéance = jour `jour` du mois situé `decalage_mois` mois après le dernier
    mois de la période (mois, trimestre ou année)."""
    __tablename__ = "echeances_legales"
    id            = db.Column(db.Integer, primary_key=True)
    code          = db.Column(db.String(20), nullable=False, unique=True)
    jour          = db.Column(db.Integer)            # 1-31 (ramené au dernier jour du mois si besoin)
    decalage_mois = db.Column(db.Integer)            # 0 = même mois que la fin de période
    source        = db.Column(db.String(300))        # texte de référence
    verifie_le    = db.Column(db.Date)
    date_modification = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class EcheanceTenant(db.Model):
    """Objectif interne d'une entreprise pour une obligation du cycle de paie
    (ex. payer l'IRPP le 10 du mois suivant). Même règle de calcul que
    EcheanceLegale ; n'efface jamais la date légale, qui reste affichée."""
    __tablename__ = "echeances_tenant"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    code          = db.Column(db.String(20), nullable=False)
    jour          = db.Column(db.Integer, nullable=False)
    decalage_mois = db.Column(db.Integer, nullable=False)
    note          = db.Column(db.String(300))
    date_modification = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (db.UniqueConstraint("tenant_id", "code", name="uq_echeance_tenant"),)


# ═══════════════════════════════════════════════════════════════════════════
# PORTAIL DES EMPLOYÉS — consultation des bulletins par les salariés
# ═══════════════════════════════════════════════════════════════════════════

class AccesSalarie(db.Model):
    """Accès d'un salarié au portail (/espace) : matricule + code PIN.
    Le PIN est choisi par le salarié lui-même à l'activation ; le RH ne le
    connaît jamais (il ne remet qu'un code d'activation à usage unique)."""
    __tablename__ = "acces_salaries"
    id                 = db.Column(db.Integer, primary_key=True)
    tenant_id          = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    salarie_id         = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False, unique=True)
    pin_hash           = db.Column(db.String(256))           # vide tant que l'accès n'est pas activé
    code_hash          = db.Column(db.String(256))           # code d'activation (haché, usage unique)
    code_expire        = db.Column(db.DateTime)
    actif              = db.Column(db.Boolean, default=True, nullable=False)
    version            = db.Column(db.Integer, default=1, nullable=False)  # +1 = sessions ouvertes invalidées
    echecs             = db.Column(db.Integer, default=0, nullable=False)
    verrou_jusqua      = db.Column(db.DateTime)
    consentement_le    = db.Column(db.DateTime)              # accord pour le bulletin électronique
    active_le          = db.Column(db.DateTime)              # première activation
    derniere_connexion = db.Column(db.DateTime)
    invite_le          = db.Column(db.DateTime)
    invite_par         = db.Column(db.String(150))
    date_creation      = db.Column(db.DateTime, default=utcnow)
    salarie = db.relationship("Salarie", backref=db.backref("acces_portail", uselist=False))


class ConsultationBulletin(db.Model):
    """Trace de mise à disposition : bulletin consulté ou téléchargé par le salarié."""
    __tablename__ = "consultations_bulletins"
    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    salarie_id  = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False, index=True)
    bulletin_id = db.Column(db.Integer, db.ForeignKey("bulletins_paie.id"), nullable=False, index=True)
    date        = db.Column(db.DateTime, default=utcnow, nullable=False)
    ip          = db.Column(db.String(45))


class DemandeDocument(db.Model):
    """Attestation demandée par un salarié sur son espace. Le PDF est généré et
    figé au moment où le RH la délivre (ou automatiquement si l'entreprise l'a
    choisi)."""
    __tablename__ = "demandes_documents"
    id              = db.Column(db.Integer, primary_key=True)
    tenant_id       = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    salarie_id      = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False, index=True)
    type_doc        = db.Column(db.String(30), nullable=False)   # ATTESTATION_TRAVAIL | ATTESTATION_SALAIRE
    motif           = db.Column(db.String(300))
    statut          = db.Column(db.String(15), nullable=False, default="DEMANDE")   # DEMANDE | DELIVRE | REFUSE
    date_demande    = db.Column(db.DateTime, default=utcnow)
    date_traitement = db.Column(db.DateTime)
    traite_par      = db.Column(db.String(150))
    motif_refus     = db.Column(db.String(300))
    pdf             = db.Column(db.LargeBinary)
    salarie = db.relationship("Salarie")


class DemandeProfil(db.Model):
    """Modification de ses informations personnelles demandée par un salarié ;
    appliquée à sa fiche seulement après acceptation par le RH."""
    __tablename__ = "demandes_profil"
    id              = db.Column(db.Integer, primary_key=True)
    tenant_id       = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    salarie_id      = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False, index=True)
    champ           = db.Column(db.String(40), nullable=False)
    ancienne_valeur = db.Column(db.String(300))
    nouvelle_valeur = db.Column(db.String(300))
    statut          = db.Column(db.String(15), nullable=False, default="DEMANDE")   # DEMANDE | ACCEPTE | REFUSE
    date_demande    = db.Column(db.DateTime, default=utcnow)
    date_traitement = db.Column(db.DateTime)
    traite_par      = db.Column(db.String(150))
    motif_refus     = db.Column(db.String(300))
    salarie = db.relationship("Salarie")


# ═══════════════════ VIREMENTS DES SALAIRES (29/10/2026) — blueprints/tenant/virements.py ═══════════════════
class CompteBancaire(db.Model):
    """Compte bancaire de l'entreprise, donneur d'ordre des virements de salaires."""
    __tablename__ = "comptes_bancaires"
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    banque        = db.Column(db.String(100), nullable=False)
    intitule      = db.Column(db.String(150))          # libellé du compte (ex. « Compte salaires »)
    rib           = db.Column(db.String(50), nullable=False)
    agence        = db.Column(db.String(150))
    actif         = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (db.Index("idx_comptes_bancaires_tenant", "tenant_id"),)


class OrdreVirement(db.Model):
    """Ordre de virement des salaires d'une période, adressé à la banque de l'entreprise.
    PREPARE → EXECUTE (bulletins payés) ; ou ANNULE (les bulletins redeviennent disponibles)."""
    __tablename__ = "ordres_virement"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    periode_id     = db.Column(db.Integer, db.ForeignKey("periodes_paie.id"), nullable=False)
    compte_id      = db.Column(db.Integer, db.ForeignKey("comptes_bancaires.id"), nullable=False)
    numero         = db.Column(db.String(30), nullable=False)        # OV-2026-0001
    statut         = db.Column(db.String(20), default="PREPARE")
    date_ordre     = db.Column(db.Date, nullable=False)
    date_execution = db.Column(db.Date)                              # date de valeur demandée / constatée
    auteur         = db.Column(db.String(150))
    motif_annulation = db.Column(db.String(300))
    date_creation  = db.Column(db.DateTime, default=utcnow)
    periode = db.relationship("PeriodePaie")
    compte  = db.relationship("CompteBancaire")
    lignes  = db.relationship("LigneOrdreVirement", back_populates="ordre", cascade="all, delete-orphan",
                              order_by="LigneOrdreVirement.nom")
    __table_args__ = (db.UniqueConstraint("tenant_id", "numero", name="uq_ordre_virement_numero"),
                      db.Index("idx_ordres_virement_tenant", "tenant_id", "periode_id"))

    @property
    def total(self):
        return round(sum(float(l.montant or 0) for l in self.lignes), 2)


class LigneOrdreVirement(db.Model):
    """Bénéficiaire d'un ordre : coordonnées et montant figés au moment de l'ordre."""
    __tablename__ = "lignes_ordre_virement"
    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    ordre_id    = db.Column(db.Integer, db.ForeignKey("ordres_virement.id"), nullable=False)
    # vide quand le bulletin d'un ordre ANNULÉ a ensuite été supprimé (brouillon) : la ligne garde
    # nom, RIB et montant figés pour l'historique bancaire
    bulletin_id = db.Column(db.Integer, db.ForeignKey("bulletins_paie.id"))
    salarie_id  = db.Column(db.Integer, db.ForeignKey("salaries.id"))
    matricule   = db.Column(db.String(50))
    nom         = db.Column(db.String(250), nullable=False)
    banque      = db.Column(db.String(100))
    rib         = db.Column(db.String(50), nullable=False)
    montant     = db.Column(db.Numeric(15, 2), nullable=False)
    ordre    = db.relationship("OrdreVirement", back_populates="lignes")
    bulletin = db.relationship("BulletinPaie")
    __table_args__ = (db.Index("idx_lignes_ov_bulletin", "tenant_id", "bulletin_id"),)


# ═══════════════════ RAPPELS DE SALAIRE (30/10/2026) — rappels_salaire.py ═══════════════════
class RappelSalaire(db.Model):
    """Rappel de salaire : augmentation ou reclassement à effet rétroactif. Les bulletins déjà
    validés ne sont jamais modifiés ; la différence due, mois par mois, est versée sur le
    bulletin de la période d'imputation (composant soumis aux cotisations et à l'IRPP).
    Statut : A_VERSER (en attente du bulletin) → VERSE quand ce bulletin est validé ; ANNULE."""
    __tablename__ = "rappels_salaire"
    id                 = db.Column(db.Integer, primary_key=True)
    tenant_id          = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    salarie_id         = db.Column(db.Integer, db.ForeignKey("salaries.id"), nullable=False)
    periode_id         = db.Column(db.Integer, db.ForeignKey("periodes_paie.id"), nullable=False)  # versement
    motif              = db.Column(db.String(300), nullable=False)
    date_effet         = db.Column(db.Date, nullable=False)          # 1er mois concerné
    ancien_salaire     = db.Column(db.Numeric(15, 2), nullable=False)
    nouveau_salaire    = db.Column(db.Numeric(15, 2), nullable=False)
    rubriques          = db.Column(db.String(200))                   # rubriques réévaluées (codes, séparés par ,)
    statut             = db.Column(db.String(20), default="A_VERSER")
    auteur             = db.Column(db.String(150))
    motif_annulation   = db.Column(db.String(300))
    date_creation      = db.Column(db.DateTime, default=utcnow)
    # Mise à jour du contrat : avenant au nouveau salaire à compter du mois d'effet.
    # contrat_maj : AVENANT (créé par le rappel) | DEJA_A_JOUR (contrat déjà au nouveau salaire)
    #               | RETABLI (rappel annulé, contrat précédent réactivé) | CONSERVE (annulé, avenant gardé)
    contrat_id           = db.Column(db.Integer, db.ForeignKey("contrats.id"))   # avenant créé
    contrat_precedent_id = db.Column(db.Integer, db.ForeignKey("contrats.id"))   # contrat désactivé par l'avenant
    contrat_maj          = db.Column(db.String(20))
    salarie = db.relationship("Salarie")
    periode = db.relationship("PeriodePaie")
    lignes  = db.relationship("LigneRappel", back_populates="rappel", cascade="all, delete-orphan",
                              order_by="LigneRappel.id")
    __table_args__ = (db.Index("idx_rappels_tenant", "tenant_id", "periode_id", "statut"),)

    @property
    def total(self):
        return round(sum(float(l.montant or 0) for l in self.lignes), 2)


class LigneRappel(db.Model):
    """Un mois rappelé : bulletin validé de référence, détail recalculé, ajustement éventuel."""
    __tablename__ = "lignes_rappel"
    id           = db.Column(db.Integer, primary_key=True)
    tenant_id    = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    rappel_id    = db.Column(db.Integer, db.ForeignKey("rappels_salaire.id"), nullable=False)
    periode_id   = db.Column(db.Integer, db.ForeignKey("periodes_paie.id"), nullable=False)
    bulletin_id  = db.Column(db.Integer, db.ForeignKey("bulletins_paie.id", ondelete="SET NULL"))   # référence (détail figé)
    libelle_mois = db.Column(db.String(30))
    detail       = db.Column(db.Text)                          # JSON {rubrique: [versé, dû, écart]}
    ecart_calcule = db.Column(db.Numeric(15, 2), default=0)
    ajustement   = db.Column(db.Numeric(15, 2), default=0)    # correction manuelle (+/-)
    montant      = db.Column(db.Numeric(15, 2), default=0)    # écart calculé + ajustement
    rappel   = db.relationship("RappelSalaire", back_populates="lignes")
    periode  = db.relationship("PeriodePaie")
    __table_args__ = (db.Index("idx_lignes_rappel", "rappel_id"),)
