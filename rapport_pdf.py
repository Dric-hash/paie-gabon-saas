"""
rapport_pdf.py — Rapport mensuel de paie PDF pour PaieGabon
============================================================
Génère un rapport PDF professionnel organisé PAR SITE :

  1. PAGE DE GARDE
  2. RÉSUMÉ GLOBAL   — KPIs consolidés (salariés + journaliers)
  3. PAR SITE        — une section par site avec :
                         • Tableau des salariés (bulletins)
                         • Tableau des journaliers (feuilles de paie)
                         • Sous-totaux du site
  4. SANS SITE       — salariés/journaliers sans affectation
  5. RÉCAPITULATIF   — charges à verser (CNSS/CNAMGS/TCS/IRPP/FNH/CFP)
"""

import io
from datetime import datetime, date
from reportlab.lib.pagesizes import A4
from reportlab.lib.units    import mm
from reportlab.lib.colors   import HexColor, black, white
from reportlab.lib.styles   import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums    import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus     import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable, PageBreak)

# ── Palette ───────────────────────────────────────────────────────────────────
C_DARK   = HexColor("#1a2332")
C_GREEN  = HexColor("#059669")
C_RED    = HexColor("#dc2626")
C_BLUE   = HexColor("#2563eb")
C_ORANGE = HexColor("#f59e0b")
C_PURPLE = HexColor("#7c3aed")
C_GRAY   = HexColor("#6b7280")
C_LGRAY  = HexColor("#f3f4f6")
C_BORDER = HexColor("#e5e7eb")


def _s(name, **kw):
    base = {
        "h1":    dict(fontSize=20, fontName="Helvetica-Bold", textColor=C_DARK,  spaceAfter=3*mm),
        "h2":    dict(fontSize=13, fontName="Helvetica-Bold", textColor=C_DARK,  spaceBefore=5*mm, spaceAfter=2*mm),
        "h3":    dict(fontSize=10, fontName="Helvetica-Bold", textColor=C_DARK,  spaceBefore=3*mm, spaceAfter=1.5*mm),
        "body":  dict(fontSize=8,  fontName="Helvetica",      textColor=C_DARK,  leading=12),
        "small": dict(fontSize=7,  fontName="Helvetica",      textColor=C_GRAY,  leading=11),
        "bold":  dict(fontSize=8,  fontName="Helvetica-Bold", textColor=C_DARK),
        "green": dict(fontSize=8,  fontName="Helvetica-Bold", textColor=C_GREEN),
        "red":   dict(fontSize=8,  fontName="Helvetica-Bold", textColor=C_RED),
        "blue":  dict(fontSize=8,  fontName="Helvetica-Bold", textColor=C_BLUE),
        "center":dict(fontSize=8,  fontName="Helvetica",      alignment=TA_CENTER),
        "white": dict(fontSize=8,  fontName="Helvetica-Bold", textColor=white),
        "cover": dict(fontSize=28, fontName="Helvetica-Bold", textColor=white,   alignment=TA_CENTER),
        "cover2":dict(fontSize=13, fontName="Helvetica",      textColor=white,   alignment=TA_CENTER),
    }
    cfg = {**base.get(name, {}), **kw}
    return ParagraphStyle(name, **cfg)


def _fcfa(v):
    try: return f"{int(round(float(v or 0))):,}".replace(",", " ") + " F"
    except: return "0 F"

def _pct(part, total):
    try: return f"{part/total*100:.1f}%" if total else "—"
    except: return "—"

def _bar(v, mx, w=18):
    if not mx: return "─"*w
    f = int(v/mx*w)
    return "█"*f + "░"*(w-f)

def _th(cells, widths):
    """Génère une ligne d'en-tête de tableau sombre."""
    row = [Paragraph(c, _s("white")) for c in cells]
    t = Table([row], colWidths=widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0),(-1,-1), C_DARK),
        ("PADDING",    (0,0),(-1,-1), 2*mm),
        ("FONTSIZE",   (0,0),(-1,-1), 7),
        ("ALIGN",      (2,0),(-1,-1), "RIGHT"),
    ]))
    return t


