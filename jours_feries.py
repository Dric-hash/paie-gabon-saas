"""
jours_feries.py — Jours fériés légaux du Gabon

Fournit la liste des jours fériés gabonais pour une année donnée, en combinant :
    - les jours fériés à date fixe (Nouvel An, Fête du Travail, Indépendance…)
    - les jours fériés mobiles chrétiens (Pâques, Ascension, Pentecôte)
      calculés par l'algorithme de Computus
    - les fêtes musulmanes (Aïd el-Fitr, Aïd el-Kebir) — approximation, car
      elles dépendent de l'observation lunaire et doivent être confirmées
      officiellement chaque année.

Usage :
    from jours_feries import est_jour_ferie, jours_feries_annee, nom_jour_ferie

    est_jour_ferie(date(2026, 8, 17))   → True  (Fête de l'Indépendance)
    nom_jour_ferie(date(2026, 8, 17))   → "Fête de l'Indépendance"
    jours_feries_annee(2026)            → {date: nom, …}

Référence : Code du travail gabonais et calendrier officiel des jours fériés.
Les dates des fêtes musulmanes sont approximatives (±1 jour) et doivent être
ajustées selon le calendrier officiel publié par les autorités gabonaises.
"""
from datetime import date, timedelta


# ══════════════════════════════════════════════════════════════════════════════
# JOURS FÉRIÉS À DATE FIXE
# ══════════════════════════════════════════════════════════════════════════════
_FERIES_FIXES = {
    (1, 1):   "Nouvel An",
    (3, 8):   "Journée internationale des droits de la femme",
    (4, 17):  "Journée des droits de la femme gabonaise",
    (5, 1):   "Fête du Travail",
    (8, 15):  "Assomption",
    (8, 16):  "Fête de l'Indépendance (1er jour)",
    (8, 17):  "Fête de l'Indépendance",
    (11, 1):  "Toussaint",
    (12, 25): "Noël",
}


# ══════════════════════════════════════════════════════════════════════════════
# JOURS FÉRIÉS MOBILES CHRÉTIENS (calcul de la date de Pâques)
# ══════════════════════════════════════════════════════════════════════════════
def _dimanche_paques(annee: int) -> date:
    """
    Calcule la date du dimanche de Pâques (algorithme de Computus / Gauss-Butcher).
    Valable pour le calendrier grégorien.
    """
    a = annee % 19
    b = annee // 100
    c = annee % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mois = (h + l - 7 * m + 114) // 31
    jour = ((h + l - 7 * m + 114) % 31) + 1
    return date(annee, mois, jour)


def _feries_mobiles_chretiens(annee: int) -> dict:
    """Retourne les jours fériés chrétiens mobiles pour l'année."""
    paques = _dimanche_paques(annee)
    return {
        paques + timedelta(days=1):  "Lundi de Pâques",
        paques + timedelta(days=39): "Ascension",
        paques + timedelta(days=50): "Lundi de Pentecôte",
    }


# ══════════════════════════════════════════════════════════════════════════════
# FÊTES MUSULMANES (approximatives — à confirmer officiellement chaque année)
# ══════════════════════════════════════════════════════════════════════════════
# Dates estimées pour les années courantes. Le Gabon reconnaît l'Aïd el-Fitr
# (fin du Ramadan) et l'Aïd el-Kebir / Tabaski (fête du sacrifice).
# Ces dates DOIVENT être confirmées chaque année par décret, car elles
# dépendent de l'observation de la lune.
_FETES_MUSULMANES = {
    2024: {(4, 10): "Aïd el-Fitr", (6, 16): "Aïd el-Kebir"},
    2025: {(3, 31): "Aïd el-Fitr", (6, 6):  "Aïd el-Kebir"},
    2026: {(3, 20): "Aïd el-Fitr", (5, 27): "Aïd el-Kebir"},
    2027: {(3, 10): "Aïd el-Fitr", (5, 16): "Aïd el-Kebir"},
    2028: {(2, 27): "Aïd el-Fitr", (5, 5):  "Aïd el-Kebir"},
}


# ══════════════════════════════════════════════════════════════════════════════
# API PUBLIQUE
# ══════════════════════════════════════════════════════════════════════════════
def jours_feries_annee(annee: int) -> dict:
    """
    Retourne tous les jours fériés d'une année sous forme {date: nom}.
    """
    feries = {}
    # Fixes
    for (mois, jour), nom in _FERIES_FIXES.items():
        try:
            feries[date(annee, mois, jour)] = nom
        except ValueError:
            pass
    # Mobiles chrétiens
    feries.update(_feries_mobiles_chretiens(annee))
    # Fêtes musulmanes (si connues pour l'année)
    for (mois, jour), nom in _FETES_MUSULMANES.get(annee, {}).items():
        try:
            feries[date(annee, mois, jour)] = nom
        except ValueError:
            pass
    return feries


def est_jour_ferie(jour: date) -> bool:
    """Retourne True si la date est un jour férié légal gabonais."""
    if jour is None:
        return False
    return jour in jours_feries_annee(jour.year)


def nom_jour_ferie(jour: date):
    """Retourne le nom du jour férié, ou None si ce n'en est pas un."""
    if jour is None:
        return None
    return jours_feries_annee(jour.year).get(jour)


def est_dimanche(jour: date) -> bool:
    """Retourne True si la date est un dimanche."""
    return jour is not None and jour.weekday() == 6


def type_jour_auto(jour: date) -> str:
    """
    Détermine automatiquement le type de jour pour le pointage :
        "FERIE"     si jour férié légal (majoration +70%)
        "DIMANCHE"  si dimanche (majoration +40%)
        "NORMAL"    sinon
    Le jour férié a priorité sur le dimanche.
    """
    if est_jour_ferie(jour):
        return "FERIE"
    if est_dimanche(jour):
        return "DIMANCHE"
    return "NORMAL"
