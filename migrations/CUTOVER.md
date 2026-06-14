# Migrations de base de données (Alembic / Flask-Migrate)

Le projet est désormais équipé d'Alembic via Flask-Migrate. Une **migration de
référence** (baseline) décrivant le schéma complet actuel se trouve dans
`migrations/versions/`.

## État actuel du démarrage

Pour ne pas perturber la base de production existante, l'application démarre
toujours avec `db.create_all()` + `run_migrations()` (ALTER idempotents). Alembic
est **disponible mais pas encore le mécanisme de démarrage** : il sert à versionner
proprement les **futurs** changements de schéma.

## Bascule en production (à faire une seule fois)

La base de production possède déjà toutes les tables (créées par `create_all`).
Il ne faut donc **pas** rejouer la baseline — il faut juste indiquer à Alembic
qu'elle est déjà appliquée :

```bash
# Sur l'environnement de production, une seule fois :
flask db stamp head
```

`stamp head` marque la migration baseline comme « déjà appliquée » sans exécuter
son contenu. À partir de là, Alembic et la base sont synchronisés.

## Cycle de travail pour les changements futurs

```bash
# 1. Modifier les modèles dans models.py
# 2. Générer la migration (comparer les modèles à une base vide pour la 1re fois,
#    ensuite à la base courante) :
SKIP_BOOTSTRAP=1 flask db migrate -m "description du changement"

# 3. Relire le fichier généré dans migrations/versions/ (toujours vérifier !)
# 4. Appliquer :
flask db upgrade
```

> `SKIP_BOOTSTRAP=1` désactive le `create_all()` de démarrage le temps de la
> commande, pour qu'Alembic raisonne uniquement sur les migrations.

## Transition recommandée (plus tard)

Quand tu seras à l'aise avec Alembic, tu pourras retirer les `ALTER TABLE` de
`run_migrations()` au profit des migrations versionnées, et faire du démarrage un
simple `flask db upgrade`. À faire progressivement, après avoir vérifié que la
bascule `stamp head` est bien passée en production.
