"""
declaration_cnss.py — Module de déclaration CNSS/CNAMGS électronique
=====================================================================
Génère les fichiers de déclaration trimestrielle uploadables sur le
portail CNSS Gabon (cnss.ga) et CNAMGS.

Formats produits :
  1. CSV portail CNSS  — uploadable sur cnss.ga → Déclarations → Import fichier
  2. CSV portail CNAMGS — même structure adaptée CNAMGS
  3. Excel formulaire officiel (déjà géré dans app.py via _gen_excel_cnss)

Structure CSV CNSS Gabon (colonnes attendues par le portail) :
  Matricule salarié | NOM | Prénom | N° CNSS | Date embauche |
  Salaire M1 | Salaire M2 | Salaire M3 | Total trimestre

Structure CSV CNAMGS :
  Matricule | NOM | Prénom | N° CNAMGS | Date embauche |
  Base M1 | Base M2 | Base M3 | Total

Constantes CNSS 2026 (Gabon) :
  Taux salarié  : 5,00 %  (branche famille + vieillesse)
  Taux patronal : 18,00 % (dont 11% famille, 5% vieillesse, 2% AT)
  Plafond       : 1 500 000 XAF/mois

Constantes CNAMGS 2026 :
  Taux salarié  : 1,50 %
  Taux patronal : 6,00 %
  Plafond       : 2 500 000 XAF/mois
"""

import csv
import io
import logging
from datetime import datetime

logger = logging.getLogger("paiegalon.declaration_cnss")

# ── Constantes ─────────────────────────────────────────────────────────────────
CNSS_TAUX_SAL   = 0.05
CNSS_TAUX_PAT   = 0.18
CNSS_PLAFOND    = 1_500_000

CNAMGS_TAUX_SAL = 0.015
CNAMGS_TAUX_PAT = 0.06
CNAMGS_PLAFOND  = 2_500_000


# ═══════════════════════════════════════════════════════════════════════════════
# CSV PORTAIL CNSS
# ═══════════════════════════════════════════════════════════════════════════════