# ═══════════════════════════════════════════════════════════════════════════════
# FONCTION PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════════

def generer_rapport_mensuel(bulletins, periode, tenant,
                             feuilles_journaliers=None,
                             sites=None, affectations=None,
                             evolution=None) -> bytes:
    """
    Args:
        bulletins            : liste BulletinPaie validés
        periode              : PeriodePaie
        tenant               : Tenant
        feuilles_journaliers : liste FeuillePaieJournalier du mois
        sites                : liste Site actifs du tenant
        affectations         : liste AffectationSite actives
        evolution            : 6 mois [{mois, annee, brut, net}]
    """
    feuilles_journaliers = feuilles_journaliers or []
    sites                = sites or []
    affectations         = affectations or []
    evolution            = evolution or []

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=12*mm,  bottomMargin=12*mm,
        title=f"Rapport paie {periode.libelle_complet} — {tenant.denomination}",
        author="PaieGabon SaaS",
    )

    # ── Construire les mappings site → salariés / journaliers ────────────────
    # salarie_id → site
    sal_site = {}
    for a in affectations:
        if a.salarie_id:
            sal_site[a.salarie_id] = a.site
    # journalier_id → site (via affectation ou via feuille.journalier.site_id)
    jour_site = {}
    for a in affectations:
        if a.journalier_id:
            jour_site[a.journalier_id] = a.site

    # Grouper bulletins par site
    bulletins_par_site = {}  # site_id (ou None) → liste bulletins
    for b in bulletins:
        site = sal_site.get(b.salarie_id)
        key  = site.id if site else None
        bulletins_par_site.setdefault(key, {"site": site, "buls": [], "feuilles": []})
        bulletins_par_site[key]["buls"].append(b)

    # Grouper feuilles journaliers par site
    for f in feuilles_journaliers:
        site = jour_site.get(f.journalier_id)
        # Essayer aussi via journalier.site_id si disponible
        if not site and hasattr(f, "journalier") and f.journalier:
            j = f.journalier
            if hasattr(j, "site_id") and j.site_id:
                site = next((s for s in sites if s.id == j.site_id), None)
        key = site.id if site else None
        if key not in bulletins_par_site:
            bulletins_par_site[key] = {"site": site, "buls": [], "feuilles": []}
        bulletins_par_site[key]["feuilles"].append(f)

    # Totaux globaux
    from calculs_paie import calculer_masse_salariale
    masse      = calculer_masse_salariale(bulletins) if bulletins else {}
    total_jour = sum(float(f.montant_brut or 0) for f in feuilles_journaliers)
    nb_jour    = len(feuilles_journaliers)

    story = []

    # ── 1. PAGE DE GARDE ─────────────────────────────────────────────────────
    story += _page_garde(tenant, periode, len(bulletins), nb_jour, sites, masse, total_jour)
    story.append(PageBreak())

    # ── 2. RÉSUMÉ GLOBAL ─────────────────────────────────────────────────────
    story += _section_resume(periode, masse, bulletins, feuilles_journaliers, evolution, sites)
    story.append(PageBreak())

    # ── 3. SECTIONS PAR SITE ─────────────────────────────────────────────────
    # Trier : sites avec nom en premier, None en dernier
    keys_tries = sorted(
        bulletins_par_site.keys(),
        key=lambda k: (k is None, (bulletins_par_site[k]["site"].nom if k is not None and bulletins_par_site[k]["site"] else ""))
    )
    for key in keys_tries:
        grp  = bulletins_par_site[key]
        site = grp["site"]
        buls = grp["buls"]
        feus = grp["feuilles"]
        story += _section_site(site, buls, feus, masse, total_jour)
        story.append(PageBreak())

    # ── 4. RÉCAPITULATIF CHARGES ─────────────────────────────────────────────
    story += _section_recap(masse, total_jour, periode, tenant)

    # Pied de page
    story.append(Spacer(1, 5*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} par PaieGabon SaaS — "
        f"Document confidentiel — {tenant.denomination}",
        _s("small")
    ))

    doc.build(story)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE DE GARDE
