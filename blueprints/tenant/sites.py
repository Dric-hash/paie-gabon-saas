# -*- coding: utf-8 -*-
"""Sites / chantiers et affectations de travailleurs — extrait de tenant.py.
Partage le même blueprint « tenant » : les endpoints (tenant.sites, etc.) sont inchangés."""
from datetime import datetime, date, timedelta
from flask import render_template, request, redirect, url_for, flash, session, abort
from flask_login import current_user
import interco_projets
import main_oeuvre_chantiers as moc
from blueprints.tenant import bp
from core import tenant_required, get_tenant
from models import (db, Site, AffectationSite, Salarie, Pointage,
                    Journalier, FeuillePaieJournalier, PeriodePaie, BulletinPaie)


@bp.route("/sites")
@tenant_required
def sites():
    t = get_tenant()
    sites_list = Site.query.filter_by(tenant_id=t.id).order_by(Site.nom).all()
    return render_template("tenant/sites.html", tenant=t, sites=sites_list)

def _appliquer_geo_site(s, form):
    """Lit latitude/longitude/rayon_m du formulaire (utilisés par l'alerte géo
    du pointage mobile). Vides = pas de géolocalisation → aucune alerte."""
    def _f(k):
        v = (form.get(k) or "").strip().replace(",", ".")
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None
    s.latitude  = _f("latitude")
    s.longitude = _f("longitude")
    r = (form.get("rayon_m") or "").strip()
    try:
        s.rayon_m = int(r) if r else 200
    except ValueError:
        s.rayon_m = 200


def _ctx_projets(t):
    conf = interco_projets.configure(t)
    return {"projets_configure": conf, "chantiers": interco_projets.chantiers(t) if conf else []}


def _appliquer_chantier_projets(t, s, form):
    """Chantier de Projets relié au site (liste relue dans Projets pour valider l'identifiant)."""
    if not interco_projets.configure(t) or form.get("projets_inchange") or "projets_chantier_id" not in form:
        return
    brut = (form.get("projets_chantier_id") or "").strip()
    if not brut:
        s.projets_chantier_id = s.projets_chantier_nom = None
        return
    liste = interco_projets.chantiers(t)
    c = next((c for c in (liste or []) if str(c.get("id")) == brut), None)
    if c is None:
        flash("Chantier de Projets introuvable ou Projets injoignable : le lien du site n'a pas changé.", "warning")
        return
    s.projets_chantier_id, s.projets_chantier_nom = int(c["id"]), str(c.get("nom") or "")[:200]


def _mois_demande(brut):
    """(année, mois) d'un « AAAA-MM » ; par défaut le mois précédent."""
    try:
        a, m = (int(x) for x in (brut or "").split("-"))
        if 2000 <= a <= 2100 and 1 <= m <= 12:
            return a, m
    except ValueError:
        pass
    d = date.today().replace(day=1) - timedelta(days=1)
    return d.year, d.month


@bp.route("/sites/projets")
@tenant_required
def sites_projets():
    """Coût de la main-d'œuvre du mois par site, et envoi aux chantiers de Projets Ameriack."""
    if not current_user.can_edit:
        abort(403)
    t = get_tenant()
    annee, mois = _mois_demande(request.args.get("mois"))
    res = moc.calculer(t.id, annee, mois)
    return render_template("tenant/sites_projets.html", tenant=t, res=res, annee=annee, mois=mois,
                           projets_configure=interco_projets.configure(t))


