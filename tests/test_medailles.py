# -*- coding: utf-8 -*-
"""tests/test_medailles.py — Médaille du travail (gestion de carrière — Phase 2).

Vérifie : le semis des paliers par défaut (Loi N° 021/2010 : 10/20/30 ans),
le calcul d'éligibilité (ancienneté continue dans l'entreprise), l'attribution
(qui crée un EvenementCarriere DISTINCTION et retire l'éligibilité), le
paramétrage des seuils, et l'attestation PDF.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_medailles_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-medailles")

import pytest
from datetime import date, timedelta

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, Salarie, Contrat,
                    CategorieEmploi, EvenementCarriere, PalierMedaille)


def _embauche_il_y_a(ans):
    """Date d'embauche située ~`ans` années plus 10 jours dans le passé
    (marge pour franchir le seuil en années pleines : days // 365)."""
    return date.today() - timedelta(days=ans * 365 + 10)


@pytest.fixture
def client():
    flask_app.config.update({"TESTING": True, "WTF_CSRF_ENABLED": False,
                             "SERVER_NAME": "localhost"})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        plan = Plan(code="PRO", nom="Pro", prix_mensuel=1, max_salaries=50,
                    max_utilisateurs=3, actif=True)
        db.session.add(plan)
        db.session.flush()
        t = Tenant(slug="a", denomination="ACME SARL", sigle="A", activite="Commerce",
                   convention="COMMERCE", ville="Franceville", pays="Gabon",
                   plan_id=plan.id, statut="ACTIF", token_api="t")
        db.session.add(t)
        db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="admin@a.ga",
                            role="TENANT_ADMIN", tenant_id=t.id, actif=True, email_verifie=True)
        admin.set_password("x")
        db.session.add(admin)
        # 3 salariés : 12 ans (éligible bronze), 25 ans (éligible argent), 3 ans (non éligible)
        s12 = Salarie(tenant_id=t.id, matricule="M12", nom="DOUZE", prenom="Jean",
                      date_embauche=_embauche_il_y_a(12), emploi="Vendeur", statut="ACTIF")
        s25 = Salarie(tenant_id=t.id, matricule="M25", nom="VINGTCINQ", prenom="Paul",
                      date_embauche=_embauche_il_y_a(25), emploi="Chef", statut="ACTIF")
        s3 = Salarie(tenant_id=t.id, matricule="M3", nom="TROIS", prenom="Luc",
                     date_embauche=_embauche_il_y_a(3), emploi="Agent", statut="ACTIF")
        db.session.add_all([s12, s25, s3])
        db.session.commit()
        ids = {"tenant": t.id, "admin": admin.id,
               "s12": s12.id, "s25": s25.id, "s3": s3.id}
        cli = flask_app.test_client()
        with cli.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True
        cli._ids = ids
        yield cli
        db.session.remove()
        db.drop_all()


def test_seuils_par_defaut_semes(client):
    """Au premier accès, les 3 paliers JO (bronze 10, argent 20, or 30) sont créés."""
    r = client.get("/medailles")
    assert r.status_code == 200
    with flask_app.app_context():
        paliers = {p.code: p.annees_requises for p in
                   PalierMedaille.query.filter_by(tenant_id=client._ids["tenant"]).all()}
        assert paliers == {"BRONZE": 10, "ARGENT": 20, "OR": 30}


def test_eligibilite_par_anciennete(client):
    """12 ans → bronze ; 25 ans → argent ; 3 ans → rien."""
    client.get("/medailles")  # sème les paliers
    from blueprints.tenant.medailles import medailles_eligibles
    with flask_app.app_context():
        t = Tenant.query.get(client._ids["tenant"])
        elig = {e["salarie"].id: e["palier"].code for e in medailles_eligibles(t)}
    assert elig.get(client._ids["s12"]) == "BRONZE"
    assert elig.get(client._ids["s25"]) == "ARGENT"
    assert client._ids["s3"] not in elig


