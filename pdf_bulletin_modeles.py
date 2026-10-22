# -*- coding: utf-8 -*-
"""pdf_bulletin_modeles.py — PDF des modèles de bulletin « Moderne »,
« Minimaliste » et « Grand livre » (reportlab), fidèles aux aperçus HTML
templates/tenant/bulletin_print_{moderne,minimaliste,grandlivre}.html.

Les trois mises en page reposent sur UNE extraction commune (donnees()) : les
montants sont identiques d'un modèle à l'autre. Les taux de cotisation ne sont
jamais écrits en dur : ils sont recalculés à partir des montants et des
assiettes du bulletin (montant / assiette) ; l'IRPP affiche « Barème ».
"""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (KeepInFrame, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

MODELES = ("moderne", "minimaliste", "grandlivre")


# ── Données communes ────────────────────────────────────────────────────────

def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _m(v, zero="") -> str:
    """Montant FCFA : 1 234 567 FCFA ; zero si nul."""
    n = _f(v)
    if n == 0:
        return zero
    return f"{int(round(n)):,}".replace(",", " ") + " FCFA"


def _n(v) -> str:
    n = _f(v)
    return f"{int(round(n)):,}".replace(",", " ") if n else ""


def _taux(part, base) -> str:
    p, b = _f(part), _f(base)
    if not p or not b:
        return ""
    t = p / b * 100
    return (f"{t:.2f}".rstrip("0").rstrip(".") + " %").replace(".", ",")


def _txt(v) -> str:
    from xml.sax.saxutils import escape
    return escape(str(v)) if v not in (None, "") else ""


# (libellé, champ montant, champ base, champ taux) — gains du salaire brut, dans
# l'ordre du modèle Grand livre (le plus complet)
_GAINS = [
    ("Salaire de base", "salaire_base", "base_salaire_base", "taux_salaire_base"),
    ("Heures supplémentaires +10%", "heures_sup_10", "base_heures_sup_10", "taux_heures_sup_10"),
    ("Heures supplémentaires +30%", "heures_sup_30", "base_heures_sup_30", "taux_heures_sup_30"),
    ("Heures supplémentaires +30% (repos/férié)", "heures_sup_30b", "base_heures_sup_30b", "taux_heures_sup_30b"),
    ("Heures supplémentaires +40%", "heures_sup_40", "base_heures_sup_40", "taux_heures_sup_40"),
    ("Heures supplémentaires +70%", "heures_sup_70", "base_heures_sup_70", "taux_heures_sup_70"),
    ("Heures sup férié chômé payé — jour", "heures_sup_fj", "base_heures_sup_fj", "taux_heures_sup_fj"),
    ("Heures sup férié chômé payé — nuit", "heures_sup_fn", "base_heures_sup_fn", "taux_heures_sup_fn"),
    ("Sursalaire", "sursalaire", "base_sursalaire", "taux_sursalaire"),
    ("Prime de caisse", "prime_caisse", "base_prime_caisse", "taux_prime_caisse"),
    ("Carburant", "carburant", "base_carburant", "taux_carburant"),
    ("Prime d'ancienneté", "prime_anciennete", "base_prime_anciennete", "taux_prime_anciennete"),
    ("Prime de nuit", "prime_nuit", None, None),
    ("Indemnité de logement", "indem_logement", "base_indem_logement", "taux_indem_logement"),
    ("Indemnité de domesticité", "indem_domesticite", "base_indem_domesticite", "taux_indem_domesticite"),
    ("Indemnité eau & électricité", "indem_eau_electricite", "base_indem_eau_electricite", "taux_indem_eau_electricite"),
    ("Indemnité de nourriture", "indem_nourriture", "base_indem_nourriture", "taux_indem_nourriture"),
    ("Prime de transport", "prime_transport", "base_prime_transport", "taux_prime_transport"),
    ("Prime de responsabilité", "prime_responsabilite", "base_prime_responsabilite", "taux_prime_responsabilite"),
    ("Prime de rendement", "prime_rendement", "base_prime_rendement", "taux_prime_rendement"),
    ("Prime d'assiduité", "prime_assiduité", "base_prime_assiduité", "taux_prime_assiduité"),
    ("Prime de qualité", "prime_qualite", "base_prime_qualite", "taux_prime_qualite"),
    ("Prime de performance", "prime_performance", "base_prime_performance", "taux_prime_performance"),
    ("Allocations de congé", "allocations_conge", "base_allocations_conge", "taux_allocations_conge"),
    ("Indem. compensatrice congé", "indem_compensatrice_conge", "base_indem_compensatrice_conge",
     "taux_indem_compensatrice_conge"),
    ("Indem. de services rendus", "indem_services_rendus", "base_indem_services_rendus", "taux_indem_services_rendus"),
    ("Indem. compensatrice préavis", "indem_compensatrice_preavis", "base_indem_compensatrice_preavis",
     "taux_indem_compensatrice_preavis"),
    ("Indemnité de licenciement", "indem_licenciement", "base_indem_licenciement", "taux_indem_licenciement"),
]
# Éléments versés en plus du salaire net (hors brut), comme dans le modèle Grand livre
_APRES_NET = [
    ("Prime de panier", "prime_panier"),
    ("Indemnité de transport (exonérée)", "indem_transport"),
    ("Indemnité de représentation", "indem_representation"),
    ("Prime de salissure", "prime_salisure"),
]


def donnees(b, t) -> dict:
    """Toutes les valeurs affichées par les trois modèles, calculées une fois."""
    s, p = b.salarie, b.periode
    g = lambda champ: getattr(b, champ, None) if champ else None   # noqa: E731
    gains = []
    for lib, champ, cb, ct in _GAINS:
        if _f(g(champ)) > 0:
            gains.append({"lib": lib, "base": _n(g(cb)), "taux": _txt(g(ct) or ""), "gain": g(champ), "ret": None})
    if _f(b.absences) > 0:
        gains.append({"lib": "Absences", "base": _n(getattr(b, "base_absences", None)),
                      "taux": _txt(getattr(b, "taux_absences", "") or ""), "gain": None, "ret": b.absences})
    try:
        from models import BulletinComposant
        comps = BulletinComposant.query.filter_by(bulletin_id=b.id).all()
    except Exception:
        comps = []
    for c in comps:
        est_gain = getattr(c, "est_gain", str(c.sens).upper() == "GAIN")
        gains.append({"lib": c.libelle + ("" if est_gain else " (retenue)"),
                      "base": _n(c.base), "taux": ("%g" % _f(c.taux)).replace(".", ",") if _f(c.taux) else "",
                      "gain": c.montant if est_gain else None, "ret": None if est_gain else c.montant})
    cot_sal = [
        ("CNSS", b.base_cnss, _taux(b.cnss_salarie, b.base_cnss), b.cnss_salarie),
        ("CNAMGS", b.base_cnamgs, _taux(b.cnamgs_salarie, b.base_cnamgs), b.cnamgs_salarie),
        ("TCS", b.base_tcs, _taux(b.tcs, b.base_tcs), b.tcs),
        ("IRPP", b.base_irpp, "Barème", b.irpp),
    ]
    cot_pat = [
        ("CNSS", b.base_cnss, _taux(b.cnss_patronale, b.base_cnss), b.cnss_patronale),
        ("CNAMGS", b.base_cnamgs, _taux(b.cnamgs_patronale, b.base_cnamgs), b.cnamgs_patronale),
        ("FNH", b.base_cnss, _taux(b.fnh, b.base_cnss), b.fnh),
        ("CFP", b.salaire_brut, _taux(b.cfp, b.salaire_brut), b.cfp),
    ]
    tot_sal = sum(_f(x[3]) for x in cot_sal)
    tot_pat = sum(_f(x[3]) for x in cot_pat)
    apres_net = [(lib, getattr(b, ch, None)) for lib, ch in _APRES_NET if _f(getattr(b, ch, None)) > 0]
    if _f(b.acompte) > 0:
        apres_net.append(("Acompte", -_f(b.acompte)))
    if _f(getattr(b, "ajustement_arrondi", 0)) != 0:
        apres_net.append(("Ajustement d'arrondi", _f(b.ajustement_arrondi)))
    try:
        from montant_lettres import nombre_en_lettres
        lettres = nombre_en_lettres(int(round(_f(b.net_a_payer))))
    except Exception:
        lettres = ""
    cat = s.categorie if getattr(s, "categorie", None) else None
    adr = []
    if getattr(t, "adresse", None):
        adr.append(t.adresse + (f" — BP {t.boite_postale}" if getattr(t, "boite_postale", None) else ""))
    ville = ", ".join(x for x in (getattr(t, "ville", None), getattr(t, "pays", None)) if x)
    if ville or getattr(t, "telephone", None):
        adr.append(ville + (f" — Tél : {t.telephone}" if getattr(t, "telephone", None) else ""))
    ids = " — ".join(x for x in (f"NIF : {t.nif}" if getattr(t, "nif", None) else "",
                                 f"N° CNSS : {t.numero_cnss}" if getattr(t, "numero_cnss", None) else "") if x)
    if ids:
        adr.append(ids)
    return {
        "b": b, "s": s, "p": p, "t": t,
        "entreprise": t.denomination or "", "activite": getattr(t, "activite", None) or "", "adresse": adr,
        "periode": p.libelle_complet if p else "", "numero": getattr(b, "numero", None) or "",
        "jours": b.nb_jours_travailles or "", "statut": b.statut or "",
        "date_edition": (b.date_creation or datetime.now()).strftime("%d/%m/%Y"),
        "nom": f"{s.nom or ''} {s.prenom or ''}".strip(), "matricule": s.matricule or "",
        "emploi": s.emploi or "—", "categorie": (f"{cat.code} — {cat.libelle}" if cat else "—"),
        "categorie_code": cat.code if cat else "—",
        "embauche": s.date_embauche.strftime("%d/%m/%Y") if s.date_embauche else "—",
        "cnss": s.numero_cnss or "—", "cnamgs": s.numero_cnamgs or "—", "nationalite": s.nationalite or "—",
        "situation": s.situation_matrimoniale or "—",
        "enfants": f"{s.nb_enfants or 0} (dont {s.nb_enfants_moins_16ans or 0} < 16 ans)",
        "parts": ("%g" % _f(s.nombre_parts or 1)).replace(".", ","),
        "assujetti_cnamgs": "OUI" if getattr(s, "assujetti_cnamgs", True) else "NON",
        "gains": gains, "brut": b.salaire_brut, "cot_sal": cot_sal, "cot_pat": cot_pat,
        "tot_sal": tot_sal, "tot_pat": tot_pat, "net_salaire": b.salaire_net, "apres_net": apres_net,
        "net": b.net_a_payer, "lettres": lettres,
    }


def _logo(t, max_w, max_h):
    try:
        from documents_rh import _logo_flowable
        return _logo_flowable(t, max_w=max_w, max_h=max_h)
    except Exception:
        return None


def _style(nom, taille=9, gras=False, couleur="#111111", align=TA_LEFT, police=None, interligne=None):
    return ParagraphStyle(nom, fontName=police or ("Helvetica-Bold" if gras else "Helvetica"), fontSize=taille,
                          leading=interligne or taille * 1.25, textColor=HexColor(couleur), alignment=align)


def P(texte, taille=9, gras=False, couleur="#111111", align=TA_LEFT, police=None):
    return Paragraph(str(texte), _style(f"s{taille}{gras}{couleur}{align}{police}", taille, gras, couleur, align, police))


W = 186 * mm   # largeur utile (A4 − 2 × 12 mm)


# ── Modèle MODERNE ──────────────────────────────────────────────────────────
# Bandeau bleu nuit #1a2332, bande salarié bleutée, en-têtes de section gris
# anthracite, lignes de total sur fond nuit, net à payer en vert d'eau.

def _moderne(d):
    NUIT, ANTH, BLEU, GRIS, FILET = "#1a2332", "#374151", "#2563eb", "#6b7280", "#e5e7eb"
    el = []
    gauche = []
    lg = _logo(d["t"], 40 * mm, 12 * mm)
    if lg:
        gauche.append(lg)
    gauche.append(P(_txt(d["entreprise"]).upper(), 14, True, "#ffffff"))
    for ligne in d["adresse"]:
        gauche.append(P(_txt(ligne), 7.5, False, "#c7cbd3"))
    droite = [P("BULLETIN DE PAIE", 13, True, "#ffffff", TA_RIGHT),
              P(_txt(d["periode"]), 8.5, False, "#dfe3ea", TA_RIGHT)]
    if d["numero"]:
        droite.append(P("N° " + _txt(d["numero"]), 8, False, "#dfe3ea", TA_RIGHT))
    h = Table([[gauche, droite]], colWidths=[W * 0.6, W * 0.4])
    h.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(NUIT)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                           ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    el.append(h)
    # Bande salarié
    band = Table([[[P(_txt(d["nom"]), 12, True, NUIT), P(_txt(d["emploi"]) + " · " + _txt(d["categorie"]), 7.5, False, GRIS)],
                   [P(_txt(d["matricule"]), 10, True, BLEU, TA_RIGHT, "Courier-Bold"),
                    P("Embauché le " + d["embauche"] + "<br/>CNSS : " + _txt(d["cnss"]), 7.5, False, GRIS, TA_RIGHT)]]],
                 colWidths=[W * 0.6, W * 0.4])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor("#f0f4ff")),
                              ("LINEBEFORE", (0, 0), (0, 0), 3, HexColor(NUIT)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                              ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    el.append(band)

    def cellule(lbl, val):
        return [P(lbl.upper(), 6.5, True, "#9ca3af"), P(_txt(val), 8.5, True, NUIT)]
    grille = Table([[cellule("Situation familiale", d["situation"]), cellule("Nb enfants", d["enfants"])],
                    [cellule("Jours travaillés", f"{d['jours']} jours" if d["jours"] else "—"),
                     cellule("N° CNAMGS", d["cnamgs"])]], colWidths=[W / 2, W / 2])
    grille.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, HexColor(FILET)),
                                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 4),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    el.append(grille)

    def section(titre):
        tb = Table([[P(titre.upper(), 7.5, True, "#ffffff")]], colWidths=[W])
        tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(ANTH)), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        return [Spacer(1, 2 * mm), tb]

    def tableau(lignes, largeurs, n_total=1):
        tb = Table(lignes, colWidths=largeurs, repeatRows=1)
        st = [("BACKGROUND", (0, 0), (-1, 0), HexColor("#f9fafb")), ("LINEBELOW", (0, 0), (-1, 0), 1.2, HexColor(FILET)),
              ("LINEBELOW", (0, 1), (-1, -1 - n_total), 0.4, HexColor("#f3f4f6")),
              ("BACKGROUND", (0, -n_total), (-1, -1), HexColor(NUIT)),
              ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
              ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        tb.setStyle(TableStyle(st))
        return tb

    th = lambda x, a=TA_LEFT: P(x.upper(), 6.5, True, GRIS, a)   # noqa: E731
    el += section("Éléments de rémunération (gains)")
    lignes = [[th("Désignation"), th("Base", TA_RIGHT), th("Taux", TA_CENTER), th("Gains (FCFA)", TA_RIGHT),
               th("Retenues (FCFA)", TA_RIGHT)]]
    for g in d["gains"]:
        lignes.append([P(_txt(g["lib"]), 8.5), P(g["base"] or "—", 8, False, GRIS, TA_RIGHT),
                       P(g["taux"] or "—", 8, False, GRIS, TA_CENTER), P(_m(g["gain"]), 8.5, False, "#111111", TA_RIGHT),
                       P(_m(g["ret"]), 8.5, False, "#dc2626", TA_RIGHT)])
    lignes.append([P("SALAIRE BRUT", 8.5, True, "#ffffff"), "", "", P(_m(d["brut"], "0 FCFA"), 8.5, True, "#ffffff", TA_RIGHT), ""])
    tb = tableau(lignes, [W * 0.40, W * 0.16, W * 0.10, W * 0.17, W * 0.17])
    tb.setStyle(TableStyle([("SPAN", (0, -1), (2, -1))]))
    el.append(tb)

    el += section("Cotisations sociales & fiscales")
    lignes = [[th("Désignation"), th("Base", TA_RIGHT), th("Taux sal.", TA_CENTER), th("Salarié", TA_RIGHT),
               th("Taux pat.", TA_CENTER), th("Patronal", TA_RIGHT)]]
    pat = {x[0]: x for x in d["cot_pat"]}
    for lib, base, tx, mt in d["cot_sal"]:
        tp = pat.get(lib)
        lignes.append([P(lib, 8.5), P(_n(base) or "—", 8, False, GRIS, TA_RIGHT), P(tx or "—", 8, False, GRIS, TA_CENTER),
                       P(_m(mt, "0 FCFA"), 8.5, False, "#dc2626", TA_RIGHT),
                       P((tp[2] if tp else "") or ("—" if tp else ""), 8, False, GRIS, TA_CENTER),
                       P(_m(tp[3]) if tp else "", 8.5, False, "#1d4ed8", TA_RIGHT)])
    for lib in ("FNH", "CFP"):
        tp = pat[lib]
        if _f(tp[3]) > 0:
            lignes.append([P(lib, 8.5), P(_n(tp[1]) or "—", 8, False, GRIS, TA_RIGHT), P("", 8), P("", 8),
                           P(tp[2] or "—", 8, False, GRIS, TA_CENTER), P(_m(tp[3]), 8.5, False, "#1d4ed8", TA_RIGHT)])
    lignes.append([P("TOTAL RETENUES SALARIALES", 7.5, True, "#ffffff"), "", "",
                   P(_m(d["tot_sal"], "0 FCFA"), 8.5, True, "#ffffff", TA_RIGHT),
                   P("CHARGES PATRONALES", 7, True, "#ffffff", TA_RIGHT), P(_m(d["tot_pat"], "0 FCFA"), 8.5, True, "#ffffff", TA_RIGHT)])
    tb = tableau(lignes, [W * 0.17, W * 0.17, W * 0.11, W * 0.19, W * 0.17, W * 0.19])
    tb.setStyle(TableStyle([("SPAN", (0, -1), (2, -1))]))
    el.append(tb)

    # Net à payer
    detail = [P("NET À PAYER", 11, True, "#ffffff")]
    det = [f"Salaire net : {_m(d['net_salaire'], '0 FCFA')}"]
    det += [f"{lib} : {'+' if v > 0 else '−'} {_m(abs(_f(v)))}" for lib, v in d["apres_net"]]
    detail.append(P(" · ".join(det), 7, False, "#c7cbd3"))
    net = Table([[detail, P(_m(d["net"], "0 FCFA"), 18, True, "#6ee7b7", TA_RIGHT, "Courier-Bold")]],
                colWidths=[W * 0.58, W * 0.42])
    net.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(NUIT)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                             ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    el += [Spacer(1, 2.5 * mm), net]
    if d["lettres"]:
        el.append(P(f"Arrêté à la somme de : {_txt(d['lettres'])} francs CFA.", 7.5, False, GRIS))
    pied = Table([[P(f"Document confidentiel — {_txt(d['entreprise'])}<br/>Édité le {d['date_edition']} · "
                     "PaieGabon by Ameriack I.T. Solutions", 7, False, "#9ca3af"),
                   [P("Cachet et signature employeur", 7, False, "#9ca3af", TA_RIGHT), Spacer(1, 12 * mm)]]],
                 colWidths=[W * 0.6, W * 0.4])
    pied.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, HexColor(FILET)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("TOPPADDING", (0, 0), (-1, -1), 6), ("LINEBELOW", (1, 0), (1, 0), 0.6, HexColor("#999999"))]))
    el += [Spacer(1, 4 * mm), pied]
    return el


