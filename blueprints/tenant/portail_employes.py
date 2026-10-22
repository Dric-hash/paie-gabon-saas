# -*- coding: utf-8 -*-
"""Gestion du portail des employés par l'entreprise : activation (administrateur),
invitations avec code d'activation, réinitialisation, désactivation, suivi des
consultations, notification « bulletin disponible »."""
from flask import render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user
from sqlalchemy import func
from datetime import timedelta

from blueprints.tenant import bp, logger
from core import tenant_required, get_tenant, send_email_async
from audit import log_action
from models import (db, utcnow, Salarie, PeriodePaie, BulletinPaie, AccesSalarie, ConsultationBulletin,
                    AuditLog)
import portail_salaries as ps

SITUATIONS = {
    "NON_INVITE": ("Pas d'accès", "#f3f4f6", "#6b7280"),
    "INVITE": ("Code remis, à activer", "#fef3c7", "#92400e"),
    "CODE_EXPIRE": ("Code expiré", "#ffedd5", "#9a3412"),
    "ACTIF": ("Actif", "#dcfce7", "#166534"),
    "DESACTIVE": ("Désactivé", "#fee2e2", "#991b1b"),
    "PARTI": ("Parti — accès coupé", "#f3f4f6", "#9ca3af"),
}


def _contexte():
    if current_user.is_super_admin:
        abort(403)
    t = get_tenant()
    if not t:
        abort(403)
    return t


def _par():
    return f"{current_user.prenom or ''} {current_user.nom or ''}".strip() or current_user.email


def _mail_actif():
    return not current_app.config.get("MAIL_SUPPRESS_SEND") or current_app.config.get("TESTING")


def _envoyer(destinataire, sujet, html):
    from flask_mail import Message
    mail = current_app.extensions.get("mail")
    if mail is None:
        return False
    msg = Message(subject=sujet, recipients=[destinataire], html=html,
                  sender=current_app.config.get("MAIL_DEFAULT_SENDER"))
    if isinstance(current_app.config.get("MAIL_CAPTURE"), list):
        current_app.config["MAIL_CAPTURE"].append(msg)
        return True
    send_email_async(mail, msg)
    return True


