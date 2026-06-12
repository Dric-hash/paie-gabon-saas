# 🔒 Audit Sécurité & Architecture — PaieGabon SaaS
**Date :** 13 juin 2026 · **Périmètre :** dépôt `paie-gabon-saas-main` (Flask 3.0 / PostgreSQL / Railway)
**Mode :** Auditeur SecOps — analyse statique du code, de la configuration et de la logique métier.

---

## 0. Synthèse exécutive

L'application est **globalement bien construite** pour un produit solo : CSRF actif, mots de passe hachés (werkzeug), en-têtes de sécurité présents, **aucune injection SQL** (ORM + requêtes paramétrées), anti-brute-force sur le login, et un `SECRET_KEY` obligatoire en production. C'est un socle sérieux.

Mais l'audit révèle **2 failles critiques sur le flux de paiement** qui permettraient d'activer un abonnement **sans payer**, plus une faille d'isolation multi-tenant et un bug de robustesse. Ce sont les priorités absolues avant la mise en vente.

### Tableau de bord des risques

| # | Constat | Gravité | Effort |
|---|---------|---------|--------|
| C1 | Webhook CinetPay falsifiable (validation par `site_id` seul, non secret) | 🛑 **CRITIQUE** | Faible |
| C2 | Webhook Airtel « fail-open » si secret non configuré + aucune vérif. du montant | 🛑 **CRITIQUE** | Faible |
| C3 | Bug structurel : `webhook_cinetpay` — bloc `except` sans `return` (500 silencieux) | 🛑 **CRITIQUE** | Faible |
| C4 | Pointage : `worker_id` non vérifié contre le tenant + crash `None.nom_complet` | 🛑 **CRITIQUE** | Faible |
| M1 | Moteur de calcul en `float` (arrondi bancaire Python) vs base `Numeric(15,2)` | ⚠️ **MOYEN** | Moyen |
| M2 | Filtre `Site.query.get()` non scopé tenant (fuite du nom de site) | ⚠️ **MOYEN** | Faible |
| M3 | `SESSION_COOKIE_SECURE` / HSTS dépendent de `== "production"` (fragile) | ⚠️ **MOYEN** | Faible |
| M4 | Tokens OAuth en dict mémoire — cassés avec `--workers 2 --preload` | ⚠️ **MOYEN** | Moyen |
| M5 | `?api_key=` en query string (fuite dans les logs/proxys) | ⚠️ **MOYEN** | Faible |
| M6 | Dépendances figées : `werkzeug==3.0.1` antérieur aux correctifs 2024 | ⚠️ **MOYEN** | Faible |
| M7 | Tenant « DEMO » créé `ACTIF` en prod + mot de passe admin loggé en clair | ⚠️ **MOYEN** | Faible |
| O1 | CSP avec `'unsafe-inline'` + `'unsafe-hashes'` (protection XSS affaiblie) | 🛡️ Optim. | Moyen |
| O2 | Pas d'Alembic : migrations `ALTER` brutes rejouées à chaque démarrage | 🛡️ Optim. | Moyen |
| O3 | `blueprints/tenant.py` = 6 596 lignes (monolithe difficile à maintenir) | 🛡️ Optim. | Élevé |
| O4 | Helper `g()` plante sur chaîne non numérique ; 404 rend le template 403 | 🛡️ Optim. | Faible |

---

# 🛑 1. FAILLES CRITIQUES

## C1 — Webhook CinetPay falsifiable

**Fichier :** `cinetpay.py` → `valider_webhook()` · `blueprints/tenant.py` → `webhook_cinetpay()`

### Le problème
La seule vérification d'authenticité du webhook est la comparaison du `site_id` reçu avec le vôtre :

```python
def valider_webhook(data):
    site_id_recu = str(data.get("cpm_site_id", "") or data.get("site_id", ""))
    if site_id_recu != str(CINETPAY_SITE_ID):
        return False
    return True
```

Or **le `site_id` n'est pas un secret** : il est intégré dans l'URL de paiement, visible côté client. Le webhook fait ensuite **confiance au champ `cpm_result` du corps de la requête** pour activer l'abonnement. N'importe qui connaissant votre `site_id` (semi-public) et une `reference_interne` (format prévisible `CP-<tenant_id>-<hex>`) peut envoyer un faux `POST /webhook/cinetpay` et **activer un abonnement gratuitement**. Le montant payé n'est jamais vérifié non plus.

