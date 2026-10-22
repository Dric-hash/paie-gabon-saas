# -*- coding: utf-8 -*-
"""Portail des employés : consultation de leurs bulletins par les salariés.

Règles de sécurité
  - Le portail est désactivé par défaut ; l'administrateur de l'entreprise
    l'active (Tenant.portail_salaries_actif), puis invite les salariés un par un.
  - Connexion : code entreprise (slug) + matricule + PIN à 6 chiffres.
  - Le RH remet un CODE D'ACTIVATION à usage unique (7 jours) ; le salarié
    choisit lui-même son PIN. Le RH ne connaît jamais le PIN.
  - 5 erreurs de PIN (ou de code d'activation) → accès verrouillé 15 minutes.
  - Réinitialiser ou désactiver l'accès invalide immédiatement les sessions
    ouvertes (version). Un salarié qui n'est plus ACTIF perd l'accès.
  - Le salarié ne voit que SES bulletins VALIDÉS ou PAYÉS (jamais un brouillon).
"""
from __future__ import annotations

import secrets
from datetime import timedelta

ESSAIS_MAX = 5
VERROU_MINUTES = 15
CODE_JOURS = 7
INACTIVITE_MINUTES = 15
ALPHABET_CODE = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # sans 0/O, 1/I/L
STATUTS_VISIBLES = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")
MOIS = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août",
        "Septembre", "Octobre", "Novembre", "Décembre"]

# Empreinte factice : même coût de calcul quand le matricule n'existe pas
_LEURRE = None


def _leurre():
    global _LEURRE
    if _LEURRE is None:
        from werkzeug.security import generate_password_hash
        _LEURRE = generate_password_hash("000000-leurre")
    return _LEURRE


# ── PIN et code d'activation ───────────────────────────────────────────────

def pin_refuse(pin: str, salarie=None) -> str | None:
    """Motif de refus d'un PIN, ou None s'il est acceptable."""
    if not pin or len(pin) != 6 or not pin.isdigit():
        return "le code PIN doit comporter exactement 6 chiffres"
    if len(set(pin)) == 1:
        return "le code PIN ne peut pas être un seul chiffre répété"
    chiffres = [int(c) for c in pin]
    pas = {chiffres[i + 1] - chiffres[i] for i in range(5)}
    if pas in ({1}, {-1}):
        return "le code PIN ne peut pas être une suite (123456, 654321…)"
    dn = getattr(salarie, "date_naissance", None) if salarie else None
    if dn:
        interdits = {dn.strftime("%d%m%y"), dn.strftime("%y%m%d"), dn.strftime("%d%m") + dn.strftime("%Y")[:2],
                     dn.strftime("%m%d%y")}
        if pin in interdits:
            return "le code PIN ne doit pas être votre date de naissance"
    mat = "".join(c for c in (getattr(salarie, "matricule", "") or "") if c.isdigit())
    if mat and len(mat) >= 4 and pin.endswith(mat[-6:]) and len(mat[-6:]) == 6:
        return "le code PIN ne doit pas reprendre votre matricule"
    return None


def nouveau_code() -> str:
    """Code d'activation lisible : XXXX-XXXX (sans caractères ambigus)."""
    brut = "".join(secrets.choice(ALPHABET_CODE) for _ in range(8))
    return f"{brut[:4]}-{brut[4:]}"


def normaliser_code(code: str) -> str:
    c = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c


def inviter(acces, par: str) -> str:
    """(Ré)initialise l'accès : nouveau code d'activation, PIN effacé,
    sessions ouvertes invalidées. Retourne le code en clair (affiché une fois)."""
    from werkzeug.security import generate_password_hash
    from models import utcnow as maintenant
    code = nouveau_code()
    acces.code_hash = generate_password_hash(code)
    acces.code_expire = maintenant() + timedelta(days=CODE_JOURS)
    acces.pin_hash = None
    acces.actif = True
    acces.echecs = 0
    acces.verrou_jusqua = None
    acces.version = (acces.version or 0) + 1
    acces.invite_le = maintenant()
    acces.invite_par = (par or "")[:150]
    return code


def acces_pour(tenant_id: int, salarie_id: int, creer: bool = False):
    from models import db, AccesSalarie
    a = AccesSalarie.query.filter_by(tenant_id=tenant_id, salarie_id=salarie_id).first()
    if a is None and creer:
        a = AccesSalarie(tenant_id=tenant_id, salarie_id=salarie_id, actif=True, version=1, echecs=0)
        db.session.add(a)
    return a


def situation(acces, salarie) -> str:
    """NON_INVITE | INVITE | ACTIF | DESACTIVE | PARTI | CODE_EXPIRE."""
    from models import utcnow as maintenant
    if (getattr(salarie, "statut", "ACTIF") or "ACTIF").upper() != "ACTIF":
        return "PARTI"
    if acces is None:
        return "NON_INVITE"
    if not acces.actif:
        return "DESACTIVE"
    if acces.pin_hash:
        return "ACTIF"
    if acces.code_expire and _naif(acces.code_expire) < _naif(maintenant()):
        return "CODE_EXPIRE"
    return "INVITE"


