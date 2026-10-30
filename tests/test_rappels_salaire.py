"""Rappels de salaire : calcul mois par mois (exemple calculé à la main), versement automatique
sur le bulletin de la période choisie, validation bloquée tant que le rappel n'y est pas,
annulation, contrôles de saisie, droits et suppression du salarié."""
from datetime import date

from test_suppression_salarie import env, _cli, _flash  # noqa: F401
from test_controle_virements import _periode
import models as M
from models import db
import rappels_salaire as rs
import controle_paie as cp
from calculs_paie import calculer_bulletin


def _bul(t, s, p, base, statut="VALIDÉ", **champs):
    b = M.BulletinPaie(tenant_id=t, salarie_id=s, periode_id=p.id, salaire_base=base, salaire_brut=base,
                       net_a_payer=base, statut=statut, **champs)
    db.session.add(b)
    db.session.flush()
    return b


def _scenario(env):
    """Salaire de base 200 000 jusqu'en octobre ; augmentation à 230 000 à compter d'août, versée en novembre."""
    t, sid = env["t"], env["s"]
    s = db.session.get(M.Salarie, sid)
    s.date_embauche = date(2020, 1, 1)
    db.session.add(M.Contrat(tenant_id=t, salarie_id=sid, date_debut=date(2020, 1, 1), salaire_base=200000, actif=True,
                             poste="Comptable", type_contrat="CDI", elements_recurrents='{"prime_transport": 20000}'))
    p = {m: _periode(t, 2026, m) for m in (7, 8, 9, 10, 11)}
    _bul(t, sid, p[7], 200000)                                                      # avant l'effet : pas rappelé
    _bul(t, sid, p[8], 200000, heures_sup_10=11538, prime_anciennete=6000)
    _bul(t, sid, p[9], 200000, absences=15385, prime_anciennete=6000)
    _bul(t, sid, p[10], 200000, prime_anciennete=6000, prime_nuit=40000)
    db.session.commit()
    return {m: x.id for m, x in p.items()}


def _creer(cli, sid, pid_versement, **extra):
    data = {"salarie_id": sid, "mois_effet": "2026-08", "ancien": "200000", "nouveau": "230 000",
            "periode_id": pid_versement, "motif": "Augmentation accordée en novembre, effet août",
            "rub_salaire_base": "1", "rub_heures_sup": "1", "rub_absences": "1", "rub_prime_anciennete": "1",
            "action": "enregistrer"}
    data.update(extra)
    return cli.post("/rappels/nouveau", data=data)


def test_calcul_mois_par_mois(env):
    p = _scenario(env)
    per = db.session.get(M.PeriodePaie, p[11])
    lignes, brouillons = rs.preparer(env["t"], env["s"], date(2026, 8, 1), per, 200000, 230000,
                                     rs.rubriques_par_defaut())
    assert [(b.periode.mois, ecart) for b, _, ecart in lignes] == [(8, 32631), (9, 28592), (10, 30900)]
    aout = lignes[0][1]
    assert aout["heures_sup"] == [11538, 13269, 1731] and aout["prime_anciennete"] == [6000, 6900, 900]
    assert lignes[1][1]["absences"] == [15385, 17693, -2308]
    assert "prime_nuit" not in lignes[2][1] and not brouillons
    # prime de nuit cochée : 40 000 × 1,15 = 46 000 → +6 000 en octobre
    lignes2, _ = rs.preparer(env["t"], env["s"], date(2026, 8, 1), per, 200000, 230000,
                             rs.rubriques_par_defaut() + ["prime_nuit"])
    assert lignes2[2][2] == 36900


