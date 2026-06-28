"""
tests/test_securite_audit.py — Tests de non-régression des correctifs de l'audit
de sécurité (juin 2026).

Couvre :
    C1 — un TENANT_ADMIN ne peut pas s'attribuer le rôle SUPER_ADMIN
    C1 — un rôle inconnu est rejeté
    M1 — un utilisateur non confirmé est bloqué sur les pages tenant
    M5 — un acompte ne peut référencer un salarié d'un autre tenant
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-securite-audit")
os.environ.setdefault("WTF_CSRF_ENABLED", "True")

import re
import pytest
from datetime import date

from app import app as flask_app
from models import (db, Plan, Tenant, Utilisateur, CategorieEmploi,
                    Salarie, Contrat, Acompte)


@pytest.fixture
def app():
    flask_app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "SERVER_NAME": "localhost",
    })
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        _seed()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _seed():
    plan = Plan(code="PRO", nom="Pro", prix_mensuel=35000,
                max_salaries=50, max_utilisateurs=10, actif=True)
    db.session.add(plan); db.session.flush()

    t_a = Tenant(slug="ent-a", denomination="ENT A", sigle="EA", activite="BTP",
                 ville="Libreville", pays="Gabon", plan_id=plan.id,
                 statut="ACTIF", token_api="tok_a")
    t_b = Tenant(slug="ent-b", denomination="ENT B", sigle="EB", activite="COMMERCE",
                 ville="Port-Gentil", pays="Gabon", plan_id=plan.id,
                 statut="ACTIF", token_api="tok_b")
    db.session.add_all([t_a, t_b]); db.session.flush()

    admin_a = Utilisateur(nom="ADMIN", prenom="Alice", email="admin@a.ga",
                          role="TENANT_ADMIN", tenant_id=t_a.id,
                          actif=True, email_verifie=True)
    admin_a.set_password("MotDePasse1")
    # Utilisateur non confirmé (pour M1)
    pasconf = Utilisateur(nom="NEW", prenom="Noé", email="new@a.ga",
                          role="RH", tenant_id=t_a.id,
                          actif=True, email_verifie=False)
    pasconf.set_password("MotDePasse1")
    db.session.add_all([admin_a, pasconf]); db.session.flush()

    sal_b = Salarie(tenant_id=t_b.id, matricule="EB001", nom="OBAME", prenom="Marie",
                    date_embauche=date(2023, 6, 1), emploi="Vendeuse",
                    situation_matrimoniale="CELIBATAIRE", nb_enfants=0, statut="ACTIF")
    db.session.add(sal_b)
    db.session.commit()


def _csrf(html_bytes):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                  html_bytes.decode("utf-8", errors="ignore"))
    return m.group(1) if m else ""


def _login(client, email, pw="MotDePasse1"):
    page = client.get("/login")
    return client.post("/login", data={
        "email": email, "password": pw, "csrf_token": _csrf(page.data),
    }, follow_redirects=False)


def _uid(email):
    with flask_app.app_context():
        return Utilisateur.query.filter_by(email=email).first().id


# ══════════════════════════════════════════════════════════════════════════════
# C1 — Escalade de privilèges via le champ `role`
# ══════════════════════════════════════════════════════════════════════════════
class TestEscaladePrivileges:
    def test_creation_super_admin_refusee(self, client):
        """Un TENANT_ADMIN qui poste role=SUPER_ADMIN ne crée PAS de super-admin."""
        _login(client, "admin@a.ga")
        page = client.get("/utilisateurs/nouveau")
        token = _csrf(page.data)
        client.post("/utilisateurs/nouveau", data={
            "email": "pirate@a.ga", "nom": "PIRATE", "prenom": "P",
            "password": "MotDePasse1", "role": "SUPER_ADMIN",
            "csrf_token": token,
        }, follow_redirects=True)
        with flask_app.app_context():
            u = Utilisateur.query.filter_by(email="pirate@a.ga").first()
            # Soit l'utilisateur n'est pas créé, soit il n'est pas SUPER_ADMIN.
            assert u is None or u.role != "SUPER_ADMIN"
            assert Utilisateur.query.filter_by(role="SUPER_ADMIN").count() == 0

    def test_modification_vers_super_admin_refusee(self, client):
        """Un TENANT_ADMIN ne peut pas promouvoir un user existant en SUPER_ADMIN."""
        _login(client, "admin@a.ga")
        target = _uid("new@a.ga")
        page = client.get(f"/utilisateurs/{target}/modifier")
        token = _csrf(page.data)
        client.post(f"/utilisateurs/{target}/modifier", data={
            "nom": "NEW", "prenom": "Noé", "role": "SUPER_ADMIN",
            "csrf_token": token,
        }, follow_redirects=True)
        with flask_app.app_context():
            u = Utilisateur.query.get(target)
            assert u.role != "SUPER_ADMIN"

    def test_role_inconnu_refuse(self, client):
        """Un rôle hors liste blanche est rejeté."""
        _login(client, "admin@a.ga")
        page = client.get("/utilisateurs/nouveau")
        token = _csrf(page.data)
        client.post("/utilisateurs/nouveau", data={
            "email": "bizarre@a.ga", "nom": "X", "prenom": "Y",
            "password": "MotDePasse1", "role": "ROOT",
            "csrf_token": token,
        }, follow_redirects=True)
        with flask_app.app_context():
            assert Utilisateur.query.filter_by(email="bizarre@a.ga").first() is None


# ══════════════════════════════════════════════════════════════════════════════
# M1 — Vérification d'email appliquée
# ══════════════════════════════════════════════════════════════════════════════
class TestVerificationEmail:
    def test_non_confirme_bloque(self, client):
        """Un utilisateur non confirmé est bloqué (403) sur une page tenant."""
        _login(client, "new@a.ga")
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 403
        assert b"Confirmez" in r.data or b"confirm" in r.data.lower()

    def test_confirme_autorise(self, client):
        """Un utilisateur confirmé accède normalement au dashboard."""
        _login(client, "admin@a.ga")
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# M5 — IDOR sur la création d'acompte
# ══════════════════════════════════════════════════════════════════════════════
class TestIdorAcompte:
    def test_acompte_salarie_autre_tenant_refuse(self, client):
        """
        L'admin du tenant A ne peut pas créer un acompte référençant un salarié
        du tenant B (IDOR). Aucun acompte ne doit être créé.
        """
        _login(client, "admin@a.ga")
        with flask_app.app_context():
            sal_b_id = Salarie.query.filter_by(matricule="EB001").first().id
        page = client.get("/acomptes/nouveau")
        token = _csrf(page.data)
        client.post("/acomptes/nouveau", data={
            "salarie_id": sal_b_id, "montant": "10000",
            "date_acompte": "2026-06-15", "mois": "6", "annee": "2026",
            "motif": "test idor", "csrf_token": token,
        }, follow_redirects=True)
        with flask_app.app_context():
            assert Acompte.query.filter_by(salarie_id=sal_b_id).count() == 0


# ══════════════════════════════════════════════════════════════════════════════
# F7 — Invalidation des sessions au changement de mot de passe
# ══════════════════════════════════════════════════════════════════════════════
class TestInvalidationSession:
    def test_changement_mdp_invalide_session(self, client):
        """Après un changement de mot de passe, l'ancienne session est rejetée."""
        _login(client, "admin@a.ga")
        # La session est valide au départ.
        assert client.get("/dashboard", follow_redirects=False).status_code == 200
        # Un changement de mot de passe fait tourner le jeton de session.
        with flask_app.app_context():
            u = Utilisateur.query.filter_by(email="admin@a.ga").first()
            u.set_password("UnAutreMdp1")
            db.session.commit()
        # L'ancienne session (jeton périmé) n'est plus authentifiée → redirection.
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 302


