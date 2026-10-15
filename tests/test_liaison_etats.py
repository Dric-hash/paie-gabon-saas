# -*- coding: utf-8 -*-
"""tests/test_liaison_etats.py — Clé de liaison Ameriack États Financiers
(lecture seule de la fiche d'identité) + corrections de la page clients OAuth."""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_liaison_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-liaison")

import pytest

from app import app as flask_app
from models import db, Plan, Tenant, Utilisateur, OAuthClient


def _client_pour(uid):
    cli = flask_app.test_client()
    with cli.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    return cli


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
        ta = Tenant(slug="a", denomination="ACME SARL", sigle="ACME", activite="BTP", nif="123456A",
                    adresse="Quartier Potos", boite_postale="123", telephone="+241 01 00 00 00",
                    ville="Franceville", pays="Gabon", representant_nom="Jean NDONG",
                    representant_fonction="Gérant", plan_id=plan.id, statut="ACTIF", token_api="ta")
        tb = Tenant(slug="b", denomination="AUTRE SA", ville="Libreville", pays="Gabon",
                    plan_id=plan.id, statut="ACTIF", token_api="tb")
        db.session.add_all([ta, tb]); db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="admin@a.ga", role="TENANT_ADMIN",
                            tenant_id=ta.id, actif=True, email_verifie=True)
        compta = Utilisateur(nom="COMPTA", prenom="C", email="compta@a.ga", role="COMPTABLE",
                             tenant_id=ta.id, actif=True, email_verifie=True)
        admin.set_password("x"); compta.set_password("x")
        db.session.add_all([admin, compta]); db.session.commit()
        ids = {"ta": ta.id, "tb": tb.id, "admin": admin.id, "compta": compta.id}
    yield ids
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


def _generer(cli):
    r = cli.post("/parametres/liaison-etats", data={"action": "generer"})
    assert r.status_code == 302
    with cli.session_transaction() as sess:
        msgs = " ".join(m for _, m in sess.get("_flashes", []))
        sess["_flashes"] = []
    return re.findall(r"(pgl_[0-9a-f]{64})", msgs)[-1]


def _identite(cle):
    return flask_app.test_client().get("/api/v1/identite", headers={"X-Liaison-Key": cle})


def test_generation_et_lecture_identite(env):
    cle = _generer(_client_pour(env["admin"]))
    with flask_app.app_context():
        t = db.session.get(Tenant, env["ta"])
        assert t.cle_liaison_hash and cle not in (t.cle_liaison_hash, t.cle_liaison_prefixe)
        assert t.cle_liaison_prefixe.startswith("pgl_")
    r = _identite(cle)
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["denomination"] == "ACME SARL" and d["nif"] == "123456A"
    assert d["boite_postale"] == "123" and d["representant_fonction"] == "Gérant"
    assert d["reference"] == env["ta"]
    # Aucune donnée de paie dans la fiche
    assert not {"salaries", "nb_salaries_actifs", "token_api", "plan"} & set(d)


def test_cle_absente_ou_fausse(env):
    assert flask_app.test_client().get("/api/v1/identite").status_code == 401
    assert _identite("pgl_" + "0" * 64).status_code == 401
    assert _identite("n_importe_quoi").status_code == 401


def test_cle_de_liaison_ne_donne_pas_acces_au_reste_de_l_api(env):
    cle = _generer(_client_pour(env["admin"]))
    cli = flask_app.test_client()
    for url in ("/api/v1/salaries", "/api/v1/bulletins", "/api/v1/me", "/api/v1/stats"):
        assert cli.get(url, headers={"X-Liaison-Key": cle}).status_code == 401, url
        assert cli.get(url, headers={"X-API-Key": cle}).status_code == 401, url
        assert cli.get(url, headers={"Authorization": f"Bearer {cle}"}).status_code == 401, url


def test_token_api_ne_sert_pas_de_cle_de_liaison(env):
    with flask_app.app_context():
        t = db.session.get(Tenant, env["ta"])
        token = t.generate_token(); db.session.commit()
    assert _identite(token).status_code == 401


def test_regeneration_invalide_l_ancienne_et_revocation(env):
    cli = _client_pour(env["admin"])
    c1 = _generer(cli)
    c2 = _generer(cli)
    assert _identite(c1).status_code == 401 and _identite(c2).status_code == 200
    cli.post("/parametres/liaison-etats", data={"action": "revoquer"})
    assert _identite(c2).status_code == 401
    with flask_app.app_context():
        assert db.session.get(Tenant, env["ta"]).cle_liaison_hash is None


def test_tenant_suspendu(env):
    cle = _generer(_client_pour(env["admin"]))
    with flask_app.app_context():
        db.session.get(Tenant, env["ta"]).statut = "SUSPENDU"; db.session.commit()
    assert _identite(cle).status_code == 403


def test_comptable_ne_peut_pas_generer(env):
    r = _client_pour(env["compta"]).post("/parametres/liaison-etats", data={"action": "generer"})
    assert r.status_code in (302, 403)
    with flask_app.app_context():
        assert db.session.get(Tenant, env["ta"]).cle_liaison_hash is None


def test_carte_parametres_affichee(env):
    page = _client_pour(env["admin"]).get("/parametres").get_data(as_text=True)
    assert "Liaison Ameriack États Financiers" in page and "Générer une clé de liaison" in page


# ── Corrections de la page « Clients OAuth » ──────────────────────────────────

def test_admin_cree_client_oauth_sans_erreur(env):
    cli = _client_pour(env["admin"])
    r = cli.post("/api/v1/api/clients/creer", data={"nom": "SIRH"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/api/v1/api/clients")
    with flask_app.app_context():
        c = OAuthClient.query.filter_by(tenant_id=env["ta"]).one()
        cid = c.id
    assert cli.get("/api/v1/api/clients").status_code == 200
    r = cli.post(f"/api/v1/api/clients/{cid}/supprimer")
    assert r.status_code == 302
    with flask_app.app_context():
        assert db.session.get(OAuthClient, cid).actif is False


def test_comptable_ne_peut_pas_creer_client_oauth(env):
    r = _client_pour(env["compta"]).post("/api/v1/api/clients/creer", data={"nom": "Pirate"})
    assert r.status_code == 403
    with flask_app.app_context():
        assert OAuthClient.query.count() == 0


def test_formulaires_oauth_pointent_vers_les_bonnes_urls(env):
    page = _client_pour(env["admin"]).get("/api/v1/api/clients").get_data(as_text=True)
    assert 'action="/api/v1/api/clients/creer"' in page


def test_csrf_exige_sur_creation_client_oauth(env):
    flask_app.config["WTF_CSRF_ENABLED"] = True
    try:
        r = _client_pour(env["admin"]).post("/api/v1/api/clients/creer", data={"nom": "X"})
        assert r.status_code == 400
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = False