def generer_csv_cnss(sal_data, periode, tenant, trim_debut, trim_fin) -> bytes:
    """
    Génère le fichier CSV uploadable sur le portail CNSS Gabon.

    Args:
        sal_data   : liste de dicts par salarié (voir structure ci-dessous)
        periode    : PeriodePaie (dernier mois du trimestre)
        tenant     : Tenant
        trim_debut : numéro du premier mois du trimestre (1, 4, 7, 10)
        trim_fin   : numéro du dernier mois (3, 6, 9, 12)

    Structure d'un élément sal_data :
        nom_complet, matricule, numero_cnss, date_embauche,
        m1_base_cnss, m2_base_cnss, m3_base_cnss

    Returns:
        bytes du CSV encodé UTF-8 avec BOM (compatible Excel + portail)
    """
    trim_num   = (trim_debut - 1) // 3 + 1
    trim_label = f"T{trim_num}/{periode.annee}"
    annee      = periode.annee

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # ── En-tête employeur ─────────────────────────────────────────────────────
    writer.writerow(["DECLARATION TRIMESTRIELLE CNSS GABON"])
    writer.writerow(["Employeur",        tenant.denomination])
    writer.writerow(["N° Matricule CNSS", getattr(tenant, "numero_cnss", "") or ""])
    writer.writerow(["NIF",              tenant.nif or ""])
    writer.writerow(["Trimestre",        trim_label])
    writer.writerow(["Généré le",        datetime.now().strftime("%d/%m/%Y %H:%M")])
    writer.writerow([])

    # ── En-têtes colonnes ─────────────────────────────────────────────────────
    mois_noms = _noms_mois_trim(trim_debut, annee)
    writer.writerow([
        "Matricule interne",
        "NOM",
        "Prénom",
        "N° Immatriculation CNSS",
        "Date d'embauche",
        f"Base CNSS {mois_noms[0]}",
        f"Base CNSS {mois_noms[1]}",
        f"Base CNSS {mois_noms[2]}",
        "Base CNSS Totale",
        "Cotisation salarié (5%)",
        "Cotisation patronale (18%)",
        "Total cotisations CNSS",
    ])

    # ── Lignes par salarié ────────────────────────────────────────────────────
    total_base = 0
    for s in sorted(sal_data, key=lambda x: x.get("nom_complet", "")):
        m1 = round(float(s.get("m1_base_cnss", 0)))
        m2 = round(float(s.get("m2_base_cnss", 0)))
        m3 = round(float(s.get("m3_base_cnss", 0)))
        base_trim = m1 + m2 + m3
        total_base += base_trim

        cot_sal = round(base_trim * CNSS_TAUX_SAL)
        cot_pat = round(base_trim * CNSS_TAUX_PAT)

        # Séparer nom et prénom depuis nom_complet si nécessaire
        nom_complet = s.get("nom_complet", "")
        parts = nom_complet.strip().split(" ", 1)
        prenom = parts[0] if len(parts) > 1 else ""
        nom    = parts[1] if len(parts) > 1 else nom_complet

        writer.writerow([
            s.get("matricule", ""),
            nom.upper(),
            prenom,
            s.get("numero_cnss", ""),
            s.get("date_embauche", ""),
            m1 or "",
            m2 or "",
            m3 or "",
            base_trim,
            cot_sal,
            cot_pat,
            cot_sal + cot_pat,
        ])

    # ── Ligne totaux ──────────────────────────────────────────────────────────
    writer.writerow([])
    writer.writerow([
        "TOTAL", "", "", "", "",
        round(sum(float(s.get("m1_base_cnss", 0)) for s in sal_data)),
        round(sum(float(s.get("m2_base_cnss", 0)) for s in sal_data)),
        round(sum(float(s.get("m3_base_cnss", 0)) for s in sal_data)),
        total_base,
        round(total_base * CNSS_TAUX_SAL),
        round(total_base * CNSS_TAUX_PAT),
        round(total_base * (CNSS_TAUX_SAL + CNSS_TAUX_PAT)),
    ])

    # ── Récapitulatif ─────────────────────────────────────────────────────────
    writer.writerow([])
    writer.writerow(["=== RÉCAPITULATIF À VERSER ==="])
    writer.writerow(["Base totale trimestrielle CNSS", total_base])
    writer.writerow(["Cotisations salariales (5%)",   round(total_base * CNSS_TAUX_SAL)])
    writer.writerow(["Cotisations patronales (18%)",  round(total_base * CNSS_TAUX_PAT)])
    writer.writerow(["TOTAL À VERSER CNSS",           round(total_base * (CNSS_TAUX_SAL + CNSS_TAUX_PAT))])
    writer.writerow([])
    writer.writerow(["Nombre de salariés déclarés", len(sal_data)])

    logger.info(
        f"[CNSS CSV] Trimestre {trim_label} — {len(sal_data)} salariés — "
        f"Base: {total_base:,.0f} XAF — "
        f"Total: {round(total_base * 0.23):,.0f} XAF"
    )

    return ("\ufeff" + output.getvalue()).encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# CSV PORTAIL CNAMGS
# ═══════════════════════════════════════════════════════════════════════════════