# ── Modèle MINIMALISTE ──────────────────────────────────────────────────────
# Noir et blanc, titre centré souligné, deux colonnes Employeur / Employé,
# tableaux épurés à filets fins, net encadré.

def _minimaliste(d):
    NOIR, GRIS, CLAIR = "#111111", "#666666", "#eeeeee"
    el = []
    lg = _logo(d["t"], 36 * mm, 12 * mm)
    if lg:
        lg.hAlign = "CENTER"
        el.append(lg)
    titre = [P("BULLETIN DE PAIE", 13, True, NOIR, TA_CENTER), P(_txt(d["periode"]), 9, False, "#555555", TA_CENTER)]
    if d["numero"]:
        titre.append(P("N° " + _txt(d["numero"]), 8.5, False, "#555555", TA_CENTER))
    titre.append(P("DOCUMENT CONFIDENTIEL", 7, False, "#999999", TA_CENTER))
    tt = Table([[titre]], colWidths=[W])
    tt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.6, HexColor(NOIR)), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    el += [tt, Spacer(1, 3 * mm)]

    def partie(titre, lignes):
        rows = [[P(titre.upper(), 7, True, "#555555"), ""]]
        rows += [[P(k, 8, False, GRIS), P(_txt(v), 8, True, NOIR, TA_RIGHT)] for k, v in lignes if v]
        tb = Table(rows, colWidths=[W * 0.17, W * 0.31])
        tb.setStyle(TableStyle([("SPAN", (0, 0), (1, 0)), ("LINEBELOW", (0, 0), (1, 0), 0.6, HexColor("#dddddd")),
                                ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        return tb
    t = d["t"]
    emp = partie("Employeur", [("Entreprise", d["entreprise"]), ("Adresse", getattr(t, "adresse", None)),
                               ("NIF", getattr(t, "nif", None)), ("CNSS Empl.", getattr(t, "numero_cnss", None))])
    sal = partie("Employé", [("Nom", d["nom"]), ("Matricule", d["matricule"]), ("Poste", d["emploi"]),
                             ("Catégorie", d["categorie_code"]), ("Jours trav.", f"{d['jours']} j" if d["jours"] else "")])
    par = Table([[emp, sal]], colWidths=[W * 0.5, W * 0.5])
    par.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (0, 0), 12)]))
    el += [par, Spacer(1, 3 * mm)]

    th = lambda x, a=TA_LEFT: P(x.upper(), 7, True, NOIR, a)   # noqa: E731

    def tableau(lignes, largeurs):
        tb = Table(lignes, colWidths=largeurs, repeatRows=1)
        tb.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1.2, HexColor(NOIR)),
                                ("LINEBELOW", (0, 1), (-1, -2), 0.5, HexColor(CLAIR)),
                                ("LINEABOVE", (0, -1), (-1, -1), 1.2, HexColor(NOIR)),
                                ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                                ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        return tb

    el.append(P("RÉMUNÉRATION", 7.5, True, NOIR))
    lignes = [[th("Désignation"), th("Base", TA_RIGHT), th("Tx", TA_CENTER), th("Gains", TA_RIGHT), th("Retenues", TA_RIGHT)]]
    for g in d["gains"]:
        lignes.append([P(_txt(g["lib"]), 8.5), P(g["base"], 8, False, NOIR, TA_RIGHT, "Courier"),
                       P(g["taux"], 8, False, NOIR, TA_CENTER), P(_m(g["gain"]), 8.5, True, NOIR, TA_RIGHT, "Courier-Bold"),
                       P(_m(g["ret"]), 8.5, True, "#dc2626", TA_RIGHT, "Courier-Bold")])
    lignes.append([P("SALAIRE BRUT", 8.5, True), "", "", P(_m(d["brut"], "0 FCFA"), 8.5, True, NOIR, TA_RIGHT, "Courier-Bold"), ""])
    el.append(tableau(lignes, [W * 0.38, W * 0.17, W * 0.09, W * 0.18, W * 0.18]))

    el += [Spacer(1, 2 * mm), P("COTISATIONS", 7.5, True, NOIR)]
    lignes = [[th("Désignation"), th("Assiette", TA_RIGHT), th("Taux sal.", TA_CENTER), th("Salarié", TA_RIGHT),
               th("Patronal", TA_RIGHT)]]
    pat = {x[0]: x for x in d["cot_pat"]}
    for lib, base, tx, mt in d["cot_sal"]:
        tp = pat.get(lib)
        lignes.append([P(lib, 8.5), P(_n(base) or "—", 8, False, NOIR, TA_RIGHT, "Courier"), P(tx or "—", 8, False, NOIR, TA_CENTER),
                       P(_m(mt, "0 FCFA"), 8.5, True, NOIR, TA_RIGHT, "Courier-Bold"),
                       P(_m(tp[3], "0 FCFA") if tp else "—", 8.5, True, NOIR, TA_RIGHT, "Courier-Bold")])
    for lib in ("FNH", "CFP"):
        tp = pat[lib]
        if _f(tp[3]) > 0:
            lignes.append([P(lib, 8.5), P(_n(tp[1]) or "—", 8, False, NOIR, TA_RIGHT, "Courier"), P("—", 8, False, NOIR, TA_CENTER),
                           P("—", 8.5, False, NOIR, TA_RIGHT), P(_m(tp[3]), 8.5, True, NOIR, TA_RIGHT, "Courier-Bold")])
    lignes.append([P("TOTAL RETENUES", 8.5, True), "", "", P(_m(d["tot_sal"], "0 FCFA"), 8.5, True, NOIR, TA_RIGHT, "Courier-Bold"),
                   P(_m(d["tot_pat"], "0 FCFA"), 8.5, True, NOIR, TA_RIGHT, "Courier-Bold")])
    el.append(tableau(lignes, [W * 0.28, W * 0.2, W * 0.14, W * 0.19, W * 0.19]))

    det = [f"Salaire net {_m(d['net_salaire'], '0 FCFA')}"]
    det += [f"{lib} {'+' if v > 0 else '−'} {_m(abs(_f(v)))}" for lib, v in d["apres_net"]]
    net = Table([[[P("NET À PAYER", 11, True, NOIR), P(" — ".join(det), 7, False, "#555555")],
                  P(_m(d["net"], "0 FCFA"), 16, True, NOIR, TA_RIGHT, "Courier-Bold")]], colWidths=[W * 0.6, W * 0.4])
    net.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.6, HexColor(NOIR)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                             ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    el += [Spacer(1, 3 * mm), net]
    if d["lettres"]:
        el.append(P(f"Arrêté à la somme de : {_txt(d['lettres'])} francs CFA.", 7.5, False, "#555555"))
    pied = Table([[P(f"Édité le {d['date_edition']} · PaieGabon by Ameriack I.T. Solutions", 7, False, "#999999"),
                   [P("Signature et cachet employeur", 7, False, "#555555", TA_RIGHT), Spacer(1, 14 * mm)]]],
                 colWidths=[W * 0.6, W * 0.4])
    pied.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, HexColor("#dddddd")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("TOPPADDING", (0, 0), (-1, -1), 6), ("LINEBELOW", (1, 0), (1, 0), 0.6, HexColor("#999999"))]))
    el += [Spacer(1, 6 * mm), pied]
    return el


