"""
pdf_bulletin.py — Génération PDF des bulletins de paie avec reportlab
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                  TableStyle, HRFlowable, PageBreak)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io
from datetime import datetime, date

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
    """Génère le PDF d'un bulletin de paie (un seul). Retourne les bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=10*mm,  bottomMargin=10*mm,
        title=f"Bulletin de paie — {bulletin.salarie.nom_complet}",
    )
    doc.build(_elements_bulletin(bulletin, tenant))
    return buffer.getvalue()


def generer_bulletins_pdf(bulletins, tenant, modele=None) -> bytes:
    """Génère UN SEUL PDF regroupant TOUS les bulletins (un par page).
    Mise en page « détaillé » si modele == 'sgtg'."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=10*mm,  bottomMargin=10*mm,
        title="Bulletins de paie",
    )
    _build = _elements_bulletin_detaille if modele == "sgtg" else _elements_bulletin
    elements = []
    for i, b in enumerate(bulletins):
        if i > 0:
            elements.append(PageBreak())
        elements.extend(_build(b, tenant))
    doc.build(elements)
    return buffer.getvalue()


def _elements_bulletin(bulletin, tenant):
    """Construit la liste des flowables d'UN bulletin (styles + contenu)."""
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

    # Signatures — employeur + employé uniquement
    sig_data = [[
        Paragraph("L'Employeur<br/><br/><br/>___________________<br/>"
                  f"{tenant.denomination}", s_center),
        Paragraph("L'Employé<br/><br/><br/>___________________<br/>"
                  f"{s.nom_complet}", s_center),
    ]]
    sig_tbl = Table(sig_data, colWidths=[W/2, W/2])
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

    return elements


# ═══════════════════════════════════════════════════════════════════════════
#  MODÈLE « DÉTAILLÉ » — reproduction du bulletin dense (type traditionnel)
# ═══════════════════════════════════════════════════════════════════════════
def _nf(v):
    """Nombre formaté avec espaces, vide si 0/None."""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return ""
    if n == 0:
        return ""
    return f"{int(round(n)):,}".replace(",", " ")

def _tx(part, base):
    """Taux = part/base*100, vide si base nulle."""
    try:
        p = float(part or 0); b = float(base or 0)
    except (TypeError, ValueError):
        return ""
    if b == 0:
        return ""
    return f"{p / b * 100:.1f}".replace(".", ",")


