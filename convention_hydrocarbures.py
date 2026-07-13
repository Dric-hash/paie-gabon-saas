"""
convention_hydrocarbures.py — Convention « Recherche et Exploitation des Hydrocarbures »
========================================================================================
Données et règles de calcul propres à cette convention, à greffer sur PaieGabon.
Toutes les valeurs sont issues de la Convention collective des Sociétés de
Recherche et Exploitation des Hydrocarbures (République Gabonaise), vérifiées
par l'utilisateur.

Ce module NE modifie rien : il expose des constantes et des fonctions pures.
Le branchement dans le moteur se fait par quelques lignes dans calculs_paie.py
et blueprints/tenant.py (voir INTEGRATION_HYDROCARBURES.md).
"""

# ── Grille des salaires minimums catégoriels (Annexe, juin 1983) ──────────────
# Montants confirmés par l'utilisateur. horaire = mensuel / 173.33.
GRILLE_HYDROCARBURES = [
    # code, libellé,                       mensuel,   horaire
    ("A", "Catégorie A — Groupe I",        106_971,    617.15),
    ("B", "Catégorie B — Groupe I",        113_000,    651.94),
    ("C", "Catégorie C — Groupe I",        121_016,    698.18),
    ("D", "Catégorie D — Groupe II",       131_391,    758.04),
    ("E", "Catégorie E — Groupe II",       144_623,    834.38),
    ("F", "Catégorie F — Groupe III",      161_385,    931.09),
    ("G", "Catégorie G — Groupe III",      182_576,   1053.34),
    ("H", "Catégorie H — Groupe IV",       209_400,   1208.10),
    ("I", "Catégorie I — Groupe IV",       243_479,   1404.71),
    ("J", "Catégorie J — Groupe V",        287_013,   1655.88),
    ("K", "Catégorie K — Groupe V",        343_000,   1978.88),
    ("L", "Catégorie L — Groupe V",        415_566,   2397.54),
    ("M", "Catégorie M — Groupe V",        510_433,   2944.86),
]

# ── Heures supplémentaires (Art. 38.2) ────────────────────────────────────────
# Correspondance avec les 5 « cases » du moteur PaieGabon :
#   "10"  : 8 premières H.S. de la semaine, de jour        → +15 %
#   "30"  : H.S. au-delà des 8 premières, de jour          → +30 %
#   "30b" : repos hebdo / dimanche / férié, de JOUR        → +35 %
#   "40"  : heures de NUIT en semaine                      → +60 %
#   "70"  : heures de NUIT les dimanches / fériés          → +120 %
COEFFS_HS_HYDROCARBURES = {
    "10": 1.15, "30": 1.30, "30b": 1.35, "40": 1.60, "70": 2.20,
}

# ── Prime d'ancienneté (Art. 46) ──────────────────────────────────────────────
def calculer_prime_anciennete_hydrocarbures(salaire_base: float,
                                            anciennete_annees: int) -> float:
    """
    Prime d'ancienneté — Convention Hydrocarbures, Art. 46.
    +2 % après 2 ans de présence, puis +1 % par année supplémentaire.
    SANS plafond conventionnel (contrairement au BTP plafonné à 30 %).
    """
    if anciennete_annees < 2 or salaire_base <= 0:
        return 0.0
    taux = 0.02 + 0.01 * (anciennete_annees - 2)   # pas de plafond
    return round(salaire_base * taux, 2)


# ── Congés : jours supplémentaires d'ancienneté (Art. 42) ─────────────────────
def bonus_conge_anciennete_hydrocarbures(anciennete_annees: int) -> int:
    """
    Jours ouvrables de congé SUPPLÉMENTAIRES selon l'ancienneté — Art. 42.
    Barème par paliers (et non +1/an) :
        +2 j après 5 ans, +4 j après 10 ans, +6 j après 15 ans, +8 j après 20 ans.
    (S'ajoutent aux 2,5 j/mois de base ; le max est +8.)
    """
    if anciennete_annees >= 20:
        return 8
    if anciennete_annees >= 15:
        return 6
    if anciennete_annees >= 10:
        return 4
    if anciennete_annees >= 5:
        return 2
    return 0


# ── Préavis (Art. 30.5 / 30.6) ────────────────────────────────────────────────
# Le barème de la convention est IDENTIQUE au barème légal déjà codé dans
# calculs_paie.calculer_preavis_code (15 j → 6 mois, +10 j/an au-delà de 30 ans).
# Aucune fonction dédiée n'est donc nécessaire : le dispatcher renverra vers le
# barème légal (voir INTEGRATION_HYDROCARBURES.md).

# ── Métadonnées d'affichage ───────────────────────────────────────────────────
CODE_CONVENTION    = "HYDROCARBURES"
LIBELLE_CONVENTION = "Recherche et Exploitation des Hydrocarbures"
