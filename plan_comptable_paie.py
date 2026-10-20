# -*- coding: utf-8 -*-
"""Comptes du journal de paie (export comptable), paramétrables par entreprise.

Comptes proposés par défaut selon le SYSCOHADA révisé (classe 66 : charges de
personnel ; 6413 : taxes sur salaires ; 42, 43, 44 : dettes). Ce sont des
propositions : chaque entreprise les adapte à son plan de comptes
(Paramètres → Comptes du journal de paie). Les mêmes rubriques et les mêmes
comptes par défaut sont utilisés par Ameriack États Financiers, qui reprend
les comptes enregistrés ici via la liaison.

Écriture mensuelle (agrégée, toujours équilibrée) :
  Débit  salaires bruts (national / non national), éléments hors brut et
         arrondis (solde calculé), cotisations patronales, taxes patronales
  Crédit CNSS, CNAMGS, impôts retenus (IRPP, TCS), taxes patronales à payer
         (FNH, CFP), acomptes et prêts imputés, net à payer.
"""
from __future__ import annotations

import json
import re
import unicodedata

# clé, libellé, compte proposé, sens
RUBRIQUES = [
    ("brut_national", "Salaires bruts — personnel national", "661", "D"),
    ("brut_non_national", "Salaires bruts — personnel non national", "662", "D"),
    ("journaliers", "Journaliers (brut)", "661", "D"),
    ("hors_brut", "Éléments versés hors brut et arrondis", "663", "D"),
    ("charges_sociales", "Cotisations patronales CNSS et CNAMGS", "664", "D"),
    ("taxes_salaires", "Taxes patronales sur salaires (FNH, CFP)", "6413", "D"),
    ("cnss", "CNSS (parts salariale et patronale)", "431", "C"),
    ("cnamgs", "CNAMGS (parts salariale et patronale)", "433", "C"),
    ("impots_retenus", "IRPP et TCS retenus sur salaires", "447", "C"),
    ("taxes_dues", "FNH et CFP à payer", "442", "C"),
    ("avances", "Acomptes et retenues de prêt imputés", "421", "C"),
    ("net", "Net à payer (salariés et journaliers)", "422", "C"),
]
CLES = [r[0] for r in RUBRIQUES]
DEFAUTS = {r[0]: r[2] for r in RUBRIQUES}
JOURNAL_DEFAUT = "PAI"


def _lire(tenant) -> dict:
    brut = getattr(tenant, "comptes_paie", None)
    if not isinstance(brut, str) or not brut.strip():
        return {}
    try:
        d = json.loads(brut)
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def comptes(tenant) -> dict:
    d = _lire(tenant).get("comptes") or {}
    return {k: (d.get(k) or DEFAUTS[k]) for k in CLES}


def journal(tenant) -> str:
    return _lire(tenant).get("journal") or JOURNAL_DEFAUT


def personnalises(tenant) -> bool:
    return bool(_lire(tenant).get("comptes"))


def lire_formulaire(form) -> dict:
    out = {}
    for k, lib, _, _ in RUBRIQUES:
        v = (form.get(f"compte_{k}") or "").strip()
        if not re.fullmatch(r"\d{2,10}", v):
            raise ValueError(f"Compte invalide pour « {lib} » : 2 à 10 chiffres.")
        out[k] = v
    j = (form.get("journal") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,6}", j):
        raise ValueError("Code journal invalide : 1 à 6 lettres ou chiffres.")
    return {"comptes": out, "journal": j}


def est_national(nationalite) -> bool:
    t = unicodedata.normalize("NFKD", str(nationalite or "")).encode("ascii", "ignore").decode().upper()
    return any(m.startswith("GABON") for m in re.split(r"[^A-Z]+", t) if m)