# ═══════════════════════════════════════════════════════════════════════════════

def _page_garde(tenant, periode, nb_sal, nb_jour, sites, masse, total_jour):
    e = []
    # Bandeau
    bg = Table(
        [[Paragraph("RAPPORT MENSUEL DE PAIE", _s("cover")),
          Paragraph(f"{periode.libelle_complet} {periode.annee}", _s("cover2"))]],
        colWidths=["60%","40%"]
    )
    bg.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), C_DARK),
        ("PADDING",   (0,0),(-1,-1), 7*mm),
        ("ROWHEIGHT", (0,0),(-1,-1), 30*mm),
        ("VALIGN",    (0,0),(-1,-1), "MIDDLE"),
    ]))
    e.append(bg)
    e.append(Spacer(1, 8*mm))

    # Infos entreprise
    infos = [
        ("Entreprise",  tenant.denomination),
        ("Sigle",       tenant.sigle or "—"),
        ("NIF",         tenant.nif   or "—"),
        ("N° CNSS",     getattr(tenant,"numero_cnss",  None) or "—"),
        ("N° CNAMGS",   getattr(tenant,"numero_cnamgs",None) or "—"),
        ("Ville",       tenant.ville or "Gabon"),
        ("Période",     f"{periode.libelle_complet} {periode.annee}"),
        ("Salariés (bulletins validés)", f"{nb_sal}"),
        ("Journaliers (feuilles de paie)", f"{nb_jour}"),
        ("Nombre de sites", f"{len(sites)}"),
        ("Généré le",   datetime.now().strftime("%d/%m/%Y %H:%M")),
    ]
    t = Table(
        [[Paragraph(k, _s("bold")), Paragraph(str(v), _s("body"))] for k,v in infos],
        colWidths=[55*mm, 110*mm]
    )
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_LGRAY, white]),
        ("PADDING",       (0,0),(-1,-1), 2.5*mm),
        ("LINEBELOW",     (0,0),(-1,-1), 0.3, C_BORDER),
    ]))
    e.append(t)
    e.append(Spacer(1, 6*mm))

    # Badge confidentiel
    bc = Table([[Paragraph("🔒  DOCUMENT CONFIDENTIEL — USAGE INTERNE", _s("center", textColor=HexColor("#92400e")))]])
    bc.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), HexColor("#fef3c7")),
        ("PADDING",   (0,0),(-1,-1), 3.5*mm),
    ]))
    e.append(bc)
    return e


# ═══════════════════════════════════════════════════════════════════════════════
# RÉSUMÉ GLOBAL
# ═══════════════════════════════════════════════════════════════════════════════

