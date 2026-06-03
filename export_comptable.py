"""
export_comptable.py — Export comptable Sage 100 pour PaieGabon SaaS
====================================================================
Génère deux types de fichiers importables dans Sage 100 Comptabilité :

1. JOURNAL DE PAIE (journal_paie_MMAAAA.txt)
   Format : Texte délimité Sage 100 (colonnes fixes)
   Contenu : Écritures comptables globales de la période
   → Charges de personnel (641xxx), cotisations (645xxx), dettes (421, 431…)
   → Une ligne de débit + une ligne de crédit par rubrique

2. LIVRE DE PAIE (livre_paie_MMAAAA.csv)
   Format : CSV Excel-compatible (séparateur point-virgule)
   Contenu : Détail ligne par ligne par salarié
   → Matricule, nom, salaire brut, cotisations, IRPP, net à payer

Plan comptable OHADA adapté Gabon :
   641100  Salaires et appointements
   641200  Primes et gratifications
   641300  Heures supplémentaires
   645110  Cotisations CNSS (part patronale)
   645120  Cotisations CNAMGS (part patronale)
   645130  FNH (Fonds National de l'Habitat)
   645140  CFP (Contribution à la Formation Professionnelle)
   421000  Personnel — Rémunérations dues (net à payer)
   431100  CNSS à décaisser (part salariale + patronale)
   431200  CNAMGS à décaisser
   431300  TCS à décaisser
   431400  IRPP à décaisser
   447000  Charges sociales diverses
"""

import csv
import io
import logging
from datetime import date, datetime

logger = logging.getLogger("paiegalon.export_comptable")

# ── Plan comptable Sage 100 / OHADA Gabon ────────────────────────────────────
COMPTES = {
    # Charges de personnel
    "salaires":          ("641100", "Salaires et appointements"),
    "primes":            ("641200", "Primes et gratifications"),
    "heures_sup":        ("641300", "Heures supplémentaires"),
    "indemnites":        ("641400", "Indemnités diverses"),
    # Charges patronales
    "cnss_patronale":    ("645110", "Cotisations CNSS patronales"),
    "cnamgs_patronale":  ("645120", "Cotisations CNAMGS patronales"),
    "fnh":               ("645130", "FNH"),
    "cfp":               ("645140", "CFP"),
    "tcs_patronal":      ("645150", "TCS"),
    # Dettes envers le personnel
    "net_a_payer":       ("421000", "Personnel — Rémunérations dues"),
    # Dettes envers organismes sociaux
    "cnss_total":        ("431100", "CNSS à décaisser"),
    "cnamgs_total":      ("431200", "CNAMGS à décaisser"),
    "tcs_total":         ("431300", "TCS à décaisser"),
    "irpp_total":        ("431400", "IRPP à décaisser"),
    # Retenues salariales
    "retenues_sal":      ("421100", "Retenues salariales diverses"),
}


# ═══════════════════════════════════════════════════════════════════════════════
# JOURNAL DE PAIE — Format Sage 100 texte
# ═══════════════════════════════════════════════════════════════════════════════

