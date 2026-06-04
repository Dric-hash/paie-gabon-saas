"""
rapport_pdf.py — Rapport mensuel de paie PDF pour PaieGabon
============================================================
Génère un rapport PDF professionnel en 3 sections :

  1. PAGE DE GARDE     — logo, période, infos entreprise
  2. RÉSUMÉ DIRECTION  — KPIs, masse salariale, graphiques ASCII, alertes
  3. TABLEAU DE PAIE   — détail par salarié (brut, cotisations, net)
  4. RÉCAPITULATIF     — totaux CNSS/CNAMGS/TCS/IRPP à verser

Utilise ReportLab (déjà dans requirements.txt).
"""

import io
from datetime import datetime, date
from reportlab.lib.pagesizes import A4
from reportlab.lib.units    import mm
from reportlab.lib.colors   import HexColor, black, white, Color
from reportlab.lib.styles   import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums    import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus     import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable,
                                    PageBreak, KeepTogether)
from reportlab.pdfbase      import pdfmetrics

# ── Palette de couleurs PaieGabon ─────────────────────────────────────────────
C_DARK    = HexColor("#1a2332")   # bleu marine (marque)
C_GREEN   = HexColor("#059669")   # vert validation
C_RED     = HexColor("#dc2626")   # rouge alerte
C_BLUE    = HexColor("#2563eb")   # bleu accent
C_ORANGE  = HexColor("#f59e0b")   # orange avertissement
C_GRAY    = HexColor("#6b7280")   # gris texte secondaire
C_LGRAY   = HexColor("#f3f4f6")   # gris fond
C_BORDER  = HexColor("#e5e7eb")   # gris bordure
C_WHITE   = white
C_BLACK   = black

# ── Styles de paragraphe ──────────────────────────────────────────────────────
def _styles():
    s = getSampleStyleSheet()
    return {
        "h1":      ParagraphStyle("h1",      fontSize=22, fontName="Helvetica-Bold",
                                  textColor=C_DARK,  spaceAfter=4*mm),
        "h2":      ParagraphStyle("h2",      fontSize=14, fontName="Helvetica-Bold",
                                  textColor=C_DARK,  spaceBefore=6*mm, spaceAfter=3*mm),
        "h3":      ParagraphStyle("h3",      fontSize=11, fontName="Helvetica-Bold",
                                  textColor=C_DARK,  spaceBefore=4*mm, spaceAfter=2*mm),
        "body":    ParagraphStyle("body",    fontSize=9,  fontName="Helvetica",
                                  textColor=C_DARK,  leading=14),
        "small":   ParagraphStyle("small",   fontSize=8,  fontName="Helvetica",
                                  textColor=C_GRAY,  leading=12),
        "center":  ParagraphStyle("center",  fontSize=9,  fontName="Helvetica",
                                  alignment=TA_CENTER),
        "bold":    ParagraphStyle("bold",    fontSize=9,  fontName="Helvetica-Bold",
                                  textColor=C_DARK),
        "green":   ParagraphStyle("green",   fontSize=9,  fontName="Helvetica-Bold",
                                  textColor=C_GREEN),
        "red":     ParagraphStyle("red",     fontSize=9,  fontName="Helvetica-Bold",
                                  textColor=C_RED),
        "cover_title": ParagraphStyle("cover_title", fontSize=32, fontName="Helvetica-Bold",
                                       textColor=C_WHITE, alignment=TA_CENTER),
        "cover_sub":   ParagraphStyle("cover_sub",   fontSize=14, fontName="Helvetica",
                                       textColor=C_WHITE, alignment=TA_CENTER),
    }


def _fcfa(v) -> str:
    """Formate un montant en FCFA avec séparateurs de milliers."""
    try:
        return f"{int(round(float(v or 0))):,}".replace(",", " ") + " FCFA"
    except Exception:
        return "0 FCFA"


def _pct(part, total) -> str:
    try:
        return f"{part / total * 100:.1f}%" if total else "—"
    except Exception:
        return "—"


def _bar(value, max_value, width=20) -> str:
    """Barre de progression ASCII pour les rapports."""
    if not max_value:
        return "─" * width
    filled = int(value / max_value * width)
    return "█" * filled + "░" * (width - filled)


