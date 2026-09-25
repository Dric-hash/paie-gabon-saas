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

@bp.route("/parametres/api/regenerer-token", methods=["POST"])
@tenant_required
@admin_only
def regenerer_token_api():
    """Régénère le token API du tenant. Le token en clair n'est affiché qu'ici,
    une seule fois (il est stocké haché). Réservé aux administrateurs du tenant."""
    t = get_tenant()
    raw = t.generate_token()
    db.session.commit()
    log_action("REGENERATE", "token_api", t.id, "Régénération du token API")
    db.session.commit()
    flash(f"Nouveau token API : {raw} — copiez-le maintenant, il ne sera plus affiché.",
          "success")
    return redirect(url_for("api_v1.api_clients_list"))


@bp.route("/profil/2fa", methods=["POST"])
@login_required
def basculer_2fa():
    """Active ou désactive la double authentification par email pour soi-même."""
    if current_user.is_super_admin:
        flash("La 2FA du super-admin est gérée séparément.", "error")
        return redirect(url_for("tenant.parametres") + "#securite")
    activer = request.form.get("activer") == "1"
    if activer and not os.environ.get("MAIL_PASSWORD"):
        flash("La 2FA nécessite la configuration de l'email serveur. Contactez l'administrateur.", "error")
        return redirect(url_for("tenant.parametres") + "#securite")
    current_user.twofa_active = activer
    db.session.commit()
    log_action("UPDATE", "utilisateur", current_user.id,
               f"2FA {'activée' if activer else 'désactivée'}")
    db.session.commit()
    flash(f"Double authentification {'activée' if activer else 'désactivée'}.", "success")
    return redirect(url_for("tenant.parametres") + "#securite")


@bp.route("/dashboard")
@login_required
def dashboard():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t=get_tenant()
    if not t: flash("Aucune entreprise associée.","error"); return redirect(url_for("auth.login"))
    # Mode cabinet : un compte cabinet voit la liste de ses entreprises, pas un
    # dashboard d'entreprise unique. (Sauf s'il est "entré" dans une entreprise
    # via la bascule — étape 3 — auquel cas get_tenant() renverra l'entreprise.)
    if t.est_cabinet:
        return redirect(url_for("tenant.cabinet_dashboard"))
    now=datetime.now()

    # ── KPIs emploi (cache TTL=5min) ─────────────────────────────────────────
    _ck_kpis = f"{t.id}:kpis_emploi"
    kpis_cached = _cache_get(_ck_kpis)
    if kpis_cached:
        nb_actifs, nb_inactifs, nb_total, nb_journaliers, nb_new_mois = kpis_cached
    else:
        from sqlalchemy import func
        _sal_q = db.session.query(
            func.sum(db.cast(Salarie.statut == "ACTIF",   db.Integer)).label("actifs"),
            func.sum(db.cast(Salarie.statut == "INACTIF", db.Integer)).label("inactifs"),
            func.count().label("total"),
        ).filter(Salarie.tenant_id == t.id).one()
        nb_actifs   = int(_sal_q.actifs   or 0)
        nb_inactifs = int(_sal_q.inactifs or 0)
        nb_total    = int(_sal_q.total    or 0)
        nb_journaliers = Journalier.query.filter_by(tenant_id=t.id, statut="ACTIF").count()
        debut_mois  = datetime(now.year, now.month, 1).date()
        nb_new_mois = Salarie.query.filter(Salarie.tenant_id==t.id,
                                           Salarie.date_embauche>=debut_mois).count()
        _cache_set(_ck_kpis, (nb_actifs, nb_inactifs, nb_total, nb_journaliers, nb_new_mois),
                   TTL_KPIS_DASH)
    nb_total_employes = nb_actifs + nb_journaliers
    debut_mois  = datetime(now.year, now.month, 1).date()
    periode = PeriodePaie.query.filter_by(tenant_id=t.id, annee=now.year, mois=now.month).first()
    masse={}; nb_v=nb_b=nb_p=0
    if periode:
        buls = BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=periode.id).all()
        masse = calculer_masse_salariale(buls)
        nb_v = sum(1 for b in buls if b.statut=="VALIDÉ")
        nb_p = sum(1 for b in buls if b.statut=="PAYÉ")
        nb_b = sum(1 for b in buls if b.statut=="BROUILLON")
    _ck_evo = f"{t.id}:evolution_{now.year}_{now.month}"
    evolution = _cache_get(_ck_evo)
    if evolution is None:
        evolution = []
        mois_noms = ["","Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"]
        for i in range(5, -1, -1):
            m = now.month - i; y = now.year
            while m <= 0: m += 12; y -= 1
            p = PeriodePaie.query.filter_by(tenant_id=t.id, annee=y, mois=m).first()
            if p:
                buls_p = BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=p.id).all()
                total_net    = sum(float(b.net_a_payer    or 0) for b in buls_p)
                total_brut   = sum(float(b.salaire_brut   or 0) for b in buls_p)
                total_charges= sum(float(b.cnss_patronale or 0)+float(b.cnamgs_patronale or 0)
                                   +float(b.fnh or 0)+float(b.cfp or 0) for b in buls_p)
            else:
                total_net=total_brut=total_charges=0; buls_p=[]
            evolution.append({"mois":mois_noms[m],"annee":y,"brut":round(total_brut),
                              "net":round(total_net),"charges":round(total_charges),
                              "nb_bulletins":len(buls_p)})
        _cache_set(_ck_evo, evolution, TTL_EVOLUTION)
    top_salaries = []
    if periode:
        top_salaries = (BulletinPaie.query
            .filter_by(tenant_id=t.id, periode_id=periode.id)
            .options(joinedload(BulletinPaie.salarie))
            .order_by(BulletinPaie.net_a_payer.desc())
            .limit(5).all())
    from sqlalchemy import func
    _ck_cats = f"{t.id}:cats_stats"
    cats_stats = _cache_get(_ck_cats)
    if cats_stats is None:
        cats_stats = db.session.query(
            CategorieEmploi.code, CategorieEmploi.libelle,
            func.count(Salarie.id).label("nb")
        ).join(Salarie, Salarie.categorie_id==CategorieEmploi.id)\
         .filter(Salarie.tenant_id==t.id, Salarie.statut=="ACTIF")\
         .group_by(CategorieEmploi.code, CategorieEmploi.libelle).all()
        # Convertir en liste de tuples simples (JSON sérialisable)
        cats_stats = [(r.code, r.libelle, r.nb) for r in cats_stats]
        _cache_set(_ck_cats, cats_stats, TTL_CATS_STATS)
    derniers = (BulletinPaie.query
        .filter_by(tenant_id=t.id)
        .options(joinedload(BulletinPaie.salarie),
                 joinedload(BulletinPaie.periode))
        .order_by(BulletinPaie.date_creation.desc())
        .limit(6).all())
    # ══════════════════════════════════════════════════════════════════════════
    # ALERTES INTELLIGENTES
    # ══════════════════════════════════════════════════════════════════════════
    import calendar
    alertes = []
    MOIS_NOMS_LONG = ["","Janvier","Février","Mars","Avril","Mai","Juin",
                      "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
    mois_courant = MOIS_NOMS_LONG[now.month]
    debut_mois_d = date(now.year, now.month, 1)
    fin_mois_d   = date(now.year, now.month, calendar.monthrange(now.year, now.month)[1])

    # ── 1. Quota employés dépassé ou proche ──────────────────────────────────
    q_info = t.quota_employes_info
    if q_info.get("max"):
        pct = round(q_info["actuel"] / q_info["max"] * 100)
        if q_info["plein"]:
            alertes.append({"type":"danger","icone":"🚫","titre":"Limite d'employés atteinte",
                "msg":f"Vous avez atteint la limite de {q_info['max']} employés de votre plan {t.plan.nom}. "
                      f"Impossible d'ajouter de nouveaux travailleurs.",
                "lien":"/parametres","lien_texte":"Changer de plan"})
        elif pct >= 80:
            alertes.append({"type":"warning","icone":"⚠️","titre":"Quota employés bientôt atteint",
                "msg":f"{q_info['actuel']}/{q_info['max']} employés utilisés ({pct}%). "
                      f"Il reste {q_info['max'] - q_info['actuel']} place(s).",
                "lien":"/parametres","lien_texte":"Voir le plan"})

    # ── 2. Période non créée pour le mois courant ─────────────────────────────
    if not periode:
        alertes.append({"type":"warning","icone":"📅","titre":f"Période {mois_courant} {now.year} manquante",
            "msg":f"Aucune période de paie n'est ouverte pour {mois_courant} {now.year}. "
                  f"Les bulletins ne peuvent pas être saisis.",
            "lien":"/periodes","lien_texte":"Créer la période"})

    # ── 3. Période précédente non clôturée ────────────────────────────────────
    mois_prec = now.month - 1 or 12
    annee_prec = now.year if now.month > 1 else now.year - 1
    periode_prec = PeriodePaie.query.filter_by(
        tenant_id=t.id, annee=annee_prec, mois=mois_prec).first()
    if periode_prec and periode_prec.statut not in ("CLÔTURÉE", "CLOTUREE", "PAYÉE"):
        buls_prec = BulletinPaie.query.filter_by(
            tenant_id=t.id, periode_id=periode_prec.id).all()
        nb_non_clos = sum(1 for b in buls_prec if b.statut in ("BROUILLON", "VALIDÉ"))
        if nb_non_clos > 0:
            alertes.append({"type":"warning","icone":"🔓","titre":f"Période {MOIS_NOMS_LONG[mois_prec]} non clôturée",
                "msg":f"{nb_non_clos} bulletin(s) de {MOIS_NOMS_LONG[mois_prec]} {annee_prec} "
                      f"ne sont pas encore payés.",
                "lien":f"/bulletins?periode_id={periode_prec.id}","lien_texte":"Voir les bulletins"})

    # ── 4. Bulletins en brouillon ce mois ─────────────────────────────────────
    if nb_b > 0:
        alertes.append({"type":"warning","icone":"📝","titre":f"{nb_b} brouillon(s) à valider",
            "msg":f"{nb_b} bulletin(s) de {mois_courant} sont en brouillon et doivent être validés.",
            "lien":f"/bulletins?periode_id={periode.id}&statut=BROUILLON" if periode else "/bulletins",
            "lien_texte":"Valider maintenant"})

    # ── 5. Salariés actifs sans bulletin ce mois ──────────────────────────────
    if periode:
        ids_avec_bulletin = {b.salarie_id for b in
            BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=periode.id).all()}
        salaries_sans_bulletin = Salarie.query.filter_by(
            tenant_id=t.id, statut="ACTIF"
        ).filter(~Salarie.id.in_(ids_avec_bulletin)).all() if ids_avec_bulletin else             Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").all()
        nb_sans = len(salaries_sans_bulletin)
        if nb_sans > 0:
            exemples = ", ".join(s.nom_complet for s in salaries_sans_bulletin[:3])
            if nb_sans > 3: exemples += f" et {nb_sans-3} autre(s)"
            alertes.append({"type":"info","icone":"👤","titre":f"{nb_sans} salarié(s) sans bulletin ce mois",
                "msg":f"{exemples}.",
                "lien":f"/bulletins/saisie","lien_texte":"Saisir un bulletin"})

    # ── 6. Journaliers avec feuilles en attente de paiement ───────────────────
    feuilles_att = FeuillePaieJournalier.query.filter_by(
        tenant_id=t.id, statut="EN_ATTENTE").all()
    nb_feuilles_att = len(feuilles_att)
    if nb_feuilles_att > 0:
        montant_att = sum(float(f.montant_brut or 0) for f in feuilles_att)
        alertes.append({"type":"warning","icone":"🦺","titre":f"{nb_feuilles_att} feuille(s) journaliers non payée(s)",
            "msg":f"Total en attente : {int(montant_att):,} FCFA.",
            "lien":"/journaliers/paie","lien_texte":"Payer maintenant"})

    # ── 7. Journaliers actifs sans pointage cette semaine ─────────────────────
    lundi = (now.date() - timedelta(days=now.weekday()))
    samedi = lundi + timedelta(days=5)
    nb_jour_actifs = Journalier.query.filter_by(tenant_id=t.id, statut="ACTIF").count()
    if nb_jour_actifs > 0:
        ids_pointes_sem = {p.journalier_id for p in
            Pointage.query.filter_by(tenant_id=t.id)
            .filter(Pointage.journalier_id.isnot(None),
                    Pointage.date_pointage >= lundi,
                    Pointage.date_pointage <= samedi).all()}
        nb_non_pointes_jour = nb_jour_actifs - len(ids_pointes_sem)
        if nb_non_pointes_jour > 0 and now.weekday() >= 1:   # après lundi
            alertes.append({"type":"info","icone":"📋","titre":f"{nb_non_pointes_jour} journalier(s) non pointé(s) cette semaine",
                "msg":f"Semaine du {lundi.strftime('%d/%m')} : {nb_non_pointes_jour} journalier(s) "
                      f"sans pointage enregistré.",
                "lien":f"/pointage/recap-semaine","lien_texte":"Voir le récap"})

    # ── 8. Acomptes en attente de déduction ───────────────────────────────────
    acomptes_att = Acompte.query.filter_by(tenant_id=t.id, statut="EN_ATTENTE").count()
    if acomptes_att > 0:
        alertes.append({"type":"info","icone":"💸","titre":f"{acomptes_att} acompte(s) en attente",
            "msg":f"{acomptes_att} acompte(s) n'ont pas encore été déduits des bulletins.",
            "lien":"/acomptes","lien_texte":"Voir les acomptes"})

    # ── Alertes RH : essai, CDD, visite médicale, congés à poser ──────────────
    from datetime import timedelta as _td
    _auj = now.date(); _j15 = _auj + _td(days=15); _j30 = _auj + _td(days=30)

    _essais = (Contrat.query.filter_by(tenant_id=t.id, actif=True)
               .filter(Contrat.date_fin_essai.isnot(None),
                       Contrat.date_fin_essai >= _auj, Contrat.date_fin_essai <= _j15).count())
    if _essais:
        alertes.append({"type":"warning","icone":"⏳","titre":f"{_essais} période(s) d'essai à échéance",
            "msg":"Une ou plusieurs périodes d'essai se terminent sous 15 jours. Confirmez ou mettez fin au contrat avant l'échéance.",
            "lien":"/salaries","lien_texte":"Voir les salariés"})

    _cdd = (Contrat.query.filter_by(tenant_id=t.id, actif=True)
            .filter(Contrat.type_contrat.ilike("%CDD%"),
                    Contrat.date_fin.isnot(None),
                    Contrat.date_fin >= _auj, Contrat.date_fin <= _j15).count())
    if _cdd:
        alertes.append({"type":"warning","icone":"📄","titre":f"{_cdd} CDD arrive(nt) à échéance",
            "msg":"Un ou plusieurs contrats à durée déterminée se terminent sous 15 jours. Pensez au renouvellement ou à la fin de contrat.",
            "lien":"/salaries","lien_texte":"Voir les salariés"})

    _vm = (Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF")
           .filter(Salarie.date_prochaine_visite_medicale.isnot(None),
                   Salarie.date_prochaine_visite_medicale <= _j30).all())
    if _vm:
        _retard = sum(1 for s in _vm if s.date_prochaine_visite_medicale < _auj)
        _msg = (f"{_retard} visite(s) médicale(s) en retard, {len(_vm)} à programmer sous 30 jours."
                if _retard else f"{len(_vm)} salarié(s) ont une visite médicale à programmer sous 30 jours.")
        alertes.append({"type":"warning" if _retard else "info","icone":"🩺",
            "titre":"Visites médicales à programmer","msg":_msg,
            "lien":"/salaries","lien_texte":"Voir les salariés"})

    _soldes = (Conge.query.filter_by(tenant_id=t.id, annee=now.year)
               .filter(Conge.date_depart.is_(None)).all())
    _aposer = sum(1 for c in _soldes if (float(c.jours_acquis or 0) - float(c.jours_pris or 0)) >= 24)
    if _aposer:
        alertes.append({"type":"info","icone":"🏖️","titre":f"{_aposer} salarié(s) avec des congés à poser",
            "msg":"Certains salariés ont accumulé 24 jours ou plus de congés non pris. Pensez à planifier leurs congés.",
            "lien":"/conges","lien_texte":"Voir les congés"})

    # ── 9. Statut abonnement/essai — TOUJOURS affiché ───────────────────────
    exp_date = t.date_expiration
    jours_restants = (exp_date.date() - now.date()).days if exp_date else None

    if t.statut == "ESSAI":
        if jours_restants is None or jours_restants <= 0:
            alertes.append({"type":"danger","icone":"🚫",
                "titre":"Période d'essai expirée",
                "msg":"Votre essai gratuit a expiré. Souscrivez maintenant pour continuer.",
                "lien":"/parametres","lien_texte":"Souscrire maintenant"})
        elif jours_restants <= 7:
            alertes.append({"type":"danger","icone":"⏰",
                "titre":f"Essai : {jours_restants} jour(s) restant(s)",
                "msg":f"Expire le {exp_date.strftime('%d/%m/%Y')}. Souscrivez pour ne pas perdre vos données.",
                "lien":"/parametres","lien_texte":"Souscrire maintenant"})
        elif jours_restants <= 14:
            alertes.append({"type":"warning","icone":"⏳",
                "titre":f"Essai : {jours_restants} jour(s) restant(s)",
                "msg":f"Votre période d'essai se termine le {exp_date.strftime('%d/%m/%Y')}.",
                "lien":"/parametres","lien_texte":"Voir les plans"})
        else:
            alertes.append({"type":"info","icone":"🧪",
                "titre":f"Période d'essai — {jours_restants} jour(s) restant(s)",
                "msg":f"Essai gratuit jusqu'au {exp_date.strftime('%d/%m/%Y')}. Plan actuel : {t.plan.nom}.",
                "lien":"/parametres","lien_texte":"Voir les plans"})

    elif t.statut == "ACTIF":
        if jours_restants is None or jours_restants <= 0:
            alertes.append({"type":"danger","icone":"🔒",
                "titre":"Abonnement expiré",
                "msg":"Votre abonnement a expiré. Renouvelez pour continuer.",
                "lien":"/parametres","lien_texte":"Renouveler"})
        elif jours_restants <= 7:
            alertes.append({"type":"danger","icone":"⏰",
                "titre":f"Abonnement : {jours_restants} jour(s) restant(s)",
                "msg":f"Expire le {exp_date.strftime('%d/%m/%Y')}. Renouvelez dès maintenant.",
                "lien":"/parametres","lien_texte":"Renouveler"})
        elif jours_restants <= 30:
            alertes.append({"type":"warning","icone":"📅",
                "titre":f"Abonnement : {jours_restants} jour(s) restant(s)",
                "msg":f"Expire le {exp_date.strftime('%d/%m/%Y')}.",
                "lien":"/parametres","lien_texte":"Renouveler"})
        else:
            alertes.append({"type":"info","icone":"✅",
                "titre":f"Abonnement actif — {jours_restants} jour(s) restant(s)",
                "msg":f"Plan {t.plan.nom} valide jusqu'au {exp_date.strftime('%d/%m/%Y')}.",
                "lien":"/parametres","lien_texte":"Gérer l'abonnement"})

    elif t.statut == "PAIEMENT_EN_ATTENTE":
        alertes.append({"type":"warning","icone":"💳",
            "titre":"Paiement en attente",
            "msg":"Votre paiement est en cours de traitement. L'accès complet sera rétabli dès confirmation.",
            "lien":"/parametres","lien_texte":"Contacter le support"})

    # Trier par priorité : danger > warning > info
    _prio = {"danger": 0, "warning": 1, "info": 2}
    alertes.sort(key=lambda a: _prio.get(a["type"], 3))

    # Compteurs pour l'en-tête
    nb_alertes_critiques = sum(1 for a in alertes if a["type"] == "danger")
    nb_alertes_warning   = sum(1 for a in alertes if a["type"] == "warning")

    # ── Parcours d'accueil (nouveaux clients) ────────────────────────────────
    # On calcule où en est le client dans sa mise en route. L'encadré d'accueil
    # s'affiche tant que toutes les étapes ne sont pas franchies.
    a_convention = bool(t.convention and t.convention != "AUCUNE")
    a_cnss = bool(t.numero_cnss)
    nb_salaries_total = Salarie.query.filter_by(tenant_id=t.id).count()
    a_salarie = nb_salaries_total > 0
    a_periode = PeriodePaie.query.filter_by(tenant_id=t.id).count() > 0
    a_bulletin = BulletinPaie.query.filter_by(tenant_id=t.id).count() > 0
    etapes_accueil = [
        {"cle": "entreprise", "titre": "Configurer votre entreprise",
         "desc": "Convention collective, CNSS, informations légales",
         "faite": (a_convention and a_cnss), "lien": "/parametres", "cta": "Configurer"},
        {"cle": "salarie", "titre": "Ajouter votre premier salarié",
         "desc": "Créez la fiche d'un de vos employés",
         "faite": a_salarie, "lien": "/salaries/nouveau", "cta": "Ajouter un salarié"},
        {"cle": "periode", "titre": "Ouvrir une période de paie",
         "desc": "Le mois pour lequel vous préparez les bulletins",
         "faite": a_periode, "lien": "/periodes", "cta": "Ouvrir une période"},
        {"cle": "bulletin", "titre": "Créer votre premier bulletin",
         "desc": "Générez le bulletin de paie d'un salarié",
         "faite": a_bulletin, "lien": "/bulletins/saisie", "cta": "Créer un bulletin"},
    ]
    nb_faites = sum(1 for e in etapes_accueil if e["faite"])
    accueil_termine = nb_faites == len(etapes_accueil)
    # Prochaine étape non faite (pour le bouton principal)
    prochaine_etape = next((e for e in etapes_accueil if not e["faite"]), None)

    return render_template("tenant/dashboard.html", tenant=t,
        nb_actifs=nb_actifs, nb_inactifs=nb_inactifs, nb_total=nb_total,
        nb_journaliers=nb_journaliers, nb_total_employes=nb_total_employes,
        nb_new_mois=nb_new_mois, periode=periode, masse=masse,
        nb_valides=nb_v, nb_payes=nb_p, nb_brouillon=nb_b,
        evolution=evolution, top_salaries=top_salaries,
        cats_stats=cats_stats, derniers=derniers, alertes=alertes,
        nb_alertes_critiques=nb_alertes_critiques,
        nb_alertes_warning=nb_alertes_warning, now=now,
        etapes_accueil=etapes_accueil, nb_faites=nb_faites,
        accueil_termine=accueil_termine, prochaine_etape=prochaine_etape)

# ── Salariés ──────────────────────────────────────────────────────────────────
@bp.route("/simulateur")
@login_required
def simulateur_paie():
    """Simulateur de paie interactif — avec comparaison scénarios, net→brut, augmentation."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    salaries_list = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF")\
        .options(joinedload(Salarie.categorie)).order_by(Salarie.nom).all()
    return render_template("tenant/simulateur.html", tenant=t, salaries=salaries_list,
                           convention=t.convention or "AUCUNE")


@bp.route("/api/simuler-paie/scenarios", methods=["POST"])
@login_required
def api_simuler_scenarios():
    """Compare jusqu'à 3 scénarios de paie côte à côte."""
    if current_user.is_super_admin: return jsonify({"error": "forbidden"}), 403
    t = get_tenant()
    if not t: return jsonify({"error": "non authentifié"}), 401

    data = request.get_json(force=True) or {}
    scenarios = data.get("scenarios", [])
    nb_parts  = float(data.get("nb_parts", 1.0))

    if not scenarios or len(scenarios) < 2:
        return jsonify({"error": "Au moins 2 scénarios requis"}), 400

    from simulation_paie import comparer_scenarios
    result = comparer_scenarios(scenarios, nb_parts=nb_parts)
    return jsonify(result)


@bp.route("/api/simuler-paie/net-vers-brut", methods=["POST"])
@login_required
def api_simuler_net_vers_brut():
    """Calcule le brut nécessaire pour atteindre un net cible."""
    if current_user.is_super_admin: return jsonify({"error": "forbidden"}), 403
    t = get_tenant()
    if not t: return jsonify({"error": "non authentifié"}), 401

    data = request.get_json(force=True) or {}
    net_cible = float(data.get("net_cible", 0))
    nb_parts  = float(data.get("nb_parts",  1.0))
    extras    = data.get("extras", {})

    if not net_cible or net_cible <= 0:
        return jsonify({"error": "Net cible invalide"}), 400

    from simulation_paie import simuler_depuis_net
    result = simuler_depuis_net(net_cible, nb_parts=nb_parts, donnees_extra=extras)
    return jsonify(result)


@bp.route("/api/simuler-paie/augmentation", methods=["POST"])
@login_required
def api_simuler_augmentation():
    """Simule l'impact d'une augmentation de salaire."""
    if current_user.is_super_admin: return jsonify({"error": "forbidden"}), 403
    t = get_tenant()
    if not t: return jsonify({"error": "non authentifié"}), 401

    data = request.get_json(force=True) or {}
    salaire_actuel      = float(data.get("salaire_actuel", 0))
    augmentation_pct    = data.get("augmentation_pct")
    augmentation_montant= data.get("augmentation_montant")
    nb_parts            = float(data.get("nb_parts", 1.0))
    extras              = data.get("extras", {})

    if not salaire_actuel or salaire_actuel <= 0:
        return jsonify({"error": "Salaire actuel invalide"}), 400

    from simulation_paie import simuler_augmentation
    result = simuler_augmentation(
        salaire_actuel       = salaire_actuel,
        augmentation_pct     = float(augmentation_pct) if augmentation_pct else None,
        augmentation_montant = float(augmentation_montant) if augmentation_montant else None,
        nb_parts             = nb_parts,
        donnees_extra        = extras,
    )
    return jsonify(result)


