"""
conges_avance.py — Module de gestion avancée des congés
========================================================
Fonctionnalités :
  1. Calcul automatique des jours acquis (2,5 jours/mois travaillé)
  2. Solde de tout compte — indemnité compensatrice de congés à la cessation
  3. Bilan congés annuel par salarié
  4. Planning des absences (données pour affichage calendrier)

Règles légales gabonaises (Code du Travail) :
  - 2,5 jours ouvrables de congé par mois de travail effectif
  - Maximum 30 jours ouvrables par an (12 mois × 2,5)
  - Période de référence : 1er juin → 31 mai (N→N+1)
  - Indemnité compensatrice = (salaire brut / 26 jours ouvrables) × jours non pris
  - Ancienneté > 5 ans : +1 jour / an supplémentaire (max +5j)
  - Ancienneté > 10 ans : +1 jour / an supplémentaire (max +5j en plus)
"""

from datetime import date, datetime
import math


# ── Constantes légales Gabon ──────────────────────────────────────────────────
JOURS_PAR_MOIS          = 2.5    # jours ouvrables / mois travaillé
JOURS_MAX_PAR_AN        = 30.0   # plafond annuel
JOURS_ANCIENNETE_PALIER1 = 5     # ans → +1 jour/an jusqu'à 5 ans
JOURS_ANCIENNETE_PALIER2 = 10    # ans → +1 jour/an au-delà
# Diviseur unique pour convertir un salaire mensuel en taux journalier
# « congés » (jours ouvrables, Art. 223). Utilisé partout dans le logiciel :
# allocation de congé ET indemnité compensatrice du solde de tout compte.
JOURS_OUVRABLES_MOIS    = 26


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CALCUL DES JOURS ACQUIS
# ═══════════════════════════════════════════════════════════════════════════════

def calculer_conge_maternite(date_accouchement, naissances_multiples=False,
                             complications=False) -> dict:
    """
    Congé de maternité — Code du travail 2021, Art. 208.

    Durée légale : 14 semaines, soit 6 semaines avant la date présumée
    d'accouchement et 8 semaines après. Prolongations :
      • +3 semaines en cas de naissances multiples ;
      • +3 semaines en cas de maladie/complications liées à la grossesse.

    Indemnisation : salaire intégral à la charge de la CNSS (et non de
    l'employeur). Ce congé n'entame pas le congé annuel (Art. 223).

    Returns: dict avec dates, durées et la mention de l'indemnisation,
             ou None si la date d'accouchement n'est pas fournie.
    """
    from datetime import timedelta
    if not date_accouchement:
        return None
    semaines_apres = 8 + (3 if naissances_multiples else 0) + (3 if complications else 0)
    date_debut = date_accouchement - timedelta(weeks=6)
    date_fin   = date_accouchement + timedelta(weeks=semaines_apres) - timedelta(days=1)
    return {
        "date_debut":      date_debut,
        "date_fin":        date_fin,
        "jours":           (date_fin - date_debut).days + 1,
        "semaines_total":  6 + semaines_apres,
        "semaines_avant":  6,
        "semaines_apres":  semaines_apres,
        "indemnise_par":   "CNSS",          # salaire intégral porté par la CNSS
        "impacte_conge_annuel": False,      # n'entame pas le congé annuel (Art. 223)
    }


def allocation_conge(bulletins_12mois, jours_pris: float, jours_mois: float = JOURS_OUVRABLES_MOIS) -> float:
    """
    Allocation de congé — Code du travail 2021, Art. 225.

    Base : moyenne mensuelle des salaires des 12 derniers mois, de laquelle
    sont exclues les primes de rendement et d'assiduité (Art. 225, al. 3),
    ramenée au jour ouvrable (÷ 26, les jours de congé étant décomptés en
    jours ouvrables — Art. 223) puis proratisée au nombre de jours pris.

    Args:
        bulletins_12mois : bulletins des 12 derniers mois
        jours_pris       : nombre de jours ouvrables de congé pris
        jours_mois       : diviseur mensuel en jours ouvrables (26 par défaut)

    Returns:
        montant de l'allocation de congé (FCFA)
    """
    from calculs_paie import fcfa
    if not bulletins_12mois or jours_pris <= 0 or jours_mois <= 0:
        return 0.0
    total = 0.0
    for b in bulletins_12mois:
        assiette = float(getattr(b, "salaire_brut", 0) or 0)
        # Exclusions autorisées par l'Art. 225 (rendement, assiduité)
        assiette -= float(getattr(b, "prime_rendement", 0) or 0)
        assiette -= float(getattr(b, "prime_assiduité", 0) or 0)
        total += max(0.0, assiette)
    moyenne_mensuelle = total / len(bulletins_12mois)
    base_journaliere  = moyenne_mensuelle / jours_mois
    return fcfa(base_journaliere * jours_pris, 0)


