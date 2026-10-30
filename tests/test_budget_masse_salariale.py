"""Budget de masse salariale : préparation depuis les contrats (mois de présence, augmentation,
ancienneté), postes prévus, suivi budget / réalisé et cumul, alerte (page et contrôle de la paie),
adoption, exports, droits, cloisonnement et suppression d'un salarié."""
from datetime import date

from test_suppression_salarie import env, _cli, _flash  # noqa: F401
from test_controle_virements import _periode
import models as M
from models import db
import budget_masse_salariale as bms
import controle_paie as cp
from calculs_paie import calculer_bulletin


def _cout(r):
    return round(r["salaire_brut"] + r["cnss_patronale"] + r["cnamgs_patronale"] + r["fnh"] + r["cfp"])


def _scenario(env):
    t = env["t"]
    a = db.session.get(M.Salarie, env["s"])
    a.date_embauche = date(2020, 1, 1)
    b = db.session.get(M.Salarie, env["autre"])
    b.date_embauche = date(2026, 3, 15)
    c = M.Salarie(tenant_id=t, matricule="M7", nom="SORTANT", prenom="S", date_embauche=date(2024, 5, 1),
                  date_cessation=date(2026, 6, 30))
    d = M.Salarie(tenant_id=t, matricule="M8", nom="SANSCONTRAT", prenom="X", date_embauche=date(2024, 1, 1))
    site = M.Site(tenant_id=t, nom="Chantier Nord")
    db.session.add_all([c, d, site])
    db.session.flush()
    db.session.add_all([
        M.Contrat(tenant_id=t, salarie_id=a.id, date_debut=date(2020, 1, 1), salaire_base=300000, actif=True,
                  elements_recurrents='{"prime_transport": 20000}'),
        M.Contrat(tenant_id=t, salarie_id=b.id, date_debut=date(2026, 3, 15), salaire_base=200000, actif=True),
        M.Contrat(tenant_id=t, salarie_id=c.id, date_debut=date(2024, 5, 1), salaire_base=150000, actif=True),
        M.AffectationSite(tenant_id=t, site_id=site.id, salarie_id=a.id, date_debut=date(2025, 1, 1), actif=True),
    ])
    db.session.commit()
    return a.id, b.id, c.id, site.id


def _preparer(cli, **k):
    data = {"annee": 2026, "augmentation_pct": "5", "mois_augmentation": 7, "seuil_alerte_pct": "5"}
    data.update(k)
    return cli.post("/budget-masse-salariale/preparer", data=data)


def test_preparation_depuis_les_contrats(env):
    a, b, c, site = _scenario(env)
    cli = _cli(env["admin"])
    _preparer(cli)
    assert "3 ligne(s)" in _flash(cli)
    bu = M.BudgetMasseSalariale.query.one()
    lig = {l.salarie_id: l for l in bu.lignes}
    assert set(lig) == {a, b, c}                                    # sans contrat : pas de ligne
    assert (lig[b].mois_debut, lig[b].mois_fin) == (3, 12) and (lig[c].mois_debut, lig[c].mois_fin) == (1, 6)
    assert lig[a].site_id == site and lig[b].site_id is None
    m = bms.mensuel(lig[a])
    # janvier : 300 000 + transport 20 000, ancienneté 6 ans ; juillet : base + 5 % = 315 000
    jan = calculer_bulletin({"salaire_base": 300000, "prime_transport": 20000, "anciennete_annees": 6,
                             "convention": None})
    jul = calculer_bulletin({"salaire_base": 315000, "prime_transport": 20000, "anciennete_annees": 6,
                             "convention": None})
    assert m[1] == [round(jan["salaire_brut"]), _cout(jan)] and m[7] == [round(jul["salaire_brut"]), _cout(jul)]
    assert m[7][0] > m[6][0] and set(m) == set(range(1, 13))
    s = bms.suivi(bu)
    assert s["total_budget"] == sum(v[1] for l in bu.lignes for v in bms.mensuel(l).values())
    # un seul budget par année
    _preparer(cli)
    assert "existe déjà" in _flash(cli)
    # contrôles de saisie
    _preparer(cli, annee=2027, augmentation_pct="5", mois_augmentation="")
    assert "mois d'effet" in _flash(cli)