# ── Modèle GRAND LIVRE ──────────────────────────────────────────────────────
# Vert PaieGabon #0f3d36 et or #d99e0b : bandeau vert, blocs parchemin,
# titres de section verts à liseré or, registre à filets, net en lettres.

def _grandlivre(d):
    VERT, VERT_F, OR, OR_C, PARCH, ENCRE, SOUS, BORD, FILET, NEG = (
        "#0f3d36", "#0a2b25", "#d99e0b", "#e9c46a", "#f7f4ec", "#14231d", "#5c6b64", "#e5e0d2", "#eee9db", "#b04a2f")
    el = []
    gauche = []
    lg = _logo(d["t"], 42 * mm, 14 * mm)
    if lg:
        gauche.append(lg)
    gauche.append(P(_txt(d["entreprise"]), 14, True, "#ffffff"))
    infos = ([_txt(d["activite"])] if d["activite"] else []) + [_txt(x) for x in d["adresse"]]
    if infos:
        gauche.append(P("<br/>".join(infos), 7.5, False, "#c9d6d2"))
    droite = [P("BULLETIN DE PAIE", 13, True, OR_C, TA_RIGHT)]
    if d["numero"]:
        droite.append(P("N° " + _txt(d["numero"]), 8, False, "#c9d6d2", TA_RIGHT))
    droite.append(P(f"Période : <b>{_txt(d['periode'])}</b><br/>Jours travaillés : <b>{d['jours'] or '—'}</b><br/>"
                    f"Date d'édition : <b>{d['date_edition']}</b><br/>Statut : <b>{_txt(d['statut'])}</b>",
                    8, False, "#e3ebe8", TA_RIGHT))
    h = Table([[gauche, droite]], colWidths=[W * 0.6, W * 0.4])
    h.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(VERT)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                           ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                           ("ROUNDEDCORNERS", [8, 8, 8, 8])]))
    el += [h, Spacer(1, 2.5 * mm)]
    bande = Table([[P(f"Matricule : <b>{_txt(d['matricule'])}</b>", 7.5), P(f"Catégorie : <b>{_txt(d['categorie_code'])}</b>", 7.5),
                    P(f"Embauche : <b>{d['embauche']}</b>", 7.5), P(f"N° CNSS : <b>{_txt(d['cnss'])}</b>", 7.5),
                    P(f"Parts IRPP : <b>{d['parts']}</b>", 7.5, False, "#111111", TA_RIGHT)]], colWidths=[W / 5] * 5)
    bande.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(PARCH)), ("BOX", (0, 0), (-1, -1), 0.6, HexColor(BORD)),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                               ("ROUNDEDCORNERS", [5, 5, 5, 5])]))
    el += [bande, Spacer(1, 2.5 * mm)]

    def bloc(titre, lignes):
        rows = [[P(titre.upper(), 7, True, OR), ""]] + [[P(k, 8, False, SOUS), P(_txt(v), 8, True, ENCRE, TA_RIGHT)]
                                                      for k, v in lignes]
        tb = Table(rows, colWidths=[W * 0.2, W * 0.26])
        tb.setStyle(TableStyle([("SPAN", (0, 0), (1, 0)), ("LINEBELOW", (0, 0), (1, 0), 0.6, HexColor(BORD)),
                                ("BACKGROUND", (0, 0), (-1, -1), HexColor(PARCH)), ("BOX", (0, 0), (-1, -1), 0.6, HexColor(BORD)),
                                ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                                ("ROUNDEDCORNERS", [6, 6, 6, 6])]))
        return tb
    par = Table([[bloc("Salarié", [("Nom & Prénom", d["nom"]), ("Poste", d["emploi"]), ("Date d'embauche", d["embauche"]),
                                   ("Nationalité", d["nationalite"])]),
                  bloc("Situation familiale & cotisations", [("Situation matrimoniale", d["situation"]),
                                                             ("Nombre d'enfants", d["enfants"]), ("Parts IRPP", d["parts"]),
                                                             ("N° CNAMGS", d["cnamgs"]),
                                                             ("Assujetti CNAMGS", d["assujetti_cnamgs"])])]],
                colWidths=[W * 0.5, W * 0.5])
    par.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (0, 0), 6), ("LEFTPADDING", (1, 0), (1, 0), 6), ("RIGHTPADDING", (1, 0), (1, 0), 0)]))
    el += [par, Spacer(1, 3 * mm)]

    def titre_section(titre, largeur):
        tb = Table([[P(titre.upper(), 7.5, True, "#ffffff")]], colWidths=[largeur])
        tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(VERT)), ("LINEBEFORE", (0, 0), (0, 0), 3, HexColor(OR)),
                                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
        return tb

    th = lambda x, a=TA_RIGHT: P(x.upper(), 6.5, True, SOUS, a)   # noqa: E731

    def registre(lignes, largeurs, sous_total=True):
        tb = Table(lignes, colWidths=largeurs, repeatRows=1)
        st = [("LINEBELOW", (0, 0), (-1, 0), 1.2, HexColor(VERT)), ("LINEBELOW", (0, 1), (-1, -2 if sous_total else -1), 0.5, HexColor(FILET)),
              ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
              ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
        if sous_total:
            st.append(("LINEABOVE", (0, -1), (-1, -1), 1.6, HexColor(VERT)))
        tb.setStyle(TableStyle(st))
        return tb

    el.append(titre_section("Éléments de rémunération", W))
    lignes = [[th("Désignation", TA_LEFT), th("Base"), th("Taux", TA_CENTER), th("Gains (FCFA)"), th("Retenues (FCFA)")]]
    for g in d["gains"]:
        lignes.append([P(_txt(g["lib"]), 8.5, False, ENCRE), P(g["base"], 7.5, False, SOUS, TA_RIGHT, "Courier"),
                       P(g["taux"], 7.5, False, SOUS, TA_CENTER), P(_m(g["gain"]), 8.5, True, VERT, TA_RIGHT),
                       P(_m(g["ret"]), 8.5, True, NEG, TA_RIGHT)])
    lignes.append([P("Salaire brut", 9, True, VERT), "", "", P(_m(d["brut"], "0 FCFA"), 10, True, VERT, TA_RIGHT), ""])
    el += [registre(lignes, [W * 0.44, W * 0.16, W * 0.09, W * 0.16, W * 0.15]), Spacer(1, 3 * mm)]

    demi = (W - 6 * mm) / 2

    def cotis(titre, lignes_c, total, lib_total, couleur):
        rows = [[th("Rubrique", TA_LEFT), th("Base"), th("Taux", TA_CENTER), th("Montant")]]
        for lib, base, tx, mt in lignes_c:
            rows.append([P(lib, 8.5, False, ENCRE), P(_n(base), 7.5, False, SOUS, TA_RIGHT, "Courier"),
                         P(tx, 7.5, False, SOUS, TA_CENTER), P(_m(mt, "0 FCFA"), 8.5, True, couleur, TA_RIGHT)])
        rows.append([P(lib_total, 8.5, True, VERT), "", "", P(_m(total, "0 FCFA"), 9, True, VERT, TA_RIGHT)])
        reg = registre(rows, [demi * 0.26, demi * 0.27, demi * 0.17, demi * 0.30])
        reg.setStyle(TableStyle([("SPAN", (0, -1), (2, -1))]))
        return [titre_section(titre, demi), reg]
    deux = Table([[cotis("Cotisations salariales", d["cot_sal"], d["tot_sal"], "Total retenues", NEG),
                   cotis("Cotisations patronales (informatif)", d["cot_pat"], d["tot_pat"], "Total charges", ENCRE)]],
                 colWidths=[demi + 3 * mm, demi + 3 * mm])
    deux.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (0, 0), 3 * mm), ("LEFTPADDING", (1, 0), (1, 0), 3 * mm),
                              ("RIGHTPADDING", (1, 0), (1, 0), 0)]))
    el += [deux, Spacer(1, 2 * mm)]

    lignes = [[P("Salaire net (brut — cotisations salariales)", 9, True, VERT),
               P(_m(d["net_salaire"], "0 FCFA"), 10, True, VERT, TA_RIGHT)]]
    for lib, v in d["apres_net"]:
        lignes.append([P(("+ " if v > 0 else "— ") + lib, 8.5, False, ENCRE),
                       P(_m(abs(_f(v))), 8.5, True, VERT if v > 0 else NEG, TA_RIGHT)])
    tb = Table(lignes, colWidths=[W * 0.6, W * 0.4])
    tb.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1.6, HexColor(VERT)),
                            ("LINEBELOW", (0, 1), (-1, -1), 0.5, HexColor(FILET)),
                            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
    el.append(tb)

    net_rows = [[P("NET À PAYER", 12, True, OR_C), P(_m(d["net"], "0 FCFA"), 19, True, "#ffffff", TA_RIGHT, "Courier-Bold")]]
    if d["lettres"]:
        net_rows.append([P(f"<i>Arrêté à la somme de : {_txt(d['lettres'])} francs CFA.</i>", 8, False, "#d6e2de"), ""])
    net = Table(net_rows, colWidths=[W * 0.5, W * 0.5])
    st = [("BACKGROUND", (0, 0), (-1, -1), HexColor(VERT_F)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
          ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("ROUNDEDCORNERS", [8, 8, 8, 8])]
    if d["lettres"]:
        st += [("SPAN", (0, 1), (1, 1)), ("LINEABOVE", (0, 1), (1, 1), 0.5, HexColor("#6b5a2a"))]
    net.setStyle(TableStyle(st))
    el += [Spacer(1, 3 * mm), net, Spacer(1, 5 * mm)]

    sig = Table([[[P("Signature et cachet de l'employeur", 8, True, SOUS), Spacer(1, 13 * mm), P(_txt(d["entreprise"]), 7.5, False, SOUS)],
                  [P("Signature du salarié <font size=6.5>(pour acquit)</font>", 8, True, SOUS), Spacer(1, 13 * mm),
                   P(_txt(d["nom"]), 7.5, False, SOUS)]]], colWidths=[W * 0.5, W * 0.5])
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("LINEBELOW", (0, 0), (0, 0), 0.5, HexColor(BORD)), ("LINEBELOW", (1, 0), (1, 0), 0.5, HexColor(BORD))]))
    el += [sig, Spacer(1, 4 * mm)]
    pied = Table([[P(f"{_txt(d['entreprise'])} — Bulletin de paie {_txt(d['periode'])} — Document confidentiel<br/>"
                     "Conformément au Code du Travail Gabonais — Conservez ce document sans limitation de durée",
                     7, False, SOUS, TA_CENTER)]], colWidths=[W])
    pied.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, HexColor(BORD)), ("TOPPADDING", (0, 0), (-1, -1), 5)]))
    el.append(pied)
    return el