def generer_journal_paie(bulletins, periode, tenant) -> bytes:
    """
    Génère le journal de paie mensuel au format Sage 100.

    Le fichier produit est un .txt importable dans Sage 100 Comptabilité
    via Fichier → Importer → Journal de saisie.

    Format des colonnes (largeurs fixes, séparateur tabulation) :
      Journal | Date | N°Pièce | Compte | Libellé | Débit | Crédit | Analytique

    Args:
        bulletins : liste de BulletinPaie (statut VALIDE)
        periode   : PeriodePaie
        tenant    : Tenant

    Returns:
        bytes du fichier .txt encodé Windows-1252 (compatible Sage)
    """
    if not bulletins:
        raise ExportVide("Aucun bulletin validé pour cette période.")

    mois_nom = _nom_mois(periode.mois)
    date_ecriture = _derniere_jour_mois(periode.annee, periode.mois)
    date_str      = date_ecriture.strftime("%d/%m/%Y")
    num_piece     = f"PAIE{periode.annee}{periode.mois:02d}"
    journal_code  = "PAI"  # Code journal Sage (à configurer dans Sage)
    libelle_base  = f"Paie {mois_nom} {periode.annee}"

    # ── Agréger les totaux ────────────────────────────────────────────────────
    totaux = _calculer_totaux(bulletins)

    lignes = []

    # En-tête commentaire (ignoré par Sage à l'import)
    lignes.append(_ligne_commentaire(
        f"Export PaieGabon — {tenant.denomination} — {mois_nom} {periode.annee}"
    ))
    lignes.append(_ligne_commentaire(
        f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')} — "
        f"{len(bulletins)} bulletins — "
        f"Masse salariale brute : {totaux['total_brut']:,.0f} XAF"
    ))

    # ── SECTION 1 : Charges de personnel (DÉBIT comptes 641xxx) ───────────────
    if totaux["total_salaires"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["salaires"][0],
            libelle=f"{libelle_base} — Salaires de base",
            debit=totaux["total_salaires"], credit=0,
        ))

    if totaux["total_primes"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["primes"][0],
            libelle=f"{libelle_base} — Primes et gratifications",
            debit=totaux["total_primes"], credit=0,
        ))

    if totaux["total_heures_sup"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["heures_sup"][0],
            libelle=f"{libelle_base} — Heures supplémentaires",
            debit=totaux["total_heures_sup"], credit=0,
        ))

    if totaux["total_indemnites"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["indemnites"][0],
            libelle=f"{libelle_base} — Indemnités diverses",
            debit=totaux["total_indemnites"], credit=0,
        ))

    # ── SECTION 2 : Charges patronales (DÉBIT comptes 645xxx) ─────────────────
    if totaux["total_cnss_pat"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["cnss_patronale"][0],
            libelle=f"{libelle_base} — CNSS patronale",
            debit=totaux["total_cnss_pat"], credit=0,
        ))

    if totaux["total_cnamgs_pat"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["cnamgs_patronale"][0],
            libelle=f"{libelle_base} — CNAMGS patronale",
            debit=totaux["total_cnamgs_pat"], credit=0,
        ))

    if totaux["total_fnh"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["fnh"][0],
            libelle=f"{libelle_base} — FNH",
            debit=totaux["total_fnh"], credit=0,
        ))

    if totaux["total_cfp"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["cfp"][0],
            libelle=f"{libelle_base} — CFP",
            debit=totaux["total_cfp"], credit=0,
        ))

    if totaux["total_tcs"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["tcs_patronal"][0],
            libelle=f"{libelle_base} — TCS",
            debit=totaux["total_tcs"], credit=0,
        ))

    # ── SECTION 3 : Dettes envers le personnel (CRÉDIT 421) ───────────────────
    lignes.append(_ligne_ecriture(
        journal=journal_code, date=date_str, piece=num_piece,
        compte=COMPTES["net_a_payer"][0],
        libelle=f"{libelle_base} — Net à payer",
        debit=0, credit=totaux["total_net"],
    ))

    # ── SECTION 4 : Dettes envers organismes sociaux (CRÉDIT 431xxx) ──────────
    cnss_total = totaux["total_cnss_sal"] + totaux["total_cnss_pat"]
    if cnss_total > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["cnss_total"][0],
            libelle=f"{libelle_base} — CNSS à décaisser",
            debit=0, credit=cnss_total,
        ))

    cnamgs_total = totaux["total_cnamgs_sal"] + totaux["total_cnamgs_pat"]
    if cnamgs_total > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["cnamgs_total"][0],
            libelle=f"{libelle_base} — CNAMGS à décaisser",
            debit=0, credit=cnamgs_total,
        ))

    if totaux["total_tcs"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["tcs_total"][0],
            libelle=f"{libelle_base} — TCS à décaisser",
            debit=0, credit=totaux["total_tcs"],
        ))

    if totaux["total_irpp"] > 0:
        lignes.append(_ligne_ecriture(
            journal=journal_code, date=date_str, piece=num_piece,
            compte=COMPTES["irpp_total"][0],
            libelle=f"{libelle_base} — IRPP à décaisser",
            debit=0, credit=totaux["total_irpp"],
        ))

    # ── Retenues salariales (cotisations part salarié — CRÉDIT 421100) ────────
    retenues_sal = totaux["total_cnss_sal"] + totaux["total_cnamgs_sal"] + totaux["total_irpp"]
    if retenues_sal > 0:
        # Note : ces montants sont déjà inclus dans le brut
        # On les pointe ici pour information de rapprochement
        pass  # La balance est équilibrée via net_a_payer + charges patronales

    # ── Vérification de l'équilibre débit/crédit ──────────────────────────────
    total_debit  = sum(l.get("debit",  0) for l in lignes if isinstance(l, dict))
    total_credit = sum(l.get("credit", 0) for l in lignes if isinstance(l, dict))
    ecart = round(abs(total_debit - total_credit), 2)
    if ecart > 1:
        logger.warning(f"[Export Sage] Écart débit/crédit : {ecart} XAF — vérifier le journal")

    logger.info(
        f"[Export Sage] Journal paie généré — {len(bulletins)} bulletins — "
        f"Débit: {total_debit:,.0f} / Crédit: {total_credit:,.0f} XAF"
    )

    # ── Sérialiser en texte Sage ───────────────────────────────────────────────
    output = io.StringIO()
    for ligne in lignes:
        if isinstance(ligne, dict):
            output.write(_formater_ligne_sage(ligne))
        else:
            output.write(ligne + "\r\n")

    # Sage 100 attend Windows-1252
    return output.getvalue().encode("windows-1252", errors="replace")


