# -*- coding: utf-8 -*-
"""tests/test_developpement.py — Développement RH (carrière Phase 3).

Formations (CRUD + renouvellement + attestation), compétences (référentiel +
cartographie salarié, 4 niveaux), évaluations (CRUD + PDF notes A–E), et les
alertes « formations à renouveler ». Aucune de ces fonctions ne touche la paie.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_dev_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-dev")

import pytest
from datetime import date, timedelta

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, Salarie,
                    Formation, Competence, CompetenceSalarie, Evaluation)


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
        t = Tenant(slug="a", denomination="ACME SARL", sigle="A", activite="BTP",
                   convention="BTP", ville="Franceville", pays="Gabon",
                   plan_id=plan.id, statut="ACTIF", token_api="t")
        db.session.add(t)
        db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="admin@a.ga",
                            role="TENANT_ADMIN", tenant_id=t.id, actif=True, email_verifie=True)
        admin.set_password("x")
        db.session.add(admin)
        s = Salarie(tenant_id=t.id, matricule="M1", nom="NDONG", prenom="Jean",
                    date_embauche=date(2020, 1, 1), emploi="Soudeur", statut="ACTIF")
        db.session.add(s)
        db.session.commit()
        ids = {"tenant": t.id, "admin": admin.id, "sal": s.id}
        cli = flask_app.test_client()
        with cli.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True
        cli._ids = ids
        yield cli
        db.session.remove()
        db.drop_all()


# ── Formations ────────────────────────────────────────────────────────────────

def test_formation_creation_et_frise(client):
    sid = client._ids["sal"]
    r = client.post(f"/salaries/{sid}/formations/nouvelle", data={
        "intitule": "Habilitation électrique", "organisme": "APAVE",
        "type": "CERTIFIANTE", "date_debut": "2026-03-01", "date_fin": "2026-03-05",
        "duree_heures": "35", "cout": "250000", "statut": "TERMINEE",
        "certifiante": "1", "date_expiration": "2029-03-05", "resultat": "Réussi",
    }, follow_redirects=True)
    assert r.status_code == 200
    with flask_app.app_context():
        f = Formation.query.filter_by(tenant_id=client._ids["tenant"], salarie_id=sid).first()
        assert f is not None
        assert f.certifiante is True
        assert f.date_expiration == date(2029, 3, 5)
    # Apparaît dans la frise de carrière
    rc = client.get(f"/salaries/{sid}/carriere")
    assert "Habilitation électrique".encode() in rc.data


def test_formation_non_certifiante_sans_expiration(client):
    sid = client._ids["sal"]
    client.post(f"/salaries/{sid}/formations/nouvelle", data={
        "intitule": "Accueil sécurité", "type": "INTERNE", "statut": "TERMINEE",
        "date_expiration": "2030-01-01",  # ignoré car non certifiante
    }, follow_redirects=True)
    with flask_app.app_context():
        f = Formation.query.filter_by(tenant_id=client._ids["tenant"], salarie_id=sid).first()
        assert f.certifiante is False
        assert f.date_expiration is None


def test_formation_attestation_pdf(client):
    sid = client._ids["sal"]
    client.post(f"/salaries/{sid}/formations/nouvelle", data={
        "intitule": "Soudure TIG", "type": "EXTERNE", "statut": "TERMINEE",
        "date_debut": "2026-02-01", "date_fin": "2026-02-10", "duree_heures": "60",
    }, follow_redirects=True)
    with flask_app.app_context():
        fid = Formation.query.filter_by(tenant_id=client._ids["tenant"], salarie_id=sid).first().id
    r = client.get(f"/formations/{fid}/attestation")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_alerte_formation_a_renouveler(client):
    """Une certification expirant sous 60 jours déclenche l'alerte dashboard."""
    sid = client._ids["sal"]
    bientot = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
    client.post(f"/salaries/{sid}/formations/nouvelle", data={
        "intitule": "CACES", "type": "CERTIFIANTE", "statut": "TERMINEE",
        "certifiante": "1", "date_expiration": bientot,
    }, follow_redirects=True)
    from blueprints.tenant.developpement import formations_a_renouveler
    with flask_app.app_context():
        t = Tenant.query.get(client._ids["tenant"])
        assert len(formations_a_renouveler(t)) == 1
    r = client.get("/dashboard", follow_redirects=True)
    assert "renouveler".encode() in r.data.lower()


# ── Compétences ─────────────────────────────────────────────────────────────

def test_referentiel_et_cartographie(client):
    sid = client._ids["sal"]
    # Créer deux compétences au référentiel
    client.post("/competences", data={"action": "ajouter", "libelle": "Soudure TIG",
                                       "categorie": "Technique"}, follow_redirects=True)
    client.post("/competences", data={"action": "ajouter", "libelle": "Anglais",
                                       "categorie": "Langue"}, follow_redirects=True)
    with flask_app.app_context():
        c_tig = Competence.query.filter_by(tenant_id=client._ids["tenant"], libelle="Soudure TIG").first()
        c_ang = Competence.query.filter_by(tenant_id=client._ids["tenant"], libelle="Anglais").first()
        id_tig, id_ang = c_tig.id, c_ang.id
    # Attribuer des niveaux au salarié (TIG=4 Expert, Anglais non évalué)
    client.post(f"/salaries/{sid}/competences", data={
        f"niveau_{id_tig}": "4", f"niveau_{id_ang}": ""}, follow_redirects=True)
    with flask_app.app_context():
        rows = CompetenceSalarie.query.filter_by(tenant_id=client._ids["tenant"], salarie_id=sid).all()
        assert len(rows) == 1
        assert rows[0].competence_id == id_tig and rows[0].niveau == 4


