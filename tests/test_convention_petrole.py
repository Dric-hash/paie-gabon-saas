# -*- coding: utf-8 -*-
"""Tests unitaires — Convention Collective des professionnels du pétrole (Gabon).

Référence : Convention SGEPP/GPP du 17 juin 1983 (stockage/distribution).
Couvre les barèmes propres au Pétrole, la 5ᵉ case d'heures sup (30b), les
dispatchers, et la NON-RÉGRESSION des conventions BTP/Commerce.
"""
from datetime import date
import calculs_paie as c


# ── Prime d'ancienneté — Art. 46.5 (5 % à 2 ans, +1 %/an) ─────────────────────
def test_anciennete_petrole():
    assert c.calculer_prime_anciennete_petrole(200000, 1) == 0        # < 2 ans
    assert c.calculer_prime_anciennete_petrole(200000, 2) == 10000    # 5 %
    assert c.calculer_prime_anciennete_petrole(200000, 3) == 12000    # 6 %
    assert c.calculer_prime_anciennete_petrole(200000, 5) == 16000    # 8 %
    assert c.calculer_prime_anciennete_petrole(200000, 40) == 60000   # plafond 30 %


def test_anciennete_petrole_distinct_du_btp():
    # À 2 ans, le Pétrole (5 %) est plus favorable que le BTP/Commerce (2 %).
    assert c.calculer_prime_anciennete_petrole(200000, 2) == 10000
    assert c.calculer_prime_anciennete_btp(200000, 2) == 4000
    assert c.calculer_prime_anciennete_commerce(200000, 2) == 4000


# ── Indemnité de services rendus — Art. 32 (20/25/30/40 %) ────────────────────
def test_isr_petrole_tranches():
    assert c.calculer_indemnite_services_rendus_petrole(300000, 0) == 0        # < 1 an
    assert c.calculer_indemnite_services_rendus_petrole(300000, 1) == 60000     # 20 %×1
    assert c.calculer_indemnite_services_rendus_petrole(300000, 3) == 180000    # 20 %×3
    assert c.calculer_indemnite_services_rendus_petrole(300000, 8) == 600000    # 25 %×8
    assert c.calculer_indemnite_services_rendus_petrole(300000, 12) == 1080000  # 30 %×12
    assert c.calculer_indemnite_services_rendus_petrole(300000, 20) == 2400000  # 40 %×20


def test_isr_petrole_min_anciennete_cadre():
    # Agent de maîtrise / cadre : minimum 2 ans.
    assert c.calculer_indemnite_services_rendus_petrole(300000, 1, min_anciennete=2) == 0
    assert c.calculer_indemnite_services_rendus_petrole(300000, 2, min_anciennete=2) == 120000


# ── Préavis — Art. 30.3 (renvoi au Code du travail) ───────────────────────────
def test_preavis_petrole_egal_legal():
    for a in (0, 2, 4, 8, 12, 18, 25, 35):
        assert c.preavis_jours("PETROLE", a) == c.calculer_preavis_code(a)


# ── Coefficients d'heures sup par convention ──────────────────────────────────
def test_coeffs_heures_sup_petrole():
    cf = c.coeffs_heures_sup("PETROLE")
    assert cf == {"10": 1.20, "30": 1.35, "30b": 1.30, "40": 1.50, "70": 2.00}


def test_coeffs_heures_sup_non_regression():
    # BTP, Commerce et AUCUNE conservent la grille historique 10/30/40/70.
    for conv in ("BTP", "COMMERCE", "AUCUNE", None):
        cf = c.coeffs_heures_sup(conv)
        assert cf["10"] == 1.10 and cf["30"] == 1.30
        assert cf["40"] == 1.40 and cf["70"] == 1.70


# ── calculer_heures_sup_btp : montants par convention + case 30b ──────────────
def test_heures_sup_montants_petrole():
    # Taux horaire = 1000 F (salaire 173 330 / 173,33).
    hs = c.calculer_heures_sup_btp(173330, h10=10, h30=5, h30b=4, h40=2, h70=1,
                                   convention="PETROLE")
    th = hs["taux_horaire"]
    assert abs(hs["montant_10"]  - round(10 * round(th * 1.20, 4), 2)) < 0.01
    assert abs(hs["montant_30b"] - round(4  * round(th * 1.30, 4), 2)) < 0.01
    assert abs(hs["montant_40"]  - round(2  * round(th * 1.50, 4), 2)) < 0.01
    assert abs(hs["montant_70"]  - round(1  * round(th * 2.00, 4), 2)) < 0.01


def test_heures_sup_montants_btp_inchange():
    # Sans convention → coefficients historiques, montant_30b = 0.
    hs = c.calculer_heures_sup_btp(173330, h10=10, h30=5, h40=2, h70=1)
    th = hs["taux_horaire"]
    assert abs(hs["montant_10"] - round(10 * round(th * 1.10, 4), 2)) < 0.01
    assert hs["montant_30b"] == 0.0


# ── distribuer_heures_semaine_petrole (hebdo) ─────────────────────────────────
def test_distrib_semaine_petrole_jour_ouvrable():
    d = c.distribuer_heures_semaine_petrole([{"heures_normales": 50, "type_jour": "NORMAL"}])
    assert d["heures_normales"] == 40.0
    assert d["heures_sup_10"] == 8.0    # 41ᵉ-48ᵉ (+20 %)
    assert d["heures_sup_30"] == 2.0    # >48ᵉ (+35 %)


