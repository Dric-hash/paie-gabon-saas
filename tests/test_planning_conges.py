"""Planning prévisionnel des congés : souhaits des salariés (portail), arbitrage RH, validation
par la direction, demandes de congé créées depuis le planning, contrôles et vue annuelle."""
from datetime import date, timedelta

from test_portail_salaries import env, _client_pour, _espace_connecte, _messages  # noqa: F401
from app import app as flask_app
from models import db, Conge, PlanningConges, PrevisionConge, Salarie
import planning_conges as pc
from conges_avance import planning_absences

A = date.today().year + 1            # planning de l'an prochain : dates toujours futures


def _d(m, j):
    return date(A, m, j).isoformat()


def _prev(**k):
    with flask_app.app_context():
        return [(x.salarie_id, x.statut, x.origine, x.date_debut.isoformat()) for x in
                PrevisionConge.query.filter_by(**k).order_by(PrevisionConge.id)]


def test_circuit_complet(env):
    esp = _espace_connecte(env)                                      # salarié s1 sur son portail
    assert "Mes souhaits pour le planning des congés" in esp.get("/espace/conges").get_data(as_text=True)
    esp.post("/espace/conges/souhaits", data={"date_debut": _d(8, 3), "date_fin": _d(8, 21), "commentaire": "village"})
    assert f"Souhait envoyé (19 jour(s)) : votre service RH l'étudiera pour le planning {A}." in _messages(esp)
    # chevauchement refusé
    esp.post("/espace/conges/souhaits", data={"date_debut": _d(8, 20), "date_fin": _d(8, 25)})
    assert "chevauchent une autre période prévue" in _messages(esp)
    assert _prev() == [(env["s1"], "PROPOSEE", "PORTAIL", _d(8, 3))]

    rh = _client_pour(env["rh"])
    rh.post(f"/conges/previsionnel/{A}/periode", data={"salarie_id": env["s2"], "date_debut": _d(8, 10),
                                                      "date_fin": _d(8, 14)})
    page = rh.get(f"/conges/previsionnel?annee={A}").get_data(as_text=True)
    assert "Propositions des salariés à traiter (1)" in page and "« village »" in page
    # validation impossible tant qu'une proposition attend ; le RH ne valide pas
    assert rh.post(f"/conges/previsionnel/{A}/valider").status_code == 403
    dr = _client_pour(env["dir"])
    dr.post(f"/conges/previsionnel/{A}/valider")
    with flask_app.app_context():
        assert PlanningConges.query.one().statut == "BROUILLON"
        pid = PrevisionConge.query.filter_by(salarie_id=env["s1"]).one().id
    rh.post(f"/conges/previsionnel/periode/{pid}/retenir")
    # demande impossible avant la validation
    rh.post(f"/conges/previsionnel/periode/{pid}/demande")
    with flask_app.app_context():
        assert Conge.query.filter(Conge.date_depart.isnot(None)).count() == 0
    dr.post(f"/conges/previsionnel/{A}/valider", data={"commentaire": "OK pour l'été"})
    with flask_app.app_context():
        p = PlanningConges.query.one()
        assert (p.statut, p.commentaire, p.valide_par_id) == ("VALIDE", "OK pour l'été", env["dir"])
    # le salarié voit son souhait retenu et ne peut plus le retirer
    t = esp.get("/espace/conges").get_data(as_text=True)
    assert "Retenu" in t
    esp.post(f"/espace/conges/souhaits/{pid}/annuler")
    assert "déjà été traité" in _messages(esp)
    # le RH crée les demandes d'août : elles suivent le circuit habituel
    r = rh.post(f"/conges/previsionnel/{A}/demandes", data={"mois": "8"}, follow_redirects=True)
    assert "2 demande(s) de congé créée(s) pour août." in r.get_data(as_text=True)
    with flask_app.app_context():
        cs = Conge.query.filter(Conge.date_depart.isnot(None)).order_by(Conge.salarie_id).all()
        assert [(c.salarie_id, c.statut, c.origine, c.jours_pris) for c in cs] == \
            [(env["s1"], "DEMANDÉ", "PLANNING", 0), (env["s2"], "DEMANDÉ", "PLANNING", 0)]
        assert all(x.conge_id for x in PrevisionConge.query)
        cid = cs[0].id
    rh.post(f"/conges/previsionnel/periode/{pid}/demande")         # déjà créée
    rh.post(f"/conges/previsionnel/periode/{pid}/supprimer")       # la période reste
    with flask_app.app_context():
        assert PrevisionConge.query.count() == 2
    # approbation habituelle : le solde bouge à ce moment-là seulement
    _client_pour(env["admin"]).post(f"/conges/{cid}/approuver")
    with flask_app.app_context():
        assert db.session.get(Conge, cid).statut == "APPROUVÉ" and float(db.session.get(Conge, cid).jours_pris) == 19