def test_versement_automatique_et_validation(env):
    p = _scenario(env)
    cli = _cli(env["admin"])
    r = _creer(cli, env["s"], p[11])
    rid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    rap = db.session.get(M.RappelSalaire, rid)
    assert (rap.total, rap.statut, len(rap.lignes)) == (92123, "A_VERSER", 3)
    msg = _flash(cli)
    assert "Rappel enregistré : 92 123 FCFA" in msg and "avenant à 230 000 FCFA à compter de 08/2026" in msg
    # contrat mis à jour : avenant au nouveau salaire, conditions reprises, événement de carrière
    contrats = M.Contrat.query.filter_by(salarie_id=env["s"]).order_by(M.Contrat.id).all()
    assert [(float(c.salaire_base), c.actif) for c in contrats] == [(200000, False), (230000, True)]
    av = contrats[1]
    assert (av.date_debut, av.poste, av.type_contrat, av.elements_recurrents) == (
        date(2026, 8, 1), "Comptable", "CDI", '{"prime_transport": 20000}')
    assert (rap.contrat_maj, rap.contrat_id, rap.contrat_precedent_id) == ("AVENANT", av.id, contrats[0].id)
    evt = M.EvenementCarriere.query.filter_by(salarie_id=env["s"]).one()
    assert (evt.type, evt.contrat_id, evt.ancienne_valeur, evt.nouvelle_valeur) == (
        "AUGMENTATION", av.id, "200 000 FCFA", "230 000 FCFA")
    assert "avenant à <b>230 000 FCFA</b> à compter du 01/08/2026" in cli.get(f"/rappels/{rid}").get_data(as_text=True)
    # génération du bulletin de novembre : le rappel y est inclus, soumis au brut
    cli.post("/bulletins/generer-lot", data={"periode_id": p[11]})
    b = M.BulletinPaie.query.filter_by(salarie_id=env["s"], periode_id=p[11]).one()
    comp = M.BulletinComposant.query.filter_by(bulletin_id=b.id).one()
    assert (comp.rappel_id, float(comp.montant), comp.sens) == (rid, 92123, "GAIN")
    assert comp.libelle == "Rappel de salaire Août 2026 à Octobre 2026"
    sans = calculer_bulletin({"salaire_base": 230000, "anciennete_annees": 6, "convention": None})
    avec = calculer_bulletin({"salaire_base": 230000, "anciennete_annees": 6, "convention": None,
                              "composants": rs.composants(env["t"], env["s"], p[11])})
    assert round(avec["salaire_brut"] - sans["salaire_brut"]) == 92123
    # validation : le rappel est versé, il ne s'annule plus
    cli.post(f"/bulletins/{b.id}/valider")
    assert db.session.get(M.BulletinPaie, b.id).statut == "VALIDÉ"
    assert float(db.session.get(M.BulletinPaie, b.id).salaire_base) == 230000     # repris de l'avenant
    assert "Versé" in cli.get(f"/rappels/{rid}").get_data(as_text=True)
    assert db.session.get(M.RappelSalaire, rid).statut == "VERSE"
    cli.post(f"/rappels/{rid}/annuler", data={"motif": "x"})
    assert "annulez d'abord la validation" in _flash(cli)
    assert M.Contrat.query.filter_by(salarie_id=env["s"], actif=True).one().id == av.id
    # état imprimable
    etat = cli.get(f"/rappels/{rid}/imprimer").get_data(as_text=True)
    assert "ÉTAT DE RAPPEL DE SALAIRE".lower() in etat.lower() and "92 123 FCFA" in etat and "Août 2026" in etat


