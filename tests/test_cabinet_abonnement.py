"""Mode cabinet : un renouvellement du cabinet couvre toutes les entreprises qu'il gère
(statut, forfait, échéance), et le paiement fait depuis une entreprise est celui du cabinet."""
import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_cabinet_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = os.environ.get("PG_URL") or "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-cabinet")

import pytest  # noqa: E402

from app import app as flask_app  # noqa: E402
from models import db, utcnow, Plan, Tenant, Utilisateur, Paiement  # noqa: E402


def _connecter(cli, uid):
    with cli.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True


@pytest.fixture
def env():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SERVER_NAME="localhost")
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        essai = Plan(code="ESSAI", nom="Essai", prix_mensuel=0, max_salaries=10, max_utilisateurs=2, actif=True)
        cab_plan = Plan(code="CABINET", nom="Cabinet", prix_mensuel=100000, max_salaries=999, max_utilisateurs=10,
                        actif=True)
        db.session.add_all([essai, cab_plan])
        db.session.flush()
        fin_essai = utcnow() - timedelta(days=2)               # essai terminé
        cab = Tenant(slug="cab", denomination="CABINET OYANE", activite="Comptabilité", ville="LBV", pays="Gabon",
                     plan_id=essai.id, statut="ESSAI", date_expiration=fin_essai, est_cabinet=True,
                     type_compte="CABINET", token_api="c")
        db.session.add(cab)
        db.session.flush()
        ents = [Tenant(slug=f"e{i}", denomination=f"ENTREPRISE {i}", activite="BTP", ville="LBV", pays="Gabon",
                       plan_id=essai.id, statut="ESSAI", date_expiration=fin_essai, cabinet_id=cab.id,
                       token_api=f"e{i}") for i in range(3)]
        autre = Tenant(slug="autre", denomination="AUTRE", activite="BTP", ville="LBV", pays="Gabon",
                       plan_id=essai.id, statut="ESSAI", date_expiration=fin_essai, token_api="a")
        db.session.add_all(ents + [autre])
        db.session.flush()
        ents[2].statut = "SUSPENDU"                            # suspendue à part par le super-admin
        admin = Utilisateur(nom="OYANE", prenom="A", email="a@cab.ga", role="TENANT_ADMIN", tenant_id=cab.id,
                            actif=True, email_verifie=True)
        sa = Utilisateur(nom="SA", prenom="S", email="sa@paiegabon.com", role="SUPER_ADMIN", actif=True,
                         email_verifie=True)
        for u in (admin, sa):
            u.set_password("MotDePasse1")
        db.session.add_all([admin, sa])
        db.session.commit()
        ids = dict(cab=cab.id, ents=[e.id for e in ents], autre=autre.id, admin=admin.id, sa=sa.id,
                   essai=essai.id, plan_cab=cab_plan.id)
        yield ids
        db.session.remove()


def _t(i):
    return db.session.get(Tenant, i)


def test_paiement_valide_renouvelle_les_entreprises(env):
    cli = flask_app.test_client()
    with flask_app.app_context():
        # avant : entreprise bloquée (échéance du cabinet passée)
        assert _t(env["ents"][0]).est_expire
        p = Paiement(tenant_id=env["cab"], plan_id=env["plan_cab"], montant=600000, duree_mois=6, moyen="VIREMENT",
                     reference_interne="T-1", reference_externe="VIR-1", statut="EN_ATTENTE")
        db.session.add(p)
        db.session.commit()
        pid = p.id
    _connecter(cli, env["sa"])
    cli.post(f"/admin/paiements/{pid}/valider")
    with cli.session_transaction() as s:
        assert any("3 entreprise(s) gérée(s) par le cabinet alignée(s) sur ce renouvellement." in m
                   for _, m in s.get("_flashes", []))
    with flask_app.app_context():
        cab = _t(env["cab"])
        e0, e1, e2 = (_t(i) for i in env["ents"])
        assert cab.statut == "ACTIF" and cab.date_expiration > utcnow() + timedelta(days=170)
        for e in (e0, e1):
            assert (e.statut, e.plan_id, e.date_expiration) == ("ACTIF", env["plan_cab"], cab.date_expiration)
            assert not e.est_expire
        # la suspension propre à une entreprise est conservée, mais l'échéance suit
        assert e2.statut == "SUSPENDU" and e2.date_expiration == cab.date_expiration
        # une entreprise non rattachée n'est pas concernée
        assert _t(env["autre"]).statut == "ESSAI"


