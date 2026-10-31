"""calendrier_paie.py — Constantes et petites fonctions de dates partagées par les modules de paie
(rappels, provision congés, budget, indicateurs RH, contrôle de la paie, prêts…), pour ne plus les
redéfinir dans chaque module."""
from calendar import monthrange
from datetime import date

# Bulletins qui engagent l'entreprise (validés ou payés) : jamais modifiés, base du « réalisé »
STATUTS_BULLETIN_VALIDES = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")

MOIS = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre",
        "Novembre", "Décembre"]
MOIS_COURTS = ["", "Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin", "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc."]


def fin_de_mois(annee, mois):
    """Dernier jour du mois."""
    return date(annee, mois, monthrange(annee, mois)[1])


def site_a_la_date(affectations, jour):
    """Site d'affectation couvrant le jour (la plus récente l'emporte), sinon None.
    affectations : [(début, fin|None, site_id)] ; le jour d'un transfert compte pour le nouveau site."""
    site = None
    for d0, d1, sid in affectations or []:
        if d0 <= jour and (d1 is None or jour < d1 or (jour == d1 and d0 == d1)):
            site = sid
    return site
