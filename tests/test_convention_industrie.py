# -*- coding: utf-8 -*-
"""Tests unitaires — Convention Collective des Entreprises Industrielles (Gabon).

Référence : Convention collective des entreprises industrielles du Gabon,
signée à Libreville le 6 mai 1983. Couvre les clauses propres aux Industrielles
(préavis A.30.3, services rendus A.32), la réutilisation des clauses identiques
au BTP (ancienneté A.46, permissions A.41), les dispatchers, et la
NON-RÉGRESSION des conventions BTP/Commerce/Pétrole.
"""
import calculs_paie as c


# ── Enregistrement de la convention ───────────────────────────────────────────
def test_convention_enregistree():
    assert "INDUSTRIE" in c.CONVENTIONS_DISPONIBLES
    assert c._conv("INDUSTRIE") == "INDUSTRIE"
    assert c._conv("industrie") == "INDUSTRIE"


# ── Préavis — Art. A.30.3 (15 j · 1/2/3/5/6/7 mois · +21 j/an > 30 ans) ────────
def test_preavis_industrie_bareme():
    assert c.calculer_preavis_industrie(0)  == 15    # < 1 an
    assert c.calculer_preavis_industrie(2)  == 30    # 1 mois
    assert c.calculer_preavis_industrie(4)  == 60    # 2 mois
    assert c.calculer_preavis_industrie(7)  == 90    # 3 mois
    assert c.calculer_preavis_industrie(12) == 150   # 5 mois
    assert c.calculer_preavis_industrie(17) == 180   # 6 mois
    assert c.calculer_preavis_industrie(25) == 210   # 7 mois
    assert c.calculer_preavis_industrie(30) == 210   # borne haute
    assert c.calculer_preavis_industrie(32) == 252   # 210 + 2×21


def test_preavis_industrie_distinct_du_btp():
    # À 12 ans : Industrie = 5 mois (150 j) vs BTP = 125 j.
    assert c.calculer_preavis_industrie(12) == 150
    assert c.calculer_preavis_btp(12) == 125
    # À 25 ans : Industrie = 7 mois (210 j) vs BTP = 180 j.
    assert c.calculer_preavis_industrie(25) == 210
    assert c.calculer_preavis_btp(25) == 180


def test_preavis_dispatch_retient_le_plus_favorable():
    # Le dispatcher renvoie max(convention, légal) ; l'Industrie domine partout.
    assert c.preavis_jours("INDUSTRIE", 12) == 150
    assert c.preavis_jours("INDUSTRIE", 25) == 210


# ── Indemnité de services rendus — Art. A.32 (20/25/33 %) ─────────────────────
def test_isr_industrie_tranches():
    assert c.calculer_indemnite_services_rendus_industrie(300000, 1)  == 0          # < 2 ans
    assert c.calculer_indemnite_services_rendus_industrie(300000, 2)  == 120000     # 20 %×2
    assert c.calculer_indemnite_services_rendus_industrie(300000, 5)  == 300000     # 20 %×5
    assert c.calculer_indemnite_services_rendus_industrie(300000, 6)  == 450000     # 25 %×6
    assert c.calculer_indemnite_services_rendus_industrie(300000, 15) == 1125000    # 25 %×15
    assert c.calculer_indemnite_services_rendus_industrie(300000, 16) == 1584000    # 33 %×16


def test_isr_industrie_distinct_du_btp():
    # À 6 ans : Industrie 25 % vs BTP 20 %.
    assert c.calculer_indemnite_services_rendus_industrie(300000, 6) == 450000
    assert c.calculer_indemnite_services_rendus_btp(300000, 6) == 360000


def test_isr_dispatch():
    assert c.indemnite_services_rendus("INDUSTRIE", 300000, 16) == 1584000


# ── Prime d'ancienneté — Art. A.46 (identique au BTP : 2 % + 1 %/an) ──────────
def test_anciennete_industrie_reutilise_btp():
    assert c.prime_anciennete("INDUSTRIE", 200000, 1) == 0       # < 2 ans
    assert c.prime_anciennete("INDUSTRIE", 200000, 2) == 4000    # 2 %
    assert c.prime_anciennete("INDUSTRIE", 200000, 5) == 10000   # 5 %
    # Identique au BTP, distinct du Pétrole (5 % à 2 ans).
    assert c.prime_anciennete("INDUSTRIE", 200000, 2) == c.prime_anciennete("BTP", 200000, 2)