@bp.route("/sites/projets/envoyer", methods=["POST"])
@tenant_required
def sites_projets_envoyer():
    if not current_user.can_edit:
        abort(403)
    t = get_tenant()
    annee, mois = _mois_demande(request.form.get("mois"))
    retour = redirect(url_for("tenant.sites_projets", mois=f"{annee}-{mois:02d}"))
    if not interco_projets.configure(t):
        flash("Interconnexion avec Projets non configurée (PROJETS_URL, PROJETS_TOKEN, référence d'interconnexion).",
              "error")
        return retour
    from calendrier_paie import fin_de_mois
    jour = fin_de_mois(annee, mois)
    res = moc.calculer(t.id, annee, mois)
    envoyes, retires, erreurs = 0, 0, []
    for l in res["sites"]:
        s = l["site"]
        for cat, montant, quoi in (("sal", l["salaries"], "salariés (coût employeur : brut + charges patronales)"),
                                   ("jour", l["journaliers"], "journaliers (feuilles de paie)")):
            corps, err = interco_projets.envoyer_depense(
                t, moc.reference(annee, mois, s.id, cat), s.projets_chantier_id, montant, jour,
                f"Main-d'œuvre {mois:02d}/{annee} — {quoi}, site {s.nom}")
            if err:
                erreurs.append(f"{s.nom} : {err}")
                if err == "Projets ne répond pas":
                    break
            elif s.projets_chantier_id and round(montant or 0) > 0:
                envoyes += 1
            elif (corps or {}).get("supprimee"):
                retires += 1
        if erreurs and erreurs[-1].endswith("Projets ne répond pas"):
            break
    if erreurs:
        for e in erreurs[:5]:
            flash(f"Envoi incomplet — {e}.", "error")
    else:
        flash(f"Main-d'œuvre de {mois:02d}/{annee} envoyée à Projets : {envoyes} dépense(s) à jour"
              + (f", {retires} retirée(s)" if retires else "") + ". Vous pouvez renvoyer le mois après une "
              "correction de paie : les montants sont remplacés, sans doublon.", "success")
    return retour


@bp.route("/sites/nouveau", methods=["GET","POST"])
@tenant_required
def site_nouveau():
    t = get_tenant()
    if request.method == "POST":
        s = Site(
            tenant_id   = t.id,
            nom         = request.form["nom"].strip(),
            code        = request.form.get("code","").strip().upper() or None,
            adresse     = request.form.get("adresse","").strip() or None,
            ville       = request.form.get("ville","").strip() or None,
            responsable = request.form.get("responsable","").strip() or None,
            telephone   = request.form.get("telephone","").strip() or None,
            description = request.form.get("description","").strip() or None,
        )
        _appliquer_geo_site(s, request.form)
        _appliquer_chantier_projets(t, s, request.form)
        db.session.add(s)
        db.session.commit()
        flash(f"Site « {s.nom} » créé avec succès.", "success")
        return redirect(url_for("tenant.sites"))
    return render_template("tenant/site_form.html", tenant=t, site=None, **_ctx_projets(t))

@bp.route("/sites/<int:id>/modifier", methods=["GET","POST"])
@tenant_required
def site_modifier(id):
    t = get_tenant()
    s = Site.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if request.method == "POST":
        s.nom         = request.form["nom"].strip()
        s.code        = request.form.get("code","").strip().upper() or None
        s.adresse     = request.form.get("adresse","").strip() or None
        s.ville       = request.form.get("ville","").strip() or None
        s.responsable = request.form.get("responsable","").strip() or None
        s.telephone   = request.form.get("telephone","").strip() or None
        s.description = request.form.get("description","").strip() or None
        _appliquer_geo_site(s, request.form)
        _appliquer_chantier_projets(t, s, request.form)
        db.session.commit()
        flash(f"Site « {s.nom} » modifié.", "success")
        return redirect(url_for("tenant.sites"))
    return render_template("tenant/site_form.html", tenant=t, site=s, **_ctx_projets(t))

@bp.route("/sites/<int:id>/toggle", methods=["POST"])
@tenant_required
def site_toggle(id):
    t = get_tenant()
    s = Site.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    s.actif = not s.actif
    db.session.commit()
    flash(f"Site « {s.nom} » {'activé' if s.actif else 'désactivé'}.", "success")
    return redirect(url_for("tenant.sites"))

