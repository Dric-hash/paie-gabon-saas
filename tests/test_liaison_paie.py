# -*- coding: utf-8 -*-
"""tests/test_liaison_paie.py — Totaux annuels agrégés de la paie pour
Ameriack États Financiers (Note 27B) : autorisation expresse, agrégats exacts,
aucune donnée nominative."""
import os
import re
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_liaison_paie_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-liaison-paie")

import pytest

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, CategorieEmploi, Salarie, Contrat, PeriodePaie,
                    BulletinPaie, Journalier, FeuillePaieJournalier, AuditLog)


def _client_pour(uid):
    cli = flask_app.test_client()
    with cli.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    return cli


def _bulletin(t, s, p, brut, statut="VALIDÉ", **kw):
    return BulletinPaie(tenant_id=t.id, salarie_id=s.id, periode_id=p.id, salaire_base=brut, salaire_brut=brut,
                        net_a_payer=kw.pop("net", brut * 0.9), statut=statut,
                        cnss_salarie=kw.pop("cnss_s", 0), cnss_patronale=kw.pop("cnss_p", 0),
                        cnamgs_salarie=kw.pop("cnam_s", 0), cnamgs_patronale=kw.pop("cnam_p", 0),
                        fnh=kw.pop("fnh", 0), cfp=kw.pop("cfp", 0), tcs=kw.pop("tcs", 0), irpp=kw.pop("irpp", 0))


@pytest.fixture
def env():
    flask_app.config.update({"TESTING": True, "WTF_CSRF_ENABLED": False, "SERVER_NAME": "localhost"})
    from app import limiter
    limiter.enabled = False
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        plan = Plan(code="PRO", nom="Pro", prix_mensuel=1, max_salaries=50, max_utilisateurs=10, actif=True)
        db.session.add(plan); db.session.flush()
        t = Tenant(slug="a", denomination="ACME SARL", ville="Franceville", pays="Gabon",
                   plan_id=plan.id, statut="ACTIF", token_api="ta")
        autre = Tenant(slug="b", denomination="AUTRE SA", pays="Gabon", plan_id=plan.id, statut="ACTIF", token_api="tb")
        db.session.add_all([t, autre]); db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="admin@a.ga", role="TENANT_ADMIN",
                            tenant_id=t.id, actif=True, email_verifie=True)
        compta = Utilisateur(nom="COMPTA", prenom="C", email="compta@a.ga", role="COMPTABLE",
                             tenant_id=t.id, actif=True, email_verifie=True)
        admin.set_password("x"); compta.set_password("x")
        db.session.add_all([admin, compta]); db.session.flush()
        c1 = CategorieEmploi(tenant_id=t.id, code="C1", libelle="Cadre")
        c2 = CategorieEmploi(tenant_id=t.id, code="E2", libelle="Employé")
        db.session.add_all([c1, c2]); db.session.flush()
        s1 = Salarie(tenant_id=t.id, matricule="M1", nom="NDONG", prenom="Jean", sexe="M", nationalite="Gabonaise",
                     categorie_id=c1.id, date_embauche=date(2020, 1, 1))
        s2 = Salarie(tenant_id=t.id, matricule="M2", nom="MBA", prenom="Awa", sexe="F", nationalite="CAMEROUNAISE",
                     categorie_id=c2.id, date_embauche=date(2021, 1, 1))
        s3 = Salarie(tenant_id=t.id, matricule="M3", nom="OBAME", prenom="Paul", sexe="M", nationalite="GABONAISE",
                     categorie_id=c2.id, date_embauche=date(2021, 1, 1))
        db.session.add_all([s1, s2, s3]); db.session.flush()
        db.session.add_all([Contrat(tenant_id=t.id, salarie_id=s1.id, type_contrat="CDI", date_debut=date(2020, 1, 1), salaire_base=1),
                            Contrat(tenant_id=t.id, salarie_id=s2.id, type_contrat="CDD", date_debut=date(2021, 1, 1), salaire_base=1),
                            Contrat(tenant_id=t.id, salarie_id=s3.id, type_contrat="cdi", date_debut=date(2021, 1, 1), salaire_base=1)])
        p11 = PeriodePaie(tenant_id=t.id, annee=2025, mois=11, libelle_mois="NOVEMBRE")
        p12 = PeriodePaie(tenant_id=t.id, annee=2025, mois=12, libelle_mois="DÉCEMBRE")
        p01 = PeriodePaie(tenant_id=t.id, annee=2026, mois=1, libelle_mois="JANVIER")
        db.session.add_all([p11, p12, p01]); db.session.flush()
        db.session.add_all([
            _bulletin(t, s1, p11, 1_000_000, cnss_p=50_000, cnam_p=41_000, fnh=10_000, cfp=5_000, irpp=80_000, tcs=20_000),
            _bulletin(t, s1, p12, 1_000_000, statut="PAYÉ", cnss_p=50_000, cnam_p=41_000),
            _bulletin(t, s2, p11, 300_000, cnss_p=15_000),
            _bulletin(t, s3, p12, 200_000, statut="BROUILLON"),          # non retenu
            _bulletin(t, s3, p01, 999_999),                              # hors intervalle
        ])
        j1 = Journalier(tenant_id=t.id, nom="ESSONO", prenom="Luc", taux_horaire=1000, nationalite="Gabonaise", sexe="M")
        j2 = Journalier(tenant_id=t.id, nom="DIALLO", prenom="Ali", taux_horaire=1000, nationalite="MALIENNE")
        db.session.add_all([j1, j2]); db.session.flush()
        db.session.add_all([
            FeuillePaieJournalier(tenant_id=t.id, journalier_id=j1.id, date_debut=date(2025, 12, 1), date_fin=date(2025, 12, 15),
                                  taux_horaire=1000, montant_brut=80_000, statut="PAYÉ"),
            FeuillePaieJournalier(tenant_id=t.id, journalier_id=j2.id, date_debut=date(2025, 11, 1), date_fin=date(2025, 11, 30),
                                  taux_horaire=1000, montant_brut=120_000, statut="EN_ATTENTE"),
        ])
        db.session.commit()
        ids = {"t": t.id, "admin": admin.id, "compta": compta.id}
    yield ids
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


