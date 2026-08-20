# -*- coding: utf-8 -*-
"""
Conversion d'un montant entier en toutes lettres (français).
Respecte les règles d'accord : « vingt » et « cent » prennent un « s »
quand ils sont multipliés et terminent le nombre, mais restent invariables
devant « mille ». « mille » est invariable. « million/milliard » sont des noms
(pluriel « s ») et n'entraînent pas la chute du « s » de vingt/cent.
"""

_UNITES = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit",
           "neuf", "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
           "dix-sept", "dix-huit", "dix-neuf"]
_DIZAINES = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante"}


def _moins_de_cent(n: int, suivi_de_mille: bool = False) -> str:
    if n < 20:
        return _UNITES[n]
    if n < 70:
        d = (n // 10) * 10
        u = n % 10
        mot = _DIZAINES[d]
        if u == 0:
            return mot
        if u == 1:
            return mot + " et un"            # vingt et un, trente et un…
        return mot + "-" + _UNITES[u]
    if n < 80:                               # 70–79 : soixante + 10..19
        u = n - 60
        if u == 11:
            return "soixante et onze"
        return "soixante-" + _UNITES[u]
    # 80–99
    u = n - 80
    if u == 0:
        return "quatre-vingt" + ("" if suivi_de_mille else "s")
    return "quatre-vingt-" + _UNITES[u]      # 81..99 : jamais de « s », jamais « et »


def _moins_de_mille(n: int, suivi_de_mille: bool = False) -> str:
    if n < 100:
        return _moins_de_cent(n, suivi_de_mille)
    c = n // 100
    r = n % 100
    cent = "cent" if c == 1 else _UNITES[c] + " cent"
    if r == 0:
        if c > 1 and not suivi_de_mille:
            return cent + "s"                # deux cents, trois cents…
        return cent
    return cent + " " + _moins_de_cent(r, suivi_de_mille)


def nombre_en_lettres(n) -> str:
    """Renvoie l'entier `n` en toutes lettres (français), sans devise."""
    try:
        n = int(round(float(n)))
    except (TypeError, ValueError):
        return ""
    if n < 0:
        return "moins " + nombre_en_lettres(-n)
    if n == 0:
        return "zéro"

    milliards = n // 1_000_000_000
    millions = (n % 1_000_000_000) // 1_000_000
    milliers = (n % 1_000_000) // 1_000
    reste = n % 1_000

    parts = []
    if milliards:
        parts.append("un milliard" if milliards == 1
                     else _moins_de_mille(milliards) + " milliards")
    if millions:
        parts.append("un million" if millions == 1
                     else _moins_de_mille(millions) + " millions")
    if milliers:
        # « mille » invariable ; vingt/cent perdent leur « s » devant mille
        parts.append("mille" if milliers == 1
                     else _moins_de_mille(milliers, suivi_de_mille=True) + " mille")
    if reste:
        parts.append(_moins_de_mille(reste))
    return " ".join(parts)


def montant_en_lettres(n, devise: str = "francs CFA") -> str:
    """Montant en toutes lettres, avec devise. Ex : 431 100 →
    « quatre cent trente et un mille cent francs CFA »."""
    txt = nombre_en_lettres(n)
    if not txt:
        return ""
    return f"{txt} {devise}"
