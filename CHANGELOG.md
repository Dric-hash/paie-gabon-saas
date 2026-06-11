# Journal des modifications — PaieGabon

Toutes les évolutions notables de l'application sont consignées ici.
Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).

---

## [Non publié] — Convention Commerce & Déclaration Annuelle des Salaires

### Ajouté

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
