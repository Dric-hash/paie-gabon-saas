# -*- coding: utf-8 -*-
"""tests/test_cycle_paie.py — Cycle de paie : paie mensuelle, impôts et taxes,
DTS CNSS / CNAMGS, écritures, DAS ; échéances jamais inventées."""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_cycle_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-cycle")

import pytest

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, CategorieEmploi, Salarie, PeriodePaie, BulletinPaie,
                    AuditLog, ObligationPaie, EcheanceLegale, EcheanceTenant)
import cycle_paie as cp


def _client_pour(uid):
    cli = flask_app.test_client()
    with cli.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    return cli


def _bul(t, s, p, brut, statut="VALIDÉ", **kw):
    return BulletinPaie(tenant_id=t.id, salarie_id=s.id, periode_id=p.id, salaire_base=brut, salaire_brut=brut,
                        net_a_payer=brut * 0.8, statut=statut,
                        cnss_salarie=kw.get("cnss_s", 0), cnss_patronale=kw.get("cnss_p", 0),
                        cnamgs_salarie=kw.get("cnam_s", 0), cnamgs_patronale=kw.get("cnam_p", 0),
                        fnh=kw.get("fnh", 0), cfp=kw.get("cfp", 0), tcs=kw.get("tcs", 0), irpp=kw.get("irpp", 0))


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
        t = Tenant(slug="a", denomination="ACME SARL", pays="Gabon", plan_id=plan.id, statut="ACTIF", token_api="ta")
        autre = Tenant(slug="b", denomination="AUTRE SA", pays="Gabon", plan_id=plan.id, statut="ACTIF", token_api="tb")
        db.session.add_all([t, autre]); db.session.flush()
        users = {}
        for cle, role, tid in (("admin", "TENANT_ADMIN", t.id), ("compta", "COMPTABLE", t.id),
                               ("dir", "DIRECTEUR", t.id), ("autre", "TENANT_ADMIN", autre.id),
                               ("sa", "SUPER_ADMIN", None)):
            u = Utilisateur(nom=cle.upper(), prenom="X", email=f"{cle}@a.ga", role=role, tenant_id=tid,
                            actif=True, email_verifie=True)
            u.set_password("x"); db.session.add(u); db.session.flush(); users[cle] = u.id
        cat = CategorieEmploi(tenant_id=t.id, code="C1", libelle="Cadre"); db.session.add(cat); db.session.flush()
        s1 = Salarie(tenant_id=t.id, matricule="M1", nom="NDONG", prenom="Jean", categorie_id=cat.id,
                     date_embauche=date(2020, 1, 1))
        s2 = Salarie(tenant_id=t.id, matricule="M2", nom="MBA", prenom="Awa", categorie_id=cat.id,
                     date_embauche=date(2020, 1, 1))
        db.session.add_all([s1, s2]); db.session.flush()
        per = {}
        for m, lib in ((1, "JANVIER"), (2, "FÉVRIER"), (3, "MARS"), (4, "AVRIL")):
            per[m] = PeriodePaie(tenant_id=t.id, annee=2026, mois=m, libelle_mois=lib, statut="OUVERT")
        db.session.add_all(per.values()); db.session.flush()
        # Janvier : 1 payé + 1 validé ; un brouillon en avril
        db.session.add_all([
            _bul(t, s1, per[1], 1_000_000, "PAYÉ", irpp=80_000, tcs=20_000, fnh=10_000, cfp=5_000,
                 cnss_s=25_000, cnss_p=100_000, cnam_s=20_000, cnam_p=41_000),
            _bul(t, s2, per[1], 500_000, "VALIDÉ", irpp=30_000, tcs=10_000, fnh=5_000, cfp=2_500,
                 cnss_s=12_500, cnss_p=50_000, cnam_s=10_000, cnam_p=20_500),
            _bul(t, s1, per[2], 1_000_000, "PAYÉ", irpp=80_000, cnss_s=25_000, cnss_p=100_000),
            _bul(t, s1, per[3], 1_000_000, "PAYÉ", irpp=80_000, cnss_s=25_000, cnss_p=100_000),
            _bul(t, s1, per[4], 1_000_000, "BROUILLON", irpp=99_999, cnss_s=99_999),
        ])
        db.session.commit()
        ids = dict(users, t=t.id, autre_t=autre.id, p1=per[1].id, p4=per[4].id)
    yield ids
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


