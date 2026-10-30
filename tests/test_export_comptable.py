"""
tests/test_export_comptable.py — Tests unitaires de l'export comptable Sage 100

Exécution :
    pytest tests/test_export_comptable.py -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock
from export_comptable import (
    generer_journal_paie,
    generer_livre_paie,
    _calculer_totaux,
    _nom_mois,
    _derniere_jour_mois,
    ExportVide,
)


# ── Helpers de création de mocks ──────────────────────────────────────────────

def _bulletin(salaire_base=400_000, salaire_brut=None, **kwargs):
    """Crée un bulletin mock avec des valeurs réalistes."""
    b = MagicMock()
    b.salaire_base      = salaire_base
    b.salaire_brut      = salaire_brut or salaire_base
    b.cnss_salarie      = kwargs.get("cnss_salarie",      salaire_base * 0.05)
    b.cnss_patronale    = kwargs.get("cnss_patronale",    salaire_base * 0.18)
    b.cnamgs_salarie    = kwargs.get("cnamgs_salarie",    salaire_base * 0.015)
    b.cnamgs_patronale  = kwargs.get("cnamgs_patronale",  salaire_base * 0.06)
    b.fnh               = kwargs.get("fnh",               salaire_base * 0.01)
    b.cfp               = kwargs.get("cfp",               salaire_base * 0.02)
    b.tcs               = kwargs.get("tcs",               round(salaire_base * 0.01))
    b.irpp              = kwargs.get("irpp",              5000.0)
    b.net_avant_irpp    = kwargs.get("net_avant_irpp",    salaire_base * 0.90)
    b.salaire_net       = kwargs.get("salaire_net",       salaire_base * 0.85)
    b.acompte           = kwargs.get("acompte",           0.0)
    b.retenue_pret      = kwargs.get("retenue_pret",      0.0)
    b.net_a_payer       = kwargs.get("net_a_payer",       salaire_base * 0.82)
    b.heures_sup_10     = kwargs.get("heures_sup_10",     0.0)
    b.heures_sup_30     = kwargs.get("heures_sup_30",     0.0)
    b.heures_sup_40     = kwargs.get("heures_sup_40",     0.0)
    b.heures_sup_70     = kwargs.get("heures_sup_70",     0.0)
    b.absences          = 0.0
    b.sursalaire        = 0.0
    b.prime_caisse      = kwargs.get("prime_caisse",      0.0)
    b.prime_anciennete  = 0.0
    b.prime_rendement   = 0.0
    b.prime_assiduité   = 0.0
    b.prime_qualite     = 0.0
    b.prime_performance = 0.0
    b.prime_responsabilite = 0.0
    b.prime_transport   = 0.0
    b.carburant         = 0.0
    b.allocations_conge = 0.0
    b.indem_logement    = 0.0
    b.indem_domesticite = 0.0
    b.indem_eau_electricite = 0.0
    b.indem_nourriture  = 0.0
    b.indem_transport   = 0.0
    b.indem_representation = 0.0
    b.prime_panier      = 0.0
    b.prime_salisure    = 0.0
    b.indem_compensatrice_conge   = 0.0
    b.indem_services_rendus       = 0.0
    b.indem_compensatrice_preavis = 0.0
    b.indem_licenciement          = 0.0
    b.statut            = "VALIDE"

    # Salarié mock
    s = MagicMock()
    s.matricule     = kwargs.get("matricule", "EMP001")
    s.nom           = kwargs.get("nom", "DUPONT")
    s.prenom        = kwargs.get("prenom", "Jean")
    s.emploi        = kwargs.get("emploi", "Technicien")
    s.categorie     = MagicMock()
    s.categorie.libelle = kwargs.get("categorie", "Techniciens")
    s.categorie.code    = "C2"
    s.nationalite   = kwargs.get("nationalite", "Gabonaise")
    b.salarie       = s
    return b


def _periode(mois=6, annee=2026):
    p = MagicMock()
    p.mois            = mois
    p.annee           = annee
    p.libelle_complet = f"Juin {annee}"
    p.libelle_mois    = "Juin"
    return p


def _tenant(comptes_paie=None):
    t = MagicMock()
    t.comptes_paie = comptes_paie
    t.denomination = "SOCIÉTÉ TEST GABON"
    t.sigle        = "STG"
    t.nif          = "GA123456"
    return t


# ── Tests helpers ─────────────────────────────────────────────────────────────

class TestHelpers:
    def test_nom_mois_juin(self):
        assert _nom_mois(6) == "Juin"

    def test_nom_mois_janvier(self):
        assert _nom_mois(1) == "Janvier"

    def test_nom_mois_decembre(self):
        assert _nom_mois(12) == "Décembre"

    def test_nom_mois_invalide(self):
        assert _nom_mois(0) == "0"
        assert _nom_mois(13) == "13"

    def test_dernier_jour_janvier(self):
        d = _derniere_jour_mois(2026, 1)
        assert d.day == 31

    def test_dernier_jour_fevrier_2026(self):
        d = _derniere_jour_mois(2026, 2)
        assert d.day == 28

    def test_dernier_jour_fevrier_2024_bissextile(self):
        d = _derniere_jour_mois(2024, 2)
        assert d.day == 29

    def test_dernier_jour_juin(self):
        d = _derniere_jour_mois(2026, 6)
        assert d.day == 30


# ── Tests calcul des totaux ────────────────────────────────────────────────────

class TestCalculerTotaux:
    def test_un_bulletin(self):
        b = _bulletin(400_000)
        t = _calculer_totaux([b])
        assert t["total_brut"] == pytest.approx(400_000.0, abs=1)
        assert t["total_cnss_sal"]  == pytest.approx(20_000.0, abs=1)
        assert t["total_cnss_pat"]  == pytest.approx(72_000.0, abs=1)

    def test_deux_bulletins_additionnes(self):
        b1 = _bulletin(300_000)
        b2 = _bulletin(500_000)
        t = _calculer_totaux([b1, b2])
        assert t["total_brut"] == pytest.approx(800_000.0, abs=1)
        assert t["total_cnss_sal"] == pytest.approx(40_000.0, abs=1)

    def test_cout_employeur_inclut_charges_patronales(self):
        b = _bulletin(400_000)
        t = _calculer_totaux([b])
        assert t["total_cout_employeur"] > t["total_brut"]
        # Coût = brut + cnss_pat + cnamgs_pat + fnh + cfp (la TCS, retenue sur le salarié, n'en fait pas partie)
        attendu = (400_000 + 400_000 * 0.18 + 400_000 * 0.06
                   + 400_000 * 0.01 + 400_000 * 0.02)
        assert t["total_cout_employeur"] == pytest.approx(attendu, abs=1)
        assert t["total_cout_employeur"] == pytest.approx(
            t["total_brut"] + t["total_cnss_pat"] + t["total_cnamgs_pat"] + t["total_fnh"] + t["total_cfp"], abs=0.01)


# ── Tests journal de paie ─────────────────────────────────────────────────────

class TestJournalPaie:
    def test_retourne_bytes(self):
        bulletins = [_bulletin(400_000)]
        contenu = generer_journal_paie(bulletins, _periode(), _tenant())
        assert isinstance(contenu, bytes)
        assert len(contenu) > 0

    def test_encodage_windows_1252(self):
        """Sage 100 nécessite Windows-1252."""
        bulletins = [_bulletin(400_000)]
        contenu = generer_journal_paie(bulletins, _periode(), _tenant())
        # Ne doit pas lever d'exception
        texte = contenu.decode("windows-1252")
        assert "PAI" in texte  # code journal

    @staticmethod
    def _ecritures(contenu):
        out = []
        for ligne in contenu.decode("windows-1252").splitlines():
            if ligne.startswith(";") or not ligne.strip():
                continue
            c = ligne.split("\t")
            m = lambda v: int(round(float(v.replace(" ", "").replace(",", ".")))) if v else 0
            out.append((c[3], m(c[5]), m(c[6])))
        return out

    def test_comptes_syscohada_par_defaut(self):
        """Charges de personnel en classe 66 (et non 641/645, comptes d'impôts et taxes en SYSCOHADA)."""
        contenu = generer_journal_paie([_bulletin(400_000)], _periode(), _tenant())
        comptes = {c for c, _, _ in self._ecritures(contenu)}
        assert {"661", "664", "6413", "431", "433", "447", "442", "422"} <= comptes
        assert not any(c.startswith(("641100", "645")) for c in comptes)
        assert "proposés par défaut" in contenu.decode("windows-1252")

    def test_journal_equilibre(self):
        """Débit = crédit, y compris TCS (retenue salariale), FNH/CFP, acompte et éléments hors brut."""
        b1 = _bulletin(400_000, acompte=50_000, net_a_payer=400_000 - 20_000 - 6_000 - 4_000 - 5_000 - 50_000 + 25_000,
                       cnss_salarie=20_000, cnamgs_salarie=6_000, tcs=4_000, irpp=5_000)
        b2 = _bulletin(500_000, nationalite="Camerounaise")
        e = self._ecritures(generer_journal_paie([b1, b2], _periode(), _tenant()))
        assert sum(d for _, d, _ in e) == sum(c for _, _, c in e)
        par = {}
        for c, d, cr in e:
            par[c] = par.get(c, 0) + d - cr
        assert par["661"] == 400_000 and par["662"] == 500_000
        assert par["421"] == -50_000                                   # acompte imputé

    def test_comptes_personnalises(self):
        import json
        cpt = {"brut_national": "661100", "brut_non_national": "662100", "journaliers": "661100", "hors_brut": "663800",
               "charges_sociales": "664100", "taxes_salaires": "641300", "cnss": "431100", "cnamgs": "431200",
               "impots_retenus": "447200", "taxes_dues": "442800", "avances": "421000", "net": "422000"}
        contenu = generer_journal_paie([_bulletin(400_000)], _periode(),
                                       _tenant(json.dumps({"comptes": cpt, "journal": "SAL"})))
        texte = contenu.decode("windows-1252")
        comptes = {c for c, _, _ in self._ecritures(contenu)}
        assert "661100" in comptes and "431200" in comptes and "661" not in comptes
        assert texte.splitlines()[-1].startswith("SAL\t") and "paramétrés par l'entreprise" in texte

    def test_formulaire_comptes(self):
        import plan_comptable_paie as pcp
        form = {f"compte_{k}": d for k, d in pcp.DEFAUTS.items()}
        assert pcp.lire_formulaire({**form, "journal": "pai"})["journal"] == "PAI"
        with pytest.raises(ValueError):
            pcp.lire_formulaire({**form, "compte_net": "42A", "journal": "PAI"})
        with pytest.raises(ValueError):
            pcp.lire_formulaire({**form, "journal": "TROP-LONG"})
        assert pcp.est_national("GABONAISE") and pcp.est_national("Gabonais") and not pcp.est_national("Malienne")

    def test_libelle_periode_present(self):
        bulletins = [_bulletin(400_000)]
        contenu = generer_journal_paie(bulletins, _periode(mois=3, annee=2026), _tenant())
        texte = contenu.decode("windows-1252")
        assert "Mars" in texte
        assert "2026" in texte

    def test_plusieurs_bulletins(self):
        bulletins = [_bulletin(300_000, matricule="E1"), _bulletin(500_000, matricule="E2")]
        contenu = generer_journal_paie(bulletins, _periode(), _tenant())
        assert len(contenu) > 0

    def test_liste_vide_leve_exception(self):
        with pytest.raises(ExportVide):
            generer_journal_paie([], _periode(), _tenant())


# ── Tests livre de paie ───────────────────────────────────────────────────────

class TestLivrePaie:
    def test_retourne_bytes(self):
        bulletins = [_bulletin(400_000)]
        contenu = generer_livre_paie(bulletins, _periode(), _tenant())
        assert isinstance(contenu, bytes)

    def test_encodage_utf8_bom(self):
        """UTF-8 avec BOM pour compatibilité Excel."""
        bulletins = [_bulletin(400_000)]
        contenu = generer_livre_paie(bulletins, _periode(), _tenant())
        # BOM UTF-8 = EF BB BF
        assert contenu[:3] == b"\xef\xbb\xbf"

    def test_contient_en_tete_societe(self):
        bulletins = [_bulletin(400_000)]
        contenu = generer_livre_paie(bulletins, _periode(), _tenant())
        texte = contenu.decode("utf-8-sig")
        assert "SOCIÉTÉ TEST GABON" in texte

    def test_contient_matricule_salarie(self):
        bulletins = [_bulletin(400_000, matricule="EMP999")]
        contenu = generer_livre_paie(bulletins, _periode(), _tenant())
        texte = contenu.decode("utf-8-sig")
        assert "EMP999" in texte

    def test_contient_ligne_totaux(self):
        bulletins = [_bulletin(400_000), _bulletin(600_000)]
        contenu = generer_livre_paie(bulletins, _periode(), _tenant())
        texte = contenu.decode("utf-8-sig")
        assert "TOTAUX" in texte

    def test_contient_recap_charges(self):
        bulletins = [_bulletin(400_000)]
        contenu = generer_livre_paie(bulletins, _periode(), _tenant())
        texte = contenu.decode("utf-8-sig")
        assert "RÉCAPITULATIF" in texte
        assert "COÛT TOTAL EMPLOYEUR" in texte

    def test_salaries_tries_par_nom(self):
        b1 = _bulletin(400_000, nom="ZOGO",   prenom="Pierre")
        b2 = _bulletin(400_000, nom="AKOMO",  prenom="Marie")
        b3 = _bulletin(400_000, nom="MBOULA", prenom="Paul")
        contenu = generer_livre_paie([b1, b2, b3], _periode(), _tenant())
        texte = contenu.decode("utf-8-sig")
        pos_akomo  = texte.find("AKOMO")
        pos_mboula = texte.find("MBOULA")
        pos_zogo   = texte.find("ZOGO")
        assert pos_akomo < pos_mboula < pos_zogo

    def test_total_net_coherent(self):
        """Le total net dans le récap doit correspondre à la somme des nets."""
        b1 = _bulletin(300_000, net_a_payer=240_000)
        b2 = _bulletin(500_000, net_a_payer=400_000)
        contenu = generer_livre_paie([b1, b2], _periode(), _tenant())
        texte = contenu.decode("utf-8-sig")
        # 240 000 + 400 000 = 640 000
        assert "640000" in texte

    def test_liste_vide_leve_exception(self):
        with pytest.raises(ExportVide):
            generer_livre_paie([], _periode(), _tenant())
