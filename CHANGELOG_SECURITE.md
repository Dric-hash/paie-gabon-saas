# Changelog — Correctifs de sécurité (audit juin 2026)

Toutes les modifications ci-dessous ont été validées : **422 tests passent**
(416 existants + 6 nouveaux tests de non-régression sécurité), l'application
démarre et tous les fichiers modifiés compilent.

---

## ✅ Corrigé

### 🔴 C1 — Escalade de privilèges via le champ `role`
**`blueprints/tenant.py`** — ajout d'une liste blanche `ROLES_TENANT_AUTORISES`
appliquée dans `utilisateur_nouveau` et `utilisateur_modifier`. Un `TENANT_ADMIN`
ne peut plus attribuer `SUPER_ADMIN` ni un rôle inconnu (un POST forgé est rejeté).

### 🟠 M1 — Vérification d'email désormais appliquée
**`blueprints/tenant.py`** (`@bp.before_request _exiger_email_confirme`)
+ **`templates/auth/email_non_confirme.html`** (nouveau). Tant que `email_verifie`
est faux, l'utilisateur tenant voit une page de confirmation (403) au lieu d'accéder
à l'app. Les routes `auth` (logout, confirmation, renvoi) et les appels JSON internes
restent accessibles pour éviter tout blocage.

### 🟠 M2 — Rate-limiting de l'API REST
**`app.py`** — `limiter.limit("100/minute")` appliqué à tout le blueprint `api_v1`
(configurable via `API_RATE_LIMIT_STR`). L'API étant exemptée de CSRF, c'est la
barrière principale contre le brute-force et l'abus.

### 🟠 M3 — XSS inline (handlers `onclick`)
**`templates/tenant/prestataire_detail.html`** — toutes les valeurs dynamiques de
`ouvrirEditAvance(...)` (dont `reference`, texte libre) passent désormais par
`|tojson`, ce qui produit des littéraux JS correctement échappés.

### 🟠 M5 — IDOR sur la création d'acompte
**`blueprints/tenant.py`** (`acompte_nouveau`) — le `salarie_id` posté est validé
comme appartenant au tenant **avant** toute création. Empêche de créer un acompte
référençant un salarié d'un autre tenant et de fuiter son nom.

### 🟡 Durcissements
- **F3** — `requests==2.32.3` épinglé (`requirements.txt`).
- **F4** — impersonation super-admin désormais journalisée (`log_action("IMPERSONATE", …)`, `blueprints/admin.py`).
- **F5** — `app.run(debug=False)` (`app.py`).
- **F6** — login : hash factice à temps constant quand le compte n'existe pas (anti-énumération par timing, `blueprints/auth.py`).
- **F8** — garde anti zip-bomb : import Excel limité à 5 Mo (`blueprints/tenant.py` + `blueprints/admin.py`).

### 🟡 F7 — Invalidation des sessions au changement de mot de passe
**`models.py`** (colonne `session_token`, `get_id()` = `id.jeton`, rotation dans
`set_password`) + **`app.py`** (`load_user` vérifie le jeton et tolère les
sessions héritées ; migration `ALTER TABLE … session_token`). Après un reset ou
un changement de mot de passe, le jeton tourne et **toutes les sessions ouvertes
sont invalidées** (anti-détournement après vol de mot de passe). *Note : au
premier déploiement, les sessions existantes restent valides jusqu'à expiration
par inactivité, puis basculent au nouveau format.*

