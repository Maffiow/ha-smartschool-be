"""More API client tests against a small fake Smartschool (no network)."""
from datetime import date

import pytest
import requests
from requests.cookies import RequestsCookieJar

from custom_components.smartschool_be.smartschool_api import (
    SmartschoolAPI,
    SmartschoolAuthError,
    SmartschoolBirthdayError,
    SmartschoolConnectionError,
    SmartschoolError,
    SmartschoolModuleUnavailable,
)

BASE = "https://s.smartschool.be"

LOGIN_FORM = """<html><form action="/login">
<input type="hidden" name="login_form[_token]" value="t">
<input name="login_form[_username]"><input name="login_form[_password]" type="password">
</form></html>"""

VERIFY_FORM = """<html><form name="account_verification_form" action="/account-verification">
<input type="hidden" name="account_verification_form[_token]" value="v">
<input name="account_verification_form[_security_question_answer]" placeholder="JJJJ-MM-DD">
</form></html>"""


def xml(body: str) -> str:
    return f'<?xml version="1.0"?><server><response><status>ok</status><actions><action><data>{body}</data></action></actions></response></server>'


class Resp:
    def __init__(self, path, text="", ctype="text/html", status=200, json_data=None):
        self.url = BASE + path
        self.text = text
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))


class FakeSmartschool:
    """Routes GET/POST by URL substring; each route is a Resp or a list (served in order)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.headers = {}
        self.cookies = RequestsCookieJar()

    def _route(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (m, needle), resp in self.routes.items():
            if m == method and needle in url:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return Resp("/", "<html>home</html>")

    def get(self, url, **kwargs):
        return self._route("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._route("POST", url, **kwargs)

    def request(self, method, url, **kwargs):
        return self._route(method.upper(), url, **kwargs)

    def close(self):
        pass

    def commands(self):
        """The XML dispatcher actions that were posted."""
        out = []
        for method, _url, kwargs in self.calls:
            data = kwargs.get("data")
            if method == "POST" and isinstance(data, dict) and "command" in data:
                out.append(data["command"].split("<action>")[1].split("</action>")[0])
        return out


def api_with(routes, birthday="2010-03-15", logged_in=True):
    api = SmartschoolAPI(BASE, "jan", "pw", birthday)
    api._session = FakeSmartschool(routes)
    api._is_logged_in = logged_in
    api._force_relogin = lambda: None
    return api


# --- login ------------------------------------------------------------------

def test_login_with_birthday_verification():
    api = api_with({
        ("GET", "/login"): Resp("/login", LOGIN_FORM),
        ("POST", "/login"): Resp("/account-verification", VERIFY_FORM),
        ("POST", "/account-verification"): Resp("/", "<html>home</html>"),
    }, logged_in=False)
    api.login()
    assert api._is_logged_in
    posted = [k["data"] for m, u, k in api._session.calls if m == "POST" and "account-verification" in u][0]
    assert posted["account_verification_form[_security_question_answer]"] == "2010-03-15"
    assert posted["account_verification_form[_token]"] == "v"


def test_login_wrong_birthday():
    api = api_with({
        ("GET", "/login"): Resp("/login", LOGIN_FORM),
        ("POST", "/login"): Resp("/account-verification", VERIFY_FORM),
        ("POST", "/account-verification"): Resp("/account-verification", VERIFY_FORM),
    }, logged_in=False)
    with pytest.raises(SmartschoolBirthdayError):
        api.login()


def test_login_without_birthday_configured():
    api = api_with({
        ("GET", "/login"): Resp("/login", LOGIN_FORM),
        ("POST", "/login"): Resp("/account-verification", VERIFY_FORM),
    }, birthday="", logged_in=False)
    with pytest.raises(SmartschoolBirthdayError):
        api.login()


def test_login_wrong_password():
    api = api_with({
        ("GET", "/login"): Resp("/login", LOGIN_FORM),
        ("POST", "/login"): Resp("/login", "<p>Ongeldige gebruikersnaam of wachtwoord</p>"),
    }, logged_in=False)
    with pytest.raises(SmartschoolAuthError):
        api.login()


def test_login_back_on_login_page():
    api = api_with({
        ("GET", "/login"): Resp("/login", LOGIN_FORM),
        ("POST", "/login"): Resp("/login", LOGIN_FORM),
    }, logged_in=False)
    with pytest.raises(SmartschoolAuthError):
        api.login()


def test_login_network_error_and_missing_form():
    api = api_with({("GET", "/login"): requests.exceptions.ConnectionError("down")}, logged_in=False)
    with pytest.raises(SmartschoolConnectionError):
        api.login()
    api = api_with({("GET", "/login"): Resp("/login", "<html>maintenance</html>")}, logged_in=False)
    with pytest.raises(SmartschoolConnectionError):
        api.login()


# --- results and students -----------------------------------------------------

def _evaluation(name, course, value, desc, when, feedback=None):
    return {
        "name": name,
        "courses": [{"name": course}],
        "graphic": {"value": value, "description": desc},
        "date": when,
        "availabilityDate": when,
        "feedbacks": [{"text": feedback}] if feedback else [],
    }


def test_all_evaluations_grouped_per_course_over_pages():
    page1 = [_evaluation(f"T{i}", "Wiskunde", 70 + i % 10, "x", f"2026-09-{i % 28 + 1:02d}") for i in range(50)]
    page2 = [_evaluation("Laatste", "Frans", 90, "9/10", "2026-10-30", feedback="Goed zo")]
    api = api_with({
        ("GET", "pageNumber=1"): Resp("/results", ctype="application/json", json_data=page1),
        ("GET", "pageNumber=2"): Resp("/results", ctype="application/json", json_data={"items": page2}),
    })
    result = api.get_all_evaluations()
    assert set(result["by_course"]) == {"Wiskunde", "Frans"}
    assert len(result["by_course"]["Wiskunde"]["history"]) == 50
    assert result["latest"]["name"] == "Laatste"
    assert result["latest"]["feedback"] == "Goed zo"
    assert len(result["recent"]) == 5


def test_evaluations_module_off_and_empty():
    api = api_with({("GET", "evaluations"): Resp("/results", status=403)})
    with pytest.raises(SmartschoolModuleUnavailable):
        api.get_all_evaluations()
    api = api_with({("GET", "evaluations"): Resp("/results", ctype="application/json", json_data=[])})
    assert api.get_all_evaluations() is None
    api = api_with({("GET", "evaluations"): Resp("/results", "<html/>")})
    assert api.get_all_evaluations() is None


def test_students():
    students = [{"userID": 42, "fullName": "Jan Peeters", "class": "3A"}]
    api = api_with({
        ("GET", "/Studentcard"): Resp("/Studentcard", "<html/>"),
        ("POST", "getStudents"): Resp("/Studentcard", ctype="application/json", json_data=students),
    })
    assert api.get_students() == students
    api = api_with({
        ("GET", "/Studentcard"): Resp("/login", LOGIN_FORM),
    })
    assert api.get_students() == []


# --- message actions ------------------------------------------------------------

def test_mark_unread_flag_trash():
    api = api_with({("POST", "dispatcher"): Resp("/", xml("<message><id>5</id></message>"), ctype="text/xml")})
    api.mark_message_unread(5)
    api.set_message_flag(5, "green")
    api.trash_message(5)
    assert api._session.commands() == ["mark message unread", "save msglabel", "quick delete"]
    with pytest.raises(ValueError):
        api.set_message_flag(5, "purple")


def test_archive():
    api = api_with({("POST", "archivemessages"): Resp("/", ctype="application/json", json_data={"success": [5]})})
    api.archive_message(5)
    api = api_with({("POST", "archivemessages"): Resp("/", ctype="application/json", json_data={"success": []})})
    with pytest.raises(SmartschoolError):
        api.archive_message(5)


def test_message_with_attachments():
    show = xml("<message><id>5</id><from>Juf</from><subject>Brief</subject><body>&lt;p&gt;Hallo&lt;/p&gt;</body>"
               "<attachment>1</attachment><receivers><to>Ouders</to></receivers><canReply>1</canReply></message>")
    attachments = xml("<attachmentlist><attachment><fileID>9</fileID><name>brief.pdf</name>"
                      "<mime>application/pdf</mime><size>12 KB</size></attachment></attachmentlist>")
    api = api_with({("POST", "dispatcher"): [Resp("/", show, ctype="text/xml"), Resp("/", attachments, ctype="text/xml")]})
    msg = api.get_message(5)
    assert msg["text"] == "Hallo"
    assert msg["attachments"] == [{"name": "brief.pdf", "size": "12 KB", "mime": "application/pdf"}]


COMPOSE_FORM = """<html><form>
<input type="hidden" name="uniqueUsc" value="usc1">
<input type="hidden" name="origMsgID" value="0">
<input type="hidden" name="randomDir" value="rd">
</form></html>"""
SENT_OK = "<html><script>window.close();</script></html>"


def test_send_message_to_one_person():
    api = api_with({
        ("GET", "composeMessage"): Resp("/?module=Messages", COMPOSE_FORM),
        ("POST", "addUserToSelected"): Resp("/?module=Messages", "<users><user><realUserId>146</realUserId></user></users>"),
        ("POST", "searchUsers"): Resp("/?module=Messages", "<results><users><user><userID>146</userID>"
                                       "<value>An Peeters</value><ssID>9</ssID><userLT>0</userLT></user></users></results>"),
        ("POST", "composeMessage"): Resp("/?module=Messages", SENT_OK),
    })
    assert api.send_message("An Peeters", "Vraag", "Hallo") == "An Peeters"
    submit = [k for m, u, k in api._session.calls if m == "POST" and "composeMessage" in u][0]
    assert submit["files"]["subject"] == (None, "Vraag")
    assert submit["files"]["message"] == (None, "<p>Hallo</p>")


def test_send_message_nobody_found():
    api = api_with({
        ("GET", "composeMessage"): Resp("/?module=Messages", COMPOSE_FORM),
        ("POST", "searchUsers"): Resp("/?module=Messages", "<results><users/></results>"),
    })
    with pytest.raises(SmartschoolError, match="Geen ontvanger"):
        api.send_message("Niemand", "Vraag", "Hallo")


# --- agenda ------------------------------------------------------------------

def test_hours_and_lessons_in_chunks():
    hours = xml("<content><hours><hour><hourID>318</hourID><start>08:25</start><end>09:15</end><title>1</title></hour></hours></content>")
    lessons = xml("<content><lessons><lesson><momentID>1</momentID><date>2026-11-02</date><hourID>318</hourID>"
                  "<courseTitle>FR4</courseTitle><subject>Les</subject><classroomTitle>E.3</classroomTitle>"
                  "<teacher>Ghysen</teacher></lesson></lessons></content>")
    api = api_with({("POST", "dispatcher"): [Resp("/", hours, ctype="text/xml"), Resp("/", lessons, ctype="text/xml")]})
    assert api.get_hours() == {"318": {"start": "08:25", "end": "09:15", "title": "1"}}

    api = api_with({("POST", "dispatcher"): Resp("/", lessons, ctype="text/xml")})
    result = api.get_lessons(date(2026, 11, 1), date(2026, 12, 15))  # 45 days -> 3 requests
    assert len([c for c in api._session.commands() if c == "get lessons"]) == 3
    assert result[0] == {
        "moment_id": "1", "date": "2026-11-02", "hour_id": "318", "course": "FR4",
        "subject": "Les", "classroom": "E.3", "teacher": "Ghysen", "note": None,
    }


# --- session handling ---------------------------------------------------------

def test_force_relogin_starts_a_fresh_session():
    api = SmartschoolAPI(BASE, "jan", "pw", "2010-03-15")
    old = api._session = FakeSmartschool({})
    api._is_logged_in = True
    api._warmed_modules = {"Messages"}
    logins = []
    api.login = lambda: logins.append(True)
    api._force_relogin()
    assert api._session is not old
    assert api._warmed_modules == set() and logins == [True]
    api.close()


def test_import_skips_broken_cookies_and_restore_handles_network_errors():
    api = api_with({("GET", "/"): requests.exceptions.ConnectionError("down")}, logged_in=False)
    api.import_cookies([{"value": "no name"}, {"name": "PHPSESSID", "value": "abc", "domain": "s.smartschool.be"}])
    assert [c.name for c in api._session.cookies] == ["PHPSESSID"]
    with pytest.raises(SmartschoolConnectionError):
        api.restore_session()
    assert SmartschoolAPI(BASE, "jan", "pw").restore_session() is False  # no cookies at all


def test_evaluations_page_edge_cases():
    api = api_with({("GET", "evaluations"): Resp("/login", LOGIN_FORM, ctype="text/html")})
    assert api._fetch_evaluations_page() is None  # sent to the login page
    api = api_with({("GET", "evaluations"): Resp("/results", ctype="application/json", json_data=None)})
    assert api._fetch_evaluations_page() is None  # unreadable JSON
    api = api_with({("GET", "evaluations"): Resp("/results", ctype="application/json", json_data={"other": 1})})
    assert api._fetch_evaluations_page() is None  # no list inside
    no_course = [{"name": "T", "courses": [{}], "graphic": {}, "date": "2026-10-01"}]
    api = api_with({("GET", "evaluations"): Resp("/results", ctype="application/json", json_data=no_course)})
    assert api.get_all_evaluations() is None  # nothing to group by course
