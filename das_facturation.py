# -*- coding: utf-8 -*-
"""Facturation de la Déclaration Annuelle des Salaires (DAS).

Modèle : la préparation et l'aperçu (filigrané) sont GRATUITS ; l'édition finale
du document officiel est débloquée après paiement, par société et par année.

Tarifs :
  • Entreprise seule ...... 200 000 FCFA
  • Société gérée par un cabinet .. 100 000 FCFA / société
Réduction early-bird : -20 % si l'édition est débloquée avant la date limite
(par défaut le 31 mars de l'année suivant l'exercice).
"""
from datetime import date

DAS_PRIX_ENTREPRISE   = 200_000
DAS_PRIX_CABINET      = 100_000
DAS_EARLYBIRD_TAUX    = 0.20          # -20 %
DAS_EARLYBIRD_MOIS    = 3             # avant le 31 mars…
DAS_EARLYBIRD_JOUR    = 31            # … de l'année N+1 (exercice N)


def _est_gere_par_cabinet(tenant):
    return bool(getattr(tenant, "cabinet_id", None))


def date_limite_earlybird(annee):
    """Date limite early-bird pour l'exercice `annee` : 31/03 de l'année suivante."""
    return date(annee + 1, DAS_EARLYBIRD_MOIS, DAS_EARLYBIRD_JOUR)


def est_periode_earlybird(annee, aujourd_hui=None):
    aujourd_hui = aujourd_hui or date.today()
    return aujourd_hui <= date_limite_earlybird(annee)


def das_prix(tenant, annee, aujourd_hui=None):
    """Renvoie le détail tarifaire pour l'édition de la DAS d'une société/année."""
    cabinet = _est_gere_par_cabinet(tenant)
    base = DAS_PRIX_CABINET if cabinet else DAS_PRIX_ENTREPRISE
    early = est_periode_earlybird(annee, aujourd_hui)
    reduction = round(base * DAS_EARLYBIRD_TAUX) if early else 0
    prix = base - reduction
    return {
        "tarif_type":     "CABINET" if cabinet else "ENTREPRISE",
        "prix_base":      base,
        "reduction":      reduction,
        "prix":           prix,
        "est_earlybird":  early,
        "date_limite":    date_limite_earlybird(annee),
        "taux_reduction": int(DAS_EARLYBIRD_TAUX * 100),
    }


def das_edition(tenant_id, annee):
    """Renvoie l'enregistrement EditionDAS (ou None) pour une société/année."""
    from models import EditionDAS
    return EditionDAS.query.filter_by(tenant_id=tenant_id, annee=annee).first()


def das_est_payee(tenant_id, annee):
    e = das_edition(tenant_id, annee)
    return bool(e and e.est_payee)


def marquer_das_payee(tenant, annee, montant, tarif_type,
                      paiement_id=None, debloque_par=None):
    """Marque la DAS d'une société/année comme payée (crée l'enregistrement au besoin)."""
    from models import db, EditionDAS, utcnow
    e = das_edition(tenant.id, annee)
    if e is None:
        e = EditionDAS(tenant_id=tenant.id, annee=annee)
        db.session.add(e)
    e.statut        = "PAYEE"
    e.montant       = montant
    e.tarif_type    = tarif_type
    e.paiement_id   = paiement_id
    e.debloque_par  = debloque_par
    e.date_paiement = utcnow()
    db.session.commit()
    return e
