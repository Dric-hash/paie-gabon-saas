"""
calculs_paie.py — Moteur de calcul de la paie selon la réglementation gabonaise
Références : CGI Gabon, Décret 578/PR/MDSFPSSN, Arrêté 037/METPS

Régime BTP (48h/semaine) :
  - Heures normales mensuelles : 173,33 h  (40h × 43,33 semaines)
  - Heures sup +10% structurelles : 17,33 h/mois (4h × 4,333 sem.)
  - Heures sup +30% structurelles : 17,33 h/mois (4h × 4,333 sem.)
"""

# ─── CONSTANTES RÉGLEMENTAIRES (Gabon 2026) ───────────────────────────────────
CNSS_TAUX_SALARIE    = 0.05
CNSS_TAUX_PATRONAL   = 0.18
CNSS_PLAFOND         = 1_500_000   # FCFA/mois

CNAMGS_TAUX_SALARIE  = 0.02
CNAMGS_TAUX_PATRONAL = 0.041
CNAMGS_PLAFOND       = 2_500_000

FNH_TAUX             = 0.03
FNH_PLAFOND          = 1_500_000

CFP_TAUX             = 0.005

TCS_TAUX             = 0.05
TCS_EXONERATION      = 150_000
LOGEMENT_PLAFOND_PCT = 0.40
LOGEMENT_PLAFOND_MAX = 250_000
TRANSPORT_EXONERATION_IRPP = 100_000
TRANSPORT_EXONERATION_CNSS = 35_000

# Salaire minimum interprofessionnel garanti (SMIG) — plancher légal mensuel.
# ⚠️ À CONFIRMER / RENDRE CONFIGURABLE (chantier conformité SMIG/RMM en cours) :
# valeur de référence retenue par défaut, utilisée notamment comme plancher lors
# de l'import des grilles conventionnelles anciennes (ex. grille Pétrole de 1983).
SMIG_GABON           = 150_000

# ─── CONSTANTES BTP ───────────────────────────────────────────────────────────
H_NORMALES_MENSUEL   = 173.33   # heures normales / mois (40h × 43,33)
H_SUP_STRUCT_10      = 17.33    # heures +10% structurelles / mois (4h × 4,333)
H_SUP_STRUCT_30      = 17.33    # heures +30% structurelles / mois (4h × 4,333)
COEFF_SUP_10         = 1.10
COEFF_SUP_30         = 1.30
COEFF_SUP_40         = 1.40     # nuit / dimanche
COEFF_SUP_70         = 1.70     # jours fériés
COEFF_SUP_30B        = 1.30     # repos/dimanche/férié — heures de JOUR (Pétrole)
COEFF_SUP_FJ         = 2.00     # jour férié chômé payé — heures de JOUR (+100 %)
COEFF_SUP_FN         = 2.50     # jour férié chômé payé — heures de NUIT (+150 %)

# ─── COEFFICIENTS DES HEURES SUPPLÉMENTAIRES PAR CONVENTION ────────────────────
# Le moteur de paie raisonne sur 5 « cases » : 10 / 30 / 30b / 40 / 70.
# Chaque case reçoit un multiplicateur qui dépend de la convention applicable.
# Par défaut (BTP, Commerce, Code du travail seul), la grille historique
# 10/30/40/70 est conservée à l'identique — la case 30b reste à 0 et son
# coefficient est sans effet. Seule la convention Pétrole alimente la 5ᵉ case.
#
# Convention Pétrole (SGEPP/GPP, 17 juin 1983) — Art. 38.2 :
#   • 41ᵉ → 48ᵉ heure hebdo, jour ouvrable ........ +20 %  → case 10
#   • au-delà de la 48ᵉ heure hebdo, jour ouvrable  +35 %  → case 30
#   • repos hebdo / dimanche / férié, de JOUR ...... +30 %  → case 30b
#   • nuit (21h-6h), jour ouvrable ................. +50 %  → case 40
#   • nuit (21h-6h), dimanche / férié ............. +100 %  → case 70
COEFFS_HEURES_SUP_DEFAUT = {
    "10":  COEFF_SUP_10,  "30":  COEFF_SUP_30,  "30b": COEFF_SUP_30B,
    "40":  COEFF_SUP_40,  "70":  COEFF_SUP_70,
    "fj":  COEFF_SUP_FJ,  "fn":  COEFF_SUP_FN,
}
COEFFS_HEURES_SUP_CONVENTION = {
    "PETROLE": {"10": 1.20, "30": 1.35, "30b": 1.30, "40": 1.50, "70": 2.00},
    "HYDROCARBURES": {"10": 1.15, "30": 1.30, "30b": 1.35, "40": 1.60, "70": 2.20},
    # Entreprises Industrielles — Art. A.38 :
    #   40→48 h ouvrable .......... +16 %  → case 10
    #   au-delà de 48 h ouvrable .. +35 %  → case 30
    #   heures fériés de JOUR ..... +50 %  → case 30b
    #   heures de nuit (ouvrable) . +80 %  → case 40
    #   heures de nuit fériés ..... +135 % → case 70
    "INDUSTRIE": {"10": 1.16, "30": 1.35, "30b": 1.50, "40": 1.80, "70": 2.35},
    # Transports Aériens — Art. A.39 (grille à 7 taux ramenée aux 5 cases) :
    #   8 h au-delà de 40 h (jour) . +15 %  → case 10
    #   au-delà de 48 h (jour) ..... +30 %  → case 30
    #   repos hebdo / fériés (jour)  +50 %  → case 30b
    #   nuit ouvrable (21h-6h) ..... +60 %  → case 40
    #   nuit repos / fériés ........ +100 % → case 70
    # (Les taux « jour chômé récupérable » +40 % jour / +80 % nuit ne sont pas
    #  captés faute de bucket dédié ; ils sont rares en pratique.)
    "AERIEN": {"10": 1.15, "30": 1.30, "30b": 1.50, "40": 1.60, "70": 2.00},
    # Hôtellerie – Restauration – Débits de Boissons — Art. 38 :
    #   1 à 8 H.S. de jour (06h-21h) ...... +15 %  → case 10
    #   au-delà de 8 H.S. de jour ......... +30 %  → case 30
    #   repos hebdo / fériés de JOUR ...... +30 %  → case 30b
    #   heures de NUIT (21h-6h), normal ... +55 %  → case 40
    #   heures de NUIT, repos / fériés .... +110 % → case 70
    "HOTELLERIE": {"10": 1.15, "30": 1.30, "30b": 1.30, "40": 1.55, "70": 2.10},
    # Industries du Bois — Art. 38 (base 40h, H.S. dès la 41ᵉ heure) :
    #   41ᵉ→48ᵉ h de jour ...... +13 % → 10 ;  49ᵉ+ de jour ..... +35 % → 30
    #   repos/fériés de JOUR ... +50 % → 30b ; NUIT normal ...... +75 % → 40
    #   NUIT repos/fériés ...... +125 % → 70
    "BOIS": {"10": 1.13, "30": 1.35, "30b": 1.50, "40": 1.75, "70": 2.25},
    # Entreprises Minières — Art. 40 (base 40h) : 8 premières HS +22 % (10),
    #   au-delà +50 % (30), nuit +68 % (40) ; repos hebdo jour +51 % (30b),
    #   nuit +102 % (70). [Fériés chômés payés +100 %/+150 % non distingués.]
    "MINIER": {"10": 1.22, "30": 1.50, "30b": 1.51, "40": 1.68, "70": 2.02,
               "fj": 2.00, "fn": 2.50},
}


def coeffs_heures_sup(convention=None) -> dict:
    """Coefficients de majoration des 5 cases d'heures sup selon la convention.

    Renvoie un dict {"10","30","30b","40","70"} de multiplicateurs. Toute
    convention non répertoriée (BTP, Commerce, AUCUNE) retombe sur la grille
    historique, garantissant l'absence de régression.
    """
    c = (convention or "").upper()
    # Fusion non-régressive : la convention surcharge les défauts (qui incluent
    # les cases fériés fj/fn). Les conventions ne définissant pas fj/fn héritent
    # des valeurs par défaut (jour +100 %, nuit +150 %).
    base = dict(COEFFS_HEURES_SUP_DEFAUT)
    base.update(COEFFS_HEURES_SUP_CONVENTION.get(c, {}))
    return base

# Barème IRPP mensuel Gabon
BAREME_IRPP = [
    (0,        125_000,   0.00),
    (125_001,  160_000,   0.05),
    (160_001,  225_000,   0.10),
    (225_001,  300_000,   0.15),
    (300_001,  430_000,   0.20),
    (430_001,  625_000,   0.25),
    (625_001,  916_667,   0.30),
    (916_668,  float("inf"), 0.35),
]


from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING


def fcfa(valeur, decimales: int = 2) -> float:
    """
    Arrondi monétaire déterministe — arrondi commercial (round-half-up),
    contrairement au round() natif de Python qui fait du round-half-to-even
    (arrondi bancaire) et donne, par ex., round(2.5) == 2.

    La conversion via str() neutralise l'imprécision binaire du float
    (ex: 0.1 + 0.2 == 0.30000000000000004). Retourne un float compatible
    avec les colonnes Numeric(15,2) de la base.

    >>> fcfa(2.5, 0)     # 3.0   (et non 2 comme round())
    >>> fcfa(1234.565)   # 1234.57
    >>> fcfa(None)       # 0.0
    """
    if valeur is None:
        return 0.0
    try:
        quant = Decimal(1).scaleb(-decimales)            # ex: Decimal("0.01")
        d = Decimal(str(valeur)).quantize(quant, rounding=ROUND_HALF_UP)
        return float(d)
    except Exception:
        return 0.0


def arrondi_pas(valeur, pas: int = 5) -> float:
    """Arrondit `valeur` au multiple de `pas` le plus proche (round-half-up).

    Utile pour les paies réglées en espèces : la plus petite coupure du franc
    CFA est de 5 F, donc un montant comme 1 099 999 n'est pas payable tel quel.
    Avec pas=5 il devient 1 100 000 ; le « …99999 » disparaît.

    >>> arrondi_pas(1099999)      # 1100000.0
    >>> arrondi_pas(149999, 5)    # 150000.0
    >>> arrondi_pas(0)            # 0.0
    """
    try:
        v = Decimal(str(valeur or 0))
        p = int(pas)
        if p <= 0:
            return float(v)
        n = (v / p).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return float(n * p)
    except Exception:
        return float(valeur or 0)


def arrondi_millier_superieur(valeur) -> float:
    """Arrondit au **millier de francs supérieur** (plafond à 1 000).

    Utilisé pour la paie des journaliers mensuels : tout montant est porté au
    millier au-dessus. 1 099 001 → 1 100 000 ; 150 000 (déjà rond) reste 150 000.

    >>> arrondi_millier_superieur(1099001)   # 1100000.0
    >>> arrondi_millier_superieur(149100)    # 150000.0
    >>> arrondi_millier_superieur(150000)    # 150000.0
    >>> arrondi_millier_superieur(0)         # 0.0
    """
    try:
        v = Decimal(str(valeur or 0))
        if v <= 0:
            return 0.0
        n = (v / 1000).to_integral_value(rounding=ROUND_CEILING)
        return float(n * 1000)
    except Exception:
        return float(valeur or 0)


def calculer_taux_horaire(salaire_base: float) -> float:
    """Taux horaire de base = salaire_base / 173,33."""
    if salaire_base <= 0:
        return 0.0
    return round(salaire_base / H_NORMALES_MENSUEL, 4)


