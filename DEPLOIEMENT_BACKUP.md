# Guide — Sauvegarde automatique de la base vers Backblaze B2

Ce guide configure les sauvegardes quotidiennes automatiques de PaieGabon.
Suivre les étapes dans l'ordre. Compter 20 minutes la première fois.

---

## Étape 1 — Créer un compte Backblaze B2 (gratuit)

1. Aller sur https://www.backblaze.com/sign-up/cloud-storage
2. Créer un compte (le free tier offre 10 Go de stockage, largement suffisant)
3. Confirmer l'email

---

## Étape 2 — Créer un bucket

1. Dans le menu, aller dans **B2 Cloud Storage → Buckets → Create a Bucket**
2. Nom du bucket : `paiegalon-backups` (doit être unique, ajoute un suffixe si pris)
3. **Files in Bucket are : Private** ← important, jamais public
4. Cliquer **Create a Bucket**
5. Noter l'**Endpoint** affiché, par exemple : `s3.us-west-004.backblazeb2.com`
   (visible dans les détails du bucket, commence par `s3.`)

---

## Étape 3 — Créer une clé applicative

1. Aller dans **App Keys → Add a New Application Key**
2. Name : `paiegalon-backup`
3. Allow access to : **sélectionner uniquement le bucket** `paiegalon-backups`
4. Type of Access : **Read and Write**
5. Cliquer **Create New Key**
6. ⚠️ **Noter immédiatement** les deux valeurs (la clé ne sera affichée qu'une fois) :
   - `keyID`        → ce sera `B2_KEY_ID`
   - `applicationKey` → ce sera `B2_APP_KEY`

---

## Étape 4 — Configurer les variables sur Railway

1. Aller sur le service **web** de ton projet Railway → onglet **Variables**
2. Ajouter ces 4 variables :

| Variable      | Valeur                                      |
|---------------|---------------------------------------------|
| `B2_KEY_ID`   | le keyID de l'étape 3                        |
| `B2_APP_KEY`  | l'applicationKey de l'étape 3               |
| `B2_BUCKET`   | `paiegalon-backups`                         |
| `B2_ENDPOINT` | l'endpoint de l'étape 2 (`s3.us-west-...`) |

3. Optionnel — pour changer la durée de conservation (défaut 30 jours) :
   `BACKUP_RETENTION_DAYS` = `30`

`DATABASE_URL` existe déjà (Railway la fournit automatiquement).

---

## Étape 5 — Vérifier que ça marche (sauvegarde manuelle)

1. Pousser le code (le `nixpacks.toml` installe `pg_dump`, attendre le redéploiement)
2. Se connecter en super-admin → menu **Sauvegardes**
3. Si la config est bonne, le bandeau d'avertissement disparaît
4. Cliquer **💾 Lancer une sauvegarde**
5. Un message vert confirme : « Sauvegarde réussie : paiegalon/backup_… »
6. La sauvegarde apparaît dans la liste

Si erreur « pg_dump introuvable » : le `nixpacks.toml` n'a pas été pris en compte,
vérifier qu'il est bien à la racine du projet et redéployer.

---

## Étape 6 — Automatiser (sauvegarde quotidienne)

Railway permet de lancer un script à intervalle régulier via un **Cron Service**.

1. Dans ton projet Railway, cliquer **+ New → Empty Service** (ou dupliquer le service web)
2. Le nommer `backup-cron`
3. Lui donner accès aux **mêmes variables** que le service web
   (le plus simple : dans Variables, utiliser les *shared variables* du projet)
4. Dans **Settings → Deploy** :
   - **Start Command** : `python backup.py`
   - **Cron Schedule** : `0 2 * * *`  (tous les jours à 2h du matin UTC)
5. Sauvegarder

Le service se réveillera chaque nuit, fera la sauvegarde, et s'arrêtera.

> **Note** : le cron schedule `0 2 * * *` signifie « minute 0, heure 2, tous les
> jours ». Pour 2h du matin heure du Gabon (UTC+1), mettre `0 1 * * *`.

---

## Restauration (en cas de besoin)

Pour restaurer une sauvegarde :

1. Télécharger le fichier `.sql.gz` depuis l'interface Backblaze (ou via le menu Sauvegardes)
2. Le décompresser : `gunzip backup_AAAAMMJJ_HHMMSS.sql.gz`
3. Le réinjecter dans une base PostgreSQL :
   ```
   psql "$DATABASE_URL" < backup_AAAAMMJJ_HHMMSS.sql
   ```

⚠️ La restauration **écrase** les données actuelles (le dump contient des `DROP TABLE`).
Toujours restaurer vers une base de test d'abord en cas de doute.

---

## Récapitulatif des variables d'environnement

```
B2_KEY_ID                 (obligatoire)  Identifiant de clé Backblaze
B2_APP_KEY                (obligatoire)  Clé applicative Backblaze
B2_BUCKET                 (obligatoire)  Nom du bucket
B2_ENDPOINT               (obligatoire)  Endpoint S3 du bucket
BACKUP_RETENTION_DAYS     (optionnel)    Jours de conservation, défaut 30
BACKUP_PREFIX             (optionnel)    Dossier dans le bucket, défaut "paiegalon"
```