def _bonus_conge_enfants(salarie) -> int:
    """
    +1 jour de congé par an et par enfant à charge de moins de 16 ans,
    pour la mère de famille — Code du travail 2021, Art. 223.
    Robuste aux objets incomplets (retourne 0 si données absentes).
    """
    try:
        if (getattr(salarie, "sexe", "") or "") != "F":
            return 0
        n = int(getattr(salarie, "nb_enfants_moins_16ans", 0) or 0)
        return max(0, n)
    except (TypeError, ValueError):
        return 0


def calculer_jours_acquis(date_embauche, date_ref=None, annee_ref=None,
                          salarie=None, taux_mensuel=None) -> dict:
    """
    Calcule les jours de congé acquis par un salarié.

    Args:
        date_embauche : date d'embauche du salarié
        date_ref      : date de référence (défaut : aujourd'hui)
        annee_ref     : année de référence pour la période (défaut : année courante)

    Returns:
        dict avec :
          - jours_acquis_periode   : jours acquis sur la période courante
          - jours_acquis_total     : total depuis l'embauche (plafonné)
          - bonus_anciennete       : jours bonus ancienneté
          - mois_travailles        : mois travaillés sur la période
          - anciennete_annees      : ancienneté en années complètes
          - periode_debut          : début de la période de référence
          - periode_fin            : fin de la période de référence
    """
    if not date_embauche:
        return _zero_result()

    if date_ref is None:
        date_ref = date.today()

    # Période de référence : 1er juin N → 31 mai N+1
    if annee_ref is None:
        annee_ref = date_ref.year if date_ref.month >= 6 else date_ref.year - 1

    periode_debut = date(annee_ref, 6, 1)
    periode_fin   = date(annee_ref + 1, 5, 31)

    # Date effective de début (la plus récente entre embauche et début période)
    debut_effectif = max(date_embauche, periode_debut)

    # Date effective de fin (la plus ancienne entre date_ref et fin période)
    fin_effectif   = min(date_ref, periode_fin)

    if debut_effectif > fin_effectif:
        return _zero_result(periode_debut=periode_debut, periode_fin=periode_fin)

    # Calcul des mois travaillés (arrondi au demi-mois)
    delta_jours   = (fin_effectif - debut_effectif).days + 1
    mois_travailles = delta_jours / 30.4375  # 365.25 / 12

    # Taux mensuel d'acquisition (Art. 222). Défaut historique : 2,5 j/mois.
    # Les moins de 18 ans ont droit à 2,5 j/mois au minimum (Art. 222).
    taux = float(taux_mensuel) if taux_mensuel else JOURS_PAR_MOIS
    if salarie is not None and getattr(salarie, "date_naissance", None):
        try:
            age = (date_ref - salarie.date_naissance).days // 365
            if age < 18:
                taux = max(taux, 2.5)
        except (TypeError, ValueError):
            pass

    # Jours acquis sur la période
    jours_periode = round(min(mois_travailles * taux, JOURS_MAX_PAR_AN), 1)

    # Ancienneté totale
    anciennete_jours  = (date_ref - date_embauche).days
    anciennete_annees = anciennete_jours // 365
    # Ancienneté fractionnaire pour le MONTANT des indemnités (Art. 90 : les
    # fractions d'année comptent). Les mois entiers restants (>= 30 j) sont
    # exprimés en douzièmes d'année. L'ancienneté entière reste utilisée pour
    # lire les paliers conventionnels (2/10/15/20 ans).
    mois_fraction      = (anciennete_jours % 365) // 30
    anciennete_annees_calcul = round(anciennete_annees + mois_fraction / 12.0, 3)

    # Bonus ancienneté (jours supplémentaires)
    bonus = 0
    if anciennete_annees >= JOURS_ANCIENNETE_PALIER1:
        bonus += min(anciennete_annees - JOURS_ANCIENNETE_PALIER1 + 1, 5)
    if anciennete_annees >= JOURS_ANCIENNETE_PALIER2:
        bonus += min(anciennete_annees - JOURS_ANCIENNETE_PALIER2 + 1, 5)

    jours_total = min(jours_periode + bonus, JOURS_MAX_PAR_AN + bonus)

    # Jour de congé supplémentaire par enfant à charge < 16 ans (mère) — Art. 223
    bonus_enfants = _bonus_conge_enfants(salarie)
    jours_total += bonus_enfants

    return {
        "jours_acquis_periode":  jours_periode,
        "jours_acquis_total":    jours_total,
        "bonus_anciennete":      bonus,
        "bonus_enfants":         bonus_enfants,
        "mois_travailles":       round(mois_travailles, 1),
        "anciennete_annees":     anciennete_annees,
        "anciennete_annees_calcul": anciennete_annees_calcul,
        "anciennete_mois":       (anciennete_jours % 365) // 30,
        "periode_debut":         periode_debut,
        "periode_fin":           periode_fin,
        "taux_mensuel":          taux,
    }


