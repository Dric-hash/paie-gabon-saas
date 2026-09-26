"""
blueprints/tenant.py — Toutes les routes tenant :
    dashboard, salariés, bulletins, congés, acomptes, périodes,
    journaliers, pointage, sites, rapports, exports, paiement,
    paramètres, utilisateurs, audit, recherche, simulateur
"""
import os, io, json, hmac, math, logging
import secrets as sec
from datetime import datetime, date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, abort, session, Response, current_app)
from flask_login import login_required, current_user, logout_user
from flask_mail import Message
from sqlalchemy.orm import joinedload
from sqlalchemy import func

from models import (db, utcnow, Plan, Tenant, Utilisateur, CategorieEmploi, Salarie,
                    Contrat, PeriodePaie, BulletinPaie, RubriquePaie, Conge,
                    Acompte, Journalier, Pointage, FeuillePaieJournalier,
                    Site, AffectationSite, Paiement, OAuthClient, AuditLog,
                    Prestataire, FacturePrestataire, ComposantPaie, BulletinComposant,
                    AvanceJournalier, MessageSupport, ModeleContrat)
from calculs_paie import (calculer_bulletin, calculer_masse_salariale,
                           calculer_heures_sup_btp, distribuer_heures_semaine_btp,
                           calculer_prime_anciennete_btp, calculer_preavis_btp,
                           calculer_indemnite_services_rendus_btp, ventiler_heures_mois)
from audit import log_action, get_audit_logs
from core import (get_tenant, tenant_required, can_edit, admin_only,
                  require_permission, calculer_parts_irpp, parse_date,
                  cache_get, cache_set, cache_delete, csv_safe,
                  _cache_get, _cache_set, _cache_delete, _parse_date, _pd,
                  send_email_async, plan_required,
                  TTL_KPIS_DASH, TTL_EVOLUTION, TTL_CATS_STATS, TTL_ALERTES)
from i18n import SUPPORTED_LANGUAGES, set_language
from jours_feries import (jours_feries_annee, est_jour_ferie,
                          nom_jour_ferie, type_jour_auto)
from notifications import get_notifications, compter_notifications

# Modèles passés au module notifications (évite les imports circulaires)
_NOTIF_MODELS = {
    "Contrat": Contrat, "Conge": Conge, "Salarie": Salarie,
    "PeriodePaie": PeriodePaie, "FacturePrestataire": FacturePrestataire,
    "Prestataire": Prestataire,
}

logger = logging.getLogger("paiegalon")

bp = Blueprint("tenant", __name__)


def _config_rubriques_dict(tenant_id):
    """Renvoie la config des rubriques fixes souples pour un tenant :
    {cle: {entre_dans_brut, soumis_cnss, soumis_cnamgs, soumis_irpp, position}}.
    Absente → le calcul applique le défaut (hors brut, non soumis)."""
    try:
        from models import ConfigRubrique
        out = {}
        for c in ConfigRubrique.query.filter_by(tenant_id=tenant_id).all():
            out[c.cle] = {"entre_dans_brut": c.entre_dans_brut, "soumis_cnss": c.soumis_cnss,
                          "soumis_cnamgs": c.soumis_cnamgs, "soumis_irpp": c.soumis_irpp,
                          "position": c.position}
        return out
    except Exception:
        return {}


def _pdf_bulletin_bytes(b, t):
    """Choisit le générateur PDF selon le modèle du tenant."""
    from pdf_bulletin import generer_bulletin_pdf, generer_bulletin_detaille_pdf
    if (getattr(t, "modele_bulletin", None) or "classique") == "sgtg":
        return generer_bulletin_detaille_pdf(b, t)
    return generer_bulletin_pdf(b, t)


# Primes récurrentes définissables au niveau du contrat (clés = champs de saisie)
PRIMES_RECURRENTES = ["sursalaire", "prime_transport", "prime_responsabilite",
                      "indem_logement", "carburant", "prime_panier",
                      "indem_representation", "indem_transport", "prime_salisure"]