# ═══════════════════════════════════════════════════════════════════════════════
# FONCTION PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════════

def generer_rapport_mensuel(bulletins, periode, tenant, evolution=None) -> bytes:
    """
    Génère le rapport mensuel PDF complet.

    Args:
        bulletins  : liste de BulletinPaie de la période (statut VALIDÉ)
        periode    : PeriodePaie
        tenant     : Tenant
        evolution  : liste de dicts {mois, annee, brut, net} pour le graphique
                     (optionnel — 6 derniers mois)

    Returns:
        bytes du PDF
    """
    buf  = io.BytesIO()
    doc  = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm,  bottomMargin=15*mm,
        title=f"Rapport paie {periode.libelle_complet} — {tenant.denomination}",
        author="PaieGabon SaaS",
    )

    S = _styles()
    story = []

    # ── Calculer les totaux ───────────────────────────────────────────────────
    from calculs_paie import calculer_masse_salariale
    masse = calculer_masse_salariale(bulletins) if bulletins else {}

    total_brut      = masse.get("total_brut",       0)
    total_net       = masse.get("total_net",         0)
    total_cnss_sal  = masse.get("total_cnss_sal",    0)
    total_cnss_pat  = masse.get("total_cnss_pat",    0)
    total_cnamgs_sal= masse.get("total_cnamgs_sal",  0)
    total_cnamgs_pat= masse.get("total_cnamgs_pat",  0)
    total_tcs       = masse.get("total_tcs",         0)
    total_irpp      = masse.get("total_irpp",        0)
    total_fnh       = masse.get("total_fnh",         0)
    total_cfp       = masse.get("total_cfp",         0)
    total_charges   = masse.get("total_charges_pat", 0)
    cout_employeur  = total_brut + total_charges
    nb_bul          = len(bulletins)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — PAGE DE GARDE
    # ══════════════════════════════════════════════════════════════════════════
    story += _page_garde(tenant, periode, nb_bul, S)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — RÉSUMÉ DIRECTION (KPIs)
    # ══════════════════════════════════════════════════════════════════════════
    story += _section_direction(
        periode, tenant, masse, bulletins, evolution, S
    )
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — TABLEAU DÉTAIL PAR SALARIÉ
    # ══════════════════════════════════════════════════════════════════════════
    story += _section_detail_salaries(bulletins, periode, S)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 4 — RÉCAPITULATIF CHARGES À VERSER
    # ══════════════════════════════════════════════════════════════════════════
    story += _section_recap_charges(masse, periode, tenant, S)

    # ── Pied de page global ───────────────────────────────────────────────────
    story.append(Spacer(1, 8*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        f"Rapport généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} par PaieGabon SaaS — "
        f"Document confidentiel — {tenant.denomination}",
        S["small"]
    ))

    doc.build(story)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — PAGE DE GARDE
# ══════════════════════════════════════════════════════════════════════════════

def _page_garde(tenant, periode, nb_bul, S):
    elements = []

    # Bandeau coloré supérieur (simulé avec un tableau)
    bandeau = Table(
        [[Paragraph(f"RAPPORT MENSUEL DE PAIE", S["cover_title"]),
          Paragraph(f"{periode.libelle_complet} {periode.annee}", S["cover_sub"])]],
        colWidths=["60%", "40%"],
    )
    bandeau.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), C_DARK),
        ("PADDING",    (0,0), (-1,-1), 8*mm),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("ROWHEIGHT",  (0,0), (-1,-1), 35*mm),
    ]))
    elements.append(bandeau)
    elements.append(Spacer(1, 10*mm))

    # Infos entreprise
    infos = [
        ["Entreprise",  tenant.denomination],
        ["Sigle",       tenant.sigle or "—"],
        ["NIF",         tenant.nif  or "—"],
        ["N° CNSS",     getattr(tenant, "numero_cnss",   None) or "—"],
        ["N° CNAMGS",   getattr(tenant, "numero_cnamgs", None) or "—"],
        ["Ville",       tenant.ville or "Gabon"],
        ["Période",     f"{periode.libelle_complet} {periode.annee}"],
        ["Bulletins",   f"{nb_bul} bulletin(s) validé(s)"],
        ["Généré le",   datetime.now().strftime("%d/%m/%Y à %H:%M")],
    ]
    t = Table(
        [[Paragraph(k, S["bold"]), Paragraph(str(v), S["body"])] for k, v in infos],
        colWidths=[50*mm, 120*mm],
    )
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [C_LGRAY, C_WHITE]),
        ("PADDING",        (0,0), (-1,-1), 3*mm),
        ("LINEBELOW",      (0,0), (-1,-1), 0.3, C_BORDER),
        ("FONTSIZE",       (0,0), (-1,-1), 9),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 8*mm))

    # Badge "CONFIDENTIEL"
    badge = Table(
        [[Paragraph("🔒  DOCUMENT CONFIDENTIEL — USAGE INTERNE", S["center"])]],
        colWidths=["100%"],
    )
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), HexColor("#fef3c7")),
        ("TEXTCOLOR",  (0,0), (-1,-1), HexColor("#92400e")),
        ("PADDING",    (0,0), (-1,-1), 4*mm),
        ("FONTNAME",   (0,0), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
    ]))
    elements.append(badge)
    return elements


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — RÉSUMÉ DIRECTION
# ══════════════════════════════════════════════════════════════════════════════