def _section_resume(periode, masse, bulletins, feuilles, evolution, sites):
    e = []
    e.append(Paragraph("1. Résumé consolidé", _s("h2")))
    e.append(HRFlowable(width="100%", thickness=1, color=C_DARK))
    e.append(Spacer(1, 3*mm))

    brut_sal     = masse.get("total_brut", 0)
    net_sal      = masse.get("total_net",  0)
    charges_pat  = masse.get("total_charges_pat", 0)
    brut_jour    = sum(float(f.montant_brut or 0) for f in feuilles)
    cout_total   = brut_sal + charges_pat + brut_jour

    # KPIs 2×3
    kpis = [
        [_kpi("Salariés (bulletins)", str(len(bulletins)), C_DARK),
         _kpi("Journaliers (feuilles)", str(len(feuilles)), C_PURPLE),
         _kpi("Sites actifs", str(len(sites)), C_BLUE)],
        [_kpi("Masse brute salariés", _fcfa(brut_sal), C_DARK),
         _kpi("Paie journaliers", _fcfa(brut_jour), C_PURPLE),
         _kpi("Coût total employeur", _fcfa(cout_total), C_RED)],
    ]
    kt = Table(kpis, colWidths=["33%","33%","34%"])
    kt.setStyle(TableStyle([("PADDING",(0,0),(-1,-1), 3*mm)]))
    e.append(kt)
    e.append(Spacer(1, 4*mm))

    # Décomposition
    e.append(Paragraph("Décomposition du coût global", _s("h3")))
    if cout_total > 0:
        lignes = [
            ("Net salariés",       net_sal,             C_GREEN),
            ("Retenues salariales",brut_sal - net_sal,  C_ORANGE),
            ("Charges patronales", charges_pat,         C_RED),
            ("Paie journaliers",   brut_jour,           C_PURPLE),
        ]
        rows = []
        for label, val, col in lignes:
            rows.append([
                Paragraph(label, _s("body")),
                Paragraph(_bar(val, cout_total, 25), ParagraphStyle("bar", fontSize=6, fontName="Courier", textColor=col)),
                Paragraph(_fcfa(val), _s("bold", textColor=col)),
                Paragraph(_pct(val, cout_total), _s("small")),
            ])
        bt = Table(rows, colWidths=[45*mm, 65*mm, 40*mm, 20*mm])
        bt.setStyle(TableStyle([
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_LGRAY, white]),
            ("PADDING",       (0,0),(-1,-1), 2*mm),
        ]))
        e.append(bt)
    e.append(Spacer(1, 4*mm))

    # Évolution 6 mois
    if evolution and len(evolution) > 1:
        e.append(Paragraph("Évolution masse salariale — 6 derniers mois", _s("h3")))
        max_b = max((ev.get("brut",0) for ev in evolution), default=1) or 1
        hdr = ["Mois","Masse brute","Net","Tendance"]
        rows_evo = [[Paragraph(h, _s("white")) for h in hdr]]
        for ev in evolution:
            rows_evo.append([
                Paragraph(f"{ev.get('mois','')} {ev.get('annee','')}", _s("body")),
                Paragraph(_fcfa(ev.get("brut",0)), _s("body")),
                Paragraph(_fcfa(ev.get("net",0)),  _s("green")),
                Paragraph(_bar(ev.get("brut",0), max_b, 22),
                          ParagraphStyle("bar2", fontSize=6, fontName="Courier", textColor=C_BLUE)),
            ])
        et = Table(rows_evo, colWidths=[28*mm, 42*mm, 42*mm, 55*mm])
        et.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  C_DARK),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [white, C_LGRAY]),
            ("PADDING",       (0,0),(-1,-1), 2*mm),
            ("LINEBELOW",     (0,0),(-1,-1), 0.2, C_BORDER),
            ("FONTSIZE",      (0,0),(-1,-1), 7),
        ]))
        e.append(et)

    return e


def _kpi(label, value, color):
    t = Table([
        [Paragraph(label, _s("small"))],
        [Paragraph(value, _s("bold", fontSize=12, textColor=color))],
    ], colWidths=["100%"])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), C_LGRAY),
        ("PADDING",   (0,0),(-1,-1), 2.5*mm),
        ("LINEBELOW", (0,1),(-1,1), 2, color),
    ]))
    return t


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION PAR SITE
# ═══════════════════════════════════════════════════════════════════════════════

