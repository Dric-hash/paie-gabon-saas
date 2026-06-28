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

### Tests
- **`tests/test_securite_audit.py`** (nouveau) — 10 tests verrouillant C1, M1, M5, F7 et M4.

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

---

## ⏸️ Volontairement différé (nécessite une fenêtre de migration / décision)

Ces points sont réels mais représentent des **chantiers à part entière** (refonte
front, nouvelle fonctionnalité) ou sont sans impact tant qu'une fonction reste
inactive. À planifier séparément :

- **F1 — Tokens OAuth en mémoire → Redis.** Le dict `_oauth_tokens` ne fonctionne
  pas avec plusieurs workers Gunicorn (et se vide au redéploiement). À migrer vers
  Redis (déjà dans la stack). **Sans impact aujourd'hui** : le endpoint OAuth
  `api_oauth_token` n'a pas de décorateur `@bp.route` — il n'est pas branché (le
  `client_secret` est néanmoins déjà haché, cf. M4, prêt pour le jour où il le sera).

- **F2 — CSP : retrait de `'unsafe-inline'` + passage à une CSP à nonce.** Suppose
  d'avoir d'abord migré tous les handlers inline (`onclick`, `oninput`…) vers des
  écouteurs JS. Chantier front à part entière. *(Le principal handler à risque a
  déjà été neutralisé via `|tojson`, cf. M3.)*

- **F9 — 2FA optionnelle pour les `TENANT_ADMIN`** (aujourd'hui réservée au
  super-admin). Fonctionnalité à concevoir (TOTP ou OTP email + écrans associés).

---

## Recommandation de déploiement

1. Déployer ce lot complet (C1 + M1/M2/M3/M4/M5 + F3/F4/F5/F6/F7/F8/F10) — testé
   (426 tests verts), migration de tokens vérifiée idempotente et sans rupture des
   clients API en place.
2. Garder F1/F2/F9 pour des itérations dédiées (refonte front, fonctionnalité 2FA).
3. Garder F2/F7/F9 pour une itération front/UX dédiée.