def _section_direction(periode, tenant, masse, bulletins, evolution, S):
    elements = []
    elements.append(Paragraph("1. Résumé de Direction", S["h2"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=C_DARK))
    elements.append(Spacer(1, 4*mm))

    total_brut    = masse.get("total_brut",       0)
    total_net     = masse.get("total_net",         0)
    total_charges = masse.get("total_charges_pat", 0)
    cout_emp      = total_brut + total_charges
    nb_bul        = masse.get("nb_bulletins",      0)

    # ── KPIs en grille 2×2 ───────────────────────────────────────────────────
    kpis = [
        [_kpi_cell("Masse salariale brute", _fcfa(total_brut),  C_DARK,  S),
         _kpi_cell("Net total à payer",     _fcfa(total_net),   C_GREEN, S)],
        [_kpi_cell("Charges patronales",    _fcfa(total_charges), C_RED, S),
         _kpi_cell("Coût total employeur",  _fcfa(cout_emp),    C_BLUE,  S)],
    ]
    kpi_table = Table(kpis, colWidths=["50%", "50%"])
    kpi_table.setStyle(TableStyle([
        ("PADDING",  (0,0), (-1,-1), 3*mm),
        ("LINEAFTER", (0,0), (0,-1), 0.5, C_BORDER),
    ]))
    elements.append(kpi_table)
    elements.append(Spacer(1, 5*mm))

    # ── Décomposition visuelle (barre ASCII) ──────────────────────────────────
    elements.append(Paragraph("Décomposition du coût employeur", S["h3"]))
    if cout_emp > 0:
        lignes_barre = [
            ("Net à payer",        total_net,    _pct(total_net,    cout_emp), C_GREEN),
            ("Retenues salariales",
             total_brut - total_net, _pct(total_brut - total_net, cout_emp), C_ORANGE),
            ("Charges patronales",  total_charges, _pct(total_charges, cout_emp), C_RED),
        ]
        barre_data = []
        for label, val, pct_str, color in lignes_barre:
            barre = _bar(val, cout_emp, 30)
            barre_data.append([
                Paragraph(label, S["body"]),
                Paragraph(f"<font color='#{color.hexval()[2:]}'>{'█' * int(val/cout_emp*30) if cout_emp else ''}</font>", S["body"]),
                Paragraph(_fcfa(val), S["bold"]),
                Paragraph(pct_str, S["small"]),
            ])
        bt = Table(barre_data, colWidths=[50*mm, 70*mm, 40*mm, 20*mm])
        bt.setStyle(TableStyle([
            ("ROWBACKGROUNDS", (0,0), (-1,-1), [C_LGRAY, C_WHITE]),
            ("PADDING",        (0,0), (-1,-1), 2*mm),
            ("FONTSIZE",       (0,0), (-1,-1), 8),
        ]))
        elements.append(bt)
    elements.append(Spacer(1, 5*mm))

    # ── Évolution 6 mois (tableau) ────────────────────────────────────────────
    if evolution and len(evolution) > 1:
        elements.append(Paragraph("Évolution masse salariale (6 derniers mois)", S["h3"]))
        max_brut = max((e.get("brut", 0) for e in evolution), default=1) or 1
        evo_header = [
            Paragraph("Mois",  S["bold"]),
            Paragraph("Brut",  S["bold"]),
            Paragraph("Net",   S["bold"]),
            Paragraph("Tendance", S["bold"]),
        ]
        evo_rows = [evo_header]
        for e in evolution:
            barre = _bar(e.get("brut", 0), max_brut, 20)
            evo_rows.append([
                Paragraph(f"{e.get('mois','')} {e.get('annee','')}", S["body"]),
                Paragraph(_fcfa(e.get("brut", 0)),  S["body"]),
                Paragraph(_fcfa(e.get("net",  0)),  S["green"]),
                Paragraph(barre, ParagraphStyle("mono", fontSize=7,
                           fontName="Courier", textColor=C_BLUE)),
            ])
        et = Table(evo_rows, colWidths=[30*mm, 45*mm, 45*mm, 55*mm])
        et.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0),  C_DARK),
            ("TEXTCOLOR",     (0,0), (-1,0),  C_WHITE),
            ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_LGRAY]),
            ("PADDING",       (0,0), (-1,-1), 2.5*mm),
            ("LINEBELOW",     (0,0), (-1,-1), 0.3, C_BORDER),
        ]))
        elements.append(et)

    return elements


