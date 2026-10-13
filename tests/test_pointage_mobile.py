# -*- coding: utf-8 -*-
"""tests/test_pointage_mobile.py — PWA de pointage chantier.

Couvre : connexion pointeur (code+PIN), roster limité au site, synchronisation
idempotente (pas de doublon au rejeu), quarantaine (un pointage SOUMIS n'entre
PAS dans la paie ni la facturation), validation RH → le pointage entre dans la
paie, alerte géo hors-zone, et isolation (un pointeur ne pointe que son équipe).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_mobile_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-mobile-pointage-123456")

import pytest
from datetime import date

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, Salarie, Journalier, Site,
                    AffectationSite, Pointage, PointeurChantier)

# Pointage mobile d'un salarié ciblé, hors équipe pour un autre.
PIN = "4321"


@pytest.fixture
def client():
    flask_app.config.update({"TESTING": True, "WTF_CSRF_ENABLED": False,
                             "SERVER_NAME": "localhost"})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        plan = Plan(code="PRO", nom="Pro", prix_mensuel=35000,
                    max_salaries=50, max_utilisateurs=3, actif=True)
        db.session.add(plan)
        db.session.flush()
        t = Tenant(slug="btp-a", denomination="BTP AKANDA SARL", sigle="BA",
                   activite="BTP", convention="BTP", ville="Libreville",
                   pays="Gabon", plan_id=plan.id, statut="ACTIF", token_api="tok_a")
        db.session.add(t)
        db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="admin@a.ga",
                            role="TENANT_ADMIN", tenant_id=t.id,
                            actif=True, email_verifie=True)
        admin.set_password("MotDePasse1")
        db.session.add(admin)
        # Chantier géolocalisé (Libreville) + rayon 200 m
        site = Site(tenant_id=t.id, nom="Chantier Akanda", code="AKD",
                    latitude=0.4162, longitude=9.4673, rayon_m=200, actif=True)
        site2 = Site(tenant_id=t.id, nom="Chantier Owendo", code="OWD", actif=True)
        db.session.add_all([site, site2])
        db.session.flush()
        # Deux salariés sur le chantier Akanda, un sur Owendo (hors équipe)
        s1 = Salarie(tenant_id=t.id, matricule="M001", nom="NDONG", prenom="Jean",
                     date_embauche=date(2023, 1, 1), emploi="Maçon", statut="ACTIF")
        s2 = Salarie(tenant_id=t.id, matricule="M002", nom="OBAME", prenom="Luc",
                     date_embauche=date(2023, 1, 1), emploi="Ferrailleur", statut="ACTIF")
        s3 = Salarie(tenant_id=t.id, matricule="M003", nom="MBA", prenom="Paul",
                     date_embauche=date(2023, 1, 1), emploi="Chef", statut="ACTIF")
        db.session.add_all([s1, s2, s3])
        db.session.flush()
        # Un journalier affecté au chantier Akanda
        j1 = Journalier(tenant_id=t.id, nom="OYONO", prenom="Marc",
                        profession="Manœuvre", taux_horaire=1500, statut="ACTIF")
        db.session.add(j1)
        db.session.flush()
        db.session.add_all([
            AffectationSite(tenant_id=t.id, site_id=site.id, salarie_id=s1.id,
                            date_debut=date(2026, 1, 1), actif=True),
            AffectationSite(tenant_id=t.id, site_id=site.id, salarie_id=s2.id,
                            date_debut=date(2026, 1, 1), actif=True),
            AffectationSite(tenant_id=t.id, site_id=site.id, journalier_id=j1.id,
                            date_debut=date(2026, 1, 1), actif=True),
            AffectationSite(tenant_id=t.id, site_id=site2.id, salarie_id=s3.id,
                            date_debut=date(2026, 1, 1), actif=True),
        ])
        # Pointeur rattaché au chantier Akanda, ET lui-même le salarié s2
        p = PointeurChantier(tenant_id=t.id, site_id=site.id,
                             nom="Luc — chef Akanda", salarie_id=s2.id,
                             code="PTTEST1", actif=True)
        p.set_pin(PIN)
        db.session.add(p)
        db.session.commit()
        ids = {"tenant": t.id, "admin": admin.id, "site": site.id,
               "s1": s1.id, "s2": s2.id, "s3": s3.id, "j1": j1.id, "code": p.code}
        cli = flask_app.test_client()
        # Session RH établie d'emblée (les appels mobiles utilisent le jeton Bearer,
        # pas la session, donc cohabiter avec une session admin est sans effet).
        with cli.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True
        cli._ids = ids
        yield cli
        db.session.remove()
        db.drop_all()


def _login(client):
    r = client.post("/m/api/login", json={"code": client._ids["code"], "pin": PIN})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    return data["token"], data


def _auth(token):
    return {"Authorization": "Bearer " + token}


# ─────────────────────────────── Connexion ────────────────────────────────

def test_login_ok_et_roster_limite_au_site(client):
    token, data = _login(client)
    assert token
    sal = {e["id"] for e in data["salaries"] if e["type"] == "S"}
    jour = {e["id"] for e in data["salaries"] if e["type"] == "J"}
    # Salariés du chantier Akanda (pas celui d'Owendo) + le journalier affecté
    assert sal == {client._ids["s1"], client._ids["s2"]}
    assert jour == {client._ids["j1"]}
    assert data["site"]["nom"] == "Chantier Akanda"
    # Le pointeur est relié au salarié s2 → marqué "moi" dans sa propre équipe
    moi = [e for e in data["salaries"] if e.get("est_pointeur")]
    assert len(moi) == 1 and moi[0]["id"] == client._ids["s2"]


def test_login_mauvais_pin(client):
    r = client.post("/m/api/login", json={"code": client._ids["code"], "pin": "0000"})
    assert r.status_code == 401
    assert r.get_json()["ok"] is False


def test_sync_sans_token_refuse(client):
    r = client.post("/m/api/sync", json={"pointages": []})
    assert r.status_code == 401


# ─────────────────────────── Sync + quarantaine ───────────────────────────

def test_sync_cree_pointage_en_quarantaine(client):
    token, _ = _login(client)
    uid = "uuid-aaa-111"
    payload = {"pointages": [{
        "uuid": uid, "date": "2026-06-10", "salarie_id": client._ids["s1"],
        "present": True, "absent": False,
        "heures_normales": 8, "heures_sup_10": 2,
        "latitude": 0.4163, "longitude": 9.4674,   # ~15 m du chantier → sur site
    }]}
    r = client.post("/m/api/sync", json=payload, headers=_auth(token))
    assert r.status_code == 200
    res = r.get_json()["resultats"][0]
    assert res["etat"] == "cree"
    assert res["hors_zone"] is False
    with flask_app.app_context():
        pt = Pointage.query.filter_by(uuid_client=uid).first()
        assert pt is not None
        assert pt.statut == "SOUMIS"       # quarantaine
        assert pt.source == "MOBILE"
        assert pt.heure_serveur is not None
        assert float(pt.heures_sup_10) == 2


def test_sync_idempotente_pas_de_doublon(client):
    token, _ = _login(client)
    item = {"uuid": "uuid-dup", "date": "2026-06-11", "salarie_id": client._ids["s1"],
            "present": True, "absent": False, "heures_normales": 8}
    r1 = client.post("/m/api/sync", json={"pointages": [item]}, headers=_auth(token))
    assert r1.get_json()["resultats"][0]["etat"] == "cree"
    # Rejeu de la même file (reconnexion) → doublon, pas de nouvelle ligne
    r2 = client.post("/m/api/sync", json={"pointages": [item]}, headers=_auth(token))
    assert r2.get_json()["resultats"][0]["etat"] == "doublon"
    with flask_app.app_context():
        assert Pointage.query.filter_by(uuid_client="uuid-dup").count() == 1


def test_sync_refuse_salarie_hors_equipe(client):
    token, _ = _login(client)
    r = client.post("/m/api/sync", json={"pointages": [{
        "uuid": "uuid-hors", "date": "2026-06-10", "salarie_id": client._ids["s3"],
        "present": True}]}, headers=_auth(token))
    assert r.get_json()["resultats"][0]["etat"] == "erreur"
    with flask_app.app_context():
        assert Pointage.query.filter_by(uuid_client="uuid-hors").count() == 0


def test_alerte_geo_hors_zone(client):
    token, _ = _login(client)
    r = client.post("/m/api/sync", json={"pointages": [{
        "uuid": "uuid-loin", "date": "2026-06-12", "salarie_id": client._ids["s2"],
        "present": True, "heures_normales": 8,
        "latitude": 0.5000, "longitude": 9.6000,   # ~20 km du chantier
    }]}, headers=_auth(token))
    assert r.get_json()["resultats"][0]["hors_zone"] is True
    with flask_app.app_context():
        pt = Pointage.query.filter_by(uuid_client="uuid-loin").first()
        assert pt.geo_hors_zone is True


# ───────────── Quarantaine vis-à-vis de la paie / facturation ─────────────

def _auth_session(client):
    # La session RH est déjà établie par la fixture ; rien à faire ici.
    return


def test_pointage_soumis_absent_de_la_paie(client):
    """Un pointage SOUMIS ne doit pas alimenter le pré-calcul du bulletin."""
    token, _ = _login(client)
    client.post("/m/api/sync", json={"pointages": [{
        "uuid": "uuid-paie", "date": "2026-06-10", "salarie_id": client._ids["s1"],
        "present": True, "heures_normales": 8, "heures_sup_10": 5}]}, headers=_auth(token))
    _auth_session(client)
    # API qui alimente la saisie du bulletin
    r = client.get(f"/api/salarie/{client._ids['s1']}/pointage-mois?mois=6&annee=2026")
    data = r.get_json()
    assert data["nb_jours"] == 0, "un pointage SOUMIS ne doit pas compter en paie"


def test_validation_rh_fait_entrer_en_paie(client):
    token, _ = _login(client)
    client.post("/m/api/sync", json={"pointages": [{
        "uuid": "uuid-val", "date": "2026-06-10", "salarie_id": client._ids["s1"],
        "present": True, "heures_normales": 8}]}, headers=_auth(token))
    with flask_app.app_context():
        pt = Pointage.query.filter_by(uuid_client="uuid-val").first()
        pid = pt.id
    _auth_session(client)
    # Avant validation : absent de la paie
    r = client.get(f"/api/salarie/{client._ids['s1']}/pointage-mois?mois=6&annee=2026")
    assert r.get_json()["nb_jours"] == 0
    # Validation RH
    client.post("/pointages-mobiles/valider", data={"pointage_ids": str(pid)},
                follow_redirects=True)
    with flask_app.app_context():
        assert Pointage.query.get(pid).statut == "VALIDE"
    # Après validation : compte en paie
    r = client.get(f"/api/salarie/{client._ids['s1']}/pointage-mois?mois=6&annee=2026")
    assert r.get_json()["nb_jours"] == 1


def test_rejet_rh(client):
    token, _ = _login(client)
    client.post("/m/api/sync", json={"pointages": [{
        "uuid": "uuid-rej", "date": "2026-06-10", "salarie_id": client._ids["s1"],
        "present": True, "heures_normales": 8}]}, headers=_auth(token))
    with flask_app.app_context():
        pid = Pointage.query.filter_by(uuid_client="uuid-rej").first().id
    _auth_session(client)
    client.post("/pointages-mobiles/rejeter", data={"pointage_ids": str(pid)},
                follow_redirects=True)
    with flask_app.app_context():
        assert Pointage.query.get(pid).statut == "REJETE"
    r = client.get(f"/api/salarie/{client._ids['s1']}/pointage-mois?mois=6&annee=2026")
    assert r.get_json()["nb_jours"] == 0


# ─────────────────────────── Côté RH : écrans ─────────────────────────────

def test_rh_cree_pointeur(client):
    _auth_session(client)
    r = client.post("/pointeurs/nouveau", data={
        "nom": "Pointeuse Owendo", "site_id": "", "pin": "1234"},
        follow_redirects=True)
    assert r.status_code == 200
    with flask_app.app_context():
        assert PointeurChantier.query.filter_by(nom="Pointeuse Owendo").count() == 1


def test_sync_journalier_cree_et_quarantaine(client):
    """Un journalier du chantier peut être pointé depuis le mobile (type J)."""
    token, _ = _login(client)
    r = client.post("/m/api/sync", json={"pointages": [{
        "uuid": "uuid-jour", "date": "2026-06-10", "type": "J",
        "worker_id": client._ids["j1"], "present": True, "heures_normales": 8}]},
        headers=_auth(token))
    assert r.get_json()["resultats"][0]["etat"] == "cree"
    with flask_app.app_context():
        pt = Pointage.query.filter_by(uuid_client="uuid-jour").first()
        assert pt.journalier_id == client._ids["j1"]
        assert pt.salarie_id is None
        assert pt.statut == "SOUMIS"       # quarantaine aussi pour les journaliers


def test_sync_journalier_hors_equipe_refuse(client):
    """Un journalier d'id inconnu (ou non affecté) est refusé."""
    token, _ = _login(client)
    r = client.post("/m/api/sync", json={"pointages": [{
        "uuid": "uuid-jx", "date": "2026-06-10", "type": "J",
        "worker_id": 999999, "present": True}]}, headers=_auth(token))
    assert r.get_json()["resultats"][0]["etat"] == "erreur"


