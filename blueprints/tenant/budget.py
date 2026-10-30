"""blueprints/tenant/budget.py — Budget de la masse salariale et suivi budget / réalisé.

  /budget-masse-salariale                         budgets (liste) et préparation d'un budget annuel
  /budget-masse-salariale/<id>                    suivi mensuel, cumul, alerte, par site, lignes
  /budget-masse-salariale/<id>/ligne              ajout ou modification d'une ligne (POST, brouillon)
  /budget-masse-salariale/<id>/ligne/<lid>/supprimer
  /budget-masse-salariale/<id>/parametres         seuil d'alerte, commentaire (POST)
  /budget-masse-salariale/<id>/adopter | rouvrir | supprimer (POST)
  /budget-masse-salariale/<id>/export.csv         suivi et lignes
  /budget-masse-salariale/<id>/imprimer           état imprimable
"""
import csv
import io
from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort, Response
from flask_login import login_required, current_user

from blueprints.tenant import bp
from core import tenant_required, get_tenant, csv_safe
from audit import log_action
from models import db, utcnow, BudgetMasseSalariale, LigneBudget, Site
import budget_masse_salariale as bms

STATUTS = {"BROUILLON": ("Brouillon", "#92400e", "#fef3c7"), "ADOPTE": ("Adopté", "#065f46", "#d1fae5")}
MOIS_LONGS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
              "novembre", "décembre"]


def _auteur():
    return getattr(current_user, "nom_complet", None) or current_user.email


def _budget(bid):
    t = get_tenant()
    return t, BudgetMasseSalariale.query.filter_by(id=bid, tenant_id=t.id).first_or_404()


def _nombre(nom, defaut=None):
    v = (request.form.get(nom) or "").replace(" ", "").replace(" ", "").replace(",", ".").strip()
    if not v:
        return defaut
    try:
        return float(v)
    except ValueError:
        return None


def _mois(nom):
    v = request.form.get(nom, type=int)
    return v if v and 1 <= v <= 12 else None


def _edition(b):
    if not current_user.can_edit:
        abort(403)
    if b.statut != "BROUILLON":
        flash("Budget adopté : rouvrez-le pour le modifier.", "error")
        return False
    return True


@bp.route("/budget-masse-salariale")
@login_required
@tenant_required
def budgets_masse_salariale():
    t = get_tenant()
    budgets = BudgetMasseSalariale.query.filter_by(tenant_id=t.id).order_by(BudgetMasseSalariale.annee.desc()).all()
    an = date.today().year
    return render_template("tenant/budgets_masse_salariale.html", budgets=budgets, statuts=STATUTS, suivi=bms.suivi,
                           annees=range(an + 1, an - 3, -1), annee_defaut=an + (1 if date.today().month >= 10 else 0),
                           mois=MOIS_LONGS)


@bp.route("/budget-masse-salariale/preparer", methods=["POST"])
@login_required
@tenant_required
def budget_masse_salariale_preparer():
    if not current_user.can_edit:
        abort(403)
    t = get_tenant()
    annee = request.form.get("annee", type=int)
    aug, seuil = _nombre("augmentation_pct", 0), _nombre("seuil_alerte_pct", 5)
    mois_aug = _mois("mois_augmentation")
    erreurs = []
    if not annee:
        erreurs.append("Choisissez l'année.")
    if aug is None or not -50 <= aug <= 100:
        erreurs.append("Augmentation générale : un pourcentage entre −50 et 100.")
    elif aug and not mois_aug:
        erreurs.append("Indiquez le mois d'effet de l'augmentation générale.")
    if seuil is None or not 0 <= seuil <= 100:
        erreurs.append("Seuil d'alerte : un pourcentage entre 0 et 100.")
    if erreurs:
        for e in erreurs:
            flash(e, "error")
        return redirect(url_for("tenant.budgets_masse_salariale"))
    try:
        b = bms.preparer(t, annee, _auteur(), augmentation_pct=aug, mois_augmentation=mois_aug, seuil=seuil)
    except ValueError as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("tenant.budgets_masse_salariale"))
    db.session.commit()
    s = bms.suivi(b)
    log_action("CREATE", "budget_masse_salariale", b.id, f"Budget de masse salariale {annee} préparé",
               apres={"lignes": len(b.lignes), "cout_annuel": s["total_budget"], "augmentation_pct": aug})
    flash(f"Budget {annee} préparé depuis les contrats : {len(b.lignes)} ligne(s), coût employeur "
          f"{s['total_budget']:,.0f} FCFA. Ajoutez les recrutements prévus puis adoptez-le.".replace(",", " "),
          "success")
    return redirect(url_for("tenant.budget_masse_salariale", bid=b.id))


