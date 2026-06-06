# PaieGabon — Logiciel SaaS de gestion de la paie

Logiciel multi-entreprises de gestion de la paie conforme à la réglementation gabonaise.  
Développé avec Flask (Python) + PostgreSQL + Tailwind CSS.

---

## Architecture

```
app.py                    ← Factory Flask (328 lignes — config, blueprints, middleware)
core.py                   ← Utilitaires partagés (cache Redis, décorateurs, email, rate limiter)
models.py                 ← Modèles SQLAlchemy
calculs_paie.py           ← Moteur de calcul paie gabonais
blueprints/
  auth.py                 ← /login  /inscription  /logout  /confirmer-email  /profil
  admin.py                ← /admin/*  (super-admin)
  tenant.py               ← /dashboard  /salaries  /bulletins  /conges  /pointage …
  api_v1.py               ← /api/v1/*  (API REST OAuth2)
```

---

## Démarrage rapide

### 1. Installer les dépendances
```bash
pip install -r requirements.txt
```

### 2. Configurer l'environnement
```bash
cp .env.example .env
# Éditez .env et définissez au minimum SECRET_KEY et DATABASE_URL
```

### 3. Lancer l'application
```bash
python app.py
```

### 4. Ouvrir dans le navigateur
```
http://localhost:5000
```

---

## Comptes initiaux

Au premier démarrage, les mots de passe sont **générés automatiquement** et affichés dans les logs :

```
[INIT] Super-admin — email: superadmin@paiegalon.com — mdp auto: <généré>
[INIT] Démo — email: demo@paiegalon.ga — mdp auto: <généré>
```

Configurez ces valeurs via les variables d'environnement `SUPER_ADMIN_EMAIL`, `SUPER_ADMIN_PASSWORD`, `DEMO_EMAIL`, `DEMO_PASSWORD`.

---

## Fonctionnalités

### Multi-entreprises
- Isolation complète des données par entreprise (tenant)
- Inscription libre des entreprises
- 3 plans : Starter (10 sal.), Pro (50 sal.), Cabinet (illimité)
- Panneau Super Admin complet

### Calculs réglementaires Gabon 2026
- CNSS : 5% salarié / 18% patronal (plafond 1 500 000 FCFA)
- CNAMGS : 2% salarié / 4,1% patronal (plafond 2 500 000 FCFA)
- TCS : 5% (exonération 150 000 FCFA)
- FNH : 3% patronal (plafond 1 500 000 FCFA)
- CFP : 0,5% patronal
- IRPP : barème progressif par quotient familial
- Convention BTP : heures 10%/30%/40%/70%, prime ancienneté, préavis, ISR

### Gestion des salariés
- Fiche complète avec historique contrats
- Suivi des congés (2 jours/mois, 24 jours/an)
- Gestion des acomptes
- Pointage journalier (salariés + journaliers)
- Gestion multi-sites / chantiers

### Sécurité
- RBAC à 5 rôles (TENANT_ADMIN, RH, COMPTABLE, DIRECTEUR, GESTIONNAIRE)
- Isolation multi-tenant (filter_by tenant_id systématique)
- Brute-force protection (blocage après N échecs)
- Validation mot de passe (longueur, majuscule, chiffre)
- Content-Security-Policy header
- Rate limiting sur les routes publiques
- Sessions avec timeout d'inactivité configurable
- CSRF protection (Flask-WTF)
- Audit trail complet

### Exports
- Bulletins PDF (3 modèles : classique, moderne, minimaliste)
- Déclaration CNSS/CNAMGS Excel + CSV
- Export comptable Sage (journal + grand livre)
- Journal de paie Excel

### API REST
- OAuth2 (client_credentials)
- Endpoints : /api/v1/salaries, /api/v1/bulletins, /api/v1/periodes, /api/v1/stats

---

## Variables d'environnement

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `SECRET_KEY` | ✅ prod | Clé secrète Flask (min 32 chars) |
| `DATABASE_URL` | ✅ prod | URL PostgreSQL |
| `SUPER_ADMIN_EMAIL` | recommandé | Email du super-admin |
| `SUPER_ADMIN_PASSWORD` | recommandé | Mot de passe super-admin |
| `REDIS_URL` | optionnel | Cache Redis (recommandé multi-workers) |
| `MAIL_USERNAME` | optionnel | Email Gmail (envoi de mails) |
| `MAIL_PASSWORD` | optionnel | App password Gmail |
| `SESSION_TIMEOUT_MINUTES` | optionnel | Timeout inactivité (défaut: 60) |
| `LOGIN_MAX_ECHECS` | optionnel | Tentatives avant blocage (défaut: 5) |
| `LOGIN_BLOCAGE_MIN` | optionnel | Durée blocage en minutes (défaut: 15) |
| `LOG_LEVEL` | optionnel | Niveau de log (défaut: INFO) |

---

## Tests

```bash
pytest tests/ -v
```

225 tests unitaires couvrant :
- Moteur de calcul paie (IRPP, CNSS, CNAMGS, TCS, BTP)
- Système de permissions RBAC
- Gestion des congés
- Déclarations CNSS
- Paiements (Airtel Money, CinetPay)
- Exports comptables