@bp.route("/sites/<int:id>")
@tenant_required
def site_detail(id):
    t = get_tenant()
    s = Site.query.filter_by(id=id, tenant_id=t.id).first_or_404()

    # Date sélectionnée pour le pointage rapide
    date_str = request.args.get("date_ptg", date.today().strftime("%Y-%m-%d"))
    try:    date_ptg = datetime.strptime(date_str, "%Y-%m-%d").date()
    except: date_ptg = date.today()

    # Affectations actives
    affectations = AffectationSite.query.filter_by(site_id=id, actif=True)        .order_by(AffectationSite.date_debut.desc()).all()

    # Séparer salariés et journaliers affectés
    ids_sal  = [a.salarie_id    for a in affectations if a.salarie_id]
    ids_jour = [a.journalier_id for a in affectations if a.journalier_id]

    salaries_site    = Salarie.query.filter(
        Salarie.tenant_id==t.id, Salarie.statut=="ACTIF",
        Salarie.id.in_(ids_sal)
    ).order_by(Salarie.nom).all() if ids_sal else []

    journaliers_site = Journalier.query.filter(
        Journalier.tenant_id==t.id, Journalier.statut=="ACTIF",
        Journalier.id.in_(ids_jour)
    ).order_by(Journalier.nom).all() if ids_jour else []

    # Pointages du jour pour ce site
    pts_sal  = {p.salarie_id:    p for p in
        Pointage.query.filter_by(tenant_id=t.id, date_pointage=date_ptg)
        .filter(Pointage.salarie_id.in_(ids_sal)).all()} if ids_sal else {}
    pts_jour = {p.journalier_id: p for p in
        Pointage.query.filter_by(tenant_id=t.id, date_pointage=date_ptg)
        .filter(Pointage.journalier_id.in_(ids_jour)).all()} if ids_jour else {}

    # Stats pointage du jour
    nb_presents  = sum(1 for p in list(pts_sal.values())+list(pts_jour.values()) if p.present)
    nb_absents   = sum(1 for p in list(pts_sal.values())+list(pts_jour.values()) if p.absent)
    nb_non_pointes = (len(salaries_site)+len(journaliers_site)) - len(pts_sal) - len(pts_jour)

    # Historique complet
    historique = AffectationSite.query.filter_by(site_id=id)        .order_by(AffectationSite.date_creation.desc()).limit(50).all()

    # Travailleurs disponibles (non affectés à ce site)
    ids_sal_aff  = {a.salarie_id    for a in affectations if a.salarie_id}
    ids_jour_aff = {a.journalier_id for a in affectations if a.journalier_id}
    salaries_dispo    = [x for x in Salarie.query.filter_by(
        tenant_id=t.id, statut="ACTIF").order_by(Salarie.nom).all()
        if x.id not in ids_sal_aff]
    journaliers_dispo = [x for x in Journalier.query.filter_by(
        tenant_id=t.id, statut="ACTIF").order_by(Journalier.nom).all()
        if x.id not in ids_jour_aff]

    # ── KPIs tableau de bord ─────────────────────────────────────────────────
    from datetime import date as _date
    import calendar

    now_d       = _date.today()
    mois_debut  = _date(now_d.year, now_d.month, 1)
    mois_fin    = _date(now_d.year, now_d.month,
                        calendar.monthrange(now_d.year, now_d.month)[1])
    lundi_sem   = now_d - timedelta(days=now_d.weekday())
    samedi_sem  = lundi_sem + timedelta(days=5)

    # Tous les pointages du mois pour ce site (salariés + journaliers)
    ids_all = ids_sal + ids_jour
    pts_mois_sal = Pointage.query.filter_by(tenant_id=t.id)        .filter(Pointage.salarie_id.in_(ids_sal),
                Pointage.date_pointage >= mois_debut,
                Pointage.date_pointage <= mois_fin).all() if ids_sal else []
    pts_mois_jour = Pointage.query.filter_by(tenant_id=t.id)        .filter(Pointage.journalier_id.in_(ids_jour),
                Pointage.date_pointage >= mois_debut,
                Pointage.date_pointage <= mois_fin).all() if ids_jour else []
    pts_mois_tous = pts_mois_sal + pts_mois_jour

    # Pointages de la semaine
    pts_sem_sal  = [p for p in pts_mois_sal  if lundi_sem <= p.date_pointage <= samedi_sem]
    pts_sem_jour = [p for p in pts_mois_jour if lundi_sem <= p.date_pointage <= samedi_sem]

    # KPI — Jours pointés (présences) ce mois
    nb_jours_pointes_mois = sum(1 for p in pts_mois_tous if p.present)
    nb_absences_mois      = sum(1 for p in pts_mois_tous if p.absent)

    # KPI — Taux de présence semaine
    nb_pres_sem = sum(1 for p in pts_sem_sal + pts_sem_jour if p.present)
    nb_abs_sem  = sum(1 for p in pts_sem_sal + pts_sem_jour if p.absent)
    total_ptg_sem = nb_pres_sem + nb_abs_sem
    taux_presence_semaine = round(nb_pres_sem / total_ptg_sem * 100) if total_ptg_sem > 0 else 0

    # KPI — Heures totales semaine
    def total_heures_pt(p):
        return (float(p.heures_normales or 8) +
                float(p.heures_sup_10 or 0) + float(p.heures_sup_30 or 0) +
                float(p.heures_sup_40 or 0) + float(p.heures_sup_70 or 0) +
                float(p.heures_sup or 0))

    heures_semaine = sum(total_heures_pt(p) for p in pts_sem_sal + pts_sem_jour if p.present)

    # KPI — Masse journalière (feuilles de paie journaliers ce mois)
    feuilles_mois = FeuillePaieJournalier.query.filter_by(tenant_id=t.id)        .filter(FeuillePaieJournalier.journalier_id.in_(ids_jour),
                FeuillePaieJournalier.date_debut >= mois_debut,
                FeuillePaieJournalier.date_fin   <= mois_fin).all() if ids_jour else []
    masse_journaliere_mois    = sum(float(f.montant_brut or 0) for f in feuilles_mois)
    feuilles_attente = sum(1 for f in feuilles_mois if f.statut == "EN_ATTENTE")
    feuilles_payees  = sum(1 for f in feuilles_mois if f.statut == "PAYÉ")

    # KPI — Bulletins salariés du mois (dernière période active)
    periode_courante = PeriodePaie.query.filter_by(
        tenant_id=t.id, annee=now_d.year, mois=now_d.month).first()
    bulletins_site = []
    masse_mensuelle = 0
    if periode_courante and ids_sal:
        bulletins_site = BulletinPaie.query.filter_by(
            tenant_id=t.id, periode_id=periode_courante.id
        ).filter(BulletinPaie.salarie_id.in_(ids_sal)).all()
        masse_mensuelle = sum(float(b.net_a_payer or 0) for b in bulletins_site)

    # Évolution présence 7 derniers jours (pour mini-graphique)
    evolution_7j = []
    for i in range(6, -1, -1):
        d = now_d - timedelta(days=i)
        p_d = [p for p in pts_mois_tous if p.date_pointage == d]
        nb_p = sum(1 for p in p_d if p.present)
        nb_a = sum(1 for p in p_d if p.absent)
        evolution_7j.append({
            "date":    d.strftime("%d/%m"),
            "jour":    ["L","Ma","Me","J","V","Sa","Di"][d.weekday()],
            "presents": nb_p,
            "absents":  nb_a,
            "heures":   round(sum(total_heures_pt(p) for p in p_d if p.present), 1),
        })

    return render_template("tenant/site_detail.html",
        tenant=t, site=s,
        affectations=affectations, historique=historique,
        salaries_dispo=salaries_dispo, journaliers_dispo=journaliers_dispo,
        salaries_site=salaries_site, journaliers_site=journaliers_site,
        pts_sal=pts_sal, pts_jour=pts_jour,
        date_ptg=date_ptg,
        date_hier=(date_ptg - timedelta(days=1)).strftime("%Y-%m-%d"),
        date_demain=(date_ptg + timedelta(days=1)).strftime("%Y-%m-%d"),
        nb_presents=nb_presents, nb_absents=nb_absents,
        nb_non_pointes=nb_non_pointes,
        # KPIs tableau de bord
        nb_sal_site=len(salaries_site), nb_jour_site=len(journaliers_site),
        nb_jours_pointes_mois=nb_jours_pointes_mois,
        nb_absences_mois=nb_absences_mois,
        taux_presence_semaine=taux_presence_semaine,
        heures_semaine=round(heures_semaine, 1),
        masse_journaliere_mois=masse_journaliere_mois,
        feuilles_attente=feuilles_attente, feuilles_payees=feuilles_payees,
        masse_mensuelle=masse_mensuelle,
        nb_bulletins_site=len(bulletins_site),
        evolution_7j=evolution_7j,
        mois_nom=["","Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"][now_d.month],
        today=str(date.today()))

