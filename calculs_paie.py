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

# ─── CONSTANTES BTP ───────────────────────────────────────────────────────────
H_NORMALES_MENSUEL   = 173.33   # heures normales / mois (40h × 43,33)
H_SUP_STRUCT_10      = 17.33    # heures +10% structurelles / mois (4h × 4,333)
H_SUP_STRUCT_30      = 17.33    # heures +30% structurelles / mois (4h × 4,333)
COEFF_SUP_10         = 1.10
COEFF_SUP_30         = 1.30
COEFF_SUP_40         = 1.40     # nuit / dimanche
COEFF_SUP_70         = 1.70     # jours fériés

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


def calculer_taux_horaire(salaire_base: float) -> float:
    """Taux horaire de base = salaire_base / 173,33."""
    if salaire_base <= 0:
        return 0.0
    return round(salaire_base / H_NORMALES_MENSUEL, 4)


def calculer_heures_sup_btp(salaire_base: float,
                              h10: float = None, h30: float = None,
                              h40: float = 0.0,  h70: float = 0.0) -> dict:
    """
    Calcule les montants des heures supplémentaires selon la logique BTP.

    Si h10 et h30 sont None → utilise les valeurs structurelles BTP (17,33h).
    Retourne un dict avec taux_horaire, montants et descriptifs pour le bulletin.
    """
    th = calculer_taux_horaire(salaire_base)
    h10 = H_SUP_STRUCT_10 if h10 is None else float(h10)
    h30 = H_SUP_STRUCT_30 if h30 is None else float(h30)
    h40 = float(h40)
    h70 = float(h70)

    # Taux majorés
    taux_10 = round(th * COEFF_SUP_10, 4)
    taux_30 = round(th * COEFF_SUP_30, 4)
    taux_40 = round(th * COEFF_SUP_40, 4)
    taux_70 = round(th * COEFF_SUP_70, 4)

    # Montants
    montant_10 = round(h10 * taux_10, 2) if h10 > 0 else 0.0
    montant_30 = round(h30 * taux_30, 2) if h30 > 0 else 0.0
    montant_40 = round(h40 * taux_40, 2) if h40 > 0 else 0.0
    montant_70 = round(h70 * taux_70, 2) if h70 > 0 else 0.0

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
        # Heures +40% (nuit/dimanche)
        "h40":             h40,
        "taux_40":         taux_40,
        "montant_40":      montant_40,
        # Heures +70% (jours fériés)
        "h70":             h70,
        "taux_70":         taux_70,
        "montant_70":      montant_70,
        # Total heures sup
        "total_sup":       round(montant_10 + montant_30 + montant_40 + montant_70, 2),
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
    return round(impot_par_part * nb_parts, 2)


