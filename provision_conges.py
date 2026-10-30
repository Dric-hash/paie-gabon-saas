"""
provision_conges.py — Provision pour congés payés (arrêté mensuel ou de clôture)
=================================================================================
À une date d'arrêté (fin de mois), pour chaque salarié présent à cette date :

  jours dus      = jours de congé restants (acquis − pris) de l'année de l'arrêté
                   + reliquat positif de l'année précédente (même calcul que la page
                   Congés : la ligne de solde saisie fait foi, sinon calcul automatique) ;
  provision      = allocation de congé de ces jours (conges_avance.allocation_conge,
                   Art. 225 : moyenne du brut des 12 derniers bulletins validés, hors
                   primes de rendement et d'assiduité, ÷ 26 jours ouvrables × jours dus) ;
                   sans bulletin validé : salaire de base du contrat ÷ 26 × jours dus ;
  charges        = provision × taux patronaux effectivement constatés sur les mêmes
                   bulletins : CNSS + CNAMGS patronales (charges sociales), FNH + CFP
                   (taxes sur salaires). Sans bulletin : taux du moteur de paie appliqué
                   au salaire du contrat.

Écriture (comptes SYSCOHADA révisé proposés par défaut, modifiables) : ajustement de
l'arrêté précédent validé vers celui-ci (variation, au franc), par nature :
  6613 / 6623  Congés payés (national / non national)  ↔ 4281 Dettes provisionnées pour congés à payer
  6641 / 6642  Charges sociales (national / non national) ↔ 4382 Charges sociales sur congés à payer
  6413         Taxes sur salaires                       ↔ 4486 État, charges à payer
Une variation positive est une dotation (débit charge / crédit dette), une variation
négative une reprise (débit dette / crédit charge).
"""
from __future__ import annotations

import json
import re
from datetime import date
from calendar import monthrange

from models import (db, Salarie, Contrat, BulletinPaie, PeriodePaie, ProvisionConges, LigneProvisionConges)
import conges_avance as ca
from plan_comptable_paie import est_national

STATUTS_VALIDES = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")
JOURS_OUVRABLES_MOIS = ca.JOURS_OUVRABLES_MOIS

# clé, libellé, compte proposé (SYSCOHADA révisé)
COMPTES = [
    ("conges_national", "Congés payés — personnel national", "6613"),
    ("conges_non_national", "Congés payés — personnel non national", "6623"),
    ("dette_conges", "Dettes provisionnées pour congés à payer", "4281"),
    ("charges_national", "Charges sociales — personnel national", "6641"),
    ("charges_non_national", "Charges sociales — personnel non national", "6642"),
    ("dette_charges", "Charges sociales sur congés à payer", "4382"),
    ("taxes", "Taxes sur salaires (FNH, CFP)", "6413"),
    ("dette_taxes", "État, charges à payer", "4486"),
]
DEFAUTS = {k: c for k, _, c in COMPTES}


def comptes(tenant) -> dict:
    try:
        d = json.loads(tenant.comptes_provision_conges or "{}")
    except (TypeError, ValueError):
        d = {}
    return {k: (d.get(k) or DEFAUTS[k]) for k in DEFAUTS}


def lire_comptes(form) -> str:
    out = {}
    for k, lib, _ in COMPTES:
        v = (form.get(f"compte_{k}") or "").strip()
        if not re.fullmatch(r"\d{2,10}", v):
            raise ValueError(f"Compte invalide pour « {lib} » : 2 à 10 chiffres.")
        out[k] = v
    return json.dumps(out)


def _f(v):
    return float(v or 0)


def fin_de_mois(annee, mois):
    return date(annee, mois, monthrange(annee, mois)[1])


def salaries_presents(tenant_id, d):
    return [s for s in Salarie.query.filter_by(tenant_id=tenant_id).order_by(Salarie.nom, Salarie.prenom).all()
            if s.date_embauche and s.date_embauche <= d and (s.date_cessation is None or s.date_cessation > d)]