def test_postes_prevus_suivi_et_alerte(env):
    a, b, c, site = _scenario(env)
    cli = _cli(env["admin"])
    _preparer(cli, augmentation_pct="0", mois_augmentation="")
    _flash(cli)
    bu = M.BudgetMasseSalariale.query.one()
    # poste prévu : recrutement en septembre
    cli.post(f"/budget-masse-salariale/{bu.id}/ligne", data={"libelle": "Recrutement : chef d'équipe", "site_id": site,
                                                              "mois_debut": 9, "mois_fin": 12, "salaire_base": "250 000"})
    assert "Ligne « Recrutement : chef d'équipe » enregistrée" in _flash(cli)
    rec = M.LigneBudget.query.filter_by(salarie_id=None).one()
    r = calculer_bulletin({"salaire_base": 250000, "convention": None})
    assert bms.mensuel(rec) == {m: [round(r["salaire_brut"]), _cout(r)] for m in range(9, 13)}
    cli.post(f"/budget-masse-salariale/{bu.id}/ligne", data={"libelle": "x", "mois_debut": 10, "mois_fin": 9,
                                                              "salaire_base": "1"})
    assert "le début doit précéder la fin" in _flash(cli)
    # réalisé : janvier et février validés ; février dépasse fortement (heures sup)
    pj, pf, pm = _periode(env["t"], 2026, 1), _periode(env["t"], 2026, 2), _periode(env["t"], 2026, 3)
    budget_jan = bms.suivi(bu)["lignes"][0]["budget_cout"]
    for p, brut in ((pj, 340000), (pf, 520000)):
        db.session.add(M.BulletinPaie(tenant_id=env["t"], salarie_id=a, periode_id=p.id, salaire_base=300000,
                                      salaire_brut=brut, net_a_payer=1, cnss_patronale=10000, cnamgs_patronale=5000,
                                      fnh=2000, cfp=1000, statut="VALIDÉ"))
    db.session.add(M.BulletinPaie(tenant_id=env["t"], salarie_id=c, periode_id=pj.id, salaire_base=150000,
                                  salaire_brut=150000, net_a_payer=1, statut="BROUILLON"))   # non validé : ignoré
    db.session.commit()
    s = bms.suivi(bu)
    jan, fev = s["lignes"][0], s["lignes"][1]
    assert jan["realise_cout"] == 340000 + 18000 and jan["nb"] == 1
    assert jan["ecart"] == 358000 - budget_jan
    assert fev["realise_cout"] == 538000 and s["dernier_mois"] == 2 and s["lignes"][2]["realise_cout"] is None
    assert s["cumul_realise"] == 896000
    assert s["ecart_cumul"] == 896000 - (jan["budget_cout"] + fev["budget_cout"])
    attendu = s["ecart_cumul_pct"] > 5
    assert s["alerte"] == attendu
    page = cli.get(f"/budget-masse-salariale/{bu.id}").get_data(as_text=True)
    assert "Chantier Nord" in page and ("Dépassement" in page) == attendu
    # contrôle de la paie : seulement si le budget est adopté
    tn = db.session.get(M.Tenant, env["t"])
    assert not any(x["code"] == "BUDGET_DEPASSE" for x in cp.controler(tn, pm))
    cli.post(f"/budget-masse-salariale/{bu.id}/adopter")
    _flash(cli)
    db.session.add(M.BulletinPaie(tenant_id=env["t"], salarie_id=a, periode_id=pm.id, salaire_base=300000,
                                  salaire_brut=3000000, net_a_payer=1, statut="BROUILLON"))
    db.session.commit()
    al = [x for x in cp.controler(tn, pm) if x["code"] == "BUDGET_DEPASSE"]
    assert al and al[0]["niveau"] == "attention" and "seuil 5 %" in al[0]["detail"]
    # seuil relevé : plus d'alerte
    cli.post(f"/budget-masse-salariale/{bu.id}/parametres", data={"seuil_alerte_pct": "100"})
    assert not any(x["code"] == "BUDGET_DEPASSE" for x in cp.controler(tn, pm))
    # budget adopté : lignes figées
    cli.post(f"/budget-masse-salariale/{bu.id}/ligne/{rec.id}/supprimer")
    assert "rouvrez-le" in _flash(cli) and db.session.get(M.LigneBudget, rec.id) is not None
    cli.post(f"/budget-masse-salariale/{bu.id}/rouvrir")
    cli.post(f"/budget-masse-salariale/{bu.id}/ligne/{rec.id}/supprimer")
    assert db.session.get(M.LigneBudget, rec.id) is None


