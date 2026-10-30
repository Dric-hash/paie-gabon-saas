# -*- coding: utf-8 -*-
"""tests/test_carriere.py — Gestion de carrière (Phase 1).

Vérifie la frise de carrière, et surtout la promotion : elle crée un AVENANT de
contrat (nouveau contrat actif, ancien désactivé) + un EvenementCarriere, met à
jour la fiche salarié, et ne touche PAS un bulletin déjà validé.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_carriere_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-carriere")

import pytest
from datetime import date

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, Salarie, Contrat,
                    CategorieEmploi, EvenementCarriere, BulletinPaie, PeriodePaie)


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
        c2 = CategorieEmploi(tenant_id=t.id, code="C2", libelle="Employé", salaire_minimum=150000)
        c3 = CategorieEmploi(tenant_id=t.id, code="C3", libelle="Agent de maîtrise", salaire_minimum=250000)
        db.session.add_all([c2, c3])
        db.session.flush()
        s = Salarie(tenant_id=t.id, matricule="M1", nom="NDONG", prenom="Jean",
                    date_embauche=date(2023, 1, 1), emploi="Vendeur",
                    categorie_id=c2.id, statut="ACTIF")
        db.session.add(s)
        db.session.flush()
        contrat = Contrat(tenant_id=t.id, salarie_id=s.id, type_contrat="CDI",
                          date_debut=date(2023, 1, 1), salaire_base=200000,
                          poste="Vendeur", categorie_id=c2.id, actif=True)
        db.session.add(contrat)
        db.session.commit()
        ids = {"tenant": t.id, "admin": admin.id, "sal": s.id, "c2": c2.id, "c3": c3.id}
        cli = flask_app.test_client()
        with cli.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True
        cli._ids = ids
        yield cli
        db.session.remove()
        db.drop_all()


def test_frise_affiche_embauche(client):
    r = client.get(f"/salaries/{client._ids['sal']}/carriere")
    assert r.status_code == 200
    assert "Embauche".encode() in r.data


def test_promotion_cree_avenant_et_evenement(client):
    sid = client._ids["sal"]
    r = client.post(f"/salaries/{sid}/promouvoir", data={
        "type_evt": "PROMOTION", "date_effet": "2026-06-01",
        "poste": "Chef de rayon", "echelon": "2",
        "categorie_id": str(client._ids["c3"]), "salaire_base": "300000",
        "motif": "Évolution",
    }, follow_redirects=True)
    assert r.status_code == 200
    with flask_app.app_context():
        # Un seul contrat actif, c'est le nouveau (300000, Chef de rayon, C3)
        actifs = Contrat.query.filter_by(salarie_id=sid, actif=True).all()
        assert len(actifs) == 1
        assert float(actifs[0].salaire_base) == 300000
        assert actifs[0].poste == "Chef de rayon"
        # L'ancien contrat est désactivé → 2 contrats au total
        assert Contrat.query.filter_by(salarie_id=sid).count() == 2
        # Fiche salarié mise à jour
        s = Salarie.query.get(sid)
        assert s.emploi == "Chef de rayon" and s.echelon == "2"
        assert s.categorie_id == client._ids["c3"]
        # Événement de carrière créé, lié à l'avenant
        evt = EvenementCarriere.query.filter_by(salarie_id=sid, type="PROMOTION").first()
        assert evt is not None
        assert evt.contrat_id == actifs[0].id
        assert "300" in (evt.nouvelle_valeur or "")


def test_promotion_reprend_primes_recurrentes_et_fin_essai(client):
    """L'avenant reprend les éléments récurrents et la fin de période d'essai du contrat en cours."""
    sid = client._ids["sal"]
    with flask_app.app_context():
        c = Contrat.query.filter_by(salarie_id=sid, actif=True).one()
        c.elements_recurrents = '{"prime_transport": 25000}'
        c.date_fin_essai = date(2026, 9, 30)
        c.date_fin = date(2027, 12, 31)
        db.session.commit()
    client.post(f"/salaries/{sid}/promouvoir", data={"type_evt": "AUGMENTATION", "date_effet": "2026-07-01",
                                                    "salaire_base": "220000"})
    with flask_app.app_context():
        av = Contrat.query.filter_by(salarie_id=sid, actif=True).one()
        assert float(av.salaire_base) == 220000
        assert (av.elements_recurrents, av.date_fin_essai, av.date_fin) == (
            '{"prime_transport": 25000}', date(2026, 9, 30), date(2027, 12, 31))


def test_promotion_ne_touche_pas_bulletin_valide(client):
    """Un bulletin déjà validé n'est jamais modifié par une promotion."""
    sid = client._ids["sal"]
    with flask_app.app_context():
        per = PeriodePaie(tenant_id=client._ids["tenant"], mois=5, annee=2026,
                          libelle_mois="MAI", statut="OUVERT")
        db.session.add(per)
        db.session.flush()
        b = BulletinPaie(tenant_id=client._ids["tenant"], salarie_id=sid, periode_id=per.id,
                         statut="VALIDÉ", salaire_base=200000, salaire_brut=200000,
                         net_a_payer=170000)
        db.session.add(b)
        db.session.commit()
        bid = b.id
    client.post(f"/salaries/{sid}/promouvoir", data={
        "type_evt": "AUGMENTATION", "date_effet": "2026-06-01",
        "poste": "Vendeur", "categorie_id": "", "salaire_base": "260000",
    }, follow_redirects=True)
    with flask_app.app_context():
        b2 = BulletinPaie.query.get(bid)
        assert b2.statut in ("VALIDÉ", "VALIDE")
        assert float(b2.salaire_base) == 200000   # inchangé
        assert float(b2.net_a_payer) == 170000


def test_evenement_manuel_et_suppression(client):
    sid = client._ids["sal"]
    client.post(f"/salaries/{sid}/carriere/evenement", data={
        "type": "FORMATION", "date_effet": "2026-03-10",
        "titre": "Formation sécurité", "details": "2 jours"},
        follow_redirects=True)
    with flask_app.app_context():
        evt = EvenementCarriere.query.filter_by(salarie_id=sid, type="FORMATION").first()
        assert evt is not None
        eid = evt.id
    client.post(f"/carriere/evenement/{eid}/supprimer", follow_redirects=True)
    with flask_app.app_context():
        assert EvenementCarriere.query.get(eid) is None


def test_attestation_promotion_pdf(client):
    sid = client._ids["sal"]
    client.post(f"/salaries/{sid}/promouvoir", data={
        "type_evt": "PROMOTION", "date_effet": "2026-06-01", "poste": "Chef",
        "categorie_id": str(client._ids["c3"]), "salaire_base": "300000"},
        follow_redirects=True)
    with flask_app.app_context():
        eid = EvenementCarriere.query.filter_by(salarie_id=sid).first().id
    r = client.get(f"/carriere/evenement/{eid}/attestation")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"
