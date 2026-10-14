# 🔒 Audit Sécurité & Bugs — PaieGabon SaaS
**Date :** 14 octobre 2026 · **Périmètre :** dépôt `paie-gabon-saas-main` complet (y compris pointage PWA, carrière Phases 1-3)
**Méthode :** analyse statique (AST, pyflakes), revue manuelle des routes, **reproduction de chaque constat** par un test qui échoue sur le code d'origine puis passe sur le code corrigé.

---

## 0. Synthèse

Le socle reste sérieux (SQL paramétré, CSRF, en-têtes de sécurité, `SECRET_KEY` obligatoire, uploads filtrés, réinitialisation de mot de passe propre, API REST isolée, webhooks revérifiés côté serveur). L'audit a néanmoins trouvé **une faille de droits importante**, **une XSS stockée pouvant atteindre le super-administrateur**, et **des bugs de congés qui faussent la paie**.

**Résultat :** 27 tests de non-régression ajoutés (`tests/test_audit_2026_10.py`) — **24 échouent sur le code d'origine**, tous passent après correction. Suite complète : **599 tests passent** (les 2 échecs restants sont la résolution DNS de gmail.com, propre au bac à sable de test, sans lien avec le code).

| # | Constat | Gravité | Statut |
|---|---|---|---|
| S1 | Rôles « lecture seule » (Comptable, Directeur, Lecture) pouvant écrire sur ~30 routes | 🔴 Élevée | ✅ Corrigé |
| S2 | XSS stockée via noms injectés dans du JavaScript — dont la dénomination d'entreprise côté **super-admin** | 🔴 Élevée | ✅ Corrigé |
| B1 | Congés : erreur 500 à chaque création, double comptage, écrasement d'un congé approuvé | 🔴 Élevée (paie) | ✅ Corrigé |
| S3 | Référence croisée entre entreprises (`categorie_id`, `site_id`, `contrat_id`) | 🟠 Moyenne | ✅ Corrigé |
| S4 | Redirection ouverte à la connexion (`?next=//site-pirate.com`) | 🟠 Moyenne | ✅ Corrigé |
| S5 | PWA pointage : PIN 4 chiffres sans limite d'essais, jeton non révoqué au changement de PIN | 🟠 Moyenne | ✅ Corrigé |
| B2 | Feuille journalier repayable ; paiement groupé ignorant les avances | 🟠 Moyenne | ✅ Corrigé |
| B3 | Bulletins : paiement d'un brouillon, re-validation d'un payé, **date de paiement jamais enregistrée** | 🟠 Moyenne | ✅ Corrigé (+ migration) |
| S6 | CinetPay : montant vérifié absent/nul accepté ; 2 chemins d'activation sans contrôle de montant | 🟡 Faible-Moyenne | ✅ Corrigé |
| S7 | Collaborateur de cabinet désassigné gardant l'accès jusqu'à reconnexion | 🟢 Faible | ✅ Corrigé |
| B4 | `migration_sgtg.py` : `utcnow` non importé (plantage) | 🟢 Faible | ✅ Corrigé |
| M1 | `blueprints/tenant.py` (10 742 lignes) : code mort masqué par le package `blueprints/tenant/` | Maintenance | ✅ Supprimé |
| R1 | Webhook Airtel accepté sans signature si secret absent et environnement non marqué « production » | 🟡 Config | ⚠️ À vérifier sur le VPS |
| R2 | Le super-admin peut supprimer un bulletin VALIDÉ | Gouvernance | ⚠️ Décision à prendre |
| R3 | Taux d'acquisition des congés unifié sur la constante du code (2,5 j/mois) | Réglementaire | ⚠️ À valider |

---

## 1. Détail des corrections