def _cle(cli):
    r = cli.post("/parametres/liaison-etats", data={"action": "generer"})
    assert r.status_code == 302
    with cli.session_transaction() as sess:
        msgs = " ".join(m for _, m in sess.get("_flashes", []))
        sess["_flashes"] = []
    return re.findall(r"(pgl_[0-9a-f]{64})", msgs)[-1]


def _paie(cle, debut="2025-11-01", fin="2025-12-31"):
    return flask_app.test_client().get(f"/api/v1/liaison/paie?debut={debut}&fin={fin}",
                                       headers={"X-Liaison-Key": cle})


def test_autorisation_expresse_requise(env):
    admin = _client_pour(env["admin"])
    cle = _cle(admin)
    r = _paie(cle)
    assert r.status_code == 403 and r.get_json()["code"] == "PAIE_NON_AUTORISEE"
    # un comptable ne peut pas autoriser
    assert _client_pour(env["compta"]).post("/parametres/liaison-etats",
                                            data={"action": "autoriser_paie"}).status_code in (302, 403)
    with flask_app.app_context():
        assert not db.session.get(Tenant, env["t"]).liaison_paie_autorisee
    admin.post("/parametres/liaison-etats", data={"action": "autoriser_paie"})
    assert _paie(cle).status_code == 200
    admin.post("/parametres/liaison-etats", data={"action": "retirer_paie"})
    assert _paie(cle).status_code == 403
    # la révocation de la clé retire aussi l'autorisation
    admin.post("/parametres/liaison-etats", data={"action": "autoriser_paie"})
    admin.post("/parametres/liaison-etats", data={"action": "revoquer"})
    with flask_app.app_context():
        assert not db.session.get(Tenant, env["t"]).liaison_paie_autorisee
    assert _paie(cle).status_code == 401


def test_agregats_exacts_et_sans_donnees_nominatives(env):
    admin = _client_pour(env["admin"])
    cle = _cle(admin)
    admin.post("/parametres/liaison-etats", data={"action": "autoriser_paie"})
    r = _paie(cle)
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["mois"] == ["2025-11", "2025-12"] and d["mois_sans_bulletin"] == []
    tot = d["salaries"]["totaux"]
    assert tot["salaire_brut"] == 2_300_000 and tot["nb_bulletins"] == 3 and tot["bulletins_non_retenus"] == 1
    assert tot["cnss_patronale"] == 115_000 and tot["cnamgs_patronale"] == 82_000
    assert tot["fnh"] == 10_000 and tot["cfp"] == 5_000 and tot["irpp"] == 80_000 and tot["nb_salaries"] == 2
    g = {(x["categorie_code"], x["nationalite"], x["sexe"], x["contrat"]): x for x in d["salaries"]["groupes"]}
    assert set(g) == {("C1", "GABONAISE", "M", "CDI"), ("E2", "CAMEROUNAISE", "F", "CDD")}
    cadre = g[("C1", "GABONAISE", "M", "CDI")]
    assert cadre["effectif_paye"] == 1 and cadre["effectif_dernier_mois"] == 1 and cadre["masse_brute"] == 2_000_000
    assert g[("E2", "CAMEROUNAISE", "F", "CDD")]["effectif_dernier_mois"] == 0   # pas payée en décembre
    j = d["journaliers"]
    assert j["totaux"]["brut"] == 200_000 and j["totaux"]["dont_en_attente"] == 120_000
    gj = {(x["nationalite"], x["sexe"]): x for x in j["groupes"]}
    assert gj[("GABONAISE", "M")]["effectif_dernier_mois"] == 1 and gj[("MALIENNE", "")]["masse_brute"] == 120_000
    texte = r.get_data(as_text=True)
    for nominatif in ("NDONG", "MBA", "Awa", "M1", "M2", "ESSONO", "DIALLO", "matricule", "salarie_id"):
        assert nominatif not in texte, nominatif
    with flask_app.app_context():
        assert AuditLog.query.filter_by(entite="liaison_paie", tenant_id=env["t"]).count() == 1


def test_periode_invalide_et_mois_sans_bulletin(env):
    admin = _client_pour(env["admin"])
    cle = _cle(admin)
    admin.post("/parametres/liaison-etats", data={"action": "autoriser_paie"})
    assert _paie(cle, debut="2025-12-31", fin="2025-01-01").status_code == 400
    assert _paie(cle, debut="2020-01-01", fin="2025-12-31").status_code == 400
    assert _paie(cle, debut="x", fin="2025-12-31").status_code == 400
    d = _paie(cle, debut="2025-01-01", fin="2025-12-31").get_json()["data"]
    assert len(d["mois_sans_bulletin"]) == 10


def test_journalier_sexe_enregistre(env):
    admin = _client_pour(env["admin"])
    r = admin.post("/journaliers/nouveau", data={"nom": "Nze", "prenom": "Eve", "taux_horaire": "1000", "sexe": "F"})
    assert r.status_code == 302
    with flask_app.app_context():
        assert Journalier.query.filter_by(nom="NZE").first().sexe == "F"