def bulletins_reference(tenant_id, salarie_id, d, n=12):
    """Les n derniers bulletins validés d'une période ≤ mois de l'arrêté."""
    q = (BulletinPaie.query.join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
         .filter(BulletinPaie.tenant_id == tenant_id, BulletinPaie.salarie_id == salarie_id,
                 BulletinPaie.statut.in_(STATUTS_VALIDES),
                 PeriodePaie.annee * 100 + PeriodePaie.mois <= d.year * 100 + d.month)
         .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()))
    return q.limit(n).all()


def jours_dus(tenant, s, d):
    """(jours dus, détail) à la date d'arrêté : restants de l'année + reliquat positif N-1."""
    cur = ca.solde_annee(tenant, s, d.year, aujourd_hui=d)
    prec = ca.solde_annee(tenant, s, d.year - 1, aujourd_hui=d) if s.date_embauche.year < d.year else None
    reliquat = max(0.0, _f(prec["restants"])) if prec else 0.0
    annee = _f(cur["restants"])
    return round(max(0.0, annee + reliquat), 2), {"acquis": cur["acquis"], "pris": cur["pris"],
                                                  "restants_annee": annee, "reliquat": reliquat}


def _taux_moteur(base, salarie):
    from calculs_paie import calculer_bulletin
    r = calculer_bulletin({"salaire_base": base}, nb_parts=_f(salarie.nombre_parts) or 1.0)
    brut = _f(r.get("salaire_brut")) or base
    soc = _f(r.get("cnss_patronale")) + _f(r.get("cnamgs_patronale"))
    tax = _f(r.get("fnh")) + _f(r.get("cfp"))
    return soc / brut, tax / brut


def calculer_ligne(tenant, s, d):
    jours, det = jours_dus(tenant, s, d)
    buls = bulletins_reference(tenant.id, s.id, d)
    if buls:
        provision = ca.allocation_conge(buls, jours) if jours > 0 else 0.0
        assiette = sum(max(0.0, _f(b.salaire_brut) - _f(b.prime_rendement) - _f(getattr(b, "prime_assiduité", 0)))
                       for b in buls) / len(buls)
        brut = sum(_f(b.salaire_brut) for b in buls)
        t_soc = sum(_f(b.cnss_patronale) + _f(b.cnamgs_patronale) for b in buls) / brut if brut else 0.0
        t_tax = sum(_f(b.fnh) + _f(b.cfp) for b in buls) / brut if brut else 0.0
        source = f"{len(buls)} bulletin(s)"
    else:
        c = (Contrat.query.filter_by(tenant_id=tenant.id, salarie_id=s.id, actif=True)
             .order_by(Contrat.date_debut.desc()).first())
        assiette = _f(c.salaire_base) if c else 0.0
        provision = round(assiette / JOURS_OUVRABLES_MOIS * jours) if jours > 0 else 0.0
        t_soc, t_tax = _taux_moteur(assiette, s) if assiette else (0.0, 0.0)
        source = "contrat" if c else "aucune base"
    provision = round(provision)
    det.update(source=source)
    return dict(salarie_id=s.id, national=est_national(s.nationalite), jours=jours,
                base_journaliere=round(assiette / JOURS_OUVRABLES_MOIS, 2), provision=provision,
                taux_charges=round(t_soc, 6), charges=round(provision * t_soc),
                taux_taxes=round(t_tax, 6), taxes=round(provision * t_tax), detail=json.dumps(det))


def calculer(tenant, annee, mois, auteur):
    """Crée ou recalcule l'arrêté (brouillon) du mois. Renvoie l'arrêté."""
    d = fin_de_mois(annee, mois)
    p = ProvisionConges.query.filter_by(tenant_id=tenant.id, annee=annee, mois=mois).first()
    if p is None:
        p = ProvisionConges(tenant_id=tenant.id, annee=annee, mois=mois, statut="BROUILLON")
        db.session.add(p)
    elif p.statut != "BROUILLON":
        raise ValueError("Cet arrêté est validé : annulez sa validation pour le recalculer.")
    p.date_arrete, p.auteur = d, auteur
    db.session.flush()
    LigneProvisionConges.query.filter_by(provision_id=p.id).delete(synchronize_session=False)
    for s in salaries_presents(tenant.id, d):
        db.session.add(LigneProvisionConges(tenant_id=tenant.id, provision_id=p.id, **calculer_ligne(tenant, s, d)))
    db.session.flush()
    db.session.refresh(p)
    return p