### 🔴 S1 — Rôles en lecture seule pouvant écrire
La matrice de permissions (`models.py`) déclare COMPTABLE et DIRECTEUR en lecture seule, et LECTURE sans permission. Mais ~30 routes POST du blueprint tenant ne vérifiaient pas les droits. **Reproduit :** un comptable supprimait un congé et marquait un journalier comme payé.
Routes concernées notamment : congés (approuver, refuser, modifier, supprimer), acomptes (valider, annuler, supprimer), paie journaliers (payer, payer la sélection, générer, modifier/supprimer une feuille, avances), pointage (sauvegarder), chantiers (créer, modifier, activer, affecter, permuter, retirer, pointage rapide), suppression du logo.
**Correctif :** garde-fou global `before_request` (`blueprints/tenant/__init__.py`) — toute écriture est refusée sans droit d'écriture, sauf liste blanche explicite (simulations, aperçus PDF, 2FA de son compte, paiement de l'abonnement). Les routes futures sont protégées d'office.
*Rectificatif :* le module prestataires était déjà correctement protégé (`@can_edit`) ; le même garde-fou y est ajouté en simple défense en profondeur.

### 🔴 S2 — XSS stockée dans les attributs JavaScript
Jinja échappe le HTML, mais dans `onclick="…'{{ nom }}'…"` le navigateur décode `&#39;` en `'` **avant** d'exécuter le JavaScript. Un nom comme `X');alert(1);//` s'exécutait.
Cas le plus grave : `admin/tenant_detail.html` insérait la **dénomination de l'entreprise** dans le bouton « Suspendre » du super-admin → une entreprise inscrite avec une dénomination piégée exécutait du code dans la session super-admin.
**Correctif :** 15 gestionnaires réécrits — les données passent par des attributs `data-*` (échappement HTML correct) et le JS lit `this.dataset.*`. Fichiers : `salarie_detail`, `salarie_rupture`, `prestataire_detail`, `site_detail`, `pointeurs`, `utilisateurs`, `medailles`, `admin/plans`, `admin/tenant_detail` (+ `tojson` pour la confirmation par saisie, qui échouait aussi pour toute dénomination contenant `&` ou `"`).