def _build_elements_recurrents(tenant_id):
    """Construit le JSON des primes récurrentes depuis la liste dynamique du
    formulaire de contrat (paires rec_key[] / rec_val[]). Renvoie JSON ou None."""
    import json
    keys = request.form.getlist("rec_key")
    vals = request.form.getlist("rec_val")
    elems = {}
    for k, v in zip(keys, vals):
        k = (k or "").strip()
        try:
            montant = float(v)
        except (TypeError, ValueError):
            continue
        if k and montant:
            elems[k] = montant
    return json.dumps(elems) if elems else None

# ── Rôles assignables au sein d'un tenant ─────────────────────────────────────
# Liste blanche stricte : un admin de tenant ne peut JAMAIS attribuer le rôle
# plateforme SUPER_ADMIN (sinon escalade de privilèges → accès cross-tenant).
ROLES_TENANT_AUTORISES = {
    "TENANT_ADMIN", "RH", "COMPTABLE", "DIRECTEUR", "GESTIONNAIRE", "LECTURE",
}


@bp.before_request
def _exiger_email_confirme():
    """
    Bloque l'accès aux pages tenant tant que l'email n'est pas confirmé.
    Ne s'applique qu'aux utilisateurs tenant authentifiés et non vérifiés.
    Les routes de déconnexion / confirmation / renvoi appartiennent au
    blueprint `auth` et ne sont donc jamais interceptées ici. Les appels
    JSON (calcul temps réel, simulateur) sont laissés passer pour ne pas
    casser le front : un utilisateur non confirmé ne peut de toute façon
    pas naviguer vers les pages qui les déclenchent.
    """
    if not current_user.is_authenticated:
        return
    if current_user.is_super_admin or getattr(current_user, "email_verifie", True):
        return
    # Laisser passer les requêtes JSON/API internes (pas de blocage HTML utile).
    if request.path.rsplit("/", 1)[-1].startswith("api") or "/api/" in request.path \
       or request.is_json or "application/json" in (request.headers.get("Accept", "")):
        return
    return render_template("auth/email_non_confirme.html", email=current_user.email), 403


# Quand l'abonnement a expiré, seule la section paiement reste accessible
# (blocage sec). Le tenant a été averti 72 h avant l'échéance.
_PAIEMENT_PREFIXE = "/paiement"


@bp.before_request
def _bloquer_si_expire():
    """Blocage sec à l'expiration : tout est redirigé vers la page de paiement.
    Le tenant a reçu un avertissement 72 h avant l'échéance. Seule la section
    paiement reste ouverte, pour qu'il puisse régler (Airtel Money / virement)."""
    if not current_user.is_authenticated or current_user.is_super_admin:
        return
    t = getattr(current_user, "tenant", None)
    if not t or not t.est_expire:
        return
    if request.path.startswith(_PAIEMENT_PREFIXE):
        return
    if request.is_json or "/api/" in request.path \
       or "application/json" in (request.headers.get("Accept", "")):
        return jsonify({
            "error": "Abonnement expiré. Veuillez renouveler pour continuer.",
            "renouveler": url_for("tenant.paiement"),
        }), 402
    flash("Votre abonnement a expiré. Veuillez le renouveler pour continuer à utiliser PaieGabon.", "error")
    return redirect(url_for("tenant.paiement"))





# ══════════════════════════════════════════════════════════════════════════════
# GESTION DES CONTRATS SALARIÉS
# ══════════════════════════════════════════════════════════════════════════════

