# ⚖️ Analyse de conformité légale — PaieGabon
**Date :** 13 juin 2026
**Textes de référence analysés :**
- **Code du travail 2021** — Loi n°022/2021 du 19 novembre 2021 (109 p.)
- **Convention collective du secteur BTP** — République gabonaise, octobre 1983 (signée le 11/10/1983)

**Méthode :** lecture intégrale des deux textes, puis croisement article par article avec le code de l'application (`calculs_paie.py`, `conges_avance.py`, `documents_rh.py`, `models.py`).

> ⚠️ Avertissement honnête : je ne suis pas juriste. Cette analyse compare la *logique implémentée* aux *textes fournis*. Les points marqués « À VÉRIFIER » dépendent de textes réglementaires (décrets, arrêtés de taux) non inclus dans les deux PDF, ou d'un choix de politique RH qui t'appartient. Fais valider les changements touchant les montants par un comptable/juriste gabonais avant production.

---

## Synthèse des écarts

| # | Sujet | Base légale | Gravité |
|---|-------|-------------|---------|
| L1 | Indemnité de licenciement non versée sous 2 ans d'ancienneté | Code Art. 87, 89, 90 | 🛑 **Non-conformité** |
| L2 | Ancienneté tronquée à l'année entière (fractions ignorées) | Code Art. 90 | 🛑 **Non-conformité** |
| L3 | Cause de cessation non prise en compte (licenciement/démission/retraite/décès) | Code Art. 87-89 | ⚠️ Important |
| L4 | Congé : taux 2,5 j/mois appliqué à tous (légal adulte = 2 j/mois) | Code Art. 222 | ⚠️ À arbitrer |
| L5 | Jour de congé supplémentaire par enfant (mère) non implémenté | Code Art. 223 | ⚠️ Manquement |
| L6 | Allocation de congé saisie manuellement au lieu d'être calculée (moyenne 12 mois) | Code Art. 225 | ⚠️ Manquement |
| L7 | Revenu Minimum Mensuel (RMM) non géré | Code Art. 180 | ⚠️ Manquement |
| L8 | Congé de maternité (14 semaines) non modélisé comme type dédié | Code Art. 208-211 | 🛡️ Amélioration |
| L9 | Préavis : cohérence Code (Art. 82) ↔ convention BTP (A.30.3) + règle de faveur | Code Art. 80, 82 | 🛡️ À vérifier |
| L10 | Taux heures sup / nuit à confirmer contre l'arrêté en vigueur | Code Art. 196-197, 202 | 🛡️ À vérifier |

---

# 🛑 L1 — Indemnité de licenciement : non versée sous 2 ans (NON-CONFORME)

### Ce que dit la loi
Le Code 2021 distingue **deux** indemnités de rupture :
- **Indemnité de licenciement** (Art. 87) : due **sans condition d'ancienneté** à tout salarié licencié pour un motif **autre que la faute lourde** (hors période d'essai).
- **Indemnité de services rendus** (Art. 88) : due au départ en **retraite**, aux **ayants droit** d'un salarié décédé, et au salarié **démissionnaire justifiant ≥ 2 ans** (ou départ amiable).
- Elles **ne sont pas cumulables** (Art. 89).
- Chacune vaut au minimum **20 % de la moyenne mensuelle du salaire global des 12 derniers mois, par année de présence** (Art. 90). C'est un **minimum** : les conventions peuvent prévoir mieux (le barème progressif BTP A.32 — 20/26/30/35 % — s'applique alors car plus favorable).

### Ce que fait l'app aujourd'hui
Dans `conges_avance.py`, `calculer_solde_tout_compte` calcule un champ `indem_licenciement` en appelant **`indemnite_services_rendus()`** — la fonction des *services rendus*, qui renvoie **0 si `anciennete_annees < 2`** :

```python
# calculs_paie.py — calculer_indemnite_services_rendus_btp
if anciennete_annees < 2 or moyenne_12_mois <= 0:
    return 0.0
```

**Conséquence :** un salarié licencié (hors faute lourde) avec 1 an et demi d'ancienneté reçoit **0 FCFA** dans l'app, alors qu'il a légalement droit à `20 % × 1,5 × moyenne mensuelle`. C'est un risque de redressement prud'homal et de litige.

### Correctif — ajouter une vraie indemnité de licenciement + un aiguillage par cause

**(a)** Ajouter dans `calculs_paie.py` (après `indemnite_services_rendus`) :