def _elements_bulletin_detaille(bulletin, tenant):
    """Flowables reportlab du bulletin au format « Détaillé »."""
    from reportlab.lib.units import mm
    b = bulletin; s = bulletin.salarie; p = getattr(bulletin, "periode", None)
    GRAY = HexColor("#c9c9c9"); LGRAY = HexColor("#dcdcdc"); BORD = HexColor("#999999")
    W = 186 * mm

    def P(txt, size=9, bold=False, align=TA_LEFT, color=C_DARK):
        st = ParagraphStyle(f"d{id(txt)}{size}{align}", fontName="Helvetica-Bold" if bold else "Helvetica",
                            fontSize=size, leading=size + 2, textColor=color, alignment=align)
        return Paragraph(str(txt), st)

    def R(align):  # right/left align helper for cells
        return align

    el = []

    # ── En-tête ──
    import calendar as _cal
    _dd = getattr(p, "date_debut", None); _df = getattr(p, "date_fin", None)
    if p and not _dd:
        try: _dd = date(p.annee, p.mois, 1)
        except Exception: _dd = None
    if p and not _df:
        try: _df = date(p.annee, p.mois, _cal.monthrange(p.annee, p.mois)[1])
        except Exception: _df = None
    per_tbl = Table([
        [P("PERIODE :", 8, True), P((p.libelle_mois or "").upper() if p else "", 8, True, TA_CENTER),
         P(p.annee if p else "", 8, True, TA_RIGHT)],
        [P("DU " + (_dd.strftime("%d/%m/%Y") if _dd else ""), 7),
         P("AU", 7, False, TA_CENTER),
         P(_df.strftime("%d/%m/%Y") if _df else "", 7, False, TA_RIGHT)],
    ], colWidths=[W*0.5*0.4, W*0.5*0.25, W*0.5*0.35])
    per_tbl.setStyle(TableStyle([("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
                                 ("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),2)]))
    edite = ""
    if getattr(b, "date_creation", None):
        edite = "Edité le : " + b.date_creation.strftime("%d/%m/%Y à %H:%M:%S")
    head = Table([[ [P(edite, 7), P("Bulletin de paie", 12, True, TA_CENTER)], per_tbl ]],
                 colWidths=[W*0.5, W*0.5])
    head.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,BORD),("LINEAFTER",(0,0),(0,0),0.6,BORD),
                              ("VALIGN",(0,0),(-1,-1),"TOP"),("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
    el.append(head)
    band = Table([[P("Matricule - Nom - Prénom - Adresse", 8, True, TA_CENTER)]], colWidths=[W])
    band.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),GRAY),("BOX",(0,0),(-1,-1),0.6,BORD),
                              ("TOPPADDING",(0,0),(-1,-1),4.5),("BOTTOMPADDING",(0,0),(-1,-1),4.5)]))
    el.append(band)

    # ── Identité société / salarié ──
    soc = [P(tenant.denomination, 8, True)]
    if getattr(tenant, "adresse", None): soc.append(P(tenant.adresse, 8))
    soc.append(P((tenant.ville or ""), 8))
    if getattr(tenant, "nif", None): soc.append(P("NIF : " + tenant.nif, 8))
    soc.append(P("N° CNSS : " + (tenant.numero_cnss or ""), 8))
    soc.append(P("N° CNAMGS : " + (tenant.numero_cnamgs or ""), 8))
    cat = (s.categorie.libelle or s.categorie.code) if getattr(s, "categorie", None) else (getattr(s, "niveau", "") or "")
    anc = ""
    if getattr(s, "date_embauche", None) and p:
        anc = f"{(p.annee - s.date_embauche.year)*12 + p.mois - s.date_embauche.month} mois"
    parts = ""
    if getattr(s, "nombre_parts", None):
        parts = f"{float(s.nombre_parts):.1f}".replace(".", ",")
    emp = Table([
        [P(f"{s.matricule} — {s.nom_complet}", 8, True), P("", 8)],
        [P("Tél : " + (getattr(s,'telephone','') or ""), 8), P(getattr(s,"nationalite","") or "GABON", 8, True, TA_RIGHT)],
        [P("Emploi : " + (getattr(s,'emploi','') or ""), 8), P("Catégorie : " + cat, 8, False, TA_RIGHT)],
        [P("N° CNSS : " + (getattr(s,'numero_cnss','') or ""), 8), P("Sit.Fam : " + (getattr(s,'situation_matrimoniale','') or ""), 8, False, TA_RIGHT)],
        [P("N° CNAMGS : " + (getattr(s,'numero_cnamgs','') or ""), 8), P("Nbre de part : " + parts, 8, False, TA_RIGHT)],
        [P("Date d'Embauche : " + (s.date_embauche.strftime("%d/%m/%Y") if getattr(s,'date_embauche',None) else ""), 8),
         P("Ancienneté : " + anc, 8, False, TA_RIGHT)],
    ], colWidths=[W*0.56*0.5, W*0.56*0.5])
    emp.setStyle(TableStyle([("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
                             ("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),2)]))
    ident = Table([[soc, emp]], colWidths=[W*0.44, W*0.56])
    ident.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,BORD),("LINEAFTER",(0,0),(0,0),0.5,C_BORDER),
                               ("VALIGN",(0,0),(-1,-1),"TOP"),("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
    el.append(Spacer(1, 7)); el.append(ident)

    # ── Rubriques ──
    def g(name): return getattr(b, name, 0)
    rows = [[P("Rubriques",7,True), P("Base",7,True,TA_RIGHT), P("Taux",7,True,TA_RIGHT),
             P("Gains",7,True,TA_RIGHT), P("Retenues",7,True,TA_RIGHT)]]
    def rub(lib, base, taux, gain, ret, force=False):
        if not force and not (_flt(gain) or _flt(ret)):
            return
        rows.append([P(lib,8), P(_nf(base),8,False,TA_RIGHT), P(str(taux) if taux else "",8,False,TA_RIGHT),
                     P(_nf(gain),8,False,TA_RIGHT), P(_nf(ret),8,False,TA_RIGHT)])
    # Config des rubriques souples
    try:
        from models import ConfigRubrique
        _cfg = {c.cle: c for c in ConfigRubrique.query.filter_by(tenant_id=tenant.id).all()}
    except Exception:
        _cfg = {}
    def _pos(cle):
        c = _cfg.get(cle); return c.position if c else "BAS"
    _configs = [("Prime de panier", g("prime_panier"), "panier"),
                ("Transport net", g("indem_transport"), "transport"),
                ("Représentation", g("indem_representation"), "representation"),
                ("Salisure", g("prime_salisure"), "salisure")]
    # composants HAUT
    try:
        from models import BulletinComposant
        comps = BulletinComposant.query.filter_by(bulletin_id=b.id).all()
    except Exception:
        comps = []
    for c in comps:
        if getattr(c, "composant", None) and c.composant.position == "HAUT":
            rub(c.composant.libelle, c.base, (c.taux or ""), c.montant if c.composant.est_gain else None,
                c.montant if not c.composant.est_gain else None)
    # configurables HAUT
    for lib, mt, cle in _configs:
        if _pos(cle) == "HAUT" and _flt(mt):
            rub(lib, None, None, mt, None)
    rub("Salaire de base", g("base_salaire_base"), g("taux_salaire"), g("salaire_base"), None, force=True)
    rub("Sursalaire", g("base_sursalaire"), g("taux_sursalaire"), g("sursalaire"), None)
    rub("Heures supplémentaires +10%", None, None, g("heures_sup_10"), None)
    rub("Heures supplémentaires +30%", None, None, g("heures_sup_30"), None)
    rub("Heures supplémentaires +40%", None, None, g("heures_sup_40"), None)
    rub("Heures supplémentaires +70%", None, None, g("heures_sup_70"), None)
    rub("Absence", g("base_absences"), g("taux_absences"), None, g("absences"))
    rub("Prime d'ancienneté", g("base_prime_anciennete"), None, g("prime_anciennete"), None)
    rub("Prime de transport", g("base_prime_transport"), None, g("prime_transport"), None)
    rub("Prime de responsabilité", None, None, g("prime_responsabilite"), None)
    rub("Carburant", g("base_carburant"), None, g("carburant"), None)
    rub("Prime de performance", None, None, g("prime_performance"), None)
    rub("Prime de rendement", None, None, g("prime_rendement"), None)
    rub("Indemnités de licenciement", g("base_indem_licenciement"), None, g("indem_licenciement"), None)
    rub("Prime de qualité", g("base_prime_qualite"), None, g("prime_qualite"), None)
    rub("Allocations de congé", g("base_allocations_conge"), None, g("allocations_conge"), None)
    rub("Indem. Compens./congé (ancienneté)", g("base_indem_compensatrice_conge"), None, g("indem_compensatrice_conge"), None)
    rub("Indemnités de services rendus", g("base_indem_services_rendus"), None, g("indem_services_rendus"), None)
    rub("Indemnités comp. de préavis", g("base_indem_compensatrice_preavis"), None, g("indem_compensatrice_preavis"), None)
    rub("Indemnité de logement", g("base_indem_logement"), None, g("indem_logement"), None)
    for c in comps:
        if getattr(c, "composant", None) and c.composant.position != "HAUT":
            rub(c.composant.libelle, c.base, (c.taux or ""), c.montant if c.composant.est_gain else None,
                c.montant if not c.composant.est_gain else None)
    rows.append([P("*** Salaire Brut ***",8,True,TA_CENTER), P(""), P(""), P(_nf(g("salaire_brut")),8,True,TA_RIGHT), P("")])
    rub_tbl = Table(rows, colWidths=[W*0.40, W*0.15, W*0.12, W*0.16, W*0.17])
    rub_st = [("BACKGROUND",(0,0),(-1,0),GRAY),("BACKGROUND",(0,-1),(-1,-1),LGRAY),
              ("BOX",(0,0),(-1,-1),0.6,BORD),("LINEBELOW",(0,0),(-1,-2),0.3,C_BORDER),
              ("TOPPADDING",(0,0),(-1,-1),4.5),("BOTTOMPADDING",(0,0),(-1,-1),4.5),
              ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4)]
    rub_tbl.setStyle(TableStyle(rub_st))
    el.append(Spacer(1, 7)); el.append(rub_tbl)

    # ── Cotisations ──
    cot = [[P("Cotisations et contributions sociales",7,True), P("Base",7,True,TA_RIGHT),
            P("Taux salar.",7,True,TA_RIGHT), P("Part salarié",7,True,TA_RIGHT),
            P("Taux Patron.",7,True,TA_RIGHT), P("Part Employeur",7,True,TA_RIGHT)]]
    def crow(lib, base, txs, ps, txp, pe):
        cot.append([P(lib,8), P(_nf(base),8,False,TA_RIGHT), P(txs,8,False,TA_RIGHT),
                    P(_nf(ps),8,False,TA_RIGHT), P(txp,8,False,TA_RIGHT), P(_nf(pe),8,False,TA_RIGHT)])
    crow("CNSS", g("base_cnss"), _tx(g("cnss_salarie"),g("base_cnss")), g("cnss_salarie"), _tx(g("cnss_patronale"),g("base_cnss")), g("cnss_patronale"))
    crow("CNAMGS", g("base_cnamgs"), _tx(g("cnamgs_salarie"),g("base_cnamgs")), g("cnamgs_salarie"), _tx(g("cnamgs_patronale"),g("base_cnamgs")), g("cnamgs_patronale"))
    crow("TCS", g("base_tcs"), _tx(g("tcs"),g("base_tcs")), g("tcs"), "", None)
    crow("IRPP (-)", g("base_irpp"), "-", g("irpp"), "", None)
    crow("Fonds National de l'Habitat", g("base_cnss"), "", None, _tx(g("fnh"),g("base_cnss")), g("fnh"))
    crow("Contrib. à la Formation Professionnelle", g("base_cnss"), "", None, _tx(g("cfp"),g("base_cnss")), g("cfp"))
    tot_sal = _flt(g("cnss_salarie"))+_flt(g("cnamgs_salarie"))+_flt(g("tcs"))+_flt(g("irpp"))
    tot_pat = _flt(g("cnss_patronale"))+_flt(g("cnamgs_patronale"))+_flt(g("fnh"))+_flt(g("cfp"))
    cot.append([P("Total cotisations et contributions",8,True), P(""), P(""),
                P(_nf(tot_sal),8,True,TA_RIGHT), P(""), P(_nf(tot_pat),8,True,TA_RIGHT)])
    cot_tbl = Table(cot, colWidths=[W*0.40, W*0.12, W*0.12, W*0.12, W*0.12, W*0.12])
    cot_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),GRAY),("BACKGROUND",(0,-1),(-1,-1),LGRAY),
                                 ("BOX",(0,0),(-1,-1),0.6,BORD),("LINEBELOW",(0,0),(-1,-2),0.3,C_BORDER),
                                 ("TOPPADDING",(0,0),(-1,-1),4.5),("BOTTOMPADDING",(0,0),(-1,-1),4.5),
                                 ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4)]))
    el.append(cot_tbl)

    # ── Net ──
    net_avant = _flt(g("net_a_payer")) + _flt(g("irpp"))
    _net_rows = [[P("Net à payer avant impôt sur le revenu",8), P(_nf(net_avant),8,True,TA_RIGHT)]]
    for lib, mt, cle in _configs:
        if _pos(cle) != "HAUT" and _flt(mt):
            _net_rows.append([P(lib + " (+)",8,color=C_GRAY), P(_nf(mt),8,False,TA_RIGHT)])
    if _flt(g("acompte")):
        _net_rows.append([P("Acompte (-)",8,color=C_GRAY), P(_nf(g("acompte")),8,False,TA_RIGHT)])
    net_left = Table(_net_rows, colWidths=[W*0.55*0.7, W*0.55*0.3])
    net_left.setStyle(TableStyle([("TOPPADDING",(0,0),(-1,-1),4.5),("BOTTOMPADDING",(0,0),(-1,-1),4.5),("LEFTPADDING",(0,0),(-1,-1),4)]))
    net_box = Table([[P("Net payé en F CFA",13,False,TA_CENTER)],[P(_nf(g("net_a_payer"))+" XAF",24,True,TA_CENTER)]],
                    colWidths=[W*0.45])
    net_box.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),GRAY),("TOPPADDING",(0,0),(-1,-1),20),("BOTTOMPADDING",(0,0),(-1,-1),20),
                                 ("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    net = Table([[net_left, net_box]], colWidths=[W*0.55, W*0.45])
    net.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,BORD),("VALIGN",(0,0),(-1,-1),"TOP")]))
    el.append(net)

    # ── Cumuls ──
    cumuls = None
    try:
        from models import BulletinPaie, PeriodePaie
        if p:
            bs = (BulletinPaie.query.join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
                  .filter(BulletinPaie.tenant_id == tenant.id, BulletinPaie.salarie_id == b.salarie_id,
                          PeriodePaie.annee == p.annee).all())
            cs = sum(_flt(x.cnss_salarie)+_flt(x.cnamgs_salarie)+_flt(x.tcs)+_flt(x.irpp) for x in bs)
            cp = sum(_flt(x.cnss_patronale)+_flt(x.cnamgs_patronale)+_flt(x.fnh)+_flt(x.cfp) for x in bs)
            bc = sum(_flt(x.salaire_brut) for x in bs)
            cumuls = {"jours": sum(_flt(x.nb_jours_travailles) for x in bs), "brut": bc,
                      "net_impos": sum(_flt(x.base_irpp) for x in bs), "cs": cs, "cp": cp,
                      "cg": cs+cp, "cout": bc+cp}
    except Exception:
        cumuls = None
    if cumuls:
        cu = [[P("Cumuls",7,True), P("Jours",7,True,TA_RIGHT), P("Brut",7,True,TA_RIGHT), P("Net impos.",7,True,TA_RIGHT),
               P("Cot. Salar.",7,True,TA_RIGHT), P("Cot. Patron.",7,True,TA_RIGHT), P("Cot. Global.",7,True,TA_RIGHT), P("Coût total",7,True,TA_RIGHT)],
              [P(f"Année {p.annee}",8,True), P(str(int(cumuls['jours'])),8,False,TA_RIGHT), P(_nf(cumuls['brut']),8,False,TA_RIGHT),
               P(_nf(cumuls['net_impos']),8,False,TA_RIGHT), P(_nf(cumuls['cs']),8,False,TA_RIGHT), P(_nf(cumuls['cp']),8,False,TA_RIGHT),
               P(_nf(cumuls['cg']),8,False,TA_RIGHT), P(_nf(cumuls['cout']),8,False,TA_RIGHT)]]
        cu_tbl = Table(cu, colWidths=[W*0.16]+[W*0.12]*7)
        cu_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),GRAY),("BOX",(0,0),(-1,-1),0.6,BORD),
                                    ("TOPPADDING",(0,0),(-1,-1),4.5),("BOTTOMPADDING",(0,0),(-1,-1),4.5),
                                    ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4)]))
        el.append(Spacer(1,7)); el.append(cu_tbl)

    # ── Congés ──
    try:
        conges = sorted(getattr(s, "conges", []) or [], key=lambda c: c.annee or 0)
    except Exception:
        conges = []
    if conges:
        cg = [[P("Congés payés",7,True), P("Dûs (jrs)",7,True,TA_RIGHT), P("Acquis (jrs)",7,True,TA_RIGHT),
               P("Pris (jrs)",7,True,TA_RIGHT), P("Restant (jrs)",7,True,TA_RIGHT)]]
        for c in conges:
            ac = _flt(c.jours_acquis); pr = _flt(c.jours_pris)
            fmt2 = lambda x: (f"{x:.2f}".replace(".", ",") if x else "-")
            cg.append([P(f"CP {c.annee}",8), P(fmt2(ac),8,False,TA_RIGHT), P(fmt2(ac),8,False,TA_RIGHT),
                       P(fmt2(pr),8,False,TA_RIGHT), P(f"{ac-pr:.2f}".replace(".",","),8,True,TA_RIGHT)])
        cg_tbl = Table(cg, colWidths=[W*0.30, W*0.175, W*0.175, W*0.175, W*0.175])
        cg_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),GRAY),("BOX",(0,0),(-1,-1),0.6,BORD),
                                    ("TOPPADDING",(0,0),(-1,-1),4.5),("BOTTOMPADDING",(0,0),(-1,-1),4.5),
                                    ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4)]))
        el.append(cg_tbl)

    # ── Signatures ──
    sig = Table([[P("EMPLOYEUR",8,True), P("EMPLOYE",8,True)]], colWidths=[W*0.5, W*0.5], rowHeights=[135])
    sig.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,BORD),("LINEAFTER",(0,0),(0,0),0.6,BORD),
                             ("VALIGN",(0,0),(-1,-1),"TOP"),("TOPPADDING",(0,0),(-1,-1),3),("LEFTPADDING",(0,0),(-1,-1),4)]))
    el.append(Spacer(1,10)); el.append(sig)
    el.append(Spacer(1,7))
    el.append(P("Dans votre intérêt et pour vous aider à faire valoir vos droits, conservez ce bulletin sans limitation de durée. "
                "L'entreprise adhère à la convention collective.", 7, False, TA_LEFT, C_GRAY))
    return el


def generer_bulletin_detaille_pdf(bulletin, tenant) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=12*mm, rightMargin=12*mm,
                            topMargin=8*mm, bottomMargin=8*mm,
                            title=f"Bulletin de paie — {bulletin.salarie.nom_complet}")
    doc.build(_elements_bulletin_detaille(bulletin, tenant))
    return buffer.getvalue()
