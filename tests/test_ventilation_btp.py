# -*- coding: utf-8 -*-
"""Tests — Ventilation mensuelle du pointage selon la réglementation BTP Gabon."""
from datetime import date
from calculs_paie import ventiler_heures_mois_btp, pointage_vers_jours


def J(d, h=0.0, nuit=0.0, ferie=None, present=None):
    x = {"date": d, "heures": h, "heures_nuit": nuit}
    if ferie is not None:
        x["ferie"] = ferie
    if present is not None:
        x["present"] = present
    return x


# Semaine ISO complète : lundi 5 jan 2026 → dimanche 11 jan 2026
def test_semaine_48h():
    jours = [J(date(2026, 1, d), 8) for d in range(5, 11)]  # lun→sam 6×8 = 48h
    r = ventiler_heures_mois_btp(jours)
    assert r["heures_normales"] == 40
    assert r["heures_sup_10"] == 4
    assert r["heures_sup_30"] == 4
    assert r["heures_sup_70"] == 0


def test_dimanche_travaille_en_70():
    jours = [J(date(2026, 1, d), 8) for d in range(5, 10)]  # lun→ven 40h
    jours.append(J(date(2026, 1, 11), 6))                    # dimanche 6h
    r = ventiler_heures_mois_btp(jours)
    assert r["heures_normales"] == 40       # le dimanche ne compte pas dans les 40h
    assert r["heures_sup_70"] == 6
    assert r["heures_sup_10"] == 0


def test_ferie_chome_8h_normales():
    jours = [J(date(2026, 1, 5), 0, ferie=True, present=False)]  # lundi férié chômé
    jours += [J(date(2026, 1, d), 8) for d in range(6, 11)]      # mar→sam 40h travaillées
    r = ventiler_heures_mois_btp(jours)
    assert r["heures_normales"] == 48       # 8h chômé + 40h travaillées
    assert r["heures_sup_10"] == 0          # le férié chômé ne déclenche pas d'heures sup
    assert r["heures_sup_30"] == 0


def test_ferie_travaille_integralite_70():
    # Pas de présomption "8h normales + sup" : tout en +70%
    jours = [J(date(2026, 1, 5), 10, ferie=True)]          # lundi férié travaillé 10h
    jours += [J(date(2026, 1, d), 8) for d in range(6, 10)]  # mar→ven 32h
    r = ventiler_heures_mois_btp(jours)
    assert r["heures_sup_70"] == 10
    assert r["heures_normales"] == 32


def test_heures_nuit_en_40():
    jours = [J(date(2026, 1, d), 8) for d in range(5, 10)]  # lun→ven 40h jour
    jours.append(J(date(2026, 1, 10), 4, nuit=3))           # samedi 4h jour + 3h nuit
    r = ventiler_heures_mois_btp(jours)
    assert r["heures_sup_40"] == 3
    assert r["heures_normales"] == 40
    assert r["heures_sup_10"] == 4          # la 41e→44e (samedi jour) en +10%


def test_seuils_independants_par_semaine():
    s1 = [J(date(2026, 1, d), 8) for d in range(5, 11)]    # S1 : 48h
    s2 = [J(date(2026, 1, d), 6) for d in range(12, 17)]   # S2 : 30h (lun→ven)
    r = ventiler_heures_mois_btp(s1 + s2)
    assert r["heures_normales"] == 70       # 40 + 30
    assert r["heures_sup_10"] == 4          # uniquement S1
    assert r["heures_sup_30"] == 4


def test_jour_non_travaille_ignore():
    # Une ligne sans heures et non fériée ne produit rien
    jours = [J(date(2026, 1, 5), 0, present=False),
             J(date(2026, 1, 6), 8), J(date(2026, 1, 7), 8)]
    r = ventiler_heures_mois_btp(jours)
    assert r["heures_normales"] == 16
    assert r["total_heures_sup"] == 0


def test_adaptateur_reconstruit_heures_brutes():
    """Un férié travaillé stocké 'à l'ancienne' (normales=0, tout en h70) est bien reconstruit."""
    class P:
        def __init__(self, d, hn=0, h10=0, h30=0, h40=0, h70=0,
                     type_jour="NORMAL", present=True, absent=False):
            self.date_pointage = d; self.heures_normales = hn
            self.heures_sup_10 = h10; self.heures_sup_30 = h30
            self.heures_sup_40 = h40; self.heures_sup_70 = h70
            self.type_jour = type_jour; self.present = present; self.absent = absent

    pts = [
        P(date(2026, 1, 5), h70=10, type_jour="FERIE"),     # férié travaillé 10h
        P(date(2026, 1, 6), hn=8), P(date(2026, 1, 7), hn=8),
        P(date(2026, 1, 8), hn=8), P(date(2026, 1, 9), hn=8),
        P(date(2026, 1, 10), hn=8, h40=2),                  # samedi 8h + 2h nuit
    ]
    r = ventiler_heures_mois_btp(pointage_vers_jours(pts))
    assert r["heures_sup_70"] == 10        # férié travaillé reconstruit
    assert r["heures_sup_40"] == 2         # nuit préservée
    assert r["heures_normales"] == 40      # mar→sam 5×8 = 40


def test_adaptateur_chome_paye():
    class P:
        def __init__(self, d, hn=0, type_jour="NORMAL", present=True, absent=False):
            self.date_pointage = d; self.heures_normales = hn
            self.heures_sup_10 = 0; self.heures_sup_30 = 0
            self.heures_sup_40 = 0; self.heures_sup_70 = 0
            self.type_jour = type_jour; self.present = present; self.absent = absent

    pts = [P(date(2026, 1, 5), hn=8, type_jour="CHOME_PAYE", present=True)]  # férié chômé payé
    pts += [P(date(2026, 1, d), hn=8) for d in range(6, 11)]                 # 40h travaillées
    r = ventiler_heures_mois_btp(pointage_vers_jours(pts))
    assert r["heures_normales"] == 48      # 8h chômé + 40h travaillées
    assert r["heures_sup_10"] == 0
