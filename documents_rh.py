"""
documents_rh.py — Génération des documents RH officiels (PDF)

Produit des documents prêts à imprimer / signer :
    - Attestation de travail (salarié en poste)
    - Certificat de travail (salarié ayant quitté l'entreprise)
    - Attestation de salaire (pour démarches bancaires, visa…)
    - Solde de tout compte (reçu pour solde de tout compte)

Tous les documents réutilisent l'identité visuelle du tenant (logo, dénomination)
et mentionnent les informations légales requises au Gabon.
"""
import io
from datetime import datetime, date

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY

C_DARK   = HexColor("#1a2332")
C_GRAY   = HexColor("#6b7280")
C_LIGHT  = HexColor("#f9fafb")
C_BORDER = HexColor("#e5e7eb")

_MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _date_fr(d):
    """Formate une date en toutes lettres : 8 juin 2026."""
    if not d:
        return "—"
    if isinstance(d, str):
        try:
            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
        except Exception:
            return d
    return f"{d.day} {_MOIS_FR[d.month]} {d.year}"


def _fmt_fcfa(v):
    try:
        return f"{int(float(v or 0)):,}".replace(",", " ") + " FCFA"
    except Exception:
        return "0 FCFA"


def _styles():
    return {
        "titre": ParagraphStyle("titre", fontName="Helvetica-Bold", fontSize=16,
                                textColor=C_DARK, alignment=TA_CENTER, spaceAfter=4),
        "soustitre": ParagraphStyle("soustitre", fontName="Helvetica", fontSize=10,
                                    textColor=C_GRAY, alignment=TA_CENTER, spaceAfter=20),
        "corps": ParagraphStyle("corps", fontName="Helvetica", fontSize=11,
                                textColor=black, alignment=TA_JUSTIFY, leading=18,
                                spaceAfter=12),
        "entete_ent": ParagraphStyle("entete_ent", fontName="Helvetica-Bold", fontSize=13,
                                    textColor=C_DARK, alignment=TA_LEFT),
        "entete_det": ParagraphStyle("entete_det", fontName="Helvetica", fontSize=8.5,
                                    textColor=C_GRAY, alignment=TA_LEFT, leading=12),
        "signature": ParagraphStyle("signature", fontName="Helvetica", fontSize=10,
                                    textColor=black, alignment=TA_RIGHT, leading=16),
        "lieu_date": ParagraphStyle("lieu_date", fontName="Helvetica", fontSize=10,
                                    textColor=black, alignment=TA_RIGHT, spaceAfter=24),
    }


def _entete(tenant, S):
    """En-tête commun : identité de l'entreprise."""
    lignes = []
    details = []
    if tenant.nif:
        details.append(f"NIF : {tenant.nif}")
    if getattr(tenant, "ville", None):
        details.append(tenant.ville)
    if getattr(tenant, "telephone", None):
        details.append(f"Tél : {tenant.telephone}")
    elements = [
        Paragraph(tenant.denomination, S["entete_ent"]),
        Paragraph(" · ".join(details), S["entete_det"]),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1.5, color=C_DARK),
        Spacer(1, 24),
    ]
    return elements


def _signature(tenant, S, ville="Libreville"):
    """Bloc lieu/date + signature en bas de document."""
    return [
        Spacer(1, 30),
        Paragraph(f"Fait à {ville}, le {_date_fr(date.today())}", S["lieu_date"]),
        Spacer(1, 8),
        Paragraph("Pour l'entreprise,<br/>La Direction", S["signature"]),
        Spacer(1, 40),
        Paragraph("_______________________", S["signature"]),
        Paragraph("Signature et cachet", S["entete_det"]),
    ]


def _build(elements):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            topMargin=20*mm, bottomMargin=20*mm,
                            leftMargin=22*mm, rightMargin=22*mm)
    doc.build(elements)
    buffer.seek(0)
    return buffer.read()


