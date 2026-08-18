"""
convention_foret.py — Convention « Exploitations Forestières »
==============================================================
Convention Collective du Travail du Secteur des Exploitations Forestières de la
République Gabonaise (signée à Libreville le 10 décembre 1985 ; annexe des
classifications professionnelles du 18 février 1986). Elle annule et remplace la
convention du 9 juillet 1959. Couvre les entreprises dont l'activité ressortit
au secteur des exploitations forestières (abattage, débardage, transport de
grumes, scieries de chantier, etc.). Les « coupeurs libres » liés par un contrat
de prestation de service sont EXCLUS (Art. 1.2).

À NE PAS CONFONDRE avec la convention « Industries du Bois, Sciages et Placages »
(convention_bois.py), qui est distincte.

Valeurs issues du TEXTE INTÉGRAL fourni par l'utilisateur (Ameriack I.T.
Solutions). Module de constantes + fonctions pures ; branchement dans
calculs_paie.py / blueprints/tenant.py.

Base horaire mensuelle : 200 h/mois (le barème conventionnel donne
Sal/mois = Sal/horaire × 200 ; cf. grille du 01/03/1994).
"""

CODE_CONVENTION = "FORET"
LIBELLE_CONVENTION = "Convention Collective des Exploitations Forestières"

# Le barème conventionnel (Sal/mois = Sal/h × 200) fixe la base mensuelle à 200 h.
HEURES_MENSUELLES = 200.0


# ── 1. Grille salariale — Barème au 1er mars 1994 ─────────────────────────────
# « GRILLE CONVENTIONNELLE DES SALAIRES DE BASE — Applicable au 01.03.94 à
#   l'exploitation forestière ». Montants MENSUELS (= salaire horaire × 200).
#   (Le barème antérieur du 20/02/1986 — base + I.S.N. — est conservé en
#    commentaire ci-dessous pour mémoire.)
GRILLE_FORET = [
    ("1",   "1ère catégorie — Main d'œuvre ordinaire",               85_383),
    ("2",   "2ème catégorie — Main d'œuvre non spécialisée",         86_759),
    ("3",   "3ème catégorie — Main d'œuvre spécialisée",             88_366),
    ("4",   "4ème catégorie — Main d'œuvre qualifiée",               89_516),
    ("5",   "5ème catégorie — Main d'œuvre très qualifiée",          93_464),
    ("6",   "6ème catégorie — Main d'œuvre hautement qualifiée",     98_291),
    ("7",   "7ème catégorie — Chefs d'équipe très hautement qual.", 107_865),
    ("AM1", "Agent de maîtrise 1",                                  114_048),
    ("AM2", "Agent de maîtrise 2",                                  148_400),
    ("C1",  "Cadre 1",                                             220_500),
    ("C2",  "Cadre 2",                                             275_625),
    ("C3",  "Cadre 3",                                             385_875),
    ("C4",  "Cadre 4 — Directeur général",                        496_125),
]

# Barème du 20/02/1986 (base conventionnelle + I.S.N. = salaire total), pour
# mémoire : 1ère 64 651 · 2ème 65 106 · 3ème 66 569 · 4ème 67 305 · 5ème 68 923 ·
# 6ème 70 718 · 7ème 86 848 · AM1 93 559 · AM2 125 000 · C1 200 000 · C2 250 000 ·
# C3 350 000 · C4 450 000.

SALAIRE_1ERE_CATEGORIE = 85_383


def grille_salaire_foret_seed() -> dict:
    """Graine de grille : {code: {"1": montant}} (barème 1994)."""
    return {code: {"1": float(montant)} for code, _lib, montant in GRILLE_FORET}


# ── 2. Majorations d'heures supplémentaires (Art. 38.2 / 39) ──────────────────
# a) jours ouvrables : 6 premières h de jour +10 % (10) · au-delà +30 % (30) ·
#    nuit (21h-6h) +60 % (40)
# b) jours de repos hebdo & jours chômés récupérables : jour +50 % (30b) ·
#    nuit +100 % (70)
# c) jours fériés légaux : jour +100 % (fj) · nuit +150 % (fn)
COEFFS_HS_FORET = {"10": 1.10, "30": 1.30, "30b": 1.50, "40": 1.60, "70": 2.00}
COEFF_FERIE_JOUR_FORET = 2.00   # +100 %
COEFF_FERIE_NUIT_FORET = 2.50   # +150 %


