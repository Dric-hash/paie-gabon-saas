"""planning_conges.py — Planning prévisionnel des congés et vue annuelle.

Déroulé d'une année :
  1. les salariés proposent leurs dates sur leur portail (PROPOSEE), le RH en saisit aussi ;
  2. le RH retient ou refuse chaque période ;
  3. la direction valide le planning (plus aucune proposition en attente) ;
  4. le moment venu, le RH transforme une période retenue en demande de congé, qui suit
     ensuite le circuit habituel (approbation → solde). Le planning ne modifie jamais
     un solde : il prévoit.

Contrôles affichés (sans rien bloquer) :
  - jours prévus comparés au solde restant de l'année (même calcul que la page Congés) ;
  - jours où trop de salariés sont absents en même temps, par chantier et sur
    l'entreprise, au-delà du seuil choisi par l'entreprise (en % de l'effectif).
"""
from datetime import date, timedelta

from models import (db, utcnow, Conge, Salarie, PlanningConges, PrevisionConge, AffectationSite, Site,
                    CategorieEmploi)

STATUTS_PREVISION = {"PROPOSEE": "Proposée", "RETENUE": "Retenue", "REFUSEE": "Refusée"}
STATUTS_REELS_ACCORDES = ("APPROUVÉ", "APPROUVE", "PRIS")
STATUTS_REELS_DEMANDES = ("DEMANDÉ", "DEMANDE")
MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre"]
EFFECTIF_MIN_ALERTE = 2      # pas d'alerte « trop d'absents » sur un groupe d'une seule personne
ABSENTS_MIN_ALERTE = 2       # … ni pour un seul absent (sur un petit groupe, 1 absent dépasse vite le seuil)
MOIS_COURTS = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


class ErreurPlanning(ValueError):
    pass


# ── Accès ────────────────────────────────────────────────────────────────────
def obtenir(t, annee, creer=False):
    p = PlanningConges.query.filter_by(tenant_id=t.id, annee=annee).first()
    if p is None and creer:
        p = PlanningConges(tenant_id=t.id, annee=annee)
        db.session.add(p)
        db.session.flush()
    return p


def bornes(annee):
    return date(annee, 1, 1), date(annee, 12, 31)


def _chevauche(a1, b1, a2, b2):
    return a1 <= b2 and a2 <= b1


def conges_reels(t, annee, salarie_id=None):
    """Congés demandés ou accordés (avec dates) qui touchent l'année."""
    d1, d2 = bornes(annee)
    q = Conge.query.filter(Conge.tenant_id == t.id, Conge.date_depart.isnot(None), Conge.date_retour.isnot(None),
                           Conge.date_depart <= d2, Conge.date_retour >= d1,
                           Conge.statut.in_(STATUTS_REELS_ACCORDES + STATUTS_REELS_DEMANDES))
    if salarie_id:
        q = q.filter(Conge.salarie_id == salarie_id)
    return q.all()


def _verifier_dates(p, debut, fin):
    if debut is None or fin is None:
        raise ErreurPlanning("dates invalides")
    if fin < debut:
        raise ErreurPlanning("la date de fin doit suivre la date de début")
    if debut.year != p.annee:
        raise ErreurPlanning(f"la période doit commencer en {p.annee}")
    if (fin - debut).days >= 120:
        raise ErreurPlanning("période trop longue (plus de 120 jours)")


def _verifier_chevauchement(p, salarie, debut, fin, sauf=None):
    for x in PrevisionConge.query.filter_by(tenant_id=p.tenant_id, planning_id=p.id, salarie_id=salarie.id):
        if x.id != sauf and x.statut != "REFUSEE" and _chevauche(debut, fin, x.date_debut, x.date_fin):
            raise ErreurPlanning(f"ces dates chevauchent une autre période prévue "
                                 f"({x.date_debut.strftime('%d/%m')} – {x.date_fin.strftime('%d/%m')})")