def test_attribution_cree_evenement_et_retire_eligibilite(client):
    client.get("/medailles")
    with flask_app.app_context():
        pid_bronze = PalierMedaille.query.filter_by(
            tenant_id=client._ids["tenant"], code="BRONZE").first().id
    r = client.post(f"/medailles/attribuer/{client._ids['s12']}/{pid_bronze}",
                    data={"date_effet": date.today().strftime("%Y-%m-%d")},
                    follow_redirects=True)
    assert r.status_code == 200
    from blueprints.tenant.medailles import medailles_eligibles
    with flask_app.app_context():
        evt = EvenementCarriere.query.filter_by(
            tenant_id=client._ids["tenant"], salarie_id=client._ids["s12"],
            type="DISTINCTION").first()
        assert evt is not None
        assert (evt.nouvelle_valeur or "").upper() == "BRONZE"
        # Plus éligible au bronze (déjà reçu)
        t = Tenant.query.get(client._ids["tenant"])
        elig = {e["salarie"].id: e["palier"].code for e in medailles_eligibles(t)}
        assert elig.get(client._ids["s12"]) != "BRONZE"


def test_pas_de_double_attribution(client):
    client.get("/medailles")
    with flask_app.app_context():
        pid = PalierMedaille.query.filter_by(
            tenant_id=client._ids["tenant"], code="BRONZE").first().id
    client.post(f"/medailles/attribuer/{client._ids['s12']}/{pid}",
                data={"date_effet": date.today().strftime("%Y-%m-%d")}, follow_redirects=True)
    client.post(f"/medailles/attribuer/{client._ids['s12']}/{pid}",
                data={"date_effet": date.today().strftime("%Y-%m-%d")}, follow_redirects=True)
    with flask_app.app_context():
        n = EvenementCarriere.query.filter_by(
            tenant_id=client._ids["tenant"], salarie_id=client._ids["s12"],
            type="DISTINCTION").count()
        assert n == 1   # la 2ᵉ tentative est refusée


def test_parametrage_change_eligibilite(client):
    """Abaisser le seuil bronze à 2 ans rend le salarié de 3 ans éligible."""
    client.get("/medailles")
    with flask_app.app_context():
        pb = PalierMedaille.query.filter_by(
            tenant_id=client._ids["tenant"], code="BRONZE").first()
        pid, p_arg, p_or = pb.id, PalierMedaille.query.filter_by(
            tenant_id=client._ids["tenant"], code="ARGENT").first().id, \
            PalierMedaille.query.filter_by(
                tenant_id=client._ids["tenant"], code="OR").first().id
    client.post("/medailles/parametres", data={
        "action": "maj",
        f"libelle_{pid}": "Médaille de bronze", f"annees_{pid}": "2", f"actif_{pid}": "1",
        f"libelle_{p_arg}": "Médaille d'argent", f"annees_{p_arg}": "20", f"actif_{p_arg}": "1",
        f"libelle_{p_or}": "Médaille d'or", f"annees_{p_or}": "30", f"actif_{p_or}": "1",
    }, follow_redirects=True)
    from blueprints.tenant.medailles import medailles_eligibles
    with flask_app.app_context():
        t = Tenant.query.get(client._ids["tenant"])
        elig = {e["salarie"].id: e["palier"].code for e in medailles_eligibles(t)}
        assert elig.get(client._ids["s3"]) == "BRONZE"


def test_ajout_palier(client):
    client.get("/medailles")
    client.post("/medailles/parametres", data={
        "action": "ajouter", "code": "GRAND_OR",
        "libelle": "Médaille grand or", "annees_requises": "35"},
        follow_redirects=True)
    with flask_app.app_context():
        p = PalierMedaille.query.filter_by(
            tenant_id=client._ids["tenant"], code="GRAND_OR").first()
        assert p is not None and p.annees_requises == 35


def test_attestation_medaille_pdf(client):
    client.get("/medailles")
    with flask_app.app_context():
        pid = PalierMedaille.query.filter_by(
            tenant_id=client._ids["tenant"], code="ARGENT").first().id
    client.post(f"/medailles/attribuer/{client._ids['s25']}/{pid}",
                data={"date_effet": date.today().strftime("%Y-%m-%d")}, follow_redirects=True)
    with flask_app.app_context():
        eid = EvenementCarriere.query.filter_by(
            tenant_id=client._ids["tenant"], salarie_id=client._ids["s25"],
            type="DISTINCTION").first().id
    r = client.get(f"/medailles/evenement/{eid}/attestation")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_dashboard_alerte_medaille(client):
    """Le tableau de bord signale les éligibilités médaille."""
    r = client.get("/dashboard", follow_redirects=True)
    assert r.status_code == 200
    assert "médaille du travail".encode() in r.data.lower() or b"m\xc3\xa9daille" in r.data.lower()