# ── Permissions familiales — Art. A.41 (identiques au BTP) ────────────────────
def test_permissions_industrie_reutilise_btp():
    assert c.permissions_familiales("INDUSTRIE", "mariage_travailleur") == 4
    assert c.permissions_familiales("INDUSTRIE", "deces_conjoint_parent_enfant") == 5
    assert c.permissions_familiales("INDUSTRIE", "naissance_enfant") == 3
    assert (c.permissions_familiales("INDUSTRIE", "mariage_enfant")
            == c.permissions_familiales("BTP", "mariage_enfant"))


# ── Non-régression des autres conventions ─────────────────────────────────────
def test_non_regression_autres_conventions():
    assert c.preavis_jours("BTP", 12) == max(125, c.calculer_preavis_code(12))
    assert c.calculer_indemnite_services_rendus_btp(300000, 6) == 360000
    assert c.prime_anciennete("PETROLE", 200000, 2) == 10000


# ── Heures supplémentaires — Art. A.38 (16/35/50/80/135 %) ────────────────────
from datetime import date


def test_heures_sup_industrie_grille():
    assert c.coeffs_heures_sup("INDUSTRIE") == {
        "10": 1.16, "30": 1.35, "30b": 1.50, "40": 1.80, "70": 2.35,
        "fj": 2.00, "fn": 2.50}


def test_heures_sup_industrie_montants():
    th = c.calculer_taux_horaire(200000)
    hs = c.calculer_heures_sup_btp(200000, h10=8, h30=2, h30b=3, h40=4, h70=1,
                                   convention="INDUSTRIE")
    assert hs["taux_10"]  == round(th * 1.16, 4)   # 40-48 h : +16 %
    assert hs["taux_30"]  == round(th * 1.35, 4)   # >48 h  : +35 %
    assert hs["taux_30b"] == round(th * 1.50, 4)   # férié jour : +50 %
    assert hs["taux_40"]  == round(th * 1.80, 4)   # nuit : +80 %
    assert hs["taux_70"]  == round(th * 2.35, 4)   # nuit férié : +135 %
    assert hs["montant_40"] == round(4 * round(th * 1.80, 4), 2)


def test_ventilation_industrie_bande_8h():
    # 5 jours ouvrables × 9 h = 45 h → 40 normales + 5 h en case 10 (bande 41-48).
    jours = [{"date": date(2026, 6, d), "heures": 9, "heures_nuit": 0, "present": True}
             for d in range(8, 13)]   # lundi 8 → vendredi 12 juin 2026
    v = c.ventiler_heures_mois("INDUSTRIE", jours)
    assert round(v["heures_sup_10"], 2) == 5.0
    assert round(v["heures_sup_30"], 2) == 0.0
    assert "heures_sup_30b" in v


def test_ventilation_industrie_au_dela_48h():
    # 5 jours × 10 h = 50 h → 8 h en case 10 (41-48) + 2 h en case 30 (>48).
    jours = [{"date": date(2026, 6, d), "heures": 10, "heures_nuit": 0, "present": True}
             for d in range(8, 13)]
    v = c.ventiler_heures_mois("INDUSTRIE", jours)
    assert round(v["heures_sup_10"], 2) == 8.0
    assert round(v["heures_sup_30"], 2) == 2.0


# ── Indemnités & primes déterministes (A.49, A.55, A.58, A.56, A.48) ──────────
def test_prime_assiduite_industrie():
    assert c.prime_assiduite_industrie() == 3000                     # nominal
    assert c.prime_assiduite_industrie(nb_retards=1) == 2250         # -1/4
    assert c.prime_assiduite_industrie(nb_retards=2) == 1500         # -2/4
    assert c.prime_assiduite_industrie(nb_retards=4) == 0            # -4/4
    assert c.prime_assiduite_industrie(nb_retards=6) == 0            # plancher 0
    assert c.prime_assiduite_industrie(absence_injustifiee=True) == 0


