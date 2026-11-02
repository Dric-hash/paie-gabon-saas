"""Revue des conventions (01/11/2026) : chaque convention applique SA règle d'heures sup
(largeur du 1ᵉʳ palier, repos/férié de jour en case 30b, fériés distincts), ses taux sur
les écrans et les bulletins, et les heures du pointage sont converties en montants.

Bugs corrigés, chacun couvert ici :
  • import Excel du pointage : les heures étaient passées au moteur comme des montants ;
  • un dimanche Pétrole/Industrie saisi en case 30b disparaissait du mois ;
  • Hydrocarbures absente de la liste du moteur : prime d'ancienneté à 0 ;
  • Commerce, Hôtellerie, Bois, Forêt, Hydrocarbures, Minier ventilés au régime BTP
    (palier de 4 h, dimanche de jour payé au taux de nuit repos/férié) ;
  • Commerce sans grille propre : nuit à +40 % et dimanche à +70 % au lieu de +70 % / +40 %."""
from datetime import date
from types import SimpleNamespace as N

import pytest

from test_suppression_salarie import env, _cli, flask_app  # noqa: F401
import calculs_paie as c
import models as M
from models import db

DIM = date(2026, 10, 4)                     # dimanche
SEMAINE = [date(2026, 10, d) for d in range(5, 10)]   # lundi 5 → vendredi 9 octobre 2026
CONV_30B = ("COMMERCE", "PETROLE", "HYDROCARBURES", "INDUSTRIE", "AERIEN", "HOTELLERIE", "BOIS", "MINIER", "FORET")


def _jours(heures_par_jour, nuit=0.0, dates=SEMAINE):
    return [{"date": d, "heures": heures_par_jour, "heures_nuit": nuit} for d in dates]


# ── Registre des conventions ──────────────────────────────────────────────────
def test_hydrocarbures_enregistree_et_anciennete_versee():
    assert c._conv("HYDROCARBURES") == "HYDROCARBURES"
    # Art. 46 : 2 % à 2 ans, +1 %/an, sans plafond → 10 ans = 10 %
    assert c.prime_anciennete("HYDROCARBURES", 300000, 10) == 30000
    r = c.calculer_bulletin({"salaire_base": 300000, "anciennete_annees": 10, "convention": "HYDROCARBURES"})
    assert r["prime_anciennete"] == 30000


def test_commerce_grille_propre_a38():
    cf = c.coeffs_heures_sup("COMMERCE")
    assert (cf["10"], cf["30"], cf["30b"], cf["40"], cf["70"]) == (
        c.COEFF_COMMERCE_JOUR_10, c.COEFF_COMMERCE_JOUR_30, c.COEFF_COMMERCE_FERIE_JOUR,
        c.COEFF_COMMERCE_NUIT, c.COEFF_COMMERCE_FERIE_NUIT)


def test_btp_et_code_inchanges():
    for conv in ("BTP", "AUCUNE", None):
        cf = c.coeffs_heures_sup(conv)
        assert (cf["10"], cf["30"], cf["40"], cf["70"]) == (1.10, 1.30, 1.40, 1.70)
        assert c.regime_heures_sup(conv) == {"palier": 4.0, "repos": "70", "feries": False}


# ── Ventilation hebdomadaire ──────────────────────────────────────────────────
@pytest.mark.parametrize("conv,h10,h30", [
    ("BTP", 4, 6), ("AUCUNE", 4, 6),
    ("COMMERCE", 8, 2), ("PETROLE", 8, 2), ("HYDROCARBURES", 8, 2), ("INDUSTRIE", 8, 2),
    ("AERIEN", 8, 2), ("HOTELLERIE", 8, 2), ("BOIS", 8, 2), ("MINIER", 8, 2),
    ("FORET", 6, 4),
])
def test_largeur_du_premier_palier(conv, h10, h30):
    v = c.ventiler_heures_mois(conv, _jours(10.0))           # 50 h dans la semaine
    assert (v["heures_normales"], v["heures_sup_10"], v["heures_sup_30"]) == (40, h10, h30)


@pytest.mark.parametrize("conv", CONV_30B)
def test_dimanche_jour_en_30b_nuit_en_70(conv):
    v = c.ventiler_heures_mois(conv, _jours(6.0, nuit=2.0, dates=[DIM]))
    assert (v["heures_sup_30b"], v["heures_sup_70"]) == (6.0, 2.0)
    assert v["heures_sup_40"] == 0 and v["total_heures_sup"] == 8.0