### La correction (principe)
Ne **jamais** faire confiance au statut du corps. À la réception du webhook, ré-interroger l'API CinetPay (`/payment/check`, déjà implémenté dans `verifier_statut`) pour obtenir le **statut authentique** et le **montant réellement payé**, puis comparer ce montant au montant attendu avant d'activer.

### Code correctif intégral — remplacer entièrement `webhook_cinetpay` dans `blueprints/tenant.py`

```python
@bp.route("/webhook/cinetpay", methods=["POST"])
def webhook_cinetpay():
    """
    Reçoit les notifications automatiques de CinetPay.

    SÉCURITÉ : on ne fait JAMAIS confiance au statut envoyé dans le corps de la
    requête (falsifiable). On ré-interroge l'API CinetPay (/payment/check) pour
    obtenir le statut et le montant authentiques, et on vérifie que le montant
    payé correspond au montant attendu avant d'activer l'abonnement.
    """
    import json
    from cinetpay import valider_webhook, verifier_statut

    # 1. Lecture tolérante du corps (CinetPay peut envoyer du JSON ou du form-data)
    data = request.get_json(silent=True) or request.form.to_dict()
    logger.info(f"[Webhook CinetPay] Reçu : {data}")

    # 2. Filtre de premier niveau : le site_id doit correspondre
    if not valider_webhook(data):
        return jsonify({"status": "SITE_ID_INVALIDE"}), 401

    # 3. Extraire la référence de transaction
    ref = (data.get("cpm_trans_id") or data.get("transaction_id")
           or data.get("metadata") or "")
    if not ref:
        logger.warning("[Webhook CinetPay] Référence manquante.")
        return jsonify({"status": "REF_MANQUANTE"}), 400

    # 4. Retrouver le paiement en base
    p = Paiement.query.filter_by(reference_interne=ref).first()
    if not p:
        token = data.get("cpm_payment_config") or data.get("payment_token", "")
        p = Paiement.query.filter_by(reference_externe=token).first() if token else None
    if not p:
        logger.warning(f"[Webhook CinetPay] Paiement introuvable ref={ref}")
        return jsonify({"status": "INTROUVABLE"}), 404

    # 5. Idempotence — déjà traité avec succès
    if p.statut == "SUCCES":
        return jsonify({"status": "DEJA_TRAITE"}), 200

    # 6. VÉRIFICATION AUTORITATIVE côté serveur (ne pas croire le corps)
    try:
        verif = verifier_statut(p.reference_interne)
    except Exception as e:
        logger.error(f"[Webhook CinetPay] Échec vérification API : {e}")
        db.session.rollback()
        return jsonify({"status": "VERIF_ERREUR"}), 502

    p.reponse_raw = json.dumps({"webhook": data, "verification": verif.get("raw", {})})

    if verif.get("statut") != "ACCEPTED":
        p.statut = "ECHEC"
        p.notes  = f"Statut CinetPay vérifié : {verif.get('statut')}"
        db.session.commit()
        logger.info(f"[Webhook CinetPay] Non confirmé — ref={ref} statut={verif.get('statut')}")
        return jsonify({"status": "NON_CONFIRME"}), 200

    # 7. Vérifier que le MONTANT payé correspond au montant attendu
    montant_attendu = int(round(float(p.montant or 0)))
    try:
        montant_paye = int(round(float(verif.get("montant") or 0)))
    except (TypeError, ValueError):
        montant_paye = 0
    if montant_paye and montant_paye < montant_attendu:
        p.statut = "ECHEC"
        p.notes  = f"Montant payé ({montant_paye}) < attendu ({montant_attendu}) — rejeté."
        db.session.commit()
        logger.warning(f"[Webhook CinetPay] Montant insuffisant ref={ref} : "
                       f"{montant_paye} < {montant_attendu}")
        return jsonify({"status": "MONTANT_INVALIDE"}), 200

    # 8. Tout est vérifié → activer l'abonnement
    try:
        _activer_abonnement(p)
        logger.info(f"[Webhook CinetPay] Succès vérifié — ref={ref} tenant={p.tenant_id}")
        return jsonify({"status": "OK"}), 200
    except Exception as e:
        logger.error(f"[Webhook CinetPay] Erreur activation : {e}")
        db.session.rollback()
        return jsonify({"status": "ERREUR_INTERNE"}), 500
```