@bp.route("/api/simuler-paie", methods=["POST"])
@login_required
def api_simuler_paie():
    """API JSON : simuler un bulletin de paie complet sans le sauvegarder."""
    t = get_tenant()
    if not t: return jsonify({"erreur": "non connecté"})
    from calculs_paie import calculer_bulletin, calculer_heures_sup_btp
    try:
        d = request.get_json(force=True) or {}
        # Accepter aussi le form-data
        if not d:
            d = {k: request.form.get(k) for k in request.form}

        def flt(key, default=0):
            try: return float(str(d.get(key) or default).replace(",",".") or default)
            except: return float(default)

        # Conversion des heures supplémentaires (saisies en HEURES) → montants.
        # Effectuée quelle que soit la convention : les coefficients +10/+30/+40/+70
        # sont communs au BTP et au Commerce (la sélection de convention côté UI
        # ne change que les libellés et le préremplissage structurel BTP).
        sal_base = flt("salaire_base")
        if sal_base > 0:
            from calculs_paie import calculer_heures_sup_btp
            hs = calculer_heures_sup_btp(sal_base,
                h10=flt("h10"), h30=flt("h30"),
                h40=flt("h40"), h70=flt("h70"),
                h30b=flt("h30b"), convention=t.convention)
            d["heures_sup_10"] = hs["montant_10"]
            d["heures_sup_30"] = hs["montant_30"]
            d["heures_sup_30b"] = hs["montant_30b"]
            d["heures_sup_40"] = hs["montant_40"]
            d["heures_sup_70"] = hs["montant_70"]
        d["convention"] = t.convention

        # Nombre de parts IRPP
        sal_id = d.get("salarie_id")
        nb_parts = 1.0
        if sal_id:
            s = Salarie.query.filter_by(id=int(sal_id), tenant_id=t.id).first()
            if s: nb_parts = float(s.nombre_parts or 1)
        nb_parts = flt("nb_parts") or nb_parts

        # Composants personnalisés (aperçu temps réel) : lus depuis composant_<id>
        comps_live = []
        for comp in ComposantPaie.query.filter_by(tenant_id=t.id, actif=True).all():
            montant = flt(f"composant_{comp.id}")
            if montant:
                comps_live.append({
                    "libelle": comp.libelle, "sens": comp.sens, "montant": montant,
                    "soumis_cnss": comp.soumis_cnss, "soumis_cnamgs": comp.soumis_cnamgs,
                    "soumis_irpp": comp.soumis_irpp,
                    "entre_dans_brut": comp.entre_dans_brut, "position": comp.position})
        d["composants"] = comps_live
        d["config_rubriques"] = _config_rubriques_dict(t.id)

        result = calculer_bulletin(d, nb_parts=nb_parts)

        # Enrichir avec détails BTP
        from calculs_paie import calculer_taux_horaire, H_NORMALES_MENSUEL
        if sal_base > 0:
            th = calculer_taux_horaire(sal_base)
            result["taux_horaire"]     = round(th, 2)
            result["h_normales_mensuel"] = H_NORMALES_MENSUEL

        # Ajouter libellés pour l'affichage
        result["gains_detail"] = [
            {"label": "Salaire de base",          "montant": result["salaire_base"]},
            {"label": "H.sup +10%",               "montant": result["heures_sup_10"]},
            {"label": "H.sup +30%",               "montant": result["heures_sup_30"]},
            {"label": "H.sup +30% (repos/férié)", "montant": result.get("heures_sup_30b", 0)},
            {"label": "H.sup +40% (nuit/dim.)",   "montant": result["heures_sup_40"]},
            {"label": "H.sup +70% (fériés)",      "montant": result["heures_sup_70"]},
            {"label": "Sursalaire",               "montant": result["sursalaire"]},
            {"label": "Prime de transport",       "montant": result["prime_transport"]},
            {"label": "Prime de responsabilité",  "montant": result["prime_responsabilite"]},
            {"label": "Indemnité logement",       "montant": result["indem_logement"]},
            {"label": "Prime d'ancienneté",      "montant": result["prime_anciennete"]},
            {"label": "Autres primes",            "montant": sum([
                result["prime_caisse"], result["carburant"],
                result["prime_rendement"], result["prime_qualite"],
                result["prime_performance"], result["prime_assiduité"],
            ])},
        ]
        result["gains_detail"] = [g for g in result["gains_detail"] if g["montant"] > 0]

        result["retenues_detail"] = [
            {"label": f"CNSS salarié (5% / base {int(result['base_cnss']):,} FCFA)", "montant": result["cnss_salarie"]},
            {"label": f"CNAMGS salarié (2% / base {int(result['base_cnamgs']):,} FCFA)", "montant": result["cnamgs_salarie"]},
            {"label": f"TCS (5% / base {int(result['base_tcs']):,} FCFA)",             "montant": result["tcs"]},
            {"label": f"IRPP ({nb_parts} part(s))",                                    "montant": result["irpp"]},
            {"label": "Acompte",                                                        "montant": result["acompte"]},
            {"label": "Retenue absences",                                               "montant": result["absences"]},
        ]
        result["retenues_detail"] = [r for r in result["retenues_detail"] if r["montant"] > 0]

        result["charges_pat_detail"] = [
            {"label": "CNSS patronal (18%)",     "montant": result["cnss_patronale"]},
            {"label": "CNAMGS patronal (4.1%)",  "montant": result["cnamgs_patronale"]},
            {"label": "FNH (3%)",                "montant": result["fnh"]},
            {"label": "CFP (0.5%)",              "montant": result["cfp"]},
        ]

        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"erreur": str(e), "trace": traceback.format_exc()[-300:]})

# ══════════════════════════════════════════════════════════════════════════════
# GESTION DES CONTRATS SALARIÉS
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/bulletins")
@login_required
def bulletins():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t=get_tenant()
    if not t: return redirect(url_for("auth.login"))
    pid          = request.args.get("periode_id", type=int)
    sf           = request.args.get("statut", "")
    site_filtre_id = request.args.get("site_id", type=int)
    periodes     = PeriodePaie.query.filter_by(tenant_id=t.id)                    .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()).all()
    sites_list   = Site.query.filter_by(tenant_id=t.id, actif=True).order_by(Site.nom).all()
    site_filtre  = Site.query.filter_by(id=site_filtre_id, tenant_id=t.id).first() if site_filtre_id else None
    ps = None; buls = []; masse = {}; pagination = None

    if pid:
        ps = PeriodePaie.query.filter_by(id=pid, tenant_id=t.id).first_or_404()
        q = BulletinPaie.query.options(
            joinedload(BulletinPaie.salarie),
            joinedload(BulletinPaie.periode),
        ).filter_by(tenant_id=t.id, periode_id=pid)
        if sf:
            q = q.filter_by(statut=sf)

        # ── Filtre par site ──────────────────────────────────────────────────
        if site_filtre_id:
            # Récupérer les IDs des salariés affectés à ce site
            ids_sal = [a.salarie_id for a in AffectationSite.query.filter_by(
                tenant_id=t.id, site_id=site_filtre_id, actif=True
            ).filter(AffectationSite.salarie_id.isnot(None)).all()]
            q = q.filter(BulletinPaie.salarie_id.in_(ids_sal))

        page_bul   = request.args.get("page", 1, type=int)
        # Une seule query base avec le join, puis on pagine
        q_joined   = q.join(Salarie).order_by(Salarie.nom)
        buls_tous  = q_joined.all()
        masse      = calculer_masse_salariale(buls_tous)
        pagination = q_joined.paginate(page=page_bul, per_page=25, error_out=False)
        buls       = pagination.items

    # Affectation site de chaque salarié pour affichage dans le tableau
    aff_sal = {a.salarie_id: a.site for a in AffectationSite.query.filter_by(
        tenant_id=t.id, actif=True
    ).filter(AffectationSite.salarie_id.isnot(None)).all()}

    _args = {k: v for k, v in request.args.items() if k != 'page'}
    _base = request.path + '?' + '&'.join(f'{k}={v}' for k, v in _args.items())
    _sep  = '&' if _args else '?'
    return render_template("tenant/bulletins.html",
        periodes=periodes, periode_sel=ps,
        bulletins=buls, masse=masse, statut_filtre=sf,
        sites=sites_list, site_filtre=site_filtre, aff_sal=aff_sal,
        pagination=pagination if pid else None,
        pagination_base=_base + _sep,
        tenant=t)

@bp.route("/bulletins/bordereau")
@login_required
def bulletins_bordereau():
    """Bordereau de paie imprimable d'une période : liste des salariés, net à
    payer, mode de paiement, avec sous-totaux Espèces / Virement.
    """
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    pid = request.args.get("periode_id", type=int)
    if not pid:
        flash("Choisissez une période pour générer le bordereau.", "error")
        return redirect(url_for("tenant.bulletins"))
    ps = PeriodePaie.query.filter_by(id=pid, tenant_id=t.id).first_or_404()

    statut_f = request.args.get("statut", "")
    q = BulletinPaie.query.options(joinedload(BulletinPaie.salarie)) \
        .filter_by(tenant_id=t.id, periode_id=pid)
    if statut_f:
        q = q.filter_by(statut=statut_f)
    bulletins = q.join(Salarie).order_by(Salarie.nom).all()

    lignes = []
    recap_mode = {"ESPECES": {"total": 0.0, "nb": 0}, "VIREMENT": {"total": 0.0, "nb": 0}}
    total_general = 0.0
    for b in bulletins:
        net = float(b.net_a_payer or 0)
        mode = (b.mode_paiement if b.statut == "PAYÉ" and b.mode_paiement
                else (b.salarie.mode_paiement if b.salarie else "ESPECES")) or "ESPECES"
        if mode not in ("ESPECES", "VIREMENT"):
            mode = "ESPECES"
        recap_mode[mode]["total"] += net
        recap_mode[mode]["nb"]    += 1
        total_general += net
        lignes.append({
            "matricule": b.salarie.matricule if b.salarie else "—",
            "nom": b.salarie.nom_complet if b.salarie else "—",
            "emploi": (b.salarie.emploi if b.salarie else "") or "—",
            "net": net, "mode": mode, "statut": b.statut,
        })

    return render_template("tenant/bulletins_bordereau_print.html",
        tenant=t, periode=ps, lignes=lignes, recap_mode=recap_mode,
        total_general=total_general, nb_total=len(lignes),
        statut=statut_f, now=datetime.now())

@bp.route("/bulletins/generer-lot", methods=["POST"])
@login_required
def bulletins_generer_lot():
    """Génère un bulletin BROUILLON pour chaque salarié actif d'une période.

    Principes de sûreté :
      - ne crée QUE des brouillons (jamais de bulletin validé) ;
      - ne touche JAMAIS un bulletin existant (salarié ignoré, pas écrasé) ;
      - refuse une période clôturée ;
      - ignore les salariés sans contrat actif ou embauchés après la période.
    Le salaire de base vient du contrat actif ; la prime d'ancienneté et les
    acomptes en attente sont appliqués automatiquement. L'utilisateur ajuste
    ensuite les cas particuliers avant de valider.
    """
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)

    pid = request.form.get("periode_id", type=int)
    if not pid:
        flash("Choisissez une période avant de générer les bulletins.", "error")
        return redirect(url_for("tenant.bulletins"))
    periode = PeriodePaie.query.filter_by(id=pid, tenant_id=t.id).first_or_404()
    retour = f"/bulletins?periode_id={pid}"

    if periode.statut not in ("OUVERT", "OUVERTE"):
        flash(f"La période {periode.libelle_mois} {periode.annee} est "
              f"{periode.statut.lower()} : aucun bulletin ne peut y être généré.", "error")
        return redirect(retour)

    import calendar as _cal
    fin_periode = date(periode.annee, periode.mois,
                       _cal.monthrange(periode.annee, periode.mois)[1])

    # Salariés déjà dotés d'un bulletin sur cette période : intouchables.
    deja = {b.salarie_id for b in
            BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=pid).all()}

    salaries = (Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF")
                .order_by(Salarie.nom, Salarie.prenom).all())

    crees = 0
    ignores_existants = ignores_sans_contrat = ignores_non_embauches = 0
    noms_sans_contrat = []

    for s in salaries:
        if s.id in deja:
            ignores_existants += 1
            continue
        if s.date_embauche and s.date_embauche > fin_periode:
            ignores_non_embauches += 1
            continue
        contrat = (Contrat.query
                   .filter_by(salarie_id=s.id, tenant_id=t.id, actif=True)
                   .order_by(Contrat.date_debut.desc()).first())
        if not contrat or not contrat.salaire_base:
            ignores_sans_contrat += 1
            if len(noms_sans_contrat) < 8:
                noms_sans_contrat.append(s.nom_complet)
            continue

        donnees = {"salaire_base": float(contrat.salaire_base)}
        if getattr(s, "travail_de_nuit", False):
            donnees["travail_de_nuit"] = True
        if s.date_embauche:
            donnees["anciennete_annees"] = max(
                0, (fin_periode - s.date_embauche).days // 365)

        # Acomptes en attente du mois : déduits comme dans la saisie unitaire.
        total_ac = float(db.session.query(db.func.sum(Acompte.montant))
                         .filter_by(tenant_id=t.id, salarie_id=s.id,
                                    mois=periode.mois, annee=periode.annee,
                                    statut="EN_ATTENTE").scalar() or 0)
        if total_ac > 0:
            donnees["acompte"] = total_ac

        res = calculer_bulletin(dict(donnees, convention=t.convention),
                                nb_parts=float(s.nombre_parts or 1))
        b = BulletinPaie(tenant_id=t.id, salarie_id=s.id, periode_id=pid)
        for k, v in res.items():
            if not k.startswith("_") and hasattr(b, k):
                setattr(b, k, v)
        b.statut = "BROUILLON"
        b.mode_paiement = s.mode_paiement or "ESPECES"
        db.session.add(b)
        crees += 1

    db.session.commit()
    log_action("GENERATE_BATCH", "bulletin", pid,
               f"Génération en lot {periode.libelle_mois} {periode.annee} : "
               f"{crees} brouillon(s) créé(s)")

    if crees:
        flash(f"{crees} bulletin(s) brouillon créé(s) pour "
              f"{periode.libelle_mois} {periode.annee}. Vérifiez-les avant validation.",
              "success")
    else:
        flash("Aucun bulletin créé : tous les salariés actifs en ont déjà un "
              "sur cette période, ou aucun n'a de contrat actif.", "warning")

    details = []
    if ignores_existants:
        details.append(f"{ignores_existants} salarié(s) avaient déjà un bulletin (inchangés)")
    if ignores_non_embauches:
        details.append(f"{ignores_non_embauches} embauché(s) après la période")
    if ignores_sans_contrat:
        noms = ", ".join(noms_sans_contrat)
        suite = "…" if ignores_sans_contrat > len(noms_sans_contrat) else ""
        details.append(f"{ignores_sans_contrat} sans contrat actif ({noms}{suite})")
    if details:
        flash(" · ".join(details), "info")

    return redirect(retour)


@bp.route("/bulletins/valider-lot", methods=["POST"])
@login_required
def bulletins_valider_lot():
    """Valider une sélection de bulletins ou tous les brouillons d'une période."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.can_edit: abort(403)

    pid      = request.form.get("periode_id", type=int)
    site_id  = request.form.get("site_id",    type=int)
    action   = request.form.get("action_lot", "valider")
    ids_str  = request.form.get("bulletin_ids", "")
    ids_sel  = [int(x) for x in ids_str.split(",") if x.strip().isdigit()]

    if not pid:
        flash("Période manquante.", "error")
        return redirect(url_for("tenant.bulletins"))

    # ── CORRECTION BUG : si aucun ID sélectionné + action valider → refuser ──
    # Avant : ids_sel vide → validait TOUS les bulletins de la période
    # Maintenant : ids_sel vide → uniquement pour les actions "tout valider" explicites
    if not ids_sel and action == "valider":
        # Vérifier que c'est bien une demande "tout valider" (bouton dédié)
        tout_valider = request.form.get("tout_valider", "0")
        if tout_valider != "1":
            flash("Aucun bulletin sélectionné. Cochez des bulletins ou utilisez 'Tout valider'.", "warning")
            return redirect(f"/bulletins?periode_id={pid}" + (f"&site_id={site_id}" if site_id else ""))

    # Charger les bulletins ciblés
    if ids_sel:
        buls = BulletinPaie.query.filter(
            BulletinPaie.id.in_(ids_sel),
            BulletinPaie.tenant_id == t.id
        ).all()
    else:
        q = BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=pid, statut="BROUILLON")
        if site_id:
            ids_sal = [a.salarie_id for a in AffectationSite.query.filter_by(
                tenant_id=t.id, site_id=site_id, actif=True
            ).filter(AffectationSite.salarie_id.isnot(None)).all()]
            q = q.filter(BulletinPaie.salarie_id.in_(ids_sal))
        buls = q.all()

    nb = 0
    if action == "valider":
        for b in buls:
            if b.statut != "BROUILLON": continue
            b.statut          = "VALIDÉ"
            b.date_validation = utcnow()
            attribuer_numero_bulletin(b)
            for a in Acompte.query.filter_by(
                tenant_id=t.id, salarie_id=b.salarie_id,
                mois=b.periode.mois, annee=b.periode.annee, statut="EN_ATTENTE").all():
                a.statut = "DEDUIT"
            nb += 1
        msg = f"✅ {nb} bulletin(s) validé(s)."
        log_action("VALIDATE", "bulletin", pid,
                   f"Validation de {nb} bulletin(s) — période {pid}")

    elif action == "annuler_validation":
        # ── NOUVEAU : annuler la validation → repasser en BROUILLON ──────────
        for b in buls:
            if b.statut not in ("VALIDÉ", "VALIDE"): continue
            b.statut          = "BROUILLON"
            b.date_validation = None
            # Remettre les acomptes déduits en attente
            for a in Acompte.query.filter_by(
                tenant_id=t.id, salarie_id=b.salarie_id,
                mois=b.periode.mois, annee=b.periode.annee, statut="DEDUIT").all():
                a.statut = "EN_ATTENTE"
            nb += 1
        msg = f"↩️ {nb} bulletin(s) remis en brouillon."
        log_action("CANCEL", "bulletin", pid,
                   f"Annulation validation de {nb} bulletin(s) — période {pid}")

    elif action == "payer":
        for b in buls:
            if b.statut not in ("VALIDÉ", "VALIDE", "BROUILLON"): continue
            b.statut = "PAYÉ"
            nb += 1
        msg = f"💰 {nb} bulletin(s) marqué(s) comme payé(s)."
        log_action("PAY", "bulletin", pid,
                   f"{nb} bulletin(s) marqué(s) payé(s) — période {pid}")

    elif action == "supprimer_brouillons":
        for b in buls:
            if b.statut != "BROUILLON": continue
            db.session.delete(b); nb += 1
        msg = f"🗑️ {nb} brouillon(s) supprimé(s)."
        log_action("DELETE", "bulletin", pid,
                   f"Suppression de {nb} brouillon(s) — période {pid}")

    else:
        flash("Action inconnue.", "error")
        return redirect(f"/bulletins?periode_id={pid}")

    db.session.commit()
    _cache_delete(f"{t.id}:")
    flash(msg, "success")
    redir = f"/bulletins?periode_id={pid}"
    if site_id: redir += f"&site_id={site_id}"
    return redirect(redir)

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


@bp.route("/bulletins/saisie", methods=["GET","POST"])
@login_required
def bulletin_saisie():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t=get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.can_edit: abort(403)
    sals=Salarie.query.filter_by(tenant_id=t.id,statut="ACTIF").order_by(Salarie.nom).all()
    pers=PeriodePaie.query.filter_by(tenant_id=t.id,statut="OUVERT").order_by(PeriodePaie.annee.desc(),PeriodePaie.mois.desc()).all()
    if request.method=="POST":
        sid=int(request.form["salarie_id"]); pid=int(request.form["periode_id"])
        s=Salarie.query.filter_by(id=sid,tenant_id=t.id).first_or_404()
        periode = PeriodePaie.query.filter_by(id=pid, tenant_id=t.id).first_or_404()
        acomptes_en_attente = Acompte.query.filter_by(
            tenant_id=t.id, salarie_id=sid,
            mois=periode.mois, annee=periode.annee, statut="EN_ATTENTE").all()
        total_acomptes = sum(float(a.montant) for a in acomptes_en_attente)
        donnees={}
        for k,v in request.form.items():
            # Exclure les champs non-numériques et les champs base_/taux_ (traités séparément)
            if k in ("salarie_id","periode_id","csrf_token","action","nb_jours_travailles"):
                continue
            if k.startswith("base_") or k.startswith("taux_") or k.startswith("composant_"):
                continue
            try:
                donnees[k] = float(v) if v else 0
            except (ValueError, TypeError):
                donnees[k] = 0
        if total_acomptes > 0:
            donnees["acompte"] = max(donnees.get("acompte", 0), total_acomptes)

        # ── Composants personnalisés du tenant (gains/retenues) ────────────────
        # Lecture des montants saisis (champ composant_<id>) ; on construit la
        # liste passée au calcul et on en garde une copie pour la persistance.
        composants_actifs = ComposantPaie.query.filter_by(tenant_id=t.id, actif=True).all()
        composants_saisis = []
        for comp in composants_actifs:
            montant = request.form.get(f"composant_{comp.id}", type=float) or 0
            if montant:
                composants_saisis.append({
                    "composant_id": comp.id, "libelle": comp.libelle, "sens": comp.sens,
                    "montant": montant, "soumis_cnss": comp.soumis_cnss,
                    "soumis_cnamgs": comp.soumis_cnamgs, "soumis_irpp": comp.soumis_irpp,
                    "entre_dans_brut": comp.entre_dans_brut, "position": comp.position,
                    "base": request.form.get(f"base_composant_{comp.id}", type=float),
                    "taux": request.form.get(f"taux_composant_{comp.id}", type=float),
                })
        donnees["composants"] = composants_saisis

        # ── Prime d'ancienneté automatique (selon convention collective) ───────
        # L'ancienneté est calculée à la fin de la période de paie. Le calcul
        # effectif se fait dans calculer_bulletin() et ne s'applique QUE si
        # aucune prime n'a été saisie manuellement (l'override reste prioritaire).
        if s.date_embauche:
            import calendar as _cal2
            _fin_periode = date(periode.annee, periode.mois,
                                _cal2.monthrange(periode.annee, periode.mois)[1])
            _anc = max(0, (_fin_periode - s.date_embauche).days // 365)
            donnees["anciennete_annees"] = _anc
            if getattr(s, "travail_de_nuit", False) and not donnees.get("travail_de_nuit"):
                donnees["travail_de_nuit"] = True
            if _anc >= 2 and not donnees.get("prime_anciennete"):
                from calculs_paie import prime_anciennete as _calc_pa
                _pa = _calc_pa(t.convention, float(donnees.get("salaire_base") or 0), _anc)
                if _pa > 0:
                    flash(
                        f"Prime d'ancienneté calculée automatiquement : "
                        f"{int(_pa):,} FCFA ({_anc} ans d'ancienneté, "
                        f"convention {t.convention}). Vous pouvez la modifier "
                        f"manuellement.".replace(",", " "),
                        "info")

        # ── L6 : Allocation de congé (Code du travail 2021, Art. 225) ──────────
        # Calcul automatique si un congé ANNUEL débute dans le mois de la période
        # et que l'utilisateur n'a pas saisi de valeur manuelle (l'override
        # manuel reste prioritaire). Versée avant le départ en congé (Art. 225).
        if not donnees.get("allocations_conge"):
            import calendar as _cal
            _debut_mois = date(periode.annee, periode.mois, 1)
            _fin_mois   = date(periode.annee, periode.mois,
                               _cal.monthrange(periode.annee, periode.mois)[1])
            jours_conge = sum(
                float(c.jours_pris or 0)
                for c in s.conges
                if (c.type_conge in ("ANNUEL", None))
                   and c.statut in ("APPROUVÉ", "APPROUVE", "PRIS")
                   and c.date_depart and _debut_mois <= c.date_depart <= _fin_mois
            )
            if jours_conge > 0:
                from conges_avance import allocation_conge
                bulletins_12 = (BulletinPaie.query
                    .filter_by(tenant_id=t.id, salarie_id=sid)
                    .filter(BulletinPaie.statut.in_(["VALIDÉ", "VALIDE", "PAYÉ"]),
                            BulletinPaie.periode_id != pid)
                    .order_by(BulletinPaie.date_creation.desc()).limit(12).all())
                alloc = allocation_conge(bulletins_12, jours_conge)
                if alloc > 0:
                    donnees["allocations_conge"] = alloc
                    flash(
                        f"Allocation de congé calculée automatiquement pour "
                        f"{jours_conge:.0f} jour(s) pris : {int(alloc):,} FCFA "
                        f"(moyenne des 12 derniers mois, Art. 225). "
                        f"Vous pouvez la modifier manuellement."
                        .replace(",", " "),
                        "info")

        donnees["config_rubriques"] = _config_rubriques_dict(t.id)
        res=calculer_bulletin(dict(donnees, convention=t.convention),nb_parts=float(s.nombre_parts or 1))
        ex=BulletinPaie.query.filter_by(tenant_id=t.id,salarie_id=sid,periode_id=pid).first()
        # 🔒 Immuabilité : un bulletin validé est un document de paie officiel.
        # Il ne peut pas être réécrit en silence — il faut d'abord annuler sa
        # validation (action tracée), sinon l'historique de paie serait modifiable.
        if ex and ex.statut in ("VALIDÉ", "VALIDE"):
            log_action("BLOCK_EDIT", "bulletin", ex.id,
                       "Tentative de modification d'un bulletin validé (bloquée)")
            flash("Ce bulletin est validé : il ne peut pas être modifié. "
                  "Annulez d'abord sa validation depuis la liste des bulletins, "
                  "puis ressaisissez-le.", "error")
            return redirect(url_for("tenant.bulletin_detail", id=ex.id))
        b=ex or BulletinPaie(tenant_id=t.id,salarie_id=sid,periode_id=pid)
        if not ex: db.session.add(b)
        for k,v in res.items():
            if not k.startswith("_") and hasattr(b,k): setattr(b,k,v)
        b.nb_jours_travailles=int(request.form.get("nb_jours_travailles") or 0)
        # ✅ Sauvegarder base et taux saisis manuellement pour chaque rubrique
        RUBRIQUES_BT = ["salaire_base","heures_sup_10","heures_sup_30","heures_sup_30b","heures_sup_40","heures_sup_70",
            "heures_sup_fj","heures_sup_fn",
            "absences","sursalaire","prime_caisse","carburant","prime_anciennete",
            "indem_logement","indem_domesticite","indem_eau_electricite","indem_nourriture",
            "prime_transport","prime_responsabilite","prime_rendement","prime_assiduité",
            "prime_qualite","prime_performance","allocations_conge",
            "indem_compensatrice_conge","indem_services_rendus",
            "indem_compensatrice_preavis","indem_licenciement"]
        for r in RUBRIQUES_BT:
            base_val = request.form.get(f"base_{r}", "")
            taux_val = request.form.get(f"taux_{r}", "")
            if hasattr(b, f"base_{r}"):
                try: setattr(b, f"base_{r}", float(base_val) if base_val else None)
                except: pass
            if hasattr(b, f"taux_{r}"):
                setattr(b, f"taux_{r}", taux_val.strip()[:20] if taux_val else "")
        action=request.form.get("action","brouillon")
        if action=="valider":
            b.statut="VALIDÉ"; b.date_validation=utcnow()
            attribuer_numero_bulletin(b)
            for a in acomptes_en_attente: a.statut = "DEDUIT"
        else:
            b.statut="BROUILLON"
        # Persistance des composants personnalisés (instantané par bulletin)
        db.session.flush()  # garantit b.id pour un nouveau bulletin
        BulletinComposant.query.filter_by(bulletin_id=b.id).delete()
        for cs in composants_saisis:
            db.session.add(BulletinComposant(
                bulletin_id=b.id, composant_id=cs["composant_id"],
                libelle=cs["libelle"], sens=cs["sens"], montant=cs["montant"],
                position=cs.get("position") or "BAS",
                base=cs.get("base"), taux=cs.get("taux"),
                soumis_cnss=cs["soumis_cnss"], soumis_cnamgs=cs["soumis_cnamgs"],
                soumis_irpp=cs["soumis_irpp"]))
        db.session.commit()
        if total_acomptes > 0:
            flash(f"Bulletin sauvegardé. Acompte de {int(total_acomptes):,} FCFA déduit automatiquement.".replace(",", " "), "success")
        else:
            flash(f"Bulletin {'validé' if b.statut=='VALIDÉ' else 'sauvegardé'}.","success")
        return redirect(url_for("tenant.bulletin_detail",id=b.id))
    sid=request.args.get("salarie_id",type=int)
    # ── Mode édition : ?id=<bulletin> pré-remplit le formulaire ──────────────
    bulletin_edit=None; valeurs_edit={}
    edit_id=request.args.get("id",type=int) or request.args.get("bulletin_id",type=int)
    if edit_id:
        be=BulletinPaie.query.filter_by(id=edit_id,tenant_id=t.id).first_or_404()
        if be.statut in ("VALIDÉ","VALIDE","PAYÉ","PAYE"):
            flash("Ce bulletin est validé ou payé : annulez d'abord sa validation "
                  "depuis la liste des bulletins pour pouvoir le modifier.","error")
            return redirect(url_for("tenant.bulletin_detail",id=be.id))
        bulletin_edit=be; sid=be.salarie_id
        _exclus={"id","tenant_id","salarie_id","periode_id","numero","numero_seq",
                 "statut","date_creation","date_validation","date_paiement","mode_paiement"}
        for col in be.__table__.columns:
            if col.name in _exclus: continue
            val=getattr(be,col.name)
            if val is None: continue
            try: valeurs_edit[col.name]=float(val)
            except (TypeError,ValueError): valeurs_edit[col.name]=val
        _comps=BulletinComposant.query.filter_by(bulletin_id=be.id).all()
        valeurs_edit["__composants"]={f"composant_{c.composant_id}":float(c.montant or 0) for c in _comps}
    ss=Salarie.query.filter_by(id=sid,tenant_id=t.id).first() if sid else None
    c=Contrat.query.filter_by(salarie_id=sid,tenant_id=t.id,actif=True).first() if sid else None
    acomptes_attente = Acompte.query.filter_by(tenant_id=t.id, salarie_id=sid, statut="EN_ATTENTE").all() if sid else []
    total_acomptes = sum(float(a.montant) for a in acomptes_attente)
    composants_actifs = ComposantPaie.query.filter_by(tenant_id=t.id, actif=True).order_by(
        ComposantPaie.ordre, ComposantPaie.libelle).all()
    return render_template("tenant/bulletin_saisie.html", salaries=sals, periodes=pers, salarie_sel=ss, contrat=c, tenant=t,
        acomptes_attente=acomptes_attente, total_acomptes=total_acomptes,
        composants_actifs=composants_actifs,
        bulletin_edit=bulletin_edit, valeurs_edit=valeurs_edit,
        periode_sel_id=(bulletin_edit.periode_id if bulletin_edit else None))

@bp.route("/bulletins/<int:id>")
@login_required
def bulletin_detail(id):
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t=get_tenant()
    if not t: return redirect(url_for("auth.login"))
    bulletin = BulletinPaie.query.filter_by(id=id,tenant_id=t.id).first_or_404()
    composants = BulletinComposant.query.filter_by(bulletin_id=bulletin.id).all()
    from datetime import date as _date
    return render_template("tenant/bulletin_detail.html",
        bulletin=bulletin, tenant=t, composants=composants,
        today=_date.today().isoformat())

@bp.route("/bulletins/<int:id>/valider", methods=["POST"])
@login_required
def bulletin_valider(id):
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    b = BulletinPaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if b.statut == "VALIDÉ":
        flash("Ce bulletin est déjà validé.", "info")
        return redirect(url_for("tenant.bulletin_detail", id=id))
    acomptes = Acompte.query.filter_by(
        tenant_id=t.id, salarie_id=b.salarie_id,
        mois=b.periode.mois, annee=b.periode.annee, statut="EN_ATTENTE").all()
    for a in acomptes: a.statut = "DEDUIT"
    b.statut = "VALIDÉ"; b.date_validation = utcnow()
    attribuer_numero_bulletin(b)
    db.session.commit()
    log_action("VALIDATE", "bulletin", b.id,
               f"Validation bulletin {b.salarie.nom_complet if b.salarie else ''} "
               f"(net {float(b.net_a_payer or 0):,.0f} F)".replace(",", " "))
    db.session.commit()
    flash("Bulletin validé avec succès.", "success")
    return redirect(url_for("tenant.bulletin_detail", id=id))

@bp.route("/bulletins/<int:id>/arrondi", methods=["POST"])
@login_required
def bulletin_arrondi(id):
    """Le tenant admin saisit le net à payer arrondi souhaité. Le système
    calcule l'écart et l'enregistre comme ligne d'ajustement d'arrondi, en
    gardant le bulletin cohérent. Interdit sur un bulletin validé."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.is_tenant_admin:
        flash("Seul l'administrateur peut ajuster le net à payer.", "error")
        return redirect(url_for("tenant.bulletin_detail", id=id))

    b = BulletinPaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if b.statut == "VALIDÉ":
        flash("Impossible d'ajuster un bulletin validé. Il faut d'abord le rouvrir.", "error")
        return redirect(url_for("tenant.bulletin_detail", id=id))

    # Net actuellement calculé, hors ajustement précédent
    ajust_actuel = float(b.ajustement_arrondi or 0)
    net_calcule = float(b.net_a_payer or 0) - ajust_actuel

    action = request.form.get("action", "definir")
    if action == "reinitialiser":
        b.net_a_payer = round(net_calcule, 2)
        b.ajustement_arrondi = 0
        db.session.commit()
        log_action("UPDATE", "bulletin", b.id, "Ajustement d'arrondi retiré")
        flash("Ajustement d'arrondi retiré. Net rétabli au montant calculé.", "success")
        return redirect(url_for("tenant.bulletin_detail", id=id))

    # Net rond souhaité
    net_souhaite_raw = request.form.get("net_souhaite", "").replace(" ", "").replace(",", ".")
    try:
        net_souhaite = round(float(net_souhaite_raw), 2)
    except (ValueError, TypeError):
        flash("Montant invalide.", "error")
        return redirect(url_for("tenant.bulletin_detail", id=id))

    ajustement = round(net_souhaite - net_calcule, 2)
    # Garde-fou : limiter l'ajustement à un vrai arrondi (±1000 F max), pour
    # éviter qu'on détourne cette fonction pour fausser un net librement.
    if abs(ajustement) > 1000:
        flash("L'ajustement d'arrondi est limité à ±1 000 F. "
              f"Écart demandé : {ajustement:,.0f} F.".replace(",", " "), "error")
        return redirect(url_for("tenant.bulletin_detail", id=id))

    b.ajustement_arrondi = ajustement
    b.net_a_payer = round(net_calcule + ajustement, 2)
    db.session.commit()
    log_action("UPDATE", "bulletin", b.id,
               f"Ajustement d'arrondi : {ajustement:+,.0f} F "
               f"(net {net_souhaite:,.0f} F)".replace(",", " "))
    flash(f"Net à payer ajusté à {net_souhaite:,.0f} F "
          f"(arrondi de {ajustement:+,.0f} F).".replace(",", " "), "success")
    return redirect(url_for("tenant.bulletin_detail", id=id))


@bp.route("/bulletins/<int:id>/payer", methods=["POST"])
@login_required
def bulletin_paye(id):
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    b = BulletinPaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    mode = (request.form.get("mode_paiement") or "").strip()
    if mode not in ("ESPECES", "VIREMENT"):
        mode = (b.salarie.mode_paiement if b.salarie else "ESPECES") or "ESPECES"
    b.mode_paiement = mode
    # Date de paiement : celle choisie dans le formulaire, sinon aujourd'hui.
    from datetime import date as _date, datetime as _dt
    date_pmt = _date.today()
    _df = (request.form.get("date_paiement") or "").strip()
    if _df:
        try:
            date_pmt = _dt.strptime(_df, "%Y-%m-%d").date()
        except ValueError:
            date_pmt = _date.today()
    b.date_paiement = date_pmt
    b.statut = "PAYÉ"; db.session.commit()
    log_action("PAY", "bulletin", b.id,
               f"Bulletin payé {b.salarie.nom_complet if b.salarie else ''}")
    db.session.commit()
    # Interconnexion Caisse : proposer la sortie correspondante (ne bloque jamais).
    try:
        from interco_caisse import proposer_ecriture
        nom = b.salarie.nom_complet if b.salarie else "salarié"
        periode = f"{b.periode.libelle_mois} {b.periode.annee}" if b.periode else ""
        proposer_ecriture(
            t, source_ref=f"bulletin-{b.id}",
            montant=float(b.net_a_payer or 0),
            motif=f"Salaire {nom} — {periode}".strip(" —"),
            compte_suggere="6611", date_operation=b.date_paiement)
    except Exception as _e:
        current_app.logger.warning(f"[COMPTA] Proposition d'écriture échouée (bulletin {b.id}) : {_e}")
    flash("Bulletin marqué comme payé.", "success")
    return redirect(url_for("tenant.bulletin_detail", id=id))

@bp.route("/bulletins/<int:id>/supprimer", methods=["POST"])
@login_required
def bulletin_supprimer(id):
    if current_user.is_super_admin:
        b = BulletinPaie.query.get_or_404(id)
        salarie_id = b.salarie_id
        db.session.delete(b); db.session.commit()
        flash("Bulletin supprimé (super admin).", "success")
        return redirect(url_for("tenant.salarie_detail", id=salarie_id))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    b = BulletinPaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if b.statut == "VALIDÉ":
        flash("Impossible de supprimer un bulletin validé.", "error")
        return redirect(url_for("tenant.bulletin_detail", id=id))
    db.session.delete(b); db.session.commit()
    flash("Bulletin supprimé.", "success")
    return redirect(url_for("tenant.bulletins"))

@bp.route("/bulletins/<int:id>/pdf")
@login_required
def bulletin_pdf(id):
    """Génère et retourne le bulletin en PDF téléchargeable."""
    if current_user.is_super_admin:
        b = BulletinPaie.query.get_or_404(id)
        t = b.salarie.tenant
    else:
        t = get_tenant()
        if not t: return redirect(url_for("auth.login"))
        b = BulletinPaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    try:
        from pdf_bulletin import generer_bulletin_pdf
        pdf_bytes = _pdf_bulletin_bytes(b, t)
        nom_fichier = (
            f"bulletin_{b.salarie.nom}_{b.salarie.prenom}_{b.periode.annee}_{b.periode.mois:02d}.pdf"
            .replace(" ", "_")
        )
        from flask import Response
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{nom_fichier}"',
                "Content-Length": str(len(pdf_bytes)),
            }
        )
    except Exception as e:
        flash(f"Erreur génération PDF : {e}", "error")
        return redirect(url_for("tenant.bulletin_detail", id=id))


