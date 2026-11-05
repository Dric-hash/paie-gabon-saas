"""Connexion avec le compte Ameriack dans PaieGabon : état et PKCE, accès existant relié par le mot de passe
PaieGabon, connexion en un clic ensuite, aucun accès créé automatiquement pour une personne inconnue
(données de paie), création de l'espace par l'administrateur, super-admin exclu, double vérification
conservée, page « Applications liées ». Le compte Ameriack est simulé ; données fictives."""
import io
import json
from urllib.parse import parse_qs, urlparse

import pytest

from test_suppression_salarie import env, _cli, _flash  # noqa: F401,F811
import blueprints.compte_ameriack as A
import models as M
from models import db
from app import app as flask_app

COMPTE = "https://compte.test"
RETOUR = "https://paiegabon.test/connexion/ameriack/retour"


class _Rep(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def identite(sub="s-1", email="a@a.ga", role="ADMIN", ref="AMK-ABC234", nom_org="ACME (fictif)"):
    return {"sub": sub, "email": email, "email_verifie": True, "nom": "Awa Nzé", "role": role,
            "organisation": {"reference": ref, "nom": nom_org, "nif": "202600000 X", "rccm": None, "ville": "Libreville",
                             "telephone": None}}


@pytest.fixture
def ameriack(env, monkeypatch):
    flask_app.config.update(AMERIACK_URL=COMPTE, AMERIACK_CLIENT_ID="paiegabon", AMERIACK_CLIENT_SECRET="s",
                            AMERIACK_RETOUR=RETOUR)
    monkeypatch.delenv("MAIL_PASSWORD", raising=False)
    etat = {"identite": identite()}

    def urlopen(req, timeout=None):
        if etat["identite"] is None:
            raise OSError("réseau")
        return _Rep(json.dumps({"access_token": "j", "utilisateur": etat["identite"]}).encode())
    monkeypatch.setattr(A.urllib.request, "urlopen", urlopen)
    yield etat
    flask_app.config.update(AMERIACK_URL="", AMERIACK_CLIENT_ID="", AMERIACK_CLIENT_SECRET="", AMERIACK_RETOUR="")


def _parcours(cli):
    r = cli.get("/connexion/ameriack")
    q = parse_qs(urlparse(r.headers["Location"]).query)
    assert r.headers["Location"].startswith(COMPTE + "/oauth/authorize?") and q["code_challenge_method"] == ["S256"]
    return cli.get("/connexion/ameriack/retour", query_string={"code": "c", "state": q["state"][0]})


def test_acces_existant_relie_puis_un_clic(env, ameriack):
    cli = flask_app.test_client()
    assert "Se connecter avec mon compte Ameriack" in cli.get("/login").get_data(as_text=True)
    cli.get("/connexion/ameriack")
    r = cli.get("/connexion/ameriack/retour", query_string={"code": "c", "state": "faux"})
    assert "expirée ou invalide" in _flash(cli)
    r = _parcours(cli)
    assert r.headers["Location"].endswith("/connexion/ameriack/relier")
    assert "mot de passe PaieGabon" in cli.get("/connexion/ameriack/relier").get_data(as_text=True)
    cli.post("/connexion/ameriack/relier", data={"password": "faux"})
    assert "incorrect" in _flash(cli)
    r = cli.post("/connexion/ameriack/relier", data={"password": "x"})
    assert r.status_code == 302 and "relié" in _flash(cli)
    u, t = db.session.get(M.Utilisateur, env["admin"]), db.session.get(M.Tenant, env["t"])
    assert (u.external_ref, t.external_ref) == ("ameriack:s-1", "AMK-ABC234")
    page = _cli(env["admin"]).get("/parametres/applications-liees").get_data(as_text=True)
    assert "AMK-ABC234" in page and "Entreprise reliée" in page and "1 accès sur 1" in page


def test_un_clic(env, ameriack):
    u = db.session.get(M.Utilisateur, env["admin"])
    u.external_ref = "ameriack:s-1"
    db.session.get(M.Tenant, env["t"]).external_ref = "AMK-ABC234"
    db.session.commit()
    cli = flask_app.test_client()
    r = _parcours(cli)
    assert r.status_code == 302 and "/connexion" not in r.headers["Location"]


def test_double_verification_conservee(env, ameriack):
    # double vérification PaieGabon active (application TOTP) : toujours demandée
    u = db.session.get(M.Utilisateur, env["admin"])
    u.external_ref = "ameriack:s-1"
    db.session.get(M.Tenant, env["t"]).external_ref = "AMK-ABC234"
    u.twofa_active, u.twofa_methode, u.twofa_secret = True, "totp", "JBSWY3DPEHPK3PXP"
    db.session.commit()
    cli = flask_app.test_client()
    r = _parcours(cli)
    assert r.headers["Location"].endswith("/login/verifier-2fa")


def test_personne_inconnue_et_super_admin(env, ameriack):
    db.session.get(M.Tenant, env["t"]).external_ref = "AMK-ABC234"
    db.session.commit()
    n = M.Utilisateur.query.count()
    ameriack["identite"] = identite(sub="s-2", email="nouveau@a.ga", role="MEMBRE")
    cli = flask_app.test_client()
    r = _parcours(cli)
    assert r.headers["Location"].endswith("/login") and "doit d'abord vous créer un accès" in _flash(cli).replace("&#39;", "'")
    assert M.Utilisateur.query.count() == n
    # même un administrateur Ameriack n'obtient pas d'accès dans une entreprise déjà reliée sans accès créé
    ameriack["identite"] = identite(sub="s-3", email="autre-admin@a.ga", role="ADMIN")
    _parcours(cli)
    assert M.Utilisateur.query.count() == n
    # le super-admin ne se relie jamais
    sa = M.Utilisateur(nom="SA", prenom="S", email="sa@paiegabon.com", role="SUPER_ADMIN", actif=True, email_verifie=True)
    sa.set_password("x")
    db.session.add(sa)
    db.session.commit()
    ameriack["identite"] = identite(sub="s-4", email="sa@paiegabon.com")
    _parcours(cli)
    cli.post("/connexion/ameriack/relier", data={"password": "x"})
    assert "super-administrateur" in _flash(cli) and db.session.get(M.Utilisateur, sa.id).external_ref is None


def test_administrateur_cree_l_espace(env, ameriack):
    ameriack["identite"] = identite(sub="s-9", email="admin@batigab.ga", ref="AMK-NEW234", nom_org="Batigab (fictif)")
    cli = flask_app.test_client()
    r = _parcours(cli)
    assert r.headers["Location"].endswith("/connexion/ameriack/creer")
    assert "Ouvrir PaieGabon pour Batigab (fictif)" in cli.get("/connexion/ameriack/creer").get_data(as_text=True)
    cli.post("/connexion/ameriack/creer", data={"type_compte": "ENTREPRISE"})
    assert "conditions générales" in _flash(cli)
    r = cli.post("/connexion/ameriack/creer", data={"type_compte": "CABINET", "cgu": "1"})
    t = M.Tenant.query.filter_by(external_ref="AMK-NEW234").one()
    u = M.Utilisateur.query.filter_by(email="admin@batigab.ga").one()
    assert (t.denomination, t.statut, t.est_cabinet, t.nif) == ("BATIGAB (FICTIF)", "ESSAI", True, "202600000 X")
    assert (u.role, u.tenant_id, u.external_ref, u.prenom, u.nom) == ("TENANT_ADMIN", t.id, "ameriack:s-9", "Awa", "NZÉ")
    assert M.CategorieEmploi.query.filter_by(tenant_id=t.id).count() == 4
    assert r.status_code == 302


def test_reference_deja_prise(env, ameriack):
    autre = M.Tenant(slug="b", denomination="AUTRE", pays="Gabon", statut="ACTIF", token_api="t2", external_ref="AMK-ABC234")
    db.session.add(autre)
    db.session.commit()
    cli = flask_app.test_client()
    _parcours(cli)
    cli.post("/connexion/ameriack/relier", data={"password": "x"})
    assert "déjà reliée à une autre entreprise" in _flash(cli).replace("&#39;", "'")
    assert db.session.get(M.Utilisateur, env["admin"]).external_ref is None
