# Documentation technique — PaieGabon
## Modèle de données & règles métier

> Document de référence sur le cœur de PaieGabon : la structure des données et les règles de calcul de la paie selon la réglementation gabonaise.
> Destiné au développeur (présent ou futur) qui doit comprendre ou faire évoluer le moteur de paie.

---

# Partie 1 — Modèle de données

## Vue d'ensemble

PaieGabon est une application **multi-tenant** : plusieurs entreprises clientes (tenants) partagent la même base, mais leurs données sont cloisonnées. **Presque toutes les tables possèdent une colonne `tenant_id`** qui garantit cette isolation — c'est la règle de sécurité la plus importante du modèle.

Les tables se regroupent en grands domaines :

### Domaine SaaS (gestion des clients)
- **Plan** — les formules d'abonnement (limites, prix). Pas de `tenant_id` : c'est une table globale.
- **Tenant** — une entreprise cliente. Rattachée à un `Plan`. 24 colonnes (dénomination, NIF, coordonnées, paramètres).
- **Utilisateur** — les comptes de connexion. Rattachés à un tenant (sauf le super-admin). Portent le rôle et les permissions.
- **Paiement** — les paiements d'abonnement des tenants.
- **OAuthClient** — clients pour l'API REST.
- **AuditLog** — journal d'audit (qui a fait quoi, quand).

### Domaine RH (les personnes)
- **Salarie** — employé mensuel. 26 colonnes. Rattaché à une `CategorieEmploi`.
- **Journalier** — travailleur payé à la journée/heure (distinct du salarié mensuel).
- **CategorieEmploi** — catégories conventionnelles (ouvrier, employé, cadre…).
- **Contrat** — contrat de travail d'un salarié (type, dates, salaire de base).
- **Conge** — demandes et soldes de congés.
- **Acompte** — avances sur salaire.

### Domaine Paie (le cœur)
- **PeriodePaie** — un mois de paie (mois, année, statut OUVERT/CLÔTURÉ).
- **BulletinPaie** — le bulletin d'un salarié pour une période. **104 colonnes** — c'est la table centrale, qui stocke tous les éléments calculés.
- **RubriquePaie** — rubriques paramétrables. Table globale.
- **FeuillePaieJournalier** — l'équivalent du bulletin pour les journaliers.

### Domaine Chantiers (BTP)
- **Site** — un chantier/site de travail.
- **AffectationSite** — affectation d'un salarié ou journalier à un site.
- **Pointage** — pointage journalier (heures, type de jour). 23 colonnes.

### Domaine Prestataires
- **Prestataire** — freelance, sous-traitant ou fournisseur. 28 colonnes.
- **ContratPrestation** — contrat de prestation.
- **FacturePrestataire** — facture reçue (avec TVA et retenue à la source).
- **PaiementPrestataire** — paiements effectués sur les factures.

## Relations clés

```
Plan ──< Tenant ──< Utilisateur
                 ├──< Salarie ──< Contrat
                 │            ├──< Conge
                 │            ├──< Acompte
                 │            └──< BulletinPaie >── PeriodePaie
                 ├──< Journalier ──< FeuillePaieJournalier
                 ├──< Site ──< AffectationSite
                 │          └──< Pointage
                 └──< Prestataire ──< ContratPrestation
                                  ├──< FacturePrestataire ──< PaiementPrestataire
```

Lecture : `──<` signifie « possède plusieurs ». Un tenant possède plusieurs salariés ; un salarié possède plusieurs bulletins ; chaque bulletin se rattache à une période.

## La table centrale : BulletinPaie

Avec ses 104 colonnes, elle mérite une explication. Elle ne stocke pas seulement le résultat, mais **tous les éléments intermédiaires** du calcul, pour trois raisons :
1. **Traçabilité légale** — un bulletin émis doit pouvoir être reconstitué à l'identique des années plus tard.
2. **Affichage** — le bulletin imprimé montre le détail ligne par ligne.
3. **Indépendance** — si les taux changent, les anciens bulletins gardent leurs valeurs d'origine.

Les colonnes se regroupent en : éléments bruts (salaire de base, heures sup, primes, indemnités), bases de cotisation (base CNSS, base CNAMGS…), cotisations salariales et patronales, et résultats (brut, net imposable, net à payer).

---

# Partie 2 — Règles métier : le calcul de la paie

> Références réglementaires : Code Général des Impôts du Gabon, Décret 578/PR/MDSFPSSN, Arrêté 037/METPS, et Convention Collective du BTP.
> Tout le moteur est dans `calculs_paie.py`.

## 2.1 — Les constantes réglementaires (Gabon 2026)

| Cotisation | Taux salarié | Taux patronal | Plafond mensuel |
|------------|--------------|---------------|-----------------|
| **CNSS** (sécurité sociale) | 5 % | 18 % | 1 500 000 FCFA |
| **CNAMGS** (assurance maladie) | 2 % | 4,1 % | 2 500 000 FCFA |
| **FNH** (habitat) | — | 3 % | 1 500 000 FCFA |
| **CFP** (formation prof.) | — | 0,5 % | — |
| **TCS** (taxe complémentaire) | 5 % | — | exonération sous 150 000 |

**Exonérations** : prime de transport exonérée d'IRPP jusqu'à 100 000 FCFA, et de CNSS jusqu'à 35 000 FCFA. Indemnité de logement plafonnée à 40 % du brut ou 250 000 FCFA.

## 2.2 — Le barème IRPP (impôt sur le revenu)

Barème mensuel progressif par tranches, après un **abattement de 20 %** et application du **quotient familial** (nombre de parts) :