# ── Périodes ─────────────────────────────────────────────────────────────────
def ajouter(p, salarie, debut, fin, origine="RH", commentaire=None, utilisateur=None):
    if salarie is None or salarie.tenant_id != p.tenant_id:
        raise ErreurPlanning("salarié introuvable")
    _verifier_dates(p, debut, fin)
    _verifier_chevauchement(p, salarie, debut, fin)
    x = PrevisionConge(tenant_id=p.tenant_id, planning_id=p.id, salarie_id=salarie.id, date_debut=debut,
                       date_fin=fin, origine=origine,
                       statut="RETENUE" if origine == "RH" else "PROPOSEE",
                       commentaire=(commentaire or "").strip()[:500] or None,
                       cree_par_id=getattr(utilisateur, "id", None))
    db.session.add(x)
    db.session.flush()
    return x


def modifier(x, debut, fin):
    if x.conge_id:
        raise ErreurPlanning("la demande de congé a déjà été créée : modifiez-la dans Congés")
    _verifier_dates(x.planning, debut, fin)
    _verifier_chevauchement(x.planning, x.salarie, debut, fin, sauf=x.id)
    x.date_debut, x.date_fin = debut, fin
    db.session.flush()


def retenir(x):
    if x.statut == "REFUSEE":
        _verifier_chevauchement(x.planning, x.salarie, x.date_debut, x.date_fin, sauf=x.id)
    x.statut, x.motif_refus = "RETENUE", None
    db.session.flush()


def refuser(x, motif):
    if x.conge_id:
        raise ErreurPlanning("la demande de congé a déjà été créée : traitez-la dans Congés")
    x.statut, x.motif_refus = "REFUSEE", (motif or "").strip()[:300] or None
    db.session.flush()


def supprimer(x):
    if x.conge_id:
        raise ErreurPlanning("la demande de congé a déjà été créée : la période reste au planning")
    db.session.delete(x)
    db.session.flush()


def creer_demande(x, utilisateur=None):
    """Transforme une période retenue d'un planning validé en demande de congé (DEMANDÉ),
    qui suit ensuite le circuit habituel. Retourne la demande."""
    p = x.planning
    if p.statut != "VALIDE":
        raise ErreurPlanning("le planning doit d'abord être validé par la direction")
    if x.statut != "RETENUE":
        raise ErreurPlanning("seule une période retenue devient une demande de congé")
    if x.conge_id:
        raise ErreurPlanning("la demande a déjà été créée")
    for c in conges_reels(_tenant(p), p.annee, x.salarie_id):
        if _chevauche(x.date_debut, x.date_fin, c.date_depart, c.date_retour):
            raise ErreurPlanning(f"un congé existe déjà sur ces dates "
                                 f"({c.date_depart.strftime('%d/%m')} – {c.date_retour.strftime('%d/%m')})")
    from blueprints.tenant.conges import _solde_annuel
    t = _tenant(p)
    _solde_annuel(t, x.salarie, x.date_debut.year)        # garantit la ligne de solde, comme une demande
    c = Conge(tenant_id=p.tenant_id, salarie_id=x.salarie_id, annee=x.date_debut.year, jours_acquis=0,
              jours_pris=0, type_conge="ANNUEL", statut="DEMANDÉ", date_depart=x.date_debut,
              date_retour=x.date_fin, origine="PLANNING", date_demande=utcnow(),
              commentaire=f"Planning prévisionnel {p.annee}" + (f" — {x.commentaire}" if x.commentaire else ""))
    db.session.add(c)
    db.session.flush()
    x.conge_id = c.id
    db.session.flush()
    return c


def _tenant(p):
    from models import Tenant
    return db.session.get(Tenant, p.tenant_id)


# ── Planning ─────────────────────────────────────────────────────────────────
def en_attente(p):
    return [x for x in p.periodes if x.statut == "PROPOSEE"]