def attribuer_numero_bulletin(b):
    """
    Attribue un numéro séquentiel immuable au bulletin lors de sa validation.
    Format : BP-<année>-<séquence 6 chiffres>, continu et unique par tenant.
    Ne fait rien si le bulletin a déjà un numéro (immuabilité du document).
    """
    if b.numero:
        return
    annee = b.periode.annee if b.periode else utcnow().year
    dernier = (db.session.query(db.func.max(BulletinPaie.numero_seq))
               .filter(BulletinPaie.tenant_id == b.tenant_id).scalar()) or 0
    b.numero_seq = dernier + 1
    b.numero     = f"BP-{annee}-{b.numero_seq:06d}"


def _recap_sites_salarie_periode(t, salarie_id, annee, mois):
    """Heures travaillées par site pour un salarié sur un mois (multi-chantiers).

    Un salarié peut pointer sur plusieurs chantiers dans le mois. On agrège ses
    heures par site pour les afficher sur le bulletin. Renvoie (recap_sites,
    multi_sites). N'altère pas le calcul de paie : la rémunération couvre déjà
    l'ensemble des heures, tous sites confondus.
    """
    import calendar
    debut = date(annee, mois, 1)
    fin   = date(annee, mois, calendar.monthrange(annee, mois)[1])
    pts = (Pointage.query
           .filter_by(tenant_id=t.id, salarie_id=salarie_id)
           .filter(Pointage.date_pointage >= debut, Pointage.date_pointage <= fin,
                   Pointage.present == True, Pointage.absent == False)
           .options(joinedload(Pointage.site))
           .all())
    agg = {}
    for p in pts:
        h = (float(p.heures_normales or 0) + float(p.heures_sup or 0)
             + float(p.heures_sup_10 or 0) + float(p.heures_sup_30 or 0)
             + float(getattr(p, "heures_sup_30b", 0) or 0)
             + float(p.heures_sup_40 or 0) + float(p.heures_sup_70 or 0))
        nom = (p.site.nom if getattr(p, "site", None) else None) or "Non affecté"
        e = agg.setdefault(nom, {"nom": nom, "heures": 0.0, "jours": 0})
        e["heures"] += h
        e["jours"]  += 1
    recap = sorted(agg.values(), key=lambda x: -x["heures"])
    for e in recap:
        e["heures"] = round(e["heures"], 2)
    return recap, (len(recap) > 1)


def _bulletin_imprimer_impl(id):
    """Aperçu imprimable du bulletin (HTML), disponible dès le brouillon.

    Impression = consultation : accessible à tout utilisateur du tenant
    (pas de restriction can_edit), y compris pour un bulletin en BROUILLON.
    """
    if current_user.is_super_admin:
        b = BulletinPaie.query.get_or_404(id)
        t = b.salarie.tenant
    else:
        t = get_tenant()
        if not t:
            return redirect(url_for("auth.login"))
        b = BulletinPaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    # Modèle de bulletin choisi par le tenant, avec repli sécurisé.
    try:
        modele = t.modele_bulletin or "classique"
    except Exception:
        modele = "classique"
    template_map = {
        "classique":   "tenant/bulletin_print.html",
        "moderne":     "tenant/bulletin_print_moderne.html",
        "minimaliste": "tenant/bulletin_print_minimaliste.html",
        "grandlivre":  "tenant/bulletin_print_grandlivre.html",
        "sgtg":        "tenant/bulletin_print_sgtg.html",
    }
    template = template_map.get(modele, "tenant/bulletin_print.html")
    # Vérifier que le template existe réellement sur le serveur, sinon repli.
    import os
    tpl_path = os.path.join(os.path.dirname(__file__), "..", "templates", template)
    if not os.path.exists(tpl_path):
        template = "tenant/bulletin_print.html"
    composants = BulletinComposant.query.filter_by(bulletin_id=b.id).all()
    # Répartition par site (multi-chantiers) — affichée seulement si > 1 site.
    from models import PeriodePaie
    per = db.session.get(PeriodePaie, b.periode_id)
    recap_sites, multi_sites = ([], False)
    if per is not None:
        recap_sites, multi_sites = _recap_sites_salarie_periode(t, b.salarie_id, per.annee, per.mois)

    # ── Cumuls année à date + congés (pour le modèle détaillé) ──────
    cumuls = None; conges_annees = []
    try:
        annee = per.annee if per else None
        if annee:
            bs = (BulletinPaie.query.join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
                  .filter(BulletinPaie.tenant_id == t.id, BulletinPaie.salarie_id == b.salarie_id,
                          PeriodePaie.annee == annee).all())
            f = lambda x: float(x or 0)
            cot_sal = sum(f(x.cnss_salarie)+f(x.cnamgs_salarie)+f(x.tcs)+f(x.irpp) for x in bs)
            cot_pat = sum(f(x.cnss_patronale)+f(x.cnamgs_patronale)+f(x.fnh)+f(x.cfp) for x in bs)
            brut_c  = sum(f(x.salaire_brut) for x in bs)
            cumuls = {"jours": sum(f(x.nb_jours_travailles) for x in bs),
                      "brut": brut_c, "net_impos": sum(f(x.base_irpp) for x in bs),
                      "cot_salar": cot_sal, "cot_patron": cot_pat,
                      "cot_global": cot_sal + cot_pat, "cout_total": brut_c + cot_pat}
        conges_annees = sorted(getattr(b.salarie, "conges", []) or [], key=lambda c: c.annee or 0)
    except Exception as _e:
        logger.warning(f"Cumuls/congés bulletin {b.id} indisponibles : {_e}")

    return render_template(template, bulletin=b, tenant=t, composants=composants,
                           recap_sites=recap_sites, multi_sites=multi_sites,
                           cumuls=cumuls, conges_annees=conges_annees,
                           config_rubriques=_config_rubriques_dict(t.id))