@bp.route("/bulletins/export-zip/<int:periode_id>")
@login_required
def bulletins_export_zip(periode_id):
    """Télécharge TOUS les bulletins d'une période dans UN SEUL PDF (un par page)."""
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    p = PeriodePaie.query.filter_by(id=periode_id, tenant_id=t.id).first_or_404()
    bulletins = (BulletinPaie.query.filter_by(periode_id=periode_id, tenant_id=t.id)
                 .join(Salarie).order_by(Salarie.nom).all())
    if not bulletins:
        flash("Aucun bulletin à exporter pour cette période.", "error")
        return redirect(url_for("tenant.bulletins"))

    from pdf_bulletin import generer_bulletins_pdf
    try:
        data = generer_bulletins_pdf(bulletins, t, modele=(t.modele_bulletin or "classique"))
    except Exception as e:
        logger.error(f"Erreur génération PDF groupé période {periode_id} : {e}")
        flash(f"Erreur lors de la génération du PDF : {e}", "error")
        return redirect(url_for("tenant.bulletins"))

    log_action("EXPORT", "bulletin", periode_id,
               f"Export PDF groupé {len(bulletins)} bulletins — {p.libelle_complet}",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()

    nom_pdf = f"bulletins_{p.libelle_mois}_{p.annee}_{t.slug}.pdf".replace(" ", "_")
    from flask import Response
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nom_pdf}"',
                             "Content-Length": str(len(data))})


@bp.route("/bulletins/<int:id>/imprimer")
@login_required
def bulletin_imprimer(id):
    """Aperçu imprimable du bulletin (HTML), disponible dès le brouillon."""
    return _bulletin_imprimer_impl(id)


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
@bp.route("/bulletins/<int:id>/envoyer-email", methods=["POST"])
@login_required
def bulletin_envoyer_email(id):
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    b = BulletinPaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    s = b.salarie
    dest_email = request.form.get("email_dest", "").strip()
    if not dest_email and s.email:
        dest_email = s.email
    if not dest_email:
        flash(f"{s.nom_complet} n'a pas d'adresse email. Renseignez-en une dans le formulaire.", "error")
        return redirect(url_for("tenant.bulletin_detail", id=id))
    if not os.environ.get("MAIL_PASSWORD"):
        flash("Email non configuré sur le serveur : ajoutez la clé API Resend "
              "(variable MAIL_PASSWORD) dans les variables d'environnement.", "error")
        return redirect(url_for("tenant.bulletin_detail", id=id))
    try:
        corps = (f"Bonjour {s.prenom},\n\n"
                 f"Veuillez trouver votre bulletin de paie pour : {b.periode.libelle_complet}\n\n"
                 f"Salaire brut : {int(b.salaire_brut or 0)} FCFA\n"
                 f"Net a payer  : {int(b.net_a_payer or 0)} FCFA\n\n"
                 f"Cordialement,\n{t.denomination}")
        msg = Message(
            subject=f"Bulletin de paie {b.periode.libelle_complet} — {t.denomination}",
            recipients=[dest_email],
            body=corps,
            sender=current_app.config["MAIL_DEFAULT_SENDER"]
        )
        # Joindre le bulletin en PDF
        from pdf_bulletin import generer_bulletin_pdf
        pdf_bytes = _pdf_bulletin_bytes(b, t)
        nom_pdf = (f"bulletin_{s.nom}_{s.prenom}_"
                   f"{b.periode.annee}_{b.periode.mois:02d}.pdf")
        msg.attach(nom_pdf, "application/pdf", pdf_bytes)
        # ✅ Envoi dans un thread séparé → le serveur répond immédiatement
        send_email_async(current_app.extensions["mail"], msg)
        flash(f"Email en cours d'envoi à {dest_email}.", "success")
    except Exception as e:
        flash(f"Erreur préparation email: {str(e)}", "error")
    return redirect(url_for("tenant.bulletin_detail", id=id))

@bp.route("/bulletins/envoyer-tous", methods=["POST"])
@login_required
def bulletins_envoyer_tous():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    periode_id = request.form.get("periode_id", type=int)
    if not periode_id: flash("Période manquante.", "error"); return redirect(url_for("tenant.bulletins"))
    if not os.environ.get("MAIL_PASSWORD"):
        flash("Email non configuré sur le serveur : ajoutez la clé API Resend "
              "(variable MAIL_PASSWORD) dans les variables d'environnement.", "error")
        return redirect(url_for("tenant.bulletins"))
    buls = BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=periode_id).all()
    nb_ok=0; nb_sans_email=0
    for b in buls:
        if not b.salarie.email: nb_sans_email+=1; continue
        try:
            corps = (f"Bonjour {b.salarie.prenom},\n\n"
                     f"Bulletin {b.periode.libelle_complet}\n"
                     f"Net a payer : {int(b.net_a_payer or 0)} FCFA\n\n"
                     f"Cordialement, {t.denomination}")
            msg = Message(subject=f"Bulletin {b.periode.libelle_complet}",
                recipients=[b.salarie.email], body=corps,
                sender=current_app.config["MAIL_DEFAULT_SENDER"])
            from pdf_bulletin import generer_bulletin_pdf
            pdf_bytes = _pdf_bulletin_bytes(b, t)
            nom_pdf = (f"bulletin_{b.salarie.nom}_{b.salarie.prenom}_"
                       f"{b.periode.annee}_{b.periode.mois:02d}.pdf")
            msg.attach(nom_pdf, "application/pdf", pdf_bytes)
            send_email_async(current_app.extensions["mail"], msg)
            nb_ok+=1
        except Exception as e:
            print(f"Erreur email {b.salarie.email}: {e}")
    flash(f"{nb_ok} email(s) en cours d'envoi. {nb_sans_email} salarié(s) sans email.", "success")
    return redirect(url_for("tenant.bulletins"))

# ── Périodes ──────────────────────────────────────────────────────────────────
@bp.route("/periodes")
@login_required
def periodes():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t=get_tenant()
    if not t: return redirect(url_for("auth.login"))
    periodes_liste = (PeriodePaie.query.filter_by(tenant_id=t.id)
                      .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()).all())

    # Masse brute et nombre de bulletins par période (une seule requête groupée)
    stats = {}
    for pid, nb, brut, net in (db.session.query(
            BulletinPaie.periode_id,
            db.func.count(BulletinPaie.id),
            db.func.sum(BulletinPaie.salaire_brut),
            db.func.sum(BulletinPaie.net_a_payer))
            .filter_by(tenant_id=t.id)
            .group_by(BulletinPaie.periode_id).all()):
        stats[pid] = {"nb": nb or 0, "brut": float(brut or 0),
                      "net": float(net or 0), "brouillons": 0}

    # Brouillons restants : ce sont eux qui bloquent une clôture sereine
    for pid, nb in (db.session.query(
            BulletinPaie.periode_id, db.func.count(BulletinPaie.id))
            .filter_by(tenant_id=t.id, statut="BROUILLON")
            .group_by(BulletinPaie.periode_id).all()):
        if pid in stats:
            stats[pid]["brouillons"] = nb or 0

    return render_template("tenant/periodes.html", tenant=t,
        periodes=periodes_liste, stats_periodes=stats,
        now=datetime.now())

@bp.route("/periodes/<int:id>")
@login_required
def periode_detail(id):
    """Fiche récapitulative d'une période : masse salariale, retenues
    salariales, charges patronales et accès aux déclarations."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    p = PeriodePaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    bulletins = (BulletinPaie.query.options(joinedload(BulletinPaie.salarie))
                 .filter_by(tenant_id=t.id, periode_id=p.id).all())

    def somme(champ, source=None):
        return round(sum(float(getattr(b, champ) or 0) for b in (source or bulletins)), 2)

    # Répartition par statut
    par_statut = {"BROUILLON": 0, "VALIDÉ": 0, "PAYÉ": 0}
    for b in bulletins:
        st = "VALIDÉ" if b.statut in ("VALIDÉ", "VALIDE") else b.statut
        par_statut[st] = par_statut.get(st, 0) + 1

    # Masse salariale
    brut = somme("salaire_brut")
    net = somme("salaire_net")
    net_a_payer = somme("net_a_payer")

    # Retenues salariales
    retenues = {
        "cnss": somme("cnss_salarie"),
        "cnamgs": somme("cnamgs_salarie"),
        "tcs": somme("tcs"),
        "irpp": somme("irpp"),
    }
    retenues["total"] = round(sum(retenues.values()), 2)

    # Charges patronales
    patronales = {
        "cnss": somme("cnss_patronale"),
        "cnamgs": somme("cnamgs_patronale"),
        "fnh": somme("fnh"),
        "cfp": somme("cfp"),
    }
    patronales["total"] = round(sum(patronales.values()), 2)

    # Reversements par organisme (part salariale + part patronale)
    organismes = [
        {"nom": "CNSS", "salarial": retenues["cnss"], "patronal": patronales["cnss"],
         "total": round(retenues["cnss"] + patronales["cnss"], 2),
         "note": "Déclaration trimestrielle"},
        {"nom": "CNAMGS", "salarial": retenues["cnamgs"], "patronal": patronales["cnamgs"],
         "total": round(retenues["cnamgs"] + patronales["cnamgs"], 2),
         "note": "Déclaration trimestrielle"},
        {"nom": "TCS", "salarial": retenues["tcs"], "patronal": 0,
         "total": retenues["tcs"], "note": "Taxe complémentaire sur les salaires"},
        {"nom": "IRPP", "salarial": retenues["irpp"], "patronal": 0,
         "total": retenues["irpp"], "note": "Retenue à la source, reversée à la DGI"},
        {"nom": "FNH", "salarial": 0, "patronal": patronales["fnh"],
         "total": patronales["fnh"], "note": "Fonds national de l'habitat"},
        {"nom": "CFP", "salarial": 0, "patronal": patronales["cfp"],
         "total": patronales["cfp"], "note": "Contribution à la formation professionnelle"},
    ]
    total_reversements = round(sum(o["total"] for o in organismes), 2)

    stats = {
        "nb_bulletins": len(bulletins),
        "par_statut": par_statut,
        "nb_brouillons": par_statut.get("BROUILLON", 0),
        "brut": brut, "net": net, "net_a_payer": net_a_payer,
        "base_cnss": somme("base_cnss"),
        "base_cnamgs": somme("base_cnamgs"),
        "base_tcs": somme("base_tcs"),
        "base_irpp": somme("base_irpp"),
        "cout_employeur": round(brut + patronales["total"], 2),
    }

    return render_template("tenant/periode_detail.html", tenant=t, p=p,
        bulletins=bulletins, stats=stats, retenues=retenues,
        patronales=patronales, organismes=organismes,
        total_reversements=total_reversements, now=datetime.now())


@bp.route("/periodes/nouvelle", methods=["POST"])
@tenant_required
@can_edit
def periode_nouvelle():
    t=get_tenant(); annee=int(request.form["annee"]); mois=int(request.form["mois"])
    noms=PeriodePaie.MOIS_NOMS
    if PeriodePaie.query.filter_by(tenant_id=t.id,annee=annee,mois=mois).first(): flash("Période existante.","warning")
    else:
        db.session.add(PeriodePaie(tenant_id=t.id,annee=annee,mois=mois,libelle_mois=noms[mois],
            trimestre=f"T{(mois-1)//3+1}",statut="OUVERT",date_ouverture=utcnow()))
        db.session.commit(); flash(f"Période {noms[mois]} {annee} créée.","success")
    return redirect(url_for("tenant.periodes"))

@bp.route("/periodes/<int:id>/cloturer", methods=["POST"])
@tenant_required
@can_edit
def periode_cloturer(id):
    """Clôture une période, en refusant de le faire à l'aveugle s'il reste des
    brouillons : ce sont des salariés potentiellement non payés. La clôture
    reste possible, mais elle doit alors être confirmée explicitement."""
    t = get_tenant()
    p = PeriodePaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()

    if p.statut != "OUVERT":
        flash("Cette période est déjà clôturée.", "warning")
        return redirect(url_for("tenant.periodes"))

    nb_brouillons = BulletinPaie.query.filter_by(
        tenant_id=t.id, periode_id=p.id, statut="BROUILLON").count()

    if nb_brouillons and request.form.get("confirmer") != "1":
        flash(f"Clôture annulée : {nb_brouillons} bulletin(s) encore en brouillon "
              f"sur {p.libelle_complet}. Validez-les d'abord, ou confirmez la "
              f"clôture depuis la fiche de la période si c'est volontaire.", "error")
        return redirect(url_for("tenant.periode_detail", id=p.id))

    p.statut = "CLÔTURÉ"
    p.date_cloture = utcnow()
    db.session.commit()
    log_action("CLOSE", "periode", p.id,
               f"Clôture de {p.libelle_complet}"
               + (f" avec {nb_brouillons} brouillon(s) restant(s)" if nb_brouillons else ""))

    if nb_brouillons:
        flash(f"Période {p.libelle_complet} clôturée avec {nb_brouillons} "
              f"brouillon(s) non validé(s). Rouvrez-la si vous devez les traiter.",
              "warning")
    else:
        flash(f"Période {p.libelle_complet} clôturée.", "success")
    return redirect(url_for("tenant.periodes"))


@bp.route("/periodes/<int:id>/rouvrir", methods=["POST"])
@tenant_required
@can_edit
def periode_rouvrir(id):
    """Rouvre une période clôturée. Une clôture par erreur reste ainsi
    rattrapable ; l'opération est tracée dans le journal d'audit."""
    t = get_tenant()
    p = PeriodePaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()

    if p.statut == "OUVERT":
        flash("Cette période est déjà ouverte.", "info")
        return redirect(url_for("tenant.periode_detail", id=p.id))

    ancienne_cloture = p.date_cloture
    p.statut = "OUVERT"
    p.date_cloture = None
    db.session.commit()
    log_action("REOPEN", "periode", p.id,
               f"Réouverture de {p.libelle_complet}"
               + (f" (clôturée le {ancienne_cloture:%d/%m/%Y})" if ancienne_cloture else ""))
    flash(f"Période {p.libelle_complet} rouverte. Vous pouvez de nouveau y "
          f"générer et valider des bulletins.", "success")
    return redirect(url_for("tenant.periode_detail", id=p.id))

# ── Paiement abonnement ───────────────────────────────────────────────────────
@bp.route("/messages", methods=["GET", "POST"])
@login_required
def messages_support():
    """Messagerie du tenant avec le support (superadmin). Réservée à l'admin
    du tenant. GET affiche et marque comme lus les messages du superadmin.
    POST envoie un message du tenant."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_messages"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.is_tenant_admin:
        flash("La messagerie support est réservée à l'administrateur.", "error")
        return redirect(url_for("tenant.dashboard"))

    if request.method == "POST":
        corps = (request.form.get("corps") or "").strip()
        if corps:
            m = MessageSupport(tenant_id=t.id, expediteur="TENANT",
                               user_id=current_user.id, corps=corps, lu=False)
            db.session.add(m)
            db.session.commit()
        return redirect(url_for("tenant.messages_support"))

    # Marquer comme lus les messages du superadmin
    MessageSupport.query.filter_by(
        tenant_id=t.id, expediteur="SUPERADMIN", lu=False
    ).update({"lu": True})
    db.session.commit()

    messages = (MessageSupport.query.filter_by(tenant_id=t.id)
                .order_by(MessageSupport.date_creation.asc()).all())
    return render_template("tenant/messages_support.html", tenant=t, messages=messages)