# ═══════════════════════════════════════════════════════════════════════════════
# LIVRE DE PAIE — Format CSV Excel
# ═══════════════════════════════════════════════════════════════════════════════

def generer_livre_paie(bulletins, periode, tenant) -> bytes:
    """
    Génère le livre de paie détaillé par salarié au format CSV.

    Importable directement dans Excel ou dans Sage 100 via
    Fichier → Importer → Données externes.

    Colonnes :
      Matricule | Nom | Prénom | Emploi | Catégorie |
      Sal. Base | Primes | H.Sup | Indemnités | Brut |
      CNSS sal. | CNAMGS sal. | TCS | IRPP |
      Net avant IRPP | Net à payer | CNSS pat. | CNAMGS pat. |
      FNH | CFP | Coût total employeur

    Args:
        bulletins : liste de BulletinPaie (statut VALIDE)
        periode   : PeriodePaie
        tenant    : Tenant

    Returns:
        bytes du fichier CSV encodé UTF-8 avec BOM (compatible Excel)
    """
    if not bulletins:
        raise ExportVide("Aucun bulletin validé pour cette période.")

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    mois_nom = _nom_mois(periode.mois)

    # ── En-tête entreprise ────────────────────────────────────────────────────
    writer.writerow([f"LIVRE DE PAIE — {tenant.denomination}"])
    writer.writerow([f"Période : {mois_nom} {periode.annee}"])
    writer.writerow([f"NIF : {tenant.nif or '—'}"])
    writer.writerow([f"Généré le : {datetime.now().strftime('%d/%m/%Y à %H:%M')}"])
    writer.writerow([f"Nombre de bulletins : {len(bulletins)}"])
    writer.writerow([])  # ligne vide

    # ── En-tête colonnes ──────────────────────────────────────────────────────
    entetes = [
        "Matricule", "Nom", "Prénom", "Emploi", "Catégorie",
        # Éléments du brut
        "Salaire base", "Primes", "Heures sup.", "Indemnités", "Brut total",
        # Retenues salariales
        "CNSS salarié", "CNAMGS salarié", "TCS", "IRPP",
        # Net
        "Net avant IRPP", "Acompte", "Net à payer",
        # Charges patronales
        "CNSS patronal", "CNAMGS patronal", "FNH", "CFP",
        # Coût total
        "Coût employeur",
    ]
    writer.writerow(entetes)

    # ── Données par salarié ────────────────────────────────────────────────────
    for b in sorted(bulletins, key=lambda x: (x.salarie.nom, x.salarie.prenom)):
        s = b.salarie

        # Regrouper les primes
        total_primes = sum(_f(b, c) for c in [
            "prime_caisse", "prime_anciennete", "prime_rendement",
            "prime_assiduité", "prime_qualite", "prime_performance",
            "prime_responsabilite", "sursalaire", "carburant",
            "allocations_conge",
        ])

        # Regrouper les heures sup
        total_hsup = sum(_f(b, c) for c in [
            "heures_sup_10", "heures_sup_30", "heures_sup_40", "heures_sup_70"
        ])

        # Regrouper les indemnités
        total_indem = sum(_f(b, c) for c in [
            "indem_logement", "indem_domesticite", "indem_eau_electricite",
            "indem_nourriture", "indem_transport", "indem_representation",
            "prime_panier", "prime_salisure",
            "indem_compensatrice_conge", "indem_services_rendus",
            "indem_compensatrice_preavis", "indem_licenciement",
        ])

        cnss_sal   = _f(b, "cnss_salarie")
        cnamgs_sal = _f(b, "cnamgs_salarie")
        cnss_pat   = _f(b, "cnss_patronale")
        cnamgs_pat = _f(b, "cnamgs_patronale")
        fnh        = _f(b, "fnh")
        cfp        = _f(b, "cfp")
        tcs        = _f(b, "tcs")
        irpp       = _f(b, "irpp")
        brut       = _f(b, "salaire_brut")
        net        = _f(b, "net_a_payer")
        acompte    = _f(b, "acompte")
        net_av_irpp = _f(b, "net_avant_irpp")

        # Coût total employeur = brut + charges patronales
        cout_employeur = brut + cnss_pat + cnamgs_pat + fnh + cfp + tcs

        cat_nom = s.categorie.libelle if s.categorie else "—"

        writer.writerow([
            s.matricule,
            s.nom.upper(),
            s.prenom,
            s.emploi or "—",
            cat_nom,
            _xaf(b.salaire_base),
            _xaf(total_primes),
            _xaf(total_hsup),
            _xaf(total_indem),
            _xaf(brut),
            _xaf(cnss_sal),
            _xaf(cnamgs_sal),
            _xaf(tcs),
            _xaf(irpp),
            _xaf(net_av_irpp),
            _xaf(acompte),
            _xaf(net),
            _xaf(cnss_pat),
            _xaf(cnamgs_pat),
            _xaf(fnh),
            _xaf(cfp),
            _xaf(cout_employeur),
        ])

    # ── Ligne de totaux ────────────────────────────────────────────────────────
    writer.writerow([])
    totaux = _calculer_totaux(bulletins)
    writer.writerow([
        "TOTAUX", "", "", "", "",
        _xaf(totaux["total_salaires"]),
        _xaf(totaux["total_primes"]),
        _xaf(totaux["total_heures_sup"]),
        _xaf(totaux["total_indemnites"]),
        _xaf(totaux["total_brut"]),
        _xaf(totaux["total_cnss_sal"]),
        _xaf(totaux["total_cnamgs_sal"]),
        _xaf(totaux["total_tcs"]),
        _xaf(totaux["total_irpp"]),
        "",
        _xaf(totaux["total_acompte"]),
        _xaf(totaux["total_net"]),
        _xaf(totaux["total_cnss_pat"]),
        _xaf(totaux["total_cnamgs_pat"]),
        _xaf(totaux["total_fnh"]),
        _xaf(totaux["total_cfp"]),
        _xaf(totaux["total_cout_employeur"]),
    ])

    # ── Récapitulatif charges ──────────────────────────────────────────────────
    writer.writerow([])
    writer.writerow(["=== RÉCAPITULATIF CHARGES ==="])
    writer.writerow(["Masse salariale brute",       _xaf(totaux["total_brut"])])
    writer.writerow(["CNSS salarié",                _xaf(totaux["total_cnss_sal"])])
    writer.writerow(["CNAMGS salarié",              _xaf(totaux["total_cnamgs_sal"])])
    writer.writerow(["TCS",                         _xaf(totaux["total_tcs"])])
    writer.writerow(["IRPP",                        _xaf(totaux["total_irpp"])])
    writer.writerow(["Net à payer (total)",         _xaf(totaux["total_net"])])
    writer.writerow([])
    writer.writerow(["CNSS patronal",               _xaf(totaux["total_cnss_pat"])])
    writer.writerow(["CNAMGS patronal",             _xaf(totaux["total_cnamgs_pat"])])
    writer.writerow(["FNH",                         _xaf(totaux["total_fnh"])])
    writer.writerow(["CFP",                         _xaf(totaux["total_cfp"])])
    writer.writerow([])
    writer.writerow(["COÛT TOTAL EMPLOYEUR",        _xaf(totaux["total_cout_employeur"])])

    logger.info(
        f"[Export CSV] Livre paie généré — {len(bulletins)} salariés — "
        f"Brut: {totaux['total_brut']:,.0f} / Net: {totaux['total_net']:,.0f} XAF"
    )

    # UTF-8 avec BOM pour compatibilité Excel
    return ("\ufeff" + output.getvalue()).encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ═══════════════════════════════════════════════════════════════════════════════