def test_exports(env):
    _scenario(env)
    cli = _cli(env["admin"])
    _preparer(cli)
    bu = M.BudgetMasseSalariale.query.one()
    csv_ = cli.get(f"/budget-masse-salariale/{bu.id}/export.csv").get_data(as_text=True)
    assert "Budget de masse salariale 2026" in csv_ and "juillet" in csv_ and "NOM0" in csv_
    etat = cli.get(f"/budget-masse-salariale/{bu.id}/imprimer").get_data(as_text=True)
    total = f"{bms.suivi(bu)['total_budget']:,.0f}".replace(",", " ")
    assert "BUDGET DE MASSE SALARIALE 2026".lower() in etat.lower() and total in etat
    assert total in cli.get("/budget-masse-salariale").get_data(as_text=True)


def test_droits_cloisonnement_suppression(env):
    a, b, c, site = _scenario(env)
    cli = _cli(env["admin"])
    _preparer(cli)
    bid = M.BudgetMasseSalariale.query.one().id
    lec = M.Utilisateur(nom="L", prenom="L", email="l@a.ga", role="LECTURE", tenant_id=env["t"], actif=True,
                        email_verifie=True)
    lec.set_password("x")
    t2 = M.Tenant(slug="b", denomination="BETA", pays="Gabon", plan_id=db.session.get(M.Tenant, env["t"]).plan_id,
                  statut="ACTIF", token_api="b")
    db.session.add_all([lec, t2])
    db.session.flush()
    u2 = M.Utilisateur(nom="B", prenom="B", email="b@b.ga", role="TENANT_ADMIN", tenant_id=t2.id, actif=True,
                       email_verifie=True)
    u2.set_password("x")
    db.session.add(u2)
    db.session.commit()
    from flask import g
    g.pop("_login_user", None)
    cl = _cli(lec.id)
    assert cl.get(f"/budget-masse-salariale/{bid}").status_code == 200
    assert cl.post(f"/budget-masse-salariale/{bid}/adopter").status_code in (302, 403)
    assert db.session.get(M.BudgetMasseSalariale, bid).statut == "BROUILLON"
    g.pop("_login_user", None)
    c2 = _cli(u2.id)
    for url in (f"/budget-masse-salariale/{bid}", f"/budget-masse-salariale/{bid}/export.csv"):
        assert c2.get(url).status_code == 404
    assert c2.post(f"/budget-masse-salariale/{bid}/supprimer").status_code == 404
    # suppression d'un salarié : la ligne budgétée reste, détachée
    import suppression_salarie as ss
    sb = db.session.get(M.Salarie, b)
    assert ss.obstacles(sb) == []
    ss.supprimer(sb)
    db.session.commit()
    l = M.LigneBudget.query.filter_by(libelle="NOM1 P").one() if M.LigneBudget.query.filter_by(libelle="NOM1 P").first() \
        else [x for x in M.LigneBudget.query.all() if x.salarie_id is None][0]
    assert l.salarie_id is None and len(db.session.get(M.BudgetMasseSalariale, bid).lignes) == 3