def _naif(d):
    return d.replace(tzinfo=None) if d is not None and d.tzinfo is not None else d


# ── Connexion ─────────────────────────────────────────────────────────────

def trouver(code_entreprise: str, matricule: str):
    """(tenant, salarie, acces) ou (tenant|None, None, None)."""
    from models import Tenant, Salarie, AccesSalarie
    slug = (code_entreprise or "").strip().lower()
    mat = (matricule or "").strip()
    if not slug or not mat:
        return None, None, None
    t = Tenant.query.filter_by(slug=slug).first()
    if t is None:
        return None, None, None
    s = Salarie.query.filter_by(tenant_id=t.id, matricule=mat).first()
    if s is None:
        # matricule saisi en minuscules ou avec espaces
        s = (Salarie.query.filter(Salarie.tenant_id == t.id)
             .filter(Salarie.matricule.ilike(mat)).first())
    if s is None:
        return t, None, None
    return t, s, AccesSalarie.query.filter_by(tenant_id=t.id, salarie_id=s.id).first()


def portail_ouvert(t) -> bool:
    return bool(t and t.portail_salaries_actif and (t.statut or "") in ("ACTIF", "ESSAI", "PAIEMENT_EN_ATTENTE"))


def verrouille(acces) -> bool:
    from models import utcnow as maintenant
    return bool(acces and acces.verrou_jusqua and _naif(acces.verrou_jusqua) > _naif(maintenant()))


def echec(acces):
    from models import utcnow as maintenant
    if acces is None:
        return
    acces.echecs = (acces.echecs or 0) + 1
    if acces.echecs >= ESSAIS_MAX:
        acces.verrou_jusqua = maintenant() + timedelta(minutes=VERROU_MINUTES)
        acces.echecs = 0


def verifier_pin(acces, pin: str) -> bool:
    from werkzeug.security import check_password_hash
    if acces is None or not acces.pin_hash:
        check_password_hash(_leurre(), pin or "x")
        return False
    return check_password_hash(acces.pin_hash, pin or "")


def verifier_code(acces, code: str) -> bool:
    from werkzeug.security import check_password_hash
    from models import utcnow as maintenant
    if acces is None or not acces.code_hash or not acces.code_expire \
            or _naif(acces.code_expire) < _naif(maintenant()):
        check_password_hash(_leurre(), code or "x")
        return False
    return check_password_hash(acces.code_hash, normaliser_code(code))


def definir_pin(acces, pin: str):
    from werkzeug.security import generate_password_hash
    from models import utcnow as maintenant
    acces.pin_hash = generate_password_hash(pin)
    acces.code_hash = None
    acces.code_expire = None
    acces.echecs = 0
    acces.verrou_jusqua = None
    acces.version = (acces.version or 0) + 1
    if not acces.active_le:
        acces.active_le = maintenant()


# ── Bulletins ──────────────────────────────────────────────────────────────

def bulletins(salarie):
    """Bulletins visibles du salarié (validés ou payés), du plus récent au plus ancien."""
    from models import BulletinPaie, PeriodePaie
    return (BulletinPaie.query.join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
            .filter(BulletinPaie.tenant_id == salarie.tenant_id, BulletinPaie.salarie_id == salarie.id,
                    PeriodePaie.tenant_id == salarie.tenant_id, BulletinPaie.statut.in_(STATUTS_VISIBLES))
            .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()).all())


def bulletin(salarie, bulletin_id: int):
    from models import db, BulletinPaie
    b = db.session.get(BulletinPaie, bulletin_id)
    if b is None or b.salarie_id != salarie.id or b.tenant_id != salarie.tenant_id \
            or (b.statut or "").upper() not in STATUTS_VISIBLES:
        return None
    return b


def recapitulatif(liste) -> list:
    """Cumuls par année : brut, retenues salariales, net payé."""
    par_an = {}
    for b in liste:
        a = b.periode.annee
        r = par_an.setdefault(a, {"annee": a, "nb": 0, "brut": 0.0, "cnss": 0.0, "cnamgs": 0.0,
                                  "tcs": 0.0, "irpp": 0.0, "net": 0.0})
        r["nb"] += 1
        r["brut"] += float(b.salaire_brut or 0)
        r["cnss"] += float(b.cnss_salarie or 0)
        r["cnamgs"] += float(b.cnamgs_salarie or 0)
        r["tcs"] += float(b.tcs or 0)
        r["irpp"] += float(b.irpp or 0)
        r["net"] += float(b.net_a_payer or 0)
    return [par_an[a] for a in sorted(par_an, reverse=True)]


def libelle(b) -> str:
    return f"{MOIS[b.periode.mois]} {b.periode.annee}"
