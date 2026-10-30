"""Provision pour congés payés : jours dus (année + reliquat), base sur 12 bulletins ÷ diviseur (24 par défaut, au choix),
charges aux taux constatés, écriture d'ajustement équilibrée, validation / arrêté suivant,
exports, comptes, droits, cloisonnement et suppression d'un salarié."""
from datetime import date

from test_suppression_salarie import env, _cli, _flash  # noqa: F401
from test_controle_virements import _periode
import models as M
from models import db
import provision_conges as pc
from calculs_paie import calculer_bulletin


def _scenario(env):
    """A : national, embauché en 2020, 12 bulletins validés (juin 2025 → mai 2026).
    B : non national, embauché le 01/03/2026, sans bulletin (base = contrat 260 000).
    C : sorti le 30/04/2026 → absent de l'arrêté au 31/05/2026."""
    t = env["t"]
    a = db.session.get(M.Salarie, env["s"])
    a.date_embauche, a.nationalite = date(2020, 1, 1), "GABONAISE"
    b = db.session.get(M.Salarie, env["autre"])
    b.date_embauche, b.nationalite = date(2026, 3, 1), "CAMEROUNAISE"
    c = M.Salarie(tenant_id=t, matricule="M9", nom="PARTI", prenom="P", date_embauche=date(2021, 1, 1),
                  date_cessation=date(2026, 4, 30))
    db.session.add(c)
    db.session.add(M.Contrat(tenant_id=t, salarie_id=b.id, date_debut=date(2026, 3, 1), salaire_base=260000, actif=True))
    mois = [(2025, m) for m in range(6, 13)] + [(2026, m) for m in range(1, 7)]
    per = {k: _periode(t, *k) for k in mois}
    for i, k in enumerate(mois[:12]):
        brut = 360000 if i == 5 else 300000                 # novembre 2025 : prime de rendement 30 000 (exclue)
        db.session.add(M.BulletinPaie(tenant_id=t, salarie_id=a.id, periode_id=per[k].id, salaire_base=300000,
                                      salaire_brut=brut, prime_rendement=30000 if i == 5 else 0, net_a_payer=250000,
                                      cnss_patronale=30000, cnamgs_patronale=12300, fnh=6000, cfp=1500, statut="VALIDÉ"))
    # bulletin de juin 2026 : postérieur à l'arrêté de mai, ignoré pour celui-ci
    db.session.add(M.BulletinPaie(tenant_id=t, salarie_id=a.id, periode_id=per[(2026, 6)].id, salaire_base=300000,
                                  salaire_brut=900000, net_a_payer=1, statut="VALIDÉ"))
    # congés : 3 jours pris en 2026 ; solde 2025 saisi (24 acquis, 20 pris → reliquat 4)
    db.session.add(M.Conge(tenant_id=t, salarie_id=a.id, annee=2026, jours_pris=3, statut="APPROUVÉ",
                           date_depart=date(2026, 2, 2), date_retour=date(2026, 2, 5)))
    db.session.add(M.Conge(tenant_id=t, salarie_id=a.id, annee=2025, jours_acquis=24, jours_pris=20))
    db.session.commit()
    return a.id, b.id, c.id


