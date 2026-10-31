"""Menu latéral de l'espace entreprise (tenant/base.html) : regroupé par domaine, chaque page une seule
fois, les pages récentes présentes, la page active marquée et son groupe identifiable."""
import re

from test_suppression_salarie import env, _cli  # noqa: F401
import models as M
from models import db

GROUPES = ["paie", "reglements", "personnel", "conges", "terrain", "tiers", "pilotage", "compte"]
PAGES_RECENTES = ["/paie/controle", "/virements", "/rappels", "/provisions-conges", "/budget-masse-salariale",
                  "/indicateurs-rh", "/prets"]


def _nav(cli, url):
    page = cli.get(url).get_data(as_text=True)
    return page[page.index("<nav"):page.index("</nav>")]


def test_menu_regroupe_sans_doublon(env):
    cli = _cli(env["admin"])
    nav = _nav(cli, "/dashboard")
    assert re.findall(r'data-groupe="([a-z]+)"', nav) == GROUPES
    liens = re.findall(r'<a href="(/[^"]*)"', nav)
    doublons = {h for h in liens if liens.count(h) > 1 and not h.startswith("/langue/")}
    assert doublons == set(), f"liens en double dans le menu : {doublons}"
    for href in PAGES_RECENTES:
        assert f'href="{href}"' in nav, f"{href} absent du menu"


def test_page_active_marquee(env):
    cli = _cli(env["admin"])
    for url, groupe in (("/bulletins", "paie"), ("/periodes", "paie"), ("/indicateurs-rh", "pilotage"),
                        ("/provisions-conges", "conges"), ("/prets", "reglements"), ("/sites", "terrain"),
                        ("/parametres", "compte"), ("/dashboard", None)):
        nav = _nav(cli, url)
        actifs = re.findall(r'<a href="(/[^"]*)"[^>]*nav-actif[^>]*aria-current="page"', nav.replace("\n", " "))
        assert actifs == [url], (url, actifs)
        if groupe:
            bloc = nav[nav.index(f'data-groupe="{groupe}"'):]
            bloc = bloc[:bloc.index("</details>")]
            assert f'href="{url}"' in bloc and "nav-actif" in bloc
    # la page Congés n'allume pas aussi « Provision congés » (et inversement)
    nav = _nav(cli, "/conges")
    assert re.findall(r'<a href="(/[^"]*)"[^>]*nav-actif', nav.replace("\n", " ")) == ["/conges"]


def test_droits_menu(env):
    """Un utilisateur en lecture seule ne voit ni Utilisateurs ni Journal d'audit."""
    lec = M.Utilisateur(nom="L", prenom="L", email="l@a.ga", role="LECTURE", tenant_id=env["t"], actif=True,
                        email_verifie=True)
    lec.set_password("x")
    db.session.add(lec)
    db.session.commit()
    from flask import g
    g.pop("_login_user", None)
    nav = _nav(_cli(lec.id), "/dashboard")
    assert 'href="/utilisateurs"' not in nav and 'href="/audit"' not in nav and 'href="/parametres"' in nav
