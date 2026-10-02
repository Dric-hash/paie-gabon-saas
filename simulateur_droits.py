# -*- coding: utf-8 -*-
"""Simulateur de droits de fin de contrat.

Réutilise le moteur de solde de tout compte (conges_avance.calculer_solde_tout_compte)
en deux modes : à partir d'un salarié existant, ou en saisie libre (stubs).
Aucune donnée n'est enregistrée : c'est une simulation à titre indicatif.
"""
from datetime import date
from conges_avance import calculer_solde_tout_compte


class _TenantStub:
    def __init__(self, convention, jours_conge_par_mois):
        self.convention = convention
        self.jours_conge_par_mois = jours_conge_par_mois


class _ContratStub:
    def __init__(self, salaire_base, date_debut):
        self.actif = True
        self.salaire_base = salaire_base
        self.date_debut = date_debut
        self.date_fin = None


class _SalarieStub:
    def __init__(self, date_embauche, salaire, nb_enfants, tenant):
        self.date_embauche = date_embauche
        self.nb_enfants = nb_enfants or 0
        self.conges = []
        self.contrats = [_ContratStub(salaire, date_embauche)]
        self.tenant = tenant
        self.nom = "SIMULATION"; self.prenom = ""; self.matricule = "—"


def simuler_fin_contrat(salarie=None, bulletins_12=None, *, salaire=None,
                        date_embauche=None, nb_enfants=0, convention="BTP",
                        cause="LICENCIEMENT", date_cessation=None,
                        jours_conge_par_mois=None):
    """Renvoie le dict de droits (indemnités, préavis, congés, cotisations, net).
    - Mode salarié : passer `salarie` (+ `bulletins_12`).
    - Mode saisie libre : passer `salaire`, `date_embauche`, `nb_enfants`."""
    date_cessation = date_cessation or date.today()
    if salarie is not None:
        return calculer_solde_tout_compte(
            salarie, bulletins_12 or [], date_cessation,
            convention=convention, cause=cause,
            jours_conge_par_mois=jours_conge_par_mois)
    # Saisie libre : construit des stubs ; bulletins vides → retombe sur le contrat.
    tenant = _TenantStub(convention, jours_conge_par_mois)
    stub = _SalarieStub(date_embauche, salaire, nb_enfants, tenant)
    return calculer_solde_tout_compte(
        stub, [], date_cessation,
        convention=convention, cause=cause,
        jours_conge_par_mois=jours_conge_par_mois)
