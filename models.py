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
    # ✅ LOGO — colonne Text pour stocker base64 sans limite
    logo_url         = db.Column(db.Text)

    utilisateurs = db.relationship("Utilisateur", backref="tenant", lazy=True, foreign_keys="Utilisateur.tenant_id")
    salaries     = db.relationship("Salarie", backref="tenant", lazy=True)
    periodes     = db.relationship("PeriodePaie", backref="tenant", lazy=True)
    categories   = db.relationship("CategorieEmploi", backref="tenant", lazy=True)

    def generate_token(self): self.token_api = secrets.token_hex(32)

    @property
    def nb_salaries_actifs(self):
        return Salarie.query.filter_by(tenant_id=self.id, statut="ACTIF").count()

    @property
    def est_dans_limite(self):
        if not self.plan or not self.plan.max_salaries: return True
        return self.nb_salaries_actifs < self.plan.max_salaries

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
    reset_token        = db.Column(db.String(200))
    reset_token_expiry = db.Column(db.DateTime)

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

    @property
    def can_edit(self): return self.role_normalized in ("SUPER_ADMIN", "TENANT_ADMIN", "GESTIONNAIRE")

    def to_dict(self):
        return {"id":self.id,"nom":self.nom,"prenom":self.prenom,
                "email":self.email,"role":self.role,
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
    __table_args__ = (db.UniqueConstraint("tenant_id","matricule"),)

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
    __table_args__ = (db.UniqueConstraint("tenant_id","salarie_id","periode_id"),)

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
    salarie = db.relationship("Salarie", backref="pointages", foreign_keys=[salarie_id])
    __table_args__ = (
        db.UniqueConstraint("tenant_id","date_pointage","salarie_id"),
        db.UniqueConstraint("tenant_id","date_pointage","journalier_id"),
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
