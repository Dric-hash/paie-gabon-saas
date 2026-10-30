"""
rappels_salaire.py — Rappels de salaire (augmentation ou reclassement à effet rétroactif)
=======================================================================================
Principe : les bulletins déjà validés ne sont JAMAIS modifiés. Pour chaque mois rappelé,
on part du bulletin validé de ce mois et on réévalue les rubriques qui découlent du
salaire de base, au rapport « nouveau salaire / ancien salaire » :

  - salaire de base (y compris un salaire de base proratisé : entrée, sortie, absence) ;
  - heures supplémentaires et jours fériés travaillés (montants = heures × taux horaire,
    et le taux horaire du moteur est proportionnel au salaire de base : base / 173,33) ;
  - absences (retenue proportionnelle au salaire) ;
  - prime d'ancienneté (le moteur la calcule en % du salaire de base) ;
  - en option : prime de nuit et prime d'assiduité (proportionnelles au salaire de base
    dans certaines conventions — à cocher seulement si c'est le cas dans l'entreprise).

Écart du mois = Σ (montant versé × rapport − montant versé), arrondi au franc par rubrique,
plus un ajustement manuel éventuel (justifié). Le total est versé sur le bulletin de la
période d'imputation comme un gain soumis à la CNSS, à la CNAMGS et à l'IRPP du mois de
versement (règle confirmée par le client le 30/10/2026).

Contrat : à l'enregistrement du rappel, un avenant au nouveau salaire est créé à compter du
mois d'effet (comme une promotion de la gestion de carrière : le contrat actif est désactivé,
ses autres conditions sont reprises) et la frise de carrière reçoit un événement
« Augmentation de salaire ». Si le contrat actif est déjà au nouveau salaire, rien n'est
créé. L'annulation du rappel supprime l'avenant et réactive le contrat précédent, sauf si le
contrat a changé depuis ou si un bulletin validé a déjà été calculé au nouveau salaire.
Un brouillon d'un mois ≥ effet resté à l'ancien salaire (ou au nouveau après rétablissement)
doit être ré-enregistré avant validation.
"""
import json
from collections import OrderedDict
from datetime import date

from models import (db, BulletinPaie, PeriodePaie, BulletinComposant, RappelSalaire, LigneRappel, Contrat,
                    EvenementCarriere)

RUBRIQUES = OrderedDict([
    ("salaire_base", ("Salaire de base", ("salaire_base",), True)),
    ("heures_sup", ("Heures supplémentaires et jours fériés",
                    ("heures_sup_10", "heures_sup_30", "heures_sup_30b", "heures_sup_40", "heures_sup_70",
                     "heures_sup_fj", "heures_sup_fn"), True)),
    ("absences", ("Absences (retenue)", ("absences",), True)),
    ("prime_anciennete", ("Prime d'ancienneté", ("prime_anciennete",), True)),
    ("prime_nuit", ("Prime de nuit", ("prime_nuit",), False)),
    ("prime_assiduite", ("Prime d'assiduité", ("prime_assiduité",), False)),
])
OBLIGATOIRES = {"salaire_base"}
STATUTS_VALIDES = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")
STATUTS = {"A_VERSER": ("À verser", "#92400e", "#fef3c7"), "VERSE": ("Versé", "#065f46", "#d1fae5"),
           "ANNULE": ("Annulé", "#b91c1c", "#fee2e2")}


def _f(v):
    return float(v or 0)


def _franc(x):
    """Arrondi au franc, demi-unité vers le haut (en valeur absolue)."""
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rubriques_par_defaut():
    return [k for k, (_, _, d) in RUBRIQUES.items() if d]


