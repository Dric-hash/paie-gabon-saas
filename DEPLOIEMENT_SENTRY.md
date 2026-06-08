# Guide — Monitoring des erreurs avec Sentry

Ce guide active les alertes automatiques en cas d'erreur en production.
Dès qu'un client rencontre un bug, tu reçois un email avec la stack trace
complète et le contexte (quel tenant, quelle action, quelle ligne). Compter
10 minutes.

Le free tier de Sentry (5 000 erreurs/mois) suffit largement et ne nécessite
aucun abonnement payant.

---

## Étape 1 — Créer un compte Sentry

1. Aller sur https://sentry.io/signup/
2. Créer un compte (gratuit, pas de carte bancaire requise)
3. Confirmer l'email

---

## Étape 2 — Créer un projet

1. Au premier lancement, Sentry propose de créer un projet
2. Choisir la plateforme : **Flask** (chercher dans la liste Python)
3. Nom du projet : `paiegalon`
4. Choisir une équipe d'alerte (ton compte par défaut)
5. Cliquer **Create Project**

---

## Étape 3 — Récupérer le DSN

Après création, Sentry affiche un bloc de code d'exemple contenant une ligne
comme :

```
dsn="https://abc123...@o456789.ingest.sentry.io/1234567"
```

Copier **uniquement la valeur entre guillemets** (l'URL complète qui commence
par `https://` et finit par une série de chiffres). C'est ton `SENTRY_DSN`.

Si tu rates cet écran : projet → **Settings → Client Keys (DSN)** → copier le DSN.

---

## Étape 4 — Configurer Railway

1. Service **web** de ton projet Railway → onglet **Variables**
2. Ajouter :

| Variable             | Valeur                                  |
|----------------------|-----------------------------------------|
| `SENTRY_DSN`         | le DSN copié à l'étape 3                |
| `SENTRY_ENVIRONMENT` | `production`                            |

3. (Optionnel) Pour suivre quelle version a introduit un bug :
   `APP_VERSION` = `1.0.0` (à incrémenter à chaque déploiement)

4. Railway redéploie automatiquement.

---

## Étape 5 — Vérifier que ça marche

1. Une fois redéployé, se connecter en **super-admin**
2. Visiter cette URL dans le navigateur :
   `https://ameriack-paie.up.railway.app/admin/monitoring/test`
3. Une page d'erreur 500 s'affiche (c'est **normal** et **volontaire**)
4. Dans les secondes qui suivent, tu reçois un **email de Sentry** avec
   le détail de l'erreur, et elle apparaît dans le tableau de bord Sentry
5. ✅ Si tu reçois l'alerte, le monitoring fonctionne

Cette route de test ne sert qu'à ça. Tu peux l'ignorer ensuite.

---

## Ce qui est protégé (confidentialité)

Avant chaque envoi à Sentry, les données sensibles sont **automatiquement
supprimées** et remplacées par `[FILTRÉ]` :

- mots de passe (et leurs hash)
- tokens (API, reset, CSRF, confirmation email)
- cookies et en-têtes d'authentification
- clés secrètes

Les **données de paie** (salaires, montants) ne sont jamais envoyées
intentionnellement — Sentry ne reçoit que le contexte technique nécessaire
au débogage : l'URL, le type d'erreur, la stack trace, l'id du tenant et le
rôle de l'utilisateur (pas son email ni son nom).

---

## Au quotidien

- **Tableau de bord Sentry** : liste toutes les erreurs, groupées par type,
  avec leur fréquence et le nombre de clients touchés.
- **Alertes email** : tu es prévenu dès la première occurrence d'une nouvelle
  erreur.
- **Contexte** : chaque erreur indique le tenant concerné (tag `tenant_id`),
  ce qui permet de reproduire le problème.

Pour réduire le bruit, tu peux dans Sentry configurer les alertes pour ne
recevoir un email que sur les **nouvelles** erreurs (pas les répétitions).

---

## Récapitulatif des variables d'environnement

```
SENTRY_DSN            (obligatoire pour activer)  Clé projet Sentry
SENTRY_ENVIRONMENT    (recommandé)                Nom de l'environnement
SENTRY_TRACES_RATE    (optionnel)                 Échantillonnage perf 0–1, défaut 0
APP_VERSION           (optionnel)                 Version pour suivre les régressions
```

Sans `SENTRY_DSN`, le monitoring est simplement désactivé — l'application
fonctionne normalement, sans aucune erreur.
