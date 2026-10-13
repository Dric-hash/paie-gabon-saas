# -*- coding: utf-8 -*-
"""Gestion des pointeurs de chantier (codes de connexion de la PWA) et
validation des pointages mobiles arrivés en quarantaine (statut SOUMIS)."""
import secrets
from datetime import datetime, date

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

from blueprints.tenant import bp, _doc_response
from core import get_tenant, _parse_date
from audit import log_action
from models import (db, Site, Salarie, Journalier, Pointage, PointeurChantier,
                    FeuillePresence)


def _pin_aleatoire():
    """PIN à 4 chiffres (non commençant par 0 pour rester à 4 positions)."""
    return str(secrets.randbelow(9000) + 1000)


# ─────────────────────────── Pointeurs (codes) ────────────────────────────

@bp.route("/pointeurs")
@login_required
def pointeurs():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    liste = (PointeurChantier.query.filter_by(tenant_id=t.id)
             .options(joinedload(PointeurChantier.site))
             .order_by(PointeurChantier.actif.desc(), PointeurChantier.nom).all())
    sites = Site.query.filter_by(tenant_id=t.id, actif=True).order_by(Site.nom).all()
    salaries = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").order_by(Salarie.nom).all()
    journaliers = Journalier.query.filter_by(tenant_id=t.id, statut="ACTIF").order_by(Journalier.nom).all()
    nb_soumis = Pointage.query.filter_by(tenant_id=t.id, statut="SOUMIS").count()
    return render_template("tenant/pointeurs.html", tenant=t, liste=liste,
                           sites=sites, salaries=salaries, journaliers=journaliers,
                           nb_soumis=nb_soumis)


@bp.route("/pointeurs/nouveau", methods=["POST"])
@login_required
def pointeur_nouveau():
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    nom = (request.form.get("nom") or "").strip()
    site_id = request.form.get("site_id", type=int)
    pin = (request.form.get("pin") or "").strip() or _pin_aleatoire()
    if not nom:
        flash("Le nom du pointeur est requis.", "error")
        return redirect(url_for("tenant.pointeurs"))
    site = Site.query.filter_by(id=site_id, tenant_id=t.id).first() if site_id else None
    # Travailleur relié : valeur "S:<id>" ou "J:<id>" (le pointeur est lui-même
    # un salarié ou un journalier du chantier).
    sal_id = jour_id = None
    trav = (request.form.get("travailleur") or "").strip()
    if trav.startswith("S:"):
        _s = Salarie.query.filter_by(id=int(trav[2:]), tenant_id=t.id).first() if trav[2:].isdigit() else None
        sal_id = _s.id if _s else None
    elif trav.startswith("J:"):
        _j = Journalier.query.filter_by(id=int(trav[2:]), tenant_id=t.id).first() if trav[2:].isdigit() else None
        jour_id = _j.id if _j else None
    p = PointeurChantier(
        tenant_id=t.id, site_id=(site.id if site else None), nom=nom,
        code=PointeurChantier.generer_code(), actif=True,
        salarie_id=sal_id, journalier_id=jour_id,
        cree_par=getattr(current_user, "email", None),
    )
    p.set_pin(pin)
    db.session.add(p)
    db.session.commit()
    log_action("CREATE", "pointeur", p.id, f"Pointeur « {nom} » — code {p.code}")
    # Le PIN n'est montré qu'ici (il est haché ensuite) : on l'affiche au RH.
    flash(f"Pointeur créé. Code : {p.code} — PIN : {pin} "
          f"(notez-les, le PIN ne sera plus affiché).", "success")
    return redirect(url_for("tenant.pointeurs"))


