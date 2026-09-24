# Découpage de `blueprints/tenant.py` — Runbook

> Objectif : passer d'un fichier monolithe (~10 700 lignes) à un **package** de
> ~12 fichiers thématiques, **sans changer une seule URL** et **sans régression**
> (les 490 tests restent verts à chaque étape).

## Principe : un package, UN SEUL blueprint

`tenant.py` devient le dossier `tenant/`. Tous les fichiers partagent le même
objet `bp` défini dans `__init__.py`. Comme le blueprint reste `"tenant"`, les
noms d'endpoints (`tenant.salaries`, `tenant.site_detail`…) ne changent pas →
`url_for(...)` et les templates continuent de marcher **sans modification**.

```
blueprints/tenant/
  __init__.py     ← bp = Blueprint("tenant", …) + constantes + helpers partagés
                    + hooks before_request, PUIS `from . import xxx` À LA FIN
  sites.py        ← from blueprints.tenant import bp  →  @bp.route(...)
  salaries.py
  ...
```

## ✅ Déjà fait (méthode validée sur le vrai code)

- **Étape 0** : `tenant.py` → `tenant/__init__.py`. Tests : 490 verts.
- **Étape 1** : extraction de `sites.py` (9 routes, /sites…). Tests : 490 verts,
  9 endpoints `tenant.site*` préservés.

Le dossier contient donc déjà `__init__.py` (~10 380 lignes) et `sites.py` (~375).

## Recette pour extraire un thème (à répéter)

Pour chaque thème (ex. `conges`) :

1. Repérer les routes du thème dans `__init__.py`
   `grep -nE '@bp\.route\("/conges' blueprints/tenant/__init__.py`
2. Vérifier ses dépendances (à mettre dans les imports du nouveau fichier) :
   - décorateurs (`@tenant_required`, `@can_edit`…)
   - models (`Conge`, `Acompte`…), helpers (`get_tenant`, `log_action`…)
   - flask (`render_template`, `request`, `flash`…), `current_user`, `db`
3. Créer `blueprints/tenant/conges.py` avec un en-tête d'imports + :
   `from blueprints.tenant import bp`
4. **Couper-coller** les fonctions de route depuis `__init__.py` vers le fichier.
5. Ajouter **à la toute fin** de `__init__.py` : `from blueprints.tenant import conges  # noqa`
6. **Lancer `python3 -m pytest tests/ -q`** → vert = OK, on committe. Rouge = on annule.

## Structure cible (thèmes)

| Fichier | Contenu | ~Routes |
|---|---|---|
| `__init__.py` | bp, constantes, helpers partagés, hooks, imports finaux | 0 |
| `_common.py` (optionnel) | les 21 helpers partagés | 0 |
| `sites.py` | ✅ sites, affectations | 9 |
| `salaries.py` | salariés, contrats, documents, modèles de contrat | ~25 |
| `bulletins.py` | bulletins, périodes, composants | ~21 |
| `journaliers.py` | journaliers, pointage, feuilles, avances | ~29 |
| `conges.py` | congés, acomptes | ~14 |
| `declarations.py` | CNSS, DAS + générateurs Excel | ~10 |
| `parametres.py` | paramètres, utilisateurs, rubriques, grille | ~18 |
| `cabinet.py` | cabinet, collaborateurs, production | ~8 |
| `paiements.py` | paiement, abonnement, webhooks, offres | ~10 |
| `divers.py` | dashboard, rapports, audit, export, profil, langue, recherche… | ~15 |

## 4 pièges à éviter

1. **Ordre d'import** : les `from . import xxx` sont TOUT EN BAS de `__init__.py`
   (après la définition de `bp` et des helpers).
2. **Pas d'import circulaire** : un sous-module importe `bp` depuis le package ;
   jamais l'inverse. Les helpers partagés vont dans `__init__.py`/`_common.py`.
3. **Ne pas renommer** les fonctions de route (l'endpoint = le nom de la fonction).
4. **Committer après chaque thème vert** : un point de retour à chaque étape.

## Ordre recommandé (du plus sûr au plus délicat)

1. ✅ `sites.py` (fait)
2. `cabinet.py`, `conges.py`, `declarations.py`
3. `journaliers.py`, `salaries.py`, `bulletins.py` (les gros)
4. `parametres.py`, `paiements.py`, `divers.py`

## Règle d'or

**Aucune étape sans lancer les 490 tests juste après.** Ce sont eux qui
garantissent zéro régression. À faire au calme, jamais en urgence.