def test_paiement_saisi_et_statut_par_le_super_admin(env):
    cli = flask_app.test_client()
    _connecter(cli, env["sa"])
    cli.post(f"/admin/tenants/{env['cab']}/enregistrer-paiement",
             data={"duree": "12", "plan_id": env["plan_cab"], "montant": "1020000", "moyen": "ESPECES"})
    with flask_app.app_context():
        cab = _t(env["cab"])
        assert all(_t(i).date_expiration == cab.date_expiration for i in env["ents"])
        assert _t(env["ents"][0]).statut == "ACTIF"
    cli.post(f"/admin/tenants/{env['cab']}/statut", data={"statut": "ACTIF", "plan_id": env["essai"],
                                                          "date_expiration": "2027-03-31"})
    with flask_app.app_context():
        e0 = _t(env["ents"][0])
        assert e0.plan_id == env["essai"] and e0.date_expiration.strftime("%Y-%m-%d") == "2027-03-31"
    # suspension du cabinet : non recopiée (elle bloque déjà l'accès par le cabinet)
    cli.post(f"/admin/tenants/{env['cab']}/statut", data={"statut": "SUSPENDU"})
    with flask_app.app_context():
        assert _t(env["cab"]).statut == "SUSPENDU" and _t(env["ents"][0]).statut == "ACTIF"


def test_paiement_declare_depuis_une_entreprise_va_au_cabinet(env):
    cli = flask_app.test_client()
    _connecter(cli, env["admin"])
    e0 = env["ents"][0]
    with cli.session_transaction() as s:
        s["cabinet_entreprise_id"] = e0                         # le cabinet travaille dans l'entreprise
    page = cli.get("/paiement").get_data(as_text=True)
    assert "CABINET OYANE" in page
    cli.post("/paiement/confirmer", data={"moyen": "VIREMENT", "reference": "VIR-77", "duree": "3",
                                          "plan_id": env["plan_cab"]})
    with flask_app.app_context():
        p = Paiement.query.one()
        assert p.tenant_id == env["cab"]
        assert _t(env["cab"]).statut == "PAIEMENT_EN_ATTENTE" and _t(e0).statut == "PAIEMENT_EN_ATTENTE"


def test_alignement_au_demarrage(env):
    import app as module_app
    with flask_app.app_context():
        cab = _t(env["cab"])
        cab.statut, cab.plan_id = "ACTIF", env["plan_cab"]
        cab.date_expiration = utcnow() + timedelta(days=90)
        db.session.commit()
        assert _t(env["ents"][0]).statut == "ESSAI"             # décalée, comme en production
        assert module_app.aligner_abonnements_cabinets() == 3   # dont la suspendue (échéance alignée)
        e0 = _t(env["ents"][0])
        assert (e0.statut, e0.plan_id) == ("ACTIF", env["plan_cab"])
        assert e0.date_expiration == _t(env["cab"]).date_expiration
        assert _t(env["cab"]).synchroniser_abonnement_entreprises() == 0     # idempotent


def test_rattachement_prend_l_abonnement_du_cabinet(env):
    cli = flask_app.test_client()
    _connecter(cli, env["sa"])
    cli.post(f"/admin/tenants/{env['cab']}/statut", data={"statut": "ACTIF", "plan_id": env["plan_cab"],
                                                          "date_expiration": "2027-06-30"})
    cli.post(f"/admin/tenants/{env['autre']}/cabinet", data={"action": "rattacher", "cabinet_id": env["cab"]})
    a = _t(env["autre"])
    assert a.cabinet_id == env["cab"] and a.statut == "ACTIF" and a.plan_id == env["plan_cab"]
    assert a.date_expiration.strftime("%Y-%m-%d") == "2027-06-30"
