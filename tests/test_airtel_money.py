"""
tests/test_airtel_money.py — Tests unitaires du module Airtel Money

Exécution :
    pytest tests/test_airtel_money.py -v

Ces tests utilisent unittest.mock pour simuler les appels HTTP
sans contacter la vraie API Airtel.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock


# ── Tests normalisation téléphone ──────────────────────────────────────────────

class TestNormalisationTelephone:
    def _norm(self, tel):
        from airtel_money import _normaliser_telephone
        return _normaliser_telephone(tel)

    def test_format_local_07(self):
        assert self._norm("07123456") == "24107123456"

    def test_format_avec_espaces(self):
        assert self._norm("07 12 34 56") == "24107123456"

    def test_format_avec_indicatif_plus(self):
        assert self._norm("+24107123456") == "24107123456"

    def test_format_avec_00241(self):
        assert self._norm("0024107123456") == "24107123456"

    def test_format_deja_complet(self):
        assert self._norm("24107123456") == "24107123456"

    def test_format_avec_tirets(self):
        assert self._norm("07-12-34-56") == "24107123456"


# ── Tests traduction erreurs ───────────────────────────────────────────────────

class TestTraductionErreurs:
    def test_code_solde_insuffisant(self):
        from airtel_money import _traduire_erreur
        msg = _traduire_erreur("DP00800001001", "")
        assert "solde" in msg.lower() or "insuffisant" in msg.lower()

    def test_code_inconnu(self):
        from airtel_money import _traduire_erreur
        msg = _traduire_erreur("INCONNU999", "Erreur mystère")
        assert "INCONNU999" in msg or "mystère" in msg.lower()


# ── Tests token OAuth ──────────────────────────────────────────────────────────

class TestGetAccessToken:
    def test_erreur_si_credentials_manquants(self):
        import airtel_money as am
        original_id  = am.AIRTEL_CLIENT_ID
        original_sec = am.AIRTEL_CLIENT_SECRET
        am.AIRTEL_CLIENT_ID     = ""
        am.AIRTEL_CLIENT_SECRET = ""
        am._token_cache = {"token": None, "expires_at": __import__("datetime").datetime(2000, 1, 1)}
        try:
            from airtel_money import AirtelConfigError
            with pytest.raises(AirtelConfigError):
                am._get_access_token()
        finally:
            am.AIRTEL_CLIENT_ID     = original_id
            am.AIRTEL_CLIENT_SECRET = original_sec

    def test_utilise_cache_si_valide(self):
        import airtel_money as am
        from datetime import datetime, timedelta
        am._token_cache = {
            "token":      "TOKEN_CACHE",
            "expires_at": datetime.utcnow() + timedelta(hours=1),
        }
        token = am._get_access_token()
        assert token == "TOKEN_CACHE"


# ── Tests initier_paiement ────────────────────────────────────────────────────

class TestInitierPaiement:
    @patch("airtel_money.requests.post")
    @patch("airtel_money._get_access_token", return_value="FAKE_TOKEN")
    def test_succes_stk_push(self, mock_token, mock_post):
        """Simule une réponse succès d'Airtel (code DP00800001006)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": {"code": "DP00800001006", "message": "Accepted"},
            "data": {"transaction": {"id": "TXN-ABC-123"}},
        }
        mock_post.return_value = mock_resp

        from airtel_money import initier_paiement
        result = initier_paiement("REF-001", "07123456", 35000)

        assert result["success"] is True
        assert result["transaction_id"] == "TXN-ABC-123"
        assert "envoyée" in result["message"].lower()

    @patch("airtel_money.requests.post")
    @patch("airtel_money._get_access_token", return_value="FAKE_TOKEN")
    def test_echec_solde_insuffisant(self, mock_token, mock_post):
        """Simule un échec pour solde insuffisant."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": {"code": "DP00800001001", "message": "Insufficient balance"},
        }
        mock_post.return_value = mock_resp

        from airtel_money import initier_paiement
        result = initier_paiement("REF-002", "07123456", 35000)

        assert result["success"] is False
        assert result["transaction_id"] is None
        assert "solde" in result["message"].lower() or "insuffisant" in result["message"].lower()

    @patch("airtel_money.requests.post", side_effect=Exception("Timeout"))
    @patch("airtel_money._get_access_token", return_value="FAKE_TOKEN")
    def test_erreur_reseau(self, mock_token, mock_post):
        """Simule une erreur réseau — ne doit pas lever d'exception."""
        from airtel_money import initier_paiement
        result = initier_paiement("REF-003", "07123456", 35000)
        assert result["success"] is False
        assert "connexion" in result["message"].lower() or "Timeout" in result["message"]


# ── Tests verifier_statut ─────────────────────────────────────────────────────

class TestVerifierStatut:
    @patch("airtel_money.requests.get")
    @patch("airtel_money._get_access_token", return_value="FAKE_TOKEN")
    def test_statut_succes(self, mock_token, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"transaction": {"status": "TS", "id": "TXN-123"}},
        }
        mock_get.return_value = mock_resp

        from airtel_money import verifier_statut
        r = verifier_statut("TXN-123")
        assert r["success"] is True
        assert r["statut"] == "SUCCESS"

    @patch("airtel_money.requests.get")
    @patch("airtel_money._get_access_token", return_value="FAKE_TOKEN")
    def test_statut_en_attente(self, mock_token, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": {"code": "DP00800001006", "message": "Pending"},
        }
        mock_get.return_value = mock_resp

        from airtel_money import verifier_statut
        r = verifier_statut("TXN-456")
        assert r["success"] is False
        assert r["statut"] == "PENDING"

    @patch("airtel_money.requests.get")
    @patch("airtel_money._get_access_token", return_value="FAKE_TOKEN")
    def test_statut_echec(self, mock_token, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"transaction": {"status": "TF"}},
        }
        mock_get.return_value = mock_resp

        from airtel_money import verifier_statut
        r = verifier_statut("TXN-789")
        assert r["success"] is False
        assert r["statut"] == "FAILED"


# ── Tests signature webhook ───────────────────────────────────────────────────

class TestSignatureWebhook:
    def test_signature_valide(self):
        import airtel_money as am
        import hmac, hashlib
        secret  = "MON_SECRET_TEST"
        payload = b'{"transaction":{"id":"TXN-1","status":"TS"}}'
        sig     = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        original = am.AIRTEL_WEBHOOK_SECRET
        am.AIRTEL_WEBHOOK_SECRET = secret
        try:
            from airtel_money import valider_signature_webhook
            assert valider_signature_webhook(payload, sig) is True
        finally:
            am.AIRTEL_WEBHOOK_SECRET = original

    def test_signature_invalide(self):
        import airtel_money as am
        original = am.AIRTEL_WEBHOOK_SECRET
        am.AIRTEL_WEBHOOK_SECRET = "MON_SECRET"
        try:
            from airtel_money import valider_signature_webhook
            assert valider_signature_webhook(b"payload", "mauvaise_signature") is False
        finally:
            am.AIRTEL_WEBHOOK_SECRET = original

    def test_sans_secret_accepte_tout(self):
        """En dev (secret non configuré), tout est accepté."""
        import airtel_money as am
        original = am.AIRTEL_WEBHOOK_SECRET
        am.AIRTEL_WEBHOOK_SECRET = ""
        try:
            from airtel_money import valider_signature_webhook
            assert valider_signature_webhook(b"payload", "") is True
        finally:
            am.AIRTEL_WEBHOOK_SECRET = original