def test_cartographie_mise_a_jour_et_retrait(client):
    sid = client._ids["sal"]
    client.post("/competences", data={"action": "ajouter", "libelle": "Lecture de plan",
                                       "categorie": "Technique"}, follow_redirects=True)
    with flask_app.app_context():
        cid = Competence.query.filter_by(tenant_id=client._ids["tenant"], libelle="Lecture de plan").first().id
    # Niveau 2 puis mise à jour à 3
    client.post(f"/salaries/{sid}/competences", data={f"niveau_{cid}": "2"}, follow_redirects=True)
    client.post(f"/salaries/{sid}/competences", data={f"niveau_{cid}": "3"}, follow_redirects=True)
    with flask_app.app_context():
        cs = CompetenceSalarie.query.filter_by(tenant_id=client._ids["tenant"], salarie_id=sid, competence_id=cid).first()
        assert cs.niveau == 3
    # Remise à vide → retrait
    client.post(f"/salaries/{sid}/competences", data={f"niveau_{cid}": ""}, follow_redirects=True)
    with flask_app.app_context():
        assert CompetenceSalarie.query.filter_by(tenant_id=client._ids["tenant"], salarie_id=sid, competence_id=cid).count() == 0


def test_competence_doublon_refuse(client):
    client.post("/competences", data={"action": "ajouter", "libelle": "Sécurité chantier",
                                      "categorie": "Sécurité"}, follow_redirects=True)
    client.post("/competences", data={"action": "ajouter", "libelle": "Sécurité chantier",
                                      "categorie": "Sécurité"}, follow_redirects=True)
    with flask_app.app_context():
        n = Competence.query.filter_by(tenant_id=client._ids["tenant"], libelle="Sécurité chantier").count()
        assert n == 1


# ── Évaluations ───────────────────────────────────────────────────────────────

def test_evaluation_creation_note_et_frise(client):
    sid = client._ids["sal"]
    r = client.post(f"/salaries/{sid}/evaluations/nouvelle", data={
        "type": "ANNUELLE", "date_evaluation": "2026-06-30", "periode": "2026",
        "note_globale": "B", "points_forts": "Rigueur", "axes_amelioration": "Délais",
        "objectifs": "Encadrer un apprenti", "statut": "REALISEE",
    }, follow_redirects=True)
    assert r.status_code == 200
    with flask_app.app_context():
        e = Evaluation.query.filter_by(tenant_id=client._ids["tenant"], salarie_id=sid).first()
        assert e is not None and e.note_globale == "B"
        assert e.note_libelle == "Très bien"
        assert e.evaluateur_nom  # rempli par défaut avec l'utilisateur courant
    rc = client.get(f"/salaries/{sid}/carriere")
    assert "Appréciation".encode() in rc.data or "Évaluation".encode() in rc.data


def test_evaluation_note_invalide_ignoree(client):
    sid = client._ids["sal"]
    client.post(f"/salaries/{sid}/evaluations/nouvelle", data={
        "type": "ANNUELLE", "date_evaluation": "2026-06-30", "note_globale": "Z"},
        follow_redirects=True)
    with flask_app.app_context():
        e = Evaluation.query.filter_by(tenant_id=client._ids["tenant"], salarie_id=sid).first()
        assert e.note_globale is None   # "Z" hors A–E → ignorée


def test_evaluation_pdf(client):
    sid = client._ids["sal"]
    client.post(f"/salaries/{sid}/evaluations/nouvelle", data={
        "type": "PROFESSIONNEL", "date_evaluation": "2026-06-30", "note_globale": "A",
        "points_forts": "Excellent", "statut": "REALISEE"}, follow_redirects=True)
    with flask_app.app_context():
        eid = Evaluation.query.filter_by(tenant_id=client._ids["tenant"], salarie_id=sid).first().id
    r = client.get(f"/evaluations/{eid}/pdf")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_isolation_tenant_formation(client):
    """Une formation d'un autre tenant n'est pas visible/éditable."""
    with flask_app.app_context():
        plan = Plan.query.first()
        t2 = Tenant(slug="b", denomination="AUTRE", sigle="B", activite="Commerce",
                    convention="COMMERCE", ville="Libreville", pays="Gabon",
                    plan_id=plan.id, statut="ACTIF", token_api="t2")
        db.session.add(t2); db.session.flush()
        s2 = Salarie(tenant_id=t2.id, matricule="X1", nom="AUTRE", prenom="Z",
                     date_embauche=date(2021, 1, 1), statut="ACTIF")
        db.session.add(s2); db.session.flush()
        f2 = Formation(tenant_id=t2.id, salarie_id=s2.id, intitule="Secrète")
        db.session.add(f2); db.session.commit()
        fid = f2.id
    # L'admin du tenant A ne peut pas éditer la formation du tenant B
    r = client.get(f"/formations/{fid}/modifier")
    assert r.status_code == 404
