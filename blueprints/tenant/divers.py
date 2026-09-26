# -*- coding: utf-8 -*-
"""Tableau de bord, rapports, exports comptables (Sage), recherche, audit, jours fériés,
notifications, simulateur, prime de fin d'année et APIs internes —
extrait de tenant.py (même blueprint « tenant »)."""
import os
from datetime import datetime, date, timedelta
from flask import (render_template, request, redirect, url_for, flash, session,
                   current_app, abort, send_file, jsonify, Response)
from flask_login import login_required, current_user
from flask_mail import Message
from sqlalchemy.orm import joinedload
from sqlalchemy import desc, func, or_, cast
from blueprints.tenant import bp, logger, _NOTIF_MODELS
from core import (tenant_required, get_tenant, can_edit, admin_only, _pd, _parse_date, parse_date,
                  _cache_get, _cache_set, _cache_delete, cache_get, cache_set, cache_delete,
                  send_email_async, TTL_KPIS_DASH, TTL_EVOLUTION, TTL_CATS_STATS)
from i18n import SUPPORTED_LANGUAGES, set_language
from notifications import get_notifications, compter_notifications
from jours_feries import jours_feries_annee, est_jour_ferie, nom_jour_ferie, type_jour_auto
from audit import log_action, get_audit_logs
from calculs_paie import calculer_masse_salariale, calculer_taux_horaire
from models import (db, Tenant, Salarie, Contrat, PeriodePaie, BulletinPaie, BulletinComposant,
                    Conge, Acompte, ComposantPaie, CategorieEmploi, Utilisateur, Site,
                    AffectationSite, Pointage, Journalier, FeuillePaieJournalier, MessageSupport,
                    Plan, Prestataire, FacturePrestataire, AuditLog, RubriquePaie)


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
