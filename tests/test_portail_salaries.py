# -*- coding: utf-8 -*-
"""tests/test_portail_salaries.py — Portail des employés : activation par
l'administrateur, invitation par code d'activation, PIN choisi par le salarié,
cloisonnement, verrouillage, brouillons invisibles, départ, notification."""
import os
import re
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_portail_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-portail")

import pytest

from app import app as flask_app
from models import (db, utcnow, Plan, Tenant, Utilisateur, CategorieEmploi, Salarie, PeriodePaie, BulletinPaie,
                    AccesSalarie, ConsultationBulletin, AuditLog, Conge, DemandeDocument, DemandeProfil)
import portail_salaries as ps


def _client_pour(uid):
    cli = flask_app.test_client()
    with cli.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    return cli


def _bul(t, s, p, brut, statut):
    return BulletinPaie(tenant_id=t.id, salarie_id=s.id, periode_id=p.id, salaire_base=brut, salaire_brut=brut,
                        net_a_payer=brut * 0.8, statut=statut, cnss_salarie=brut * 0.025, cnamgs_salarie=brut * 0.02,
                        irpp=brut * 0.05, tcs=brut * 0.01)


@pytest.fixture
def env():
    flask_app.config.update({"TESTING": True, "WTF_CSRF_ENABLED": False, "SERVER_NAME": "localhost",
                             "MAIL_CAPTURE": []})
    from app import limiter
    limiter.enabled = False
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        plan = Plan(code="ESSENTIEL", nom="Essentiel", prix_mensuel=1, max_salaries=50, max_utilisateurs=10, actif=True)
        db.session.add(plan); db.session.flush()
        t = Tenant(slug="acme", denomination="ACME SARL", pays="Gabon", plan_id=plan.id, statut="ACTIF", token_api="ta")
        b = Tenant(slug="beta", denomination="BETA SA", pays="Gabon", plan_id=plan.id, statut="ACTIF", token_api="tb",
                   portail_salaries_actif=True)
        db.session.add_all([t, b]); db.session.flush()
        ids = {}
        for cle, role, tid in (("admin", "TENANT_ADMIN", t.id), ("rh", "RH", t.id), ("dir", "DIRECTEUR", t.id),
                               ("admin_b", "TENANT_ADMIN", b.id)):
            u = Utilisateur(nom=cle.upper(), prenom="X", email=f"{cle}@a.ga", role=role, tenant_id=tid, actif=True,
                            email_verifie=True)
            u.set_password("x"); db.session.add(u); db.session.flush(); ids[cle] = u.id
        cat = CategorieEmploi(tenant_id=t.id, code="C1", libelle="Cadre")
        catb = CategorieEmploi(tenant_id=b.id, code="C1", libelle="Cadre")
        db.session.add_all([cat, catb]); db.session.flush()
        s1 = Salarie(tenant_id=t.id, matricule="M1", nom="NDONG", prenom="Jean", categorie_id=cat.id,
                     date_embauche=date(2020, 1, 1), date_naissance=date(1990, 5, 12), email="jean@ndong.ga")
        s2 = Salarie(tenant_id=t.id, matricule="M2", nom="MBA", prenom="Awa", categorie_id=cat.id,
                     date_embauche=date(2020, 1, 1))
        s3 = Salarie(tenant_id=t.id, matricule="M3", nom="OBAME", prenom="Paul", categorie_id=cat.id,
                     date_embauche=date(2020, 1, 1), statut="SORTI")
        sb = Salarie(tenant_id=b.id, matricule="M1", nom="AUTRE", prenom="Zoé", categorie_id=catb.id,
                     date_embauche=date(2020, 1, 1))
        db.session.add_all([s1, s2, s3, sb]); db.session.flush()
        p1 = PeriodePaie(tenant_id=t.id, annee=2026, mois=1, libelle_mois="JANVIER")
        p2 = PeriodePaie(tenant_id=t.id, annee=2026, mois=2, libelle_mois="FÉVRIER")
        p3 = PeriodePaie(tenant_id=t.id, annee=2026, mois=3, libelle_mois="MARS")
        pb = PeriodePaie(tenant_id=b.id, annee=2026, mois=1, libelle_mois="JANVIER")
        db.session.add_all([p1, p2, p3, pb]); db.session.flush()
        b11, b12, b13 = _bul(t, s1, p1, 1_000_000, "VALIDÉ"), _bul(t, s1, p2, 1_000_000, "PAYÉ"), \
            _bul(t, s1, p3, 1_000_000, "BROUILLON")
        b21 = _bul(t, s2, p1, 500_000, "VALIDÉ")
        bb = _bul(b, sb, pb, 700_000, "VALIDÉ")
        db.session.add_all([b11, b12, b13, b21, bb]); db.session.commit()
        ids.update(t=t.id, b=b.id, s1=s1.id, s2=s2.id, s3=s3.id, sb=sb.id, p1=p1.id,
                   b11=b11.id, b12=b12.id, b13=b13.id, b21=b21.id, bb=bb.id)
    yield ids
    flask_app.config.pop("MAIL_CAPTURE", None)
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


