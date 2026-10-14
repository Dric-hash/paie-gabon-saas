# -*- coding: utf-8 -*-
"""Congés et acomptes — extrait de tenant.py (même blueprint « tenant »)."""
from datetime import datetime, date, timedelta
from flask import render_template, request, redirect, url_for, flash, session, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy import func, desc
from blueprints.tenant import bp, _doc_response
from core import tenant_required, get_tenant, _parse_date
from audit import log_action
from models import db, Acompte, BulletinPaie, Conge, Contrat, Salarie, PeriodePaie


@bp.route("/acomptes")
@login_required
def acomptes():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    now = datetime.now()
    mois = request.args.get("mois", now.month, type=int)
    annee = request.args.get("annee", now.year, type=int)
    salarie_id = request.args.get("salarie_id", type=int)
    try:
        query = Acompte.query.filter_by(tenant_id=t.id, annee=annee, mois=mois)
        if salarie_id: query = query.filter_by(salarie_id=salarie_id)
        liste = query.order_by(Acompte.date_acompte.desc()).all()
    except Exception:
        db.create_all(); db.session.rollback(); liste = []
    total_mois       = sum(float(a.montant) for a in liste if a.statut != "ANNULE")
    total_en_attente = sum(float(a.montant) for a in liste if a.statut == "EN_ATTENTE")
    total_deduit     = sum(float(a.montant) for a in liste if a.statut == "DEDUIT")
    salaries_list = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").order_by(Salarie.nom).all()
    return render_template("tenant/acomptes.html", tenant=t, liste=liste, salaries=salaries_list,
        mois=mois, annee=annee, now=now, total_mois=total_mois,
        total_en_attente=total_en_attente, total_deduit=total_deduit, MOIS_NOMS=PeriodePaie.MOIS_NOMS)

@bp.route("/acomptes/imprimer", methods=["POST"])
@login_required
def acomptes_imprimer():
    """Page imprimable de la liste des acomptes sélectionnés (avec colonne signature)."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    ids = [int(i) for i in request.form.getlist("acompte_ids") if str(i).isdigit()]
    mois  = request.form.get("mois", type=int)
    annee = request.form.get("annee", type=int)
    if not ids:
        flash("Sélectionnez au moins un acompte à imprimer.", "error")
        return redirect(url_for("tenant.acomptes", mois=mois, annee=annee))
    liste = Acompte.query.filter(
        Acompte.tenant_id == t.id,
        Acompte.id.in_(ids)
    ).options(joinedload(Acompte.salarie)).order_by(Acompte.date_acompte).all()
    total = sum(float(a.montant or 0) for a in liste if a.statut != "ANNULE")
    return render_template("tenant/acomptes_print.html",
        tenant=t, liste=liste, total=total, mois=mois, annee=annee,
        MOIS_NOMS=PeriodePaie.MOIS_NOMS, now=datetime.now())


@bp.route("/acomptes/<int:id>/recu")
@login_required
def acompte_recu(id):
    """Reçu d'acompte individuel (PDF) à faire signer par le salarié."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    a = Acompte.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    from documents_rh import generer_recu_acompte_pdf
    pdf = generer_recu_acompte_pdf(t, a)
    log_action("EXPORT", "acompte", a.id, "Reçu d'acompte édité")
    nom = f"recu_acompte_{(a.salarie.nom if a.salarie else 'salarie')}_{a.id}.pdf"
    return _doc_response(pdf, nom)


