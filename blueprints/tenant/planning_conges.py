"""blueprints/tenant/planning_conges.py — Planning prévisionnel des congés et vue annuelle."""
from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from blueprints.tenant import bp
from core import tenant_required, get_tenant, _parse_date
from audit import log_action
from models import db, Salarie, PrevisionConge
import planning_conges as pc


def _annee(v):
    try:
        a = int(v)
    except (TypeError, ValueError):
        abort(404)
    if not 2000 <= a <= 2100:
        abort(404)
    return a


def _peut_valider():
    """La direction : administrateur de l'entreprise ou directeur."""
    return current_user.is_tenant_admin or (current_user.role_normalized == "DIRECTEUR")


def _retour(annee):
    return redirect(url_for("tenant.conges_previsionnel", annee=annee))


def _periode(pid):
    t = get_tenant()
    x = PrevisionConge.query.filter_by(id=pid, tenant_id=t.id).first_or_404()
    return t, x


def _executer(annee, fn, succes):
    try:
        res = fn()
    except pc.ErreurPlanning as e:
        db.session.rollback()
        flash(f"Impossible : {e}.", "error")
        return _retour(annee)
    db.session.commit()
    if succes:
        flash(succes(res) if callable(succes) else succes, "success")
    return _retour(annee)


# ═══════════════════════════ PLANNING PRÉVISIONNEL ═══════════════════════════
@bp.route("/conges/previsionnel")
@login_required
@tenant_required
def conges_previsionnel():
    t = get_tenant()
    annee = _annee(request.args.get("annee", date.today().year))
    p = pc.obtenir(t, annee)
    ctx = {"tenant": t, "annee": annee, "p": p, "annees": list(range(date.today().year - 1, date.today().year + 3)),
           "STATUTS": pc.STATUTS_PREVISION, "peut_valider": _peut_valider(),
           "salaries": Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").order_by(Salarie.nom).all()}
    if p is not None:
        ctrl = pc.controles(t, p)
        par_sal = {}
        for x in p.periodes:
            par_sal.setdefault(x.salarie_id, []).append(x)
        ctx.update(ctrl=ctrl, par_sal=par_sal, en_attente=pc.en_attente(p),
                   ouvertes=pc.propositions_ouvertes(p), mois_noms=pc.MOIS_FR)
    return render_template("tenant/conges_previsionnel.html", **ctx)


@bp.route("/conges/previsionnel/<int:annee>/periode", methods=["POST"])
@login_required
@tenant_required
def conges_previsionnel_ajouter(annee):
    t = get_tenant()
    annee = _annee(annee)
    s = Salarie.query.filter_by(id=request.form.get("salarie_id", type=int), tenant_id=t.id).first()
    debut, fin = _parse_date(request.form.get("date_debut")), _parse_date(request.form.get("date_fin"))

    def f():
        p = pc.obtenir(t, annee, creer=True)
        x = pc.ajouter(p, s, debut, fin, "RH", request.form.get("commentaire"), current_user)
        log_action("CREATE", "prevision_conge", x.id,
                   f"Planning {annee} : {s.nom_complet} du {debut:%d/%m} au {fin:%d/%m} ({x.jours} j)")
        return x
    return _executer(annee, f, lambda x: f"Période ajoutée au planning : {x.salarie.nom_complet}, {x.jours} jour(s).")


@bp.route("/conges/previsionnel/periode/<int:pid>/<action>", methods=["POST"])
@login_required
@tenant_required
def conges_previsionnel_periode(pid, action):
    t, x = _periode(pid)
    annee = x.planning.annee
    nom = x.salarie.nom_complet if x.salarie else ""
    if action == "retenir":
        return _executer(annee, lambda: (pc.retenir(x), log_action("UPDATE", "prevision_conge", x.id,
                                                                   f"Planning {annee} : période retenue — {nom}")),
                         f"Période de {nom} retenue.")
    if action == "refuser":
        motif = request.form.get("motif", "")
        return _executer(annee, lambda: (pc.refuser(x, motif), log_action("UPDATE", "prevision_conge", x.id,
                                                                          f"Planning {annee} : période refusée — {nom}")),
                         f"Période de {nom} refusée.")
    if action == "modifier":
        debut, fin = _parse_date(request.form.get("date_debut")), _parse_date(request.form.get("date_fin"))
        return _executer(annee, lambda: pc.modifier(x, debut, fin), f"Période de {nom} modifiée.")
    if action == "supprimer":
        return _executer(annee, lambda: (log_action("DELETE", "prevision_conge", x.id,
                                                    f"Planning {annee} : période supprimée — {nom}"), pc.supprimer(x)),
                         "Période retirée du planning.")
    if action == "demande":
        def f():
            c = pc.creer_demande(x, current_user)
            log_action("CREATE", "conge", c.id, f"Demande de congé créée depuis le planning {annee} — {nom} "
                                                f"({x.jours} j)")
            return c
        return _executer(annee, f, f"Demande de congé créée pour {nom} : approuvez-la dans Congés le moment venu.")
    abort(404)


@bp.route("/conges/previsionnel/<int:annee>/demandes", methods=["POST"])
@login_required
@tenant_required
def conges_previsionnel_demandes(annee):
    """Crée d'un coup les demandes de congé des périodes retenues qui commencent dans un mois."""
    t = get_tenant()
    annee = _annee(annee)
    mois = request.form.get("mois", type=int)
    if not mois or not 1 <= mois <= 12:
        flash("Choisissez le mois.", "error")
        return _retour(annee)

    def f():
        p = pc.obtenir(t, annee)
        if p is None:
            raise pc.ErreurPlanning("aucun planning pour cette année")
        faites, erreurs = 0, []
        for x in p.periodes:
            if x.statut == "RETENUE" and not x.conge_id and x.date_debut.month == mois:
                try:
                    with db.session.begin_nested():
                        pc.creer_demande(x, current_user)
                    faites += 1
                except pc.ErreurPlanning as e:
                    erreurs.append(f"{x.salarie.nom_complet} : {e}")
        if faites:
            log_action("CREATE", "conge", None, f"Planning {annee} : {faites} demande(s) créée(s) pour {pc.MOIS_FR[mois]}")
        for e in erreurs:
            flash(e, "error")
        return faites
    return _executer(annee, f, lambda n: f"{n} demande(s) de congé créée(s) pour {pc.MOIS_FR[mois]}.")


@bp.route("/conges/previsionnel/<int:annee>/parametres", methods=["POST"])
@login_required
@tenant_required
def conges_previsionnel_parametres(annee):
    t = get_tenant()
    annee = _annee(annee)
    seuil = request.form.get("seuil_absents_pct", type=int)
    limite = _parse_date(request.form.get("date_limite_propositions"))

    def f():
        p = pc.obtenir(t, annee, creer=True)
        if seuil is None or not 0 <= seuil <= 100:
            raise pc.ErreurPlanning("le seuil doit être compris entre 0 et 100 %")
        p.seuil_absents_pct = seuil
        p.date_limite_propositions = limite
    return _executer(annee, f, "Réglages du planning enregistrés.")


@bp.route("/conges/previsionnel/<int:annee>/valider", methods=["POST"])
@login_required
@tenant_required
def conges_previsionnel_valider(annee):
    if not _peut_valider():
        abort(403)
    t = get_tenant()
    annee = _annee(annee)
    p = pc.obtenir(t, annee)
    if p is None:
        abort(404)

    def f():
        pc.valider(p, current_user, request.form.get("commentaire"))
        log_action("VALIDATE", "planning_conges", p.id, f"Planning des congés {annee} validé")
    return _executer(annee, f, f"Planning des congés {annee} validé.")


@bp.route("/conges/previsionnel/<int:annee>/rouvrir", methods=["POST"])
@login_required
@tenant_required
def conges_previsionnel_rouvrir(annee):
    if not _peut_valider():
        abort(403)
    t = get_tenant()
    annee = _annee(annee)
    p = pc.obtenir(t, annee)
    if p is None:
        abort(404)

    def f():
        pc.rouvrir(p)
        log_action("UPDATE", "planning_conges", p.id, f"Planning des congés {annee} rouvert")
    return _executer(annee, f, f"Planning {annee} rouvert.")


# ═══════════════════════════ VUE ANNUELLE ═══════════════════════════
@bp.route("/conges/planning-annuel")
@login_required
@tenant_required
def conges_planning_annuel():
    t = get_tenant()
    annee = _annee(request.args.get("annee", date.today().year))
    site_id = request.args.get("site", type=int)
    categorie_id = request.args.get("categorie", type=int)
    choix = request.args.getlist("prevus")          # case à cocher + champ caché « 0 »
    avec_prevus = not choix or "1" in choix
    v = pc.vue_annuelle(t, annee, site_id, categorie_id, avec_prevus)
    return render_template("tenant/conges_planning_annuel.html", tenant=t, annee=annee, v=v, site_id=site_id,
                           categorie_id=categorie_id, avec_prevus=avec_prevus,
                           edite_le=date.today())
