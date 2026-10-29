"""suppression_salarie.py — Suppression d'une fiche salarié et de tout ce qui s'y rattache.

La suppression est réservée aux fiches créées par erreur. Elle est refusée quand elle
ferait disparaître un historique qui engage l'entreprise :
  - un bulletin validé ou payé (passer plutôt le salarié en INACTIF) ;
  - un prêt en cours, soldé ou déjà remboursé en partie (somme due ou remboursements) ;
  - un dossier RH : sanction, évaluation ou formation.

Sinon, toutes les données attachées au salarié sont supprimées, dans l'ordre imposé par
les clés étrangères. Un compte pointeur rattaché au salarié est conservé, détaché.

TABLES_TRAITEES liste chaque table qui référence `salaries` : un test vérifie qu'aucune
table n'est oubliée (un nouveau module doit l'y ajouter et dire quoi faire).
"""
from models import (db, Salarie, BulletinPaie, BulletinComposant, ConsultationBulletin, Contrat, EvenementCarriere,
                    HistoriqueSalarie, Pointage, PointeurChantier, Acompte, Conge, PrevisionConge, DocumentSalarie,
                    AffectationSite, AffectationMAD, PretPersonnel, RemboursementPret, AccesSalarie, DemandeDocument,
                    DemandeProfil, CompetenceSalarie, Evaluation, Formation, Sanction)

TABLES_TRAITEES = {
    "bulletins_paie", "consultations_bulletins", "contrats", "evenements_carriere", "historique_salarie",
    "pointages", "pointeurs_chantier", "acomptes", "conges", "previsions_conges", "documents_salarie",
    "affectations_sites", "affectations_mad", "prets_personnel", "acces_salaries", "demandes_documents",
    "demandes_profil", "competences_salarie", "evaluations", "formations", "sanctions",
}

STATUTS_BULLETIN_FIGES = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")


def _fcfa(v):
    return f"{float(v or 0):,.0f}".replace(",", " ")


def obstacles(s):
    """Raisons qui empêchent de supprimer la fiche (liste vide : suppression possible)."""
    raisons = []
    n = (BulletinPaie.query.filter_by(salarie_id=s.id)
         .filter(BulletinPaie.statut.in_(STATUTS_BULLETIN_FIGES)).count())
    if n:
        raisons.append(f"{n} bulletin(s) validé(s) ou payé(s)")
    for p in PretPersonnel.query.filter_by(tenant_id=s.tenant_id, salarie_id=s.id).all():
        a_rembourse = RemboursementPret.query.filter_by(pret_id=p.id).count() > 0
        if p.statut == "ANNULE" and not a_rembourse:
            continue
        if a_rembourse:
            raisons.append(f"un prêt de {_fcfa(p.montant_total)} F avec des remboursements enregistrés")
        else:
            raisons.append(f"un prêt {p.statut_libelle.lower() if hasattr(p, 'statut_libelle') else 'en cours'} "
                           f"de {_fcfa(p.montant_total)} F (supprimez-le ou annulez-le d'abord dans Prêts)")
    # Dossier RH (décision du 29/10/2026) : sanctions, évaluations et formations sont conservés
    for modele, libelle in ((Sanction, "sanction(s)"), (Evaluation, "évaluation(s)"), (Formation, "formation(s)")):
        n = modele.query.filter_by(tenant_id=s.tenant_id, salarie_id=s.id).count()
        if n:
            raisons.append(f"{n} {libelle} dans son dossier")
    return raisons


def supprimer(s):
    """Supprime la fiche et ses données. À n'appeler que si obstacles(s) est vide.
    Ne valide pas la transaction (l'appelant fait le commit)."""
    sid = s.id
    bulletins = [b.id for b in BulletinPaie.query.filter_by(salarie_id=sid).with_entities(BulletinPaie.id)]
    if bulletins:
        BulletinComposant.query.filter(BulletinComposant.bulletin_id.in_(bulletins)).delete(synchronize_session=False)
        ConsultationBulletin.query.filter(ConsultationBulletin.bulletin_id.in_(bulletins)).delete(synchronize_session=False)
        RemboursementPret.query.filter(RemboursementPret.bulletin_id.in_(bulletins)).update(
            {"bulletin_id": None}, synchronize_session=False)
    ConsultationBulletin.query.filter_by(salarie_id=sid).delete(synchronize_session=False)
    BulletinPaie.query.filter_by(salarie_id=sid).delete(synchronize_session=False)
    # prêts annulés sans remboursement (les autres bloquent la suppression)
    for p in PretPersonnel.query.filter_by(salarie_id=sid).all():
        db.session.delete(p)
    PrevisionConge.query.filter_by(salarie_id=sid).delete(synchronize_session=False)
    Conge.query.filter_by(salarie_id=sid).delete(synchronize_session=False)
    EvenementCarriere.query.filter_by(salarie_id=sid).delete(synchronize_session=False)
    HistoriqueSalarie.query.filter_by(salarie_id=sid).delete(synchronize_session=False)
    Contrat.query.filter_by(salarie_id=sid).delete(synchronize_session=False)
    Pointage.query.filter_by(salarie_id=sid).delete(synchronize_session=False)
    PointeurChantier.query.filter_by(salarie_id=sid).update({"salarie_id": None}, synchronize_session=False)
    for modele in (Acompte, DocumentSalarie, AffectationSite, AffectationMAD, AccesSalarie, DemandeDocument,
                   DemandeProfil, CompetenceSalarie, Evaluation, Formation, Sanction):
        modele.query.filter_by(salarie_id=sid).delete(synchronize_session=False)
    db.session.flush()
    db.session.delete(s)
    db.session.flush()