> Cette réécriture corrige **C1 ET C3** (le bloc `except` orphelin) d'un coup.

---

## C2 — Webhook Airtel « fail-open »

**Fichier :** `airtel_money.py` → `valider_signature_webhook()`

### Le problème
```python
def valider_signature_webhook(payload_bytes, signature_header):
    if not AIRTEL_WEBHOOK_SECRET:
        logger.warning("...signature non vérifiée.")
        return True  # En dev, on accepte tout
```

Si `AIRTEL_WEBHOOK_SECRET` n'est pas défini (oubli de configuration en production = scénario réaliste), la fonction **accepte n'importe quelle requête** → même faille que C1. De plus, le webhook Airtel active l'abonnement sur la foi du `status` du payload **sans vérifier le montant**.

### Code correctif intégral — remplacer entièrement `valider_signature_webhook` dans `airtel_money.py`

```python
def valider_signature_webhook(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Vérifie la signature HMAC-SHA256 du webhook Airtel (header 'X-Airtel-Signature').

    SÉCURITÉ — fail-closed en production :
      • Si le secret est absent ET qu'on est en production → REJET (return False).
      • L'acceptation sans signature n'est tolérée qu'en dev/sandbox, pour les tests.
    """
    if not AIRTEL_WEBHOOK_SECRET:
        est_production = (
            os.environ.get("RAILWAY_ENVIRONMENT") is not None
            or os.environ.get("AIRTEL_ENV") == "production"
        )
        if est_production:
            logger.error(
                "[Airtel] AIRTEL_WEBHOOK_SECRET absent en PRODUCTION — webhook REJETÉ. "
                "Configurez la variable d'environnement immédiatement."
            )
            return False
        logger.warning("[Airtel] AIRTEL_WEBHOOK_SECRET absent (dev) — signature non vérifiée.")
        return True

    if not signature_header:
        return False

    expected = hmac.new(
        AIRTEL_WEBHOOK_SECRET.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

### Renforcement complémentaire — vérifier le montant dans le webhook Airtel
Dans `blueprints/tenant.py`, fonction `webhook_airtel()`, remplacer le bloc d'activation par une vérification active du statut + montant (même logique que CinetPay). Remplacer :

```python
        if statut_api in ("TS", "SUCCESS", "200"):
            _activer_abonnement(p)
            logger.info(f"[Webhook Airtel] Succès — ref={ref} tenant={p.tenant_id}")
```

par :

```python
        if statut_api in ("TS", "SUCCESS", "200"):
            # Confirmation autoritative : on ré-interroge Airtel avant d'activer
            from airtel_money import verifier_statut
            verif = verifier_statut(p.reference_externe or ref)
            if verif.get("statut") != "SUCCESS":
                p.statut = "ECHEC"
                p.notes  = f"Webhook OK mais vérification Airtel = {verif.get('statut')}"
                db.session.commit()
                logger.warning(f"[Webhook Airtel] Vérif. divergente — ref={ref}")
                return jsonify({"status": "NON_CONFIRME"}), 200
            _activer_abonnement(p)
            logger.info(f"[Webhook Airtel] Succès vérifié — ref={ref} tenant={p.tenant_id}")
```

---

## C3 — Bug structurel : `webhook_cinetpay` retourne `None` sur erreur

**Fichier :** `blueprints/tenant.py`, lignes ~2094-2097

### Le problème
```python
    except Exception as e:
        logger.error(f"[Webhook CinetPay] Erreur : {e}")
        db.session.rollback()