# ✅ ENVOI EMAIL ASYNCHRONE — ne bloque plus le serveur


def _activer_abonnement(paiement: "Paiement"):
    """
    Appelé après confirmation d'un paiement (webhook ou polling).
    Met à jour le tenant : statut ACTIF, date_expiration prolongée.
    Envoie un email de confirmation.
    """
    from datetime import timezone
    p = paiement
    p.statut           = "SUCCES"
    p.date_confirmation = utcnow()

    t = p.tenant
    now = utcnow()

    # Prolonger depuis aujourd'hui ou depuis la date d'expiration si future
    base = t.date_expiration if (t.date_expiration and t.date_expiration > now) else now
    t.date_expiration = base + timedelta(days=30 * p.duree_mois)
    t.statut = "ACTIF"

    if p.plan_id:
        t.plan_id = p.plan_id

    db.session.commit()
    _cache_delete(f"{t.id}:")  # invalider le cache dashboard

    logger.info(
        f"[Abonnement] Tenant {t.id} activé jusqu'au "
        f"{t.date_expiration.strftime('%d/%m/%Y')} — "
        f"{p.duree_mois} mois via {p.moyen}"
    )

    # Email de confirmation
    try:
        msg = Message(
            subject=f"Abonnement PaieGabon activé — {t.denomination}",
            recipients=[u.email for u in t.utilisateurs if u.role == "TENANT_ADMIN" and u.email],
            body=(
                f"Bonjour,\n\n"
                f"Votre paiement de {float(p.montant):,.0f} FCFA a été confirmé.\n"
                f"Abonnement actif jusqu'au : {t.date_expiration.strftime('%d/%m/%Y')}\n"
                f"Référence : {p.reference_interne}\n\n"
                f"Merci de votre confiance.\n"
                f"L'équipe PaieGabon"
            ),
        )
        send_email_async(current_app.extensions["mail"], msg)
    except Exception as e:
        logger.warning(f"[Abonnement] Email de confirmation non envoyé : {e}")


# ══════════════════════════════════════════════════════════════════════════════
# CINETPAY — Paiement multi-opérateurs (Airtel, Moov, Visa, Mastercard)
# ══════════════════════════════════════════════════════════════════════════════

