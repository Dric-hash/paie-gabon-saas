# -*- coding: utf-8 -*-
"""Cycle de paie : vue annuelle (mois, trimestres, DAS) et liste de contrôle du mois."""
from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from blueprints.tenant import bp
from core import tenant_required, get_tenant
from audit import log_action
from models import db, PeriodePaie
import cycle_paie as cp


def _peut_saisir():
    # même règle que la garde globale des rôles en lecture seule (COMPTABLE, DIRECTEUR)
    return bool(current_user.can_edit)


def _das_disponible(t):
    return bool(t and t.plan and (t.plan.code or "").upper() in ("CABINET", "CABINET_COMPTABLE"))


def _annees(t):
    ans = {p.annee for p in PeriodePaie.query.filter_by(tenant_id=t.id).all()}
    ans.add(date.today().year)
    return sorted(ans, reverse=True)


@bp.route("/cycle-paie")
@login_required
@tenant_required
def cycle_paie():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    annees = _annees(t)
    annee = request.args.get("annee", type=int) or annees[0]
    if not (2000 <= annee <= 2100):
        abort(404)
    etat = cp.etat_annee(t, annee)
    return render_template("tenant/cycle_paie.html", tenant=t, annees=annees, annee=annee, etat=etat,
                           MOIS=cp.MOIS, MOIS_COURTS=cp.MOIS_COURTS, peut_saisir=_peut_saisir(),
                           das_dispo=_das_disponible(t), aujourd_hui=date.today())


@bp.route("/cycle-paie/<int:annee>/<int:mois>")
@login_required
@tenant_required
def cycle_paie_mois(annee, mois):
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not (1 <= mois <= 12 and 2000 <= annee <= 2100):
        abort(404)
    e = cp.etat_mois(t, annee, mois)
    # trimestre auquel appartient le mois (DTS CNSS / CNAMGS)
    tri = next(x for x in cp.etat_annee(t, annee)["trimestres"] if x["num"] == e["trimestre"])
    return render_template("tenant/cycle_paie_mois.html", tenant=t, e=e, tri=tri, MOIS=cp.MOIS,
                           peut_saisir=_peut_saisir(), aujourd_hui=date.today())


@bp.route("/cycle-paie/obligation", methods=["POST"])
@login_required
@tenant_required
def cycle_paie_obligation():
    if current_user.is_super_admin:
        abort(403)
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not _peut_saisir():
        abort(403)
    f = request.form
    code = (f.get("code") or "").upper()
    action = (f.get("action") or "").upper()
    annee = f.get("annee", type=int) or 0
    periode = f.get("periode", type=int)
    retour = f.get("retour") or ""
    if not retour.startswith("/cycle-paie"):
        retour = url_for("tenant.cycle_paie", annee=annee or None)
    codes = [c for c in (f.getlist("codes") or [code]) if c]
    if not codes:
        flash("Aucune obligation sélectionnée.", "error")
        return redirect(retour)
    faits = []
    try:
        for c in codes:
            c = c.upper()
            if c not in cp.OBLIGATIONS:
                raise ValueError("obligation inconnue")
            ob = cp.enregistrer(t, c, annee, periode if periode is not None else -1, action, f,
                                f"{current_user.prenom or ''} {current_user.nom or ''}".strip())
            lib = f"{cp.OBLIGATIONS[c]['libelle']} {cp.libelle_periode(c, annee, periode)}"
            if action == "ANNULER":
                log_action("DELETE", "obligation_paie", None, f"Cycle de paie : suivi annulé — {lib}", tenant_id=t.id)
            else:
                db.session.flush()
                log_action("UPDATE", "obligation_paie", ob.id,
                           f"Cycle de paie : {cp.STATUTS_OBLIGATION.get(ob.statut, ob.statut).lower()} — {lib}"
                           + (f" (réf. {ob.reference})" if ob.reference else ""), tenant_id=t.id)
            faits.append(lib)
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        flash(f"Enregistrement impossible : {e}.", "error")
        return redirect(retour)
    verbe = {"DECLARE": "déclaration enregistrée", "PAYE": "paiement enregistré",
             "ANNULER": "suivi annulé"}[action]
    flash(f"{', '.join(faits)} : {verbe}.", "success")
    return redirect(retour)


@bp.route("/cycle-paie/echeances", methods=["GET", "POST"])
@login_required
@tenant_required
def cycle_paie_echeances():
    """Objectifs internes de l'entreprise : ses propres dates, à côté des dates légales."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    from models import EcheanceTenant
    internes = cp.echeances_tenant(t.id)
    if request.method == "POST":
        if not _peut_saisir():
            abort(403)
        erreurs, modifs = [], []
        for code in cp.CODES_INTERNES:
            jour = (request.form.get(f"jour_{code}") or "").strip()
            dec = (request.form.get(f"decalage_{code}") or "").strip()
            note = (request.form.get(f"note_{code}") or "").strip()[:300]
            r = internes.get(code)
            if not jour and not dec:
                if r is not None:
                    db.session.delete(r)
                    modifs.append(f"{code} retiré")
                continue
            try:
                j, d = int(jour), int(dec)
                if not (1 <= j <= 31 and 0 <= d <= 24):
                    raise ValueError
            except ValueError:
                erreurs.append(cp.OBLIGATIONS[code]["libelle"])
                continue
            if r is None:
                r = EcheanceTenant(tenant_id=t.id, code=code)
                db.session.add(r)
            if (r.jour, r.decalage_mois, r.note) != (j, d, note or None):
                modifs.append(f"{code} : jour {j}, +{d} mois")
            r.jour, r.decalage_mois, r.note = j, d, note or None
        if erreurs:
            db.session.rollback()
            flash("Valeurs invalides pour : " + ", ".join(erreurs)
                  + " (jour de 1 à 31, décalage de 0 à 24 mois). Rien n'a été enregistré.", "error")
            return redirect(url_for("tenant.cycle_paie_echeances"))
        if modifs:
            log_action("UPDATE", "echeance_tenant", None,
                       "Cycle de paie : échéances internes — " + " ; ".join(modifs), tenant_id=t.id)
        db.session.commit()
        flash("Vos échéances ont été enregistrées.", "success")
        return redirect(url_for("tenant.cycle_paie_echeances"))

    legales = cp.echeances()
    annee = date.today().year
    lignes = []
    for code in cp.CODES_INTERNES:
        per = cp.OBLIGATIONS[code]["periodicite"]
        pex = 0 if per == "ANNUEL" else 1
        legal = cp.date_limite(code, annee, pex, legales) if code in cp.CODES_ECHEANCE else None
        interne = cp.date_limite(code, annee, pex, internes)
        lignes.append({"code": code, "libelle": cp.OBLIGATIONS[code]["libelle"], "periodicite": per,
                       "r": internes.get(code), "legale": legales.get(code), "legal_ex": legal,
                       "interne_ex": interne, "exemple_periode": cp.libelle_periode(code, annee, pex),
                       "apres_legal": bool(legal and interne and interne > legal),
                       "sans_legal": code in cp.CODES_ECHEANCE and legales.get(code) is None})
    return render_template("tenant/cycle_paie_echeances.html", tenant=t, lignes=lignes,
                           peut_saisir=_peut_saisir())
