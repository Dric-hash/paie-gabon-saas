# -*- coding: utf-8 -*-
"""Tests unitaires — Convention Collective des Exploitations Forestières (Gabon).

Référence : Convention collective du travail du secteur des exploitations
forestières de la République Gabonaise, signée à Libreville le 10 décembre 1985
(annexe classifications du 18 février 1986 ; barème salarial applicable au
01/03/1994). Distincte de la convention « Industries du Bois » (BOIS).

Couvre : grille salariale, préavis (A.30.3), indemnité de services rendus (A.32),
prime d'ancienneté (A.46.5), prime d'assiduité (A.50.4), prime de panier (A.47.3),
indemnité de déplacement (A.52), indemnité de caisse (A.54), heures sup (A.38.2),
permissions familiales (A.41), dispatchers, et NON-RÉGRESSION des autres conventions.
"""
import calculs_paie as c
import convention_foret as f


# ── Enregistrement de la convention ───────────────────────────────────────────
def test_convention_enregistree():
    assert "FORET" in c.CONVENTIONS_DISPONIBLES
    assert c._conv("FORET") == "FORET"
    assert c._conv("foret") == "FORET"


# ── Grille salariale — barème 1994 ────────────────────────────────────────────
def test_grille_seed_foret():
    g = f.grille_salaire_foret_seed()
    assert g["1"]["1"] == 85383.0
    assert g["7"]["1"] == 107865.0
    assert g["AM2"]["1"] == 148400.0
    assert g["C4"]["1"] == 496125.0
    # 13 catégories : 7 ouvrières + AM1/AM2 + C1..C4
    assert len(g) == 13


def test_base_mensuelle_200h():
    # Le barème pose Sal/mois = Sal/horaire × 200.
    assert f.HEURES_MENSUELLES == 200.0
    assert round(85383 / 200.0, 3) == 426.915   # cat.1 : salaire horaire


# ── Préavis — Art. 30.3 (barème unique) ───────────────────────────────────────
def test_preavis_foret_bareme():
    assert f.calculer_preavis_foret(0)  == 15    # < 1 an
    assert f.calculer_preavis_foret(2)  == 30    # 1–3 ans
    assert f.calculer_preavis_foret(4)  == 60    # 3–5 ans
    assert f.calculer_preavis_foret(7)  == 90    # 5–10 ans
    assert f.calculer_preavis_foret(12) == 120   # 10–15 ans
    assert f.calculer_preavis_foret(17) == 160   # 15–20 ans
    assert f.calculer_preavis_foret(22) == 180   # 20–25 ans
    assert f.calculer_preavis_foret(25) == 180   # borne
    assert f.calculer_preavis_foret(26) == 190   # +10 j/an au-delà de 25 ans
    assert f.calculer_preavis_foret(27) == 200


def test_preavis_dispatch_plus_favorable():
    # Le dispatcher retient max(convention, légal). À 17 ans : 160 vs 150 légal.
    assert c.preavis_jours("FORET", 17) == 160
    assert c.preavis_jours("FORET", 22) == 180


# ── Indemnité de services rendus — Art. 32 (20/22/25/28/32 %) ─────────────────
def test_isr_foret_tranches():
    assert f.calculer_indemnite_services_rendus_foret(300000, 1)  == 0        # < 2 ans
    assert f.calculer_indemnite_services_rendus_foret(300000, 2)  == 120000   # 20 %×2
    assert f.calculer_indemnite_services_rendus_foret(300000, 5)  == 300000   # 20 %×5
    assert f.calculer_indemnite_services_rendus_foret(300000, 6)  == 366000   # +22 %
    assert f.calculer_indemnite_services_rendus_foret(300000, 10) == 630000   # 5×20+5×22 %
    assert f.calculer_indemnite_services_rendus_foret(300000, 20) == 1425000  # jusqu'à 28 %
    assert f.calculer_indemnite_services_rendus_foret(300000, 21) == 1521000  # +32 %


def test_isr_dispatch():
    assert c.indemnite_services_rendus("FORET", 300000, 6) == 366000


# ── Prime d'ancienneté — Art. 46.5 (2 % à 2 ans, +1 %/an) ─────────────────────
def test_anciennete_foret():
    assert f.calculer_prime_anciennete_foret(200000, 1) == 0       # < 2 ans
    assert f.calculer_prime_anciennete_foret(200000, 2) == 4000    # 2 %
    assert f.calculer_prime_anciennete_foret(200000, 5) == 10000   # 5 %
    # Dispatcher + identique au BTP.
    assert c.prime_anciennete("FORET", 200000, 2) == 4000
    assert c.prime_anciennete("FORET", 200000, 5) == c.prime_anciennete("BTP", 200000, 5)


