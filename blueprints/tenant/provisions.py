"""blueprints/tenant/provisions.py — Provision pour congés payés.

  /provisions-conges                      arrêtés (liste) et calcul d'un nouvel arrêté
  /provisions-conges/<id>                 détail par salarié, écriture d'ajustement
  /provisions-conges/<id>/valider         fige l'arrêté (POST) ; /devalider pour le rouvrir
  /provisions-conges/<id>/supprimer       supprime un brouillon (POST)
  /provisions-conges/<id>/ecriture.txt    écriture au format Sage 100 (journal de paie)
  /provisions-conges/<id>/export.csv      détail par salarié
  /provisions-conges/<id>/imprimer        état signable
  /provisions-conges/comptes              comptes de l'écriture (POST)
  /provisions-conges/diviseur             diviseur de la provision, 24 par défaut (POST)
"""
import csv
import io
import json
from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort, Response
from flask_login import login_required, current_user

from blueprints.tenant import bp
from core import tenant_required, get_tenant, csv_safe
from audit import log_action
from models import db, utcnow, ProvisionConges
import provision_conges as pc

from calendrier_paie import MOIS  # noqa: E402
STATUTS = {"BROUILLON": ("Brouillon", "#92400e", "#fef3c7"), "VALIDE": ("Validé", "#065f46", "#d1fae5")}


def _auteur():
    return getattr(current_user, "nom_complet", None) or current_user.email


def _arrete(pid):
    t = get_tenant()
    return t, ProvisionConges.query.filter_by(id=pid, tenant_id=t.id).first_or_404()


def _ctx_detail(t, p):
    prec = pc.precedent(p)
    lignes = sorted(p.lignes, key=lambda l: (l.salarie.nom or "", l.salarie.prenom or ""))
    return dict(p=p, t=t, lignes=lignes, tot=pc.totaux(p), prec=prec, tot_prec=pc.totaux(prec),
                ecriture=pc.ecriture(p, t), statuts=STATUTS, mois=MOIS, detail=lambda l: json.loads(l.detail or "{}"))


@bp.route("/provisions-conges")
@login_required
@tenant_required
def provisions_conges():
    t = get_tenant()
    arretes = (ProvisionConges.query.filter_by(tenant_id=t.id)
               .order_by(ProvisionConges.annee.desc(), ProvisionConges.mois.desc()).all())
    today = date.today()
    defaut = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return render_template("tenant/provisions_conges.html", arretes=arretes, totaux=pc.totaux, statuts=STATUTS,
                           mois=MOIS, defaut=defaut, annees=range(today.year + 1, today.year - 4, -1),
                           comptes=pc.comptes(t), liste_comptes=pc.COMPTES, diviseur=pc.diviseur(t))


@bp.route("/provisions-conges/calculer", methods=["POST"])
@login_required
@tenant_required
def provision_conges_calculer():
    if not current_user.can_edit:
        abort(403)
    t = get_tenant()
    annee, mois = request.form.get("annee", type=int), request.form.get("mois", type=int)
    if not annee or not mois or not 1 <= mois <= 12:
        flash("Choisissez le mois de l'arrêté.", "error")
        return redirect(url_for("tenant.provisions_conges"))
    try:
        p = pc.calculer(t, annee, mois, _auteur())
    except ValueError as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("tenant.provisions_conges"))
    db.session.commit()
    tot = pc.totaux(p)
    log_action("CREATE", "provision_conges", p.id, f"Provision congés payés au {p.date_arrete:%d/%m/%Y} calculée",
               apres={"salaries": len(p.lignes), "provision": tot["provision"], "total": tot["total"]})
    flash(f"Provision au {p.date_arrete:%d/%m/%Y} calculée : {len(p.lignes)} salarié(s), "
          f"{tot['total']:,.0f} FCFA charges comprises.".replace(",", " "), "success")
    return redirect(url_for("tenant.provision_conges_detail", pid=p.id))


@bp.route("/provisions-conges/<int:pid>")
@login_required
@tenant_required
def provision_conges_detail(pid):
    t, p = _arrete(pid)
    return render_template("tenant/provision_conges_detail.html", **_ctx_detail(t, p))


@bp.route("/provisions-conges/<int:pid>/valider", methods=["POST"])
@login_required
@tenant_required
def provision_conges_valider(pid):
    if not current_user.can_edit:
        abort(403)
    t, p = _arrete(pid)
    if p.statut == "VALIDE":
        flash("Arrêté déjà validé.", "info")
    else:
        p.statut, p.valide_par, p.date_validation = "VALIDE", _auteur(), utcnow()
        db.session.commit()
        log_action("VALIDATE", "provision_conges", p.id, f"Provision congés payés au {p.date_arrete:%d/%m/%Y} validée")
        flash("Arrêté validé : il sert de point de départ à l'écriture de l'arrêté suivant.", "success")
    return redirect(url_for("tenant.provision_conges_detail", pid=p.id))


