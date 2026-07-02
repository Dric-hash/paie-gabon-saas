# -*- coding: utf-8 -*-
"""Tests — Convention Collective des Compagnies de Transports Aériens (Gabon).

Couvre : préavis A.30.3 (double barème exécution / cadre), services rendus A.32
(20/25/30/35/40), heures sup A.39 (grille 5 cases), primes A.53/A.48, réutilisation
d'ancienneté A.47 et permissions A.42, dispatchers, et non-régression.
"""
import calculs_paie as c


def test_convention_enregistree():
    assert "AERIEN" in c.CONVENTIONS_DISPONIBLES
    assert c._conv("aerien") == "AERIEN"


# ── Préavis A.30.3 — personnel d'exécution ────────────────────────────────────
def test_preavis_aerien_execution():
    f = c.calculer_preavis_aerien
    assert f(0) == 15 and f(2) == 30 and f(4) == 60 and f(7) == 90
    assert f(12) == 120 and f(17) == 150 and f(25) == 180
    assert f(30) == 180 and f(33) == 180 + 3 * 10   # +10 j/an au-delà de 30


# ── Préavis A.30.3 — maîtrise & cadres ────────────────────────────────────────
def test_preavis_aerien_cadre():
    f = lambda a: c.calculer_preavis_aerien(a, cadre=True)
    assert f(0) == 30 and f(2) == 60 and f(4) == 90 and f(7) == 120
    assert f(12) == 150 and f(17) == 180 and f(25) == 210
    assert f(33) == 210 + 3 * 15   # +15 j/an au-delà de 30


def test_preavis_dispatch_cadre_param():
    assert c.preavis_jours("AERIEN", 25) == 180              # exécution
    assert c.preavis_jours("AERIEN", 25, cadre=True) == 210  # cadre


# ── Services rendus A.32 (20/25/30/35/40) ─────────────────────────────────────
def test_isr_aerien():
    f = c.calculer_indemnite_services_rendus_aerien
    assert f(300000, 1) == 0                       # < 2 ans
    assert f(300000, 5) == 0.20 * 5 * 300000
    assert f(300000, 8) == 0.25 * 8 * 300000
    assert f(300000, 13) == 0.30 * 13 * 300000
    assert f(300000, 18) == 0.35 * 18 * 300000
    assert f(300000, 25) == 0.40 * 25 * 300000
    assert c.indemnite_services_rendus("AERIEN", 300000, 25) == 0.40 * 25 * 300000


# ── Heures sup A.39 (mapping 5 cases) ─────────────────────────────────────────
def test_heures_sup_aerien_coefficients():
    assert c.coeffs_heures_sup("AERIEN") == {
        "10": 1.15, "30": 1.30, "30b": 1.50, "40": 1.60, "70": 2.00}


# ── Prime d'assiduité A.53 (3 % base, −50 %/−100 %) ──────────────────────────
def test_prime_assiduite_aerien():
    f = c.prime_assiduite_aerien
    assert f(200000) == 6000            # 3 %
    assert f(200000, nb_absences=1) == 3000   # −50 %
    assert f(200000, nb_absences=2) == 0      # −100 %
    assert f(200000, nb_absences=5) == 0


# ── Prime de panier A.48 (max(1.5×horaire, 4×SMIG)) ──────────────────────────
def test_prime_panier_aerien():
    assert c.prime_panier_aerien(1000, smig_horaire=500) == 2000    # 1.5×1000 > 4×500
    assert c.prime_panier_aerien(1000, smig_horaire=800) == 3200    # 4×800 > 1.5×1000


# ── Réutilisations (ancienneté A.47, permissions A.42) ───────────────────────
def test_reutilisations_aerien():
    assert c.prime_anciennete("AERIEN", 200000, 2) == 4000          # 2 %
    assert c.permissions_familiales("AERIEN", "mariage_travailleur") == 4
    assert c.permissions_familiales("AERIEN", "deces_conjoint_parent_enfant") == 5


# ── Non-régression ────────────────────────────────────────────────────────────
def test_non_regression():
    assert c.calculer_preavis_industrie(12) == 150
    assert c.calculer_indemnite_services_rendus_btp(300000, 6) == 360000
    assert c.coeffs_heures_sup("INDUSTRIE") == {
        "10": 1.16, "30": 1.35, "30b": 1.50, "40": 1.80, "70": 2.35}
