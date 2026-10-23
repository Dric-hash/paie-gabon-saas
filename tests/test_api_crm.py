# -*- coding: utf-8 -*-
"""tests/test_api_crm.py — API commerciale en lecture seule pour Ameriack CRM."""
import os
import sys
import tempfile
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_apicrm_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-apicrm")

import pytest

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, CategorieEmploi, Salarie, PeriodePaie, BulletinPaie, Paiement,
                    EditionDAS)

CLE = "cle-crm-de-test-0123456789"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CRM_API_KEY", CLE)
    monkeypatch.delenv("CRM_API_LOCAL_SEULEMENT", raising=False)
    flask_app.config.update({"TESTING": True, "WTF_CSRF_ENABLED": False, "SERVER_NAME": "localhost"})
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        pl = Plan(code="PRO", nom="Pro", prix_mensuel=35000, max_salaries=50, max_utilisateurs=10, actif=True)
        db.session.add(pl); db.session.flush()
        t = Tenant(slug="acme", denomination="ACME SARL", ville="Franceville", telephone="+241 01 02 03 04",
                   plan_id=pl.id, statut="ACTIF", token_api="ta", date_expiration=datetime(2026, 12, 31))
        e = Tenant(slug="essai", denomination="ESSAI SA", plan_id=pl.id, statut="ESSAI", token_api="tb")
        db.session.add_all([t, e]); db.session.flush()
        u = Utilisateur(nom="NDONG", prenom="Jean", email="jean@acme.ga", role="TENANT_ADMIN", tenant_id=t.id,
                        actif=True, email_verifie=True, derniere_connexion=datetime(2026, 10, 20, 9, 0))
        u.set_password("x"); db.session.add(u)
        cat = CategorieEmploi(tenant_id=t.id, code="C1", libelle="Cadre"); db.session.add(cat); db.session.flush()
        s = Salarie(tenant_id=t.id, matricule="M1", nom="X", prenom="Y", categorie_id=cat.id,
                    date_embauche=date(2020, 1, 1))
        db.session.add(s); db.session.flush()
        p = PeriodePaie(tenant_id=t.id, annee=2026, mois=9, libelle_mois="SEPTEMBRE"); db.session.add(p); db.session.flush()
        db.session.add(BulletinPaie(tenant_id=t.id, salarie_id=s.id, periode_id=p.id, salaire_base=1, salaire_brut=1,
                                    net_a_payer=1, statut="PAYÉ"))
        db.session.add_all([
            Paiement(tenant_id=t.id, moyen="AIRTEL_MONEY", montant=35000, reference_interne="R1", statut="SUCCES",
                     date_creation=datetime(2026, 9, 1)),
            Paiement(tenant_id=t.id, moyen="MANUEL", montant=35000, reference_interne="R2", statut="EN_ATTENTE"),
            EditionDAS(tenant_id=t.id, annee=2025, statut="PAYEE"),
        ])
        db.session.commit()
    yield
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


def _get(headers=None, addr="127.0.0.1"):
    return flask_app.test_client().get("/api/crm/v1/comptes", headers=headers or {},
                                       environ_base={"REMOTE_ADDR": addr})


def test_donnees_commerciales(env):
    r = _get({"X-CRM-Key": CLE})
    assert r.status_code == 200
    d = r.get_json()
    assert d["application"] == "PAIEGABON" and d["version"] == 1
    acme = next(c for c in d["comptes"] if c["nom"] == "ACME SARL")
    assert acme["statut"] == "ACTIF" and acme["offre"] == {"code": "PRO", "nom": "Pro", "prix_mensuel": 35000.0}
    assert acme["email_contact"] == "jean@acme.ga" and acme["nom_contact"] == "Jean NDONG"
    assert acme["derniere_activite"].startswith("2026-10-20")
    assert acme["indicateurs"] == {"utilisateurs": 1, "salaries_actifs": 1, "bulletins": 1, "derniere_paie": "2026-09"}
    assert acme["paiements"]["total_encaisse"] == 35000.0 and acme["paiements"]["en_attente"] == 1
    assert acme["options"]["das_payees"] == [2025]
    essai = next(c for c in d["comptes"] if c["nom"] == "ESSAI SA")
    assert essai["statut"] == "ESSAI" and essai["paiements"]["nombre"] == 0
    # aucune donnée de paie nominative
    assert "salaire" not in r.get_data(as_text=True).lower().replace("salaries_actifs", "")


def test_securite(env, monkeypatch):
    assert _get().status_code == 401                                         # sans clé
    assert _get({"X-CRM-Key": "mauvaise"}).status_code == 401
    assert _get({"X-CRM-Key": CLE}, addr="41.158.1.2").status_code == 403    # depuis internet
    assert _get({"X-CRM-Key": CLE, "X-Forwarded-For": "41.158.1.2"}).status_code == 403   # via nginx
    monkeypatch.setenv("CRM_API_LOCAL_SEULEMENT", "0")
    assert _get({"X-CRM-Key": CLE}, addr="41.158.1.2").status_code == 200
    monkeypatch.setenv("CRM_API_KEY", "")
    assert _get({"X-CRM-Key": ""}).status_code == 404                         # API désactivée
    # lecture seule : aucune autre méthode
    assert flask_app.test_client().post("/api/crm/v1/comptes", headers={"X-CRM-Key": CLE}).status_code in (400, 404, 405)
