# -*- coding: utf-8 -*-
"""Vérification de la validité d'une adresse email à l'inscription.

Trois niveaux, sans service tiers :
  1. Format strict (regex).
  2. Détection des fautes de frappe courantes (gmial → gmail).
  3. Vérification que le domaine accepte réellement les emails (enregistrement MX).
Le tout en complément de l'email de confirmation (preuve ultime de propriété).
"""
import re
import socket

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Fautes de frappe fréquentes sur les grands fournisseurs
TYPOS = {
    "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gmail.co": "gmail.com",
    "gmail.con": "gmail.com", "gamil.com": "gmail.com", "gmil.com": "gmail.com",
    "hotmial.com": "hotmail.com", "hotmai.com": "hotmail.com", "hotmil.com": "hotmail.com",
    "yaho.com": "yahoo.com", "yahou.com": "yahoo.com", "yahoo.co": "yahoo.com",
    "outlok.com": "outlook.com", "outloo.com": "outlook.com", "hotmail.con": "hotmail.com",
    "icloud.con": "icloud.com", "live.con": "live.com",
}

# Domaines jetables/temporaires fréquents (liste courte, extensible)
JETABLES = {
    "mailinator.com", "yopmail.com", "10minutemail.com", "guerrillamail.com",
    "tempmail.com", "trashmail.com", "getnada.com", "sharklasers.com",
    "temp-mail.org", "throwawaymail.com", "fakeinbox.com", "maildrop.cc",
}


def suggerer_correction(email):
    """Renvoie une correction probable si le domaine ressemble à une faute de frappe."""
    try:
        local, dom = email.rsplit("@", 1)
    except ValueError:
        return None
    if dom in TYPOS:
        return f"{local}@{TYPOS[dom]}"
    return None


def domaine_accepte_mail(domaine, timeout=5):
    """True si le domaine a un serveur mail (MX) ou au moins un A record ;
    False si le domaine n'existe pas ; None si indéterminé (ne pas bloquer)."""
    # 1) MX via dnspython si dispo
    try:
        import dns.resolver
        try:
            rep = dns.resolver.resolve(domaine, "MX", lifetime=timeout)
            return len(rep) > 0
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            # pas de MX : certains domaines reçoivent quand même via A record
            try:
                dns.resolver.resolve(domaine, "A", lifetime=timeout)
                return True
            except dns.resolver.NXDOMAIN:
                return False
            except Exception:
                return None
        except Exception:
            return None
    except ImportError:
        pass
    # 2) Fallback : le domaine existe-t-il ? (A record via socket)
    try:
        socket.gethostbyname(domaine)
        return True
    except socket.gaierror:
        return False
    except Exception:
        return None


def verifier_email(email):
    """Retourne (ok: bool, message: str, suggestion: str|None)."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return False, "Adresse email au format invalide.", None
    sugg = suggerer_correction(email)
    if sugg:
        return False, f"Avez-vous voulu écrire « {sugg} » ?", sugg
    dom = email.rsplit("@", 1)[1]
    if dom in JETABLES:
        return False, "Veuillez utiliser une adresse email permanente (pas jetable).", None
    mx = domaine_accepte_mail(dom)
    if mx is False:
        return False, "Ce domaine de messagerie n'existe pas ou n'accepte pas les emails. Vérifiez l'adresse.", None
    # mx True ou None (indéterminé) → on laisse passer (l'email de confirmation tranchera)
    return True, "", None