def test_distrib_semaine_petrole_dimanche():
    d = c.distribuer_heures_semaine_petrole(
        [{"heures_normales": 8, "heures_sup_nuit": 2, "type_jour": "DIMANCHE"}])
    assert d["heures_sup_30b"] == 8.0   # jour dimanche → +30 %
    assert d["heures_sup_70"] == 2.0    # nuit dimanche → +100 %
    assert d["heures_normales"] == 0.0


# ── ventiler_heures_mois (mensuel, semaine par semaine) ───────────────────────
def test_ventilation_mois_petrole_dimanche_jour():
    # Dimanche 7 juin 2026, 8h de jour → case 30b.
    jours = [{"date": date(2026, 6, 7), "heures": 8, "heures_nuit": 0, "present": True}]
    v = c.ventiler_heures_mois("PETROLE", jours)
    assert v["heures_sup_30b"] == 8.0
    assert v["heures_sup_70"] == 0.0


def test_ventilation_mois_petrole_semaine_chargee():
    # Lun-Sam (1-6 juin 2026), 9h/j ouvrable = 54h cumul hebdo.
    jours = [{"date": date(2026, 6, d), "heures": 9, "heures_nuit": 0, "present": True}
             for d in range(1, 7)]
    v = c.ventiler_heures_mois("PETROLE", jours)
    assert v["heures_normales"] == 40.0
    assert v["heures_sup_10"] == 8.0     # 41ᵉ-48ᵉ
    assert v["heures_sup_30"] == 6.0     # >48ᵉ
    assert v["heures_sup_30b"] == 0.0


def test_ventilation_mois_btp_inclut_cle_30b_a_zero():
    # Le dispatcher garantit la présence de la case 30b (=0) pour le BTP.
    jours = [{"date": date(2026, 6, d), "heures": 8, "heures_nuit": 0, "present": True}
             for d in range(1, 6)]
    v = c.ventiler_heures_mois("BTP", jours)
    assert v.get("heures_sup_30b", 0.0) == 0.0


# ── calculer_bulletin : case 30b portée au brut ───────────────────────────────
def test_bulletin_petrole_30b_dans_brut():
    base = c.calculer_bulletin({"salaire_base": 200000, "convention": "PETROLE"})
    avec = c.calculer_bulletin({"salaire_base": 200000, "heures_sup_30b": 5000,
                                "convention": "PETROLE"})
    assert avec["heures_sup_30b"] == 5000.0
    assert round(avec["salaire_brut"] - base["salaire_brut"], 2) == 5000.0


def test_bulletin_taux_affichage_petrole():
    b = c.calculer_bulletin({"salaire_base": 173330, "convention": "PETROLE"})
    th = b["taux_horaire_base"]
    assert abs(b["taux_horaire_10"] - round(th * 1.20, 4)) < 0.001
    assert abs(b["taux_horaire_30b"] - round(th * 1.30, 4)) < 0.001
    assert abs(b["taux_horaire_70"] - round(th * 2.00, 4)) < 0.001


def test_bulletin_sans_convention_inchange():
    # Aucune convention transmise → taux historiques, 30b absent du brut.
    b = c.calculer_bulletin({"salaire_base": 173330})
    th = b["taux_horaire_base"]
    assert abs(b["taux_horaire_10"] - round(th * 1.10, 4)) < 0.001
    assert b["heures_sup_30b"] == 0.0


# ── Dispatchers ───────────────────────────────────────────────────────────────
def test_dispatchers_petrole():
    assert "PETROLE" in c.CONVENTIONS_DISPONIBLES
    assert c.prime_anciennete("PETROLE", 200000, 2) == 10000
    assert c.indemnite_services_rendus("PETROLE", 300000, 8) == 600000
    assert c.permissions_familiales("PETROLE", "mariage_travailleur") == 4
    # distribuer_heures_semaine route bien vers le Pétrole (case 30b présente)
    d = c.distribuer_heures_semaine("PETROLE",
                                    [{"heures_normales": 8, "type_jour": "DIMANCHE"}])
    assert d["heures_sup_30b"] == 8.0


# ── Indemnité de rupture (favour + non-cumul) sous convention Pétrole ─────────
def test_rupture_petrole_licenciement_favorable():
    # Licenciement 8 ans : max(légal 20 %/an, ISR Pétrole 25 %×8).
    r = c.indemnite_rupture("PETROLE", "LICENCIEMENT", 300000, 8)
    assert r["type"] == "LICENCIEMENT"
    assert r["montant"] == 600000   # 25 %×8 (conv) > 20 %×8 (légal)


# ── Grille conventionnelle ────────────────────────────────────────────────────
def test_grille_petrole_presente():
    codes = [g[0] for g in c.GRILLE_PETROLE]
    for code in ("A", "I", "AMI", "AMS", "CP0", "HC"):
        assert code in codes
    hc = next(g for g in c.GRILLE_PETROLE if g[0] == "HC")
    assert hc[2] == 1_200_000
