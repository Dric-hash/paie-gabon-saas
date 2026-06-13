"""
tests/test_conges_simulation.py — Tests unitaires
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from datetime import date
from unittest.mock import MagicMock


# ═══════════════════════════════════════════════════════════
# TESTS CONGÉS AVANCÉS
# ═══════════════════════════════════════════════════════════

from conges_avance import (
    calculer_jours_acquis, calculer_solde_tout_compte,
    bilan_conges_tenant, planning_absences, allocation_conge,
    JOURS_PAR_MOIS, JOURS_MAX_PAR_AN
)


class TestJoursAcquis:
    def test_1_an_complet(self):
        """12 mois dans la période juin→mai = 30 jours"""
        emb = date(2024, 6, 1)
        ref = date(2025, 5, 31)  # dernier jour de la période
        r = calculer_jours_acquis(emb, date_ref=ref, annee_ref=2024)
        assert r["jours_acquis_periode"] == pytest.approx(30, abs=1)

    def test_6_mois(self):
        """6 mois ≈ 15 jours"""
        emb = date(2024, 6, 1)
        ref = date(2024, 12, 1)
        r = calculer_jours_acquis(emb, date_ref=ref)
        assert r["jours_acquis_periode"] == pytest.approx(15, abs=1)

    def test_plafond_30_jours(self):
        """Plus de 12 mois → plafonné à 30j"""
        emb = date(2023, 1, 1)
        ref = date(2025, 1, 1)
        r = calculer_jours_acquis(emb, date_ref=ref)
        assert r["jours_acquis_periode"] <= JOURS_MAX_PAR_AN

    def test_date_embauche_none(self):
        r = calculer_jours_acquis(None)
        assert r["jours_acquis_total"] == 0

    def test_embauche_future(self):
        r = calculer_jours_acquis(date(2099, 1, 1))
        assert r["jours_acquis_total"] == 0

    def test_anciennete_calcul(self):
        emb = date(2020, 1, 1)
        ref = date(2026, 6, 1)
        r = calculer_jours_acquis(emb, date_ref=ref)
        assert r["anciennete_annees"] == 6

    def test_bonus_anciennete_apres_5_ans(self):
        emb = date(2015, 1, 1)
        ref = date(2026, 1, 1)
        r = calculer_jours_acquis(emb, date_ref=ref)
        assert r["bonus_anciennete"] > 0

    def test_pas_bonus_avant_5_ans(self):
        emb = date(2023, 1, 1)
        ref = date(2026, 1, 1)
        r = calculer_jours_acquis(emb, date_ref=ref)
        assert r["bonus_anciennete"] == 0

    def test_taux_mensuel_correct(self):
        r = calculer_jours_acquis(date(2025, 1, 1))
        assert r["taux_mensuel"] == JOURS_PAR_MOIS


class TestSoldeToutCompte:
    def _salarie(self, date_embauche, bruts=None):
        s = MagicMock()
        s.date_embauche = date_embauche
        s.conges = []
        c = MagicMock()
        c.actif = True
        c.salaire_base = 400000
        s.contrats = [c]

        class FakeBul:
            def __init__(self, b): self.salaire_brut = b
        s.bulletins = [FakeBul(b) for b in (bruts or [400000]*6)]
        return s

    def _bulletins(self, bruts):
        buls = []
        for b in bruts:
            m = MagicMock()
            m.salaire_brut = b
            buls.append(m)
        return buls

    def test_indemnite_positive(self):
        s = self._salarie(date(2023, 1, 1))
        buls = self._bulletins([400000]*6)
        r = calculer_solde_tout_compte(s, buls, date(2026, 1, 1))
        assert r["indemnite_conges"] >= 0

    def test_jours_restants_positifs(self):
        s = self._salarie(date(2024, 1, 1))
        buls = self._bulletins([400000]*6)
        r = calculer_solde_tout_compte(s, buls)
        assert r["jours_restants"] >= 0

    def test_base_journaliere(self):
        s = self._salarie(date(2024, 1, 1))
        buls = self._bulletins([300000]*6)
        r = calculer_solde_tout_compte(s, buls)
        assert r["base_journaliere"] == pytest.approx(300000 / 26, abs=1)

    def test_indem_licenciement_apres_1_an(self):
        s = self._salarie(date(2020, 1, 1))
        buls = self._bulletins([500000]*12)
        r = calculer_solde_tout_compte(s, buls, date(2026, 1, 1))
        assert r["indem_licenciement"] > 0

    def test_indem_licenciement_meme_moins_1_an(self):
        # Code 2021 Art. 87 : l'indemnité de licenciement est due SANS
        # condition d'ancienneté. Un salarié licencié à ~7 mois y a droit.
        s = self._salarie(date(2025, 6, 1))
        buls = self._bulletins([400000]*3)
        r = calculer_solde_tout_compte(s, buls, date(2026, 1, 1), cause="LICENCIEMENT")
        assert r["indem_licenciement"] > 0

    def test_pas_indem_demission_moins_2_ans(self):
        # Démission < 2 ans : aucune indemnité de services rendus (Art. 88).
        s = self._salarie(date(2025, 6, 1))
        buls = self._bulletins([400000]*3)
        r = calculer_solde_tout_compte(s, buls, date(2026, 1, 1), cause="DEMISSION")
        assert r["indem_licenciement"] == 0

    def test_pas_indem_faute_lourde(self):
        # Faute lourde : aucune indemnité de rupture (Art. 87).
        s = self._salarie(date(2020, 1, 1))
        buls = self._bulletins([500000]*12)
        r = calculer_solde_tout_compte(s, buls, date(2026, 1, 1), cause="FAUTE_LOURDE")
        assert r["indem_licenciement"] == 0

    def test_total_somme_indemnites(self):
        s = self._salarie(date(2022, 1, 1))
        buls = self._bulletins([400000]*12)
        r = calculer_solde_tout_compte(s, buls, date(2026, 1, 1))
        assert r["total_a_payer"] == r["indemnite_conges"] + r["indem_licenciement"]


class TestBilanConges:
    def _sal(self, nom, emb, statut="ACTIF"):
        s = MagicMock()
        s.nom = nom
        s.prenom = "Test"
        s.nom_complet = f"{nom} Test"
        s.emploi = "Poste"
        s.date_embauche = emb
        s.statut = statut
        s.conges = []
        return s

    def test_exclut_inactifs(self):
        sals = [
            self._sal("ACTIF",   date(2023,1,1), "ACTIF"),
            self._sal("INACTIF", date(2023,1,1), "INACTIF"),
        ]
        b = bilan_conges_tenant(sals)
        assert len(b) == 1
        assert b[0]["salarie"].nom == "ACTIF"

    def test_trie_par_nom(self):
        sals = [
            self._sal("ZULU", date(2023,1,1)),
            self._sal("ALPHA", date(2023,1,1)),
        ]
        b = bilan_conges_tenant(sals)
        assert b[0]["salarie"].nom == "ALPHA"

    def test_sans_date_embauche_exclu(self):
        s = self._sal("SANS_DATE", None)
        b = bilan_conges_tenant([s])
        assert len(b) == 0


# ═══════════════════════════════════════════════════════════
# TESTS SIMULATION PAIE
# ═══════════════════════════════════════════════════════════

from simulation_paie import (
    comparer_scenarios, simuler_depuis_net, simuler_augmentation
)


class TestComparerScenarios:
    def test_deux_scenarios(self):
        r = comparer_scenarios([
            {"label": "Sc1", "salaire_base": 400000},
            {"label": "Sc2", "salaire_base": 500000},
        ])
        assert r["nb_scenarios"] == 2
        assert len(r["resultats"]) == 2

    def test_recommandation_presente(self):
        r = comparer_scenarios([
            {"label": "Bas",  "salaire_base": 300000},
            {"label": "Haut", "salaire_base": 600000},
        ])
        assert "meilleur_net" in r["recommandation"]

    def test_haut_salaire_meilleur_net(self):
        r = comparer_scenarios([
            {"label": "300k", "salaire_base": 300000},
            {"label": "600k", "salaire_base": 600000},
        ])
        valides = [r2 for r2 in r["resultats"] if "error" not in r2]
        nets = [v["net_a_payer"] for v in valides]
        assert nets[1] > nets[0]

    def test_minimum_2_scenarios(self):
        r = comparer_scenarios([{"label": "Sc1", "salaire_base": 400000}])
        assert "error" in r or r.get("nb_scenarios", 0) < 2

    def test_max_3_scenarios(self):
        r = comparer_scenarios([
            {"label": "S1", "salaire_base": 300000},
            {"label": "S2", "salaire_base": 400000},
            {"label": "S3", "salaire_base": 500000},
            {"label": "S4", "salaire_base": 600000},
        ])
        assert r["nb_scenarios"] <= 3


class TestSimulerDepuisNet:
    def test_converge_vers_net_cible(self):
        cible = 350000
        r = simuler_depuis_net(cible)
        assert abs(r["net_obtenu"] - cible) <= 500

    def test_brut_superieur_au_net(self):
        r = simuler_depuis_net(300000)
        assert r["brut_necessaire"] > r["net_obtenu"]

    def test_cout_employeur_superieur_brut(self):
        r = simuler_depuis_net(300000)
        assert r["cout_employeur"] > r["brut_necessaire"]

    def test_net_cible_invalide(self):
        r = simuler_depuis_net(0)
        assert "error" in r

    def test_net_cible_negatif(self):
        r = simuler_depuis_net(-50000)
        assert "error" in r

    def test_net_eleve(self):
        r = simuler_depuis_net(1000000)
        assert r["brut_necessaire"] > 1000000

    def test_champs_presents(self):
        r = simuler_depuis_net(250000)
        for k in ["brut_necessaire","net_obtenu","cout_employeur","cnss_salarie","irpp"]:
            assert k in r


class TestSimulerAugmentation:
    def test_augmentation_pct(self):
        r = simuler_augmentation(400000, augmentation_pct=10)
        assert r["nouveau_salaire"] == pytest.approx(440000, abs=1)

    def test_augmentation_montant(self):
        r = simuler_augmentation(400000, augmentation_montant=50000)
        assert r["nouveau_salaire"] == pytest.approx(450000, abs=1)

    def test_net_augmente_avec_salaire(self):
        r = simuler_augmentation(400000, augmentation_pct=20)
        assert r["apres_net"] > r["avant_net"]

    def test_cout_augmente_avec_salaire(self):
        r = simuler_augmentation(400000, augmentation_pct=10)
        assert r["apres_cout"] > r["avant_cout"]

    def test_delta_net_positif(self):
        r = simuler_augmentation(400000, augmentation_pct=10)
        assert r["delta_net"] > 0

    def test_impact_annuel_12_fois_mensuel(self):
        r = simuler_augmentation(400000, augmentation_pct=10)
        assert r["impact_annuel_net"]  == pytest.approx(r["delta_net"]  * 12, rel=0.01)
        assert r["impact_annuel_cout"] == pytest.approx(r["delta_cout"] * 12, rel=0.01)

    def test_sans_augmentation_erreur(self):
        r = simuler_augmentation(400000)
        assert "error" in r

    def test_salaire_invalide(self):
        r = simuler_augmentation(0, augmentation_pct=10)
        assert "error" in r


class TestAllocationConge:
    """Allocation de congé — Code du travail 2021, Art. 225."""

    class _Bul:
        def __init__(self, brut, rendement=0, assiduite=0):
            self.salaire_brut = brut
            self.prime_rendement = rendement
            setattr(self, "prime_assiduité", assiduite)

    def test_moyenne_12_mois(self):
        buls = [self._Bul(300000) for _ in range(12)]
        # 300000/26 x 30 jours ouvrables
        assert allocation_conge(buls, 30) == pytest.approx(300000 / 26 * 30, abs=1)

    def test_prorata_jours(self):
        buls = [self._Bul(300000) for _ in range(12)]
        assert allocation_conge(buls, 15) == pytest.approx(300000 / 26 * 15, abs=1)

    def test_exclut_rendement_et_assiduite(self):
        # 280000 de base après exclusion des primes rendement/assiduité
        buls = [self._Bul(300000, rendement=15000, assiduite=5000) for _ in range(12)]
        assert allocation_conge(buls, 30) == pytest.approx(280000 / 26 * 30, abs=1)

    def test_zero_si_aucun_bulletin(self):
        assert allocation_conge([], 15) == 0

    def test_zero_si_aucun_jour(self):
        buls = [self._Bul(300000) for _ in range(12)]
        assert allocation_conge(buls, 0) == 0