# ── Prime d'assiduité — Art. 50.4 (3 %, -50 %/1 abs, -100 %/2 abs) ────────────
def test_prime_assiduite_foret():
    assert f.calculer_prime_assiduite_foret(100000) == 3000        # 3 %
    assert f.calculer_prime_assiduite_foret(100000, nb_absences=1) == 1500   # -50 %
    assert f.calculer_prime_assiduite_foret(100000, nb_absences=2) == 0      # -100 %
    assert f.calculer_prime_assiduite_foret(100000, nb_absences=5) == 0      # plancher


# ── Prime de panier — Art. 47.3 (1,5 × salaire horaire) ───────────────────────
def test_prime_panier_foret():
    # salaire 100 000 → horaire 500 → 1,5 × 500 = 750
    assert f.calculer_prime_panier_foret(100000) == 750.0
    assert f.calculer_prime_panier_foret(0) == 0.0


# ── Indemnité de déplacement — Art. 52.2 (4× / 8× / 12×) ──────────────────────
def test_indemnite_deplacement_foret():
    # salaire 100 000 → horaire 500
    assert f.calculer_indemnite_deplacement_foret(100000, nb_repas=1) == 2000
    assert f.calculer_indemnite_deplacement_foret(100000, nb_repas=2) == 4000
    assert f.calculer_indemnite_deplacement_foret(100000, nb_repas=2, avec_nuit=True) == 6000


# ── Indemnité de caisse — Art. 54 (≥ 10 %) ────────────────────────────────────
def test_indemnite_caisse_foret():
    assert f.calculer_indemnite_caisse_foret(100000) == 10000
    assert f.calculer_indemnite_caisse_foret(0) == 0.0


# ── Gratification de fin d'année — Art. 51 (discrétionnaire, ≥ 2 ans) ─────────
def test_gratification_foret_discretionnaire():
    assert f.gratification_eligible_foret(1) is False
    assert f.gratification_eligible_foret(2) is True
    # Aucun taux conventionnel : montant à l'appréciation de l'employeur.
    assert f.calculer_gratification_fin_annee_foret(5) == 0.0


# ── Heures supplémentaires — Art. 38.2 (10/30/50/60/100 %, fériés 100/150 %) ──
def test_heures_sup_foret_coefficients():
    assert c.coeffs_heures_sup("FORET") == {
        "10": 1.10, "30": 1.30, "30b": 1.50, "40": 1.60, "70": 2.00,
        "fj": 2.00, "fn": 2.50}


def test_heures_sup_foret_montants():
    th = c.calculer_taux_horaire(200000)
    hs = c.calculer_heures_sup_btp(200000, h10=6, h30=2, h30b=3, h40=4, h70=1,
                                   convention="FORET")
    assert hs["taux_10"]  == round(th * 1.10, 4)   # 6 premières h jour : +10 %
    assert hs["taux_30"]  == round(th * 1.30, 4)   # au-delà de jour : +30 %
    assert hs["taux_30b"] == round(th * 1.50, 4)   # repos/chômé jour : +50 %
    assert hs["taux_40"]  == round(th * 1.60, 4)   # nuit ouvrable : +60 %
    assert hs["taux_70"]  == round(th * 2.00, 4)   # repos/chômé nuit : +100 %


# ── Permissions familiales — Art. 41 (barème identique au BTP) ────────────────
def test_permissions_foret():
    assert c.permissions_familiales("FORET", "mariage_travailleur") == 4
    assert c.permissions_familiales("FORET", "deces_conjoint_parent_enfant") == 5
    assert c.permissions_familiales("FORET", "naissance_enfant") == 3
    assert f.PLAFOND_PERMISSIONS_ANNUEL_FORET == 10


# ── Période d'essai — Art. 13 (plafond 6 mois) ────────────────────────────────
def test_periode_essai_foret_plafond():
    assert f.periode_essai_max_foret("1") == 30
    assert f.periode_essai_max_foret("5") == 90
    assert f.periode_essai_max_foret("AM1") == 120
    assert f.periode_essai_max_foret("C4") == 180    # 6 mois, plafond conventionnel


# ── Non-régression des autres conventions ─────────────────────────────────────
def test_non_regression_autres_conventions():
    assert c.preavis_jours("BTP", 12) == max(125, c.calculer_preavis_code(12))
    assert c.prime_anciennete("PETROLE", 200000, 2) == 10000
    assert c.indemnite_services_rendus("MINIER", 300000, 6) > 0
    assert c.coeffs_heures_sup("MINIER")["40"] == 1.68
