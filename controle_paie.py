"""
controle_paie.py — Contrôle de la paie d'une période avant validation
=====================================================================
Repère ce qui mérite une vérification avant de valider les bulletins et de payer :

  BLOQUANT (la validation est refusée)
    - net à payer négatif ;
    - deux bulletins pour le même salarié sur la période ;
    - rappel de salaire à verser mais absent du bulletin (brouillon à ré-enregistrer).
    - salaire de base d'un brouillon pas aligné sur un rappel (ancien salaire après avenant,
      ou nouveau salaire après annulation et rétablissement du contrat).
  ATTENTION (à vérifier, la validation reste possible)
    - salarié actif sans bulletin ;
    - net à payer nul ;
    - net qui varie de plus de X % par rapport au mois précédent (X réglable, 20 % par défaut) ;
    - salaire de base du contrat inférieur au minimum mensuel de référence (réglable ;
      à défaut la valeur SMIG_GABON du moteur de paie, elle-même marquée « à confirmer ») ;
    - heures supplémentaires plus de 2 fois supérieures à la moyenne des 3 derniers mois ;
    - salarié sorti avant la période mais payé ;
    - paiement par virement sans RIB.
  INFORMATION
    - entrées et sorties du mois (prorata, solde de tout compte à vérifier).

Ces seuils sont des repères de contrôle interne, pas des règles légales : l'entreprise
les ajuste dans ses paramètres.
"""
from calendar import monthrange
from datetime import date

from models import db, BulletinPaie, PeriodePaie, Salarie, Contrat

SEUIL_VARIATION_DEFAUT = 20          # %
FACTEUR_HEURES_SUP = 2               # × la moyenne des 3 mois précédents
STATUTS_VALIDES = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")
CHAMPS_HS = ("heures_sup_10", "heures_sup_30", "heures_sup_30b", "heures_sup_40", "heures_sup_70",
             "heures_sup_fj", "heures_sup_fn")
NIVEAUX = {"bloquant": ("Bloquant", "#b91c1c", "#fee2e2"),
           "attention": ("À vérifier", "#92400e", "#fef3c7"),
           "info": ("Information", "#1e40af", "#dbeafe")}


def _f(v):
    return float(v or 0)


def _fcfa(v):
    return f"{_f(v):,.0f}".replace(",", " ") + " FCFA"


def montant_heures_sup(b):
    return sum(_f(getattr(b, c, 0)) for c in CHAMPS_HS)


def salaire_minimum(tenant):
    """(montant, source) du minimum mensuel de référence utilisé par le contrôle."""
    if tenant is not None and tenant.controle_salaire_minimum:
        return _f(tenant.controle_salaire_minimum), "paramètre de l'entreprise"
    from calculs_paie import SMIG_GABON
    return float(SMIG_GABON), "valeur du moteur de paie (à confirmer)"


def seuil_variation(tenant):
    if tenant is not None and tenant.controle_seuil_variation is not None:
        return _f(tenant.controle_seuil_variation)
    return SEUIL_VARIATION_DEFAUT


def _periode_precedente(tenant_id, p):
    a, m = (p.annee, p.mois - 1) if p.mois > 1 else (p.annee - 1, 12)
    return PeriodePaie.query.filter_by(tenant_id=tenant_id, annee=a, mois=m).first()


def _periodes_avant(tenant_id, p, n):
    res, a, m = [], p.annee, p.mois
    for _ in range(n):
        a, m = (a, m - 1) if m > 1 else (a - 1, 12)
        x = PeriodePaie.query.filter_by(tenant_id=tenant_id, annee=a, mois=m).first()
        if x:
            res.append(x)
    return res


def _anomalie(niveau, code, titre, detail, salarie=None, bulletin=None):
    return {"niveau": niveau, "code": code, "titre": titre, "detail": detail, "salarie": salarie, "bulletin": bulletin}