def _kpi_cell(label, value, color, S):
    """Cellule KPI colorée."""
    cell_data = [
        [Paragraph(label, ParagraphStyle("kpi_lbl", fontSize=8,
                   fontName="Helvetica", textColor=C_GRAY))],
        [Paragraph(value, ParagraphStyle("kpi_val", fontSize=14,
                   fontName="Helvetica-Bold", textColor=color))],
    ]
    t = Table(cell_data, colWidths=["100%"])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), C_LGRAY),
        ("PADDING",    (0,0), (-1,-1), 3*mm),
        ("LINEBELOW",  (0,1), (-1,1), 2, color),
    ]))
    return t


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — DÉTAIL PAR SALARIÉ
# ══════════════════════════════════════════════════════════════════════════════

def _section_detail_salaries(bulletins, periode, S):
    elements = []
    elements.append(Paragraph("2. Tableau de paie détaillé", S["h2"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=C_DARK))
    elements.append(Paragraph(
        f"Période : {periode.libelle_complet} {periode.annee} — "
        f"{len(bulletins)} salarié(s)",
        S["small"]
    ))
    elements.append(Spacer(1, 4*mm))

    if not bulletins:
        elements.append(Paragraph("Aucun bulletin validé pour cette période.", S["body"]))
        return elements

    # En-têtes du tableau
    headers = [
        Paragraph("Matricule", S["bold"]),
        Paragraph("Nom & Prénom", S["bold"]),
        Paragraph("Brut", S["bold"]),
        Paragraph("CNSS sal.", S["bold"]),
        Paragraph("CNAMGS sal.", S["bold"]),
        Paragraph("TCS", S["bold"]),
        Paragraph("IRPP", S["bold"]),
        Paragraph("Net à payer", S["bold"]),
        Paragraph("Statut", S["bold"]),
    ]

    rows = [headers]
    total_brut = total_net = total_cnss = total_cnamgs = total_tcs = total_irpp = 0

    for b in sorted(bulletins, key=lambda x: (x.salarie.nom, x.salarie.prenom)):
        s = b.salarie
        brut    = float(b.salaire_brut  or 0)
        net     = float(b.net_a_payer   or 0)
        cnss    = float(b.cnss_salarie  or 0)
        cnamgs  = float(b.cnamgs_salarie or 0)
        tcs     = float(b.tcs           or 0)
        irpp    = float(b.irpp          or 0)

        total_brut   += brut
        total_net    += net
        total_cnss   += cnss
        total_cnamgs += cnamgs
        total_tcs    += tcs
        total_irpp   += irpp

        statut_color = C_GREEN if b.statut in ("VALIDÉ","VALIDE") else (
            C_BLUE if b.statut == "PAYÉ" else C_ORANGE)

        rows.append([
            Paragraph(s.matricule or "—",    S["small"]),
            Paragraph(s.nom_complet,          S["body"]),
            Paragraph(_fcfa(brut),            S["body"]),
            Paragraph(_fcfa(cnss),            S["small"]),
            Paragraph(_fcfa(cnamgs),          S["small"]),
            Paragraph(_fcfa(tcs),             S["small"]),
            Paragraph(_fcfa(irpp),            S["small"]),
            Paragraph(_fcfa(net), ParagraphStyle("net_val", fontSize=9,
                      fontName="Helvetica-Bold", textColor=C_GREEN)),
            Paragraph(b.statut, ParagraphStyle("statut", fontSize=7,
                      fontName="Helvetica-Bold", textColor=statut_color)),
        ])

    # Ligne des totaux
    rows.append([
        Paragraph("", S["body"]),
        Paragraph("TOTAUX", S["bold"]),
        Paragraph(_fcfa(total_brut),   S["bold"]),
        Paragraph(_fcfa(total_cnss),   S["bold"]),
        Paragraph(_fcfa(total_cnamgs), S["bold"]),
        Paragraph(_fcfa(total_tcs),    S["bold"]),
        Paragraph(_fcfa(total_irpp),   S["bold"]),
        Paragraph(_fcfa(total_net), ParagraphStyle("tot_net", fontSize=9,
                  fontName="Helvetica-Bold", textColor=C_GREEN)),
        Paragraph("", S["body"]),
    ])

    col_widths = [18*mm, 40*mm, 25*mm, 18*mm, 18*mm, 14*mm, 18*mm, 25*mm, 14*mm]
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        # En-tête
        ("BACKGROUND",    (0,0),  (-1,0),  C_DARK),
        ("TEXTCOLOR",     (0,0),  (-1,0),  C_WHITE),
        ("FONTNAME",      (0,0),  (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),  (-1,0),  7),
        ("ALIGN",         (2,0),  (-1,0),  "RIGHT"),
        # Corps
        ("ROWBACKGROUNDS",(0,1),  (-1,-2), [C_WHITE, C_LGRAY]),
        ("FONTSIZE",      (0,1),  (-1,-1), 7),
        ("ALIGN",         (2,1),  (-1,-1), "RIGHT"),
        ("PADDING",       (0,0),  (-1,-1), 2*mm),
        ("LINEBELOW",     (0,0),  (-1,-1), 0.2, C_BORDER),
        # Ligne totaux
        ("BACKGROUND",    (0,-1), (-1,-1), C_DARK),
        ("TEXTCOLOR",     (0,-1), (-1,-1), C_WHITE),
        ("FONTNAME",      (0,-1), (-1,-1), "Helvetica-Bold"),
    ]))
    elements.append(t)
    return elements


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — RÉCAPITULATIF CHARGES
# ══════════════════════════════════════════════════════════════════════════════

