"""Indicateurs RH (bilan social simplifié) : effectifs et répartitions, entrées / sorties et taux de
rotation, ancienneté, pyramide des âges, absentéisme (congés + pointage, sans double compte), masse
salariale par mois / site / catégorie / sexe, filtres, exports et cloisonnement. Exemple calculé à la main
sur janvier → juin 2026."""
import re
from datetime import date, timedelta

from test_suppression_salarie import env, _cli  # noqa: F401
from test_controle_virements import _periode
import models as M
from models import db
import indicateurs_rh as ir

DEB, FIN = date(2026, 1, 1), date(2026, 6, 30)


def _ouvrables(d0, d1):
    n, d = 0, d0
    while d <= d1:
        n += d.weekday() < 6
        d += timedelta(days=1)
    return n


def _scenario(env):
    t = env["t"]
    c2 = M.CategorieEmploi(tenant_id=t, code="C2", libelle="Employé", salaire_minimum=1)
    c3 = M.CategorieEmploi(tenant_id=t, code="C3", libelle="Agent de maîtrise", salaire_minimum=1)
    nord = M.Site(tenant_id=t, nom="Chantier Nord")
    db.session.add_all([c2, c3, nord])
    db.session.flush()
    a = db.session.get(M.Salarie, env["s"])            # H, 46 ans, 11 ans d'ancienneté, national, C2, Nord, CDI
    a.nom, a.prenom, a.sexe, a.date_naissance, a.date_embauche, a.nationalite, a.categorie_id = \
        "MOUSSAVOU", "Jean", "M", date(1980, 3, 10), date(2015, 1, 1), "GABONAISE", c2.id
    b = db.session.get(M.Salarie, env["autre"])        # F, 30 ans, entrée le 01/03/2026, non nationale, C3, CDD
    b.nom, b.prenom, b.sexe, b.date_naissance, b.date_embauche, b.nationalite, b.categorie_id = \
        "NDONG", "Alice", "F", date(1995, 7, 1), date(2026, 3, 1), "CAMEROUNAISE", c3.id
    c = M.Salarie(tenant_id=t, matricule="M7", nom="OBAME", prenom="Paul", sexe="M", date_naissance=date(1970, 1, 1),
                  date_embauche=date(2010, 1, 1), date_cessation=date(2026, 4, 30), type_rupture="DEMISSION")
    d = M.Salarie(tenant_id=t, matricule="M8", nom="ESSONO", prenom="X", date_embauche=date(2020, 6, 1))
    db.session.add_all([c, d])
    db.session.flush()
    db.session.add_all([
        M.Contrat(tenant_id=t, salarie_id=a.id, date_debut=date(2015, 1, 1), salaire_base=300000, type_contrat="CDI", actif=True),
        M.Contrat(tenant_id=t, salarie_id=b.id, date_debut=date(2026, 3, 1), salaire_base=200000, type_contrat="CDD", actif=True),
        M.AffectationSite(tenant_id=t, site_id=nord.id, salarie_id=a.id, date_debut=date(2025, 1, 1), actif=True),
        # absences : maladie de A du lundi 02/02 au retour samedi 07/02 → 5 jours ouvrables
        M.Conge(tenant_id=t, salarie_id=a.id, annee=2026, type_conge="MALADIE", statut="APPROUVÉ",
                date_depart=date(2026, 2, 2), date_retour=date(2026, 2, 7), jours_pris=5),
        M.Conge(tenant_id=t, salarie_id=d.id, annee=2026, type_conge="ANNUEL", statut="APPROUVÉ",
                date_depart=date(2026, 5, 4), date_retour=date(2026, 5, 9), jours_pris=5),        # congé payé : exclu
        M.Conge(tenant_id=t, salarie_id=d.id, annee=2026, type_conge="MALADIE", statut="DEMANDÉ",
                date_depart=date(2026, 5, 11), date_retour=date(2026, 5, 12)),                   # non accordé : exclu
        M.Pointage(tenant_id=t, salarie_id=a.id, date_pointage=date(2026, 2, 4), absent=True, motif_absence="MALADIE"),
        M.Pointage(tenant_id=t, salarie_id=a.id, date_pointage=date(2026, 3, 10), absent=True, motif_absence="SANS_MOTIF"),
        M.Pointage(tenant_id=t, salarie_id=a.id, date_pointage=date(2026, 3, 11), absent=True, motif_absence="CONGE"),
        M.Pointage(tenant_id=t, salarie_id=b.id, date_pointage=date(2026, 2, 10), absent=True, motif_absence="SANS_MOTIF"),
        M.Pointage(tenant_id=t, salarie_id=d.id, date_pointage=date(2026, 6, 2), absent=True, motif_absence="MALADIE",
                   statut="SOUMIS"),                                                             # non validé : exclu
    ])
    per = {m: _periode(t, 2026, m) for m in (1, 2, 3, 7)}

    def bul(sid, m, brut, statut="VALIDÉ"):
        db.session.add(M.BulletinPaie(tenant_id=t, salarie_id=sid, periode_id=per[m].id, salaire_base=brut,
                                      salaire_brut=brut, net_a_payer=brut * 0.8, cnss_patronale=brut * 0.1,
                                      cnamgs_patronale=brut * 0.04, fnh=brut * 0.01, cfp=brut * 0.005,
                                      tcs=brut * 0.01, statut=statut))
    bul(a.id, 1, 300000); bul(a.id, 2, 300000); bul(b.id, 3, 200000); bul(c.id, 1, 250000)
    bul(d.id, 1, 180000, "BROUILLON")                  # non validé : exclu
    bul(a.id, 7, 300000)                               # hors période : exclu
    db.session.commit()
    return a.id, b.id, c.id, d.id, nord.id, c3.id


