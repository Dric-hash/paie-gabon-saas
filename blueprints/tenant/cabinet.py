# -*- coding: utf-8 -*-
"""Espace cabinet : tableau de bord, production mensuelle, entreprises gérées,
collaborateurs et assignations — extrait de tenant.py (même blueprint « tenant »)."""
from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from blueprints.tenant import bp, ROLES_TENANT_AUTORISES
from core import tenant_required, get_tenant
from audit import log_action
from models import (db, Tenant, Utilisateur, Salarie, PeriodePaie, BulletinPaie,
                    CategorieEmploi, CollaborateurEntreprise)


@bp.route("/cabinet")
@login_required
def cabinet_dashboard():
    """Tableau de bord d'un cabinet : liste de ses entreprises clientes avec,
    pour chacune, un aperçu (salariés, statut). Réservé aux comptes cabinet."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not t.est_cabinet:
        # Un tenant normal n'a pas de tableau de bord cabinet.
        return redirect(url_for("tenant.dashboard"))

    from sqlalchemy import func
    entreprises = (Tenant.query.filter_by(cabinet_id=t.id)
                   .order_by(Tenant.denomination).all())
    # Un collaborateur ne voit que les entreprises qui lui sont assignées.
    if not current_user.is_tenant_admin:
        from models import CollaborateurEntreprise
        ids_ok = {a.entreprise_id for a in CollaborateurEntreprise.query.filter_by(
            utilisateur_id=current_user.id).all()}
        entreprises = [e for e in entreprises if e.id in ids_ok]

    # Aperçu par entreprise : nombre de salariés actifs + dernière période
    apercu = []
    for e in entreprises:
        nb_sal = Salarie.query.filter_by(tenant_id=e.id, statut="ACTIF").count()
        derniere_periode = (PeriodePaie.query.filter_by(tenant_id=e.id)
                            .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc())
                            .first())
        apercu.append({
            "tenant": e,
            "nb_salaries": nb_sal,
            "derniere_periode": derniere_periode,
        })

    total_salaries = sum(a["nb_salaries"] for a in apercu)

    return render_template("tenant/cabinet_dashboard.html",
        cabinet=t, apercu=apercu,
        nb_entreprises=len(entreprises), total_salaries=total_salaries)


@bp.route("/cabinet/production")
@login_required
def cabinet_production():
    """Tableau de production mensuelle : état de chaque entreprise pour un mois donné."""
    t = get_tenant()
    if not t or not t.est_cabinet:
        return redirect(url_for("tenant.dashboard"))

    _now = datetime.now()
    mois  = request.args.get("mois", type=int) or _now.month
    annee = request.args.get("annee", type=int) or _now.year
    MOIS_FR = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
               "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

    entreprises = (Tenant.query.filter_by(cabinet_id=t.id)
                   .order_by(Tenant.denomination).all())
    # Un collaborateur ne voit que les entreprises qui lui sont assignées.
    if not current_user.is_tenant_admin:
        from models import CollaborateurEntreprise
        ids_ok = {a.entreprise_id for a in CollaborateurEntreprise.query.filter_by(
            utilisateur_id=current_user.id).all()}
        entreprises = [e for e in entreprises if e.id in ids_ok]
    VALIDE = ("VALIDÉ", "VALIDE", "PAYÉ")
    lignes = []
    nb_todo = nb_encours = nb_termine = 0
    for e in entreprises:
        nb_sal = Salarie.query.filter_by(tenant_id=e.id, statut="ACTIF").count()
        periode = (PeriodePaie.query.filter_by(tenant_id=e.id, mois=mois, annee=annee).first())
        nb_bul = nb_val = 0
        if periode:
            nb_bul = BulletinPaie.query.filter_by(tenant_id=e.id, periode_id=periode.id).count()
            nb_val = (BulletinPaie.query.filter_by(tenant_id=e.id, periode_id=periode.id)
                      .filter(BulletinPaie.statut.in_(VALIDE)).count())
        # Statut global de l'entreprise pour ce mois
        if nb_sal == 0:
            statut = "vide"
        elif nb_bul == 0:
            statut = "todo"; nb_todo += 1
        elif nb_val >= nb_sal and nb_sal > 0:
            statut = "termine"; nb_termine += 1
        else:
            statut = "encours"; nb_encours += 1
        lignes.append({
            "tenant": e, "nb_salaries": nb_sal, "periode": periode,
            "nb_bulletins": nb_bul, "nb_valides": nb_val, "statut": statut,
        })

    return render_template("tenant/cabinet_production.html",
        tenant=t, cabinet=t, lignes=lignes, mois=mois, annee=annee,
        mois_label=MOIS_FR[mois], mois_fr=MOIS_FR,
        nb_todo=nb_todo, nb_encours=nb_encours, nb_termine=nb_termine,
        nb_entreprises=len(entreprises))


@bp.route("/cabinet/entrer/<int:entreprise_id>")
@login_required
def cabinet_entrer(entreprise_id):
    """Le cabinet 'entre' dans une de ses entreprises pour y travailler.
    Sécurité : on vérifie que l'entreprise appartient bien à ce cabinet."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = current_user.tenant
    if not t or not t.est_cabinet:
        flash("Réservé aux comptes cabinet.", "error")
        return redirect(url_for("tenant.dashboard"))

    entreprise = Tenant.query.get_or_404(entreprise_id)
    # GARDE-FOU : l'entreprise doit appartenir à CE cabinet.
    if entreprise.cabinet_id != t.id:
        flash("Cette entreprise ne fait pas partie de votre portefeuille.", "error")
        return redirect(url_for("tenant.cabinet_dashboard"))
    # GARDE-FOU 2 : un collaborateur ne peut entrer que dans SES entreprises assignées.
    if not current_user.is_tenant_admin:
        from models import CollaborateurEntreprise
        assigne = CollaborateurEntreprise.query.filter_by(
            utilisateur_id=current_user.id, entreprise_id=entreprise.id).first()
        if not assigne:
            flash("Vous n'êtes pas assigné à cette entreprise.", "error")
            return redirect(url_for("tenant.cabinet_dashboard"))

    session["cabinet_entreprise_id"] = entreprise.id
    log_action("SUPPORT_ACCESS", "tenant", entreprise.id,
               f"Cabinet {t.denomination} accède à l'entreprise {entreprise.denomination}",
               user_id=current_user.id, tenant_id=entreprise.id)
    db.session.commit()
    flash(f"Vous gérez maintenant : {entreprise.denomination}", "success")
    return redirect(url_for("tenant.dashboard"))