def _section_site(site, bulletins, feuilles, masse_globale, total_jour_global):
    e = []
    nom_site = site.nom if site else "Sans site affecté"
    code     = f" ({site.code})" if site and site.code else ""
    ville    = f" — {site.ville}" if site and site.ville else ""
    resp     = f" | Resp. : {site.responsable}" if site and site.responsable else ""

    # En-tête de section site
    titre = Table([[
        Paragraph(f"Site : {nom_site}{code}{ville}", _s("h2", textColor=white, spaceBefore=0, spaceAfter=0)),
        Paragraph(f"{len(bulletins)} salarié(s) · {len(feuilles)} journalier(s){resp}",
                  _s("small", textColor=HexColor("#9ca3af"))),
    ]], colWidths=["60%","40%"])
    titre.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), C_DARK if site else C_GRAY),
        ("PADDING",   (0,0),(-1,-1), 3*mm),
        ("VALIGN",    (0,0),(-1,-1), "MIDDLE"),
    ]))
    e.append(titre)
    e.append(Spacer(1, 3*mm))

    # ── Sous-section : SALARIÉS ───────────────────────────────────────────────
    if bulletins:
        e.append(Paragraph("Salariés", _s("h3", textColor=C_BLUE)))
        cols = [16*mm, 38*mm, 22*mm, 16*mm, 16*mm, 12*mm, 16*mm, 22*mm, 12*mm]
        hdr  = ["Matricule","Nom & Prénom","Brut","CNSS sal.","CNAMGS sal.","TCS","IRPP","Net à payer","Statut"]
        rows = [_tr_header(hdr, cols)]

        ts_brut = ts_net = ts_cnss = ts_cnamgs = ts_tcs = ts_irpp = 0
        for b in sorted(bulletins, key=lambda x: x.salarie.nom):
            s  = b.salarie
            br = float(b.salaire_brut   or 0)
            nt = float(b.net_a_payer    or 0)
            cs = float(b.cnss_salarie   or 0)
            cm = float(b.cnamgs_salarie or 0)
            tc = float(b.tcs            or 0)
            ir = float(b.irpp           or 0)
            ts_brut += br; ts_net += nt; ts_cnss += cs
            ts_cnamgs += cm; ts_tcs += tc; ts_irpp += ir
            sc = C_GREEN if b.statut in ("VALIDÉ","VALIDE") else (C_BLUE if b.statut=="PAYÉ" else C_ORANGE)
            rows.append([
                Paragraph(s.matricule or "—", _s("small")),
                Paragraph(s.nom_complet,      _s("body")),
                Paragraph(_fcfa(br),          _s("body")),
                Paragraph(_fcfa(cs),          _s("small")),
                Paragraph(_fcfa(cm),          _s("small")),
                Paragraph(_fcfa(tc),          _s("small")),
                Paragraph(_fcfa(ir),          _s("small")),
                Paragraph(_fcfa(nt),          _s("green")),
                Paragraph(b.statut,           _s("small", textColor=sc)),
            ])
        # Totaux salariés
        rows.append(_tr_total(["","SOUS-TOTAL",_fcfa(ts_brut),_fcfa(ts_cnss),
                                _fcfa(ts_cnamgs),_fcfa(ts_tcs),_fcfa(ts_irpp),
                                _fcfa(ts_net),""], cols))
        e.append(_table(rows, cols))
        e.append(Spacer(1, 3*mm))

    # ── Sous-section : JOURNALIERS ────────────────────────────────────────────
    if feuilles:
        e.append(Paragraph("Journaliers", _s("h3", textColor=C_PURPLE)))
        cols_j = [38*mm, 25*mm, 22*mm, 22*mm, 22*mm, 25*mm, 12*mm]
        hdr_j  = ["Nom & Prénom","Profession","Période","Nb jours","Taux horaire","Montant brut","Statut"]
        rows_j = [_tr_header(hdr_j, cols_j)]

        tj_brut = tj_jours = 0
        for f in sorted(feuilles, key=lambda x: x.journalier.nom if x.journalier else ""):
            j  = f.journalier
            br = float(f.montant_brut or 0)
            nb = int(f.nb_jours or 0)
            th = float(f.taux_horaire or 0)
            tj_brut  += br
            tj_jours += nb
            periode_str = f"{f.date_debut.strftime('%d/%m') if f.date_debut else '—'} — {f.date_fin.strftime('%d/%m/%Y') if f.date_fin else '—'}"
            sc = C_GREEN if f.statut == "PAYÉ" else (C_ORANGE if f.statut=="EN_ATTENTE" else C_GRAY)
            rows_j.append([
                Paragraph(j.nom_complet if j else "—", _s("body")),
                Paragraph(j.profession  if j else "—", _s("small")),
                Paragraph(periode_str,                 _s("small")),
                Paragraph(str(nb),                     _s("body")),
                Paragraph(_fcfa(th) + "/h",            _s("small")),
                Paragraph(_fcfa(br),                   _s("bold", textColor=C_PURPLE)),
                Paragraph(f.statut,                    _s("small", textColor=sc)),
            ])
        rows_j.append(_tr_total(["SOUS-TOTAL JOURNALIERS","",
                                  f"{tj_jours} jours","","",
                                  _fcfa(tj_brut),""], cols_j))
        e.append(_table(rows_j, cols_j))
        e.append(Spacer(1, 3*mm))

    # ── Récap site ────────────────────────────────────────────────────────────
    if bulletins or feuilles:
        site_brut_sal = sum(float(b.salaire_brut or 0) for b in bulletins)
        site_net_sal  = sum(float(b.net_a_payer  or 0) for b in bulletins)
        site_brut_j   = sum(float(f.montant_brut or 0) for f in feuilles)
        site_total    = site_brut_sal + site_brut_j
        recap = Table([
            [Paragraph(f"Masse salariale brute (salariés) : {_fcfa(site_brut_sal)}", _s("body")),
             Paragraph(f"Net à payer (salariés) : {_fcfa(site_net_sal)}", _s("green")),
             Paragraph(f"Paie journaliers : {_fcfa(site_brut_j)}", _s("bold", textColor=C_PURPLE)),
             Paragraph(f"Coût brut total site : {_fcfa(site_total)}", _s("bold", textColor=C_RED))],
        ], colWidths=["25%","25%","25%","25%"])
        recap.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1), HexColor("#f0fdf4")),
            ("PADDING",   (0,0),(-1,-1), 2.5*mm),
            ("LINEABOVE", (0,0),(-1,0), 1, C_GREEN),
        ]))
        e.append(recap)

    return e