@bp.route("/sites/<int:id>/pointage-rapide", methods=["POST"])
@tenant_required
def site_pointage_rapide(id):
    """Sauvegarder le pointage rapide depuis la page d'un site."""
    t = get_tenant()
    s = Site.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    date_str = request.form.get("date_pointage")
    try:    date_p = datetime.strptime(date_str, "%Y-%m-%d").date()
    except: date_p = date.today()

    nb = 0
    for key, val in request.form.items():
        # Salariés
        if key.startswith("sal_present_"):
            sid = int(key.replace("sal_present_", ""))
            present = (val == "1"); absent = not present
            pt = Pointage.query.filter_by(
                tenant_id=t.id, date_pointage=date_p, salarie_id=sid).first()
            if not pt:
                pt = Pointage(tenant_id=t.id, date_pointage=date_p,
                              salarie_id=sid, site_id=id)
                db.session.add(pt)
            pt.present = present; pt.absent = absent
            pt.heures_normales = float(request.form.get(f"sal_h_{sid}", 8) or 8)
            pt.heures_sup_10   = float(request.form.get(f"sal_s10_{sid}", 0) or 0)
            pt.heures_sup_30   = float(request.form.get(f"sal_s30_{sid}", 0) or 0)
            pt.heures_sup_30b  = float(request.form.get(f"sal_s30b_{sid}", 0) or 0)
            pt.heures_sup_40   = float(request.form.get(f"sal_s40_{sid}", 0) or 0)
            pt.heures_sup_70   = float(request.form.get(f"sal_s70_{sid}", 0) or 0)
            pt.motif_absence   = request.form.get(f"sal_motif_{sid}", "") if absent else None
            nb += 1
        # Journaliers
        elif key.startswith("jour_present_"):
            jid = int(key.replace("jour_present_", ""))
            present = (val == "1"); absent = not present
            pt = Pointage.query.filter_by(
                tenant_id=t.id, date_pointage=date_p, journalier_id=jid).first()
            if not pt:
                pt = Pointage(tenant_id=t.id, date_pointage=date_p,
                              journalier_id=jid, site_id=id)
                db.session.add(pt)
            pt.present = present; pt.absent = absent
            pt.heures_normales = float(request.form.get(f"jour_h_{jid}", 8) or 8)
            pt.heures_sup      = float(request.form.get(f"jour_s_{jid}", 0) or 0)
            pt.motif_absence   = request.form.get(f"jour_motif_{jid}", "") if absent else None
            nb += 1

    db.session.commit()
    flash(f"✅ Pointage du {date_p.strftime('%d/%m/%Y')} sauvegardé — {nb} travailleur(s).", "success")
    return redirect(url_for("tenant.site_detail", id=id) + f"?date_ptg={date_str}")

