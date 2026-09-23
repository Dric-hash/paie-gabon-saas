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
                                TableStyle, HRFlowable, Image)
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


def _logo_flowable(tenant, max_w=44*mm, max_h=20*mm):
    """Construit l'image du logo à partir de tenant.logo_url (data URI base64).
    Renvoie None si absent/illisible — le document ne casse jamais pour un logo."""
    url = getattr(tenant, "logo_url", None)
    if not url or "base64," not in url:
        return None
    try:
        import base64
        raw = base64.b64decode(url.split("base64,", 1)[1])
        try:
            from PIL import Image as PILImage
            iw, ih = PILImage.open(io.BytesIO(raw)).size
            ratio = min(max_w / iw, max_h / ih)
            w, h = iw * ratio, ih * ratio
        except Exception:
            w, h = max_h, max_h
        return Image(io.BytesIO(raw), width=w, height=h)
    except Exception:
        return None


def _entete(tenant, S):
    """En-tête commun : logo (si présent) + identité de l'entreprise."""
    details = []
    if tenant.nif:
        details.append(f"NIF : {tenant.nif}")
    if getattr(tenant, "ville", None):
        details.append(tenant.ville)
    if getattr(tenant, "telephone", None):
        details.append(f"Tél : {tenant.telephone}")
    texte = [
        Paragraph(tenant.denomination, S["entete_ent"]),
        Paragraph(" · ".join(details), S["entete_det"]),
    ]
    logo = _logo_flowable(tenant)
    if logo is not None:
        bloc = Table([[logo, texte]], colWidths=[48*mm, None])
        bloc.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 10),
        ]))
        head = [bloc]
    else:
        head = texte
    return head + [
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1.5, color=C_DARK),
        Spacer(1, 24),
    ]


