"""Impression des prêts : état regroupé par contractant (sous-totaux, filtres statut / contractant / site,
situation à une date, détail des remboursements, retard d'après l'échéancier), relevé d'un contractant,
fiche détaillée d'un prêt, cloisonnement entre entreprises."""
import re
from datetime import date

from test_suppression_salarie import env, _cli  # noqa: F401
import models as M
from models import db


def _page(cli, url):
    return re.sub(r"\s+", " ", cli.get(url).get_data(as_text=True))


def _scenario(env):
    t = env["t"]
    a = db.session.get(M.Salarie, env["s"])
    a.nom, a.prenom, a.matricule = "MOUSSAVOU", "Jean", "D014"
    b = db.session.get(M.Salarie, env["autre"])
    b.nom, b.prenom, b.matricule = "NDONG", "Alice", "D021"
    site = M.Site(tenant_id=t, nom="Chantier Nord")
    db.session.add(site)
    db.session.flush()
    db.session.add(M.AffectationSite(tenant_id=t, site_id=site.id, salarie_id=a.id, date_debut=date(2026, 1, 1), actif=True))
    # A : 120 000 en 4 mensualités de 30 000 (juin → septembre) ; 2 remboursements
    p1 = M.PretPersonnel(tenant_id=t, salarie_id=a.id, montant_total=120000, date_octroi=date(2026, 5, 10),
                         nb_echeances=4, motif="Loyer", statut="EN_COURS", montant_rembourse=60000)
    # A : 50 000 en 1 mensualité (septembre)
    p2 = M.PretPersonnel(tenant_id=t, salarie_id=a.id, montant_total=50000, date_octroi=date(2026, 8, 1),
                         nb_echeances=1, motif="Scolarité", statut="EN_COURS", montant_rembourse=0)
    # B : soldé, et un prêt annulé
    p3 = M.PretPersonnel(tenant_id=t, salarie_id=b.id, montant_total=100000, date_octroi=date(2026, 1, 5),
                         nb_echeances=2, statut="SOLDE", montant_rembourse=100000)
    p4 = M.PretPersonnel(tenant_id=t, salarie_id=b.id, montant_total=40000, date_octroi=date(2026, 3, 1),
                         nb_echeances=1, statut="ANNULE", montant_rembourse=0)
    # A : prêt accordé après la date de situation (exclu au 31/08)
    p5 = M.PretPersonnel(tenant_id=t, salarie_id=a.id, montant_total=10000, date_octroi=date(2026, 9, 15),
                         nb_echeances=1, statut="EN_COURS", montant_rembourse=0)
    db.session.add_all([p1, p2, p3, p4, p5])
    db.session.flush()
    db.session.add_all([
        M.RemboursementPret(tenant_id=t, pret_id=p1.id, montant=30000, mois=6, annee=2026, bulletin_id=None,
                            note="retenue juin", date_remboursement=date(2026, 6, 30)),
        M.RemboursementPret(tenant_id=t, pret_id=p1.id, montant=30000, mois=7, annee=2026,
                            date_remboursement=date(2026, 7, 31)),
        M.RemboursementPret(tenant_id=t, pret_id=p3.id, montant=100000, mois=3, annee=2026,
                            date_remboursement=date(2026, 3, 31)),
    ])
    db.session.commit()
    return a.id, b.id, site.id, p1.id


