"""Smartschool API client."""
import hashlib
import hmac
import html as html_lib
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from html import unescape
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

import requests
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)

# Smartschool message flags ("labels") as used by the web client.
MESSAGE_FLAGS = {"none": 0, "green": 1, "yellow": 2, "red": 3, "blue": 4}

_MESSAGES_DISPATCHER = "/?module=Messages&file=dispatcher"
_AGENDA_DISPATCHER = "/?module=Agenda&file=dispatcher"
_SEND_ERROR_MARKERS = ("var error", "type='error'", 'type="error"')
_WINDOW_CLOSE_RE = re.compile(r"\bwindow\.close\s*\(\s*\)")


class _SessionExpired(Exception):
    """Internal: Smartschool answered with a login/verification page."""


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _el_to_dict(element: ET.Element) -> dict:
    """Convert a flat-ish XML element into a dict.

    Leaf children become strings; children that only hold repeated leaves
    (e.g. <receivers><to>A</to><to>B</to></receivers>) become lists.
    """
    result: dict[str, Any] = {}
    for child in element:
        if len(child) == 0:
            result[child.tag] = (child.text or "").strip()
        elif all(len(grandchild) == 0 for grandchild in child):
            result[child.tag] = [(grandchild.text or "").strip() for grandchild in child]
        else:
            result[child.tag] = _el_to_dict(child)
    return result


_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


class TotpSecretError(ValueError):
    """The 2FA key cannot be used; `reason` is a translation key for the form."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_totp_secret(secret: str) -> str:
    """Clean an authenticator key as leniently as authenticator apps do.

    Accepts the key with spaces, dashes, invisible characters, '=' padding,
    lower case, or a whole otpauth:// link (from the QR code).
    """
    text = (secret or "").strip()
    if text.lower().startswith("otpauth://"):
        match = re.search(r"[?&]secret=([^&#]+)", text, re.IGNORECASE)
        text = match.group(1) if match else ""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", text).upper()
    if not cleaned:
        return ""
    if cleaned.isdigit():
        raise TotpSecretError("totp_looks_like_code")
    if any(char not in _B32_ALPHABET for char in cleaned) or len(cleaned) < 16:
        raise TotpSecretError("invalid_totp_secret")
    return cleaned


def _b32decode_lenient(secret: str) -> bytes:
    """Base32 decode without padding rules (trailing bits are ignored)."""
    bits = 0
    value = 0
    out = bytearray()
    for char in secret:
        value = (value << 5) | _B32_ALPHABET.index(char)
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((value >> bits) & 0xFF)
    return bytes(out)


def totp_code(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 time-based one-time password (as an authenticator app shows it)."""
    key = _b32decode_lenient(secret)
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def html_to_text(value: str | None) -> str:
    """Render message HTML as plain text."""
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"]):
        block.append("\n\n")
    for block in soup.find_all(["div", "li", "tr"]):
        block.append("\n")
    text = soup.get_text()
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def text_to_html(value: str) -> str:
    """Turn plain text (with newlines) into simple message HTML."""
    paragraphs = [p for p in re.split(r"\n\s*\n", value.strip()) if p.strip()]
    return "".join(
        "<p>" + html_lib.escape(p.strip()).replace("\n", "<br>") + "</p>" for p in paragraphs
    )


class SmartschoolError(Exception):
    """Base exception for Smartschool errors."""


class SmartschoolConnectionError(SmartschoolError):
    """Network problem or unexpected page while talking to Smartschool."""


class SmartschoolAuthError(SmartschoolError):
    """Credentials or account verification rejected by Smartschool."""


class SmartschoolBirthdayError(SmartschoolAuthError):
    """Wrong or missing birthday during account verification."""


class SmartschoolTwoFactorRequired(SmartschoolAuthError):
    """Smartschool asks for a 2FA code but no authenticator key is configured."""


class SmartschoolTwoFactorFailed(SmartschoolAuthError):
    """Smartschool rejected the generated 2FA code."""


class SmartschoolModuleUnavailable(SmartschoolError):
    """The school (or this account) has this Smartschool module switched off."""