@bp.route("/offres")
@login_required
def offres():
    """Page 'Nos offres' : le client compare les plans et en choisit un,
    ce qui le mène au paiement avec ce plan présélectionné."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    plans = Plan.query.filter_by(actif=True).order_by(Plan.prix_mensuel).all()
    from paliers_cabinet import PALIERS_CABINET, SUR_DEVIS
    return render_template("tenant/offres.html", tenant=t, plans=plans,
                           paliers_cabinet=PALIERS_CABINET, sur_devis=SUR_DEVIS)


@bp.route("/paiement")
@login_required
def paiement():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    plans = Plan.query.filter_by(actif=True).order_by(Plan.prix_mensuel).all()
    historique = Paiement.query.filter_by(tenant_id=t.id)\
        .order_by(Paiement.date_creation.desc()).limit(10).all()
    from coordonnees_paiement import AIRTEL_MONEY, BANQUE, CONTACT_PAIEMENT
    # Plan présélectionné (?plan=<id>) depuis la page "Nos offres" ; sinon plan actuel
    plan_choisi = request.args.get("plan", type=int) or t.plan_id
    return render_template("tenant/paiement.html", tenant=t, plans=plans,
                           historique=historique, plan_choisi=plan_choisi,
                           airtel=AIRTEL_MONEY, banque=BANQUE, contact=CONTACT_PAIEMENT)


# ── Airtel Money — Initiation ──────────────────────────────────────────────────
@bp.route("/paiement/airtel/initier", methods=["POST"])
@login_required
def paiement_airtel_initier():
    """
    Lance une demande de paiement STK Push Airtel Money.
    Le client reçoit une notification USSD sur son téléphone.
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    telephone  = request.form.get("telephone", "").strip()
    duree      = int(request.form.get("duree", 1) or 1)
    plan_id    = request.form.get("plan_id", type=int) or (t.plan_id)

    plan = db.session.get(Plan, plan_id) if plan_id else t.plan
    if not plan:
        flash("Plan introuvable.", "error")
        return redirect(url_for("tenant.paiement"))

    if not telephone:
        flash("Veuillez saisir votre numéro Airtel Money.", "error")
        return redirect(url_for("tenant.paiement"))

    montant = float(plan.prix_mensuel) * duree

    # Générer une référence unique
    import uuid
    reference = f"AM-{t.id}-{uuid.uuid4().hex[:10].upper()}"

    # Enregistrer la tentative en base
    p = Paiement(
        tenant_id=t.id,
        moyen="AIRTEL_MONEY",
        montant=montant,
        duree_mois=duree,
        plan_id=plan.id,
        reference_interne=reference,
        telephone=telephone,
        statut="EN_ATTENTE",
    )
    db.session.add(p)
    db.session.commit()

    # Appeler l'API Airtel
    try:
        from airtel_money import initier_paiement, AirtelConfigError
        resultat = initier_paiement(
            reference=reference,
            telephone=telephone,
            montant=montant,
            description=f"Abonnement PaieGabon {plan.nom} — {duree} mois",
        )
        p.reference_externe = resultat.get("transaction_id")
        import json
        p.reponse_raw = json.dumps(resultat.get("raw", {}))

        if resultat["success"]:
            db.session.commit()
            logger.info(f"[Paiement] Airtel initié — ref={reference} tenant={t.id}")
            flash(
                f"Demande de paiement envoyée sur le {telephone}. "
                "Validez sur votre téléphone dans les 2 minutes.",
                "success"
            )
            return redirect(url_for("tenant.paiement_airtel_attente", reference=reference))
        else:
            p.statut = "ECHEC"
            p.notes  = resultat["message"]
            db.session.commit()
            flash(f"Échec : {resultat['message']}", "error")
            return redirect(url_for("tenant.paiement"))

    except Exception as e:
        p.statut = "ECHEC"
        p.notes  = str(e)
        db.session.commit()
        logger.error(f"[Paiement] Erreur Airtel : {e}")
        flash(f"Erreur de connexion Airtel Money. Réessayez ou contactez le support.", "error")
        return redirect(url_for("tenant.paiement"))


# ── Airtel Money — Page d'attente ──────────────────────────────────────────────
@bp.route("/paiement/airtel/attente/<reference>")
@login_required
def paiement_airtel_attente(reference):
    """
    Page d'attente affichée après l'initiation.
    Fait un polling automatique toutes les 5 secondes via AJAX.
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    p = Paiement.query.filter_by(
        reference_interne=reference, tenant_id=t.id
    ).first_or_404()
    return render_template("tenant/paiement_attente.html", paiement=p, tenant=t)


# ── Airtel Money — Vérification statut (AJAX polling) ─────────────────────────
@bp.route("/paiement/airtel/statut/<reference>")
@login_required
def paiement_airtel_statut(reference):
    """
    Endpoint JSON pour le polling côté client.
    Retourne le statut actuel de la transaction.
    """
    t = get_tenant()
    if not t: return jsonify({"statut": "ERREUR", "message": "Non connecté"}), 401

    p = Paiement.query.filter_by(
        reference_interne=reference, tenant_id=t.id
    ).first_or_404()

    # Si déjà confirmé en base, retourner directement
    if p.statut == "SUCCES":
        return jsonify({"statut": "SUCCES", "message": "Paiement confirmé !"})
    if p.statut == "ECHEC":
        return jsonify({"statut": "ECHEC", "message": p.notes or "Paiement refusé."})
    if p.statut == "EXPIRE":
        return jsonify({"statut": "EXPIRE", "message": "Délai dépassé. Recommencez."})

    # Interroger l'API Airtel si on a un transaction_id
    if p.reference_externe:
        try:
            from airtel_money import verifier_statut
            r = verifier_statut(p.reference_externe)
            if r["statut"] == "SUCCESS":
                _activer_abonnement(p)
                return jsonify({"statut": "SUCCES", "message": "Paiement confirmé !"})
            elif r["statut"] in ("FAILED", "EXPIRED"):
                p.statut = "ECHEC" if r["statut"] == "FAILED" else "EXPIRE"
                p.notes  = r.get("message", "")
                db.session.commit()
                return jsonify({"statut": p.statut, "message": p.notes})
        except Exception as e:
            logger.warning(f"[Paiement] Polling Airtel erreur : {e}")

    return jsonify({"statut": "EN_ATTENTE", "message": "En attente de confirmation…"})


# ── Airtel Money — Webhook (callback automatique d'Airtel) ────────────────────
@bp.route("/webhook/airtel", methods=["POST"])
def webhook_airtel():
    """
    Reçoit les notifications automatiques d'Airtel après paiement du client.
    Airtel appelle cette URL avec le résultat de la transaction.
    """
    from airtel_money import valider_signature_webhook
    import json

    payload_bytes = request.get_data()
    signature     = request.headers.get("X-Airtel-Signature", "")

    # Vérifier la signature si configurée
    if not valider_signature_webhook(payload_bytes, signature):
        logger.warning("[Webhook Airtel] Signature invalide — requête ignorée.")
        return jsonify({"status": "SIGNATURE_INVALIDE"}), 401

    try:
        data = request.get_json(force=True) or {}
        logger.info(f"[Webhook Airtel] Reçu : {data}")

        # Extraire les infos de la transaction
        txn   = data.get("transaction", {}) or data.get("data", {}).get("transaction", {})
        ref   = txn.get("id") or txn.get("reference") or data.get("reference", "")
        statut_api = (txn.get("status") or data.get("status", {}).get("code", "")).upper()

        if not ref:
            logger.warning("[Webhook Airtel] Référence manquante dans le payload.")
            return jsonify({"status": "REF_MANQUANTE"}), 400

        # Retrouver le paiement
        p = Paiement.query.filter(
            (Paiement.reference_interne == ref) |
            (Paiement.reference_externe == ref)
        ).first()

        if not p:
            logger.warning(f"[Webhook Airtel] Paiement introuvable pour ref={ref}")
            return jsonify({"status": "INTROUVABLE"}), 404

        if p.statut == "SUCCES":
            # Idempotence — déjà traité
            return jsonify({"status": "DEJA_TRAITE"}), 200

        p.reponse_raw = json.dumps(data)

        if statut_api in ("TS", "SUCCESS", "200"):
            # Confirmation autoritative : on ré-interroge Airtel avant d'activer
            from airtel_money import verifier_statut
            verif = verifier_statut(p.reference_externe or ref)
            if verif.get("statut") != "SUCCESS":
                p.statut = "ECHEC"
                p.notes  = f"Webhook OK mais vérification Airtel = {verif.get('statut')}"
                db.session.commit()
                logger.warning(f"[Webhook Airtel] Vérif. divergente — ref={ref}")
                return jsonify({"status": "NON_CONFIRME"}), 200
            _activer_abonnement(p)
            logger.info(f"[Webhook Airtel] Succès vérifié — ref={ref} tenant={p.tenant_id}")
        else:
            p.statut = "ECHEC"
            p.notes  = f"Code Airtel : {statut_api}"
            db.session.commit()
            logger.info(f"[Webhook Airtel] Échec — ref={ref} code={statut_api}")

        return jsonify({"status": "OK"}), 200

    except Exception as e:
        logger.error(f"[Webhook Airtel] Erreur traitement : {e}")
        db.session.rollback()
        return jsonify({"status": "ERREUR_INTERNE"}), 500


# ── Helper : activer l'abonnement après paiement confirmé ─────────────────────
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


@bp.route("/paiement/confirmer", methods=["POST"])
@login_required
def paiement_confirmer():
    """Paiement manuel : le client déclare avoir payé (Airtel Money ou virement).
    Crée un Paiement EN_ATTENTE que le super-admin validera après vérification."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    moyen     = request.form.get("moyen", "MANUEL").strip().upper()  # AIRTEL_MONEY | VIREMENT
    reference = request.form.get("reference", "").strip()
    duree     = int(request.form.get("duree", 1) or 1)
    plan_id   = request.form.get("plan_id", type=int) or t.plan_id
    if not reference:
        flash("Veuillez indiquer la référence de la transaction.", "error")
        return redirect(url_for("tenant.paiement"))

    plan = db.session.get(Plan, plan_id) if plan_id else t.plan
    # Montant attendu avec remises par durée (3 mois -5%, 6 mois -10%, 12 mois -15%)
    remises = {1: 1.0, 3: 0.95, 6: 0.90, 12: 0.85}
    coef = remises.get(duree, 1.0)
    montant = round(float(plan.prix_mensuel) * duree * coef) if plan else 0

    import uuid
    ref_interne = f"MAN-{t.id}-{uuid.uuid4().hex[:8].upper()}"
    libelle_moyen = {"AIRTEL_MONEY": "Airtel Money", "VIREMENT": "Virement bancaire"}.get(moyen, moyen)
    p = Paiement(
        tenant_id=t.id, moyen=moyen, montant=montant,
        duree_mois=duree, plan_id=plan.id if plan else None,
        reference_interne=ref_interne, reference_externe=reference,
        statut="EN_ATTENTE",
        notes=f"Paiement {libelle_moyen} déclaré par {current_user.email}",
    )
    db.session.add(p)
    t.statut = "PAIEMENT_EN_ATTENTE"
    db.session.commit()
    log_action("CREATE", "paiement", p.id,
               f"Déclaration paiement {libelle_moyen} — réf {reference}, {duree} mois",
               user_id=current_user.id, tenant_id=t.id)
    flash(f"Paiement déclaré (réf. {reference}). Votre abonnement sera activé "
          f"après vérification, généralement sous 24-48h. Merci !", "success")
    return redirect(url_for("tenant.paiement"))


# ══════════════════════════════════════════════════════════════════════════════
# CINETPAY — Paiement multi-opérateurs (Airtel, Moov, Visa, Mastercard)
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/paiement/cinetpay/initier", methods=["POST"])
@login_required
def paiement_cinetpay_initier():
    """
    Initie un paiement CinetPay.
    Crée une session et redirige le client vers la page de paiement CinetPay
    où il choisit son moyen : Airtel Money, Moov Money ou carte bancaire.
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    duree   = int(request.form.get("duree", 1) or 1)
    plan_id = request.form.get("plan_id", type=int) or t.plan_id
    plan    = db.session.get(Plan, plan_id) if plan_id else t.plan

    if not plan:
        flash("Plan introuvable.", "error")
        return redirect(url_for("tenant.paiement"))

    montant = float(plan.prix_mensuel) * duree

    import uuid
    reference = f"CP-{t.id}-{uuid.uuid4().hex[:10].upper()}"

    # Récupérer l'admin du tenant pour pré-remplir les infos client
    admin = Utilisateur.query.filter_by(tenant_id=t.id, role="TENANT_ADMIN").first()
    nom_client   = admin.nom_complet if admin else t.denomination
    email_client = admin.email if admin else ""

    # Enregistrer la tentative
    p = Paiement(
        tenant_id=t.id,
        moyen="CINETPAY",
        montant=montant,
        duree_mois=duree,
        plan_id=plan.id,
        reference_interne=reference,
        statut="EN_ATTENTE",
    )
    db.session.add(p)
    db.session.commit()

    try:
        from cinetpay import initier_paiement, CinetPayConfigError
        resultat = initier_paiement(
            reference=reference,
            montant=montant,
            description=f"PaieGabon {plan.nom} — {duree} mois — {t.denomination}",
            nom_client=nom_client,
            email_client=email_client,
        )

        import json
        p.reponse_raw = json.dumps(resultat.get("raw", {}))

        if resultat["success"]:
            p.reference_externe = resultat.get("payment_token", "")
            db.session.commit()
            logger.info(f"[CinetPay] Session créée — ref={reference} tenant={t.id}")
            # Rediriger directement vers la page CinetPay
            return redirect(resultat["payment_url"])
        else:
            p.statut = "ECHEC"
            p.notes  = resultat["message"]
            db.session.commit()
            flash(f"Erreur CinetPay : {resultat['message']}", "error")
            return redirect(url_for("tenant.paiement"))

    except Exception as e:
        p.statut = "ECHEC"
        p.notes  = str(e)
        db.session.commit()
        logger.error(f"[CinetPay] Erreur initiation : {e}")
        flash("Erreur de connexion CinetPay. Réessayez ou contactez le support.", "error")
        return redirect(url_for("tenant.paiement"))


@bp.route("/paiement/cinetpay/retour")
@login_required
def paiement_cinetpay_retour():
    """
    Page de retour après la page de paiement CinetPay.
    CinetPay redirige ici après que le client ait terminé (succès ou annulation).
    On affiche un message d'attente pendant que le webhook confirme.
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    transaction_id = request.args.get("transaction_id", "")
    # Chercher le paiement par référence interne ou externe
    p = None
    if transaction_id:
        p = Paiement.query.filter(
            (Paiement.reference_interne == transaction_id) |
            (Paiement.reference_externe == transaction_id),
            Paiement.tenant_id == t.id
        ).first()

    # Vérification immédiate du statut
    if p and p.statut == "EN_ATTENTE" and (p.reference_interne or p.reference_externe):
        try:
            from cinetpay import verifier_statut
            ref = p.reference_interne
            r   = verifier_statut(ref)
            if r["statut"] == "ACCEPTED":
                _activer_abonnement(p)
                flash("Paiement confirmé ! Votre abonnement est actif.", "success")
                return redirect(url_for("tenant.dashboard"))
            elif r["statut"] in ("REFUSED", "CANCELLED"):
                p.statut = "ECHEC"
                p.notes  = r.get("message", "Paiement refusé ou annulé.")
                db.session.commit()
        except Exception as e:
            logger.warning(f"[CinetPay] Vérification retour échouée : {e}")

    if p and p.statut == "SUCCES":
        flash("Paiement confirmé ! Votre abonnement est actif.", "success")
        return redirect(url_for("tenant.dashboard"))

    # Afficher la page d'attente (le webhook va confirmer dans quelques secondes)
    return render_template("tenant/paiement_cinetpay_retour.html",
                           paiement=p, tenant=t,
                           transaction_id=transaction_id)


@bp.route("/paiement/cinetpay/statut/<reference>")
@login_required
def paiement_cinetpay_statut(reference):
    """Endpoint JSON pour le polling côté client sur la page de retour."""
    t = get_tenant()
    if not t: return jsonify({"statut": "ERREUR"}), 401

    p = Paiement.query.filter(
        (Paiement.reference_interne == reference),
        Paiement.tenant_id == t.id
    ).first_or_404()

    if p.statut == "SUCCES":
        return jsonify({"statut": "SUCCES", "message": "Paiement confirmé !"})
    if p.statut == "ECHEC":
        return jsonify({"statut": "ECHEC", "message": p.notes or "Paiement refusé."})

    # Vérification active
    try:
        from cinetpay import verifier_statut
        r = verifier_statut(reference)
        if r["statut"] == "ACCEPTED":
            _activer_abonnement(p)
            return jsonify({"statut": "SUCCES", "message": "Paiement confirmé !"})
        elif r["statut"] in ("REFUSED", "CANCELLED"):
            p.statut = "ECHEC"
            p.notes  = r.get("message", "")
            db.session.commit()
            return jsonify({"statut": "ECHEC", "message": p.notes})
    except Exception as e:
        logger.warning(f"[CinetPay] Polling statut erreur : {e}")

    return jsonify({"statut": "EN_ATTENTE", "message": "Vérification en cours…"})


@bp.route("/webhook/cinetpay", methods=["POST"])
def webhook_cinetpay():
    """
    Reçoit les notifications automatiques de CinetPay.

    SÉCURITÉ : on ne fait JAMAIS confiance au statut envoyé dans le corps de la
    requête (falsifiable — le site_id n'est pas un secret). On ré-interroge
    l'API CinetPay (/payment/check) pour obtenir le statut et le montant
    authentiques, et on vérifie que le montant payé correspond au montant
    attendu avant d'activer l'abonnement.
    """
    import json
    from cinetpay import valider_webhook, verifier_statut

    # 1. Lecture tolérante du corps (CinetPay peut envoyer du JSON ou du form-data)
    data = request.get_json(silent=True) or request.form.to_dict()
    logger.info(f"[Webhook CinetPay] Reçu : {data}")

    # 2. Filtre de premier niveau : le site_id doit correspondre
    if not valider_webhook(data):
        return jsonify({"status": "SITE_ID_INVALIDE"}), 401

    # 3. Extraire la référence de transaction
    ref = (data.get("cpm_trans_id") or data.get("transaction_id")
           or data.get("metadata") or "")
    if not ref:
        logger.warning("[Webhook CinetPay] Référence manquante.")
        return jsonify({"status": "REF_MANQUANTE"}), 400

    # 4. Retrouver le paiement en base
    p = Paiement.query.filter_by(reference_interne=ref).first()
    if not p:
        token = data.get("cpm_payment_config") or data.get("payment_token", "")
        p = Paiement.query.filter_by(reference_externe=token).first() if token else None
    if not p:
        logger.warning(f"[Webhook CinetPay] Paiement introuvable ref={ref}")
        return jsonify({"status": "INTROUVABLE"}), 404

    # 5. Idempotence — déjà traité avec succès
    if p.statut == "SUCCES":
        return jsonify({"status": "DEJA_TRAITE"}), 200

    # 6. VÉRIFICATION AUTORITATIVE côté serveur (ne pas croire le corps)
    try:
        verif = verifier_statut(p.reference_interne)
    except Exception as e:
        logger.error(f"[Webhook CinetPay] Échec vérification API : {e}")
        db.session.rollback()
        return jsonify({"status": "VERIF_ERREUR"}), 502

    p.reponse_raw = json.dumps({"webhook": data, "verification": verif.get("raw", {})})

    if verif.get("statut") != "ACCEPTED":
        p.statut = "ECHEC"
        p.notes  = f"Statut CinetPay vérifié : {verif.get('statut')}"
        db.session.commit()
        logger.info(f"[Webhook CinetPay] Non confirmé — ref={ref} statut={verif.get('statut')}")
        return jsonify({"status": "NON_CONFIRME"}), 200

    # 7. Vérifier que le MONTANT payé correspond au montant attendu
    montant_attendu = int(round(float(p.montant or 0)))
    try:
        montant_paye = int(round(float(verif.get("montant") or 0)))
    except (TypeError, ValueError):
        montant_paye = 0
    if montant_paye and montant_paye < montant_attendu:
        p.statut = "ECHEC"
        p.notes  = f"Montant payé ({montant_paye}) < attendu ({montant_attendu}) — rejeté."
        db.session.commit()
        logger.warning(f"[Webhook CinetPay] Montant insuffisant ref={ref} : "
                       f"{montant_paye} < {montant_attendu}")
        return jsonify({"status": "MONTANT_INVALIDE"}), 200

    # 8. Tout est vérifié → activer l'abonnement
    try:
        _activer_abonnement(p)
        logger.info(f"[Webhook CinetPay] Succès vérifié — ref={ref} tenant={p.tenant_id}")
        return jsonify({"status": "OK"}), 200
    except Exception as e:
        logger.error(f"[Webhook CinetPay] Erreur activation : {e}")
        db.session.rollback()
        return jsonify({"status": "ERREUR_INTERNE"}), 500


@bp.route("/parametres/export-donnees")
@tenant_required
def parametres_export_donnees():
    """Exporte toutes les données de l'entreprise dans un ZIP (classeur Excel multi-onglets)."""
    import openpyxl, zipfile
    from flask import Response
    from openpyxl.styles import Font, PatternFill
    t = get_tenant()

    # (Onglet, Modèle, requête filtrée sur le tenant)
    exports = [
        ("Salariés",     Salarie,        Salarie.query.filter_by(tenant_id=t.id)),
        ("Contrats",     Contrat,        Contrat.query.filter_by(tenant_id=t.id)),
        ("Bulletins",    BulletinPaie,   BulletinPaie.query.filter_by(tenant_id=t.id)),
        ("Périodes",     PeriodePaie,    PeriodePaie.query.filter_by(tenant_id=t.id)),
        ("Congés",       Conge,          Conge.query.filter_by(tenant_id=t.id)),
        ("Catégories",   CategorieEmploi, CategorieEmploi.query.filter_by(tenant_id=t.id)),
        ("Composants",   ComposantPaie,  ComposantPaie.query.filter_by(tenant_id=t.id)),
    ]

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    HF = PatternFill("solid", fgColor="0f3d36"); HN = Font(bold=True, color="FFFFFF")

    def _val(v):
        if v is None: return ""
        if isinstance(v, (datetime, date)): return v.strftime("%d/%m/%Y %H:%M") if isinstance(v, datetime) else v.strftime("%d/%m/%Y")
        if isinstance(v, bool): return "Oui" if v else "Non"
        return v

    for titre, modele, query in exports:
        cols = [c.name for c in modele.__table__.columns]
        ws = wb.create_sheet(titre[:31])
        for ci, cn in enumerate(cols, 1):
            cell = ws.cell(1, ci, cn); cell.fill = HF; cell.font = HN
        for ri, obj in enumerate(query.all(), 2):
            for ci, cn in enumerate(cols, 1):
                ws.cell(ri, ci, _val(getattr(obj, cn, None)))
        ws.freeze_panes = "A2"

    xlsx_buf = io.BytesIO(); wb.save(xlsx_buf); xlsx_buf.seek(0)

    # ZIP : le classeur + un LISEZ-MOI
    nom_ent = (t.denomination or "entreprise").replace(" ", "_")[:30]
    horodatage = datetime.now().strftime("%Y-%m-%d_%H%M")
    readme = (
        f"EXPORT DES DONNÉES — {t.denomination}\n"
        f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}\n\n"
        f"Ce fichier contient une copie de vos données PaieGabon :\n"
        f"  • Salariés, Contrats, Bulletins, Périodes, Congés, Catégories, Composants.\n\n"
        f"Ouvrez le fichier .xlsx avec Excel, LibreOffice ou Google Sheets.\n"
        f"Conservez cette archive en lieu sûr (disque, cloud personnel).\n\n"
        f"— Ameriack I.T. Solutions / PaieGabon\n"
    )
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"donnees_{nom_ent}_{horodatage}.xlsx", xlsx_buf.getvalue())
        zf.writestr("LISEZ-MOI.txt", readme)
    zip_buf.seek(0)

    log_action("EXPORT", "tenant", t.id, "Export des données de l'entreprise",
               user_id=current_user.id, tenant_id=t.id)
    nom_zip = f"export_paiegabon_{nom_ent}_{horodatage}.zip"
    return Response(zip_buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nom_zip}"'})