@bp.route("/sites/<int:site_id>/affecter", methods=["POST"])
@tenant_required
def site_affecter(site_id):
    """Affecter un ou plusieurs travailleurs à un site."""
    t  = get_tenant()
    s  = Site.query.filter_by(id=site_id, tenant_id=t.id).first_or_404()
    date_debut = request.form.get("date_debut") or str(date.today())
    motif      = request.form.get("motif","").strip() or None
    nb         = 0

    for key in request.form:
        if key.startswith("sal_"):
            sal_id = int(key[4:])
            sal = Salarie.query.filter_by(id=sal_id, tenant_id=t.id).first()
            if not sal: continue
            # Désactiver toute affectation active précédente sur un AUTRE site
            prev = AffectationSite.query.filter_by(
                salarie_id=sal_id, actif=True, tenant_id=t.id).first()
            if prev and prev.site_id != site_id:
                prev.actif    = False
                prev.date_fin = date.today()
                prev.motif    = f"Transféré vers {s.nom}"
            elif prev and prev.site_id == site_id:
                continue  # Déjà sur ce site
            a = AffectationSite(
                tenant_id=t.id, site_id=site_id, salarie_id=sal_id,
                date_debut=date_debut, actif=True, motif=motif,
                cree_par=current_user.email)
            db.session.add(a); nb += 1

        elif key.startswith("jour_"):
            jour_id = int(key[5:])
            jour = Journalier.query.filter_by(id=jour_id, tenant_id=t.id).first()
            if not jour: continue
            prev = AffectationSite.query.filter_by(
                journalier_id=jour_id, actif=True, tenant_id=t.id).first()
            if prev and prev.site_id != site_id:
                prev.actif    = False
                prev.date_fin = date.today()
                prev.motif    = f"Transféré vers {s.nom}"
            elif prev and prev.site_id == site_id:
                continue
            a = AffectationSite(
                tenant_id=t.id, site_id=site_id, journalier_id=jour_id,
                date_debut=date_debut, actif=True, motif=motif,
                cree_par=current_user.email)
            db.session.add(a); nb += 1

    db.session.commit()
    flash(f"{nb} travailleur(s) affecté(s) à « {s.nom} ».", "success")
    return redirect(url_for("tenant.site_detail", id=site_id))