@bp.route("/cabinet/sortir")
@login_required
def cabinet_sortir():
    """Le cabinet quitte l'entreprise courante et revient à son portefeuille."""
    session.pop("cabinet_entreprise_id", None)
    return redirect(url_for("tenant.cabinet_dashboard"))


@bp.route("/cabinet/entreprise/nouvelle", methods=["GET", "POST"])
@login_required
def cabinet_entreprise_nouvelle():
    """Le cabinet ajoute une nouvelle entreprise cliente à son portefeuille.
    Crée un tenant rattaché (cabinet_id), avec ses catégories par défaut.
    Pas de nouvel utilisateur : c'est le cabinet qui gère l'entreprise."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t or not t.est_cabinet:
        flash("Réservé aux comptes cabinet.", "error")
        return redirect(url_for("tenant.dashboard"))
    if not current_user.is_tenant_admin:
        flash("Seul l'administrateur du cabinet peut ajouter une entreprise.", "error")
        return redirect(url_for("tenant.cabinet_dashboard"))

    # Blocage strict : le cabinet ne peut pas dépasser la limite de son palier.
    if not t.peut_ajouter_entreprise:
        flash(f"Vous avez atteint la limite de votre forfait "
              f"({t.limite_entreprises_effective} entreprises). "
              f"Pour en gérer davantage, passez au palier supérieur.", "error")
        return redirect(url_for("tenant.cabinet_dashboard"))

    from calculs_paie import CONVENTIONS_DISPONIBLES

    if request.method == "POST":
        denom = request.form.get("denomination", "").strip()
        if not denom:
            flash("La dénomination de l'entreprise est obligatoire.", "error")
            return render_template("tenant/cabinet_entreprise_form.html",
                cabinet=t, conventions=CONVENTIONS_DISPONIBLES)

        convention = request.form.get("convention", "AUCUNE").strip().upper()
        if convention not in CONVENTIONS_DISPONIBLES:
            convention = "AUCUNE"

        # Slug unique
        slug_base = denom.lower().replace(" ", "_")[:30]
        slug = slug_base
        i = 1
        while Tenant.query.filter_by(slug=slug).first():
            slug = f"{slug_base}_{i}"; i += 1

        # Créer l'entreprise rattachée au cabinet.
        # Elle hérite du plan du cabinet (forfait) et de son statut/expiration :
        # tant que le cabinet est à jour, ses entreprises le sont aussi.
        e = Tenant(
            slug=slug, denomination=denom.upper(),
            sigle=request.form.get("sigle", "").strip().upper(),
            activite=request.form.get("activite", "").strip(),
            nif=request.form.get("nif", "").strip(),
            numero_cnss=request.form.get("numero_cnss", "").strip(),
            telephone=request.form.get("telephone", "").strip(),
            ville=request.form.get("ville", "Libreville").strip() or "Libreville",
            pays="Gabon",
            convention=convention,
            cabinet_id=t.id,               # ← rattachement au cabinet
            plan_id=t.plan_id,             # hérite du forfait cabinet
            statut=t.statut,               # suit le statut du cabinet
            date_expiration=t.date_expiration,
        )
        e.generate_token()
        db.session.add(e)
        db.session.flush()

        # Catégories d'emploi par défaut (comme à l'inscription classique)
        for code, lib in [("C1", "Ouvriers"), ("C2", "Techniciens"),
                          ("C3", "Conducteurs de Travaux"), ("C4", "Cadres")]:
            db.session.add(CategorieEmploi(tenant_id=e.id, code=code, libelle=lib))

        db.session.commit()
        log_action("CREATE", "tenant", e.id,
                   f"Entreprise '{e.denomination}' ajoutée au cabinet {t.denomination}",
                   user_id=current_user.id, tenant_id=t.id)
        flash(f"Entreprise « {e.denomination} » ajoutée à votre portefeuille.", "success")
        return redirect(url_for("tenant.cabinet_dashboard"))

    return render_template("tenant/cabinet_entreprise_form.html",
        cabinet=t, conventions=CONVENTIONS_DISPONIBLES)




@bp.route("/cabinet/collaborateurs")
@tenant_required
def cabinet_collaborateurs():
    """Liste les collaborateurs du cabinet + le nombre d'entreprises assignées."""
    t = get_tenant()
    if not t or not t.est_cabinet or not current_user.is_tenant_admin:
        return redirect(url_for("tenant.cabinet_dashboard") if t and t.est_cabinet else url_for("tenant.dashboard"))
    from models import CollaborateurEntreprise
    collaborateurs = Utilisateur.query.filter_by(tenant_id=t.id).order_by(Utilisateur.nom).all()
    nb_entreprises = Tenant.query.filter_by(cabinet_id=t.id).count()
    infos = []
    for u in collaborateurs:
        nb = CollaborateurEntreprise.query.filter_by(utilisateur_id=u.id).count()
        infos.append({"user": u, "nb_assignees": nb, "admin": u.is_tenant_admin})
    return render_template("tenant/cabinet_collaborateurs.html",
                           tenant=t, collaborateurs=infos, nb_entreprises=nb_entreprises)


