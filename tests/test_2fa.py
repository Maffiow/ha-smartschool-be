"""Two-factor (authenticator app) login tests, without network."""
import json
from unittest.mock import MagicMock

import pytest

from custom_components.smartschool_be.smartschool_api import (
    SmartschoolAPI,
    SmartschoolTwoFactorFailed,
    SmartschoolTwoFactorRequired,
    normalize_totp_secret,
    totp_code,
)

BASE = "https://s.smartschool.be"
# RFC 6238 test key "12345678901234567890" in base32
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

LOGIN_FORM = """<html><form action="/login">
<input type="hidden" name="login_form[_token]" value="t">
<input name="login_form[_username]"><input name="login_form[_password]" type="password">
</form></html>"""


def test_totp_rfc6238_vectors():
    # SHA1 vectors from RFC 6238 appendix B, last 6 digits
    assert totp_code(RFC_SECRET, at=59) == "287082"
    assert totp_code(RFC_SECRET, at=1111111109) == "081804"
    assert totp_code(RFC_SECRET, at=1234567890) == "005924"


def test_normalize_secret():
    assert normalize_totp_secret("gezd gnbv-gy3t qojq") == "GEZDGNBVGY3TQOJQ"
    assert normalize_totp_secret("") == ""
    with pytest.raises(ValueError):
        normalize_totp_secret("123456")  # the 6-digit code instead of the key


class FakeSession:
    """Routes requests by method + path; records what was posted."""

    def __init__(self, accept_code: bool = True):
        self.accept_code = accept_code
        self.passed_2fa = False
        self.posted_code = None
        self.headers = {}

    def _resp(self, path, text="", ctype="text/html", status=200, json_data=None):
        r = MagicMock()
        r.url = BASE + path
        r.text = text
        r.status_code = status
        r.headers = {"Content-Type": ctype}
        r.raise_for_status = MagicMock()
        r.json.return_value = json_data
        return r

    def get(self, url, **kwargs):
        path = url.replace(BASE, "") or "/"
        if path.startswith("/login"):
            return self._resp("/login", LOGIN_FORM)
        if path == "/2fa/api/v1/config":
            return self._resp(path, ctype="application/json",
                              json_data={"possibleAuthenticationMechanisms": ["googleAuthenticator"]})
        # Every other page redirects to /2fa until the code was accepted.
        return self._resp("/" if self.passed_2fa else "/2fa", "<html>home</html>")

    def post(self, url, data=None, **kwargs):
        path = url.replace(BASE, "")
        if path == "/login":
            return self._resp("/2fa", "<html>2fa</html>")
        if path == "/2fa/api/v1/google-authenticator":
            self.posted_code = json.loads(data)["google2fa"]
            self.posted_headers = kwargs.get("headers") or {}
            self.passed_2fa = self.accept_code
            return self._resp(path, "{}", ctype="application/json")
        raise AssertionError(f"unexpected POST {path}")

    def close(self):
        pass


def _api(secret="", accept=True):
    api = SmartschoolAPI(BASE, "jan", "pw", "2010-01-01", secret)
    api._session = FakeSession(accept_code=accept)
    return api


def test_2fa_without_key_asks_for_it():
    api = _api()
    with pytest.raises(SmartschoolTwoFactorRequired):
        api.login()
    assert api._session.posted_code is None


def test_2fa_with_key_logs_in():
    api = _api(RFC_SECRET)
    api.login()
    assert api._is_logged_in
    code = api._session.posted_code
    assert len(code) == 6 and code.isdigit()
    assert code == totp_code(RFC_SECRET) or code == totp_code(RFC_SECRET, at=__import__("time").time() - 30)
    assert api._session.posted_headers["Content-Type"] == "application/json"


def test_2fa_rejected_code():
    api = _api(RFC_SECRET, accept=False)
    with pytest.raises(SmartschoolTwoFactorFailed):
        api.login()
    assert not api._is_logged_in