def _grille_tenant(t):
    """Grille de salaires ENREGISTRÉE du tenant → dict {code: {echelon: montant}}.
    Vide si non renseignée. Utilisée pour le pré-remplissage du salaire de base."""
    import json as _json
    if not t or not t.grille_salaires:
        return {}
    try:
        return _json.loads(t.grille_salaires)
    except (ValueError, TypeError):
        return {}




# ══════════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL — Journal des actions
# ══════════════════════════════════════════════════════════════════════════════







def _doc_response(pdf_bytes, nom_fichier):
    """Helper : renvoie un PDF en téléchargement."""
    from flask import Response
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"',
                             "Content-Length": str(len(pdf_bytes))})




# ══════════════════════════════════════════════════════════════════════════════
# ── IMPRESSION DES POINTAGES (salariés & journaliers) ─────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_MOIS_FR = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
            "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
_JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_TYPE_JOUR_LABEL = {
    "NORMAL": "Ordinaire", "DIMANCHE": "Dimanche", "FERIE": "Férié travaillé",
    "CHOME_PAYE": "Férié chômé payé", "CHOME_RECUPERABLE": "Férié récupérable",
}


def _pointages_mois_contexte(t, pointages, convention):
    """Construit le contexte d'impression d'un relevé de pointage mensuel.

    Retourne un dict : lignes par jour, totaux ventilés (heures normales et
    supplémentaires) et, pour la convention BTP, le détail de répartition
    semaine par semaine (utile en cas de réclamation du travailleur).
    """
    pts = sorted(pointages, key=lambda p: p.date_pointage)
    conv = (convention or "").upper()

    # Carte des heures de nuit PAR JOUR (BTP) : calculée depuis les horaires
    # réels via la même fonction que la ventilation (pointage_vers_jours), afin
    # que la colonne « DONT NUIT » de chaque ligne soit cohérente avec le total
    # +40 % affiché en bas (somme des lignes = total).
    nuit_par_date = {}
    if conv in ("BTP", "PETROLE", "INDUSTRIE", "AERIEN"):
        from calculs_paie import pointage_vers_jours
        for j in pointage_vers_jours(pts):
            d = j.get("date")
            if d is not None:
                nuit_par_date[d] = nuit_par_date.get(d, 0.0) + float(j.get("heures_nuit") or 0)

    lignes = []
    for p in pts:
        hn   = float(p.heures_normales or 0)
        hsup = float(p.heures_sup or 0)     # heures sup "simples" (journaliers, non majorées)
        h10  = float(p.heures_sup_10 or 0)
        h30  = float(p.heures_sup_30 or 0)
        h30b = float(getattr(p, "heures_sup_30b", 0) or 0)
        h40  = float(p.heures_sup_40 or 0)
        h70  = float(p.heures_sup_70 or 0)
        absent = bool(p.absent)
        present = bool(p.present) and not absent
        total_jour = hn + hsup + h10 + h30 + h30b + h40 + h70
        tj = (p.type_jour or "NORMAL").upper()
        # Nuit du jour : depuis l'horaire (BTP/Pétrole/Industrie), sinon valeur stockée (journaliers)
        if conv in ("BTP", "PETROLE", "INDUSTRIE", "AERIEN"):
            nuit_jour = nuit_par_date.get(p.date_pointage, 0.0)
        else:
            nuit_jour = h40
        lignes.append({
            "date": p.date_pointage,
            "jour_sem": _JOURS_FR[p.date_pointage.weekday()],
            "type_label": _TYPE_JOUR_LABEL.get(tj, tj.title()),
            "present": present, "absent": absent,
            "motif": p.motif_absence or "",
            "site": (p.site.nom if getattr(p, "site", None) else ""),
            "heures_travaillees": round(total_jour, 2),
            "heures_nuit": round(nuit_jour, 2),
            "observation": p.observation or "",
        })

    pts_travailles = [p for p in pts if p.present and not p.absent]
    pts_absents    = [p for p in pts if p.absent]

    if conv in ("BTP", "PETROLE", "INDUSTRIE", "AERIEN", "MINIER") and pts_travailles:
        from calculs_paie import ventiler_heures_mois, pointage_vers_jours
        _feries = set()
        for _an in {p.date.year for p in pts if getattr(p, "date", None)}:
            _feries |= set(jours_feries_annee(_an).keys())
        v = ventiler_heures_mois(conv, pointage_vers_jours(pts), feries=_feries, seuil_normales=t.seuil_hs)
        totaux = {
            "heures_normales": v["heures_normales"],
            "heures_sup_10":   v["heures_sup_10"],
            "heures_sup_30":   v["heures_sup_30"],
            "heures_sup_30b":  v.get("heures_sup_30b", 0.0),
            "heures_sup_40":   v["heures_sup_40"],
            "heures_sup_70":   v["heures_sup_70"],
            "heures_sup_fj":   v.get("heures_sup_fj", 0.0),
            "heures_sup_fn":   v.get("heures_sup_fn", 0.0),
        }
        detail_semaines = v.get("detail_semaines", [])
    else:
        totaux = {
            "heures_normales": sum(float(p.heures_normales or 0) for p in pts_travailles),
            "heures_sup_10":   sum(float(p.heures_sup_10 or 0) for p in pts_travailles),
            "heures_sup_30":   sum(float(p.heures_sup_30 or 0) for p in pts_travailles),
            "heures_sup_30b":  sum(float(getattr(p, "heures_sup_30b", 0) or 0) for p in pts_travailles),
            "heures_sup_40":   sum(float(p.heures_sup_40 or 0) for p in pts_travailles),
            "heures_sup_70":   sum(float(p.heures_sup_70 or 0) for p in pts_travailles),
        }
        detail_semaines = []

    totaux = {k: round(v, 2) for k, v in totaux.items()}
    # Heures sup "simples" (colonne heures_sup) : utilisées par les journaliers,
    # non majorées. Nulles pour les salariés BTP (qui utilisent les tranches).
    heures_sup_simple = round(sum(float(p.heures_sup or 0) for p in pts_travailles), 2)
    totaux["heures_sup_simple"] = heures_sup_simple
    totaux["total_sup"] = round(totaux["heures_sup_10"] + totaux["heures_sup_30"]
                                + totaux.get("heures_sup_30b", 0)
                                + totaux["heures_sup_40"] + totaux["heures_sup_70"]
                                + heures_sup_simple, 2)
    totaux["total_general"] = round(totaux["heures_normales"] + totaux["total_sup"], 2)
    # Total des heures de nuit = somme des heures de nuit de chaque ligne (cohérent
    # avec la colonne « dont nuit » du tableau).
    totaux["heures_nuit"] = round(sum(l["heures_nuit"] for l in lignes), 2)
    # Heures supplémentaires DE JOUR = total des sup. moins la part de nuit, pour
    # trois catégories additives sans double comptage :
    #   heures normales + heures sup. de jour + heures de nuit = total travaillé.
    totaux["heures_sup_jour"] = round(max(0.0, totaux["total_sup"] - totaux["heures_nuit"]), 2)
    totaux["nb_jours"]    = len(pts_travailles)
    totaux["nb_absences"] = len(pts_absents)

    # Taux de majoration RÉELS de la convention, pour étiqueter correctement les
    # tranches (BTP ≠ Pétrole ≠ Industrie). Évite d'afficher « +10 % » à tort.
    from calculs_paie import coeffs_heures_sup
    _c = coeffs_heures_sup(conv)
    coeffs_pct = {k: int(round((float(v) - 1) * 100)) for k, v in _c.items()}

    # Répartition par site (multi-chantiers) : un salarié/journalier peut
    # travailler sur plusieurs sites dans le mois. On agrège ses heures par site
    # à partir des lignes (jours travaillés uniquement).
    sites_agg = {}
    for p, l in zip(pts, lignes):
        if l["absent"]:
            continue
        nom = (p.site.nom if getattr(p, "site", None) else None) or "Non affecté"
        e = sites_agg.setdefault(nom, {"nom": nom, "heures": 0.0, "nuit": 0.0, "jours": 0})
        e["heures"] += l["heures_travaillees"]
        e["nuit"]   += l["heures_nuit"]
        e["jours"]  += 1
    recap_sites = sorted(sites_agg.values(), key=lambda x: -x["heures"])
    for e in recap_sites:
        e["heures"] = round(e["heures"], 2)
        e["nuit"]   = round(e["nuit"], 2)
    multi_sites = len(recap_sites) > 1

    return {"lignes": lignes, "totaux": totaux, "detail_semaines": detail_semaines,
            "convention": conv, "coeffs_pct": coeffs_pct,
            "recap_sites": recap_sites, "multi_sites": multi_sites}


