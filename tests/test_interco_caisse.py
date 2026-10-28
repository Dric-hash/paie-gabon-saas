"""Interconnexion PaieGabon → Caisse : acomptes (421), avances journaliers (401PREST),
avances prestataires (compte fournisseur 401XXX + libellé), annulations, référence."""
import io
import json
import os
import sys
import tempfile
from datetime import date
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_interco_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-interco")

import pytest

import interco_caisse
from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, Salarie, Journalier, AvanceJournalier, Prestataire,
                    AvancePrestataire, Acompte)


class _Reponse(io.BytesIO):
    def getcode(self):
        return 201

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def client(monkeypatch):
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SERVER_NAME="localhost",
                            CAISSE_INTERCO_ACTIF=True, CAISSE_URL="http://caisse.test", CAISSE_TOKEN="jeton")
    envois = []

    def urlopen(req, timeout=None):
        assert req.get_header("X-interco-token") == "jeton" and timeout == interco_caisse._TIMEOUT
        envois.append((req.full_url.replace("http://caisse.test", ""), json.loads(req.data)))
        return _Reponse(b"{}")
    monkeypatch.setattr(interco_caisse.urllib.request, "urlopen", urlopen)
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        pl = Plan(code="PRO", nom="Pro", prix_mensuel=1, max_salaries=99, max_utilisateurs=99, actif=True)
        db.session.add(pl)
        db.session.flush()
        t = Tenant(slug="batigab", denomination="BATIGAB", activite="BTP", ville="LBV", pays="Gabon",
                   plan_id=pl.id, statut="ACTIF", token_api="x", external_ref="BATIGAB")
        db.session.add(t)
        db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="a@a.ga", role="TENANT_ADMIN", tenant_id=t.id,
                            actif=True, email_verifie=True)
        admin.set_password("MotDePasse1")
        sal = Salarie(tenant_id=t.id, matricule="M001", nom="NDONG", prenom="Jean-Pierre",
                      date_embauche=date(2024, 1, 1), statut="ACTIF")
        j = Journalier(tenant_id=t.id, nom="MOUBAMBA", prenom="Serge", profession="Manœuvre", taux_horaire=1000,
                       type_paie="JOURNALIER", statut="ACTIF")
        total = Prestataire(tenant_id=t.id, code="PRE0001", categorie="FOURNISSEUR", raison_sociale="TOTAL GABON",
                            sigle="TOTAL")
        petro = Prestataire(tenant_id=t.id, code="PRE0002", categorie="FOURNISSEUR", raison_sociale="Pétro Gabon SA")
        db.session.add_all([admin, sal, j, total, petro])
        db.session.commit()
        ids = SimpleNamespace(tenant=t.id, admin=admin.id, sal=sal.id, j=j.id, total=total.id, petro=petro.id)
        cli = flask_app.test_client()
        with cli.session_transaction() as s:
            s["_user_id"] = str(admin.id)
            s["_fresh"] = True
        cli.ids, cli.envois = ids, envois
        yield cli
        db.session.remove()
    flask_app.config.update(CAISSE_URL="", CAISSE_TOKEN="")


def test_compte_fournisseur():
    P = lambda **k: SimpleNamespace(**{"code": "", "sigle": "", "raison_sociale": "", **k})  # noqa: E731
    assert interco_caisse.compte_fournisseur(P(sigle="Total", code="PRE0001", raison_sociale="TOTAL GABON")) == "401TOTAL"
    assert interco_caisse.compte_fournisseur(P(code="PRE0002", raison_sociale="Pétro Gabon SA")) == "401PETROGABO"
    assert interco_caisse.compte_fournisseur(P(code="cimgabon", raison_sociale="Ciments du Gabon")) == "401CIMGABON"
    assert interco_caisse.compte_fournisseur(P()) == "401PREST"


def test_acompte_propose_puis_annule_et_supprime(client):
    r = client.post("/acomptes/nouveau", data={"salarie_id": client.ids.sal, "montant": "75000",
                                               "date_acompte": "2026-10-28", "mois": "10", "annee": "2026",
                                               "motif": "Urgence"})
    assert r.status_code == 302
    with flask_app.app_context():
        a1 = Acompte.query.one().id
    chemin, p = client.envois[0]
    assert chemin == "/api/interco/ecriture"
    assert p == {"tenant_ref": "BATIGAB", "source_app": "paiegabon", "source_ref": f"acompte-{a1}", "sens": "SORTIE",
                 "montant": 75000.0, "motif": "Acompte sur salaire — NDONG Jean-Pierre", "compte_suggere": "421",
                 "devise": "XAF", "date_operation": "2026-10-28"}
    client.post(f"/acomptes/{a1}/annuler")
    assert client.envois[1] == ("/api/interco/ecriture/annuler",
                                {"tenant_ref": "BATIGAB", "source_app": "paiegabon", "source_ref": f"acompte-{a1}"})
    client.post(f"/acomptes/{a1}/supprimer")
    assert client.envois[2][0] == "/api/interco/ecriture/annuler" and len(client.envois) == 3