def test_btp_dimanche_en_bloc_a_70():
    v = c.ventiler_heures_mois("BTP", _jours(6.0, nuit=2.0, dates=[DIM]))
    assert (v["heures_sup_30b"], v["heures_sup_70"]) == (0.0, 8.0)


@pytest.mark.parametrize("conv", ["MINIER", "FORET"])
def test_ferie_chome_paye_travaille_distinct(conv):
    ferie = date(2026, 11, 1)          # Toussaint (dimanche en 2026) : le férié prime
    v = c.ventiler_heures_mois(conv, [{"date": ferie, "heures": 6.0, "heures_nuit": 2.0, "ferie": True}])
    assert (v["heures_sup_fj"], v["heures_sup_fn"], v["heures_sup_30b"], v["heures_sup_70"]) == (6.0, 2.0, 0.0, 0.0)
    assert v["total_heures_sup"] == 8.0


def test_pointage_30b_compte_dans_le_mois():
    p = N(date_pointage=DIM, type_jour="DIMANCHE", heures_normales=0, heures_sup_10=0, heures_sup_30=0,
          heures_sup_30b=8, heures_sup_40=0, heures_sup_70=0, present=True, absent=False,
          entree_matin=None, sortie_matin=None, entree_apmidi=None, sortie_apmidi=None,
          entree_sup=None, sortie_sup=None)
    v = c.ventiler_heures_mois("PETROLE", c.pointage_vers_jours([p]))
    assert v["heures_sup_30b"] == 8.0


def test_pointage_nuit_du_dimanche_isolee():
    p = N(date_pointage=DIM, type_jour="DIMANCHE", heures_normales=0, heures_sup_10=0, heures_sup_30=0,
          heures_sup_30b=0, heures_sup_40=0, heures_sup_70=6, present=True, absent=False,
          entree_matin=None, sortie_matin=None, entree_apmidi="17:00", sortie_apmidi="23:00",
          entree_sup=None, sortie_sup=None)
    j = c.pointage_vers_jours([p])
    assert (j[0]["heures"], j[0]["heures_nuit"]) == (4.0, 2.0)
    # Hôtellerie : 4 h de jour à +30 % (30b), 2 h de nuit à +110 % (70) ; BTP : 6 h à +70 %
    v = c.ventiler_heures_mois("HOTELLERIE", j)
    assert (v["heures_sup_30b"], v["heures_sup_70"]) == (4.0, 2.0)
    assert c.ventiler_heures_mois("BTP", j)["heures_sup_70"] == 6.0


# ── Heures → montants ─────────────────────────────────────────────────────────
def test_montants_au_franc():
    # Taux horaire = 173 330 / 173,33 = 1 000 F exactement.
    hs = c.calculer_heures_sup_btp(173330, h10=8, h30=2, h30b=6, h40=3, h70=2, hfj=4, hfn=1, convention="MINIER")
    assert hs["taux_horaire"] == 1000.0
    assert (hs["montant_10"], hs["montant_30"], hs["montant_30b"], hs["montant_40"], hs["montant_70"],
            hs["montant_fj"], hs["montant_fn"]) == (9760.0, 3000.0, 9060.0, 5040.0, 4040.0, 8000.0, 2500.0)
    assert hs["total_sup"] == 41400.0


# ── Libellés : les taux réels de la convention ────────────────────────────────
def test_libelles():
    assert c.libelles_heures_sup("PETROLE")["10"] == "H. sup. de jour, 8 premières +20 %"
    assert c.libelles_heures_sup("BTP")["10"] == "H. sup. de jour, 4 premières +10 %"
    assert c.libelles_heures_sup("BTP")["70"] == "H. sup. dimanche/férié +70 %"
    assert c.libelles_heures_sup("COMMERCE")["40"] == "H. sup. de nuit +70 %"
    assert c.libelles_heures_sup("HOTELLERIE")["70"] == "H. sup. repos/férié, nuit +110 %"
    assert c.libelles_heures_sup("INDUSTRIE")["70"] == "H. sup. repos/férié, nuit +135 %"
    assert c.libelles_heures_sup("FORET")["10"] == "H. sup. de jour, 6 premières +10 %"


def test_permissions_par_convention():
    assert c.permissions_familiales("MINIER", "demenagement") == 1
    assert c.permissions_familiales("HOTELLERIE", "deces_beau_parent") == 2
    assert c.permissions_familiales("BOIS", "naissance_enfant") == 3