@bp.route("/parametres")          # ← le décorateur suit IMMÉDIATEMENT
def parametres():
```

Le bloc `except` **n'a pas de `return`**. Sur toute exception, la vue renvoie `None`, ce que Flask transforme en erreur *« View function did not return a valid response »* (500). Un webhook qui échoue silencieusement = paiements perdus sans trace exploitable côté CinetPay (qui considère l'envoi en échec et peut réessayer indéfiniment).

### La correction
✅ **Déjà corrigé par la réécriture complète de C1** : chaque branche de la nouvelle fonction se termine par un `return jsonify(...)` explicite. Vérifiez simplement qu'après collage, la définition de `parametres()` reste bien séparée et intacte.

---

## C4 — Pointage : `worker_id` non validé + crash sur travailleur inconnu

**Fichier :** `blueprints/tenant.py`, fonction de pointage individuel (~ligne 2771 et 2874)

### Le problème
```python
wid = int(request.form.get("worker_id", 0))          # ← valeur brute du formulaire
...
pt = Pointage(tenant_id=t.id, ..., salarie_id=wid)   # ← aucun contrôle d'appartenance
...
worker_name = (Salarie.query.get(wid) or Journalier.query.get(wid)).nom_complet
```

Deux défauts :
1. **Isolation tenant** : `wid` n'est jamais vérifié contre `t.id`. Un tenant peut créer un pointage référençant le salarié d'un **autre** tenant, et lire son nom via le message flash (fuite de données + corruption d'intégrité).
2. **Crash (`Mode Impeccable` règle 3)** : si `wid` ne correspond à aucun `Salarie` ni `Journalier` (saisie erronée, enregistrement supprimé, `wid=0`), alors `(None or None).nom_complet` lève `AttributeError` → 500, **après** que le `commit()` a déjà eu lieu.

### Code correctif intégral
**(a)** Remplacer la ligne d'extraction de `wid` (juste après `wtype = request.form.get("worker_type", "sal")`) :

```python
        wtype  = request.form.get("worker_type", "sal")
        wid    = request.form.get("worker_id", type=int)

        # Validation stricte : le travailleur doit exister ET appartenir au tenant
        if wtype == "sal":
            worker_obj = Salarie.query.filter_by(id=wid, tenant_id=t.id).first()
        else:
            worker_obj = Journalier.query.filter_by(id=wid, tenant_id=t.id).first()

        if not worker_obj:
            flash("Travailleur introuvable ou non autorisé.", "error")
            return redirect(url_for("tenant.pointage"))
