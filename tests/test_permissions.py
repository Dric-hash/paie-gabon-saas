"""
tests/test_permissions.py — Tests unitaires du système de permissions

Exécution :
    pytest tests/test_permissions.py -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock


class _MockUser:
    """
    Simule un Utilisateur sans SQLAlchemy.
    Copie la matrice _PERMISSIONS et les propriétés du vrai modèle.
    """
    _PERMISSIONS = {
        "SUPER_ADMIN":  {"all"},
        "TENANT_ADMIN": {"all"},
        "RH":           {"view_dashboard","view_salaries","edit_salaries","view_bulletins",
                         "edit_bulletins","view_conges","edit_conges","view_acomptes",
                         "edit_acomptes","view_periodes","edit_periodes","view_pointage",
                         "edit_pointage","view_journaliers","edit_journaliers",
                         "view_sites","view_rapports","view_declaration","export_excel"},
        "COMPTABLE":    {"view_dashboard","view_salaries","view_bulletins","view_conges",
                         "view_acomptes","view_periodes","view_pointage","view_journaliers",
                         "view_sites","view_rapports","view_declaration",
                         "export_excel","export_sage","export_cnss"},
        "DIRECTEUR":    {"view_dashboard","view_rapports","view_salaries","view_bulletins",
                         "view_declaration","view_journaliers","view_sites"},
        "GESTIONNAIRE": {"view_dashboard","view_salaries","edit_salaries","view_bulletins",
                         "edit_bulletins","view_conges","edit_conges","view_acomptes",
                         "edit_acomptes","view_periodes","edit_periodes","view_pointage",
                         "edit_pointage","view_journaliers","edit_journaliers",
                         "view_sites","view_rapports","view_declaration","export_excel"},
    }
    _LABELS = {
        "SUPER_ADMIN": "Super Admin", "TENANT_ADMIN": "Administrateur",
        "RH": "Responsable RH", "COMPTABLE": "Comptable",
        "DIRECTEUR": "Directeur", "GESTIONNAIRE": "Gestionnaire",
    }

    def __init__(self, role):
        self.role = role

    @property
    def role_normalized(self): return (self.role or "").strip().upper()

    @property
    def is_super_admin(self): return self.role_normalized == "SUPER_ADMIN"

    @property
    def is_tenant_admin(self): return self.role_normalized in ("SUPER_ADMIN", "TENANT_ADMIN")

    def has_permission(self, perm):
        perms = self._PERMISSIONS.get(self.role_normalized, set())
        return "all" in perms or perm in perms

    @property
    def can_edit(self): return self.has_permission("edit_salaries") or self.is_tenant_admin

    @property
    def can_edit_bulletins(self): return self.has_permission("edit_bulletins") or self.is_tenant_admin

    @property
    def can_view_only(self): return self.role_normalized in ("COMPTABLE", "DIRECTEUR")

    @property
    def can_export(self): return self.has_permission("export_excel") or self.is_tenant_admin

    @property
    def can_export_sage(self): return self.has_permission("export_sage") or self.is_tenant_admin

    @property
    def can_manage_parametres(self): return self.is_tenant_admin

    @property
    def can_manage_users(self): return self.is_tenant_admin

    @property
    def role_label(self): return self._LABELS.get(self.role_normalized, self.role)


def _user(role):
    return _MockUser(role)


class TestRoleNormalized:
    def test_tenant_admin(self):
        u = _user("TENANT_ADMIN")
        assert u.role_normalized == "TENANT_ADMIN"

    def test_lowercase_normalization(self):
        u = _user("rh")
        assert u.role_normalized == "RH"

    def test_whitespace_stripped(self):
        u = _user("  COMPTABLE  ")
        assert u.role_normalized == "COMPTABLE"


class TestIsSuperAdmin:
    def test_super_admin_true(self):
        assert _user("SUPER_ADMIN").is_super_admin is True

    def test_tenant_admin_false(self):
        assert _user("TENANT_ADMIN").is_super_admin is False

    def test_rh_false(self):
        assert _user("RH").is_super_admin is False


class TestIsTenantAdmin:
    def test_super_admin_is_tenant_admin(self):
        assert _user("SUPER_ADMIN").is_tenant_admin is True

    def test_tenant_admin_true(self):
        assert _user("TENANT_ADMIN").is_tenant_admin is True

    def test_rh_false(self):
        assert _user("RH").is_tenant_admin is False

    def test_comptable_false(self):
        assert _user("COMPTABLE").is_tenant_admin is False

    def test_directeur_false(self):
        assert _user("DIRECTEUR").is_tenant_admin is False


class TestHasPermission:
    # ── TENANT_ADMIN — tout faire ──────────────────────────────────────────
    def test_admin_has_all(self):
        u = _user("TENANT_ADMIN")
        for perm in ["edit_salaries","edit_bulletins","export_sage","view_rapports"]:
            assert u.has_permission(perm) is True, f"TENANT_ADMIN devrait avoir {perm}"

    # ── RH ─────────────────────────────────────────────────────────────────
    def test_rh_can_edit_salaries(self):
        assert _user("RH").has_permission("edit_salaries") is True

    def test_rh_can_edit_bulletins(self):
        assert _user("RH").has_permission("edit_bulletins") is True

    def test_rh_cannot_export_sage(self):
        assert _user("RH").has_permission("export_sage") is False

    def test_rh_cannot_manage_parametres_implied(self):
        u = _user("RH")
        assert u.can_manage_parametres is False

    # ── COMPTABLE ──────────────────────────────────────────────────────────
    def test_comptable_can_view_bulletins(self):
        assert _user("COMPTABLE").has_permission("view_bulletins") is True

    def test_comptable_cannot_edit_bulletins(self):
        assert _user("COMPTABLE").has_permission("edit_bulletins") is False

    def test_comptable_can_export_sage(self):
        assert _user("COMPTABLE").has_permission("export_sage") is True

    def test_comptable_can_export_cnss(self):
        assert _user("COMPTABLE").has_permission("export_cnss") is True

    def test_comptable_cannot_edit_salaries(self):
        assert _user("COMPTABLE").has_permission("edit_salaries") is False

    # ── DIRECTEUR ──────────────────────────────────────────────────────────
    def test_directeur_can_view_dashboard(self):
        assert _user("DIRECTEUR").has_permission("view_dashboard") is True

    def test_directeur_can_view_rapports(self):
        assert _user("DIRECTEUR").has_permission("view_rapports") is True

    def test_directeur_cannot_edit_salaries(self):
        assert _user("DIRECTEUR").has_permission("edit_salaries") is False

    def test_directeur_cannot_edit_bulletins(self):
        assert _user("DIRECTEUR").has_permission("edit_bulletins") is False

    def test_directeur_cannot_export_sage(self):
        assert _user("DIRECTEUR").has_permission("export_sage") is False

    # ── GESTIONNAIRE (compatibilité) ───────────────────────────────────────
    def test_gestionnaire_can_edit_salaries(self):
        assert _user("GESTIONNAIRE").has_permission("edit_salaries") is True

    def test_gestionnaire_cannot_export_sage(self):
        assert _user("GESTIONNAIRE").has_permission("export_sage") is False

    # ── Rôle inconnu ──────────────────────────────────────────────────────
    def test_unknown_role_no_permission(self):
        assert _user("INCONNU").has_permission("edit_salaries") is False


class TestCanEdit:
    def test_admin_can_edit(self):
        assert _user("TENANT_ADMIN").can_edit is True

    def test_rh_can_edit(self):
        assert _user("RH").can_edit is True

    def test_gestionnaire_can_edit(self):
        assert _user("GESTIONNAIRE").can_edit is True

    def test_comptable_cannot_edit(self):
        assert _user("COMPTABLE").can_edit is False

    def test_directeur_cannot_edit(self):
        assert _user("DIRECTEUR").can_edit is False


class TestCanExport:
    def test_admin_can_export(self):
        assert _user("TENANT_ADMIN").can_export is True

    def test_rh_can_export_excel(self):
        assert _user("RH").can_export is True

    def test_comptable_can_export(self):
        assert _user("COMPTABLE").can_export is True

    def test_directeur_cannot_export(self):
        assert _user("DIRECTEUR").can_export is False


class TestCanExportSage:
    def test_admin_can_export_sage(self):
        assert _user("TENANT_ADMIN").can_export_sage is True

    def test_comptable_can_export_sage(self):
        assert _user("COMPTABLE").can_export_sage is True

    def test_rh_cannot_export_sage(self):
        assert _user("RH").can_export_sage is False

    def test_directeur_cannot_export_sage(self):
        assert _user("DIRECTEUR").can_export_sage is False


class TestCanManageParametres:
    def test_admin_can_manage(self):
        assert _user("TENANT_ADMIN").can_manage_parametres is True

    def test_rh_cannot_manage(self):
        assert _user("RH").can_manage_parametres is False

    def test_comptable_cannot_manage(self):
        assert _user("COMPTABLE").can_manage_parametres is False


class TestCanManageUsers:
    def test_admin_can_manage_users(self):
        assert _user("TENANT_ADMIN").can_manage_users is True

    def test_rh_cannot_manage_users(self):
        assert _user("RH").can_manage_users is False


class TestRoleLabel:
    def test_admin_label(self):
        assert _user("TENANT_ADMIN").role_label == "Administrateur"

    def test_rh_label(self):
        assert _user("RH").role_label == "Responsable RH"

    def test_comptable_label(self):
        assert _user("COMPTABLE").role_label == "Comptable"

    def test_directeur_label(self):
        assert _user("DIRECTEUR").role_label == "Directeur"

    def test_gestionnaire_label(self):
        assert _user("GESTIONNAIRE").role_label == "Gestionnaire"


class TestViewOnly:
    def test_comptable_view_only(self):
        assert _user("COMPTABLE").can_view_only is True

    def test_directeur_view_only(self):
        assert _user("DIRECTEUR").can_view_only is True

    def test_rh_not_view_only(self):
        assert _user("RH").can_view_only is False

    def test_admin_not_view_only(self):
        assert _user("TENANT_ADMIN").can_view_only is False