def test_calcul_a_la_main(env):
    a, b, c = _scenario(env)
    t = db.session.get(M.Tenant, env["t"])
    p = pc.calculer(t, 2026, 5, "Test")
    db.session.commit()
    lig = {l.salarie_id: l for l in p.lignes}
    assert set(lig) == {a, b}                                       # C est sorti avant l'arrêté
    la = lig[a]
    # 2026 : 01/01 → 31/05 = 150 j / 30,44 = 4,9 mois × 2 j = 9,8 acquis − 3 pris = 6,8 ; + reliquat 2025 : 4 → 10,8
    assert float(la.jours) == 10.8
    # moyenne : (11 × 300 000 + (360 000 − 30 000)) / 12 = 302 500 ; ÷ 24 = 12 604,17 ; × 10,8 = 136 125
    assert (float(la.base_journaliere), float(la.provision), float(p.diviseur)) == (12604.17, 136125, 24)
    # taux : 12 × 42 300 / 3 660 000 et 12 × 7 500 / 3 660 000
    assert float(la.charges) == round(136125 * 507600 / 3660000) == 18879
    assert float(la.taxes) == round(136125 * 90000 / 3660000) == 3347
    assert la.national
    lb = lig[b]
    # 01/03 → 31/05 = 91 j / 30,44 = 3,0 mois × 2 = 6 jours ; 260 000 ÷ 24 × 6 = 65 000
    assert (float(lb.jours), float(lb.provision), lb.national) == (6.0, 65000, False)
    r = calculer_bulletin({"salaire_base": 260000}, nb_parts=1.0)
    t_soc = (r["cnss_patronale"] + r["cnamgs_patronale"]) / r["salaire_brut"]
    assert float(lb.charges) == round(65000 * t_soc)
    tot = pc.totaux(p)
    assert tot["provision"] == 201125
    # écriture du premier arrêté : de zéro à la provision, équilibrée, comptes SYSCOHADA
    e = pc.ecriture(p, t)
    assert sum(x["debit"] for x in e) == sum(x["credit"] for x in e) == tot["total"]
    assert {(x["compte"], x["debit"]) for x in e if x["debit"]} >= {("6613", 136125), ("6623", 65000),
                                                                    ("6641", 18879), ("6413", 3347 + float(lb.taxes))}
    assert {x["compte"] for x in e if x["credit"]} == {"4281", "4382", "4486"}


def test_validation_arrete_suivant_et_reprise(env):
    a, b, c = _scenario(env)
    cli = _cli(env["admin"])
    cli.post("/provisions-conges/calculer", data={"annee": 2026, "mois": 5})
    assert "2 salarié(s)" in _flash(cli)
    p5 = M.ProvisionConges.query.one()
    cli.post(f"/provisions-conges/{p5.id}/valider")
    assert db.session.get(M.ProvisionConges, p5.id).statut == "VALIDE"
    # un arrêté validé ne se recalcule ni ne se supprime
    cli.post("/provisions-conges/calculer", data={"annee": 2026, "mois": 5})
    assert "annulez sa validation" in _flash(cli)
    cli.post(f"/provisions-conges/{p5.id}/supprimer")
    assert db.session.get(M.ProvisionConges, p5.id) is not None
    tot5 = pc.totaux(db.session.get(M.ProvisionConges, p5.id))
    # juin : A prend 10 jours → la dette baisse pour A ; l'écriture est l'ajustement depuis mai
    db.session.add(M.Conge(tenant_id=env["t"], salarie_id=a, annee=2026, jours_pris=10, statut="APPROUVÉ",
                           date_depart=date(2026, 6, 1), date_retour=date(2026, 6, 12)))
    db.session.commit()
    cli.post("/provisions-conges/calculer", data={"annee": 2026, "mois": 6})
    _flash(cli)
    p6 = M.ProvisionConges.query.filter_by(mois=6).one()
    t = db.session.get(M.Tenant, env["t"])
    assert pc.precedent(p6).id == p5.id
    tot6 = pc.totaux(p6)
    e = pc.ecriture(p6, t)
    assert sum(x["debit"] for x in e) == sum(x["credit"] for x in e)
    delta = tot6["provision_national"] - tot5["provision_national"]
    assert delta < 0
    assert {"compte": "4281", "libelle": "Provision congés payés (nationaux)", "debit": round(-delta), "credit": 0} in e
    page = cli.get(f"/provisions-conges/{p6.id}").get_data(as_text=True)
    assert "Écriture d'ajustement" in page and "4281" in page
    # mai ne peut pas être rouvert tant que juin est validé
    cli.post(f"/provisions-conges/{p6.id}/valider")
    cli.post(f"/provisions-conges/{p5.id}/devalider")
    assert "annulez d'abord sa validation" in _flash(cli)
    cli.post(f"/provisions-conges/{p6.id}/devalider")
    cli.post(f"/provisions-conges/{p6.id}/supprimer")
    assert M.ProvisionConges.query.count() == 1


