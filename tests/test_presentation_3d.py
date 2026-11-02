"""Page de présentation : le calculateur public passe par le moteur de paie (plus de copie du barème
en JavaScript, TCS comprise) et la 3D respecte les garde-fous (version de Three.js figée, préférence
« moins d'animations », pas d'émoji). Bug du 31/10/2026 : le calculateur ignorait la TCS et
annonçait 431 100 FCFA de net pour 500 000 de brut au lieu de 417 950."""
import re

import pytest

from test_suppression_salarie import env, flask_app  # noqa: F401
from calculs_paie import calculer_bulletin
from blueprints.auth import estimation_net


def _api(cli, brut):
    return cli.get(f"/api/estimation-net?brut={brut}")


@pytest.mark.parametrize("brut", [0, 80000, 150000, 250000, 500000, 800000, 1200000, 2000000, 3000000, 10000000])
def test_estimation_egale_au_moteur(env, brut):
    cli = flask_app.test_client()
    rep = _api(cli, brut)
    assert rep.status_code == 200
    e = rep.get_json()
    r = calculer_bulletin({"salaire_base": float(brut)}, nb_parts=1.0)
    assert e == {"brut": brut, "cnss": round(r["cnss_salarie"]), "cnamgs": round(r["cnamgs_salarie"]),
                 "tcs": round(r["tcs"]), "irpp": round(r["irpp"]), "net": round(r["net_a_payer"])}
    # Les lignes affichées se retrouvent au franc près
    assert e["brut"] - e["cnss"] - e["cnamgs"] - e["tcs"] - e["irpp"] == e["net"]
    assert "max-age" in rep.headers["Cache-Control"]


def test_valeurs_de_reference_500000(env):
    e = estimation_net(500000)
    assert e == {"brut": 500000, "cnss": 25000, "cnamgs": 10000, "tcs": 15750, "irpp": 31300, "net": 417950}


@pytest.mark.parametrize("brut", ["", "abc", "-1", "10000001", "inf", "nan", "1e40"])
def test_estimation_refuse_les_valeurs_invalides(env, brut):
    rep = _api(flask_app.test_client(), brut)
    assert rep.status_code == 400
    assert "erreur" in rep.get_json()


def _page():
    rep = flask_app.test_client().get("/")
    assert rep.status_code == 200
    return rep.get_data(as_text=True)


def test_page_valeurs_initiales_du_moteur(env):
    page = _page()
    e = estimation_net(500000)
    f = lambda n: f"{n:,}".replace(",", " ")  # noqa: E731
    for ident, cle in (("calc-cnss", "cnss"), ("calc-cnamgs", "cnamgs"), ("calc-tcs", "tcs"), ("calc-irpp", "irpp")):
        assert re.search(rf'id="{ident}">{f(e[cle])}<', page), ident
    assert f'id="calc-net" aria-live="polite"' in page and f'{f(e["net"])} FCFA' in page
    assert "– TCS" in page
    assert "/api/estimation-net?brut=" in page
    assert "431 100" not in page


def test_page_sans_copie_du_bareme(env):
    page = _page()
    assert "BAREME" not in page and "function irpp" not in page


def test_page_3d_garde_fous(env):
    page = _page()
    # Three.js figé sur une version précise
    assert re.findall(r"three@([\d.]+)/", page) == ["0.160.0"]
    assert "ShaderMaterial" in page
    # Pause hors écran / onglet caché, contexte perdu géré, préférence « moins d'animations »
    for attendu in ("IntersectionObserver", "visibilitychange", "webglcontextlost", "prefers-reduced-motion: reduce",
                    "ResizeObserver", "pointer: fine"):
        assert attendu in page, attendu
    bloc = page[page.index("@media (prefers-reduced-motion: reduce){\n    .scene-3d"):]
    bloc = bloc[:bloc.index("}\n  }") + 4]
    assert ".hero3d{display:none}" in bloc and ".reflet::after{display:none}" in bloc
    # Aucune émoji (icônes SVG uniquement)
    assert not re.search("[\U0001F300-\U0001FAFF☀-⛿]", page)


def test_css_de_la_page_bien_forme(env):
    """Une accolade de trop dans le <style> casse toute la mise en page qui suit (vu le 31/10/2026)."""
    page = _page()
    css = page[page.index("<style"):page.index("</style>")]
    css = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", css)
    prof = 0
    for ch in css:
        prof += {"{": 1, "}": -1}.get(ch, 0)
        assert prof >= 0
    assert prof == 0


def test_3d_dans_toutes_les_sections(env):
    page = _page()
    for section in ("comment", "fonctions", "conventions", "tarifs"):
        bloc = page[page.index(f'id="{section}"'):]
        bloc = bloc[:bloc.index("</section>")]
        assert 'c3d reflet' in bloc, section
    conv = page[page.index('id="conventions"'):]
    assert conv[:conv.index("</section>")].count("conv-carte") == 11     # 10 conventions + « Et la vôtre ? »
    assert page.count("secu-carte c3d") == 4
    assert 'class="final-scene" aria-hidden="true"' in page
    bloc = page[page.index("@media (prefers-reduced-motion: reduce){\n    .scene-3d"):]
    bloc = bloc[:bloc.index("}\n  }") + 4]
    for attendu in (".c3d.visible.inclinee{transform:none}", ".conv-carte.reveal,.secu-carte.reveal{transform:none}",
                    ".final-scene{display:none}"):
        assert attendu in bloc, attendu
