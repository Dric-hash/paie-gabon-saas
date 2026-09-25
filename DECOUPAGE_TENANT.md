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
- **Étape 1** : `sites.py` (9 routes). ✅ 490 verts.
- **Étape 2** : `cabinet.py` (8 routes, 2 blocs non contigus + constante `ROLES_TENANT_AUTORISES`). ✅ 490 verts.
- **Étape 3** : `conges.py` (14 routes, 2 blocs). ✅ après imports sqlalchemy.
- **Étape 4** : `declarations.py` (10 routes + 4 helpers, 4 blocs). ✅ (après `plan_required`).
- **Étape 5** : `journaliers.py` (29 routes, 5 blocs, ~2066 lignes). ✅ du premier coup.
  Leçon : certains helpers sont **partagés** (`_pointages_mois_contexte`,
  `_resoudre_mois_annee`, `_pd`) → ils RESTENT dans `__init__`/`core` et sont
  IMPORTÉS ; seuls les helpers journaliers-only voyagent avec les routes.
  → Toujours vérifier si un helper est utilisé HORS du thème avant de le déplacer.
- **Étape 6** : `salaries.py` (28 routes, 10 blocs dispersés). ✅ après ajout de
  `logger` (défini dans `__init__`, L47) et `parse_date` (core). **Noms module
  fréquents à ne pas oublier** dans les prochains thèmes : `logger`, `parse_date`.
- **Étape 7** : `bulletins.py` (41 routes bulletins/périodes/composants/API, 7 blocs). ✅
  après ajout de `calculer_bulletin` & co (calculs_paie), utilisés par les routes API.
  Note : `calculer_parts_irpp` est dans **core**, pas calculs_paie. `mail` s'accède
  via `current_app.extensions["mail"]` (pas d'import).

`__init__.py` est passé de 10 742 à **~9 580 lignes**.

### ⚠️ Leçon (thème congés)
Le test a échoué sur `NameError: joinedload` : un thème peut utiliser des imports
**sqlalchemy** (`joinedload`, `func`, `desc`, `and_`, `or_`…) qu'il faut remettre
dans l'en-tête du nouveau fichier. **Vérifier aussi ces imports**, pas seulement
les models/helpers. Et surtout : **les tests attrapent tout — d'où la règle d'or.**

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
| ✅ `cabinet.py` | cabinet, collaborateurs, production | 8 |
| ✅ `conges.py` | congés, acomptes | 14 |
| ✅ `declarations.py` | CNSS, DAS, exports | 10 |
| ✅ `journaliers.py` | journaliers, pointage, feuilles, avances | 29 |
| ✅ `salaries.py` | salariés, contrats, documents, modèles | 28 |
| ✅ `bulletins.py` | bulletins, périodes, composants, API | 41 |
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

1. ✅ `sites.py`, ✅ `cabinet.py`, ✅ `conges.py` (faits)
2. ✅ `declarations.py` (fait)
3. ✅ `journaliers.py` (fait) ; ✅ `salaries.py`, ✅ `bulletins.py` (faits)
4. `parametres.py`, `paiements.py`, `divers.py`

## Règle d'or

**Aucune étape sans lancer les 490 tests juste après.** Ce sont eux qui
garantissent zéro régression. À faire au calme, jamais en urgence.
