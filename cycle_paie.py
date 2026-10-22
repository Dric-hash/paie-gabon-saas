# -*- coding: utf-8 -*-
"""Cycle de paie : vue d'ensemble de l'année et liste de contrôle du mois.

Le cycle s'appuie sur ce que PaieGabon fait déjà (périodes, bulletins,
déclarations, export du journal, DAS) ; il ajoute le suivi de ce qui se passe
hors de l'application : dépôt des déclarations et paiement aux organismes.

  Ordre du cycle (chaque étape attend la précédente) :
    1. Pointage du mois arrêté (pointages mobiles validés ou rejetés)
    2. Bulletins calculés → validés → salaires payés
    3. Impôts et taxes (IRPP, TCS, FNH, CFP) déclarés et payés
       (puis écritures de paie comptabilisées et période clôturée)
    4. Chaque trimestre : DTS CNSS et CNAMGS déclarées et payées
    5. Chaque année : DAS déposée
  « Attend » sert à l'affichage et aux alertes ; rien n'est bloqué dans les
  écrans existants, et une date légale dépassée est toujours signalée.

Montants : bulletins VALIDÉS ou PAYÉS uniquement (jamais les brouillons).
Échéances : aucune date limite n'est codée en dur. Elles sont réglées par le
super-administrateur (table echeances_legales) ; tant qu'elles ne le sont pas,
l'écran affiche « échéance à renseigner ».
"""
from __future__ import annotations

import calendar
from datetime import date

STATUTS_RETENUS = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")
STATUTS_PAYES = ("PAYÉ", "PAYE")
STATUTS_BROUILLON = ("BROUILLON",)
MOIS = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août",
        "Septembre", "Octobre", "Novembre", "Décembre"]
