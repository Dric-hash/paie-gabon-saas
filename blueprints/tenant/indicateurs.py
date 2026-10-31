"""blueprints/tenant/indicateurs.py — Indicateurs RH (bilan social simplifié).

  /indicateurs-rh              tableau de bord de la période (filtres : du mois, au mois, site, catégorie)
  /indicateurs-rh/imprimer     état imprimable (bilan social simplifié)
  /indicateurs-rh/export.csv   tous les tableaux au format CSV
"""
import csv
import io
from datetime import date, datetime

from flask import render_template, request, Response
from flask_login import login_required

from blueprints.tenant import bp
from core import tenant_required, get_tenant, csv_safe
from audit import log_action
from models import Site, CategorieEmploi
import indicateurs_rh as ir


def _mois(param, defaut):
    try:
        a, m = (int(x) for x in (request.args.get(param) or "").split("-"))
        return date(a, m, 1)
    except (ValueError, TypeError):
        return defaut


def _parametres(t):
    today = date.today()
    du = _mois("du", date(today.year, 1, 1))
    au = _mois("au", date(today.year, today.month, 1))
    if au < du:
        du, au = au, du
    fin = ir.fin_mois(au.year, au.month)
    site_arg = request.args.get("site_id", "")
    site_id = int(site_arg) if site_arg.isdigit() else None
    site = Site.query.filter_by(id=site_id, tenant_id=t.id).first() if site_id else None
    if site_id and site is None:
        site_id = None
    cat_id = request.args.get("categorie_id", type=int)
    cat = CategorieEmploi.query.filter_by(id=cat_id, tenant_id=t.id).first() if cat_id else None
    filtres = []
    if site_id == 0:
        filtres.append("salariés sans site affecté")
    elif site:
        filtres.append(f"site : {site.nom}")
    if cat:
        filtres.append(f"catégorie : {cat.code} — {cat.libelle}")
    return dict(du=du, au=au, fin=fin, site_id=site_id, categorie_id=cat.id if cat else None, filtres=filtres)


def _contexte(t):
    p = _parametres(t)
    d = ir.calculer(t, p["du"], p["fin"], site_id=p["site_id"], categorie_id=p["categorie_id"])
    return dict(t=t, tenant=t, d=d, p=p, MOIS=ir.MOIS, now=datetime.now(),
                sites=Site.query.filter_by(tenant_id=t.id).order_by(Site.nom).all(),
                categories=CategorieEmploi.query.filter_by(tenant_id=t.id).order_by(CategorieEmploi.code).all())


@bp.route("/indicateurs-rh")
@login_required
@tenant_required
def indicateurs_rh():
    t = get_tenant()
    return render_template("tenant/indicateurs_rh.html", **_contexte(t))


@bp.route("/indicateurs-rh/imprimer")
@login_required
@tenant_required
def indicateurs_rh_imprimer():
    t = get_tenant()
    ctx = _contexte(t)
    log_action("EXPORT", "indicateurs_rh", None, f"Bilan social simplifié imprimé ({ctx['d']['debut']:%m/%Y} → "
                                                 f"{ctx['d']['fin']:%m/%Y})")
    return render_template("tenant/indicateurs_rh_print.html", **ctx)


@bp.route("/indicateurs-rh/export.csv")
@login_required
@tenant_required
def indicateurs_rh_csv():
    t = get_tenant()
    ctx = _contexte(t)
    d = ctx["d"]
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    n = lambda v: "" if v is None else (f"{v:.2f}".replace(".", ",") if isinstance(v, float) else v)  # noqa: E731
    w.writerow([f"Indicateurs RH — {t.denomination}", f"{d['debut']:%d/%m/%Y} au {d['fin']:%d/%m/%Y}"]
               + ctx["p"]["filtres"])
    w.writerow([])
    w.writerow(["EFFECTIFS", "Début", "Fin", "Moyen", "Entrées", "Sorties", "Taux de rotation %"])
    w.writerow(["", d["effectif_debut"], d["effectif_fin"], n(d["effectif_moyen"]), len(d["entrees"]),
                len(d["sorties"]), n(d["rotation"])])
    for titre, rows in d["repartitions"].items():
        w.writerow([])
        w.writerow([f"Répartition — {titre}", "Effectif"])
        for k, v in rows:
            w.writerow([csv_safe(k), v])
    w.writerow([])
    w.writerow(["MOUVEMENTS", "Effectif fin de mois", "Entrées", "Sorties"])
    for x in d["mouvements"]:
        w.writerow([x["libelle"], x["effectif"], x["entrees"], x["sorties"]])
    w.writerow([])
    w.writerow(["Entrées", "Matricule", "Nom", "Date d'embauche"])
    for s in d["entrees"]:
        w.writerow(["", csv_safe(s.matricule), csv_safe(s.nom_complet), s.date_embauche.strftime("%d/%m/%Y")])
    w.writerow(["Sorties", "Matricule", "Nom", "Date de sortie", "Motif"])
    for s in d["sorties"]:
        w.writerow(["", csv_safe(s.matricule), csv_safe(s.nom_complet), s.date_cessation.strftime("%d/%m/%Y"),
                    csv_safe(s.type_rupture or "")])
    w.writerow([])
    w.writerow(["ANCIENNETÉ", "Effectif"])
    for k, v in d["anciennete"]:
        w.writerow([k, v])
    w.writerow(["Ancienneté moyenne (ans)", n(d["anciennete_moyenne"])])
    w.writerow([])
    w.writerow(["PYRAMIDE DES ÂGES", "Hommes", "Femmes", "Sexe non renseigné"])
    for lib, v in d["pyramide"]:
        w.writerow([lib, v["H"], v["F"], v["NR"]])
    w.writerow(["Âge moyen", n(d["age_moyen"])])
    w.writerow(["Date de naissance non renseignée", d["sans_age"]])
    w.writerow([])
    a = d["absenteisme"]
    w.writerow(["ABSENTÉISME", "Jours d'absence", "Jours théoriques", "Taux %"])
    w.writerow(["Période", a["jours"], a["theoriques"], n(a["taux"])])
    for x in a["par_mois"]:
        w.writerow([x["libelle"], x["jours"], x["theoriques"], n(x["taux"])])
    for k, v in a["par_motif"]:
        w.writerow([f"Motif : {k}", v])
    m = d["masse"]
    for titre, rows in (("MASSE SALARIALE PAR SITE", m["par_site"]), ("PAR CATÉGORIE", m["par_categorie"]),
                        ("PAR SEXE", m["par_sexe"])):
        w.writerow([])
        w.writerow([titre, "Bulletins", "Brut", "Coût employeur", "Net à payer", "Brut moyen", "Part du coût %"])
        for x in rows:
            w.writerow([csv_safe(x["libelle"]), n(x["nb"]), round(x["brut"]), round(x["cout"]), round(x["net"]),
                        round(x["brut_moyen"]), n(x["part"])])
    w.writerow([])
    w.writerow(["MASSE SALARIALE PAR MOIS", "Bulletins", "Brut", "Coût employeur", "Net à payer"])
    for x in m["par_mois"]:
        w.writerow([x["libelle"], round(x["nb"]), round(x["brut"]), round(x["cout"]), round(x["net"])])
    w.writerow(["Total", round(m["total"]["nb"]), round(m["total"]["brut"]), round(m["total"]["cout"]),
                round(m["total"]["net"])])
    log_action("EXPORT", "indicateurs_rh", None, "Indicateurs RH exportés (CSV)")
    return Response("﻿" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=indicateurs_rh_{d['debut']:%Y%m}_"
                                                    f"{d['fin']:%Y%m}.csv"})
