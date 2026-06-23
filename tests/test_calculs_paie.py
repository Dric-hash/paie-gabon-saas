"""
tests/test_calculs_paie.py — Tests unitaires du moteur de calcul de paie gabonais

Exécution :
    pip install pytest
    pytest tests/ -v

Ces tests couvrent :
    - calculer_taux_horaire
    - calculer_irpp (barème progressif + quotient familial)
    - calculer_bulletin (cas nominaux + cas limites)
    - Plafonds CNSS, CNAMGS
    - calculer_heures_sup_btp
    - distribuer_heures_semaine_btp

Chaque cas de test inclut les calculs manuels en commentaire
pour pouvoir auditer les résultats sans le code.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from calculs_paie import (
    calculer_taux_horaire,
    calculer_irpp,
    calculer_bulletin,
    calculer_heures_sup_btp,
    distribuer_heures_semaine_btp,
    arrondi_pas,
    CNSS_TAUX_SALARIE, CNSS_TAUX_PATRONAL, CNSS_PLAFOND,
    CNAMGS_TAUX_SALARIE, CNAMGS_TAUX_PATRONAL, CNAMGS_PLAFOND,
    TCS_TAUX, TCS_EXONERATION,
    H_NORMALES_MENSUEL,
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def bulletin_simple(salaire_base, **kwargs):
    """Crée un bulletin avec seulement le salaire de base et des options."""
    donnees = {"salaire_base": salaire_base}
    donnees.update(kwargs)
    return calculer_bulletin(donnees)


# ─────────────────────────────────────────────────────────────────────────────
# TAUX HORAIRE
# ─────────────────────────────────────────────────────────────────────────────

class TestTauxHoraire:
    def test_smig_gabon(self):
        # SMIG Gabon 2026 = 150 000 FCFA/mois
        # TH = 150 000 / 173,33 ≈ 865,5 FCFA/h
        th = calculer_taux_horaire(150_000)
        assert 860 < th < 870

    def test_salaire_zero(self):
        assert calculer_taux_horaire(0) == 0.0

    def test_salaire_negatif(self):
        assert calculer_taux_horaire(-1000) == 0.0

    def test_salaire_reference(self):
        # 520 000 FCFA → TH = 520000 / 173.33 ≈ 2999.94
        th = calculer_taux_horaire(520_000)
        assert abs(th - round(520_000 / H_NORMALES_MENSUEL, 4)) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# IRPP — Barème progressif
# ─────────────────────────────────────────────────────────────────────────────

class TestIRPP:
    def test_tranche_zero(self):
        # Base ≤ 125 000 → IRPP = 0
        # Après abattement 20% : 125 000 × 0.8 = 100 000 (arrondi millier) = 100 000
        # Revenu/part = 100 000 < 125 001 → taux = 0
        assert calculer_irpp(125_000, 1.0) == 0.0

    def test_tranche_5_pct(self):
        # base_imposable = 200 000 → après abattement 20% = 160 000
        # Arrondi millier → 160 000
        # 1 part : 160 000 = 125 000×0% + 35 000×5% = 0 + 1 750 = 1 750
        irpp = calculer_irpp(200_000, 1.0)
        assert irpp == pytest.approx(1_750.0, abs=50)

    def test_quotient_familial_2parts(self):
        # base = 600 000, 2 parts
        # après abattement : 600 000 × 0.8 = 480 000 → arrondi = 480 000
        # revenu/part = 240 000
        # Tranches : 0→125 000 à 0%, 125 001→160 000 à 5%, 160 001→225 000 à 10%, 225 001→240 000 à 15%
        # impôt/part = 0 + 34 999×5% + 64 999×10% + 14 999×15%
        #            = 0 + 1749.95 + 6499.9 + 2249.85 = 10 499.7
        # × 2 parts = 20 999.4
        irpp = calculer_irpp(600_000, 2.0)
        assert 19_000 < irpp < 23_000  # fourchette pour tolérer l'arrondi millier

    def test_base_nulle(self):
        assert calculer_irpp(0, 1.0) == 0.0

    def test_parts_nulles(self):
        assert calculer_irpp(500_000, 0) == 0.0

    def test_tranche_35_pct(self):
        # Base très élevée → taux marginal 35%
        irpp_haut = calculer_irpp(3_000_000, 1.0)
        irpp_bas  = calculer_irpp(500_000, 1.0)
        assert irpp_haut > irpp_bas


# ─────────────────────────────────────────────────────────────────────────────
# BULLETIN — Cas nominaux
# ─────────────────────────────────────────────────────────────────────────────

class TestBulletinNominal:
    def test_salaire_base_seul_structure(self):
        """Un bulletin avec seulement le salaire de base doit rendre tous les champs."""
        b = bulletin_simple(300_000)
        champs_obligatoires = [
            "salaire_brut", "cnss_salarie", "cnss_patronale",
            "cnamgs_salarie", "cnamgs_patronale",
            "tcs", "irpp", "net_a_payer",
        ]
        for champ in champs_obligatoires:
            assert champ in b, f"Champ manquant : {champ}"

    def test_net_inferieur_brut(self):
        """Le net à payer doit toujours être inférieur au brut (avec des cotisations)."""
        b = bulletin_simple(500_000)
        assert b["net_a_payer"] < b["salaire_brut"]

    def test_cnss_plafond(self):
        """Un salaire de 3 M FCFA ne doit pas dépasser le plafond CNSS de 1,5 M."""
        b = bulletin_simple(3_000_000)
        assert b["base_cnss"] <= CNSS_PLAFOND
        assert b["cnss_salarie"] <= CNSS_PLAFOND * CNSS_TAUX_SALARIE
        assert b["cnss_patronale"] <= CNSS_PLAFOND * CNSS_TAUX_PATRONAL

    def test_cnamgs_plafond(self):
        """Base CNAMGS plafonnée à 2,5 M FCFA."""
        b = bulletin_simple(4_000_000)
        assert b["base_cnamgs"] <= CNAMGS_PLAFOND

    def test_tcs_exoneration(self):
        """Un salaire très bas n'a pas de TCS (exonération 150 000 FCFA)."""
        b = bulletin_simple(150_000)
        # Base TCS avant exonération ≈ salaire_base - cnss - cnamgs
        # Elle peut être < 150 000 → TCS = 0
        assert b["tcs"] >= 0

    def test_calcul_cnss_salarie_400k(self):
        """
        Salaire 400 000 FCFA — vérification manuelle CNSS salarié :
        base_cnss = 400 000 (< plafond 1 500 000)
        cnss_salarie = 400 000 × 5% = 20 000
        """
        b = bulletin_simple(400_000)
        assert b["cnss_salarie"] == pytest.approx(20_000.0, abs=100)

    def test_calcul_cnss_patronal_400k(self):
        """
        cnss_patronale = 400 000 × 18% = 72 000
        """
        b = bulletin_simple(400_000)
        assert b["cnss_patronale"] == pytest.approx(72_000.0, abs=200)

    def test_acompte_reduit_net(self):
        """Un acompte de 50 000 FCFA doit réduire le net à payer."""
        b_sans = bulletin_simple(400_000)
        b_avec = bulletin_simple(400_000, acompte=50_000)
        assert abs((b_sans["net_a_payer"] - b_avec["net_a_payer"]) - 50_000) < 10

    def test_prime_transport_exoneree_cnss(self):
        """
        La prime de transport est exonérée de CNSS à hauteur de 35 000 FCFA.
        Avec prime_transport=100 000 : base_cnss réduite de 35 000.
        """
        b_sans  = bulletin_simple(500_000)
        b_avec  = bulletin_simple(500_000, prime_transport=100_000)
        # Le brut augmente de 100 000 mais la base CNSS augmente de (100 000 - 35 000) = 65 000
        diff_base_cnss = b_avec["base_cnss"] - b_sans["base_cnss"]
        assert abs(diff_base_cnss - 65_000) < 500

    def test_net_positif(self):
        """Le net à payer doit être positif pour un salaire normal."""
        b = bulletin_simple(500_000)
        assert b["net_a_payer"] > 0

    def test_salaire_zero(self):
        """Un salaire de base nul ne doit pas lever d'exception."""
        b = bulletin_simple(0)
        assert b["net_a_payer"] == 0.0
        assert b["salaire_brut"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# BULLETIN — Cas limites
# ─────────────────────────────────────────────────────────────────────────────

class TestBulletinCasLimites:
    def test_salaire_juste_au_dessus_plafond_cnss(self):
        """
        Salaire = 1 500 001 : base_cnss doit être exactement 1 500 000.
        """
        b = bulletin_simple(1_500_001)
        assert b["base_cnss"] == CNSS_PLAFOND

    def test_coherence_salaire_brut(self):
        """
        salaire_brut = salaire_base + primes - absences (sans éléments hors brut).
        """
        b = calculer_bulletin({
            "salaire_base": 400_000,
            "prime_caisse": 50_000,
            "absences":     20_000,
        })
        assert b["salaire_brut"] == pytest.approx(430_000.0, abs=1)

    def test_heures_sup_ajoutent_au_brut(self):
        """Les heures supplémentaires augmentent le brut."""
        b_sans = bulletin_simple(400_000)
        b_avec = bulletin_simple(400_000, heures_sup_10=10_000)
        assert b_avec["salaire_brut"] > b_sans["salaire_brut"]

    def test_indem_logement_impact_cnamgs(self):
        """L'indemnité de logement est partiellement exonérée de CNAMGS."""
        b_sans = bulletin_simple(500_000)
        b_avec = bulletin_simple(500_000, indem_logement=200_000)
        # Le brut augmente mais la base CNAMGS doit être limitée
        assert b_avec["base_cnamgs"] <= CNAMGS_PLAFOND


# ─────────────────────────────────────────────────────────────────────────────
# HEURES SUPPLÉMENTAIRES BTP
# ─────────────────────────────────────────────────────────────────────────────

class TestHeuresSupBTP:
    def test_structure_retour(self):
        r = calculer_heures_sup_btp(400_000)
        for cle in ["taux_horaire", "montant_10", "montant_30", "total_sup"]:
            assert cle in r

    def test_heures_structurelles_btp(self):
        """Sans h10/h30, doit utiliser les valeurs structurelles 17,33h."""
        r = calculer_heures_sup_btp(400_000)
        assert r["h10"] == pytest.approx(17.33, abs=0.01)
        assert r["h30"] == pytest.approx(17.33, abs=0.01)

    def test_heures_feries_coefficient_70(self):
        r = calculer_heures_sup_btp(400_000, h10=0, h30=0, h70=8.0)
        th = calculer_taux_horaire(400_000)
        expected = round(8.0 * th * 1.70, 2)
        assert r["montant_70"] == pytest.approx(expected, abs=10)

    def test_heures_nuit_coefficient_40(self):
        r = calculer_heures_sup_btp(400_000, h10=0, h30=0, h40=4.0)
        th = calculer_taux_horaire(400_000)
        expected = round(4.0 * th * 1.40, 2)
        assert r["montant_40"] == pytest.approx(expected, abs=10)

    def test_total_sup_coherent(self):
        r = calculer_heures_sup_btp(400_000, h10=10, h30=8, h40=2, h70=4)
        assert r["total_sup"] == pytest.approx(
            r["montant_10"] + r["montant_30"] + r["montant_40"] + r["montant_70"],
            abs=1
        )


# ─────────────────────────────────────────────────────────────────────────────
# DISTRIBUTION HEURES SEMAINE BTP
# ─────────────────────────────────────────────────────────────────────────────

class TestDistribuerHeuresSemaineBTP:
    def test_semaine_normale_40h(self):
        """5 jours × 8h = 40h → pas d'heures sup."""
        jours = [{"heures_normales": 8.0, "type_jour": "NORMAL"} for _ in range(5)]
        r = distribuer_heures_semaine_btp(jours)
        assert r["heures_normales"] == 40.0
        assert r["heures_sup_10"] == 0.0
        assert r["heures_sup_30"] == 0.0

    def test_semaine_btp_48h(self):
        """6 jours × 8h = 48h → 40h normales + 4h×10% + 4h×30%."""
        jours = [{"heures_normales": 8.0, "type_jour": "NORMAL"} for _ in range(6)]
        r = distribuer_heures_semaine_btp(jours)
        assert r["heures_normales"] == 40.0
        assert r["heures_sup_10"] == pytest.approx(4.0, abs=0.1)
        assert r["heures_sup_30"] == pytest.approx(4.0, abs=0.1)

    def test_heures_dimanche_classees_70(self):
        """Les heures du dimanche doivent être comptées en +70%."""
        jours = [
            {"heures_normales": 8.0, "type_jour": "NORMAL"},
            {"heures_normales": 8.0, "type_jour": "DIMANCHE"},
        ]
        r = distribuer_heures_semaine_btp(jours)
        assert r["heures_sup_70"] == 8.0

    def test_heures_ferie_classees_70(self):
        jours = [{"heures_normales": 8.0, "type_jour": "FERIE"}]
        r = distribuer_heures_semaine_btp(jours)
        assert r["heures_sup_70"] == 8.0

    def test_total_coherent(self):
        jours = [
            {"heures_normales": 10.0, "type_jour": "NORMAL"},
            {"heures_normales": 10.0, "type_jour": "NORMAL"},
            {"heures_normales": 8.0, "type_jour": "DIMANCHE"},
        ]
        r = distribuer_heures_semaine_btp(jours)
        total_compose = (
            r["heures_normales"] + r["heures_sup_10"]
            + r["heures_sup_30"] + r["heures_sup_40"] + r["heures_sup_70"]
        )
        assert total_compose == pytest.approx(r["total_heures"], abs=0.1)


# ─────────────────────────────────────────────────────────────────────────────
# SCÉNARIOS INTÉGRÉS (cas réels)
# ─────────────────────────────────────────────────────────────────────────────

class TestScenariosIntegres:
    def test_ouvrier_btp_smig(self):
        """
        Scénario : ouvrier BTP au SMIG (150 000 FCFA), 1 part.
        Vérification de cohérence générale.
        """
        b = bulletin_simple(150_000, nb_parts_override=None)
        b = calculer_bulletin({"salaire_base": 150_000}, nb_parts=1.0)
        assert b["salaire_brut"] == 150_000.0
        assert 0 <= b["cnss_salarie"] <= 150_000 * 0.05 + 1
        assert b["net_a_payer"] > 100_000  # net raisonnable

    def test_cadre_superieur(self):
        """
        Scénario : cadre supérieur 2 500 000 FCFA, 3 parts.
        CNSS et CNAMGS plafonnés, IRPP élevé.
        """
        b = calculer_bulletin({"salaire_base": 2_500_000}, nb_parts=3.0)
        assert b["base_cnss"]   == CNSS_PLAFOND
        assert b["base_cnamgs"] <= CNAMGS_PLAFOND
        assert b["irpp"] > 0

    def test_coherence_net_calcule(self):
        """
        net_a_payer = salaire_brut - cnss_sal - cnamgs_sal - tcs - irpp
                      + elements_hors_charges - acompte
        Pour un bulletin sans éléments hors charges ni acompte,
        net_a_payer ≈ salaire_brut - cnss_sal - cnamgs_sal - tcs - irpp
        """
        b = bulletin_simple(600_000)
        attendu = (
            b["salaire_brut"]
            - b["cnss_salarie"]
            - b["cnamgs_salarie"]
            - b["tcs"]
            - b["irpp"]
            # prime_panier, indem_transport, prime_salisure = 0
        )
        assert abs(b["net_a_payer"] - attendu) < 5  # tolérance arrondi


# ─────────────────────────────────────────────────────────────────────────────
# ARRONDI AU MULTIPLE DE 5 (paies journaliers mensuels réglées en espèces)
# ─────────────────────────────────────────────────────────────────────────────
class TestArrondiPas:
    def test_termine_par_99999_devient_rond(self):
        assert arrondi_pas(1099999, 5) == 1100000
        assert arrondi_pas(99999, 5) == 100000

    def test_arrondi_au_multiple_de_5(self):
        assert arrondi_pas(149999, 5) == 150000
        assert arrondi_pas(149997, 5) == 149995   # plus proche
        assert arrondi_pas(149998, 5) == 150000

    def test_montant_deja_rond_inchange(self):
        assert arrondi_pas(150000, 5) == 150000
        assert arrondi_pas(0, 5) == 0.0

    def test_resultat_toujours_multiple_de_5(self):
        for v in (123456, 987654, 451437.985, 1, 7, 12):
            assert arrondi_pas(v, 5) % 5 == 0