@bp.route("/budget-masse-salariale/<int:bid>")
@login_required
@tenant_required
def budget_masse_salariale(bid):
    t, b = _budget(bid)
    s = bms.suivi(b)
    lignes = sorted(b.lignes, key=lambda l: (l.salarie_id is None, (l.libelle or "").upper()))
    return render_template("tenant/budget_masse_salariale.html", b=b, s=s, sites=bms.par_site(b, s["realise"]),
                           lignes=lignes, mensuel=bms.mensuel, statuts=STATUTS, mois=MOIS_LONGS,
                           mois_courts=bms.MOIS, liste_sites=Site.query.filter_by(tenant_id=t.id).order_by(Site.nom).all())


@bp.route("/budget-masse-salariale/<int:bid>/ligne", methods=["POST"])
@login_required
@tenant_required
def budget_masse_salariale_ligne(bid):
    t, b = _budget(bid)
    if not _edition(b):
        return redirect(url_for("tenant.budget_masse_salariale", bid=b.id))
    lid = request.form.get("ligne_id", type=int)
    l = LigneBudget.query.filter_by(id=lid, budget_id=b.id).first_or_404() if lid else None
    libelle = (request.form.get("libelle") or "").strip()[:200] or (l.libelle if l else "")
    base, aug = _nombre("salaire_base"), _nombre("augmentation_pct", 0)
    deb, fin, mois_aug = _mois("mois_debut"), _mois("mois_fin"), _mois("mois_augmentation")
    site_id = request.form.get("site_id", type=int)
    site = Site.query.filter_by(id=site_id, tenant_id=t.id).first() if site_id else None
    erreurs = []
    if not libelle:
        erreurs.append("Indiquez le poste ou le salarié.")
    if base is None or base <= 0:
        erreurs.append("Le salaire de base mensuel doit être positif.")
    if not deb or not fin or deb > fin:
        erreurs.append("Mois de début et de fin : le début doit précéder la fin.")
    if aug is None or not -50 <= aug <= 100:
        erreurs.append("Augmentation : un pourcentage entre −50 et 100.")
    elif aug and not mois_aug:
        erreurs.append("Indiquez le mois d'effet de l'augmentation.")
    if erreurs:
        for e in erreurs:
            flash(e, "error")
        return redirect(url_for("tenant.budget_masse_salariale", bid=b.id))
    avant = bms.suivi(b)["total_budget"]
    if l is None:
        l = LigneBudget(tenant_id=t.id, budget_id=b.id, libelle=libelle)
        db.session.add(l)
        l.budget = b
    l.libelle, l.site_id, l.mois_debut, l.mois_fin = libelle, (site.id if site else None), deb, fin
    l.salaire_base, l.augmentation_pct, l.mois_augmentation = base, aug or 0, (mois_aug if aug else None)
    bms.calculer_ligne(l, t)
    db.session.commit()
    apres = bms.suivi(b)["total_budget"]
    log_action("UPDATE" if lid else "CREATE", "budget_masse_salariale", b.id,
               f"Budget {b.annee} : ligne « {libelle} » {'modifiée' if lid else 'ajoutée'}",
               avant={"cout_annuel": avant}, apres={"cout_annuel": apres})
    flash(f"Ligne « {libelle} » enregistrée. Coût annuel du budget : {apres:,.0f} FCFA.".replace(",", " "), "success")
    return redirect(url_for("tenant.budget_masse_salariale", bid=b.id))


@bp.route("/budget-masse-salariale/<int:bid>/ligne/<int:lid>/supprimer", methods=["POST"])
@login_required
@tenant_required
def budget_masse_salariale_ligne_supprimer(bid, lid):
    t, b = _budget(bid)
    if _edition(b):
        l = LigneBudget.query.filter_by(id=lid, budget_id=b.id).first_or_404()
        libelle = l.libelle
        db.session.delete(l)
        db.session.commit()
        log_action("DELETE", "budget_masse_salariale", b.id, f"Budget {b.annee} : ligne « {libelle} » supprimée")
        flash(f"Ligne « {libelle} » supprimée.", "success")
    return redirect(url_for("tenant.budget_masse_salariale", bid=b.id))


@bp.route("/budget-masse-salariale/<int:bid>/parametres", methods=["POST"])
@login_required
@tenant_required
def budget_masse_salariale_parametres(bid):
    if not current_user.can_edit:
        abort(403)
    t, b = _budget(bid)
    seuil = _nombre("seuil_alerte_pct")
    if seuil is None or not 0 <= seuil <= 100:
        flash("Seuil d'alerte : un pourcentage entre 0 et 100.", "error")
    else:
        b.seuil_alerte_pct = seuil
        b.commentaire = (request.form.get("commentaire") or "").strip()[:500] or None
        db.session.commit()
        log_action("UPDATE", "budget_masse_salariale", b.id, f"Budget {b.annee} : seuil d'alerte {seuil:g} %")
        flash("Paramètres du budget enregistrés.", "success")
    return redirect(url_for("tenant.budget_masse_salariale", bid=b.id))