### 🟠 M4 — Secrets stockés hachés (token API + client_secret OAuth)
**`models.py`** (`hash_secret`/`verifier_secret`, colonne `token_api_hash`,
`generate_token()` renvoie le token en clair une fois et ne stocke qu'un hash
SHA-256 + un préfixe lisible) + **`api_rest.py`** (lookup par hash) +
**`blueprints/api_v1.py`** (vérification du `client_secret` par hash, création
qui n'affiche le secret qu'une fois) + **`app.py`** (migration `token_api_hash`
et **backfill Python idempotent**). Le token API et le `client_secret` ne sont
**plus stockés en clair**. La migration hache les tokens existants en place : les
intégrations API en cours **continuent de fonctionner** (vérifié : le token
d'origine est retrouvé par hash, et le backfill est idempotent). Nouvelle route
tenant `regenerer_token_api` + boutons UI mis à jour (token affiché une seule fois).

### 🟡 F10 — `api_cache_clear` repassé sous protection CSRF
**`app.py`** — endpoint retiré de la liste d'exemptions CSRF (le front envoyait
déjà un en-tête `X-CSRFToken`, aucun changement JS nécessaire).

### 🟡 F1 — Flux OAuth2 branché + store de tokens Redis
**`blueprints/api_v1.py`** (route `/oauth/token` désormais branchée — elle était
définie sans décorateur, donc inactive) + **`core.py`** (`oauth_token_store/get/
delete` : persistance Redis avec TTL natif, partagée entre workers Gunicorn, repli
mémoire si Redis absent) + **`api_rest.py`** (lookup via le store). Le dict en
mémoire process (inopérant en multi-workers) est supprimé. Le endpoint est protégé
par le rate-limit de blueprint (M2) et vérifie le `client_secret` haché (M4). Flux
testé de bout en bout (obtention → usage Bearer → révocation).

### 🟡 F9 — 2FA optionnelle pour les utilisateurs tenant
**`models.py`** (colonne `twofa_active`) + **`app.py`** (migration) +
**`blueprints/auth.py`** (déclenchement du code email généralisé aux utilisateurs
ayant activé l'option, en plus du super-admin) + **`blueprints/tenant.py`** (route
`basculer_2fa`) + **`templates/tenant/parametres.html`** (carte « Sécurité de mon
compte »). Réutilise la machinerie OTP email existante : un utilisateur 2FA-activé
doit saisir un code à 6 chiffres reçu par email à chaque connexion.

### 🟡 F2 — Phase 1 : outillage pour une CSP stricte
**`app.py`** (nonce par requête `{{ csp_nonce }}`, **CSP Report-Only opt-in** via
`CSP_REPORT_ONLY=1`, endpoint `/csp-report`). La CSP imposée reste inchangée (aucun
risque de casse) ; le mode Report-Only collecte les violations avant de basculer en
politique stricte. La migration des 341 handlers inline est documentée dans
**`GUIDE_MIGRATION_CSP.md`** (chantier front à mener écran par écran).

### Tests
- **`tests/test_securite_audit.py`** — 18 tests verrouillant C1, M1, M5, M4, F7, F1,
  F9 et F2. **Suite globale : 434 tests verts.**

---

## ⏸️ Reste à faire (chantier dédié)

- **F2 — Phase 2 : migration des handlers inline + bascule CSP en enforce.**
  L'outillage est livré (nonce, Report-Only opt-in, endpoint de report). Reste à
  migrer les ≈ 341 handlers `on*=` vers des écouteurs JS nonce'd, écran par écran,
  puis retirer `'unsafe-inline'` de la CSP imposée. Procédure détaillée dans
  **`GUIDE_MIGRATION_CSP.md`**. Sans urgence : le principal vecteur XSS en texte
  libre est déjà neutralisé (M3) et le reste de la CSP est verrouillé.

---

## Recommandation de déploiement

1. Déployer ce lot complet (C1 + M1→M5 + F1, F3→F10) — **434 tests verts**,
   migration des tokens vérifiée idempotente et sans rupture des clients API.
2. Après déploiement : activer `CSP_REPORT_ONLY=1` en staging pour amorcer la
   phase 2 de F2 (observer les violations avant de durcir).
3. La 2FA tenant (F9) est livrée **désactivée par défaut** — chaque utilisateur
   l'active depuis Paramètres → Utilisateurs → « Sécurité de mon compte ». Elle
   nécessite que l'email serveur (`MAIL_PASSWORD`) soit configuré.
