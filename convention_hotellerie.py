"""
convention_hotellerie.py — Convention « Hôtellerie – Restauration – Débits de Boissons »
========================================================================================
Données et règles de calcul propres à cette convention collective nationale du
Gabon (signée le 24 avril 1986, révisée en 1994), à greffer sur PaieGabon.

Toutes les valeurs sont issues de la convention et ont été fournies/validées par
l'utilisateur (Ameriack I.T. Solutions).

Ce module NE modifie rien : il expose des constantes et des fonctions pures.
Le branchement dans le moteur se fait par quelques lignes dans calculs_paie.py
et blueprints/tenant.py.
"""

CODE_CONVENTION = "HOTELLERIE"
LIBELLE_CONVENTION = "Convention Collective Hôtellerie – Restauration – Débits de Boissons"


# ── 1. Grille des salaires minimums catégoriels (révision 1994) ───────────────
# code, libellé, salaire de base minimum mensuel (FCFA). horaire = mensuel / 173.33
GRILLE_HOTELLERIE = [
    # I. Personnel d'exécution
    ("EX1", "Personnel d'exécution — Catégorie 1",  83_366),
    ("EX2", "Personnel d'exécution — Catégorie 2",  87_938),
    ("EX3", "Personnel d'exécution — Catégorie 3",  92_303),
    ("EX4", "Personnel d'exécution — Catégorie 4",  93_751),
    ("EX5", "Personnel d'exécution — Catégorie 5",  94_568),
    ("EX6", "Personnel d'exécution — Catégorie 6",  98_140),
    # II. Agents de maîtrise
    ("AM1", "Agent de maîtrise — AM1",             133_100),
    ("AM2", "Agent de maîtrise — AM2",             151_250),
    ("AM3", "Agent de maîtrise — AM3",             181_500),
    # III. Cadres
    ("C1",  "Cadre — C1",                          220_320),
    ("C2",  "Cadre — C2",                          247_860),
    ("C3",  "Cadre — C3",                          385_560),
]


def grille_salaire_hotellerie_seed() -> dict:
    """Graine de grille au format attendu par l'app : {code: {"1": montant}}.
    Un seul échelon (le minimum catégoriel) ; l'utilisateur peut compléter."""
    return {code: {"1": float(montant)} for code, _lib, montant in GRILLE_HOTELLERIE}


# ── 2. Heures supplémentaires (Art. 38) ───────────────────────────────────────
# Correspondance avec les 5 « cases » du moteur PaieGabon :
#   "10"  : 1 à 8 H.S. de la semaine, de JOUR (06h-21h) ...... +15 %
#   "30"  : au-delà de 8 H.S., de JOUR ...................... +30 %
#   "30b" : repos hebdomadaire / jours fériés, de JOUR ...... +30 %
#   "40"  : heures de NUIT (21h-6h), jours normaux .......... +55 %
#   "70"  : heures de NUIT, repos hebdomadaire / fériés ..... +110 %
#
# NB : la convention prévoit +55 % pour TOUTE heure sup de nuit en jour normal
# (que ce soit dans les 8 premières ou au-delà) ; le moteur applique donc le
# même coefficient nuit quel que soit le palier, ce qui est conforme.
COEFFS_HS_HOTELLERIE = {
    "10": 1.15, "30": 1.30, "30b": 1.30, "40": 1.55, "70": 2.10,
}


# ── 3. Prime d'ancienneté (Art. 46.5) ─────────────────────────────────────────
def calculer_prime_anciennete_hotellerie(salaire_base: float,
                                         anciennete_annees: int) -> float:
    """
    Prime d'ancienneté — Art. 46.5.
    +2 % après 2 ans de service continu, puis +1 % par année supplémentaire.
    (Barème identique au BTP ; aucun plafond n'est précisé par la convention,
    on applique donc le plafond légal usuel de 30 % par prudence.)
    """
    if anciennete_annees < 2 or salaire_base <= 0:
        return 0.0
    taux = min(0.02 + 0.01 * (anciennete_annees - 2), 0.30)
    return round(salaire_base * taux, 2)