def valider(p, utilisateur, commentaire=None):
    if p.statut == "VALIDE":
        raise ErreurPlanning("le planning est déjà validé")
    n = len(en_attente(p))
    if n:
        raise ErreurPlanning(f"{n} proposition(s) de salarié en attente : retenez-les ou refusez-les d'abord")
    if not any(x.statut == "RETENUE" for x in p.periodes):
        raise ErreurPlanning("aucune période retenue dans le planning")
    p.statut, p.valide_par_id, p.date_validation = "VALIDE", utilisateur.id, utcnow()
    if commentaire is not None:
        p.commentaire = commentaire.strip()[:500] or None
    db.session.flush()


def rouvrir(p):
    p.statut, p.valide_par_id, p.date_validation = "BROUILLON", None, None
    db.session.flush()


def propositions_ouvertes(p, aujourd_hui=None):
    """Les salariés peuvent-ils encore proposer des dates sur leur portail ?"""
    aujourd_hui = aujourd_hui or date.today()
    return p is None or p.date_limite_propositions is None or aujourd_hui <= p.date_limite_propositions


# ── Contrôles et vue annuelle ────────────────────────────────────────────────
def salaries_actifs(t, site_id=None, categorie_id=None):
    q = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF")
    if categorie_id:
        q = q.filter(Salarie.categorie_id == categorie_id)
    sals = q.order_by(Salarie.nom, Salarie.prenom).all()
    if site_id:
        ids = {a.salarie_id for a in AffectationSite.query.filter_by(tenant_id=t.id, site_id=site_id, actif=True)
               if a.salarie_id}
        sals = [s for s in sals if s.id in ids]
    return sals


def sites_des_salaries(t):
    """{salarie_id: (site_id, nom)} : chantier d'affectation en cours."""
    noms = {s.id: s.nom for s in Site.query.filter_by(tenant_id=t.id).all()}
    out = {}
    for a in AffectationSite.query.filter_by(tenant_id=t.id, actif=True).filter(AffectationSite.salarie_id.isnot(None)):
        out[a.salarie_id] = (a.site_id, noms.get(a.site_id, "Chantier"))
    return out


def _absences(t, p, annee, avec_prevus=True):
    """{salarie_id: [(debut, fin, nature)]} pour l'année, bornées à l'année.
    nature : ACCORDE | DEMANDE | RETENUE | PROPOSEE."""
    d1, d2 = bornes(annee)
    out = {}
    for c in conges_reels(t, annee):
        nature = "ACCORDE" if (c.statut or "").upper() in STATUTS_REELS_ACCORDES else "DEMANDE"
        out.setdefault(c.salarie_id, []).append((max(c.date_depart, d1), min(c.date_retour, d2), nature, c))
    if avec_prevus and p is not None:
        for x in p.periodes:
            if x.statut == "REFUSEE" or x.conge_id:      # une période devenue demande est déjà comptée
                continue
            out.setdefault(x.salarie_id, []).append((max(x.date_debut, d1), min(x.date_fin, d2), x.statut, x))
    return out