# ══════════════════════════════════════════════════════════════════════════════
# F7 — Invalidation des sessions au changement de mot de passe
# ══════════════════════════════════════════════════════════════════════════════
class TestInvalidationSession:
    def test_load_user_rejette_jeton_perime(self, client):
        """
        Cœur de F7 : load_user n'authentifie une session que si le jeton qu'elle
        porte correspond au jeton courant. Après rotation (changement de mot de
        passe), l'ancienne identité de session est rejetée, la nouvelle acceptée.
        """
        from app import load_user
        with flask_app.app_context():
            u = Utilisateur.query.filter_by(email="admin@a.ga").first()
            ancienne_identite = u.get_id()           # "id.jeton1"
            assert load_user(ancienne_identite) is not None

            u.set_password("NouveauMdp9")            # rotation du jeton
            db.session.commit()

            nouvelle_identite = u.get_id()           # "id.jeton2"
            # L'ancienne session est invalidée, la nouvelle est valide.
            assert load_user(ancienne_identite) is None
            assert load_user(nouvelle_identite) is not None


# ══════════════════════════════════════════════════════════════════════════════
# M4 — Stockage haché des secrets (token API, client_secret OAuth)
# ══════════════════════════════════════════════════════════════════════════════
class TestSecretsHaches:
    def test_token_api_stocke_hache(self, client):
        """generate_token() stocke un hash + un préfixe, jamais le secret en clair."""
        from models import hash_secret
        with flask_app.app_context():
            t = Tenant.query.filter_by(slug="ent-a").first()
            raw = t.generate_token()
            db.session.commit()
            assert t.token_api_hash == hash_secret(raw)
            assert raw not in (t.token_api or "")      # le clair n'est pas stocké
            assert t.token_api_hash != raw             # ce n'est pas le clair

    def test_auth_api_fonctionne_avec_token_hache(self, client):
        """Un appel API authentifié par le token en clair est accepté (lookup par hash)."""
        with flask_app.app_context():
            t = Tenant.query.filter_by(slug="ent-a").first()
            raw = t.generate_token()
            db.session.commit()
        r = client.get("/api/v1/me", headers={"X-API-Key": raw})
        assert r.status_code == 200

    def test_auth_api_refuse_mauvais_token(self, client):
        """Un token invalide est rejeté."""
        r = client.get("/api/v1/me", headers={"X-API-Key": "mauvais-token-xyz"})
        assert r.status_code in (401, 403)
