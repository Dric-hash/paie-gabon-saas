"""tests/test_prestataires_avances.py — Avances prestataires + relevé imprimable.

Démarre l'app Flask avec SQLite en mémoire, crée un tenant + admin + prestataire,
puis vérifie : création d'avance, affichage sur la fiche, rendu du relevé
imprimable (avec la liste des avances), suppression, et isolation multi-tenant.
"""
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Base fichier temporaire (et non :memory:) pour un partage déterministe de la
# base entre toutes les connexions/clients de test.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_test_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-avances")

import pytest
from datetime import date

from app import app as flask_app
from models import db, Plan, Tenant, Utilisateur, Prestataire, AvancePrestataire, Site


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
        basic = Utilisateur(nom="AGENT", prenom="B", email="agent@a.ga",
                            role="CONSULTANT", tenant_id=t_a.id,
                            actif=True, email_verifie=True)
        basic.set_password("MotDePasse1")
        db.session.add(basic)
        # Prestataire sous-traitant du tenant A + un du tenant B (isolation)
        st = Prestataire(tenant_id=t_a.id, code="ST001", categorie="SOUS_TRAITANT",
                         raison_sociale="BTP SOUS-TRAITANT SARL")
        autre = Prestataire(tenant_id=t_b.id, code="ST999", categorie="SOUS_TRAITANT",
                            raison_sociale="AUTRE TENANT SARL")
        db.session.add_all([st, autre])
        db.session.commit()
        chantier = Site(tenant_id=t_a.id, nom="Chantier Akanda", code="CH1")
        db.session.add(chantier)
        db.session.commit()
        ids = {"tenant_a": t_a.id, "admin": admin.id, "basic": basic.id,
               "prest_a": st.id, "prest_b": autre.id, "site_a": chantier.id}
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


# ── Nouveaux comportements : édition, validation, site, devises ────────────────
def _creer(client, **extra):
    pid = client._ids["prest_a"]
    data = {"montant": "100000", "date_avance": "2026-06-10"}
    data.update(extra)
    client.post(f"/prestataires/{pid}/avances/nouvelle", data=data, follow_redirects=True)
    with flask_app.app_context():
        return AvancePrestataire.query.filter_by(prestataire_id=pid).order_by(
            AvancePrestataire.id.desc()).first().id


def test_modifier_avance_en_attente(client):
    aid = _creer(client, motif="initial")
    client.post(f"/prestataires/avances/{aid}/modifier",
                data={"montant": "120000", "date_avance": "2026-06-11",
                      "motif": "corrigé", "devise": "XAF"}, follow_redirects=True)
    with flask_app.app_context():
        a = db.session.get(AvancePrestataire, aid)
        assert float(a.montant) == 120000
        assert a.motif == "corrigé"


def test_valider_fige_avance(client):
    aid = _creer(client)
    # Validation par le chef de chantier
    client.post(f"/prestataires/avances/{aid}/valider",
                data={"valide_par_nom": "M. NZE"}, follow_redirects=True)
    with flask_app.app_context():
        a = db.session.get(AvancePrestataire, aid)
        assert a.statut == "VALIDEE"
        assert a.valide_par_nom == "M. NZE"
        assert a.date_validation is not None
        assert a.est_modifiable is False
    # Une fois validée : ni modification ni suppression
    client.post(f"/prestataires/avances/{aid}/modifier",
                data={"montant": "1", "date_avance": "2026-06-11"}, follow_redirects=True)
    client.post(f"/prestataires/avances/{aid}/supprimer", follow_redirects=True)
    with flask_app.app_context():
        a = db.session.get(AvancePrestataire, aid)
        assert a is not None                 # toujours là (suppression refusée)
        assert float(a.montant) == 100000    # inchangée (modif refusée)


def test_validation_exige_nom_chef(client):
    aid = _creer(client)
    client.post(f"/prestataires/avances/{aid}/valider",
                data={"valide_par_nom": ""}, follow_redirects=True)
    with flask_app.app_context():
        assert db.session.get(AvancePrestataire, aid).statut == "EN_ATTENTE"


def test_avance_sur_chantier(client):
    sid = client._ids["site_a"]
    aid = _creer(client, site_id=str(sid), motif="avance chantier")
    with flask_app.app_context():
        a = db.session.get(AvancePrestataire, aid)
        assert a.site_id == sid
        assert a.site.nom == "Chantier Akanda"


