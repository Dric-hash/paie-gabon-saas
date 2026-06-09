"""
tests/test_routes_integration.py — Tests d'intégration des routes HTTP

Ces tests démarrent l'application Flask complète avec une base SQLite en
mémoire et simulent de vraies requêtes HTTP via le client de test. Ils
attrapent les bugs qui passent les tests unitaires mais cassent l'app réelle :
    - routes perdues lors d'un refactoring
    - imports manquants (NameError au runtime)
    - blocages CSRF sur les appels JSON
    - défauts d'isolation multi-tenant
    - erreurs 500 sur les pages

Exécution :
    pytest tests/test_routes_integration.py -v
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Configuration de test AVANT l'import de l'app ─────────────────────────────
os.environ["DATABASE_URL"]   = "sqlite:///:memory:"
os.environ["SECRET_KEY"]     = "test-secret-key-pour-tests-integration"
os.environ["WTF_CSRF_ENABLED"] = "True"   # on veut tester le comportement CSRF réel

import pytest
from datetime import date, datetime

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, CategorieEmploi,
                    Salarie, PeriodePaie)


# ══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════════
@pytest.fixture
def app():
    """Application de test avec une base SQLite en mémoire fraîche."""
    flask_app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "SERVER_NAME": "localhost",
    })
    with flask_app.app_context():
        db.drop_all()      # repartir d'une base vraiment vide
        db.create_all()
        _seed_data()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_data():
    """Crée un jeu de données minimal : 2 tenants, 1 admin, 1 RH, salariés."""
    plan = Plan(code="PRO", nom="Pro", prix_mensuel=35000,
                max_salaries=50, max_utilisateurs=3, actif=True)
    db.session.add(plan)
    db.session.flush()

    # ── Tenant A ──────────────────────────────────────────────────────────────
    t_a = Tenant(slug="entreprise-a", denomination="ENTREPRISE A", sigle="EA",
                 activite="BTP", ville="Libreville", pays="Gabon",
                 plan_id=plan.id, statut="ACTIF", token_api="token_a")
    # ── Tenant B (pour tester l'isolation) ─────────────────────────────────────
    t_b = Tenant(slug="entreprise-b", denomination="ENTREPRISE B", sigle="EB",
                 activite="COMMERCE", ville="Port-Gentil", pays="Gabon",
                 plan_id=plan.id, statut="ACTIF", token_api="token_b")
    db.session.add_all([t_a, t_b])
    db.session.flush()

    for t in (t_a, t_b):
        db.session.add(CategorieEmploi(tenant_id=t.id, code="C1", libelle="Ouvriers"))

    # ── Admin du tenant A ───────────────────────────────────────────────────────
    admin_a = Utilisateur(nom="ADMIN", prenom="Alice", email="admin@a.ga",
                          role="TENANT_ADMIN", tenant_id=t_a.id,
                          actif=True, email_verifie=True)
    admin_a.set_password("MotDePasse1")
    # ── RH du tenant A ──────────────────────────────────────────────────────────
    rh_a = Utilisateur(nom="RH", prenom="Robert", email="rh@a.ga",
                       role="RH", tenant_id=t_a.id, actif=True, email_verifie=True)
    rh_a.set_password("MotDePasse1")
    # ── Admin du tenant B ───────────────────────────────────────────────────────
    admin_b = Utilisateur(nom="ADMIN", prenom="Bob", email="admin@b.ga",
                          role="TENANT_ADMIN", tenant_id=t_b.id,
                          actif=True, email_verifie=True)
    admin_b.set_password("MotDePasse1")
    db.session.add_all([admin_a, rh_a, admin_b])
    db.session.flush()

    # ── Salarié du tenant A ─────────────────────────────────────────────────────
    sal_a = Salarie(tenant_id=t_a.id, matricule="EA001", nom="NDONG", prenom="Jean",
                    date_embauche=date(2023, 1, 1), emploi="Maçon",
                    situation_matrimoniale="MARIE", nb_enfants=2, statut="ACTIF")
    # ── Salarié du tenant B ─────────────────────────────────────────────────────
    sal_b = Salarie(tenant_id=t_b.id, matricule="EB001", nom="OBAME", prenom="Marie",
                    date_embauche=date(2023, 6, 1), emploi="Vendeuse",
                    situation_matrimoniale="CELIBATAIRE", nb_enfants=0, statut="ACTIF")
    db.session.add_all([sal_a, sal_b])

    # ── Période de paie tenant A ────────────────────────────────────────────────
    periode_a = PeriodePaie(tenant_id=t_a.id, mois=6, annee=2026,
                            libelle_mois="JUIN", statut="OUVERT")
    db.session.add(periode_a)
    db.session.commit()


def login(client, email, password="MotDePasse1"):
    """Connecte un utilisateur via le formulaire de login (avec CSRF)."""
    # Récupérer le token CSRF de la page de login
    page = client.get("/login")
    token = _extract_csrf(page.data)
    return client.post("/login", data={
        "email": email, "password": password, "csrf_token": token,
    }, follow_redirects=False)


def _extract_csrf(html_bytes):
    """Extrait le token CSRF d'une page HTML."""
    import re
    html = html_bytes.decode("utf-8", errors="ignore")
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ""


