"""Suppression d'un salarié : refusée quand elle effacerait un historique qui engage
l'entreprise (bulletin validé, prêt), sinon complète — y compris les données des modules
récents (prêts annulés, carrière, formations, portail…). Bug du 29/10/2026 : un prêt
bloquait la suppression avec une erreur PostgreSQL (NotNullViolation)."""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="paiegabon_suppr_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = os.environ.get("PG_URL") or "sqlite:///" + _DB_PATH
os.environ.setdefault("SECRET_KEY", "test-secret-suppr")

import pytest  # noqa: E402

from app import app as flask_app  # noqa: E402
import models as M  # noqa: E402
from models import db  # noqa: E402
import suppression_salarie as ss  # noqa: E402

D = date(2026, 10, 10)


@pytest.fixture
def env():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SERVER_NAME="localhost")
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        pl = M.Plan(code="PRO", nom="Pro", prix_mensuel=1, max_salaries=99, max_utilisateurs=9, actif=True)
        db.session.add(pl)
        db.session.flush()
        t = M.Tenant(slug="a", denomination="ACME", pays="Gabon", plan_id=pl.id, statut="ACTIF", token_api="t")
        db.session.add(t)
        db.session.flush()
        admin = M.Utilisateur(nom="ADMIN", prenom="A", email="a@a.ga", role="TENANT_ADMIN", tenant_id=t.id, actif=True,
                              email_verifie=True)
        admin.set_password("x")
        sals = [M.Salarie(tenant_id=t.id, matricule=f"M{i}", nom=f"NOM{i}", prenom="P", date_embauche=D)
                for i in range(2)]
        db.session.add_all([admin] + sals)
        db.session.commit()
        yield {"t": t.id, "admin": admin.id, "s": sals[0].id, "autre": sals[1].id}
        db.session.remove()


def _cli(uid):
    cli = flask_app.test_client()
    with cli.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return cli


def _pret(env, statut="EN_COURS", rembourse=False):
    p = M.PretPersonnel(tenant_id=env["t"], salarie_id=env["s"], montant_total=120000, date_octroi=D, nb_echeances=5,
                        motif="PRISE DE LOGEMENT", statut=statut)
    db.session.add(p)
    db.session.flush()
    if rembourse:
        db.session.add(M.RemboursementPret(tenant_id=env["t"], pret_id=p.id, montant=24000))
    db.session.commit()
    return p.id


def _flash(cli):
    with cli.session_transaction() as s:
        return " ".join(m for _, m in s.pop("_flashes", []))


def test_toutes_les_tables_liees_sont_traitees():
    """Toute table qui référence un salarié doit être prise en charge par la suppression."""
    liees = {t.name for t in db.metadata.sorted_tables for fk in t.foreign_keys if fk.column.table.name == "salaries"}
    assert liees - ss.TABLES_TRAITEES == set(), "table oubliée par suppression_salarie"


def test_pret_en_cours_bloque_puis_suppression(env):
    """Le cas signalé : un prêt en cours empêchait la suppression (erreur NotNullViolation)."""
    cli = _cli(env["admin"])
    pid = _pret(env)
    cli.post(f"/salaries/{env['s']}/supprimer")
    m = _flash(cli)
    assert "Impossible de supprimer" in m and "prêt en cours de 120 000 F" in m and "INACTIF" in m
    assert db.session.get(M.Salarie, env["s"]) is not None
    # prêt annulé sans remboursement : il part avec la fiche
    db.session.get(M.PretPersonnel, pid).statut = "ANNULE"
    db.session.commit()
    cli.post(f"/salaries/{env['s']}/supprimer")
    assert "supprimé" in _flash(cli)
    db.session.expire_all()
    assert db.session.get(M.Salarie, env["s"]) is None and db.session.get(M.PretPersonnel, pid) is None


def test_pret_rembourse_bloque_meme_annule(env):
    _pret(env, statut="ANNULE", rembourse=True)
    cli = _cli(env["admin"])
    cli.post(f"/salaries/{env['s']}/supprimer")
    assert "remboursements enregistrés" in _flash(cli)
    assert db.session.get(M.Salarie, env["s"]) is not None


def test_bulletin_valide_bloque(env):
    per = M.PeriodePaie(tenant_id=env["t"], annee=2026, mois=10, libelle_mois="OCTOBRE")
    db.session.add(per)
    db.session.flush()
    db.session.add(M.BulletinPaie(tenant_id=env["t"], salarie_id=env["s"], periode_id=per.id, salaire_base=1,
                                  salaire_brut=1, net_a_payer=1, statut="VALIDÉ"))
    db.session.commit()
    cli = _cli(env["admin"])
    cli.post(f"/salaries/{env['s']}/supprimer")
    assert "1 bulletin(s) validé(s) ou payé(s)" in _flash(cli)


