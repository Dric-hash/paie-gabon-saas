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


from decimal import Decimal, ROUND_HALF_UP


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
    cnss_salarie   = fcfa(base_cnss * CNSS_TAUX_SALARIE)
    cnss_patronale = fcfa(base_cnss * CNSS_TAUX_PATRONAL)

    # ── 4. CNAMGS ────────────────────────────────────────────────────────────
    transport_exo_cnamgs = min(prime_transport, TRANSPORT_EXONERATION_IRPP)
    logement_imposable = min(indem_logement, salaire_brut * LOGEMENT_PLAFOND_PCT, LOGEMENT_PLAFOND_MAX)
    base_cnamgs = min(
        salaire_brut - transport_exo_cnamgs - indem_logement + logement_imposable - prime_qualite,
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


def pointage_vers_jours(pointages):
    """
    Adaptateur : convertit des enregistrements ORM `Pointage` en liste de dicts
    pour `ventiler_heures_mois_btp`, en lisant chaque ligne indépendamment.

    On RECONSTRUIT les heures réellement travaillées en sommant toutes les
    colonnes (heures_normales + sup 10/30/40/70), car la ventilation par jour
    déjà appliquée a pu vider `heures_normales` (ex. un férié travaillé range
    ses 8h de base dans heures_sup_70). On laisse ensuite l'algorithme
    hebdomadaire reclasser correctement.

    Règles de mapping :
      - type_jour CHOME_PAYE / CHOME_RECUPERABLE → férié chômé (8h normales)
      - type_jour FERIE                          → férié travaillé (+70%)
      - dimanche (détecté par la date) travaillé  → +70%
      - jour ordinaire                           → heures de jour + nuit (heures_sup_40)
    """
    jours = []
    for p in pointages:
        d = getattr(p, "date_pointage", None)
        if d is None:
            continue
        type_jour = (getattr(p, "type_jour", "") or "").upper()
        nuit = float(getattr(p, "heures_sup_40", 0) or 0)
        raw = (float(getattr(p, "heures_normales", 0) or 0)
               + float(getattr(p, "heures_sup_10", 0) or 0)
               + float(getattr(p, "heures_sup_30", 0) or 0)
               + nuit
               + float(getattr(p, "heures_sup_70", 0) or 0))
        present = bool(getattr(p, "present", True)) and not bool(getattr(p, "absent", False))

        if type_jour in ("CHOME_PAYE", "CHOME_RECUPERABLE"):
            jours.append({"date": d, "heures": 0.0, "heures_nuit": 0.0,
                          "ferie": True, "present": False})
        elif type_jour == "FERIE":
            jours.append({"date": d, "heures": raw, "heures_nuit": 0.0,
                          "ferie": True, "present": present})
        elif hasattr(d, "weekday") and d.weekday() == 6:
            # Dimanche : l'intégralité bascule en +70% (géré par l'algorithme)
            jours.append({"date": d, "heures": raw, "heures_nuit": 0.0,
                          "ferie": False, "present": present})
        else:
            # Jour ordinaire : on isole la nuit (+40%), le reste alimente le cumul hebdo
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
# DISPATCHER PAR CONVENTION
# Permet à l'application d'appliquer le bon barème selon Tenant.convention.
# Valeurs possibles : "BTP" | "COMMERCE" | "AUCUNE"
# ══════════════════════════════════════════════════════════════════════════════

CONVENTIONS_DISPONIBLES = {
    "AUCUNE":   "Aucune convention (Code du travail seul)",
    "BTP":      "Convention Collective BTP",
    "COMMERCE": "Convention Collective du Commerce",
}


def _conv(convention) -> str:
    c = (convention or "AUCUNE").upper()
    return c if c in CONVENTIONS_DISPONIBLES else "AUCUNE"


def prime_anciennete(convention, salaire_base: float, anciennete_annees: int) -> float:
    """Prime d'ancienneté selon la convention (BTP/COMMERCE : 2% + 1%/an après 2 ans)."""
    c = _conv(convention)
    if c == "COMMERCE":
        return calculer_prime_anciennete_commerce(salaire_base, anciennete_annees)
    if c == "BTP":
        return calculer_prime_anciennete_btp(salaire_base, anciennete_annees)
    return 0.0


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


def preavis_jours(convention, anciennete_annees: int) -> int:
    """
    Durée du préavis (jours) selon la convention applicable, en retenant
    toujours la durée la plus favorable au salarié (Code Art. 80 & 82).
    """
    c = _conv(convention)
    legal = calculer_preavis_code(anciennete_annees)
    if c == "COMMERCE":
        return max(calculer_preavis_commerce(anciennete_annees), legal)
    if c == "BTP":
        return max(calculer_preavis_btp(anciennete_annees), legal)
    # Aucune convention : barème légal du Code du travail.
    return legal


def indemnite_services_rendus(convention, moyenne_12_mois: float, anciennete_annees: int) -> float:
    """Indemnité de services rendus (Art. A.32) selon la convention."""
    c = _conv(convention)
    if c == "COMMERCE":
        return calculer_indemnite_services_rendus_commerce(moyenne_12_mois, anciennete_annees)
    if c == "BTP":
        return calculer_indemnite_services_rendus_btp(moyenne_12_mois, anciennete_annees)
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
    if c == "BTP":
        return permissions_familiales_btp(evenement)
    # COMMERCE et défaut : même barème
    return permissions_familiales_commerce(evenement)


def distribuer_heures_semaine(convention, heures_par_jour: list, types_par_jour: list = None,
                              seuil_normales: float = 40.0) -> dict:
    """Distribution des heures hebdomadaires selon la convention (BTP vs COMMERCE).

    seuil_normales : seuil hebdomadaire de déclenchement des heures sup (défaut 40h).
    """
    c = _conv(convention)
    if c == "COMMERCE":
        return distribuer_heures_semaine_commerce(heures_par_jour, types_par_jour)
    return distribuer_heures_semaine_btp(heures_par_jour, types_par_jour, seuil_normales=seuil_normales)
