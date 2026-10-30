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
versement (traitement à valider par le gestionnaire de paie).
"""
import json
from collections import OrderedDict
from datetime import date

from models import db, BulletinPaie, PeriodePaie, BulletinComposant, RappelSalaire, LigneRappel

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


def a_reenregistrer(bulletin):
    """True si le bulletin doit être ré-enregistré avant validation (rappel manquant ou annulé inclus)."""
    return bool(manquants(bulletin) or annules_inclus(bulletin))


def enregistrer(tenant_id, salarie, periode_versement, date_effet, ancien, nouveau, rubriques, motif, auteur,
                ajustements=None):
    """Crée le rappel et ses lignes. ajustements : {bulletin_id: (montant, )}."""
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
    return r


def detail_ligne(l):
    try:
        return json.loads(l.detail or "{}")
    except ValueError:
        return {}
