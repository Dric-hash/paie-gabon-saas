"""
convention_minier.py — Convention « Entreprises Minières et Assimilées »
=========================================================================
Convention Collective des Entreprises Minières et Assimilées du Gabon
(signée à MOANDA le 20 février 1984 ; barème salarial au 1er mars 1994). Couvre
l'exploitation minière, les carrières, gravières, sablières et les organismes de
recherche/prospection. Hydrocarbures EXCLUS (Art. 1.1).

Valeurs issues du TEXTE INTÉGRAL fourni par l'utilisateur (Ameriack I.T.
Solutions). Module de constantes + fonctions pures ; branchement dans
calculs_paie.py / tenant.py. Base horaire : 40 h/sem → 173,33 h/mois.
"""

CODE_CONVENTION = "MINIER"
LIBELLE_CONVENTION = "Convention Collective des Entreprises Minières et Assimilées"

HEURES_MENSUELLES = 173.33


# ── 1. Grille salariale — Barème au 1er mars 1994 (Art. 47) ───────────────────
GRILLE_MINIER = [
    ("MO",   "Manœuvre ordinaire / Employé ordinaire", 104_975),
    ("MS",   "Manœuvre spécialisé / Employé aide",     105_841),
    ("OS1",  "Ouvrier spécialisé 1",                   106_060),
    ("OS2",  "Ouvrier spécialisé 2",                   106_966),
    ("OS3",  "Ouvrier spécialisé 3",                   108_564),
    ("OS4",  "Ouvrier spécialisé 4",                   110_139),
    ("OQ1",  "Ouvrier qualifié 1",                     112_080),
    ("OQ2",  "Ouvrier qualifié 2",                     113_107),
    ("OQ3",  "Ouvrier qualifié 3",                     119_177),
    ("OQ4",  "Ouvrier qualifié 4",                     125_248),
    ("OHQ1", "Ouvrier hautement qualifié 1",           129_219),
    ("OHQ2", "Ouvrier hautement qualifié 2",           142_876),
    ("OHQ3", "Ouvrier hautement qualifié 3",           157_584),
    ("OHQ4", "Ouvrier hautement qualifié 4",           172_291),
    ("M6",   "Maîtrise — Cat. 6",                      174_693),
    ("M7A",  "Maîtrise — 7A",                          196_317),
    ("M7B",  "Maîtrise — 7B",                          215_748),
    ("M7C",  "Maîtrise — 7C",                          235_180),
    ("T8A",  "Technicien — 8A",                        257_435),
    ("T8B",  "Technicien — 8B",                        276_667),
    ("T8C",  "Technicien — 8C",                        296_094),
    ("T9A",  "Technicien — 9A",                        338_336),
    ("T9B",  "Technicien — 9B",                        356_944),
    ("C10A", "Cadre — 10A",                            376_461),
    ("C10B", "Cadre — 10B",                            401_922),
    ("C10C", "Cadre — 10C",                            436_019),
    ("C11A", "Cadre — 11A",                            457_566),
    ("C11B", "Cadre — 11B",                            482_268),
    ("C11C", "Cadre — 11C",                            507_142),
    ("D12A", "Directeur — 12A",                        553_850),
    ("D12B", "Directeur — 12B",                        580_258),
    ("D12C", "Directeur — 12C",                        611_890),
]

SALAIRE_1ERE_CATEGORIE = 104_975


def grille_salaire_minier_seed() -> dict:
    """Graine de grille : {code: {"1": montant}}."""
    return {code: {"1": float(montant)} for code, _lib, montant in GRILLE_MINIER}


# ── 2. Majorations d'heures supplémentaires (Art. 40) ─────────────────────────
# a) jour ouvrable : 8 premières +22 % (10) · au-delà +50 % (30) · nuit +68 % (40)
# b) repos hebdo : jour +51 % (30b) · nuit +102 % (70)
# c) férié chômé payé : jour +100 % · nuit +150 %  (voir COEFF_FERIE_* ci-dessous)
#    Le moteur n'ayant qu'une case repos/férié jour (30b) et nuit (70), on y place
#    les taux du repos hebdomadaire (le plus fréquent en activité continue).
COEFFS_HS_MINIER = {"10": 1.22, "30": 1.50, "30b": 1.51, "40": 1.68, "70": 2.02}
COEFF_FERIE_JOUR_MINIER = 2.00   # +100 %
COEFF_FERIE_NUIT_MINIER = 2.50   # +150 %


