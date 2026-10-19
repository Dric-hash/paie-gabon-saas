# -*- coding: utf-8 -*-
"""Totaux ANNUELS AGRÉGÉS de la paie pour Ameriack États Financiers.

Servis par GET /api/v1/liaison/paie avec la clé de liaison, uniquement si
l'administrateur de l'entreprise a coché « Autoriser la transmission des
totaux de paie » (Paramètres → Liaison Ameriack États Financiers).

Usage côté États Financiers :
  - Note 27B de la DSF (effectifs et masse salariale par catégorie, sexe et
    nationalité) ;
  - rapprochement paie / comptabilité (salaires bruts ↔ comptes 661 à 663,
    cotisations patronales ↔ 664…).

AUCUNE donnée nominative : ni nom, ni matricule, ni identifiant, ni montant
individuel. Seuls des regroupements (catégorie × nationalité × sexe × type de
contrat) avec leur effectif et leur masse brute, et des totaux globaux.

Bulletins retenus : validés ou payés (mêmes statuts que la DAS).
Feuilles de journaliers retenues : en attente de paiement ou payées, dont la
fin de période tombe dans l'intervalle demandé.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

STATUTS_BULLETINS = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")
STATUTS_FEUILLES = ("EN_ATTENTE", "PAYÉ", "PAYE")
MOIS_MAX = 24
CHAMPS_TOTAUX = ("salaire_brut", "cnss_salarie", "cnss_patronale", "cnamgs_salarie", "cnamgs_patronale",
                 "fnh", "cfp", "tcs", "irpp", "net_a_payer")


class ErreurPeriode(ValueError):
    pass


def mois_couverts(debut: date, fin: date) -> list[tuple[int, int]]:
    if fin < debut:
        raise ErreurPeriode("La date de fin précède la date de début.")
    mois, a, m = [], debut.year, debut.month
    while (a, m) <= (fin.year, fin.month):
        mois.append((a, m))
        if len(mois) > MOIS_MAX:
            raise ErreurPeriode(f"Intervalle limité à {MOIS_MAX} mois.")
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return mois


def _texte(v) -> str:
    return " ".join(str(v or "").strip().upper().split())


def _f(v) -> float:
    return float(v or 0)


def totaux(tenant, debut: date, fin: date) -> dict:
    from models import (db, PeriodePaie, BulletinPaie, Salarie, Contrat, CategorieEmploi,
                        FeuillePaieJournalier, Journalier)
    mois = mois_couverts(debut, fin)
    dernier = mois[-1]
    periodes = [p for p in PeriodePaie.query.filter_by(tenant_id=tenant.id).all() if (p.annee, p.mois) in set(mois)]
    pid_mois = {p.id: (p.annee, p.mois) for p in periodes}

    bulletins = []
    non_retenus = 0
    if pid_mois:
        for b in BulletinPaie.query.filter(BulletinPaie.tenant_id == tenant.id,
                                           BulletinPaie.periode_id.in_(list(pid_mois))).all():
            if (b.statut or "").upper() in STATUTS_BULLETINS:
                bulletins.append(b)
            else:
                non_retenus += 1

    sal_ids = {b.salarie_id for b in bulletins}
    salaries = {s.id: s for s in Salarie.query.filter(Salarie.tenant_id == tenant.id,
                                                      Salarie.id.in_(sal_ids)).all()} if sal_ids else {}
    categories = {c.id: c for c in CategorieEmploi.query.filter_by(tenant_id=tenant.id).all()}
    # Type de contrat : dernier contrat commencé avant la fin de l'intervalle
    contrat_de = {}
    if sal_ids:
        for c in (Contrat.query.filter(Contrat.tenant_id == tenant.id, Contrat.salarie_id.in_(sal_ids))
                  .order_by(Contrat.date_debut).all()):
            if c.date_debut is None or c.date_debut <= fin:
                contrat_de[c.salarie_id] = _texte(c.type_contrat) or "NON RENSEIGNÉ"

    groupes = defaultdict(lambda: {"salaries": set(), "dernier_mois": set(), "masse_brute": 0.0})
    tot = {k: 0.0 for k in CHAMPS_TOTAUX}
    mois_payes = set()
    for b in bulletins:
        s = salaries.get(b.salarie_id)
        if s is None:
            continue
        cat = categories.get(s.categorie_id)
        cle = (cat.code if cat else "", (cat.libelle or "") if cat else "", _texte(s.nationalite),
               (s.sexe or "").upper()[:1], contrat_de.get(s.id, "NON RENSEIGNÉ"))
        g = groupes[cle]
        g["salaries"].add(s.id)
        g["masse_brute"] += _f(b.salaire_brut)
        if pid_mois[b.periode_id] == dernier:
            g["dernier_mois"].add(s.id)
        mois_payes.add(pid_mois[b.periode_id])
        for k in CHAMPS_TOTAUX:
            tot[k] += _f(getattr(b, k))

    # Journaliers
    feuilles = [f for f in FeuillePaieJournalier.query.filter(
        FeuillePaieJournalier.tenant_id == tenant.id,
        FeuillePaieJournalier.date_fin >= debut, FeuillePaieJournalier.date_fin <= fin).all()
        if (f.statut or "").upper() in STATUTS_FEUILLES]
    jids = {f.journalier_id for f in feuilles}
    journaliers = {j.id: j for j in Journalier.query.filter(Journalier.tenant_id == tenant.id,
                                                            Journalier.id.in_(jids)).all()} if jids else {}
    gj = defaultdict(lambda: {"journaliers": set(), "dernier_mois": set(), "masse_brute": 0.0})
    brut_j = attente_j = 0.0
    for f in feuilles:
        j = journaliers.get(f.journalier_id)
        if j is None:
            continue
        cle = (_texte(j.nationalite), (j.sexe or "").upper()[:1], _texte(j.type_paie) or "JOURNALIER")
        g = gj[cle]
        g["journaliers"].add(j.id)
        g["masse_brute"] += _f(f.montant_brut)
        if (f.date_fin.year, f.date_fin.month) == dernier:
            g["dernier_mois"].add(j.id)
        brut_j += _f(f.montant_brut)
        if (f.statut or "").upper() == "EN_ATTENTE":
            attente_j += _f(f.montant_brut)

    def r(v):
        return round(v, 2)

    return {
        "debut": debut.isoformat(), "fin": fin.isoformat(),
        "mois": [f"{a:04d}-{m:02d}" for a, m in mois],
        "mois_sans_bulletin": [f"{a:04d}-{m:02d}" for a, m in mois if (a, m) not in mois_payes],
        "statuts_retenus": list(STATUTS_BULLETINS),
        "salaries": {
            "groupes": [{"categorie_code": k[0], "categorie_libelle": k[1], "nationalite": k[2], "sexe": k[3],
                         "contrat": k[4], "effectif_paye": len(g["salaries"]),
                         "effectif_dernier_mois": len(g["dernier_mois"]), "masse_brute": r(g["masse_brute"])}
                        for k, g in sorted(groupes.items())],
            "totaux": {**{k: r(v) for k, v in tot.items()}, "nb_bulletins": len(bulletins),
                       "nb_salaries": len(sal_ids), "bulletins_non_retenus": non_retenus},
        },
        "journaliers": {
            "groupes": [{"nationalite": k[0], "sexe": k[1], "type_paie": k[2],
                         "effectif_paye": len(g["journaliers"]), "effectif_dernier_mois": len(g["dernier_mois"]),
                         "masse_brute": r(g["masse_brute"])} for k, g in sorted(gj.items())],
            "totaux": {"brut": r(brut_j), "dont_en_attente": r(attente_j), "nb_feuilles": len(feuilles),
                       "nb_journaliers": len(jids)},
        },
    }