def _zero_result(**kw):
    return {
        "jours_acquis_periode": 0,
        "jours_acquis_total":   0,
        "bonus_anciennete":     0,
        "bonus_enfants":        0,
        "mois_travailles":      0,
        "anciennete_annees":    0,
        "anciennete_annees_calcul": 0,
        "anciennete_mois":      0,
        "periode_debut":        kw.get("periode_debut"),
        "periode_fin":          kw.get("periode_fin"),
        "taux_mensuel":         JOURS_PAR_MOIS,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SOLDE DE TOUT COMPTE — INDEMNITÉ COMPENSATRICE DE CONGÉS
# ═══════════════════════════════════════════════════════════════════════════════

def calculer_solde_tout_compte(salarie, bulletins_12mois, date_cessation=None,
                               convention="BTP", cause="LICENCIEMENT",
                               jours_conge_par_mois=None) -> dict:
    """
    Calcule l'indemnité compensatrice de congés non pris à la cessation du contrat,
    ainsi que l'indemnité de rupture (licenciement / services rendus) due selon la
    CAUSE de cessation, conformément au Code du travail 2021 (Art. 87 à 90, 224).

    cause ∈ {"LICENCIEMENT", "RETRAITE", "DECES", "DEMISSION", "FAUTE_LOURDE"}

    Formule de l'indemnité compensatrice de congés :
      Base journalière = max(moy_12_mois, dernier_brut) / 26 (jours ouvrables)
      Indemnité = Base journalière × jours non pris

    Returns:
        dict avec tous les éléments du calcul
    """
    if date_cessation is None:
        date_cessation = date.today()

    # Jours acquis non pris
    acquis_calc = calculer_jours_acquis(
        salarie.date_embauche, date_cessation,
        salarie=salarie, taux_mensuel=jours_conge_par_mois
    )
    jours_acquis = acquis_calc["jours_acquis_total"]

    # Jours déjà pris (depuis les congés en base)
    jours_pris = sum(
        float(c.jours_pris or 0)
        for c in salarie.conges
        if c.statut in ("APPROUVÉ", "APPROUVE", "PRIS")
           and c.annee >= (date_cessation.year - 1)
    )
    jours_restants = max(0, jours_acquis - jours_pris)

    # Base de calcul
    bruts_12 = [float(b.salaire_brut or 0) for b in bulletins_12mois if b.salaire_brut]
    if bruts_12:
        moyenne_12 = sum(bruts_12) / len(bruts_12)
        dernier_brut = bruts_12[-1] if bruts_12 else 0
    else:
        # Fallback sur le contrat actuel
        contrat = next((c for c in salarie.contrats if c.actif), None)
        moyenne_12 = float(contrat.salaire_base) if contrat else 0
        dernier_brut = moyenne_12

    base_calcul      = max(moyenne_12, dernier_brut)
    base_journaliere = round(base_calcul / JOURS_OUVRABLES_MOIS, 2)
    indemnite        = round(base_journaliere * jours_restants, 0)

    # ── Indemnité de rupture selon la CAUSE (Code Art. 87-90) ────────────────
    # Licenciement (hors faute lourde) : 20 %/an SANS condition d'ancienneté,
    #   ou barème conventionnel BTP/Commerce s'il est plus favorable.
    # Services rendus : retraite, décès, ou démission >= 2 ans.
    # Non-cumul (Art. 89) : une seule de ces indemnités est versée.
    from calculs_paie import indemnite_rupture
    anciennete_calcul = acquis_calc["anciennete_annees_calcul"]   # fractionnaire (Art. 90)
    rupture = indemnite_rupture(convention, cause, base_calcul, anciennete_calcul)
    indem_rupture = rupture["montant"]

    return {
        "salarie":            salarie,
        "date_cessation":     date_cessation,
        "cause_cessation":    (cause or "").upper(),
        "type_indemnite":     rupture["type"],
        "jours_acquis":       jours_acquis,
        "jours_pris":         jours_pris,
        "jours_restants":     jours_restants,
        "bruts_12mois":       bruts_12,
        "nb_bulletins":       len(bruts_12),
        "moyenne_12_mois":    round(moyenne_12, 0),
        "dernier_brut":       round(dernier_brut, 0),
        "base_calcul":        round(base_calcul, 0),
        "base_journaliere":   base_journaliere,
        "indemnite_conges":   indemnite,
        "anciennete_annees":  acquis_calc["anciennete_annees"],
        "anciennete_mois":    acquis_calc["anciennete_mois"],
        "indem_licenciement": indem_rupture,
        "total_a_payer":      indemnite + indem_rupture,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BILAN CONGÉS PAR SALARIÉ
# ═══════════════════════════════════════════════════════════════════════════════

def bilan_conges_tenant(salaries, annee=None) -> list:
    """
    Calcule le bilan congés de tous les salariés actifs d'un tenant.

    Returns:
        liste de dicts [{salarie, jours_acquis, jours_pris, jours_restants,
                         anciennete, alerte}]
    """
    if annee is None:
        annee = date.today().year

    bilan = []
    for s in salaries:
        if s.statut != "ACTIF" or not s.date_embauche:
            continue

        calc    = calculer_jours_acquis(s.date_embauche)
        acquis  = calc["jours_acquis_total"]

        # Jours pris cette année
        pris = sum(
            float(c.jours_pris or 0)
            for c in s.conges
            if c.statut in ("APPROUVÉ","APPROUVE","PRIS") and c.annee == annee
        )
        restants = max(0, acquis - pris)

        # Alerte si solde > 30 jours (accumulation excessive)
        alerte = None
        if restants > 30:
            alerte = "excess"
        elif restants < 0:
            alerte = "debit"
        elif acquis < 5 and calc["anciennete_annees"] >= 1:
            alerte = "low"

        bilan.append({
            "salarie":          s,
            "jours_acquis":     acquis,
            "jours_pris":       pris,
            "jours_restants":   restants,
            "anciennete_annees":calc["anciennete_annees"],
            "anciennete_mois":  calc["anciennete_mois"],
            "bonus_anciennete": calc["bonus_anciennete"],
            "alerte":           alerte,
        })

    return sorted(bilan, key=lambda x: x["salarie"].nom)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PLANNING DES ABSENCES (données pour calendrier)
# ═══════════════════════════════════════════════════════════════════════════════

def planning_absences(conges, annee, mois=None) -> list:
    """
    Retourne les congés pour affichage dans un planning mensuel.

    Returns:
        liste de dicts [{salarie_nom, date_depart, date_retour, jours,
                         type_conge, statut, couleur}]
    """
    COULEURS = {
        "ANNUEL":    "#3b82f6",
        "MALADIE":   "#ef4444",
        "MATERNITE": "#ec4899",
        "PATERNITE": "#8b5cf6",
        "SANS_SOLDE":"#9ca3af",
        "AUTRE":     "#6b7280",
    }

    planning = []
    for c in conges:
        if not c.date_depart:
            continue

        # Filtrer par mois si demandé
        if mois and c.date_depart.month != mois and (
            not c.date_retour or c.date_retour.month != mois
        ):
            continue

        if c.date_depart.year != annee and (
            not c.date_retour or c.date_retour.year != annee
        ):
            continue

        jours = float(c.jours_pris or 0)
        if not jours and c.date_retour:
            jours = (c.date_retour - c.date_depart).days + 1

        planning.append({
            "id":            c.id,
            "salarie_nom":   c.salarie.nom_complet if c.salarie else "—",
            "salarie_id":    c.salarie_id,
            "date_depart":   c.date_depart.strftime("%Y-%m-%d"),
            "date_retour":   c.date_retour.strftime("%Y-%m-%d") if c.date_retour else None,
            "jours":         jours,
            "type_conge":    c.type_conge or "ANNUEL",
            "statut":        c.statut,
            "couleur":       COULEURS.get(c.type_conge or "ANNUEL", "#6b7280"),
        })

    return sorted(planning, key=lambda x: x["date_depart"])
