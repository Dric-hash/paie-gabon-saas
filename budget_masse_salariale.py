"""
budget_masse_salariale.py — Budget annuel de la masse salariale et suivi budget / réalisé
=========================================================================================
Budget : une ligne par salarié (préparée depuis son contrat actif) ou par poste prévu
(recrutement), avec un site, des mois de présence dans l'année, un salaire de base, les
éléments récurrents du contrat et une augmentation éventuelle à partir d'un mois donné.

Chaque mois budgété est calculé par le moteur de paie (calculs_paie.calculer_bulletin),
comme la génération en lot : salaire de base, éléments récurrents, ancienneté à la fin du
mois (prime d'ancienneté de la convention), travail de nuit, nombre de parts. On retient :
  brut           = salaire brut ;
  coût employeur = brut + CNSS et CNAMGS patronales + FNH + CFP + TCS.
Les heures supplémentaires, absences, primes ponctuelles et rappels ne sont pas
prévisibles : ils apparaissent dans l'écart.

Réalisé : bulletins validés (ou payés) des périodes de l'année, même définition du coût.
Sites : le budget d'une ligne peut changer de site à partir d'un mois (transfert prévu, ou déjà
constaté dans l'historique des affectations à la préparation). Le réalisé d'un mois est réparti
entre les sites au prorata des jours d'affectation du salarié dans ce mois (historique des
affectations : date de début et date de fin) ; les jours sans affectation vont à « Sans site ».

Alerte : le coût réalisé cumulé dépasse le budget cumulé à la même date de plus du seuil
du budget (en %).
"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date

from models import (db, Salarie, Contrat, BulletinPaie, PeriodePaie, BudgetMasseSalariale, LigneBudget,
                    AffectationSite)

STATUTS_VALIDES = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")
MOIS = ["", "Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin", "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc."]


def _f(v):
    return float(v or 0)


from calculs_paie import cout_employeur  # noqa: E402  (définition unique, TCS comprise)


def mois_presence(s, annee):
    """(premier, dernier) mois de présence d'un salarié dans l'année, ou None."""
    deb = 1 if s.date_embauche.year < annee else (s.date_embauche.month if s.date_embauche.year == annee else 13)
    fin = 12
    if s.date_cessation:
        fin = 0 if s.date_cessation.year < annee else (s.date_cessation.month if s.date_cessation.year == annee else 12)
    return (deb, fin) if deb <= fin else None


def affectations(tenant_id, salarie_ids=None):
    """{salarie_id: [(debut, fin|None, site_id)]} : historique complet des affectations."""
    q = AffectationSite.query.filter(AffectationSite.tenant_id == tenant_id, AffectationSite.salarie_id.isnot(None))
    if salarie_ids is not None:
        q = q.filter(AffectationSite.salarie_id.in_(list(salarie_ids)))
    out = {}
    for a in q.order_by(AffectationSite.date_debut, AffectationSite.id).all():
        fin = a.date_fin
        if fin is None and not a.actif:
            fin = a.date_debut            # affectation close sans date de fin : un jour
        out.setdefault(a.salarie_id, []).append((a.date_debut, fin, a.site_id))
    return out


def repartition_mois(periodes_aff, annee, mois):
    """{site_id|None: fraction du mois} au prorata des jours d'affectation (une journée de
    transfert compte pour le nouveau site)."""
    debut, fin = date(annee, mois, 1), date(annee, mois, monthrange(annee, mois)[1])
    jours = (fin - debut).days + 1
    parts = {}
    for i in range(jours):
        j = date.fromordinal(debut.toordinal() + i)
        site = None
        for d0, d1, sid in periodes_aff or []:     # la plus récente qui couvre le jour l'emporte
            if d0 <= j and (d1 is None or j < d1 or (j == d1 and d0 == d1)):
                site = sid
        parts[site] = parts.get(site, 0) + 1
    return {k: v / jours for k, v in parts.items()}


def site_du_mois(l, m):
    """Site budgété d'une ligne pour le mois m (changement de site à partir de mois_transfert)."""
    if l.nouveau_site_id is not None and l.mois_transfert and m >= l.mois_transfert:
        return l.nouveau_site_id
    return l.site_id