def test_brouillon_existant_a_reenregistrer(env):
    p = _scenario(env)
    t, sid = env["t"], env["s"]
    brouillon = _bul(t, sid, db.session.get(M.PeriodePaie, p[11]), 200000, statut="BROUILLON")  # avant l'augmentation
    db.session.commit()
    bid = brouillon.id
    cli = _cli(env["admin"])
    r = _creer(cli, sid, p[11])
    rid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    assert "Ouvrez le brouillon existant" in _flash(cli)
    # validation refusée (unitaire et en lot), signalée par le contrôle de la paie
    cli.post(f"/bulletins/{bid}/valider")
    assert "Rappel de salaire à mettre à jour" in _flash(cli)
    cli.post("/bulletins/valider-lot", data={"periode_id": p[11], "action_lot": "valider", "tout_valider": "1"})
    assert db.session.get(M.BulletinPaie, bid).statut == "BROUILLON"
    an = cp.controler(db.session.get(M.Tenant, t), db.session.get(M.PeriodePaie, p[11]))
    assert any(a["code"] == "RAPPEL_ABSENT" and a["niveau"] == "bloquant" for a in an)
    base = [a for a in an if a["code"] == "RAPPEL_BASE"]
    assert base and base[0]["niveau"] == "bloquant" and "porté à 230 000 FCFA" in base[0]["detail"]
    # ouverture en modification : le salaire de base est repris du contrat (avenant)
    page = cli.get(f"/bulletins/saisie?id={bid}").get_data(as_text=True)
    assert "Salaire de base repris du contrat (230 000 FCFA)" in page
    # ré-enregistrement de la saisie : le rappel est inclus automatiquement
    cli.post("/bulletins/saisie", data={"salarie_id": sid, "periode_id": p[11], "salaire_base": "230000",
                                        "action": "brouillon"})
    assert "rappel(s) de salaire inclus automatiquement" in _flash(cli)
    assert M.BulletinComposant.query.filter_by(bulletin_id=bid, rappel_id=rid).count() == 1
    assert not rs.a_reenregistrer(db.session.get(M.BulletinPaie, bid))
    # annulation : avenant supprimé, contrat précédent réactivé ; le brouillon (rappel + base 230 000) bloque
    cli.post(f"/rappels/{rid}/annuler", data={"motif": "Augmentation reportée"})
    msg = _flash(cli)
    assert "le contrat revient à 200 000 FCFA" in msg and "Ré-enregistrez le brouillon" in msg
    assert [(float(c.salaire_base), c.actif) for c in M.Contrat.query.filter_by(salarie_id=sid).all()] == [(200000, True)]
    assert M.EvenementCarriere.query.filter_by(salarie_id=sid).count() == 0
    assert db.session.get(M.RappelSalaire, rid).contrat_maj == "RETABLI"
    an = cp.controler(db.session.get(M.Tenant, t), db.session.get(M.PeriodePaie, p[11]))
    assert {"RAPPEL_ANNULE", "RAPPEL_BASE"} <= {a["code"] for a in an}
    assert "rétabli à 200 000 FCFA" in [a for a in an if a["code"] == "RAPPEL_BASE"][0]["detail"]
    cli.post(f"/bulletins/{bid}/valider")
    assert db.session.get(M.BulletinPaie, bid).statut == "BROUILLON"
    cli.post("/bulletins/saisie", data={"salarie_id": sid, "periode_id": p[11], "salaire_base": "200000",
                                        "action": "brouillon"})
    assert M.BulletinComposant.query.filter_by(bulletin_id=bid).count() == 0
    cli.post(f"/bulletins/{bid}/valider")
    assert db.session.get(M.BulletinPaie, bid).statut == "VALIDÉ"


def test_controles_de_saisie_et_ajustement(env):
    p = _scenario(env)
    cli = _cli(env["admin"])
    sid = env["s"]
    def page(**k):
        return _creer(cli, sid, p[11] if "pid" not in k else k.pop("pid"), **k).get_data(as_text=True)
    assert "doit précéder la période de versement" in page(mois_effet="2026-11")
    assert "égal à l&#39;ancien" in page(nouveau="200000")
    assert "Indiquez le motif" in page(motif="")
    assert "déjà validé" in page(pid=p[10])                      # octobre est validé : pas de versement possible
    b9 = M.BulletinPaie.query.filter_by(salarie_id=sid, periode_id=p[9]).one().id
    assert "doit être justifié" in page(**{f"ajustement_{b9}": "-15000"})
    assert M.RappelSalaire.query.count() == 0
    # aperçu sans enregistrement
    page = cli.post("/rappels/nouveau", data={"salarie_id": sid, "mois_effet": "2026-08", "ancien": "200000",
                                              "nouveau": "230000", "periode_id": p[11], "motif": "x",
                                              "action": "calculer"}).get_data(as_text=True)
    # aucune rubrique cochée : seul le salaire de base (obligatoire) est réévalué, 3 × 30 000
    assert "Calcul mois par mois" in page and "+90 000 FCFA" in page and M.RappelSalaire.query.count() == 0
    # ajustement justifié : effet au 15 août, moitié du mois d'août retirée
    _creer(cli, sid, p[11], **{f"ajustement_{M.BulletinPaie.query.filter_by(salarie_id=sid, periode_id=p[8]).one().id}": "-16316",
                               "justif_ajustement": "effet au 15 août"})
    r = M.RappelSalaire.query.one()
    assert r.total == 92123 - 16316 and "effet au 15 août" in r.motif
    assert [float(l.montant) for l in r.lignes] == [32631 - 16316, 28592, 30900]