### 🔴 B1 — Congés (impact direct sur la paie)
Reproduit pas à pas :
- **Création :** erreur 500 systématique (`conge.date_debut` n'existe pas — le champ est `date_depart`), congé pourtant enregistré.
- **Double approbation :** 10 jours comptés 20. Or l'**allocation de congé du bulletin** et l'**indemnité compensatrice du solde de tout compte** additionnent ces jours → sur-paiement d'un côté, sous-évaluation de l'autre.
- **2ᵉ demande dans l'année :** réutilisait et **écrasait** la 1ʳᵉ (dates remplacées, statut repassé à DEMANDÉ, jours hérités → une demande de 5 jours portait 25 jours).
- **Suppression / refus d'un congé approuvé :** solde non restitué ; suppression suivie d'une erreur 500.
- Solde annuel créé à 2,0 j/mois à un endroit, 2,5 j/mois à un autre.
**Correctif (`blueprints/tenant/conges.py`) :** une ligne de solde annuel (`statut = SOLDE`, sans dates) + une ligne par demande ; approbation idempotente avec `jours_pris` = durée exacte ; refus/suppression/modification restituent ou ajustent le solde ; validation des dates et du salarié. Notifications et fiche salarié alignées.

### 🟠 S3 — Référence croisée entre entreprises
`categorie_id`, `site_id`, `contrat_id` reçus du formulaire étaient enregistrés sans vérifier leur appartenance au tenant (création/modification salarié et contrat, conversion journalier, promotion, journaliers, prestataires). **Correctif :** helper central `core.id_du_tenant()` appliqué à 17 points.

### 🟠 S4 — Redirection ouverte
`next.startswith("/")` laissait passer `//site-pirate.com` et `/\site-pirate.com`. **Correctif :** helper `core.chemin_interne()` (refuse schémas, hôtes, antislash, caractères de contrôle), appliqué à la connexion, à la suppression de pointage et au paramètre `retour` de la Phase 3.

### 🟠 S5 — PWA pointage
- Limitation des essais de connexion : **par code pointeur** 5/min et 10/h (9 000 PIN ≈ 37 jours), **par IP** 60/min (large : les opérateurs mobiles regroupent beaucoup d'abonnés derrière une même IP). Réponse JSON 429.
- Le jeton embarque une empreinte du PIN : **régénérer le PIN déconnecte l'ancien téléphone**. Transition douce : les jetons émis avant ce correctif restent valides jusqu'à leur expiration (30 j) ; la file hors-ligne n'est jamais perdue (la déconnexion la conserve).

### 🟠 B2 — Paie journaliers
Une feuille déjà payée pouvait être repayée : les avances plus récentes étaient alors « régularisées » sur une paie déjà versée. Le paiement groupé, lui, ignorait les avances, le journal d'audit et la caisse. **Correctif :** fonction commune `_payer_feuille_journalier()` + garde d'idempotence.

### 🟠 B3 — Bulletins
- Un bulletin BROUILLON pouvait être marqué PAYÉ (unitaire **et par lot**) : ni numéro officiel, ni acomptes déduits.
- Valider un bulletin PAYÉ le rétrogradait en VALIDÉ.
- **La date de paiement saisie n'était jamais enregistrée** : la colonne `bulletins_paie.date_paiement` n'existait pas (attribut Python temporaire). Ajoutée au modèle, à la migration automatique au démarrage, et à `migrations/migration_audit_2026_10.sql`. Vérifié sur PostgreSQL 16.

### 🟡 S6 — CinetPay
Le webhook sautait le contrôle de montant si le montant vérifié était absent ou nul ; la page de retour et l'interrogation de statut activaient l'abonnement sans aucun contrôle de montant. **Correctif :** `_cinetpay_montant_conforme()` appliqué aux trois chemins. (Risque concret limité : le montant est fixé côté serveur à l'initialisation.)

---

## 2. À traiter par toi

**R1 — Webhook Airtel.** Sans `AIRTEL_WEBHOOK_SECRET`, la signature n'est vérifiée que si `FLASK_ENV`, `APP_ENV` ou `AIRTEL_ENV` vaut `production`. La revérification du statut auprès d'Airtel limite le risque, mais vérifie la configuration du service.

**R2 — Suppression d'un bulletin validé par le super-admin.** Possible aujourd'hui (support). Un bulletin validé est un document officiel numéroté : à garder, journaliser davantage, ou interdire — ta décision.

**R3 — Taux d'acquisition des congés.** Le code définit `JOURS_PAR_MOIS = 2.5` (30 j/an) dans `conges_avance.py`, utilisé par le bilan et le solde de tout compte ; la création de congé utilisait 2,0 en dur. J'ai unifié sur la constante du code **sans trancher la règle légale** : merci de valider la valeur au regard du Code du travail gabonais (et de la convention applicable).

**Données existantes.** `migrations/diagnostic_audit_2026_10.sql` (lecture seule, validé sur PostgreSQL 16) liste : congés aux jours erronés, demandes ayant hérité de jours, **bulletins du mois concerné** (allocation de congé possiblement gonflée), bulletins payés sans numéro, références croisées, noms piégés. Aucune correction automatique : chaque cas touche l'historique de paie.

---

## 3. Points solides confirmés
SQL 100 % paramétré · uploads (liste blanche, taille, MIME fixé, téléchargement en pièce jointe) · jeton de réinitialisation aléatoire 32 octets, 2 h, usage unique, sans énumération des comptes · API REST isolée par tenant + limitation de débit · CSP, HSTS, `nosniff`, cookies HttpOnly/SameSite/Secure · `SECRET_KEY` obligatoire en production · limitation de la connexion (20/min, vérifiée) · webhooks revérifiant le statut auprès du prestataire.