def calculer_ligne(l, tenant):
    """Remplit l.mensuel (JSON {mois: [brut, coût]}) d'après les hypothèses de la ligne."""
    from calculs_paie import calculer_bulletin
    try:
        elements = json.loads(l.elements or "{}")
    except ValueError:
        elements = {}
    s = l.salarie
    out = {}
    for m in range(max(1, l.mois_debut or 1), min(12, l.mois_fin or 12) + 1):
        base = _f(l.salaire_base)
        if l.augmentation_pct and l.mois_augmentation and m >= l.mois_augmentation:
            base = round(base * (1 + _f(l.augmentation_pct) / 100))
        donnees = {"salaire_base": base, "convention": tenant.convention}
        donnees.update({k: v for k, v in elements.items() if k != "salaire_base"})
        fin_mois = date(l.budget.annee, m, monthrange(l.budget.annee, m)[1])
        if s is not None:
            if s.date_embauche:
                donnees["anciennete_annees"] = max(0, (fin_mois - s.date_embauche).days // 365)
            if getattr(s, "travail_de_nuit", False):
                donnees["travail_de_nuit"] = True
        r = calculer_bulletin(donnees, nb_parts=_f(getattr(s, "nombre_parts", None)) or 1.0)
        out[m] = [round(_f(r["salaire_brut"])), round(cout_employeur(r))]
    l.mensuel = json.dumps(out)
    return out


def mensuel(l):
    try:
        return {int(k): v for k, v in json.loads(l.mensuel or "{}").items()}
    except ValueError:
        return {}


def preparer(tenant, annee, auteur, augmentation_pct=0, mois_augmentation=None, seuil=5):
    """Crée le budget de l'année depuis les contrats actifs des salariés présents dans l'année."""
    if BudgetMasseSalariale.query.filter_by(tenant_id=tenant.id, annee=annee).first():
        raise ValueError(f"Un budget {annee} existe déjà : modifiez-le ou supprimez-le.")
    b = BudgetMasseSalariale(tenant_id=tenant.id, annee=annee, statut="BROUILLON", seuil_alerte_pct=seuil,
                             auteur=auteur)
    db.session.add(b)
    db.session.flush()
    histo = affectations(tenant.id)
    for s in Salarie.query.filter_by(tenant_id=tenant.id).order_by(Salarie.nom, Salarie.prenom).all():
        if not s.date_embauche:
            continue
        pres = mois_presence(s, annee)
        c = (Contrat.query.filter_by(tenant_id=tenant.id, salarie_id=s.id, actif=True)
             .order_by(Contrat.date_debut.desc()).first())
        if pres is None or c is None or not c.salaire_base:
            continue
        # site au premier mois de présence ; transfert déjà connu dans l'année : changement de site
        aff = histo.get(s.id, [])
        sites_mois = {m: max(repartition_mois(aff, annee, m).items(), key=lambda kv: kv[1])[0]
                      for m in range(pres[0], pres[1] + 1)}
        premier = sites_mois[pres[0]]
        transfert = next((m for m in range(pres[0] + 1, pres[1] + 1) if sites_mois[m] != premier), None)
        l = LigneBudget(tenant_id=tenant.id, budget_id=b.id, salarie_id=s.id, libelle=s.nom_complet,
                        site_id=premier, nouveau_site_id=(sites_mois[transfert] if transfert else None),
                        mois_transfert=transfert,
                        mois_debut=pres[0], mois_fin=pres[1], salaire_base=c.salaire_base,
                        elements=c.elements_recurrents, augmentation_pct=augmentation_pct or 0,
                        mois_augmentation=(mois_augmentation if augmentation_pct else None))
        db.session.add(l)
        l.budget, l.salarie = b, s
        calculer_ligne(l, tenant)
    db.session.flush()
    return b


def realise(tenant_id, annee):
    """{mois: {"brut", "cout", "nb", "par_salarie": {sid: coût}}} des bulletins validés de l'année."""
    out = {m: {"brut": 0.0, "cout": 0.0, "nb": 0, "par_salarie": {}} for m in range(1, 13)}
    q = (BulletinPaie.query.join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
         .filter(BulletinPaie.tenant_id == tenant_id, PeriodePaie.annee == annee,
                 BulletinPaie.statut.in_(STATUTS_VALIDES)))
    for bu in q.all():
        m = bu.periode.mois
        c = cout_employeur(bu)
        out[m]["brut"] += _f(bu.salaire_brut)
        out[m]["cout"] += c
        out[m]["nb"] += 1
        out[m]["par_salarie"][bu.salarie_id] = out[m]["par_salarie"].get(bu.salarie_id, 0) + c
    return out


def suivi(b):
    """Tableau mensuel budget / réalisé avec cumuls et écarts (sur le coût employeur)."""
    bud = {m: [0.0, 0.0] for m in range(1, 13)}
    for l in b.lignes:
        for m, (brut, cout) in mensuel(l).items():
            bud[m][0] += brut
            bud[m][1] += cout
    rea = realise(b.tenant_id, b.annee)
    lignes, cb, cr = [], 0.0, 0.0
    for m in range(1, 13):
        a_realise = rea[m]["nb"] > 0
        cb += bud[m][1]
        if a_realise:
            cr += rea[m]["cout"]
        ecart = (rea[m]["cout"] - bud[m][1]) if a_realise else None
        lignes.append({"mois": m, "libelle": MOIS[m], "budget_brut": bud[m][0], "budget_cout": bud[m][1],
                       "realise_brut": rea[m]["brut"] if a_realise else None,
                       "realise_cout": rea[m]["cout"] if a_realise else None, "nb": rea[m]["nb"],
                       "ecart": ecart, "ecart_pct": (ecart / bud[m][1] * 100) if a_realise and bud[m][1] else None})
    # cumul à date : mois ayant du réalisé (jusqu'au dernier mois réalisé)
    derniers = [x["mois"] for x in lignes if x["realise_cout"] is not None]
    dernier = max(derniers) if derniers else 0
    cum_b = sum(x["budget_cout"] for x in lignes if x["mois"] <= dernier)
    cum_r = sum(x["realise_cout"] or 0 for x in lignes if x["mois"] <= dernier)
    ecart_cum = cum_r - cum_b
    pct = (ecart_cum / cum_b * 100) if cum_b else None
    total_b = sum(x["budget_cout"] for x in lignes)
    return {"lignes": lignes, "dernier_mois": dernier, "cumul_budget": cum_b, "cumul_realise": cum_r,
            "ecart_cumul": ecart_cum, "ecart_cumul_pct": pct, "total_budget": total_b,
            "total_budget_brut": sum(x["budget_brut"] for x in lignes),
            "alerte": pct is not None and pct > _f(b.seuil_alerte_pct),
            "consomme_pct": (cum_r / total_b * 100) if total_b else None, "realise": rea}


def par_site(b, rea=None):
    """Budget annuel, budget et réalisé cumulés à date, par site : budget selon le site de chaque
    mois de la ligne ; réalisé réparti au prorata des jours d'affectation du mois."""
    from models import Site
    rea = rea or realise(b.tenant_id, b.annee)
    derniers = [m for m in range(1, 13) if rea[m]["nb"]]
    dernier = max(derniers) if derniers else 0
    histo = affectations(b.tenant_id)
    noms = {s.id: s.nom for s in Site.query.filter_by(tenant_id=b.tenant_id).all()}
    out = {}

    def ligne(k):
        return out.setdefault(k, {"site": noms.get(k, "Sans site"), "annuel": 0.0, "budget": 0.0, "realise": 0.0})
    for l in b.lignes:
        for m, (_, cout) in mensuel(l).items():
            k = site_du_mois(l, m)
            x = ligne(k if k in noms else None)
            x["annuel"] += cout
            if m <= dernier:
                x["budget"] += cout
    for m in range(1, dernier + 1):
        for sid, c in rea[m]["par_salarie"].items():
            for k, frac in repartition_mois(histo.get(sid), b.annee, m).items():
                ligne(k if k in noms else None)["realise"] += c * frac
    res = sorted(out.values(), key=lambda x: (x["site"] == "Sans site", x["site"].upper()))
    for x in res:
        x["ecart"] = x["realise"] - x["budget"]
        x["ecart_pct"] = (x["ecart"] / x["budget"] * 100) if x["budget"] else None
    return res


def alerte_periode(tenant, periode):
    """Pour le contrôle de la paie : écart cumulé si le budget adopté de l'année est dépassé
    au-delà du seuil en incluant tous les bulletins de la période (brouillons compris)."""
    b = BudgetMasseSalariale.query.filter_by(tenant_id=tenant.id, annee=periode.annee, statut="ADOPTE").first()
    if b is None:
        return None
    cum_b = 0.0
    for l in b.lignes:
        cum_b += sum(c for m, (_, c) in mensuel(l).items() if m <= periode.mois)
    rea = realise(tenant.id, periode.annee)
    cum_r = sum(rea[m]["cout"] for m in range(1, periode.mois))
    cum_r += sum(cout_employeur(x) for x in BulletinPaie.query.filter_by(tenant_id=tenant.id, periode_id=periode.id))
    if not cum_b:
        return None
    pct = (cum_r - cum_b) / cum_b * 100
    if pct > _f(b.seuil_alerte_pct):
        return {"budget": b, "cumul_budget": cum_b, "cumul_realise": cum_r, "ecart_pct": pct}
    return None