def _gabarit_mail(t, titre, paragraphes, bouton, lien):
    from html import escape
    corps = "".join(f'<p style="color:#374151;line-height:1.6;font-size:15px;margin:0 0 12px">{p}</p>'
                    for p in paragraphes)
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto">
  <div style="background:#0f3d36;padding:24px 26px;border-radius:14px 14px 0 0">
    <div style="color:#E9B043;font-size:12px;letter-spacing:1px;font-weight:bold">{escape(t.denomination or '')}</div>
    <h1 style="color:#fff;margin:6px 0 0;font-size:20px">{escape(titre)}</h1>
  </div>
  <div style="background:#fff;padding:26px;border:1px solid #e6e2d6;border-top:none;border-radius:0 0 14px 14px">
    {corps}
    <p style="margin:22px 0"><a href="{lien}" style="background:#0f3d36;color:#fff;padding:12px 20px;border-radius:10px;
      text-decoration:none;font-weight:bold">{escape(bouton)}</a></p>
    <p style="color:#9ca3af;font-size:12px;margin:18px 0 0">Message envoyé via PaieGabon pour le compte de votre employeur.
      Ne communiquez jamais votre code PIN, même au service RH.</p>
  </div></div>"""


@bp.route("/portail-employes")
@login_required
@tenant_required
def portail_employes():
    t = _contexte()
    salaries = (Salarie.query.filter_by(tenant_id=t.id)
                .order_by(Salarie.statut.desc(), Salarie.nom, Salarie.prenom).all())
    acces = {a.salarie_id: a for a in AccesSalarie.query.filter_by(tenant_id=t.id).all()}
    derniere = dict(db.session.query(ConsultationBulletin.salarie_id, func.max(ConsultationBulletin.date))
                    .filter(ConsultationBulletin.tenant_id == t.id).group_by(ConsultationBulletin.salarie_id).all())
    lignes = []
    for s in salaries:
        a = acces.get(s.id)
        sit = ps.situation(a, s)
        if sit == "PARTI" and a is None:
            continue                       # ancien salarié sans accès : inutile de l'afficher
        lignes.append({"s": s, "a": a, "sit": sit, "sit_lib": SITUATIONS[sit], "vu": derniere.get(s.id)})
    compte = {k: sum(1 for l in lignes if l["sit"] == k) for k in SITUATIONS}

    # Périodes ayant des bulletins visibles, pour la notification « bulletin disponible »
    periodes = (PeriodePaie.query.filter_by(tenant_id=t.id)
                .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()).limit(12).all())
    stats_periodes = []
    for p in periodes:
        visibles = (BulletinPaie.query.filter(BulletinPaie.tenant_id == t.id, BulletinPaie.periode_id == p.id,
                                              BulletinPaie.statut.in_(ps.STATUTS_VISIBLES)).all())
        if not visibles:
            continue
        ids = [b.id for b in visibles]
        vus = (db.session.query(func.count(func.distinct(ConsultationBulletin.bulletin_id)))
               .filter(ConsultationBulletin.bulletin_id.in_(ids)).scalar() or 0)
        notif = (AuditLog.query.filter(AuditLog.entite == "portail_notification", AuditLog.entite_id == p.id)
                 .order_by(AuditLog.id.desc()).first())
        stats_periodes.append({"p": p, "nb": len(visibles), "vus": vus,
                               "notifie_le": notif.date_action if notif else None})
    lien = url_for("espace.accueil", e=t.slug, _external=True)
    return render_template("tenant/portail_employes.html", tenant=t, lignes=lignes, compte=compte,
                           periodes=stats_periodes, lien=lien, peut_saisir=current_user.can_edit,
                           est_admin=current_user.is_tenant_admin, SITUATIONS=SITUATIONS,
                           mail_actif=_mail_actif(), CODE_JOURS=ps.CODE_JOURS)


@bp.route("/portail-employes/activation", methods=["POST"])
@login_required
@tenant_required
def portail_employes_activation():
    t = _contexte()
    if not current_user.is_tenant_admin:
        abort(403)
    actif = request.form.get("actif") == "1"
    if bool(t.portail_salaries_actif) != actif:
        t.portail_salaries_actif = actif
        log_action("UPDATE", "tenant", t.id,
                   f"Portail des employés {'activé' if actif else 'désactivé'}", tenant_id=t.id)
        db.session.commit()
    flash("Portail des employés activé. Invitez maintenant vos salariés." if actif else
          "Portail des employés désactivé : plus aucun salarié ne peut s'y connecter.",
          "success" if actif else "warning")
    return redirect(url_for("tenant.portail_employes"))


@bp.route("/portail-employes/inviter", methods=["POST"])
@login_required
@tenant_required
def portail_employes_inviter():
    """Crée ou réinitialise l'accès des salariés cochés ; affiche les fiches à remettre (une seule fois)."""
    t = _contexte()
    if not current_user.can_edit:
        abort(403)
    if not t.portail_salaries_actif:
        flash("Activez d'abord le portail des employés.", "error")
        return redirect(url_for("tenant.portail_employes"))
    ids = [int(x) for x in request.form.getlist("salarie_ids") if str(x).isdigit()]
    if not ids:
        flash("Cochez au moins un salarié.", "warning")
        return redirect(url_for("tenant.portail_employes"))
    salaries = (Salarie.query.filter(Salarie.tenant_id == t.id, Salarie.id.in_(ids)).order_by(Salarie.nom).all())
    envoyer = request.form.get("envoyer_email") == "1"
    lien = url_for("espace.activer", e=t.slug, _external=True)
    fiches, ignores = [], []
    for s in salaries:
        if (s.statut or "ACTIF").upper() != "ACTIF":
            ignores.append(f"{s.prenom} {s.nom} (n'est plus actif)")
            continue
        a = ps.acces_pour(t.id, s.id, creer=True)
        code = ps.inviter(a, _par())
        par_mail = False
        if envoyer and s.email and _mail_actif():
            html = _gabarit_mail(t, "Votre espace salarié PaieGabon", [
                f"Bonjour {s.prenom or ''},",
                f"Votre employeur met vos bulletins de paie à votre disposition sur votre espace salarié.",
                f"Code entreprise : <strong>{t.slug}</strong><br>Matricule : <strong>{s.matricule}</strong><br>"
                f"Code d'activation : <strong style=\"font-size:18px;letter-spacing:2px\">{code}</strong>",
                f"Ce code est valable {ps.CODE_JOURS} jours et ne sert qu'une fois : il vous permet de choisir votre "
                f"code PIN personnel.",
            ], "Activer mon accès", f"{lien}&m={s.matricule}")
            par_mail = _envoyer(s.email, f"{t.denomination} — votre espace salarié", html)
        fiches.append({"s": s, "code": code, "par_mail": par_mail})
    if fiches:
        log_action("UPDATE", "acces_salarie", None,
                   f"Portail des employés : code d'activation remis à {len(fiches)} salarié(s) — "
                   + ", ".join(f["s"].matricule for f in fiches), tenant_id=t.id)
    db.session.commit()
    return render_template("tenant/portail_employes_fiches.html", tenant=t, fiches=fiches, ignores=ignores,
                           lien=lien, CODE_JOURS=ps.CODE_JOURS,
                           expire_le=(utcnow() + timedelta(days=ps.CODE_JOURS)).date())