@bp.route("/parametres")
@tenant_required
def parametres():
    t=get_tenant()
    # Passer tous les plans actifs pour l'onglet abonnement
    plans_dispo = Plan.query.filter_by(actif=True).order_by(Plan.prix_mensuel.asc()).all()
    return render_template("tenant/parametres.html", tenant=t,
        config_rubriques=_config_rubriques_dict(t.id),
        rubriques=RubriquePaie.query.filter_by(actif=True).all(),
        categories=CategorieEmploi.query.filter_by(tenant_id=t.id).all(),
        users=Utilisateur.query.filter_by(tenant_id=t.id).all(),
        plans_dispo=plans_dispo)

@bp.route("/parametres/grille-salaires", methods=["GET", "POST"])
@tenant_required
def grille_salaires():
    """Édition/vérification de la grille de salaires conventionnelle.
    L'auto-remplissage du salaire de base (fiche salarié) n'utilise QUE la grille
    enregistrée ici — jamais la graine brute — pour garantir des montants validés."""
    import json as _json
    from calculs_paie import GRILLE_CATEGORIES_AERIEN, grille_salaire_aerien_seed
    t = get_tenant()
    if not current_user.can_edit:
        abort(403)

    if request.method == "POST":
        grille = {}
        for key, val in request.form.items():
            if not key.startswith("montant_") or not (val or "").strip():
                continue
            try:
                _, code, ech = key.split("_", 2)
                montant = float(val.replace(" ", "").replace("\u202f", "").replace(",", "."))
            except (ValueError, IndexError):
                continue
            if montant > 0:
                grille.setdefault(code, {})[ech] = round(montant, 2)
        t.grille_salaires = _json.dumps(grille, ensure_ascii=False)
        db.session.commit()
        log_action("UPDATE", "tenant", t.id, "Grille de salaires mise à jour")
        flash("Grille de salaires enregistrée. Elle est désormais utilisée pour "
              "pré-remplir le salaire de base des salariés.", "success")
        return redirect(url_for("tenant.grille_salaires"))

    # GET : grille sauvegardée, sinon graine aérienne (à vérifier) si convention AERIEN.
    grille, est_seed = {}, False
    if t.grille_salaires:
        try:
            grille = _json.loads(t.grille_salaires)
        except (ValueError, TypeError):
            grille = {}
    if not grille and (t.convention or "").upper() == "AERIEN":
        grille = grille_salaire_aerien_seed()
        est_seed = True
    if not grille and (t.convention or "").upper() == "HOTELLERIE":
        from convention_hotellerie import grille_salaire_hotellerie_seed
        grille = grille_salaire_hotellerie_seed()
        est_seed = True
    if not grille and (t.convention or "").upper() == "BOIS":
        from convention_bois import grille_salaire_bois_seed
        grille = grille_salaire_bois_seed()
        est_seed = True
    if not grille and (t.convention or "").upper() == "MINIER":
        from convention_minier import grille_salaire_minier_seed
        grille = grille_salaire_minier_seed()
        est_seed = True
    if not grille and (t.convention or "").upper() == "FORET":
        from convention_foret import grille_salaire_foret_seed
        grille = grille_salaire_foret_seed()
        est_seed = True
    return render_template("tenant/grille_salaires.html", tenant=t,
        categories=GRILLE_CATEGORIES_AERIEN, grille=grille,
        est_seed=est_seed, nb_echelons=10)


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


@bp.route("/parametres/logo", methods=["POST"])
@login_required
def parametres_logo():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    logo_file = request.files.get("logo")
    if logo_file and logo_file.filename:
        import base64
        file_data = logo_file.read()
        if len(file_data) > 1_000_000:
            flash("Fichier trop volumineux. Maximum 1 Mo.", "error")
            return redirect(url_for("tenant.parametres"))

        # ── Validation de l'extension ─────────────────────────────────────────
        ext = logo_file.filename.rsplit(".", 1)[-1].lower() if "." in logo_file.filename else ""
        EXTENSIONS_AUTORISEES = {"png", "jpg", "jpeg", "gif", "webp"}
        if ext not in EXTENSIONS_AUTORISEES:
            flash("Format non autorisé. Utilisez PNG, JPG, JPEG, GIF ou WEBP (pas SVG).", "error")
            return redirect(url_for("tenant.parametres"))

        # ── Validation du MIME réel (magic bytes) — pas seulement l'extension ─
        MAGIC = {
            b"\x89PNG":   "image/png",
            b"\xff\xd8\xff": "image/jpeg",
            b"GIF8":      "image/gif",
            b"RIFF":      None,  # WebP — vérification complémentaire ci-dessous
        }
        detected_mime = None
        for magic, mime in MAGIC.items():
            if file_data[:len(magic)] == magic:
                if magic == b"RIFF" and file_data[8:12] == b"WEBP":
                    detected_mime = "image/webp"
                else:
                    detected_mime = mime
                break
        if not detected_mime:
            flash("Le contenu du fichier ne correspond pas à une image valide.", "error")
            return redirect(url_for("tenant.parametres"))

        b64 = base64.b64encode(file_data).decode("utf-8")
        logo_data = f"data:{detected_mime};base64,{b64}"
        try:
            db.session.execute(db.text("UPDATE tenants SET logo_url = :logo WHERE id = :id"),{"logo": logo_data, "id": t.id})
            db.session.commit(); db.session.expire(t)
            log_action("UPDATE", "parametres", t.id, "Mise à jour du logo de la société")
            db.session.commit()
            flash("Logo mis à jour avec succès.", "success")
        except Exception as e:
            db.session.rollback(); flash(f"Erreur: {str(e)}", "error")
    else:
        flash("Aucun fichier sélectionné.", "error")
    return redirect(url_for("tenant.parametres"))

@bp.route("/parametres/logo/supprimer", methods=["POST"])
@login_required
def parametres_logo_supprimer():
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    t.logo_url = None; db.session.commit()
    log_action("UPDATE", "parametres", t.id, "Suppression du logo de la société")
    db.session.commit()
    flash("Logo supprime.", "success")
    return redirect(url_for("tenant.parametres"))

@bp.route("/parametres/modele-bulletin", methods=["POST"])
@login_required
def parametres_modele_bulletin():
    """Changer le modèle d'impression des bulletins."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    modele = request.form.get("modele_bulletin", "classique")
    if modele not in ("classique", "moderne", "minimaliste", "grandlivre", "sgtg"):
        modele = "classique"
    t.modele_bulletin = modele
    db.session.commit()
    log_action("UPDATE", "parametres", t.id, f"Modèle d'impression bulletins : {modele}")
    db.session.commit()
    flash(f"Modèle d'impression « {modele.capitalize()} » appliqué.", "success")
    return redirect(url_for("tenant.parametres"))

@bp.route("/parametres/rubriques", methods=["POST"])
@login_required
def parametres_rubriques():
    """Enregistre le paramétrage des rubriques fixes souples (panier, transport,
    représentation, salisure) : entre dans le brut, position, soumis CNSS/CNAMGS/IRPP."""
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        flash("Accès refusé.", "error"); return redirect(url_for("tenant.parametres"))
    from models import ConfigRubrique
    for cle in ("panier", "transport", "representation", "salisure"):
        c = ConfigRubrique.query.filter_by(tenant_id=t.id, cle=cle).first()
        if not c:
            c = ConfigRubrique(tenant_id=t.id, cle=cle)
            db.session.add(c)
        c.entre_dans_brut = request.form.get(f"{cle}_brut") == "on"
        c.position        = "HAUT" if request.form.get(f"{cle}_position") == "HAUT" else "BAS"
        c.soumis_cnss     = request.form.get(f"{cle}_cnss") == "on"
        c.soumis_cnamgs   = request.form.get(f"{cle}_cnamgs") == "on"
        c.soumis_irpp     = request.form.get(f"{cle}_irpp") == "on"
    db.session.commit()
    flash("Paramétrage des rubriques enregistré.", "success")
    return redirect(url_for("tenant.parametres"))


@bp.route("/parametres/societe", methods=["POST"])
@tenant_required
@can_edit
def parametres_societe():
    if not current_user.can_manage_parametres:
        flash("Accès refusé. Seul l'administrateur peut modifier les paramètres.", "error")
        return redirect(url_for("tenant.parametres"))
    t=get_tenant()
    for f in ["denomination","sigle","activite","secteur","nif","numero_cnss","numero_cnamgs","adresse","boite_postale","telephone","ville","region","representant_nom","representant_fonction"]:
        if f not in request.form:   # champ absent de ce formulaire → ne pas écraser
            continue
        try: setattr(t,f,request.form.get(f,"").strip() or None)
        except: pass
    # Convention collective applicable — mise à jour uniquement si le champ est
    # présent dans le formulaire soumis (évite d'écraser la valeur depuis un
    # formulaire qui ne l'inclut pas, ex. la carte « Informations générales »).
    from calculs_paie import CONVENTIONS_DISPONIBLES
    conv_raw = request.form.get("convention")
    if conv_raw is not None:
        conv = (conv_raw or "AUCUNE").upper()
        if conv in CONVENTIONS_DISPONIBLES:
            t.convention = conv
    # Seuil hebdomadaire de déclenchement des heures supplémentaires (dérogation).
    # La loi gabonaise fixe le seuil légal à 40h/semaine ; une dérogation peut le
    # porter jusqu'à 48h. On borne strictement la valeur saisie dans cet intervalle.
    seuil_raw = request.form.get("seuil_heures_sup_hebdo")
    if seuil_raw is not None and str(seuil_raw).strip() != "":
        try:
            seuil = float(str(seuil_raw).replace(",", "."))
            t.seuil_heures_sup_hebdo = max(40.0, min(seuil, 48.0))
        except (ValueError, TypeError):
            flash("Seuil d'heures supplémentaires invalide — valeur inchangée.", "error")
    # Langue de l'interface — idem, seulement si le champ est soumis.
    langue = request.form.get("langue")
    if langue is not None and langue in SUPPORTED_LANGUAGES:
        t.langue = langue
        set_language(langue)
    db.session.commit()
    log_action("UPDATE", "parametres", t.id, "Modification des informations de la société")
    db.session.commit()
    flash("Informations mises à jour." if (t.langue or "fr") == "fr" else "Settings updated.", "success")
    return redirect(url_for("tenant.parametres"))


@bp.route("/parametres/importer-grille-commerce", methods=["POST"])
@tenant_required
@can_edit
def importer_grille_commerce():
    """Crée/complète les catégories d'emploi à partir de la grille conventionnelle COMMERCE."""
    if not current_user.can_manage_parametres:
        flash("Accès refusé. Seul l'administrateur peut modifier les paramètres.", "error")
        return redirect(url_for("tenant.parametres"))
    t = get_tenant()
    from calculs_paie import GRILLE_COMMERCE
    existantes = {c.code for c in CategorieEmploi.query.filter_by(tenant_id=t.id).all()}
    ajout = 0
    maj = 0
    for code, libelle, mensuel, _horaire in GRILLE_COMMERCE:
        if code in existantes:
            cat = CategorieEmploi.query.filter_by(tenant_id=t.id, code=code).first()
            if cat and (cat.salaire_minimum is None or float(cat.salaire_minimum or 0) == 0):
                cat.salaire_minimum = mensuel
                maj += 1
        else:
            db.session.add(CategorieEmploi(
                tenant_id=t.id, code=code, libelle=libelle, salaire_minimum=mensuel,
                description="Grille Convention Collective du Commerce"))
            ajout += 1
    # Bascule la convention du tenant sur COMMERCE si pas déjà fait
    if t.convention != "COMMERCE":
        t.convention = "COMMERCE"
    db.session.commit()
    flash(f"Grille Commerce importée : {ajout} catégorie(s) ajoutée(s), {maj} mise(s) à jour.", "success")
    return redirect(url_for("tenant.parametres"))


@bp.route("/parametres/importer-grille-hydrocarbures", methods=["POST"])
@tenant_required
@can_edit
def importer_grille_hydrocarbures():
    """Crée/complète les catégories A→M à partir de la grille Hydrocarbures."""
    if not current_user.can_manage_parametres:
        flash("Accès refusé. Seul l'administrateur peut modifier les paramètres.", "error")
        return redirect(url_for("tenant.parametres"))
    t = get_tenant()
    from convention_hydrocarbures import GRILLE_HYDROCARBURES
    existantes = {c.code for c in CategorieEmploi.query.filter_by(tenant_id=t.id).all()}
    ajout = 0
    maj = 0
    for code, libelle, mensuel, _horaire in GRILLE_HYDROCARBURES:
        if code in existantes:
            cat = CategorieEmploi.query.filter_by(tenant_id=t.id, code=code).first()
            if cat and (cat.salaire_minimum is None or float(cat.salaire_minimum or 0) == 0):
                cat.salaire_minimum = mensuel
                maj += 1
        else:
            db.session.add(CategorieEmploi(
                tenant_id=t.id, code=code, libelle=libelle, salaire_minimum=mensuel,
                description="Grille Convention Hydrocarbures (Recherche & Exploitation)"))
            ajout += 1
    # Bascule la convention du tenant sur HYDROCARBURES
    if t.convention != "HYDROCARBURES":
        t.convention = "HYDROCARBURES"
    # Congés de base : 2,5 jours ouvrables / mois (Art. 42)
    if hasattr(t, "jours_conge_par_mois"):
        t.jours_conge_par_mois = 2.5
    db.session.commit()
    flash(f"Grille Hydrocarbures importée : {ajout} catégorie(s) ajoutée(s), {maj} mise(s) à jour.", "success")
    return redirect(url_for("tenant.parametres"))


@bp.route("/parametres/importer-grille-petrole", methods=["POST"])
@tenant_required
@can_edit
def importer_grille_petrole():
    """Crée/complète les catégories d'emploi à partir de la grille conventionnelle PÉTROLE.

    ⚠️ Les montants de l'Annexe n°2 (1983) sont obsolètes : les premières
    catégories sont sous le SMIG actuel. On importe donc la STRUCTURE des
    catégories en appliquant un plancher au SMIG légal, et on n'écrase jamais une
    catégorie existante. Les montants doivent être actualisés par l'entreprise.
    """
    if not current_user.can_manage_parametres:
        flash("Accès refusé. Seul l'administrateur peut modifier les paramètres.", "error")
        return redirect(url_for("tenant.parametres"))
    t = get_tenant()
    from calculs_paie import GRILLE_PETROLE, SMIG_GABON
    existantes = {c.code for c in CategorieEmploi.query.filter_by(tenant_id=t.id).all()}
    ajout = 0
    maj = 0
    plancher_applique = False
    for code, libelle, mensuel_1983 in GRILLE_PETROLE:
        # Plancher SMIG : aucune catégorie ne peut être créée sous le minimum légal.
        mensuel = max(int(mensuel_1983), int(SMIG_GABON))
        if mensuel != int(mensuel_1983):
            plancher_applique = True
        if code in existantes:
            cat = CategorieEmploi.query.filter_by(tenant_id=t.id, code=code).first()
            if cat and (cat.salaire_minimum is None or float(cat.salaire_minimum or 0) == 0):
                cat.salaire_minimum = mensuel
                maj += 1
        else:
            db.session.add(CategorieEmploi(
                tenant_id=t.id, code=code, libelle=libelle, salaire_minimum=mensuel,
                description="Grille Convention Pétrole (montants à actualiser)"))
            ajout += 1
    if t.convention != "PETROLE":
        t.convention = "PETROLE"
    db.session.commit()
    msg = f"Grille Pétrole importée : {ajout} catégorie(s) ajoutée(s), {maj} mise(s) à jour."
    if plancher_applique:
        msg += (f" ⚠️ Certains montants de 1983 étaient sous le SMIG "
                f"({int(SMIG_GABON):,} FCFA) et ont été relevés au plancher légal — "
                f"actualisez-les selon votre grille interne.").replace(",", " ")
    flash(msg, "success")
    return redirect(url_for("tenant.parametres"))


@bp.route("/langue/<lang>")
def changer_langue(lang):
    """Change la langue de l'interface — accessible depuis n'importe quelle page."""
    set_language(lang)
    # Sauvegarder sur le tenant si connecté
    if current_user.is_authenticated and not current_user.is_super_admin:
        t = get_tenant()
        if t and lang in SUPPORTED_LANGUAGES:
            t.langue = lang
            db.session.commit()
    # Rediriger vers la page précédente
    return redirect(request.referrer or url_for("tenant.dashboard"))


# ══════════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL — Journal des actions
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/audit")
@tenant_required
def audit_trail():
    """
    Page du journal d'audit — visible par l'admin du tenant.
    Affiche qui a fait quoi et quand.
    """
    t = get_tenant()
    if not current_user.is_tenant_admin:
        flash("Accès réservé à l'administrateur.", "error")
        return redirect(url_for("tenant.dashboard"))

    page      = request.args.get("page", 1, type=int)
    action    = request.args.get("action", "")
    entite    = request.args.get("entite", "")
    user_id   = request.args.get("user_id", type=int)
    recherche = request.args.get("q", "").strip()
    d_debut   = _parse_date(request.args.get("date_debut", ""))
    d_fin     = _parse_date(request.args.get("date_fin", ""))
    per_page  = 50

    # La date de fin est inclusive (jusqu'à 23:59:59)
    from datetime import datetime as _dt, time as _time
    d_fin_dt = _dt.combine(d_fin, _time.max) if d_fin else None

    logs, total = get_audit_logs(
        tenant_id  = t.id,
        limit      = per_page,
        offset     = (page - 1) * per_page,
        action     = action or None,
        entite     = entite or None,
        user_id    = user_id or None,
        date_debut = d_debut or None,
        date_fin   = d_fin_dt,
        recherche  = recherche or None,
    )

    # Liste des utilisateurs pour le filtre
    users = Utilisateur.query.filter_by(tenant_id=t.id, actif=True).order_by(Utilisateur.nom).all()

    import math
    nb_pages = math.ceil(total / per_page) if total else 1

    return render_template("tenant/audit_trail.html",
        tenant=t, logs=logs, total=total,
        page=page, nb_pages=nb_pages, per_page=per_page,
        action_filtre=action, entite_filtre=entite, user_filtre=user_id,
        recherche=recherche,
        date_debut=request.args.get("date_debut", ""),
        date_fin=request.args.get("date_fin", ""),
        users=users,
        ACTIONS=["CREATE","UPDATE","DELETE","VALIDATE","CANCEL","PAY",
                 "LOGIN","LOGOUT","EXPORT","IMPORT"],
        ENTITES=["salarie","bulletin","conge","acompte","periode","paiement",
                 "pointage","journalier","avance_journalier","feuille_journalier",
                 "prestataire","facture_prestataire","contrat_prestation",
                 "avance_prestataire","utilisateur","parametres"],
    )


@bp.route("/audit/support")
@tenant_required
def audit_support():
    """Journal des interventions support de l'éditeur (Ameriack).
    Présenté séparément du journal courant : transparence sur les accès
    de support, sans parasiter le suivi quotidien du client."""
    t = get_tenant()
    if not current_user.is_tenant_admin:
        flash("Accès réservé à l'administrateur.", "error")
        return redirect(url_for("tenant.dashboard"))

    from audit import get_audit_logs_support
    page = request.args.get("page", 1, type=int)
    per_page = 50
    logs, total = get_audit_logs_support(
        tenant_id=t.id, limit=per_page, offset=(page - 1) * per_page)

    import math
    nb_pages = math.ceil(total / per_page) if total else 1

    return render_template("tenant/audit_support.html",
        tenant=t, logs=logs, total=total,
        page=page, nb_pages=nb_pages, per_page=per_page)


