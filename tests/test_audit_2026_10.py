# -*- coding: utf-8 -*-
"""tests/test_audit_2026_10.py — Non-régression de l'audit sécurité & bugs (oct. 2026).

Chaque test reproduit une faille ou un bug constaté puis corrigé :
  • rôles en lecture seule qui pouvaient écrire (congés, paie journaliers, prestataires…)
  • références croisées entre entreprises (catégorie / chantier d'un autre tenant)
  • XSS stockée via des noms injectés dans des attributs JavaScript
  • redirection ouverte à la connexion (« //site-pirate »)
  • congés : erreur 500 à la création, double comptage, écrasement, suppression
  • paie journaliers : double paiement ; bulletins : paiement d'un brouillon
  • PWA pointage : jeton non révoqué au changement de PIN
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_audit_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-audit")

import pytest
from datetime import date, timedelta

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, Salarie, CategorieEmploi, Site,
                    Conge, Journalier, FeuillePaieJournalier, AvanceJournalier,
                    BulletinPaie, PeriodePaie, PointeurChantier, Prestataire)


def _client_pour(uid):
    cli = flask_app.test_client()
    with cli.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    return cli


@pytest.fixture
def env():
    flask_app.config.update({"TESTING": True, "WTF_CSRF_ENABLED": False,
                             "SERVER_NAME": "localhost"})
    # Limiter (mémoire) partagé sur toute la session pytest : désactivé ici,
    # comme dans les autres fichiers de tests (sinon les connexions répétées
    # de la suite épuiseraient les fenêtres). Testé à part, explicitement.
    from app import limiter
    limiter.enabled = False
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        plan = Plan(code="PRO", nom="Pro", prix_mensuel=1, max_salaries=50,
                    max_utilisateurs=10, actif=True)
        db.session.add(plan); db.session.flush()
        ta = Tenant(slug="a", denomination="ACME SARL", sigle="A", activite="BTP",
                    convention="BTP", ville="Franceville", pays="Gabon",
                    plan_id=plan.id, statut="ACTIF", token_api="ta")
        tb = Tenant(slug="b", denomination="AUTRE SA", sigle="B", activite="BTP",
                    convention="BTP", ville="Libreville", pays="Gabon",
                    plan_id=plan.id, statut="ACTIF", token_api="tb")
        db.session.add_all([ta, tb]); db.session.flush()
        admin = Utilisateur(nom="ADMIN", prenom="A", email="admin@a.ga", role="TENANT_ADMIN",
                            tenant_id=ta.id, actif=True, email_verifie=True)
        compta = Utilisateur(nom="COMPTA", prenom="C", email="compta@a.ga", role="COMPTABLE",
                             tenant_id=ta.id, actif=True, email_verifie=True)
        admin.set_password("x"); compta.set_password("x")
        db.session.add_all([admin, compta])
        cat_b = CategorieEmploi(tenant_id=tb.id, code="SECRET", libelle="Catégorie de B",
                                salaire_minimum=999999)
        site_b = Site(tenant_id=tb.id, nom="Chantier confidentiel de B")
        s = Salarie(tenant_id=ta.id, matricule="M1", nom="NDONG", prenom="Jean",
                    date_embauche=date(2020, 1, 1), statut="ACTIF")
        j = Journalier(tenant_id=ta.id, nom="OBAME", prenom="Paul", taux_horaire=1000,
                       statut="ACTIF", mode_paiement="ESPECES")
        db.session.add_all([cat_b, site_b, s, j]); db.session.commit()
        ids = {"ta": ta.id, "tb": tb.id, "admin": admin.id, "compta": compta.id,
               "cat_b": cat_b.id, "site_b": site_b.id, "sal": s.id, "jour": j.id}
    # Contexte fermé avant le test : chaque requête du client de test aura ses
    # propres session SQLAlchemy et `g` (utilisateur Flask-Login), comme en
    # production. (Un contexte gardé ouvert fait partager le cache utilisateur
    # et la session entre requêtes, ce qui fausse les tests de droits.)
    yield ids
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


# ── 1. Rôles en lecture seule ─────────────────────────────────────────────────

def test_comptable_ne_peut_pas_approuver_ni_supprimer_un_conge(env):
    with flask_app.app_context():
        c = Conge(tenant_id=env["ta"], salarie_id=env["sal"], annee=2026,
                  date_depart=date(2026, 3, 2), date_retour=date(2026, 3, 6), statut="DEMANDÉ")
        db.session.add(c); db.session.commit(); cid = c.id
    cli = _client_pour(env["compta"])
    for url in (f"/conges/{cid}/approuver", f"/conges/{cid}/supprimer"):
        r = cli.post(url)
        assert r.status_code in (302, 403)
        assert "/login" not in (r.headers.get("Location") or "")   # bien authentifié, refusé par les droits
    with flask_app.app_context():
        c = db.session.get(Conge, cid)
        assert c is not None and c.statut == "DEMANDÉ"


def test_comptable_ne_peut_pas_payer_un_journalier(env):
    with flask_app.app_context():
        f = FeuillePaieJournalier(tenant_id=env["ta"], journalier_id=env["jour"],
                                  date_debut=date(2026, 3, 1), date_fin=date(2026, 3, 7),
                                  taux_horaire=1000, montant_brut=40000)
        db.session.add(f); db.session.commit(); fid = f.id
    r = _client_pour(env["compta"]).post(f"/journaliers/paie/{fid}/payer")
    assert "/login" not in (r.headers.get("Location") or "")
    with flask_app.app_context():
        assert db.session.get(FeuillePaieJournalier, fid).statut == "EN_ATTENTE"


def test_comptable_ne_peut_pas_creer_de_prestataire(env):
    r = _client_pour(env["compta"]).post("/prestataires/nouveau",
                                         data={"raison_sociale": "Fraude SARL"})
    assert "/login" not in (r.headers.get("Location") or "")
    with flask_app.app_context():
        assert Prestataire.query.filter_by(tenant_id=env["ta"]).count() == 0


def test_comptable_garde_les_simulations(env):
    """Les calculs sans écriture restent accessibles en lecture seule."""
    r = _client_pour(env["compta"]).post("/api/simuler-paie", json={"salaire_base": 200000})
    assert r.status_code != 403


# ── 2. Références croisées entre entreprises ──────────────────────────────────

def test_salarie_ne_peut_pas_prendre_la_categorie_d_une_autre_entreprise(env):
    cli = _client_pour(env["admin"])
    cli.post("/salaries/nouveau", data={
        "matricule": "M2", "nom": "TEST", "prenom": "X", "date_embauche": "2026-01-05",
        "categorie_id": str(env["cat_b"]), "salaire_base": "150000", "type_contrat": "CDI"})
    with flask_app.app_context():
        s = Salarie.query.filter_by(tenant_id=env["ta"], matricule="M2").first()
        assert s is not None
        assert s.categorie_id is None   # la catégorie de B a été refusée


def test_id_du_tenant(env):
    from core import id_du_tenant
    with flask_app.app_context():
        ta = db.session.get(Tenant, env["ta"]); tb = db.session.get(Tenant, env["tb"])
        assert id_du_tenant(Site, env["site_b"], ta) is None
        assert id_du_tenant(Site, env["site_b"], tb) == env["site_b"]
        assert id_du_tenant(Site, "abc", tb) is None
        assert id_du_tenant(Site, "", tb) is None


# ── 3. XSS stockée dans les attributs JavaScript ──────────────────────────────

def test_nom_piege_non_execute_dans_la_fiche_salarie(env):
    with flask_app.app_context():
        s = db.session.get(Salarie, env["sal"])
        s.nom = "X');alert(document.domain);//"
        db.session.commit()
    html = _client_pour(env["admin"]).get(f"/salaries/{env['sal']}").get_data(as_text=True)
    import re
    # Aucun gestionnaire d'événement JavaScript ne contient la donnée piégée…
    gestionnaires = re.findall(r'\son[a-z]+="([^"]*)"', html)
    assert gestionnaires and all("alert(document.domain)" not in g for g in gestionnaires)
    assert "confirm(this.dataset.confirm)" in html
    # … elle n'apparaît que comme TEXTE échappé dans data-confirm.
    confirms = re.findall(r'data-confirm="([^"]*)"', html)
    assert any("X&#39;);alert(document.domain);//" in c for c in confirms)


# ── 4. Redirection ouverte ────────────────────────────────────────────────────

@pytest.mark.parametrize("cible", ["//evil.com", "/\\evil.com", "https://evil.com",
                                   "javascript:alert(1)", "/\t/evil.com", "evil.com"])
def test_chemin_interne_refuse_les_sorties(cible):
    from core import chemin_interne
    assert chemin_interne(cible, "/defaut") == "/defaut"


def test_chemin_interne_accepte_un_chemin_local():
    from core import chemin_interne
    assert chemin_interne("/bulletins?periode_id=3", "/x") == "/bulletins?periode_id=3"


def test_login_next_protocole_relatif_neutralise(env):
    r = flask_app.test_client().post("/login?next=//evil.com",
                                     data={"email": "admin@a.ga", "password": "x"})
    assert "evil.com" not in (r.headers.get("Location") or "")


# ── 5. Congés ─────────────────────────────────────────────────────────────────

def _creer_conge(cli, sid, dep, ret):
    return cli.post("/conges/nouveau", data={"salarie_id": sid, "annee": 2026,
                                             "date_depart": dep, "date_retour": ret,
                                             "type_conge": "ANNUEL"})


def test_creation_conge_sans_erreur_500(env):
    r = _creer_conge(_client_pour(env["admin"]), env["sal"], "2026-03-02", "2026-03-11")
    assert r.status_code == 302


def test_double_approbation_ne_double_pas_les_jours(env):
    cli = _client_pour(env["admin"])
    _creer_conge(cli, env["sal"], "2026-03-02", "2026-03-11")   # 10 jours
    with flask_app.app_context():
        cid = Conge.query.filter(Conge.salarie_id == env["sal"],
                                 Conge.date_depart.isnot(None)).first().id
    cli.post(f"/conges/{cid}/approuver"); cli.post(f"/conges/{cid}/approuver")
    with flask_app.app_context():
        assert float(db.session.get(Conge, cid).jours_pris) == 10
        solde = Conge.query.filter(Conge.salarie_id == env["sal"],
                                   Conge.date_depart.is_(None)).one()
        assert float(solde.jours_pris) == 10
        assert solde.statut == "SOLDE"


def test_deuxieme_demande_n_ecrase_pas_la_premiere(env):
    cli = _client_pour(env["admin"])
    _creer_conge(cli, env["sal"], "2026-03-02", "2026-03-11")
    with flask_app.app_context():
        c1 = Conge.query.filter(Conge.salarie_id == env["sal"],
                                Conge.date_depart.isnot(None)).first().id
    cli.post(f"/conges/{c1}/approuver")
    _creer_conge(cli, env["sal"], "2026-08-03", "2026-08-07")
    with flask_app.app_context():
        premier = db.session.get(Conge, c1)
        assert premier.date_depart == date(2026, 3, 2) and premier.statut == "APPROUVÉ"
        demandes = Conge.query.filter(Conge.salarie_id == env["sal"],
                                      Conge.date_depart.isnot(None)).all()
        assert len(demandes) == 2
        aout = [d for d in demandes if d.id != c1][0]
        assert float(aout.jours_pris or 0) == 0   # n'hérite pas des 10 jours de mars


def test_suppression_conge_approuve_restitue_le_solde(env):
    cli = _client_pour(env["admin"])
    _creer_conge(cli, env["sal"], "2026-03-02", "2026-03-06")   # 5 jours
    with flask_app.app_context():
        cid = Conge.query.filter(Conge.salarie_id == env["sal"],
                                 Conge.date_depart.isnot(None)).first().id
    cli.post(f"/conges/{cid}/approuver")
    r = cli.post(f"/conges/{cid}/supprimer")
    assert r.status_code == 302        # plus d'erreur 500 après suppression
    with flask_app.app_context():
        assert db.session.get(Conge, cid) is None
        solde = Conge.query.filter(Conge.salarie_id == env["sal"],
                                   Conge.date_depart.is_(None)).one()
        assert float(solde.jours_pris) == 0


def test_conge_salarie_inconnu_sans_erreur_500(env):
    r = _creer_conge(_client_pour(env["admin"]), 999999, "2026-09-01", "2026-09-02")
    assert r.status_code == 200      # formulaire réaffiché avec un message


# ── 6. Paie journaliers & bulletins ───────────────────────────────────────────

def test_feuille_journalier_payee_une_seule_fois(env):
    with flask_app.app_context():
        f = FeuillePaieJournalier(tenant_id=env["ta"], journalier_id=env["jour"],
                                  date_debut=date(2026, 3, 1), date_fin=date(2026, 3, 7),
                                  taux_horaire=1000, montant_brut=40000)
        db.session.add(f); db.session.commit(); fid = f.id
    cli = _client_pour(env["admin"])
    cli.post(f"/journaliers/paie/{fid}/payer")
    # Nouvelle avance APRÈS le paiement : elle ne doit pas être imputée sur cette feuille
    with flask_app.app_context():
        a = AvanceJournalier(tenant_id=env["ta"], journalier_id=env["jour"],
                             montant=5000, date_avance=date(2026, 3, 9))
        db.session.add(a); db.session.commit(); aid = a.id
    cli.post(f"/journaliers/paie/{fid}/payer")
    with flask_app.app_context():
        assert float(db.session.get(FeuillePaieJournalier, fid).avance_deduite or 0) == 0
        assert float(db.session.get(AvanceJournalier, aid).montant_regularise or 0) == 0


def test_paiement_groupe_deduit_les_avances(env):
    with flask_app.app_context():
        f = FeuillePaieJournalier(tenant_id=env["ta"], journalier_id=env["jour"],
                                  date_debut=date(2026, 3, 1), date_fin=date(2026, 3, 7),
                                  taux_horaire=1000, montant_brut=40000)
        a = AvanceJournalier(tenant_id=env["ta"], journalier_id=env["jour"],
                             montant=5000, date_avance=date(2026, 3, 3))
        db.session.add_all([f, a]); db.session.commit(); fid = f.id
    _client_pour(env["admin"]).post("/journaliers/paie/payer-selection",
                                    data={"feuille_ids": str(fid)})
    with flask_app.app_context():
        f = db.session.get(FeuillePaieJournalier, fid)
        assert f.statut == "PAYÉ" and float(f.avance_deduite) == 5000


def test_bulletin_brouillon_non_payable(env):
    with flask_app.app_context():
        per = PeriodePaie(tenant_id=env["ta"], annee=2026, mois=5, libelle_mois="MAI")
        db.session.add(per); db.session.flush()
        b = BulletinPaie(tenant_id=env["ta"], salarie_id=env["sal"], periode_id=per.id,
                         statut="BROUILLON", salaire_base=200000, salaire_brut=200000,
                         net_a_payer=170000)
        db.session.add(b); db.session.commit(); bid = b.id
    _client_pour(env["admin"]).post(f"/bulletins/{bid}/payer")
    with flask_app.app_context():
        assert db.session.get(BulletinPaie, bid).statut == "BROUILLON"


def test_valider_un_bulletin_paye_ne_le_retrograde_pas(env):
    with flask_app.app_context():
        per = PeriodePaie(tenant_id=env["ta"], annee=2026, mois=6, libelle_mois="JUIN")
        db.session.add(per); db.session.flush()
        b = BulletinPaie(tenant_id=env["ta"], salarie_id=env["sal"], periode_id=per.id,
                         statut="PAYÉ", salaire_base=200000, salaire_brut=200000,
                         net_a_payer=170000)
        db.session.add(b); db.session.commit(); bid = b.id
    _client_pour(env["admin"]).post(f"/bulletins/{bid}/valider")
    with flask_app.app_context():
        assert db.session.get(BulletinPaie, bid).statut == "PAYÉ"


# ── 7. PWA pointage : révocation du jeton au changement de PIN ────────────────

def test_changement_de_pin_revoque_le_jeton(env):
    with flask_app.app_context():
        p = PointeurChantier(tenant_id=env["ta"], nom="Chef", code="PTTEST01", actif=True)
        p.set_pin("4321"); db.session.add(p); db.session.commit(); pid = p.id
    mob = flask_app.test_client()
    tok = mob.post("/m/api/login", json={"code": "PTTEST01", "pin": "4321"}).get_json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    assert mob.get("/m/api/roster", headers=hdr).status_code == 200
    _client_pour(env["admin"]).post(f"/pointeurs/{pid}/pin")   # le RH régénère le PIN
    assert mob.get("/m/api/roster", headers=hdr).status_code == 401


def test_ancien_jeton_sans_empreinte_reste_accepte(env):
    """Transition : les jetons émis avant le correctif restent valides jusqu'à expiration."""
    from blueprints.mobile import _serializer
    with flask_app.app_context():
        p = PointeurChantier(tenant_id=env["ta"], nom="Chef", code="PTTEST02", actif=True)
        p.set_pin("1111"); db.session.add(p); db.session.commit(); pid = p.id
    with flask_app.test_request_context():
        legacy = _serializer().dumps({"pid": pid})
    r = flask_app.test_client().get("/m/api/roster", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200


def test_force_brute_du_pin_bloquee(env):
    """Au-delà de 5 essais par minute sur un même code pointeur → 429."""
    from app import limiter
    with flask_app.app_context():
        p = PointeurChantier(tenant_id=env["ta"], nom="Chef", code="PTBRUTE1", actif=True)
        p.set_pin("9876"); db.session.add(p); db.session.commit()
    limiter.enabled = True
    try:
        limiter.reset()
        mob = flask_app.test_client()
        codes = [mob.post("/m/api/login", json={"code": "PTBRUTE1", "pin": str(1000 + i)}).status_code
                 for i in range(7)]
        assert codes[:5] == [401] * 5          # essais refusés (mauvais PIN)
        assert 429 in codes[5:]                 # puis blocage
        assert mob.post("/m/api/login", json={"code": "PTBRUTE1", "pin": "9876"}).status_code == 429
    finally:
        limiter.reset()
        limiter.enabled = False


# ── 8. Paiement : uniquement le circuit MANUEL (API Airtel / CinetPay retirées) ─

@pytest.mark.parametrize("url", ["/webhook/airtel", "/webhook/cinetpay",
                                 "/paiement/airtel/initier", "/paiement/cinetpay/initier"])
def test_api_de_paiement_automatiques_retirees(env, url):
    assert flask_app.test_client().post(url).status_code == 404


def test_paiement_manuel_toujours_fonctionnel(env):
    from models import Paiement
    cli = _client_pour(env["admin"])
    page = cli.get("/paiement").get_data(as_text=True)
    assert "Airtel Money" in page and "Virement bancaire" in page   # options manuelles affichées
    r = cli.post("/paiement/confirmer", data={"moyen": "VIREMENT", "reference": "VIR-0001", "duree": "3"})
    assert r.status_code == 302
    with flask_app.app_context():
        p = Paiement.query.filter_by(tenant_id=env["ta"]).one()
        assert p.statut == "EN_ATTENTE" and p.reference_externe == "VIR-0001"
        assert db.session.get(Tenant, env["ta"]).statut == "PAIEMENT_EN_ATTENTE"


# ── 9. Congés : taux légal d'acquisition (Art. 222, Loi 022/2021) ──────────────

class _T:   # entreprise minimale
    def __init__(self, taux): self.jours_conge_par_mois = taux


class _S:   # salarié minimal
    def __init__(self, naissance, tenant=None): self.date_naissance = naissance; self.tenant = tenant


def test_taux_legal_adulte_2_jours():
    from conges_avance import taux_acquisition
    assert taux_acquisition(tenant=_T(None), salarie=_S(date(1990, 5, 1))) == 2.0
    assert taux_acquisition() == 2.0


def test_taux_mineur_2_5_jours():
    from conges_avance import taux_acquisition
    assert taux_acquisition(tenant=_T(2.0), salarie=_S(date(2010, 6, 1)),
                            date_ref=date(2026, 10, 14)) == 2.5     # 16 ans
    assert taux_acquisition(tenant=_T(2.0), salarie=_S(date(2008, 10, 14)),
                            date_ref=date(2026, 10, 14)) == 2.0     # 18 ans ce jour


def test_taux_contractuel_plus_favorable_et_plancher_legal():
    from conges_avance import taux_acquisition
    assert taux_acquisition(tenant=_T(2.5), salarie=_S(date(1990, 1, 1))) == 2.5
    assert taux_acquisition(tenant=_T(1.5), salarie=_S(date(1990, 1, 1))) == 2.0   # jamais sous la loi


def test_ne_le_29_fevrier_ne_plante_pas():
    from conges_avance import taux_acquisition
    assert taux_acquisition(tenant=_T(2.0), salarie=_S(date(2008, 2, 29)),
                            date_ref=date(2025, 12, 31)) == 2.5


def test_annee_complete_24_jours_pour_un_adulte():
    from conges_avance import calculer_jours_acquis
    r = calculer_jours_acquis(date(2020, 1, 1), date_ref=date(2026, 5, 31), annee_ref=2025,
                              salarie=_S(date(1990, 1, 1), tenant=_T(2.0)))
    assert r["jours_acquis_periode"] == 24.0


def test_parametre_taux_conges_borne(env):
    cli = _client_pour(env["admin"])
    cli.post("/parametres/societe", data={"jours_conge_par_mois": "3"})
    with flask_app.app_context():
        assert float(db.session.get(Tenant, env["ta"]).jours_conge_par_mois) == 2.5
    cli.post("/parametres/societe", data={"jours_conge_par_mois": "1"})
    with flask_app.app_context():
        assert float(db.session.get(Tenant, env["ta"]).jours_conge_par_mois) == 2.0


def test_page_conges_avec_salarie_ne_29_fevrier(env):
    with flask_app.app_context():
        s = db.session.get(Salarie, env["sal"]); s.date_naissance = date(2008, 2, 29); db.session.commit()
    r = _client_pour(env["admin"]).get("/conges?annee=2025")
    assert r.status_code == 200