def controler(tenant, periode):
    """Liste des anomalies de la période (triées : bloquant, attention, info, puis nom)."""
    tid = tenant.id
    debut = date(periode.annee, periode.mois, 1)
    fin = date(periode.annee, periode.mois, monthrange(periode.annee, periode.mois)[1])
    bulletins = BulletinPaie.query.filter_by(tenant_id=tid, periode_id=periode.id).all()
    par_salarie = {}
    for b in bulletins:
        par_salarie.setdefault(b.salarie_id, []).append(b)
    res = []

    # Bulletins précédents (variation du net, moyenne des heures sup)
    prec = _periode_precedente(tid, periode)
    nets_prec = {}
    if prec:
        for b in BulletinPaie.query.filter(BulletinPaie.tenant_id == tid, BulletinPaie.periode_id == prec.id,
                                           BulletinPaie.statut.in_(STATUTS_VALIDES)).all():
            nets_prec[b.salarie_id] = _f(b.net_a_payer)
    hs_hist = {}
    avant = _periodes_avant(tid, periode, 3)
    if avant:
        for b in BulletinPaie.query.filter(BulletinPaie.tenant_id == tid,
                                           BulletinPaie.periode_id.in_([x.id for x in avant]),
                                           BulletinPaie.statut.in_(STATUTS_VALIDES)).all():
            hs_hist.setdefault(b.salarie_id, []).append(montant_heures_sup(b))
    contrats = {}
    for c in (Contrat.query.filter_by(tenant_id=tid, actif=True).order_by(Contrat.date_debut).all()):
        contrats[c.salarie_id] = c          # le plus récent l'emporte
    minimum, source_min = salaire_minimum(tenant)
    seuil = seuil_variation(tenant)

    salaries = Salarie.query.filter_by(tenant_id=tid).all()
    for s in salaries:
        buls = par_salarie.get(s.id, [])
        embauche, sortie = s.date_embauche, s.date_cessation
        present = (embauche is None or embauche <= fin) and (sortie is None or sortie >= debut)
        if not buls:
            if present and (s.statut or "ACTIF") == "ACTIF":
                res.append(_anomalie("attention", "SANS_BULLETIN", "Salarié actif sans bulletin",
                                     "Aucun bulletin pour la période : oubli ou absence à justifier.", s))
            continue
        if len(buls) > 1:
            res.append(_anomalie("bloquant", "DOUBLON", "Plusieurs bulletins sur la période",
                                 f"{len(buls)} bulletins pour ce salarié : supprimez le doublon.", s, buls[0]))
        b = buls[0]
        net = _f(b.net_a_payer)
        if net < 0:
            res.append(_anomalie("bloquant", "NET_NEGATIF", "Net à payer négatif",
                                 f"Net de {_fcfa(net)} : retenues (acompte, prêt…) supérieures au salaire.", s, b))
        elif net == 0:
            res.append(_anomalie("attention", "NET_NUL", "Net à payer nul",
                                 "Vérifiez les absences et les retenues du mois.", s, b))
        if s.id in nets_prec and nets_prec[s.id] > 0 and net >= 0:
            ecart = (net - nets_prec[s.id]) / nets_prec[s.id] * 100
            if abs(ecart) > seuil:
                res.append(_anomalie("attention", "VARIATION", f"Net en {'hausse' if ecart > 0 else 'baisse'} de "
                                     f"{abs(ecart):.0f} %",
                                     f"{_fcfa(nets_prec[s.id])} le mois précédent, {_fcfa(net)} ce mois-ci "
                                     f"(seuil {seuil:g} %).", s, b))
        c = contrats.get(s.id)
        if c is not None and _f(c.salaire_base) > 0 and _f(c.salaire_base) < minimum:
            res.append(_anomalie("attention", "SOUS_MINIMUM", "Salaire de base sous le minimum de référence",
                                 f"Contrat à {_fcfa(c.salaire_base)} pour un minimum mensuel de {_fcfa(minimum)} "
                                 f"({source_min}). Normal pour un temps partiel ; sinon à corriger.", s, b))
        hs = montant_heures_sup(b)
        hist = hs_hist.get(s.id) or []
        if hs > 0 and hist:
            moyenne = sum(hist) / len(hist)
            if moyenne > 0 and hs > FACTEUR_HEURES_SUP * moyenne:
                res.append(_anomalie("attention", "HEURES_SUP", "Heures supplémentaires inhabituelles",
                                     f"{_fcfa(hs)} ce mois-ci pour une moyenne de {_fcfa(moyenne)} sur les "
                                     f"{len(hist)} mois précédents.", s, b))
        if sortie and sortie < debut:
            res.append(_anomalie("attention", "SORTI_PAYE", "Salarié sorti avant la période",
                                 f"Sorti le {sortie:%d/%m/%Y} : ce bulletin est-il voulu (solde, rappel) ?", s, b))
        if b.statut not in STATUTS_VALIDES:
            import rappels_salaire
            for r in rappels_salaire.manquants(b):
                res.append(_anomalie("bloquant", "RAPPEL_ABSENT", "Rappel de salaire non inclus",
                                     f"{rappels_salaire.libelle_composant(r)} ({_fcfa(r.total)}) doit être versé sur ce "
                                     f"bulletin : ouvrez-le en modification et enregistrez-le.", s, b))
            for r in rappels_salaire.annules_inclus(b):
                res.append(_anomalie("bloquant", "RAPPEL_ANNULE", "Rappel annulé encore sur le bulletin",
                                     f"{rappels_salaire.libelle_composant(r)} a été annulé : ré-enregistrez le bulletin "
                                     f"pour le retirer.", s, b))
            r = rappels_salaire.base_perimee(b)
            if r is not None:
                res.append(_anomalie("bloquant", "RAPPEL_BASE", "Salaire de base non aligné sur le rappel",
                                     f"Salaire de base {_fcfa(b.salaire_base)} alors que le contrat a été "
                                     f"{'rétabli à ' + _fcfa(r.ancien_salaire) if r.statut == 'ANNULE' else 'porté à ' + _fcfa(r.nouveau_salaire)} "
                                     f"(rappel de salaire) : ouvrez le bulletin en modification et enregistrez-le.", s, b))
        if (s.mode_paiement or "ESPECES") == "VIREMENT" and not (s.rib or "").strip():
            res.append(_anomalie("attention", "SANS_RIB", "Virement sans RIB",
                                 "Mode de paiement « virement » mais aucun RIB sur la fiche du salarié.", s, b))
        if embauche and debut <= embauche <= fin:
            res.append(_anomalie("info", "ENTREE", f"Entrée le {embauche:%d/%m/%Y}",
                                 "Vérifiez le prorata du premier mois.", s, b))
        if sortie and debut <= sortie <= fin:
            res.append(_anomalie("info", "SORTIE", f"Sortie le {sortie:%d/%m/%Y}",
                                 "Vérifiez le solde de tout compte (congés, préavis, indemnités).", s, b))
    ordre = {"bloquant": 0, "attention": 1, "info": 2}
    res.sort(key=lambda a: (ordre[a["niveau"]], (a["salarie"].nom or "") if a["salarie"] else ""))
    return res


def resume(anomalies):
    r = {"bloquant": 0, "attention": 0, "info": 0}
    for a in anomalies:
        r[a["niveau"]] += 1
    return r


def bulletins_bloques(anomalies):
    """Ids des bulletins dont la validation doit être refusée."""
    return {a["bulletin"].id for a in anomalies if a["niveau"] == "bloquant" and a["bulletin"] is not None
            and a["code"] == "NET_NEGATIF"}