def calculer_bulletin(donnees: dict, nb_parts: float = 1.0) -> dict:
    """
    Calcule un bulletin de paie complet selon la réglementation gabonaise.
    Les heures sup sont attendues en MONTANTS (déjà calculés via calculer_heures_sup_btp).
    """
    def g(key):
        val = donnees.get(key, 0)
        return float(val) if val else 0.0

    # ── 1. ÉLÉMENTS BRUTS ───────────────────────────────────────────────────
    salaire_base      = g("salaire_base")
    heures_sup_10     = g("heures_sup_10")
    heures_sup_30     = g("heures_sup_30")
    heures_sup_40     = g("heures_sup_40")
    heures_sup_70     = g("heures_sup_70")
    absences          = g("absences")
    sursalaire        = g("sursalaire")
    prime_caisse      = g("prime_caisse")
    carburant         = g("carburant")
    prime_anciennete  = g("prime_anciennete")
    prime_rendement   = g("prime_rendement")
    prime_assiduité   = g("prime_assiduité")
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

    # Taux horaire et détails heures sup (pour le retour enrichi)
    th = calculer_taux_horaire(salaire_base)

    # ── 2. SALAIRE BRUT ─────────────────────────────────────────────────────
    salaire_brut = (
        salaire_base
        + heures_sup_10 + heures_sup_30 + heures_sup_40 + heures_sup_70
        - absences
        + sursalaire
        + prime_caisse + carburant + prime_anciennete
        + indem_logement + indem_domesticite + indem_eau_electricite + indem_nourriture
        + prime_rendement + prime_assiduité + prime_qualite + prime_performance
        + prime_transport + prime_responsabilite
        + allocations_conge
        + indem_compensatrice_conge + indem_services_rendus
        + indem_compensatrice_preavis + indem_licenciement
    )

    # ── 3. CNSS ─────────────────────────────────────────────────────────────
    transport_exo_cnss = min(prime_transport, TRANSPORT_EXONERATION_CNSS)
    base_cnss = min(salaire_brut - transport_exo_cnss, CNSS_PLAFOND)
    base_cnss = max(base_cnss, 0)
    cnss_salarie   = round(base_cnss * CNSS_TAUX_SALARIE, 2)
    cnss_patronale = round(base_cnss * CNSS_TAUX_PATRONAL, 2)

    # ── 4. CNAMGS ────────────────────────────────────────────────────────────
    transport_exo_cnamgs = min(prime_transport, TRANSPORT_EXONERATION_IRPP)
    logement_imposable = min(indem_logement, salaire_brut * LOGEMENT_PLAFOND_PCT, LOGEMENT_PLAFOND_MAX)
    base_cnamgs = min(
        salaire_brut - transport_exo_cnamgs - indem_logement + logement_imposable - prime_qualite,
        CNAMGS_PLAFOND
    )
    base_cnamgs = max(base_cnamgs, 0)
    cnamgs_salarie   = round(base_cnamgs * CNAMGS_TAUX_SALARIE, 2)
    cnamgs_patronale = round(base_cnamgs * CNAMGS_TAUX_PATRONAL, 2)

    # ── 5. FNH ──────────────────────────────────────────────────────────────
    base_fnh = min(max(base_cnss - indem_logement, 0), FNH_PLAFOND)
    fnh = round(base_fnh * FNH_TAUX, 2)

    # ── 6. CFP ──────────────────────────────────────────────────────────────
    base_cfp = max(base_cnss - indem_logement, 0)
    cfp = round(base_cfp * CFP_TAUX, 2)

    # ── 7. TCS ──────────────────────────────────────────────────────────────
    base_tcs = (
        salaire_brut
        - prime_qualite - prime_rendement - prime_performance
        - transport_exo_cnamgs - indem_logement
        - cnss_salarie - cnamgs_salarie
        + (indem_domesticite + indem_eau_electricite + indem_nourriture)
    )
    base_tcs_imposable = max(base_tcs - TCS_EXONERATION, 0)
    tcs = round(base_tcs_imposable * TCS_TAUX, 2)

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
        "heures_sup_40":               round(heures_sup_40, 2),
        "heures_sup_70":               round(heures_sup_70, 2),
        # Infos calcul heures sup (pour affichage bulletin)
        "taux_horaire_base":           round(th, 4),
        "taux_horaire_10":             round(th * COEFF_SUP_10, 4),
        "taux_horaire_30":             round(th * COEFF_SUP_30, 4),
        "taux_horaire_40":             round(th * COEFF_SUP_40, 4),
        "taux_horaire_70":             round(th * COEFF_SUP_70, 4),
        "h_normales_mensuel":          H_NORMALES_MENSUEL,
        "h_sup_struct_10":             H_SUP_STRUCT_10,
        "h_sup_struct_30":             H_SUP_STRUCT_30,
        # Tous les autres éléments
        "absences":                    round(absences, 2),
        "sursalaire":                  round(sursalaire, 2),
        "prime_caisse":                round(prime_caisse, 2),
        "carburant":                   round(carburant, 2),
        "prime_anciennete":            round(prime_anciennete, 2),
        "indem_logement":              round(indem_logement, 2),
        "indem_domesticite":           round(indem_domesticite, 2),
        "indem_eau_electricite":       round(indem_eau_electricite, 2),
        "indem_nourriture":            round(indem_nourriture, 2),
        "prime_rendement":             round(prime_rendement, 2),
        "prime_assiduité":             round(prime_assiduité, 2),
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