def test_pointeur_peut_se_pointer_lui_meme(client):
    """Le pointeur (relié au salarié s2) figure dans son équipe et se pointe."""
    token, data = _login(client)
    assert any(e.get("est_pointeur") for e in data["salaries"])
    r = client.post("/m/api/sync", json={"pointages": [{
        "uuid": "uuid-moi", "date": "2026-06-10", "type": "S",
        "worker_id": client._ids["s2"], "present": True, "heures_normales": 8}]},
        headers=_auth(token))
    assert r.get_json()["resultats"][0]["etat"] == "cree"
    with flask_app.app_context():
        assert Pointage.query.filter_by(uuid_client="uuid-moi",
                                        salarie_id=client._ids["s2"]).count() == 1


def test_rh_cree_pointeur_relie_journalier(client):
    _auth_session(client)
    client.post("/pointeurs/nouveau", data={
        "nom": "Pointeur journalier", "site_id": str(client._ids["site"]),
        "travailleur": "J:%d" % client._ids["j1"], "pin": "1234"},
        follow_redirects=True)
    with flask_app.app_context():
        p = PointeurChantier.query.filter_by(nom="Pointeur journalier").first()
        assert p is not None
        assert p.journalier_id == client._ids["j1"]
        assert p.salarie_id is None


def test_rh_page_validation_affiche_soumis(client):
    token, _ = _login(client)
    client.post("/m/api/sync", json={"pointages": [{
        "uuid": "uuid-aff", "date": "2026-06-10", "salarie_id": client._ids["s1"],
        "present": True, "heures_normales": 8}]}, headers=_auth(token))
    _auth_session(client)
    r = client.get("/pointages-mobiles")
    assert r.status_code == 200
    assert "NDONG".encode() in r.data