```python
def indemnite_licenciement(moyenne_12_mois: float, anciennete_annees: float) -> float:
    """
    Indemnité de licenciement — Code du travail 2021, Art. 87 & 90.
    Due SANS condition d'ancienneté à tout salarié licencié pour un motif
    autre que la faute lourde (hors période d'essai).
    Minimum légal : 20 % de la moyenne mensuelle du salaire global des
    12 derniers mois, par année de présence continue (fractions comprises).
    """
    if moyenne_12_mois <= 0 or anciennete_annees <= 0:
        return 0.0
    return fcfa(moyenne_12_mois * 0.20 * anciennete_annees, 0)


def indemnite_rupture(convention, cause, moyenne_12_mois: float,
                      anciennete_annees: float) -> dict:
    """
    Aiguille vers la bonne indemnité de rupture selon la CAUSE de cessation,
    conformément au Code du travail 2021 (Art. 87 à 90).

    cause ∈ {"LICENCIEMENT", "RETRAITE", "DECES", "DEMISSION", "FAUTE_LOURDE"}

    Règles appliquées :
      • Non-cumul (Art. 89) : licenciement OU services rendus, jamais les deux.
      • Faveur (Art. 90)    : le barème conventionnel (BTP/Commerce) s'applique
                              s'il est plus avantageux que le minimum légal 20 %/an.
      • Licenciement (Art. 87) : AUCUNE condition d'ancienneté.
      • Services rendus (Art. 88) : retraite, décès, ou démission >= 2 ans.

    Returns: {"type": str|None, "montant": float}
    """
    cause = (cause or "").upper()
    if cause == "FAUTE_LOURDE" or moyenne_12_mois <= 0 or anciennete_annees <= 0:
        return {"type": None, "montant": 0.0}

    if cause in ("RETRAITE", "DECES") or (cause == "DEMISSION" and anciennete_annees >= 2):
        montant = indemnite_services_rendus(convention, moyenne_12_mois, anciennete_annees)
        return {"type": "SERVICES_RENDUS", "montant": montant}

    if cause == "LICENCIEMENT":
        legal = indemnite_licenciement(moyenne_12_mois, anciennete_annees)
        conv  = indemnite_services_rendus(convention, moyenne_12_mois, anciennete_annees)
        # On retient le plus favorable au salarié (Art. 90).
        return {"type": "LICENCIEMENT", "montant": max(legal, conv)}

    # Démission < 2 ans : aucune indemnité de rupture.
    return {"type": None, "montant": 0.0}
```

**(b)** Dans `conges_avance.py`, `calculer_solde_tout_compte` doit recevoir la **cause** et l'utiliser. Remplacer la signature et le bloc de calcul :

```python
def calculer_solde_tout_compte(salarie, bulletins_12mois, date_cessation=None,
                               convention="BTP", cause="LICENCIEMENT") -> dict:
    ...
    from calculs_paie import indemnite_rupture
    anciennete_annees = acquis_calc["anciennete_annees"]   # désormais fractionnaire (cf. L2)
    rupture = indemnite_rupture(convention, cause, base_calcul, anciennete_annees)
    indem_rupture = rupture["montant"]

    return {
        ...
        "cause_cessation":    cause,
        "type_indemnite":     rupture["type"],
        "indem_licenciement": indem_rupture,   # conservé pour compat. d'affichage
        "total_a_payer":      indemnite + indem_rupture,
    }
```

**(c)** Côté route (`blueprints/tenant.py`, ~ligne 3712), passer la cause choisie par l'utilisateur (un `<select>` LICENCIEMENT / DÉMISSION / RETRAITE / DÉCÈS sur l'écran de solde de tout compte).

---

# 🛑 L2 — Ancienneté tronquée à l'année entière (NON-CONFORME)

### Ce que dit la loi
Art. 90 : pour le calcul de l'indemnité, **les fractions d'année comptent** (le texte fixe un seuil en jours calendaires ; voir réserve ci-dessous). Tronquer à l'année entière sous-paie le salarié.

### Ce que fait l'app
`conges_avance.py` : `anciennete_annees = anciennete_jours // 365` → **division entière**. Un salarié à 4 ans et 11 mois est compté pour **4 ans**, perdant presque une année d'indemnité.

### Correctif — calculer une ancienneté fractionnaire

```python
    anciennete_jours = (date_ref - date_embauche).days
    annees_entieres  = anciennete_jours // 365
    jours_restants   = anciennete_jours % 365
    # Les fractions d'année comptent (Art. 90). On les exprime en douzièmes
    # d'année à partir des mois entiers restants (>= 30 jours = 1 mois).
    mois_fraction    = jours_restants // 30
    anciennete_annees = round(annees_entieres + mois_fraction / 12.0, 3)
```