def test_indemnite_transport_industrie():
    assert c.indemnite_transport_industrie(26) == 6240              # plein mois
    assert c.indemnite_transport_industrie(13) == 3120              # demi-mois
    assert c.indemnite_transport_industrie(0)  == 0
    assert c.indemnite_transport_industrie(30) == 6240              # plafonné à 26


def test_indemnite_logement_industrie():
    assert c.indemnite_logement_industrie(200000) == 50000           # 25 %
    assert c.indemnite_logement_industrie(200000, hors_categorie=True) == 24000  # 12 %


def test_indemnite_veuvage_industrie():
    assert c.indemnite_veuvage_industrie(2400000) == 100000          # 1/24


def test_indemnite_deplacement_industrie():
    th = 1000.0
    assert c.indemnite_deplacement_industrie(th, repas=1) == 4000     # 4×
    assert c.indemnite_deplacement_industrie(th, repas=2) == 6000     # 6×
    assert c.indemnite_deplacement_industrie(th, repas=2, couchage=True) == 8000  # 8×


# ── Heures supplémentaires — Art. A.38 (16/35/50/80/135 %) ────────────────────
def test_heures_sup_industrie_coefficients():
    g = c.coeffs_heures_sup("INDUSTRIE")
    assert g == {"10": 1.16, "30": 1.35, "30b": 1.50, "40": 1.80, "70": 2.35,
                 "fj": 2.00, "fn": 2.50}


def test_heures_sup_industrie_ventilation_5_buckets():
    # INDUSTRIE doit produire les 5 cases (bande 41→48 h de 8 h, comme le Pétrole).
    from datetime import date
    jours = [{"date": date(2026, 1, d), "heures": 10.0, "heures_nuit": 0, "present": True}
             for d in range(5, 11)]   # 6 jours × 10 h = 60 h
    res = c.ventiler_heures_mois("INDUSTRIE", jours)
    for k in ("heures_sup_10", "heures_sup_30", "heures_sup_30b",
              "heures_sup_40", "heures_sup_70"):
        assert k in res


# ── Prime d'assiduité — Art. A.49 (3 000 F, −¼/retard, 0 si absence injust.) ──
def test_prime_assiduite_industrie():
    assert c.prime_assiduite_industrie() == 3000
    assert c.prime_assiduite_industrie(nb_retards=1) == 2250    # −1/4
    assert c.prime_assiduite_industrie(nb_retards=2) == 1500
    assert c.prime_assiduite_industrie(nb_retards=4) == 0       # plancher
    assert c.prime_assiduite_industrie(nb_retards=9) == 0       # jamais négatif
    assert c.prime_assiduite_industrie(absence_injustifiee=True) == 0


# ── Indemnité de transport — Art. A.55 (60 F × 2 × 26 ×2 = 6 240 F) ───────────
def test_indemnite_transport_industrie():
    assert c.indemnite_transport_industrie(26) == 6240
    assert c.indemnite_transport_industrie(13) == 3120   # prorata
    assert c.indemnite_transport_industrie(0) == 0


# ── Indemnité de logement — Art. A.58 (25 % / 12 %) ──────────────────────────
def test_indemnite_logement_industrie():
    assert c.indemnite_logement_industrie(200000) == 50000             # 25 %
    assert c.indemnite_logement_industrie(200000, hors_categorie=True) == 24000  # 12 %
    assert c.indemnite_logement_industrie(0) == 0


# ── Indemnité de veuvage — Art. A.56 (1/24 du brut 12 mois) ──────────────────
def test_indemnite_veuvage_industrie():
    # 1/24 du brut total des 12 derniers mois (éligibilité vérifiée par l'appelant).
    assert c.indemnite_veuvage_industrie(2400000) == 100000
    assert c.indemnite_veuvage_industrie(0) == 0


# ── Indemnité de déplacement — Art. A.48 (4× / 6× / 8×) ──────────────────────
def test_indemnite_deplacement_industrie():
    assert c.indemnite_deplacement_industrie(1000) == 4000                       # 1 repas
    assert c.indemnite_deplacement_industrie(1000, repas=2) == 6000             # 2 repas
    assert c.indemnite_deplacement_industrie(1000, repas=2, couchage=True) == 8000  # + couchage
