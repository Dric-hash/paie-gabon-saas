"""Paie des journaliers MENSUELS — montant, avances et audit.

L'arrondi au millier de franc supérieur est désormais OPTIONNEL et appliqué à la
génération (case à cocher). `FeuillePaieJournalier.montant_a_payer` renvoie le
montant enregistré tel quel (il ne re-force plus l'arrondi), afin que toute
modification manuelle soit respectée. Les feuilles ci-dessous simulent une
génération avec l'arrondi activé (montant déjà à 230 000).
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
                    FeuillePaieJournalier, AvanceJournalier, AuditLog)


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
                                   montant_brut=230000.0, statut="EN_ATTENTE")
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


def test_mensuel_montant_respecte(client):
    # Nouveau contrat : montant_a_payer renvoie le montant ENREGISTRÉ tel quel.
    # L'arrondi millier est décidé à la génération, plus re-forcé à la lecture,
    # donc toute valeur enregistrée (ronde, exacte ou éditée) est respectée.
    with flask_app.app_context():
        fm = db.session.get(FeuillePaieJournalier, client._ids["fm"])
        assert float(fm.montant_brut) == pytest.approx(230000)
        assert fm.montant_a_payer == 230000
        # Une valeur non ronde n'est PAS re-arrondie
        fm.montant_brut = 229999.20; db.session.commit()
        assert fm.montant_a_payer == pytest.approx(229999.20)


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


def test_avance_modifiable_avant_validation(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle", data={"montant": "50000"}, follow_redirects=True)
    with flask_app.app_context():
        aid = AvanceJournalier.query.filter_by(journalier_id=jid).first().id
    client.post(f"/journaliers/avances/{aid}/modifier",
                data={"montant": "70000", "date_avance": "2026-06-12", "motif": "corrigé"},
                follow_redirects=True)
    with flask_app.app_context():
        a = db.session.get(AvanceJournalier, aid)
        assert float(a.montant) == 70000 and a.motif == "corrigé"


def test_avance_validee_est_figee(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle", data={"montant": "50000"}, follow_redirects=True)
    with flask_app.app_context():
        aid = AvanceJournalier.query.filter_by(journalier_id=jid).first().id
    client.post(f"/journaliers/avances/{aid}/valider", follow_redirects=True)
    # Modification refusée
    client.post(f"/journaliers/avances/{aid}/modifier", data={"montant": "999"}, follow_redirects=True)
    # Suppression refusée
    client.post(f"/journaliers/avances/{aid}/supprimer", follow_redirects=True)
    with flask_app.app_context():
        a = db.session.get(AvanceJournalier, aid)
        assert a is not None                       # pas supprimée
        assert a.statut == "VALIDEE" and a.est_modifiable is False
        assert float(a.montant) == 50000            # pas modifiée


def test_avance_validee_toujours_deduite(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle", data={"montant": "50000"}, follow_redirects=True)
    with flask_app.app_context():
        aid = AvanceJournalier.query.filter_by(journalier_id=jid).first().id
    client.post(f"/journaliers/avances/{aid}/valider", follow_redirects=True)
    client.post(f"/journaliers/paie/{client._ids['fm']}/payer", follow_redirects=True)
    with flask_app.app_context():
        fm = db.session.get(FeuillePaieJournalier, client._ids["fm"])
        assert float(fm.avance_deduite) == 50000    # déduite même validée


# ── Journal d'audit ──────────────────────────────────────────────────────────
def test_action_journalisee_dans_audit(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle",
                data={"montant": "50000", "motif": "test"}, follow_redirects=True)
    with flask_app.app_context():
        log = (AuditLog.query.filter_by(entite="avance_journalier", action="CREATE")
               .order_by(AuditLog.id.desc()).first())
        assert log is not None
        assert log.user_id == client._ids["admin"]
        assert log.tenant_id == client._ids["tenant"]


def test_paiement_journalise(client):
    client.post(f"/journaliers/paie/{client._ids['fm']}/payer", follow_redirects=True)
    with flask_app.app_context():
        log = AuditLog.query.filter_by(entite="feuille_journalier", action="PAY").first()
        assert log is not None


def test_page_audit_admin_ok(client):
    r = client.get("/audit")
    assert r.status_code == 200
    assert "audit" in r.data.decode("utf-8", "ignore").lower()


def test_audit_recherche_filtre(client):
    jid = _journalier_mensuel_id(client)
    client.post(f"/journaliers/{jid}/avances/nouvelle",
                data={"montant": "50000"}, follow_redirects=True)
    ok = client.get("/audit?q=DIARRA").data.decode("utf-8", "ignore")
    assert "DIARRA" in ok                       # trouvé par la recherche
    ko = client.get("/audit?q=ZZQWERTYINTROUVABLE").data.decode("utf-8", "ignore")
    assert "DIARRA" not in ko                    # filtré


def test_parametres_modele_journalise(client):
    client.post("/parametres/modele-bulletin",
                data={"modele_bulletin": "moderne"}, follow_redirects=True)
    with flask_app.app_context():
        log = (AuditLog.query.filter_by(entite="parametres", action="UPDATE")
               .order_by(AuditLog.id.desc()).first())
        assert log is not None
