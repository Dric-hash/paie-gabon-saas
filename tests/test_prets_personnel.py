# -*- coding: utf-8 -*-
"""tests/test_prets_personnel.py — Prêts au personnel + intégration bulletin.

Vérifie le cycle de vie d'un prêt (octroi, remboursement manuel, solde, reçu PDF)
ET l'intégration avec le bulletin de paie : la « Retenue prêt » saisie sur un
bulletin validé est imputée automatiquement sur les prêts EN_COURS du salarié,
réduit le net à payer, et déclenche l'alerte de quotité cessible en cas de
dépassement. On teste aussi la ré-imputation propre lors d'une re-sauvegarde.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_prets_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-prets")

import pytest
from datetime import date

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, Salarie, PeriodePaie,
                    PretPersonnel, RemboursementPret, BulletinPaie, Acompte)


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
        t = Tenant(slug="ent-a", denomination="ENTREPRISE A", sigle="EA",
                   activite="Commerce", convention="COMMERCE", ville="Libreville",
                   pays="Gabon", plan_id=plan.id, statut="ACTIF", token_api="tok_a",
                   quotite_cessible_pct=50)
        db.session.add(t)
        db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="admin@a.ga",
                            role="TENANT_ADMIN", tenant_id=t.id,
                            actif=True, email_verifie=True)
        admin.set_password("MotDePasse1")
        db.session.add(admin)
        sal = Salarie(tenant_id=t.id, matricule="EA001", nom="NDONG", prenom="Jean",
                      date_embauche=date(2023, 1, 1), emploi="Vendeur",
                      situation_matrimoniale="CELIBATAIRE", nb_enfants=0,
                      nombre_parts=1, statut="ACTIF")
        db.session.add(sal)
        periode = PeriodePaie(tenant_id=t.id, mois=6, annee=2026,
                              libelle_mois="JUIN", statut="OUVERT")
        db.session.add(periode)
        db.session.commit()
        ids = {"tenant": t.id, "admin": admin.id, "sal": sal.id, "periode": periode.id}
        cli = flask_app.test_client()
        with cli.session_transaction() as sess:
            sess["_user_id"] = str(ids["admin"])
            sess["_fresh"] = True
        cli._ids = ids
        yield cli
        db.session.remove()
        db.drop_all()


def _creer_pret(client, montant=300000, nb=3, date_octroi="2026-05-10"):
    r = client.post("/prets/nouveau", data={
        "salarie_id": client._ids["sal"], "montant_total": str(montant),
        "nb_echeances": str(nb), "date_octroi": date_octroi,
        "motif": "Avance scolaire",
    }, follow_redirects=True)
    assert r.status_code == 200
    with flask_app.app_context():
        return PretPersonnel.query.filter_by(tenant_id=client._ids["tenant"]).first()


# ─────────────────────────── Cycle de vie du prêt ───────────────────────────

def test_octroi_pret(client):
    p = _creer_pret(client)
    assert p is not None
    assert float(p.montant_total) == 300000
    assert p.nb_echeances == 3
    assert p.statut == "EN_COURS"
    assert p.montant_echeance == 100000
    assert p.montant_restant == 300000


def test_remboursement_manuel_et_solde(client):
    p = _creer_pret(client)
    pid = p.id
    # Deux mensualités
    client.post(f"/prets/{pid}/rembourser",
                data={"montant": "100000", "mois": "6", "annee": "2026"},
                follow_redirects=True)
    client.post(f"/prets/{pid}/rembourser",
                data={"montant": "100000", "mois": "7", "annee": "2026"},
                follow_redirects=True)
    with flask_app.app_context():
        p2 = PretPersonnel.query.get(pid)
        assert float(p2.montant_rembourse) == 200000
        assert p2.montant_restant == 100000
        assert p2.statut == "EN_COURS"
    # Sur-remboursement : plafonné au restant → SOLDE
    client.post(f"/prets/{pid}/rembourser",
                data={"montant": "999999", "mois": "8", "annee": "2026"},
                follow_redirects=True)
    with flask_app.app_context():
        p3 = PretPersonnel.query.get(pid)
        assert float(p3.montant_rembourse) == 300000
        assert p3.montant_restant == 0
        assert p3.statut == "SOLDE"


def test_recu_pdf(client):
    p = _creer_pret(client)
    r = client.get(f"/prets/{p.id}/recu")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_annulation(client):
    p = _creer_pret(client)
    client.post(f"/prets/{p.id}/annuler", data={}, follow_redirects=True)
    with flask_app.app_context():
        assert PretPersonnel.query.get(p.id).statut == "ANNULE"


# ───────────────────── Intégration bulletin ↔ prêt ──────────────────────────

def test_retenue_pret_sur_bulletin_impute_et_reduit_net(client):
    p = _creer_pret(client)
    pid = p.id
    sid, pr_id = client._ids["sal"], client._ids["periode"]
    r = client.post("/bulletins/saisie", data={
        "salarie_id": sid, "periode_id": pr_id,
        "salaire_base": "400000", "retenue_pret": "100000",
        "action": "valider",
    }, follow_redirects=True)
    assert r.status_code == 200
    with flask_app.app_context():
        b = BulletinPaie.query.filter_by(salarie_id=sid, periode_id=pr_id).first()
        assert b is not None
        assert b.statut in ("VALIDÉ", "VALIDE")
        assert float(b.retenue_pret) == 100000
        # Le net doit être amputé de la retenue prêt
        brut = float(b.salaire_brut or 0)
        # un bulletin sans retenue prêt a un net > net avec retenue
        assert float(b.net_a_payer) < brut
        # Un remboursement rattaché au bulletin a été créé
        rbs = RemboursementPret.query.filter_by(bulletin_id=b.id).all()
        assert len(rbs) == 1
        assert float(rbs[0].montant) == 100000
        assert rbs[0].mois == 6 and rbs[0].annee == 2026
        p2 = PretPersonnel.query.get(pid)
        assert float(p2.montant_rembourse) == 100000
        assert p2.montant_restant == 200000


def test_retenue_pret_non_imputee_en_brouillon(client):
    p = _creer_pret(client)
    sid, pr_id = client._ids["sal"], client._ids["periode"]
    client.post("/bulletins/saisie", data={
        "salarie_id": sid, "periode_id": pr_id,
        "salaire_base": "400000", "retenue_pret": "100000",
        "action": "brouillon",
    }, follow_redirects=True)
    with flask_app.app_context():
        b = BulletinPaie.query.filter_by(salarie_id=sid, periode_id=pr_id).first()
        assert b.statut == "BROUILLON"
        # Pas d'imputation tant que le bulletin n'est pas validé
        assert RemboursementPret.query.filter_by(bulletin_id=b.id).count() == 0
        assert float(PretPersonnel.query.get(p.id).montant_rembourse) == 0


def test_reimputation_propre_sur_resauvegarde(client):
    """Re-sauvegarder le même bulletin ne doit pas doubler le remboursement."""
    p = _creer_pret(client)
    pid = p.id
    sid, pr_id = client._ids["sal"], client._ids["periode"]
    payload = {"salarie_id": sid, "periode_id": pr_id,
               "salaire_base": "400000", "retenue_pret": "100000",
               "action": "valider"}
    client.post("/bulletins/saisie", data=payload, follow_redirects=True)
    # Annuler la validation pour pouvoir ressaisir, puis re-valider avec 120000
    with flask_app.app_context():
        b = BulletinPaie.query.filter_by(salarie_id=sid, periode_id=pr_id).first()
        bid = b.id
    client.post("/bulletins/valider-lot", data={
        "periode_id": pr_id, "action_lot": "annuler_validation",
        "bulletin_ids": str(bid)}, follow_redirects=True)
    client.post("/bulletins/saisie", data={**payload, "retenue_pret": "120000"},
                follow_redirects=True)
    with flask_app.app_context():
        p2 = PretPersonnel.query.get(pid)
        # Une seule imputation nette de 120000 (l'ancienne de 100000 a été annulée)
        rbs = RemboursementPret.query.filter_by(pret_id=pid).all()
        assert len(rbs) == 1
        assert float(rbs[0].montant) == 120000
        assert float(p2.montant_rembourse) == 120000


def test_acompte_recu_pdf(client):
    """Le reçu d'acompte individuel est un PDF téléchargeable."""
    sid = client._ids["sal"]
    r = client.post("/acomptes/nouveau", data={
        "salarie_id": sid, "montant": "75000", "date_acompte": "2026-06-10",
        "mois": "6", "annee": "2026", "motif": "Dépense urgente",
    }, follow_redirects=True)
    assert r.status_code == 200
    with flask_app.app_context():
        a = Acompte.query.filter_by(salarie_id=sid).first()
        assert a is not None
        aid = a.id
    r = client.get(f"/acomptes/{aid}/recu")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_acompte_recu_isolation_tenant(client):
    """Un acompte d'un autre tenant ne doit pas être accessible (404)."""
    r = client.get("/acomptes/999999/recu")
    assert r.status_code == 404


def test_alerte_quotite_depassee(client):
    """Retenues > 50 % du brut → message d'alerte (info) sans bloquer."""
    _creer_pret(client, montant=500000, nb=2)
    sid, pr_id = client._ids["sal"], client._ids["periode"]
    # Brut ≈ salaire_base 300000 ; retenue prêt 200000 > 50 % (150000)
    r = client.post("/bulletins/saisie", data={
        "salarie_id": sid, "periode_id": pr_id,
        "salaire_base": "300000", "retenue_pret": "200000",
        "action": "valider",
    }, follow_redirects=True)
    html = r.data.decode("utf-8", errors="ignore")
    assert "quotité" in html.lower() or "quotite" in html.lower()