_CONSTRUCTEURS = {"moderne": _moderne, "minimaliste": _minimaliste, "grandlivre": _grandlivre}


def elements(b, t, modele: str) -> list:
    return _CONSTRUCTEURS[modele](donnees(b, t))


def _doc(buffer, titre):
    return SimpleDocTemplate(buffer, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=10 * mm,
                             bottomMargin=10 * mm, title=titre, author="PaieGabon")


def generer(b, t, modele: str) -> bytes:
    """PDF d'un bulletin au modèle donné (moderne | minimaliste | grandlivre), sur une page."""
    buf = io.BytesIO()
    doc = _doc(buf, f"Bulletin de paie — {b.salarie.nom_complet}")
    doc.build([KeepInFrame(doc.width, doc.height, elements(b, t, modele), mode="shrink", hAlign="LEFT", vAlign="TOP")])
    return buf.getvalue()


def generer_lot(bulletins, t, modele: str) -> bytes:
    """Un seul PDF, un bulletin par page."""
    buf = io.BytesIO()
    doc = _doc(buf, "Bulletins de paie")
    el = []
    for i, b in enumerate(bulletins):
        if i:
            el.append(PageBreak())
        el.append(KeepInFrame(doc.width, doc.height, elements(b, t, modele), mode="shrink", hAlign="LEFT", vAlign="TOP"))
    doc.build(el)
    return buf.getvalue()