def _etat(ids, aujourd_hui=date(2026, 5, 20)):
    t = db.session.get(Tenant, ids["t"])
    return cp.etat_annee(t, 2026, aujourd_hui)


def test_montants_du_mois_hors_brouillons(env):
    with flask_app.app_context():
        e = _etat(env)
        jan = e["mois"][0]
        assert jan["montants"]["IRPP"] == 110_000
        assert jan["montants"]["TCS"] == 30_000
        assert jan["montants"]["FNH"] == 15_000
        assert jan["montants"]["CFP"] == 7_500
        assert jan["montants"]["BRUT"] == 1_500_000
        avr = e["mois"][3]
        assert avr["nb_brouillon"] == 1
        assert avr["montants"]["IRPP"] == 0          # le brouillon n'est jamais compté
        assert avr["colonnes"]["PAIE"] == "A_FAIRE"
        # janvier : un bulletin validé non payé → salaires à payer
        assert jan["colonnes"]["PAIEMENT"] == "A_FAIRE"
        assert e["mois"][1]["colonnes"]["PAIEMENT"] == "FAIT"


def test_trimestre_cnss_cnamgs(env):
    with flask_app.app_context():
        e = _etat(env)
        t1 = e["trimestres"][0]
        ob = {x["code"]: x for x in t1["obligations"]}
        assert ob["CNSS"]["du"] == 37_500 + 150_000 + 125_000 + 125_000
        assert ob["CNAMGS"]["du"] == 61_000 + 30_500
        # aucun mois clôturé → DTS pas encore prête
        assert t1["pret"] is False and ob["CNSS"]["etat"] == "ATTENTE"
        for p in PeriodePaie.query.filter(PeriodePaie.mois <= 3).all():
            p.statut = "CLÔTURÉ"
        db.session.commit()
        t1 = _etat(env)["trimestres"][0]
        assert t1["pret"] is True and t1["obligations"][0]["etat"] == "A_FAIRE"
        # T2 : seul un brouillon → rien à déclarer
        t2 = _etat(env)["trimestres"][1]
        assert t2["nb_brouillon"] == 1 and t2["obligations"][0]["du"] == 0


def test_aucune_echeance_inventee(env):
    with flask_app.app_context():
        assert EcheanceLegale.query.count() == 0
        e = _etat(env, aujourd_hui=date(2030, 1, 1))
        assert set(e["echeances_manquantes"]) == set(cp.CODES_ECHEANCE)
        # sans échéance, rien n'est jamais « en retard », même des années plus tard
        assert e["compte"]["RETARD"] == 0
        assert all(x["limite"] is None for x in e["a_faire"])


def test_echeance_reglee_et_retard(env):
    with flask_app.app_context():
        db.session.add(EcheanceLegale(code="IRPP", jour=31, decalage_mois=1, source="test"))
        db.session.commit()
        regles = cp.echeances()
        assert cp.date_limite("IRPP", 2026, 1, regles) == date(2026, 2, 28)   # ramené à fin février
        assert cp.date_limite("IRPP", 2026, 12, regles) == date(2027, 1, 31)  # passage d'année
        e = _etat(env, aujourd_hui=date(2026, 3, 1))
        irpp_jan = next(x for x in e["mois"][0]["taxes"] if x["code"] == "IRPP")
        assert irpp_jan["etat"] == "RETARD"
        assert e["a_faire"][0]["etat"] == "RETARD"
        # TCS sans règle : jamais en retard
        tcs_jan = next(x for x in e["mois"][0]["taxes"] if x["code"] == "TCS")
        assert tcs_jan["etat"] == "A_FAIRE" and tcs_jan["limite"] is None


def test_page_annee_et_mois(env):
    cli = _client_pour(env["admin"])
    r = cli.get("/cycle-paie?annee=2026")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Cycle de paie 2026" in html and "DTS trimestrielles CNSS et CNAMGS" in html
    assert "Dates limites légales non renseignées" in html
    assert "Préparer la DAS" not in html          # plan PRO : DAS non comprise
    r = cli.get("/cycle-paie/2026/1")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Janvier 2026" in html and "110 000 FCFA" in html
    assert cli.get("/cycle-paie/2026/13").status_code == 404