def calculer_heures_sup_btp(salaire_base: float,
                              h10: float = None, h30: float = None,
                              h40: float = 0.0,  h70: float = 0.0,
                              h30b: float = 0.0, convention=None) -> dict:
    """
    Calcule les montants des heures supplémentaires selon la convention.

    Si h10 et h30 sont None → utilise les valeurs structurelles BTP (17,33h).
    Les coefficients de majoration sont résolus par `coeffs_heures_sup(convention)` :
    BTP/Commerce/AUCUNE conservent 10/30/40/70 ; la convention Pétrole applique
    sa propre grille (20/35/30b/50/100) et alimente la 5ᵉ case `h30b`.

    Retourne un dict avec taux_horaire, montants et descriptifs pour le bulletin.
    """
    th = calculer_taux_horaire(salaire_base)
    h10 = H_SUP_STRUCT_10 if h10 is None else float(h10)
    h30 = H_SUP_STRUCT_30 if h30 is None else float(h30)
    h40  = float(h40)
    h70  = float(h70)
    h30b = float(h30b)

    coeffs = coeffs_heures_sup(convention)

    # Taux majorés
    taux_10  = round(th * coeffs["10"],  4)
    taux_30  = round(th * coeffs["30"],  4)
    taux_40  = round(th * coeffs["40"],  4)
    taux_70  = round(th * coeffs["70"],  4)
    taux_30b = round(th * coeffs["30b"], 4)

    # Montants
    montant_10  = round(h10  * taux_10,  2) if h10  > 0 else 0.0
    montant_30  = round(h30  * taux_30,  2) if h30  > 0 else 0.0
    montant_40  = round(h40  * taux_40,  2) if h40  > 0 else 0.0
    montant_70  = round(h70  * taux_70,  2) if h70  > 0 else 0.0
    montant_30b = round(h30b * taux_30b, 2) if h30b > 0 else 0.0

    return {
        "taux_horaire":    th,
        # Heures +10%
        "h10":             h10,
        "taux_10":         taux_10,
        "montant_10":      montant_10,
        # Heures +30%
        "h30":             h30,
        "taux_30":         taux_30,
        "montant_30":      montant_30,
        # Heures repos/férié de jour (case 30b — convention Pétrole)
        "h30b":            h30b,
        "taux_30b":        taux_30b,
        "montant_30b":     montant_30b,
        # Heures +40% (nuit/dimanche)
        "h40":             h40,
        "taux_40":         taux_40,
        "montant_40":      montant_40,
        # Heures +70% (jours fériés)
        "h70":             h70,
        "taux_70":         taux_70,
        "montant_70":      montant_70,
        # Total heures sup
        "total_sup":       round(montant_10 + montant_30 + montant_30b
                                 + montant_40 + montant_70, 2),
    }