MOIS_COURTS = ["", "Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]

# code → libellé, périodicité, champs du bulletin qui donnent le montant dû
OBLIGATIONS = {
    "IRPP":      {"libelle": "IRPP", "periodicite": "MENSUEL", "champs": ("irpp",)},
    "TCS":       {"libelle": "TCS", "periodicite": "MENSUEL", "champs": ("tcs",)},
    "FNH":       {"libelle": "FNH", "periodicite": "MENSUEL", "champs": ("fnh",)},
    "CFP":       {"libelle": "CFP", "periodicite": "MENSUEL", "champs": ("cfp",)},
    "POINTAGE":  {"libelle": "Pointage du mois", "periodicite": "MENSUEL", "champs": ()},
    "ECRITURES": {"libelle": "Écritures de paie", "periodicite": "MENSUEL", "champs": ()},
    "CNSS":      {"libelle": "DTS CNSS", "periodicite": "TRIMESTRIEL", "champs": ("cnss_salarie", "cnss_patronale")},
    "CNAMGS":    {"libelle": "DTS CNAMGS", "periodicite": "TRIMESTRIEL",
                  "champs": ("cnamgs_salarie", "cnamgs_patronale")},
    "DAS":       {"libelle": "DAS", "periodicite": "ANNUEL", "champs": ()},
}
TAXES_MENSUELLES = ("IRPP", "TCS", "FNH", "CFP")
SOCIALES = ("CNSS", "CNAMGS")
# codes pour lesquels un échéancier légal a un sens (pas les écritures internes)
CODES_ECHEANCE = ("IRPP", "TCS", "FNH", "CFP", "CNSS", "CNAMGS", "DAS")
STATUTS_OBLIGATION = {"A_FAIRE": "À faire", "DECLARE": "Déclaré", "PAYE": "Payé"}
# Les écritures n'ont pas de paiement : « comptabilisé » vaut terminé.
LIBELLE_FAIT = {"POINTAGE": "Arrêté", "ECRITURES": "Comptabilisées", "DAS": "Déposée"}
# Étapes sans paiement : « fait » une fois enregistrées (arrêté, comptabilisé, déposé).
SANS_PAIEMENT = ("POINTAGE", "ECRITURES", "DAS")
TERMINES = ("FAIT", "SANS_OBJET")


# ── Périodes ───────────────────────────────────────────────────────────────

def trimestre(mois: int) -> int:
    return (mois - 1) // 3 + 1


def mois_du_trimestre(t: int) -> list:
    return [3 * (t - 1) + 1, 3 * (t - 1) + 2, 3 * (t - 1) + 3]


def dernier_mois(code: str, periode: int) -> int:
    per = OBLIGATIONS[code]["periodicite"]
    if per == "MENSUEL":
        return periode
    if per == "TRIMESTRIEL":
        return 3 * periode
    return 12


def libelle_periode(code: str, annee: int, periode: int) -> str:
    per = OBLIGATIONS[code]["periodicite"]
    if per == "MENSUEL":
        return f"{MOIS[periode]} {annee}"
    if per == "TRIMESTRIEL":
        return f"T{periode} {annee}"
    return f"Exercice {annee}"


def periode_valide(code: str, periode: int) -> bool:
    per = OBLIGATIONS.get(code, {}).get("periodicite")
    if per == "MENSUEL":
        return 1 <= periode <= 12
    if per == "TRIMESTRIEL":
        return 1 <= periode <= 4
    return per == "ANNUEL" and periode == 0


# ── Échéances (réglées par le super-admin, jamais inventées) ─────────────

def echeances() -> dict:
    from models import EcheanceLegale
    return {e.code: e for e in EcheanceLegale.query.all()}


def date_limite(code: str, annee: int, periode: int, regles: dict) -> date | None:
    """Date limite calculée depuis la règle saisie ; None si la règle manque."""
    r = regles.get(code)
    if r is None or not r.jour or r.decalage_mois is None:
        return None
    m = dernier_mois(code, periode) + int(r.decalage_mois)
    a = annee + (m - 1) // 12
    m = (m - 1) % 12 + 1
    j = min(int(r.jour), calendar.monthrange(a, m)[1])
    return date(a, m, j)


# ── Échéances propres à l'entreprise (objectifs internes) ────────────────
# Chaque entreprise peut se fixer sa propre date (ex. payer l'IRPP le 10 alors
# que la date légale est le 15). La date légale reste la seule qui déclenche
# « En retard » ; l'objectif interne déclenche « Objectif dépassé ».
CODES_INTERNES = CODES_ECHEANCE + ("POINTAGE", "ECRITURES")


class Regles:
    """Échéances légales (communes) + objectifs internes (d'une entreprise)."""
    def __init__(self, legal: dict, interne: dict):
        self.legal, self.interne = legal, interne


def echeances_tenant(tenant_id: int) -> dict:
    from models import EcheanceTenant
    return {e.code: e for e in EcheanceTenant.query.filter_by(tenant_id=tenant_id).all()}


def regles_pour(tenant_id: int) -> Regles:
    return Regles(echeances(), echeances_tenant(tenant_id))


def _regles(regles) -> Regles:
    # compatibilité : un simple dict = échéances légales seules
    return regles if isinstance(regles, Regles) else Regles(regles or {}, {})


# ── Lecture des données ────────────────────────────────────────────────────

def _f(v) -> float:
    return float(v or 0)


def _bulletins_annee(tenant_id: int, annee: int):
    """{mois: [bulletins]} pour l'année, toutes situations confondues."""
    from models import BulletinPaie, PeriodePaie
    lignes = (BulletinPaie.query.join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
              .filter(BulletinPaie.tenant_id == tenant_id, PeriodePaie.tenant_id == tenant_id,
                      PeriodePaie.annee == annee)
              .with_entities(PeriodePaie.mois, BulletinPaie.statut, BulletinPaie.salaire_brut,
                             BulletinPaie.net_a_payer, BulletinPaie.irpp, BulletinPaie.tcs, BulletinPaie.fnh,
                             BulletinPaie.cfp, BulletinPaie.cnss_salarie, BulletinPaie.cnss_patronale,
                             BulletinPaie.cnamgs_salarie, BulletinPaie.cnamgs_patronale)
              .all())
    par_mois = {m: [] for m in range(1, 13)}
    for l in lignes:
        par_mois[l.mois].append(l)
    return par_mois


def _exports_journal(tenant_id: int, periode_ids: list) -> dict:
    """{periode_id: date du dernier export du journal de paie} (journal d'audit)."""
    from models import AuditLog
    if not periode_ids:
        return {}
    res = {}
    # pas de filtre sur tenant_id : en mode cabinet, le journal d'audit porte le
    # cabinet ; les identifiants de période appartiennent déjà à l'entreprise.
    q = (AuditLog.query.filter(AuditLog.action == "EXPORT",
                               AuditLog.entite == "bulletin", AuditLog.entite_id.in_(periode_ids),
                               AuditLog.description.like("Export Sage Journal%"))
         .order_by(AuditLog.id))
    for a in q.all():
        res[a.entite_id] = a.date_action
    return res


def _bornes_mois(annee: int, mois: int):
    return date(annee, mois, 1), date(annee, mois, calendar.monthrange(annee, mois)[1])


def _pointages_annee(tenant_id: int, annee: int) -> dict:
    """{mois: {"sal": nb salariés pointés, "jour": nb journaliers pointés,
    "lignes": nb pointages validés, "attente": nb pointages mobiles SOUMIS}}."""
    from models import Pointage
    lignes = (Pointage.query.filter(Pointage.tenant_id == tenant_id,
                                    Pointage.date_pointage >= date(annee, 1, 1),
                                    Pointage.date_pointage <= date(annee, 12, 31))
              .with_entities(Pointage.date_pointage, Pointage.salarie_id, Pointage.journalier_id, Pointage.statut)
              .all())
    res = {m: {"sal": set(), "jour": set(), "lignes": 0, "attente": 0} for m in range(1, 13)}
    for d, sid, jid, st in lignes:
        r = res[d.month]
        st = (st or "VALIDE").upper()
        if st == "SOUMIS":
            r["attente"] += 1
        elif st == "VALIDE":
            r["lignes"] += 1
            if sid:
                r["sal"].add(sid)
            if jid:
                r["jour"].add(jid)
    return {m: {"sal": len(r["sal"]), "jour": len(r["jour"]), "lignes": r["lignes"], "attente": r["attente"]}
            for m, r in res.items()}


def utilise_pointage(tenant_id: int) -> bool:
    from models import Pointage
    return Pointage.query.filter_by(tenant_id=tenant_id).first() is not None


def pointages_en_attente(tenant_id: int, annee: int, mois: int) -> int:
    from models import Pointage
    debut, fin = _bornes_mois(annee, mois)
    return (Pointage.query.filter(Pointage.tenant_id == tenant_id, Pointage.statut == "SOUMIS",
                                  Pointage.date_pointage >= debut, Pointage.date_pointage <= fin).count())


def obligations(tenant_id: int, annee: int) -> dict:
    """{(code, periode): ObligationPaie} pour l'année."""
    from models import ObligationPaie
    return {(o.code, o.periode): o for o in ObligationPaie.query.filter_by(tenant_id=tenant_id, annee=annee).all()}


def montants(bulletins) -> dict:
    """Montants dus par code, calculés sur les bulletins validés ou payés."""
    retenus = [b for b in bulletins if (b.statut or "").upper() in STATUTS_RETENUS]
    res = {}
    for code, d in OBLIGATIONS.items():
        if d["champs"]:
            res[code] = round(sum(_f(getattr(b, c)) for b in retenus for c in d["champs"]), 2)
    res["BRUT"] = round(sum(_f(b.salaire_brut) for b in retenus), 2)
    res["NET"] = round(sum(_f(b.net_a_payer) for b in retenus), 2)
    return res


# ── États ──────────────────────────────────────────────────────────────────

def _etat_obligation(code, annee, periode, ob, du, regles, aujourd_hui, pret=True):
    """Situation d'une obligation :
    FAIT | DECLARE | RETARD | OBJECTIF | A_FAIRE | ATTENTE | SANS_OBJET.
    RETARD   : date légale dépassée ;
    OBJECTIF : objectif interne de l'entreprise dépassé (date légale pas encore atteinte)."""
    regles = _regles(regles)
    limite = date_limite(code, annee, periode, regles.legal) if code in CODES_ECHEANCE else None
    cible = date_limite(code, annee, periode, regles.interne) if code in CODES_INTERNES else None
    statut = ob.statut if ob else "A_FAIRE"
    termine = statut == "PAYE" or (statut == "DECLARE" and (code in SANS_PAIEMENT or not du))
    if termine:
        etat = "FAIT"
    elif du == 0 and code not in SANS_PAIEMENT and statut == "A_FAIRE":
        etat = "SANS_OBJET"
    elif limite and aujourd_hui > limite:
        etat = "RETARD"
    elif cible and aujourd_hui > cible:
        etat = "OBJECTIF"
    elif statut == "DECLARE":
        etat = "DECLARE"
    elif not pret:
        etat = "ATTENTE"
    else:
        etat = "A_FAIRE"
    return {"code": code, "libelle": OBLIGATIONS[code]["libelle"], "annee": annee, "periode": periode,
            "du": du, "statut": statut, "etat": etat, "limite": limite, "cible": cible, "ob": ob,
            "libelle_periode": libelle_periode(code, annee, periode)}


def etat_mois(t, annee: int, mois: int, par_mois=None, periodes=None, obs=None, regles=None,
              exports=None, aujourd_hui: date | None = None, pointages=None, avec_pointage=None) -> dict:
    """Liste de contrôle d'un mois, dans l'ordre du cycle :
    pointage → bulletins → paiement des salaires → impôts et taxes → écritures → clôture."""
    from models import PeriodePaie
    aujourd_hui = aujourd_hui or date.today()
    if par_mois is None:
        par_mois = _bulletins_annee(t.id, annee)
    if periodes is None:
        periodes = {p.mois: p for p in PeriodePaie.query.filter_by(tenant_id=t.id, annee=annee).all()}
    obs = obligations(t.id, annee) if obs is None else obs
    regles = regles_pour(t.id) if regles is None else regles
    if pointages is None:
        pointages = _pointages_annee(t.id, annee)
    if avec_pointage is None:
        avec_pointage = utilise_pointage(t.id)
    p = periodes.get(mois)
    if exports is None:
        exports = _exports_journal(t.id, [p.id] if p else [])
    debut, fin = _bornes_mois(annee, mois)
    commence = debut <= aujourd_hui
    buls = par_mois.get(mois, [])
    st = [(b.statut or "").upper() for b in buls]
    nb = len(buls)
    nb_brouillon = sum(1 for s in st if s in STATUTS_BROUILLON)
    nb_payes = sum(1 for s in st if s in STATUTS_PAYES)
    nb_valides = sum(1 for s in st if s in STATUTS_RETENUS)  # validés + payés
    m = montants(buls)
    cloture = bool(p and (p.statut or "").upper().startswith("CL"))
    lien_bul = f"/bulletins?periode_id={p.id}" if p else "/periodes"

    # 1. Pointage
    pt = pointages.get(mois, {"sal": 0, "jour": 0, "lignes": 0, "attente": 0})
    pointage = _etat_obligation("POINTAGE", annee, mois, obs.get(("POINTAGE", mois)), None, regles,
                                aujourd_hui, pret=commence)
    if pointage["etat"] != "FAIT" and not avec_pointage and not pt["attente"]:
        pointage["etat"] = "SANS_OBJET"          # l'entreprise n'utilise pas le pointage
    pointage.update({"stats": pt, "commence": commence,
                     "lien": f"/pointage?date={debut.isoformat()}",
                     "lien_mobile": "/pointages-mobiles" if pt["attente"] else None})
    pointage_ok = pointage["etat"] in TERMINES

    # 2. Bulletins et paiement des salaires
    if nb:
        calcul = "FAIT"
    else:
        calcul = "A_FAIRE" if (pointage_ok and commence) else "ATTENTE"
    validation = "FAIT" if nb and not nb_brouillon else ("A_FAIRE" if nb else "ATTENTE")
    paie_ok = bool(nb_valides) and not nb_brouillon and nb_payes == nb_valides
    paiement = "FAIT" if paie_ok else ("A_FAIRE" if nb_valides and not nb_brouillon else "ATTENTE")
    etapes = [
        {"code": "PERIODE", "libelle": "Période de paie ouverte", "etat": "FAIT" if p else
         ("A_FAIRE" if commence else "ATTENTE"), "detail": "Ouverte" if p else "Période à créer",
         "lien": f"/periodes/{p.id}" if p else "/periodes"},
        {"code": "CALCUL", "libelle": "Bulletins calculés", "etat": calcul,
         "detail": (f"{nb} bulletin(s)" if nb else
                    ("Aucun bulletin" if pointage_ok else "Après l'arrêt du pointage")),
         "lien": lien_bul},
        {"code": "VALIDATION", "libelle": "Bulletins validés", "etat": validation,
         "detail": (f"{nb_brouillon} brouillon(s) à valider" if nb_brouillon
                    else (f"{nb_valides} validé(s)" if nb else "—")), "lien": lien_bul},
        {"code": "PAIEMENT", "libelle": "Salaires payés", "etat": paiement,
         "detail": (f"{nb_payes} / {nb_valides} bulletin(s) payé(s)" if nb_valides else "—"),
         "montant": m["NET"], "lien": lien_bul},
    ]

    # 3. Impôts et taxes : après le paiement des salaires
    taxes = [_etat_obligation(c, annee, mois, obs.get((c, mois)), m[c], regles, aujourd_hui, paie_ok)
             for c in TAXES_MENSUELLES]
    taxes_ok = paie_ok and all(x["etat"] in TERMINES for x in taxes)

    # Écritures puis clôture
    ecr = _etat_obligation("ECRITURES", annee, mois, obs.get(("ECRITURES", mois)), None, regles,
                           aujourd_hui, paie_ok)
    ecr["exporte_le"] = exports.get(p.id) if p else None
    etapes.append({"code": "CLOTURE", "libelle": "Période clôturée",
                   "etat": "FAIT" if cloture else ("A_FAIRE" if taxes_ok and ecr["etat"] == "FAIT" else "ATTENTE"),
                   "detail": "Clôturée" if cloture else "Ouverte",
                   "lien": f"/periodes/{p.id}" if p else None})
    # mois sans aucune paie et déjà terminé : neutre pour les trimestres et la DAS
    vide = not p and not nb and fin < aujourd_hui
    return {"annee": annee, "mois": mois, "libelle": f"{MOIS[mois]} {annee}", "periode": p,
            "nb": nb, "nb_brouillon": nb_brouillon, "nb_valides": nb_valides, "nb_payes": nb_payes,
            "montants": m, "pointage": pointage, "etapes": etapes, "taxes": taxes, "ecritures": ecr,
            "cloture": cloture, "pointage_ok": pointage_ok, "paie_ok": paie_ok, "taxes_ok": taxes_ok,
            "complet": pointage_ok and taxes_ok, "vide": vide, "commence": commence,
            "trimestre": trimestre(mois), "fin_trimestre": mois % 3 == 0}


A_TRAITER = ("RETARD", "OBJECTIF", "A_FAIRE", "DECLARE")


def _resume(etats: list) -> str:
    """Résumé d'un groupe d'états : RETARD > OBJECTIF > A_FAIRE/DECLARE > ATTENTE > FAIT."""
    es = [e for e in etats if e != "SANS_OBJET"]
    if not es:
        return "SANS_OBJET"
    for cle in ("RETARD", "OBJECTIF", "A_FAIRE", "DECLARE", "ATTENTE"):
        if cle in es:
            return cle
    return "FAIT"


def etat_annee(t, annee: int, aujourd_hui: date | None = None) -> dict:
    from models import PeriodePaie
    aujourd_hui = aujourd_hui or date.today()
    par_mois = _bulletins_annee(t.id, annee)
    periodes = {p.mois: p for p in PeriodePaie.query.filter_by(tenant_id=t.id, annee=annee).all()}
    obs = obligations(t.id, annee)
    regles = regles_pour(t.id)
    exports = _exports_journal(t.id, [p.id for p in periodes.values()])
    pointages = _pointages_annee(t.id, annee)
    avec_pointage = utilise_pointage(t.id)
    mois = []
    for m in range(1, 13):
        e = etat_mois(t, annee, m, par_mois, periodes, obs, regles, exports, aujourd_hui, pointages, avec_pointage)
        etapes = {x["code"]: x["etat"] for x in e["etapes"]}
        e["colonnes"] = {
            "POINTAGE": e["pointage"]["etat"],
            "PAIE": _resume([etapes["CALCUL"], etapes["VALIDATION"]]),
            "PAIEMENT": etapes["PAIEMENT"],
            "TAXES": _resume([x["etat"] for x in e["taxes"]]),
            "ECRITURES": e["ecritures"]["etat"],
            "CLOTURE": etapes["CLOTURE"],
        }
        e["actif"] = bool(e["periode"] or e["nb"] or pointages[m]["lignes"] or pointages[m]["attente"]
                          or any(obs.get((c, m)) for c in TAXES_MENSUELLES + ("POINTAGE", "ECRITURES")))
        mois.append(e)

    # 4. DTS : quand les trois mois du trimestre sont payés et leurs impôts réglés
    trimestres = []
    for tr in range(1, 5):
        ms = mois_du_trimestre(tr)
        buls = [b for m in ms for b in par_mois[m]]
        mt = montants(buls)
        nb_brouillon = sum(1 for m in ms for b in par_mois[m] if (b.statut or "").upper() in STATUTS_BROUILLON)
        avec_paie = [m for m in ms if not mois[m - 1]["vide"]]
        complets = [m for m in avec_paie if mois[m - 1]["complet"]]
        pret = bool(avec_paie) and len(complets) == len(avec_paie) and \
            all(mois[m - 1]["complet"] or mois[m - 1]["vide"] for m in ms)
        ob_lignes = [_etat_obligation(c, annee, tr, obs.get((c, tr)), mt[c], regles, aujourd_hui, pret)
                     for c in SOCIALES]
        trimestres.append({"num": tr, "libelle": f"T{tr} {annee}",
                           "mois": ms, "mois_libelles": " · ".join(MOIS_COURTS[m] for m in ms),
                           "montants": mt, "brut": mt["BRUT"], "nb_brouillon": nb_brouillon,
                           "mois_complets": len(complets), "mois_attendus": len(avec_paie) or 3,
                           "mois_clotures": sum(1 for m in ms if mois[m - 1]["cloture"]),
                           "pret": pret, "obligations": ob_lignes,
                           "termine": all(x["etat"] in TERMINES for x in ob_lignes) and pret,
                           "actif": bool([m for m in ms if mois[m - 1]["nb"]]) or any(obs.get((c, tr)) for c in SOCIALES)})

    # 5. DAS : quand les quatre trimestres sont déclarés et payés
    nb_clotures = sum(1 for e in mois if e["cloture"])
    trims_utiles = [tr for tr in trimestres if any(not mois[m - 1]["vide"] for m in tr["mois"])]
    dts_faites = sum(1 for tr in trims_utiles if tr["termine"])
    # l'exercice doit être terminé et toutes les DTS de l'année faites
    das_pret = bool(trims_utiles) and dts_faites == len(trims_utiles) and date(annee, 12, 31) < aujourd_hui
    das = _etat_obligation("DAS", annee, 0, obs.get(("DAS", 0)), None, regles, aujourd_hui, pret=das_pret)
    das.update({"mois_clotures": nb_clotures, "dts_faites": dts_faites, "dts_attendues": len(trims_utiles) or 4})

    # Parcours : avancement de chaque grande étape, dans l'ordre
    ecoules = [e for e in mois if e["commence"] and not e["vide"]]
    n = len(ecoules)
    parcours = [
        {"code": "POINTAGE", "libelle": "Pointage", "fait": sum(1 for e in ecoules if e["pointage_ok"]), "total": n,
         "unite": "mois"},
        {"code": "PAIE", "libelle": "Bulletins et paiement", "fait": sum(1 for e in ecoules if e["paie_ok"]),
         "total": n, "unite": "mois"},
        {"code": "IMPOTS", "libelle": "Impôts et taxes", "fait": sum(1 for e in ecoules if e["taxes_ok"]),
         "total": n, "unite": "mois"},
        {"code": "DTS", "libelle": "DTS CNSS et CNAMGS", "fait": dts_faites, "total": len(trims_utiles),
         "unite": "trimestre(s)"},
        {"code": "DAS", "libelle": "DAS", "fait": 1 if das["etat"] == "FAIT" else 0, "total": 1, "unite": ""},
    ]
    for etape in parcours:
        etape["etat"] = ("FAIT" if etape["total"] and etape["fait"] == etape["total"]
                         else ("A_FAIRE" if etape["total"] else "ATTENTE"))
    parcours[-1]["etat"] = das["etat"]

    # À faire maintenant : retards d'abord, puis ce qui est prêt, dans l'ordre du cycle
    a_faire = []
    for e in mois:
        for x in [e["pointage"]] + e["taxes"] + [e["ecritures"]]:
            if x["etat"] in A_TRAITER:
                a_faire.append(x)
    for tr in trimestres:
        for x in tr["obligations"]:
            if x["etat"] in A_TRAITER:
                a_faire.append(x)
    if das["etat"] in A_TRAITER:
        a_faire.append(das)
    ordre = {"RETARD": 0, "OBJECTIF": 1, "DECLARE": 2, "A_FAIRE": 3}
    a_faire.sort(key=lambda x: (ordre[x["etat"]], min(x["cible"] or date.max, x["limite"] or date.max)))
    compte = {k: sum(1 for x in a_faire if x["etat"] == k) for k in ("RETARD", "OBJECTIF")}
    return {"annee": annee, "mois": mois, "trimestres": trimestres, "das": das, "a_faire": a_faire,
            "compte": compte, "parcours": parcours, "avec_pointage": avec_pointage,
            "echeances_manquantes": [c for c in CODES_ECHEANCE if date_limite(c, annee, 1 if
                                     OBLIGATIONS[c]["periodicite"] != "ANNUEL" else 0,
                                     regles.legal) is None],
            "nb_clotures": nb_clotures}


# ── Saisie ─────────────────────────────────────────────────────────────────

def _date(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ValueError("date invalide (format AAAA-MM-JJ)")


def _montant(v):
    v = (v or "").replace(" ", "").replace(" ", "").replace("\xa0", "").replace(",", ".").strip()
    if not v:
        return None
    try:
        x = round(float(v), 2)
    except ValueError:
        raise ValueError("montant invalide")
    if x < 0:
        raise ValueError("montant négatif")
    return x


def enregistrer(t, code: str, annee: int, periode: int, action: str, form, par: str):
    """Enregistre une déclaration, un paiement ou une annulation. Retourne l'obligation.
    action : DECLARE | PAYE | ANNULER."""
    from models import db, ObligationPaie
    if code not in OBLIGATIONS:
        raise ValueError("obligation inconnue")
    if not periode_valide(code, periode):
        raise ValueError("période invalide")
    if not (2000 <= annee <= 2100):
        raise ValueError("année invalide")
    if action not in ("DECLARE", "PAYE", "ANNULER"):
        raise ValueError("action inconnue")
    if code in SANS_PAIEMENT and action == "PAYE":
        action = "DECLARE"
    if code == "POINTAGE" and action == "DECLARE":
        n = pointages_en_attente(t.id, annee, periode)
        if n:
            raise ValueError(f"{n} pointage(s) mobile(s) du mois attendent encore une validation ou un rejet")
    ob = ObligationPaie.query.filter_by(tenant_id=t.id, code=code, annee=annee, periode=periode).first()
    if action == "ANNULER":
        if ob is not None:
            db.session.delete(ob)
        return None
    if ob is None:
        ob = ObligationPaie(tenant_id=t.id, code=code, annee=annee, periode=periode, statut="A_FAIRE")
        db.session.add(ob)
    d_decl = _date(form.get("date_declaration"))
    d_pay = _date(form.get("date_paiement"))
    mt = _montant(form.get("montant_paye"))
    ref = (form.get("reference") or "").strip()[:100]
    com = (form.get("commentaire") or "").strip()[:500]
    if action == "DECLARE":
        ob.statut = "DECLARE" if ob.statut != "PAYE" else ob.statut
        ob.date_declaration = d_decl or ob.date_declaration or date.today()
    else:
        ob.statut = "PAYE"
        ob.date_paiement = d_pay or ob.date_paiement or date.today()
        ob.date_declaration = d_decl or ob.date_declaration or ob.date_paiement
        if mt is not None:
            ob.montant_paye = mt
    if ref:
        ob.reference = ref
    if com:
        ob.commentaire = com
    ob.saisi_par = (par or "")[:150]
    return ob
