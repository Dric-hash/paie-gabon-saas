# -*- coding: utf-8 -*-
"""Développement RH (gestion de carrière — Phase 3) : formations, compétences,
évaluations.

Aucune de ces fonctions n'intervient dans le calcul de la paie. Les formations
et les évaluations alimentent la frise de carrière. Isolation stricte par tenant.
"""
from datetime import datetime, date, timedelta

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from blueprints.tenant import bp, _doc_response
from core import get_tenant, _parse_date, chemin_interne
from audit import log_action
from models import (db, Salarie, Formation, Competence, CompetenceSalarie,
                    Evaluation)


def _num(v):
    """Convertit une saisie en nombre souple (virgule ou point), None si vide."""
    s = (v or "").strip().replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# FORMATIONS
# ══════════════════════════════════════════════════════════════════════════════

def formations_a_renouveler(t, dans_jours=60, ref=None):
    """Formations certifiantes dont la validité expire bientôt ou est dépassée.
    Partagé avec le tableau de bord (alerte « formation à renouveler »)."""
    ref = ref or date.today()
    limite = ref + timedelta(days=dans_jours)
    return (Formation.query.filter_by(tenant_id=t.id)
            .filter(Formation.date_expiration.isnot(None),
                    Formation.date_expiration <= limite,
                    Formation.statut != "ANNULEE")
            .order_by(Formation.date_expiration).all())


@bp.route("/formations")
@login_required
def formations():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    statut = request.args.get("statut", "")
    q = Formation.query.filter_by(tenant_id=t.id)
    if statut in Formation.STATUTS:
        q = q.filter_by(statut=statut)
    liste = q.order_by(Formation.date_debut.desc().nullslast()).all()
    a_renouveler = {f.id for f in formations_a_renouveler(t)}
    return render_template("tenant/formations.html", tenant=t, formations=liste,
                           a_renouveler=a_renouveler, statut=statut,
                           STATUTS=Formation.STATUTS, now=datetime.now())


def _formation_depuis_form(t, f):
    """Remplit un objet Formation à partir du formulaire (création ou édition)."""
    f.intitule = (request.form.get("intitule") or "").strip()
    f.organisme = (request.form.get("organisme") or "").strip() or None
    f.type = request.form.get("type") if request.form.get("type") in Formation.TYPES else "EXTERNE"
    f.date_debut = _parse_date(request.form.get("date_debut"))
    f.date_fin = _parse_date(request.form.get("date_fin"))
    f.duree_heures = _num(request.form.get("duree_heures"))
    f.cout = _num(request.form.get("cout"))
    f.statut = request.form.get("statut") if request.form.get("statut") in Formation.STATUTS else "PLANIFIEE"
    f.certifiante = request.form.get("certifiante") == "1"
    f.date_expiration = _parse_date(request.form.get("date_expiration")) if f.certifiante else None
    f.resultat = (request.form.get("resultat") or "").strip() or None
    f.details = (request.form.get("details") or "").strip() or None


