"""
convention_bois.py — Convention « Industries du Bois, Sciages et Placages »
============================================================================
Données et règles de calcul propres à la Convention Collective Nationale des
Industries du Bois du Gabon (26 février 1983, révision salariale 1994).

Valeurs issues de la convention et fournies/validées par l'utilisateur
(Ameriack I.T. Solutions). Module de constantes + fonctions pures : il ne
modifie rien ; le branchement se fait dans calculs_paie.py / tenant.py.

Base horaire : 40 h/semaine → 173,33 h/mois (les heures sup commencent à la
41ᵉ heure, conformément à l'Art. 38).
"""

CODE_CONVENTION = "BOIS"
LIBELLE_CONVENTION = "Convention Collective des Industries du Bois, Sciages et Placages"

HEURES_MENSUELLES = 173.33  # 40 h/sem


# ── 1. Grille salariale conventionnelle (circulaire 1994) ─────────────────────
# code, libellé, salaire mensuel minimum (FCFA)
GRILLE_BOIS = [
    # I. Personnel d'exécution & ouvriers
    ("MO",  "Manœuvre Ordinaire",           82_000),
    ("MS",  "Manœuvre Spécialisé",          82_500),
    ("OS1", "Ouvrier Spécialisé 1",         83_500),
    ("OS2", "Ouvrier Spécialisé 2",         84_500),
    ("OP1", "Ouvrier Professionnel 1",      85_200),
    ("OP2", "Ouvrier Professionnel 2",      86_400),
    ("OP3", "Ouvrier Professionnel 3",      94_500),
    # II. Employés de bureau
    ("E2",  "Employé de bureau — E2",       82_500),
    ("E3",  "Employé de bureau — E3",       84_000),
    ("E4",  "Employé de bureau — E4",      101_500),
    # III. Agent de maîtrise
    ("AM",  "Agent de maîtrise",           115_900),
    # IV. Cadres & ingénieurs
    ("C1",  "Cadre débutant (Pos. I)",     248_700),
    ("C2",  "Cadre confirmé (Pos. II)",    333_100),
    ("C3",  "Cadre supérieur (Pos. III)",  416_300),
    ("CS",  "Cadre dirigeant (Pos. Sup.)", 556_000),
]


def grille_salaire_bois_seed() -> dict:
    """Graine de grille au format attendu par l'app : {code: {"1": montant}}."""
    return {code: {"1": float(montant)} for code, _lib, montant in GRILLE_BOIS}


# ── 2. Heures supplémentaires (Art. 38) ───────────────────────────────────────
# Correspondance avec les 5 « cases » du moteur PaieGabon :
#   "10"  : 41ᵉ→48ᵉ heure (1 à 8 H.S.), de JOUR ............ +13 %
#   "30"  : 49ᵉ heure et plus, de JOUR ..................... +35 %
#   "30b" : repos hebdomadaire / jours fériés, de JOUR ..... +50 %
#   "40"  : heures de NUIT, jours normaux .................. +75 %
#   "70"  : heures de NUIT, repos hebdomadaire / fériés .... +125 %
COEFFS_HS_BOIS = {
    "10": 1.13, "30": 1.35, "30b": 1.50, "40": 1.75, "70": 2.25,
}


# ── 3. Prime d'ancienneté (Art. 46.5) ─────────────────────────────────────────
def calculer_prime_anciennete_bois(salaire_base: float, anciennete_annees: int) -> float:
    """Prime d'ancienneté — Art. 46.5 : +2 % après 2 ans, puis +1 %/an,
    calculée sur le salaire de base conventionnel. (Barème identique BTP.)"""
    if anciennete_annees < 2 or salaire_base <= 0:
        return 0.0
    taux = min(0.02 + 0.01 * (anciennete_annees - 2), 0.30)
    return round(salaire_base * taux, 2)


# ── 4. Prime d'assiduité (Art. 49) ────────────────────────────────────────────
# Base : forfait de 11 h/mois payé au tarif horaire de base de la catégorie.
# Pénalités d'absence non autorisée : 1 → -25 %, 2 → -50 %, 3 → -100 %.
FORFAIT_ASSIDUITE_HEURES = 11


def calculer_prime_assiduite_bois(salaire_base: float, nb_absences: int = 0) -> float:
    """Prime d'assiduité — Art. 49.
    Forfait de 11 h au taux horaire (= salaire_base / 173,33).
    -25 % à 1 absence, -50 % à 2, supprimée dès 3."""
    if salaire_base <= 0:
        return 0.0
    taux_horaire = salaire_base / HEURES_MENSUELLES
    forfait = FORFAIT_ASSIDUITE_HEURES * taux_horaire
    if nb_absences >= 3:
        return 0.0
    if nb_absences == 2:
        return round(forfait * 0.50, 2)
    if nb_absences == 1:
        return round(forfait * 0.75, 2)
    return round(forfait, 2)


# ── 5. Indemnité compensatrice de logement (Art. 53) ──────────────────────────
# Due au personnel recruté hors du lieu d'emploi et NON logé par l'entreprise.
# Taux minimum : 20 % du salaire de base de la catégorie.
TAUX_INDEMNITE_LOGEMENT = 0.20


