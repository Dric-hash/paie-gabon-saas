"""
notifications.py — Calcul centralisé des notifications et rappels

Rassemble les rappels qui demandent l'attention de l'utilisateur :
    - Contrats arrivant à échéance (dans les 30 jours)
    - Congés en attente de validation
    - Factures prestataires en retard de paiement
    - Périodes de paie à clôturer

Chaque notification a la forme :
    {
        "type":      "danger" | "warning" | "info",
        "categorie": "contrat" | "conge" | "facture" | "periode",
        "icone":     str,
        "titre":     str,
        "msg":       str,
        "lien":      str,
        "lien_texte": str,
    }

Utilisé par :
    - la cloche dans la barre du haut (compteur)
    - la page /notifications (liste détaillée)
    - le dashboard (peut réutiliser get_notifications)
"""
from datetime import date, datetime, timedelta

# Seuil d'alerte pour les contrats arrivant à échéance (en jours)
SEUIL_CONTRAT_ECHEANCE = 30


def get_notifications(tenant, db, models):
    """
    Calcule toutes les notifications pour un tenant.
    `models` est un dict des classes nécessaires pour éviter les imports circulaires.
    Retourne une liste triée par priorité (danger > warning > info).
    """
    Contrat            = models["Contrat"]
    Conge              = models["Conge"]
    Salarie            = models["Salarie"]
    PeriodePaie        = models["PeriodePaie"]
    FacturePrestataire = models.get("FacturePrestataire")
    Prestataire        = models.get("Prestataire")

    notifs = []
    today = date.today()
    tid = tenant.id

    # ── 1. Contrats arrivant à échéance ──────────────────────────────────────
    limite = today + timedelta(days=SEUIL_CONTRAT_ECHEANCE)
    contrats_echeance = (Contrat.query
        .filter_by(tenant_id=tid, actif=True)
        .filter(Contrat.date_fin.isnot(None))
        .filter(Contrat.date_fin >= today)
        .filter(Contrat.date_fin <= limite)
        .all())
    for c in contrats_echeance:
        jours = (c.date_fin - today).days
        sal = Salarie.query.get(c.salarie_id)
        nom = sal.nom_complet if sal else f"Salarié #{c.salarie_id}"
        urgent = jours <= 7
        notifs.append({
            "type": "danger" if urgent else "warning",
            "categorie": "contrat",
            "icone": "📄",
            "titre": f"Contrat de {nom} — échéance dans {jours} j",
            "msg": f"Le contrat se termine le {c.date_fin.strftime('%d/%m/%Y')}. "
                   f"Pensez à le renouveler ou à préparer la fin de contrat.",
            "lien": f"/salaries/{c.salarie_id}",
            "lien_texte": "Voir le salarié",
        })

    # Contrats déjà expirés mais toujours marqués actifs
    contrats_expires = (Contrat.query
        .filter_by(tenant_id=tid, actif=True)
        .filter(Contrat.date_fin.isnot(None))
        .filter(Contrat.date_fin < today)
        .all())
    for c in contrats_expires:
        sal = Salarie.query.get(c.salarie_id)
        nom = sal.nom_complet if sal else f"Salarié #{c.salarie_id}"
        notifs.append({
            "type": "danger",
            "categorie": "contrat",
            "icone": "⚠️",
            "titre": f"Contrat de {nom} expiré",
            "msg": f"Le contrat a expiré le {c.date_fin.strftime('%d/%m/%Y')} "
                   f"mais est toujours actif. Régularisez la situation.",
            "lien": f"/salaries/{c.salarie_id}",
            "lien_texte": "Régulariser",
        })

    # ── 2. Congés en attente de validation ───────────────────────────────────
    conges_attente = (Conge.query
        .filter_by(tenant_id=tid)
        .filter(Conge.statut.in_(["DEMANDÉ", "DEMANDE", "EN_ATTENTE"]))
        .all())
    if conges_attente:
        n = len(conges_attente)
        notifs.append({
            "type": "warning",
            "categorie": "conge",
            "icone": "🏖️",
            "titre": f"{n} demande(s) de congé à valider",
            "msg": f"{n} demande(s) de congé en attente de votre décision.",
            "lien": "/conges",
            "lien_texte": "Traiter les demandes",
        })

    # ── 3. Factures prestataires en retard ───────────────────────────────────
    if FacturePrestataire is not None:
        factures_retard = (FacturePrestataire.query
            .filter_by(tenant_id=tid)
            .filter(FacturePrestataire.statut.in_(["EN_ATTENTE", "PARTIELLE"]))
            .filter(FacturePrestataire.date_echeance.isnot(None))
            .filter(FacturePrestataire.date_echeance < today)
            .all())
        for f in factures_retard:
            jours_retard = (today - f.date_echeance).days
            presta = Prestataire.query.get(f.prestataire_id) if Prestataire else None
            nom = presta.raison_sociale if presta else f"Prestataire #{f.prestataire_id}"
            reste = float(f.montant_net_a_payer or 0) - float(f.montant_paye or 0)
            notifs.append({
                "type": "danger" if jours_retard > 30 else "warning",
                "categorie": "facture",
                "icone": "💰",
                "titre": f"Facture {f.numero} en retard ({jours_retard} j)",
                "msg": f"{nom} — échéance dépassée le {f.date_echeance.strftime('%d/%m/%Y')}. "
                       f"Reste à payer : {int(reste):,} FCFA.".replace(",", " "),
                "lien": f"/prestataires/{f.prestataire_id}",
                "lien_texte": "Voir la facture",
            })

    # ── 4. Périodes de paie à clôturer ───────────────────────────────────────
    # Une période d'un mois passé encore ouverte doit être clôturée
    periodes_ouvertes = (PeriodePaie.query
        .filter_by(tenant_id=tid, statut="OUVERT")
        .all())
    for p in periodes_ouvertes:
        # Période antérieure au mois courant ?
        est_passee = (p.annee < today.year) or (p.annee == today.year and p.mois < today.month)
        if est_passee:
            notifs.append({
                "type": "warning",
                "categorie": "periode",
                "icone": "📅",
                "titre": f"Période {p.libelle_mois} {p.annee} à clôturer",
                "msg": f"La période de paie {p.libelle_mois} {p.annee} est passée "
                       f"mais toujours ouverte. Clôturez-la pour figer les bulletins.",
                "lien": "/periodes",
                "lien_texte": "Clôturer",
            })

    # ── Tri par priorité ─────────────────────────────────────────────────────
    prio = {"danger": 0, "warning": 1, "info": 2}
    notifs.sort(key=lambda n: prio.get(n["type"], 3))
    return notifs


def compter_notifications(tenant, db, models):
    """Retourne (total, nb_critiques) pour la cloche."""
    notifs = get_notifications(tenant, db, models)
    total = len(notifs)
    critiques = sum(1 for n in notifs if n["type"] == "danger")
    return total, critiques