def test_exports_et_comptes(env):
    _scenario(env)
    cli = _cli(env["admin"])
    cli.post("/provisions-conges/calculer", data={"annee": 2026, "mois": 5})
    pid = M.ProvisionConges.query.one().id
    txt = cli.get(f"/provisions-conges/{pid}/ecriture.txt").data.decode("windows-1252")
    assert "\t6613\t" in txt and "136 125,00" in txt and "PCP202605" in txt
    csv_ = cli.get(f"/provisions-conges/{pid}/export.csv").get_data(as_text=True)
    assert "M0;NOM0" in csv_.replace('"', "") and ";10.8;" in csv_ and "12 bulletin(s)" in csv_
    etat = cli.get(f"/provisions-conges/{pid}/imprimer").get_data(as_text=True)
    assert "Provision pour congés payés au 31/05/2026" in etat and "201 125" in etat and "÷ 24 jours" in etat
    # comptes personnalisés
    data = {f"compte_{k}": c for k, _, c in pc.COMPTES}
    data["compte_dette_conges"] = "42810"
    cli.post("/provisions-conges/comptes", data=data)
    assert "Comptes enregistrés" in _flash(cli)
    assert "\t42810\t" in cli.get(f"/provisions-conges/{pid}/ecriture.txt").data.decode("windows-1252")
    data["compte_taxes"] = "64AB"
    cli.post("/provisions-conges/comptes", data=data)
    assert "Compte invalide" in _flash(cli)
    cli.post("/provisions-conges/comptes", data={"action": "defaut"})
    assert pc.comptes(db.session.get(M.Tenant, env["t"]))["dette_conges"] == "4281"


def test_droits_cloisonnement_suppression(env):
    a, b, c = _scenario(env)
    cli = _cli(env["admin"])
    cli.post("/provisions-conges/calculer", data={"annee": 2026, "mois": 5})
    pid = M.ProvisionConges.query.one().id
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
    assert cl.get(f"/provisions-conges/{pid}").status_code == 200
    assert cl.post(f"/provisions-conges/{pid}/valider").status_code in (302, 403)
    assert db.session.get(M.ProvisionConges, pid).statut == "BROUILLON"
    g.pop("_login_user", None)
    c2 = _cli(u2.id)
    for url in (f"/provisions-conges/{pid}", f"/provisions-conges/{pid}/ecriture.txt", f"/provisions-conges/{pid}/imprimer"):
        assert c2.get(url).status_code == 404
    assert c2.post(f"/provisions-conges/{pid}/supprimer").status_code == 404
    # suppression d'un salarié : retiré d'un brouillon, bloquée par un arrêté validé
    import suppression_salarie as ss
    sb = db.session.get(M.Salarie, b)
    assert ss.obstacles(sb) == []
    db.session.get(M.ProvisionConges, pid).statut = "VALIDE"
    db.session.commit()
    assert "1 arrêté(s) validé(s) de provision pour congés payés" in ss.obstacles(sb)
    db.session.get(M.ProvisionConges, pid).statut = "BROUILLON"
    db.session.commit()
    ss.supprimer(sb)
    db.session.commit()
    assert {l.salarie_id for l in db.session.get(M.ProvisionConges, pid).lignes} == {a}


def test_diviseur_au_choix(env):
    """Diviseur paramétrable (26 ici) : appliqué au calcul suivant, figé sur l'arrêté ; contrôle de saisie."""
    a, b, c = _scenario(env)
    cli = _cli(env["admin"])
    cli.post("/provisions-conges/diviseur", data={"diviseur": "0"})
    assert "Diviseur invalide" in _flash(cli)
    cli.post("/provisions-conges/diviseur", data={"diviseur": "26"})
    assert "Diviseur enregistré : 26 jours" in _flash(cli)
    cli.post("/provisions-conges/calculer", data={"annee": 2026, "mois": 5})
    _flash(cli)
    p = M.ProvisionConges.query.one()
    la = [l for l in p.lignes if l.salarie_id == a][0]
    # 302 500 ÷ 26 × 10,8 = 125 653,85 → 125 654
    assert (float(p.diviseur), float(la.base_journaliere), float(la.provision)) == (26, 11634.62, 125654)
    cli.post(f"/provisions-conges/{p.id}/valider")
    cli.post("/provisions-conges/diviseur", data={"diviseur": "24"})
    _flash(cli)
    assert float(db.session.get(M.ProvisionConges, p.id).diviseur) == 26          # arrêté validé inchangé
    assert "÷ 24 jours" in cli.get("/provisions-conges").get_data(as_text=True)