@bp.route("/audit/export")
@tenant_required
def audit_export():
    """Export CSV du journal d'audit."""
    t = get_tenant()
    if not current_user.is_tenant_admin:
        flash("Accès réservé à l'administrateur.", "error")
        return redirect(url_for("tenant.audit_trail"))

    from datetime import datetime as _dt, time as _time
    d_debut = _parse_date(request.args.get("date_debut", ""))
    d_fin   = _parse_date(request.args.get("date_fin", ""))
    logs, _ = get_audit_logs(
        tenant_id=t.id, limit=5000,
        action=request.args.get("action") or None,
        entite=request.args.get("entite") or None,
        user_id=request.args.get("user_id", type=int) or None,
        recherche=request.args.get("q", "").strip() or None,
        date_debut=d_debut or None,
        date_fin=(_dt.combine(d_fin, _time.max) if d_fin else None))

    import csv, io as _io
    output = _io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Date","Utilisateur","Rôle","Action","Objet","ID","Description","IP"])
    for l in logs:
        writer.writerow([
            l.date_action.strftime("%d/%m/%Y %H:%M:%S") if l.date_action else "",
            csv_safe(l.user.nom_complet if l.user else "Système"),
            l.user.role_label  if l.user else "—",
            csv_safe(l.action), csv_safe(l.entite or ""), l.entite_id or "",
            csv_safe(l.description or ""), l.ip_address or "",
        ])

    log_action("EXPORT", "audit", None, "Export CSV journal d'audit")
    db.session.commit()

    return send_file(
        io.BytesIO(("\ufeff" + output.getvalue()).encode("utf-8")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"audit_{t.sigle or t.id}_{datetime.now().strftime('%Y%m%d')}.csv",
    )


@bp.route("/parametres/demande-changement-plan", methods=["POST"])
@login_required
def demande_changement_plan():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.is_tenant_admin: abort(403)
    plan_souhaite_id = request.form.get("plan_id", type=int)
    motif = request.form.get("motif", "").strip()
    plan_souhaite = db.session.get(Plan, plan_souhaite_id) if plan_souhaite_id else None
    if not plan_souhaite:
        flash("Plan invalide.", "error")
        return redirect(url_for("tenant.parametres"))
    # Enregistrer la demande dans les notes + changer statut
    note_demande = (
        f"[DEMANDE CHANGEMENT PLAN — {datetime.now().strftime('%d/%m/%Y %H:%M')}] "
        f"Plan souhaité : {plan_souhaite.nom} ({int(plan_souhaite.prix_mensuel):,} FCFA/mois). "
        f"Motif : {motif or 'Non précisé'}. "
        f"Demandé par : {current_user.nom_complet} ({current_user.email})."
    )
    # Ajouter à la suite des notes existantes
    t.notes = (t.notes or "") + ("\n" if t.notes else "") + note_demande
    t.statut = "PAIEMENT_EN_ATTENTE"
    db.session.commit()
    flash(f"Demande de passage au plan « {plan_souhaite.nom} » enregistrée. L'équipe PaieGabon vous contactera sous 24h pour finaliser.", "success")
    return redirect(url_for("tenant.parametres"))

@bp.route("/parametres/annuler-abonnement", methods=["POST"])
@login_required
def annuler_abonnement():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.is_tenant_admin: abort(403)
    motif = request.form.get("motif", "").strip()
    t.statut = "ANNULATION_DEMANDEE"
    t.notes = f"Annulation demandée le {datetime.now().strftime('%d/%m/%Y')}. Motif: {motif}"
    db.session.commit()
    flash("Demande d annulation enregistrée. L equipe PaieGabon vous contactera sous 48h.", "success")
    return redirect(url_for("tenant.parametres"))

# ── Utilisateurs ──────────────────────────────────────────────────────────────
@bp.route("/utilisateurs")
@login_required
def utilisateurs():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    liste = Utilisateur.query.filter_by(tenant_id=t.id).order_by(Utilisateur.nom).all()
    return render_template("tenant/utilisateurs.html", tenant=t, utilisateurs=liste, users=liste)

@bp.route("/utilisateurs/nouveau", methods=["GET","POST"])
@login_required
def utilisateur_nouveau():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.is_tenant_admin:
        flash("Réservé à l administrateur.", "error")
        return redirect(url_for("tenant.utilisateurs"))
    # ── Vérifier la limite dès le GET (bloquer l'accès au formulaire) ───────
    if t.plan and t.plan.max_utilisateurs:
        nb_actuel = Utilisateur.query.filter_by(tenant_id=t.id, actif=True).count()
        if nb_actuel >= t.plan.max_utilisateurs:
            flash(
                f"Limite atteinte — Plan « {t.plan.nom} » : "
                f"{t.plan.max_utilisateurs} utilisateur(s) maximum "
                f"(vous en avez {nb_actuel}). "
                f"Passez au plan supérieur pour en ajouter d'autres.",
                "error"
            )
            return redirect(url_for("tenant.utilisateurs"))

    if request.method == "GET":
        nb_utilisateurs = Utilisateur.query.filter_by(tenant_id=t.id, actif=True).count()
        return render_template("tenant/utilisateur_form.html", tenant=t,
            nb_utilisateurs=nb_utilisateurs)
    email = request.form.get("email", "").strip().lower()
    nom = request.form.get("nom", "").strip().upper()
    prenom = request.form.get("prenom", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "GESTIONNAIRE").strip().upper()
    # Liste blanche : empêche l'attribution de SUPER_ADMIN ou d'un rôle inconnu.
    if role not in ROLES_TENANT_AUTORISES:
        flash("Rôle invalide.", "error")
        return redirect(url_for("tenant.utilisateurs"))
    if not email or not nom or not password:
        flash("Veuillez remplir tous les champs.", "error")
        return render_template("tenant/utilisateur_form.html", tenant=t)
    if Utilisateur.query.filter_by(email=email).first():
        flash("Email déjà utilisé.", "error")
        return render_template("tenant/utilisateur_form.html", tenant=t)
    u = Utilisateur(nom=nom, prenom=prenom, email=email, role=role, tenant_id=t.id, actif=True)
    u.set_password(password)
    db.session.add(u); db.session.commit()
    log_action("CREATE", "utilisateur", u.id,
               f"Création utilisateur {u.nom_complet} ({u.role})")
    db.session.commit()
    flash(f"Utilisateur {u.nom_complet} créé.", "success")
    return redirect(url_for("tenant.utilisateurs"))

@bp.route("/utilisateurs/<int:id>/toggle", methods=["POST"])
@login_required
def utilisateur_toggle(id):
    """Activer / désactiver un utilisateur."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.is_tenant_admin:
        flash("Réservé à l'administrateur.", "error")
        return redirect(url_for("tenant.utilisateurs"))
    u = Utilisateur.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if u.id == current_user.id:
        flash("Vous ne pouvez pas vous désactiver vous-même.", "error")
        return redirect(url_for("tenant.utilisateurs"))
    u.actif = not u.actif
    db.session.commit()
    etat = "activé" if u.actif else "désactivé"
    log_action("UPDATE", "utilisateur", u.id,
               f"Utilisateur {u.nom_complet} {etat} par {current_user.nom_complet}")
    db.session.commit()
    flash(f"Utilisateur {u.nom_complet} {etat}.", "success")
    return redirect(url_for("tenant.utilisateurs"))


@bp.route("/utilisateurs/<int:id>/modifier", methods=["GET","POST"])
@login_required
def utilisateur_modifier(id):
    """Modifier le rôle et les infos d'un utilisateur."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.is_tenant_admin:
        flash("Réservé à l'administrateur.", "error")
        return redirect(url_for("tenant.utilisateurs"))

    u = Utilisateur.query.filter_by(id=id, tenant_id=t.id).first_or_404()

    if request.method == "POST":
        ancien_role = u.role
        nouveau_role = request.form.get("role", u.role).strip().upper()
        # Liste blanche : empêche l'escalade vers SUPER_ADMIN ou un rôle inconnu.
        if nouveau_role not in ROLES_TENANT_AUTORISES:
            flash("Rôle invalide.", "error")
            return redirect(url_for("tenant.utilisateurs"))

        # Empêcher de retirer son propre rôle admin
        if u.id == current_user.id and nouveau_role != "TENANT_ADMIN":
            flash("Vous ne pouvez pas changer votre propre rôle.", "error")
            return redirect(url_for("tenant.utilisateurs"))

        u.nom    = request.form.get("nom", u.nom).strip().upper()
        u.prenom = request.form.get("prenom", u.prenom).strip()
        u.role   = nouveau_role

        # Changer le mot de passe si fourni
        nouveau_mdp = request.form.get("nouveau_mdp", "").strip()
        if nouveau_mdp:
            if len(nouveau_mdp) < 8:
                flash("Le mot de passe doit faire au moins 8 caractères.", "error")
                return render_template("tenant/utilisateur_form.html",
                                       utilisateur=u, tenant=t, mode="modifier")
            u.set_password(nouveau_mdp)

        db.session.commit()
        log_action("UPDATE", "utilisateur", u.id,
                   f"Modification {u.nom_complet} — rôle : {ancien_role} → {nouveau_role}")
        db.session.commit()
        flash(f"Utilisateur {u.nom_complet} mis à jour.", "success")
        return redirect(url_for("tenant.utilisateurs"))

    return render_template("tenant/utilisateur_form.html",
                           utilisateur=u, tenant=t, mode="modifier")


@bp.route("/utilisateurs/<int:id>/supprimer", methods=["POST"])
@login_required
def utilisateur_supprimer(id):
    """
    Supprime définitivement un utilisateur.
    Règles :
      - Seul l'admin du tenant peut supprimer
      - On ne peut pas se supprimer soi-même
      - On ne peut pas supprimer le dernier admin
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.is_tenant_admin:
        flash("Réservé à l'administrateur.", "error")
        return redirect(url_for("tenant.utilisateurs"))

    u = Utilisateur.query.filter_by(id=id, tenant_id=t.id).first_or_404()

    # Règle 1 : pas de suicide
    if u.id == current_user.id:
        flash("Vous ne pouvez pas supprimer votre propre compte.", "error")
        return redirect(url_for("tenant.utilisateurs"))

    # Règle 2 : conserver au moins un admin actif
    if u.role == "TENANT_ADMIN":
        nb_admins = Utilisateur.query.filter_by(
            tenant_id=t.id, role="TENANT_ADMIN", actif=True
        ).filter(Utilisateur.id != u.id).count()
        if nb_admins == 0:
            flash("Impossible de supprimer le seul administrateur actif du compte. "
                  "Activez ou désignez d'abord un autre administrateur.", "error")
            return redirect(url_for("tenant.utilisateurs"))

    nom_sauvegarde = u.nom_complet
    log_action("DELETE", "utilisateur", u.id,
               f"Suppression définitive de {nom_sauvegarde} ({u.role_label})")
    db.session.delete(u)
    db.session.commit()
    flash(f"Utilisateur {nom_sauvegarde} supprimé définitivement.", "success")
    return redirect(url_for("tenant.utilisateurs"))

# ── Journaliers ───────────────────────────────────────────────────────────────
# ── Acomptes ──────────────────────────────────────────────────────────────────
def _doc_response(pdf_bytes, nom_fichier):
    """Helper : renvoie un PDF en téléchargement."""
    from flask import Response
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"',
                             "Content-Length": str(len(pdf_bytes))})


@bp.route("/api/conges/jours-acquis/<int:sal_id>")
@login_required
def api_jours_acquis(sal_id):
    """Retourne les jours acquis d'un salarié (JSON)."""
    t = get_tenant()
    if not t: return jsonify({"error": "non authentifié"}), 401
    s = Salarie.query.filter_by(id=sal_id, tenant_id=t.id).first_or_404()

    from conges_avance import calculer_jours_acquis
    result = calculer_jours_acquis(s.date_embauche, convention=getattr(s.tenant, "convention", None))
    # Sérialiser les dates
    for k in ["periode_debut","periode_fin"]:
        if result.get(k):
            result[k] = str(result[k])
    return jsonify(result)


# ── Congés ────────────────────────────────────────────────────────────────────
@bp.route("/prime-fin-annee")
@login_required
def prime_fin_annee():
    """Calcul de la prime de fin d'année (convention Hôtellerie, Art. 50).
    Pour chaque salarié : moyenne mensuelle des salaires de base de l'année ×
    30 %, au prorata des mois de présence, si ≥ 6 mois de présence effective.
    Page de consultation/aide au versement (aucune écriture automatique)."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    from datetime import datetime as _dt, date as _date
    from convention_hotellerie import calculer_prime_fin_annee_hotellerie
    from convention_bois import calculer_gratification_fin_annee_bois
    annee = request.args.get("annee", _dt.now().year, type=int)
    conv = (t.convention or "").upper()

    bulletins = (BulletinPaie.query
                 .join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
                 .filter(BulletinPaie.tenant_id == t.id, PeriodePaie.annee == annee)
                 .all())
    par_salarie = {}
    for b in bulletins:
        par_salarie.setdefault(b.salarie_id, []).append(b)

    lignes = []
    total = 0.0
    for sal_id, buls in par_salarie.items():
        sal = db.session.get(Salarie, sal_id)
        if not sal:
            continue
        mois_presence = len(buls)
        bases = [float(x.salaire_base or 0) for x in buls if float(x.salaire_base or 0) > 0]
        moyenne = round(sum(bases) / len(bases), 2) if bases else 0.0
        # Ancienneté (années) au 31/12 de l'année considérée
        anc = 0.0
        if sal.date_embauche:
            anc = max(0, (_date(annee, 12, 31) - sal.date_embauche).days / 365.0)
        if conv == "BOIS":
            # Gratification Art. 52 : 25 %, requiert ≥ 1 an de présence continue
            prime = calculer_gratification_fin_annee_bois(moyenne, mois_presence, anc)
            eligible = anc >= 1 and prime > 0
        elif conv == "MINIER":
            # 13ᵉ mois Art. 55 : 1 mois de salaire de base + prime d'ancienneté,
            # au prorata du temps de présence ; requiert ≥ 1 an de présence.
            from convention_minier import (calculer_treizieme_mois_minier,
                                           calculer_prime_anciennete_minier)
            pa = calculer_prime_anciennete_minier(moyenne, int(anc))
            prime = calculer_treizieme_mois_minier(moyenne, pa, mois_presence)
            eligible = anc >= 1 and prime > 0
        elif conv == "FORET":
            # Gratification Art. 51 : discrétionnaire, requiert ≥ 2 ans
            # d'ancienneté (essai compris) ; le montant relève de l'appréciation
            # de l'employeur (aucun taux conventionnel) → à saisir manuellement.
            prime = 0.0
            eligible = anc >= 2
        else:
            # Hôtellerie Art. 50 : 30 %, requiert ≥ 6 mois de présence
            prime = calculer_prime_fin_annee_hotellerie(moyenne, mois_presence)
            eligible = mois_presence >= 6 and prime > 0
        lignes.append({
            "salarie": sal, "mois_presence": mois_presence,
            "moyenne_base": moyenne, "prime": prime, "eligible": eligible,
        })
        total += prime
    lignes.sort(key=lambda x: x["salarie"].nom or "")

    return render_template("tenant/prime_fin_annee.html",
        tenant=t, annee=annee, lignes=lignes, total=round(total, 2), conv=conv,
        annees=list(range(_dt.now().year, _dt.now().year - 4, -1)))


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


@bp.route("/api/travailleur/stats-sans-site")
@tenant_required
def api_stats_sans_site():
    t = get_tenant()
    # Salariés actifs sans affectation active
    sal_ids_affectes = {a.salarie_id for a in 
        AffectationSite.query.filter_by(tenant_id=t.id, actif=True).all() if a.salarie_id}
    jour_ids_affectes = {a.journalier_id for a in 
        AffectationSite.query.filter_by(tenant_id=t.id, actif=True).all() if a.journalier_id}
    nb_sal  = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").count()
    nb_jour = Journalier.query.filter_by(tenant_id=t.id, statut="ACTIF").count()
    sans_site = (nb_sal - len(sal_ids_affectes)) + (nb_jour - len(jour_ids_affectes))
    return jsonify({"total": max(0, sans_site)})

# ══════════════════════════════════════════════════════════════════════════════
# ── SITES & AFFECTATIONS ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/composants")
@tenant_required
def composants():
    t = get_tenant()
    composants_list = ComposantPaie.query.filter_by(tenant_id=t.id).order_by(
        ComposantPaie.ordre, ComposantPaie.libelle).all()
    return render_template("tenant/composants.html", tenant=t, composants=composants_list)


@bp.route("/composants/nouveau", methods=["GET", "POST"])
@tenant_required
def composant_nouveau():
    t = get_tenant()
    if not current_user.can_edit: abort(403)
    if request.method == "POST":
        libelle = request.form.get("libelle", "").strip()
        if not libelle:
            flash("Le libellé est obligatoire.", "error")
            return render_template("tenant/composant_form.html", tenant=t, composant=None)
        c = ComposantPaie(
            tenant_id     = t.id,
            libelle       = libelle,
            sens          = "RETENUE" if request.form.get("sens") == "RETENUE" else "GAIN",
            soumis_cnss   = request.form.get("soumis_cnss")   == "on",
            soumis_cnamgs = request.form.get("soumis_cnamgs") == "on",
            soumis_irpp   = request.form.get("soumis_irpp")   == "on",
            entre_dans_brut = request.form.get("entre_dans_brut") == "on",
            position      = "HAUT" if request.form.get("position") == "HAUT" else "BAS",
            ordre         = request.form.get("ordre", type=int) or 0,
            actif         = True,
        )
        db.session.add(c)
        db.session.commit()
        flash(f"Composant « {c.libelle} » créé.", "success")
        return redirect(url_for("tenant.composants"))
    return render_template("tenant/composant_form.html", tenant=t, composant=None)


@bp.route("/composants/<int:id>/modifier", methods=["GET", "POST"])
@tenant_required
def composant_modifier(id):
    t = get_tenant()
    if not current_user.can_edit: abort(403)
    c = ComposantPaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if request.method == "POST":
        libelle = request.form.get("libelle", "").strip()
        if not libelle:
            flash("Le libellé est obligatoire.", "error")
            return render_template("tenant/composant_form.html", tenant=t, composant=c)
        c.libelle       = libelle
        c.sens          = "RETENUE" if request.form.get("sens") == "RETENUE" else "GAIN"
        c.soumis_cnss   = request.form.get("soumis_cnss")   == "on"
        c.soumis_cnamgs = request.form.get("soumis_cnamgs") == "on"
        c.soumis_irpp   = request.form.get("soumis_irpp")   == "on"
        c.entre_dans_brut = request.form.get("entre_dans_brut") == "on"
        c.position      = "HAUT" if request.form.get("position") == "HAUT" else "BAS"
        c.ordre         = request.form.get("ordre", type=int) or 0
        db.session.commit()
        flash(f"Composant « {c.libelle} » modifié.", "success")
        return redirect(url_for("tenant.composants"))
    return render_template("tenant/composant_form.html", tenant=t, composant=c)


@bp.route("/composants/<int:id>/toggle", methods=["POST"])
@tenant_required
def composant_toggle(id):
    t = get_tenant()
    if not current_user.can_edit: abort(403)
    c = ComposantPaie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    c.actif = not c.actif
    db.session.commit()
    flash(f"Composant « {c.libelle} » {'activé' if c.actif else 'désactivé'}.", "success")
    return redirect(url_for("tenant.composants"))


@bp.route("/api/travailleur/<string:type>/<int:id>/site")
@tenant_required
def api_travailleur_site(type, id):
    """API : retourne le site actuel d'un travailleur."""
    t = get_tenant()
    if type == "salarie":
        a = AffectationSite.query.filter_by(
            salarie_id=id, tenant_id=t.id, actif=True).first()
    else:
        a = AffectationSite.query.filter_by(
            journalier_id=id, tenant_id=t.id, actif=True).first()
    if a:
        return jsonify({"site_id": a.site_id, "site_nom": a.site.nom,
                        "date_debut": str(a.date_debut)})
    return jsonify({"site_id": None, "site_nom": None})


@bp.route("/bulletins/export/<int:periode_id>")
@tenant_required
def export_journal(periode_id):
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Font, PatternFill, Alignment
    t=get_tenant()
    p=PeriodePaie.query.filter_by(id=periode_id,tenant_id=t.id).first_or_404()
    buls=BulletinPaie.query.filter_by(periode_id=periode_id,tenant_id=t.id).join(Salarie).order_by(Salarie.nom).all()
    wb=Workbook(); ws=wb.active; ws.title=f"Journal {p.libelle_complet}"
    ws.merge_cells("A1:R1"); ws["A1"]=f"JOURNAL DE PAIE — {p.libelle_complet} — {t.denomination}"
    ws["A1"].font=Font(bold=True,size=13); ws["A1"].alignment=Alignment(horizontal="center")
    hdrs=["Matricule","Nom","Prénom","Emploi","Cat.","Base","Brut","CNSS Sal.","CNAMGS Sal.","TCS","IRPP","Net","Net à Payer","CNSS Pat.","CNAMGS Pat.","FNH","CFP","Statut"]
    for col,h in enumerate(hdrs,1):
        c=ws.cell(row=3,column=col,value=h); c.font=Font(bold=True,color="FFFFFF")
        c.fill=PatternFill("solid",fgColor="1a2332"); c.alignment=Alignment(horizontal="center")
    for row,b in enumerate(buls,4):
        s=b.salarie
        vals=[s.matricule,s.nom,s.prenom,s.emploi,s.categorie.code if s.categorie else "",
              float(b.salaire_base or 0),float(b.salaire_brut or 0),float(b.cnss_salarie or 0),
              float(b.cnamgs_salarie or 0),float(b.tcs or 0),float(b.irpp or 0),
              float(b.salaire_net or 0),float(b.net_a_payer or 0),float(b.cnss_patronale or 0),
              float(b.cnamgs_patronale or 0),float(b.fnh or 0),float(b.cfp or 0),b.statut]
        for col,v in enumerate(vals,1):
            cell=ws.cell(row=row,column=col,value=csv_safe(v) if isinstance(v,str) else v)
            if isinstance(v,float): cell.number_format='#,##0'
            if row%2==0: cell.fill=PatternFill("solid",fgColor="F5F5F5")
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return send_file(out,mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,download_name=f"Journal_{p.libelle_mois}_{p.annee}_{t.slug}.xlsx")

@bp.route("/api/calculer-bulletin", methods=["POST"])
@login_required
def api_calculer():
    try:
        t = get_tenant()
        data = request.get_json() or {}
        sid = data.pop("salarie_id", None)
        nb_parts = 1.0
        if sid and t:
            s = Salarie.query.filter_by(id=sid, tenant_id=t.id).first()
            if s: nb_parts = float(s.nombre_parts or 1)
        mois  = data.pop("mois_periode", None)
        annee = data.pop("annee_periode", None)
        # ── Ancienneté du salarié (pour la prime d'ancienneté automatique) ──
        # Référence : fin de la période de paie si connue, sinon aujourd'hui.
        if sid and t:
            s_anc = Salarie.query.filter_by(id=sid, tenant_id=t.id).first()
            if s_anc and s_anc.date_embauche:
                from datetime import date as _date
                try:
                    if mois and annee:
                        m, a = int(mois), int(annee)
                        ref = (_date(a + 1, 1, 1) if m == 12 else _date(a, m + 1, 1))
                        ref = ref - timedelta(days=1)      # dernier jour du mois
                    else:
                        ref = _date.today()
                except (TypeError, ValueError):
                    ref = _date.today()
                data["anciennete_annees"] = max(0, (ref - s_anc.date_embauche).days // 365)
        total_acomptes = 0.0
        if sid and t and mois and annee:
            total_acomptes = float(db.session.query(db.func.sum(Acompte.montant))
                .filter_by(tenant_id=t.id, salarie_id=int(sid), mois=int(mois),
                           annee=int(annee), statut="EN_ATTENTE").scalar() or 0)
        if total_acomptes > 0:
            data["acompte"] = max(float(data.get("acompte", 0)), total_acomptes)
        # Composants personnalisés (aperçu temps réel) : lus depuis composant_<id>
        if t:
            comps_live = []
            for comp in ComposantPaie.query.filter_by(tenant_id=t.id, actif=True).all():
                try:
                    montant = float(data.get(f"composant_{comp.id}") or 0)
                except (TypeError, ValueError):
                    montant = 0.0
                if montant:
                    comps_live.append({
                        "libelle": comp.libelle, "sens": comp.sens, "montant": montant,
                        "soumis_cnss": comp.soumis_cnss, "soumis_cnamgs": comp.soumis_cnamgs,
                        "soumis_irpp": comp.soumis_irpp,
                        "entre_dans_brut": comp.entre_dans_brut, "position": comp.position})
            data["composants"] = comps_live
        if t:
            data["convention"] = t.convention
            data["config_rubriques"] = _config_rubriques_dict(t.id)
        res = calculer_bulletin(data, nb_parts=nb_parts)
        res["acompte_auto"] = total_acomptes
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/salarie/<int:id>/contrat")
@login_required
def api_contrat(id):
    t=get_tenant()
    s=Salarie.query.filter_by(id=id,tenant_id=t.id).first()
    if not s: return jsonify({})
    c=Contrat.query.filter_by(salarie_id=id,tenant_id=t.id,actif=True).first()
    base={"nom":s.nom_complet,"poste":s.emploi,"matricule":s.matricule,"nombre_parts":float(s.nombre_parts or 1)}
    if c:
        base["salaire_base"]=float(c.salaire_base); base["poste"]=c.poste or s.emploi
        try:
            import json as _json
            base["elements"] = _json.loads(c.elements_recurrents) if c.elements_recurrents else {}
        except Exception:
            base["elements"] = {}
    return jsonify(base)

@bp.route("/api/salarie/<int:id>/pointage-mois")
@login_required
def api_pointage_mois(id):
    """Retourne le cumul des heures du pointage pour un salarié sur un mois donné."""
    t = get_tenant()
    if not t: return jsonify({})
    mois  = request.args.get("mois",  type=int)
    annee = request.args.get("annee", type=int)
    if not mois or not annee:
        return jsonify({"erreur": "mois et annee requis"})
    import calendar
    dernier_jour = calendar.monthrange(annee, mois)[1]
    debut = date(annee, mois, 1)
    fin   = date(annee, mois, dernier_jour)
    pts = Pointage.query.filter_by(tenant_id=t.id, salarie_id=id)        .filter(Pointage.date_pointage >= debut, Pointage.date_pointage <= fin).all()
    pts_travailles = [p for p in pts if p.present and not p.absent]
    if not pts_travailles:
        return jsonify({"nb_jours": 0, "nb_absences": 0,
            "heures_sup_10": 0, "heures_sup_30": 0, "heures_sup_40": 0, "heures_sup_70": 0,
            "heures_normales_total": 0, "total_sup": 0,
            "message": "Aucun pointage pour cette période"})
    nb_jours = len(pts_travailles)

    if (t.convention or "").upper() in ("BTP", "PETROLE", "INDUSTRIE", "AERIEN", "MINIER"):
        # Ventilation réglementaire : semaine par semaine, ligne par ligne
        from calculs_paie import ventiler_heures_mois, pointage_vers_jours
        _feries = set()
        for _an in {p.date.year for p in pts if getattr(p, "date", None)}:
            _feries |= set(jours_feries_annee(_an).keys())
        v = ventiler_heures_mois(t.convention, pointage_vers_jours(pts), feries=_feries, seuil_normales=t.seuil_hs)
        heures_normales = v["heures_normales"]
        heures_sup_10   = v["heures_sup_10"]
        heures_sup_30   = v["heures_sup_30"]
        heures_sup_30b  = v.get("heures_sup_30b", 0.0)
        heures_sup_40   = v["heures_sup_40"]
        heures_sup_70   = v["heures_sup_70"]
        heures_sup_fj   = v.get("heures_sup_fj", 0.0)
        heures_sup_fn   = v.get("heures_sup_fn", 0.0)
        detail_semaines = v["detail_semaines"]
    else:
        # Autres conventions : cumul direct des colonnes déjà ventilées par jour
        heures_normales = sum(float(p.heures_normales or 8) for p in pts_travailles)
        heures_sup_10   = sum(float(p.heures_sup_10 or 0) for p in pts_travailles)
        heures_sup_30   = sum(float(p.heures_sup_30 or 0) for p in pts_travailles)
        heures_sup_30b  = sum(float(getattr(p, "heures_sup_30b", 0) or 0) for p in pts_travailles)
        heures_sup_40   = sum(float(p.heures_sup_40 or 0) for p in pts_travailles)
        heures_sup_70   = sum(float(p.heures_sup_70 or 0) for p in pts_travailles)
        heures_sup_fj   = 0.0
        heures_sup_fn   = 0.0
        detail_semaines = []
    pts_absents = Pointage.query.filter_by(tenant_id=t.id, salarie_id=id)        .filter(Pointage.date_pointage >= debut, Pointage.date_pointage <= fin,
                Pointage.absent == True).all()
    return jsonify({
        "nb_jours":              nb_jours,
        "nb_absences":           len(pts_absents),
        "heures_normales_total": round(heures_normales, 2),
        "heures_sup_10":         round(heures_sup_10, 2),
        "heures_sup_30":         round(heures_sup_30, 2),
        "heures_sup_30b":        round(heures_sup_30b, 2),
        "heures_sup_40":         round(heures_sup_40, 2),
        "heures_sup_70":         round(heures_sup_70, 2),
        "heures_sup_fj":         round(heures_sup_fj, 2),
        "heures_sup_fn":         round(heures_sup_fn, 2),
        "total_sup":             round(heures_sup_10+heures_sup_30+heures_sup_30b+heures_sup_40+heures_sup_70+heures_sup_fj+heures_sup_fn, 2),
        "detail_semaines":       detail_semaines,
        "message":               f"{nb_jours} jour(s) pointé(s) sur {dernier_jour}"
    })

@bp.route("/api/cache/clear", methods=["POST"])
@login_required
def api_cache_clear():
    """Vider le cache du dashboard (bouton rafraîchir)."""
    t = get_tenant()
    if not t: return jsonify({"ok": False})
    _cache_delete(f"{t.id}:")
    return jsonify({"ok": True, "msg": "Cache vidé"})


@bp.route("/api/jour-ferie")
@login_required
def api_jour_ferie():
    """
    Indique si une date est un jour férié / dimanche, pour pré-remplir
    automatiquement le type de jour dans le pointage.
    Param : date (format YYYY-MM-DD)
    Retour : {type_jour, est_ferie, nom, est_dimanche}
    """
    t = get_tenant()
    if not t:
        return jsonify({"erreur": "non connecté"}), 401
    date_str = request.args.get("date", "")
    d = _parse_date(date_str)
    if not d:
        return jsonify({"erreur": "date invalide"}), 400
    nom = nom_jour_ferie(d)
    return jsonify({
        "date":         d.strftime("%Y-%m-%d"),
        "type_jour":    type_jour_auto(d),
        "est_ferie":    nom is not None,
        "nom":          nom,
        "est_dimanche": d.weekday() == 6,
    })


@bp.route("/notifications")
@login_required
def notifications_page():
    """Page listant tous les rappels et notifications."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    notifs = get_notifications(t, db, _NOTIF_MODELS)
    # Grouper par catégorie pour l'affichage
    par_categorie = {}
    for n in notifs:
        par_categorie.setdefault(n["categorie"], []).append(n)
    labels_cat = {
        "contrat": "Contrats", "conge": "Congés",
        "facture": "Factures prestataires", "periode": "Périodes de paie",
    }
    nb_critiques = sum(1 for n in notifs if n["type"] == "danger")
    return render_template("tenant/notifications.html",
        tenant=t, notifs=notifs, par_categorie=par_categorie,
        labels_cat=labels_cat, total=len(notifs), nb_critiques=nb_critiques)


@bp.route("/api/notifications/count")
@login_required
def api_notifications_count():
    """Compteur pour la cloche (rafraîchi en arrière-plan)."""
    t = get_tenant()
    if not t:
        return jsonify({"total": 0, "critiques": 0})
    total, critiques = compter_notifications(t, db, _NOTIF_MODELS)
    return jsonify({"total": total, "critiques": critiques})


@bp.route("/jours-feries")
@login_required
def jours_feries_page():
    """Affiche le calendrier des jours fériés gabonais pour une année."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    annee = request.args.get("annee", datetime.now().year, type=int)
    feries = sorted(jours_feries_annee(annee).items())
    jours_sem = ["Lundi", "Mardi", "Mercredi", "Jeudi",
                 "Vendredi", "Samedi", "Dimanche"]
    feries_fmt = [{
        "date":      d,
        "nom":       nom,
        "jour_sem":  jours_sem[d.weekday()],
        "est_we":    d.weekday() >= 5,
    } for d, nom in feries]
    annees_dispo = list(range(datetime.now().year - 1, datetime.now().year + 3))
    return render_template("tenant/jours_feries.html",
                           tenant=t, annee=annee, feries=feries_fmt,
                           annees_dispo=annees_dispo)


@bp.route("/api/semaine-btp")
@login_required
def api_semaine_btp():
    """
    Calcule la distribution BTP des heures pour un travailleur sur une semaine.
    Params : type (sal|jour), id, date (n'importe quel jour de la semaine)
    """
    t = get_tenant()
    if not t: return jsonify({"erreur": "non connecté"})

    type_w    = request.args.get("type", "sal")
    worker_id = request.args.get("id", type=int)
    date_str  = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))

    try:
        date_ref = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        date_ref = datetime.now().date()

    lundi  = date_ref - timedelta(days=date_ref.weekday())
    samedi = lundi + timedelta(days=5)

    if type_w == "sal":
        pts = Pointage.query.filter_by(tenant_id=t.id, salarie_id=worker_id)            .filter(Pointage.date_pointage >= lundi,
                    Pointage.date_pointage <= samedi,
                    Pointage.present == True).all()
    else:
        pts = Pointage.query.filter_by(tenant_id=t.id, journalier_id=worker_id)            .filter(Pointage.date_pointage >= lundi,
                    Pointage.date_pointage <= samedi,
                    Pointage.present == True).all()

    if not pts:
        return jsonify({
            "semaine": f"{lundi.strftime('%d/%m')} → {samedi.strftime('%d/%m/%Y')}",
            "nb_jours": 0, "heures_normales": 0,
            "heures_sup_10": 0, "heures_sup_30": 0,
            "heures_sup_40": 0, "heures_sup_70": 0,
            "message": "Aucun pointage cette semaine"
        })

    from calculs_paie import distribuer_heures_semaine_btp
    jours_data = []
    for p in pts:
        h_norm = float(p.heures_normales or 0)
        if type_w == "sal":
            h_nuit = float(p.heures_sup_40 or 0)
        else:
            h_nuit = 0
        jours_data.append({
            "heures_normales": h_norm,
            "heures_sup_nuit": h_nuit,
            "type_jour": p.type_jour or "NORMAL"
        })

    dist = distribuer_heures_semaine_btp(jours_data, seuil_normales=t.seuil_hs)
    dist["semaine"]  = f"{lundi.strftime('%d/%m')} → {samedi.strftime('%d/%m/%Y')}"
    dist["nb_jours"] = len(pts)
    dist["jours_detail"] = [
        {
            "date":     str(p.date_pointage),
            "jour_fr":  ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"][p.date_pointage.weekday()],
            "heures":   float(p.heures_normales or 0),
            "type_jour": p.type_jour or "NORMAL",
        } for p in sorted(pts, key=lambda x: x.date_pointage)
    ]

    # Montants si salaire connu
    salaire_base = request.args.get("salaire", type=float)
    if salaire_base:
        from calculs_paie import calculer_taux_horaire, COEFF_SUP_10, COEFF_SUP_30
        th = calculer_taux_horaire(salaire_base)
        dist["taux_horaire"]    = round(th, 2)
        dist["montant_10"]      = round(dist["heures_sup_10"] * th * COEFF_SUP_10, 2)
        dist["montant_30"]      = round(dist["heures_sup_30"] * th * COEFF_SUP_30, 2)
        dist["montant_total_sup"] = round(dist["montant_10"] + dist["montant_30"]
                                         + dist["heures_sup_40"] * th * 1.40
                                         + dist["heures_sup_70"] * th * 1.70, 2)

    return jsonify(dist)