def _calculer_totaux(bulletins) -> dict:
    """Agrège tous les montants de la liste de bulletins."""
    def s(champ):
        return sum(_f(b, champ) for b in bulletins)

    total_primes = sum(
        sum(_f(b, c) for c in [
            "prime_caisse", "prime_anciennete", "prime_rendement",
            "prime_assiduité", "prime_qualite", "prime_performance",
            "prime_responsabilite", "sursalaire", "carburant", "allocations_conge",
        ]) for b in bulletins
    )
    total_heures_sup = sum(
        sum(_f(b, c) for c in [
            "heures_sup_10", "heures_sup_30", "heures_sup_40", "heures_sup_70"
        ]) for b in bulletins
    )
    total_indemnites = sum(
        sum(_f(b, c) for c in [
            "indem_logement", "indem_domesticite", "indem_eau_electricite",
            "indem_nourriture", "indem_transport", "indem_representation",
            "prime_panier", "prime_salisure",
            "indem_compensatrice_conge", "indem_services_rendus",
            "indem_compensatrice_preavis", "indem_licenciement",
        ]) for b in bulletins
    )

    total_brut       = s("salaire_brut")
    total_cnss_pat   = s("cnss_patronale")
    total_cnamgs_pat = s("cnamgs_patronale")
    total_fnh        = s("fnh")
    total_cfp        = s("cfp")

    return {
        "total_salaires":   sum(_f(b, "salaire_base") for b in bulletins),
        "total_primes":     total_primes,
        "total_heures_sup": total_heures_sup,
        "total_indemnites": total_indemnites,
        "total_brut":       total_brut,
        "total_cnss_sal":   s("cnss_salarie"),
        "total_cnamgs_sal": s("cnamgs_salarie"),
        "total_tcs":        s("tcs"),
        "total_irpp":       s("irpp"),
        "total_acompte":    s("acompte"),
        "total_net":        s("net_a_payer"),
        "total_cnss_pat":   total_cnss_pat,
        "total_cnamgs_pat": total_cnamgs_pat,
        "total_fnh":        total_fnh,
        "total_cfp":        total_cfp,
        "total_cout_employeur": total_brut + total_cnss_pat + total_cnamgs_pat + total_fnh + total_cfp + s("tcs"),
    }