# ── 3. Prime d'ancienneté (Art. 48.6) ─────────────────────────────────────────
TAUX_ANCIENNETE_MINIER = {
    2: 3, 3: 4, 4: 5, 5: 7, 6: 8, 7: 9, 8: 10, 9: 11, 10: 12, 11: 13,
    12: 14, 13: 15, 14: 17, 15: 18, 16: 19, 17: 20, 18: 21, 19: 22,
    20: 25, 21: 25, 22: 25,
}


def calculer_prime_anciennete_minier(salaire_base: float, anciennete_annees: int) -> float:
    """Prime d'ancienneté — Art. 48.6. Dès 2 ans ; +1 %/an au-delà de 22 ans."""
    a = int(anciennete_annees or 0)
    if a < 2 or salaire_base <= 0:
        return 0.0
    taux = TAUX_ANCIENNETE_MINIER[a] if a <= 22 else 25 + (a - 22)
    return round(salaire_base * taux / 100.0, 2)


# ── 4. Prime d'assiduité (Art. 51) ────────────────────────────────────────────
def calculer_prime_assiduite_minier(salaire_base: float, nb_absences: int = 0) -> float:
    """4 % du salaire de base, - 25 % par absence non autorisée (Art. 51.4)."""
    if salaire_base <= 0:
        return 0.0
    base = 0.04 * salaire_base
    facteur = max(0.0, 1.0 - 0.25 * max(0, nb_absences))
    return round(base * facteur, 2)


# ── 5. Prime de panier (Art. 49) ──────────────────────────────────────────────
def calculer_prime_panier_minier(salaire_1ere_cat: float = SALAIRE_1ERE_CATEGORIE) -> float:
    """3 × le salaire horaire de la 1ère catégorie (Art. 49.3)."""
    return round(3 * (float(salaire_1ere_cat) / HEURES_MENSUELLES), 2)


# ── 6. Indemnité de déplacement (Art. 50) ─────────────────────────────────────
def calculer_indemnite_deplacement_minier(salaire_base: float, categorie_num: int,
                                          nb_repas: int = 1) -> float:
    """Par repas (Art. 50.2) : Cat.1-6 5× · 7 4× · 8 4× · 9-10 3× · 11-12 2×
    (× salaire horaire de base de la catégorie)."""
    if salaire_base <= 0:
        return 0.0
    th = salaire_base / HEURES_MENSUELLES
    c = int(categorie_num or 1)
    if c <= 6:         mult = 5
    elif c in (7, 8):  mult = 4
    elif c in (9, 10): mult = 3
    else:              mult = 2
    return round(th * mult * max(1, nb_repas), 2)


# ── 7. Treizième mois (Art. 55) ───────────────────────────────────────────────
def calculer_treizieme_mois_minier(salaire_base: float, prime_anciennete: float = 0.0,
                                   mois_presence: float = 12) -> float:
    """13ᵉ mois — Art. 55 : 1 mois de salaire de base + prime d'ancienneté,
    au prorata du temps de présence (≥ 1 an, présent au 31/12)."""
    if salaire_base <= 0:
        return 0.0
    montant = (float(salaire_base) + float(prime_anciennete or 0)) * (min(mois_presence, 12) / 12.0)
    return round(montant, 2)


# ── 8. Périodes d'essai maximales (Art. 13.2) ─────────────────────────────────
def periode_essai_max_minier(categorie_code: str) -> int:
    """Cat. I 8 j · II 15 j · III-V 30 j · VI-VII 60 j · VIII-XII 90 j."""
    c = (categorie_code or "").strip().upper().replace(" ", "")
    equivalences = {
        "I": 8, "1": 8, "II": 15, "2": 15,
        "III": 30, "3": 30, "IV": 30, "4": 30, "V": 30, "5": 30,
        "VI": 60, "6": 60, "VII": 60, "7": 60,
        "VIII": 90, "8": 90, "IX": 90, "9": 90, "X": 90, "10": 90,
        "XI": 90, "11": 90, "XII": 90, "12": 90,
    }
    return equivalences.get(c, 30)


# ── 9. Prime d'intérim d'un emploi supérieur (Art. 27) ────────────────────────
def prime_interim_minier(salaire_base_agent: float, salaire_base_poste: float) -> float:
    """75 % de la différence de salaire de base (Art. 27)."""
    diff = float(salaire_base_poste or 0) - float(salaire_base_agent or 0)
    return round(0.75 * diff, 2) if diff > 0 else 0.0