def test_calcul_a_la_main(env):
    a, b, c, d, nord, c3 = _scenario(env)
    r = ir.calculer(db.session.get(M.Tenant, env["t"]), DEB, FIN)
    # effectifs : début (31/12/2025) A, C, D ; fin (30/06) A, B, D ; fins de mois 3, 3, 4, 3, 3, 3
    assert (r["effectif_debut"], r["effectif_fin"]) == (3, 3)
    assert [x["effectif"] for x in r["mouvements"]] == [3, 3, 4, 3, 3, 3]
    assert abs(r["effectif_moyen"] - 19 / 6) < 1e-9
    assert [s.id for s in r["entrees"]] == [b] and [s.id for s in r["sorties"]] == [c]
    assert r["motifs_sortie"] == [("DEMISSION", 1)]
    assert abs(r["rotation"] - (1 / (19 / 6) * 100)) < 1e-9                    # 31,58 %
    rep = r["repartitions"]
    assert dict(rep["Sexe"]) == {"Hommes": 1, "Femmes": 1, "Non renseigné": 1}
    assert dict(rep["Nationalité"]) == {"Nationaux": 2, "Non nationaux": 1}
    assert dict(rep["Type de contrat"]) == {"CDI": 1, "CDD": 1, "Sans contrat actif": 1}
    assert dict(rep["Site"]) == {"Chantier Nord": 1, "Sans site affecté": 2}
    assert dict(rep["Catégorie"]) == {"C2 — Employé": 1, "C3 — Agent de maîtrise": 1, "Sans catégorie": 1}
    # ancienneté au 30/06/2026 : A 11,5 ans, B 0,33, D 6,08
    assert dict(r["anciennete"]) == {"Moins d'1 an": 1, "1 à 3 ans": 0, "3 à 5 ans": 0, "5 à 10 ans": 1,
                                     "10 à 20 ans": 1, "20 ans et plus": 0}
    # âges : A 46 ans (45–49), B 30 ans (30–34) ; D sans date de naissance
    pyr = dict(r["pyramide"])
    assert pyr["45–49 ans"] == {"H": 1, "F": 0, "NR": 0} and pyr["30–34 ans"] == {"H": 0, "F": 1, "NR": 0}
    assert (r["age_moyen"], r["sans_age"]) == (38.0, 1)
    # absentéisme : 5 j maladie (le pointage du 04/02 n'est pas compté deux fois) + 1 j injustifié
    ab = r["absenteisme"]
    assert ab["jours"] == 6 and dict(ab["par_motif"]) == {"Maladie": 5, "Absence injustifiée": 1}
    theo = (_ouvrables(DEB, FIN) * 2                                              # A et D, toute la période
            + _ouvrables(DEB, date(2026, 4, 29))                                  # C jusqu'à la veille de sa sortie
            + _ouvrables(date(2026, 3, 1), FIN))                                  # B depuis son embauche
    assert ab["theoriques"] == theo and abs(ab["taux"] - 6 / theo * 100) < 1e-9
    assert [x["jours"] for x in ab["par_mois"]] == [0, 5, 1, 0, 0, 0]
    # masse : 4 bulletins validés de la période ; coût = brut × 1,165 (TCS comprise)
    m = r["masse"]
    assert (m["total"]["nb"], m["total"]["brut"]) == (4, 1050000)
    assert round(m["total"]["cout"]) == round(1050000 * 1.165)
    site = {x["libelle"]: x for x in m["par_site"]}
    assert round(site["Chantier Nord"]["cout"]) == round(600000 * 1.165)
    assert round(site["Sans site affecté"]["cout"]) == round(450000 * 1.165)
    cat = {x["libelle"]: x for x in m["par_categorie"]}
    assert (cat["C2 — Employé"]["brut_moyen"], cat["Sans catégorie"]["brut"]) == (300000, 250000)
    sexe = {x["libelle"]: x for x in m["par_sexe"]}
    assert (sexe["Hommes"]["brut"], sexe["Femmes"]["brut"]) == (850000, 200000)
    assert [round(x["brut"]) for x in m["par_mois"]] == [550000, 300000, 200000, 0, 0, 0]


