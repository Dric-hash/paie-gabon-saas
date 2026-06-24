"""Arrondi au millier de franc supérieur — paie des journaliers MENSUELS.

Vérifie que `FeuillePaieJournalier.montant_a_payer` arrondit au millier supérieur
pour les journaliers de type MENSUEL (et seulement ceux-là), y compris pour des
feuilles déjà enregistrées avant la mise en place de l'arrondi, et que les
impressions affichent bien le montant arrondi.
"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_arrondi_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-arrondi")

import pytest
from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, Journalier,
                    FeuillePaieJournalier, AvanceJournalier)


@pytest.fixture
def client():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SERVER_NAME="localhost")
    with flask_app.app_context():
        db.drop_all(); db.create_all()
        pl = Plan(code="PRO", nom="Pro", prix_mensuel=1, max_salaries=99,
                  max_utilisateurs=99, actif=True); db.session.add(pl); db.session.flush()
        t = Tenant(slug="a", denomination="SGTG", activite="BTP", ville="LBV",
                   pays="Gabon", plan_id=pl.id, statut="ACTIF", token_api="x")
        db.session.add(t); db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="a@a.ga", role="TENANT_ADMIN",
                            tenant_id=t.id, actif=True, email_verifie=True)
        admin.set_password("MotDePasse1"); db.session.add(admin)
        # Journalier MENSUEL : 240 h × 958.33 = 229 999,2
        jm = Journalier(tenant_id=t.id, nom="DIARRA", prenom="IBRAHIM",
                        profession="Gardien jour", taux_horaire=958.33,
                        type_paie="MENSUEL", statut="ACTIF",
                        date_embauche=date(2025, 2, 8))
        # Journalier JOURNALIER (paie à la journée — non arrondie)
        jj = Journalier(tenant_id=t.id, nom="KOMBILA", prenom="PAUL",
                        profession="Manœuvre", taux_horaire=1000,
                        type_paie="JOURNALIER", statut="ACTIF")
        db.session.add_all([jm, jj]); db.session.flush()
        fm = FeuillePaieJournalier(tenant_id=t.id, journalier_id=jm.id,
                                   date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 30),
                                   nb_jours=30, total_heures=240, taux_horaire=958.33,
                                   montant_brut=229999.20, statut="EN_ATTENTE")
        fj = FeuillePaieJournalier(tenant_id=t.id, journalier_id=jj.id,
                                   date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 15),
                                   nb_jours=5, total_heures=40, taux_horaire=1000,
                                   montant_brut=40000, statut="EN_ATTENTE")
        db.session.add_all([fm, fj]); db.session.commit()
        ids = {"tenant": t.id, "admin": admin.id, "fm": fm.id, "fj": fj.id}
        cli = flask_app.test_client()
        with cli.session_transaction() as s:
            s["_user_id"] = str(admin.id); s["_fresh"] = True
        cli._ids = ids
        yield cli
        db.session.remove()


def test_mensuel_arrondi_au_millier_superieur(client):
    with flask_app.app_context():
        fm = db.session.get(FeuillePaieJournalier, client._ids["fm"])
        assert float(fm.montant_brut) == pytest.approx(229999.20)   # brut inchangé
        assert fm.montant_a_payer == 230000                         # arrondi à l'affichage


def test_journalier_non_arrondi(client):
    with flask_app.app_context():
        fj = db.session.get(FeuillePaieJournalier, client._ids["fj"])
        assert fj.montant_a_payer == 40000                          # pas d'arrondi


def test_impression_sites_affiche_montant_arrondi(client):
    html = client.get("/journaliers/paie/imprimer-sites"
                      "?date_debut=2026-06-01&date_fin=2026-06-30").data.decode("utf-8", "ignore")
    assert "230000" in html and "229999" not in html


def test_impression_normale_affiche_montant_arrondi(client):
    html = client.get("/journaliers/paie/imprimer"
                      "?date_debut=2026-06-01&date_fin=2026-06-30").data.decode("utf-8", "ignore")
    assert "230000" in html and "229999" not in html


def test_paiement_fige_le_montant_arrondi(client):
    client.post(f"/journaliers/paie/{client._ids['fm']}/payer", follow_redirects=True)
    with flask_app.app_context():
        fm = db.session.get(FeuillePaieJournalier, client._ids["fm"])
        assert fm.statut == "PAYÉ"
        assert float(fm.montant_brut) == 230000     # arrondi figé au paiement


# ── Avances des journaliers : déduction de la paie de période ─────────────────
def _journalier_mensuel_id(client):
    with flask_app.app_context():
        return db.session.get(FeuillePaieJournalier, client._ids["fm"]).journalier_id


def test_avance_deduite_de_la_paie_a_l_impression(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle",
                data={"montant": "50000", "date_avance": "2026-06-10", "motif": "avance"},
                follow_redirects=True)
    # Brut mensuel 230 000 − avance 50 000 = net 180 000
    html = client.get("/journaliers/paie/imprimer"
                      "?date_debut=2026-06-01&date_fin=2026-06-30").data.decode("utf-8", "ignore")
    assert "180000" in html


def test_paiement_regularise_l_avance(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle",
                data={"montant": "50000", "date_avance": "2026-06-10"}, follow_redirects=True)
    client.post(f"/journaliers/paie/{client._ids['fm']}/payer", follow_redirects=True)
    with flask_app.app_context():
        fm = db.session.get(FeuillePaieJournalier, client._ids["fm"])
        assert fm.statut == "PAYÉ"
        assert float(fm.montant_brut) == 230000          # arrondi figé
        assert float(fm.avance_deduite) == 50000          # avance déduite figée
        av = AvanceJournalier.query.filter_by(journalier_id=jid).first()
        assert float(av.montant_regularise) == 50000      # avance régularisée
        assert av.reste_a_regulariser == 0


def test_avance_superieure_au_net_plafonnee(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle",
                data={"montant": "500000", "date_avance": "2026-06-10"}, follow_redirects=True)
    client.post(f"/journaliers/paie/{client._ids['fm']}/payer", follow_redirects=True)
    with flask_app.app_context():
        fm = db.session.get(FeuillePaieJournalier, client._ids["fm"])
        assert float(fm.avance_deduite) == 230000         # plafonné au brut
        av = AvanceJournalier.query.filter_by(journalier_id=jid).first()
        assert float(av.montant_regularise) == 230000     # seulement 230 000 régularisés
        assert av.reste_a_regulariser == 270000           # 500 000 − 230 000 restent dus


def test_avance_regularisee_non_supprimable(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle",
                data={"montant": "50000"}, follow_redirects=True)
    client.post(f"/journaliers/paie/{client._ids['fm']}/payer", follow_redirects=True)
    with flask_app.app_context():
        aid = AvanceJournalier.query.filter_by(journalier_id=jid).first().id
    client.post(f"/journaliers/avances/{aid}/supprimer", follow_redirects=True)
    with flask_app.app_context():
        assert db.session.get(AvanceJournalier, aid) is not None   # toujours là (déduite)


def test_avance_non_deduite_supprimable(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle",
                data={"montant": "50000"}, follow_redirects=True)
    with flask_app.app_context():
        aid = AvanceJournalier.query.filter_by(journalier_id=jid).first().id
    client.post(f"/journaliers/avances/{aid}/supprimer", follow_redirects=True)
    with flask_app.app_context():
        assert db.session.get(AvanceJournalier, aid) is None       # supprimée