def _activer_portail(env):
    r = _client_pour(env["admin"]).post("/portail-employes/activation", data={"actif": "1"})
    assert r.status_code == 302


def _inviter(env, *cles, email=False):
    cli = _client_pour(env["admin"])
    data = {"salarie_ids": [str(env[c]) for c in cles]}
    if email:
        data["envoyer_email"] = "1"
    r = cli.post("/portail-employes/inviter", data=data)
    assert r.status_code == 200
    return re.findall(r'class="code">([A-Z0-9]{4}-[A-Z0-9]{4})<', r.get_data(as_text=True))


def _activer_compte(code, matricule="M1", pin="482915", entreprise="acme"):
    cli = flask_app.test_client()
    r = cli.post("/espace/activer", data={"code_entreprise": entreprise, "matricule": matricule,
                                          "code_activation": code, "pin": pin, "pin2": pin, "consentement": "1"})
    return cli, r


def _connexion(pin="482915", matricule="M1", entreprise="acme", cli=None):
    cli = cli or flask_app.test_client()
    r = cli.post("/espace/connexion", data={"code_entreprise": entreprise, "matricule": matricule, "pin": pin})
    return cli, r


def _messages(cli):
    with cli.session_transaction() as sess:
        m = " ".join(x for _, x in sess.get("_flashes", []))
        sess["_flashes"] = []
    return m


def test_portail_desactive_par_defaut_et_reserve_a_l_admin(env):
    with flask_app.app_context():
        assert db.session.get(Tenant, env["t"]).portail_salaries_actif is False
    # le RH ne peut pas activer le portail ; le directeur (lecture seule) non plus
    assert _client_pour(env["rh"]).post("/portail-employes/activation", data={"actif": "1"}).status_code == 403
    _client_pour(env["dir"]).post("/portail-employes/activation", data={"actif": "1"})
    with flask_app.app_context():
        assert db.session.get(Tenant, env["t"]).portail_salaries_actif is False
    # invitation refusée tant que le portail est fermé
    r = _client_pour(env["admin"]).post("/portail-employes/inviter", data={"salarie_ids": [str(env["s1"])]})
    assert r.status_code == 302
    with flask_app.app_context():
        assert AccesSalarie.query.count() == 0
    cli, _ = _connexion()
    assert "n'est pas activé" in _messages(cli)
    assert _client_pour(env["admin"]).get("/portail-employes").status_code == 200


def test_invitation_activation_et_bulletins(env):
    _activer_portail(env)
    codes = _inviter(env, "s1", email=True)
    assert len(codes) == 1
    code = codes[0]
    # code envoyé aussi par e-mail ; jamais de PIN connu du RH
    mails = flask_app.config["MAIL_CAPTURE"]
    assert len(mails) == 1 and code in mails[0].html and mails[0].recipients == ["jean@ndong.ga"]
    with flask_app.app_context():
        a = AccesSalarie.query.one()
        assert a.pin_hash is None and a.code_hash and code not in a.code_hash
    # refus : mauvais code, PIN faible, date de naissance, PIN différents, sans consentement
    cli, _ = _activer_compte("AAAA-AAAA")
    assert "incorrect ou expiré" in _messages(cli)
    for pin, attendu in (("123456", "suite"), ("777777", "répété"), ("120590", "date de naissance")):
        cli, _ = _activer_compte(code, pin=pin)
        assert attendu in _messages(cli)
    cli = flask_app.test_client()
    cli.post("/espace/activer", data={"code_entreprise": "acme", "matricule": "M1", "code_activation": code,
                                      "pin": "482915", "pin2": "482915"})
    assert "accepter" in _messages(cli)
    # activation réussie (code saisi en minuscules sans tiret)
    cli, r = _activer_compte(code.replace("-", "").lower())
    assert r.status_code == 302 and r.headers["Location"].endswith("/espace/bulletins")
    html = cli.get("/espace/bulletins").get_data(as_text=True)
    assert "Janvier 2026" in html and "Février 2026" in html and "Mars 2026" not in html   # brouillon invisible
    assert "2 000 000 FCFA" in html          # brut cumulé des 2 bulletins visibles
    with flask_app.app_context():
        a = AccesSalarie.query.one()
        assert a.pin_hash and a.code_hash is None and a.consentement_le and a.active_le
    # le code d'activation ne sert qu'une fois
    cli2, _ = _activer_compte(code, pin="593017")
    assert "incorrect ou expiré" in _messages(cli2)


