"""impression_sites.py — Listes imprimées classées par site (chantier d'affectation en cours).

Le site d'une personne est celui de son affectation active (la plus récente s'il y en a
plusieurs). Les sites sont classés par ordre alphabétique ; les personnes sans affectation
active viennent en dernier, sous « Sans site affecté ». Dans chaque site : ordre alphabétique.
"""
from models import AffectationSite, Site

SANS_SITE = "Sans site affecté"


def site_de(t, champ):
    """{id de la personne: site_id} d'après les affectations actives. champ : salarie_id | journalier_id."""
    col = getattr(AffectationSite, champ)
    out = {}
    for a in (AffectationSite.query.filter(AffectationSite.tenant_id == t.id, AffectationSite.actif.is_(True),
                                           col.isnot(None))
              .order_by(AffectationSite.date_debut, AffectationSite.id)):
        out[getattr(a, champ)] = a.site_id          # la plus récente l'emporte
    return out


def grouper(t, personnes, champ, site_id=None):
    """[{"site": Site|None, "nom": str, "personnes": [...]}] dans l'ordre d'impression.
    site_id : ne garder qu'un site (0 = les personnes sans site)."""
    affect = site_de(t, champ)
    sites = {s.id: s for s in Site.query.filter_by(tenant_id=t.id).all()}
    groupes = {}
    for p in personnes:
        sid = affect.get(p.id)
        if sid not in sites:
            sid = None
        groupes.setdefault(sid, []).append(p)
    if site_id is not None:
        cle = None if site_id == 0 else site_id
        groupes = {cle: groupes.get(cle, [])}
    ordre = sorted((k for k in groupes if k is not None), key=lambda k: (sites[k].nom or "").upper())
    if None in groupes:
        ordre.append(None)
    tri = lambda p: ((p.nom or "").upper(), (p.prenom or "").upper())  # noqa: E731
    return [{"site": sites.get(k), "nom": sites[k].nom if k is not None else SANS_SITE,
             "personnes": sorted(groupes[k], key=tri)} for k in ordre]


def sites_du_tenant(t):
    return Site.query.filter_by(tenant_id=t.id).order_by(Site.nom).all()
