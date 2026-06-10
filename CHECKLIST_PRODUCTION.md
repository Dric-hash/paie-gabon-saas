# Checklist de mise en production — PaieGabon

> Document de préparation au lancement commercial.
> Statut au moment de l'audit : **l'application est techniquement prête**, avec quelques points de vigilance détaillés ci-dessous.

---

## 1. Sécurité — état des lieux

### ✅ Déjà en place (vérifié)

- **Cookies de session sécurisés** : `HttpOnly`, `SameSite=Lax`, et `Secure` activé automatiquement en production.
- **Protection CSRF** active sur tous les formulaires (Flask-WTF), avec exemption ciblée des seules API internes.
- **En-têtes de sécurité HTTP** : `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, et `Content-Security-Policy` complète.
- **HSTS** (force HTTPS pendant 1 an) — ajouté lors de cet audit, actif en production.
- **Hachage des mots de passe** (jamais stockés en clair).
- **Protection brute-force** sur le login (blocage après N échecs).
- **Rate limiting** global actif (Flask-Limiter).
- **Aucun secret en dur** dans le code : toutes les clés passent par les variables d'environnement. Les seules valeurs « en dur » trouvées sont des valeurs factices dans les tests.
- **`.env` protégé** par `.gitignore` (jamais committé).
- **Isolation multi-tenant** vérifiée par des tests automatisés (un client ne voit jamais les données d'un autre).

### ⚠️ À vérifier avant le lancement

- [ ] **`SECRET_KEY` de production** : générer une vraie clé unique et la définir dans les variables Railway. Ne jamais réutiliser celle de développement.
      Générer avec : `python -c "import secrets; print(secrets.token_hex(32))"`
- [ ] **Mot de passe super-admin** : changer le mot de passe par défaut après le premier déploiement.
- [ ] **Compte démo** : si un compte démo existe, s'assurer qu'il ne contient aucune donnée sensible réelle, ou le désactiver en production.
- [ ] **`debug=True`** : présent uniquement dans le bloc de lancement local (`if __name__ == "__main__"`). Gunicorn ne l'utilise pas en production — **aucune action requise**, mais ne jamais lancer l'app en production avec `python app.py`.

---

## 2. Sauvegardes — point critique

- [ ] **Activer les sauvegardes de la base de données.** C'est le point le plus important avant d'accueillir de vrais clients : une perte de données de paie serait catastrophique.
- Le module de sauvegarde vers Backblaze B2 (`backup.py`) est **codé et prêt**, mais en attente d'activation (il nécessitait le plan Railway Pro).
- **Deux options** :
  1. Activer le plan Railway Pro pour bénéficier des sauvegardes PostgreSQL automatiques de Railway.
  2. Activer le module B2 en renseignant les variables `B2_KEY_ID`, `B2_APP_KEY`, `B2_BUCKET`, `B2_ENDPOINT`.
- [ ] **Tester une restauration** au moins une fois. Une sauvegarde qu'on n'a jamais testée n'est pas une sauvegarde fiable.

---

## 3. Configuration & variables d'environnement

Variables à renseigner dans Railway (voir `.env.example` pour le détail) :

| Variable | Obligatoire | Rôle |
|----------|-------------|------|
| `SECRET_KEY` | ✅ Oui | Signature des sessions |
| `DATABASE_URL` | ✅ Oui (auto Railway) | Connexion PostgreSQL |
| `RAILWAY_ENVIRONMENT=production` | ✅ Oui | Active HTTPS, cookies sécurisés, HSTS |
| `SUPER_ADMIN_EMAIL` / `_PASSWORD` | ✅ Oui | Compte administrateur initial |
| `SENTRY_DSN` | Recommandé | Monitoring des erreurs (déjà actif) |
| `REDIS_URL` | Recommandé | Cache (accélère les KPIs) |
| `MAIL_USERNAME` / `_PASSWORD` | Recommandé | Envoi d'emails |
| `B2_*` | Si sauvegardes B2 | Sauvegardes externes |

- [ ] Vérifier que `RAILWAY_ENVIRONMENT=production` est bien défini — sinon les cookies sécurisés et le HSTS ne s'activent pas.
- [ ] Activer **Redis** (plugin Railway) pour le cache : sans lui, les indicateurs du tableau de bord sont recalculés à chaque visite.

---

## 4. Surveillance & fiabilité

### ✅ En place

- **Sentry** déployé et actif (remontée des erreurs en temps réel, avec filtrage des données personnelles).
- **Endpoint de santé** `/health` — ajouté lors de cet audit. Vérifie que l'app et la base répondent. Renvoie `200` si tout va bien, `503` sinon.
- **Pages d'erreur personnalisées** (403, 404, et 500 ajouté lors de cet audit, avec rollback de transaction).
- **Logs** configurés.

### ⚠️ À configurer

- [ ] **Alertes Sentry** : configurer les règles de notification (email) pour être prévenu des erreurs. La remontée fonctionne, mais les alertes email étaient à activer.
- [ ] **Surveillance uptime** : brancher un service de monitoring (ex. UptimeRobot) sur `/health` pour être alerté si le site tombe.

---

## 5. Performances

### ✅ Optimisé

- Requêtes N+1 corrigées sur les pages les plus lourdes (`/conges` : −80 %, `/salaries` : −60 %).
- Pagination sur toutes les longues listes.
- Compression Gzip active.
- Cache des indicateurs (si Redis activé).
- Gunicorn configuré avec plusieurs workers et threads.

### À surveiller

- [ ] Observer les temps de réponse réels une fois en production avec de vrais volumes.
- [ ] Le tableau de bord fait plusieurs requêtes (calcul des alertes) — surveiller s'il ralentit avec beaucoup de données ; le cache Redis atténue ce point.

---

## 6. Conformité & légal (Gabon)

> Point souvent négligé mais important pour un logiciel qui traite des données de paie (donc des données personnelles).

- [ ] **Conditions Générales d'Utilisation (CGU)** : rédiger et faire accepter à l'inscription.
- [ ] **Politique de confidentialité** : expliquer quelles données sont collectées, comment elles sont protégées, et la durée de conservation.
- [ ] **Protection des données personnelles** : le Gabon dispose d'un cadre sur la protection des données (CNPDCP). Vérifier les obligations applicables à un traitement de données de paie (déclaration éventuelle, droits des personnes).
- [ ] **Mentions légales** : identité de l'éditeur (Ameriack I.T. Solutions), contact, hébergeur.
- [ ] **Consentement & droit à l'effacement** : prévoir une procédure si un client résilie (export puis suppression de ses données).

*Note : je ne suis pas juriste — fais valider ces points par un professionnel du droit gabonais avant le lancement commercial.*

---

## 7. Avant d'ouvrir aux premiers clients

- [ ] Faire un **test complet de bout en bout** avec un compte client réel : inscription → création de salariés → bulletins → clôture → export.
- [ ] Vérifier les **emails transactionnels** (confirmation de compte, réinitialisation de mot de passe) en conditions réelles.
- [ ] Préparer un **canal de support** (email, WhatsApp) et le rendre visible dans l'app.
- [ ] Définir une **procédure en cas d'incident** : qui est prévenu, comment, dans quel délai.
- [ ] **Sauvegarde testée** (voir section 2) — à ne pas négliger.

---

## Résumé : les 5 actions prioritaires

1. **Activer et tester les sauvegardes** de la base de données.
2. Définir une **`SECRET_KEY` de production** unique et changer le mot de passe admin.
3. Vérifier `RAILWAY_ENVIRONMENT=production` et activer **Redis**.
4. Configurer les **alertes Sentry** + une surveillance uptime sur `/health`.
5. Préparer les **documents légaux** (CGU, confidentialité, mentions légales).

Le reste (sécurité applicative, performances, monitoring technique) est déjà en place.