@bp.route("/acomptes/nouveau", methods=["GET","POST"])
@login_required
def acompte_nouveau():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.can_edit: abort(403)
    salaries_list = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").order_by(Salarie.nom).all()
    if request.method == "POST":
        salarie_id = request.form.get("salarie_id", type=int)
        montant    = float(request.form.get("montant", 0) or 0)
        date_ac    = _parse_date(request.form.get("date_acompte"))
        mois       = request.form.get("mois", type=int)
        annee      = request.form.get("annee", type=int)
        motif      = request.form.get("motif", "").strip()
        if not salarie_id or montant <= 0 or not date_ac:
            flash("Veuillez remplir tous les champs.", "error")
        else:
            # Valider que le salarié appartient bien au tenant (anti-IDOR).
            sal = Salarie.query.filter_by(id=salarie_id, tenant_id=t.id).first()
            if not sal:
                flash("Salarié introuvable.", "error")
                return render_template("tenant/acompte_form.html", tenant=t, salaries=salaries_list, now=datetime.now())
            contrat = Contrat.query.filter_by(salarie_id=salarie_id, tenant_id=t.id, actif=True).first()
            if contrat and montant > float(contrat.salaire_base) * 0.5:
                flash(f"Acompte maximum 50% du salaire de base ({float(contrat.salaire_base)*0.5:,.0f} FCFA).".replace(",", " "), "error")
                return render_template("tenant/acompte_form.html", tenant=t, salaries=salaries_list, now=datetime.now())
            ac = Acompte(tenant_id=t.id, salarie_id=salarie_id, montant=montant,
                date_acompte=date_ac, mois=mois, annee=annee, motif=motif, statut="EN_ATTENTE")
            db.session.add(ac)
            db.session.commit()
            log_action("CREATE", "acompte", ac.id,
                       f"Acompte {montant:,.0f} F — {sal.nom_complet}".replace(",", " "))
            db.session.commit()
            flash(f"Acompte de {montant:,.0f} FCFA enregistré.".replace(",", " "), "success")
            return redirect(url_for("tenant.acomptes", mois=mois, annee=annee))
    return render_template("tenant/acompte_form.html", tenant=t, salaries=salaries_list, now=datetime.now())

@bp.route("/acomptes/<int:id>/valider", methods=["POST"])
@login_required
def acompte_valider(id):
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    a = Acompte.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    a.statut = "DEDUIT"; db.session.commit()
    log_action("VALIDATE", "acompte", a.id, f"Acompte déduit ({float(a.montant or 0):,.0f} F)".replace(",", " "))
    db.session.commit()
    flash("Acompte marqué comme déduit.", "success")
    return redirect(url_for("tenant.acomptes", mois=a.mois, annee=a.annee))

@bp.route("/acomptes/<int:id>/annuler", methods=["POST"])
@login_required
def acompte_annuler(id):
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    a = Acompte.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    a.statut = "ANNULE"; db.session.commit()
    log_action("CANCEL", "acompte", a.id, "Acompte annulé")
    db.session.commit()
    flash("Acompte annulé.", "success")
    return redirect(url_for("tenant.acomptes", mois=a.mois, annee=a.annee))

@bp.route("/acomptes/<int:id>/supprimer", methods=["POST"])
@login_required
def acompte_supprimer(id):
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    a = Acompte.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    mois, annee = a.mois, a.annee
    db.session.delete(a); db.session.commit()
    log_action("DELETE", "acompte", id, "Suppression acompte")
    db.session.commit()
    flash("Acompte supprimé.", "success")
    return redirect(url_for("tenant.acomptes", mois=mois, annee=annee))

# ── Congés avancés ────────────────────────────────────────────────────────────

@bp.route("/conges/bilan")
@tenant_required
def conges_bilan():
    """Bilan congés de tous les salariés actifs avec jours acquis, pris, restants."""
    t = get_tenant()
    annee = request.args.get("annee", date.today().year, type=int)
    salaries_actifs = Salarie.query.filter_by(
        tenant_id=t.id, statut="ACTIF"
    ).options(
        joinedload(Salarie.conges),
        joinedload(Salarie.contrats),
    ).order_by(Salarie.nom).all()

    from conges_avance import bilan_conges_tenant
    bilan = bilan_conges_tenant(salaries_actifs, annee)

    # Stats globales
    total_acquis   = sum(b["jours_acquis"]   for b in bilan)
    total_pris     = sum(b["jours_pris"]     for b in bilan)
    total_restants = sum(b["jours_restants"] for b in bilan)
    nb_alertes     = sum(1 for b in bilan if b["alerte"])

    return render_template("tenant/conges_bilan.html",
        tenant=t, bilan=bilan, annee=annee,
        total_acquis=total_acquis, total_pris=total_pris,
        total_restants=total_restants, nb_alertes=nb_alertes,
        annees=list(range(date.today().year - 2, date.today().year + 2)),
    )


