# Paie Gabon — Logiciel SaaS de gestion de la paie

Logiciel multi-entreprises de gestion de la paie conforme à la réglementation gabonaise.

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

## Comptes par défaut

| Rôle | Email | Mot de passe |
|------|-------|--------------|
| Super Admin | superadmin@paiegalon.com | Admin2026! |
| Compte démo | demo@paiegalon.ga | Demo2026! |

> **Important** : Changez ces mots de passe avant toute mise en production.

## Structure du projet

```
paie_gabon_saas/
├── app.py                  # Application principale
├── models.py               # Base de données (10 tables)
├── calculs_paie.py         # Moteur de calcul (CNSS, CNAMGS, TCS, IRPP)
├── requirements.txt        # Dépendances Python
├── Procfile                # Configuration Railway/Heroku
├── runtime.txt             # Version Python
├── templates/
│   ├── base.html           # Layout principal
│   ├── auth/               # Connexion & inscription
│   ├── admin/              # Panneau super-administrateur
│   └── tenant/             # Interface entreprise
└── static/                 # CSS & JS
```

## Fonctionnalités

- Multi-entreprises avec isolation complète des données
- Calcul automatique : CNSS (2.5%/16%), CNAMGS (2%/4.1%), TCS (5%), FNH (2%), CFP (0.5%), IRPP
- 4 plans d'abonnement : Starter, Pro, Premium, Cabinet
- 4 niveaux d'accès : Super Admin, Tenant Admin, Gestionnaire, Lecture
- Export journal de paie Excel
- Inscription libre des entreprises

## Réglementation appliquée

- CGI Gabon
- Décret 578/PR/MDSFPSSN
- Arrêté 037/METPS

## Hébergement (Railway.app)

Variables d'environnement requises :
```
SECRET_KEY=votre-cle-secrete-longue
DATABASE_URL=sqlite:///paie.db
```