def test_avance_devise_etrangere(client):
    # 1000 EUR à parité fixe 655,957 -> 655 957 XAF
    aid = _creer(client, montant="1000", devise="EUR", taux_change="655.957")
    with flask_app.app_context():
        a = db.session.get(AvancePrestataire, aid)
        assert a.devise == "EUR"
        assert abs(a.montant_en_xaf - 655957) < 1
    # Le relevé affiche la devise et l'équivalent XAF
    r = client.get(f"/prestataires/{client._ids['prest_a']}/releve")
    html = r.data.decode("utf-8", errors="ignore")
    assert "EUR" in html and "655 957" in html


def test_api_taux_devise(client):
    r = client.get("/api/prestataire/taux-devise?devise=EUR")
    assert r.status_code == 200
    j = r.get_json()
    assert j["devise"] == "EUR"
    assert abs(j["taux_xaf"] - 655.957) < 0.01


def test_fiche_contient_fonctions_js(client):
    """Garde-fou : les fonctions JS des boutons doivent être déclarées
    (évite une régression où un bloc <script> cassé rend les boutons inertes)."""
    pid = client._ids["prest_a"]
    html = client.get(f"/prestataires/{pid}").data.decode("utf-8", errors="ignore")
    for fn in ("function ouvrirAvance(", "function ouvrirEditAvance(",
               "function calculerFacture(", "function ouvrirPaiement(",
               "function ouvrirFacture(", "function ouvrirEditFacture(",
               "function ajouterLigne("):
        assert fn in html, f"Fonction JS manquante : {fn}"
    assert "<script>\n  const ht" not in html


# ── Factures : multi-lignes + workflow brouillon→validée→payable ──────────────
from models import FacturePrestataire, LigneFacturePrestataire  # noqa: E402


def _facture(client, **extra):
    pid = client._ids["prest_a"]
    data = {
        "numero": extra.pop("numero", "F-001"),
        "date_facture": "2026-06-12", "taux_tva": "0", "taux_retenue": "0",
        "ligne_designation": ["Dalle béton", "Carrelage"],
        "ligne_quantite": ["100", "50"],
        "ligne_unite": ["m²", "m²"],
        "ligne_prix": ["15000", "8000"],
    }
    data.update(extra)
    client.post(f"/prestataires/{pid}/factures/nouvelle", data=data, follow_redirects=True)
    with flask_app.app_context():
        return FacturePrestataire.query.filter_by(prestataire_id=pid).order_by(
            FacturePrestataire.id.desc()).first().id


def test_facture_multi_lignes(client):
    fid = _facture(client)
    with flask_app.app_context():
        f = db.session.get(FacturePrestataire, fid)
        assert f.statut == "BROUILLON"
        assert len(f.lignes) == 2
        # 100×15000 + 50×8000 = 1 500 000 + 400 000 = 1 900 000
        assert float(f.montant_ht) == 1900000
        assert float(f.montant_net_a_payer) == 1900000


def test_paiement_bloque_avant_validation(client):
    fid = _facture(client)
    client.post(f"/prestataires/factures/{fid}/payer",
                data={"montant": "100000", "date_paiement": "2026-06-13"},
                follow_redirects=True)
    with flask_app.app_context():
        f = db.session.get(FacturePrestataire, fid)
        assert float(f.montant_paye or 0) == 0     # paiement refusé
        assert f.statut == "BROUILLON"


def test_modifier_puis_valider_puis_payer(client):
    fid = _facture(client)
    # Modification autorisée en brouillon : on remplace par une seule ligne
    client.post(f"/prestataires/factures/{fid}/modifier", data={
        "numero": "F-001", "date_facture": "2026-06-12", "taux_tva": "0", "taux_retenue": "0",
        "ligne_designation": ["Forfait gros œuvre"], "ligne_quantite": ["1"],
        "ligne_unite": ["forfait"], "ligne_prix": ["2000000"],
    }, follow_redirects=True)
    with flask_app.app_context():
        f = db.session.get(FacturePrestataire, fid)
        assert len(f.lignes) == 1
        assert float(f.montant_ht) == 2000000
    # Validation
    client.post(f"/prestataires/factures/{fid}/valider", follow_redirects=True)
    with flask_app.app_context():
        f = db.session.get(FacturePrestataire, fid)
        assert f.statut == "VALIDEE"
        assert f.est_payable is True
        assert f.est_modifiable is False
    # Paiement maintenant autorisé
    client.post(f"/prestataires/factures/{fid}/payer",
                data={"montant": "2000000", "date_paiement": "2026-06-14"},
                follow_redirects=True)
    with flask_app.app_context():
        f = db.session.get(FacturePrestataire, fid)
        assert f.statut == "PAYEE"
        assert float(f.montant_paye) == 2000000