def _resoudre_mois_annee():
    now = datetime.now()
    mois  = request.args.get("mois",  type=int) or now.month
    annee = request.args.get("annee", type=int) or now.year
    mois  = min(max(mois, 1), 12)
    return mois, annee



# ══════════════════════════════════════════════════════════════════════════════
# ── SITES & AFFECTATIONS ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

















# ══════════════════════════════════════════════════════════════════════════════
# RAPPORT MENSUEL PAR SITE
# ══════════════════════════════════════════════════════════════════════════════








# ══════════════════════════════════════════════════════════════════════════════
# EXPORT COMPTABLE SAGE 100
# ══════════════════════════════════════════════════════════════════════════════







# ══════════════════════════════════════════════════════════════════════════════
# RAPPORT PDF MENSUEL
# ══════════════════════════════════════════════════════════════════════════════





# ══════════════════════════════════════════════════════════════════════════════
# API REST v1 — Intégration grandes entreprises
# ══════════════════════════════════════════════════════════════════════════════
# Authentification : X-API-Key: <token>  ou  Authorization: Bearer <oauth_token>
# Tous les endpoints retournent JSON. Préfixe : /api/v1/
# ══════════════════════════════════════════════════════════════════════════════

from api_rest import (api_auth_required, _ok, _err, _paginate,
                      _salarie_dict, _bulletin_dict, _periode_dict,
                      OAUTH_TOKEN_TTL)