# ── Bout en bout ──────────────────────────────────────────────────────────────
def _tenant(env, convention):
    t = db.session.get(M.Tenant, env["t"])
    t.convention = convention
    s = db.session.get(M.Salarie, env["s"])
    s.statut = "ACTIF"
    db.session.add(M.Contrat(tenant_id=t.id, salarie_id=s.id, date_debut=date(2026, 1, 1),
                             salaire_base=173330, actif=True))
    db.session.add(M.PeriodePaie(tenant_id=t.id, annee=2026, mois=10, libelle_mois="OCTOBRE"))
    db.session.commit()
    return t, s


def test_import_pointage_excel_convertit_les_heures(env):
    with flask_app.app_context():
        t, s = _tenant(env, "PETROLE")
        cli = _cli(env["admin"])
        with cli.session_transaction() as ses:
            ses["pointage_sal"] = {"annee": 2026, "mois": 10, "lignes": [{
                "salarie_id": s.id, "matricule": s.matricule, "nom": "X",
                "heures_sup_10": 8, "heures_sup_30": 2, "heures_sup_30b": 6,
                "heures_sup_40": 0, "heures_sup_70": 0}]}
        rep = cli.post("/salaries/pointage/confirmer")
        assert rep.status_code == 302
        b = M.BulletinPaie.query.filter_by(tenant_id=t.id, salarie_id=s.id).one()
        # Pétrole : 8 h × 1 200 + 2 h × 1 350 + 6 h × 1 300 (taux horaire 1 000 F)
        assert (float(b.heures_sup_10), float(b.heures_sup_30), float(b.heures_sup_30b)) == (9600.0, 2700.0, 7800.0)
        assert float(b.salaire_brut) == 173330 + 9600 + 2700 + 7800
        assert (float(b.base_heures_sup_10), b.taux_heures_sup_10) == (1200.0, "8")


def test_api_pointage_mois_hotellerie(env):
    with flask_app.app_context():
        t, s = _tenant(env, "HOTELLERIE")
        pts = [M.Pointage(tenant_id=t.id, salarie_id=s.id, date_pointage=d, heures_normales=10,
                          type_jour="NORMAL", statut="VALIDE") for d in SEMAINE]
        pts.append(M.Pointage(tenant_id=t.id, salarie_id=s.id, date_pointage=DIM, heures_normales=0,
                              heures_sup_70=8, type_jour="DIMANCHE", statut="VALIDE"))
        db.session.add_all(pts)
        db.session.commit()
        d = _cli(env["admin"]).get(f"/api/salarie/{s.id}/pointage-mois?mois=10&annee=2026").get_json()
        # Semaine de 50 h : 8 h à +15 %, 2 h à +30 % ; dimanche de jour : 8 h à +30 % (30b)
        assert (d["heures_sup_10"], d["heures_sup_30"], d["heures_sup_30b"], d["heures_sup_70"]) == (8, 2, 8, 0)


def test_ecran_saisie_taux_de_la_convention(env):
    with flask_app.app_context():
        t, s = _tenant(env, "PETROLE")
        page = _cli(env["admin"]).get("/bulletins/saisie").get_data(as_text=True)
        assert '"10": 1.2' in page and "H. sup. de jour, 8 premières +20 %" in page
        assert 'name="heures_sup_30b"' in page          # case repos/férié affichée hors Pétrole aussi
        assert "const C10 = 1.10" not in page


@pytest.mark.parametrize("modele", ["classique", "moderne", "minimaliste", "grandlivre", "sgtg"])
def test_bulletins_imprimes_avec_les_taux_de_la_convention(env, modele):
    with flask_app.app_context():
        t, s = _tenant(env, "PETROLE")
        t.modele_bulletin = modele
        per = M.PeriodePaie.query.filter_by(tenant_id=t.id).one()
        r = c.calculer_bulletin({"salaire_base": 173330, "heures_sup_10": 9600, "heures_sup_30b": 7800,
                                 "convention": "PETROLE"})
        b = M.BulletinPaie(tenant_id=t.id, salarie_id=s.id, periode_id=per.id, statut="BROUILLON")
        for k, v in r.items():
            if not k.startswith("_") and hasattr(b, k):
                setattr(b, k, v)
        b.base_heures_sup_10, b.taux_heures_sup_10 = 1200, "8"
        db.session.add(b)
        db.session.commit()
        cli = _cli(env["admin"])
        page = cli.get(f"/bulletins/{b.id}/imprimer").get_data(as_text=True)
        assert "+20 %" in page and "repos/férié, jour +30 %" in page, modele
        assert "Heures supplémentaires +10%" not in page and "H.sup +10%" not in page
        pdf = cli.get(f"/bulletins/{b.id}/pdf")
        assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
