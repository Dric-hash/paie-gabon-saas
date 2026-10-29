"""Listes imprimées des salariés et des journaliers classées par site."""
import re
from datetime import date

from test_suppression_salarie import env, _cli  # noqa: F401
import models as M
from models import db


def _ordre(html, noms):
    pos = {n: html.find(n) for n in noms}
    assert all(v >= 0 for v in pos.values()), pos
    return [n for n, _ in sorted(pos.items(), key=lambda x: x[1])]


def _donnees(env):
    t = env["t"]
    owendo, akanda, ferme = M.Site(tenant_id=t, nom="Owendo"), M.Site(tenant_id=t, nom="Akanda"), \
        M.Site(tenant_id=t, nom="Zone fermée")
    db.session.add_all([owendo, akanda, ferme])
    db.session.flush()
    s0, s1 = db.session.get(M.Salarie, env["s"]), db.session.get(M.Salarie, env["autre"])
    s0.nom, s1.nom = "ZOLA", "ABAGA"
    s2 = M.Salarie(tenant_id=t, matricule="M9", nom="MBA", prenom="Awa", date_embauche=date(2024, 1, 1))
    j1 = M.Journalier(tenant_id=t, nom="NZE", prenom="Paul", taux_horaire=1000, statut="ACTIF")
    j2 = M.Journalier(tenant_id=t, nom="BIYOGO", prenom="Luc", taux_horaire=1000, statut="ACTIF")
    db.session.add_all([s2, j1, j2])
    db.session.flush()
    db.session.add_all([
        M.AffectationSite(tenant_id=t, site_id=owendo.id, salarie_id=s0.id, date_debut=date(2025, 1, 1)),
        # ABAGA : ancienne affectation à Owendo terminée, active à Akanda
        M.AffectationSite(tenant_id=t, site_id=owendo.id, salarie_id=s1.id, date_debut=date(2024, 1, 1), actif=False),
        M.AffectationSite(tenant_id=t, site_id=akanda.id, salarie_id=s1.id, date_debut=date(2025, 6, 1)),
        M.AffectationSite(tenant_id=t, site_id=owendo.id, journalier_id=j1.id, date_debut=date(2025, 1, 1)),
    ])
    db.session.commit()
    return {"owendo": owendo.id, "akanda": akanda.id}


def test_salaries_par_site(env):
    ids = _donnees(env)
    cli = _cli(env["admin"])
    h = cli.get("/salaries/imprimer").get_data(as_text=True)
    assert "classée par site" in h
    # sites par ordre alphabétique, sans site en dernier ; Zone fermée (vide) absente
    assert _ordre(h, ['class="site">Akanda', 'class="site">Owendo', 'class="site">Sans site affecté']) == \
        ['class="site">Akanda', 'class="site">Owendo', 'class="site">Sans site affecté']
    assert "Zone fermée" not in h.split("</form>")[-1]
    assert _ordre(h, ["ABAGA", "ZOLA", "MBA Awa"]) == ["ABAGA", "ZOLA", "MBA Awa"]
    assert h.count("<table>") == 3
    # un seul site, une page par site, ou classement alphabétique
    h = cli.get(f"/salaries/imprimer?site={ids['owendo']}").get_data(as_text=True).split("</form>")[-1]
    assert "ZOLA" in h and "ABAGA" not in h and "MBA Awa" not in h and "classée par site — Owendo" in h
    h = cli.get("/salaries/imprimer?site=0").get_data(as_text=True).split("</form>")[-1]
    assert "MBA Awa" in h and "ZOLA" not in h
    assert cli.get("/salaries/imprimer?saut=1").get_data(as_text=True).count("site-bloc saut") == 2
    h = cli.get("/salaries/imprimer?classement=alpha").get_data(as_text=True)
    assert 'class="site"' not in h and h.count("<table>") == 1
    assert _ordre(h, ["ABAGA", "MBA Awa", "ZOLA"]) == ["ABAGA", "MBA Awa", "ZOLA"]


def test_journaliers_par_site(env):
    _donnees(env)
    h = _cli(env["admin"]).get("/journaliers/imprimer").get_data(as_text=True)
    assert _ordre(h, ['class="site">Owendo', "NZE Paul", 'class="site">Sans site affecté', "BIYOGO Luc"]) == \
        ['class="site">Owendo', "NZE Paul", 'class="site">Sans site affecté', "BIYOGO Luc"]
    assert re.search(r"Owendo <span>· 1 journalier\(s\)", h)