def test_filtres_site_et_categorie(env):
    a, b, c, d, nord, c3 = _scenario(env)
    tn = db.session.get(M.Tenant, env["t"])
    r = ir.calculer(tn, DEB, FIN, site_id=nord)
    assert r["effectif_fin"] == 1 and r["masse"]["total"]["brut"] == 600000 and r["absenteisme"]["jours"] == 6
    r = ir.calculer(tn, DEB, FIN, categorie_id=c3)
    assert (r["effectif_fin"], len(r["entrees"]), r["masse"]["total"]["brut"]) == (1, 1, 200000)
    r = ir.calculer(tn, DEB, FIN, site_id=0)                    # sans site : B, D et C (sorti, sans site)
    assert r["effectif_fin"] == 2 and len(r["sorties"]) == 1


def test_pages_exports_cloisonnement(env):
    a, b, c, d, nord, c3 = _scenario(env)
    cli = _cli(env["admin"])
    page = re.sub(r"\s+", " ", cli.get("/indicateurs-rh?du=2026-01&au=2026-06").get_data(as_text=True))
    assert "Bilan social simplifié du 01/01/2026 au 30/06/2026" in page
    assert "Pyramide des âges" in page and "Taux d&#39;absentéisme" in page and "Chantier Nord" in page
    assert "31,6 %" in page                                                       # taux de rotation
    page = cli.get(f"/indicateurs-rh?du=2026-01&au=2026-06&site_id={nord}").get_data(as_text=True)
    assert "site : Chantier Nord" in page
    etat = cli.get("/indicateurs-rh/imprimer?du=2026-01&au=2026-06").get_data(as_text=True)
    assert "Bilan social simplifié — indicateurs RH" in etat and "Visa de la direction" in etat
    csv_ = cli.get("/indicateurs-rh/export.csv?du=2026-01&au=2026-06").get_data(as_text=True)
    assert "EFFECTIFS;Début;Fin" in csv_ and "Motif : Maladie;5" in csv_ and "DEMISSION" in csv_
    # période inversée : remise dans l'ordre ; défaut : année en cours
    assert "au 30/06/2026" in cli.get("/indicateurs-rh?du=2026-06&au=2026-01").get_data(as_text=True)
    assert cli.get("/indicateurs-rh").status_code == 200
    # autre entreprise : rien de cette entreprise, et un site étranger est ignoré
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
    page = c2.get(f"/indicateurs-rh?du=2026-01&au=2026-06&site_id={nord}").get_data(as_text=True)
    assert "MOUSSAVOU" not in page and "Chantier Nord" not in page and "NDONG" not in page