def _signature(tenant, S, ville=None):
    """Bloc lieu/date + signature, nominatif : nomme le représentant légal."""
    ville = ville or getattr(tenant, "ville", None) or "Libreville"
    nom = (getattr(tenant, "representant_nom", None) or "").strip()
    fonction = (getattr(tenant, "representant_fonction", None) or "").strip()
    if nom:
        qui = f"{fonction}<br/>{nom}" if fonction else nom
    else:
        qui = "La Direction"
    return [
        Spacer(1, 30),
        Paragraph(f"Fait à {ville}, le {_date_fr(date.today())}", S["lieu_date"]),
        Spacer(1, 8),
        Paragraph(f"Pour l'entreprise,<br/>{qui}", S["signature"]),
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
    if solde.get("preavis_montant", 0) > 0:
        data.append(["Indemnité de préavis",
                     f"{solde.get('preavis_jours', 0)} jour(s)",
                     _fmt_fcfa(solde.get("preavis_montant", 0))])
    if solde.get("indem_licenciement", 0) > 0:
        data.append(["Indemnité de licenciement", "(exonérée)",
                     _fmt_fcfa(solde.get("indem_licenciement", 0))])
    data.append(["", "TOTAL BRUT",
                 _fmt_fcfa(solde.get("total_brut", 0))])
    # Déductions sur la partie cotisable
    data.append(["CNSS (salarié)", "", "- " + _fmt_fcfa(solde.get("stc_cnss_salarie", 0))])
    data.append(["CNAMGS (salarié)", "", "- " + _fmt_fcfa(solde.get("stc_cnamgs_salarie", 0))])
    data.append(["TCS", "", "- " + _fmt_fcfa(solde.get("stc_tcs", 0))])
    data.append(["IRPP", "", "- " + _fmt_fcfa(solde.get("stc_irpp", 0))])
    data.append(["", "TOTAL COTISATIONS", "- " + _fmt_fcfa(solde.get("total_cotisations", 0))])
    data.append(["", "TOTAL NET À PAYER",
                 _fmt_fcfa(solde.get("total_net", solde.get("total_a_payer", 0)))])

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


# ═══════════════════════════════════════════════════════════════════════════
# CONTRATS DE TRAVAIL — modèles éditables par le tenant + génération PDF
# ═══════════════════════════════════════════════════════════════════════════
# Balises disponibles dans les modèles (affichées au tenant dans l'éditeur).
BALISES_CONTRAT = [
    ("{{entreprise}}",            "Nom de l'entreprise"),
    ("{{entreprise_adresse}}",    "Adresse de l'entreprise"),
    ("{{entreprise_ville}}",      "Ville de l'entreprise"),
    ("{{entreprise_nif}}",        "NIF de l'entreprise"),
    ("{{entreprise_cnss}}",       "N° CNSS employeur"),
    ("{{representant}}",          "Nom du représentant légal"),
    ("{{representant_fonction}}", "Fonction du représentant"),
    ("{{nom_complet}}",           "Nom et prénom du salarié"),
    ("{{nom}}",                   "Nom du salarié"),
    ("{{prenom}}",                "Prénom du salarié"),
    ("{{matricule}}",             "Matricule du salarié"),
    ("{{sexe}}",                  "Sexe"),
    ("{{date_naissance}}",        "Date de naissance"),
    ("{{nationalite}}",           "Nationalité"),
    ("{{adresse}}",               "Adresse du salarié"),
    ("{{situation_matrimoniale}}","Situation matrimoniale"),
    ("{{numero_cnss}}",           "N° CNSS du salarié"),
    ("{{numero_cnamgs}}",         "N° CNAMGS du salarié"),
    ("{{poste}}",                 "Poste / fonction"),
    ("{{emploi}}",                "Emploi"),
    ("{{categorie}}",             "Catégorie professionnelle"),
    ("{{type_contrat}}",          "Type de contrat (CDI, CDD…)"),
    ("{{date_debut}}",            "Date de début du contrat"),
    ("{{date_fin}}",              "Date de fin (CDD)"),
    ("{{periode_essai}}",         "Fin de période d'essai"),
    ("{{salaire_base}}",          "Salaire de base (chiffres)"),
    ("{{date_jour}}",             "Date du jour"),
    ("{{ville}}",                 "Ville (signature)"),
]


def _contexte_contrat(salarie, tenant, contrat=None):
    """Construit le dictionnaire balise -> valeur pour un salarié donné."""
    contrat = contrat or next((c for c in getattr(salarie, "contrats", []) if c.actif), None) \
              or (salarie.contrats[-1] if getattr(salarie, "contrats", None) else None)
    cat = ""
    if getattr(salarie, "categorie", None):
        cat = salarie.categorie.libelle or salarie.categorie.code or ""
    def d(v): return _date_fr(v) if v else ""
    return {
        "entreprise":            tenant.denomination or "",
        "entreprise_adresse":    getattr(tenant, "adresse", "") or "",
        "entreprise_ville":      getattr(tenant, "ville", "") or "",
        "entreprise_nif":        getattr(tenant, "nif", "") or "",
        "entreprise_cnss":       getattr(tenant, "numero_cnss", "") or "",
        "representant":          getattr(tenant, "representant_nom", "") or "",
        "representant_fonction": getattr(tenant, "representant_fonction", "") or "",
        "nom_complet":           salarie.nom_complet if hasattr(salarie, "nom_complet") else f"{salarie.nom} {salarie.prenom}",
        "nom":                   salarie.nom or "",
        "prenom":                salarie.prenom or "",
        "matricule":             salarie.matricule or "",
        "sexe":                  salarie.sexe or "",
        "date_naissance":        d(salarie.date_naissance),
        "nationalite":           salarie.nationalite or "",
        "adresse":               salarie.adresse or "",
        "situation_matrimoniale":salarie.situation_matrimoniale or "",
        "numero_cnss":           salarie.numero_cnss or "",
        "numero_cnamgs":         salarie.numero_cnamgs or "",
        "poste":                 (contrat.poste if contrat and contrat.poste else (salarie.emploi or "")),
        "emploi":                salarie.emploi or "",
        "categorie":             cat,
        "type_contrat":          (contrat.type_contrat if contrat else "") or "",
        "date_debut":            d(contrat.date_debut) if contrat else d(salarie.date_embauche),
        "date_fin":              d(contrat.date_fin) if contrat else "",
        "periode_essai":         d(contrat.date_fin_essai) if contrat else "",
        "salaire_base":          _fmt_fcfa(contrat.salaire_base) if contrat and contrat.salaire_base else "",
        "date_jour":             _date_fr(date.today()),
        "ville":                 getattr(tenant, "ville", "") or "",
    }


def _remplir_balises(texte, contexte):
    """Remplace {{cle}} par sa valeur ; laisse la balise si inconnue (visible = à corriger)."""
    import re
    def repl(m):
        cle = m.group(1).strip()
        return str(contexte.get(cle, m.group(0)))
    return re.sub(r"\{\{\s*([\w]+)\s*\}\}", repl, texte or "")


def generer_contrat_pdf(modele, salarie, tenant, contrat=None) -> bytes:
    """Génère le PDF d'un contrat à partir d'un modèle du tenant et des données du salarié."""
    S = _styles()
    ctx = _contexte_contrat(salarie, tenant, contrat)
    texte = _remplir_balises(modele.contenu, ctx)

    el = _entete(tenant, S)
    titre = (modele.nom or "CONTRAT DE TRAVAIL").upper()
    el.append(Paragraph(titre, ParagraphStyle("t", parent=S["titre"], alignment=TA_CENTER,
                                              fontSize=14, spaceAfter=14)))
    corps = ParagraphStyle("corps_contrat", parent=S["corps"], alignment=TA_JUSTIFY,
                           fontSize=10, leading=15, spaceAfter=6)
    for bloc in (texte or "").split("\n"):
        bloc = bloc.strip()
        if bloc:
            el.append(Paragraph(bloc.replace("&", "&amp;"), corps))
        else:
            el.append(Spacer(1, 6))
    el.append(Spacer(1, 24))
    el.extend(_signature(tenant, S, ville=ctx.get("ville")))
    return _build(el)


# ═══════════════════════════════════════════════════════════════════════════
# TRAMES DE CONTRAT — structures neutres (articles + balises) à COMPLÉTER.
# Aucune clause juridique n'est imposée : le contenu entre [ ] est à préciser
# par l'entreprise, sous sa responsabilité.
# ═══════════════════════════════════════════════════════════════════════════
_SOUSCRITS = (
    "ENTRE LES SOUSSIGNÉS :\n\n"
    "{{entreprise}}, sise à {{entreprise_adresse}}, {{entreprise_ville}}, "
    "NIF {{entreprise_nif}}, immatriculée à la CNSS sous le n° {{entreprise_cnss}}, "
    "représentée par {{representant}}, {{representant_fonction}},\n"
    "ci-après dénommée « l'Employeur », d'une part,\n\n"
    "ET\n\n"
    "{{nom_complet}}, né(e) le {{date_naissance}}, de nationalité {{nationalite}}, "
    "demeurant à {{adresse}}, immatriculé(e) à la CNSS sous le n° {{numero_cnss}},\n"
    "ci-après dénommé(e) « le Salarié », d'autre part,\n\n"
    "IL A ÉTÉ CONVENU CE QUI SUIT :\n\n"
)
_SIGN_FIN = "\nFait à {{ville}}, le {{date_jour}}, en deux exemplaires originaux.\n"

TRAMES_CONTRAT = {
    "CDI": _SOUSCRITS +
        "ARTICLE 1 – ENGAGEMENT\n"
        "L'Employeur engage le Salarié en qualité de {{poste}}, à compter du {{date_debut}}, "
        "dans le cadre d'un contrat à durée indéterminée.\n\n"
        "ARTICLE 2 – PÉRIODE D'ESSAI\n"
        "Le présent contrat est assorti d'une période d'essai qui prend fin le {{periode_essai}}. "
        "[Précisez les conditions de renouvellement et de rupture durant l'essai.]\n\n"
        "ARTICLE 3 – FONCTIONS\n"
        "[Décrivez les missions et responsabilités du Salarié.]\n\n"
        "ARTICLE 4 – LIEU DE TRAVAIL\n"
        "[Précisez le lieu d'exécution du travail.]\n\n"
        "ARTICLE 5 – DURÉE DU TRAVAIL\n"
        "[Précisez l'horaire et la durée hebdomadaire de travail.]\n\n"
        "ARTICLE 6 – RÉMUNÉRATION\n"
        "Le Salarié perçoit un salaire de base mensuel de {{salaire_base}}. "
        "[Précisez les éléments accessoires éventuels : primes, indemnités…]\n\n"
        "ARTICLE 7 – CONGÉS\n"
        "[Précisez les droits à congés conformément à la convention applicable.]\n\n"
        "ARTICLE 8 – OBLIGATIONS DES PARTIES\n"
        "[Précisez les obligations réciproques, la confidentialité, etc.]\n\n"
        "ARTICLE 9 – RUPTURE DU CONTRAT\n"
        "[Précisez les conditions de préavis et de rupture.]\n"
        + _SIGN_FIN,

    "CDD": _SOUSCRITS +
        "ARTICLE 1 – OBJET ET DURÉE\n"
        "L'Employeur engage le Salarié en qualité de {{poste}} dans le cadre d'un contrat à durée "
        "déterminée, du {{date_debut}} au {{date_fin}}.\n"
        "[Précisez le motif du recours au CDD.]\n\n"
        "ARTICLE 2 – PÉRIODE D'ESSAI\n"
        "La période d'essai prend fin le {{periode_essai}}.\n\n"
        "ARTICLE 3 – FONCTIONS\n"
        "[Décrivez les missions confiées au Salarié.]\n\n"
        "ARTICLE 4 – RÉMUNÉRATION\n"
        "Le salaire de base mensuel est fixé à {{salaire_base}}.\n\n"
        "ARTICLE 5 – FIN DE CONTRAT\n"
        "[Précisez les conditions de fin de contrat et l'indemnité éventuelle.]\n"
        + _SIGN_FIN,

    "CHANTIER": _SOUSCRITS +
        "ARTICLE 1 – OBJET\n"
        "L'Employeur engage le Salarié en qualité de {{poste}} pour l'exécution des travaux du chantier "
        "[désignez le chantier], à compter du {{date_debut}}.\n\n"
        "ARTICLE 2 – DURÉE\n"
        "Le contrat est conclu pour la durée du chantier. [Précisez la fin prévisionnelle : {{date_fin}}.]\n\n"
        "ARTICLE 3 – LIEU DES TRAVAUX\n"
        "[Indiquez le lieu du chantier.]\n\n"
        "ARTICLE 4 – RÉMUNÉRATION\n"
        "Le salaire de base est fixé à {{salaire_base}}. [Précisez les primes de chantier éventuelles.]\n\n"
        "ARTICLE 5 – FIN DU CONTRAT\n"
        "Le contrat prend fin à l'achèvement des travaux pour lesquels le Salarié a été engagé. "
        "[Précisez les modalités.]\n"
        + _SIGN_FIN,

    "JOURNALIER": _SOUSCRITS +
        "ARTICLE 1 – OBJET\n"
        "L'Employeur engage le Salarié en qualité de {{poste}} pour une prestation journalière, "
        "à compter du {{date_debut}}.\n\n"
        "ARTICLE 2 – RÉMUNÉRATION\n"
        "[Précisez le taux journalier / horaire et les modalités de paiement.]\n\n"
        "ARTICLE 3 – CONDITIONS DE TRAVAIL\n"
        "[Précisez le lieu, les horaires et la nature des tâches.]\n"
        + _SIGN_FIN,
}