def _section_recap_charges(masse, periode, tenant, S):
    elements = []
    elements.append(Paragraph("3. Récapitulatif des charges à verser", S["h2"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=C_DARK))
    elements.append(Spacer(1, 4*mm))

    cnss_sal  = masse.get("total_cnss_sal",    0)
    cnss_pat  = masse.get("total_cnss_pat",    0)
    cnamgs_sal= masse.get("total_cnamgs_sal",  0)
    cnamgs_pat= masse.get("total_cnamgs_pat",  0)
    tcs       = masse.get("total_tcs",         0)
    irpp      = masse.get("total_irpp",        0)
    fnh       = masse.get("total_fnh",         0)
    cfp       = masse.get("total_cfp",         0)

    total_cnss   = cnss_sal + cnss_pat
    total_cnamgs = cnamgs_sal + cnamgs_pat

    # Tableau des versements
    recap_data = [
        [Paragraph("Organisme", S["bold"]),
         Paragraph("Part salarié", S["bold"]),
         Paragraph("Part patronale", S["bold"]),
         Paragraph("TOTAL À VERSER", S["bold"]),
         Paragraph("Date limite", S["bold"])],
        # CNSS
        [Paragraph("CNSS", S["body"]),
         Paragraph(_fcfa(cnss_sal),  S["body"]),
         Paragraph(_fcfa(cnss_pat),  S["body"]),
         Paragraph(_fcfa(total_cnss), ParagraphStyle("tot",fontSize=9,
                   fontName="Helvetica-Bold", textColor=C_RED)),
         Paragraph("Fin du mois suivant", S["small"])],
        # CNAMGS
        [Paragraph("CNAMGS", S["body"]),
         Paragraph(_fcfa(cnamgs_sal),   S["body"]),
         Paragraph(_fcfa(cnamgs_pat),   S["body"]),
         Paragraph(_fcfa(total_cnamgs), ParagraphStyle("tot2",fontSize=9,
                   fontName="Helvetica-Bold", textColor=C_RED)),
         Paragraph("Fin du mois suivant", S["small"])],
        # TCS
        [Paragraph("TCS", S["body"]),
         Paragraph(_fcfa(tcs), S["body"]),
         Paragraph("—", S["small"]),
         Paragraph(_fcfa(tcs), ParagraphStyle("tot3",fontSize=9,
                   fontName="Helvetica-Bold", textColor=C_ORANGE)),
         Paragraph("Fin du mois suivant", S["small"])],
        # IRPP
        [Paragraph("IRPP", S["body"]),
         Paragraph(_fcfa(irpp), S["body"]),
         Paragraph("—", S["small"]),
         Paragraph(_fcfa(irpp), ParagraphStyle("tot4",fontSize=9,
                   fontName="Helvetica-Bold", textColor=C_ORANGE)),
         Paragraph("Fin du mois suivant", S["small"])],
        # FNH
        [Paragraph("FNH", S["body"]),
         Paragraph("—", S["small"]),
         Paragraph(_fcfa(fnh), S["body"]),
         Paragraph(_fcfa(fnh), S["body"]),
         Paragraph("Fin du mois suivant", S["small"])],
        # CFP
        [Paragraph("CFP", S["body"]),
         Paragraph("—", S["small"]),
         Paragraph(_fcfa(cfp), S["body"]),
         Paragraph(_fcfa(cfp), S["body"]),
         Paragraph("Fin du mois suivant", S["small"])],
        # Total
        [Paragraph("TOTAL GÉNÉRAL", S["bold"]),
         Paragraph("", S["body"]),
         Paragraph("", S["body"]),
         Paragraph(_fcfa(total_cnss + total_cnamgs + tcs + irpp + fnh + cfp),
                   ParagraphStyle("gtot", fontSize=11,
                   fontName="Helvetica-Bold", textColor=C_RED)),
         Paragraph("", S["body"])],
    ]

    rt = Table(recap_data, colWidths=[30*mm, 35*mm, 35*mm, 45*mm, 35*mm])
    rt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),  (-1,0),  C_DARK),
        ("TEXTCOLOR",     (0,0),  (-1,0),  C_WHITE),
        ("FONTNAME",      (0,0),  (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),  (-1,0),  8),
        ("ROWBACKGROUNDS",(0,1),  (-1,-2), [C_WHITE, C_LGRAY]),
        ("FONTSIZE",      (0,1),  (-1,-1), 8),
        ("ALIGN",         (1,0),  (-1,-1), "RIGHT"),
        ("PADDING",       (0,0),  (-1,-1), 2.5*mm),
        ("LINEBELOW",     (0,0),  (-1,-1), 0.3, C_BORDER),
        ("BACKGROUND",    (0,-1), (-1,-1), HexColor("#fef2f2")),
        ("FONTNAME",      (0,-1), (-1,-1), "Helvetica-Bold"),
        ("LINEABOVE",     (0,-1), (-1,-1), 1.5, C_RED),
    ]))
    elements.append(rt)
    elements.append(Spacer(1, 6*mm))

    # Encadré récapitulatif final
    elements.append(Paragraph("Rappel des obligations légales (Gabon)", S["h3"]))
    obligations = [
        "CNSS : cotisations salariales (5%) + patronales (18%) — trimestriel",
        "CNAMGS : cotisations salariales (1,5%) + patronales (6%) — trimestriel",
        "TCS : Taxe sur les Contrats de Salaires — mensuel",
        "IRPP : Impôt sur le Revenu des Personnes Physiques — mensuel",
        "FNH : Fonds National de l'Habitat (1%) — mensuel",
        "CFP : Contribution à la Formation Professionnelle (2%) — mensuel",
    ]
    for o in obligations:
        elements.append(Paragraph(f"• {o}", S["small"]))

    return elements
