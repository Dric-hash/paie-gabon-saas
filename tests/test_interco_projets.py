"""Interconnexion PaieGabon → Projets Ameriack : coût de la main-d'œuvre d'un mois réparti par site
(salariés au prorata des jours d'affectation, journaliers selon les jours pointés), envoyé aux chantiers
reliés comme dépenses « Main-d'œuvre » idempotentes. Données fictives ; montants au franc."""
import io
import json
from datetime import date

import pytest

from test_suppression_salarie import env, _cli, _flash  # noqa: F401,F811
from test_controle_virements import _periode
import interco_projets
import main_oeuvre_chantiers as moc
import models as M
from models import db
from app import app as flask_app

CHANTIERS = [{"id": 7, "nom": "Dispensaire de Kango (fictif)", "statut_label": "En cours"},
             {"id": 9, "nom": "École de Nzeng-Ayong (fictif)", "statut_label": "En cours"}]


class _Rep(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def projets(env, monkeypatch):
    flask_app.config.update(PROJETS_URL="http://projets.test", PROJETS_TOKEN="jeton")
    db.session.get(M.Tenant, env["t"]).external_ref = "ACME"
    db.session.commit()
    appels = {"liste": [], "hors_ligne": False}

    def urlopen(req, timeout=None):
        assert req.get_header("X-interco-token") == "jeton" and timeout == interco_projets._TIMEOUT
        if appels["hors_ligne"]:
            raise OSError("réseau")
        if req.get_method() == "GET":
            assert "tenant_ref=ACME" in req.full_url
            return _Rep(json.dumps({"ok": True, "chantiers": CHANTIERS}).encode())
        corps = json.loads(req.data)
        appels["liste"].append(corps)
        return _Rep(json.dumps({"ok": True, "supprimee": bool(corps.get("supprimer"))}).encode())
    monkeypatch.setattr(interco_projets.urllib.request, "urlopen", urlopen)
    yield appels
    flask_app.config.update(PROJETS_URL="", PROJETS_TOKEN="")


def _scenario(env):
    t = env["t"]
    nord, sud, atelier = (M.Site(tenant_id=t, nom=n) for n in ("Chantier Nord", "Chantier Sud", "Atelier"))
    nord.projets_chantier_id, nord.projets_chantier_nom = 7, "Dispensaire de Kango (fictif)"
    sud.projets_chantier_id, sud.projets_chantier_nom = 9, "École de Nzeng-Ayong (fictif)"
    c = M.Salarie(tenant_id=t, matricule="M9", nom="BROUILLON", prenom="B", date_embauche=date(2024, 1, 1))
    j1 = M.Journalier(tenant_id=t, nom="MOUSSAVOU", prenom="J", taux_horaire=1000)
    j2 = M.Journalier(tenant_id=t, nom="OBAME", prenom="K", taux_horaire=1000)
    db.session.add_all([nord, sud, atelier, c, j1, j2])
    db.session.flush()
    a, b = env["s"], env["autre"]
    p = _periode(t, 2026, 10)
    db.session.add_all([
        # salarié A : Nord du 1er au 10 octobre, Sud à partir du 11 (jour du transfert compté pour Sud)
        M.AffectationSite(tenant_id=t, site_id=nord.id, salarie_id=a, date_debut=date(2025, 1, 1),
                          date_fin=date(2026, 10, 11), actif=False),
        M.AffectationSite(tenant_id=t, site_id=sud.id, salarie_id=a, date_debut=date(2026, 10, 11), actif=True),
        M.AffectationSite(tenant_id=t, site_id=nord.id, journalier_id=j2.id, date_debut=date(2026, 9, 1), actif=True),
        # coût employeur A : 620 000 + 31 000 + 25 000 + 6 000 + 3 100 = 685 100
        M.BulletinPaie(tenant_id=t, salarie_id=a, periode_id=p.id, salaire_base=600000, salaire_brut=620000,
                       net_a_payer=1, cnss_patronale=31000, cnamgs_patronale=25000, fnh=6000, cfp=3100, tcs=0,
                       statut="VALIDÉ"),
        # B sans affectation : « sans site » ; C en brouillon : ignoré
        M.BulletinPaie(tenant_id=t, salarie_id=b, periode_id=p.id, salaire_base=300000, salaire_brut=300000,
                       net_a_payer=1, statut="PAYÉ"),
        M.BulletinPaie(tenant_id=t, salarie_id=c.id, periode_id=p.id, salaire_base=900000, salaire_brut=900000,
                       net_a_payer=1, statut="BROUILLON"),
        # J1 : 96 000 du 20 au 31/10, 4 jours pointés au Sud et 2 à l'atelier (feuille non payée)
        M.FeuillePaieJournalier(tenant_id=t, journalier_id=j1.id, date_debut=date(2026, 10, 20),
                                date_fin=date(2026, 10, 31), taux_horaire=1000, montant_brut=96000, statut="EN_ATTENTE"),
        # J2 : 70 000 du 28/09 au 04/10 (fin en octobre), aucun pointage : affectation au Nord
        M.FeuillePaieJournalier(tenant_id=t, journalier_id=j2.id, date_debut=date(2026, 9, 28),
                                date_fin=date(2026, 10, 4), taux_horaire=1000, montant_brut=70000, statut="PAYÉ"),
        M.FeuillePaieJournalier(tenant_id=t, journalier_id=j2.id, date_debut=date(2026, 9, 1),
                                date_fin=date(2026, 9, 27), taux_horaire=1000, montant_brut=50000, statut="PAYÉ"),
    ])
    for k, (jour, site) in enumerate([(20, sud), (21, sud), (22, sud), (23, sud), (26, atelier), (27, atelier)]):
        db.session.add(M.Pointage(tenant_id=t, journalier_id=j1.id, date_pointage=date(2026, 10, jour), present=True,
                                  site_id=site.id))
    db.session.add(M.Pointage(tenant_id=t, journalier_id=j1.id, date_pointage=date(2026, 10, 28), present=False,
                              absent=True, site_id=atelier.id))
    db.session.commit()
    return nord.id, sud.id, atelier.id


def test_cout_par_site_au_franc(env):
    nord, sud, atelier = _scenario(env)
    r = moc.calculer(env["t"], 2026, 10)
    par = {l["site"].id: l for l in r["sites"]}
    # 685 100 × 10/31 = 221 000 ; × 21/31 = 464 100
    assert (par[nord]["salaries"], par[nord]["journaliers"], par[nord]["total"]) == (221000, 70000, 291000)
    assert (par[sud]["salaries"], par[sud]["journaliers"], par[sud]["en_attente"]) == (464100, 64000, 1)
    assert (par[atelier]["salaries"], par[atelier]["journaliers"]) == (0, 32000)
    assert r["sans_site"] == {"salaries": 300000, "journaliers": 0}
    assert (r["total_salaries"], r["total_journaliers"]) == (985100, 166000)
    assert moc.reference(2026, 10, nord, "sal") == f"paie-sal-202610-s{nord}"


def test_envoi_a_projets_idempotent(env, projets):
    nord, sud, atelier = _scenario(env)
    cli = _cli(env["admin"])
    page = cli.get("/sites/projets?mois=2026-10").get_data(as_text=True)
    assert "Dispensaire de Kango (fictif)" in page and "464 100" in page and "dont 1 feuille(s) non payée(s)" in page
    cli.post("/sites/projets/envoyer", data={"mois": "2026-10"})
    assert "4 dépense(s) à jour" in _flash(cli)
    envois = {c["source_ref"]: c for c in projets["liste"]}
    assert envois[f"paie-sal-202610-s{nord}"] == {
        "source_app": "paie", "source_ref": f"paie-sal-202610-s{nord}", "chantier_id": 7, "montant": 221000,
        "date": "2026-10-31", "poste": "Main-d'œuvre", "tenant_ref": "ACME",
        "libelle": "Main-d'œuvre 10/2026 — salariés (coût employeur : brut + charges patronales), site Chantier Nord"}
    assert (envois[f"paie-jour-202610-s{sud}"]["chantier_id"], envois[f"paie-jour-202610-s{sud}"]["montant"]) == (9, 64000)
    # atelier non relié : rien n'est créé, toute ancienne dépense est retirée
    assert envois[f"paie-jour-202610-s{atelier}"].get("supprimer") is True
    assert len(projets["liste"]) == 6
    # Sud détaché du chantier puis mois renvoyé : ses dépenses sont retirées, pas de doublon
    projets["liste"].clear()
    cli.post(f"/sites/{sud}/modifier", data={"nom": "Chantier Sud", "projets_chantier_id": ""})
    assert db.session.get(M.Site, sud).projets_chantier_id is None
    cli.post("/sites/projets/envoyer", data={"mois": "2026-10"})
    m = _flash(cli)
    assert "2 dépense(s) à jour, 4 retirée(s)" in m
    assert {c["source_ref"] for c in projets["liste"] if c.get("supprimer")} >= {f"paie-sal-202610-s{sud}",
                                                                                 f"paie-jour-202610-s{sud}"}
    # Projets injoignable : message clair, arrêt au premier échec
    projets["hors_ligne"] = True
    cli.post("/sites/projets/envoyer", data={"mois": "2026-10"})
    m = _flash(cli)
    assert "Envoi incomplet" in m and "Projets ne répond pas" in m


def test_site_relie_a_un_chantier(env, projets):
    cli = _cli(env["admin"])
    page = cli.get("/sites/nouveau").get_data(as_text=True)
    assert "École de Nzeng-Ayong (fictif) (En cours)" in page
    cli.post("/sites/nouveau", data={"nom": "Kango", "projets_chantier_id": "7"})
    s = M.Site.query.filter_by(nom="Kango").one()
    assert (s.projets_chantier_id, s.projets_chantier_nom) == (7, "Dispensaire de Kango (fictif)")
    # identifiant inconnu de Projets : lien inchangé
    cli.post(f"/sites/{s.id}/modifier", data={"nom": "Kango", "projets_chantier_id": "999"})
    assert "introuvable" in _flash(cli) and db.session.get(M.Site, s.id).projets_chantier_id == 7
    # Projets injoignable : le formulaire le dit et ne touche pas au lien
    projets["hors_ligne"] = True
    assert "Projets ne répond pas" in cli.get(f"/sites/{s.id}/modifier").get_data(as_text=True)
    cli.post(f"/sites/{s.id}/modifier", data={"nom": "Kango", "projets_inchange": "1"})
    assert db.session.get(M.Site, s.id).projets_chantier_id == 7


def test_droits_et_sans_configuration(env):
    _scenario(env)
    cli = _cli(env["admin"])
    page = cli.get("/sites/projets?mois=2026-10").get_data(as_text=True)
    assert "non configurée" in page and "Envoyer octobre 2026" not in page
    cli.post("/sites/projets/envoyer", data={"mois": "2026-10"})
    assert "non configurée" in _flash(cli)


def test_lecture_seule_refusee(env):
    lecteur = M.Utilisateur(nom="DIR", prenom="D", email="d@a.ga", role="DIRECTEUR", tenant_id=env["t"], actif=True,
                            email_verifie=True)
    lecteur.set_password("x")
    db.session.add(lecteur)
    db.session.commit()
    cli = _cli(lecteur.id)
    assert cli.get("/sites/projets").status_code == 403
    r = cli.post("/sites/projets/envoyer", data={"mois": "2026-10"})
    assert r.status_code in (302, 403) and "envoyée à Projets" not in _flash(cli)   # lecture seule