@bp.route("/conges/planning")
@tenant_required
def conges_planning():
    """Planning visuel des congés — vue calendrier mensuel."""
    t    = get_tenant()
    mois = request.args.get("mois", date.today().month, type=int)
    annee= request.args.get("annee", date.today().year,  type=int)

    tous_conges = Conge.query.filter_by(tenant_id=t.id)\
        .options(joinedload(Conge.salarie))\
        .filter(Conge.statut.in_(["APPROUVÉ","APPROUVE","DEMANDÉ","DEMANDE","PRIS"]))\
        .all()

    from conges_avance import planning_absences
    planning = planning_absences(tous_conges, annee, mois)

    # Infos du mois pour le calendrier
    import calendar
    cal = calendar.monthcalendar(annee, mois)
    nb_jours = calendar.monthrange(annee, mois)[1]
    MOIS_FR = ["","Janvier","Février","Mars","Avril","Mai","Juin",
               "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]

    return render_template("tenant/conges_planning.html",
        tenant=t, planning=planning, mois=mois, annee=annee,
        mois_nom=MOIS_FR[mois], cal=cal, nb_jours=nb_jours,
        MOIS_FR=MOIS_FR,
    )




@bp.route("/conges")
@login_required
def conges():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    now   = datetime.now()
    annee = request.args.get("annee", now.year, type=int)
    q     = request.args.get("q", "")

    # ── Calcul du solde — Code du Travail gabonais ───────────────────────────
    # Art. 222 : 2 j/mois (≥ 18 ans) | 2,5 j/mois (< 18 ans) | taux contractuel plus favorable
    # Allocation = max(Σ bruts 12 mois, dernier brut×12) / 288 × jours acquis
    # Exclusion : prime de transport (Art. 213 al. 3)

    def taux_conge(salarie, annee_ref):
        """Taux de l'entreprise (2 j/mois minimum légal, Art. 222), porté à
        2,5 j/mois pour un salarié de moins de 18 ans au 31/12 de l'année."""
        from conges_avance import taux_acquisition
        return taux_acquisition(tenant=t, salarie=salarie,
                                date_ref=date(annee_ref, 12, 31))

    def calculer_solde_auto(salarie, annee_ref):
        """Jours acquis proratisés selon le taux applicable."""
        tx = taux_conge(salarie, annee_ref)
        if not salarie.date_embauche:
            return round(12 * tx, 1)
        emb         = salarie.date_embauche
        debut_annee = datetime(annee_ref, 1, 1).date()
        fin_annee   = datetime(annee_ref, 12, 31).date()
        debut_acq   = max(emb, debut_annee)
        fin_acq     = min(datetime.now().date(), fin_annee)
        if fin_acq < debut_acq:
            return 0.0
        mois_trav = min(round((fin_acq - debut_acq).days / 30.44, 1), 12)
        return round(mois_trav * tx, 1)

    salaries_list = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF")        .options(joinedload(Salarie.categorie), joinedload(Salarie.contrats)).order_by(Salarie.nom).all()

    # ── PRÉCHARGEMENT pour éviter les requêtes N+1 ───────────────────────────
    from datetime import timedelta
    sal_ids = [s.id for s in salaries_list]

    # 1. Tous les soldes de congés de l'année en une requête
    soldes_db_map = {}
    if sal_ids:
        for c in Conge.query.filter(
            Conge.tenant_id == t.id, Conge.salarie_id.in_(sal_ids),
            Conge.annee == annee, Conge.date_depart == None
        ).all():
            soldes_db_map[c.salarie_id] = c

    # 2. Tous les congés approuvés de l'année (pour cumul jours pris) en une requête
    pris_map = {}
    if sal_ids:
        for c in Conge.query.filter(
            Conge.tenant_id == t.id, Conge.salarie_id.in_(sal_ids),
            Conge.annee == annee, Conge.statut == "APPROUVÉ"
        ).all():
            pris_map.setdefault(c.salarie_id, 0.0)
            pris_map[c.salarie_id] += float(c.jours_pris or 0)

    # 3. Tous les bulletins des 12 derniers mois en une requête, groupés par salarié
    buls_map = {}
    if sal_ids:
        debut_periode = (datetime(annee, 12, 31) - timedelta(days=365)).date()
        buls_query = (BulletinPaie.query
            .filter(BulletinPaie.tenant_id == t.id,
                    BulletinPaie.salarie_id.in_(sal_ids))
            .join(PeriodePaie)
            .filter(PeriodePaie.annee >= debut_periode.year)
            .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc())
            .all())
        for b in buls_query:
            buls_map.setdefault(b.salarie_id, []).append(b)

    def calculer_allocation_conge(salarie, jours_acquis, annee_ref):
        """
        Allocation congés = max(Σbruts12mois, dernierBrut×12) / 288 × jours_acquis
        Prime de transport exclue de la base (Art. 213 al. 3).
        Utilise les données préchargées (buls_map) — aucune requête SQL ici.
        """
        if jours_acquis <= 0:
            return 0.0, 0.0
        buls = buls_map.get(salarie.id, [])[:12]
        if not buls:
            contrat = next((c for c in salarie.contrats if c.actif), None)
            if not contrat: return 0.0, 0.0
            last_brut = float(contrat.salaire_base or 0)
            somme_12  = last_brut * 12
        else:
            somme_12  = sum(
                float(b.salaire_brut or 0) - float(b.prime_transport or 0)
                for b in buls
            )
            last_brut = float(buls[0].salaire_brut or 0) - float(buls[0].prime_transport or 0)
        base_methode1 = somme_12  / 288
        base_methode2 = (last_brut * 12) / 288
        base          = max(base_methode1, base_methode2)
        allocation    = round(base * jours_acquis, 0)
        return round(base, 2), allocation

    soldes = []
    for s in salaries_list:
        if q and q.lower() not in f"{s.nom} {s.prenom} {s.matricule}".lower():
            continue
        solde_db = soldes_db_map.get(s.id)

        jours_auto = calculer_solde_auto(s, annee)

        if solde_db:
            acquis    = float(solde_db.jours_acquis or jours_auto)
            pris      = float(solde_db.jours_pris   or 0)
        else:
            acquis    = jours_auto
            pris      = pris_map.get(s.id, 0.0)

        taux_j    = taux_conge(s, annee)
        base_all, allocation = calculer_allocation_conge(s, acquis, annee)
        soldes.append({
            "salarie":        s,
            "solde_db":       solde_db,
            "jours_auto":     jours_auto,
            "jours_acquis":   acquis,
            "jours_pris":     pris,
            "jours_restants": round(acquis - pris, 1),
            "alerte":         (acquis - pris) < 5,
            "taux_j":         taux_j,
            "base_allocation":base_all,
            "allocation":     allocation,
            "mineur":         taux_j == 2.5,
        })

    # Demandes (avec date_depart renseignée) — paginées
    page_conges = request.args.get("page", 1, type=int)
    q_demandes = (Conge.query.filter_by(tenant_id=t.id)
                  .filter(Conge.date_depart.isnot(None))
                  .options(joinedload(Conge.salarie))
                  .order_by(Conge.date_depart.desc()))
    pagination = q_demandes.paginate(page=page_conges, per_page=25, error_out=False)
    demandes = pagination.items

    _args = {k: v for k, v in request.args.items() if k != "page"}
    _base = request.path + "?" + "&".join(f"{k}={v}" for k, v in _args.items())
    _sep  = "&" if _args else "?"

    annees_dispo = sorted(set(
        [now.year, now.year-1, now.year+1]
        + [c.annee for c in Conge.query.filter_by(tenant_id=t.id).all()]
    ), reverse=True)

    return render_template("tenant/conges.html",
        tenant=t, soldes=soldes, demandes=demandes,
        pagination=pagination, pagination_base=_base + _sep,
        annee=annee, annees_dispo=annees_dispo, now=now, q=q,
        salaries=salaries_list)