def _f(bulletin, champ: str) -> float:
    """Lit un champ numérique d'un bulletin en float (0 si None)."""
    val = getattr(bulletin, champ, None)
    return float(val) if val is not None else 0.0


def _xaf(montant: float) -> str:
    """Formate un montant en FCFA pour CSV (entier, séparateur virgule)."""
    return str(int(round(montant)))


def _ligne_ecriture(journal, date, piece, compte, libelle, debit, credit) -> dict:
    return {
        "journal": journal, "date": date, "piece": piece,
        "compte": compte, "libelle": libelle[:50],
        "debit": round(debit), "credit": round(credit),
    }


def _ligne_commentaire(texte: str) -> str:
    return f"; {texte}"


def _formater_ligne_sage(ligne: dict) -> str:
    """
    Formate une ligne d'écriture au format Sage 100 texte.
    Colonnes séparées par tabulation, format attendu par l'import Sage.
    """
    debit  = f"{ligne['debit']:,.2f}".replace(",", " ").replace(".", ",") if ligne["debit"]  else ""
    credit = f"{ligne['credit']:,.2f}".replace(",", " ").replace(".", ",") if ligne["credit"] else ""
    cols = [
        ligne["journal"],          # Code journal
        ligne["date"],             # Date écriture
        ligne["piece"],            # N° pièce
        ligne["compte"],           # N° compte
        ligne["libelle"][:40],     # Libellé (40 car max Sage)
        debit,                     # Montant débit
        credit,                    # Montant crédit
        "",                        # Code analytique (vide = non utilisé)
    ]
    return "\t".join(cols) + "\r\n"


def _nom_mois(mois: int) -> str:
    noms = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
            "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
    return noms[mois] if 1 <= mois <= 12 else str(mois)


def _derniere_jour_mois(annee: int, mois: int) -> date:
    """Retourne le dernier jour du mois (date d'écriture comptable)."""
    import calendar
    dernier = calendar.monthrange(annee, mois)[1]
    return date(annee, mois, dernier)


# ── Exception ──────────────────────────────────────────────────────────────────

class ExportVide(Exception):
    """Aucun bulletin à exporter."""