@bp.route("/cabinet/collaborateurs/<int:uid>/assigner", methods=["GET", "POST"])
@tenant_required
def cabinet_collaborateur_assigner(uid):
    """Assigne des entreprises à un collaborateur (cases à cocher)."""
    t = get_tenant()
    if not t or not t.est_cabinet or not current_user.is_tenant_admin:
        return redirect(url_for("tenant.dashboard"))
    u = Utilisateur.query.filter_by(id=uid, tenant_id=t.id).first_or_404()
    from models import CollaborateurEntreprise
    entreprises = Tenant.query.filter_by(cabinet_id=t.id).order_by(Tenant.denomination).all()

    if request.method == "POST":
        choisies = set(request.form.getlist("entreprises", type=int))
        actuelles = {a.entreprise_id: a for a in CollaborateurEntreprise.query.filter_by(utilisateur_id=u.id).all()}
        # Ajouter les nouvelles
        for eid in choisies:
            if eid not in actuelles:
                db.session.add(CollaborateurEntreprise(utilisateur_id=u.id, entreprise_id=eid))
        # Retirer les décochées
        for eid, a in actuelles.items():
            if eid not in choisies:
                db.session.delete(a)
        db.session.commit()
        log_action("UPDATE", "utilisateur", u.id,
                   f"Assignation de {len(choisies)} entreprise(s) à {u.nom_complet}",
                   user_id=current_user.id, tenant_id=t.id)
        db.session.commit()
        flash(f"{len(choisies)} entreprise(s) assignée(s) à {u.nom_complet}.", "success")
        return redirect(url_for("tenant.cabinet_collaborateurs"))

    assignees = {a.entreprise_id for a in CollaborateurEntreprise.query.filter_by(utilisateur_id=u.id).all()}
    return render_template("tenant/cabinet_collaborateur_assigner.html",
                           tenant=t, collaborateur=u, entreprises=entreprises, assignees=assignees)