# ══════════════════════════════════════════════════════════════════════════════
# ATTESTATION DE TRAVAIL (salarié en poste)
# ══════════════════════════════════════════════════════════════════════════════
def attestation_travail(salarie, tenant) -> bytes:
    S = _styles()
    el = _entete(tenant, S)
    el.append(Paragraph("ATTESTATION DE TRAVAIL", S["titre"]))
    el.append(Spacer(1, 24))

    civilite = "Monsieur" if (salarie.sexe or "").upper().startswith("M") else "Madame"
    texte = (
        f"Je soussigné(e), représentant légal de l'entreprise "
        f"<b>{tenant.denomination}</b>, atteste par la présente que "
        f"<b>{civilite} {salarie.nom_complet}</b>, "
    )
    if salarie.numero_cnss:
        texte += f"immatriculé(e) à la CNSS sous le numéro {salarie.numero_cnss}, "
    texte += (
        f"est employé(e) au sein de notre entreprise depuis le "
        f"<b>{_date_fr(salarie.date_embauche)}</b>"
    )
    if salarie.emploi:
        texte += f", en qualité de <b>{salarie.emploi}</b>"
    texte += "."
    el.append(Paragraph(texte, S["corps"]))

    el.append(Paragraph(
        "La présente attestation est délivrée à l'intéressé(e) pour servir et "
        "valoir ce que de droit.", S["corps"]))

    el += _signature(tenant, S, getattr(tenant, "ville", None) or "Libreville")
    return _build(el)


# ══════════════════════════════════════════════════════════════════════════════
# CERTIFICAT DE TRAVAIL (salarié ayant quitté l'entreprise)
# ══════════════════════════════════════════════════════════════════════════════
def certificat_travail(salarie, tenant, date_sortie=None) -> bytes:
    S = _styles()
    el = _entete(tenant, S)
    el.append(Paragraph("CERTIFICAT DE TRAVAIL", S["titre"]))
    el.append(Spacer(1, 24))

    civilite = "Monsieur" if (salarie.sexe or "").upper().startswith("M") else "Madame"
    sortie = date_sortie or salarie.date_cessation or date.today()

    texte = (
        f"Je soussigné(e), représentant légal de l'entreprise "
        f"<b>{tenant.denomination}</b>, certifie que "
        f"<b>{civilite} {salarie.nom_complet}</b> a été employé(e) "
        f"dans notre entreprise du <b>{_date_fr(salarie.date_embauche)}</b> "
        f"au <b>{_date_fr(sortie)}</b>"
    )
    if salarie.emploi:
        texte += f", en qualité de <b>{salarie.emploi}</b>"
    texte += "."
    el.append(Paragraph(texte, S["corps"]))

    el.append(Paragraph(
        f"{civilite} {salarie.nom_complet} nous quitte libre de tout engagement.",
        S["corps"]))
    el.append(Paragraph(
        "Le présent certificat est délivré à l'intéressé(e) pour servir et "
        "valoir ce que de droit.", S["corps"]))

    el += _signature(tenant, S, getattr(tenant, "ville", None) or "Libreville")
    return _build(el)


# ══════════════════════════════════════════════════════════════════════════════
# ATTESTATION DE SALAIRE
# ══════════════════════════════════════════════════════════════════════════════
def attestation_salaire(salarie, tenant, salaire_brut=None, salaire_net=None) -> bytes:
    S = _styles()
    el = _entete(tenant, S)
    el.append(Paragraph("ATTESTATION DE SALAIRE", S["titre"]))
    el.append(Spacer(1, 24))

    civilite = "Monsieur" if (salarie.sexe or "").upper().startswith("M") else "Madame"
    texte = (
        f"Je soussigné(e), représentant légal de l'entreprise "
        f"<b>{tenant.denomination}</b>, atteste que "
        f"<b>{civilite} {salarie.nom_complet}</b>, "
        f"employé(e) depuis le <b>{_date_fr(salarie.date_embauche)}</b>"
    )
    if salarie.emploi:
        texte += f" en qualité de <b>{salarie.emploi}</b>"
    texte += ", perçoit la rémunération suivante :"
    el.append(Paragraph(texte, S["corps"]))

    data = [["Élément", "Montant"]]
    if salaire_brut is not None:
        data.append(["Salaire brut mensuel", _fmt_fcfa(salaire_brut)])
    if salaire_net is not None:
        data.append(["Salaire net mensuel", _fmt_fcfa(salaire_net)])
    if len(data) > 1:
        t = Table(data, colWidths=[90*mm, 70*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), C_DARK),
            ("TEXTCOLOR", (0,0), (-1,0), HexColor("#ffffff")),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTNAME", (0,1), (-1,-1), "Helvetica"),
            ("FONTSIZE", (0,0), (-1,-1), 10),
            ("ALIGN", (1,0), (1,-1), "RIGHT"),
            ("GRID", (0,0), (-1,-1), 0.5, C_BORDER),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [HexColor("#ffffff"), C_LIGHT]),
            ("TOPPADDING", (0,0), (-1,-1), 7),
            ("BOTTOMPADDING", (0,0), (-1,-1), 7),
            ("LEFTPADDING", (0,0), (-1,-1), 10),
        ]))
        el.append(t)
        el.append(Spacer(1, 16))

    el.append(Paragraph(
        "La présente attestation est délivrée à l'intéressé(e) pour servir et "
        "valoir ce que de droit.", S["corps"]))
    el += _signature(tenant, S, getattr(tenant, "ville", None) or "Libreville")
    return _build(el)