def controles(t, p):
    """Soldes et absences simultanées pour le planning p."""
    from conges_avance import solde_annee
    annee = p.annee
    sals = salaries_actifs(t)
    # Année future : droits de l'année entière, calculés comme sur la page Congés (fin d'année)
    projete = annee > date.today().year
    fin_annee = date(annee, 12, 31) if projete else None
    abs_ = _absences(t, p, annee)
    par_salarie = []
    for s in sals:
        prevus = [x for x in p.periodes if x.salarie_id == s.id and x.statut != "REFUSEE" and not x.conge_id]
        jours_prevus = sum(x.jours for x in prevus)
        solde = solde_annee(t, s, annee, aujourd_hui=fin_annee)
        demandes = sum((c.date_retour - c.date_depart).days + 1 for c in conges_reels(t, annee, s.id)
                       if (c.statut or "").upper() in STATUTS_REELS_DEMANDES and c.annee == annee)
        disponible = round(float(solde["restants"]) - demandes, 1)
        par_salarie.append({"salarie": s, "jours_prevus": jours_prevus, "solde": solde, "demandes": demandes,
                            "projete": projete,
                            "disponible": disponible, "depasse": jours_prevus > disponible + 1e-9})
    # absences simultanées
    sites = sites_des_salaries(t)
    groupes = {"__entreprise__": ("Toute l'entreprise", {s.id for s in sals})}
    for s in sals:
        if s.id in sites:
            sid, nom = sites[s.id]
            groupes.setdefault(sid, (nom, set()))[1].add(s.id)
    seuil = p.seuil_absents_pct or 0
    alertes = []
    if seuil > 0:
        d1, d2 = bornes(annee)
        for cle, (nom, membres) in groupes.items():
            effectif = len(membres)
            if effectif < EFFECTIF_MIN_ALERTE:
                continue
            limite = effectif * seuil / 100.0
            periode = None
            j = d1
            while j <= d2:
                n = sum(1 for sid in membres if any(a <= j <= b for a, b, _, _ in abs_.get(sid, ())))
                if n > limite and n >= ABSENTS_MIN_ALERTE:
                    if periode and periode["fin"] == j - timedelta(days=1):
                        periode["fin"], periode["max"] = j, max(periode["max"], n)
                    else:
                        periode = {"groupe": nom, "debut": j, "fin": j, "max": n, "effectif": effectif}
                        alertes.append(periode)
                j += timedelta(days=1)
    return {"salaries": par_salarie, "alertes": alertes, "seuil": seuil, "projete": projete}


def vue_annuelle(t, annee, site_id=None, categorie_id=None, avec_prevus=True):
    """Lignes de la vue sur 12 mois : barres positionnées en % de l'année."""
    p = obtenir(t, annee)
    sals = salaries_actifs(t, site_id, categorie_id)
    abs_ = _absences(t, p, annee, avec_prevus)
    d1, d2 = bornes(annee)
    nb = (d2 - d1).days + 1
    lignes = []
    charge = [0] * nb
    for s in sals:
        barres = []
        for a, b, nature, obj in sorted(abs_.get(s.id, ()), key=lambda z: z[0]):
            barres.append({"gauche": round((a - d1).days / nb * 100, 3), "largeur": round(((b - a).days + 1) / nb * 100, 3),
                           "nature": nature, "debut": a, "fin": b, "jours": (b - a).days + 1,
                           "type": getattr(obj, "type_conge", None) or "ANNUEL"})
            for k in range((a - d1).days, (b - d1).days + 1):
                charge[k] += 1
        lignes.append({"salarie": s, "barres": barres,
                       "jours": sum(x["jours"] for x in barres if x["nature"] in ("ACCORDE", "DEMANDE", "RETENUE"))})
    mois = []
    for m in range(1, 13):
        debut = date(annee, m, 1)
        fin = date(annee + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        mois.append({"nom": MOIS_FR[m], "court": MOIS_COURTS[m], "gauche": round((debut - d1).days / nb * 100, 3),
                     "largeur": round(((fin - debut).days + 1) / nb * 100, 3),
                     "max": max(charge[(debut - d1).days:(fin - d1).days + 1] or [0])})
    aujourd_hui = date.today()
    return {"planning": p, "lignes": lignes, "mois": mois, "effectif": len(sals),
            "pic": max(charge) if charge else 0,
            "aujourd_hui": round((aujourd_hui - d1).days / nb * 100, 3) if d1 <= aujourd_hui <= d2 else None,
            "categories": CategorieEmploi.query.filter_by(tenant_id=t.id).order_by(CategorieEmploi.code).all(),
            "sites": Site.query.filter_by(tenant_id=t.id).order_by(Site.nom).all()}