@bp.route("/cabinet/collaborateurs/nouveau", methods=["POST"])
@tenant_required
def cabinet_collaborateur_nouveau():
    """Crée un collaborateur directement depuis la page cabinet, puis enchaîne sur l'assignation."""
    t = get_tenant()
    if not t or not t.est_cabinet or not current_user.is_tenant_admin:
        return redirect(url_for("tenant.dashboard"))
    # Limite d'utilisateurs du plan
    if t.plan and t.plan.max_utilisateurs:
        nb = Utilisateur.query.filter_by(tenant_id=t.id, actif=True).count()
        if nb >= t.plan.max_utilisateurs:
            flash(f"Limite atteinte — Plan « {t.plan.nom} » : {t.plan.max_utilisateurs} utilisateur(s) maximum. "
                  "Passez au plan supérieur pour en ajouter d'autres.", "error")
            return redirect(url_for("tenant.cabinet_collaborateurs"))
    email    = request.form.get("email", "").strip().lower()
    nom      = request.form.get("nom", "").strip()
    prenom   = request.form.get("prenom", "").strip()
    password = request.form.get("password", "")
    role     = request.form.get("role", "COMPTABLE").strip().upper()
    # Un collaborateur n'est jamais administrateur du cabinet
    if role not in ROLES_TENANT_AUTORISES or role == "TENANT_ADMIN":
        role = "COMPTABLE"
    if not email or not nom or not password:
        flash("Veuillez renseigner au moins le nom, l'email et le mot de passe.", "error")
        return redirect(url_for("tenant.cabinet_collaborateurs"))
    if len(password) < 8:
        flash("Le mot de passe doit contenir au moins 8 caractères.", "error")
        return redirect(url_for("tenant.cabinet_collaborateurs"))
    if Utilisateur.query.filter_by(email=email).first():
        flash("Cet email est déjà utilisé.", "error")
        return redirect(url_for("tenant.cabinet_collaborateurs"))
    u = Utilisateur(nom=nom, prenom=prenom, email=email, role=role,
                    tenant_id=t.id, actif=True, email_verifie=True)
    u.set_password(password)
    db.session.add(u); db.session.commit()
    log_action("CREATE", "utilisateur", u.id,
               f"Collaborateur {u.nom_complet} créé ({role})",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Collaborateur {u.nom_complet} créé. Assignez-lui maintenant des entreprises.", "success")
    return redirect(url_for("tenant.cabinet_collaborateur_assigner", uid=u.id))