def test_cloisonnement_et_pdf(env):
    _activer_portail(env)
    code = _inviter(env, "s1")[0]
    cli, _ = _activer_compte(code)
    r = cli.get(f"/espace/bulletins/{env['b11']}.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.headers["Cache-Control"] == "no-store"
    assert "attachment" in cli.get(f"/espace/bulletins/{env['b12']}.pdf?dl=1").headers["Content-Disposition"]
    # bulletin d'un collègue, brouillon, bulletin d'une autre entreprise : introuvables
    for bid in (env["b21"], env["b13"], env["bb"]):
        assert cli.get(f"/espace/bulletins/{bid}.pdf").status_code == 404
    with flask_app.app_context():
        assert ConsultationBulletin.query.count() == 2
    # la session du portail n'ouvre aucune page de gestion
    r = cli.get("/bulletins")
    assert r.status_code == 302 and "/espace" not in r.headers["Location"]
    assert cli.get("/portail-employes").status_code == 302
    # même matricule M1 dans une autre entreprise : le PIN d'ACME n'y ouvre rien
    cli_b, _ = _connexion(entreprise="beta")
    assert "incorrect" in _messages(cli_b)
    # le tableau de suivi montre la consultation
    html = _client_pour(env["admin"]).get("/portail-employes").get_data(as_text=True)
    assert "Actif" in html and "2 / 2" not in html and "1 / 2" in html   # janvier : 1 bulletin consulté sur 2


def test_verrouillage_apres_5_erreurs(env):
    _activer_portail(env)
    _activer_compte(_inviter(env, "s1")[0])
    for _ in range(ps.ESSAIS_MAX):
        cli, _ = _connexion(pin="000111")
    cli, r = _connexion()                     # bon PIN, mais compte verrouillé
    assert "bloqué" in _messages(cli)
    assert cli.get("/espace/bulletins").status_code == 302
    with flask_app.app_context():
        a = AccesSalarie.query.one()
        a.verrou_jusqua = utcnow() - timedelta(seconds=1)
        db.session.commit()
    cli, r = _connexion()
    assert r.headers["Location"].endswith("/espace/bulletins")


def test_desactivation_depart_reinvitation_et_portail_ferme(env):
    _activer_portail(env)
    cli, _ = _activer_compte(_inviter(env, "s1")[0])
    assert cli.get("/espace/bulletins").status_code == 200
    rh = _client_pour(env["rh"])
    rh.post(f"/portail-employes/{env['s1']}/desactiver")
    assert cli.get("/espace/bulletins").status_code == 302          # déconnecté immédiatement
    cli, _ = _connexion()
    assert "incorrect" in _messages(cli)
    rh.post(f"/portail-employes/{env['s1']}/reactiver")
    cli, r = _connexion()
    assert r.headers["Location"].endswith("/espace/bulletins")
    # réinvitation (PIN oublié) : l'ancien PIN ne fonctionne plus, la session ouverte non plus
    code = _inviter(env, "s1")[0]
    assert cli.get("/espace/bulletins").status_code == 302
    cli, _ = _connexion()
    assert "incorrect" in _messages(cli)
    cli, _ = _activer_compte(code, pin="739146")
    assert cli.get("/espace/bulletins").status_code == 200
    # départ du salarié : accès coupé
    with flask_app.app_context():
        db.session.get(Salarie, env["s1"]).statut = "SORTI"
        db.session.commit()
    assert cli.get("/espace/bulletins").status_code == 302
    with flask_app.app_context():
        db.session.get(Salarie, env["s1"]).statut = "ACTIF"
        db.session.commit()
    cli, _ = _connexion(pin="739146")
    assert cli.get("/espace/bulletins").status_code == 200
    # portail fermé par l'administrateur : plus personne
    _client_pour(env["admin"]).post("/portail-employes/activation", data={"actif": "0"})
    assert cli.get("/espace/bulletins").status_code == 302
    # un salarié parti n'est jamais invité
    _activer_portail(env)
    r = _client_pour(env["admin"]).post("/portail-employes/inviter", data={"salarie_ids": [str(env["s3"])]})
    assert "plus actif" in r.get_data(as_text=True)


def test_inactivite_et_changement_de_pin(env):
    _activer_portail(env)
    cli, _ = _activer_compte(_inviter(env, "s1")[0])
    r = cli.post("/espace/pin", data={"pin_actuel": "482915", "pin": "111111", "pin2": "111111"})
    assert "répété" in _messages(cli)
    cli.post("/espace/pin", data={"pin_actuel": "482915", "pin": "605182", "pin2": "605182"})
    assert "modifié" in _messages(cli)
    assert cli.get("/espace/bulletins").status_code == 200             # la session courante reste ouverte
    autre, _ = _connexion(pin="482915")
    assert "incorrect" in _messages(autre)
    # 15 minutes sans activité : reconnexion demandée
    with cli.session_transaction() as sess:
        d = sess["espace"]; d["t"] = (utcnow() - timedelta(minutes=ps.INACTIVITE_MINUTES + 1)).isoformat()
        sess["espace"] = d
    r = cli.get("/espace/bulletins")
    assert r.status_code == 302 and "expiré" in _messages(cli)


def test_notification_bulletin_disponible(env):
    _activer_portail(env)
    _inviter(env, "s2")                                    # M2 invité mais n'active pas son accès
    _activer_compte(_inviter(env, "s1")[0])                # M1 active le sien
    flask_app.config["MAIL_CAPTURE"].clear()
    cli = _client_pour(env["admin"])
    cli.post("/portail-employes/prevenir", data={"periode_id": env["p1"]})
    mails = flask_app.config["MAIL_CAPTURE"]
    assert len(mails) == 1 and mails[0].recipients == ["jean@ndong.ga"] and "janvier 2026" in mails[0].subject
    assert "1 sans accès activé" in _messages(cli)
    with flask_app.app_context():
        assert AuditLog.query.filter_by(entite="portail_notification", entite_id=env["p1"]).count() == 1


def test_pwa_et_pin(env):
    cli = flask_app.test_client()
    r = cli.get("/espace/manifest.webmanifest")
    assert r.status_code == 200 and r.mimetype == "application/manifest+json" and b'"scope": "/espace"' in r.data
    r = cli.get("/espace/sw.js")
    assert r.status_code == 200 and r.headers["Service-Worker-Allowed"] == "/espace"
    assert cli.get("/espace").status_code == 200 and cli.get("/espace/hors-ligne").status_code == 200

    class S:
        date_naissance = date(1985, 12, 3)
        matricule = "004215"
    assert ps.pin_refuse("031285", S) and ps.pin_refuse("12345", S) and ps.pin_refuse("987654", S)
    assert ps.pin_refuse("482915", S) is None



def _espace_connecte(env):
    _activer_portail(env)
    cli, _ = _activer_compte(_inviter(env, "s1")[0])
    return cli


def test_conges_demande_annulation_et_traitement_rh(env):
    cli = _espace_connecte(env)
    r = cli.get("/espace/conges")
    assert r.status_code == 200 and "Mon solde" in r.get_data(as_text=True)
    dep = date.today() + timedelta(days=10)
    ret = dep + timedelta(days=4)
    cli.post("/espace/conges", data={"type_conge": "ANNUEL", "date_depart": dep.isoformat(),
                                     "date_retour": ret.isoformat(), "commentaire": "vacances"})
    assert "5 jour(s)" in _messages(cli)
    with flask_app.app_context():
        c = Conge.query.filter(Conge.date_depart.isnot(None)).one()
        assert (c.statut, c.origine, c.commentaire, c.salarie_id) == ("DEMANDÉ", "PORTAIL", "vacances", env["s1"])
        # la ligne de solde de l'année existe, comme pour une demande saisie par le RH
        assert Conge.query.filter(Conge.date_depart.is_(None), Conge.salarie_id == env["s1"]).count() == 1
        cid = c.id
    # refus : chevauchement, dates passées, type réservé au RH, retour avant départ
    for data, attendu in (
            ({"type_conge": "ANNUEL", "date_depart": ret.isoformat(), "date_retour": (ret + timedelta(days=2)).isoformat()}, "chevauchent"),
            ({"type_conge": "ANNUEL", "date_depart": "2020-01-01", "date_retour": "2020-01-03"}, "passée"),
            ({"type_conge": "MALADIE", "date_depart": dep.isoformat(), "date_retour": dep.isoformat()}, "non disponible"),
            ({"type_conge": "ANNUEL", "date_depart": ret.isoformat(), "date_retour": dep.isoformat()}, "doit suivre")):
        cli.post("/espace/conges", data=data)
        assert attendu in _messages(cli)
    # visible côté RH avec la mention « via le portail »
    html = _client_pour(env["admin"]).get("/conges").get_data(as_text=True)
    assert "via le portail" in html and "vacances" in html
    assert "1</strong> demande(s) de congé" in _client_pour(env["admin"]).get("/portail-employes").get_data(as_text=True)
    # le salarié annule sa demande en attente ; un autre salarié ne peut pas l'annuler
    with flask_app.app_context():
        autre = Conge(tenant_id=env["t"], salarie_id=env["s2"], annee=dep.year, type_conge="ANNUEL", statut="DEMANDÉ",
                      date_depart=dep, date_retour=dep)
        db.session.add(autre); db.session.commit(); autre_id = autre.id
    assert cli.post(f"/espace/conges/{autre_id}/annuler").status_code == 404
    cli.post(f"/espace/conges/{cid}/annuler")
    with flask_app.app_context():
        assert db.session.get(Conge, cid) is None and db.session.get(Conge, autre_id) is not None
    # une demande accordée ne peut plus être annulée par le salarié
    cli.post("/espace/conges", data={"type_conge": "PERMISSION", "date_depart": dep.isoformat(),
                                     "date_retour": dep.isoformat()})
    with flask_app.app_context():
        c = Conge.query.filter_by(salarie_id=env["s1"], type_conge="PERMISSION").one()
        cid = c.id
    _client_pour(env["admin"]).post(f"/conges/{cid}/approuver")
    cli.post(f"/espace/conges/{cid}/annuler")
    assert "en attente" in _messages(cli)
    assert "Accordé" in cli.get("/espace/conges").get_data(as_text=True)


def test_attestations_validation_rh_puis_automatique(env):
    cli = _espace_connecte(env)
    cli.post("/espace/documents", data={"type_doc": "ATTESTATION_TRAVAIL", "motif": "banque"})
    cli.post("/espace/documents", data={"type_doc": "ATTESTATION_TRAVAIL"})
    assert "déjà en attente" in _messages(cli)
    with flask_app.app_context():
        d = DemandeDocument.query.one()
        assert d.statut == "DEMANDE" and d.pdf is None
        did = d.id
    assert cli.get(f"/espace/documents/{did}.pdf").status_code == 404          # pas encore délivrée
    rh = _client_pour(env["rh"])
    assert "banque" in rh.get("/portail-employes").get_data(as_text=True)
    flask_app.config["MAIL_CAPTURE"].clear()
    rh.post(f"/portail-employes/document/{did}/delivrer")
    assert len(flask_app.config["MAIL_CAPTURE"]) == 1                       # salarié prévenu par e-mail
    r = cli.get(f"/espace/documents/{did}.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:4] == b"%PDF"
    # refus avec motif
    cli.post("/espace/documents", data={"type_doc": "ATTESTATION_SALAIRE"})
    with flask_app.app_context():
        d2 = DemandeDocument.query.filter_by(type_doc="ATTESTATION_SALAIRE").one().id
    rh.post(f"/portail-employes/document/{d2}/refuser", data={"motif_refus": "voir le RH"})
    assert "voir le RH" in cli.get("/espace/documents").get_data(as_text=True)
    # délivrance automatique : réservée à l'administrateur, immédiate pour le salarié
    assert rh.post("/portail-employes/attestations-auto", data={"auto": "1"}).status_code == 403
    _client_pour(env["admin"]).post("/portail-employes/attestations-auto", data={"auto": "1"})
    cli.post("/espace/documents", data={"type_doc": "ATTESTATION_SALAIRE"})
    assert "prête" in _messages(cli)
    with flask_app.app_context():
        d3 = DemandeDocument.query.filter_by(type_doc="ATTESTATION_SALAIRE", statut="DELIVRE").one()
        assert d3.traite_par == "Délivrance automatique" and d3.pdf[:4] == b"%PDF"
        d3id = d3.id
    # document d'un autre salarié : introuvable
    with flask_app.app_context():
        autre = DemandeDocument(tenant_id=env["t"], salarie_id=env["s2"], type_doc="ATTESTATION_TRAVAIL",
                                statut="DELIVRE", pdf=b"%PDF-x", date_traitement=utcnow())
        db.session.add(autre); db.session.commit(); aid = autre.id
    assert cli.get(f"/espace/documents/{aid}.pdf").status_code == 404
    assert cli.get(f"/espace/documents/{d3id}.pdf").status_code == 200


def test_attestation_salaire_sans_bulletin_valide(env):
    _activer_portail(env)
    cli, _ = _activer_compte(_inviter(env, "s1")[0])
    with flask_app.app_context():
        for b in BulletinPaie.query.filter_by(salarie_id=env["s1"]).all():
            b.statut = "BROUILLON"
        db.session.commit()
    cli.post("/espace/documents", data={"type_doc": "ATTESTATION_SALAIRE"})
    with flask_app.app_context():
        did = DemandeDocument.query.one().id
    rh = _client_pour(env["rh"])
    rh.post(f"/portail-employes/document/{did}/delivrer")
    assert "aucun bulletin validé" in _messages(rh)
    with flask_app.app_context():
        assert DemandeDocument.query.one().statut == "DEMANDE"


def test_profil_demande_et_validation_rh(env):
    cli = _espace_connecte(env)
    assert "NDONG" in cli.get("/espace/profil").get_data(as_text=True)
    # champs inchangés : rien n'est créé
    cli.post("/espace/profil", data={"email": "jean@ndong.ga", "telephone": ""})
    assert "Aucune modification" in _messages(cli)
    cli.post("/espace/profil", data={"email": "pas-un-email"})
    assert "invalide" in _messages(cli)
    cli.post("/espace/profil", data={"telephone": "+241 77 12 34 56", "situation_matrimoniale": "MARIE AEAC",
                                     "nb_enfants": "2", "email": "jean@ndong.ga"})
    with flask_app.app_context():
        ds = {d.champ: d for d in DemandeProfil.query.all()}
        assert set(ds) == {"telephone", "situation_matrimoniale", "nb_enfants"}
        s1 = db.session.get(Salarie, env["s1"])
        assert s1.telephone is None                       # rien n'est appliqué avant validation
        ids = {k: v.id for k, v in ds.items()}
    # nouvelle demande sur le même champ : remplace la précédente en attente
    cli.post("/espace/profil", data={"telephone": "+241 66 00 00 00"})
    with flask_app.app_context():
        tel = DemandeProfil.query.filter_by(champ="telephone").all()
        assert len(tel) == 1 and tel[0].nouvelle_valeur == "+241 66 00 00 00"
        ids["telephone"] = tel[0].id
    rh = _client_pour(env["rh"])
    for champ in ("telephone", "situation_matrimoniale", "nb_enfants"):
        rh.post(f"/portail-employes/profil/{ids[champ]}/accepter")
    with flask_app.app_context():
        s1 = db.session.get(Salarie, env["s1"])
        assert (s1.telephone, s1.situation_matrimoniale, s1.nb_enfants) == ("+241 66 00 00 00", "MARIE AEAC", 2)
        from core import calculer_parts_irpp
        assert float(s1.nombre_parts) == calculer_parts_irpp("MARIE AEAC", 2)
    # refus avec motif, visible par le salarié ; une demande déjà traitée ne se retraite pas
    cli.post("/espace/profil", data={"adresse": "Quartier Potos, Franceville"})
    with flask_app.app_context():
        aid = DemandeProfil.query.filter_by(champ="adresse").one().id
    rh.post(f"/portail-employes/profil/{aid}/refuser", data={"motif_refus": "justificatif manquant"})
    assert "justificatif manquant" in cli.get("/espace/profil").get_data(as_text=True)
    assert rh.post(f"/portail-employes/profil/{aid}/accepter").status_code == 404
    # lecture seule : aucune validation possible
    _client_pour(env["dir"]).post(f"/portail-employes/profil/{ids['telephone']}/refuser")
    with flask_app.app_context():
        assert DemandeProfil.query.get(ids["telephone"]).statut == "ACCEPTE"
        assert db.session.get(Salarie, env["s1"]).adresse is None