@bp.route("/api/salarie/<int:id>/acomptes-mois")
@login_required
def api_acomptes_mois(id):
    t = get_tenant()
    mois=request.args.get("mois",type=int); annee=request.args.get("annee",type=int)
    if not t or not mois or not annee: return jsonify({"total":0})
    total = db.session.query(db.func.sum(Acompte.montant))\
            .filter_by(tenant_id=t.id,salarie_id=id,mois=mois,annee=annee,statut="EN_ATTENTE").scalar() or 0
    return jsonify({"total":float(total)})

@bp.route("/api/pointage/semaine")
@login_required
def api_pointage_semaine():
    t = get_tenant()
    if not t: return jsonify({})
    date_str = request.args.get("date")
    try: date_sel = datetime.strptime(date_str, "%Y-%m-%d").date()
    except: date_sel = datetime.now().date()
    lundi=date_sel-timedelta(days=date_sel.weekday()); samedi=lundi+timedelta(days=5)
    pts = Pointage.query.filter_by(tenant_id=t.id).filter(Pointage.date_pointage>=lundi,Pointage.date_pointage<=samedi).all()
    stats={}
    for p in pts:
        key=str(p.date_pointage)
        if key not in stats: stats[key]={"presents":0,"absents":0,"heures":0}
        if p.present: stats[key]["presents"]+=1; stats[key]["heures"]+=p.total_heures
        else: stats[key]["absents"]+=1
    return jsonify(stats)


# ══════════════════════════════════════════════════════════════════════════════
# RAPPORT MENSUEL PAR SITE
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/rapports/mensuel-site")
@login_required
def rapport_mensuel_site():
    """Page rapport mensuel par site : pointage + paie journalier + masse salariale."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    MOIS_NOMS_LONG = ["","Janvier","Février","Mars","Avril","Mai","Juin",
                      "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]

    # Paramètres
    now_d  = datetime.now()
    mois   = request.args.get("mois",  type=int, default=now_d.month)
    annee  = request.args.get("annee", type=int, default=now_d.year)
    site_id= request.args.get("site_id", type=int)

    import calendar
    _, nb_jours_mois = calendar.monthrange(annee, mois)
    mois_debut = date(annee, mois, 1)
    mois_fin   = date(annee, mois, nb_jours_mois)

    sites_list = Site.query.filter_by(tenant_id=t.id, actif=True).order_by(Site.nom).all()
    site_sel   = Site.query.filter_by(id=site_id, tenant_id=t.id).first() if site_id else None

    # Affectations actives pour ce mois
    aff_sal  = {}   # salarie_id  → site_id
    aff_jour = {}   # journalier_id → site_id
    for a in AffectationSite.query.filter_by(tenant_id=t.id).all():
        if a.actif or (a.date_fin and a.date_fin >= mois_debut):
            if a.salarie_id:    aff_sal[a.salarie_id]    = a.site_id
            if a.journalier_id: aff_jour[a.journalier_id] = a.site_id

    def _build_rapport_site(s):
        """Construit le rapport complet pour un site donné."""
        sid = s.id
        # Travailleurs affectés à ce site
        ids_sal  = [k for k,v in aff_sal.items()  if v == sid]
        ids_jour = [k for k,v in aff_jour.items() if v == sid]

        salaries_aff    = Salarie.query.filter(
            Salarie.tenant_id==t.id, Salarie.id.in_(ids_sal)
        ).order_by(Salarie.nom).all() if ids_sal else []
        journaliers_aff = Journalier.query.filter(
            Journalier.tenant_id==t.id, Journalier.id.in_(ids_jour)
        ).order_by(Journalier.nom).all() if ids_jour else []

        # ── Pointage mensuel ─────────────────────────────────────────────────
        pts_sal  = Pointage.query.filter_by(tenant_id=t.id)            .filter(Pointage.salarie_id.in_(ids_sal),
                    Pointage.date_pointage >= mois_debut,
                    Pointage.date_pointage <= mois_fin).all() if ids_sal else []
        pts_jour = Pointage.query.filter_by(tenant_id=t.id)            .filter(Pointage.journalier_id.in_(ids_jour),
                    Pointage.date_pointage >= mois_debut,
                    Pointage.date_pointage <= mois_fin).all() if ids_jour else []
        pts_tous = pts_sal + pts_jour

        nb_presences  = sum(1 for p in pts_tous if p.present)
        nb_absences   = sum(1 for p in pts_tous if p.absent)
        taux_pres = round(nb_presences / (nb_presences + nb_absences) * 100
                         ) if (nb_presences + nb_absences) > 0 else 0

        # Heures totales
        heures_normales = sum(float(p.heures_normales or 8) for p in pts_tous if p.present)
        heures_sup = sum(
            float(p.heures_sup_10 or 0) + float(p.heures_sup_30 or 0) +
            float(p.heures_sup_40 or 0) + float(p.heures_sup_70 or 0) +
            float(p.heures_sup or 0)
            for p in pts_tous if p.present)

        # Détail par travailleur (pointage)
        detail_sal = []
        for sal in salaries_aff:
            pts_s = [p for p in pts_sal if p.salarie_id == sal.id]
            nb_p  = sum(1 for p in pts_s if p.present)
            nb_a  = sum(1 for p in pts_s if p.absent)
            h_n   = sum(float(p.heures_normales or 8) for p in pts_s if p.present)
            h_s   = sum(float(p.heures_sup_10 or 0)+float(p.heures_sup_30 or 0)+
                        float(p.heures_sup_40 or 0)+float(p.heures_sup_70 or 0)
                        for p in pts_s if p.present)
            detail_sal.append({
                "nom": sal.nom_complet, "matricule": sal.matricule,
                "emploi": sal.emploi or "—", "type": "MENSUEL",
                "nb_presences": nb_p, "nb_absences": nb_a,
                "heures_normales": round(h_n, 1), "heures_sup": round(h_s, 1),
                "taux": round(nb_p/(nb_p+nb_a)*100) if (nb_p+nb_a) > 0 else 0,
            })

        detail_jour = []
        for jour in journaliers_aff:
            pts_j = [p for p in pts_jour if p.journalier_id == jour.id]
            nb_p  = sum(1 for p in pts_j if p.present)
            nb_a  = sum(1 for p in pts_j if p.absent)
            h_n   = sum(float(p.heures_normales or 8) for p in pts_j if p.present)
            h_s   = sum(float(p.heures_sup or 0) for p in pts_j if p.present)
            detail_jour.append({
                "nom": jour.nom_complet, "profession": jour.profession or "—",
                "taux_horaire": float(jour.taux_horaire or 0), "type": "JOURNALIER",
                "nb_presences": nb_p, "nb_absences": nb_a,
                "heures_normales": round(h_n, 1), "heures_sup": round(h_s, 1),
                "taux": round(nb_p/(nb_p+nb_a)*100) if (nb_p+nb_a) > 0 else 0,
            })

        # ── Paie journaliers ─────────────────────────────────────────────────
        feuilles = FeuillePaieJournalier.query.filter_by(tenant_id=t.id)            .filter(FeuillePaieJournalier.journalier_id.in_(ids_jour),
                    FeuillePaieJournalier.date_debut >= mois_debut,
                    FeuillePaieJournalier.date_fin   <= mois_fin).all() if ids_jour else []
        masse_jour_brut   = sum(float(f.montant_brut or 0) for f in feuilles)
        feuilles_payees   = sum(1 for f in feuilles if f.statut == "PAYÉ")
        feuilles_attente  = sum(1 for f in feuilles if f.statut == "EN_ATTENTE")

        detail_feuilles = [{
            "nom":         f.journalier.nom_complet,
            "profession":  f.journalier.profession or "—",
            "date_debut":  f.date_debut.strftime("%d/%m/%Y") if f.date_debut else "",
            "date_fin":    f.date_fin.strftime("%d/%m/%Y")   if f.date_fin   else "",
            "nb_jours":    f.nb_jours,
            "heures":      round(float(f.total_heures or 0), 1),
            "taux":        round(float(f.taux_horaire or 0)),
            "montant":     round(float(f.montant_brut or 0)),
            "statut":      f.statut,
        } for f in feuilles]

        # ── Bulletins salariés ────────────────────────────────────────────────
        periode = PeriodePaie.query.filter_by(
            tenant_id=t.id, annee=annee, mois=mois).first()
        bulletins_site = []
        masse_mensuelle = {}
        detail_bulletins = []
        if periode and ids_sal:
            bulletins_site = BulletinPaie.query.filter_by(
                tenant_id=t.id, periode_id=periode.id
            ).filter(BulletinPaie.salarie_id.in_(ids_sal)).all()
            masse_mensuelle = calculer_masse_salariale(bulletins_site)
            detail_bulletins = [{
                "nom":        b.salarie.nom_complet,
                "matricule":  b.salarie.matricule,
                "emploi":     b.salarie.emploi or "—",
                "brut":       round(float(b.salaire_brut  or 0)),
                "net":        round(float(b.net_a_payer   or 0)),
                "cnss":       round(float(b.cnss_salarie  or 0)),
                "irpp":       round(float(b.irpp          or 0)),
                "statut":     b.statut,
            } for b in sorted(bulletins_site, key=lambda x: x.salarie.nom)]

        return {
            "site": s,
            "effectif_sal":   len(salaries_aff),
            "effectif_jour":  len(journaliers_aff),
            "effectif_total": len(salaries_aff) + len(journaliers_aff),
            # Pointage
            "nb_presences": nb_presences, "nb_absences": nb_absences,
            "taux_presence": taux_pres,
            "heures_normales": round(heures_normales, 1),
            "heures_sup": round(heures_sup, 1),
            "detail_sal": detail_sal,
            "detail_jour": detail_jour,
            # Paie journaliers
            "feuilles": detail_feuilles,
            "masse_jour_brut": round(masse_jour_brut),
            "feuilles_payees": feuilles_payees,
            "feuilles_attente": feuilles_attente,
            # Bulletins mensuels
            "bulletins": detail_bulletins,
            "masse_mensuelle": masse_mensuelle,
        }

    # Construire rapport(s)
    if site_sel:
        rapports = [_build_rapport_site(site_sel)]
    else:
        rapports = [_build_rapport_site(s) for s in sites_list]

    # Totaux globaux
    totaux = {
        "effectif": sum(r["effectif_total"] for r in rapports),
        "presences": sum(r["nb_presences"]   for r in rapports),
        "absences":  sum(r["nb_absences"]    for r in rapports),
        "h_normales": round(sum(r["heures_normales"] for r in rapports), 1),
        "h_sup":      round(sum(r["heures_sup"]     for r in rapports), 1),
        "masse_jour": sum(r["masse_jour_brut"]  for r in rapports),
        "masse_men":  sum(r["masse_mensuelle"].get("total_net", 0) for r in rapports),
    }

    return render_template("tenant/rapport_mensuel_site.html",
        tenant=t, rapports=rapports, totaux=totaux,
        sites=sites_list, site_sel=site_sel,
        mois=mois, annee=annee,
        mois_nom=MOIS_NOMS_LONG[mois],
        MOIS_NOMS=MOIS_NOMS_LONG,
        now=datetime.now())


@bp.route("/rapports/mensuel-site/export")
@login_required
def rapport_mensuel_site_export():
    """Export Excel du rapport mensuel par site."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    import io, calendar

    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    MOIS_NOMS_LONG = ["","Janvier","Février","Mars","Avril","Mai","Juin",
                      "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]

    mois   = request.args.get("mois",  type=int, default=datetime.now().month)
    annee  = request.args.get("annee", type=int, default=datetime.now().year)
    site_id= request.args.get("site_id", type=int)
    mois_nom = MOIS_NOMS_LONG[mois]
    _, nb_jours_mois = calendar.monthrange(annee, mois)
    mois_debut = date(annee, mois, 1)
    mois_fin   = date(annee, mois, nb_jours_mois)

    # Reconstruire le rapport (même logique que la route GET)
    # On rappelle simplement la route interne via redirect vers export dédié
    # Pour éviter la duplication, on re-calcule ici directement

    sites_list = Site.query.filter_by(tenant_id=t.id, actif=True).order_by(Site.nom).all()
    site_sel   = Site.query.filter_by(id=site_id, tenant_id=t.id).first() if site_id else None
    sites_a_traiter = [site_sel] if site_sel else sites_list

    aff_sal  = {}
    aff_jour = {}
    for a in AffectationSite.query.filter_by(tenant_id=t.id).all():
        if a.actif or (a.date_fin and a.date_fin >= mois_debut):
            if a.salarie_id:    aff_sal[a.salarie_id]    = a.site_id
            if a.journalier_id: aff_jour[a.journalier_id] = a.site_id

    # Styles
    def hdr(ws, row, cols, texts, fill_color="1a2332"):
        for i, txt in enumerate(texts, 1):
            c = ws.cell(row, i, txt)
            c.font      = Font(bold=True, color="FFFFFF", size=9)
            c.fill      = PatternFill("solid", fgColor=fill_color)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border    = Border(**{s: Side(style="thin", color="D1D5DB")
                                    for s in ["left","right","top","bottom"]})
        ws.row_dimensions[row].height = 20

    def titre_section(ws, row, txt, color="374151", ncols=10):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        c = ws.cell(row, 1, txt)
        c.font = Font(bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor=color)
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[row].height = 18

    MONEY = "#,##0"
    thin  = {s: Side(style="thin", color="E5E7EB") for s in ["left","right","top","bottom"]}
    EVEN  = PatternFill("solid", fgColor="F8FAFC")

    wb = Workbook()

    # ══════════════════════════════════════════════════════════════════════════
    # ONGLET 1 — SYNTHÈSE GLOBALE
    # ══════════════════════════════════════════════════════════════════════════
    ws = wb.active
    ws.title = "Synthèse"
    ws.freeze_panes = "A4"

    # Titre principal
    titre_doc = f"RAPPORT MENSUEL — {mois_nom.upper()} {annee} — {t.denomination}"
    if site_sel: titre_doc += f" — {site_sel.nom}"
    ws.merge_cells("A1:K1")
    ws["A1"] = titre_doc
    ws["A1"].font = Font(bold=True, size=13, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="1a2332")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 24
    ws.append([f"Édité le {datetime.now().strftime('%d/%m/%Y à %H:%M')}"])
    ws.append([])

    hdr(ws, 3, 11,
        ["Site","Effectif","Mensuels","Journaliers","Présences","Absences",
         "Taux prés.","H.normales","H.sup","Masse journaliers (FCFA)","Net mensuels (FCFA)"])

    grand_tot = {"eff":0,"pres":0,"abs":0,"hn":0,"hs":0,"mj":0,"mn":0}
    for row_i, s in enumerate(sites_a_traiter, 4):
        ids_sal  = [k for k,v in aff_sal.items()  if v == s.id]
        ids_jour = [k for k,v in aff_jour.items() if v == s.id]
        pts = Pointage.query.filter_by(tenant_id=t.id)            .filter(Pointage.date_pointage >= mois_debut,
                    Pointage.date_pointage <= mois_fin)            .filter(db.or_(
                Pointage.salarie_id.in_(ids_sal)    if ids_sal  else db.false(),
                Pointage.journalier_id.in_(ids_jour) if ids_jour else db.false()
            )).all()
        nb_p  = sum(1 for p in pts if p.present)
        nb_a  = sum(1 for p in pts if p.absent)
        taux  = round(nb_p/(nb_p+nb_a)*100) if (nb_p+nb_a) > 0 else 0
        hn    = round(sum(float(p.heures_normales or 8) for p in pts if p.present), 1)
        hs    = round(sum(float(p.heures_sup_10 or 0)+float(p.heures_sup_30 or 0)+
                          float(p.heures_sup_40 or 0)+float(p.heures_sup_70 or 0)+
                          float(p.heures_sup or 0) for p in pts if p.present), 1)
        feuilles = FeuillePaieJournalier.query.filter_by(tenant_id=t.id)            .filter(FeuillePaieJournalier.journalier_id.in_(ids_jour),
                    FeuillePaieJournalier.date_debut >= mois_debut,
                    FeuillePaieJournalier.date_fin   <= mois_fin).all() if ids_jour else []
        mj = round(sum(float(f.montant_brut or 0) for f in feuilles))
        per= PeriodePaie.query.filter_by(tenant_id=t.id, annee=annee, mois=mois).first()
        mn = 0
        if per and ids_sal:
            buls = BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=per.id)                .filter(BulletinPaie.salarie_id.in_(ids_sal)).all()
            mn = round(sum(float(b.net_a_payer or 0) for b in buls))
        eff = len(ids_sal) + len(ids_jour)
        row_data = [csv_safe(s.nom), eff, len(ids_sal), len(ids_jour),
                    nb_p, nb_a, f"{taux}%", hn, hs, mj, mn]
        ws.append(row_data)
        for ci, v in enumerate(row_data, 1):
            c = ws.cell(row_i, ci)
            c.border = Border(**thin)
            if row_i % 2 == 0: c.fill = EVEN
            if ci in (10, 11): c.number_format = MONEY; c.alignment = Alignment(horizontal="right")
            if ci in (5,6,7,8,9): c.alignment = Alignment(horizontal="center")
        grand_tot["eff"]+=eff; grand_tot["pres"]+=nb_p; grand_tot["abs"]+=nb_a
        grand_tot["hn"]+=hn; grand_tot["hs"]+=hs; grand_tot["mj"]+=mj; grand_tot["mn"]+=mn

    # Total
    tr = ws.max_row + 1
    ws.append(["TOTAL", grand_tot["eff"], "", "", grand_tot["pres"], grand_tot["abs"],
                "", round(grand_tot["hn"],1), round(grand_tot["hs"],1),
                grand_tot["mj"], grand_tot["mn"]])
    for ci in range(1, 12):
        c = ws.cell(tr, ci)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1a2332")
        if ci in (10, 11): c.number_format = MONEY; c.alignment = Alignment(horizontal="right")

    for i, w in enumerate([24,10,10,12,10,10,10,12,10,22,22], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ══════════════════════════════════════════════════════════════════════════
    # ONGLETS PAR SITE
    # ══════════════════════════════════════════════════════════════════════════
    for s in sites_a_traiter:
        ws_s = wb.create_sheet(s.nom[:28])
        ws_s.freeze_panes = "A4"
        ids_sal  = [k for k,v in aff_sal.items()  if v == s.id]
        ids_jour = [k for k,v in aff_jour.items() if v == s.id]

        # Titre
        ws_s.merge_cells("A1:I1")
        ws_s["A1"] = f"{s.nom} — {mois_nom} {annee} — {t.denomination}"
        ws_s["A1"].font = Font(bold=True, size=12, color="FFFFFF")
        ws_s["A1"].fill = PatternFill("solid", fgColor="1a2332")
        ws_s["A1"].alignment = Alignment(horizontal="center", vertical="center")
        ws_s.row_dimensions[1].height = 20
        ws_s.append([])
        row_cur = 2

        # ── Section pointage ──────────────────────────────────────────────────
        row_cur += 1
        titre_section(ws_s, row_cur, f"📅 POINTAGE — {mois_nom} {annee}", "065f46", 9)
        row_cur += 1
        hdr(ws_s, row_cur, 9,
            ["Nom","Type","Emploi/Profession","Présences","Absences","Taux %","H.norm.","H.sup","Total h."],
            "065f46")
        row_cur += 1

        pts_sal  = Pointage.query.filter_by(tenant_id=t.id)            .filter(Pointage.salarie_id.in_(ids_sal),
                    Pointage.date_pointage >= mois_debut,
                    Pointage.date_pointage <= mois_fin).all() if ids_sal else []
        pts_jour = Pointage.query.filter_by(tenant_id=t.id)            .filter(Pointage.journalier_id.in_(ids_jour),
                    Pointage.date_pointage >= mois_debut,
                    Pointage.date_pointage <= mois_fin).all() if ids_jour else []

        salaries_aff    = Salarie.query.filter(Salarie.id.in_(ids_sal)).order_by(Salarie.nom).all() if ids_sal else []
        journaliers_aff = Journalier.query.filter(Journalier.id.in_(ids_jour)).order_by(Journalier.nom).all() if ids_jour else []

        for trv_list, pts_list, typ in [
            (salaries_aff, pts_sal, "Mensuel"),
            (journaliers_aff, pts_jour, "Journalier")
        ]:
            for trv in trv_list:
                if typ == "Mensuel":
                    pts_t = [p for p in pts_list if p.salarie_id == trv.id]
                    emploi = trv.emploi or "—"
                else:
                    pts_t = [p for p in pts_list if p.journalier_id == trv.id]
                    emploi = trv.profession or "—"
                nb_p = sum(1 for p in pts_t if p.present)
                nb_a = sum(1 for p in pts_t if p.absent)
                hn   = round(sum(float(p.heures_normales or 8) for p in pts_t if p.present), 1)
                hs   = round(sum(float(p.heures_sup_10 or 0)+float(p.heures_sup_30 or 0)+
                                 float(p.heures_sup_40 or 0)+float(p.heures_sup_70 or 0)+
                                 float(p.heures_sup or 0) for p in pts_t if p.present), 1)
                taux = round(nb_p/(nb_p+nb_a)*100) if (nb_p+nb_a) > 0 else 0
                row_d = [trv.nom_complet, typ, emploi, nb_p, nb_a, f"{taux}%", hn, hs, round(hn+hs,1)]
                ws_s.append(row_d)
                for ci, v in enumerate(row_d, 1):
                    c = ws_s.cell(row_cur, ci)
                    c.border = Border(**thin)
                    if row_cur % 2 == 0: c.fill = EVEN
                    if ci in (4,5,6,7,8,9): c.alignment = Alignment(horizontal="center")
                row_cur += 1

        ws_s.append([])
        row_cur += 1

        # ── Section paie journaliers ──────────────────────────────────────────
        if ids_jour:
            titre_section(ws_s, row_cur, f"🦺 PAIE JOURNALIERS — {mois_nom} {annee}", "92400e", 9)
            row_cur += 1
            hdr(ws_s, row_cur, 9,
                ["Journalier","Profession","Période du","au","Nb jours","Heures","Taux/h","Montant (FCFA)","Statut"],
                "92400e")
            row_cur += 1
            feuilles = FeuillePaieJournalier.query.filter_by(tenant_id=t.id)                .filter(FeuillePaieJournalier.journalier_id.in_(ids_jour),
                        FeuillePaieJournalier.date_debut >= mois_debut,
                        FeuillePaieJournalier.date_fin   <= mois_fin).all()
            total_feuilles = 0
            for f in feuilles:
                m = round(float(f.montant_brut or 0)); total_feuilles += m
                row_d = [f.journalier.nom_complet, f.journalier.profession or "—",
                         f.date_debut.strftime("%d/%m/%Y") if f.date_debut else "",
                         f.date_fin.strftime("%d/%m/%Y")   if f.date_fin   else "",
                         f.nb_jours, round(float(f.total_heures or 0),1),
                         round(float(f.taux_horaire or 0)), m, f.statut]
                ws_s.append(row_d)
                for ci, v in enumerate(row_d, 1):
                    c = ws_s.cell(row_cur, ci)
                    c.border = Border(**thin)
                    if row_cur % 2 == 0: c.fill = EVEN
                    if ci == 8: c.number_format = MONEY; c.alignment = Alignment(horizontal="right")
                    if ci in (5,6,7): c.alignment = Alignment(horizontal="center")
                row_cur += 1
            # Total
            ws_s.append(["TOTAL JOURNALIERS", "", "", "", "", "", "", total_feuilles, ""])
            for ci in range(1, 10):
                c = ws_s.cell(row_cur, ci)
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="92400e")
                if ci == 8: c.number_format = MONEY; c.alignment = Alignment(horizontal="right")
            row_cur += 2; ws_s.append([])

        # ── Section bulletins mensuels ────────────────────────────────────────
        if ids_sal:
            per = PeriodePaie.query.filter_by(tenant_id=t.id, annee=annee, mois=mois).first()
            if per:
                buls = BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=per.id)                    .filter(BulletinPaie.salarie_id.in_(ids_sal)).all()
                if buls:
                    titre_section(ws_s, row_cur,
                        f"📄 BULLETINS DE PAIE — {mois_nom} {annee}", "1e40af", 9)
                    row_cur += 1
                    hdr(ws_s, row_cur, 9,
                        ["Salarié","Matricule","Emploi","Brut (FCFA)","CNSS sal.",
                         "TCS","IRPP","Net à payer (FCFA)","Statut"], "1e40af")
                    row_cur += 1
                    total_brut = total_net = 0
                    for b in sorted(buls, key=lambda x: x.salarie.nom):
                        brut = round(float(b.salaire_brut or 0))
                        net  = round(float(b.net_a_payer  or 0))
                        total_brut += brut; total_net += net
                        row_d = [b.salarie.nom_complet, b.salarie.matricule,
                                 b.salarie.emploi or "—", brut,
                                 round(float(b.cnss_salarie or 0)),
                                 round(float(b.tcs  or 0)),
                                 round(float(b.irpp or 0)), net, b.statut]
                        ws_s.append(row_d)
                        for ci, v in enumerate(row_d, 1):
                            c = ws_s.cell(row_cur, ci)
                            c.border = Border(**thin)
                            if row_cur % 2 == 0: c.fill = EVEN
                            if ci in (4,5,6,7,8): c.number_format = MONEY; c.alignment = Alignment(horizontal="right")
                        row_cur += 1
                    ws_s.append(["TOTAL SALARIÉS","","",total_brut,"","","",total_net,""])
                    for ci in range(1, 10):
                        c = ws_s.cell(row_cur, ci)
                        c.font = Font(bold=True, color="FFFFFF")
                        c.fill = PatternFill("solid", fgColor="1e40af")
                        if ci in (4,8): c.number_format = MONEY; c.alignment = Alignment(horizontal="right")

        for i, w in enumerate([28,12,20,10,10,10,10,20,12], 1):
            ws_s.column_dimensions[get_column_letter(i)].width = w

    # Export
    out = io.BytesIO(); wb.save(out); out.seek(0)
    fname_parts = [f"Rapport_{mois_nom}_{annee}"]
    if site_sel: fname_parts.append(site_sel.nom.replace(" ","_"))
    fname_parts.append(datetime.now().strftime("%Y%m%d"))
    return send_file(out,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="_".join(fname_parts)+".xlsx")