def test_droits_cloisonnement_et_suppression(env):
    p = _scenario(env)
    cli = _cli(env["admin"])
    r = _creer(cli, env["s"], p[11])
    rid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    # lecture seule : consultation possible, création refusée
    lec = M.Utilisateur(nom="L", prenom="L", email="l@a.ga", role="LECTURE", tenant_id=env["t"], actif=True,
                        email_verifie=True)
    lec.set_password("x")
    db.session.add(lec)
    db.session.commit()
    from flask import g
    g.pop("_login_user", None)
    cl = _cli(lec.id)
    assert cl.get("/rappels").status_code == 200
    assert cl.post("/rappels/nouveau", data={"salarie_id": env["s"]}).status_code in (302, 403)
    assert M.RappelSalaire.query.count() == 1
    # une autre entreprise ne voit pas le rappel
    t2 = M.Tenant(slug="b", denomination="BETA", pays="Gabon", plan_id=db.session.get(M.Tenant, env["t"]).plan_id,
                  statut="ACTIF", token_api="b")
    db.session.add(t2)
    db.session.flush()
    u2 = M.Utilisateur(nom="B", prenom="B", email="b@b.ga", role="TENANT_ADMIN", tenant_id=t2.id, actif=True,
                       email_verifie=True)
    u2.set_password("x")
    db.session.add(u2)
    db.session.commit()
    g.pop("_login_user", None)
    c2 = _cli(u2.id)
    for url in (f"/rappels/{rid}", f"/rappels/{rid}/imprimer"):
        assert c2.get(url).status_code == 404
    assert c2.post(f"/rappels/{rid}/annuler", data={"motif": "x"}).status_code == 404
    # suppression du salarié : le rappel non versé part avec la fiche
    import suppression_salarie as ss
    s = db.session.get(M.Salarie, env["autre"])
    per = db.session.get(M.PeriodePaie, p[11])
    rap = M.RappelSalaire(tenant_id=env["t"], salarie_id=s.id, periode_id=per.id, motif="x", date_effet=date(2026, 8, 1),
                          ancien_salaire=1, nouveau_salaire=2)
    db.session.add(rap)
    db.session.commit()
    assert ss.obstacles(s) == []
    ss.supprimer(s)
    db.session.commit()
    assert M.RappelSalaire.query.filter_by(salarie_id=env["autre"]).count() == 0


def test_contrat_deja_a_jour_ou_modifie_depuis(env):
    """Contrat déjà au nouveau salaire : pas d'avenant. Contrat modifié après le rappel : l'annulation le conserve."""
    p = _scenario(env)
    t, sid = env["t"], env["s"]
    cli = _cli(env["admin"])
    # 1) promotion déjà saisie par la gestion de carrière (contrat à 230 000) : le rappel ne crée rien
    c0 = M.Contrat.query.filter_by(salarie_id=sid).one()
    c0.salaire_base = 230000
    db.session.commit()
    r = _creer(cli, sid, p[11])
    rid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    assert "Le contrat était déjà au nouveau salaire" in _flash(cli)
    rap = db.session.get(M.RappelSalaire, rid)
    assert (rap.contrat_maj, rap.contrat_id) == ("DEJA_A_JOUR", None) and M.Contrat.query.filter_by(salarie_id=sid).count() == 1
    cli.post(f"/rappels/{rid}/annuler", data={"motif": "doublon"})
    assert float(M.Contrat.query.filter_by(salarie_id=sid).one().salaire_base) == 230000
    # 2) contrat ramené à 200 000, nouveau rappel (avenant), puis promotion à 250 000 avant l'annulation
    c0.salaire_base = 200000
    db.session.commit()
    r = _creer(cli, sid, p[11])
    rid2 = int(r.headers["Location"].rstrip("/").split("/")[-1])
    _flash(cli)
    assert db.session.get(M.RappelSalaire, rid2).contrat_maj == "AVENANT"
    cli.post(f"/salaries/{sid}/promouvoir", data={"type_evt": "AUGMENTATION", "date_effet": "2026-11-01",
                                                 "salaire_base": "250000"})
    cli.post(f"/rappels/{rid2}/annuler", data={"motif": "erreur de taux"})
    assert "avenant au nouveau salaire est conservé" in _flash(cli)
    actif = M.Contrat.query.filter_by(salarie_id=sid, actif=True).one()
    assert float(actif.salaire_base) == 250000 and db.session.get(M.RappelSalaire, rid2).contrat_maj == "CONSERVE"
    assert M.Contrat.query.filter_by(salarie_id=sid).count() == 3
