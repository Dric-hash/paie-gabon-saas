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
  - Indemnité compensatrice = (salaire brut / 30) × jours non pris
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


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CALCUL DES JOURS ACQUIS
# ═══════════════════════════════════════════════════════════════════════════════

def calculer_jours_acquis(date_embauche, date_ref=None, annee_ref=None) -> dict:
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

    # Jours acquis sur la période
    jours_periode = round(min(mois_travailles * JOURS_PAR_MOIS, JOURS_MAX_PAR_AN), 1)

    # Ancienneté totale
    anciennete_jours  = (date_ref - date_embauche).days
    anciennete_annees = anciennete_jours // 365

    # Bonus ancienneté (jours supplémentaires)
    bonus = 0
    if anciennete_annees >= JOURS_ANCIENNETE_PALIER1:
        bonus += min(anciennete_annees - JOURS_ANCIENNETE_PALIER1 + 1, 5)
    if anciennete_annees >= JOURS_ANCIENNETE_PALIER2:
        bonus += min(anciennete_annees - JOURS_ANCIENNETE_PALIER2 + 1, 5)

    jours_total = min(jours_periode + bonus, JOURS_MAX_PAR_AN + bonus)

    return {
        "jours_acquis_periode":  jours_periode,
        "jours_acquis_total":    jours_total,
        "bonus_anciennete":      bonus,
        "mois_travailles":       round(mois_travailles, 1),
        "anciennete_annees":     anciennete_annees,
        "anciennete_mois":       (anciennete_jours % 365) // 30,
        "periode_debut":         periode_debut,
        "periode_fin":           periode_fin,
        "taux_mensuel":          JOURS_PAR_MOIS,
    }


def _zero_result(**kw):
    return {
        "jours_acquis_periode": 0,
        "jours_acquis_total":   0,
        "bonus_anciennete":     0,
        "mois_travailles":      0,
        "anciennete_annees":    0,
        "anciennete_mois":      0,
        "periode_debut":        kw.get("periode_debut"),
        "periode_fin":          kw.get("periode_fin"),
        "taux_mensuel":         JOURS_PAR_MOIS,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SOLDE DE TOUT COMPTE — INDEMNITÉ COMPENSATRICE DE CONGÉS
# ═══════════════════════════════════════════════════════════════════════════════

def calculer_solde_tout_compte(salarie, bulletins_12mois, date_cessation=None,
                               convention="BTP") -> dict:
    """
    Calcule l'indemnité compensatrice de congés non pris à la cessation du contrat.

    Formule légale gabonaise :
      Base journalière = max(moy_12_mois, dernier_brut) / 30
      Indemnité = Base journalière × jours non pris

    Args:
        salarie         : objet Salarie
        bulletins_12mois: liste des bulletins des 12 derniers mois
        date_cessation  : date de cessation (défaut : aujourd'hui)

    Returns:
        dict avec tous les éléments du calcul
    """
    if date_cessation is None:
        date_cessation = date.today()

    # Jours acquis non pris
    acquis_calc = calculer_jours_acquis(
        salarie.date_embauche, date_cessation
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
    base_journaliere = round(base_calcul / 30, 2)
    indemnite        = round(base_journaliere * jours_restants, 0)

    # ── Indemnité de services rendus — Art. A.32 (selon convention) ──────────
    # Base : moyenne mensuelle du salaire global des 12 derniers mois.
    # Le barème dépend de la convention collective applicable :
    #   BTP      → 20% (2-10) / 26% (10-15) / 30% (15-20) / 35% (>20)
    #   COMMERCE → 20% (2-5)  / 25% (5-10)  / 30% (10-20) / 35% (>20)
    from calculs_paie import indemnite_services_rendus
    anciennete_annees = acquis_calc["anciennete_annees"]
    indem_licenciement = indemnite_services_rendus(
        convention, base_calcul, anciennete_annees
    )

    return {
        "salarie":            salarie,
        "date_cessation":     date_cessation,
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
        "anciennete_annees":  anciennete_annees,
        "anciennete_mois":    acquis_calc["anciennete_mois"],
        "indem_licenciement": indem_licenciement,
        "total_a_payer":      indemnite + indem_licenciement,
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