class SmartschoolAPI:
    """Client for Smartschool."""

    def __init__(
        self, base_url: str, username: str, password: str, birthday: str = "", totp_secret: str = ""
    ):
        self._base_url = base_url.rstrip("/")
        self._login_url = f"{self._base_url}/login"
        self._results_url = f"{self._base_url}/results"
        self._evaluations_api = f"{self._base_url}/results/api/v1/evaluations/"
        self._username = username
        self._password = password
        self._birthday = birthday
        self._totp_secret = normalize_totp_secret(totp_secret) if totp_secret else ""
        self._pending_code = ""
        self._studentcard_url = f"{self._base_url}/Studentcard"
        self._students_url = f"{self._base_url}/Studentcard/Student/getStudents"
        self._session = requests.Session()
        self._is_logged_in = False
        # Coordinators and actions run in executor threads; serialize all
        # calls so they never race on the shared login session.
        self.lock = threading.RLock()
        self._warmed_modules: set[str] = set()

        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )

    # ------------------------------------------------------------------
    # Form helpers
    # ------------------------------------------------------------------

    def _extract_form(self, html: str, form_name: str, current_url: str = "") -> tuple[str, dict]:
        """Extract form action URL and all input fields from a named form.

        Empty action means "post to current page" (browser behaviour).
        """
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", {"name": form_name})
        if not form:
            raise SmartschoolConnectionError(f"Form '{form_name}' niet gevonden op pagina.")

        action = form.get("action") or ""
        if not action:
            post_url = current_url or self._base_url
        elif action.startswith("http"):
            post_url = action
        else:
            post_url = f"{self._base_url}{action}"

        payload = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")

        return post_url, payload

    def _extract_login_form(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        form = None
        for candidate in soup.find_all("form"):
            if candidate.find("input", {"name": "login_form[_username]"}) and candidate.find(
                "input", {"name": "login_form[_password]"}
            ):
                form = candidate
                break

        if not form:
            raise SmartschoolConnectionError("Login form niet gevonden.")

        action = form.get("action") or "/login"
        post_url = action if action.startswith("http") else f"{self._base_url}{action}"

        payload = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")

        payload["login_form[_username]"] = self._username
        payload["login_form[_password]"] = self._password
        return post_url, payload

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _is_login_page(self, response: requests.Response) -> bool:
        url = response.url or ""
        if "/login" in url:
            return True
        txt = (response.text or "")[:2000]
        return "login_form[_username]" in txt or "login_form[_password]" in txt

    def _is_account_verification_page(self, response: requests.Response) -> bool:
        return "/account-verification" in (response.url or "")

    @staticmethod
    def _is_2fa_page(response: requests.Response) -> bool:
        url = response.url if isinstance(response.url, str) else ""
        return bool(re.search(r"^https?://[^/]+/2fa(?:[/?#]|$)", url))

    def _handle_2fa(self, response: requests.Response) -> requests.Response:
        """Answer Smartschool's two-factor step with a code from the authenticator key."""
        if self._totp_secret:
            code = totp_code(self._totp_secret)
        elif self._pending_code:
            # A code typed by the user is valid once, for 30 seconds.
            code, self._pending_code = self._pending_code, ""
        else:
            raise SmartschoolTwoFactorRequired(
                "Smartschool vraagt een 2FA-code; meld de integratie opnieuw aan met de "
                "code uit je authenticator-app"
            )

        config = self._session.get(
            f"{self._base_url}/2fa/api/v1/config",
            headers={"Accept": "application/json", "Referer": response.url},
            timeout=20,
        )
        config.raise_for_status()
        try:
            mechanisms = config.json().get("possibleAuthenticationMechanisms") or []
        except ValueError as err:
            raise SmartschoolConnectionError("2FA-configuratie onleesbaar") from err
        if "googleAuthenticator" not in mechanisms:
            raise SmartschoolAuthError(
                f"Deze 2FA-methode wordt niet ondersteund: {', '.join(mechanisms) or 'onbekend'}"
            )

        _LOGGER.debug("Answering Smartschool 2FA with an authenticator code")
        answer = self._session.post(
            f"{self._base_url}/2fa/api/v1/google-authenticator",
            data=json.dumps({"google2fa": code}),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self._base_url,
                "Referer": response.url,
            },
            timeout=20,
        )
        _LOGGER.debug("2FA answer: status=%s", answer.status_code)

        home = self._session.get(f"{self._base_url}/", timeout=20, allow_redirects=True)
        home.raise_for_status()
        if self._is_2fa_page(home):
            raise SmartschoolTwoFactorFailed(
                "Smartschool weigerde de 2FA-code (verlopen of fout getypt?)"
            )
        return home

    def _handle_account_verification(self, response: requests.Response) -> requests.Response:
        """Submit birthday to the account-verification form."""
        if not self._birthday:
            raise SmartschoolBirthdayError(
                "Smartschool vraagt om account-verificatie (geboortedatum), "
                "maar er is geen geboortedatum geconfigureerd."
            )

        _LOGGER.debug("Handling /account-verification with birthday")

        soup_check = BeautifulSoup(response.text, "html.parser")
        inp = soup_check.find("input", {"name": "account_verification_form[_security_question_answer]"})
        if inp:
            _LOGGER.debug(
                "Answer field — placeholder='%s' type='%s'",
                inp.get("placeholder", ""),
                inp.get("type", ""),
            )

        try:
            post_url, payload = self._extract_form(
                response.text, "account_verification_form", current_url=response.url
            )
        except SmartschoolConnectionError:
            post_url = response.url
            payload = {}

        payload["account_verification_form[_security_question_answer]"] = self._birthday
        _LOGGER.debug("Posting account-verification to %s", post_url)

        verified = self._session.post(
            post_url,
            data=payload,
            timeout=20,
            allow_redirects=True,
            headers={
                "Origin": self._base_url,
                "Referer": response.url,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        verified.raise_for_status()
        _LOGGER.debug(
            "Account-verification response: status=%s url=%s",
            verified.status_code,
            verified.url,
        )
        return verified

    def login(self):
        """Log in, handling MFA/account-verification if needed."""
        if self._is_logged_in:
            return

        _LOGGER.debug("Attempting Smartschool login for user '%s'", self._username)

        try:
            login_page = self._session.get(self._login_url, timeout=20, allow_redirects=True)
            login_page.raise_for_status()
            post_url, payload = self._extract_login_form(login_page.text)
            _LOGGER.debug("Login form found, posting to %s", post_url)

            post_resp = self._session.post(
                post_url,
                data=payload,
                timeout=20,
                allow_redirects=True,
                headers={
                    "Origin": self._base_url,
                    "Referer": self._login_url,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            post_resp.raise_for_status()
            _LOGGER.debug("Login POST response: status=%s url=%s", post_resp.status_code, post_resp.url)

        except requests.exceptions.RequestException as err:
            raise SmartschoolConnectionError("Login netwerkfout") from err

        txt = (post_resp.text or "").lower()
        if "ongeldige gebruikersnaam of wachtwoord" in txt:
            raise SmartschoolAuthError("Ongeldige logingegevens")

        if self._is_account_verification_page(post_resp):
            try:
                post_resp = self._handle_account_verification(post_resp)
            except requests.exceptions.RequestException as err:
                raise SmartschoolConnectionError("Account-verificatie netwerkfout") from err

            _LOGGER.debug("Post-verification response: status=%s url=%s", post_resp.status_code, post_resp.url)

            if self._is_account_verification_page(post_resp):
                raise SmartschoolBirthdayError(
                    "Account-verificatie mislukt — controleer de geboortedatum (formaat JJJJ-MM-DD)"
                )

        if self._is_2fa_page(post_resp):
            try:
                post_resp = self._handle_2fa(post_resp)
            except requests.exceptions.RequestException as err:
                raise SmartschoolConnectionError("2FA netwerkfout") from err

        if self._is_login_page(post_resp):
            raise SmartschoolAuthError("Login mislukt: teruggeleid naar inlogpagina")

        # Smartschool may only ask for 2FA once a page is opened: check the homepage.
        try:
            home = self._session.get(f"{self._base_url}/", timeout=20, allow_redirects=True)
            if self._is_2fa_page(home):
                self._handle_2fa(home)
        except requests.exceptions.RequestException as err:
            raise SmartschoolConnectionError("Login netwerkfout") from err

        try:
            warm = self._session.get(
                self._results_url,
                timeout=20,
                allow_redirects=True,
                headers={"Referer": self._base_url + "/"},
            )
            warm.raise_for_status()
            _LOGGER.debug("Warmup /results: status=%s url=%s", warm.status_code, warm.url)

            if self._is_account_verification_page(warm):
                warm = self._handle_account_verification(warm)

        except requests.exceptions.RequestException as err:
            _LOGGER.debug("Warmup request failed (non-fatal): %s", err)

        self._is_logged_in = True
        _LOGGER.debug("Successfully logged in to Smartschool.")

    def set_2fa_code(self, code: str) -> None:
        """Use this 6-digit authenticator code at the next 2FA step."""
        self._pending_code = re.sub(r"\D", "", code or "")

    def export_cookies(self) -> list[dict]:
        """Session cookies, to keep the login across restarts (no 2FA code needed)."""
        return [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "secure": bool(c.secure),
                "expires": c.expires,
            }
            for c in list(self._session.cookies)
        ]

    def import_cookies(self, cookies: list[dict]) -> None:
        for c in cookies or []:
            try:
                self._session.cookies.set(
                    c["name"],
                    c["value"],
                    domain=c.get("domain") or "",
                    path=c.get("path") or "/",
                    secure=bool(c.get("secure")),
                    expires=c.get("expires"),
                )
            except (KeyError, TypeError) as err:
                _LOGGER.debug("Skipping stored cookie: %s", err)

    def restore_session(self) -> bool:
        """True when the stored cookies still give a logged-in session."""
        with self.lock:
            if not list(self._session.cookies):
                return False
            try:
                home = self._session.get(f"{self._base_url}/", timeout=20, allow_redirects=True)
            except requests.exceptions.RequestException as err:
                raise SmartschoolConnectionError("Smartschool niet bereikbaar") from err
            url = home.url if isinstance(home.url, str) else ""
            if (
                "/login" in url
                or self._is_2fa_page(home)
                or self._is_account_verification_page(home)
                or home.status_code >= 400
            ):
                _LOGGER.debug("Stored Smartschool session is no longer valid")
                self._session.cookies.clear()
                return False
            self._is_logged_in = True
            return True

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()
        self._is_logged_in = False

    def _force_relogin(self):
        """Reset session and re-authenticate."""
        _LOGGER.debug("Forcing re-login (session likely expired)")
        self._is_logged_in = False
        self._warmed_modules = set()
        self._session.close()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )
        self.login()

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_evaluations_page(self, page: int = 1, items: int = 50) -> list | None:
        """Fetch one page of evaluations from the API."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self._results_url,
            "Origin": self._base_url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        url = f"{self._evaluations_api}?pageNumber={page}&itemsOnPage={items}"
        resp = self._session.get(url, timeout=20, allow_redirects=True, headers=headers)
        if resp.status_code == 403:
            raise SmartschoolModuleUnavailable("Resultaten zijn niet actief voor dit account")
        resp.raise_for_status()

        ctype = (resp.headers.get("Content-Type") or "").lower()
        _LOGGER.debug("Evaluations API p%d: status=%s ctype=%s url=%s", page, resp.status_code, ctype, resp.url)

        if self._is_account_verification_page(resp) or self._is_login_page(resp):
            _LOGGER.debug("Evaluations API redirected to auth page — session lost")
            return None

        if "json" not in ctype:
            _LOGGER.debug("API non-json response preview: %s", (resp.text or "")[:200].replace("\n", " "))
            return None

        try:
            data = resp.json()
        except Exception as err:
            _LOGGER.debug("Failed to parse API JSON: %s", err)
            return None

        if isinstance(data, list):
            return data or None
        if isinstance(data, dict):
            for key in ("evaluations", "items", "data", "results"):
                val = data.get(key)
                if isinstance(val, list) and val:
                    return val
        return None

    def _normalize(self, evaluation: dict) -> dict:
        """Normalize a raw evaluation dict to a consistent structure."""
        feedback_list = evaluation.get("feedbacks") or evaluation.get("feedback") or []
        feedback_text = None
        if isinstance(feedback_list, list) and feedback_list:
            feedback_text = feedback_list[0].get("text")

        courses = evaluation.get("courses") or [{}]
        return {
            "name": evaluation.get("name"),
            "course": courses[0].get("name") if courses else None,
            "score_value": (evaluation.get("graphic") or {}).get("value"),
            "score_description": (evaluation.get("graphic") or {}).get("description"),
            "date": evaluation.get("date"),
            "availability_date": evaluation.get("availabilityDate"),
            "feedback": feedback_text,
        }

    def _fetch_all_evaluations(self) -> list | None:
        """Fetch all pages of evaluations and return them as a single list."""
        all_items: list = []
        page = 1
        items_per_page = 50

        while True:
            try:
                batch = self._fetch_evaluations_page(page=page, items=items_per_page)
            except SmartschoolModuleUnavailable:
                raise
            except Exception as err:
                _LOGGER.debug("API page %d fetch failed: %s", page, err)
                break

            if not batch:
                break

            all_items.extend(batch)
            _LOGGER.debug("Page %d: got %d items (total so far: %d)", page, len(batch), len(all_items))

            if len(batch) < items_per_page:
                # Last page — no more data.
                break

            page += 1

        return all_items or None

    def get_all_evaluations(self) -> dict | None:
        """Return all evaluations grouped by course + overall latest.

        Returns:
            {
                "by_course": {course_name: normalized_eval},  # latest per course
                "latest": normalized_eval,                    # most recent overall
            }
        or None on failure.
        """
        if not self._is_logged_in:
            self.login()

        raw = None

        try:
            raw = self._fetch_all_evaluations()
        except SmartschoolModuleUnavailable:
            # No re-login: the module is simply off for this account.
            raise
        except Exception as err:
            _LOGGER.debug("API fetch failed: %s", err)

        if raw is None and self._is_logged_in:
            _LOGGER.debug("No data from API — forcing re-login")
            try:
                self._force_relogin()
                raw = self._fetch_all_evaluations()
            except Exception as err:
                _LOGGER.debug("Re-login/retry failed: %s", err)

        if not raw:
            _LOGGER.debug("No evaluations found for '%s'.", self._username)
            return None

        _LOGGER.debug("Fetched %d evaluations in total", len(raw))

        # Sort ascending by date so history lists are chronological.
        raw.sort(
            key=lambda x: x.get("availabilityDate") or x.get("date") or "",
        )

        by_course: dict[str, dict] = {}
        for item in raw:
            normalized = self._normalize(item)
            course = normalized.get("course")
            if not course:
                continue
            if course not in by_course:
                by_course[course] = {"latest": normalized, "history": []}
            by_course[course]["history"].append(normalized)
            by_course[course]["latest"] = normalized  # last in sorted order = most recent

        if not by_course:
            _LOGGER.warning("Evaluations found but no course names could be extracted.")
            return None

        # Overall latest = most recent across all courses.
        latest = self._normalize(raw[-1])
        recent = [self._normalize(item) for item in reversed(raw[-5:])]

        _LOGGER.debug(
            "Courses found: %s",
            {k: len(v["history"]) for k, v in by_course.items()},
        )
        return {"by_course": by_course, "latest": latest, "recent": recent}

    def _fetch_students(self) -> list | None:
        """Fetch students from Smartschool Studentcard API."""
        warm = self._session.get(
            self._studentcard_url,
            timeout=20,
            allow_redirects=True,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{self._base_url}/",
            },
        )
        warm.raise_for_status()

        if self._is_account_verification_page(warm) or self._is_login_page(warm):
            _LOGGER.debug("Studentcard warmup redirected to auth page — session lost")
            return None

        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self._base_url}/Studentcard",
            "Origin": self._base_url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        resp = self._session.post(
            self._students_url,
            data={},
            timeout=20,
            allow_redirects=True,
            headers=headers,
        )
        resp.raise_for_status()

        if self._is_account_verification_page(resp) or self._is_login_page(resp):
            _LOGGER.debug("Students API redirected to auth page — session lost")
            return None

        ctype = (resp.headers.get("Content-Type") or "").lower()

        _LOGGER.debug("Students API: status=%s ctype=%s", resp.status_code, ctype)

        if "json" not in ctype:
            return None

        data = resp.json()

        return data if isinstance(data, list) else None

    def get_students(self) -> list:
        """Return students linked to the current account."""
        if not self._is_logged_in:
            self.login()

        students = None

        try:
            students = self._fetch_students()
        except Exception as err:
            _LOGGER.debug("Students API fetch failed: %s", err)

        if students is None and self._is_logged_in:
            _LOGGER.debug("No students data from API — forcing re-login")
            try:
                self._force_relogin()
                students = self._fetch_students()
            except Exception as err:
                _LOGGER.debug("Students API re-login/retry failed: %s", err)

        return students or []


    # ------------------------------------------------------------------
    # Generic request helpers (messages / agenda)
    # ------------------------------------------------------------------

    def _with_relogin(self, func, *args, **kwargs):
        """Run func; when the session turned out to be expired, log in again once."""
        with self.lock:
            if not self._is_logged_in:
                self.login()
            try:
                return func(*args, **kwargs)
            except _SessionExpired:
                self._force_relogin()
                try:
                    return func(*args, **kwargs)
                except _SessionExpired as err:
                    # Login itself succeeded, so the credentials are fine: never trigger reauth here.
                    raise SmartschoolConnectionError("Sessie verloren na opnieuw aanmelden") from err
            except requests.exceptions.RequestException as err:
                raise SmartschoolConnectionError(str(err)) from err

    def _check_session(self, response: requests.Response) -> None:
        """Raise _SessionExpired for a login page, ModuleUnavailable for a 403."""
        if (
            self._is_account_verification_page(response)
            or "/login" in (response.url or "")
            or self._is_2fa_page(response)
        ):
            raise _SessionExpired
        if response.status_code == 403:
            raise SmartschoolModuleUnavailable(f"Geen toegang (403): {response.url}")

    @staticmethod
    def _describe(resp: requests.Response) -> str:
        """Short, non-personal description of an unexpected answer (for logs)."""
        url = resp.url if isinstance(resp.url, str) else ""
        path = url.split("?", 1)[0].split("/", 3)[-1] if url.count("/") >= 3 else ""
        ctype = str(resp.headers.get("Content-Type") or "").split(";", 1)[0]
        text = resp.text if isinstance(resp.text, str) else ""
        title = ""
        match = re.search(r"<title>([^<]{0,80})", text, re.IGNORECASE)
        if match:
            title = f", titel '{match.group(1).strip()}'"
        return f"HTTP {resp.status_code}, /{path}, {ctype or 'geen type'}{title}"

    def _ajax_headers(self, module: str | None = None) -> dict[str, str]:
        referer = f"{self._base_url}/?module={module}&file=index" if module else f"{self._base_url}/"
        return {
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self._base_url,
            "Referer": referer,
        }

    def _warm_module(self, module: str) -> None:
        """Open a module page once per session, as a browser does before its XHR calls."""
        if module in self._warmed_modules:
            return
        self._warmed_modules.add(module)
        try:
            resp = self._session.get(
                f"{self._base_url}/?module={module}&file=index",
                headers={"Referer": f"{self._base_url}/"},
                timeout=20,
            )
            _LOGGER.debug("Warmup module %s: %s", module, self._describe(resp))
        except requests.exceptions.RequestException as err:
            _LOGGER.debug("Warmup module %s failed: %s", module, err)

    def _xml_command(self, path: str, subsystem: str, action: str, params: dict) -> ET.Element:
        """POST a Smartschool XML dispatcher command and return the parsed root."""
        module_match = re.search(r"module=(\w+)", path)
        module = module_match.group(1) if module_match else None
        if module:
            self._warm_module(module)

        command = "<request><command>"
        command += f"<subsystem>{subsystem}</subsystem><action>{action}</action><params>"
        for key, value in params.items():
            command += f"<param name={quoteattr(str(key))}><![CDATA[{value}]]></param>"
        command += "</params></command></request>"

        resp = self._session.post(
            f"{self._base_url}{path}",
            data={"command": command},
            headers=self._ajax_headers(module),
            timeout=20,
        )
        self._check_session(resp)
        resp.raise_for_status()

        text = (resp.text or "").lstrip("\ufeff").strip()
        if not text:
            return ET.Element("server")
        if not text.startswith("<?xml") and not text.startswith("<server"):
            # Not a login page (checked above): Smartschool redirects to the
            # homepage when this module is switched off for the account.
            raise SmartschoolModuleUnavailable(
                f"{subsystem}/{action} is niet beschikbaar ({self._describe(resp)})"
            )
        root = ET.fromstring(text)
        if root.findtext(".//status") == "error":
            raise SmartschoolError(root.findtext(".//message") or "Onbekende fout")
        return root

    def _get_json(self, path: str, method: str = "get", **kwargs) -> Any:
        resp = self._session.request(
            method,
            f"{self._base_url}{path}",
            headers={**self._ajax_headers(), "Accept": "application/json"},
            timeout=20,
            **kwargs,
        )
        self._check_session(resp)
        resp.raise_for_status()
        if "json" not in (resp.headers.get("Content-Type") or "").lower():
            raise SmartschoolModuleUnavailable(
                f"{path.split('?', 1)[0]} is niet beschikbaar ({self._describe(resp)})"
            )
        data = resp.json()
        # Some endpoints answer with a JSON-encoded string.
        if isinstance(data, str):
            data = json.loads(data)
        return data

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_header(raw: dict) -> dict:
        return {
            "id": int(raw.get("id") or 0),
            "from": raw.get("from"),
            "subject": raw.get("subject"),
            "date": raw.get("date"),
            "unread": _truthy(raw.get("unread")),
            "attachments": int(raw.get("attachment") or 0),
            "flag": next(
                (k for k, v in MESSAGE_FLAGS.items() if str(v) == str(raw.get("label") or "0")),
                "none",
            ),
            "can_reply": _truthy(raw.get("allowreply")),
        }

    def get_messages(self, box: str = "inbox") -> list[dict]:
        """Return the message headers of a box (newest first)."""

        def _fetch():
            root = self._xml_command(
                _MESSAGES_DISPATCHER,
                "postboxes",
                "message list",
                {
                    "boxType": box,
                    "boxID": "0",
                    "sortField": "date",
                    "sortKey": "desc",
                    "poll": "false",
                    "poll_ids": "",
                    "layout": "new",
                },
            )
            return [
                self._normalize_header(_el_to_dict(el))
                for el in root.findall(".//messages/message")
            ]

        return self._with_relogin(_fetch)

    def get_message(self, msg_id: int, box: str = "inbox") -> dict:
        """Return one full message (Smartschool marks it as read)."""

        def _fetch():
            root = self._xml_command(
                _MESSAGES_DISPATCHER,
                "postboxes",
                "show message",
                {"msgID": msg_id, "boxType": box, "limitList": "true"},
            )
            el = root.find(".//data/message")
            if el is None:
                raise SmartschoolError(f"Bericht {msg_id} niet gevonden")
            raw = _el_to_dict(el)

            attachments = []
            if (raw.get("attachment") or "0") not in ("", "0"):
                att_root = self._xml_command(
                    _MESSAGES_DISPATCHER,
                    "postboxes",
                    "attachment list",
                    {"msgID": msg_id, "boxType": box, "limitList": "true"},
                )
                for att in att_root.findall(".//attachmentlist/attachment"):
                    a = _el_to_dict(att)
                    attachments.append({"name": a.get("name"), "size": a.get("size"), "mime": a.get("mime")})
            return raw, attachments

        raw, attachments = self._with_relogin(_fetch)

        def _as_list(value):
            if isinstance(value, list):
                return value
            return [value] if value else []

        body_html = raw.get("body") or ""
        return {
            "id": int(raw.get("id") or msg_id),
            "from": raw.get("from"),
            "subject": raw.get("subject"),
            "date": raw.get("date"),
            "to": _as_list(raw.get("receivers")),
            "cc": _as_list(raw.get("ccreceivers")),
            "text": html_to_text(body_html),
            "html": body_html,
            "can_reply": _truthy(raw.get("canReply")),
            "attachments": attachments,
        }

    def mark_message_unread(self, msg_id: int, box: str = "inbox") -> None:
        self._with_relogin(
            self._xml_command,
            _MESSAGES_DISPATCHER,
            "postboxes",
            "mark message unread",
            {"boxType": box, "boxID": "0", "msgID": msg_id, "clAction": "status"},
        )

    def set_message_flag(self, msg_id: int, flag: str, box: str = "inbox") -> None:
        if flag not in MESSAGE_FLAGS:
            raise ValueError(f"Onbekende vlag: {flag}")
        self._with_relogin(
            self._xml_command,
            _MESSAGES_DISPATCHER,
            "postboxes",
            "save msglabel",
            {"boxType": box, "msgLabel": MESSAGE_FLAGS[flag], "msgID": msg_id, "clAction": "label"},
        )

    def trash_message(self, msg_id: int) -> None:
        """Move a message to the trash (it stays recoverable in Smartschool)."""
        self._with_relogin(
            self._xml_command, _MESSAGES_DISPATCHER, "postboxes", "quick delete", {"msgID": msg_id}
        )

    def archive_message(self, msg_id: int) -> None:
        def _post():
            resp = self._session.post(
                f"{self._base_url}/Messages/Xhr/archivemessages",
                data={"msgIDs[]": [msg_id]},
                headers=self._ajax_headers("Messages"),
                timeout=20,
            )
            resp.raise_for_status()
            self._check_session(resp)
            try:
                success = resp.json().get("success") or []
            except ValueError as err:
                raise SmartschoolError(f"Archiveren van bericht {msg_id} mislukt") from err
            if int(msg_id) not in [int(x) for x in success]:
                raise SmartschoolError(f"Archiveren van bericht {msg_id} mislukt")

        self._with_relogin(_post)

    # --- composing ----------------------------------------------------

    def _compose_url(self, box: str, compose_type: int, msg_id: int | str) -> str:
        return (
            f"{self._base_url}/?module=Messages&file=composeMessage"
            f"&boxType={box}&composeType={compose_type}&msgID={msg_id}"
        )

    def _load_compose_form(self, url: str) -> tuple[dict, str]:
        self._warm_module("Messages")
        resp = self._session.get(
            url, headers={"Referer": f"{self._base_url}/?module=Messages&file=index"}, timeout=20
        )
        self._check_session(resp)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        hidden = {
            str(inp["name"]): str(inp.get("value", ""))
            for inp in soup.select("input[type=hidden][name]")
        }
        subject_input = soup.find("input", {"name": "subject"})
        subject = str(subject_input.get("value", "")) if subject_input else ""
        if not hidden.get("uniqueUsc"):
            raise SmartschoolError(
                "Het berichtenformulier kon niet geladen worden (geen rechten om berichten te sturen?)"
            )
        return hidden, subject

    def _submit_compose_form(
        self, url: str, hidden: dict, box: str, compose_type: int, msg_id: int | str,
        subject: str, body_html: str, default_action: str,
    ) -> None:
        payload = {
            "module": "Messages",
            "file": "composeMessage",
            "boxType": box,
            "composeType": str(compose_type),
            "msgID": str(msg_id),
            "encryptedSender": hidden.get("encryptedSender", ""),
            "send": "send",
            "origMsgID": hidden.get("origMsgID", "0"),
            "composeAction": hidden.get("composeAction", default_action),
            "randomDir": hidden.get("randomDir", ""),
            "uniqueUsc": hidden.get("uniqueUsc", ""),
            "showTab": hidden.get("showTab", "tab1Container"),
            "delFile": hidden.get("delFile", "0"),
            "msgFormSelectedTab": hidden.get("msgFormSelectedTab", ""),
            "sendDate": hidden.get("sendDate", ""),
            "searchField3": "",
            "searchField1": "",
            "searchField4": "",
            "searchField5": "",
            "subject": subject,
            "copyToLVS": "dontCopyToLVS",
            "message": body_html,
            "bcc": "0",
        }
        try:
            resp = self._session.post(
                url,
                files={k: (None, v) for k, v in payload.items()},
                headers={"Origin": self._base_url, "Referer": url},
                timeout=30,
            )
        except requests.exceptions.RequestException as err:
            # Never retry a send: it may have gone out already.
            raise SmartschoolError(
                f"Geen antwoord van Smartschool na verzenden ({err}); controleer 'Verzonden' voor je opnieuw verstuurt."
            ) from err

        body = resp.text or ""
        confirmed = (
            resp.status_code == 200
            and not any(marker in body for marker in _SEND_ERROR_MARKERS)
            and any(
                _WINDOW_CLOSE_RE.search(script.get_text() or "")
                for script in BeautifulSoup(body, "html.parser").find_all("script")
            )
        )
        if not confirmed:
            raise SmartschoolError(
                f"Smartschool bevestigde het verzenden niet (HTTP {resp.status_code}); "
                "controleer 'Verzonden' voor je opnieuw verstuurt."
            )

    def reply_message(
        self, msg_id: int, text: str, reply_all: bool = False, subject: str | None = None,
        box: str = "inbox",
    ) -> None:
        """Reply to a message as the Reply (all) button does."""
        compose_type = 2 if reply_all else 1
        url = self._compose_url(box, compose_type, msg_id)

        def _load():
            hidden, form_subject = self._load_compose_form(url)
            if hidden.get("origMsgID") != str(msg_id):
                raise SmartschoolError(f"Bericht {msg_id} kan niet beantwoord worden")
            return hidden, form_subject

        with self.lock:
            hidden, form_subject = self._with_relogin(_load)
            final_subject = subject or form_subject
            if not final_subject:
                original = self.get_message(msg_id, box)
                final_subject = f"Re: {original.get('subject') or ''}".strip()
            # The form's tokens belong to this session: submit without re-login.
            self._submit_compose_form(
                url, hidden, box, compose_type, msg_id, final_subject, text_to_html(text), "2"
            )

    def search_recipients(self, query: str, unique_usc: str) -> list[dict]:
        resp = self._session.post(
            f"{self._base_url}/?module=Messages&file=searchUsers",
            data={
                "val": query,
                "type": "0",
                "parentNodeId": "insertSearchFieldContainer_0_0",
                "xml": "<results></results>",
                "uniqueUsc": unique_usc,
            },
            headers=self._ajax_headers("Messages"),
            timeout=20,
        )
        self._check_session(resp)
        resp.raise_for_status()
        root = ET.fromstring((resp.text or "<results/>").strip())
        users = []
        for user in root.findall("./users/user"):
            u = _el_to_dict(user)
            users.append(
                {
                    "user_id": u.get("userID"),
                    "name": u.get("value"),
                    "ss_id": u.get("ssID"),
                    "user_lt": u.get("userLT") or "0",
                    "class": u.get("classname") or None,
                }
            )
        return users

    def send_message(self, recipient: str, subject: str, text: str) -> str:
        """Send a new message to exactly one person found by name; returns their name."""
        url = self._compose_url("inbox", 0, "undefined")

        with self.lock:
            hidden, _ = self._with_relogin(self._load_compose_form, url)
            try:
                users = self.search_recipients(recipient, hidden["uniqueUsc"])
            except requests.exceptions.RequestException as err:
                raise SmartschoolConnectionError(str(err)) from err

            exact = [u for u in users if (u["name"] or "").casefold() == recipient.casefold()]
            matches = exact or users
            if not matches:
                raise SmartschoolError(f"Geen ontvanger gevonden voor '{recipient}'")
            if len(matches) > 1:
                names = ", ".join(u["name"] or "?" for u in matches[:10])
                raise SmartschoolError(
                    f"Meerdere ontvangers gevonden voor '{recipient}': {names}. Gebruik de volledige naam."
                )
            user = matches[0]

            resp = self._session.post(
                f"{self._base_url}/?module=Messages&file=searchUsers&function=addUserToSelected",
                data={
                    "id": str(user["user_id"]),
                    "typeId": "users",
                    "type": "0",
                    "parentNodeId": "insertSearchFieldContainer_0_0",
                    "ssid": str(user["ss_id"]),
                    "userlt": str(user["user_lt"]),
                    "uniqueUsc": hidden["uniqueUsc"],
                },
                headers=self._ajax_headers("Messages"),
                timeout=20,
            )
            resp.raise_for_status()
            if str(user["user_id"]) not in (resp.text or ""):
                raise SmartschoolError(f"Smartschool weigerde ontvanger '{user['name']}'")

            self._submit_compose_form(
                url, hidden, "inbox", 0, "undefined", subject, text_to_html(text), "0"
            )
            return user["name"]

    # ------------------------------------------------------------------
    # Agenda: lessons, hours and upcoming tasks
    # ------------------------------------------------------------------

    def get_future_tasks(self) -> list[dict]:
        """Return upcoming tasks/tests, one dict per task."""

        def _fetch():
            data = self._get_json(
                "/Agenda/Futuretasks/getFuturetasks",
                method="post",
                data={"lastAssignmentID": 0, "lastDate": "", "filterType": "false", "filterID": "false"},
            )
            tasks = []
            for day in (data or {}).get("days", []):
                for course in day.get("courses", []):
                    for task in (course.get("items") or {}).get("tasks", []):
                        tasks.append(
                            {
                                "date": task.get("date") or day.get("date"),
                                "type": task.get("label"),
                                "description": task.get("description"),
                                "course": task.get("course"),
                                "course_title": course.get("course_title"),
                                "hour_id": task.get("hourID") or course.get("hourID"),
                                "warning": bool(task.get("warning")),
                            }
                        )
            return tasks

        return self._with_relogin(_fetch)

    def get_hours(self) -> dict[str, dict]:
        """Return lesson periods keyed by hourID: {start, end, title}."""

        def _fetch():
            root = self._xml_command(
                _AGENDA_DISPATCHER, "grid", "get hours", {"date": int(time.time())}
            )
            hours = {}
            for el in root.findall(".//hour"):
                h = _el_to_dict(el)
                hours[h.get("hourID")] = {"start": h.get("start"), "end": h.get("end"), "title": h.get("title")}
            return hours

        return self._with_relogin(_fetch)

    def get_lessons(self, start: date, end: date) -> list[dict]:
        """Return lessons between start and end (inclusive), max 20 days per call."""

        def _fetch(chunk_start: date, chunk_end: date):
            start_ts = int(datetime.combine(chunk_start, datetime.min.time()).timestamp())
            end_ts = int(datetime.combine(chunk_end, datetime.max.time()).timestamp())
            root = self._xml_command(
                _AGENDA_DISPATCHER,
                "agenda",
                "get lessons",
                {
                    "startDateTimestamp": start_ts,
                    "endDateTimestamp": end_ts,
                    "filterType": "false",
                    "filterID": "false",
                    "gridType": "1",
                    "classID": "0",
                    "endDateTimestampOld": end_ts,
                    "forcedTeacher": "0",
                    "forcedClass": "0",
                    "forcedClassroom": "0",
                    "assignmentTypeID": "1",
                },
            )
            return [_el_to_dict(el) for el in root.findall(".//lesson")]

        lessons: list[dict] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + timedelta(days=19))
            for raw in self._with_relogin(_fetch, cursor, chunk_end):
                lessons.append(
                    {
                        "moment_id": raw.get("momentID"),
                        "date": raw.get("date"),
                        "hour_id": raw.get("hourID"),
                        "course": raw.get("courseTitle") or raw.get("course"),
                        "subject": raw.get("subject") or None,
                        "classroom": raw.get("classroomTitle") or raw.get("classroom") or None,
                        "teacher": raw.get("teacher") or None,
                        "note": raw.get("note") or None,
                    }
                )
            cursor = chunk_end + timedelta(days=1)
        return lessons

    # ------------------------------------------------------------------
    # Account identity, message counters and planner
    # ------------------------------------------------------------------

    _AUTH_USER_RE = re.compile(r'"authenticatedUser"\s*:\s*\{[^{}]*?"id"\s*:\s*"([^"]+)"')

    def get_student_planner_id(self) -> str:
        """Planner id of the student this login belongs to.

        The page config holds the logged-in account as
        ``<platform>_<user>_<coaccount>``; a parent (co-account) sees the
        student's planner under co-account index 0.
        """

        def _fetch():
            resp = self._session.get(f"{self._base_url}/", timeout=20)
            self._check_session(resp)
            resp.raise_for_status()
            # The page config is embedded as JSON.parse('...') with \uXXXX escapes.
            text = re.sub(
                r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), resp.text or ""
            ).replace('\\"', '"')
            match = self._AUTH_USER_RE.search(text)
            if not match:
                raise SmartschoolModuleUnavailable("Gebruikers-id niet gevonden op de startpagina")
            return re.sub(r"_\d+$", "_0", match.group(1))

        return self._with_relogin(_fetch)

    def get_message_counters(self) -> dict:
        """Return {"unread": n, "total": n} for the inbox (all messages, not just one page)."""

        def _fetch():
            root = self._xml_command(
                _MESSAGES_DISPATCHER, "postboxes", "calculate_postbox_counters", {}
            )
            raw = root.findtext(".//data/data/inbox") or root.findtext(".//inbox") or "[]"
            boxes = json.loads(raw) if raw.strip() else []
            return {
                "unread": sum(int(b.get("unread") or 0) for b in boxes),
                "total": sum(int(b.get("total") or 0) for b in boxes),
            }

        return self._with_relogin(_fetch)

    def get_planned_elements(self, planner_id: str, start: datetime, end: datetime) -> list[dict]:
        """Return planner items (lessons, assignments, to-dos) between start and end."""

        def _fetch():
            data = self._get_json(
                f"/planner/api/v1/planned-elements/user/{planner_id}",
                params={
                    "from": start.isoformat(timespec="seconds"),
                    "to": end.isoformat(timespec="seconds"),
                    "types": "planned-lessons,planned-assignments,planned-to-dos,"
                    "planned-lesson-cluster-assignments",
                    "includes": "icon,courses,locations",
                },
            )
            items = []
            for raw in data or []:
                period = raw.get("period") or {}
                courses = [c.get("name") for c in raw.get("courses") or [] if c.get("name")]
                locations = [
                    loc.get("title") or loc.get("number")
                    for loc in raw.get("locations") or []
                    if loc.get("title") or loc.get("number")
                ]
                element_type = raw.get("plannedElementType") or ""
                assignment = raw.get("assignmentType") or {}
                items.append(
                    {
                        "id": raw.get("id"),
                        "kind": "lesson" if element_type.endswith("lessons") else "task",
                        "type": assignment.get("name")
                        or ("To-do" if element_type.endswith("to-dos") else "Opdracht"),
                        "name": raw.get("name") or "",
                        "course": ", ".join(courses) or None,
                        "location": ", ".join(locations) or None,
                        "start": period.get("dateTimeFrom"),
                        "end": period.get("dateTimeTo"),
                        "whole_day": bool(period.get("wholeDay")),
                        "deadline": bool(period.get("deadline")),
                        "status": raw.get("resolvedStatus") or None,
                    }
                )
            return items

        return self._with_relogin(_fetch)