# ── 3. Prime d'ancienneté (Art. 46.5) ─────────────────────────────────────────
def calculer_prime_anciennete_foret(salaire_base: float, anciennete_annees: int) -> float:
    """Prime d'ancienneté — Art. 46.5 : majoration du salaire de base
    conventionnel, attribuée après 2 ans au taux de 2 %, puis + 1 %/an."""
    a = int(anciennete_annees or 0)
    if a < 2 or salaire_base <= 0:
        return 0.0
    taux = 2 + (a - 2)          # 2 % à 2 ans, +1 %/an au-delà
    return round(salaire_base * taux / 100.0, 2)


# ── 4. Prime d'assiduité (Art. 50) ────────────────────────────────────────────
def calculer_prime_assiduite_foret(salaire_base: float, nb_absences: int = 0) -> float:
    """Art. 50.4 : 3 % du salaire mensuel de base conventionnel, avec abattement
    de 50 % pour une absence et de 100 % pour deux absences (ou plus) dans le
    mois. Réservée au personnel d'exécution (Art. 50.2)."""
    if salaire_base <= 0:
        return 0.0
    n = max(0, int(nb_absences or 0))
    if n >= 2:
        facteur = 0.0
    elif n == 1:
        facteur = 0.5
    else:
        facteur = 1.0
    return round(0.03 * salaire_base * facteur, 2)


# ── 5. Prime de panier (Art. 47) ──────────────────────────────────────────────
def calculer_prime_panier_foret(salaire_base_categorie: float) -> float:
    """Art. 47.3 : 1,5 × le salaire horaire de base conventionnel de la catégorie
    du travailleur. Due lorsqu'une prolongation exceptionnelle empêche le repas
    (sauf repas gratuit fourni, Art. 47.2)."""
    if salaire_base_categorie <= 0:
        return 0.0
    taux_horaire = float(salaire_base_categorie) / HEURES_MENSUELLES
    return round(1.5 * taux_horaire, 2)


# ── 6. Indemnité de déplacement (Art. 52) ─────────────────────────────────────
def calculer_indemnite_deplacement_foret(salaire_base_categorie: float,
                                         nb_repas: int = 1,
                                         avec_nuit: bool = False) -> float:
    """Art. 52.2 (× salaire horaire de base conventionnel de la catégorie) :
      - 1 repas .............. 4 ×
      - 2 repas .............. 8 ×
      - 2 repas + 1 nuit .... 12 ×
    """
    if salaire_base_categorie <= 0:
        return 0.0
    th = float(salaire_base_categorie) / HEURES_MENSUELLES
    if avec_nuit:
        mult = 12
    elif int(nb_repas or 0) >= 2:
        mult = 8
    else:
        mult = 4
    return round(th * mult, 2)


# ── 7. Indemnité de caisse (Art. 54) ──────────────────────────────────────────
def calculer_indemnite_caisse_foret(salaire_base_categorie: float) -> float:
    """Art. 54 : l'employé assumant la charge d'une caisse d'espèces perçoit une
    indemnité de caisse d'AU MOINS 10 % du salaire de base mensuel conventionnel
    de sa catégorie (plancher ; l'employeur peut accorder davantage)."""
    if salaire_base_categorie <= 0:
        return 0.0
    return round(0.10 * float(salaire_base_categorie), 2)


# ── 8. Gratification de fin d'année (Art. 51) ─────────────────────────────────
def gratification_eligible_foret(anciennete_annees: float) -> bool:
    """Art. 51 : une gratification PEUT être accordée au travailleur ayant deux
    ans d'ancienneté (période d'essai comprise). Éligibilité seule ; le MONTANT
    est laissé à l'appréciation de l'employeur (aucun taux conventionnel)."""
    return float(anciennete_annees or 0) >= 2