# ══════════════════════════════════════════════════════════════════════════════
# RECHERCHE GLOBALE
# ══════════════════════════════════════════════════════════════════════════════




# ═══════════════════════════════════════════════════════════════════════════
#  POINTAGE SALARIÉS — import mensuel (format LONG : 1 ligne / salarié / jour)
#  Circuit : modèle Excel → remplir → téléverser (aperçu) → confirmer (brouillons)
#  Le classement des heures (10/30/40/70) et la détection dimanche/férié sont
#  délégués à ventiler_heures_mois() ; aucune règle de paie n'est réécrite ici.
# ═══════════════════════════════════════════════════════════════════════════
_MOIS_FR_SAL = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
_JOURS_FR_SAL = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
# Motifs d'absence reconnus (comptés, jamais déduits automatiquement)
_MOTIFS_ABSENCE = ["CONGE", "MALADIE", "INJUSTIFIEE"]


def _sal_periode_demandee():
    """Lit le mois demandé (YYYY-MM) ou le mois courant. Retourne (annee, mois)."""
    mois_str = request.args.get("mois", "") or request.form.get("mois", "")
    try:
        annee, mois = (int(x) for x in mois_str.split("-"))
        date(annee, mois, 1)
        return annee, mois
    except (ValueError, TypeError):
        today = datetime.now()
        return today.year, today.month


