"""Parsing tests for the Smartschool API client (no network)."""
from unittest.mock import MagicMock

import pytest

from custom_components.smartschool_be.smartschool_api import (
    SmartschoolAPI,
    SmartschoolError,
    html_to_text,
    text_to_html,
)

# Synthetic answers in the shape of Smartschool's XML dispatcher.
LIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<server><response><status>ok</status><actions><action><data><messages>
<message><id>11</id><from>Juf An</from><subject>Uitstap</subject><date>2026-10-17 09:00</date>
<unread>1</unread><attachment>2</attachment><label>3</label><allowreply>1</allowreply></message>
<message><id>10</id><from>Meester Tom</from><subject>Turnen</subject><date>2026-10-16 09:00</date>
<unread>0</unread><attachment>0</attachment><label>0</label><allowreply>0</allowreply></message>
</messages></data></action></actions></response></server>"""

SHOW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<server><response><status>ok</status><actions><action><data><message>
<id>11</id><from>Juf An</from><subject>Uitstap</subject><date>2026-10-17 09:00</date>
<body>&lt;p&gt;Beste ouders,&lt;br&gt;tot morgen&lt;/p&gt;</body><attachment>0</attachment>
<receivers><to>Ouders 3A</to></receivers><ccreceivers/><canReply>1</canReply>
</message></data></action></actions></response></server>"""

ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<server><response><status>error</status><message>Geen toegang</message></response></server>"""

SENT_OK = "<html><script>window.opener.reload(); window.close();</script></html>"

REPLY_FORM = """<html><form>
<input type="hidden" name="uniqueUsc" value="usc1">
<input type="hidden" name="origMsgID" value="11">
<input type="hidden" name="composeAction" value="2">
<input type="hidden" name="randomDir" value="rd">
<input type="text" name="subject" value="Re: Uitstap">
</form></html>"""


def _resp(text, url="https://s.smartschool.be/?module=Messages", ctype="text/xml", status=200):
    r = MagicMock()
    r.text = text
    r.url = url
    r.status_code = status
    r.headers = {"Content-Type": ctype}
    r.raise_for_status = MagicMock()
    return r


@pytest.fixture
def api():
    client = SmartschoolAPI("https://s.smartschool.be", "jan", "pw", "2010-01-01")
    client._is_logged_in = True
    client._session = MagicMock()
    return client


def test_get_messages(api):
    api._session.post.return_value = _resp(LIST_XML)
    messages = api.get_messages()
    assert [m["id"] for m in messages] == [11, 10]
    assert messages[0] == {
        "id": 11, "from": "Juf An", "subject": "Uitstap", "date": "2026-10-17 09:00",
        "unread": True, "attachments": 2, "flag": "red", "can_reply": True,
    }
    assert messages[1]["unread"] is False
    command = api._session.post.call_args.kwargs["data"]["command"]
    assert "<action>message list</action>" in command


def test_get_message_text(api):
    api._session.post.return_value = _resp(SHOW_XML)
    msg = api.get_message(11)
    assert msg["text"] == "Beste ouders,\ntot morgen"
    assert msg["to"] == ["Ouders 3A"]
    assert msg["cc"] == []
    assert msg["can_reply"] is True


def test_error_answer(api):
    api._session.post.return_value = _resp(ERROR_XML)
    with pytest.raises(SmartschoolError, match="Geen toegang"):
        api.get_messages()


def test_expired_session_relogs_once(api):
    login_page = _resp("<html>login</html>", url="https://s.smartschool.be/login", ctype="text/html")
    api._session.post.side_effect = [login_page, _resp(LIST_XML)]
    api._force_relogin = MagicMock()
    assert len(api.get_messages()) == 2
    api._force_relogin.assert_called_once()


def test_reply_message(api):
    api._session.get.return_value = _resp(REPLY_FORM, ctype="text/html")
    api._session.post.return_value = _resp(SENT_OK, ctype="text/html")
    api.reply_message(11, "Bedankt!\nTot dan")

    url = api._session.post.call_args.args[0]
    assert "composeType=1" in url and "msgID=11" in url
    files = api._session.post.call_args.kwargs["files"]
    assert files["subject"] == (None, "Re: Uitstap")
    assert files["message"] == (None, "<p>Bedankt!<br>Tot dan</p>")
    assert files["origMsgID"] == (None, "11")
    assert files["uniqueUsc"] == (None, "usc1")


def test_reply_unconfirmed_raises(api):
    api._session.get.return_value = _resp(REPLY_FORM, ctype="text/html")
    api._session.post.return_value = _resp("<html>var error = 'x'</html>", ctype="text/html")
    with pytest.raises(SmartschoolError, match="niet"):
        api.reply_message(11, "Hallo")


def test_reply_wrong_form_sends_nothing(api):
    api._session.get.return_value = _resp(REPLY_FORM.replace('value="11"', 'value="0"'), ctype="text/html")
    with pytest.raises(SmartschoolError):
        api.reply_message(11, "Hallo")
    api._session.post.assert_not_called()


def test_send_message_ambiguous_recipient(api):
    form = REPLY_FORM.replace('value="11"', 'value="0"')
    api._session.get.return_value = _resp(form, ctype="text/html")
    api._session.post.return_value = _resp(
        "<results><users>"
        "<user><userID>1</userID><value>An Peeters</value><ssID>9</ssID><userLT>0</userLT></user>"
        "<user><userID>2</userID><value>Tom Peeters</value><ssID>9</ssID><userLT>0</userLT></user>"
        "</users></results>"
    )
    with pytest.raises(SmartschoolError, match="Meerdere"):
        api.send_message("Peeters", "Vraag", "Hallo")
    # Only the search was posted; nothing was sent.
    assert api._session.post.call_count == 1


def test_future_tasks_double_encoded_json(api):
    payload = (
        '{"days":[{"date":"2026-10-20","courses":[{"course_title":"AAR1","hourID":"3",'
        '"items":{"tasks":[{"label":"Toets","description":"H3","course":"AAR1",'
        '"date":"2026-10-20","hourID":"3","warning":false}]}}]}]}'
    )
    r = _resp("", ctype="application/json")
    r.json.return_value = payload  # Smartschool answers with a JSON string
    api._session.request.return_value = r
    tasks = api.get_future_tasks()
    assert tasks == [{
        "date": "2026-10-20", "type": "Toets", "description": "H3", "course": "AAR1",
        "course_title": "AAR1", "hour_id": "3", "warning": False,
    }]


def test_text_html_roundtrip():
    assert text_to_html("a & b\nc\n\nd") == "<p>a &amp; b<br>c</p><p>d</p>"
    assert html_to_text("<p>a<br>b</p><p>c</p>") == "a\nb\n\nc"