@bp.route("/salaries/<int:id>/formations/nouvelle", methods=["GET", "POST"])
@login_required
def formation_nouvelle(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if request.method == "POST":
        f = Formation(tenant_id=t.id, salarie_id=s.id,
                      cree_par=current_user.id,
                      cree_par_nom=getattr(current_user, "nom_complet", None) or current_user.email)
        _formation_depuis_form(t, f)
        if not f.intitule:
            flash("L'intitulé de la formation est obligatoire.", "error")
            return render_template("tenant/formation_form.html", tenant=t, salarie=s,
                                   formation=None, TYPES=Formation.TYPES,
                                   STATUTS=Formation.STATUTS, now=datetime.now())
        db.session.add(f)
        db.session.commit()
        log_action("CREATE", "formation", f.id, f"Formation « {f.intitule} » — {s.nom_complet}")
        flash("Formation enregistrée.", "success")
        return redirect(url_for("tenant.carriere", id=s.id))
    return render_template("tenant/formation_form.html", tenant=t, salarie=s,
                           formation=None, TYPES=Formation.TYPES,
                           STATUTS=Formation.STATUTS, now=datetime.now())


@bp.route("/formations/<int:id>/modifier", methods=["GET", "POST"])
@login_required
def formation_modifier(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    f = Formation.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    s = f.salarie
    if request.method == "POST":
        _formation_depuis_form(t, f)
        if not f.intitule:
            flash("L'intitulé de la formation est obligatoire.", "error")
        else:
            db.session.commit()
            log_action("UPDATE", "formation", f.id, f"Formation modifiée — {s.nom_complet}")
            flash("Formation mise à jour.", "success")
            return redirect(url_for("tenant.carriere", id=s.id))
    return render_template("tenant/formation_form.html", tenant=t, salarie=s,
                           formation=f, TYPES=Formation.TYPES,
                           STATUTS=Formation.STATUTS, now=datetime.now())


@bp.route("/formations/<int:id>/supprimer", methods=["POST"])
@login_required
def formation_supprimer(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    f = Formation.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    sid = f.salarie_id
    db.session.delete(f)
    db.session.commit()
    log_action("DELETE", "formation", id, "Formation supprimée")
    flash("Formation supprimée.", "success")
    return redirect(chemin_interne(request.form.get("retour"), url_for("tenant.carriere", id=sid)))


@bp.route("/formations/<int:id>/attestation")
@login_required
def formation_attestation(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    f = Formation.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    from documents_rh import generer_attestation_formation_pdf
    pdf = generer_attestation_formation_pdf(t, f.salarie, f)
    log_action("EXPORT", "formation", f.id, "Attestation de formation éditée")
    nom = f"attestation_formation_{(f.salarie.nom if f.salarie else 'salarie')}_{f.id}.pdf"
    return _doc_response(pdf, nom)


# ══════════════════════════════════════════════════════════════════════════════
# COMPÉTENCES
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/competences", methods=["GET", "POST"])
@login_required
def competences():
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        if not current_user.can_edit:
            abort(403)
        action = request.form.get("action", "ajouter")
        if action == "ajouter":
            lib = (request.form.get("libelle") or "").strip()
            cat = (request.form.get("categorie") or "").strip() or None
            if not lib:
                flash("Le libellé de la compétence est obligatoire.", "error")
            elif Competence.query.filter_by(tenant_id=t.id, libelle=lib).first():
                flash(f"La compétence « {lib} » existe déjà.", "error")
            else:
                db.session.add(Competence(tenant_id=t.id, libelle=lib, categorie=cat, actif=True))
                db.session.commit()
                log_action("CREATE", "competence", None, f"Compétence « {lib} »")
                flash("Compétence ajoutée.", "success")
        elif action == "toggle":
            c = Competence.query.filter_by(id=request.form.get("id", type=int), tenant_id=t.id).first()
            if c:
                c.actif = not c.actif
                db.session.commit()
                flash(f"Compétence « {c.libelle} » {'activée' if c.actif else 'désactivée'}.", "success")
        return redirect(url_for("tenant.competences"))

    liste = (Competence.query.filter_by(tenant_id=t.id)
             .order_by(Competence.categorie, Competence.libelle).all())
    return render_template("tenant/competences_referentiel.html", tenant=t,
                           competences=liste, CATEGORIES=Competence.CATEGORIES)


@bp.route("/salaries/<int:id>/competences", methods=["GET", "POST"])
@login_required
def competences_salarie(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    refs = (Competence.query.filter_by(tenant_id=t.id, actif=True)
            .order_by(Competence.categorie, Competence.libelle).all())
    existantes = {cs.competence_id: cs for cs in
                  CompetenceSalarie.query.filter_by(tenant_id=t.id, salarie_id=s.id).all()}

    if request.method == "POST":
        if not current_user.can_edit:
            abort(403)
        auj = date.today()
        qui = getattr(current_user, "nom_complet", None) or current_user.email
        for c in refs:
            val = request.form.get(f"niveau_{c.id}", type=int)
            cs = existantes.get(c.id)
            if val and 1 <= val <= 4:
                if cs:
                    if cs.niveau != val:
                        cs.niveau = val
                        cs.date_evaluation = auj
                        cs.evaluateur_nom = qui
                else:
                    db.session.add(CompetenceSalarie(
                        tenant_id=t.id, salarie_id=s.id, competence_id=c.id,
                        niveau=val, date_evaluation=auj, evaluateur_nom=qui))
            elif cs and not val:
                # Niveau remis à vide → on retire l'évaluation de cette compétence
                db.session.delete(cs)
        db.session.commit()
        log_action("UPDATE", "competences_salarie", s.id, f"Cartographie compétences — {s.nom_complet}")
        flash("Compétences du salarié enregistrées.", "success")
        return redirect(url_for("tenant.carriere", id=s.id))

    return render_template("tenant/competences_salarie.html", tenant=t, salarie=s,
                           competences=refs, existantes=existantes,
                           NIVEAUX=CompetenceSalarie.NIVEAUX)


# ══════════════════════════════════════════════════════════════════════════════
# ÉVALUATIONS
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/evaluations")
@login_required
def evaluations():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    liste = (Evaluation.query.filter_by(tenant_id=t.id)
             .order_by(Evaluation.date_evaluation.desc().nullslast()).all())
    return render_template("tenant/evaluations.html", tenant=t, evaluations=liste,
                           NOTES=Evaluation.NOTES, now=datetime.now())


def _evaluation_depuis_form(e):
    e.type = request.form.get("type") if request.form.get("type") in Evaluation.TYPES else "ANNUELLE"
    e.date_evaluation = _parse_date(request.form.get("date_evaluation")) or date.today()
    e.periode = (request.form.get("periode") or "").strip() or None
    note = (request.form.get("note_globale") or "").strip().upper()
    e.note_globale = note if note in Evaluation.NOTES else None
    e.evaluateur_nom = (request.form.get("evaluateur_nom") or "").strip() or None
    e.points_forts = (request.form.get("points_forts") or "").strip() or None
    e.axes_amelioration = (request.form.get("axes_amelioration") or "").strip() or None
    e.objectifs = (request.form.get("objectifs") or "").strip() or None
    e.commentaire_salarie = (request.form.get("commentaire_salarie") or "").strip() or None
    e.statut = request.form.get("statut") if request.form.get("statut") in ("PLANIFIEE", "REALISEE") else "PLANIFIEE"


@bp.route("/salaries/<int:id>/evaluations/nouvelle", methods=["GET", "POST"])
@login_required
def evaluation_nouvelle(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if request.method == "POST":
        e = Evaluation(tenant_id=t.id, salarie_id=s.id,
                       evaluateur_id=current_user.id,
                       cree_par=current_user.id,
                       cree_par_nom=getattr(current_user, "nom_complet", None) or current_user.email)
        _evaluation_depuis_form(e)
        if not e.evaluateur_nom:
            e.evaluateur_nom = getattr(current_user, "nom_complet", None) or current_user.email
        db.session.add(e)
        db.session.commit()
        log_action("CREATE", "evaluation", e.id, f"{e.type_libelle} — {s.nom_complet}")
        flash("Évaluation enregistrée.", "success")
        return redirect(url_for("tenant.carriere", id=s.id))
    return render_template("tenant/evaluation_form.html", tenant=t, salarie=s,
                           evaluation=None, TYPES=Evaluation.TYPES,
                           NOTES=Evaluation.NOTES, now=datetime.now())


@bp.route("/evaluations/<int:id>/modifier", methods=["GET", "POST"])
@login_required
def evaluation_modifier(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    e = Evaluation.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    s = e.salarie
    if request.method == "POST":
        _evaluation_depuis_form(e)
        db.session.commit()
        log_action("UPDATE", "evaluation", e.id, f"Évaluation modifiée — {s.nom_complet}")
        flash("Évaluation mise à jour.", "success")
        return redirect(url_for("tenant.carriere", id=s.id))
    return render_template("tenant/evaluation_form.html", tenant=t, salarie=s,
                           evaluation=e, TYPES=Evaluation.TYPES,
                           NOTES=Evaluation.NOTES, now=datetime.now())


@bp.route("/evaluations/<int:id>/supprimer", methods=["POST"])
@login_required
def evaluation_supprimer(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    e = Evaluation.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    sid = e.salarie_id
    db.session.delete(e)
    db.session.commit()
    log_action("DELETE", "evaluation", id, "Évaluation supprimée")
    flash("Évaluation supprimée.", "success")
    return redirect(chemin_interne(request.form.get("retour"), url_for("tenant.carriere", id=sid)))


@bp.route("/evaluations/<int:id>/pdf")
@login_required
def evaluation_pdf(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    e = Evaluation.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    from documents_rh import generer_compte_rendu_evaluation_pdf
    pdf = generer_compte_rendu_evaluation_pdf(t, e.salarie, e)
    log_action("EXPORT", "evaluation", e.id, "Compte rendu d'évaluation édité")
    nom = f"evaluation_{(e.salarie.nom if e.salarie else 'salarie')}_{e.id}.pdf"
    return _doc_response(pdf, nom)
