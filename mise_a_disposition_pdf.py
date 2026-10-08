# -*- coding: utf-8 -*-
"""Génération de la facture de mise à disposition de personnel (PDF).

La facture est émise par le tenant (entreprise prestataire) à l'adresse d'une
entreprise utilisatrice (client), pour un mois donné.
"""
import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)
from reportlab.lib.enums import TA_RIGHT

C_TEAL = HexColor("#0F3D36")
C_OR   = HexColor("#E9B043")
C_GRAY = HexColor("#6b7280")
C_LINE = HexColor("#e5e7eb")
C_SOFT = HexColor("#f4f3ee")


def _fmt(n):
    try:
        return f"{float(n or 0):,.0f}".replace(",", " ")
    except (ValueError, TypeError):
        return "0"


def generer_facture_mad_pdf(tenant, client, lignes, mois, annee, numero, mois_label):
    """lignes : liste de dicts {poste, salarie, mode, unite, quantite, valeur, montant}."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    st_titre = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=20,
                              textColor=C_TEAL, leading=24)
    st_h = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=10,
                          textColor=C_TEAL, leading=13)
    st_n = ParagraphStyle("n", fontName="Helvetica", fontSize=9.5,
                          textColor=black, leading=13)
    st_s = ParagraphStyle("s", fontName="Helvetica", fontSize=8.5,
                          textColor=C_GRAY, leading=11)
    st_r = ParagraphStyle("r", fontName="Helvetica", fontSize=9.5,
                          textColor=black, leading=13, alignment=TA_RIGHT)

    el = []

    # ── En-tête : prestataire + FACTURE ──
    presta = [
        Paragraph(tenant.denomination or "—", st_h),
    ]
    for val in [getattr(tenant, "adresse", None),
                f"NIF : {tenant.nif}" if getattr(tenant, "nif", None) else None,
                f"RCCM : {tenant.rccm}" if getattr(tenant, "rccm", None) else None,
                f"Tél : {tenant.telephone}" if getattr(tenant, "telephone", None) else None]:
        if val:
            presta.append(Paragraph(str(val), st_s))
    droite = [
        Paragraph("FACTURE", st_titre),
        Spacer(1, 2 * mm),
        Paragraph(f"N° {numero}", st_n),
        Paragraph(f"Date : {datetime.now().strftime('%d/%m/%Y')}", st_s),
        Paragraph(f"Période : {mois_label} {annee}", st_s),
    ]
    tete = Table([[presta, droite]], colWidths=[95 * mm, 79 * mm])
    tete.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    el.append(tete)
    el.append(Spacer(1, 6 * mm))
    el.append(HRFlowable(width="100%", thickness=1.2, color=C_OR))
    el.append(Spacer(1, 5 * mm))

    # ── Client ──
    el.append(Paragraph("Facturé à", st_s))
    el.append(Paragraph(client.nom, st_h))
    for val in [client.adresse,
                f"NIF : {client.nif}" if client.nif else None,
                f"À l'attention de {client.contact_nom}" if client.contact_nom else None]:
        if val:
            el.append(Paragraph(str(val), st_n))
    el.append(Spacer(1, 6 * mm))

    # ── Tableau des prestations ──
    head = ["Prestation / Salarié", "Base", "Qté", "P.U. (FCFA)", "Montant (FCFA)"]
    data = [head]
    for l in lignes:
        libelle = l["poste"] or "Mise à disposition"
        sousl = l.get("salarie") or ""
        cell = Paragraph(f"<b>{libelle}</b><br/><font size=8 color='#6b7280'>{sousl}</font>", st_n)
        base = f"{l['mode']}"
        if l["unite"] and l["unite"] != "×":
            qte = _fmt(l["quantite"]) + f" {l['unite']}"
        elif l["unite"] == "×":
            qte = f"×{_fmt(l['valeur'])}"
        else:
            qte = "1"
        pu = _fmt(l["valeur"]) if l["unite"] != "×" else "—"
        data.append([cell, Paragraph(base, st_s), Paragraph(qte, st_r),
                     Paragraph(pu, st_r), Paragraph(_fmt(l["montant"]), st_r)])

    total = sum(float(l["montant"] or 0) for l in lignes)
    data.append(["", "", "", Paragraph("<b>TOTAL</b>", st_r),
                 Paragraph(f"<b>{_fmt(total)}</b>", st_r)])

    t = Table(data, colWidths=[78 * mm, 30 * mm, 20 * mm, 23 * mm, 23 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, C_LINE),
        ("BACKGROUND", (0, -1), (-1, -1), C_SOFT),
        ("LINEABOVE", (0, -1), (-1, -1), 1, C_TEAL),
        ("SPAN", (0, -1), (2, -1)),
    ]))
    el.append(t)
    el.append(Spacer(1, 10 * mm))

    # ── Mentions ──
    el.append(HRFlowable(width="100%", thickness=0.5, color=C_LINE))
    el.append(Spacer(1, 3 * mm))
    el.append(Paragraph(
        "Prestation de mise à disposition de personnel. L'entreprise prestataire "
        "demeure l'employeur juridique des salariés concernés et assure le paiement "
        "des salaires ainsi que les déclarations sociales (CNSS, CNAMGS).", st_s))
    el.append(Spacer(1, 2 * mm))
    el.append(Paragraph(
        "Montants exprimés en francs CFA. Document généré par PaieGabon — à "
        "compléter selon votre régime de TVA le cas échéant.", st_s))

    doc.build(el)
    buf.seek(0)
    return buf.getvalue()