def test_etat_par_contractant(env):
    a, b, site, p1 = _scenario(env)
    cli = _cli(env["admin"])
    page = _page(cli, "/prets/imprimer?au=2026-08-31&detail=1")      # en cours par défaut
    assert "prêts en cours" in page and "État des prêts au personnel" in page and "Situation au :</b> 31/08/2026" in page
    assert "Sous-total MOUSSAVOU Jean" in page and "NDONG" not in page
    # p1 au 31/08 : attendu 90 000 (juin, juillet, août), remboursé 60 000 → restant 60 000, retard 30 000
    assert "Loyer" in page and "2 / 4" in page and "60 000" in page and "30 000" in page
    # sous-total A : prêté 170 000, remboursé 60 000, restant 110 000 ; prêt de septembre exclu
    assert "170 000" in page and "110 000" in page and "N° " in page and "15/09/2026" not in page
    assert "↳ 30/06/2026 · Juin 2026 · remboursement direct · retenue juin" in page
    assert "TOTAL GÉNÉRAL (2 prêt(s))" in page
    # tous les statuts : B apparaît (soldé et annulé), l'annulé n'entre pas dans les totaux
    page = _page(cli, "/prets/imprimer?au=2026-08-31&statut=")
    assert "Sous-total NDONG Alice" in page and "Annulé" in page and "TOTAL GÉNÉRAL (3 prêt(s))" in page
    assert "270 000" in page and "160 000" in page        # prêté 170 000 + 100 000 ; remboursé 60 000 + 100 000
    assert "↳" not in page                                   # sans détail
    # situation au 15/07 : remboursement du 31/07 non compté (remboursé 30 000, retard 30 000)
    page = _page(cli, "/prets/imprimer?au=2026-07-15&detail=1")
    assert "1 / 4" in page and "31/07/2026" not in page
    # filtres contractant et site
    page = _page(cli, f"/prets/imprimer?statut=&salarie_id={b}")
    assert "NDONG" in page and "MOUSSAVOU" not in page and "contractant : NDONG Alice" in page
    page = _page(cli, f"/prets/imprimer?statut=&site_id={site}")
    assert "MOUSSAVOU" in page and "NDONG" not in page and "site : Chantier Nord" in page
    page = _page(cli, "/prets/imprimer?statut=&site_id=0")
    assert "NDONG" in page and "MOUSSAVOU" not in page


def test_releve_contractant_et_fiche(env):
    a, b, site, p1 = _scenario(env)
    cli = _cli(env["admin"])
    page = _page(cli, f"/prets/contractant/{a}/imprimer?au=2026-08-31")
    assert "Relevé des prêts du contractant" in page and "MOUSSAVOU Jean — matricule D014" in page
    assert "Loyer" in page and "Scolarité" in page and "↳ 31/07/2026" in page and "(lu et approuvé)" in page
    page = _page(cli, f"/prets/{p1}/imprimer?au=2026-08-31")
    assert "Fiche de prêt au personnel N° " in page and "4 mensualité(s) de 30 000 F" in page
    assert "Sept. 2026" in page and "90 000 F attendus" in page and "Total remboursé" in page
    # liens depuis la liste et le détail
    assert "Imprimer l'état des prêts" in _page(cli, "/prets")
    det = _page(cli, f"/prets/{p1}")
    assert f"/prets/{p1}/imprimer" in det and f"/prets/contractant/{a}/imprimer" in det


def test_cloisonnement(env):
    a, b, site, p1 = _scenario(env)
    t2 = M.Tenant(slug="b", denomination="BETA", pays="Gabon", plan_id=db.session.get(M.Tenant, env["t"]).plan_id,
                  statut="ACTIF", token_api="b")
    db.session.add(t2)
    db.session.flush()
    u2 = M.Utilisateur(nom="B", prenom="B", email="b@b.ga", role="TENANT_ADMIN", tenant_id=t2.id, actif=True,
                       email_verifie=True)
    u2.set_password("x")
    db.session.add(u2)
    db.session.commit()
    from flask import g
    g.pop("_login_user", None)
    c2 = _cli(u2.id)
    assert c2.get(f"/prets/{p1}/imprimer").status_code == 404
    assert c2.get(f"/prets/contractant/{a}/imprimer").status_code == 404
    assert c2.get(f"/prets/imprimer?salarie_id={a}").status_code == 404
    assert c2.get(f"/prets/imprimer?site_id={site}").status_code == 404
    page = c2.get("/prets/imprimer?statut=").get_data(as_text=True)
    assert "MOUSSAVOU" not in page and "Aucun prêt pour cette sélection" in page