def test_avance_journalier_proposee_a_la_validation(client):
    client.post(f"/journaliers/{client.ids.j}/avances/nouvelle",
                data={"montant": "25000", "date_avance": "2026-10-27", "mode_paiement": "MOBILE_MONEY"})
    assert not client.envois                                  # pas avant la validation
    with flask_app.app_context():
        aid = AvanceJournalier.query.one().id
    client.post(f"/journaliers/avances/{aid}/valider")
    client.post(f"/journaliers/avances/{aid}/valider")        # double clic : un seul envoi
    assert len(client.envois) == 1
    p = client.envois[0][1]
    assert (p["source_ref"], p["compte_suggere"], p["mode_paiement"], p["montant"]) == \
        (f"avance-journalier-{aid}", "401PREST", "MOBILE_MONEY", 25000.0)
    assert p["motif"] == "Avance sur paie journalier — MOUBAMBA Serge" and p["date_operation"] == "2026-10-27"


def test_avance_prestataire_compte_fournisseur(client):
    for pid, mt in ((client.ids.total, "450000"), (client.ids.petro, "120000")):
        client.post(f"/prestataires/{pid}/avances/nouvelle", data={"montant": mt, "date_avance": "2026-10-26",
                                                                   "mode_paiement": "VIREMENT", "motif": "Carburant"})
    with flask_app.app_context():
        ids = [a.id for a in AvancePrestataire.query.order_by(AvancePrestataire.id)]
    assert not client.envois
    client.post(f"/prestataires/avances/{ids[0]}/valider", data={"valide_par_nom": ""})   # nom du chef manquant
    assert not client.envois
    for aid in ids:
        client.post(f"/prestataires/avances/{aid}/valider", data={"valide_par_nom": "Chef OBAME"})
    p1, p2 = client.envois[0][1], client.envois[1][1]
    assert (p1["compte_suggere"], p1["libelle_compte"], p1["mode_paiement"], p1["montant"]) == \
        ("401TOTAL", "TOTAL GABON", "VIREMENT", 450000.0)
    assert p1["source_ref"] == f"avance-prestataire-{ids[0]}" and p1["motif"] == "Avance prestataire — TOTAL GABON (Carburant)"
    assert (p2["compte_suggere"], p2["libelle_compte"]) == ("401PETROGABO", "Pétro Gabon SA")
    client.post(f"/prestataires/avances/{ids[0]}/valider", data={"valide_par_nom": "Chef OBAME"})   # déjà validée
    assert len(client.envois) == 2


def test_sans_reference_ni_configuration_rien_ne_part(client):
    with flask_app.app_context():
        db.session.get(Tenant, client.ids.tenant).external_ref = None
        db.session.commit()
    client.post("/acomptes/nouveau", data={"salarie_id": client.ids.sal, "montant": "1000", "date_acompte": "2026-10-28",
                                           "mois": "10", "annee": "2026"})
    assert not client.envois
    with flask_app.app_context():
        assert Acompte.query.count() == 1


def test_caisse_injoignable_n_empeche_rien(client, monkeypatch):
    def panne(req, timeout=None):
        raise OSError("Caisse injoignable")
    monkeypatch.setattr(interco_caisse.urllib.request, "urlopen", panne)
    r = client.post("/acomptes/nouveau", data={"salarie_id": client.ids.sal, "montant": "1000",
                                               "date_acompte": "2026-10-28", "mois": "10", "annee": "2026"})
    assert r.status_code == 302
    with flask_app.app_context():
        assert Acompte.query.count() == 1
        assert interco_caisse.annuler_ecriture(db.session.get(Tenant, client.ids.tenant), "x") is False


def test_reference_en_console_super_admin(client):
    with flask_app.app_context():
        sa = Utilisateur(nom="SA", prenom="S", email="sa@ameriack.com", role="SUPER_ADMIN", actif=True,
                         email_verifie=True)
        sa.set_password("MotDePasse1")
        autre = Tenant(slug="autre", denomination="AUTRE", activite="BTP", ville="LBV", pays="Gabon",
                       statut="ACTIF", token_api="y")
        db.session.add_all([sa, autre])
        db.session.commit()
        sid, aid = sa.id, autre.id
    with client.session_transaction() as s:
        s["_user_id"] = str(sid)
    r = client.post(f"/admin/tenants/{aid}/reference", data={"external_ref": "BATIGAB"}, follow_redirects=True)
    assert "déjà utilisée" in r.get_data(as_text=True)
    client.post(f"/admin/tenants/{aid}/reference", data={"external_ref": "a b"})
    client.post(f"/admin/tenants/{client.ids.tenant}/reference", data={"external_ref": "BATIGAB-2"})
    with flask_app.app_context():
        assert db.session.get(Tenant, aid).external_ref is None
        assert db.session.get(Tenant, client.ids.tenant).external_ref == "BATIGAB-2"
    client.post(f"/admin/tenants/{client.ids.tenant}/reference", data={"external_ref": ""})
    with flask_app.app_context():
        assert db.session.get(Tenant, client.ids.tenant).external_ref is None