def precedent(p):
    """Dernier arrêté validé antérieur."""
    return (ProvisionConges.query.filter(ProvisionConges.tenant_id == p.tenant_id, ProvisionConges.statut == "VALIDE",
                                         ProvisionConges.annee * 100 + ProvisionConges.mois < p.annee * 100 + p.mois)
            .order_by(ProvisionConges.annee.desc(), ProvisionConges.mois.desc()).first())


def totaux(p):
    t = {"provision_national": 0.0, "provision_non_national": 0.0, "charges_national": 0.0,
         "charges_non_national": 0.0, "taxes": 0.0, "jours": 0.0}
    for l in p.lignes if p else []:
        suf = "national" if l.national else "non_national"
        t[f"provision_{suf}"] += _f(l.provision)
        t[f"charges_{suf}"] += _f(l.charges)
        t["taxes"] += _f(l.taxes)
        t["jours"] += _f(l.jours)
    t["provision"] = t["provision_national"] + t["provision_non_national"]
    t["charges"] = t["charges_national"] + t["charges_non_national"]
    t["total"] = t["provision"] + t["charges"] + t["taxes"]
    return t


def ecriture(p, tenant):
    """Lignes d'ajustement (variation vs arrêté précédent validé), équilibrées."""
    cpt = comptes(tenant)
    actuel, avant = totaux(p), totaux(precedent(p))
    lignes = []

    def paire(charge, dette, delta, libelle):
        delta = round(delta)
        if not delta:
            return
        if delta > 0:
            lignes.append({"compte": cpt[charge], "libelle": libelle, "debit": delta, "credit": 0})
            lignes.append({"compte": cpt[dette], "libelle": libelle, "debit": 0, "credit": delta})
        else:
            lignes.append({"compte": cpt[dette], "libelle": libelle, "debit": -delta, "credit": 0})
            lignes.append({"compte": cpt[charge], "libelle": libelle, "debit": 0, "credit": -delta})

    for k, charge, dette, lib in (
            ("provision_national", "conges_national", "dette_conges", "Provision congés payés (nationaux)"),
            ("provision_non_national", "conges_non_national", "dette_conges", "Provision congés payés (non nationaux)"),
            ("charges_national", "charges_national", "dette_charges", "Charges sociales sur congés (nationaux)"),
            ("charges_non_national", "charges_non_national", "dette_charges",
             "Charges sociales sur congés (non nationaux)"),
            ("taxes", "taxes", "dette_taxes", "Taxes sur congés à payer")):
        paire(charge, dette, actuel[k] - avant[k], lib)
    return lignes


def ecriture_sage(p, tenant) -> bytes:
    """Écriture au format texte Sage 100 (même format que le journal de paie)."""
    import plan_comptable_paie as pcp
    from export_comptable import _ligne_ecriture, _formater_ligne_sage, _nom_mois
    jrn = pcp.journal(tenant)
    date_str = p.date_arrete.strftime("%d/%m/%Y")
    piece = f"PCP{p.annee}{p.mois:02d}"
    prec = precedent(p)
    out = [f"; Provision congés payés — {tenant.denomination} — arrêté au {date_str}\r\n",
           f"; Ajustement depuis {'l arrêté du ' + prec.date_arrete.strftime('%d/%m/%Y') if prec else 'zéro (premier arrêté)'}\r\n"]
    for l in ecriture(p, tenant):
        out.append(_formater_ligne_sage(_ligne_ecriture(jrn, date_str, piece, l["compte"],
                                                        f"PCP {_nom_mois(p.mois)} {p.annee} {l['libelle']}",
                                                        l["debit"], l["credit"])))
    return "".join(out).encode("windows-1252", errors="replace")
