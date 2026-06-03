"""
tests/test_declaration_cnss.py — Tests unitaires du module déclaration CNSS

Exécution :
    pytest tests/test_declaration_cnss.py -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock
from declaration_cnss import (
    generer_csv_cnss,
    generer_csv_cnamgs,
    calculer_trimestre,
    _noms_mois_trim,
    CNSS_TAUX_SAL, CNSS_TAUX_PAT,
    CNAMGS_TAUX_SAL, CNAMGS_TAUX_PAT,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _periode(mois=3, annee=2026):
    p = MagicMock()
    p.mois  = mois
    p.annee = annee
    return p


def _tenant():
    t = MagicMock()
    t.denomination = "SOCIÉTÉ TEST GABON"
    t.nif          = "GA123456"
    t.numero_cnss  = "EMP-00123"
    t.numero_cnamgs = "CNAMGS-00456"
    return t


def _sal(nom="DUPONT Jean", matricule="E001",
         cnss="CNS001", cnamgs="CNA001",
         m1=400_000, m2=400_000, m3=400_000):
    return {
        "nom_complet":    nom,
        "matricule":      matricule,
        "numero_cnss":    cnss,
        "numero_cnamgs":  cnamgs,
        "date_embauche":  "01/01/2020",
        "m1_base_cnss":   m1,
        "m2_base_cnss":   m2,
        "m3_base_cnss":   m3,
        "m1_base_cnamgs": m1,
        "m2_base_cnamgs": m2,
        "m3_base_cnamgs": m3,
    }


# ── Tests calculer_trimestre ──────────────────────────────────────────────────

class TestCalculerTrimestre:
    def test_t1_janvier(self):
        num, debut, fin, label = calculer_trimestre(1)
        assert num == 1
        assert debut == 1
        assert fin   == 3
        assert "T1"  in label

    def test_t1_mars(self):
        num, debut, fin, _ = calculer_trimestre(3)
        assert num == 1 and debut == 1 and fin == 3

    def test_t2_avril(self):
        num, debut, fin, label = calculer_trimestre(4)
        assert num == 2 and debut == 4 and fin == 6

    def test_t3_juillet(self):
        num, debut, fin, label = calculer_trimestre(7)
        assert num == 3 and debut == 7 and fin == 9

    def test_t4_decembre(self):
        num, debut, fin, label = calculer_trimestre(12)
        assert num == 4 and debut == 10 and fin == 12

    def test_label_contient_mois(self):
        _, _, _, label = calculer_trimestre(4)
        assert "Avr" in label or "Jun" in label


# ── Tests noms mois ───────────────────────────────────────────────────────────

class TestNomsMois:
    def test_t1(self):
        noms = _noms_mois_trim(1, 2026)
        assert len(noms) == 3
        assert "Jan" in noms[0]
        assert "Fév" in noms[1]
        assert "Mar" in noms[2]

    def test_t4(self):
        noms = _noms_mois_trim(10, 2026)
        assert "Oct" in noms[0]
        assert "Nov" in noms[1]
        assert "Déc" in noms[2]

    def test_annee_presente(self):
        noms = _noms_mois_trim(1, 2026)
        assert "2026" in noms[0]


# ── Tests CSV CNSS ────────────────────────────────────────────────────────────

class TestCsvCnss:
    def test_retourne_bytes(self):
        sal = [_sal()]
        r = generer_csv_cnss(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert isinstance(r, bytes)
        assert len(r) > 0

    def test_encodage_utf8_bom(self):
        sal = [_sal()]
        r = generer_csv_cnss(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert r[:3] == b"\xef\xbb\xbf"

    def test_contient_nom_entreprise(self):
        sal = [_sal()]
        r = generer_csv_cnss(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert "SOCIÉTÉ TEST GABON" in r.decode("utf-8-sig")

    def test_contient_matricule_salarie(self):
        sal = [_sal(matricule="EMP999")]
        r = generer_csv_cnss(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert "EMP999" in r.decode("utf-8-sig")

    def test_contient_numero_cnss(self):
        sal = [_sal(cnss="CNS-12345")]
        r = generer_csv_cnss(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert "CNS-12345" in r.decode("utf-8-sig")

    def test_total_cotisations_correct(self):
        """
        Salarié avec base 400 000 × 3 mois = 1 200 000
        CNSS total = 1 200 000 × 23% = 276 000
        """
        sal = [_sal(m1=400_000, m2=400_000, m3=400_000)]
        r = generer_csv_cnss(sal, _periode(3, 2026), _tenant(), 1, 3)
        texte = r.decode("utf-8-sig")
        assert "276000" in texte

    def test_ligne_totaux_presente(self):
        sal = [_sal(), _sal(nom="MARTIN Paul", matricule="E002")]
        r = generer_csv_cnss(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert "TOTAL" in r.decode("utf-8-sig")

    def test_recap_present(self):
        sal = [_sal()]
        r = generer_csv_cnss(sal, _periode(3, 2026), _tenant(), 1, 3)
        texte = r.decode("utf-8-sig")
        assert "RÉCAPITULATIF" in texte
        assert "TOTAL À VERSER CNSS" in texte

    def test_trimestre_t2(self):
        sal = [_sal()]
        r = generer_csv_cnss(sal, _periode(6, 2026), _tenant(), 4, 6)
        texte = r.decode("utf-8-sig")
        assert "T2" in texte
        assert "Avr" in texte

    def test_plusieurs_salaries(self):
        sal = [_sal("DUPONT Jean", "E001"), _sal("MARTIN Paul", "E002"),
               _sal("NZAMBA Marie", "E003")]
        r = generer_csv_cnss(sal, _periode(3, 2026), _tenant(), 1, 3)
        texte = r.decode("utf-8-sig")
        assert "E001" in texte
        assert "E002" in texte
        assert "E003" in texte


# ── Tests CSV CNAMGS ──────────────────────────────────────────────────────────

class TestCsvCnamgs:
    def test_retourne_bytes(self):
        sal = [_sal()]
        r = generer_csv_cnamgs(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert isinstance(r, bytes)

    def test_encodage_utf8_bom(self):
        sal = [_sal()]
        r = generer_csv_cnamgs(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert r[:3] == b"\xef\xbb\xbf"

    def test_contient_cnamgs_dans_titre(self):
        sal = [_sal()]
        r = generer_csv_cnamgs(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert "CNAMGS" in r.decode("utf-8-sig")

    def test_taux_cnamgs_correct(self):
        """
        Base 400 000 × 3 mois = 1 200 000
        CNAMGS total = 1 200 000 × 7,5% = 90 000
        """
        sal = [_sal(m1=400_000, m2=400_000, m3=400_000)]
        r = generer_csv_cnamgs(sal, _periode(3, 2026), _tenant(), 1, 3)
        texte = r.decode("utf-8-sig")
        assert "90000" in texte

    def test_numero_cnamgs_present(self):
        sal = [_sal(cnamgs="CNAM-789")]
        r = generer_csv_cnamgs(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert "CNAM-789" in r.decode("utf-8-sig")

    def test_recap_present(self):
        sal = [_sal()]
        r = generer_csv_cnamgs(sal, _periode(3, 2026), _tenant(), 1, 3)
        assert "TOTAL À VERSER CNAMGS" in r.decode("utf-8-sig")


# ── Tests cohérence CNSS vs CNAMGS ────────────────────────────────────────────

class TestCoherenceCnssVsCnamgs:
    def test_cnss_plus_eleve_que_cnamgs(self):
        """CNSS 23% > CNAMGS 7,5% — les montants CNSS doivent toujours être plus élevés."""
        assert (CNSS_TAUX_SAL + CNSS_TAUX_PAT) > (CNAMGS_TAUX_SAL + CNAMGS_TAUX_PAT)

    def test_fichiers_differents(self):
        """Les deux fichiers ne doivent pas être identiques."""
        sal = [_sal()]
        p = _periode()
        t = _tenant()
        csv1 = generer_csv_cnss(sal, p, t, 1, 3)
        csv2 = generer_csv_cnamgs(sal, p, t, 1, 3)
        assert csv1 != csv2