def test_controles_solde_et_absences_simultanees(env):
    rh = _client_pour(env["rh"])
    for sid, d1, d2 in ((env["s1"], _d(7, 1), _d(9, 30)), (env["s2"], _d(7, 15), _d(7, 31))):
        rh.post(f"/conges/previsionnel/{A}/periode", data={"salarie_id": sid, "date_debut": d1, "date_fin": d2})
    rh.post(f"/conges/previsionnel/{A}/parametres", data={"seuil_absents_pct": "50"})
    with flask_app.app_context():
        from models import Tenant
        t = db.session.get(Tenant, env["t"])
        ctrl = pc.controles(t, pc.obtenir(t, A))
        s1 = next(l for l in ctrl["salaries"] if l["salarie"].id == env["s1"])
        assert s1["jours_prevus"] == 92 and s1["depasse"]          # 92 jours prévus, bien au-delà du solde
        alertes = [(a["groupe"], a["debut"], a["fin"], a["max"], a["effectif"]) for a in ctrl["alertes"]]
        assert alertes == [("Toute l'entreprise", date(A, 7, 15), date(A, 7, 31), 2, 2)]
    page = rh.get(f"/conges/previsionnel?annee={A}").get_data(as_text=True)
    assert "Trop d'absents en même temps" in page and "Jours prévus supérieurs au solde disponible" in page
    # période hors de l'année, ou à l'envers : refusées
    for d1, d2 in ((f"{A + 1}-01-03", f"{A + 1}-01-05"), (_d(3, 10), _d(3, 1))):
        rh.post(f"/conges/previsionnel/{A}/periode", data={"salarie_id": env["s1"], "date_debut": d1, "date_fin": d2})
    with flask_app.app_context():
        assert PrevisionConge.query.count() == 2
    # seuil hors bornes
    r = rh.post(f"/conges/previsionnel/{A}/parametres", data={"seuil_absents_pct": "150"}, follow_redirects=True)
    assert "entre 0 et 100" in r.get_data(as_text=True)


def test_vue_annuelle_et_vue_du_mois(env):
    rh = _client_pour(env["rh"])
    rh.post(f"/conges/previsionnel/{A}/periode", data={"salarie_id": env["s1"], "date_debut": _d(1, 20),
                                                      "date_fin": _d(3, 10)})
    t = rh.get(f"/conges/planning-annuel?annee={A}").get_data(as_text=True)
    assert f"Planning des congés {A}" in t and "NDONG Jean" in t and "pa-barre pa-RETENUE" in t
    assert "pa-barre pa-RETENUE" not in rh.get(f"/conges/planning-annuel?annee={A}&prevus=0").get_data(as_text=True)
    assert "NDONG Jean" not in rh.get(f"/conges/planning-annuel?annee={A}&categorie=99999").get_data(as_text=True)
    # vue du mois : un congé de janvier à mars apparaît aussi en février
    with flask_app.app_context():
        s = db.session.get(Salarie, env["s1"])
        c = Conge(tenant_id=env["t"], salarie_id=s.id, annee=A, type_conge="ANNUEL", statut="APPROUVÉ",
                  date_depart=date(A, 1, 20), date_retour=date(A, 3, 10))
        db.session.add(c)
        db.session.commit()
        assert [p["salarie_id"] for p in planning_absences([c], A, 2)] == [s.id]
        assert planning_absences([c], A, 4) == []


def test_portail_limites_et_cloisonnement(env):
    esp = _espace_connecte(env)
    rh = _client_pour(env["rh"])
    rh.post(f"/conges/previsionnel/{A}/parametres", data={"seuil_absents_pct": "30",
                                                         "date_limite_propositions": (date.today() - timedelta(days=1)).isoformat()})
    esp.post("/espace/conges/souhaits", data={"date_debut": _d(5, 4), "date_fin": _d(5, 8)})
    assert f"les propositions pour le planning {A} sont closes" in _messages(esp).lower()
    esp.post("/espace/conges/souhaits", data={"date_debut": (date.today() - timedelta(days=2)).isoformat(),
                                              "date_fin": date.today().isoformat()})
    assert "Proposez des dates entre aujourd'hui" in _messages(esp)
    rh.post(f"/conges/previsionnel/{A}/parametres", data={"seuil_absents_pct": "30", "date_limite_propositions": ""})
    esp.post("/espace/conges/souhaits", data={"date_debut": _d(5, 4), "date_fin": _d(5, 8)})
    with flask_app.app_context():
        x = PrevisionConge.query.one()
        pid = x.id
        assert (x.salarie_id, x.statut) == (env["s1"], "PROPOSEE")
    esp.post(f"/espace/conges/souhaits/{pid}/annuler")
    with flask_app.app_context():
        assert PrevisionConge.query.count() == 0
    # une autre entreprise ne voit ni ne touche les périodes de celle-ci
    rh.post(f"/conges/previsionnel/{A}/periode", data={"salarie_id": env["s1"], "date_debut": _d(6, 1),
                                                      "date_fin": _d(6, 5)})
    with flask_app.app_context():
        pid = PrevisionConge.query.one().id
    autre = _client_pour(env["admin_b"])
    for action in ("retenir", "refuser", "supprimer", "demande", "modifier"):
        assert autre.post(f"/conges/previsionnel/periode/{pid}/{action}").status_code == 404
    assert "NDONG" not in autre.get(f"/conges/previsionnel?annee={A}").get_data(as_text=True)
    # salarié d'une autre entreprise : refusé à l'ajout
    autre.post(f"/conges/previsionnel/{A}/periode", data={"salarie_id": env["s1"], "date_debut": _d(6, 8),
                                                         "date_fin": _d(6, 9)})
    with flask_app.app_context():
        assert PrevisionConge.query.count() == 1
