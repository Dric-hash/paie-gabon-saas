# Journal des modifications — PaieGabon

Toutes les évolutions notables de l'application sont consignées ici.
Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).

---

## [Non publié] — Convention Commerce & Déclaration Annuelle des Salaires

### Ajouté

#### Déduction automatique des avances sur les factures
- Les **avances sont désormais déduites automatiquement des factures**, par
  **chantier / site** : une avance liée à un chantier s'impute sur les factures
  de ce chantier (la plus ancienne d'abord) ; une avance sans chantier alimente
  un pool général. Le reliquat non imputé reste « disponible ».
- Le **solde se calcule automatiquement en XAF**, donc correctement même si
  l'avance a été perçue en **EUR, USD ou MAD** (conversion au taux retenu).
- **Tableau de bord du prestataire** : carte « Solde net dû » (= facturé − payé −
  avances) ; la carte « Avances perçues » indique la part déjà déduite et la part
  encore disponible ; chaque facture affiche son solde après déduction.
- **Impression de chaque facture** : bloc des avances du chantier déduites + ligne
  « Solde à payer » calculée automatiquement.


- **Factures multi-lignes** : chaque facture peut comporter plusieurs lignes de
  détail (désignation, quantité, unité — dont m² —, prix unitaire) ; le HT est la
  somme des lignes. Nouveau modèle `LigneFacturePrestataire`.
- **Workflow** : une facture créée est en **Brouillon** (modifiable et
  re-modifiable), doit être **validée** pour devenir **payable** ; une fois
  validée, elle est figée. Le **paiement est bloqué** tant que la facture n'est
  pas validée.
- **Impression de la facture** (`/prestataires/factures/<id>/imprimer`) : en-tête
  entreprise, prestataire, lignes de détail, totaux (HT/TVA/TTC/retenue/net),
  devise **et** équivalent XAF, statut, signatures.
- **Devise lisible** : à la saisie et à l'affichage, les montants apparaissent
  dans la devise choisie (dirham, euro, dollar…) avec l'équivalent FCFA, au lieu
  d'être toujours libellés en F CFA.
- Migrations Alembic `e5f6a7b8c9d0` (colonnes de validation + table lignes) ;
  colonnes posées en prod par `run_migrations()`, table par `create_all()`.
  Les factures déjà saisies (ancien statut « En attente ») passent en « Validée »
  pour rester payables.
- **Override administrateur** : l'administrateur du compte peut **modifier ou
  supprimer** une facture même validée ou payée (boutons dédiés sur la fiche).
  La suppression retire aussi les paiements et lignes liés. Réservé au rôle admin
  (route `facture_supprimer` protégée par `admin_only`).


- Nouveau modèle `AvancePrestataire` : sommes versées à un prestataire ou
  sous-traitant **hors facture** (avances de démarrage, acomptes de chantier).
- **Cycle de validation** : une avance est *En attente* (modifiable et
  supprimable), puis **validée par le chef de chantier** (saisie de son nom) ;
  une fois validée elle est **figée** — ni modifiable ni supprimable. La
  dernière avance en attente est mise en avant sur la fiche pour validation.
- **Chantier / Site** : on peut indiquer sur quel chantier une avance **ou** une
  facture a été perçue/établie.
- **Multi-devises** (prestataires étrangers) : XAF, EUR, USD, MAD. Conversion au
  jour affichée **à la saisie** et **à l'impression** ; EUR à parité fixe FCFA
  (655,957), USD/MAD au taux du jour (API + cache quotidien `taux_devises`, repli
  configurable `TAUX_USD_XAF` / `TAUX_MAD_XAF`, taux ajustable à la main).
- **BTP** : factures établies **au m²** (surface × prix unitaire → HT) et
  paiements suivis **au pourcentage de réalisation**.
- Fiche prestataire : section « Avances » (création/édition/validation/
  suppression), carte de synthèse, et bouton « Imprimer le relevé ».
- **Relevé imprimable par prestataire/sous-traitant** (`/prestataires/<id>/releve`)
  : en-tête entreprise, identité, synthèse, **liste des avances** (devise +
  équivalent XAF + statut), et tableau des factures. Impression directe.
- Nouveau module `devises.py` ; nouvel endpoint `/api/prestataire/taux-devise`.
- Migrations Alembic `c3d4e5f6a7b8` (table avances) et `d4e5f6a7b8c9` (validation,
  site, devises, m²/%, table `taux_devises`) ; colonnes posées en prod par
  `run_migrations()`.
- 12 tests d'intégration (`tests/test_prestataires_avances.py`).


#### Convention Collective des professionnels du pétrole (SGEPP/GPP, 17 juin 1983)
- Intégration de la Convention Pétrole (stockage/distribution, hors transport et
  commerce de détail) :
  - **Prime d'ancienneté** (Art. 46.5) : **5 %** après 2 ans, +1 %/an, plafond 30 %
    (barème distinct du BTP/Commerce qui démarrent à 2 %).
  - **Indemnité de services rendus** (Art. 32) : 20 / 25 / 30 / 40 % selon
    l'ancienneté (0-5 / 6-10 / 11-15 / ≥ 16 ans), minimum 1 an (ouvrier/employé)
    ou 2 ans (maîtrise/cadre).
  - **Préavis** (Art. 30.3) : renvoi au barème du Code du travail.
  - **Heures supplémentaires** (Art. 38.2) : +20 % (41ᵉ-48ᵉ h), +35 % (> 48ᵉ h),
    **+30 % repos/dimanche/férié de jour (nouvelle case `heures_sup_30b`)**,
    +50 % nuit ouvrable, +100 % nuit dimanche/férié. Coefficients désormais
    résolus par convention (`coeffs_heures_sup`), sans régression sur BTP/Commerce.
  - **Ventilation mensuelle** dédiée (`ventiler_heures_mois_petrole`) et
    distribution hebdomadaire (`distribuer_heures_semaine_petrole`).
  - **Permissions familiales** (Art. 41) : barème identique au BTP/Commerce.
  - **Primes** : assiduité forfaitaire 5 000 F (Art. 49), naissance 10 000 F
    (Art. 58), occasionnelle 15 % (Art. 56).
  - **Grille de classification** (Annexe n°2) : catégories A→I, AMI→AMS, CP0→HC.
    ⚠️ Montants de 1983 obsolètes : l'import applique un **plancher SMIG** et
    signale les montants à actualiser.
- 5ᵉ case d'heures supplémentaires `heures_sup_30b` ajoutée sur `Pointage` et
  `BulletinPaie` (montant + base/taux), migration Alembic `b2c3d4e5f6a7`.
  Nulle pour BTP/Commerce/AUCUNE — aucun impact sur les paies existantes.
- 20 tests unitaires (`tests/test_convention_petrole.py`), suite complète à 360 tests.


#### Convention Collective du Secteur Commerce (Gabon)
- Intégration complète de la Convention Collective du Commerce (Libreville, 8 juin 1988) :
  - **Préavis** (Art. A.30.3) : barème en jours par tranche d'ancienneté.
  - **Indemnité de services rendus** (Art. A.32) : 20 % / 25 % / 30 % / 35 %
    selon l'ancienneté (2-5 / 5-10 / 10-20 / > 20 ans).
  - **Prime d'ancienneté** (Art. A.46.5) : 2 % après 2 ans, +1 %/an, plafond 30 %.
  - **Heures supplémentaires** (Art. A.38) : calcul **par jour** (8 h +10 %,
    9ᵉ h et + +30 %), nuit +70 %, jour férié/repos +40 % (jour) / +140 % (nuit).
  - **Permissions familiales** (Art. A.41).
  - **Grille de salaires conventionnelle** (E1-E7, AM1-AM2, C1-C4) importable
    en un clic dans les catégories d'emploi.
- Nouveau champ `Tenant.convention` (`AUCUNE` | `BTP` | `COMMERCE`) et
  **dispatcher par convention** dans `calculs_paie.py` (le calcul applique
  automatiquement le bon barème ; la convention BTP existante est préservée).
- Paramètres → carte « Convention collective » : sélecteur + bouton
  « Importer la grille de salaires Commerce ».

#### Déclaration Annuelle des Salaires (DAS) — réservée à l'abonnement Cabinet
- Nouveau module `declaration_das.py` : agrégation annuelle des bulletins
  validés/payés par salarié et génération du fichier Excel officiel
  (onglets **Paramètres**, **ID19** détail par salarié, **ID20** récapitulatif).
- Génération **en valeurs** via openpyxl (aucune dépendance LibreOffice au
  runtime — même méthode que l'export CNSS, compatible Railway).
- Volet **honoraires** alimenté depuis le module Prestataires :
  agrégation des factures de l'exercice par prestataire, ventilation
  **ID23** (versés au Gabon, résidents — retenue locale) /
  **ID24** (versés hors du Gabon, étrangers — retenue étrangers), art. 189 CGI.
  Feuille « ID23-24 - Honoraires » + bloc dédié dans l'écran et le récapitulatif.
- Écran `/declaration-das` : sélecteur d'exercice, cartes de synthèse,
  détail par salarié et par prestataire, téléchargement Excel.
- Nouveau décorateur `core.plan_required("CABINET")` : restreint la DAS à
  l'abonnement Cabinet (100 000 FCFA) ; les autres plans sont redirigés vers
  la page d'abonnement. Entrée de menu visible uniquement pour ce plan.
- **Constantes de ventilation** imposable / non imposable externalisées et
  documentées en tête de `declaration_das.py` (`AVANTAGES_NATURE`,
  `BRUT_CONGE`, `INDEMNITES_IMPOSABLES_657`, `NON_IMPOSABLE_*`,
  `IMPOTS_RETENUS`, `TAUX_TVA`, `TAUX_RETENUE_LOCAL`, `TAUX_RETENUE_ETRANGER`,
  `STATUTS_FACTURES_RETENUS`, `CATEGORIES_HONORAIRES`).

### Modifié
- `conges_avance.py` : `calculer_solde_tout_compte()` prend un paramètre
  `convention` et applique le barème d'indemnité de services rendus
  correspondant (les appels passent désormais `tenant.convention`).
- `calculs_paie.py` : ajout des fonctions Commerce et du dispatcher générique
  (`prime_anciennete`, `preavis_jours`, `indemnite_services_rendus`,
  `permissions_familiales`, `distribuer_heures_semaine`).
- **Simulateur de paie** : le toggle « Mode BTP » est remplacé par un
  sélecteur de **convention** (Aucune / BTP / Commerce) pré-rempli avec la
  convention de l'entreprise ; les libellés des heures supplémentaires
  s'adaptent à la convention choisie.

### Corrigé
- **Pointage / Simulateur BTP** : nouvel algorithme de ventilation mensuelle des
  heures (`ventiler_heures_mois_btp`) qui analyse chaque jour **indépendamment**
  puis applique le filtre réglementaire **semaine par semaine** : 0→40h normales,
  40→44h +10% (4h max), 44h et au-delà +30%, nuit +40%, dimanche/férié travaillé
  +70% (intégralité, sans présumer « 8h normales + sup »), férié chômé en semaine
  = 8h normales. Branché dans le cumul mensuel du pointage pour les tenants BTP.
  Correction au passage : un dimanche travaillé est désormais ventilé en +70%
  (et non +40%).
- **Simulateur** : la section « Simulations avancées » (onglets Augmentation /
  Net→Brut / Comparer scénarios) était placée dans `{% block scripts %}`, que
  `base.html` injecte **hors du `<main>`** — elle s'affichait donc sans la marge
  de la sidebar et passait sous celle-ci à gauche (« bas caché côté gauche »).
  Section déplacée dans `{% block content %}`.
- **Simulateur** : correction du débordement horizontal (« grid blowout » des
  colonnes `1fr`) qui faisait passer le contenu sous la sidebar fixe — le bas
  de page était masqué côté gauche. Grilles passées en `minmax(0,1fr)` +
  `min-width:0`, panneaux empilés sous 1100 px, et `min-width:0` sur le `<main>`
  (garde global anti-débordement).
- **Simulateur** : les heures supplémentaires sont désormais prises en compte
  quelle que soit la convention (auparavant ignorées hors « Mode BTP »).

- **Écran de saisie du bulletin** : ajout d'un détail repliable de la
  répartition **semaine par semaine** (heures pointées, normales, +10/+30/+40/+70%)
  pour visualiser la ventilation BTP avant de valider le bulletin.

- **Refonte UI — Saisie du bulletin (`bulletin_saisie.html`)** : migration vers
  **Tailwind CSS** (abandon du CSS inline). Sections en **cartes** (`rounded-lg`
  `shadow-md`), titres bleu marine, fonds gris doux, **tableaux** propres avec
  défilement horizontal sur mobile, boutons avec effet de survol, et mise en page
  **mobile-first** (colonnes empilées sur téléphone, 2 colonnes sur grand écran).
  Tous les identifiants et la logique de calcul en temps réel sont préservés.

- **Refonte UI — Page Pointage (`pointage.html`)** : migration vers Tailwind CSS,
  mise en page **mobile-first** (KPIs 2 colonnes sur mobile / 4 sur desktop ;
  listes Mensuels et Journaliers empilées sur téléphone, côte à côte sur grand
  écran), en-tête adaptatif, champs d'heures et boutons en classes Tailwind avec
  effets de survol. IDs et logique JS (présence, calcul du gain, sélection)
  préservés.

### Base de données (migration automatique au démarrage)
- `ALTER TABLE tenants ADD COLUMN convention VARCHAR(20) DEFAULT 'AUCUNE'`
  (appliquée par `run_migrations()` — idempotente, aucune action manuelle).

### Tests
- `tests/test_convention_commerce.py` : barèmes préavis, indemnité de services
  rendus, ancienneté, heures sup, dispatcher, grille.
- `tests/test_declaration_das.py` : agrégation salaires + honoraires, cohérence
  comptable, génération Excel, gating de l'abonnement Cabinet.
- **309 tests passent** (aucune régression).

### Notes de ventilation (ajustables)
- Le **salaire brut de présence** inclut volontairement la part transport
  exonérée : la DAS déclare le **brut**, l'exonération se reflétant dans les
  retenues (TCTS/IRPP) et non dans la base déclarée.
- La **nationalité** est mappée au mieux (Gabon → 1, CEMAC → 2, sinon → 4)
  car l'application stocke un texte libre ; ajustable manuellement si besoin.
