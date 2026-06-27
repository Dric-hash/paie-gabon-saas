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

### Tests
- **`tests/test_securite_audit.py`** (nouveau) — 6 tests verrouillant C1, M1 et M5.

---

## ⏸️ Volontairement différé (nécessite une fenêtre de migration / décision)

Ces points sont réels mais **risqués à appliquer à l'aveugle sur la prod** sans
migration de données ou validation manuelle. À planifier séparément :

- **M4 — Hachage de `Tenant.token_api` et `OAuthClient.client_secret`.**
  Aujourd'hui stockés en clair. Les hacher casserait les intégrations API
  existantes sans migration (re-hash des valeurs en place + bascule du lookup
  vers une comparaison de hash + affichage du token uniquement à la génération).
  *Note : le flux OAuth est actuellement du code mort — `api_oauth_token` n'a pas
  de décorateur `@bp.route` — donc hacher `client_secret` seul serait sans risque
  immédiat et constitue un bon premier pas.*

- **F1 — Tokens OAuth en mémoire → Redis.** Le dict `_oauth_tokens` ne fonctionne
  pas avec plusieurs workers Gunicorn (et se vide au redéploiement). À migrer vers
  Redis (déjà dans la stack). Sans impact tant que le endpoint OAuth reste non branché.

- **F2 — CSP : retrait de `'unsafe-inline'` + passage à une CSP à nonce.** Suppose
  d'avoir d'abord migré tous les handlers inline (`onclick`, `oninput`…) vers des
  écouteurs JS. Chantier front à part entière.

- **F7 — Invalidation des sessions au changement de mot de passe.** Nécessite un
  « security stamp » dans `get_id()` de Flask-Login ; déconnecte les sessions
  actives au reset (comportement souhaité mais à tester).

- **F9 — 2FA optionnelle pour les `TENANT_ADMIN`** (aujourd'hui réservée au super-admin).

- **F10 — `tenant.api_cache_clear` exempté de CSRF.** Impact très faible (simple
  invalidation de cache) ; le retirer de l'exemption obligerait à ajouter un token
  CSRF à l'appel JS correspondant.

---

## Recommandation de déploiement

1. Déployer ce lot (C1 + M1/M2/M3/M5 + durcissements) — sûr, testé, sans migration.
2. Planifier ensuite M4 + F1 (sécurité des secrets/tokens) avec une vraie fenêtre.
3. Garder F2/F7/F9 pour une itération front/UX dédiée.