# ── 10. Préavis (Art. 30.3) ───────────────────────────────────────────────────
# Trois barèmes selon la catégorie (EN JOURS, mois comptés à 30 j) :
#   Ancienneté        Exécution   Encadr. 6-7   Cadres 8-12
#   1 mois – 1 an        15           30            30
#   1 – 3 ans            30           60            90
#   3 – 5 ans            60           60            90
#   5 – 10 ans           90           90            90
#   10 – 15 ans         120          120           120
#   15 – 20 ans         150          150           150
#   20 – 30 ans         180          180           180
#   Au-delà de 30 ans : + 15 j par année supplémentaire (les trois barèmes).
def calculer_preavis_minier(anciennete_annees: float, cadre: bool = False,
                            encadrement: bool = False) -> int:
    """Préavis EN JOURS — Art. 30.3.
      - encadrement=True → barème « Encadrement Cat. 6-7 »
      - cadre=True       → barème « Cadres / Maîtrise Cat. 8-12 »
      - sinon            → barème « Personnel d'Exécution »
    """
    a = anciennete_annees
    if cadre:          # Cadres / Maîtrise (Cat. 8-12)
        if a < 1:      base = 30
        elif a < 3:    base = 90
        elif a < 5:    base = 90
        elif a < 10:   base = 90
        elif a < 15:   base = 120
        elif a < 20:   base = 150
        else:          base = 180
    elif encadrement:  # Encadrement (Cat. 6-7)
        if a < 1:      base = 30
        elif a < 3:    base = 60
        elif a < 5:    base = 60
        elif a < 10:   base = 90
        elif a < 15:   base = 120
        elif a < 20:   base = 150
        else:          base = 180
    else:              # Personnel d'exécution
        if a < 1:      base = 15
        elif a < 3:    base = 30
        elif a < 5:    base = 60
        elif a < 10:   base = 90
        elif a < 15:   base = 120
        elif a < 20:   base = 150
        else:          base = 180
    if a > 30:
        base += 15 * (int(a) - 30)
    return base


# ── 11. Indemnité de services rendus (Art. 32) ────────────────────────────────
def calculer_indemnite_services_rendus_minier(salaire_moyen_mensuel: float,
                                              anciennete_annees: float) -> float:
    """Art. 32 : 1→5 20 % · 6→10 25 % · 11→15 30 % · 16→20 35 % · >20 37 %. ≥ 2 ans."""
    if salaire_moyen_mensuel <= 0 or anciennete_annees < 2:
        return 0.0
    annees = int(anciennete_annees)
    total = 0.0
    for rang in range(1, annees + 1):
        if rang <= 5:      taux = 0.20
        elif rang <= 10:   taux = 0.25
        elif rang <= 15:   taux = 0.30
        elif rang <= 20:   taux = 0.35
        else:              taux = 0.37
        total += salaire_moyen_mensuel * taux
    return round(total, 2)


# ── 12. Permissions pour événements familiaux (Art. 43) ───────────────────────
PERMISSIONS_EVENEMENTS_MINIER = {
    "mariage_travailleur": 4, "mariage_enfant": 2, "mariage_frere_soeur": 1,
    "deces_conjoint_parent_enfant": 5, "deces_frere_soeur": 2, "deces_beau_parent": 2,
    "naissance_enfant": 3, "ceremonie_religieuse": 1, "demenagement": 1,
}
PLAFOND_PERMISSIONS_ANNUEL_MINIER = 13
CREDIT_HEURES_DELEGUE_MINIER = 15


# ── 13. Barème de préavis selon la catégorie du salarié ───────────────────────
def tier_preavis_minier(categorie_code: str):
    """Détermine le barème de préavis à partir du code catégorie du salarié.
    Renvoie (cadre: bool, encadrement: bool) :
      - Cadres/Maîtrise (Cat. 8-12) → (True, False)
      - Encadrement (Cat. 6-7)      → (False, True)
      - Personnel d'exécution       → (False, False)
    Gère les codes de la grille minière (MO, OS1, M6, T8A, C10A, D12C…) et les
    codes numériques (1-12)."""
    import re
    c = (categorie_code or "").strip().upper()
    if c.startswith(("MO", "MS", "OS", "OQ", "OHQ")):
        return (False, False)          # exécution explicite
    m = re.search(r"(\d+)", c)
    num = int(m.group(1)) if m else 0
    if num >= 8:
        return (True, False)           # cadres 8-12
    if num in (6, 7):
        return (False, True)           # encadrement 6-7
    return (False, False)              # exécution