@bp.route("/portail-employes/<int:salarie_id>/<action>", methods=["POST"])
@login_required
@tenant_required
def portail_employes_acces(salarie_id, action):
    t = _contexte()
    if not current_user.can_edit:
        abort(403)
    if action not in ("desactiver", "reactiver"):
        abort(404)
    s = Salarie.query.filter_by(id=salarie_id, tenant_id=t.id).first_or_404()
    a = ps.acces_pour(t.id, s.id)
    if a is None:
        abort(404)
    a.actif = action == "reactiver"
    a.version = (a.version or 0) + 1           # déconnecte immédiatement
    a.echecs, a.verrou_jusqua = 0, None
    log_action("UPDATE", "acces_salarie", a.id,
               f"Portail des employés : accès {'réactivé' if a.actif else 'désactivé'} — {s.matricule}",
               tenant_id=t.id)
    db.session.commit()
    flash(f"Accès de {s.prenom} {s.nom} {'réactivé' if a.actif else 'désactivé'}.", "success")
    return redirect(url_for("tenant.portail_employes"))


@bp.route("/portail-employes/prevenir", methods=["POST"])
@login_required
@tenant_required
def portail_employes_prevenir():
    """E-mail « votre bulletin est disponible » aux salariés ayant un accès actif et une adresse e-mail."""
    t = _contexte()
    if not current_user.can_edit:
        abort(403)
    if not t.portail_salaries_actif:
        flash("Le portail des employés est désactivé.", "error")
        return redirect(url_for("tenant.portail_employes"))
    p = PeriodePaie.query.filter_by(id=request.form.get("periode_id", type=int), tenant_id=t.id).first_or_404()
    if not _mail_actif():
        flash("L'envoi d'e-mails n'est pas configuré sur ce serveur : aucun message n'est parti.", "error")
        return redirect(url_for("tenant.portail_employes"))
    buls = (BulletinPaie.query.filter(BulletinPaie.tenant_id == t.id, BulletinPaie.periode_id == p.id,
                                      BulletinPaie.statut.in_(ps.STATUTS_VISIBLES)).all())
    acces = {a.salarie_id: a for a in AccesSalarie.query.filter_by(tenant_id=t.id, actif=True).all()}
    lien = url_for("espace.accueil", e=t.slug, _external=True)
    envoyes, sans_email, sans_acces = 0, 0, 0
    for b in buls:
        s, a = b.salarie, acces.get(b.salarie_id)
        if a is None or not a.pin_hash or (s.statut or "ACTIF").upper() != "ACTIF":
            sans_acces += 1
            continue
        if not s.email:
            sans_email += 1
            continue
        html = _gabarit_mail(t, f"Votre bulletin de {ps.MOIS[p.mois].lower()} {p.annee} est disponible", [
            f"Bonjour {s.prenom or ''},",
            f"Votre bulletin de paie de <strong>{ps.MOIS[p.mois].lower()} {p.annee}</strong> est disponible sur votre "
            f"espace salarié.",
        ], "Voir mon bulletin", lien)
        if _envoyer(s.email, f"Votre bulletin de {ps.MOIS[p.mois].lower()} {p.annee} est disponible", html):
            envoyes += 1
    log_action("EXPORT", "portail_notification", p.id,
               f"Portail des employés : {envoyes} salarié(s) prévenu(s) — bulletin {p.libelle_complet}",
               tenant_id=t.id)
    db.session.commit()
    msg = f"{envoyes} salarié(s) prévenu(s) par e-mail pour {p.libelle_complet}."
    if sans_email:
        msg += f" {sans_email} sans adresse e-mail dans sa fiche."
    if sans_acces:
        msg += f" {sans_acces} sans accès activé."
    flash(msg, "success" if envoyes else "warning")
    return redirect(url_for("tenant.portail_employes"))