def calculer_indemnite_logement_bois(salaire_base: float, recrute_hors_lieu: bool = False,
                                     non_loge: bool = True) -> float:
    """Indemnité compensatrice de logement — Art. 53 : 20 % du salaire de base,
    si le salarié est recruté hors du lieu d'emploi et non logé."""
    if salaire_base <= 0 or not (recrute_hors_lieu and non_loge):
        return 0.0
    return round(salaire_base * TAUX_INDEMNITE_LOGEMENT, 2)


# ── 6. Gratification de fin d'année (Art. 52) ─────────────────────────────────
# Due après 1 an de présence continue. Part fixe = 25 % (proratisée au salaire
# de base). Part variable (75 %) laissée à l'appréciation hiérarchique.
TAUX_GRATIFICATION_FIXE = 0.25


def calculer_gratification_fin_annee_bois(moyenne_mensuelle_base: float,
                                          mois_presence: int = 12,
                                          anciennete_annees: float = 0) -> float:
    """Gratification de fin d'année (part fixe) — Art. 52.
    25 % de la moyenne mensuelle du salaire de base, au prorata des mois de
    présence, à condition d'au moins 1 an de présence continue."""
    if moyenne_mensuelle_base <= 0 or anciennete_annees < 1:
        return 0.0
    prorata = min(mois_presence, 12) / 12.0
    return round(moyenne_mensuelle_base * TAUX_GRATIFICATION_FIXE * prorata, 2)


# ── 7. Périodes d'essai (Art. 13) — durées maximales (hors renouvellement) ────
def periode_essai_max_bois(categorie_code: str) -> int:
    """Durée maximale de la période d'essai EN JOURS, selon la catégorie
    (renouvelable une fois — on renvoie la durée de base).
      Manœuvres (MO, MS) ................ 15 jours
      OS1, OS2, E2 ...................... 30 jours (1 mois)
      OP1, OP2 .......................... 60 jours (2 mois)
      OP3, E3, E4, AM, Cadres ........... 90 jours (3 mois)
    """
    code = (categorie_code or "").upper()
    if code in ("MO", "MS"):
        return 15
    if code in ("OS1", "OS2", "E2"):
        return 30
    if code in ("OP1", "OP2"):
        return 60
    # OP3, E3, E4, AM, C1, C2, C3, CS
    return 90


# ── 8. Préavis de licenciement / démission (Art. 30.3) ────────────────────────
def calculer_preavis_bois(anciennete_annees: float) -> int:
    """Durée du préavis EN JOURS — Art. 30.3.
      < 1 an ............... 15 jours
      1 à 3 ans ............ 1 mois (30 j)
      3 à 5 ans ............ 2 mois (60 j)
      5 à 10 ans ........... 3 mois (90 j)
      10 à 15 ans .......... 4 mois (120 j)
      15 à 20 ans .......... 6 mois (180 j)
      20 à 30 ans .......... 7 mois (210 j)
      > 30 ans ............. 7 mois + 15 j par année supplémentaire au-delà de 30
    (Conversion mois→jours à 30 j.)
    """
    a = anciennete_annees
    if a < 1:
        return 15
    if a < 3:
        return 30
    if a < 5:
        return 60
    if a < 10:
        return 90
    if a < 15:
        return 120
    if a < 20:
        return 180
    if a <= 30:
        return 210
    return 210 + 15 * (int(a) - 30)


# ── 9. Indemnité de services rendus / licenciement (Art. 32) ──────────────────
# Due dès 2 ans d'ancienneté (hors faute lourde) ou départ retraite. Calculée
# sur le salaire global moyen des 12 derniers mois, par tranches d'années :
#   2ᵉ→5ᵉ année ......... 20 % / an
#   6ᵉ→10ᵉ année ........ 25 % / an
#   11ᵉ→15ᵉ année ....... 30 % / an
#   16ᵉ année et plus ... 40 % / an
def calculer_indemnite_services_rendus_bois(salaire_moyen_mensuel: float,
                                            anciennete_annees: float) -> float:
    """Indemnité de services rendus — Art. 32. Requiert ≥ 2 ans d'ancienneté."""
    if salaire_moyen_mensuel <= 0 or anciennete_annees < 2:
        return 0.0
    annees = int(anciennete_annees)
    total = 0.0
    for rang in range(1, annees + 1):
        if rang <= 5:
            taux = 0.20
        elif rang <= 10:
            taux = 0.25
        elif rang <= 15:
            taux = 0.30
        else:
            taux = 0.40
        total += salaire_moyen_mensuel * taux
    return round(total, 2)


# ── 10. Permissions pour événements familiaux (Art. 41) ───────────────────────
# Limite globale de 10 jours ouvrables/an, non déductibles du congé légal.
PERMISSIONS_EVENEMENTS_BOIS = {
    "mariage_travailleur":       4,
    "mariage_enfant":            2,
    "mariage_frere_soeur":       1,
    "deces_conjoint_parent_enfant": 5,   # conjoint, père, mère, enfant
    "deces_frere_soeur":         2,
    "deces_beau_parent":         2,       # beau-père, belle-mère
    "naissance_enfant":          3,
    "ceremonie_religieuse":      1,
}
PLAFOND_PERMISSIONS_ANNUEL = 10


def jours_permission_bois(evenement: str) -> int:
    """Nombre de jours accordés pour un événement familial — Art. 41."""
    return PERMISSIONS_EVENEMENTS_BOIS.get((evenement or "").lower(), 0)
