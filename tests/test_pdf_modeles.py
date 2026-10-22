# -*- coding: utf-8 -*-
"""tests/test_pdf_modeles.py — PDF des modèles Moderne, Minimaliste et Grand
livre : chaque modèle choisi dans Paramètres produit SON PDF (bouton PDF du
RH, PDF groupé de la période, portail des employés), montants identiques,
taux recalculés (jamais écrits en dur)."""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_pdfmod_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-pdfmod")

import pytest
from reportlab.platypus import Paragraph, Table, KeepInFrame

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, CategorieEmploi, Salarie, PeriodePaie, BulletinPaie,
                    BulletinComposant)
import pdf_bulletin_modeles as pm


def _client_pour(uid):
    cli = flask_app.test_client()
    with cli.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    return cli


@pytest.fixture
def env():
    flask_app.config.update({"TESTING": True, "WTF_CSRF_ENABLED": False, "SERVER_NAME": "localhost"})
    from app import limiter
    limiter.enabled = False
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        plan = Plan(code="PRO", nom="Pro", prix_mensuel=1, max_salaries=50, max_utilisateurs=10, actif=True)
        db.session.add(plan); db.session.flush()
        t = Tenant(slug="acme", denomination="ACME SARL", adresse="BP 12", ville="Franceville", pays="Gabon",
                   nif="NIF-1", numero_cnss="CN-1", plan_id=plan.id, statut="ACTIF", token_api="ta")
        db.session.add(t); db.session.flush()
        u = Utilisateur(nom="A", prenom="A", email="a@a.ga", role="TENANT_ADMIN", tenant_id=t.id, actif=True,
                        email_verifie=True)
        u.set_password("x"); db.session.add(u)
        cat = CategorieEmploi(tenant_id=t.id, code="C3", libelle="Maîtrise"); db.session.add(cat); db.session.flush()
        s1 = Salarie(tenant_id=t.id, matricule="M1", nom="NDONG", prenom="Jean", categorie_id=cat.id,
                     date_embauche=date(2019, 3, 1), nb_enfants=2, nombre_parts=2)
        s2 = Salarie(tenant_id=t.id, matricule="M2", nom="MBA", prenom="Awa", categorie_id=cat.id,
                     date_embauche=date(2020, 1, 1))
        db.session.add_all([s1, s2]); db.session.flush()
        p = PeriodePaie(tenant_id=t.id, annee=2026, mois=10, libelle_mois="OCTOBRE"); db.session.add(p); db.session.flush()
        b1 = BulletinPaie(tenant_id=t.id, salarie_id=s1.id, periode_id=p.id, statut="VALIDÉ", numero="BP-2026-000007",
                          nb_jours_travailles=26, salaire_base=650000, heures_sup_10=28125, prime_transport=30000,
                          salaire_brut=708125, base_cnss=708125, base_cnamgs=708125, base_tcs=558125, base_irpp=640000,
                          cnss_salarie=17703, cnamgs_salarie=14163, tcs=27906, irpp=31250, cnss_patronale=113300,
                          cnamgs_patronale=29033, fnh=21244, cfp=3541, salaire_net=617103, prime_panier=26000,
                          acompte=50000, ajustement_arrondi=-3, net_a_payer=593100)
        # bulletin presque vide : aucun champ facultatif renseigné
        b2 = BulletinPaie(tenant_id=t.id, salarie_id=s2.id, periode_id=p.id, statut="VALIDÉ", salaire_base=300000,
                          salaire_brut=300000, net_a_payer=300000)
        db.session.add_all([b1, b2]); db.session.flush()
        db.session.add(BulletinComposant(bulletin_id=b1.id, libelle="Retenue mutuelle", sens="RETENUE", montant=5000))
        db.session.commit()
        ids = {"t": t.id, "u": u.id, "b1": b1.id, "b2": b2.id, "p": p.id}
    yield ids
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


def _textes(flowables):
    out = []

    def visite(x):
        if isinstance(x, Paragraph):
            out.append(x.getPlainText())
        elif isinstance(x, Table):
            for ligne in x._cellvalues:
                for c in ligne:
                    for y in (c if isinstance(c, (list, tuple)) else [c]):
                        visite(y)
        elif isinstance(x, KeepInFrame):
            for y in x._content:
                visite(y)
        elif isinstance(x, (list, tuple)):
            for y in x:
                visite(y)
    visite(flowables)
    return " | ".join(out)


SIGNATURES = {"moderne": "ÉLÉMENTS DE RÉMUNÉRATION (GAINS)", "minimaliste": "DOCUMENT CONFIDENTIEL",
              "grandlivre": "COTISATIONS PATRONALES (INFORMATIF)"}


@pytest.mark.parametrize("modele", pm.MODELES)
def test_contenu_et_taux_recalcules(env, modele):
    with flask_app.app_context():
        b = db.session.get(BulletinPaie, env["b1"])
        txt = _textes(pm.elements(b, db.session.get(Tenant, env["t"]), modele))
        assert SIGNATURES[modele] in txt
        for attendu in ("ACME SARL", "NDONG", "OCTOBRE 2026", "708 125 FCFA", "593 100 FCFA", "Retenue mutuelle",
                        "BP-2026-000007"):
            assert attendu in txt, (modele, attendu)
        # taux recalculés à partir des montants : 17 703 / 708 125 = 2,5 % ; 113 300 / 708 125 = 16 %
        assert "2,5 %" in txt and "Barème" in txt
        if modele != "minimaliste":                                 # pas de colonne « taux patronal » en minimaliste
            assert "16 %" in txt
        assert "18%" not in txt and "18 %" not in txt                # aucun taux écrit en dur
        assert "cinq cent quatre-vingt-treize mille cent" in txt    # net en lettres
        # bulletin presque vide : aucun plantage
        b2 = db.session.get(BulletinPaie, env["b2"])
        pdf = pm.generer(b2, db.session.get(Tenant, env["t"]), modele)
        assert pdf[:4] == b"%PDF"


@pytest.mark.parametrize("modele", ["classique", "sgtg"] + list(pm.MODELES))
def test_pdf_du_rh_au_modele_choisi(env, modele, monkeypatch):
    with flask_app.app_context():
        db.session.get(Tenant, env["t"]).modele_bulletin = modele
        db.session.commit()
    appels = []
    vrai = pm.generer
    monkeypatch.setattr(pm, "generer", lambda b, t, m: appels.append(m) or vrai(b, t, m))
    r = _client_pour(env["u"]).get(f"/bulletins/{env['b1']}/pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:4] == b"%PDF"
    assert appels == ([modele] if modele in pm.MODELES else [])
    # PDF groupé de la période : un bulletin par page, au même modèle
    r = _client_pour(env["u"]).get(f"/bulletins/export-zip/{env['p']}")
    assert r.status_code == 200 and r.data[:4] == b"%PDF" and b"/Count 2" in r.data