def _lire_pointage_salaries(fichier):
    """Lit le fichier Excel de pointage (format LONG) et regroupe les lignes par
    salarié (matricule). Retourne (data, erreurs) où data est un dict
    matricule -> {"nom":..., "jours":[...], "absences":{motif:count}}.
    Chaque jour = {"date": date, "heures": float, "heures_nuit": float}."""
    from openpyxl import load_workbook
    erreurs = []
    try:
        wb = load_workbook(fichier, data_only=True, read_only=True)
    except Exception:
        return None, ["Fichier illisible : n'est pas un classeur Excel valide."]
    ws = wb.active

    # Repérer la ligne d'en-tête (celle qui contient "Matricule")
    entete_row = None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=12, values_only=True), 1):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if "matricule" in cells:
            entete_row = i
            break
    if entete_row is None:
        return None, ["En-tête introuvable : le fichier doit contenir une colonne « Matricule »."]

    data = {}
    for row in ws.iter_rows(min_row=entete_row + 1, values_only=True):
        if not row or all(c is None for c in row):
            continue
        mat = (str(row[0]).strip() if row[0] is not None else "")
        if not mat:
            continue
        nom = (str(row[1]).strip() if len(row) > 1 and row[1] is not None else "")
        date_str = (str(row[2]).strip() if len(row) > 2 and row[2] is not None else "")
        heures = row[5] if len(row) > 5 else None
        nuit = row[6] if len(row) > 6 else None
        motif = (str(row[7]).strip().upper() if len(row) > 7 and row[7] is not None else "")

        # Date : accepte "JJ/MM/AAAA" ou un vrai objet date Excel
        d = None
        if isinstance(date_str, str) and "/" in date_str:
            try:
                jj, mm, aa = (int(x) for x in date_str.split("/"))
                d = date(aa, mm, jj)
            except (ValueError, TypeError):
                d = None
        if d is None and hasattr(row[2], "year"):
            d = row[2] if isinstance(row[2], date) else None
        if d is None:
            continue  # ligne sans date exploitable : ignorée silencieusement

        entree = data.setdefault(mat, {"nom": nom, "jours": [], "absences": {}})
        # Absence : on compte par motif, on ne pose PAS d'heures ce jour-là
        if motif:
            m = motif if motif in _MOTIFS_ABSENCE else "AUTRE"
            entree["absences"][m] = entree["absences"].get(m, 0) + 1
            continue
        try:
            h = float(heures) if heures not in (None, "") else 0.0
        except (ValueError, TypeError):
            h = 0.0
        try:
            hn = float(nuit) if nuit not in (None, "") else 0.0
        except (ValueError, TypeError):
            hn = 0.0
        if h <= 0 and hn <= 0:
            continue  # jour non travaillé, rien à ventiler
        entree["jours"].append({"date": d, "heures": h, "heures_nuit": hn})

    return data, erreurs


# ═══════════════════════════════════════════════════════════════════════════
# MODÈLES DE CONTRAT — le tenant crée ses propres modèles (balises {{...}})
# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
# COLLABORATEURS DU CABINET — assignation d'entreprises
# ═══════════════════════════════════════════════════════════════════════════
# ── Sous-modules (routes réparties par thème) — importés en dernier ──
from blueprints.tenant import sites  # noqa: E402,F401
from blueprints.tenant import cabinet  # noqa: E402,F401
from blueprints.tenant import conges  # noqa: E402,F401
from blueprints.tenant import declarations  # noqa: E402,F401
from blueprints.tenant import journaliers  # noqa: E402,F401
from blueprints.tenant import salaries  # noqa: E402,F401
from blueprints.tenant import bulletins  # noqa: E402,F401
from blueprints.tenant import parametres  # noqa: E402,F401
from blueprints.tenant import paiements  # noqa: E402,F401
from blueprints.tenant import divers  # noqa: E402,F401