def _tr_header(cells, widths):
    row = [Paragraph(c, _s("white")) for c in cells]
    t = Table([row], colWidths=widths)
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), C_DARK),
        ("PADDING",   (0,0),(-1,-1), 1.8*mm),
        ("ALIGN",     (2,0),(-1,-1), "RIGHT"),
        ("FONTSIZE",  (0,0),(-1,-1), 6.5),
    ]))
    return t


def _tr_total(cells, widths):
    row = [Paragraph(str(c), _s("bold", textColor=white)) for c in cells]
    t = Table([row], colWidths=widths)
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), HexColor("#374151")),
        ("PADDING",   (0,0),(-1,-1), 1.8*mm),
        ("ALIGN",     (2,0),(-1,-1), "RIGHT"),
        ("FONTSIZE",  (0,0),(-1,-1), 7),
    ]))
    return t


def _table(rows, widths):
    """Transforme une liste de Table-rows en un vrai Table."""
    # Les rows sont déjà des Table (header/total) ou des listes de Paragraph
    # On doit extraire les données pour en faire un seul Table
    # Stratégie : wrapper dans un Table de tables (approche compatible ReportLab)
    from reportlab.platypus import KeepTogether
    elements = []
    for row in rows:
        if isinstance(row, Table):
            elements.append(row)
        else:
            t = Table([row], colWidths=widths)
            t.setStyle(TableStyle([
                ("ROWBACKGROUNDS",(0,0),(-1,-1),[white, C_LGRAY]),
                ("PADDING",       (0,0),(-1,-1), 1.5*mm),
                ("ALIGN",         (2,0),(-1,-1), "RIGHT"),
                ("FONTSIZE",      (0,0),(-1,-1), 7),
                ("LINEBELOW",     (0,0),(-1,-1), 0.2, C_BORDER),
            ]))
            elements.append(t)
    return KeepTogether(elements) if len(elements) <= 30 else elements[0]


# ═══════════════════════════════════════════════════════════════════════════════
# RÉCAPITULATIF CHARGES
# ═══════════════════════════════════════════════════════════════════════════════