# ── Congés : une ligne « SOLDE » par salarié et par année (date_depart vide)
# porte les jours acquis / pris ; chaque demande est une ligne distincte.
# Les calculs d'argent (allocation de congé du bulletin, indemnité de départ)
# additionnent les jours_pris des DEMANDES approuvées : une demande ne doit donc
# jamais hériter des jours d'une autre, ni être comptée deux fois.
_STATUTS_APPROUVES = ("APPROUVÉ", "APPROUVE")


def _jours_demande(dep, ret):
    return (ret - dep).days + 1 if dep and ret and ret >= dep else 0


def _solde_annuel(t, salarie, annee):
    """Ligne de solde annuel (créée au besoin). Taux d'acquisition : le calcul
    unique du logiciel (conges_avance.taux_acquisition, Art. 222)."""
    from conges_avance import taux_acquisition
    solde = (Conge.query.filter_by(tenant_id=t.id, salarie_id=salarie.id, annee=annee)
             .filter(Conge.date_depart.is_(None)).first())
    if not solde:
        mois = max(1, (date.today() - salarie.date_embauche).days // 30) if salarie.date_embauche else 12
        solde = Conge(tenant_id=t.id, salarie_id=salarie.id, annee=annee,
                      jours_acquis=round(min(mois, 12) * taux_acquisition(
                          tenant=t, salarie=salarie, date_ref=date(annee, 12, 31)), 1),
                      jours_pris=0, statut="SOLDE")
        db.session.add(solde)
    elif solde.statut in (None, "DEMANDÉ", "DEMANDE"):
        # Anciennes lignes de solde créées avec le statut par défaut : on les
        # sort des « congés en attente ».
        solde.statut = "SOLDE"
    return solde


def _imputer_solde(t, c, delta):
    """Reporte delta jours sur le solde annuel de la demande c (±)."""
    if not delta or not c.salarie:
        return
    solde = _solde_annuel(t, c.salarie, c.annee)
    solde.jours_pris = max(0.0, float(solde.jours_pris or 0) + delta)


@bp.route("/conges/nouveau", methods=["GET","POST"])
@login_required
def conge_nouveau():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    salaries_list = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").order_by(Salarie.nom).all()
    if request.method == "POST":
        salarie_id = request.form.get("salarie_id", type=int)
        annee = request.form.get("annee", datetime.now().year, type=int)
        date_dep = _parse_date(request.form.get("date_depart"))
        date_ret = _parse_date(request.form.get("date_retour"))
        type_c = request.form.get("type_conge", "ANNUEL")
        # Congé de maternité : si une date présumée d'accouchement est fournie,
        # les 14 semaines légales (Art. 208) sont calculées automatiquement.
        if type_c == "MATERNITE":
            date_acc = _parse_date(request.form.get("date_accouchement"))
            if date_acc:
                from conges_avance import calculer_conge_maternite
                mat = calculer_conge_maternite(
                    date_acc,
                    naissances_multiples=(request.form.get("naissances_multiples") == "1"),
                    complications=(request.form.get("complications_grossesse") == "1"),
                )
                date_dep, date_ret = mat["date_debut"], mat["date_fin"]
        s = Salarie.query.filter_by(id=salarie_id, tenant_id=t.id).first() if salarie_id else None
        if not s:
            flash("Veuillez choisir un salarié.", "error")
            return render_template("tenant/conge_form.html", tenant=t, salaries=salaries_list, now=datetime.now())
        if not date_dep or not date_ret or date_ret < date_dep:
            flash("Dates de congé invalides (la date de retour doit suivre la date de départ).", "error")
            return render_template("tenant/conge_form.html", tenant=t, salaries=salaries_list, now=datetime.now())
        jours = _jours_demande(date_dep, date_ret)
        _solde_annuel(t, s, annee)   # garantit la ligne de solde de l'année
        # Toujours une NOUVELLE demande : on n'écrase jamais une demande existante.
        conge = Conge(tenant_id=t.id, salarie_id=s.id, annee=annee,
                      jours_acquis=0, jours_pris=0, type_conge=type_c, statut="DEMANDÉ",
                      date_depart=date_dep, date_retour=date_ret)
        db.session.add(conge)
        db.session.commit()
        log_action("CREATE", "conge", conge.id,
                   f"Demande de congé {s.nom_complet} ({jours} j) — "
                   f"{date_dep.strftime('%d/%m/%Y')}→{date_ret.strftime('%d/%m/%Y')}")
        db.session.commit()
        flash(f"✅ Demande de congé enregistrée ({jours} jour(s)).", "success")
        return redirect(url_for("tenant.conges"))
    return render_template("tenant/conge_form.html", tenant=t, salaries=salaries_list, now=datetime.now())

@bp.route("/conges/<int:id>/modifier", methods=["GET","POST"])
@login_required
def conge_modifier(id):
    """Modifier une demande de congé existante."""
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    c = Conge.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    salaries_list = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").order_by(Salarie.nom).all()

    if request.method == "POST":
        date_dep  = _parse_date(request.form.get("date_depart"))
        date_ret  = _parse_date(request.form.get("date_retour"))
        type_c    = request.form.get("type_conge", "ANNUEL")
        if not date_dep or not date_ret or date_ret < date_dep:
            flash("Dates de congé invalides (la date de retour doit suivre la date de départ).", "error")
            return render_template("tenant/conge_form.html", tenant=t, salaries=salaries_list,
                                   conge=c, now=datetime.now(), mode="modifier")
        new_jours = _jours_demande(date_dep, date_ret)
        nouveau_statut = request.form.get("statut", c.statut)
        etait_approuve = c.statut in _STATUTS_APPROUVES
        sera_approuve  = nouveau_statut in _STATUTS_APPROUVES

        # Le solde reflète exactement les demandes approuvées : on retire
        # l'ancienne contribution puis on ajoute la nouvelle.
        if etait_approuve:
            _imputer_solde(t, c, -float(c.jours_pris or 0))
        c.date_depart = date_dep
        c.date_retour = date_ret
        c.type_conge  = type_c
        c.statut      = nouveau_statut
        c.jours_pris  = new_jours if sera_approuve else 0
        if sera_approuve:
            _imputer_solde(t, c, new_jours)
        db.session.commit()
        log_action("UPDATE", "conge", c.id,
                   f"Modification congé — {c.salarie.nom_complet if c.salarie else ''} ({new_jours} j)")
        db.session.commit()
        flash(f"✅ Congé modifié ({new_jours} jour(s)).", "success")
        return redirect(url_for("tenant.conges"))

    return render_template("tenant/conge_form.html",
        tenant=t, salaries=salaries_list,
        conge=c, now=datetime.now(), mode="modifier")


@bp.route("/conges/<int:id>/approuver", methods=["POST"])
@login_required
def conge_approuver(id):
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    c = Conge.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    # Idempotence : un double-clic ne doit pas compter les jours deux fois.
    if c.statut in _STATUTS_APPROUVES:
        flash("Ce congé est déjà approuvé.", "info")
        return redirect(url_for("tenant.conges"))
    if not c.date_depart or not c.date_retour:
        flash("Ce congé n'a pas de dates : il ne peut pas être approuvé.", "error")
        return redirect(url_for("tenant.conges"))
    jours = _jours_demande(c.date_depart, c.date_retour)
    c.jours_pris = jours            # valeur exacte, jamais un cumul hérité
    c.statut = "APPROUVÉ"
    _imputer_solde(t, c, jours)
    db.session.commit()
    log_action("VALIDATE", "conge", c.id,
               f"Congé approuvé — {c.salarie.nom_complet if c.salarie else ''} ({jours} j)")
    db.session.commit()
    flash(f"✅ Congé de {c.salarie.nom_complet if c.salarie else ''} approuvé.", "success")
    return redirect(url_for("tenant.conges"))

@bp.route("/conges/<int:id>/refuser", methods=["POST"])
@login_required
def conge_refuser(id):
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    c = Conge.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    # Refuser un congé déjà approuvé restitue ses jours au solde.
    if c.statut in _STATUTS_APPROUVES:
        _imputer_solde(t, c, -float(c.jours_pris or 0))
        c.jours_pris = 0
    c.statut = "REFUSÉ"; db.session.commit()
    log_action("CANCEL", "conge", c.id,
               f"Congé refusé — {c.salarie.nom_complet if c.salarie else ''}")
    db.session.commit()
    flash("Congé refusé.", "success")
    return redirect(url_for("tenant.conges"))

@bp.route("/conges/<int:id>/supprimer", methods=["POST"])
@login_required
def conge_supprimer(id):
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    c = Conge.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    nom = c.salarie.nom_complet if c.salarie else ""   # lu AVANT la suppression
    if c.date_depart is None:
        flash("La ligne de solde annuel ne se supprime pas.", "error")
        return redirect(url_for("tenant.conges"))
    if c.statut in _STATUTS_APPROUVES:
        _imputer_solde(t, c, -float(c.jours_pris or 0))   # restitue les jours
    db.session.delete(c); db.session.commit()
    log_action("DELETE", "conge", id, f"Suppression demande de congé — {nom}")
    db.session.commit()
    flash("Demande supprimée.", "success")
    return redirect(url_for("tenant.conges"))


