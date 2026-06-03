"""
tests/test_cinetpay.py — Tests unitaires du module CinetPay

Exécution :
    pytest tests/test_cinetpay.py -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock


# ── Tests normalisation téléphone ──────────────────────────────────────────────

class TestNormalisationTelephone:
    def _norm(self, tel):
        from cinetpay import _normaliser_telephone
        return _normaliser_telephone(tel)

    def test_format_local(self):
        assert self._norm("06123456") == "+24106123456"

    def test_format_avec_espaces(self):
        assert self._norm("06 12 34 56") == "+24106123456"

    def test_format_indicatif_plus(self):
        assert self._norm("+24106123456") == "+24106123456"

    def test_format_00241(self):
        assert self._norm("0024106123456") == "+24106123456"

    def test_format_241_sans_plus(self):
        assert self._norm("24106123456") == "+24106123456"


# ── Tests traduction erreurs ───────────────────────────────────────────────────

class TestTraductionErreurs:
    def test_code_401(self):
        from cinetpay import _traduire_erreur
        msg = _traduire_erreur("401", "")
        assert "api" in msg.lower() or "clé" in msg.lower()

    def test_code_623_reference_dupliquee(self):
        from cinetpay import _traduire_erreur
        msg = _traduire_erreur("623", "")
        assert "référence" in msg.lower() or "utilisée" in msg.lower()

    def test_code_inconnu(self):
        from cinetpay import _traduire_erreur
        msg = _traduire_erreur("999", "Erreur mystère")
        assert "999" in msg


# ── Tests initier_paiement ────────────────────────────────────────────────────

class TestInitierPaiement:
    def test_erreur_si_config_manquante(self):
        import cinetpay as cp
        orig_key  = cp.CINETPAY_API_KEY
        orig_site = cp.CINETPAY_SITE_ID
        cp.CINETPAY_API_KEY  = ""
        cp.CINETPAY_SITE_ID  = ""
        try:
            from cinetpay import CinetPayConfigError
            with pytest.raises(CinetPayConfigError):
                cp.initier_paiement("REF-001", 35000)
        finally:
            cp.CINETPAY_API_KEY  = orig_key
            cp.CINETPAY_SITE_ID  = orig_site

    def test_montant_trop_bas(self):
        import cinetpay as cp
        cp.CINETPAY_API_KEY  = "FAKE_KEY"
        cp.CINETPAY_SITE_ID  = "12345"
        result = cp.initier_paiement("REF-000", 50)
        assert result["success"] is False
        assert "100" in result["message"]

    @patch("cinetpay.requests.post")
    def test_succes_retourne_payment_url(self, mock_post):
        import cinetpay as cp
        cp.CINETPAY_API_KEY = "FAKE_KEY"
        cp.CINETPAY_SITE_ID = "12345"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": "201",
            "message": "CREATED",
            "data": {
                "payment_url":   "https://secure.cinetpay.com/payment/ABC123",
                "payment_token": "TOKEN_ABC123",
            }
        }
        mock_post.return_value = mock_resp

        result = cp.initier_paiement("REF-CP-001", 35000,
                                      nom_client="Jean Dupont",
                                      email_client="jean@test.ga")
        assert result["success"] is True
        assert "cinetpay.com" in result["payment_url"]
        assert result["payment_token"] == "TOKEN_ABC123"

    @patch("cinetpay.requests.post")
    def test_echec_cle_invalide(self, mock_post):
        import cinetpay as cp
        cp.CINETPAY_API_KEY = "MAUVAISE_CLE"
        cp.CINETPAY_SITE_ID = "12345"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": "401",
            "message": "Invalid API Key",
        }
        mock_post.return_value = mock_resp

        result = cp.initier_paiement("REF-CP-002", 35000)
        assert result["success"] is False
        assert result["payment_url"] is None

    @patch("cinetpay.requests.post", side_effect=Exception("Timeout réseau"))
    def test_erreur_reseau_ne_leve_pas_exception(self, mock_post):
        import cinetpay as cp
        cp.CINETPAY_API_KEY = "FAKE"
        cp.CINETPAY_SITE_ID = "12345"
        result = cp.initier_paiement("REF-CP-003", 35000)
        assert result["success"] is False
        assert "connexion" in result["message"].lower() or "Timeout" in result["message"]


# ── Tests verifier_statut ─────────────────────────────────────────────────────

class TestVerifierStatut:
    @patch("cinetpay.requests.post")
    def test_statut_accepted(self, mock_post):
        import cinetpay as cp
        cp.CINETPAY_API_KEY = "FAKE"
        cp.CINETPAY_SITE_ID = "12345"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": "00",
            "message": "SUCCESS",
            "data": {"status": "ACCEPTED", "amount": 35000, "payment_method": "AIRTEL_MONEY"}
        }
        mock_post.return_value = mock_resp

        r = cp.verifier_statut("CP-1-ABC")
        assert r["success"] is True
        assert r["statut"] == "ACCEPTED"
        assert r["operateur"] == "AIRTEL_MONEY"

    @patch("cinetpay.requests.post")
    def test_statut_refused(self, mock_post):
        import cinetpay as cp
        cp.CINETPAY_API_KEY = "FAKE"
        cp.CINETPAY_SITE_ID = "12345"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": "600",
            "data": {"status": "REFUSED"}
        }
        mock_post.return_value = mock_resp

        r = cp.verifier_statut("CP-2-DEF")
        assert r["success"] is False
        assert r["statut"] == "REFUSED"

    @patch("cinetpay.requests.post")
    def test_statut_pending(self, mock_post):
        import cinetpay as cp
        cp.CINETPAY_API_KEY = "FAKE"
        cp.CINETPAY_SITE_ID = "12345"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": "00", "data": {"status": "PENDING"}}
        mock_post.return_value = mock_resp

        r = cp.verifier_statut("CP-3-GHI")
        assert r["success"] is False
        assert r["statut"] == "PENDING"


# ── Tests validation webhook ──────────────────────────────────────────────────

class TestValiderWebhook:
    def test_site_id_correct(self):
        import cinetpay as cp
        orig = cp.CINETPAY_SITE_ID
        cp.CINETPAY_SITE_ID = "99999"
        try:
            assert cp.valider_webhook({"cpm_site_id": "99999"}) is True
        finally:
            cp.CINETPAY_SITE_ID = orig

    def test_site_id_incorrect(self):
        import cinetpay as cp
        orig = cp.CINETPAY_SITE_ID
        cp.CINETPAY_SITE_ID = "99999"
        try:
            assert cp.valider_webhook({"cpm_site_id": "11111"}) is False
        finally:
            cp.CINETPAY_SITE_ID = orig

    def test_sans_site_id(self):
        import cinetpay as cp
        assert cp.valider_webhook({}) is False

    def test_champ_site_id_alternatif(self):
        """CinetPay peut envoyer site_id ou cpm_site_id selon la version."""
        import cinetpay as cp
        orig = cp.CINETPAY_SITE_ID
        cp.CINETPAY_SITE_ID = "77777"
        try:
            assert cp.valider_webhook({"site_id": "77777"}) is True
        finally:
            cp.CINETPAY_SITE_ID = orig