@bp.route("/pointeurs/<int:id>/pin", methods=["POST"])
@login_required
def pointeur_pin(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    p = PointeurChantier.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    pin = _pin_aleatoire()
    p.set_pin(pin)
    db.session.commit()
    log_action("UPDATE", "pointeur", p.id, "Nouveau PIN généré")
    flash(f"Nouveau PIN pour « {p.nom} » (code {p.code}) : {pin}", "success")
    return redirect(url_for("tenant.pointeurs"))


@bp.route("/pointeurs/<int:id>/toggle", methods=["POST"])
@login_required
def pointeur_toggle(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    p = PointeurChantier.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    p.actif = not p.actif
    db.session.commit()
    log_action("UPDATE", "pointeur", p.id, "Activé" if p.actif else "Désactivé")
    flash(f"Pointeur « {p.nom} » {'activé' if p.actif else 'désactivé'}.", "success")
    return redirect(url_for("tenant.pointeurs"))


@bp.route("/pointeurs/<int:id>/supprimer", methods=["POST"])
@login_required
def pointeur_supprimer(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    p = PointeurChantier.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    # On conserve l'historique : les pointages gardent pointeur_id même après
    # suppression du compte, donc on délie proprement.
    Pointage.query.filter_by(pointeur_id=p.id).update({"pointeur_id": None})
    db.session.delete(p)
    db.session.commit()
    log_action("DELETE", "pointeur", id, f"Pointeur « {p.nom} » supprimé")
    flash("Pointeur supprimé.", "success")
    return redirect(url_for("tenant.pointeurs"))


# ─────────────────── Validation des pointages mobiles ─────────────────────

@bp.route("/pointages-mobiles")
@login_required
def pointages_mobiles():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    statut = request.args.get("statut", "SOUMIS")
    if statut not in ("SOUMIS", "VALIDE", "REJETE"):
        statut = "SOUMIS"
    pts = (Pointage.query.filter_by(tenant_id=t.id, source="MOBILE", statut=statut)
           .options(joinedload(Pointage.salarie), joinedload(Pointage.journalier),
                    joinedload(Pointage.site))
           .order_by(Pointage.date_pointage.desc(), Pointage.site_id).all())
    # Regroupement par (date, chantier) pour une validation par chantier/jour.
    groupes = {}
    for p in pts:
        cle = (p.date_pointage, p.site_id, p.site.nom if p.site else "—")
        groupes.setdefault(cle, []).append(p)
    # Feuilles signées (chef) pour savoir où afficher la signature.
    feuilles = {(f.date_pointage, f.site_id): f for f in
                FeuillePresence.query.filter_by(tenant_id=t.id).all()}
    groupes = [{"date": d, "site_id": sid, "site": s, "pointages": lst,
                "nb_hors_zone": sum(1 for x in lst if x.geo_hors_zone),
                "nb_signes": sum(1 for x in lst if x.signature),
                "feuille_signee": (d, sid) in feuilles}
               for (d, sid, s), lst in sorted(groupes.items(), key=lambda kv: (kv[0][0], kv[0][2]), reverse=True)]
    nb_soumis = Pointage.query.filter_by(tenant_id=t.id, source="MOBILE", statut="SOUMIS").count()
    return render_template("tenant/pointages_mobiles.html", tenant=t,
                           groupes=groupes, statut=statut, nb_soumis=nb_soumis)


def _ids_cibles(t):
    ids = [int(i) for i in request.form.getlist("pointage_ids") if str(i).isdigit()]
    if not ids:
        return []
    return (Pointage.query.filter(Pointage.tenant_id == t.id,
                                  Pointage.id.in_(ids),
                                  Pointage.source == "MOBILE").all())


@bp.route("/pointages-mobiles/feuille")
@login_required
def pointages_mobiles_feuille():
    """Feuille de présence PDF d'un chantier pour une date (signatures incluses)."""
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    d = _parse_date(request.args.get("date"))
    site_id = request.args.get("site_id", type=int)
    if not d:
        flash("Date manquante.", "error")
        return redirect(url_for("tenant.pointages_mobiles"))
    pts = (Pointage.query.filter_by(tenant_id=t.id, source="MOBILE", date_pointage=d)
           .filter(Pointage.site_id == site_id if site_id else Pointage.site_id.is_(None))
           .options(joinedload(Pointage.salarie), joinedload(Pointage.journalier)).all())
    if not pts:
        flash("Aucun pointage mobile pour cette feuille.", "error")
        return redirect(url_for("tenant.pointages_mobiles"))
    site = Site.query.filter_by(id=site_id, tenant_id=t.id).first() if site_id else None
    feuille = FeuillePresence.query.filter_by(
        tenant_id=t.id, date_pointage=d, site_id=site_id).first()
    from documents_rh import generer_feuille_presence_pdf
    pts.sort(key=lambda p: ((p.salarie or p.journalier).nom if (p.salarie or p.journalier) else ""))
    pdf = generer_feuille_presence_pdf(t, site, d, pts, feuille)
    log_action("EXPORT", "feuille_presence", 0, f"Feuille {d} / {(site.nom if site else '—')}")
    nom = f"feuille_presence_{(site.code if site and site.code else 'chantier')}_{d}.pdf"
    return _doc_response(pdf, nom)


@bp.route("/pointages-mobiles/valider", methods=["POST"])
@login_required
def pointages_mobiles_valider():
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    cibles = _ids_cibles(t)
    n = 0
    for p in cibles:
        if p.statut != "SOUMIS":
            continue
        p.statut = "VALIDE"      # entre désormais dans la paie et la facturation
        n += 1
    db.session.commit()
    log_action("VALIDATE", "pointage_mobile", 0, f"{n} pointage(s) mobile(s) validé(s)")
    flash(f"✅ {n} pointage(s) validé(s) — ils entrent maintenant dans la paie.", "success")
    return redirect(url_for("tenant.pointages_mobiles"))


@bp.route("/pointages-mobiles/rejeter", methods=["POST"])
@login_required
def pointages_mobiles_rejeter():
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    cibles = _ids_cibles(t)
    n = 0
    for p in cibles:
        if p.statut != "SOUMIS":
            continue
        p.statut = "REJETE"
        n += 1
    db.session.commit()
    log_action("REJECT", "pointage_mobile", 0, f"{n} pointage(s) mobile(s) rejeté(s)")
    flash(f"{n} pointage(s) rejeté(s).", "success")
    return redirect(url_for("tenant.pointages_mobiles"))