| Tranche (FCFA/part) | Taux |
|---------------------|------|
| 0 – 125 000 | 0 % |
| 125 001 – 160 000 | 5 % |
| 160 001 – 225 000 | 10 % |
| 225 001 – 300 000 | 15 % |
| 300 001 – 430 000 | 20 % |
| 430 001 – 625 000 | 25 % |
| 625 001 – 916 667 | 30 % |
| au-delà | 35 % |

L'IRPP se calcule par part puis se multiplie par le nombre de parts.

## 2.3 — L'enchaînement du calcul d'un bulletin

La fonction `calculer_bulletin()` suit un ordre précis (l'ordre compte, car chaque étape dépend des précédentes) :

1. **Éléments bruts** — récupération du salaire de base, heures sup, primes, indemnités.
2. **Salaire brut** — somme de tous les éléments imposables moins les absences.
3. **CNSS** — sur le brut moins transport exonéré, plafonné à 1,5 M.
4. **CNAMGS** — base ajustée (logement imposable, primes exclues), plafonnée à 2,5 M.
5. **FNH** — sur base CNSS moins logement.
6. **CFP** — idem FNH.
7. **TCS** — base spécifique (certaines primes exclues, cotisations déduites), moins exonération 150 000.
8. **Net avant IRPP** — brut moins cotisations salariales.
9. **IRPP** — sur base imposable, avec quotient familial.
10. **Net à payer** — net après IRPP, plus éléments non imposables (panier, transport net…), moins acompte.

**Point d'attention** : les bases de calcul diffèrent d'une cotisation à l'autre (le logement, certaines primes, le transport entrent ou non selon la cotisation). C'est la partie la plus subtile et la plus sujette aux erreurs si on modifie le code — toute modification doit être validée par les tests.

## 2.4 — Le régime des heures supplémentaires (BTP, 48h/semaine)

Le BTP gabonais suit un régime à 48h hebdomadaires, traduit en mensuel :

| Élément | Heures/mois | Coefficient |
|---------|-------------|-------------|
| Heures normales | 173,33 h | × 1,0 |
| Heures sup +10 % (structurelles) | 17,33 h | × 1,10 |
| Heures sup +30 % (structurelles) | 17,33 h | × 1,30 |
| Heures +40 % (nuit/dimanche) | variable | × 1,40 |
| Heures +70 % (jours fériés) | variable | × 1,70 |

Le **taux horaire de base** = salaire de base ÷ 173,33. Les majorations s'appliquent dessus. Les heures à +40 % et +70 % sont détectées automatiquement via le module des jours fériés (`jours_feries.py`) lors du pointage.

## 2.5 — Les règles spécifiques de la Convention Collective BTP

Ces calculs sont dans `calculs_paie.py` et référencent les articles de la convention :

**Prime d'ancienneté (Art. A.46)** — après 2 ans : 2 % du salaire de base, +1 % par année supplémentaire, plafonnée à 30 %.

**Préavis (Art. A.30.3)** — barème par ancienneté, de 15 jours (moins d'1 an) à 190 jours+ (26 ans et plus).

**Indemnité de services rendus (Art. A.32)** — en cas de licenciement après 2 ans, basée sur la moyenne des 12 derniers mois : 20 % par an (2-10 ans), 26 % (10-15 ans), 30 % (15-20 ans), 35 % (au-delà).

**Permissions familiales (Art. A.41)** — jours exceptionnels NON déduits du congé annuel (mariage, décès, naissance…), de 1 à 5 jours selon l'événement.

## 2.6 — Le calcul de l'allocation de congés

L'allocation de congés (présente dans la route `/conges`) suit l'Art. 213 :
- Base = max(somme des bruts sur 12 mois, dernier brut × 12) ÷ 288
- La **prime de transport est exclue** de cette base (Art. 213 al. 3)
- Allocation = base × jours acquis

## 2.7 — Les journaliers : un calcul distinct

Les journaliers ne suivent pas le calcul mensuel. Ils sont payés selon les jours/heures pointés × leur taux, avec un traitement social allégé. Leur paie est gérée séparément (`FeuillePaieJournalier`) du circuit des bulletins mensuels.

---

# Partie 3 — Points de vigilance pour faire évoluer le code

1. **Ne jamais oublier le `tenant_id`** dans une requête. C'est ce qui garantit qu'un client ne voit pas les données d'un autre. Toutes les requêtes filtrent par `tenant_id`.

2. **Les bases de cotisation sont toutes différentes.** Avant de modifier un calcul, comprendre quelle prime/indemnité entre dans quelle base. Une erreur ici fausse les bulletins.

3. **Les bulletins émis sont figés.** Si un taux change, créer une nouvelle logique pour les nouveaux bulletins sans recalculer les anciens.

4. **Les tests sont le filet de sécurité.** Le projet a une large couverture de tests d'intégration (dont les calculs de paie). Toute modification du moteur doit passer les tests : `python -m pytest tests/ -q`.

5. **Les taux réglementaires changent.** Les constantes en haut de `calculs_paie.py` (taux CNSS, barème IRPP…) doivent être revues chaque année selon les lois de finances gabonaises.

6. **Les dates des fêtes musulmanes sont approximatives** dans `jours_feries.py` (calendrier lunaire). À confirmer chaque année.

---

*Document généré à partir d'un examen direct du code (`models.py`, `calculs_paie.py`, `jours_feries.py`). En cas de divergence entre ce document et le code, le code fait foi — et ce document devrait être mis à jour.*