def auth_session(client, email):
    """Authentifie directement via la session (sans passer par le formulaire)."""
    with flask_app.app_context():
        u = Utilisateur.query.filter_by(email=email).first()
        uid = u.id
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — PAGES PUBLIQUES
# ══════════════════════════════════════════════════════════════════════════════
class TestPagesPubliques:
    def test_login_accessible(self, client):
        assert client.get("/login").status_code == 200

    def test_inscription_accessible(self, client):
        assert client.get("/inscription").status_code == 200

    def test_mot_de_passe_oublie_accessible(self, client):
        assert client.get("/mot-de-passe-oublie").status_code == 200

    def test_racine_redirige_vers_login(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_dashboard_non_connecte_redirige(self, client):
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 302  # redirigé vers login


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — AUTHENTIFICATION
# ══════════════════════════════════════════════════════════════════════════════
class TestAuthentification:
    def test_login_correct(self, client):
        r = login(client, "admin@a.ga")
        assert r.status_code == 302  # redirection après login réussi

    def test_login_mauvais_mot_de_passe(self, client):
        page = client.get("/login")
        token = _extract_csrf(page.data)
        r = client.post("/login", data={
            "email": "admin@a.ga", "password": "FauxMotDePasse",
            "csrf_token": token,
        })
        assert r.status_code == 200  # reste sur la page de login
        assert b"incorrect" in r.data.lower() or b"error" in r.data.lower()

    def test_login_sans_csrf_rejete(self, client):
        """Une soumission de login sans token CSRF doit être rejetée."""
        r = client.post("/login", data={"email": "admin@a.ga",
                                        "password": "MotDePasse1"})
        assert r.status_code == 400  # CSRF manquant

    def test_logout(self, client):
        auth_session(client, "admin@a.ga")
        r = client.get("/logout", follow_redirects=False)
        assert r.status_code == 302


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — PAGES TENANT (authentifié)
# ══════════════════════════════════════════════════════════════════════════════
class TestPagesTenant:
    """Vérifie que toutes les pages principales répondent sans erreur 500."""

    PAGES = ["/dashboard", "/salaries", "/bulletins", "/conges", "/acomptes",
             "/journaliers", "/pointage", "/sites", "/periodes", "/parametres",
             "/utilisateurs", "/declaration-cnss", "/simulateur", "/recherche",
             "/audit"]

    @pytest.mark.parametrize("page", PAGES)
    def test_page_repond_sans_erreur(self, client, page):
        auth_session(client, "admin@a.ga")
        r = client.get(page, follow_redirects=False)
        assert r.status_code != 500, f"{page} renvoie une erreur 500"
        assert r.status_code in (200, 302)


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — CALCUL TEMPS RÉEL (le bug CSRF qu'on a corrigé)
# ══════════════════════════════════════════════════════════════════════════════
class TestCalculBulletin:
    def test_calculer_bulletin_sans_csrf_fonctionne(self, client):
        """Le calcul temps réel (POST JSON sans CSRF) doit fonctionner."""
        auth_session(client, "admin@a.ga")
        r = client.post("/api/calculer-bulletin",
                        json={"salaire_base": 500000},
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 200, "Le calcul ne doit pas être bloqué par CSRF"
        data = r.get_json()
        assert "net_a_payer" in data
        assert data["salaire_brut"] == 500000

    def test_calcul_cnss_correct(self, client):
        """Vérifie que le calcul renvoie une CNSS cohérente (5% du brut)."""
        auth_session(client, "admin@a.ga")
        r = client.post("/api/calculer-bulletin", json={"salaire_base": 500000})
        data = r.get_json()
        # CNSS salarié = 5% de 500 000 = 25 000
        assert data["cnss_salarie"] == 25000

    def test_recherche_rapide_fonctionne(self, client):
        """L'autocomplétion doit renvoyer du JSON."""
        auth_session(client, "admin@a.ga")
        r = client.get("/api/recherche-rapide?q=ndo")
        assert r.status_code == 200
        assert r.is_json
        results = r.get_json()
        # Doit trouver le salarié NDONG du tenant A
        assert any("NDONG" in x.get("titre", "") for x in results)


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — PROTECTION CSRF DES FORMULAIRES
# ══════════════════════════════════════════════════════════════════════════════
class TestProtectionCSRF:
    def test_creation_salarie_sans_csrf_rejetee(self, client):
        """Un formulaire de modification SANS token doit être bloqué."""
        auth_session(client, "admin@a.ga")
        r = client.post("/salaries/nouveau",
                        data={"nom": "PIRATE", "prenom": "Sans Token"})
        assert r.status_code == 400  # CSRF protège bien


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — ISOLATION MULTI-TENANT (sécurité critique)
# ══════════════════════════════════════════════════════════════════════════════
class TestIsolationMultiTenant:
    def test_recherche_isolee_par_tenant(self, client):
        """L'admin du tenant A ne doit PAS voir les salariés du tenant B."""
        auth_session(client, "admin@a.ga")
        r = client.get("/api/recherche-rapide?q=obame")  # salarié du tenant B
        results = r.get_json()
        assert not any("OBAME" in x.get("titre", "") for x in results), \
            "Fuite de données entre tenants !"

    def test_admin_a_voit_son_salarie(self, client):
        """L'admin du tenant A voit bien son propre salarié."""
        auth_session(client, "admin@a.ga")
        r = client.get("/api/recherche-rapide?q=ndong")
        results = r.get_json()
        assert any("NDONG" in x.get("titre", "") for x in results)


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — PERMISSIONS (super-admin vs tenant)
# ══════════════════════════════════════════════════════════════════════════════
class TestPermissionsRoutes:
    def test_tenant_admin_ne_voit_pas_admin(self, client):
        """Un admin de tenant ne doit PAS accéder au panneau super-admin."""
        auth_session(client, "admin@a.ga")
        r = client.get("/admin", follow_redirects=False)
        assert r.status_code == 403

    def test_validation_mot_de_passe_inscription(self, client):
        """Un mot de passe faible doit être refusé à l'inscription."""
        page = client.get("/inscription")
        token = _extract_csrf(page.data)
        r = client.post("/inscription", data={
            "email": "nouveau@test.ga", "password": "123",  # trop court
            "denomination": "Test SARL", "nom": "Test", "prenom": "User",
            "csrf_token": token,
        }, follow_redirects=True)
        # Le mot de passe faible doit être signalé
        assert b"caract" in r.data.lower() or b"majuscule" in r.data.lower() \
            or b"chiffre" in r.data.lower()


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — SAUVEGARDE BASE DE DONNÉES
# ══════════════════════════════════════════════════════════════════════════════
class TestBackup:
    def test_page_backups_super_admin(self, client):
        """Le super-admin accède à la page des sauvegardes."""
        # Créer un super-admin dans la base de test
        with flask_app.app_context():
            from models import Utilisateur
            sa = Utilisateur(nom="SUPER", prenom="Admin", email="super@test.ga",
                             role="SUPER_ADMIN", actif=True, email_verifie=True)
            sa.set_password("MotDePasse1")
            db.session.add(sa)
            db.session.commit()
            sa_id = sa.id
        with client.session_transaction() as sess:
            sess["_user_id"] = str(sa_id); sess["_fresh"] = True
        r = client.get("/admin/backups")
        assert r.status_code == 200

    def test_page_backups_interdite_tenant(self, client):
        """Un admin de tenant ne peut PAS accéder aux sauvegardes."""
        auth_session(client, "admin@a.ga")
        r = client.get("/admin/backups", follow_redirects=False)
        assert r.status_code == 403

    def test_backup_sans_config_message_clair(self):
        """Sans config B2, run_backup renvoie un échec propre (pas un crash)."""
        import backup
        ok, message = backup.run_backup()
        # En environnement de test sans B2, doit échouer proprement
        assert isinstance(ok, bool)
        assert isinstance(message, str)
        assert len(message) > 0


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — MONITORING SENTRY
# ══════════════════════════════════════════════════════════════════════════════
class TestMonitoring:
    def test_scrub_filtre_mots_de_passe(self):
        """Les champs sensibles doivent être filtrés."""
        from monitoring import _scrub
        data = {"password": "secret", "email": "u@test.ga", "salaire": 500000}
        result = _scrub(data)
        assert result["password"] == "[FILTRÉ]"
        assert result["email"] == "u@test.ga"      # non sensible
        assert result["salaire"] == 500000          # donnée métier conservée

    def test_scrub_recursif_imbrique(self):
        """Le filtrage doit fonctionner en profondeur."""
        from monitoring import _scrub
        data = {"niveau1": {"token_api": "x", "nom": "NDONG"}}
        result = _scrub(data)
        assert result["niveau1"]["token_api"] == "[FILTRÉ]"
        assert result["niveau1"]["nom"] == "NDONG"

    def test_init_sentry_sans_dsn_desactive(self, monkeypatch):
        """Sans SENTRY_DSN, l'init retourne False sans erreur."""
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        from monitoring import init_sentry
        assert init_sentry() is False

    def test_set_user_context_ne_casse_pas(self):
        """set_user_context ne doit jamais lever d'exception."""
        from monitoring import set_user_context
        # Avec None, un objet vide, un objet bizarre — aucune exception attendue
        set_user_context(None)
        set_user_context(object())
        # Si on arrive ici, c'est bon
        assert True


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — JOURS FÉRIÉS GABONAIS
# ══════════════════════════════════════════════════════════════════════════════
class TestJoursFeries:
    def test_calcul_paques(self):
        """L'algorithme de Computus doit donner les bonnes dates de Pâques."""
        from jours_feries import _dimanche_paques
        from datetime import date
        assert _dimanche_paques(2024) == date(2024, 3, 31)
        assert _dimanche_paques(2025) == date(2025, 4, 20)
        assert _dimanche_paques(2026) == date(2026, 4, 5)

    def test_jours_fixes_presents(self):
        """Les jours fériés fixes gabonais doivent être détectés."""
        from jours_feries import est_jour_ferie
        from datetime import date
        assert est_jour_ferie(date(2026, 1, 1))    # Nouvel An
        assert est_jour_ferie(date(2026, 5, 1))    # Fête du Travail
        assert est_jour_ferie(date(2026, 8, 17))   # Indépendance
        assert est_jour_ferie(date(2026, 12, 25))  # Noël

    def test_jour_normal_non_ferie(self):
        """Un jour ordinaire ne doit pas être férié."""
        from jours_feries import est_jour_ferie
        from datetime import date
        assert not est_jour_ferie(date(2026, 6, 9))  # mardi ordinaire

    def test_type_jour_auto(self):
        """La détection automatique du type de jour."""
        from jours_feries import type_jour_auto
        from datetime import date
        assert type_jour_auto(date(2026, 8, 17)) == "FERIE"     # Indépendance
        assert type_jour_auto(date(2026, 6, 7))  == "DIMANCHE"  # dimanche
        assert type_jour_auto(date(2026, 6, 9))  == "NORMAL"    # mardi

    def test_api_jour_ferie(self, client):
        """L'API doit renvoyer le type de jour correct."""
        auth_session(client, "admin@a.ga")
        r = client.get("/api/jour-ferie?date=2026-08-17")
        assert r.status_code == 200
        data = r.get_json()
        assert data["type_jour"] == "FERIE"
        assert data["est_ferie"] is True

    def test_page_jours_feries(self, client):
        """La page calendrier doit s'afficher."""
        auth_session(client, "admin@a.ga")
        r = client.get("/jours-feries")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — PAGINATION
# ══════════════════════════════════════════════════════════════════════════════
class TestPagination:
    def test_salaries_pagine(self, client):
        """La liste des salariés accepte le paramètre page."""
        auth_session(client, "admin@a.ga")
        r = client.get("/salaries?page=1")
        assert r.status_code == 200

    def test_conges_pagine(self, client):
        """La liste des congés accepte le paramètre page."""
        auth_session(client, "admin@a.ga")
        r = client.get("/conges?page=1")
        assert r.status_code == 200

    def test_page_inexistante_pas_erreur(self, client):
        """Une page hors limites ne doit pas planter (error_out=False)."""
        auth_session(client, "admin@a.ga")
        r = client.get("/salaries?page=9999")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — MODULE PRESTATAIRES
# ══════════════════════════════════════════════════════════════════════════════
class TestPrestataires:
    def test_liste_prestataires(self, client):
        auth_session(client, "admin@a.ga")
        r = client.get("/prestataires")
        assert r.status_code == 200

    def test_page_nouveau_prestataire(self, client):
        auth_session(client, "admin@a.ga")
        r = client.get("/prestataires/nouveau")
        assert r.status_code == 200

    def test_etat_annuel(self, client):
        auth_session(client, "admin@a.ga")
        r = client.get("/prestataires/etat-annuel?annee=2026")
        assert r.status_code == 200

    def test_calcul_facture_correct(self):
        """Le calcul TVA + retenue à la source doit être exact."""
        from models import FacturePrestataire
        from datetime import date
        f = FacturePrestataire(tenant_id=1, prestataire_id=1, numero="T1",
                               date_facture=date.today(),
                               montant_ht=2000000, taux_tva=18, taux_retenue=9.5)
        f.calculer()
        assert f.montant_tva == 360000          # 18% de 2M
        assert f.montant_ttc == 2360000         # 2M + TVA
        assert f.montant_retenue == 190000      # 9.5% de 2M
        assert f.montant_net_a_payer == 2170000 # TTC - retenue

    def test_api_calculer_facture(self, client):
        auth_session(client, "admin@a.ga")
        r = client.post("/api/prestataire/calculer-facture",
                        json={"montant_ht": 1000000, "taux_tva": 18, "taux_retenue": 5})
        assert r.status_code == 200
        d = r.get_json()
        assert d["montant_tva"] == 180000
        assert d["montant_net_a_payer"] == 1130000  # 1.18M - 50k

    def test_creation_prestataire_isole_par_tenant(self, client):
        """Un prestataire créé par le tenant A n'est pas visible par le tenant B."""
        from models import Prestataire
        # Créer un prestataire pour le tenant A directement
        with flask_app.app_context():
            from models import Tenant
            ta = Tenant.query.filter_by(slug="entreprise-a").first()
            p = Prestataire(tenant_id=ta.id, code="PREA1",
                            raison_sociale="FOURNISSEUR A", categorie="FOURNISSEUR")
            db.session.add(p)
            db.session.commit()
            pid = p.id
        # Le tenant B ne doit pas y accéder
        auth_session(client, "admin@b.ga")
        r = client.get(f"/prestataires/{pid}", follow_redirects=False)
        assert r.status_code == 404  # introuvable pour le tenant B


# ══════════════════════════════════════════════════════════════════════════════
# TESTS — DOCUMENTS RH & EXPORT EN MASSE
# ══════════════════════════════════════════════════════════════════════════════
class TestDocumentsRH:
    def _setup_salarie(self):
        """Crée un salarié dans le tenant A et retourne son id."""
        with flask_app.app_context():
            from models import Tenant, Salarie
            from datetime import date
            ta = Tenant.query.filter_by(slug="entreprise-a").first()
            s = Salarie(tenant_id=ta.id, matricule="DOC01", nom="MBA", prenom="Paul",
                        date_embauche=date(2021, 1, 1), emploi="Chef de chantier",
                        sexe="M", statut="ACTIF")
            db.session.add(s)
            db.session.commit()
            return s.id

    def test_attestation_travail_pdf(self, client):
        sid = self._setup_salarie()
        auth_session(client, "admin@a.ga")
        r = client.get(f"/salaries/{sid}/document/attestation-travail")
        assert r.status_code == 200
        assert r.data[:4] == b"%PDF"

    def test_certificat_travail_pdf(self, client):
        sid = self._setup_salarie()
        auth_session(client, "admin@a.ga")
        r = client.get(f"/salaries/{sid}/document/certificat-travail")
        assert r.status_code == 200
        assert r.data[:4] == b"%PDF"

    def test_attestation_salaire_pdf(self, client):
        sid = self._setup_salarie()
        auth_session(client, "admin@a.ga")
        r = client.get(f"/salaries/{sid}/document/attestation-salaire")
        assert r.status_code == 200
        assert r.data[:4] == b"%PDF"

    def test_solde_tout_compte_pdf(self, client):
        sid = self._setup_salarie()
        auth_session(client, "admin@a.ga")
        r = client.get(f"/salaries/{sid}/document/solde-tout-compte")
        assert r.status_code == 200
        assert r.data[:4] == b"%PDF"

    def test_document_type_inconnu(self, client):
        sid = self._setup_salarie()
        auth_session(client, "admin@a.ga")
        r = client.get(f"/salaries/{sid}/document/type-bidon", follow_redirects=False)
        assert r.status_code == 302  # redirige avec message d'erreur

    def test_document_isole_par_tenant(self, client):
        """Le tenant B ne peut pas générer de document pour un salarié du tenant A."""
        sid = self._setup_salarie()  # salarié du tenant A
        auth_session(client, "admin@b.ga")
        r = client.get(f"/salaries/{sid}/document/attestation-travail",
                       follow_redirects=False)
        assert r.status_code == 404

    def test_export_zip_bulletins(self, client):
        """L'export ZIP doit produire une archive."""
        with flask_app.app_context():
            from models import Tenant, Salarie, PeriodePaie, BulletinPaie
            from datetime import date
            ta = Tenant.query.filter_by(slug="entreprise-a").first()
            s = Salarie(tenant_id=ta.id, matricule="ZIP01", nom="OYONO", prenom="Luc",
                        date_embauche=date(2022, 1, 1), statut="ACTIF")
            db.session.add(s); db.session.flush()
            p = PeriodePaie(tenant_id=ta.id, mois=5, annee=2026,
                            libelle_mois="MAI", statut="OUVERT")
            db.session.add(p); db.session.flush()
            b = BulletinPaie(tenant_id=ta.id, salarie_id=s.id, periode_id=p.id,
                             salaire_base=400000, salaire_brut=400000,
                             net_a_payer=340000, statut="VALIDE")
            db.session.add(b); db.session.commit()
            pid = p.id
        auth_session(client, "admin@a.ga")
        r = client.get(f"/bulletins/export-zip/{pid}")
        assert r.status_code == 200
        assert r.data[:2] == b"PK"  # signature ZIP