@bp.route("/budget-masse-salariale/<int:bid>/adopter", methods=["POST"])
@login_required
@tenant_required
def budget_masse_salariale_adopter(bid):
    t, b = _budget(bid)
    if _edition(b):
        if not b.lignes:
            flash("Le budget n'a aucune ligne.", "error")
        else:
            b.statut, b.adopte_par, b.date_adoption = "ADOPTE", _auteur(), utcnow()
            db.session.commit()
            log_action("VALIDATE", "budget_masse_salariale", b.id, f"Budget de masse salariale {b.annee} adopté")
            flash("Budget adopté : il est suivi mois par mois et le contrôle de la paie signale un dépassement.",
                  "success")
    return redirect(url_for("tenant.budget_masse_salariale", bid=b.id))


@bp.route("/budget-masse-salariale/<int:bid>/rouvrir", methods=["POST"])
@login_required
@tenant_required
def budget_masse_salariale_rouvrir(bid):
    if not current_user.can_edit:
        abort(403)
    t, b = _budget(bid)
    if b.statut == "ADOPTE":
        b.statut, b.adopte_par, b.date_adoption = "BROUILLON", None, None
        db.session.commit()
        log_action("UPDATE", "budget_masse_salariale", b.id, f"Budget {b.annee} rouvert")
        flash("Budget rouvert : il peut être modifié (le suivi et l'alerte sont suspendus).", "success")
    return redirect(url_for("tenant.budget_masse_salariale", bid=b.id))


@bp.route("/budget-masse-salariale/<int:bid>/supprimer", methods=["POST"])
@login_required
@tenant_required
def budget_masse_salariale_supprimer(bid):
    t, b = _budget(bid)
    if not _edition(b):
        return redirect(url_for("tenant.budget_masse_salariale", bid=b.id))
    annee = b.annee
    db.session.delete(b)
    db.session.commit()
    log_action("DELETE", "budget_masse_salariale", bid, f"Budget de masse salariale {annee} supprimé")
    flash(f"Budget {annee} supprimé.", "success")
    return redirect(url_for("tenant.budgets_masse_salariale"))


@bp.route("/budget-masse-salariale/<int:bid>/export.csv")
@login_required
@tenant_required
def budget_masse_salariale_csv(bid):
    t, b = _budget(bid)
    s = bms.suivi(b)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow([f"Budget de masse salariale {b.annee}", STATUTS[b.statut][0]])
    w.writerow(["Mois", "Budget brut", "Budget coût employeur", "Réalisé brut", "Réalisé coût employeur", "Écart",
                "Écart %"])
    for x in s["lignes"]:
        w.writerow([MOIS_LONGS[x["mois"]], round(x["budget_brut"]), round(x["budget_cout"]),
                    "" if x["realise_brut"] is None else round(x["realise_brut"]),
                    "" if x["realise_cout"] is None else round(x["realise_cout"]),
                    "" if x["ecart"] is None else round(x["ecart"]),
                    "" if x["ecart_pct"] is None else f"{x['ecart_pct']:.1f}".replace(".", ",")])
    w.writerow([])
    w.writerow(["Ligne", "Site", "Du mois", "Au mois", "Salaire de base", "Augmentation %", "À partir de",
                "Coût annuel"] + bms.MOIS[1:])
    for l in sorted(b.lignes, key=lambda l: (l.libelle or "").upper()):
        m = bms.mensuel(l)
        w.writerow([csv_safe(l.libelle), csv_safe(l.site.nom if l.site else ""), l.mois_debut, l.mois_fin,
                    round(float(l.salaire_base)), float(l.augmentation_pct or 0), l.mois_augmentation or "",
                    sum(v[1] for v in m.values())] + [m.get(i, [0, 0])[1] for i in range(1, 13)])
    return Response("﻿" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=budget_masse_salariale_{b.annee}.csv"})


@bp.route("/budget-masse-salariale/<int:bid>/imprimer")
@login_required
@tenant_required
def budget_masse_salariale_imprimer(bid):
    t, b = _budget(bid)
    s = bms.suivi(b)
    return render_template("tenant/budget_masse_salariale_print.html", b=b, t=t, s=s,
                           sites=bms.par_site(b, s["realise"]), statuts=STATUTS, mois=MOIS_LONGS, now=utcnow())
