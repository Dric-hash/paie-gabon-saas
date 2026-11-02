"""Impression de la paie des journaliers : heures normales, supplémentaires et du dimanche
affichées séparément (affichage seulement, taux et montants inchangés)."""
from datetime import date

from test_suppression_salarie import env, _cli, flask_app  # noqa: F401
import models as M
from models import db

PERIODE = {"date_debut": "2026-10-12", "date_fin": "2026-10-18"}     # lundi 12 → dimanche 18


def _donnees(env):
    t = db.session.get(M.Tenant, env["t"])
    site = M.Site(tenant_id=t.id, nom="Chantier Akanda", actif=True)
    db.session.add(site)
    db.session.flush()
    js = [M.Journalier(tenant_id=t.id, nom=n, prenom="X", taux_horaire=tx, statut="ACTIF")
          for n, tx in (("MBA", 750), ("NDONG", 700), ("OBAME", 600))]
    db.session.add_all(js)
    db.session.flush()
    for j in js:
        db.session.add(M.AffectationSite(tenant_id=t.id, site_id=site.id, journalier_id=j.id, actif=True,
                                         date_debut=date(2026, 10, 1)))

    def pt(j, d, hn, hs=0, tj="NORMAL"):
        db.session.add(M.Pointage(tenant_id=t.id, journalier_id=j.id, date_pointage=date(2026, 10, d),
                                  heures_normales=hn, heures_sup=hs, type_jour=tj, present=True,
                                  absent=False, statut="VALIDE"))
    for d in range(12, 17):
        pt(js[0], d, 8, 2 if d in (13, 15) else 0)
    pt(js[0], 17, 4)                       # samedi : heures normales
    pt(js[0], 18, 0, 6, "DIMANCHE")        # dimanche saisi en individuel (rangé en sup.)
    for d in range(12, 17):
        pt(js[1], d, 8, 1)
    pt(js[1], 18, 5, 0)                    # dimanche saisi par le pointage de chantier (en normales)
    for d in range(12, 17):
        pt(js[2], d, 8)
    db.session.commit()
    cli = _cli(env["admin"])
    cli.post("/journaliers/paie/generer", data=dict(PERIODE, journalier_ids=[str(js[0].id), str(js[1].id)]))
    cli.post("/journaliers/paie/generer",
             data=dict(PERIODE, journalier_ids=[str(js[2].id)], **{f"heures_{js[2].id}": "42"}))
    return cli, js


def test_detail_des_heures(env):
    with flask_app.app_context():
        _, js = _donnees(env)
        from blueprints.tenant.journaliers import _detail_heures_feuilles
        t = db.session.get(M.Tenant, env["t"])
        fs = {f.journalier_id: f for f in M.FeuillePaieJournalier.query.all()}
        d = _detail_heures_feuilles(t, list(fs.values()))
        assert d[fs[js[0].id].id] == {"normales": 44.0, "sup": 4.0, "dimanche": 6.0, "fiable": True}
        assert d[fs[js[1].id].id] == {"normales": 40.0, "sup": 5.0, "dimanche": 5.0, "fiable": True}
        # Heures saisies à la main (42 h) ≠ pointage (40 h) : partage enregistré, signalé
        assert d[fs[js[2].id].id] == {"normales": 42.0, "sup": 0.0, "dimanche": 0.0, "fiable": False}
        # Montants inchangés : heures totales × taux horaire
        assert [float(fs[j.id].montant_brut) for j in js] == [54 * 750, 50 * 700, 42 * 600]


def test_impressions(env):
    with flask_app.app_context():
        cli, _ = _donnees(env)
        q = "?date_debut=2026-10-12&date_fin=2026-10-18"
        for url in ("/journaliers/paie/imprimer" + q, "/journaliers/paie/imprimer-sites" + q):
            page = cli.get(url).get_data(as_text=True)
            for attendu in ("H. normales", "H. sup.", "H. dimanche", "Total h", "Total des heures",
                            "126.0", "9.0", "11.0", "146.0", "100700"):
                assert attendu in page, (url, attendu)
            assert 'class="ast">*</sup>' in page and "Détail non disponible" in page