@bp.route("/provisions-conges/<int:pid>/devalider", methods=["POST"])
@login_required
@tenant_required
def provision_conges_devalider(pid):
    if not current_user.can_edit:
        abort(403)
    t, p = _arrete(pid)
    suivant = (ProvisionConges.query.filter(ProvisionConges.tenant_id == t.id, ProvisionConges.statut == "VALIDE",
                                            ProvisionConges.annee * 100 + ProvisionConges.mois > p.annee * 100 + p.mois)
               .first())
    if suivant is not None:
        flash(f"Un arrêté postérieur est validé ({MOIS[suivant.mois]} {suivant.annee}) : annulez d'abord sa validation.",
              "error")
    elif p.statut == "VALIDE":
        p.statut, p.valide_par, p.date_validation = "BROUILLON", None, None
        db.session.commit()
        log_action("UPDATE", "provision_conges", p.id, "Validation de l'arrêté de provision annulée")
        flash("Validation annulée : l'arrêté peut être recalculé.", "success")
    return redirect(url_for("tenant.provision_conges_detail", pid=p.id))


@bp.route("/provisions-conges/<int:pid>/supprimer", methods=["POST"])
@login_required
@tenant_required
def provision_conges_supprimer(pid):
    if not current_user.can_edit:
        abort(403)
    t, p = _arrete(pid)
    if p.statut != "BROUILLON":
        flash("Un arrêté validé ne se supprime pas : annulez d'abord sa validation.", "error")
        return redirect(url_for("tenant.provision_conges_detail", pid=p.id))
    libelle = f"{p.date_arrete:%d/%m/%Y}"
    db.session.delete(p)
    db.session.commit()
    log_action("DELETE", "provision_conges", pid, f"Arrêté de provision au {libelle} supprimé")
    flash("Arrêté supprimé.", "success")
    return redirect(url_for("tenant.provisions_conges"))


@bp.route("/provisions-conges/<int:pid>/ecriture.txt")
@login_required
@tenant_required
def provision_conges_ecriture(pid):
    t, p = _arrete(pid)
    log_action("EXPORT", "provision_conges", p.id, "Écriture de provision congés exportée")
    return Response(pc.ecriture_sage(p, t), mimetype="text/plain; charset=windows-1252",
                    headers={"Content-Disposition": f"attachment; filename=provision_conges_{p.mois:02d}{p.annee}.txt"})


@bp.route("/provisions-conges/<int:pid>/export.csv")
@login_required
@tenant_required
def provision_conges_csv(pid):
    t, p = _arrete(pid)
    ctx = _ctx_detail(t, p)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Matricule", "Nom", "National", "Jours acquis (année)", "Jours pris (année)", "Reliquat N-1",
                "Jours dus", "Base journalière", "Provision", "Charges sociales", "Taxes", "Total", "Base"])
    for l in ctx["lignes"]:
        d = ctx["detail"](l)
        w.writerow([csv_safe(l.salarie.matricule), csv_safe(l.salarie.nom_complet), "oui" if l.national else "non",
                    d.get("acquis", ""), d.get("pris", ""), d.get("reliquat", ""), float(l.jours),
                    f"{float(l.base_journaliere):.2f}".replace(".", ","), int(l.provision), int(l.charges),
                    int(l.taxes), int(l.provision + l.charges + l.taxes), d.get("source", "")])
    return Response("﻿" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=provision_conges_{p.mois:02d}{p.annee}.csv"})


@bp.route("/provisions-conges/<int:pid>/imprimer")
@login_required
@tenant_required
def provision_conges_imprimer(pid):
    t, p = _arrete(pid)
    return render_template("tenant/provision_conges_print.html", now=utcnow(), **_ctx_detail(t, p))


@bp.route("/provisions-conges/comptes", methods=["POST"])
@login_required
@tenant_required
def provision_conges_comptes():
    if not current_user.can_edit:
        abort(403)
    t = get_tenant()
    if request.form.get("action") == "defaut":
        t.comptes_provision_conges = None
    else:
        try:
            t.comptes_provision_conges = pc.lire_comptes(request.form)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("tenant.provisions_conges"))
    db.session.commit()
    log_action("UPDATE", "parametres", t.id, "Comptes de la provision pour congés payés modifiés")
    flash("Comptes enregistrés.", "success")
    return redirect(url_for("tenant.provisions_conges"))


@bp.route("/provisions-conges/diviseur", methods=["POST"])
@login_required
@tenant_required
def provision_conges_diviseur():
    if not current_user.can_edit:
        abort(403)
    t = get_tenant()
    try:
        d = pc.lire_diviseur(request.form.get("diviseur"))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("tenant.provisions_conges"))
    avant = pc.diviseur(t)
    t.diviseur_provision_conges = d
    db.session.commit()
    log_action("UPDATE", "parametres", t.id, f"Diviseur de la provision pour congés : {avant:g} → {d:g}",
               avant={"diviseur": avant}, apres={"diviseur": d})
    flash(f"Diviseur enregistré : {d:g} jours. Il s'applique aux prochains calculs (recalculez les brouillons ; "
          f"les arrêtés validés gardent leur diviseur).", "success")
    return redirect(url_for("tenant.provisions_conges"))