def bulletins_rappeles(tenant_id, salarie_id, date_effet, periode_versement):
    """Bulletins validés/payés du salarié, des mois ≥ date d'effet et < période de versement.
    Renvoie (bulletins triés, brouillons ignorés)."""
    q = (BulletinPaie.query.join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
         .filter(BulletinPaie.tenant_id == tenant_id, BulletinPaie.salarie_id == salarie_id)
         .order_by(PeriodePaie.annee, PeriodePaie.mois))
    valides, brouillons = [], []
    debut = (date_effet.year, date_effet.month)
    fin = (periode_versement.annee, periode_versement.mois)
    for b in q.all():
        cle = (b.periode.annee, b.periode.mois)
        if not (debut <= cle < fin):
            continue
        (valides if b.statut in STATUTS_VALIDES else brouillons).append(b)
    return valides, brouillons


def calculer_ligne(bulletin, ancien, nouveau, rubriques):
    """(détail {code: [versé, dû, écart]}, écart total) pour un bulletin validé."""
    rapport = _f(nouveau) / _f(ancien)
    detail, total = OrderedDict(), 0.0
    for code in rubriques:
        if code not in RUBRIQUES:
            continue
        verse = sum(_f(getattr(bulletin, champ, 0)) for champ in RUBRIQUES[code][1])
        if verse == 0:
            continue
        signe = -1 if code == "absences" else 1           # une absence vient en déduction du brut
        du = _franc(verse * rapport)
        ecart = signe * (du - verse)
        detail[code] = [verse, du, ecart]
        total += ecart
    return detail, _franc(total)


def preparer(tenant_id, salarie_id, date_effet, periode_versement, ancien, nouveau, rubriques):
    """Lignes calculées (sans enregistrement) : [(bulletin, détail, écart)], brouillons ignorés."""
    valides, brouillons = bulletins_rappeles(tenant_id, salarie_id, date_effet, periode_versement)
    lignes = [(b, *calculer_ligne(b, ancien, nouveau, rubriques)) for b in valides]
    return lignes, brouillons


def statut_effectif(r):
    """VERSE si le bulletin de la période de versement est validé et contient le rappel."""
    if r.statut == "ANNULE":
        return "ANNULE"
    b = BulletinPaie.query.filter_by(tenant_id=r.tenant_id, salarie_id=r.salarie_id, periode_id=r.periode_id).first()
    if b is not None and b.statut in STATUTS_VALIDES and BulletinComposant.query.filter_by(
            bulletin_id=b.id, rappel_id=r.id).first():
        return "VERSE"
    return "A_VERSER"


def synchroniser_statuts(tenant_id, salarie_id=None, periode_id=None):
    q = RappelSalaire.query.filter(RappelSalaire.tenant_id == tenant_id, RappelSalaire.statut != "ANNULE")
    if salarie_id:
        q = q.filter_by(salarie_id=salarie_id)
    if periode_id:
        q = q.filter_by(periode_id=periode_id)
    for r in q.all():
        r.statut = statut_effectif(r)


def rappels_a_verser(tenant_id, salarie_id, periode_id):
    return (RappelSalaire.query.filter(RappelSalaire.tenant_id == tenant_id, RappelSalaire.salarie_id == salarie_id,
                                       RappelSalaire.periode_id == periode_id, RappelSalaire.statut != "ANNULE")
            .order_by(RappelSalaire.id).all())


def libelle_composant(r):
    mois = [l.libelle_mois for l in r.lignes if l.libelle_mois]
    plage = f" {mois[0]} à {mois[-1]}" if len(mois) > 1 else (f" {mois[0]}" if mois else "")
    return f"Rappel de salaire{plage}"[:120]


def composants(tenant_id, salarie_id, periode_id):
    """Composants (format calculer_bulletin) des rappels à verser sur ce bulletin."""
    res = []
    for r in rappels_a_verser(tenant_id, salarie_id, periode_id):
        total = r.total
        if total == 0:
            continue
        res.append({"rappel_id": r.id, "composant_id": None, "libelle": libelle_composant(r),
                    "sens": "GAIN" if total > 0 else "RETENUE", "montant": abs(total),
                    "soumis_cnss": True, "soumis_cnamgs": True, "soumis_irpp": True,
                    "entre_dans_brut": True, "position": "HAUT", "base": None, "taux": None})
    return res