# ── 4. Prime de nuit (Art. 39.2) ──────────────────────────────────────────────
# 20 % du salaire catégoriel de base, pour le travail de nuit posté/normal
# (21h-6h). C'est une prime DISTINCTE des majorations d'heures supplémentaires
# de nuit : elle rémunère le fait de travailler de nuit, indépendamment des H.S.
TAUX_PRIME_NUIT = 0.20


def calculer_prime_nuit_hotellerie(salaire_base: float, travail_de_nuit: bool = False) -> float:
    """Prime de nuit — Art. 39.2 : 20 % du salaire catégoriel de base.
    Versée dès lors que le poste est un poste de nuit (21h-6h)."""
    if not travail_de_nuit or salaire_base <= 0:
        return 0.0
    return round(salaire_base * TAUX_PRIME_NUIT, 2)


# ── 5. Prime d'assiduité (Art. 49) ────────────────────────────────────────────
# 9 % du salaire catégoriel de base.
#   • Amputée de 50 % pour 1 jour d'absence non justifiée dans le mois.
#   • Supprimée entièrement dès 2 jours d'absence non justifiée dans le mois.
#   • 8 h de retard cumulées = 1 jour d'absence.
TAUX_PRIME_ASSIDUITE = 0.09


def calculer_prime_assiduite_hotellerie(salaire_base: float,
                                        jours_absence_injustifiee: int = 0,
                                        heures_retard_cumulees: float = 0.0) -> float:
    """Prime d'assiduité — Art. 49.
    9 % du salaire de base ; 8 h de retard = 1 jour d'absence ;
    -50 % à 1 jour, supprimée dès 2 jours."""
    if salaire_base <= 0:
        return 0.0
    jours_equiv = jours_absence_injustifiee + int(heures_retard_cumulees // 8)
    base = round(salaire_base * TAUX_PRIME_ASSIDUITE, 2)
    if jours_equiv >= 2:
        return 0.0
    if jours_equiv == 1:
        return round(base * 0.5, 2)
    return base


# ── 6. Prime de fin d'année (Art. 50) ─────────────────────────────────────────
# Salariés ayant ≥ 6 mois de présence effective.
# Part fixe = 30 % de la base de calcul (moyenne mensuelle des salaires de base
# de l'année), au prorata du temps de présence. Part variable laissée à la
# hiérarchie (non calculée automatiquement).
TAUX_PRIME_FIN_ANNEE = 0.30


def calculer_prime_fin_annee_hotellerie(moyenne_mensuelle_base: float,
                                        mois_presence: int = 12) -> float:
    """Prime de fin d'année (part fixe) — Art. 50.
    30 % de la moyenne mensuelle des salaires de base, au prorata des mois de
    présence, à condition d'avoir au moins 6 mois de présence effective."""
    if moyenne_mensuelle_base <= 0 or mois_presence < 6:
        return 0.0
    prorata = min(mois_presence, 12) / 12.0
    return round(moyenne_mensuelle_base * TAUX_PRIME_FIN_ANNEE * prorata, 2)


# ── 7. Périodes d'essai (Art. 13) — durées maximales ──────────────────────────
def periode_essai_max_hotellerie(categorie_code: str) -> int:
    """Durée maximale de la période d'essai en mois, selon la catégorie.
    Exécution (EX1-6) : 1 mois ; Maîtrise (AM) : 2 mois ; Cadres (C) : 3 mois."""
    code = (categorie_code or "").upper()
    if code.startswith("AM"):
        return 2
    if code.startswith("C"):
        return 3
    return 1  # personnel d'exécution


# ── 8. Préavis de licenciement / démission (Art. 30.3) ────────────────────────
def calculer_preavis_hotellerie(anciennete_annees: float) -> int:
    """Durée du préavis EN JOURS — Art. 30.3.
      < 1 an ............... 15 jours
      1 à 3 ans ............ 1 mois (30 j)
      3 à 5 ans ............ 2 mois (60 j)
      5 à 10 ans ........... 3 mois (90 j)
      10 à 15 ans .......... 4 mois + 1 j/an de présence
      15 à 20 ans .......... 5 mois + 1 j/an de présence
      20 à 30 ans .......... 6 mois + 2 j/an de présence
      > 30 ans ............. 12 j/an de présence
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
        return 120 + int(a)
    if a < 20:
        return 150 + int(a)
    if a < 30:
        return 180 + 2 * int(a)
    return 12 * int(a)


# ── 9. Indemnité de licenciement / services rendus (Art. 32) ──────────────────
# ≥ 12 mois d'ancienneté continue. Calculée sur la moyenne mensuelle du salaire
# global brut des 12 derniers mois, par tranches d'années :
#   1ʳᵉ→5ᵉ année ......... 20 % du salaire moyen / an
#   6ᵉ→10ᵉ année ......... 27 % / an
#   11ᵉ→20ᵉ année ........ 32 % / an
#   au-delà de 20 ans .... 37 % / an
# Cas de compression : dès 6 mois, 10 % de la moyenne du salaire brut mensuel.
def calculer_indemnite_licenciement_hotellerie(salaire_moyen_mensuel: float,
                                               anciennete_annees: float,
                                               compression: bool = False) -> float:
    """Indemnité de licenciement — Art. 32.
    Barème par tranches ; requiert ≥ 12 mois d'ancienneté (sauf compression)."""
    if salaire_moyen_mensuel <= 0:
        return 0.0
    if compression:
        # Dès 6 mois d'ancienneté
        if anciennete_annees < 0.5:
            return 0.0
        return round(salaire_moyen_mensuel * 0.10, 2)
    if anciennete_annees < 1:
        return 0.0
    annees = int(anciennete_annees)
    total = 0.0
    for annee_rang in range(1, annees + 1):
        if annee_rang <= 5:
            taux = 0.20
        elif annee_rang <= 10:
            taux = 0.27
        elif annee_rang <= 20:
            taux = 0.32
        else:
            taux = 0.37
        total += salaire_moyen_mensuel * taux
    return round(total, 2)


# ── 10. Permissions pour événements familiaux (Art. 41.1) ─────────────────────
# Limite globale de 10 jours ouvrables rémunérés/an, non déductibles du congé légal.
PERMISSIONS_EVENEMENTS_HOTELLERIE = {
    "mariage_travailleur":       4,
    "mariage_enfant":            2,
    "mariage_frere_soeur":       1,
    "deces_conjoint_parent_enfant": 5,   # conjoint, père, mère, enfant
    "deces_frere_soeur_beau":    2,       # frère, sœur, beau-père, belle-mère
    "naissance_enfant":          3,
    "ceremonie_religieuse":      1,
}
PLAFOND_PERMISSIONS_ANNUEL = 10  # jours ouvrables rémunérés / an


def jours_permission_hotellerie(evenement: str) -> int:
    """Nombre de jours accordés pour un événement familial — Art. 41.1."""
    return PERMISSIONS_EVENEMENTS_HOTELLERIE.get((evenement or "").lower(), 0)


# ── 11. Indemnités de déplacement (Art. 48.2) ─────────────────────────────────
# Exprimées en multiples du taux horaire minimum de la catégorie, ou en % du
# salaire minimum de base pour l'étranger.
def indemnite_deplacement_hotellerie(taux_horaire_categorie: float, cas: str) -> float:
    """Indemnité de déplacement — Art. 48.2.
      "1_repas"                 : 1,5 × taux horaire
      "2_repas"                 : 4 × taux horaire
      "2_repas_pdj_decouche"    : 6 × taux horaire
    (Le cas « hors Gabon » = 15 % du salaire min de base/jour se calcule à part.)
    """
    th = max(taux_horaire_categorie or 0.0, 0.0)
    cas = (cas or "").lower()
    if cas == "1_repas":
        return round(th * 1.5, 2)
    if cas == "2_repas":
        return round(th * 4.0, 2)
    if cas == "2_repas_pdj_decouche":
        return round(th * 6.0, 2)
    return 0.0