def test_facture_devise_mad(client):
    # Facture en dirham : montant_xaf = net × taux
    fid = _facture(client, numero="F-MAD", devise="MAD", taux_change="60",
                   ligne_designation=["Étude"], ligne_quantite=["1"],
                   ligne_unite=["forfait"], ligne_prix=["10000"])
    with flask_app.app_context():
        f = db.session.get(FacturePrestataire, fid)
        assert f.devise == "MAD"
        assert float(f.montant_ht) == 10000
        assert abs(f.montant_net_en_xaf - 600000) < 1   # 10000 × 60
    # Impression : devise + équivalent XAF présents
    r = client.get(f"/prestataires/factures/{fid}/imprimer")
    assert r.status_code == 200
    html = r.data.decode("utf-8", errors="ignore")
    assert "MAD" in html and "Étude" in html and "600 000" in html


def test_facture_impression(client):
    fid = _facture(client, numero="F-IMP")
    r = client.get(f"/prestataires/factures/{fid}/imprimer")
    assert r.status_code == 200
    html = r.data.decode("utf-8", errors="ignore")
    assert "Facture N° F-IMP" in html
    assert "Dalle béton" in html and "Carrelage" in html


def test_admin_only_protege_la_suppression(client):
    """Verrou d'autorisation (utilisé par la route de suppression de facture) :
    laisse passer un administrateur, bloque (403) un non-admin. Test unitaire
    déterministe via login_user, indépendant des aléas de session HTTP."""
    from flask_login import login_user, logout_user
    from werkzeug.exceptions import Forbidden
    from core import admin_only

    @admin_only
    def vue_protegee():
        return "ok"

    with flask_app.test_request_context():
        admin = db.session.get(Utilisateur, client._ids["admin"])
        login_user(admin)
        assert vue_protegee() == "ok"          # admin : autorisé
        logout_user()

    with flask_app.test_request_context():
        agent = db.session.get(Utilisateur, client._ids["basic"])
        login_user(agent)
        assert agent.is_tenant_admin is False
        with pytest.raises(Forbidden):
            vue_protegee()                      # non-admin : 403


def test_admin_modifie_facture_validee(client):
    fid = _facture(client, numero="F-ADM")
    client.post(f"/prestataires/factures/{fid}/valider", follow_redirects=True)
    # Admin modifie une facture VALIDÉE
    client.post(f"/prestataires/factures/{fid}/modifier", data={
        "numero": "F-ADM", "date_facture": "2026-06-12", "taux_tva": "0", "taux_retenue": "0",
        "ligne_designation": ["Reprise"], "ligne_quantite": ["1"],
        "ligne_unite": ["forfait"], "ligne_prix": ["500000"],
    }, follow_redirects=True)
    with flask_app.app_context():
        f = db.session.get(FacturePrestataire, fid)
        assert float(f.montant_ht) == 500000
        assert f.statut == "VALIDEE"   # reste cohérente


def test_admin_supprime_facture_validee_et_payee(client):
    fid = _facture(client, numero="F-DEL")
    client.post(f"/prestataires/factures/{fid}/valider", follow_redirects=True)
    client.post(f"/prestataires/factures/{fid}/payer",
                data={"montant": "100000", "date_paiement": "2026-06-14"},
                follow_redirects=True)
    # Admin supprime même validée + payée
    client.post(f"/prestataires/factures/{fid}/supprimer", follow_redirects=True)
    with flask_app.app_context():
        assert db.session.get(FacturePrestataire, fid) is None
        assert FacturePrestataire.query.filter_by(numero="F-DEL").count() == 0
        assert LigneFacturePrestataire.query.filter_by(facture_id=fid).count() == 0





