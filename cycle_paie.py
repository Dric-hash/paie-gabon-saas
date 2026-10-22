# -*- coding: utf-8 -*-
"""Cycle de paie : vue d'ensemble de l'année et liste de contrôle du mois.

Le cycle s'appuie sur ce que PaieGabon fait déjà (périodes, bulletins,
déclarations, export du journal, DAS) ; il ajoute le suivi de ce qui se passe
hors de l'application : dépôt des déclarations et paiement aux organismes.

  Chaque mois       : bulletins calculés → validés → salaires payés
                      → impôts et taxes (IRPP, TCS, FNH, CFP) déclarés / payés
                      → écritures de paie exportées / comptabilisées
                      → période clôturée
  Chaque trimestre  : DTS CNSS et CNAMGS déclarées / payées
  Chaque année      : DAS déposée

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
LIBELLE_FAIT = {"ECRITURES": "Comptabilisées", "DAS": "Déposée"}


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
    """Situation d'une obligation : FAIT | DECLARE | RETARD | A_FAIRE | ATTENTE | SANS_OBJET."""
    limite = date_limite(code, annee, periode, regles) if code in CODES_ECHEANCE else None
    statut = ob.statut if ob else "A_FAIRE"
    termine = statut == "PAYE" or (statut == "DECLARE" and (code in ("ECRITURES", "DAS") or not du))
    if termine:
        etat = "FAIT"
    elif du == 0 and code not in ("ECRITURES", "DAS") and statut == "A_FAIRE":
        etat = "SANS_OBJET"
    elif limite and aujourd_hui > limite:
        etat = "RETARD"
    elif statut == "DECLARE":
        etat = "DECLARE"
    elif not pret:
        etat = "ATTENTE"
    else:
        etat = "A_FAIRE"
    return {"code": code, "libelle": OBLIGATIONS[code]["libelle"], "annee": annee, "periode": periode,
            "du": du, "statut": statut, "etat": etat, "limite": limite, "ob": ob,
            "libelle_periode": libelle_periode(code, annee, periode)}


def etat_mois(t, annee: int, mois: int, par_mois=None, periodes=None, obs=None, regles=None,
              exports=None, aujourd_hui: date | None = None) -> dict:
    """Liste de contrôle d'un mois."""
    from models import PeriodePaie, Salarie
    aujourd_hui = aujourd_hui or date.today()
    if par_mois is None:
        par_mois = _bulletins_annee(t.id, annee)
    if periodes is None:
        periodes = {p.mois: p for p in PeriodePaie.query.filter_by(tenant_id=t.id, annee=annee).all()}
    obs = obligations(t.id, annee) if obs is None else obs
    regles = echeances() if regles is None else regles
    p = periodes.get(mois)
    if exports is None:
        exports = _exports_journal(t.id, [p.id] if p else [])
    buls = par_mois.get(mois, [])
    st = [(b.statut or "").upper() for b in buls]
    nb = len(buls)
    nb_brouillon = sum(1 for s in st if s in STATUTS_BROUILLON)
    nb_payes = sum(1 for s in st if s in STATUTS_PAYES)
    nb_valides = sum(1 for s in st if s in STATUTS_RETENUS)  # validés + payés
    m = montants(buls)
    cloture = bool(p and (p.statut or "").upper().startswith("CL"))

    etapes = []
    etapes.append({"code": "PERIODE", "libelle": "Période de paie ouverte",
                   "etat": "FAIT" if p else "A_FAIRE",
                   "detail": ("Ouverte" if p else "Période à créer"),
                   "lien": (f"/periodes/{p.id}" if p else "/periodes")})
    etapes.append({"code": "CALCUL", "libelle": "Bulletins calculés",
                   "etat": "FAIT" if nb else ("ATTENTE" if not p else "A_FAIRE"),
                   "detail": f"{nb} bulletin(s)" if nb else "Aucun bulletin",
                   "lien": (f"/bulletins?periode_id={p.id}" if p else None)})
    etapes.append({"code": "VALIDATION", "libelle": "Bulletins validés",
                   "etat": ("FAIT" if nb and not nb_brouillon else ("A_FAIRE" if nb else "ATTENTE")),
                   "detail": (f"{nb_brouillon} brouillon(s) à valider" if nb_brouillon
                              else (f"{nb_valides} validé(s)" if nb else "—")),
                   "lien": (f"/bulletins?periode_id={p.id}" if p else None)})
    etapes.append({"code": "PAIEMENT", "libelle": "Salaires payés",
                   "etat": ("FAIT" if nb_valides and nb_payes == nb_valides and not nb_brouillon
                            else ("A_FAIRE" if nb_valides else "ATTENTE")),
                   "detail": (f"{nb_payes} / {nb_valides} bulletin(s) payé(s)" if nb_valides else "—"),
                   "montant": m["NET"], "lien": (f"/bulletins?periode_id={p.id}" if p else None)})
    pret = bool(nb_valides) and not nb_brouillon
    taxes = [_etat_obligation(c, annee, mois, obs.get((c, mois)), m[c], regles, aujourd_hui, pret)
             for c in TAXES_MENSUELLES]
    ecr = _etat_obligation("ECRITURES", annee, mois, obs.get(("ECRITURES", mois)), None, regles,
                           aujourd_hui, pret)
    ecr["exporte_le"] = exports.get(p.id) if p else None
    etapes.append({"code": "CLOTURE", "libelle": "Période clôturée",
                   "etat": "FAIT" if cloture else ("A_FAIRE" if pret else "ATTENTE"),
                   "detail": ("Clôturée" if cloture else "Ouverte"),
                   "lien": (f"/periodes/{p.id}" if p else None)})
    tri = trimestre(mois)
    return {"annee": annee, "mois": mois, "libelle": f"{MOIS[mois]} {annee}", "periode": p,
            "nb": nb, "nb_brouillon": nb_brouillon, "nb_valides": nb_valides, "nb_payes": nb_payes,
            "montants": m, "etapes": etapes, "taxes": taxes, "ecritures": ecr, "cloture": cloture,
            "trimestre": tri, "fin_trimestre": mois % 3 == 0}