> ⚠️ **À VÉRIFIER** : la formulation exacte du seuil (« 30 jours calendaires ») dans l'Art. 90 mérite d'être recoupée avec le texte publié au Journal Officiel — la version Droit Afrique est légèrement ambiguë sur ce point. Le principe (les fractions comptent, on ne tronque pas) est lui certain. Garde l'ancienneté **entière** pour le *barème* des services rendus BTP (les paliers 2/10/15/20 ans se lisent en années révolues) mais l'ancienneté **fractionnaire** pour le *multiplicateur* du montant.

---

# ⚠️ L3 — La cause de cessation n'est pas prise en compte

`calculer_solde_tout_compte` calcule toujours la même indemnité, quelle que soit la raison du départ. Or licenciement, démission, retraite et décès ouvrent des droits différents (Art. 87-89). Le correctif L1 (paramètre `cause` + `indemnite_rupture`) règle ce point. Prévois aussi le cas **faute lourde** → aucune indemnité de rupture (mais l'indemnité compensatrice de **congés** reste due, Art. 224).

---

# ⚠️ L4 — Taux de congé : 2,5 j/mois pour tout le monde

### Ce que dit la loi
Art. 222 : **2 jours ouvrables par mois** de service effectif pour les adultes (soit 24 j/an) ; **2,5 jours** pour les **moins de 18 ans**. L'ancienneté augmente la durée, **plafonnée à 2 mois** (Art. 223). La convention BTP (A.42) renvoie à la loi.

### Ce que fait l'app
`conges_avance.py` : `JOURS_PAR_MOIS = 2.5` appliqué à **tous**, plafond 30 j/an. C'est le **taux des mineurs** appliqué aux adultes.

### Analyse
2,5 j/mois reste **légal** (plus favorable que le minimum, ce qu'autorise l'Art. 222 « sauf dispositions plus favorables »). Mais :
1. Ce devrait être un **paramètre par tenant**, pas une constante codée en dur — toutes les entreprises BTP n'offrent pas 30 j/an.
2. Le taché **2,5 j doit rester** pour les salariés de moins de 18 ans, quel que soit le réglage du tenant.

### Correctif
Rendre le taux configurable (champ `jours_conge_par_mois` sur le `Tenant`, défaut **2.0**), avec surcharge mineurs :

```python
def taux_conge_mensuel(tenant, salarie) -> float:
    """Jours de congé acquis par mois (Art. 222)."""
    base = float(getattr(tenant, "jours_conge_par_mois", 2.0) or 2.0)
    # Les moins de 18 ans : 2,5 j/mois minimum, même si le tenant applique moins.
    if getattr(salarie, "date_naissance", None):
        from datetime import date
        age = (date.today() - salarie.date_naissance).days // 365
        if age < 18:
            return max(base, 2.5)
    return base
```

> Décision qui t'appartient : si tes clients BTP offrent réellement 30 j/an, garde 2,5 en défaut — mais documente-le comme un **avantage conventionnel**, pas comme le minimum légal.

---

# ⚠️ L5 — Jour de congé supplémentaire par enfant (mère)

Art. 223 : **la mère de famille a droit à 1 jour de congé supplémentaire par an et par enfant à charge de moins de 16 ans**. Non implémenté dans `calculer_jours_acquis`.

### Correctif
Ajouter au calcul des jours acquis :

```python
def bonus_conge_enfants(salarie, date_ref) -> int:
    """+1 jour/an par enfant à charge de moins de 16 ans, pour la mère (Art. 223)."""
    if getattr(salarie, "sexe", "") != "F":
        return 0
    enfants = getattr(salarie, "enfants", None) or []
    return sum(
        1 for e in enfants
        if e.date_naissance and (date_ref - e.date_naissance).days < 16 * 365
    )
```

puis `jours_total += bonus_conge_enfants(salarie, date_ref)`. Cela suppose de modéliser les enfants à charge (table `Enfant` reliée au salarié, avec date de naissance) — utile aussi pour l'IRPP (nombre de parts).

---

# ⚠️ L6 — Allocation de congé : saisie manuelle au lieu d'être calculée

Art. 225 : l'allocation de congé = **moyenne des salaires, indemnités, primes et commissions des 12 mois précédant le départ**. Peuvent être **exclus** : primes de rendement et d'assiduité, indemnités de risques/inconvénients, et indemnités de frais (sauf logement). Elle **doit figurer expressément sur le bulletin** (et l'app le fait déjà via le champ dédié).

Aujourd'hui, `allocations_conge` est un **champ saisi** (`g("allocations_conge")`). Le risque : erreur de saisie, base incohérente, exclusions oubliées. Mieux vaut la **calculer** depuis les 12 bulletins, avec les exclusions de l'Art. 225 :

```python
def allocation_conge(bulletins_12mois, jours_pris: float, jours_an: float = 24.0) -> float:
    """
    Allocation de congé — Code du travail 2021, Art. 225.
    Moyenne mensuelle des 12 derniers mois, hors primes de rendement/assiduité
    et indemnités de risques/frais (sauf logement), proratisée au congé pris.
    """
    if not bulletins_12mois or jours_an <= 0:
        return 0.0
    total = 0.0
    for b in bulletins_12mois:
        assiette = float(b.salaire_brut or 0)
        assiette -= float(getattr(b, "prime_rendement", 0) or 0)
        assiette -= float(getattr(b, "prime_assiduité", 0) or 0)
        # (déduire ici toute indemnité de risque/frais hors logement présente)
        total += assiette
    moyenne_mensuelle = total / len(bulletins_12mois)
    base_journaliere  = moyenne_mensuelle / (jours_an / 12.0)  # ≈ par jour ouvrable
    return fcfa(base_journaliere * jours_pris, 0)
```

> Le détail des rubriques à exclure dépend de ta nomenclature exacte ; le principe Art. 225 est : on part du brut moyen 12 mois et on retire rendement, assiduité, risques et frais (hors logement).

---

# ⚠️ L7 — Revenu Minimum Mensuel (RMM) non géré

Art. 179-180 : deux planchers distincts — le **SMIG** (salaire minimum) et le **RMM** (revenu minimum mensuel). Tout salarié dont le **brut mensuel est inférieur au RMM** fixé par décret a droit au RMM. L'app a un champ `salaire_minimum` (sur la convention) mais ne semble pas :
1. **Bloquer/alerter** si un `salaire_base` saisi est inférieur au SMIG en vigueur ;
2. Gérer le **complément RMM** quand le brut < RMM.

### Recommandation
- Stocker SMIG et RMM en paramètres (configurables, car fixés par décret et révisables).
- À la saisie d'un salaire : avertir si `salaire_base < SMIG`.
- Au calcul du bulletin : si `salaire_brut < RMM`, ajouter une ligne « complément RMM » portant le brut au niveau du RMM (à confirmer avec un comptable sur l'assiette exacte du complément).

> ⚠️ **À VÉRIFIER** : les montants courants du SMIG et du RMM gabonais sont fixés par décret et évoluent — paramètre-les, ne les code pas en dur.

---

# 🛡️ L8 — Congé de maternité (14 semaines)

Art. 208-211 : suspension de **14 semaines** (6 avant + 8 après l'accouchement), +3 semaines si maladie liée, +3 semaines si naissances multiples. **Salaire intégral maintenu, à la charge de la CNSS**. Repos d'allaitement : **2 h/jour les 6 premiers mois, puis 1 h/jour**. Protection contre le licenciement (autorisation inspecteur, 15 mois après accouchement).

L'app gère les congés génériques mais ne modélise pas la maternité comme **type de congé dédié** avec : durée 14 semaines auto-calculée, indemnisation portée par la CNSS (donc traitement paie distinct du salaire employeur), et non-décompte du congé annuel (Art. 223 l'exclut explicitement du calcul de la durée de congé). À ajouter comme type `MATERNITE` dans le module congés, avec une note « indemnités CNSS » plutôt que charge employeur.

---

# 🛡️ L9 — Préavis : Code (mois) vs convention BTP (jours)

Deux barèmes coexistent :

| Ancienneté | Code Art. 82 (2021) | Convention BTP A.30.3 (1983) |
|---|---|---|
| < 1 an | 15 jours | 15 jours |
| 1–3 ans | 1 mois | 30 jours |
| 3–5 ans | 2 mois | 60 jours |
| 5–10 ans | 3 mois (~90 j) | **95 jours** |
| 10–15 ans | 4 mois (~120 j) | **125 jours** |
| 15–20 ans | 5 mois (~150 j) | **160 jours** |
| 20–25/30 ans | 6 mois (~180 j) | **180 jours** |
| > 25/30 ans | +10 j/an | +10 j/an |

L'Art. 80 autorise les conventions à être **plus favorables**. Pour le BTP, la convention l'est (95/125/160 j > 3/4/5 mois). Ton app implémente A.30.3, **c'est donc le bon choix pour le BTP**. Deux vérifications :
1. Pour un tenant **hors convention BTP**, l'app doit basculer sur le barème **Code Art. 82**.
2. Applique systématiquement la **règle de faveur** (le plus avantageux des deux pour le salarié) plutôt qu'un barème figé.

> ⚠️ **À VÉRIFIER** : confirme que ta table de préavis reproduit exactement A.30.3 et que le cas « hors BTP » existe.

---

# 🛡️ L10 — Taux des heures supplémentaires et de nuit

Le Code **ne fixe pas** les taux : l'Art. 196-197 renvoie aux **textes réglementaires et conventions**, et la convention BTP A.38 fait de même. Ton moteur applique **+10 % / +30 % / +40 % / +70 %** (référencés « Décret 578/PR, Arrêté 037/METPS » dans ton `DOC_TECHNIQUE`).

Je **ne peux pas valider ces taux** à partir des deux documents fournis (qui renvoient ailleurs). Points confirmés en revanche :
- **Travail de nuit = 21 h–6 h** (Art. 202) ✅ cohérent avec ton app (A.39).
- Nuit ≤ 8 h consécutives (Art. 202).
- Durée légale **40 h/semaine** (Art. 195), dérogations sectorielles par convention (Art. 197) → ton régime BTP 48 h (173,33 h/mois) est une dérogation conventionnelle valable.

> ⚠️ **À VÉRIFIER** : recoupe tes taux 10/30/40/70 % avec l'**arrêté en vigueur sur les heures supplémentaires** (le texte 037/METPS que tu cites). C'est le seul point où une erreur de taux aurait un impact direct sur chaque bulletin.

---

# ✅ Ce qui est déjà conforme (à conserver)

- **Durée du travail BTP 48 h / 173,33 h** : dérogation conventionnelle valable (Art. 197).
- **Travail de nuit 21 h–6 h** (Art. 202 / A.39) ✅.
- **Prime d'ancienneté** 2 % après 2 ans puis +1 %/an (A.46) ✅ correspond à ton `prime_anciennete`.
- **Prime de panier** 1,5 × salaire horaire (A.47), **prime d'assiduité** 3 % (A.49.1), **indemnité de caisse** ≥ 10 % (A.49.3) ✅ présents.
- **Permissions familiales** (A.41 : mariage 4 j, décès conjoint/parent/enfant 5 j, naissance 3 j, etc.) ✅ ton barème `permissions_familiales_btp`.
- **Indemnité compensatrice de congés** à la cessation (Art. 224) ✅ calculée.
- **Bulletin de paie obligatoire** + **solde de tout compte** + mention « pour solde de tout compte » (Art. 183) ✅, et l'allocation de congé figure bien sur le bulletin (Art. 225).
- **Égalité de rémunération** H/F et sans discrimination (Art. 170) — principe respecté par un moteur basé sur catégorie/poste.

---

# Plan d'action priorisé

**Conformité (avant de facturer des clients qui gèrent des départs) :**
1. **L1** — créer `indemnite_licenciement` + `indemnite_rupture` (aiguillage par cause, non-cumul, faveur).
2. **L2** — ancienneté fractionnaire pour le montant des indemnités.
3. **L3** — passer la cause de cessation au solde de tout compte.

**Exactitude paie (impact sur les montants) :**
4. **L10** — confirmer les taux heures sup contre l'arrêté.
5. **L6** — calculer l'allocation de congé depuis la moyenne 12 mois (Art. 225).
6. **L7** — paramétrer SMIG/RMM + alerte de saisie + complément RMM.

**Complétude réglementaire :**
7. **L4** — taux de congé configurable (défaut légal 2 j, 2,5 j pour mineurs).
8. **L5** — jour supplémentaire par enfant (mère) + modélisation des enfants à charge.
9. **L8** — type de congé maternité dédié (14 semaines, indemnisation CNSS).
10. **L9** — vérifier le barème de préavis et le cas hors-BTP.

---

*Rappel : les éléments « À VÉRIFIER » (taux heures sup, montants SMIG/RMM, seuil exact des fractions d'année, barème d'ancienneté congé) dépendent de décrets/arrêtés non fournis ici. Fais-les confirmer par un comptable ou juriste gabonais avant mise en production — c'est le complément naturel à cette analyse de code.*