def manquants(bulletin):
    """Rappels à verser sur ce bulletin mais absents de ses composants (bulletin à ré-enregistrer)."""
    presents = {c.rappel_id for c in BulletinComposant.query.filter_by(bulletin_id=bulletin.id).all() if c.rappel_id}
    return [r for r in rappels_a_verser(bulletin.tenant_id, bulletin.salarie_id, bulletin.periode_id)
            if r.total != 0 and r.id not in presents]


def annules_inclus(bulletin):
    """Rappels annulés mais encore présents sur le bulletin (brouillon à ré-enregistrer)."""
    ids = {c.rappel_id for c in BulletinComposant.query.filter_by(bulletin_id=bulletin.id).all() if c.rappel_id}
    if not ids:
        return []
    return RappelSalaire.query.filter(RappelSalaire.id.in_(ids), RappelSalaire.statut == "ANNULE").all()


def base_perimee(bulletin):
    """Rappel dont le salaire de base contredit ce bulletin non validé d'un mois ≥ effet :
    encore à l'ancien salaire alors que le rappel est actif, ou au nouveau salaire alors que
    le rappel est annulé et le contrat rétabli. None sinon."""
    if bulletin.statut in STATUTS_VALIDES or bulletin.periode is None:
        return None
    cle = (bulletin.periode.annee, bulletin.periode.mois)
    base = _f(bulletin.salaire_base)
    for r in (RappelSalaire.query.filter_by(tenant_id=bulletin.tenant_id, salarie_id=bulletin.salarie_id)
              .order_by(RappelSalaire.id.desc()).all()):
        if (r.date_effet.year, r.date_effet.month) > cle:
            continue
        if r.statut != "ANNULE":
            return r if abs(base - _f(r.ancien_salaire)) < 0.5 else None
        if r.contrat_maj == "RETABLI":
            return r if abs(base - _f(r.nouveau_salaire)) < 0.5 else None
    return None


def a_reenregistrer(bulletin):
    """True si le bulletin doit être ré-enregistré avant validation (rappel manquant ou annulé
    inclus, salaire de base pas aligné sur le rappel)."""
    return bool(manquants(bulletin) or annules_inclus(bulletin) or base_perimee(bulletin))


def _fcfa(x):
    return f"{_f(x):,.0f}".replace(",", " ")


def contrat_actif(tenant_id, salarie_id):
    return (Contrat.query.filter_by(tenant_id=tenant_id, salarie_id=salarie_id, actif=True)
            .order_by(Contrat.date_debut.desc(), Contrat.id.desc()).first())


def appliquer_au_contrat(r, user_id=None):
    """Avenant au nouveau salaire à compter du mois d'effet (sauf contrat déjà à jour).
    Renvoie l'avenant créé ou None."""
    actif = contrat_actif(r.tenant_id, r.salarie_id)
    if actif is not None and abs(_f(actif.salaire_base) - _f(r.nouveau_salaire)) < 0.5:
        r.contrat_maj = "DEJA_A_JOUR"
        return None
    Contrat.query.filter_by(tenant_id=r.tenant_id, salarie_id=r.salarie_id, actif=True).update(
        {"actif": False}, synchronize_session=False)
    c = Contrat(tenant_id=r.tenant_id, salarie_id=r.salarie_id,
                type_contrat=(actif.type_contrat if actif else "CDI"), date_debut=r.date_effet,
                date_fin=(actif.date_fin if actif else None),
                date_fin_essai=(actif.date_fin_essai if actif else None),
                salaire_base=r.nouveau_salaire,
                poste=(actif.poste if actif else r.salarie.emploi),
                categorie_id=(actif.categorie_id if actif else r.salarie.categorie_id),
                elements_recurrents=(actif.elements_recurrents if actif else None), actif=True)
    db.session.add(c)
    db.session.flush()
    db.session.add(EvenementCarriere(
        tenant_id=r.tenant_id, salarie_id=r.salarie_id, type="AUGMENTATION", date_effet=r.date_effet,
        titre="Augmentation de salaire (rappel)", details=r.motif,
        ancienne_valeur=f"{_fcfa(actif.salaire_base if actif else r.ancien_salaire)} FCFA",
        nouvelle_valeur=f"{_fcfa(r.nouveau_salaire)} FCFA", contrat_id=c.id,
        cree_par=user_id, cree_par_nom=r.auteur))
    r.contrat_id, r.contrat_precedent_id, r.contrat_maj = c.id, (actif.id if actif else None), "AVENANT"
    db.session.flush()
    return c


