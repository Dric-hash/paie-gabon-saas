"""main_oeuvre_chantiers.py — Coût de la main-d'œuvre d'un mois, réparti par site (chantier).

Salariés : bulletins validés ou payés de la période du mois ; coût employeur (calculs_paie.cout_employeur :
brut + CNSS et CNAMGS patronales + FNH + CFP + TCS), réparti entre les sites au prorata des jours
d'affectation du salarié dans le mois (même règle que le suivi budget / réalisé de la masse salariale).

Journaliers : feuilles de paie dont la fin de période tombe dans le mois (payées ou en attente de
paiement) ; montant brut de la feuille, réparti au prorata des jours de présence pointés sur chaque site
pendant la période de la feuille, à défaut au prorata des jours d'affectation.

Les montants sont arrondis au franc par site et par catégorie ; la part des jours sans affectation
reste « sans site » et n'est envoyée à aucun chantier.
"""
from __future__ import annotations

from datetime import date, timedelta

from models import (BulletinPaie, PeriodePaie, FeuillePaieJournalier, Pointage, Site, AffectationSite)
from calculs_paie import cout_employeur
from calendrier_paie import STATUTS_BULLETIN_VALIDES, fin_de_mois, site_a_la_date
import budget_masse_salariale as bms


def _affectations_journaliers(tenant_id):
    """{journalier_id: [(debut, fin|None, site_id)]} (même lecture que celle des salariés)."""
    out = {}
    q = AffectationSite.query.filter(AffectationSite.tenant_id == tenant_id, AffectationSite.journalier_id.isnot(None))
    for a in q.order_by(AffectationSite.date_debut, AffectationSite.id).all():
        fin = a.date_fin
        if fin is None and not a.actif:
            fin = a.date_debut
        out.setdefault(a.journalier_id, []).append((a.date_debut, fin, a.site_id))
    return out


def _repartition_feuille(f, pointages, affect):
    """{site_id|None: fraction} pour une feuille de journalier."""
    jours = {}
    for p in pointages.get(f.journalier_id, []):
        if f.date_debut <= p.date_pointage <= f.date_fin and p.present and p.site_id:
            jours[p.site_id] = jours.get(p.site_id, 0) + 1
    if jours:
        n = sum(jours.values())
        return {k: v / n for k, v in jours.items()}
    parts, d, n = {}, f.date_debut, 0
    while d <= f.date_fin:
        s = site_a_la_date(affect.get(f.journalier_id), d)
        parts[s] = parts.get(s, 0) + 1
        n += 1
        d += timedelta(days=1)
    return {k: v / n for k, v in parts.items()} if n else {None: 1.0}


def calculer(tenant_id, annee, mois):
    """{"sites": [{site, salaries, nb_salaries, journaliers, nb_feuilles, en_attente, total}],
        "sans_site": {"salaries", "journaliers"}, "total_salaries", "total_journaliers", "periode"}."""
    sites = {s.id: s for s in Site.query.filter_by(tenant_id=tenant_id).order_by(Site.nom).all()}
    lignes = {sid: {"site": s, "salaries": 0.0, "nb_salaries": set(), "journaliers": 0.0, "nb_feuilles": 0.0,
                    "en_attente": 0} for sid, s in sites.items()}
    sans = {"salaries": 0.0, "journaliers": 0.0}

    periode = PeriodePaie.query.filter_by(tenant_id=tenant_id, annee=annee, mois=mois).first()
    total_sal = 0.0
    if periode:
        buls = BulletinPaie.query.filter(BulletinPaie.tenant_id == tenant_id, BulletinPaie.periode_id == periode.id,
                                         BulletinPaie.statut.in_(STATUTS_BULLETIN_VALIDES)).all()
        histo = bms.affectations(tenant_id, {b.salarie_id for b in buls})
        for b in buls:
            cout = cout_employeur(b)
            total_sal += cout
            for sid, frac in bms.repartition_mois(histo.get(b.salarie_id), annee, mois).items():
                cible = lignes.get(sid)
                if cible is None:
                    sans["salaries"] += cout * frac
                else:
                    cible["salaries"] += cout * frac
                    cible["nb_salaries"].add(b.salarie_id)

    debut, fin = date(annee, mois, 1), fin_de_mois(annee, mois)
    feuilles = FeuillePaieJournalier.query.filter(FeuillePaieJournalier.tenant_id == tenant_id,
                                                  FeuillePaieJournalier.date_fin >= debut,
                                                  FeuillePaieJournalier.date_fin <= fin).all()
    total_jour = 0.0
    if feuilles:
        ids = {f.journalier_id for f in feuilles}
        d0 = min(f.date_debut for f in feuilles)
        pointages = {}
        for p in Pointage.query.filter(Pointage.tenant_id == tenant_id, Pointage.journalier_id.in_(ids),
                                       Pointage.date_pointage >= d0, Pointage.date_pointage <= fin).all():
            pointages.setdefault(p.journalier_id, []).append(p)
        affect = _affectations_journaliers(tenant_id)
        for f in feuilles:
            montant = float(f.montant_brut or 0)
            total_jour += montant
            for sid, frac in _repartition_feuille(f, pointages, affect).items():
                cible = lignes.get(sid)
                if cible is None:
                    sans["journaliers"] += montant * frac
                else:
                    cible["journaliers"] += montant * frac
                    cible["nb_feuilles"] += frac
                    if f.statut != "PAYÉ":
                        cible["en_attente"] += 1

    res = []
    for l in lignes.values():
        l["salaries"], l["journaliers"] = round(l["salaries"]), round(l["journaliers"])
        l["total"] = l["salaries"] + l["journaliers"]
        l["nb_salaries"] = len(l["nb_salaries"])
        res.append(l)
    return {"sites": res, "sans_site": {k: round(v) for k, v in sans.items()},
            "total_salaries": round(total_sal), "total_journaliers": round(total_jour), "periode": periode}


def reference(annee, mois, site_id, categorie):
    """Référence stable de la dépense envoyée à Projets (une par mois, site et catégorie)."""
    return f"paie-{categorie}-{annee}{mois:02d}-s{site_id}"