def test_transfert_de_site_en_cours_d_annee(env):
    """Transfert le 10/02/2026 de Chantier Nord vers Chantier Sud : le budget change de site dès février,
    le réalisé de février est réparti 9/28 Nord et 19/28 Sud."""
    a, b, c, nord = _scenario(env)
    t = env["t"]
    sud = M.Site(tenant_id=t, nom="Chantier Sud")
    db.session.add(sud)
    db.session.flush()
    aff = M.AffectationSite.query.filter_by(salarie_id=a).one()
    aff.actif, aff.date_fin = False, date(2026, 2, 10)
    db.session.add(M.AffectationSite(tenant_id=t, site_id=sud.id, salarie_id=a, date_debut=date(2026, 2, 10), actif=True))
    db.session.commit()
    rep = bms.repartition_mois(bms.affectations(t)[a], 2026, 2)
    assert rep == {nord: 9 / 28, sud.id: 19 / 28}
    cli = _cli(env["admin"])
    _preparer(cli, augmentation_pct="0", mois_augmentation="")
    _flash(cli)
    bu = M.BudgetMasseSalariale.query.one()
    la = M.LigneBudget.query.filter_by(salarie_id=a).one()
    assert (la.site_id, la.nouveau_site_id, la.mois_transfert) == (nord, sud.id, 2)
    # réalisé : janvier et février, 280 000 de coût chacun
    for mo in (1, 2):
        p = _periode(t, 2026, mo)
        db.session.add(M.BulletinPaie(tenant_id=t, salarie_id=a, periode_id=p.id, salaire_base=280000,
                                      salaire_brut=280000, net_a_payer=1, statut="VALIDÉ"))
    db.session.commit()
    sites = {x["site"]: x for x in bms.par_site(bu)}
    m = bms.mensuel(la)
    assert round(sites["Chantier Nord"]["realise"]) == 280000 + round(280000 * 9 / 28) == 370000
    assert round(sites["Chantier Sud"]["realise"]) == round(280000 * 19 / 28) == 190000
    assert sites["Chantier Nord"]["budget"] == m[1][1] and sites["Chantier Sud"]["budget"] == m[2][1]
    assert sites["Chantier Sud"]["annuel"] == sum(v[1] for k, v in m.items() if k >= 2)
    page = cli.get(f"/budget-masse-salariale/{bu.id}").get_data(as_text=True)
    assert "Chantier Nord → Chantier Sud" not in page          # brouillon : formulaire d'édition
    # modification : transfert vers Nord depuis Nord refusé ; mois de transfert obligatoire
    base = {"ligne_id": la.id, "libelle": la.libelle, "site_id": nord, "mois_debut": 1, "mois_fin": 12,
            "salaire_base": "300000"}
    cli.post(f"/budget-masse-salariale/{bu.id}/ligne", data=dict(base, nouveau_site_id=nord, mois_transfert=5))
    assert "doit être différent" in _flash(cli)
    cli.post(f"/budget-masse-salariale/{bu.id}/ligne", data=dict(base, nouveau_site_id=sud.id))
    assert "Transfert : choisissez un mois" in _flash(cli)
    cli.post(f"/budget-masse-salariale/{bu.id}/ligne", data=dict(base, nouveau_site_id=sud.id, mois_transfert=7))
    _flash(cli)
    assert db.session.get(M.LigneBudget, la.id).mois_transfert == 7
    cli.post(f"/budget-masse-salariale/{bu.id}/adopter")
    page = cli.get(f"/budget-masse-salariale/{bu.id}").get_data(as_text=True)
    assert "Chantier Nord → Chantier Sud" in page and "dès Juil." in page
    assert "Chantier Sud" in cli.get(f"/budget-masse-salariale/{bu.id}/export.csv").get_data(as_text=True)
