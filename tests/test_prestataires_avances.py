"""tests/test_prestataires_avances.py — Avances prestataires + relevé imprimable.

Démarre l'app Flask avec SQLite en mémoire, crée un tenant + admin + prestataire,
puis vérifie : création d'avance, affichage sur la fiche, rendu du relevé
imprimable (avec la liste des avances), suppression, et isolation multi-tenant.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-avances")

import pytest
from datetime import date

from app import app as flask_app
from models import db, Plan, Tenant, Utilisateur, Prestataire, AvancePrestataire


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
        t_a = Tenant(slug="ent-a", denomination="ENTREPRISE A", sigle="EA",
                     activite="BTP", ville="Libreville", pays="Gabon",
                     plan_id=plan.id, statut="ACTIF", token_api="tok_a")
        t_b = Tenant(slug="ent-b", denomination="ENTREPRISE B", sigle="EB",
                     activite="BTP", ville="Port-Gentil", pays="Gabon",
                     plan_id=plan.id, statut="ACTIF", token_api="tok_b")
        db.session.add_all([t_a, t_b])
        db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="admin@a.ga",
                            role="TENANT_ADMIN", tenant_id=t_a.id,
                            actif=True, email_verifie=True)
        admin.set_password("MotDePasse1")
        db.session.add(admin)
        # Prestataire sous-traitant du tenant A + un du tenant B (isolation)
        st = Prestataire(tenant_id=t_a.id, code="ST001", categorie="SOUS_TRAITANT",
                         raison_sociale="BTP SOUS-TRAITANT SARL")
        autre = Prestataire(tenant_id=t_b.id, code="ST999", categorie="SOUS_TRAITANT",
                            raison_sociale="AUTRE TENANT SARL")
        db.session.add_all([st, autre])
        db.session.commit()
        ids = {"tenant_a": t_a.id, "admin": admin.id,
               "prest_a": st.id, "prest_b": autre.id}
        cli = flask_app.test_client()
        with cli.session_transaction() as sess:
            sess["_user_id"] = str(ids["admin"])
            sess["_fresh"] = True
        cli._ids = ids
        yield cli
        db.session.remove()
        db.drop_all()


def test_creer_avance(client):
    pid = client._ids["prest_a"]
    r = client.post(f"/prestataires/{pid}/avances/nouvelle", data={
        "montant": "150000", "date_avance": "2026-06-10",
        "mode_paiement": "MOBILE_MONEY", "reference": "MM-7788",
        "motif": "Avance de démarrage chantier",
    }, follow_redirects=True)
    assert r.status_code == 200
    with flask_app.app_context():
        av = AvancePrestataire.query.filter_by(prestataire_id=pid).all()
        assert len(av) == 1
        assert float(av[0].montant) == 150000
        assert av[0].motif == "Avance de démarrage chantier"
        assert av[0].reste_a_regulariser == 150000


def test_avance_refuse_montant_negatif(client):
    pid = client._ids["prest_a"]
    client.post(f"/prestataires/{pid}/avances/nouvelle",
                data={"montant": "-5000", "date_avance": "2026-06-10"},
                follow_redirects=True)
    with flask_app.app_context():
        assert AvancePrestataire.query.filter_by(prestataire_id=pid).count() == 0


def test_avance_visible_sur_fiche(client):
    pid = client._ids["prest_a"]
    client.post(f"/prestataires/{pid}/avances/nouvelle",
                data={"montant": "80000", "date_avance": "2026-06-12",
                      "motif": "Acompte matériaux"}, follow_redirects=True)
    r = client.get(f"/prestataires/{pid}")
    assert r.status_code == 200
    html = r.data.decode("utf-8", errors="ignore")
    assert "Acompte matériaux" in html
    assert "Avances perçues" in html


def test_releve_imprimable_contient_avances(client):
    pid = client._ids["prest_a"]
    client.post(f"/prestataires/{pid}/avances/nouvelle",
                data={"montant": "200000", "date_avance": "2026-06-05",
                      "motif": "Avance n°1"}, follow_redirects=True)
    client.post(f"/prestataires/{pid}/avances/nouvelle",
                data={"montant": "50000", "date_avance": "2026-06-15",
                      "motif": "Avance n°2"}, follow_redirects=True)
    r = client.get(f"/prestataires/{pid}/releve")
    assert r.status_code == 200
    html = r.data.decode("utf-8", errors="ignore")
    assert "Relevé" in html and "Sous-traitant" in html
    assert "Avance n°1" in html and "Avance n°2" in html
    # Total des avances = 250 000 (formaté avec espace insécable ou normal)
    assert "250" in html
    assert "Avances déjà perçues (2)" in html


def test_supprimer_avance(client):
    pid = client._ids["prest_a"]
    client.post(f"/prestataires/{pid}/avances/nouvelle",
                data={"montant": "30000", "date_avance": "2026-06-18"},
                follow_redirects=True)
    with flask_app.app_context():
        aid = AvancePrestataire.query.filter_by(prestataire_id=pid).first().id
    client.post(f"/prestataires/avances/{aid}/supprimer", follow_redirects=True)
    with flask_app.app_context():
        assert AvancePrestataire.query.get(aid) is None


def test_isolation_multi_tenant(client):
    # L'admin du tenant A ne doit pas accéder au relevé d'un prestataire du tenant B.
    pid_b = client._ids["prest_b"]
    r = client.get(f"/prestataires/{pid_b}/releve")
    assert r.status_code == 404
    r2 = client.post(f"/prestataires/{pid_b}/avances/nouvelle",
                     data={"montant": "999", "date_avance": "2026-06-01"})
    assert r2.status_code == 404