def _section_recap(masse, total_jour, periode, tenant):
    e = []
    e.append(Paragraph("4. Récapitulatif des charges à verser", _s("h2")))
    e.append(HRFlowable(width="100%", thickness=1, color=C_DARK))
    e.append(Spacer(1, 3*mm))

    cs  = masse.get("total_cnss_sal",    0)
    cp  = masse.get("total_cnss_pat",    0)
    ms  = masse.get("total_cnamgs_sal",  0)
    mp  = masse.get("total_cnamgs_pat",  0)
    tcs = masse.get("total_tcs",         0)
    ir  = masse.get("total_irpp",        0)
    fnh = masse.get("total_fnh",         0)
    cfp = masse.get("total_cfp",         0)

    data = [
        [Paragraph(h, _s("white")) for h in
         ["Organisme","Part salarié","Part patronal","Total à verser","Périodicité"]],
        [Paragraph("CNSS",   _s("body")), Paragraph(_fcfa(cs), _s("body")),
         Paragraph(_fcfa(cp), _s("body")),
         Paragraph(_fcfa(cs+cp), _s("red")), Paragraph("Trimestriel", _s("small"))],
        [Paragraph("CNAMGS", _s("body")), Paragraph(_fcfa(ms), _s("body")),
         Paragraph(_fcfa(mp), _s("body")),
         Paragraph(_fcfa(ms+mp), _s("red")), Paragraph("Trimestriel", _s("small"))],
        [Paragraph("TCS",    _s("body")), Paragraph(_fcfa(tcs),_s("body")),
         Paragraph("—", _s("small")),
         Paragraph(_fcfa(tcs), _s("bold", textColor=C_ORANGE)), Paragraph("Mensuel", _s("small"))],
        [Paragraph("IRPP",   _s("body")), Paragraph(_fcfa(ir), _s("body")),
         Paragraph("—", _s("small")),
         Paragraph(_fcfa(ir),  _s("bold", textColor=C_ORANGE)), Paragraph("Mensuel", _s("small"))],
        [Paragraph("FNH",    _s("body")), Paragraph("—", _s("small")),
         Paragraph(_fcfa(fnh),_s("body")),
         Paragraph(_fcfa(fnh), _s("body")), Paragraph("Mensuel", _s("small"))],
        [Paragraph("CFP",    _s("body")), Paragraph("—", _s("small")),
         Paragraph(_fcfa(cfp),_s("body")),
         Paragraph(_fcfa(cfp), _s("body")), Paragraph("Mensuel", _s("small"))],
        [Paragraph("Paie journaliers", _s("bold", textColor=C_PURPLE)),
         Paragraph("—",_s("small")), Paragraph("—",_s("small")),
         Paragraph(_fcfa(total_jour), _s("bold", textColor=C_PURPLE)),
         Paragraph("—", _s("small"))],
        [Paragraph("TOTAL GÉNÉRAL", _s("bold")),
         Paragraph("",_s("body")), Paragraph("",_s("body")),
         Paragraph(_fcfa(cs+cp+ms+mp+tcs+ir+fnh+cfp+total_jour),
                   _s("bold", fontSize=11, textColor=C_RED)),
         Paragraph("",_s("body"))],
    ]

    rt = Table(data, colWidths=[32*mm, 32*mm, 32*mm, 42*mm, 32*mm])
    rt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  C_DARK),
        ("ROWBACKGROUNDS",(0,1), (-1,-2), [white, C_LGRAY]),
        ("ALIGN",         (1,0), (-1,-1), "RIGHT"),
        ("PADDING",       (0,0), (-1,-1), 2*mm),
        ("FONTSIZE",      (0,0), (-1,-1), 7.5),
        ("LINEBELOW",     (0,0), (-1,-1), 0.2, C_BORDER),
        ("BACKGROUND",    (0,-1),(-1,-1), HexColor("#fef2f2")),
        ("LINEABOVE",     (0,-1),(-1,-1), 1.5, C_RED),
    ]))
    e.append(rt)
    e.append(Spacer(1, 5*mm))

    # Note légale
    e.append(Paragraph("Obligations légales — Gabon", _s("h3")))
    for txt in [
        "CNSS/CNAMGS : déclaration et versement dans les 30 jours suivant la fin du trimestre.",
        "TCS, IRPP, FNH, CFP : versement mensuel au plus tard le dernier jour du mois suivant.",
        "Journaliers : pas de cotisations CNSS/CNAMGS sur les rémunérations journalières (sauf contrat spécifique).",
    ]:
        e.append(Paragraph(f"• {txt}", _s("small")))

    return e