def test_declarer_payer_annuler(env):
    cli = _client_pour(env["admin"])
    r = cli.post("/cycle-paie/obligation", data={"code": "IRPP", "annee": 2026, "periode": 1, "action": "DECLARE",
                                                   "date_declaration": "2026-02-05", "retour": "/cycle-paie/2026/1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/cycle-paie/2026/1")
    with flask_app.app_context():
        ob = ObligationPaie.query.filter_by(code="IRPP").one()
        assert ob.statut == "DECLARE" and ob.date_declaration == date(2026, 2, 5)
        assert _etat(env)["mois"][0]["taxes"][0]["etat"] == "DECLARE"
    cli.post("/cycle-paie/obligation", data={"code": "IRPP", "annee": 2026, "periode": 1, "action": "PAYE",
                                              "date_paiement": "2026-02-10", "montant_paye": "110 000",
                                              "reference": "Q-123"})
    with flask_app.app_context():
        ob = ObligationPaie.query.filter_by(code="IRPP").one()
        assert ob.statut == "PAYE" and float(ob.montant_paye) == 110_000 and ob.reference == "Q-123"
        assert ob.date_declaration == date(2026, 2, 5)
        assert _etat(env)["mois"][0]["taxes"][0]["etat"] == "FAIT"
        assert AuditLog.query.filter(AuditLog.description.like("Cycle de paie : payé — IRPP Janvier 2026%")).count() == 1
    cli.post("/cycle-paie/obligation", data={"code": "IRPP", "annee": 2026, "periode": 1, "action": "ANNULER"})
    with flask_app.app_context():
        assert ObligationPaie.query.count() == 0


def test_saisie_groupee_et_validations(env):
    cli = _client_pour(env["admin"])
    cli.post("/cycle-paie/obligation", data={"codes": ["IRPP", "TCS", "FNH", "CFP"], "annee": 2026, "periode": 1,
                                              "action": "PAYE", "date_paiement": "2026-02-10", "reference": "BORD-1"})
    with flask_app.app_context():
        obs = ObligationPaie.query.filter_by(annee=2026, periode=1).all()
        assert {o.code for o in obs} == {"IRPP", "TCS", "FNH", "CFP"}
        assert all(o.statut == "PAYE" and o.reference == "BORD-1" for o in obs)
        assert _etat(env)["mois"][0]["colonnes"]["TAXES"] == "FAIT"
    # périodes ou codes invalides refusés, rien n'est écrit
    for data in ({"code": "CNSS", "annee": 2026, "periode": 7, "action": "PAYE"},
                 {"code": "IRPP", "annee": 2026, "periode": 13, "action": "PAYE"},
                 {"code": "DAS", "annee": 2026, "periode": 1, "action": "DECLARE"},
                 {"code": "XXX", "annee": 2026, "periode": 1, "action": "PAYE"},
                 {"code": "IRPP", "annee": 2026, "periode": 2, "action": "PAYE", "montant_paye": "-5"},
                 {"code": "IRPP", "annee": 2026, "periode": 2, "action": "PAYE", "date_paiement": "10/02/2026"}):
        cli.post("/cycle-paie/obligation", data=data)
    with flask_app.app_context():
        assert ObligationPaie.query.count() == 4
    # redirection externe refusée
    r = cli.post("/cycle-paie/obligation", data={"code": "DAS", "annee": 2026, "periode": 0, "action": "PAYE",
                                                   "retour": "https://exemple.com/"})
    assert "exemple.com" not in r.headers["Location"]
    with flask_app.app_context():
        das = ObligationPaie.query.filter_by(code="DAS").one()
        assert das.statut == "DECLARE"                  # la DAS se dépose, elle ne se « paie » pas
        assert _etat(env)["das"]["etat"] == "FAIT"


def test_droits_et_cloisonnement(env):
    # lecture seule (directeur, comptable) : peut voir, ne peut rien saisir
    for qui in ("dir", "compta"):
        cli = _client_pour(env[qui])
        assert cli.get("/cycle-paie").status_code == 200
        html = cli.get("/cycle-paie/2026/1").get_data(as_text=True)
        assert "Déclarer / payer" not in html
        r = cli.post("/cycle-paie/obligation", data={"code": "IRPP", "annee": 2026, "periode": 1, "action": "PAYE"})
        assert r.status_code in (302, 403)
    with flask_app.app_context():
        assert ObligationPaie.query.count() == 0
    cli = _client_pour(env["admin"])
    assert "Déclarer / payer" in cli.get("/cycle-paie/2026/1").get_data(as_text=True)
    cli.post("/cycle-paie/obligation", data={"code": "ECRITURES", "annee": 2026, "periode": 1, "action": "DECLARE"})
    # une autre entreprise écrit toujours chez elle, jamais chez ACME
    cli = _client_pour(env["autre"])
    cli.post("/cycle-paie/obligation", data={"code": "IRPP", "annee": 2026, "periode": 1, "action": "PAYE"})
    html = cli.get("/cycle-paie?annee=2026").get_data(as_text=True)
    assert "110 000" not in html
    with flask_app.app_context():
        assert ObligationPaie.query.filter_by(tenant_id=env["t"]).one().code == "ECRITURES"
        assert ObligationPaie.query.filter_by(tenant_id=env["autre_t"]).one().code == "IRPP"
        assert _etat(env)["mois"][0]["ecritures"]["etat"] == "FAIT"


def test_export_journal_inclut_payes_et_detecte(env):
    cli = _client_pour(env["admin"])
    r = cli.get(f"/export/sage/journal/{env['p1']}")
    assert r.status_code == 200 and r.mimetype == "text/plain"
    contenu = r.get_data().decode("cp1252")     # fichier Sage encodé en Windows-1252
    assert "2 bulletins" in contenu and "\t661" in contenu and "\t422\t" in contenu   # payé + validé
    with flask_app.app_context():
        e = _etat(env)
        assert e["mois"][0]["ecritures"]["exporte_le"] is not None
        assert e["mois"][1]["ecritures"]["exporte_le"] is None
    # février : uniquement des bulletins PAYÉS → l'export fonctionne désormais
    with flask_app.app_context():
        p2 = PeriodePaie.query.filter_by(tenant_id=env["t"], mois=2).one().id
    r = cli.get(f"/export/sage/journal/{p2}")
    assert r.status_code == 200 and r.mimetype == "text/plain"
    # le directeur (sans droit d'export Sage) est refusé sur les trois exports
    cli = _client_pour(env["dir"])
    for url in (f"/export/sage/journal/{p2}", f"/export/sage/livre/{p2}", f"/export/sage/les-deux/{p2}"):
        assert cli.get(url).status_code == 302


def test_admin_echeances(env):
    assert _client_pour(env["admin"]).get("/admin/echeances-paie").status_code == 403
    cli = _client_pour(env["sa"])
    r = cli.get("/admin/echeances-paie")
    assert r.status_code == 200 and "aucune valeur par défaut" in r.get_data(as_text=True)
    r = cli.post("/admin/echeances-paie", data={"jour_CNSS": "15", "decalage_CNSS": "1",
                                                 "source_CNSS": "Texte à citer", "verifie_CNSS": "2026-10-22"})
    assert r.status_code == 302
    with flask_app.app_context():
        e = EcheanceLegale.query.filter_by(code="CNSS").one()
        assert (e.jour, e.decalage_mois, e.source, e.verifie_le) == (15, 1, "Texte à citer", date(2026, 10, 22))
        assert cp.date_limite("CNSS", 2026, 1, cp.echeances()) == date(2026, 4, 15)
        assert EcheanceLegale.query.count() == 1
    # valeur invalide : rien n'est modifié
    cli.post("/admin/echeances-paie", data={"jour_CNSS": "40", "decalage_CNSS": "1"})
    with flask_app.app_context():
        assert EcheanceLegale.query.filter_by(code="CNSS").one().jour == 15
    # ligne vidée : échéance retirée
    cli.post("/admin/echeances-paie", data={})
    with flask_app.app_context():
        assert EcheanceLegale.query.count() == 0


def test_objectif_interne_a_cote_de_la_date_legale(env):
    with flask_app.app_context():
        db.session.add(EcheanceLegale(code="IRPP", jour=15, decalage_mois=1, source="test"))
        db.session.add(EcheanceTenant(tenant_id=env["t"], code="IRPP", jour=10, decalage_mois=1))
        db.session.add(EcheanceTenant(tenant_id=env["t"], code="ECRITURES", jour=20, decalage_mois=1))
        # objectif d'une AUTRE entreprise : sans effet sur ACME
        db.session.add(EcheanceTenant(tenant_id=env["autre_t"], code="TCS", jour=1, decalage_mois=1))
        db.session.commit()
        def irpp_jan(j):
            return next(x for x in _etat(env, aujourd_hui=j)["mois"][0]["taxes"] if x["code"] == "IRPP")
        x = irpp_jan(date(2026, 2, 5))
        assert (x["cible"], x["limite"], x["etat"]) == (date(2026, 2, 10), date(2026, 2, 15), "A_FAIRE")
        assert irpp_jan(date(2026, 2, 12))["etat"] == "OBJECTIF"      # objectif passé, date légale non
        assert irpp_jan(date(2026, 2, 16))["etat"] == "RETARD"        # la date légale prime
        e = _etat(env, aujourd_hui=date(2026, 2, 12))
        assert e["compte"]["OBJECTIF"] >= 1 and e["a_faire"][0]["etat"] == "OBJECTIF"
        tcs = next(x for x in e["mois"][0]["taxes"] if x["code"] == "TCS")
        assert tcs["cible"] is None and tcs["etat"] == "A_FAIRE"
        # écritures : objectif interne seulement (pas de date légale)
        ecr = _etat(env, aujourd_hui=date(2026, 2, 21))["mois"][0]["ecritures"]
        assert ecr["cible"] == date(2026, 2, 20) and ecr["limite"] is None and ecr["etat"] == "OBJECTIF"
        # l'obligation payée n'est plus jamais signalée
        db.session.add(ObligationPaie(tenant_id=env["t"], code="IRPP", annee=2026, periode=1, statut="PAYE"))
        db.session.commit()
        assert irpp_jan(date(2026, 3, 1))["etat"] == "FAIT"


def test_page_mes_echeances(env):
    cli = _client_pour(env["admin"])
    r = cli.get("/cycle-paie/echeances")
    assert r.status_code == 200 and "Mes échéances" in r.get_data(as_text=True)
    r = cli.post("/cycle-paie/echeances", data={"jour_IRPP": "10", "decalage_IRPP": "1", "note_IRPP": "bordereau du 10",
                                                 "jour_CNSS": "20", "decalage_CNSS": "1"})
    assert r.status_code == 302
    with flask_app.app_context():
        lignes = {e.code: e for e in EcheanceTenant.query.filter_by(tenant_id=env["t"]).all()}
        assert set(lignes) == {"IRPP", "CNSS"}
        assert (lignes["IRPP"].jour, lignes["IRPP"].decalage_mois, lignes["IRPP"].note) == (10, 1, "bordereau du 10")
        assert AuditLog.query.filter(AuditLog.entite == "echeance_tenant").count() == 1
        assert EcheanceLegale.query.count() == 0                        # la date légale n'est jamais touchée
    html = cli.get("/cycle-paie/2026/1").get_data(as_text=True)
    assert "objectif 10/02/2026" in html
    # valeur invalide : rien n'est modifié
    cli.post("/cycle-paie/echeances", data={"jour_IRPP": "45", "decalage_IRPP": "1", "jour_CNSS": "20", "decalage_CNSS": "1"})
    with flask_app.app_context():
        assert EcheanceTenant.query.filter_by(tenant_id=env["t"], code="IRPP").one().jour == 10
    # ligne vidée : objectif retiré
    cli.post("/cycle-paie/echeances", data={"jour_CNSS": "20", "decalage_CNSS": "1"})
    with flask_app.app_context():
        assert {e.code for e in EcheanceTenant.query.filter_by(tenant_id=env["t"]).all()} == {"CNSS"}
    # lecture seule : voit la page, ne modifie rien ; autre entreprise : ne voit pas ces dates
    for qui in ("dir", "compta"):
        c = _client_pour(env[qui])
        assert c.get("/cycle-paie/echeances").status_code == 200
        c.post("/cycle-paie/echeances", data={})
    html = _client_pour(env["autre"]).get("/cycle-paie/echeances").get_data(as_text=True)
    assert 'value="20"' not in html
    with flask_app.app_context():
        assert EcheanceTenant.query.filter_by(tenant_id=env["t"]).count() == 1