def generer_csv_cnamgs(sal_data, periode, tenant, trim_debut, trim_fin) -> bytes:
    """
    Génère le fichier CSV uploadable sur le portail CNAMGS Gabon.
    Même structure que CNSS mais avec les bases et taux CNAMGS.
    """
    trim_num   = (trim_debut - 1) // 3 + 1
    trim_label = f"T{trim_num}/{periode.annee}"
    annee      = periode.annee

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # ── En-tête employeur ─────────────────────────────────────────────────────
    writer.writerow(["DECLARATION TRIMESTRIELLE CNAMGS GABON"])
    writer.writerow(["Employeur",          tenant.denomination])
    writer.writerow(["N° Matricule CNAMGS", getattr(tenant, "numero_cnamgs", "") or ""])
    writer.writerow(["NIF",                tenant.nif or ""])
    writer.writerow(["Trimestre",          trim_label])
    writer.writerow(["Généré le",          datetime.now().strftime("%d/%m/%Y %H:%M")])
    writer.writerow([])

    mois_noms = _noms_mois_trim(trim_debut, annee)
    writer.writerow([
        "Matricule interne",
        "NOM",
        "Prénom",
        "N° Immatriculation CNAMGS",
        "Date d'embauche",
        f"Base CNAMGS {mois_noms[0]}",
        f"Base CNAMGS {mois_noms[1]}",
        f"Base CNAMGS {mois_noms[2]}",
        "Base CNAMGS Totale",
        "Cotisation salarié (1,5%)",
        "Cotisation patronale (6%)",
        "Total cotisations CNAMGS",
    ])

    total_base = 0
    for s in sorted(sal_data, key=lambda x: x.get("nom_complet", "")):
        m1 = round(float(s.get("m1_base_cnamgs", 0)))
        m2 = round(float(s.get("m2_base_cnamgs", 0)))
        m3 = round(float(s.get("m3_base_cnamgs", 0)))
        base_trim = m1 + m2 + m3
        total_base += base_trim

        cot_sal = round(base_trim * CNAMGS_TAUX_SAL)
        cot_pat = round(base_trim * CNAMGS_TAUX_PAT)

        nom_complet = s.get("nom_complet", "")
        parts = nom_complet.strip().split(" ", 1)
        prenom = parts[0] if len(parts) > 1 else ""
        nom    = parts[1] if len(parts) > 1 else nom_complet

        writer.writerow([
            s.get("matricule", ""),
            nom.upper(),
            prenom,
            s.get("numero_cnamgs", ""),
            s.get("date_embauche", ""),
            m1 or "",
            m2 or "",
            m3 or "",
            base_trim,
            cot_sal,
            cot_pat,
            cot_sal + cot_pat,
        ])

    writer.writerow([])
    writer.writerow([
        "TOTAL", "", "", "", "",
        round(sum(float(s.get("m1_base_cnamgs", 0)) for s in sal_data)),
        round(sum(float(s.get("m2_base_cnamgs", 0)) for s in sal_data)),
        round(sum(float(s.get("m3_base_cnamgs", 0)) for s in sal_data)),
        total_base,
        round(total_base * CNAMGS_TAUX_SAL),
        round(total_base * CNAMGS_TAUX_PAT),
        round(total_base * (CNAMGS_TAUX_SAL + CNAMGS_TAUX_PAT)),
    ])

    writer.writerow([])
    writer.writerow(["=== RÉCAPITULATIF À VERSER ==="])
    writer.writerow(["Base totale trimestrielle CNAMGS", total_base])
    writer.writerow(["Cotisations salariales (1,5%)",   round(total_base * CNAMGS_TAUX_SAL)])
    writer.writerow(["Cotisations patronales (6%)",     round(total_base * CNAMGS_TAUX_PAT)])
    writer.writerow(["TOTAL À VERSER CNAMGS",           round(total_base * (CNAMGS_TAUX_SAL + CNAMGS_TAUX_PAT))])
    writer.writerow([])
    writer.writerow(["Nombre de salariés déclarés", len(sal_data)])

    logger.info(
        f"[CNAMGS CSV] Trimestre {trim_label} — {len(sal_data)} salariés — "
        f"Base: {total_base:,.0f} XAF — "
        f"Total: {round(total_base * 0.075):,.0f} XAF"
    )

    return ("\ufeff" + output.getvalue()).encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _noms_mois_trim(trim_debut: int, annee: int) -> list:
    """Retourne les 3 noms de mois du trimestre (ex: ['Jan 2026', 'Fév 2026', 'Mar 2026'])."""
    noms = ["", "Jan", "Fév", "Mar", "Avr", "Mai", "Jun",
            "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]
    return [
        f"{noms[trim_debut]} {annee}",
        f"{noms[trim_debut + 1]} {annee}",
        f"{noms[trim_debut + 2]} {annee}",
    ]


def calculer_trimestre(mois: int):
    """
    Retourne (trim_num, trim_debut, trim_fin, trim_label) pour un mois donné.
    Exemple : mois=5 → (2, 4, 6, "T2 (Avr-Jun)")
    """
    trim_num   = (mois - 1) // 3 + 1
    trim_debut = ((mois - 1) // 3) * 3 + 1
    trim_fin   = trim_debut + 2
    labels     = ["Jan-Mar", "Avr-Jun", "Jul-Sep", "Oct-Déc"]
    trim_label = f"T{trim_num} ({labels[trim_num - 1]})"
    return trim_num, trim_debut, trim_fin, trim_label


class DeclarationVide(Exception):
    """Aucun bulletin pour ce trimestre."""