# ══════════════════════════════════════════════════════════════════════════════
# SOLDE DE TOUT COMPTE
# ══════════════════════════════════════════════════════════════════════════════
def solde_tout_compte_pdf(salarie, tenant, solde, date_cessation=None) -> bytes:
    S = _styles()
    el = _entete(tenant, S)
    el.append(Paragraph("REÇU POUR SOLDE DE TOUT COMPTE", S["titre"]))
    el.append(Spacer(1, 20))

    civilite = "Monsieur" if (salarie.sexe or "").upper().startswith("M") else "Madame"
    cessation = date_cessation or solde.get("date_cessation") or date.today()

    el.append(Paragraph(
        f"Concernant <b>{civilite} {salarie.nom_complet}</b>"
        + (f", {salarie.emploi}" if salarie.emploi else "")
        + f", dont le contrat prend fin le <b>{_date_fr(cessation)}</b>.",
        S["corps"]))

    # Tableau détaillé du solde
    data = [["Élément", "Détail", "Montant"]]
    data.append(["Ancienneté",
                 f"{solde.get('anciennete_annees', 0)} an(s)", ""])
    data.append(["Congés acquis non pris",
                 f"{solde.get('jours_restants', 0):.1f} jour(s)",
                 _fmt_fcfa(solde.get("indemnite_conges", 0))])
    if solde.get("indem_licenciement", 0) > 0:
        data.append(["Indemnité de licenciement", "",
                     _fmt_fcfa(solde.get("indem_licenciement", 0))])
    data.append(["", "TOTAL À PAYER",
                 _fmt_fcfa(solde.get("total_a_payer", 0))])

    t = Table(data, colWidths=[70*mm, 50*mm, 45*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), C_DARK),
        ("TEXTCOLOR", (0,0), (-1,0), HexColor("#ffffff")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTNAME", (0,1), (-1,-1), "Helvetica"),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 9.5),
        ("ALIGN", (2,0), (2,-1), "RIGHT"),
        ("GRID", (0,0), (-1,-1), 0.5, C_BORDER),
        ("BACKGROUND", (0,-1), (-1,-1), C_LIGHT),
        ("TOPPADDING", (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
    ]))
    el.append(t)
    el.append(Spacer(1, 20))

    el.append(Paragraph(
        f"Pour solde de tout compte, je soussigné(e) {civilite} "
        f"{salarie.nom_complet} reconnais avoir reçu la somme de "
        f"<b>{_fmt_fcfa(solde.get('total_a_payer', 0))}</b> et déclare n'avoir "
        f"plus aucune réclamation à formuler à l'encontre de l'entreprise "
        f"{tenant.denomination}.", S["corps"]))

    # Double signature
    el.append(Spacer(1, 24))
    sig_data = [[
        Paragraph("Le salarié<br/>(précédé de la mention\n« lu et approuvé »)", S["entete_det"]),
        Paragraph("Pour l'entreprise<br/>La Direction", S["entete_det"]),
    ]]
    sig = Table(sig_data, colWidths=[80*mm, 80*mm])
    sig.setStyle(TableStyle([
        ("TOPPADDING", (0,0), (-1,-1), 50),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    el.append(sig)
    el.append(Paragraph(f"Fait à {getattr(tenant,'ville',None) or 'Libreville'}, "
                        f"le {_date_fr(date.today())}", S["entete_det"]))
    return _build(el)
