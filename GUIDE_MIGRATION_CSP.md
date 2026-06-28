# Guide de migration vers une CSP stricte (F2)

L'audit recommandait de retirer `'unsafe-inline'` de la `Content-Security-Policy`.
C'est un **chantier front à part entière** (≈ 341 handlers inline sur 51 templates
+ 43 blocs `<script>`, et Tailwind chargé en CDN), à mener par étapes avec un test
visuel de chaque écran. La **phase 1 (outillage)** est déjà livrée ; ce guide décrit
la suite.

## Ce qui est déjà en place

- **Nonce par requête** : `g.csp_nonce` est généré à chaque requête et exposé aux
  templates via la variable `{{ csp_nonce }}` (context processor dans `app.py`).
- **CSP Report-Only opt-in** : en posant `CSP_REPORT_ONLY=1`, l'app publie une
  politique stricte à base de nonce en mode *Report-Only* — elle **n'impose rien**
  et ne casse rien, elle se contente de **signaler** les violations.
- **Endpoint `/csp-report`** : reçoit les rapports du navigateur et les journalise
  (`logger.warning("[CSP] violation: …")`).

## Étapes de migration

### 1. Observer (zéro risque)
Activer `CSP_REPORT_ONLY=1` en staging (ou en prod), naviguer dans l'app, puis lire
les logs `[CSP] violation`. On y verra deux familles de violations :
- les blocs `<script>…</script>` sans nonce ;
- les handlers inline (`onclick=`, `oninput=`, `onsubmit=`…).

### 2. Nonce sur les `<script>` (faible risque)
Ajouter l'attribut nonce à chaque balise de script inline :
```html
<script nonce="{{ csp_nonce }}"> … </script>
```
Cela ne change aucun comportement sous la politique actuelle (qui tolère encore
`'unsafe-inline'`), mais rend ces scripts conformes à la future politique stricte.

### 3. Migrer les handlers inline (le gros du travail)
Les attributs `on*=` **ne peuvent pas** porter de nonce : il faut les déplacer dans
un script nonce'd. Pattern de conversion :

Avant :
```html
<button onclick="ouvrirModale(42)">Ouvrir</button>
```
Après :
```html
<button data-action="ouvrir" data-id="42">Ouvrir</button>
<script nonce="{{ csp_nonce }}">
  document.querySelectorAll('[data-action="ouvrir"]').forEach(b =>
    b.addEventListener('click', () => ouvrirModale(b.dataset.id)));
</script>
```
À faire écran par écran, en testant l'interactivité après chaque template.

### 4. Tailwind CDN
`cdn.tailwindcss.com` injecte des styles inline au runtime et impose
`'unsafe-inline'` côté `style-src`. Pour une CSP vraiment stricte, **compiler
Tailwind** (build statique servi depuis `/static`) et retirer le CDN. Sinon, garder
`'unsafe-inline'` uniquement sur `style-src` (le risque XSS principal vient de
`script-src`, pas de `style-src`).

### 5. Basculer en enforce
Quand les logs Report-Only ne remontent plus de violation « script » :
- dans `app.py`, remplacer dans la CSP **enforce** `'unsafe-inline' 'unsafe-hashes'`
  de `script-src` par `'nonce-{nonce}'` (reprendre la chaîne déjà construite pour le
  Report-Only) ;
- retirer la variable d'environnement `CSP_REPORT_ONLY` (le mode strict est devenu
  la politique imposée).

## Repère d'effort
Comptez la migration des handlers comme la tâche dominante. Le principal vecteur XSS
en texte libre a déjà été neutralisé (correctif M3, `|tojson`), donc cette migration
est un durcissement défensif, pas une faille ouverte — elle peut se faire
progressivement, template par template, sans urgence.
