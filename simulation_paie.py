"""
simulation_paie.py — Simulation de paie avancée pour PaieGabon
===============================================================
Trois modes de simulation :

  1. COMPARAISON DE SCÉNARIOS
     Comparer 2 à 3 configurations (avec/sans prime, avec/sans heures sup)
     → Tableau côte à côte avec les différences

  2. SIMULATION INVERSE (net cible → brut nécessaire)
     À partir d'un net souhaité, calculer le brut brut à fixer
     et les charges patronales

  3. SIMULATION D'AUGMENTATION
     Impact d'une augmentation de salaire sur :
       - le coût employeur
       - les cotisations
       - le net salarié
       - le taux de charge global
"""

from calculs_paie import calculer_bulletin


# ═══════════════════════════════════════════════════════════════════════════════
# 1. COMPARAISON DE SCÉNARIOS
# ═══════════════════════════════════════════════════════════════════════════════

def comparer_scenarios(scenarios: list, nb_parts: float = 1.0) -> dict:
    """
    Compare jusqu'à 3 scénarios de paie côte à côte.

    Args:
        scenarios : liste de dicts, chacun ayant les clés de calculer_bulletin
                    + un champ "label" (nom du scénario)
        nb_parts  : nombre de parts IRPP

    Returns:
        dict avec resultats, differences, recommandation
    """
    if not scenarios:
        return {"error": "Aucun scénario fourni"}

    resultats = []
    for sc in scenarios[:3]:  # max 3 scénarios
        label   = sc.pop("label", f"Scénario {len(resultats)+1}")
        donnees = {k: v for k, v in sc.items()}
        try:
            res = calculer_bulletin(donnees, nb_parts=nb_parts)
            res["label"]         = label
            res["scenario_input"]= sc
            res["cout_employeur"]= (
                res.get("salaire_brut", 0)
                + res.get("cnss_patronale", 0)
                + res.get("cnamgs_patronale", 0)
                + res.get("fnh", 0)
                + res.get("cfp", 0)
                + res.get("tcs", 0)
            )
            resultats.append(res)
        except Exception as e:
            resultats.append({"label": label, "error": str(e)})

    if len(resultats) < 2:
        return {"resultats": resultats, "differences": {}}

    # Calculer les différences entre scénario 1 et les autres
    ref = resultats[0]
    differences = []
    for r in resultats[1:]:
        if "error" in r:
            continue
        diff = {
            "label":          f"{ref['label']} → {r['label']}",
            "net_delta":      round(r.get("net_a_payer",0)    - ref.get("net_a_payer",0)),
            "brut_delta":     round(r.get("salaire_brut",0)   - ref.get("salaire_brut",0)),
            "cout_delta":     round(r.get("cout_employeur",0) - ref.get("cout_employeur",0)),
            "irpp_delta":     round(r.get("irpp",0)           - ref.get("irpp",0)),
            "cnss_delta":     round(r.get("cnss_salarie",0)   - ref.get("cnss_salarie",0)),
        }
        differences.append(diff)

    # Recommandation automatique (scénario avec meilleur net/coût)
    valid = [r for r in resultats if "error" not in r]
    if valid:
        best_net  = max(valid, key=lambda r: r.get("net_a_payer", 0))
        best_cout = min(valid, key=lambda r: r.get("cout_employeur", 0))
        recommandation = {
            "meilleur_net":  best_net["label"],
            "moindre_cout":  best_cout["label"],
        }
    else:
        recommandation = {}

    return {
        "resultats":       resultats,
        "differences":     differences,
        "recommandation":  recommandation,
        "nb_scenarios":    len(resultats),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SIMULATION INVERSE — Net cible → Brut nécessaire
# ═══════════════════════════════════════════════════════════════════════════════

def simuler_depuis_net(net_cible: float, nb_parts: float = 1.0,
                       donnees_extra: dict = None, precision: float = 100) -> dict:
    """
    Calcule le salaire brut nécessaire pour atteindre un net cible.
    Utilise une recherche dichotomique (bisection).

    Args:
        net_cible    : montant net souhaité (FCFA)
        nb_parts     : nombre de parts IRPP
        donnees_extra: autres éléments de paie (primes, etc.)
        precision    : précision souhaitée en FCFA (défaut 100)

    Returns:
        dict avec brut_necessaire, verifications, cout_employeur
    """
    if not net_cible or net_cible <= 0:
        return {"error": "Net cible doit être positif"}

    donnees_base = donnees_extra or {}

    # Bornes initiales
    brut_min = net_cible                    # le net ne peut pas dépasser le brut
    brut_max = net_cible * 1.6             # approximation large (charges ~40%)
    iterations = 0
    MAX_ITER = 50

    while iterations < MAX_ITER:
        brut_test = (brut_min + brut_max) / 2
        donnees   = {**donnees_base, "salaire_base": brut_test}

        try:
            res = calculer_bulletin(donnees, nb_parts=nb_parts)
            net_obtenu = res.get("net_a_payer", 0)
        except Exception:
            break

        ecart = net_obtenu - net_cible

        if abs(ecart) <= precision:
            # Converge
            brut_final = brut_test
            break

        if net_obtenu < net_cible:
            brut_min = brut_test
        else:
            brut_max = brut_test

        iterations += 1
    else:
        brut_final = (brut_min + brut_max) / 2

    # Calcul final avec le brut trouvé
    donnees_finale = {**donnees_base, "salaire_base": round(brut_final)}
    try:
        res_final = calculer_bulletin(donnees_finale, nb_parts=nb_parts)
    except Exception as e:
        return {"error": f"Erreur de calcul : {e}"}

    cout_emp = (
        res_final.get("salaire_brut", 0)
        + res_final.get("cnss_patronale", 0)
        + res_final.get("cnamgs_patronale", 0)
        + res_final.get("fnh", 0)
        + res_final.get("cfp", 0)
        + res_final.get("tcs", 0)
    )

    taux_charge = ((cout_emp - res_final.get("salaire_brut",0)) /
                   res_final.get("salaire_brut",1) * 100) if res_final.get("salaire_brut") else 0

    return {
        "net_cible":           net_cible,
        "brut_necessaire":     round(brut_final),
        "net_obtenu":          round(res_final.get("net_a_payer",0)),
        "ecart":               round(res_final.get("net_a_payer",0) - net_cible),
        "cout_employeur":      round(cout_emp),
        "cnss_salarie":        round(res_final.get("cnss_salarie", 0)),
        "cnamgs_salarie":      round(res_final.get("cnamgs_salarie", 0)),
        "tcs":                 round(res_final.get("tcs", 0)),
        "irpp":                round(res_final.get("irpp", 0)),
        "cnss_patronale":      round(res_final.get("cnss_patronale", 0)),
        "cnamgs_patronale":    round(res_final.get("cnamgs_patronale", 0)),
        "taux_charge_patronal": round(taux_charge, 1),
        "iterations":          iterations,
        "bulletin_complet":    res_final,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SIMULATION D'AUGMENTATION
# ═══════════════════════════════════════════════════════════════════════════════

def simuler_augmentation(
    salaire_actuel: float,
    augmentation_pct: float = None,
    augmentation_montant: float = None,
    nb_parts: float = 1.0,
    donnees_extra: dict = None,
) -> dict:
    """
    Simule l'impact d'une augmentation sur net et coût employeur.

    Args:
        salaire_actuel      : salaire brut actuel
        augmentation_pct    : pourcentage d'augmentation (ex: 10 pour 10%)
        augmentation_montant: montant fixe d'augmentation
        nb_parts            : parts IRPP
        donnees_extra       : autres éléments de paie

    Returns:
        dict avec avant, après, impact
    """
    if not salaire_actuel or salaire_actuel <= 0:
        return {"error": "Salaire actuel invalide"}

    if augmentation_pct is not None:
        nouveau_salaire = salaire_actuel * (1 + augmentation_pct / 100)
    elif augmentation_montant is not None:
        nouveau_salaire = salaire_actuel + augmentation_montant
        augmentation_pct = augmentation_montant / salaire_actuel * 100
    else:
        return {"error": "Fournissez augmentation_pct ou augmentation_montant"}

    base = donnees_extra or {}

    def _calc(salaire):
        d   = {**base, "salaire_base": salaire}
        res = calculer_bulletin(d, nb_parts=nb_parts)
        cout = (
            res.get("salaire_brut", 0)
            + res.get("cnss_patronale", 0)
            + res.get("cnamgs_patronale", 0)
            + res.get("fnh", 0)
            + res.get("cfp", 0)
            + res.get("tcs", 0)
        )
        return {**res, "cout_employeur": round(cout)}

    avant  = _calc(salaire_actuel)
    apres  = _calc(nouveau_salaire)

    # Projections sur 12 mois
    impact_mensuel_net  = apres.get("net_a_payer",0)  - avant.get("net_a_payer",0)
    impact_mensuel_cout = apres.get("cout_employeur",0) - avant.get("cout_employeur",0)

    return {
        "salaire_actuel":        salaire_actuel,
        "nouveau_salaire":       round(nouveau_salaire),
        "augmentation_pct":      round(augmentation_pct or 0, 2),
        "augmentation_montant":  round(nouveau_salaire - salaire_actuel),
        # Avant
        "avant_net":             round(avant.get("net_a_payer", 0)),
        "avant_cout":            round(avant.get("cout_employeur", 0)),
        "avant_irpp":            round(avant.get("irpp", 0)),
        "avant_cnss_sal":        round(avant.get("cnss_salarie", 0)),
        # Après
        "apres_net":             round(apres.get("net_a_payer", 0)),
        "apres_cout":            round(apres.get("cout_employeur", 0)),
        "apres_irpp":            round(apres.get("irpp", 0)),
        "apres_cnss_sal":        round(apres.get("cnss_salarie", 0)),
        # Deltas
        "delta_net":             round(impact_mensuel_net),
        "delta_cout":            round(impact_mensuel_cout),
        "delta_irpp":            round(apres.get("irpp",0) - avant.get("irpp",0)),
        # Projections annuelles
        "impact_annuel_net":     round(impact_mensuel_net  * 12),
        "impact_annuel_cout":    round(impact_mensuel_cout * 12),
        # Bulletins complets
        "bulletin_avant":  avant,
        "bulletin_apres":  apres,
    }
