# -*- coding: utf-8 -*-
"""Tests — Grille de salaires conventionnelle (aérien) + pré-remplissage."""
import json
import calculs_paie as c


def test_seed_structure():
    seed = c.grille_salaire_aerien_seed()
    assert len(seed) == 11                       # 11 catégories EI..CII
    assert set(["EI", "EII", "EVII", "MI", "MII", "CI", "CII"]).issubset(seed.keys())
    assert seed["EI"]["1"] == 101526             # 1er échelon EI (repère lisible)
    assert len(seed["EI"]) == 10                 # 10 échelons pour EI


def test_seed_categories_liste():
    codes = [code for code, _ in c.GRILLE_CATEGORIES_AERIEN]
    assert codes == ["EI", "EII", "EIII", "EIV", "EV", "EVI", "EVII", "MI", "MII", "CI", "CII"]


def test_seed_montants_croissants_par_echelon():
    # Dans chaque catégorie, le salaire doit croître avec l'échelon (cohérence).
    seed = c.grille_salaire_aerien_seed()
    for code, mont in seed.items():
        vals = [mont[str(i)] for i in range(1, len(mont) + 1) if str(i) in mont]
        assert vals == sorted(vals), f"Montants non croissants pour {code}"


def test_grille_json_roundtrip():
    # La grille se sérialise/désérialise sans perte (comme en base).
    g = {"EI": {"1": 101526.0, "2": 110970.0}, "CII": {"1": 1335725.0}}
    s = json.dumps(g)
    assert json.loads(s) == g