# ══════════════════════════════════════════════════════════════════════════════
# EXPORT COMPTABLE SAGE 100
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/export/sage/journal/<int:periode_id>")
@tenant_required
def export_sage_journal(periode_id):
    if not current_user.can_export_sage:
        flash("Accès refusé. Seuls les Administrateurs et Comptables peuvent exporter vers Sage.", "error")
        return redirect(url_for("tenant.bulletins"))
    """
    Export du journal de paie mensuel au format Sage 100 (.txt).
    Importable dans Sage 100 Comptabilité via Fichier → Importer → Journal.
    Seuls les bulletins VALIDE sont inclus.
    """
    t = get_tenant()
    periode = PeriodePaie.query.filter_by(id=periode_id, tenant_id=t.id).first_or_404()
    bulletins = (BulletinPaie.query
                 .filter(BulletinPaie.periode_id==periode_id, BulletinPaie.tenant_id==t.id, BulletinPaie.statut.in_(["VALIDE","VALIDÉ"]))
                 .join(Salarie)
                 .order_by(Salarie.nom)
                 .all())

    if not bulletins:
        flash("Aucun bulletin validé pour cette période. Validez les bulletins avant l'export.", "warning")
        return redirect(url_for("tenant.bulletins"))

    try:
        from export_comptable import generer_journal_paie, ExportVide
        contenu = generer_journal_paie(bulletins, periode, t)
        nom_fichier = f"journal_paie_{periode.mois:02d}{periode.annee}_{t.sigle or t.id}.txt"
        logger.info(f"[Export Sage] Journal paie — tenant={t.id} période={periode.libelle_complet}")
        log_action("EXPORT", "bulletin", periode_id,
                   f"Export Sage Journal de paie — {periode.libelle_complet} ({len(bulletins)} bulletins)")
        db.session.commit()
        return send_file(
            io.BytesIO(contenu),
            mimetype="text/plain",
            as_attachment=True,
            download_name=nom_fichier,
        )
    except Exception as e:
        logger.error(f"[Export Sage] Erreur journal : {e}")
        flash(f"Erreur lors de la génération : {e}", "error")
        return redirect(url_for("tenant.bulletins"))


@bp.route("/export/sage/livre/<int:periode_id>")
@tenant_required
def export_sage_livre(periode_id):
    """
    Export du livre de paie détaillé par salarié au format CSV (.csv).
    Compatible Excel et importable dans Sage 100.
    Seuls les bulletins VALIDE sont inclus.
    """
    t = get_tenant()
    periode = PeriodePaie.query.filter_by(id=periode_id, tenant_id=t.id).first_or_404()
    bulletins = (BulletinPaie.query
                 .filter(BulletinPaie.periode_id==periode_id, BulletinPaie.tenant_id==t.id, BulletinPaie.statut.in_(["VALIDE","VALIDÉ"]))
                 .join(Salarie)
                 .order_by(Salarie.nom)
                 .all())

    if not bulletins:
        flash("Aucun bulletin validé pour cette période. Validez les bulletins avant l'export.", "warning")
        return redirect(url_for("tenant.bulletins"))

    try:
        from export_comptable import generer_livre_paie, ExportVide
        contenu = generer_livre_paie(bulletins, periode, t)
        nom_fichier = f"livre_paie_{periode.mois:02d}{periode.annee}_{t.sigle or t.id}.csv"
        logger.info(f"[Export Sage] Livre paie — tenant={t.id} période={periode.libelle_complet}")
        return send_file(
            io.BytesIO(contenu),
            mimetype="text/csv; charset=utf-8",
            as_attachment=True,
            download_name=nom_fichier,
        )
    except Exception as e:
        logger.error(f"[Export Sage] Erreur livre : {e}")
        flash(f"Erreur lors de la génération : {e}", "error")
        return redirect(url_for("tenant.bulletins"))


@bp.route("/export/sage/les-deux/<int:periode_id>")
@tenant_required
def export_sage_les_deux(periode_id):
    """
    Export des deux fichiers (journal + livre) dans une archive ZIP.
    Pratique pour envoyer tout au comptable en une fois.
    """
    import zipfile
    t = get_tenant()
    periode = PeriodePaie.query.filter_by(id=periode_id, tenant_id=t.id).first_or_404()
    bulletins = (BulletinPaie.query
                 .filter(BulletinPaie.periode_id==periode_id, BulletinPaie.tenant_id==t.id, BulletinPaie.statut.in_(["VALIDE","VALIDÉ"]))
                 .join(Salarie)
                 .order_by(Salarie.nom)
                 .all())

    if not bulletins:
        flash("Aucun bulletin validé pour cette période.", "warning")
        return redirect(url_for("tenant.bulletins"))

    try:
        from export_comptable import generer_journal_paie, generer_livre_paie
        journal = generer_journal_paie(bulletins, periode, t)
        livre   = generer_livre_paie(bulletins, periode, t)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                f"journal_paie_{periode.mois:02d}{periode.annee}_{t.sigle or t.id}.txt",
                journal
            )
            zf.writestr(
                f"livre_paie_{periode.mois:02d}{periode.annee}_{t.sigle or t.id}.csv",
                livre
            )
        zip_buffer.seek(0)

        nom_zip = f"export_sage_{periode.mois:02d}{periode.annee}_{t.sigle or t.id}.zip"
        logger.info(f"[Export Sage] ZIP généré — tenant={t.id}")
        return send_file(
            zip_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=nom_zip,
        )
    except Exception as e:
        logger.error(f"[Export Sage] Erreur ZIP : {e}")
        flash(f"Erreur lors de la génération : {e}", "error")
        return redirect(url_for("tenant.bulletins"))


# ══════════════════════════════════════════════════════════════════════════════
# RAPPORT PDF MENSUEL
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/rapport/pdf/<int:periode_id>")
@tenant_required
def rapport_pdf(periode_id):
    """
    Génère le rapport PDF mensuel complet organisé par site :
      - Salariés + journaliers regroupés par site
      - Récapitulatif global et charges à verser
    """
    t = get_tenant()
    if not current_user.can_export and not current_user.is_tenant_admin:
        flash("Accès non autorisé.", "error")
        return redirect(url_for("tenant.bulletins"))

    periode = PeriodePaie.query.filter_by(id=periode_id, tenant_id=t.id).first_or_404()

    # ── Bulletins salariés validés ────────────────────────────────────────────
    bulletins = (BulletinPaie.query
        .filter(BulletinPaie.periode_id == periode_id,
                BulletinPaie.tenant_id  == t.id,
                BulletinPaie.statut.in_(["VALIDÉ","VALIDE","PAYÉ"]))
        .options(joinedload(BulletinPaie.salarie))
        .join(Salarie).order_by(Salarie.nom).all())

    # ── Feuilles de paie journaliers du même mois ─────────────────────────────
    from datetime import date as _date
    debut_mois = _date(periode.annee, periode.mois, 1)
    import calendar
    dernier_jour = calendar.monthrange(periode.annee, periode.mois)[1]
    fin_mois = _date(periode.annee, periode.mois, dernier_jour)

    feuilles = (FeuillePaieJournalier.query
        .filter_by(tenant_id=t.id)
        .filter(FeuillePaieJournalier.date_debut >= debut_mois,
                FeuillePaieJournalier.date_fin   <= fin_mois)
        .options(joinedload(FeuillePaieJournalier.journalier))
        .all())

    if not bulletins and not feuilles:
        flash("Aucun bulletin ni feuille de paie pour cette période.", "warning")
        return redirect(url_for("tenant.bulletins"))

    # ── Sites et affectations ─────────────────────────────────────────────────
    sites = Site.query.filter_by(tenant_id=t.id, actif=True).order_by(Site.nom).all()
    affectations = AffectationSite.query.filter_by(tenant_id=t.id, actif=True).all()

    # ── Évolution 6 mois ──────────────────────────────────────────────────────
    MOIS_FR = ["","Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"]
    evolution = []
    for i in range(5, -1, -1):
        mois  = (periode.mois - i - 1) % 12 + 1
        annee = periode.annee - ((i + 12 - periode.mois) // 12) if i >= periode.mois else periode.annee
        buls_m = BulletinPaie.query.join(PeriodePaie).filter(
            BulletinPaie.tenant_id == t.id,
            PeriodePaie.mois == mois, PeriodePaie.annee == annee,
        ).all()
        evolution.append({
            "mois":  MOIS_FR[mois],
            "annee": annee,
            "brut":  sum(float(b.salaire_brut or 0) for b in buls_m),
            "net":   sum(float(b.net_a_payer  or 0) for b in buls_m),
        })

    try:
        from rapport_pdf import generer_rapport_mensuel
        pdf_bytes = generer_rapport_mensuel(
            bulletins            = bulletins,
            periode              = periode,
            tenant               = t,
            feuilles_journaliers = feuilles,
            sites                = sites,
            affectations         = affectations,
            evolution            = evolution,
        )

        nom_fichier = f"rapport_paie_{periode.mois:02d}{periode.annee}_{t.sigle or t.id}.pdf"
        log_action("EXPORT", "rapport", periode_id,
                   f"Rapport PDF — {periode.libelle_complet} — "
                   f"{len(bulletins)} salariés, {len(feuilles)} journaliers, "
                   f"{len(sites)} sites")
        db.session.commit()
        logger.info(f"[Rapport PDF] Généré — tenant={t.id} période={periode.libelle_complet}")
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=nom_fichier,
        )
    except Exception as e:
        logger.error(f"[Rapport PDF] Erreur : {e}")
        flash(f"Erreur lors de la génération du PDF : {e}", "error")
        return redirect(url_for("tenant.bulletins"))


@bp.route("/rapport/pdf/<int:periode_id>/envoyer-email", methods=["POST"])
@tenant_required
def rapport_pdf_email(periode_id):
    """
    Génère le rapport PDF et l'envoie par email aux admins et directeurs du tenant.
    Peut être déclenché manuellement ou automatiquement en fin de mois.
    """
    t = get_tenant()
    if not current_user.is_tenant_admin:
        flash("Accès réservé à l'administrateur.", "error")
        return redirect(url_for("tenant.bulletins"))

    periode = PeriodePaie.query.filter_by(id=periode_id, tenant_id=t.id).first_or_404()
    bulletins_p = (BulletinPaie.query
        .filter(BulletinPaie.periode_id == periode_id,
                BulletinPaie.tenant_id  == t.id,
                BulletinPaie.statut.in_(["VALIDÉ","VALIDE","PAYÉ"]))
        .options(joinedload(BulletinPaie.salarie))
        .join(Salarie).order_by(Salarie.nom).all())

    if not bulletins_p:
        flash("Aucun bulletin validé — rapport non envoyé.", "warning")
        return redirect(url_for("tenant.bulletins"))

    # Destinataires : admins et directeurs actifs
    destinataires = [
        u.email for u in Utilisateur.query.filter_by(tenant_id=t.id, actif=True).all()
        if u.email and u.role in ("TENANT_ADMIN", "DIRECTEUR")
    ]
    email_extra = request.form.get("email_extra", "").strip()
    if email_extra:
        destinataires.append(email_extra)

    if not destinataires:
        flash("Aucun destinataire configuré.", "error")
        return redirect(url_for("tenant.bulletins"))

    try:
        from rapport_pdf import generer_rapport_mensuel
        evolution = []  # Simplifié pour l'envoi auto
        pdf_bytes = generer_rapport_mensuel(bulletins_p, periode, t, evolution)
        nom_fichier = f"rapport_paie_{periode.mois:02d}{periode.annee}_{t.sigle or t.id}.pdf"

        from calculs_paie import calculer_masse_salariale
        masse = calculer_masse_salariale(bulletins_p)

        msg = Message(
            subject=f"[PaieGabon] Rapport mensuel — {t.denomination} — {periode.libelle_complet}",
            recipients=destinataires,
            body=(
                f"Bonjour,\n\n"
                f"Veuillez trouver en pièce jointe le rapport mensuel de paie de {t.denomination} "
                f"pour la période {periode.libelle_complet} {periode.annee}.\n\n"
                f"Résumé :\n"
                f"  • Nombre de bulletins : {len(bulletins_p)}\n"
                f"  • Masse salariale brute : {int(masse.get('total_brut',0)):,} FCFA\n"
                f"  • Net total à payer : {int(masse.get('total_net',0)):,} FCFA\n"
                f"  • Coût total employeur : {int(masse.get('total_brut',0) + masse.get('total_charges_pat',0)):,} FCFA\n\n"
                f"Ce rapport est confidentiel et destiné à usage interne uniquement.\n\n"
                f"Cordialement,\nPaieGabon SaaS"
            ),
        )
        msg.attach(nom_fichier, "application/pdf", pdf_bytes)
        send_email_async(current_app.extensions["mail"], msg)

        log_action("EXPORT", "rapport", periode_id,
                   f"Rapport PDF envoyé par email à {len(destinataires)} destinataire(s)")
        db.session.commit()

        flash(f"Rapport envoyé par email à {len(destinataires)} destinataire(s).", "success")
        logger.info(f"[Rapport PDF Email] Envoyé à {destinataires} — tenant={t.id}")

    except Exception as e:
        logger.error(f"[Rapport PDF Email] Erreur : {e}")
        flash(f"Erreur lors de l'envoi : {e}", "error")

    return redirect(url_for("tenant.bulletins", periode_id=periode_id))


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
@bp.route("/recherche")
@login_required
def recherche_globale():
    """Recherche globale : salariés, journaliers, bulletins, acomptes."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    q = request.args.get("q", "").strip()
    if not q:
        return render_template("tenant/recherche.html",
                               tenant=t, q="", resultats={}, nb_total=0)

    like = f"%{q}%"
    resultats = {}

    # ── Salariés ──────────────────────────────────────────────────────────────
    sals = (Salarie.query.filter_by(tenant_id=t.id)
            .filter(db.or_(
                Salarie.nom.ilike(like), Salarie.prenom.ilike(like),
                Salarie.matricule.ilike(like), Salarie.emploi.ilike(like),
                Salarie.telephone.ilike(like)))
            .order_by(Salarie.nom).limit(10).all())
    if sals:
        resultats["salaries"] = [{"id": s.id, "titre": s.nom_complet,
            "sous_titre": f"{s.emploi or '—'} · {s.matricule}",
            "badge": s.statut, "lien": f"/salaries/{s.id}",
            "icone": "👤"} for s in sals]

    # ── Journaliers ───────────────────────────────────────────────────────────
    jours = (Journalier.query.filter_by(tenant_id=t.id)
             .filter(db.or_(
                 Journalier.nom.ilike(like), Journalier.prenom.ilike(like),
                 Journalier.profession.ilike(like), Journalier.telephone.ilike(like)))
             .order_by(Journalier.nom).limit(10).all())
    if jours:
        resultats["journaliers"] = [{"id": j.id, "titre": j.nom_complet,
            "sous_titre": f"{j.profession or '—'} · {int(j.taux_horaire or 0)} FCFA/h",
            "badge": j.statut, "lien": f"/journaliers/{j.id}",
            "icone": "🦺"} for j in jours]

    # ── Bulletins ─────────────────────────────────────────────────────────────
    buls = (BulletinPaie.query.filter_by(tenant_id=t.id)
            .join(Salarie, BulletinPaie.salarie_id == Salarie.id)
            .join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
            .filter(db.or_(
                Salarie.nom.ilike(like), Salarie.prenom.ilike(like),
                Salarie.matricule.ilike(like)))
            .order_by(BulletinPaie.date_creation.desc()).limit(10).all())
    if buls:
        resultats["bulletins"] = [{"id": b.id,
            "titre": b.salarie.nom_complet,
            "sous_titre": f"{b.periode.libelle_complet} · Net : {int(b.net_a_payer or 0):,} FCFA",
            "badge": b.statut, "lien": f"/bulletins/{b.id}",
            "icone": "📄"} for b in buls]

    # ── Acomptes ──────────────────────────────────────────────────────────────
    acomps = (Acompte.query.filter_by(tenant_id=t.id)
              .join(Salarie, Acompte.salarie_id == Salarie.id)
              .filter(db.or_(
                  Salarie.nom.ilike(like), Salarie.prenom.ilike(like),
                  Salarie.matricule.ilike(like)))
              .order_by(Acompte.date_acompte.desc()).limit(10).all())
    if acomps:
        resultats["acomptes"] = [{"id": a.id,
            "titre": a.salarie.nom_complet,
            "sous_titre": f"{int(a.montant or 0):,} FCFA · {a.date_acompte.strftime('%d/%m/%Y') if a.date_acompte else ''}",
            "badge": a.statut, "lien": "/acomptes",
            "icone": "💸"} for a in acomps]

    # ── Prestataires / sous-traitants ──────────────────────────────────────────
    prests = (Prestataire.query.filter_by(tenant_id=t.id)
              .filter(db.or_(
                  Prestataire.raison_sociale.ilike(like), Prestataire.code.ilike(like),
                  Prestataire.activite.ilike(like), Prestataire.telephone.ilike(like)))
              .order_by(Prestataire.raison_sociale).limit(10).all())
    if prests:
        resultats["prestataires"] = [{"id": pr.id, "titre": pr.raison_sociale,
            "sous_titre": f"{pr.categorie_label} · {pr.code}",
            "badge": pr.statut if hasattr(pr, "statut") else None,
            "lien": f"/prestataires/{pr.id}", "icone": "🛠️"} for pr in prests]

    nb_total = sum(len(v) for v in resultats.values())
    return render_template("tenant/recherche.html",
                           tenant=t, q=q, resultats=resultats, nb_total=nb_total)


@bp.route("/api/recherche-rapide")
@login_required
def api_recherche_rapide():
    """API JSON pour l'autocomplétion dans la barre de recherche."""
    t = get_tenant()
    if not t:
        return jsonify([])
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    like = f"%{q}%"
    resultats = []

    for s in (Salarie.query.filter_by(tenant_id=t.id)
              .filter(db.or_(Salarie.nom.ilike(like), Salarie.prenom.ilike(like),
                             Salarie.matricule.ilike(like)))
              .limit(5).all()):
        resultats.append({"icone": "👤", "titre": s.nom_complet,
            "sous_titre": s.emploi or "Salarié", "lien": f"/salaries/{s.id}",
            "categorie": "Salariés"})

    for j in (Journalier.query.filter_by(tenant_id=t.id)
              .filter(db.or_(Journalier.nom.ilike(like), Journalier.prenom.ilike(like),
                             Journalier.profession.ilike(like)))
              .limit(5).all()):
        resultats.append({"icone": "🦺", "titre": j.nom_complet,
            "sous_titre": j.profession or "Journalier", "lien": f"/journaliers/{j.id}",
            "categorie": "Journaliers"})

    for pr in (Prestataire.query.filter_by(tenant_id=t.id)
               .filter(db.or_(Prestataire.raison_sociale.ilike(like),
                              Prestataire.code.ilike(like),
                              Prestataire.activite.ilike(like)))
               .limit(5).all()):
        resultats.append({"icone": "🛠️", "titre": pr.raison_sociale,
            "sous_titre": pr.categorie_label or "Prestataire", "lien": f"/prestataires/{pr.id}",
            "categorie": "Prestataires"})

    # Bulletins — recherchés par le salarié concerné (nom, prénom, matricule)
    for b in (BulletinPaie.query.filter_by(tenant_id=t.id)
              .join(Salarie, BulletinPaie.salarie_id == Salarie.id)
              .filter(db.or_(Salarie.nom.ilike(like), Salarie.prenom.ilike(like),
                             Salarie.matricule.ilike(like)))
              .order_by(BulletinPaie.date_creation.desc())
              .limit(5).all()):
        resultats.append({"icone": "📄", "titre": b.salarie.nom_complet,
            "sous_titre": f"Bulletin · {b.periode.libelle_complet}",
            "lien": f"/bulletins/{b.id}", "categorie": "Bulletins"})

    return jsonify(resultats[:15])


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
