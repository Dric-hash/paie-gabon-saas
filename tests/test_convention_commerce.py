# -*- coding: utf-8 -*-
"""Tests unitaires — Convention Collective du Commerce (Gabon)."""
import calculs_paie as c


# ── Préavis — Art. A.30.3 ─────────────────────────────────────────────────────
def test_preavis_commerce_tranches():
    assert c.calculer_preavis_commerce(0) == 15      # < 1 an
    assert c.calculer_preavis_commerce(2) == 30      # 1-3 ans
    assert c.calculer_preavis_commerce(4) == 60      # 3-5 ans
    assert c.calculer_preavis_commerce(7) == 90      # 5-10 ans
    assert c.calculer_preavis_commerce(12) == 120    # 10-15 ans
    assert c.calculer_preavis_commerce(17) == 150    # 15-20 ans
    assert c.calculer_preavis_commerce(22) == 180    # 20-25 ans
    assert c.calculer_preavis_commerce(27) == 200    # 26→190, 27→200
    assert c.calculer_preavis_commerce(31) == 245    # > 30 ans : 230 + 15


# ── Indemnité de services rendus — Art. A.32 ──────────────────────────────────
def test_isr_commerce_tranches():
    assert c.calculer_indemnite_services_rendus_commerce(300000, 1) == 0      # < 2 ans
    assert c.calculer_indemnite_services_rendus_commerce(300000, 4) == 240000  # 20%×4
    assert c.calculer_indemnite_services_rendus_commerce(300000, 8) == 600000  # 25%×8
    assert c.calculer_indemnite_services_rendus_commerce(300000, 15) == 1350000  # 30%×15
    assert c.calculer_indemnite_services_rendus_commerce(300000, 25) == 2625000  # 35%×25


# ── Prime d'ancienneté — Art. A.46.5 ──────────────────────────────────────────
def test_anciennete_commerce():
    assert c.calculer_prime_anciennete_commerce(200000, 1) == 0       # < 2 ans
    assert c.calculer_prime_anciennete_commerce(200000, 2) == 4000    # 2%
    assert c.calculer_prime_anciennete_commerce(200000, 5) == 10000   # 5%
    # Plafond 30 %
    assert c.calculer_prime_anciennete_commerce(200000, 40) == 60000  # 30%


# ── Heures supplémentaires — Art. A.38 (calcul par jour) ──────────────────────
def test_heures_sup_commerce_jour_ouvrable():
    d = c.distribuer_heures_semaine_commerce([{"heures_normales": 11, "type_jour": "NORMAL"}])
    assert d["heures_normales"] == 8.0
    assert d["heures_sup_10"] == 3.0       # 9e, 10e, 11e heure → +10% (≤ 8 heures sup)
    assert d["heures_sup_30"] == 0.0


def test_heures_sup_commerce_ferie():
    d = c.distribuer_heures_semaine_commerce(
        [{"heures_normales": 6, "heures_sup_nuit": 2, "type_jour": "FERIE"}])
    assert d["heures_sup_40"] == 6.0       # férié jour +40%
    assert d["heures_sup_140"] == 2.0      # férié nuit +140%


# ── Dispatcher par convention ─────────────────────────────────────────────────
def test_dispatcher_convention():
    assert c.preavis_jours("COMMERCE", 8) == 90
    assert c.preavis_jours("BTP", 8) == 95          # barème BTP distinct
    assert c.indemnite_services_rendus("COMMERCE", 300000, 8) == 600000  # 25%
    assert c.indemnite_services_rendus("BTP", 300000, 8) == 480000       # 20% (BTP)
    assert c.prime_anciennete("AUCUNE", 200000, 5) == 0
    assert "COMMERCE" in c.CONVENTIONS_DISPONIBLES


def test_grille_commerce_presente():
    codes = [g[0] for g in c.GRILLE_COMMERCE]
    assert "E1" in codes and "AM1" in codes and "C4" in codes
    e1 = next(g for g in c.GRILLE_COMMERCE if g[0] == "E1")
    assert e1[2] == 98_500   # salaire mensuel minimum E1
