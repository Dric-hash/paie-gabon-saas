"""
models.py — Modèles multi-tenant SaaS Paie Gabon
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import secrets

db = SQLAlchemy()

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
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow)
    date_expiration  = db.Column(db.DateTime)
    token_api        = db.Column(db.String(64), unique=True)
    notes            = db.Column(db.Text)
    modele_bulletin  = db.Column(db.String(30), default="classique")
    # Valeurs : "classique" | "moderne" | "minimaliste"
    # ✅ LOGO — colonne Text pour stocker base64 sans limite
    logo_url         = db.Column(db.Text)
    langue           = db.Column(db.String(5), default="fr")  # "fr" | "en"
    # Convention collective applicable : "AUCUNE" | "BTP" | "COMMERCE"
    convention       = db.Column(db.String(20), default="AUCUNE")
    # Taux d'acquisition des congés (jours ouvrables / mois travaillé, Art. 222).
    # Minimum légal adulte = 2.0 ; défaut historique de l'app = 2.5 (avantage).
    jours_conge_par_mois = db.Column(db.Numeric(3, 1), default=2.5)
    # Seuil hebdomadaire de déclenchement des heures supplémentaires (en heures).
    # La loi fixe le seuil légal à 40h/semaine. Certaines entreprises bénéficient
    # d'une dérogation portant ce seuil à 45h ou 48h : en deçà, les heures restent
    # payées au taux normal. Bornes admises : 40h (légal) → 48h (max contractuel).
    seuil_heures_sup_hebdo = db.Column(db.Numeric(4, 1), default=40.0)

    utilisateurs = db.relationship("Utilisateur", backref="tenant", lazy=True, foreign_keys="Utilisateur.tenant_id")
    salaries     = db.relationship("Salarie", backref="tenant", lazy=True)
    periodes     = db.relationship("PeriodePaie", backref="tenant", lazy=True)
    categories   = db.relationship("CategorieEmploi", backref="tenant", lazy=True)

    def generate_token(self): self.token_api = secrets.token_hex(32)

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
    date_creation      = db.Column(db.DateTime, default=datetime.utcnow)
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

    def set_password(self, pw): self.mot_de_passe_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.mot_de_passe_hash, pw)

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
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

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
    date_creation   = db.Column(db.DateTime, default=datetime.utcnow)
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
    situation_matrimoniale = db.Column(db.String(50))
    nb_enfants             = db.Column(db.Integer, default=0)
    nb_enfants_moins_16ans = db.Column(db.Integer, default=0)
    nombre_parts           = db.Column(db.Numeric(4,1), default=1)
    numero_cnss            = db.Column(db.String(30))
    numero_cnamgs          = db.Column(db.String(30))
    emploi                 = db.Column(db.String(200))
    assujetti_cnamgs       = db.Column(db.Boolean, default=True)
    type_rupture           = db.Column(db.String(100))
    statut                 = db.Column(db.String(20), default="ACTIF")
    date_creation          = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification      = db.Column(db.DateTime, onupdate=datetime.utcnow)

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
    salaire_base = db.Column(db.Numeric(15,2), nullable=False)
    poste        = db.Column(db.String(200))
    categorie_id = db.Column(db.Integer, db.ForeignKey("categories_emploi.id"))
    actif        = db.Column(db.Boolean, default=True)
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
    nb_jours_travailles   = db.Column(db.Integer)
    salaire_base          = db.Column(db.Numeric(15,2), nullable=False)
    heures_sup_10         = db.Column(db.Numeric(15,2), default=0)
    heures_sup_30         = db.Column(db.Numeric(15,2), default=0)
    heures_sup_40         = db.Column(db.Numeric(15,2), default=0)
    heures_sup_70         = db.Column(db.Numeric(15,2), default=0)
    absences              = db.Column(db.Numeric(15,2), default=0)
    sursalaire            = db.Column(db.Numeric(15,2), default=0)
    prime_caisse          = db.Column(db.Numeric(15,2), default=0)
    carburant             = db.Column(db.Numeric(15,2), default=0)
    prime_anciennete      = db.Column(db.Numeric(15,2), default=0)
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
    net_a_payer           = db.Column(db.Numeric(15,2), nullable=False)

    # Base et Taux saisis manuellement pour chaque rubrique
    base_salaire_base                   = db.Column(db.Numeric(15,2), default=0)
    taux_salaire_base                   = db.Column(db.String(20), default='')
    base_heures_sup_10                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_10                  = db.Column(db.String(20), default='')
    base_heures_sup_30                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_30                  = db.Column(db.String(20), default='')
    base_heures_sup_40                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_40                  = db.Column(db.String(20), default='')
    base_heures_sup_70                  = db.Column(db.Numeric(15,2), default=0)
    taux_heures_sup_70                  = db.Column(db.String(20), default='')
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
    date_creation         = db.Column(db.DateTime, default=datetime.utcnow)
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
    salarie = db.relationship("Salarie", backref="conges")
    @property
    def jours_restants(self): return float(self.jours_acquis or 0) - float(self.jours_pris or 0)


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
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
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
    statut        = db.Column(db.String(20), default="ACTIF")
    date_embauche = db.Column(db.Date)
    date_debut    = db.Column(db.Date)           # début de mission (libre)
    date_fin      = db.Column(db.Date)           # fin de mission (optionnel)
    nationalite   = db.Column(db.String(60))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    pointages = db.relationship("Pointage", backref="journalier", lazy=True,
                foreign_keys="Pointage.journalier_id")
    @property
    def nom_complet(self): return f"{self.nom} {self.prenom}"
    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        if d["taux_horaire"]: d["taux_horaire"] = float(d["taux_horaire"])
        if d["date_creation"]: d["date_creation"] = str(d["date_creation"])
        if d.get("date_embauche"): d["date_embauche"] = str(d["date_embauche"])
        d["nom_complet"] = self.nom_complet
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
    taux_horaire  = db.Column(db.Numeric(12,2), nullable=False)
    montant_brut  = db.Column(db.Numeric(15,2), default=0)
    statut        = db.Column(db.String(20), default="EN_ATTENTE")
    observation   = db.Column(db.String(200))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    journalier = db.relationship("Journalier", backref="feuilles_paie")
    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k in ["date_debut","date_fin","date_paiement","date_creation"]:
            if d[k]: d[k] = str(d[k])
        for k in ["total_heures","taux_horaire","montant_brut"]:
            if d[k] is not None: d[k] = float(d[k])
        return d


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
    date_creation    = db.Column(db.DateTime, default=datetime.utcnow)
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
    client_secret = db.Column(db.String(128), nullable=False)
    description   = db.Column(db.String(300))
    actif         = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
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
    date_action  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

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

    statut        = db.Column(db.String(20), default="ACTIF")  # ACTIF | INACTIF
    notes         = db.Column(db.Text)
    date_creation     = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, onupdate=datetime.utcnow)

    contrats = db.relationship("ContratPrestation", backref="prestataire", lazy=True)
    factures = db.relationship("FacturePrestataire", backref="prestataire", lazy=True)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "code"),
        db.Index("idx_prestataires_tenant_statut", "tenant_id", "statut"),
        db.Index("idx_prestataires_tenant_cat", "tenant_id", "categorie"),
    )

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
    date_creation  = db.Column(db.DateTime, default=datetime.utcnow)

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

    numero         = db.Column(db.String(50), nullable=False)  # n° de facture
    date_facture   = db.Column(db.Date, nullable=False)
    date_echeance  = db.Column(db.Date)
    description    = db.Column(db.String(300))

    # Montants
    montant_ht     = db.Column(db.Numeric(15,2), nullable=False)
    taux_tva       = db.Column(db.Numeric(5,2), default=18)     # TVA Gabon = 18%
    montant_tva    = db.Column(db.Numeric(15,2), default=0)
    taux_retenue   = db.Column(db.Numeric(5,2), default=0)      # retenue à la source
    montant_retenue = db.Column(db.Numeric(15,2), default=0)
    montant_ttc    = db.Column(db.Numeric(15,2), default=0)     # HT + TVA
    montant_net_a_payer = db.Column(db.Numeric(15,2), default=0)  # TTC - retenue

    statut         = db.Column(db.String(20), default="EN_ATTENTE")  # EN_ATTENTE | PAYEE | PARTIELLE | ANNULEE
    montant_paye   = db.Column(db.Numeric(15,2), default=0)
    date_creation  = db.Column(db.DateTime, default=datetime.utcnow)

    paiements = db.relationship("PaiementPrestataire", backref="facture", lazy=True)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "prestataire_id", "numero"),
        db.Index("idx_factures_tenant_statut", "tenant_id", "statut"),
        db.Index("idx_factures_tenant_date", "tenant_id", "date_facture"),
    )

    def calculer(self):
        """Recalcule TVA, retenue, TTC et net à payer à partir du HT."""
        ht = float(self.montant_ht or 0)
        self.montant_tva = round(ht * float(self.taux_tva or 0) / 100, 2)
        self.montant_ttc = round(ht + float(self.montant_tva), 2)
        self.montant_retenue = round(ht * float(self.taux_retenue or 0) / 100, 2)
        self.montant_net_a_payer = round(float(self.montant_ttc) - float(self.montant_retenue), 2)
        return self

    @property
    def reste_a_payer(self):
        return round(float(self.montant_net_a_payer or 0) - float(self.montant_paye or 0), 2)

    @property
    def statut_label(self):
        return {"EN_ATTENTE": "En attente", "PAYEE": "Payée",
                "PARTIELLE": "Partielle", "ANNULEE": "Annulée"}.get(self.statut, self.statut)

    def to_dict(self):
        d = {}
        for c in self.__table__.columns:
            val = getattr(self, c.name)
            d[c.name] = (str(val) if isinstance(val, (date, datetime))
                         else (float(val) if hasattr(val, "__float__") and val is not None else val))
        d["reste_a_payer"] = self.reste_a_payer
        d["statut_label"] = self.statut_label
        return d


class PaiementPrestataire(db.Model):
    """Paiement effectué sur une facture de prestataire."""
    __tablename__ = "paiements_prestataire"
    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    facture_id     = db.Column(db.Integer, db.ForeignKey("factures_prestataire.id"), nullable=False)

    montant        = db.Column(db.Numeric(15,2), nullable=False)
    date_paiement  = db.Column(db.Date, nullable=False)
    mode_paiement  = db.Column(db.String(30), default="VIREMENT")
    reference      = db.Column(db.String(100))   # n° de transaction / chèque
    notes          = db.Column(db.String(300))
    date_creation  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index("idx_paiements_prest_tenant", "tenant_id", "facture_id"),
    )

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k in ("date_paiement", "date_creation"):
            if d.get(k): d[k] = str(d[k])
        if d.get("montant"): d["montant"] = float(d["montant"])
        return d
