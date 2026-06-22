"""
pdf_bulletin.py — Génération PDF des bulletins de paie avec reportlab
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                  TableStyle, HRFlowable)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io
from datetime import datetime

# ── Palette couleurs ─────────────────────────────────────────────────────────
C_DARK   = HexColor("#1a2332")
C_GREEN  = HexColor("#059669")
C_RED    = HexColor("#dc2626")
C_BLUE   = HexColor("#1e40af")
C_AMBER  = HexColor("#d97706")
C_GRAY   = HexColor("#6b7280")
C_LIGHT  = HexColor("#f9fafb")
C_BORDER = HexColor("#e5e7eb")
C_GAIN   = HexColor("#065f46")
C_RET    = HexColor("#991b1b")

def _fmt(v):
    """Formater un montant FCFA."""
    try:
        n = int(float(v or 0))
        return f"{n:,}".replace(",", " ") + " FCFA"
    except:
        return "0 FCFA"

def _flt(v):
    try:
        return float(v or 0)
    except:
        return 0.0

def generer_bulletin_pdf(bulletin, tenant) -> bytes:
    """
    Génère le PDF d'un bulletin de paie.
    Retourne les bytes du PDF.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=10*mm,  bottomMargin=10*mm,
        title=f"Bulletin de paie — {bulletin.salarie.nom_complet}",
    )

    # ── Styles ────────────────────────────────────────────────────────────────
    def sty(name, **kwargs):
        defaults = dict(fontName="Helvetica", fontSize=9, leading=12,
                        textColor=black, spaceAfter=0, spaceBefore=0)
        defaults.update(kwargs)
        return ParagraphStyle(name, **defaults)

    s_title   = sty("title",   fontSize=14, fontName="Helvetica-Bold",
                    textColor=C_DARK, alignment=TA_LEFT)
    s_company = sty("company", fontSize=8,  textColor=C_GRAY)
    s_label   = sty("label",   fontSize=8,  textColor=C_GRAY)
    s_value   = sty("value",   fontSize=8.5, fontName="Helvetica-Bold")
    s_hdr     = sty("hdr",     fontSize=7.5, fontName="Helvetica-Bold",
                    textColor=white, alignment=TA_CENTER)
    s_cell    = sty("cell",    fontSize=8)
    s_cell_r  = sty("cell_r",  fontSize=8, alignment=TA_RIGHT)
    s_gain    = sty("gain",    fontSize=8.5, fontName="Helvetica-Bold",
                    textColor=C_GAIN, alignment=TA_RIGHT)
    s_ret     = sty("ret",     fontSize=8.5, fontName="Helvetica-Bold",
                    textColor=C_RET, alignment=TA_RIGHT)
    s_bold    = sty("bold",    fontSize=9, fontName="Helvetica-Bold")
    s_center  = sty("center",  fontSize=8, alignment=TA_CENTER)
    s_net     = sty("net",     fontSize=16, fontName="Helvetica-Bold",
                    textColor=C_DARK, alignment=TA_RIGHT)
    s_footnote= sty("footnote",fontSize=7, textColor=C_GRAY, alignment=TA_CENTER)

    b  = bulletin
    s  = b.salarie
    p  = b.periode
    t  = tenant
    sal= s  # alias

    W = 186*mm  # largeur utile

    elements = []

    # ══════════════════════════════════════════════════════════════════════════
    # EN-TÊTE : Entreprise + Titre
    # ══════════════════════════════════════════════════════════════════════════
    addr_lines = [t.denomination]
    if t.adresse:        addr_lines.append(t.adresse)
    if t.ville:          addr_lines.append(t.ville)
    if t.telephone:      addr_lines.append(f"Tél : {t.telephone}")
    if t.nif:            addr_lines.append(f"NIF : {t.nif}")
    if t.numero_cnss:    addr_lines.append(f"N° CNSS : {t.numero_cnss}")

    header_data = [[
        Paragraph(t.denomination.upper(), sty("co_name", fontSize=13,
                  fontName="Helvetica-Bold", textColor=C_DARK)),
        Paragraph(
            "<br/>".join([
                "<b>BULLETIN DE PAIE</b>",
                f"Période : <b>{p.libelle_complet}</b>",
                f"Émis le : {datetime.now().strftime('%d/%m/%Y')}",
            ]),
            sty("bul_hdr", fontSize=9, fontName="Helvetica",
                textColor=C_DARK, alignment=TA_RIGHT)
        ),
    ]]
    header_tbl = Table(header_data, colWidths=[W*0.6, W*0.4])
    header_tbl.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("LINEBELOW",     (0,0), (-1,-1), 1.5, C_DARK),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    elements.append(header_tbl)
    elements.append(Spacer(1, 4*mm))

    # Infos entreprise sur une ligne
    info_ent = " | ".join(filter(None, [
        t.adresse or "", t.ville or "", t.telephone or "",
        f"NIF : {t.nif}" if t.nif else "",
        f"N°CNSS {t.numero_cnss}" if t.numero_cnss else "",
        f"N°CNAMGS {t.numero_cnamgs}" if t.numero_cnamgs else "",
    ]))
    if info_ent.strip(" |"):
        elements.append(Paragraph(info_ent, s_company))
        elements.append(Spacer(1, 3*mm))

    # ══════════════════════════════════════════════════════════════════════════
    # FICHE SALARIÉ
    # ══════════════════════════════════════════════════════════════════════════
    contrat = next((c for c in s.contrats if c.actif), None)

    sal_data = [
        [
            Paragraph("EMPLOYÉ", sty("sec_hdr", fontSize=7.5, fontName="Helvetica-Bold",
                      textColor=C_DARK)),
            "",
            Paragraph("CONTRAT & POSTE", sty("sec_hdr", fontSize=7.5, fontName="Helvetica-Bold",
                      textColor=C_DARK)),
            "",
        ],
        [
            Paragraph("Nom & Prénom", s_label), Paragraph(s.nom_complet, s_bold),
            Paragraph("Poste", s_label), Paragraph(s.emploi or "—", s_bold),
        ],
        [
            Paragraph("Matricule", s_label), Paragraph(s.matricule or "—", s_value),
            Paragraph("Catégorie", s_label),
            Paragraph(s.categorie.code if s.categorie else "—", s_value),
        ],
        [
            Paragraph("N° CNSS", s_label), Paragraph(s.numero_cnss or "—", s_value),
            Paragraph("Nb parts IRPP", s_label),
            Paragraph(str(float(s.nombre_parts or 1)), s_value),
        ],
        [
            Paragraph("Date embauche", s_label),
            Paragraph(s.date_embauche.strftime("%d/%m/%Y") if s.date_embauche else "—", s_value),
            Paragraph("Salaire de base", s_label),
            Paragraph(_fmt(b.salaire_base), sty("sal_b", fontSize=9,
                      fontName="Helvetica-Bold", textColor=C_DARK)),
        ],
    ]
    cw_sal = [W*0.15, W*0.35, W*0.15, W*0.35]
    sal_tbl = Table(sal_data, colWidths=cw_sal)
    sal_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), C_LIGHT),
        ("LINEBELOW",     (0,0), (-1,0), 0.5, C_BORDER),
        ("LINEBELOW",     (0,-1),(-1,-1),0.5, C_BORDER),
        ("BOX",           (0,0), (-1,-1), 0.5, C_BORDER),
        ("ROWBACKGROUNDS",(0,0), (-1,-1), [C_LIGHT, white]),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
    ]))
    elements.append(sal_tbl)
    elements.append(Spacer(1, 4*mm))

    # ══════════════════════════════════════════════════════════════════════════
    # TABLEAU RÉMUNÉRATION
    # ══════════════════════════════════════════════════════════════════════════
    def ligne(designation, base, taux, gain=None, retenue=None, bold=False):
        fn = "Helvetica-Bold" if bold else "Helvetica"
        col_g = sty("g", fontSize=8, fontName=fn, textColor=C_GAIN if gain else black,
                    alignment=TA_RIGHT)
        col_r = sty("r", fontSize=8, fontName=fn, textColor=C_RET  if retenue else black,
                    alignment=TA_RIGHT)
        return [
            Paragraph(designation, sty("d", fontSize=8, fontName=fn)),
            Paragraph(f"{int(float(base or 0)):,}".replace(",", " ") if base else "", s_cell_r),
            Paragraph(str(taux) if taux else "", s_center),
            Paragraph(_fmt(gain)    if gain    else "", col_g),
            Paragraph(_fmt(retenue) if retenue else "", col_r),
        ]

    rows = []
    # En-tête tableau
    rows.append([
        Paragraph("DÉSIGNATION",       s_hdr),
        Paragraph("BASE",              s_hdr),
        Paragraph("TAUX",              s_hdr),
        Paragraph("GAINS",             s_hdr),
        Paragraph("RETENUES",          s_hdr),
    ])

    # ── Salaire de base ──────────────────────────────────────────────────────
    rows.append(ligne("Salaire de base", _flt(b.salaire_base)/173.33, "173,33h",
                      gain=b.salaire_base, bold=True))

    # ── Heures supplémentaires ───────────────────────────────────────────────
    def add_hsup(label, montant, base_taux, taux_h):
        if _flt(montant) > 0:
            rows.append(ligne(label, _flt(base_taux) if base_taux else "",
                              f"{_flt(taux_h):.2f}h" if taux_h else "",
                              gain=montant))

    add_hsup("H.sup +10%", b.heures_sup_10, b.base_heures_sup_10, b.taux_heures_sup_10)
    add_hsup("H.sup +30%", b.heures_sup_30, b.base_heures_sup_30, b.taux_heures_sup_30)
    add_hsup("H.sup +30% (repos/férié)", getattr(b, "heures_sup_30b", 0),
             getattr(b, "base_heures_sup_30b", 0), getattr(b, "taux_heures_sup_30b", ""))
    add_hsup("H.sup +40% (nuit/dim.)", b.heures_sup_40, b.base_heures_sup_40, b.taux_heures_sup_40)
    add_hsup("H.sup +70% (jours fériés)", b.heures_sup_70, b.base_heures_sup_70, b.taux_heures_sup_70)

    # ── Primes et indemnités ─────────────────────────────────────────────────
    extras = [
        ("Sursalaire",              b.sursalaire),
        ("Prime de caisse",         b.prime_caisse),
        ("Carburant",               b.carburant),
        ("Prime d'ancienneté",      b.prime_anciennete),
        ("Indemnité de logement",   b.indem_logement),
        ("Indemnité eau/élec.",     b.indem_eau_electricite),
        ("Indemnité nourriture",    b.indem_nourriture),
        ("Prime de rendement",      b.prime_rendement),
        ("Prime d'assiduité",       b.prime_assiduité),
        ("Prime de qualité",        b.prime_qualite),
        ("Prime de performance",    b.prime_performance),
        ("Prime de transport",      b.prime_transport),
        ("Prime de responsabilité", b.prime_responsabilite),
        ("Allocations de congé",    b.allocations_conge),
        ("Indem. compensatrice congé", b.indem_compensatrice_conge),
        ("Indem. services rendus",  b.indem_services_rendus),
        ("Indem. préavis",          b.indem_compensatrice_preavis),
        ("Indem. licenciement",     b.indem_licenciement),
    ]
    for label, val in extras:
        if _flt(val) > 0:
            rows.append(ligne(label, "", "", gain=val))

    # ── Composants personnalisés (créés par l'entreprise) ────────────────────
    try:
        from models import BulletinComposant
        comps = BulletinComposant.query.filter_by(bulletin_id=b.id).all()
    except Exception:
        comps = []
    for c in comps:
        base_val = c.base if _flt(c.base) > 0 else ""
        taux_txt = (("%g" % _flt(c.taux)) if _flt(c.taux) else "")
        if str(c.sens).upper() == "GAIN":
            rows.append(ligne(c.libelle, base_val, taux_txt, gain=c.montant))
        else:
            rows.append(ligne(c.libelle, base_val, taux_txt, retenue=c.montant))

    # ── Absences ─────────────────────────────────────────────────────────────
    if _flt(b.absences) > 0:
        rows.append(ligne("Retenue pour absences", "", "", retenue=b.absences))

    # ── Sous-total brut ───────────────────────────────────────────────────────
    rows.append(ligne("SALAIRE BRUT", "", "", gain=b.salaire_brut, bold=True))

    # ── Cotisations sociales ──────────────────────────────────────────────────
    rows.append(ligne(f"CNSS salarié (5% — base {_fmt(b.base_cnss)})",
                      "", "5%", retenue=b.cnss_salarie))
    rows.append(ligne(f"CNAMGS salarié (2% — base {_fmt(b.base_cnamgs)})",
                      "", "2%", retenue=b.cnamgs_salarie))
    rows.append(ligne(f"TCS (5% — base {_fmt(b.base_tcs)})",
                      "", "5%", retenue=b.tcs))
    rows.append(ligne(f"IRPP ({b.salarie.nombre_parts or 1} part(s))",
                      "", "", retenue=b.irpp))

    # ── Hors cotisations ─────────────────────────────────────────────────────
    hors_cot = [
        ("Prime panier (hors cotis.)",     b.prime_panier),
        ("Indem. transport (hors cotis.)", b.indem_transport),
        ("Indem. représentation",          b.indem_representation),
        ("Prime de salubrité",             b.prime_salisure),
    ]
    for label, val in hors_cot:
        if _flt(val) > 0:
            rows.append(ligne(label, "", "", gain=val))

    if _flt(b.acompte) > 0:
        rows.append(ligne("Acompte déduit", "", "", retenue=b.acompte))

    # Largeurs colonnes
    cw = [W*0.44, W*0.16, W*0.09, W*0.155, W*0.155]
    rem_tbl = Table(rows, colWidths=cw, repeatRows=1)

    row_styles = [
        # En-tête
        ("BACKGROUND",    (0,0), (-1,0),  C_DARK),
        ("TEXTCOLOR",     (0,0), (-1,0),  white),
        ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,0),  7.5),
        ("ALIGN",         (0,0), (-1,0),  "CENTER"),
        ("TOPPADDING",    (0,0), (-1,0),  4),
        ("BOTTOMPADDING", (0,0), (-1,0),  4),
        # Corps
        ("FONTSIZE",      (0,1), (-1,-1), 8),
        ("TOPPADDING",    (0,1), (-1,-1), 2),
        ("BOTTOMPADDING", (0,1), (-1,-1), 2),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("GRID",          (0,0), (-1,-1), 0.3, C_BORDER),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [white, C_LIGHT]),
    ]
    # Mettre en évidence les lignes totales (salaire brut)
    for i, row in enumerate(rows):
        if row and hasattr(row[0], 'text'):
            txt = getattr(row[0], 'text', '')
            if 'BRUT' in txt or 'BRUT' in str(txt):
                row_styles.append(("BACKGROUND", (0,i), (-1,i), HexColor("#e8f4fd")))
                row_styles.append(("FONTNAME",   (0,i), (-1,i), "Helvetica-Bold"))

    rem_tbl.setStyle(TableStyle(row_styles))
    elements.append(rem_tbl)
    elements.append(Spacer(1, 4*mm))

    # ══════════════════════════════════════════════════════════════════════════
    # NET À PAYER
    # ══════════════════════════════════════════════════════════════════════════
    net_data = [[
        Paragraph("NET À PAYER", sty("nl", fontSize=13, fontName="Helvetica-Bold",
                  textColor=C_DARK)),
        Paragraph(_fmt(b.net_a_payer), sty("nv", fontSize=18, fontName="Helvetica-Bold",
                  textColor=C_DARK, alignment=TA_RIGHT)),
    ]]
    net_tbl = Table(net_data, colWidths=[W*0.5, W*0.5])
    net_tbl.setStyle(TableStyle([
        ("BOX",           (0,0), (-1,-1), 2, C_DARK),
        ("BACKGROUND",    (0,0), (-1,-1), HexColor("#f8fafc")),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
        ("RIGHTPADDING",  (0,0), (-1,-1), 10),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    elements.append(net_tbl)
    elements.append(Spacer(1, 4*mm))

    # ══════════════════════════════════════════════════════════════════════════
    # CHARGES PATRONALES + SIGNATURES
    # ══════════════════════════════════════════════════════════════════════════
    pat_data = [
        [Paragraph("CHARGES PATRONALES", sty("cp_hdr", fontSize=7.5,
                   fontName="Helvetica-Bold", textColor=C_DARK)),
         "", "", ""],
        [Paragraph("CNSS patronal (18%)", s_label),
         Paragraph(_fmt(b.cnss_patronale), sty("cpv", fontSize=8, alignment=TA_RIGHT)),
         Paragraph("CNAMGS patronal (4.1%)", s_label),
         Paragraph(_fmt(b.cnamgs_patronale), sty("cpv2", fontSize=8, alignment=TA_RIGHT))],
        [Paragraph("FNH (3%)", s_label),
         Paragraph(_fmt(b.fnh), sty("cpv3", fontSize=8, alignment=TA_RIGHT)),
         Paragraph("CFP (0.5%)", s_label),
         Paragraph(_fmt(b.cfp), sty("cpv4", fontSize=8, alignment=TA_RIGHT))],
        [Paragraph("TOTAL CHARGES PATRONALES", sty("cpt", fontSize=8,
                   fontName="Helvetica-Bold")),
         Paragraph(_fmt((_flt(b.cnss_patronale)+_flt(b.cnamgs_patronale)+
                         _flt(b.fnh)+_flt(b.cfp))),
                   sty("cptv", fontSize=8, fontName="Helvetica-Bold",
                       textColor=C_BLUE, alignment=TA_RIGHT)),
         Paragraph("COÛT TOTAL EMPLOYEUR", sty("cpe", fontSize=8, fontName="Helvetica-Bold")),
         Paragraph(_fmt(_flt(b.salaire_brut)+_flt(b.cnss_patronale)+
                        _flt(b.cnamgs_patronale)+_flt(b.fnh)+_flt(b.cfp)),
                   sty("cpev", fontSize=8, fontName="Helvetica-Bold",
                       textColor=C_DARK, alignment=TA_RIGHT))],
    ]
    pat_tbl = Table(pat_data, colWidths=[W*0.28, W*0.22, W*0.28, W*0.22])
    pat_tbl.setStyle(TableStyle([
        ("BOX",           (0,0), (-1,-1), 0.5, C_BORDER),
        ("GRID",          (0,0), (-1,-1), 0.3, C_BORDER),
        ("BACKGROUND",    (0,0), (-1,0),  C_LIGHT),
        ("SPAN",          (0,0), (-1,0)),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [white, C_LIGHT]),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
    ]))
    elements.append(pat_tbl)
    elements.append(Spacer(1, 5*mm))

    # Signatures
    sig_data = [[
        Paragraph("Signature Employé<br/><br/><br/>___________________<br/>"
                  f"{s.nom_complet}", s_center),
        Paragraph("Visa Responsable hiérarchique<br/><br/><br/>___________________", s_center),
        Paragraph("Visa Responsable Site<br/><br/><br/>___________________", s_center),
    ]]
    sig_tbl = Table(sig_data, colWidths=[W/3, W/3, W/3])
    sig_tbl.setStyle(TableStyle([
        ("LINEABOVE",     (0,0), (-1,0), 0.5, C_BORDER),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    elements.append(sig_tbl)
    elements.append(Spacer(1, 3*mm))

    # Pied de page
    elements.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    elements.append(Spacer(1, 1*mm))
    elements.append(Paragraph(
        f"Document généré par PaieGabon le {datetime.now().strftime('%d/%m/%Y à %H:%M')} — "
        f"Statut : <b>{b.statut}</b> — "
        f"Ce bulletin est confidentiel.",
        s_footnote
    ))

    doc.build(elements)
    return buffer.getvalue()