def calculer_irpp(base_imposable: float, nb_parts: float) -> float:
    if base_imposable <= 0 or nb_parts <= 0:
        return 0.0
    base_apres_abattement = base_imposable * 0.80
    base_arrondie = (int(base_apres_abattement) // 1000) * 1000
    revenu_par_part = base_arrondie / nb_parts
    impot_par_part = 0.0
    for borne_inf, borne_sup, taux in BAREME_IRPP:
        if revenu_par_part <= borne_inf:
            break
        tranche = min(revenu_par_part, borne_sup) - borne_inf
        impot_par_part += tranche * taux
    return fcfa(impot_par_part * nb_parts)


def calculer_bulletin(donnees: dict, nb_parts: float = 1.0) -> dict:
    """
    Calcule un bulletin de paie complet selon la réglementation gabonaise.
    Les heures sup sont attendues en MONTANTS (déjà calculés via calculer_heures_sup_btp).
    """
    def g(key):
        val = donnees.get(key, 0)
        try:
            return float(val) if val not in (None, "") else 0.0
        except (TypeError, ValueError):
            return 0.0

    # ── 1. ÉLÉMENTS BRUTS ───────────────────────────────────────────────────
    salaire_base      = g("salaire_base")
    heures_sup_10     = g("heures_sup_10")
    heures_sup_30     = g("heures_sup_30")
    heures_sup_30b    = g("heures_sup_30b")
    heures_sup_40     = g("heures_sup_40")
    heures_sup_fj     = g("heures_sup_fj")   # montant férié chômé payé — jour
    heures_sup_fn     = g("heures_sup_fn")   # montant férié chômé payé — nuit
    heures_sup_70     = g("heures_sup_70")
    absences          = g("absences")
    sursalaire        = g("sursalaire")
    prime_caisse      = g("prime_caisse")
    carburant         = g("carburant")
    prime_anciennete  = g("prime_anciennete")
    # ── Prime d'ancienneté automatique ──────────────────────────────────────
    # Si aucune prime n'est saisie manuellement, on la calcule d'après la
    # convention collective du tenant et l'ancienneté du salarié (en années).
    # Une valeur saisie à la main reste prioritaire : elle n'est jamais écrasée.
    anciennete_annees = int(g("anciennete_annees"))
    prime_anciennete_auto = 0.0
    if anciennete_annees > 0 and salaire_base > 0:
        prime_anciennete_auto = _prime_anciennete_convention(
            donnees.get("convention"), salaire_base, anciennete_annees)
    prime_anciennete_est_auto = False
    if prime_anciennete <= 0 and prime_anciennete_auto > 0:
        prime_anciennete = prime_anciennete_auto
        prime_anciennete_est_auto = True
    prime_rendement   = g("prime_rendement")
    prime_assiduité   = g("prime_assiduité")
    # ── Prime d'assiduité automatique (convention Hôtellerie, Art. 49) ───────
    # 9 % du salaire de base, amputée selon les absences injustifiées du mois
    # (-50 % à 1 jour, supprimée dès 2 jours ; 8 h de retard = 1 jour).
    # Une valeur manuelle reste prioritaire.
    prime_assiduite_est_auto = False
    if prime_assiduité <= 0 and (donnees.get("convention") or "").upper() == "HOTELLERIE" and salaire_base > 0:
        from convention_hotellerie import calculer_prime_assiduite_hotellerie
        prime_assiduité = calculer_prime_assiduite_hotellerie(
            salaire_base,
            jours_absence_injustifiee=int(g("jours_absence_injustifiee")),
            heures_retard_cumulees=float(g("heures_retard_cumulees")),
        )
        prime_assiduite_est_auto = True
    elif prime_assiduité <= 0 and (donnees.get("convention") or "").upper() == "BOIS" and salaire_base > 0:
        # Bois Art. 49 : forfait 11 h au taux horaire ; -25 %/-50 %/-100 %
        # selon 1/2/3 absences non autorisées du mois.
        from convention_bois import calculer_prime_assiduite_bois
        prime_assiduité = calculer_prime_assiduite_bois(
            salaire_base, nb_absences=int(g("jours_absence_injustifiee")))
        prime_assiduite_est_auto = True
    elif prime_assiduité <= 0 and (donnees.get("convention") or "").upper() == "MINIER" and salaire_base > 0:
        # Minier Art. 51 : 4 % du salaire de base, -25 %/journée d'absence non autorisée.
        from convention_minier import calculer_prime_assiduite_minier
        prime_assiduité = calculer_prime_assiduite_minier(
            salaire_base, nb_absences=int(g("jours_absence_injustifiee")))
        prime_assiduite_est_auto = True
    prime_qualite     = g("prime_qualite")
    prime_performance = g("prime_performance")
    prime_transport   = g("prime_transport")
    prime_responsabilite = g("prime_responsabilite")
    allocations_conge = g("allocations_conge")
    indem_logement        = g("indem_logement")
    indem_domesticite     = g("indem_domesticite")
    indem_eau_electricite = g("indem_eau_electricite")
    indem_nourriture      = g("indem_nourriture")
    indem_compensatrice_conge   = g("indem_compensatrice_conge")
    indem_services_rendus       = g("indem_services_rendus")
    indem_compensatrice_preavis = g("indem_compensatrice_preavis")
    indem_licenciement          = g("indem_licenciement")
    prime_panier          = g("prime_panier")
    indem_transport_net   = g("indem_transport")
    indem_representation  = g("indem_representation")
    prime_salisure        = g("prime_salisure")
    acompte               = g("acompte")

    # ── Prime de nuit automatique (convention Hôtellerie, Art. 39.2) ─────────
    # 20 % du salaire de base pour un poste de nuit (21h-6h). Distincte des
    # majorations d'heures sup de nuit. Une valeur manuelle reste prioritaire.
    prime_nuit = g("prime_nuit")
    prime_nuit_est_auto = False
    if prime_nuit <= 0 and (donnees.get("convention") or "").upper() == "HOTELLERIE":
        if donnees.get("travail_de_nuit") and salaire_base > 0:
            from convention_hotellerie import calculer_prime_nuit_hotellerie
            prime_nuit = calculer_prime_nuit_hotellerie(salaire_base, True)
            prime_nuit_est_auto = True

    # Taux horaire et détails heures sup (pour le retour enrichi). Les
    # coefficients d'affichage suivent la convention transmise (clé "convention")
    # afin que les taux majorés du bulletin reflètent la grille applicable.
    th = calculer_taux_horaire(salaire_base)
    _coeffs_hs = coeffs_heures_sup(donnees.get("convention"))

    # ── 2. SALAIRE BRUT ─────────────────────────────────────────────────────
    salaire_brut = (
        salaire_base
        + heures_sup_10 + heures_sup_30 + heures_sup_30b + heures_sup_40 + heures_sup_70
        + heures_sup_fj + heures_sup_fn
        - absences
        + sursalaire
        + prime_caisse + carburant + prime_anciennete
        + prime_nuit
        + indem_logement + indem_domesticite + indem_eau_electricite + indem_nourriture
        + prime_rendement + prime_assiduité + prime_qualite + prime_performance
        + prime_transport + prime_responsabilite
        + allocations_conge
        + indem_compensatrice_conge + indem_services_rendus
        + indem_compensatrice_preavis + indem_licenciement
    )

    # ── 2bis. COMPOSANTS PERSONNALISÉS (primes/indemnités/retenues du tenant) ──
    # Chaque composant est un dict : {libelle, sens('GAIN'|'RETENUE'), montant,
    # soumis_cnss, soumis_cnamgs, soumis_irpp}. Les gains s'ajoutent au brut, les
    # retenues s'en déduisent. On calcule en parallèle les "deltas" à retirer de
    # chaque base pour les composants NON soumis (afin de préserver exactement la
    # logique existante : avec zéro composant, tous les deltas valent 0).
    composants_perso = donnees.get("composants") or []
    delta_brut = 0.0
    delta_non_cnss = 0.0
    delta_non_cnamgs = 0.0
    delta_non_irpp = 0.0
    total_composants_gains = 0.0
    total_composants_retenues = 0.0
    composants_detail = []
    for c in composants_perso:
        try:
            montant = float(c.get("montant") or 0)
        except (TypeError, ValueError):
            montant = 0.0
        if montant == 0:
            continue
        est_gain = str(c.get("sens", "GAIN")).upper() == "GAIN"
        signe = montant if est_gain else -montant
        s_cnss   = bool(c.get("soumis_cnss", True))
        s_cnamgs = bool(c.get("soumis_cnamgs", True))
        s_irpp   = bool(c.get("soumis_irpp", True))
        delta_brut += signe
        if est_gain: total_composants_gains += montant
        else:        total_composants_retenues += montant
        if not s_cnss:   delta_non_cnss   += signe
        if not s_cnamgs: delta_non_cnamgs += signe
        if not s_irpp:   delta_non_irpp   += signe
        composants_detail.append({
            "libelle": c.get("libelle", ""), "sens": "GAIN" if est_gain else "RETENUE",
            "montant": round(montant, 2), "montant_signe": round(signe, 2),
            "soumis_cnss": s_cnss, "soumis_cnamgs": s_cnamgs, "soumis_irpp": s_irpp,
        })
    salaire_brut += delta_brut

    # ── 3. CNSS ─────────────────────────────────────────────────────────────
    transport_exo_cnss = min(prime_transport, TRANSPORT_EXONERATION_CNSS)
    base_cnss = min(salaire_brut - transport_exo_cnss - delta_non_cnss, CNSS_PLAFOND)
    base_cnss = max(base_cnss, 0)
    cnss_salarie   = fcfa(base_cnss * CNSS_TAUX_SALARIE)
    cnss_patronale = fcfa(base_cnss * CNSS_TAUX_PATRONAL)

    # ── 4. CNAMGS ────────────────────────────────────────────────────────────
    transport_exo_cnamgs = min(prime_transport, TRANSPORT_EXONERATION_IRPP)
    logement_imposable = min(indem_logement, salaire_brut * LOGEMENT_PLAFOND_PCT, LOGEMENT_PLAFOND_MAX)
    base_cnamgs = min(
        salaire_brut - transport_exo_cnamgs - indem_logement + logement_imposable - prime_qualite - delta_non_cnamgs,
        CNAMGS_PLAFOND
    )
    base_cnamgs = max(base_cnamgs, 0)
    cnamgs_salarie   = fcfa(base_cnamgs * CNAMGS_TAUX_SALARIE)
    cnamgs_patronale = fcfa(base_cnamgs * CNAMGS_TAUX_PATRONAL)

    # ── 5. FNH ──────────────────────────────────────────────────────────────
    base_fnh = min(max(base_cnss - indem_logement, 0), FNH_PLAFOND)
    fnh = fcfa(base_fnh * FNH_TAUX)

    # ── 6. CFP ──────────────────────────────────────────────────────────────
    base_cfp = max(base_cnss - indem_logement, 0)
    cfp = fcfa(base_cfp * CFP_TAUX)

    # ── 7. TCS ──────────────────────────────────────────────────────────────
    base_tcs = (
        salaire_brut
        - prime_qualite - prime_rendement - prime_performance
        - transport_exo_cnamgs - indem_logement
        - cnss_salarie - cnamgs_salarie
        + (indem_domesticite + indem_eau_electricite + indem_nourriture)
        - delta_non_irpp
    )
    base_tcs_imposable = max(base_tcs - TCS_EXONERATION, 0)
    tcs = fcfa(base_tcs_imposable * TCS_TAUX)

    # ── 8. NET AVANT IRPP ───────────────────────────────────────────────────
    net_avant_irpp = salaire_brut - cnss_salarie - cnamgs_salarie - tcs

    # ── 9. IRPP ─────────────────────────────────────────────────────────────
    base_irpp = max(base_tcs - tcs, 0)
    irpp = calculer_irpp(base_irpp, nb_parts)

    # ── 10. NET À PAYER ──────────────────────────────────────────────────────
    salaire_net = net_avant_irpp - irpp
    net_a_payer = (
        salaire_net
        + prime_panier + indem_transport_net + indem_representation + prime_salisure
        - acompte
    )

    return {
        "salaire_base":                round(salaire_base, 2),
        # Heures supplémentaires — montants
        "heures_sup_10":               round(heures_sup_10, 2),
        "heures_sup_30":               round(heures_sup_30, 2),
        "heures_sup_30b":              round(heures_sup_30b, 2),
        "heures_sup_40":               round(heures_sup_40, 2),
        "heures_sup_70":               round(heures_sup_70, 2),
        "heures_sup_fj":               round(heures_sup_fj, 2),
        "heures_sup_fn":               round(heures_sup_fn, 2),
        # Infos calcul heures sup (pour affichage bulletin)
        "taux_horaire_base":           round(th, 4),
        "taux_horaire_10":             round(th * _coeffs_hs["10"], 4),
        "taux_horaire_30":             round(th * _coeffs_hs["30"], 4),
        "taux_horaire_30b":            round(th * _coeffs_hs["30b"], 4),
        "taux_horaire_40":             round(th * _coeffs_hs["40"], 4),
        "taux_horaire_70":             round(th * _coeffs_hs["70"], 4),
        "h_normales_mensuel":          H_NORMALES_MENSUEL,
        "h_sup_struct_10":             H_SUP_STRUCT_10,
        "h_sup_struct_30":             H_SUP_STRUCT_30,
        # Tous les autres éléments
        "absences":                    round(absences, 2),
        "sursalaire":                  round(sursalaire, 2),
        "prime_caisse":                round(prime_caisse, 2),
        "carburant":                   round(carburant, 2),
        "prime_anciennete":            round(prime_anciennete, 2),
        "prime_anciennete_auto":       round(prime_anciennete_auto, 2),
        "prime_anciennete_est_auto":   prime_anciennete_est_auto,
        "prime_nuit":                  round(prime_nuit, 2),
        "prime_nuit_est_auto":         prime_nuit_est_auto,
        "anciennete_annees":           anciennete_annees,
        "indem_logement":              round(indem_logement, 2),
        "indem_domesticite":           round(indem_domesticite, 2),
        "indem_eau_electricite":       round(indem_eau_electricite, 2),
        "indem_nourriture":            round(indem_nourriture, 2),
        "prime_rendement":             round(prime_rendement, 2),
        "prime_assiduité":             round(prime_assiduité, 2),
        "prime_assiduite_est_auto":    prime_assiduite_est_auto,
        "prime_qualite":               round(prime_qualite, 2),
        "prime_performance":           round(prime_performance, 2),
        "prime_transport":             round(prime_transport, 2),
        "prime_responsabilite":        round(prime_responsabilite, 2),
        "allocations_conge":           round(allocations_conge, 2),
        "indem_compensatrice_conge":   round(indem_compensatrice_conge, 2),
        "indem_services_rendus":       round(indem_services_rendus, 2),
        "indem_compensatrice_preavis": round(indem_compensatrice_preavis, 2),
        "indem_licenciement":          round(indem_licenciement, 2),
        "prime_panier":                round(prime_panier, 2),
        "indem_transport":             round(indem_transport_net, 2),
        "indem_representation":        round(indem_representation, 2),
        "prime_salisure":              round(prime_salisure, 2),
        "acompte":                     round(acompte, 2),
        "composants":                  composants_detail,
        "total_composants_gains":      round(total_composants_gains, 2),
        "total_composants_retenues":   round(total_composants_retenues, 2),
        "salaire_brut":                round(salaire_brut, 2),
        "base_cnss":                   round(base_cnss, 2),
        "cnss_salarie":                round(cnss_salarie, 2),
        "cnss_patronale":              round(cnss_patronale, 2),
        "base_cnamgs":                 round(base_cnamgs, 2),
        "cnamgs_salarie":              round(cnamgs_salarie, 2),
        "cnamgs_patronale":            round(cnamgs_patronale, 2),
        "base_fnh":                    round(base_fnh, 2),
        "fnh":                         round(fnh, 2),
        "base_cfp":                    round(base_cfp, 2),
        "cfp":                         round(cfp, 2),
        "base_tcs":                    round(base_tcs, 2),
        "tcs":                         round(tcs, 2),
        "base_irpp":                   round(base_irpp, 2),
        "irpp":                        round(irpp, 2),
        "net_avant_irpp":              round(net_avant_irpp, 2),
        "salaire_net":                 round(salaire_net, 2),
        "net_a_payer":                 round(net_a_payer, 2),
        "_charges_patronales_total":   round(cnss_patronale + cnamgs_patronale + fnh + cfp, 2),
    }


def calculer_masse_salariale(bulletins: list) -> dict:
    totaux = {
        "nb_bulletins": len(bulletins),
        "total_brut": 0, "total_cnss_sal": 0, "total_cnamgs_sal": 0,
        "total_tcs": 0, "total_irpp": 0, "total_net": 0,
        "total_cnss_pat": 0, "total_cnamgs_pat": 0, "total_fnh": 0, "total_cfp": 0,
    }
    for b in bulletins:
        totaux["total_brut"]       += float(b.salaire_brut or 0)
        totaux["total_cnss_sal"]   += float(b.cnss_salarie or 0)
        totaux["total_cnamgs_sal"] += float(b.cnamgs_salarie or 0)
        totaux["total_tcs"]        += float(b.tcs or 0)
        totaux["total_irpp"]       += float(b.irpp or 0)
        totaux["total_net"]        += float(b.net_a_payer or 0)
        totaux["total_cnss_pat"]   += float(b.cnss_patronale or 0)
        totaux["total_cnamgs_pat"] += float(b.cnamgs_patronale or 0)
        totaux["total_fnh"]        += float(b.fnh or 0)
        totaux["total_cfp"]        += float(b.cfp or 0)
    totaux["total_charges_pat"] = (
        totaux["total_cnss_pat"] + totaux["total_cnamgs_pat"]
        + totaux["total_fnh"] + totaux["total_cfp"]
    )
    totaux["total_charges_patronales"] = totaux["total_charges_pat"]
    return {k: round(v, 2) for k, v in totaux.items()}


def distribuer_heures_semaine_petrole(heures_par_jour: list,
                                       types_par_jour: list = None,
                                       seuil_normales: float = 40.0) -> dict:
    """
    Distribue les heures d'une semaine selon la Convention Pétrole — Art. 38.2.

    Même structure hebdomadaire que le BTP, mais palier +20 % large de 8 h
    (41ᵉ→48ᵉ) puis +35 % au-delà, et aiguillage pétrolier des cases :
        0 → S          : Heures normales
        S → S+8        : case 10 (+20 %)
        > S+8          : case 30 (+35 %)
        Dim/Férié jour : case 30b (+30 %)
        Nuit ouvrable  : case 40 (+50 %)
        Nuit dim/férié : case 70 (+100 %)
    """
    total_norm = 0.0   # heures de jour ordinaires cumulées
    h30b = 0.0         # dim/férié, heures de jour (+30 %)
    h40  = 0.0         # nuit ouvrable (+50 %)
    h70  = 0.0         # nuit dim/férié (+100 %)

    SEUIL_NORM = float(seuil_normales)
    SEUIL_20   = SEUIL_NORM + 8.0   # fin du palier +20 % (8h) / début +35 %

    for i, jour in enumerate(heures_par_jour):
        if isinstance(jour, dict):
            h      = float(jour.get("heures_normales", 0) or 0)
            h_nuit = float(jour.get("heures_sup_nuit", 0) or 0)
            tj     = (jour.get("type_jour", "NORMAL") or "NORMAL").upper()
        else:
            h = float(jour or 0)
            h_nuit = 0
            tj = (types_par_jour[i] if types_par_jour and i < len(types_par_jour) else "NORMAL").upper()

        if tj in ("DIMANCHE", "FERIE", "REPOS"):
            h30b += h         # heures de jour un dimanche/férié → +30 %
            h70  += h_nuit    # heures de nuit un dimanche/férié → +100 %
        else:
            total_norm += h
            h40 += h_nuit     # nuit un jour ouvrable → +50 %

    h_norm_finale = min(total_norm, SEUIL_NORM)
    reste = total_norm - h_norm_finale
    h_10 = min(reste, SEUIL_20 - SEUIL_NORM)   # S→S+8, max 8h (+20 %)
    reste -= h_10
    h_30 = reste                                # au-delà de S+8 (+35 %)

    return {
        "total_heures":     round(total_norm + h30b + h40 + h70, 2),
        "heures_normales":  round(h_norm_finale, 2),
        "heures_sup_10":    round(h_10, 2),     # 41ᵉ-48ᵉ (+20 %)
        "heures_sup_30":    round(h_30, 2),     # >48ᵉ (+35 %)
        "heures_sup_30b":   round(h30b, 2),     # dim/férié jour (+30 %)
        "heures_sup_40":    round(h40, 2),      # nuit ouvrable (+50 %)
        "heures_sup_70":    round(h70, 2),      # nuit dim/férié (+100 %)
        "seuil_10_atteint": total_norm >= SEUIL_NORM,
        "seuil_30_atteint": total_norm >= SEUIL_20,
        "seuil_48_depasse": total_norm > SEUIL_20,
        "heures_restantes_avant_10": max(0, round(SEUIL_NORM - total_norm, 2)),
        "heures_restantes_avant_30": max(0, round(SEUIL_20 - total_norm, 2)),
    }


def distribuer_heures_semaine_btp(heures_par_jour: list,
                                   types_par_jour: list = None,
                                   seuil_normales: float = 40.0) -> dict:
    """
    Distribue les heures d'une semaine selon la convention BTP Gabon.

    heures_par_jour : liste de dict par jour travaillé :
        {"heures_normales": 8.0, "heures_sup_nuit": 0, "type_jour": "NORMAL"}
    types_par_jour  : optionnel, liste de str ("NORMAL","DIMANCHE","FERIE",...)
    seuil_normales  : seuil hebdomadaire (en heures) à partir duquel les heures
        supplémentaires se déclenchent. Défaut = 40h (seuil légal). Une entreprise
        sous dérogation peut le porter à 45h ou 48h : en deçà du seuil, les heures
        restent payées au taux normal. La grille de majoration (+10% sur les 4
        premières heures sup, +30% au-delà) se décale avec le seuil.

    Logique BTP (seuil légal de 40h) :
        0  → 40h  : Heures normales
        40 → 44h  : +10% (max 4h)
        44 → 48h  : +30% (max 4h)
        > 48h     : +30% (au-delà)
        Nuit      : +40% (indépendant, cumulable)
        Dim/Férié : +70% (remplace le taux normal pour ces heures)

    Avec un seuil dérogatoire S, la grille devient :
        0 → S, S → S+4 (+10%), S+4 → S+8 (+30%), > S+8 (+30%).
    """
    total_norm  = 0.0  # heures normales cumulées (hors dim/férié/nuit)
    h40_fin_btp = 0.0  # +40% cumulées (nuit)
    h70_fin_btp = 0.0  # +70% cumulées (dim/férié)

    # Bornes calées sur le seuil dérogatoire (40h par défaut), largeurs BTP préservées.
    SEUIL_10 = float(seuil_normales)        # fin des heures normales / début +10%
    SEUIL_30 = SEUIL_10 + 4.0               # fin du palier +10% (4h) / début +30%
    SEUIL_48 = SEUIL_30 + 4.0               # repère informatif (palier +30% « plein »)

    for i, jour in enumerate(heures_par_jour):
        if isinstance(jour, dict):
            h     = float(jour.get("heures_normales", 0) or 0)
            h_nuit = float(jour.get("heures_sup_nuit", 0) or 0)
            tj    = (jour.get("type_jour", "NORMAL") or "NORMAL").upper()
        else:
            h = float(jour or 0)
            h_nuit = 0
            tj = (types_par_jour[i] if types_par_jour and i < len(types_par_jour) else "NORMAL").upper()

        if tj in ("DIMANCHE", "FERIE"):
            # Toutes les heures de ce jour → +70%
            h70_fin_btp += h
        else:
            total_norm += h

        if h_nuit > 0:
            h40_fin_btp += h_nuit

    # Distribution des heures normales sur la grille 40/44/48
    h_norm_finale = min(total_norm, SEUIL_10)
    reste = total_norm - h_norm_finale

    h_10 = min(reste, SEUIL_30 - SEUIL_10)   # max 4h
    reste -= h_10

    h_30 = min(reste, SEUIL_48 - SEUIL_30)   # max 4h
    reste -= h_30

    h_30_plus = reste  # heures au-delà de 48h → aussi +30% en BTP

    return {
        "total_heures":     round(total_norm + h70_fin_btp + h40_fin_btp, 2),
        "heures_normales":  round(h_norm_finale, 2),      # 0→40h
        "heures_sup_10":    round(h_10, 2),               # 40→44h
        "heures_sup_30":    round(h_30 + h_30_plus, 2),  # 44→48h + >48h
        "heures_sup_40":    round(h40_fin_btp, 2),        # nuit
        "heures_sup_70":    round(h70_fin_btp, 2),        # dim/férié
        "seuil_10_atteint": total_norm >= SEUIL_10,
        "seuil_30_atteint": total_norm >= SEUIL_30,
        "seuil_48_depasse": total_norm > SEUIL_48,
        "heures_restantes_avant_10": max(0, round(SEUIL_10 - total_norm, 2)),
        "heures_restantes_avant_30": max(0, round(SEUIL_30 - total_norm, 2)),
    }


# ══════════════════════════════════════════════════════════════════════════════
# VENTILATION MENSUELLE DU POINTAGE — RÉGIME BTP GABON (40h légal / 48h contractuel)
# Analyse chaque jour (ligne de pointage) de façon INDÉPENDANTE, puis applique
# le filtre réglementaire SEMAINE PAR SEMAINE (lundi → dimanche).
# ══════════════════════════════════════════════════════════════════════════════

# Heures théoriques d'un jour férié chômé (comptées en normales pour préserver le salaire)
HEURES_JOUR_FERIE_CHOME = 8.0
# Seuils hebdomadaires BTP
_SEUIL_NORMALES = 40.0   # 0 → 40h : normales
_SEUIL_10       = 44.0   # 40 → 44h : +10% (4h max)
# au-delà de 44h : +30%


def _classer_jour_btp(jour, feries_set):
    """
    Analyse UNE ligne de pointage de façon indépendante.
    Renvoie un tuple (categorie, heures_jour, heures_nuit) où categorie ∈
    {"ORDINAIRE", "DIM_FERIE_TRAVAILLE", "FERIE_CHOME", "REPOS"}.

    Aucune présomption : on lit exactement ce qui est pointé (pas de "8h d'office").
    """
    d = jour.get("date")
    hj = float(jour.get("heures", jour.get("heures_jour", 0)) or 0)
    hn = float(jour.get("heures_nuit", 0) or 0)

    # Jour férié : flag explicite prioritaire, sinon appartenance à l'ensemble fourni
    if "ferie" in jour and jour["ferie"] is not None:
        est_ferie = bool(jour["ferie"])
    else:
        est_ferie = bool(feries_set and d in feries_set)

    est_dimanche = bool(d is not None and hasattr(d, "weekday") and d.weekday() == 6)

    # Présence : explicite si fournie, sinon déduite des heures réellement pointées
    if "present" in jour and jour["present"] is not None:
        travaille = bool(jour["present"]) and (hj + hn) > 0
    else:
        travaille = (hj + hn) > 0

    if (est_dimanche or est_ferie) and travaille:
        # Dimanche/férié travaillé : l'intégralité bascule en +70% (base + fin de journée)
        return ("DIM_FERIE_TRAVAILLE", hj, hn)
    if est_ferie and not travaille and not est_dimanche:
        # Férié chômé en semaine : 8h théoriques comptées en normales
        return ("FERIE_CHOME", 0.0, 0.0)
    if travaille:
        return ("ORDINAIRE", hj, hn)
    return ("REPOS", 0.0, 0.0)


def _cle_semaine(d):
    """Clé (année ISO, semaine ISO) pour regrouper les jours par semaine lundi→dimanche."""
    iso = d.isocalendar()
    return (iso[0], iso[1])


def ventiler_heures_mois_btp(jours, feries=None, seuil_normales: float = None) -> dict:
    """
    Ventile un MOIS de pointage selon la réglementation BTP Gabon, semaine par semaine.

    Paramètres
    ----------
    jours : liste de dict, une entrée par jour (ligne de pointage), traitées
            INDÉPENDAMMENT. Champs reconnus :
        - "date"        : datetime.date  (REQUIS — détermine le jour de la semaine)
        - "heures"      : float — heures de JOUR travaillées (alias : "heures_jour")
        - "heures_nuit" : float — heures de nuit travaillées ce jour  (→ +40%)
        - "ferie"       : bool  — jour férié (sinon déduit de `feries`)
        - "present"     : bool  — présence (sinon déduite des heures pointées)
    feries : ensemble/liste de datetime.date fériés (utilisé si "ferie" absent du jour).
    seuil_normales : seuil hebdomadaire (en heures) de déclenchement des heures
        supplémentaires. None ou non fourni → 40h (seuil légal). Une entreprise sous
        dérogation peut le porter à 45h/48h : sous le seuil, les heures restent au
        taux normal. Le palier +10% (4h) puis +30% se décale avec le seuil.

    Règles appliquées par semaine (lundi → dimanche), avec un seuil S (40h par défaut) :
        • 0 → S (hors dim./fériés)                → Heures normales
        • S → S+4                                 → +10% (4h max)
        • S+4 et au-delà (+ heures sup de semaine)→ +30%
        • Dimanche/férié TRAVAILLÉ                → +70% (intégralité de la journée)
        • Férié chômé en semaine                  → 8h en Heures normales
        • Heures de nuit                          → +40% (indépendant des seuils)

    Retour : dict des totaux mensuels ventilés + le détail par semaine.
    """
    feries_set = set(feries) if feries else set()

    # Seuils calés sur la dérogation éventuelle (40h légal par défaut),
    # largeur du palier +10% préservée (4h) conformément à la convention BTP.
    seuil_norm = _SEUIL_NORMALES if seuil_normales is None else float(seuil_normales)
    seuil_10   = seuil_norm + 4.0

    semaines = {}   # cle_semaine -> accumulateurs
    for jour in jours:
        d = jour.get("date")
        if d is None:
            continue
        cat, hj, hn = _classer_jour_btp(jour, feries_set)
        sem = semaines.setdefault(_cle_semaine(d), {
            "cumul_ordinaire": 0.0,  # heures de jour ordinaires (cumul hebdo)
            "nuit": 0.0,             # heures de nuit (+40%)
            "dim_ferie": 0.0,        # heures dim/férié travaillés (+70%)
            "feries_chomes": 0.0,    # heures normales issues de fériés chômés
        })
        if cat == "DIM_FERIE_TRAVAILLE":
            sem["dim_ferie"] += hj + hn
        elif cat == "FERIE_CHOME":
            sem["feries_chomes"] += HEURES_JOUR_FERIE_CHOME
        elif cat == "ORDINAIRE":
            sem["cumul_ordinaire"] += hj
            sem["nuit"] += hn
        # "REPOS" : rien

    tot = {"heures_normales": 0.0, "heures_sup_10": 0.0, "heures_sup_30": 0.0,
           "heures_sup_40": 0.0, "heures_sup_70": 0.0}
    detail_semaines = []

    for cle in sorted(semaines.keys()):
        s = semaines[cle]
        cumul = s["cumul_ordinaire"]

        normales = min(cumul, seuil_norm) + s["feries_chomes"]
        h10 = min(max(cumul - seuil_norm, 0.0), seuil_10 - seuil_norm)  # S→S+4, max 4h
        h30 = max(cumul - seuil_10, 0.0)                                # S+4 et +
        h40 = s["nuit"]
        h70 = s["dim_ferie"]

        tot["heures_normales"] += normales
        tot["heures_sup_10"]   += h10
        tot["heures_sup_30"]   += h30
        tot["heures_sup_40"]   += h40
        tot["heures_sup_70"]   += h70

        detail_semaines.append({
            "semaine": f"{cle[0]}-S{cle[1]:02d}",
            "cumul_ordinaire": round(cumul, 2),
            "heures_normales": round(normales, 2),
            "heures_sup_10": round(h10, 2),
            "heures_sup_30": round(h30, 2),
            "heures_sup_40": round(h40, 2),
            "heures_sup_70": round(h70, 2),
        })

    resultat = {k: round(v, 2) for k, v in tot.items()}
    resultat["total_heures"] = round(sum(tot.values()), 2)
    resultat["total_heures_sup"] = round(
        tot["heures_sup_10"] + tot["heures_sup_30"] + tot["heures_sup_40"] + tot["heures_sup_70"], 2)
    resultat["detail_semaines"] = detail_semaines
    return resultat


def ventiler_heures_mois_petrole(jours, feries=None, seuil_normales: float = None) -> dict:
    """
    Ventile un MOIS de pointage selon la Convention Pétrole (SGEPP/GPP), Art. 38.2.

    Même structure hebdomadaire que le BTP (cumul lundi→dimanche, seuils glissants),
    mais l'aiguillage des cases diffère pour coller au barème pétrolier :

        • 0 → S (jour ouvrable, hors dim./férié)      → Heures normales
        • S → S+8 (jour ouvrable)                     → case 10 (+20 % : 41ᵉ-48ᵉ h)
        • au-delà de S+8 (jour ouvrable)              → case 30 (+35 % : >48ᵉ h)
        • dimanche / férié TRAVAILLÉ, heures de JOUR  → case 30b (+30 %)
        • nuit (21h-6h) un jour ouvrable              → case 40 (+50 %)
        • nuit (21h-6h) un dimanche / férié           → case 70 (+100 %)
        • férié chômé en semaine                      → 8 h en Heures normales

    Le seuil S vaut 40 h par défaut (déclenchement légal). Une dérogation peut le
    porter jusqu'à 48 h ; le palier +20 % couvre alors les 8 heures suivantes.

    Retour : dict des totaux mensuels ventilés (5 cases) + le détail par semaine.
    """
    feries_set = set(feries) if feries else set()
    seuil_norm = _SEUIL_NORMALES if seuil_normales is None else float(seuil_normales)
    # Largeur du 1er palier pétrolier : 41ᵉ → 48ᵉ heure = 8 h (contre 4 h en BTP).
    seuil_20 = seuil_norm + 8.0

    semaines = {}
    for jour in jours:
        d = jour.get("date")
        if d is None:
            continue
        cat, hj, hn = _classer_jour_btp(jour, feries_set)
        sem = semaines.setdefault(_cle_semaine(d), {
            "cumul_ordinaire": 0.0,   # heures de jour ordinaires (cumul hebdo)
            "nuit": 0.0,              # nuit jour ouvrable (+50 %)
            "dim_ferie_jour": 0.0,    # dim/férié travaillé, heures de jour (+30 %)
            "dim_ferie_nuit": 0.0,    # dim/férié travaillé, heures de nuit (+100 %)
            "feries_chomes": 0.0,     # heures normales issues de fériés chômés
        })
        if cat == "DIM_FERIE_TRAVAILLE":
            sem["dim_ferie_jour"] += hj
            sem["dim_ferie_nuit"] += hn
        elif cat == "FERIE_CHOME":
            sem["feries_chomes"] += HEURES_JOUR_FERIE_CHOME
        elif cat == "ORDINAIRE":
            sem["cumul_ordinaire"] += hj
            sem["nuit"] += hn
        # "REPOS" : rien

    tot = {"heures_normales": 0.0, "heures_sup_10": 0.0, "heures_sup_30": 0.0,
           "heures_sup_30b": 0.0, "heures_sup_40": 0.0, "heures_sup_70": 0.0}
    detail_semaines = []

    for cle in sorted(semaines.keys()):
        s = semaines[cle]
        cumul = s["cumul_ordinaire"]

        normales = min(cumul, seuil_norm) + s["feries_chomes"]
        h10  = min(max(cumul - seuil_norm, 0.0), seuil_20 - seuil_norm)  # S→S+8 (+20 %)
        h30  = max(cumul - seuil_20, 0.0)                                # >S+8  (+35 %)
        h30b = s["dim_ferie_jour"]                                       # repos/férié jour
        h40  = s["nuit"]                                                 # nuit ouvrable
        h70  = s["dim_ferie_nuit"]                                       # nuit dim/férié

        tot["heures_normales"] += normales
        tot["heures_sup_10"]   += h10
        tot["heures_sup_30"]   += h30
        tot["heures_sup_30b"]  += h30b
        tot["heures_sup_40"]   += h40
        tot["heures_sup_70"]   += h70

        detail_semaines.append({
            "semaine": f"{cle[0]}-S{cle[1]:02d}",
            "cumul_ordinaire": round(cumul, 2),
            "heures_normales": round(normales, 2),
            "heures_sup_10": round(h10, 2),
            "heures_sup_30": round(h30, 2),
            "heures_sup_30b": round(h30b, 2),
            "heures_sup_40": round(h40, 2),
            "heures_sup_70": round(h70, 2),
        })

    resultat = {k: round(v, 2) for k, v in tot.items()}
    resultat["total_heures"] = round(sum(tot.values()), 2)
    resultat["total_heures_sup"] = round(
        tot["heures_sup_10"] + tot["heures_sup_30"] + tot["heures_sup_30b"]
        + tot["heures_sup_40"] + tot["heures_sup_70"], 2)
    resultat["detail_semaines"] = detail_semaines
    return resultat


def ventiler_heures_mois(convention, jours, feries=None, seuil_normales: float = None) -> dict:
    """Ventilation mensuelle du pointage selon la convention (BTP ou Pétrole).

    Les deux conventions raisonnent par seuils hebdomadaires ; le résultat
    contient toujours les 5 cases (la case 30b reste à 0 hors Pétrole).
    """
    c = (convention or "").upper()
    if c in ("PETROLE", "INDUSTRIE", "AERIEN"):
        # Même structure de ventilation : bande 41→48 h (8 h) puis >48 h, et
        # 5 cases distinctes (jour férié / nuit / nuit férié). Seuls les
        # coefficients diffèrent (cf. COEFFS_HEURES_SUP_CONVENTION).
        return ventiler_heures_mois_petrole(jours, feries=feries, seuil_normales=seuil_normales)
    res = ventiler_heures_mois_btp(jours, feries=feries, seuil_normales=seuil_normales)
    res.setdefault("heures_sup_30b", 0.0)
    return res


def _hhmm_vers_minutes(horaire):
    """Convertit une chaîne d'horaire 'H:MM' ou 'HH:MM' en minutes depuis minuit.

    Renvoie None si la valeur est absente ou invalide. Tolère '24:00' (= minuit
    du lendemain, soit 1440 minutes).
    """
    if not horaire:
        return None
    s = str(horaire).strip()
    if ":" not in s:
        return None
    parts = s.split(":")
    try:
        h = int(parts[0]); m = int(parts[1])
    except (ValueError, IndexError):
        return None
    if h == 24 and m == 0:
        return 1440
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def heures_nuit_depuis_horaires(entree_sup, sortie_sup):
    """Calcule les heures de NUIT (fenêtre légale 21h00 → 06h00) comprises dans
    UNE plage horaire [entree, sortie] (chaînes 'HH:MM').

    Gère le passage de minuit (ex. 20:00 → 02:00 = 5h de nuit : 21h→02h).
    Renvoie un float (heures de nuit, arrondi au centième), ou None si les
    horaires sont absents/invalides.

    Exemples :
        ('17:00', '22:00') → 1.0   (seule la tranche 21h-22h est de nuit)
        ('14:00', '18:00') → 0.0   (rien après 21h)
        ('20:00', '02:00') → 5.0   (21h→02h)
        ('21:00', '06:00') → 9.0   (toute la plage est de nuit)
    """
    debut = _hhmm_vers_minutes(entree_sup)
    fin   = _hhmm_vers_minutes(sortie_sup)
    if debut is None or fin is None:
        return None
    if fin <= debut:
        fin += 1440  # la sortie a lieu le lendemain

    # Fenêtres de nuit (21h00→06h00) projetées sur une frise de 48h pour couvrir
    # les plages qui débordent après minuit :
    #   • 00h00-06h00 (jour J)             → [0, 360]
    #   • 21h00 (jour J) → 06h00 (jour J+1) → [1260, 1800]   (contiguës)
    #   • 21h00-06h00 (jour J+1)           → [2700, 3240]    (sécurité plages longues)
    fenetres_nuit = ((0, 360), (1260, 1800), (2700, 3240))
    minutes_nuit = 0
    for (a, b) in fenetres_nuit:
        minutes_nuit += max(0, min(fin, b) - max(debut, a))
    return round(minutes_nuit / 60.0, 2)


def heures_nuit_journee(p):
    """Cumule les heures de nuit (21h00 → 06h00) sur TOUTES les plages travaillées
    d'un pointage : matin, après-midi ET heures supplémentaires.

    Conforme au Code du Travail gabonais : toute heure effectuée entre 21h00 et
    06h00 est une heure de nuit, quel que soit le créneau où elle tombe.

    Renvoie un float (total des heures de nuit du jour), ou None si AUCUNE des
    trois plages n'a d'horaire exploitable (l'appelant utilisera alors la valeur
    stockée `heures_sup_40` comme repli — compatibilité ascendante).
    """
    plages = (
        (getattr(p, "entree_matin",  None), getattr(p, "sortie_matin",  None)),
        (getattr(p, "entree_apmidi", None), getattr(p, "sortie_apmidi", None)),
        (getattr(p, "entree_sup",    None), getattr(p, "sortie_sup",    None)),
    )
    total = 0.0
    trouve = False
    for entree, sortie in plages:
        n = heures_nuit_depuis_horaires(entree, sortie)
        if n is not None:
            trouve = True
            total += n
    if not trouve:
        return None
    return round(total, 2)


def pointage_vers_jours(pointages):
    """
    Adaptateur : convertit des enregistrements ORM `Pointage` en liste de dicts
    pour `ventiler_heures_mois_btp`, en lisant chaque ligne indépendamment.

    On RECONSTRUIT les heures réellement travaillées en sommant toutes les
    colonnes (heures_normales + sup 10/30/40/70), car la ventilation par jour
    déjà appliquée a pu vider `heures_normales` (ex. un férié travaillé range
    ses 8h de base dans heures_sup_70). On laisse ensuite l'algorithme
    hebdomadaire reclasser correctement.

    HEURES DE NUIT (Code du Travail gabonais) :
      Les heures de nuit sont CALCULÉES à partir des horaires réels de TOUTES les
      plages travaillées (matin, après-midi ET heures supplémentaires) : toute
      heure effectuée entre 21h00 et 06h00 est une heure de nuit, rémunérée à
      +40 % en semaine. Si aucun horaire n'est exploitable (anciens pointages),
      on retombe sur la valeur stockée `heures_sup_40` (compatibilité ascendante).

    Règles de mapping :
      - type_jour CHOME_PAYE / CHOME_RECUPERABLE → férié chômé (8h normales)
      - type_jour FERIE                          → férié travaillé (+70%)
      - dimanche (détecté par la date) travaillé  → +70% (jour ET nuit)
      - jour ordinaire (lun-sam)                 → nuit isolée à +40%, reste au
                                                    cumul hebdomadaire (10%/30%)
    """
    jours = []
    for p in pointages:
        d = getattr(p, "date_pointage", None)
        if d is None:
            continue
        type_jour = (getattr(p, "type_jour", "") or "").upper()

        # Total des heures réellement travaillées ce jour-là
        raw = (float(getattr(p, "heures_normales", 0) or 0)
               + float(getattr(p, "heures_sup_10", 0) or 0)
               + float(getattr(p, "heures_sup_30", 0) or 0)
               + float(getattr(p, "heures_sup_40", 0) or 0)
               + float(getattr(p, "heures_sup_70", 0) or 0))

        # Heures de nuit : cumulées sur TOUTES les plages (matin + après-midi +
        # sup) depuis les horaires réels ; repli sur la valeur stockée si aucun
        # horaire n'est exploitable (anciens pointages).
        nuit_calc = heures_nuit_journee(p)
        if nuit_calc is None:
            nuit = float(getattr(p, "heures_sup_40", 0) or 0)
        else:
            nuit = nuit_calc
        # Sécurité : la nuit ne peut pas dépasser le total travaillé
        nuit = min(max(nuit, 0.0), raw)

        present = bool(getattr(p, "present", True)) and not bool(getattr(p, "absent", False))

        if type_jour in ("CHOME_PAYE", "CHOME_RECUPERABLE"):
            jours.append({"date": d, "heures": 0.0, "heures_nuit": 0.0,
                          "ferie": True, "present": False})
        elif type_jour == "FERIE":
            # Férié travaillé : tout passe en +70%, pas d'isolement de la nuit
            jours.append({"date": d, "heures": raw, "heures_nuit": 0.0,
                          "ferie": True, "present": present})
        elif hasattr(d, "weekday") and d.weekday() == 6:
            # Dimanche : l'intégralité (jour ET nuit) bascule en +70%
            jours.append({"date": d, "heures": raw, "heures_nuit": 0.0,
                          "ferie": False, "present": present})
        else:
            # Jour ordinaire (lun-sam) : on isole la nuit (+40%), le reste
            # alimente le cumul hebdomadaire (seuils 10%/30%)
            jours.append({"date": d, "heures": max(raw - nuit, 0.0), "heures_nuit": nuit,
                          "ferie": False, "present": present})
    return jours

def calculer_prime_anciennete_btp(salaire_base: float, anciennete_annees: int) -> float:
    """
    Calcule la prime d'ancienneté BTP Gabon — Art. A.46.
    Attribuée après 2 ans de présence continue.
    Taux : 2% du salaire de base conventionnel, majoré de 1% par an.

    Exemples :
      2 ans → 2%    du salaire base
      3 ans → 3%
      5 ans → 5%
     10 ans → 10%
    """
    if anciennete_annees < 2 or salaire_base <= 0:
        return 0.0
    taux = min(0.02 + 0.01 * (anciennete_annees - 2), 0.30)  # plafond 30%
    return round(salaire_base * taux, 0)


def calculer_preavis_btp(anciennete_annees: int) -> int:
    """
    Calcule la durée du préavis en jours — Convention BTP Art. A.30.3.
    Applicable aux CDI.

    Barème :
      1 mois à 1 an  → 15 jours
      1 à 3 ans      → 30 jours
      3 à 5 ans      → 60 jours
      5 à 10 ans     → 95 jours
      10 à 15 ans    → 125 jours
      15 à 20 ans    → 160 jours
      20 à 25 ans    → 180 jours
      26 ans+        → 190 jours + 10j par année supplémentaire
    """
    if anciennete_annees < 1:
        return 15
    elif anciennete_annees < 3:
        return 30
    elif anciennete_annees < 5:
        return 60
    elif anciennete_annees < 10:
        return 95
    elif anciennete_annees < 15:
        return 125
    elif anciennete_annees < 20:
        return 160
    elif anciennete_annees < 26:
        return 180
    else:
        return 190 + (anciennete_annees - 26) * 10


def calculer_indemnite_services_rendus_btp(
    moyenne_12_mois: float, anciennete_annees: int
) -> float:
    """
    Calcule l'indemnité de services rendus — Convention BTP Art. A.32.
    En cas de licenciement (hors faute lourde) après 2 ans de présence.

    Base : moyenne mensuelle salaire global des 12 derniers mois
    Taux :
      2 à 10 ans  → 20% × années
      10 à 15 ans → 26% × années
      15 à 20 ans → 30% × années
      > 20 ans    → 35% × années
    """
    if anciennete_annees < 2 or moyenne_12_mois <= 0:
        return 0.0
    if anciennete_annees <= 10:
        taux = 0.20
    elif anciennete_annees <= 15:
        taux = 0.26
    elif anciennete_annees <= 20:
        taux = 0.30
    else:
        taux = 0.35
    return round(moyenne_12_mois * taux * anciennete_annees, 0)


def calculer_preavis_industrie(anciennete_annees: int) -> int:
    """
    Durée du préavis (jours) — Convention Entreprises Industrielles, Art. A.30.3.
    Applicable aux CDI. Barème confirmé sur l'original :

      1 mois à 1 an  → 15 jours
      1 à 3 ans      → 1 mois  (30 j)
      3 à 5 ans      → 2 mois  (60 j)
      5 à 10 ans     → 3 mois  (90 j)
      10 à 15 ans    → 5 mois  (150 j)
      15 à 20 ans    → 6 mois  (180 j)
      20 à 30 ans    → 7 mois  (210 j)
      au-delà de 30  → 210 j + 21 jours par année de présence supplémentaire
    (Conversion mois→jours à 30 j, comme pour le barème légal.)
    """
    if anciennete_annees < 1:
        return 15
    elif anciennete_annees < 3:
        return 30
    elif anciennete_annees < 5:
        return 60
    elif anciennete_annees < 10:
        return 90
    elif anciennete_annees < 15:
        return 150
    elif anciennete_annees < 20:
        return 180
    elif anciennete_annees <= 30:
        return 210
    else:
        return 210 + (anciennete_annees - 30) * 21


def calculer_indemnite_services_rendus_industrie(
    moyenne_12_mois: float, anciennete_annees: int
) -> float:
    """
    Indemnité de services rendus — Convention Entreprises Industrielles, Art. A.32.
    Due après 2 ans de présence, pour un motif autre que la faute lourde.

    Base : moyenne mensuelle du salaire global des 12 derniers mois.
    Taux (par année de présence) :
      0 à 5 ans   → 20 %
      5 à 15 ans  → 25 %
      au-delà 15  → 33 %
    """
    if anciennete_annees < 2 or moyenne_12_mois <= 0:
        return 0.0
    if anciennete_annees <= 5:
        taux = 0.20
    elif anciennete_annees <= 15:
        taux = 0.25
    else:
        taux = 0.33
    return round(moyenne_12_mois * taux * anciennete_annees, 0)


def calculer_preavis_aerien(anciennete_annees: int, cadre: bool = False) -> int:
    """
    Durée du préavis (jours) — Convention Transports Aériens, Art. A.30.3.
    Deux barèmes selon la qualification :

    Personnel d'exécution (cadre=False) :
      <1 an 15 j · 1-3 ans 1 mois · 3-5 2 mois · 5-10 3 mois · 10-15 4 mois ·
      15-20 5 mois · 20-30 6 mois · au-delà de 30 : +10 j / année.

    Personnel de maîtrise & cadres (cadre=True) :
      <1 an 1 mois · 1-3 ans 2 mois · 3-5 3 mois · 5-10 4 mois · 10-15 5 mois ·
      15-20 6 mois · 20-30 7 mois · au-delà de 30 : +15 j / année.
    (Conversion mois→jours à 30 j.)
    """
    if cadre:
        if anciennete_annees < 1:   return 30
        elif anciennete_annees < 3: return 60
        elif anciennete_annees < 5: return 90
        elif anciennete_annees < 10: return 120
        elif anciennete_annees < 15: return 150
        elif anciennete_annees < 20: return 180
        elif anciennete_annees <= 30: return 210
        else: return 210 + (anciennete_annees - 30) * 15
    else:
        if anciennete_annees < 1:   return 15
        elif anciennete_annees < 3: return 30
        elif anciennete_annees < 5: return 60
        elif anciennete_annees < 10: return 90
        elif anciennete_annees < 15: return 120
        elif anciennete_annees < 20: return 150
        elif anciennete_annees <= 30: return 180
        else: return 180 + (anciennete_annees - 30) * 10


def calculer_indemnite_services_rendus_aerien(
    moyenne_12_mois: float, anciennete_annees: int
) -> float:
    """
    Indemnité de services rendus — Convention Transports Aériens, Art. A.32.
    Due après 2 ans de présence, pour un motif autre que la faute lourde.
    Base : moyenne mensuelle du salaire global des 12 derniers mois.
    Taux (par année de présence) :
      jusqu'à 5 ans → 20 % · 5-10 → 25 % · 10-15 → 30 % · 15-20 → 35 % · >20 → 40 %
    """
    if anciennete_annees < 2 or moyenne_12_mois <= 0:
        return 0.0
    if anciennete_annees <= 5:
        taux = 0.20
    elif anciennete_annees <= 10:
        taux = 0.25
    elif anciennete_annees <= 15:
        taux = 0.30
    elif anciennete_annees <= 20:
        taux = 0.35
    else:
        taux = 0.40
    return round(moyenne_12_mois * taux * anciennete_annees, 0)


def prime_assiduite_aerien(salaire_mensuel_base: float, nb_absences: int = 0) -> float:
    """
    Prime d'assiduité — Convention Transports Aériens, Art. A.53.
    Taux : 3 % du salaire mensuel de base conventionnel de la catégorie ;
    abattement de 50 % pour une absence, 100 % pour deux absences dans le mois.
    """
    if salaire_mensuel_base <= 0:
        return 0.0
    nb_absences = max(0, int(nb_absences))
    prime = salaire_mensuel_base * 0.03
    if nb_absences >= 2:
        return 0.0
    if nb_absences == 1:
        prime *= 0.5
    return round(prime, 0)


def prime_panier_aerien(salaire_horaire_base: float, smig_horaire: float = 0.0) -> float:
    """
    Prime de panier — Convention Transports Aériens, Art. A.48.
    Égale à 1,5 × le salaire horaire de base de la catégorie, sans être inférieure
    à 4 × le SMIG horaire.
    """
    if salaire_horaire_base <= 0:
        return 0.0
    return round(max(salaire_horaire_base * 1.5, smig_horaire * 4), 0)



# ── Indemnités & primes déterministes — Convention Entreprises Industrielles ───
def prime_assiduite_industrie(nb_retards: int = 0, absence_injustifiee: bool = False) -> float:
    """
    Prime d'assiduité — Art. A.49.
    Base : 3 000 F/mois, quelle que soit la catégorie.
      • Absence injustifiée → suppression totale.
      • Chaque retard      → suppression d'un quart (¼) de la prime.
    """
    if absence_injustifiee:
        return 0.0
    montant = 3000.0 * (1 - 0.25 * max(0, int(nb_retards)))
    return round(max(0.0, montant), 0)


def indemnite_transport_industrie(jours_presence: int = 26) -> float:
    """
    Indemnité de transport — Art. A.55.
    60 F × 2 voyages × 26 j (matin) + idem (soir) = 6 240 F/mois,
    calculée au prorata des jours de présence (référence : 26 jours).
    """
    base_mensuelle = 60.0 * 2 * 26 + 60.0 * 2 * 26   # matin + soir = 6 240 F
    jp = max(0, min(int(jours_presence), 26))
    return round(base_mensuelle * jp / 26, 0)


def indemnite_logement_industrie(salaire_mensuel_categorie: float,
                                 hors_categorie: bool = False) -> float:
    """
    Indemnité d'aide au logement — Art. A.58 (travailleur déplacé de son lieu
    de recrutement). 25 % du salaire mensuel de la catégorie ; 12 % hors catégorie.
    """
    taux = 0.12 if hors_categorie else 0.25
    return round(max(0.0, salaire_mensuel_categorie) * taux, 0)


def indemnite_veuvage_industrie(brut_12_mois: float) -> float:
    """
    Indemnité (allocation) de veuvage — Art. A.56.
    Allocation unique = 1/24 du salaire brut des 12 derniers mois.
    Éligibilité (≥ 8 ans de présence, mariage ≥ 1 an avant le décès) à vérifier
    en amont par l'appelant.
    """
    return round(max(0.0, brut_12_mois) / 24.0, 0)


def indemnite_deplacement_industrie(salaire_horaire_min: float,
                                    repas: int = 1, couchage: bool = False) -> float:
    """
    Indemnité de déplacement — Art. A.48, multiple du salaire de base horaire
    minimal de la catégorie :
      • 4×  → 1 repas principal hors lieu d'emploi,
      • 6×  → 2 repas principaux,
      • 8×  → 2 repas principaux + couchage.
    """
    if couchage:
        mult = 8
    elif repas >= 2:
        mult = 6
    else:
        mult = 4
    return round(max(0.0, salaire_horaire_min) * mult, 0)


def permissions_familiales_btp(evenement: str) -> int:
    """
    Jours de permissions exceptionnelles — Convention BTP Art. A.41.
    Ces jours NE SONT PAS déduits du congé annuel.

    Événements reconnus :
      mariage_travailleur, mariage_enfant, mariage_frere_soeur,
      deces_conjoint_parent_enfant, deces_frere_soeur,
      deces_beau_parent, naissance_enfant, ceremonie_religieuse
    """
    BAREMES = {
        "mariage_travailleur":         4,
        "mariage_enfant":              2,
        "mariage_frere_soeur":         1,
        "deces_conjoint_parent_enfant":5,
        "deces_frere_soeur":           2,
        "deces_beau_parent":           2,
        "naissance_enfant":            3,
        "ceremonie_religieuse":        1,
    }
    return BAREMES.get(evenement, 0)


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES CONVENTION COMMERCE GABON
# (Convention Collective du Secteur Commerce — Libreville, 8 juin 1988)
# ══════════════════════════════════════════════════════════════════════════════

# ─── Grille de salaires conventionnelle COMMERCE ──────────────────────────────
# Source : Grille de salaire Convention Collective du Commerce (A.I.I. 9).
# Montants en FCFA. "mensuel" = salaire minimum mensuel ; "horaire" = taux horaire.
GRILLE_COMMERCE = [
    # code, libellé,                       mensuel,  horaire
    ("E1",  "Personnel d'exécution Cat 1",   98_500,   568.28),
    ("E2",  "Personnel d'exécution Cat 2",  102_600,   591.93),
    ("E3",  "Personnel d'exécution Cat 3",  105_300,   607.51),
    ("E4",  "Personnel d'exécution Cat 4",  109_800,   633.47),
    ("E5",  "Personnel d'exécution Cat 5",  115_500,   666.36),
    ("E6",  "Personnel d'exécution Cat 6",  123_600,   713.09),
    ("E7",  "Personnel d'exécution Cat 7",  144_000,   830.79),
    ("AM1", "Agent de maîtrise AM1",         182_800,  1_054.64),
    ("AM2", "Agent de maîtrise AM2",         212_900,  1_228.29),
    ("C1",  "Cadres C1",                     280_500,  1_618.30),
    ("C2",  "Cadres C2",                     358_800,  2_070.04),
    ("C3",  "Cadres C3",                     449_700,  2_594.47),
    ("C4",  "Cadres C4",                     562_100,  3_242.95),
]

# Taux heures supplémentaires COMMERCE — Art. A.38
COEFF_COMMERCE_JOUR_10    = 1.10    # jours ouvrables, 8 premières heures
COEFF_COMMERCE_JOUR_30    = 1.30    # jours ouvrables, à partir de la 9e heure
COEFF_COMMERCE_NUIT       = 1.70    # nuit (21h-6h), jours ouvrables
COEFF_COMMERCE_FERIE_JOUR = 1.40    # jours fériés / repos hebdo, de jour
COEFF_COMMERCE_FERIE_NUIT = 2.40    # jours fériés / repos hebdo, de nuit (+140%)

# Prime d'assiduité minimale — Art. A.49.1
PRIME_ASSIDUITE_COMMERCE_PCT = 0.015   # 1,5 % du salaire conventionnel de base


def calculer_prime_anciennete_commerce(salaire_base: float, anciennete_annees: int) -> float:
    """
    Prime d'ancienneté COMMERCE — Art. A.46.5.
    Attribuée après 2 ans de présence : 2 % du salaire de base conventionnel,
    majorée de 1 % par année supplémentaire (identique au barème BTP).
    """
    if anciennete_annees < 2 or salaire_base <= 0:
        return 0.0
    taux = min(0.02 + 0.01 * (anciennete_annees - 2), 0.30)
    return round(salaire_base * taux, 0)


def calculer_preavis_commerce(anciennete_annees: int) -> int:
    """
    Durée du préavis en jours — Convention COMMERCE Art. A.30.3.

    Barème :
      1 mois à 1 an  → 15 jours
      1 à 3 ans      → 1 mois  (30 j)
      3 à 5 ans      → 2 mois  (60 j)
      5 à 10 ans     → 3 mois  (90 j)
      10 à 15 ans    → 4 mois  (120 j)
      15 à 20 ans    → 5 mois  (150 j)
      20 à 25 ans    → 6 mois  (180 j)
      26 à 30 ans    → 6 mois + 10 j par année au-delà de 25 ans
      > 30 ans       → +15 j par année de présence au-delà de 30 ans
    """
    if anciennete_annees < 1:
        return 15
    elif anciennete_annees < 3:
        return 30
    elif anciennete_annees < 5:
        return 60
    elif anciennete_annees < 10:
        return 90
    elif anciennete_annees < 15:
        return 120
    elif anciennete_annees < 20:
        return 150
    elif anciennete_annees <= 25:
        return 180
    elif anciennete_annees <= 30:
        # 26→190, 27→200, ... (6 mois + 10 j/an au-delà de 25 ans)
        return 180 + (anciennete_annees - 25) * 10
    else:
        # base à 30 ans = 180 + 5*10 = 230 j, puis +15 j/an
        return 230 + (anciennete_annees - 30) * 15


def calculer_indemnite_services_rendus_commerce(
    moyenne_12_mois: float, anciennete_annees: int
) -> float:
    """
    Indemnité de services rendus — Convention COMMERCE Art. A.32.
    Départ retraite / décès / licenciement (hors faute lourde) après 2 ans continus.

    Base : moyenne mensuelle du salaire global des 12 derniers mois.
    Taux × nombre d'années de présence :
      2 à 5 ans   → 20 %/année
      5 à 10 ans  → 25 %/année
      10 à 20 ans → 30 %/année
      > 20 ans    → 35 %/année
    """
    if anciennete_annees < 2 or moyenne_12_mois <= 0:
        return 0.0
    if anciennete_annees <= 5:
        taux = 0.20
    elif anciennete_annees <= 10:
        taux = 0.25
    elif anciennete_annees <= 20:
        taux = 0.30
    else:
        taux = 0.35
    return round(moyenne_12_mois * taux * anciennete_annees, 0)


def permissions_familiales_commerce(evenement: str) -> int:
    """
    Permissions exceptionnelles pour événements familiaux — Art. A.41.
    Déductibles du congé dans la limite de 10 jours/an (barème identique au BTP).
    """
    BAREMES = {
        "mariage_travailleur":          4,
        "mariage_enfant":               2,
        "mariage_frere_soeur":          1,
        "deces_conjoint_parent_enfant": 5,
        "deces_frere_soeur":            2,
        "deces_beau_parent":            2,
        "naissance_enfant":             3,
        "ceremonie_religieuse":         1,
    }
    return BAREMES.get(evenement, 0)


def distribuer_heures_semaine_commerce(heures_par_jour: list,
                                       types_par_jour: list = None) -> dict:
    """
    Distribue les heures d'une semaine selon la convention COMMERCE — Art. A.38.

    Contrairement au BTP (seuils hebdomadaires), le COMMERCE raisonne PAR JOUR :
        Jour ouvrable : 8 premières heures → +10% ; à partir de la 9e → +30%
        Nuit (21h-6h) ouvrable             → +70%
        Jour férié / repos hebdo, de jour  → +40%
        Jour férié / repos hebdo, de nuit  → +140%

    heures_par_jour : liste de dict par jour :
        {"heures_normales": 8.0, "heures_sup_nuit": 0, "type_jour": "NORMAL"}
    """
    h_norm  = 0.0   # heures normales (≤ 8h/jour ouvrable)
    h_10    = 0.0   # +10% (1→8h ouvrable au-delà de la durée normale… ici 8 premières)
    h_30    = 0.0   # +30% (9e heure et + en jour ouvrable)
    h_nuit  = 0.0   # +70% (nuit ouvrable)
    h_ferie_jour = 0.0  # +40%
    h_ferie_nuit = 0.0  # +140%

    SEUIL_JOUR = 8.0

    for i, jour in enumerate(heures_par_jour):
        if isinstance(jour, dict):
            h   = float(jour.get("heures_normales", 0) or 0)
            hn  = float(jour.get("heures_sup_nuit", 0) or 0)
            tj  = (jour.get("type_jour", "NORMAL") or "NORMAL").upper()
        else:
            h  = float(jour or 0)
            hn = 0.0
            tj = (types_par_jour[i] if types_par_jour and i < len(types_par_jour) else "NORMAL").upper()

        if tj in ("FERIE", "DIMANCHE", "REPOS"):
            h_ferie_jour += h
            h_ferie_nuit += hn
        else:
            # Jour ouvrable : 8 premières heures à taux normal,
            # heures supplémentaires +10% sur les 8 premières heures sup,
            # +30% au-delà. Convention : les 8 premières h = normales.
            normales = min(h, SEUIL_JOUR)
            sup      = max(h - SEUIL_JOUR, 0)
            h_norm += normales
            # Les heures sup de jour : +10% jusqu'à la 8e heure sup, +30% ensuite
            h_10 += min(sup, SEUIL_JOUR)
            h_30 += max(sup - SEUIL_JOUR, 0)
            h_nuit += hn

    return {
        "total_heures":    round(h_norm + h_10 + h_30 + h_nuit + h_ferie_jour + h_ferie_nuit, 2),
        "heures_normales": round(h_norm, 2),
        "heures_sup_10":   round(h_10, 2),          # +10%
        "heures_sup_30":   round(h_30, 2),          # +30%
        "heures_sup_70":   round(h_nuit, 2),        # nuit ouvrable +70%
        "heures_sup_40":   round(h_ferie_jour, 2),  # férié/repos jour +40%
        "heures_sup_140":  round(h_ferie_nuit, 2),  # férié/repos nuit +140%
    }


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES CONVENTION PÉTROLE GABON
# (Convention Collective des professionnels du pétrole — SGEPP/GPP, 17 juin 1983 ;
#  stockage et distribution, hors transport et commerce de détail.)
# ══════════════════════════════════════════════════════════════════════════════

# ─── Grille conventionnelle des salaires PÉTROLE — Annexe n°2 ──────────────────
# ⚠️ Les montants de l'Annexe n°2 datent de 1983 : les premières catégories sont
# DÉSORMAIS INFÉRIEURES AU SMIG en vigueur. Cette grille fournit la STRUCTURE de
# classification (catégories) ; les montants doivent impérativement être actualisés
# par l'entreprise et ne peuvent en aucun cas descendre sous le SMIG légal.
# Catégories : A→I (employés/ouvriers), AMI→AMS (maîtrise), CP0→HC (cadres).
GRILLE_PETROLE = [
    # code,  libellé,                              mensuel 1983 (à actualiser)
    ("A",   "Employé/Ouvrier catégorie A",            51_212),
    ("B",   "Employé/Ouvrier catégorie B",            55_138),
    ("C",   "Employé/Ouvrier catégorie C",            56_203),
    ("D",   "Employé/Ouvrier catégorie D",            63_000),
    ("E",   "Employé/Ouvrier catégorie E",            68_000),
    ("F",   "Employé/Ouvrier catégorie F",            78_000),
    ("G",   "Employé/Ouvrier catégorie G",            91_000),
    ("H",   "Employé/Ouvrier catégorie H",            97_000),
    ("I",   "Employé/Ouvrier catégorie I",           124_720),
    ("AMI", "Agent de maîtrise I",                   133_704),
    ("AMII","Agent de maîtrise II",                  160_864),
    ("AMIII","Agent de maîtrise III",                193_646),
    ("AMS", "Agent de maîtrise supérieur",           253_764),
    ("CP0", "Cadre CP0",                             270_000),
    ("CP1", "Cadre CP1",                             305_000),
    ("CP2", "Cadre CP2",                             350_000),
    ("CP3", "Cadre CP3",                             400_000),
    ("CP4", "Cadre CP4",                             500_000),
    ("CP5", "Cadre CP5",                             650_000),
    ("CPS", "Cadre supérieur",                       900_000),
    ("HC",  "Hors catégorie",                      1_200_000),
]

# Primes forfaitaires PÉTROLE (montants de référence 1983 — à actualiser)
PRIME_ASSIDUITE_PETROLE        = 5_000    # Art. 49 — forfait mensuel (vs 1,5 % au Commerce)
PRIME_NAISSANCE_PETROLE        = 10_000   # Art. 58 — par enfant
PRIME_OCCASIONNELLE_PETROLE_PCT = 0.15    # Art. 56 — 15 % du salaire horaire (pénible/dangereux)


def calculer_prime_anciennete_petrole(salaire_base: float, anciennete_annees: int) -> float:
    """
    Prime d'ancienneté PÉTROLE — Art. 46.5.
    Attribuée après 2 ans de présence : **5 %** du salaire de base conventionnel,
    majorée de 1 % par année supplémentaire.

    ⚠️ Diffère du BTP/Commerce (qui démarrent à 2 % à 2 ans).
        2 ans → 5 %   |   3 ans → 6 %   |   10 ans → 13 %
    """
    if anciennete_annees < 2 or salaire_base <= 0:
        return 0.0
    taux = min(0.05 + 0.01 * (anciennete_annees - 2), 0.30)  # plafond 30 %
    return round(salaire_base * taux, 0)


def calculer_indemnite_services_rendus_petrole(
    moyenne_12_mois: float, anciennete_annees: int, min_anciennete: float = 1.0
) -> float:
    """
    Indemnité de services rendus — Convention PÉTROLE Art. 32.
    Due au travailleur licencié (hors faute lourde) ou partant à la retraite,
    sous condition d'ancienneté minimale : 1 an (ouvrier/employé), 2 ans
    (agent de maîtrise/cadre) — paramètre `min_anciennete`.

    Base : moyenne mensuelle du salaire global des 12 derniers mois.
    Taux × nombre d'années de présence continue :
        0 à 5 ans      → 20 %/année
        6 à 10 ans     → 25 %/année
        11 à 15 ans    → 30 %/année
        au-delà de 16 ans → 40 %/année
    """
    if anciennete_annees < min_anciennete or moyenne_12_mois <= 0:
        return 0.0
    if anciennete_annees <= 5:
        taux = 0.20
    elif anciennete_annees <= 10:
        taux = 0.25
    elif anciennete_annees <= 15:
        taux = 0.30
    else:
        taux = 0.40
    return round(moyenne_12_mois * taux * anciennete_annees, 0)


def permissions_familiales_petrole(evenement: str) -> int:
    """
    Permissions exceptionnelles pour événements familiaux — Art. 41.
    Non déductibles du congé dans la limite de 10 jours/an.
    Barème identique à celui du BTP/Commerce.
    """
    BAREMES = {
        "mariage_travailleur":          4,
        "mariage_enfant":               2,
        "mariage_frere_soeur":          1,
        "deces_conjoint_parent_enfant": 5,
        "deces_frere_soeur":            2,
        "deces_beau_parent":            2,
        "naissance_enfant":             3,
        "ceremonie_religieuse":         1,
    }
    return BAREMES.get(evenement, 0)


# ══════════════════════════════════════════════════════════════════════════════
# DISPATCHER PAR CONVENTION
# Permet à l'application d'appliquer le bon barème selon Tenant.convention.
# Valeurs possibles : "BTP" | "COMMERCE" | "AUCUNE"
# ══════════════════════════════════════════════════════════════════════════════

CONVENTIONS_DISPONIBLES = {
    "AUCUNE":   "Aucune convention (Code du travail seul)",
    "BTP":      "Convention Collective BTP",
    "COMMERCE": "Convention Collective du Commerce",
    "PETROLE":  "Convention Collective des professionnels du pétrole",
    "INDUSTRIE": "Convention Collective des Entreprises Industrielles",
    "AERIEN":    "Convention Collective des Compagnies de Transports Aériens",
    "HOTELLERIE": "Convention Collective Hôtellerie – Restauration – Débits de Boissons",
    "BOIS":       "Convention Collective des Industries du Bois, Sciages et Placages",
    "MINIER":     "Convention Collective des Entreprises Minières et Assimilées",
}


def _conv(convention) -> str:
    c = (convention or "AUCUNE").upper()
    return c if c in CONVENTIONS_DISPONIBLES else "AUCUNE"


def prime_anciennete(convention, salaire_base: float, anciennete_annees: int) -> float:
    """Prime d'ancienneté selon la convention (BTP/COMMERCE : 2% + 1%/an après 2 ans ;
    PÉTROLE : 5% + 1%/an après 2 ans)."""
    c = _conv(convention)
    if c == "PETROLE":
        return calculer_prime_anciennete_petrole(salaire_base, anciennete_annees)
    if c == "HYDROCARBURES":
        from convention_hydrocarbures import calculer_prime_anciennete_hydrocarbures
        return calculer_prime_anciennete_hydrocarbures(salaire_base, anciennete_annees)
    if c == "COMMERCE":
        return calculer_prime_anciennete_commerce(salaire_base, anciennete_annees)
    if c == "HOTELLERIE":
        from convention_hotellerie import calculer_prime_anciennete_hotellerie
        return calculer_prime_anciennete_hotellerie(salaire_base, anciennete_annees)
    if c == "BOIS":
        from convention_bois import calculer_prime_anciennete_bois
        return calculer_prime_anciennete_bois(salaire_base, anciennete_annees)
    if c == "MINIER":
        from convention_minier import calculer_prime_anciennete_minier
        return calculer_prime_anciennete_minier(salaire_base, anciennete_annees)
    if c in ("BTP", "INDUSTRIE", "AERIEN"):
        # A.46/A.47 identique : 2 % après 2 ans, +1 %/an.
        return calculer_prime_anciennete_btp(salaire_base, anciennete_annees)
    return 0.0


def _prime_anciennete_convention(convention, salaire_base: float,
                                 anciennete_annees: int) -> float:
    """Alias de prime_anciennete() utilisable depuis calculer_bulletin().

    Nécessaire car, dans calculer_bulletin, la variable locale `prime_anciennete`
    masque la fonction du même nom : l'appeler directement lèverait un TypeError.
    """
    return prime_anciennete(convention, salaire_base, anciennete_annees)


def calculer_preavis_code(anciennete_annees: int) -> int:
    """
    Durée du préavis en jours — Code du travail 2021, Art. 82.
    Barème légal applicable à défaut de convention plus favorable :
      jusqu'à 1 an : 15 jours
      1 à 3 ans    : 1 mois  (30 j)
      3 à 5 ans    : 2 mois  (60 j)
      5 à 10 ans   : 3 mois  (90 j)
      10 à 15 ans  : 4 mois  (120 j)
      15 à 20 ans  : 5 mois  (150 j)
      20 à 30 ans  : 6 mois  (180 j)
      > 30 ans     : +10 jours par année de présence
    (Conversion mois→jours à 30 j ; le Code raisonne en mois.)
    """
    if anciennete_annees < 1:
        return 15
    elif anciennete_annees < 3:
        return 30
    elif anciennete_annees < 5:
        return 60
    elif anciennete_annees < 10:
        return 90
    elif anciennete_annees < 15:
        return 120
    elif anciennete_annees < 20:
        return 150
    elif anciennete_annees <= 30:
        return 180
    else:
        return 180 + (anciennete_annees - 30) * 10


def preavis_jours(convention, anciennete_annees: int, cadre: bool = False) -> int:
    """
    Durée du préavis (jours) selon la convention applicable, en retenant
    toujours la durée la plus favorable au salarié (Code Art. 80 & 82).
    Le paramètre `cadre` distingue, pour les conventions à double barème
    (ex. Transports Aériens), le personnel de maîtrise/cadres de l'exécution.
    """
    c = _conv(convention)
    legal = calculer_preavis_code(anciennete_annees)
    if c == "COMMERCE":
        return max(calculer_preavis_commerce(anciennete_annees), legal)
    if c == "BTP":
        return max(calculer_preavis_btp(anciennete_annees), legal)
    if c == "INDUSTRIE":
        return max(calculer_preavis_industrie(anciennete_annees), legal)
    if c == "AERIEN":
        return max(calculer_preavis_aerien(anciennete_annees, cadre=cadre), legal)
    if c == "HOTELLERIE":
        from convention_hotellerie import calculer_preavis_hotellerie
        return max(calculer_preavis_hotellerie(anciennete_annees), legal)
    if c == "BOIS":
        from convention_bois import calculer_preavis_bois
        return max(calculer_preavis_bois(anciennete_annees), legal)
    if c == "MINIER":
        from convention_minier import calculer_preavis_minier
        return max(calculer_preavis_minier(anciennete_annees, cadre=cadre), legal)
    if c == "HYDROCARBURES":
        # Art. 30.5/30.6 : barème identique au Code du travail.
        return calculer_preavis_code(anciennete_annees)
    # PÉTROLE (Art. 30.3 : renvoi au Code du travail) et AUCUNE : barème légal.
    return legal


def indemnite_services_rendus(convention, moyenne_12_mois: float, anciennete_annees: int) -> float:
    """Indemnité de services rendus (Art. A.32) selon la convention."""
    c = _conv(convention)
    if c == "PETROLE":
        return calculer_indemnite_services_rendus_petrole(moyenne_12_mois, anciennete_annees)
    if c == "COMMERCE":
        return calculer_indemnite_services_rendus_commerce(moyenne_12_mois, anciennete_annees)
    if c == "INDUSTRIE":
        return calculer_indemnite_services_rendus_industrie(moyenne_12_mois, anciennete_annees)
    if c == "AERIEN":
        return calculer_indemnite_services_rendus_aerien(moyenne_12_mois, anciennete_annees)
    if c == "BTP":
        return calculer_indemnite_services_rendus_btp(moyenne_12_mois, anciennete_annees)
    if c == "HOTELLERIE":
        from convention_hotellerie import calculer_indemnite_licenciement_hotellerie
        return calculer_indemnite_licenciement_hotellerie(moyenne_12_mois, anciennete_annees)
    if c == "BOIS":
        from convention_bois import calculer_indemnite_services_rendus_bois
        return calculer_indemnite_services_rendus_bois(moyenne_12_mois, anciennete_annees)
    if c == "MINIER":
        from convention_minier import calculer_indemnite_services_rendus_minier
        return calculer_indemnite_services_rendus_minier(moyenne_12_mois, anciennete_annees)
    return 0.0


def indemnite_licenciement(moyenne_12_mois: float, anciennete_annees: float) -> float:
    """
    Indemnité de licenciement — Code du travail 2021, Art. 87 & 90.

    Due SANS condition d'ancienneté à tout salarié licencié pour un motif
    autre que la faute lourde (hors période d'essai).
    Minimum légal : 20 % de la moyenne mensuelle du salaire global des
    12 derniers mois, par année de présence continue (fractions comprises,
    Art. 90).
    """
    if moyenne_12_mois <= 0 or anciennete_annees <= 0:
        return 0.0
    return fcfa(moyenne_12_mois * 0.20 * anciennete_annees, 0)


def indemnite_rupture(convention, cause, moyenne_12_mois: float,
                      anciennete_annees: float) -> dict:
    """
    Aiguille vers la bonne indemnité de rupture selon la CAUSE de cessation,
    conformément au Code du travail 2021 (Art. 87 à 90).

    cause ∈ {"LICENCIEMENT", "RETRAITE", "DECES", "DEMISSION", "FAUTE_LOURDE"}

    Règles appliquées :
      • Non-cumul (Art. 89) : licenciement OU services rendus, jamais les deux.
      • Faveur (Art. 90)    : le barème conventionnel (BTP/Commerce) s'applique
                              s'il est plus avantageux que le minimum légal 20 %/an.
      • Licenciement (Art. 87) : AUCUNE condition d'ancienneté.
      • Services rendus (Art. 88) : retraite, décès, ou démission >= 2 ans.

    Returns: {"type": str | None, "montant": float}
    """
    cause = (cause or "").upper()
    if cause == "FAUTE_LOURDE" or moyenne_12_mois <= 0 or anciennete_annees <= 0:
        return {"type": None, "montant": 0.0}

    # Indemnité de services rendus : retraite, décès, ou démission >= 2 ans
    if cause in ("RETRAITE", "DECES") or (cause == "DEMISSION" and anciennete_annees >= 2):
        montant = indemnite_services_rendus(convention, moyenne_12_mois, anciennete_annees)
        return {"type": "SERVICES_RENDUS", "montant": montant}

    # Indemnité de licenciement : tout licenciement hors faute lourde, SANS minimum.
    if cause == "LICENCIEMENT":
        legal = indemnite_licenciement(moyenne_12_mois, anciennete_annees)
        conv  = indemnite_services_rendus(convention, moyenne_12_mois, anciennete_annees)
        # On retient le plus favorable au salarié (Art. 90).
        return {"type": "LICENCIEMENT", "montant": max(legal, conv)}

    # Démission < 2 ans : aucune indemnité de rupture.
    return {"type": None, "montant": 0.0}


def permissions_familiales(convention, evenement: str) -> int:
    """Jours de permission exceptionnelle (Art. A.41) selon la convention."""
    c = _conv(convention)
    if c == "PETROLE":
        return permissions_familiales_petrole(evenement)
    if c in ("BTP", "INDUSTRIE", "AERIEN"):
        # A.41/A.42 identique : 4/2/1 (mariages), 5/2/2 (décès), 3 (naissance), 1 (cérémonie).
        return permissions_familiales_btp(evenement)
    # COMMERCE et défaut : même barème
    return permissions_familiales_commerce(evenement)


def distribuer_heures_semaine(convention, heures_par_jour: list, types_par_jour: list = None,
                              seuil_normales: float = 40.0) -> dict:
    """Distribution des heures hebdomadaires selon la convention (BTP vs COMMERCE).

    seuil_normales : seuil hebdomadaire de déclenchement des heures sup (défaut 40h).
    """
    c = _conv(convention)
    if c in ("PETROLE", "INDUSTRIE", "AERIEN"):
        return distribuer_heures_semaine_petrole(heures_par_jour, types_par_jour,
                                                 seuil_normales=seuil_normales)
    if c == "COMMERCE":
        return distribuer_heures_semaine_commerce(heures_par_jour, types_par_jour)
    return distribuer_heures_semaine_btp(heures_par_jour, types_par_jour, seuil_normales=seuil_normales)


# ──────────────────────────────────────────────────────────────────────────────
# GRILLE DE SALAIRES — Convention Transports Aériens (Annexe I / "nouvelle grille")
# Catégories du personnel au sol (11) et montants mensuels par échelon.
#
# ⚠️  Les MONTANTS ci-dessous sont une TRANSCRIPTION d'un document scanné dégradé
#     (1987) et doivent être VÉRIFIÉS par l'utilisateur avant tout usage en paie.
#     Ils ne servent qu'à PRÉ-REMPLIR l'écran d'édition de la grille ; le calcul
#     de paie n'utilise que la grille enregistrée (donc validée) par le tenant.
# ──────────────────────────────────────────────────────────────────────────────
GRILLE_CATEGORIES_AERIEN = [
    ("EI",   "Manœuvres"),
    ("EII",  "Employés et Ouvriers"),
    ("EIII", "Employés et Ouvriers spécialisés"),
    ("EIV",  "Employés et Ouvriers professionnels"),
    ("EV",   "Employés qualifiés"),
    ("EVI",  "Employés très qualifiés I"),
    ("EVII", "Employés hautement qualifiés"),
    ("MI",   "Maîtrise / Techniciens"),
    ("MII",  "Haute maîtrise"),
    ("CI",   "Cadres"),
    ("CII",  "Cadres supérieurs"),
]

# code → liste des montants mensuels par échelon (échelon 1 … n). À VÉRIFIER.
GRILLE_AERIEN_SEED = {
    "EI":   [101526, 110970, 120414, 129859, 139303, 151108, 162914, 174719, 186525, 198330],
    "EII":  [113331, 125137, 127672, 151108, 160552, 174719, 188886, 203052, 217219, 231382],
    "EIII": [146387, 157022, 167636, 181802, 195969, 212496, 231382, 253815, 271523, 295132],
    "EIV":  [181802, 203052, 224302, 247913, 269160, 285687, 311659, 337631, 363602, 389575],
    "EV":   [242008, 253815, 283326, 319923, 345895, 375408, 404922, 434434, 463947, 493463],
    "EVI":  [312840, 341173, 369505, 397839, 424990, 458045, 509447, 524156, 557210, 590128],
    "EVII": [394296, 426172, 458045, 489920, 509447, 557210, 592626, 628043, 663457, 698874],
    "MI":   [512601, 559815, 586794, 647496, 741923, 782391, 843094, 917286, 977989, 1052181],
    "MII":  [809370, 843094, 876818, 917286, 964500, 1011713, 1058926, 1075539, 1180332, 1281503],
    "CI":   [1103425, 1167308, 1238998, 1306688, 1405415, 1440260, 1538988, 1672560, 0, 0],
    "CII":  [1335725, 1469298, 1602870, 1736443, 1870015, 0, 0, 0, 0, 0],
}


def grille_salaire_aerien_seed():
    """Renvoie la grille aérienne pré-remplie sous forme {code: {"1": montant, …}}
    (montants non nuls uniquement). Sert à initialiser l'écran d'édition."""
    out = {}
    for code, montants in GRILLE_AERIEN_SEED.items():
        out[code] = {str(i + 1): m for i, m in enumerate(montants) if m}
    return out