def test_suppression_complete_sans_toucher_aux_autres(env):
    t, sid, autre = env["t"], env["s"], env["autre"]
    site = M.Site(tenant_id=t, nom="Owendo")
    client = M.ClientUtilisateur(tenant_id=t, nom="CLIENT")
    comp = M.Competence(tenant_id=t, libelle="Soudure")
    per = M.PeriodePaie(tenant_id=t, annee=2026, mois=10, libelle_mois="OCTOBRE")
    pointeur = M.PointeurChantier(tenant_id=t, nom="Chef", code="CHEF01", pin_hash="x", salarie_id=sid)
    db.session.add_all([site, client, comp, per, pointeur])
    db.session.flush()
    for s_id in (sid, autre):
        c = M.Contrat(tenant_id=t, salarie_id=s_id, date_debut=D, salaire_base=150000)
        b = M.BulletinPaie(tenant_id=t, salarie_id=s_id, periode_id=per.id, salaire_base=1, salaire_brut=1,
                           net_a_payer=1, statut="BROUILLON")
        pl = M.PlanningConges.query.filter_by(tenant_id=t, annee=2027).first() or M.PlanningConges(tenant_id=t, annee=2027)
        db.session.add_all([c, b, pl])
        db.session.flush()
        cg = M.Conge(tenant_id=t, salarie_id=s_id, annee=2026, date_depart=D, date_retour=D, statut="DEMANDÉ")
        db.session.add(cg)
        db.session.flush()
        db.session.add_all([
            M.BulletinComposant(bulletin_id=b.id, libelle="Prime"),
            M.ConsultationBulletin(tenant_id=t, salarie_id=s_id, bulletin_id=b.id),
            M.EvenementCarriere(tenant_id=t, salarie_id=s_id, type="PROMOTION", date_effet=D, contrat_id=c.id),
            M.HistoriqueSalarie(tenant_id=t, salarie_id=s_id, champ="emploi"),
            M.Pointage(tenant_id=t, salarie_id=s_id, date_pointage=D),
            M.Acompte(tenant_id=t, salarie_id=s_id, montant=1, date_acompte=D, mois=10, annee=2026),
            M.PrevisionConge(tenant_id=t, planning_id=pl.id, salarie_id=s_id, date_debut=date(2027, 8, 1),
                             date_fin=date(2027, 8, 5), conge_id=cg.id),
            M.DocumentSalarie(tenant_id=t, salarie_id=s_id),
            M.AffectationSite(tenant_id=t, site_id=site.id, salarie_id=s_id),
            M.AffectationMAD(tenant_id=t, client_id=client.id, salarie_id=s_id, date_debut=D),
            M.AccesSalarie(tenant_id=t, salarie_id=s_id),
            M.DemandeDocument(tenant_id=t, salarie_id=s_id, type_doc="ATTESTATION_TRAVAIL"),
            M.DemandeProfil(tenant_id=t, salarie_id=s_id, champ="telephone"),
            M.CompetenceSalarie(tenant_id=t, salarie_id=s_id, competence_id=comp.id, niveau=2),
        ])
        if s_id == autre:                       # dossier RH : bloquerait la suppression
            db.session.add_all([M.Evaluation(tenant_id=t, salarie_id=s_id),
                                M.Formation(tenant_id=t, salarie_id=s_id, intitule="CACES"),
                                M.Sanction(tenant_id=t, salarie_id=s_id, type="AVERTISSEMENT", date_sanction=D)])
    db.session.commit()
    pointeur_id = pointeur.id
    pid = _pret(env, statut="ANNULE")

    def compte(s_id):
        return {tb: db.session.execute(db.text(f"SELECT COUNT(*) FROM {tb} WHERE salarie_id = :s"), {"s": s_id}).scalar()
                for tb in ss.TABLES_TRAITEES}
    avant_autre = compte(autre)
    cli = _cli(env["admin"])
    cli.post(f"/salaries/{sid}/supprimer")
    assert "Salarié NOM0 P supprimé." in _flash(cli) or "supprimé" in _flash(cli)
    db.session.expire_all()
    assert db.session.get(M.Salarie, sid) is None
    assert all(v == 0 for v in compte(sid).values()), compte(sid)
    assert compte(autre) == avant_autre                     # l'autre salarié est intact
    assert db.session.get(M.PretPersonnel, pid) is None
    p = db.session.get(M.PointeurChantier, pointeur_id)
    assert p is not None and p.salarie_id is None           # le compte pointeur reste, détaché
    assert M.BulletinComposant.query.count() == 1 and M.ConsultationBulletin.query.count() == 1


def test_echec_inattendu_ne_supprime_rien(env, monkeypatch):
    def panne(s):
        M.Contrat.query.filter_by(salarie_id=s.id).delete()
        raise RuntimeError("panne")
    monkeypatch.setattr(ss, "supprimer", panne)
    db.session.add(M.Contrat(tenant_id=env["t"], salarie_id=env["s"], date_debut=D, salaire_base=1))
    db.session.commit()
    cli = _cli(env["admin"])
    cli.post(f"/salaries/{env['s']}/supprimer")
    assert "rien n'a été supprimé" in _flash(cli)
    db.session.expire_all()
    assert M.Contrat.query.filter_by(salarie_id=env["s"]).count() == 1


@pytest.mark.parametrize("dossier,texte", [
    (lambda e: M.Sanction(tenant_id=e["t"], salarie_id=e["s"], type="AVERTISSEMENT", date_sanction=D), "1 sanction(s)"),
    (lambda e: M.Evaluation(tenant_id=e["t"], salarie_id=e["s"]), "1 évaluation(s)"),
    (lambda e: M.Formation(tenant_id=e["t"], salarie_id=e["s"], intitule="CACES"), "1 formation(s)"),
])
def test_dossier_rh_bloque(env, dossier, texte):
    db.session.add(dossier(env))
    db.session.commit()
    cli = _cli(env["admin"])
    cli.post(f"/salaries/{env['s']}/supprimer")
    m = _flash(cli)
    assert "Impossible de supprimer" in m and f"{texte} dans son dossier" in m
    assert db.session.get(M.Salarie, env["s"]) is not None