```

**(b)** Remplacer la ligne 2874 (`worker_name = (Salarie.query.get(wid) ...`) par :

```python
        worker_name = worker_obj.nom_complet
```

> `worker_obj` est désormais garanti non-`None` et appartenant au bon tenant : plus de crash, plus de fuite inter-tenant, et on évite une requête SQL redondante.

---

# ⚠️ 2. MANQUEMENTS TECHNIQUES & LOGIQUES (Gravité moyenne)

## M1 — Arrondis de paie en `float` (arrondi bancaire) vs base `Numeric(15,2)`

**Fichier :** `calculs_paie.py` (tout le moteur)

### Le problème — rigueur métier
La base stocke correctement les montants en `db.Numeric(15,2)` (Decimal). Mais **le moteur de calcul travaille en `float`** : `float(val)`, puis `round(x, 2)` partout (CNSS, CNAMGS, FNH, CFP, TCS, IRPP…). Deux risques au « centime près » que vous avez exigé :

1. **`round()` de Python = arrondi *bancaire* (round-half-to-even)**, pas l'arrondi commercial (round-half-**up**) attendu en paie. Exemples : `round(2.5)` → `2`, `round(0.125, 2)` → `0.12`. Sur les demi-unités, vous obtiendrez ponctuellement **1 franc d'écart** avec le calcul officiel/manuel.
2. **Imprécision flottante** : `base * 0.041` en `float` peut produire `…0000001`, et l'**accumulation** sur une vingtaine de rubriques peut dériver d'un franc sur le net.

> ⚠️ Note d'honnêteté métier : je ne peux pas certifier *quel* mode d'arrondi la DGI/CNSS gabonaise impose officiellement (à la tranche, au franc supérieur/inférieur ?). À vérifier sur le texte officiel. Ce qui est sûr techniquement : `round()` flottant est non déterministe sur les demi-unités, ce qui n'est jamais souhaitable en paie.

### Code correctif — helper Decimal à arrondi commercial
Ajouter en tête de `calculs_paie.py` :

```python
from decimal import Decimal, ROUND_HALF_UP

def fcfa(valeur, decimales: int = 2) -> float:
    """
    Arrondi monétaire déterministe (arrondi commercial, round-half-up),
    contrairement au round() natif de Python qui fait du round-half-to-even.

    Convertit via str() pour neutraliser l'imprécision binaire du float
    (ex: 0.1 + 0.2). Retourne un float compatible avec les colonnes Numeric(15,2).

    >>> fcfa(2.5, 0)      # 3.0  (et non 2 comme round())
    >>> fcfa(1234.565)    # 1234.57
    """
    if valeur is None:
        return 0.0
    quant = Decimal(1).scaleb(-decimales)  # ex: Decimal("0.01")
    d = Decimal(str(valeur)).quantize(quant, rounding=ROUND_HALF_UP)
    return float(d)
```

**Application** — remplacer chaque `round(x, 2)` sur un **montant monétaire** par `fcfa(x)`. Exemple :

```python
    cnss_salarie   = fcfa(base_cnss * CNSS_TAUX_SALARIE)
    cnss_patronale = fcfa(base_cnss * CNSS_TAUX_PATRONAL)
    cnamgs_salarie = fcfa(base_cnamgs * CNAMGS_TAUX_SALARIE)
    # … idem FNH, CFP, TCS, IRPP, net_a_payer, etc.
```

> Gardez `round(x, 4)` pour les **taux** intermédiaires (taux horaire), où l'on veut justement de la précision avant l'arrondi final. La cible robuste à terme est de passer tout le moteur en `Decimal` de bout en bout ; le helper `fcfa()` est le premier pas à faible risque qui fiabilise immédiatement les montants stockés.

---

## M2 — Filtre `Site` non scopé tenant (fuite du nom de site)

**Fichier :** `blueprints/tenant.py`, lignes ~1104, ~2707, ~3016

```python
site_filtre = Site.query.get(site_filtre_id) if site_filtre_id else None
```

Un tenant peut passer `?site_id=<id d'un autre tenant>` et l'objet `Site` (donc son `.nom`) est transmis au template → fuite mineure inter-tenant. **Correction** (aux 3 emplacements) :

```python
site_filtre = (Site.query.filter_by(id=site_filtre_id, tenant_id=t.id).first()
               if site_filtre_id else None)
```

---

## M3 — `SESSION_COOKIE_SECURE` / HSTS dépendent d'une égalité fragile

**Fichier :** `app.py`

```python
app.config["SESSION_COOKIE_SECURE"] = (os.environ.get("RAILWAY_ENVIRONMENT") == "production")
# … et plus bas pour HSTS :
if os.environ.get("RAILWAY_ENVIRONMENT") == "production":
```

Railway positionne `RAILWAY_ENVIRONMENT` au **nom de l'environnement**. Par défaut c'est `"production"`, mais si vous renommez l'environnement (ou utilisez un autre indicateur), le cookie de session **n'aura plus le flag `Secure`** — il pourra transiter en clair. Le garde `SECRET_KEY`, lui, teste la simple présence de la variable : incohérence dangereuse.

### Code correctif — un seul indicateur cohérent (`app.py`)
Juste après la création de `app = Flask(__name__)` :

```python
# Indicateur de production unique et robuste (présence, pas égalité stricte)
EST_PRODUCTION = bool(
    os.environ.get("RAILWAY_ENVIRONMENT")
    or os.environ.get("FLASK_ENV") == "production"
)
app.config["EST_PRODUCTION"] = EST_PRODUCTION
```

Puis remplacer :
```python
app.config["SESSION_COOKIE_SECURE"] = EST_PRODUCTION
```
et dans `add_security_headers` :
```python
        if EST_PRODUCTION:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
```
et dans le garde SECRET_KEY :
```python
if not _secret:
    if EST_PRODUCTION:
        logger.critical("SECRET_KEY non définie — démarrage interrompu.")
        sys.exit(1)
```

---

## M4 — Tokens OAuth en mémoire : cassés avec `--workers 2 --preload`

**Fichier :** `api_rest.py` (`_oauth_tokens: dict = {}`) · `Procfile` (`gunicorn … --workers 2 --preload`)

Le store `_oauth_tokens` est un dict **au niveau module**. Avec `--preload`, il est créé dans le process parent puis **copié** dans chacun des 2 workers au `fork()` ; ensuite chaque worker a sa propre copie. Conséquences :
- Un token émis par le worker A est **invalide** si la requête suivante tombe sur le worker B (~50 % d'échecs `TOKEN_INVALID`).
- La révocation et l'expiration ne sont pas partagées ; un **redéploiement efface tous les tokens**.

### Correction recommandée
Persister les tokens. Le plus simple sans nouvelle dépendance : une table `OAuthToken` (token hashé, `tenant_id`, `expires_at`). Si vous activez Redis (déjà dans `requirements.txt`), stockez-les avec TTL natif :

```python
import os, json
import redis as _redis

_redis_url = os.environ.get("REDIS_URL")
_rds = _redis.from_url(_redis_url) if _redis_url else None

def _token_store_set(token, tenant_id, ttl):
    payload = json.dumps({"tenant_id": tenant_id})
    if _rds:
        _rds.setex(f"oauth:{token}", ttl, payload)
    else:
        _oauth_tokens[token] = {"tenant_id": tenant_id,
                                "expires_at": datetime.utcnow() + timedelta(seconds=ttl)}

def _token_store_get(token):
    if _rds:
        raw = _rds.get(f"oauth:{token}")
        return json.loads(raw) if raw else None
    entry = _oauth_tokens.get(token)
    if entry and datetime.utcnow() < entry["expires_at"]:
        return {"tenant_id": entry["tenant_id"]}
    return None
```

> Tant que la persistance n'est pas en place, l'API OAuth est **non fiable en multi-worker**. À défaut, passez temporairement à `--workers 1` (au prix du débit) pour éviter le comportement erratique.

---

## M5 — Token API accepté en query string

**Fichier :** `api_rest.py` → `_get_tenant_from_request`

```python
or request.args.get("api_key")   # « déconseillé mais supporté »
```

Les query strings finissent dans les logs serveur, l'historique navigateur, les en-têtes `Referer` et les proxys. Un token de tenant qui fuit = accès complet à ses données de paie. **Correction** : supprimer ce fallback (ne garder que `X-API-Key` et `Authorization: Bearer`).

```python
    token = (
        request.headers.get("X-API-Key")
        or _extract_bearer(request.headers.get("Authorization", ""))
    )
```

---

## M6 — Dépendances figées antérieures aux correctifs de sécurité

**Fichier :** `requirements.txt`

`werkzeug==3.0.1` et `flask==3.0.0` datent de fin 2023 et **précèdent plusieurs correctifs de sécurité Werkzeug de 2024** (notamment le débogueur et le parsing multipart). Recommandations :
- Mettre à jour `werkzeug` vers la dernière 3.x (≥ 3.0.6), `flask` vers la dernière 3.x.
- Intégrer un contrôle automatisé : `pip install pip-audit` puis `pip-audit -r requirements.txt` en CI/pré-déploiement.

> Je n'affirme pas de numéros de CVE de mémoire — **vérifiez l'état actuel** via `pip-audit`, qui croise vos versions exactes avec la base d'avis à jour. C'est l'outil de référence et il est gratuit.

---

## M7 — Tenant « DEMO » actif en prod + secrets loggés

**Fichier :** `app.py` → `init_db()`

- Un tenant `demo` + utilisateur `demo@paiegalon.ga` sont créés `statut="ACTIF"` à chaque première initialisation, **y compris en production** → surface de connexion publique avec identifiants devinables.
- Si `SUPER_ADMIN_PASSWORD` / `DEMO_PASSWORD` ne sont pas définis, un mot de passe aléatoire est généré **et écrit dans les logs** (`logger.info(... mdp auto: ...)`). Les logs Railway/Sentry peuvent être consultés par des tiers.

### Corrections
1. Conditionner la création du tenant démo au hors-production :
```python
    if not Tenant.query.first() and not EST_PRODUCTION:
        # … création du tenant démo réservée au dev …
```
2. Ne **jamais** logguer un mot de passe. En production, exiger les variables et échouer proprement si absentes plutôt que d'auto-générer-et-logguer :
```python
        sa_password = os.environ.get("SUPER_ADMIN_PASSWORD", "")
        if not sa_password:
            if EST_PRODUCTION:
                logger.critical("SUPER_ADMIN_PASSWORD requis en production.")
                sys.exit(1)
            sa_password = sec.token_urlsafe(16)
            logger.info("[DEV] Super-admin créé avec un mot de passe temporaire (non loggué).")
```

---

# 🛡️ 3. OPTIMISATIONS (« finitions impeccables »)

## O1 — CSP avec `'unsafe-inline'` affaiblit la protection XSS
**`app.py`** : `script-src 'self' 'unsafe-inline' 'unsafe-hashes' …`. Avec `'unsafe-inline'`, la CSP ne bloque quasiment plus l'injection de scripts — protection XSS largement neutralisée. Pour une app financière, migrez à terme vers une CSP **à nonce** : générez un nonce par requête, ajoutez-le aux `<script>` légitimes et remplacez `'unsafe-inline'` par `'nonce-<valeur>'`. C'est un chantier (il faut toucher les handlers `oninput`/`onclick` inline des templates), d'où le classement en optimisation, mais c'est le bon cap.

## O2 — Pas de gestion de migrations versionnée
Les `ALTER TABLE … IF NOT EXISTS` de `run_migrations()` sont rejoués **à chaque démarrage**. Ça fonctionne mais c'est fragile (pas de rollback, pas d'historique, ordre implicite). Adoptez **Flask-Migrate/Alembic** : migrations versionnées, réversibles, exécutées une fois. Effort moyen mais investissement structurant avant d'avoir des clients en production.

## O3 — `blueprints/tenant.py` = 6 596 lignes
Un seul fichier concentre salariés, contrats, bulletins, pointage, congés, sites, paiements, paramètres… C'est ingérable à terme et augmente le risque de régressions (le bug C3 en est un symptôme : un `return` manquant noyé dans le monolithe). Découpez en sous-modules (`tenant/salaries.py`, `tenant/paiements.py`, `tenant/pointage.py`…) regroupés sous le même blueprint ou des blueprints imbriqués.

## O4 — Robustesse mineure
- **Helper `g()`** (`calculs_paie.py`) : `float(val) if val else 0.0` lève `ValueError` si `val` est une chaîne non numérique (`"abc"`). Sécurisez :
```python
    def g(key):
        val = donnees.get(key, 0)
        try:
            return float(val) if val not in (None, "") else 0.0
        except (TypeError, ValueError):
            return 0.0
```
- **Handler 404** (`app.py`) : `@app.errorhandler(404)` rend `auth/403.html`. Créez un `auth/404.html` dédié pour ne pas afficher un message « accès interdit » sur une simple page introuvable.

---

# ✅ Plan d'action priorisé

**Avant toute mise en vente (bloquant) :**
1. **C1** — réécrire `webhook_cinetpay` (vérification API + montant). *Corrige aussi C3.*
2. **C2** — `valider_signature_webhook` fail-closed + vérif. montant Airtel.
3. **C4** — valider `worker_id` contre le tenant + supprimer le crash.

**Dans la foulée (jours suivants) :**
4. **M3** — indicateur `EST_PRODUCTION` cohérent (cookies Secure + HSTS garantis).
5. **M7** — pas de démo en prod, plus de secrets dans les logs.
6. **M5** — retirer `?api_key=`. · **M6** — `pip-audit` + montée de version werkzeug/flask.
7. **M1** — helper `fcfa()` sur les montants (après validation du mode d'arrondi DGI/CNSS).
8. **M2** — scoper le filtre `Site` au tenant.

**Quand l'API/le multi-worker montent en charge :**
9. **M4** — persister les tokens OAuth (Redis/DB).

**Dette technique de fond :**
10. **O2** Alembic · **O3** découper `tenant.py` · **O1** CSP à nonce · **O4** robustesse.

---

### Ce qui est déjà solide (à conserver)
Aucune injection SQL (ORM + requête `logo_url` paramétrée), CSRF actif avec exemptions justifiées, mots de passe hachés (werkzeug), anti-brute-force avec verrouillage de compte, tokens de réinitialisation `secrets.token_urlsafe(32)` à expiration 2 h, isolation tenant correcte sur la grande majorité des routes (`filter_by(tenant_id=t.id)`), `SECRET_KEY` obligatoire en prod, en-têtes `X-Frame-Options`/`nosniff`/`Referrer-Policy`/HSTS présents, idempotence des webhooks. C'est un niveau de soin supérieur à la moyenne des SaaS solo.