def calculer_gratification_fin_annee_foret(anciennete_annees: float) -> float:
    """Montant de la gratification (Art. 51). La convention ne fixe AUCUN taux :
    le montant relève de l'appréciation de l'employeur. Renvoie donc 0.0 (à
    saisir manuellement) ; l'éligibilité s'obtient via gratification_eligible_foret."""
    return 0.0


# ── 9. Préavis (Art. 30.3) ────────────────────────────────────────────────────
# Barème unique (pas de distinction cadre/exécution) EN JOURS :
#   1 mois – 1 an ....... 15 j        10 – 15 ans ......... 120 j
#   1 – 3 ans ........... 30 j        15 – 20 ans ......... 160 j
#   3 – 5 ans ........... 60 j        20 – 25 ans ......... 180 j
#   5 – 10 ans .......... 90 j        au-delà de 25 ans : + 10 j / année
def calculer_preavis_foret(anciennete_annees: float) -> int:
    """Préavis EN JOURS — Art. 30.3 (barème unique)."""
    a = anciennete_annees
    if a < 1:
        base = 15
    elif a < 3:
        base = 30
    elif a < 5:
        base = 60
    elif a < 10:
        base = 90
    elif a < 15:
        base = 120
    elif a < 20:
        base = 160
    elif a <= 25:
        base = 180
    else:
        base = 180 + 10 * (int(a) - 25)
    return base


# ── 10. Indemnité de services rendus (Art. 32) ────────────────────────────────
def calculer_indemnite_services_rendus_foret(salaire_moyen_mensuel: float,
                                             anciennete_annees: float) -> float:
    """Art. 32 : hors faute lourde, due au travailleur licencié, partant à la
    retraite ou décédé, dès 2 ans d'ancienneté. Pourcentages (cumulés par année)
    de la moyenne mensuelle du salaire global des 12 derniers mois :
      1→5 ans 20 % · 5e→10e 22 % · 10e→15e 25 % · 15e→20e 28 % · ≥ 20e 32 %.
    Les fractions d'année ≥ 30 jours calendaires sont prises en compte."""
    if salaire_moyen_mensuel <= 0 or anciennete_annees < 2:
        return 0.0
    annees = int(anciennete_annees)
    total = 0.0
    for rang in range(1, annees + 1):
        if rang <= 5:      taux = 0.20
        elif rang <= 10:   taux = 0.22
        elif rang <= 15:   taux = 0.25
        elif rang <= 20:   taux = 0.28
        else:              taux = 0.32
        total += salaire_moyen_mensuel * taux
    return round(total, 2)


# ── 11. Permissions pour événements familiaux (Art. 41) ───────────────────────
PERMISSIONS_EVENEMENTS_FORET = {
    "mariage_travailleur": 4, "mariage_enfant": 2, "mariage_frere_soeur": 1,
    "deces_conjoint_parent_enfant": 5, "deces_frere_soeur": 2, "deces_beau_parent": 2,
    "naissance_enfant": 3, "ceremonie_religieuse": 1,
}
# Art. 41.2 : ces permissions ne peuvent être déduites du congé acquis que dans
# la limite de 10 jours par année civile.
PLAFOND_PERMISSIONS_ANNUEL_FORET = 10


# ── 12. Période d'essai (Art. 13) ─────────────────────────────────────────────
def periode_essai_max_foret(categorie_code: str = "") -> int:
    """Art. 13 : la durée maximale de la période d'essai est fixée par l'annexe
    des classifications ; en AUCUN cas elle ne peut excéder 6 mois (renouvellement
    compris). L'annexe transmise ne détaille pas de durée par catégorie : on
    applique donc, à défaut, un barème usuel croissant, PLAFONNÉ à 6 mois — à
    ajuster si un barème d'essai conventionnel chiffré est communiqué.
    Renvoie une durée EN JOURS (mois comptés à 30 j)."""
    c = (categorie_code or "").strip().upper()
    if c in ("1", "2"):
        return 30                     # main d'œuvre ordinaire / non spécialisée
    if c in ("3", "4", "5", "6", "7"):
        return 90                     # ouvriers / employés qualifiés
    if c in ("AM1", "AM2"):
        return 120                    # agents de maîtrise
    if c in ("C1", "C2", "C3", "C4"):
        return 180                    # cadres — plafond conventionnel (6 mois)
    return 90