def _resume(etats: list) -> str:
    """Résumé d'un groupe d'états : RETARD > A_FAIRE/DECLARE > ATTENTE > FAIT."""
    es = [e for e in etats if e != "SANS_OBJET"]
    if not es:
        return "SANS_OBJET"
    for cle in ("RETARD", "A_FAIRE", "DECLARE", "ATTENTE"):
        if cle in es:
            return cle
    return "FAIT"


def etat_annee(t, annee: int, aujourd_hui: date | None = None) -> dict:
    from models import PeriodePaie
    aujourd_hui = aujourd_hui or date.today()
    par_mois = _bulletins_annee(t.id, annee)
    periodes = {p.mois: p for p in PeriodePaie.query.filter_by(tenant_id=t.id, annee=annee).all()}
    obs = obligations(t.id, annee)
    regles = echeances()
    exports = _exports_journal(t.id, [p.id for p in periodes.values()])
    mois = []
    for m in range(1, 13):
        e = etat_mois(t, annee, m, par_mois, periodes, obs, regles, exports, aujourd_hui)
        etapes = {x["code"]: x["etat"] for x in e["etapes"]}
        e["colonnes"] = {
            "PAIE": _resume([etapes["CALCUL"], etapes["VALIDATION"]]),
            "PAIEMENT": etapes["PAIEMENT"],
            "TAXES": _resume([x["etat"] for x in e["taxes"]]),
            "ECRITURES": e["ecritures"]["etat"],
            "CLOTURE": etapes["CLOTURE"],
        }
        e["actif"] = bool(e["periode"] or e["nb"] or any(obs.get((c, m)) for c in TAXES_MENSUELLES + ("ECRITURES",)))
        mois.append(e)

    trimestres = []
    for tr in range(1, 5):
        ms = mois_du_trimestre(tr)
        buls = [b for m in ms for b in par_mois[m]]
        mt = montants(buls)
        nb_brouillon = sum(1 for m in ms for b in par_mois[m] if (b.statut or "").upper() in STATUTS_BROUILLON)
        mois_paies = [m for m in ms if any((b.statut or "").upper() in STATUTS_RETENUS for b in par_mois[m])]
        clotures = [m for m in ms if mois[m - 1]["cloture"]]
        pret = bool(mois_paies) and not nb_brouillon and len(clotures) == len(ms)
        ob_lignes = [_etat_obligation(c, annee, tr, obs.get((c, tr)), mt[c], regles, aujourd_hui, pret)
                     for c in SOCIALES]
        trimestres.append({"num": tr, "libelle": f"T{tr} {annee}",
                           "mois": ms, "mois_libelles": " · ".join(MOIS_COURTS[m] for m in ms),
                           "montants": mt, "brut": mt["BRUT"], "nb_brouillon": nb_brouillon,
                           "mois_clotures": len(clotures), "pret": pret, "obligations": ob_lignes,
                           "actif": bool(mois_paies) or any(obs.get((c, tr)) for c in SOCIALES)})

    nb_clotures = sum(1 for e in mois if e["cloture"])
    das = _etat_obligation("DAS", annee, 0, obs.get(("DAS", 0)), None, regles, aujourd_hui,
                           pret=nb_clotures == 12)
    das["mois_clotures"] = nb_clotures

    # À faire maintenant : retards d'abord, puis ce qui est prêt
    a_faire = []
    for e in mois:
        for x in e["taxes"] + [e["ecritures"]]:
            if x["etat"] in ("RETARD", "A_FAIRE", "DECLARE"):
                a_faire.append(x)
    for tr in trimestres:
        for x in tr["obligations"]:
            if x["etat"] in ("RETARD", "A_FAIRE", "DECLARE"):
                a_faire.append(x)
    if das["etat"] in ("RETARD", "A_FAIRE", "DECLARE"):
        a_faire.append(das)
    ordre = {"RETARD": 0, "DECLARE": 1, "A_FAIRE": 2}
    a_faire.sort(key=lambda x: (ordre[x["etat"]], x["limite"] or date.max))
    compte = {"RETARD": sum(1 for x in a_faire if x["etat"] == "RETARD")}
    return {"annee": annee, "mois": mois, "trimestres": trimestres, "das": das, "a_faire": a_faire,
            "compte": compte, "echeances_manquantes": [c for c in CODES_ECHEANCE if date_limite(c, annee, 1 if
                                                       OBLIGATIONS[c]["periodicite"] != "ANNUEL" else 0,
                                                       regles) is None],
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
    if code in ("ECRITURES", "DAS") and action == "PAYE":
        action = "DECLARE"
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
