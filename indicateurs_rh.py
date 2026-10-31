"""
indicateurs_rh.py — Indicateurs RH (bilan social simplifié) sur une période de mois
===================================================================================
Population : les salariés (hors journaliers) de l'entreprise, éventuellement restreints à un site
(affectation à la date de fin) ou à une catégorie. Un salarié est présent à une date s'il est
embauché à cette date et n'est pas sorti (date de cessation postérieure).

Effectifs   : au début et à la fin de la période, effectif moyen = moyenne des effectifs aux fins
              de mois ; répartition à la fin par sexe, nationalité, catégorie, type de contrat, site.
Mouvements  : entrées (date d'embauche dans la période), sorties (date de cessation dans la
              période) par mois et par motif ; taux de rotation = ((entrées + sorties) / 2)
              / effectif moyen × 100.
Ancienneté  : à la date de fin, moyenne et tranches.
Âges        : à la date de fin, pyramide par tranches de 5 ans et par sexe, âge moyen.
Absentéisme : jours ouvrables (lundi → samedi) d'absence dans la période :
                - congés accordés autres que le congé annuel (maladie, maternité, paternité,
                  permission, exceptionnel, sans solde), du départ (inclus) au retour (exclu) ;
                - absences du pointage (validées) hors motif « CONGE » ;
              un même jour n'est compté qu'une fois par salarié (le congé l'emporte sur le pointage).
              Taux = jours d'absence / jours ouvrables de présence théorique × 100 (présence
              théorique : jours ouvrables entre l'embauche et la sortie, dans la période).
Masse       : bulletins validés ou payés des mois de la période : brut, coût employeur
              (calculs_paie.cout_employeur), net à payer, nombre de bulletins, brut moyen ;
              par mois, par site (au prorata des jours d'affectation de chaque mois), par
              catégorie (catégorie actuelle du salarié) et par sexe.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from models import (Salarie, Contrat, Conge, Pointage, BulletinPaie, PeriodePaie, CategorieEmploi, Site)
from calculs_paie import cout_employeur
from plan_comptable_paie import est_national
import budget_masse_salariale as bms

STATUTS_VALIDES = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")
CONGES_ACCORDES = ("APPROUVÉ", "APPROUVE", "PRIS")
MOTIFS_CONGE = {"MALADIE": "Maladie", "MATERNITE": "Maternité", "PATERNITE": "Paternité",
                "PERMISSION": "Permission (événement familial)", "EXCEPTIONNEL": "Congé exceptionnel",
                "SANS_SOLDE": "Congé sans solde"}
MOTIFS_POINTAGE = {"MALADIE": "Maladie", "AUTORISATION": "Absence autorisée", "SANS_MOTIF": "Absence injustifiée",
                   "INJUSTIFIEE": "Absence injustifiée", "ABSENT": "Absence (motif non précisé)",
                   "": "Absence (motif non précisé)"}
TRANCHES_ANCIENNETE = [(0, 1, "Moins d'1 an"), (1, 3, "1 à 3 ans"), (3, 5, "3 à 5 ans"), (5, 10, "5 à 10 ans"),
                       (10, 20, "10 à 20 ans"), (20, 200, "20 ans et plus")]
TRANCHES_AGE = [(0, 20, "Moins de 20 ans")] + [(a, a + 5, f"{a}–{a + 4} ans") for a in range(20, 65, 5)] + \
               [(65, 200, "65 ans et plus")]
MOIS = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre",
        "Novembre", "Décembre"]


def _f(v):
    return float(v or 0)


def fin_mois(a, m):
    return date(a, m, monthrange(a, m)[1])


def mois_de(debut, fin):
    """[(annee, mois)] de debut à fin inclus."""
    out, a, m = [], debut.year, debut.month
    while (a, m) <= (fin.year, fin.month):
        out.append((a, m))
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return out


def present(s, d):
    return bool(s.date_embauche and s.date_embauche <= d and (s.date_cessation is None or s.date_cessation > d))


def annees_entre(d0, d1):
    """Années révolues entre deux dates."""
    return d1.year - d0.year - ((d1.month, d1.day) < (d0.month, d0.day))


def jours_ouvrables(d0, d1):
    """Dates ouvrables (lundi → samedi) de d0 à d1 inclus."""
    out, d = [], d0
    while d <= d1:
        if d.weekday() < 6:
            out.append(d)
        d += timedelta(days=1)
    return out


def _tranche(v, tranches):
    for lo, hi, lib in tranches:
        if lo <= v < hi:
            return lib
    return tranches[-1][2]


def _site_a(histo, d):
    """Site d'affectation couvrant la date d (la plus récente l'emporte), sinon None."""
    site = None
    for d0, d1, sid in histo or []:
        if d0 <= d and (d1 is None or d < d1 or (d == d1 and d0 == d1)):
            site = sid
    return site


def calculer(tenant, debut, fin, site_id=None, categorie_id=None):
    """Indicateurs de la période [debut, fin] (dates). site_id : 0 = sans site ; None = tous."""
    tid = tenant.id
    histo = bms.affectations(tid)
    sites = {s.id: s.nom for s in Site.query.filter_by(tenant_id=tid).all()}
    cats = {c.id: c for c in CategorieEmploi.query.filter_by(tenant_id=tid).all()}
    tous = [s for s in Salarie.query.filter_by(tenant_id=tid).all() if s.date_embauche]

    def retenu(s):
        if categorie_id and s.categorie_id != categorie_id:
            return False
        if site_id is not None:
            sid = _site_a(histo.get(s.id), min(fin, s.date_cessation - timedelta(days=1))
                          if s.date_cessation and s.date_cessation <= fin else fin)
            if (sid or 0) != site_id:
                return False
        return True
    pop = [s for s in tous if retenu(s)]
    ids = {s.id for s in pop}
    mois = mois_de(debut, fin)

    # ── Effectifs ────────────────────────────────────────────────────────────
    veille = debut - timedelta(days=1)
    eff_debut = sum(1 for s in pop if present(s, veille))
    actifs_fin = [s for s in pop if present(s, fin)]
    eff_mois = [(a, m, sum(1 for s in pop if present(s, min(fin_mois(a, m), fin)))) for a, m in mois]
    eff_moyen = sum(n for _, _, n in eff_mois) / len(eff_mois) if eff_mois else 0.0
    contrats = {}
    for c in (Contrat.query.filter(Contrat.tenant_id == tid, Contrat.actif.is_(True))
              .order_by(Contrat.date_debut, Contrat.id).all()):
        contrats[c.salarie_id] = c

    def repart(cle):
        out = {}
        for s in actifs_fin:
            k = cle(s)
            out[k] = out.get(k, 0) + 1
        return sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))

    sexe_lib = {"M": "Hommes", "F": "Femmes"}
    repartitions = {
        "Sexe": repart(lambda s: sexe_lib.get((s.sexe or "").upper(), "Non renseigné")),
        "Nationalité": repart(lambda s: "Nationaux" if est_national(s.nationalite) else "Non nationaux"),
        "Catégorie": repart(lambda s: (f"{cats[s.categorie_id].code} — {cats[s.categorie_id].libelle}"
                                       if s.categorie_id in cats else "Sans catégorie")),
        "Type de contrat": repart(lambda s: (contrats[s.id].type_contrat or "Non précisé") if s.id in contrats
                                  else "Sans contrat actif"),
        "Site": repart(lambda s: sites.get(_site_a(histo.get(s.id), fin), "Sans site affecté")),
    }

    # ── Entrées et sorties ───────────────────────────────────────────────────
    entrees = sorted([s for s in pop if debut <= s.date_embauche <= fin], key=lambda s: s.date_embauche)
    sorties = sorted([s for s in pop if s.date_cessation and debut <= s.date_cessation <= fin],
                     key=lambda s: s.date_cessation)
    mouvements = [{"annee": a, "mois": m, "libelle": f"{MOIS[m]} {a}", "effectif": n,
                   "entrees": sum(1 for s in entrees if (s.date_embauche.year, s.date_embauche.month) == (a, m)),
                   "sorties": sum(1 for s in sorties if (s.date_cessation.year, s.date_cessation.month) == (a, m))}
                  for a, m, n in eff_mois]
    motifs_sortie = {}
    for s in sorties:
        k = s.type_rupture or "Non précisé"
        motifs_sortie[k] = motifs_sortie.get(k, 0) + 1
    rotation = (((len(entrees) + len(sorties)) / 2) / eff_moyen * 100) if eff_moyen else None

    # ── Ancienneté et âges (à la date de fin) ────────────────────────────────
    anc_jours = [(fin - s.date_embauche).days / 365.25 for s in actifs_fin]
    anciennete = {lib: 0 for _, _, lib in TRANCHES_ANCIENNETE}
    for v in anc_jours:
        anciennete[_tranche(v, TRANCHES_ANCIENNETE)] += 1
    pyramide = {lib: {"H": 0, "F": 0, "NR": 0} for _, _, lib in TRANCHES_AGE}
    ages, sans_age = [], 0
    for s in actifs_fin:
        if not s.date_naissance:
            sans_age += 1
            continue
        age = annees_entre(s.date_naissance, fin)
        ages.append(age)
        sx = (s.sexe or "").upper()
        pyramide[_tranche(age, TRANCHES_AGE)]["H" if sx == "M" else "F" if sx == "F" else "NR"] += 1
    pyr_max = max([max(v["H"], v["F"]) for v in pyramide.values()] + [1])

    # ── Absentéisme ──────────────────────────────────────────────────────────
    absences = {}                 # (salarie_id, date) -> motif
    for c in Conge.query.filter(Conge.tenant_id == tid, Conge.date_depart.isnot(None),
                                Conge.statut.in_(CONGES_ACCORDES)).all():
        t = (c.type_conge or "ANNUEL").upper()
        if t == "ANNUEL" or c.salarie_id not in ids:
            continue
        retour = c.date_retour or c.date_depart + timedelta(days=1)
        d0, d1 = max(c.date_depart, debut), min(retour - timedelta(days=1), fin)
        for d in jours_ouvrables(d0, d1) if d0 <= d1 else []:
            absences[(c.salarie_id, d)] = MOTIFS_CONGE.get(t, t.capitalize())
    for p in Pointage.query.filter(Pointage.tenant_id == tid, Pointage.salarie_id.isnot(None),
                                   Pointage.absent.is_(True), Pointage.date_pointage >= debut,
                                   Pointage.date_pointage <= fin).all():
        if p.salarie_id not in ids or (p.statut or "VALIDE") != "VALIDE" or p.date_pointage.weekday() == 6:
            continue
        motif = (p.motif_absence or "").upper()
        if motif == "CONGE":
            continue
        absences.setdefault((p.salarie_id, p.date_pointage), MOTIFS_POINTAGE.get(motif, motif.capitalize()))
    theoriques = 0
    presence = {}
    for s in pop:
        d0 = max(s.date_embauche, debut)
        d1 = min(s.date_cessation - timedelta(days=1), fin) if s.date_cessation else fin
        jours = set(jours_ouvrables(d0, d1)) if d0 <= d1 else set()
        presence[s.id] = jours
        theoriques += len(jours)
    absences = {k: v for k, v in absences.items() if k[1] in presence.get(k[0], set())}
    par_motif = {}
    for motif in absences.values():
        par_motif[motif] = par_motif.get(motif, 0) + 1
    abs_mois = []
    for a, m in mois:
        th = sum(1 for js in presence.values() for d in js if (d.year, d.month) == (a, m))
        ab = sum(1 for (_, d) in absences if (d.year, d.month) == (a, m))
        abs_mois.append({"libelle": f"{MOIS[m]} {a}", "jours": ab, "theoriques": th,
                         "taux": (ab / th * 100) if th else None})
    absenteisme = {"jours": len(absences), "theoriques": theoriques,
                   "taux": (len(absences) / theoriques * 100) if theoriques else None,
                   "par_motif": sorted(par_motif.items(), key=lambda kv: -kv[1]), "par_mois": abs_mois,
                   "salaries": len({k[0] for k in absences})}

    # ── Masse salariale ──────────────────────────────────────────────────────
    buls = (BulletinPaie.query.join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
            .filter(BulletinPaie.tenant_id == tid, BulletinPaie.statut.in_(STATUTS_VALIDES),
                    PeriodePaie.annee * 100 + PeriodePaie.mois >= debut.year * 100 + debut.month,
                    PeriodePaie.annee * 100 + PeriodePaie.mois <= fin.year * 100 + fin.month).all())
    buls = [b for b in buls if b.salarie_id in ids]
    sal_par_id = {s.id: s for s in pop}

    def agreg():
        return {"brut": 0.0, "cout": 0.0, "net": 0.0, "nb": 0.0}

    total, par_mois_m, par_site, par_cat, par_sexe = agreg(), {}, {}, {}, {}

    def ajout(x, b, frac=1.0):
        x["brut"] += _f(b.salaire_brut) * frac
        x["cout"] += cout_employeur(b) * frac
        x["net"] += _f(b.net_a_payer) * frac
        x["nb"] += frac
    for b in buls:
        s = sal_par_id[b.salarie_id]
        a, m = b.periode.annee, b.periode.mois
        ajout(total, b)
        ajout(par_mois_m.setdefault((a, m), agreg()), b)
        for k, frac in bms.repartition_mois(histo.get(b.salarie_id), a, m).items():
            ajout(par_site.setdefault(sites.get(k, "Sans site affecté"), agreg()), b, frac)
        cle_cat = f"{cats[s.categorie_id].code} — {cats[s.categorie_id].libelle}" if s.categorie_id in cats \
            else "Sans catégorie"
        ajout(par_cat.setdefault(cle_cat, agreg()), b)
        ajout(par_sexe.setdefault(sexe_lib.get((s.sexe or "").upper(), "Non renseigné"), agreg()), b)

    def lignes(dico):
        out = []
        for k, x in dico.items():
            out.append({"libelle": k, **x, "brut_moyen": (x["brut"] / x["nb"]) if x["nb"] else 0.0,
                        "part": (x["cout"] / total["cout"] * 100) if total["cout"] else 0.0})
        return sorted(out, key=lambda x: -x["cout"])
    masse = {"total": {**total, "brut_moyen": (total["brut"] / total["nb"]) if total["nb"] else 0.0},
             "par_mois": [{"libelle": f"{MOIS[m]} {a}", **par_mois_m.get((a, m), agreg())} for a, m in mois],
             "par_site": lignes(par_site), "par_categorie": lignes(par_cat), "par_sexe": lignes(par_sexe)}
    masse["max_mois"] = max([x["cout"] for x in masse["par_mois"]] + [1])

    return {"debut": debut, "fin": fin, "mois": mois,
            "effectif_debut": eff_debut, "effectif_fin": len(actifs_fin), "effectif_moyen": eff_moyen,
            "repartitions": repartitions, "mouvements": mouvements, "entrees": entrees, "sorties": sorties,
            "motifs_sortie": sorted(motifs_sortie.items(), key=lambda kv: -kv[1]), "rotation": rotation,
            "anciennete": list(anciennete.items()),
            "anciennete_moyenne": (sum(anc_jours) / len(anc_jours)) if anc_jours else None,
            "pyramide": [(lib, v) for lib, v in reversed(list(pyramide.items()))], "pyramide_max": pyr_max,
            "age_moyen": (sum(ages) / len(ages)) if ages else None, "sans_age": sans_age,
            "absenteisme": absenteisme, "masse": masse}
