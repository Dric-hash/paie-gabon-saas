# PaieGabon — Logiciel SaaS de gestion de la paie

Logiciel multi-entreprises de gestion de la paie conforme à la réglementation gabonaise.
Développé avec Flask (Python) + PostgreSQL + Tailwind CSS.

---

## Démarrage rapide

### 1. Installer les dépendances
```bash
pip install -r requirements.txt
```

### 2. Lancer l'application
```bash
python app.py
```

### 3. Ouvrir dans le navigateur
```
http://localhost:5000
```

---

## Comptes par défaut

| Rôle | Email | Mot de passe |
|------|-------|--------------|
| Super Admin | superadmin@paiegalon.com | Admin2026! |
| Compte démo | demo@paiegalon.ga | Demo2026! |

> Changez ces mots de passe avant toute mise en production.

### Promouvoir votre compte en super-admin (production)

Définissez la variable d'environnement `SUPER_ADMIN_EMAIL` avec l'email de votre compte, puis redéployez ou redémarrez l'application :

```
SUPER_ADMIN_EMAIL=votre.email@entreprise.ga
```

Au démarrage, ce compte reçoit le rôle `SUPER_ADMIN` et peut accéder à `/admin`.

---

## Fonctionnalités

### Multi-entreprises
- Isolation complète des données par entreprise (tenant)
- Inscription libre des entreprises
- 4 plans : Starter, Pro, Premium, Cabinet
- Panneau Super Admin complet

### Gestion de la paie
- Saisie bulletin avec calcul automatique en temps réel
- Impression PDF professionnelle des bulletins
- Export journal de paie Excel
- Historique complet par période

### Calculs réglementaires Gabon
- CNSS : 5% salarié / 18% patronal (plafond 1 500 000 FCFA)
- CNAMGS : 2% salarié / 4,1% patronal (plafond 2 500 000 FCFA)
- TCS : 5% (exonération 150 000 FCFA)
- FNH : 2% patronal (plafond 1 500 000 FCFA)
- CFP : 0,5% patronal
- IRPP : barème progressif par quotient familial

### Gestion des salariés
- Fiche complète avec statistiques cumulées
- Suivi des congés (2 jours/mois, 24 jours/an)
- Gestion des contrats

### Tableau de bord
- KPIs en temps réel
- Évolution masse salariale sur 6 mois
- Répartition par catégorie (C1-C4)
- Top 5 salaires
- Alertes automatiques

### Administration
- Import données Excel (SGTG compatible)
- Gestion utilisateurs par entreprise
- Paramètres société complets (5 onglets)

---

## Structure du projet

```
paie_gabon_saas/
├── app.py                      # Application principale (939 lignes)
├── models.py                   # Base de données (10 tables)
├── calculs_paie.py             # Moteur de calcul gabonais
├── migration_sgtg.py           # Script migration Excel → BDD
├── requirements.txt            # Dépendances Python
├── Procfile                    # Configuration Railway
├── runtime.txt                 # Python 3.11.6
├── templates/
│   ├── base.html               # Layout principal
│   ├── auth/
│   │   ├── login.html          # Connexion
│   │   └── inscription.html    # Inscription entreprise
│   ├── admin/
│   │   ├── dashboard.html      # Tableau de bord super admin
│   │   ├── tenants.html        # Liste entreprises
│   │   ├── tenant_detail.html  # Détail entreprise
│   │   ├── plans.html          # Gestion plans
│   │   ├── rubriques.html      # Rubriques paie
│   │   ├── stats.html          # Statistiques globales
│   │   └── import_excel.html   # Import données Excel
│   └── tenant/
│       ├── base.html           # Layout tenant
│       ├── dashboard.html      # Tableau de bord entreprise
│       ├── salaries.html       # Liste salariés
│       ├── salarie_detail.html # Fiche salarié complète
│       ├── salarie_form.html   # Formulaire salarié
│       ├── bulletin_saisie.html# Saisie bulletin (temps réel)
│       ├── bulletin_detail.html# Détail bulletin
│       ├── bulletin_print.html # Impression PDF professionnelle
│       ├── bulletins.html      # Historique bulletins
│       ├── periodes.html       # Gestion périodes
│       ├── conges.html         # Gestion congés
│       ├── conge_form.html     # Formulaire congé
│       ├── parametres.html     # Paramètres (5 onglets)
│       ├── utilisateurs.html   # Gestion utilisateurs
│       └── utilisateur_form.html
```

---

## Routes principales (39 routes)

| Route | Description |
|-------|-------------|
| `/` | Accueil → redirection |
| `/login` | Connexion |
| `/inscription` | Inscription entreprise |
| `/dashboard` | Tableau de bord |
| `/salaries` | Liste salariés |
| `/salaries/<id>` | Fiche salarié |
| `/bulletins/saisie` | Saisie bulletin |
| `/bulletins/<id>/imprimer` | Impression PDF |
| `/bulletins/export/<periode>` | Export Excel |
| `/conges` | Gestion congés |
| `/parametres` | Paramètres entreprise |
| `/admin` | Panneau super admin |
| `/admin/import` | Import Excel |
| `/api/calculer-bulletin` | API calcul temps réel |

---

## Hébergement (Railway.app)

Variables d'environnement requises :
```
SECRET_KEY=votre-cle-secrete-longue
DATABASE_URL=postgresql://...
```

URL actuelle : https://ameriack-paie.up.railway.app

---

## Réglementation appliquée

- Code du Travail Gabonais
- CGI Gabon
- Décret 578/PR/MDSFPSSN
- Arrêté 037/METPS
- SMIG : 150 000 FCFA/mois
- Congés : 2 jours/mois (24 jours/an)

---

*Version 2.0 — Mai 2026*
*Développé avec Flask + PostgreSQL + Tailwind CSS*