def retablir_contrat(r):
    """À l'annulation : supprime l'avenant créé par le rappel et réactive le contrat précédent.
    Renvoie RETABLI, CONSERVE (contrat modifié depuis, ou bulletin validé au nouveau salaire)
    ou None (le rappel n'avait pas créé d'avenant)."""
    if r.contrat_maj != "AVENANT" or not r.contrat_id:
        return None
    c = db.session.get(Contrat, r.contrat_id)
    actif = contrat_actif(r.tenant_id, r.salarie_id)
    deja_paye = (BulletinPaie.query.join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
                 .filter(BulletinPaie.tenant_id == r.tenant_id, BulletinPaie.salarie_id == r.salarie_id,
                         BulletinPaie.statut.in_(STATUTS_VALIDES), BulletinPaie.salaire_base == r.nouveau_salaire,
                         (PeriodePaie.annee * 100 + PeriodePaie.mois) >= r.date_effet.year * 100 + r.date_effet.month)
                 .first())
    if (c is None or actif is None or actif.id != c.id or abs(_f(c.salaire_base) - _f(r.nouveau_salaire)) >= 0.5
            or deja_paye is not None):
        r.contrat_maj = "CONSERVE"
        return "CONSERVE"
    EvenementCarriere.query.filter_by(tenant_id=r.tenant_id, contrat_id=c.id).delete(synchronize_session=False)
    r.contrat_id = None
    db.session.flush()
    db.session.delete(c)
    if r.contrat_precedent_id:
        prec = db.session.get(Contrat, r.contrat_precedent_id)
        if prec is not None:
            prec.actif = True
    r.contrat_maj = "RETABLI"
    db.session.flush()
    return "RETABLI"


def enregistrer(tenant_id, salarie, periode_versement, date_effet, ancien, nouveau, rubriques, motif, auteur,
                ajustements=None, user_id=None):
    """Crée le rappel, ses lignes et l'avenant au contrat. ajustements : {bulletin_id: montant}."""
    lignes, _ = preparer(tenant_id, salarie.id, date_effet, periode_versement, ancien, nouveau, rubriques)
    r = RappelSalaire(tenant_id=tenant_id, salarie_id=salarie.id, periode_id=periode_versement.id, motif=motif,
                      date_effet=date_effet, ancien_salaire=ancien, nouveau_salaire=nouveau,
                      rubriques=",".join(rubriques), auteur=auteur, statut="A_VERSER")
    db.session.add(r)
    db.session.flush()
    ajustements = ajustements or {}
    for b, detail, ecart in lignes:
        aj = _franc(ajustements.get(b.id, 0) or 0)
        db.session.add(LigneRappel(tenant_id=tenant_id, rappel_id=r.id, periode_id=b.periode_id, bulletin_id=b.id,
                                   libelle_mois=f"{b.periode.libelle_mois.capitalize()} {b.periode.annee}",
                                   detail=json.dumps(detail), ecart_calcule=ecart, ajustement=aj,
                                   montant=_franc(ecart + aj)))
    db.session.flush()
    appliquer_au_contrat(r, user_id)
    return r


def detail_ligne(l):
    try:
        return json.loads(l.detail or "{}")
    except ValueError:
        return {}