@bp.route("/sites/affecter-travailleur/<int:affectation_id>/retirer", methods=["POST"])
@tenant_required
def site_retirer(affectation_id):
    """Retirer un travailleur de son site (fin d'affectation)."""
    t = get_tenant()
    a = AffectationSite.query.filter_by(id=affectation_id, tenant_id=t.id).first_or_404()
    motif = request.form.get("motif","").strip() or "Retrait manuel"
    a.actif    = False
    a.date_fin = date.today()
    a.motif    = motif
    db.session.commit()
    flash(f"Affectation terminée pour {a.nom_travailleur}.", "success")
    return redirect(url_for("tenant.site_detail", id=a.site_id))

@bp.route("/sites/permuter", methods=["POST"])
@tenant_required
def site_permuter():
    """Permuter un travailleur d'un site vers un autre."""
    t         = get_tenant()
    aff_id    = request.form.get("affectation_id", type=int)
    nouveau_site_id = request.form.get("site_destination_id", type=int)
    motif     = request.form.get("motif","Permutation").strip()

    aff_old = AffectationSite.query.filter_by(id=aff_id, tenant_id=t.id, actif=True).first_or_404()
    site_dest = Site.query.filter_by(id=nouveau_site_id, tenant_id=t.id).first_or_404()

    # Fermer l'affectation actuelle
    aff_old.actif    = False
    aff_old.date_fin = date.today()
    aff_old.motif    = f"Permuté vers {site_dest.nom} — {motif}"

    # Créer la nouvelle affectation
    aff_new = AffectationSite(
        tenant_id     = t.id,
        site_id       = nouveau_site_id,
        salarie_id    = aff_old.salarie_id,
        journalier_id = aff_old.journalier_id,
        date_debut    = date.today(),
        actif         = True,
        motif         = f"Permuté depuis {aff_old.site.nom} — {motif}",
        cree_par      = current_user.email,
    )
    db.session.add(aff_new)
    db.session.commit()
    flash(f"{aff_old.nom_travailleur} permuté vers « {site_dest.nom} ».", "success")
    return redirect(url_for("tenant.site_detail", id=nouveau_site_id))

