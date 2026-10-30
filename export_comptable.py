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

Comptes du journal : paramétrables par l'entreprise (plan_comptable_paie.py),
proposés par défaut selon le SYSCOHADA révisé — 661/662 salaires bruts,
663 éléments hors brut, 664 cotisations patronales, 6413 taxes sur salaires,
431 CNSS, 433 CNAMGS, 447 impôts retenus, 442 taxes à payer, 421 acomptes,
422 net à payer. (Avant le 20/10/2026 : comptes 641/645, qui sont en
SYSCOHADA des comptes d'impôts et taxes, et journal non équilibré.)
"""

import csv
import io
import logging
from core import csv_safe
from datetime import date, datetime

logger = logging.getLogger("paiegalon.export_comptable")

# ═══════════════════════════════════════════════════════════════════════════════
# JOURNAL DE PAIE — Format Sage 100 texte
# ═══════════════════════════════════════════════════════════════════════════════

def generer_journal_paie(bulletins, periode, tenant) -> bytes:
    """
    Journal de paie mensuel (une écriture agrégée, équilibrée), au format texte
    importable dans Sage 100 Comptabilité (Fichier → Importer → Journal de saisie).

    Comptes : ceux enregistrés par l'entreprise (Paramètres → Comptes du journal
    de paie), à défaut ceux proposés selon le SYSCOHADA révisé
    (voir plan_comptable_paie.RUBRIQUES).

    Format des colonnes (séparateur tabulation) :
      Journal | Date | N°Pièce | Compte | Libellé | Débit | Crédit | Analytique

    Returns:
        bytes du fichier .txt encodé Windows-1252 (compatible Sage)
    """
    import plan_comptable_paie as pcp
    if not bulletins:
        raise ExportVide("Aucun bulletin validé pour cette période.")

    lignes_compta = ecriture_paie(bulletins, tenant)
    mois_nom = _nom_mois(periode.mois)
    date_str = _derniere_jour_mois(periode.annee, periode.mois).strftime("%d/%m/%Y")
    num_piece = f"PAIE{periode.annee}{periode.mois:02d}"
    journal_code = pcp.journal(tenant)
    libelle_base = f"Paie {mois_nom} {periode.annee}"
    totaux = _calculer_totaux(bulletins)

    lignes = [
        _ligne_commentaire(f"Export PaieGabon — {tenant.denomination} — {mois_nom} {periode.annee}"),
        _ligne_commentaire(f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')} — {len(bulletins)} bulletins — "
                           f"Masse salariale brute : {totaux['total_brut']:,.0f} XAF"),
        _ligne_commentaire("Comptes : " + ("paramétrés par l'entreprise" if pcp.personnalises(tenant)
                                           else "proposés par défaut (SYSCOHADA révisé), à vérifier dans Paramètres")),
    ]
    for l in lignes_compta:
        lignes.append(_ligne_ecriture(journal=journal_code, date=date_str, piece=num_piece, compte=l["compte"],
                                      libelle=f"{libelle_base} — {l['libelle']}", debit=l["debit"], credit=l["credit"]))

    total_debit = sum(l["debit"] for l in lignes if isinstance(l, dict))
    total_credit = sum(l["credit"] for l in lignes if isinstance(l, dict))
    if total_debit != total_credit:            # impossible par construction (solde hors brut) ; garde-fou
        raise ValueError(f"Journal de paie déséquilibré : débit {total_debit} / crédit {total_credit}")
    logger.info(f"[Export Sage] Journal paie généré — {len(bulletins)} bulletins — "
                f"Débit: {total_debit:,.0f} / Crédit: {total_credit:,.0f} XAF")

    output = io.StringIO()
    for ligne in lignes:
        output.write(_formater_ligne_sage(ligne) if isinstance(ligne, dict) else ligne + "\r\n")
    return output.getvalue().encode("windows-1252", errors="replace")


def ecriture_paie(bulletins, tenant) -> list[dict]:
    """Lignes de l'écriture de paie agrégée (montants arrondis au franc),
    équilibrée : les éléments hors brut (primes de panier, transport, arrondis…)
    sont le solde net à payer + acomptes + prêts + retenues salariales − brut."""
    import plan_comptable_paie as pcp
    cpt = pcp.comptes(tenant)

    def r(v):
        return int(round(v))

    nat = non_nat = 0
    t = {k: 0 for k in ("cnss_s", "cnss_p", "cnam_s", "cnam_p", "fnh", "cfp", "tcs", "irpp", "acompte", "pret", "net")}
    for b in bulletins:
        brut = r(_f(b, "salaire_brut"))
        s = getattr(b, "salarie", None)
        if pcp.est_national(getattr(s, "nationalite", "")):
            nat += brut
        else:
            non_nat += brut
        for k, champ in (("cnss_s", "cnss_salarie"), ("cnss_p", "cnss_patronale"), ("cnam_s", "cnamgs_salarie"),
                         ("cnam_p", "cnamgs_patronale"), ("fnh", "fnh"), ("cfp", "cfp"), ("tcs", "tcs"),
                         ("irpp", "irpp"), ("acompte", "acompte"), ("pret", "retenue_pret"), ("net", "net_a_payer")):
            t[k] += r(_f(b, champ))
    brut = nat + non_nat
    hors_brut = t["net"] + t["acompte"] + t["pret"] + t["cnss_s"] + t["cnam_s"] + t["tcs"] + t["irpp"] - brut
    montants = {
        "brut_national": nat, "brut_non_national": non_nat, "journaliers": 0, "hors_brut": hors_brut,
        "charges_sociales": t["cnss_p"] + t["cnam_p"], "taxes_salaires": t["fnh"] + t["cfp"],
        "cnss": t["cnss_s"] + t["cnss_p"], "cnamgs": t["cnam_s"] + t["cnam_p"], "impots_retenus": t["irpp"] + t["tcs"],
        "taxes_dues": t["fnh"] + t["cfp"], "avances": t["acompte"] + t["pret"], "net": t["net"],
    }
    out = []
    for k, lib, _, sens in pcp.RUBRIQUES:
        v = montants[k]
        if not v:
            continue
        au_debit = (sens == "D") == (v > 0)
        out.append({"cle": k, "compte": cpt[k], "libelle": lib, "debit": abs(v) if au_debit else 0,
                    "credit": 0 if au_debit else abs(v)})
    return out


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
    writer.writerow([f"LIVRE DE PAIE — {csv_safe(tenant.denomination)}"])
    writer.writerow([f"Période : {mois_nom} {periode.annee}"])
    writer.writerow([f"NIF : {csv_safe(tenant.nif) or '—'}"])
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
        cout_employeur = brut + cnss_pat + cnamgs_pat + fnh + cfp + tcs   # TCS comprise (décision du 30/10/2026)

        cat_nom = s.categorie.libelle if s.categorie else "—"

        writer.writerow([
            csv_safe(s.matricule),
            csv_safe(s.nom.upper()),
            csv_safe(s.prenom),
            csv_safe(s.emploi or "—"),
            csv_safe(cat_nom),
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
