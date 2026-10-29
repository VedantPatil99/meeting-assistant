"""Tasks scheduled from meetings, follow-up drafts, and the Gmail connector (against a fake Google)."""

import base64
import json
import threading
from datetime import date
from email import message_from_bytes
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from meeting_assistant.after_meeting import push_to_gmail, save_suggestions, send
from meeting_assistant.anchors import parse_anchors
from meeting_assistant.automations import Prefs, Store
from meeting_assistant.gmail import GmailClient, GmailConnection, GmailError, Tokens
from meeting_assistant.gmail import oauth
from meeting_assistant.models import (
    ActionItem,
    CoverageResult,
    CoverageStatus,
    MeetingReport,
    Point,
    Summary,
)
from meeting_assistant.pipeline.followups import (
    EmailDraft,
    Recipient,
    RecipientList,
    leaked_note,
    match_address,
    suggest_followups,
)
from meeting_assistant.scheduling import describe_due, resolve_due

from .conftest import FakeProvider
from .test_notion import FakeSecrets

MEETING_DAY = "2026-10-28T15:00:00"  # a Wednesday


@pytest.mark.parametrize("said, expected", [
    ("Friday", date(2026, 10, 30)),
    ("by Friday", date(2026, 10, 30)),
    ("next Friday", date(2026, 11, 6)),
    ("Wednesday", date(2026, 11, 4)),  # said on a Wednesday: next week's
    ("tomorrow", date(2026, 10, 29)),
    ("day after tomorrow", date(2026, 10, 30)),
    ("EOD", date(2026, 10, 28)),
    ("end of the week", date(2026, 10, 30)),
    ("end of next week", date(2026, 11, 6)),
    ("end of month", date(2026, 10, 31)),
    ("next week", date(2026, 11, 2)),
    ("in 2 weeks", date(2026, 11, 11)),
    ("in three days", date(2026, 10, 31)),
    ("15 December", date(2026, 12, 15)),
    ("by Dec 15th", date(2026, 12, 15)),
    ("15/12", date(2026, 12, 15)),
    ("10 January", date(2027, 1, 10)),
    ("2026-11-20", date(2026, 11, 20)),
    ("sometime soon", None),
    (None, None),
])
def test_spoken_due_dates_become_calendar_dates(said, expected):
    assert resolve_due(said, MEETING_DAY) == expected


def test_due_labels():
    today = date(2026, 10, 28)
    assert describe_due(date(2026, 10, 26), today) == "Overdue 2 days"
    assert describe_due(today, today) == "Today"
    assert describe_due(date(2026, 10, 29), today) == "Tomorrow"
    assert describe_due(date(2026, 11, 6), today) == "Fri 6 Nov"
    assert describe_due(None, today) == "No date"


@pytest.fixture
def report():
    return MeetingReport(
        title="Dashboard pilot feedback",
        meeting_type="client feedback",
        anchors=parse_anchors("internal: client seems unhappy with pricing\n! go-live 20 December"),
        summary=Summary(
            decisions=[Point(text="Go-live on 20 December")],
            action_items=[ActionItem(task="Send licence breakdown", owner="Vedant", due="Friday"),
                          ActionItem(task="Investigate Monday slowness", owner="Ankit")],
            key_points=[Point(text="Client seems unhappy with pricing")],
        ),
        coverage=[CoverageResult(anchor_id=1, status=CoverageStatus.COVERED,
                                 evidence="Client seems unhappy with pricing"),
                  CoverageResult(anchor_id=2, status=CoverageStatus.COVERED, evidence="Go-live on 20 December")],
        conflicts=[],
    )


# tasks

def test_action_items_become_suggested_tasks_with_dates(report):
    store = Store(":memory:")
    assert save_suggestions(store, report, MEETING_DAY, None, []) == (2, 0)
    tasks = {t["text"]: t for t in store.tasks("suggested")}
    assert tasks["Send licence breakdown"]["due_date"] == "2026-10-30"
    assert tasks["Investigate Monday slowness"]["due_date"] is None
    # processing the same meeting again doesn't duplicate
    assert save_suggestions(store, report, MEETING_DAY, None, []) == (0, 0)


def test_scheduling_a_suggestion_and_reminders(report):
    store = Store(":memory:")
    save_suggestions(store, report, MEETING_DAY, None, [])
    task = next(t for t in store.tasks() if t["text"] == "Send licence breakdown")
    assert store.due_for_reminder("2026-10-30") == []  # suggestions don't remind
    # an automation creating the same task schedules the suggestion instead of duplicating it
    assert store.add_task("Send licence breakdown", "Vedant", "Friday", report.title) == task["id"]
    assert [t["id"] for t in store.due_for_reminder("2026-10-30")] == [task["id"]]
    store.update_task(task["id"], reminded_on="2026-10-30")
    assert store.due_for_reminder("2026-10-30") == []
    store.update_task(task["id"], status="done")
    assert store.due_for_reminder("2026-10-31") == []
    with pytest.raises(ValueError):
        store.update_task(task["id"], meeting="something else")


def test_old_databases_get_the_new_task_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, text TEXT, owner TEXT, due TEXT, meeting TEXT, "
               "created_at TEXT, status TEXT DEFAULT 'open')")
    db.execute("INSERT INTO tasks (text, owner, due, meeting) VALUES ('old task', '', '', 'm')")
    db.commit()
    db.close()
    store = Store(path)
    [row] = store.tasks()
    assert row["text"] == "old task" and row["due_date"] is None


# follow-up drafts

def test_names_are_matched_to_addresses():
    attendees = ["rahul.mehta@client.com", "priya@client.com", "ankit@us.com"]
    assert match_address("Rahul", attendees, {}) == "rahul.mehta@client.com"
    assert match_address("Priya Shah", attendees, {}) == "priya@client.com"
    assert match_address("the finance team", attendees, {}) is None
    assert match_address("Neha", attendees, {"Neha Rao": "neha@client.com"}) == "neha@client.com"


def test_leak_check():
    note = "client seems unhappy with pricing"
    assert leaked_note("We know you seem unhappy with pricing.", [note]) == note
    assert leaked_note("Attached is the licence breakdown we promised.", [note]) is None


def followup_provider(body_for_external: str = "Hi Rahul, go-live is on 20 December. Best regards,"):
    def draft(prompt):
        return EmailDraft(subject="Follow-up: dashboard pilot",
                          body=body_for_external if "outside the company" in prompt else "Ankit, please look into Monday.")
    return FakeProvider({
        RecipientList: lambda p: RecipientList(recipients=[
            Recipient(name="Rahul", audience="internal", reason="He asked for the licence breakdown"),
            Recipient(name="Ankit", audience="internal", reason="Owns the Monday investigation"),
            Recipient(name="Rahul", audience="external", reason="duplicate"),
        ]),
        EmailDraft: draft,
    })


def test_followups_pick_people_and_keep_internal_notes_out(report):
    provider = followup_provider()
    prefs = Prefs(my_email="vedant@us.com", company_domain="us.com")
    emails = suggest_followups(provider, report, ["rahul@client.com", "ankit@us.com"], prefs)

    assert [(e.recipient, e.to, e.audience) for e in emails] == [
        ("Rahul", ["rahul@client.com"], "external"),  # the address decides, not the model's guess
        ("Ankit", ["ankit@us.com"], "internal"),
    ]
    assert not emails[0].warning
    external_prompt = next(p for name, p in provider.calls if name == "EmailDraft" and "outside" in p)
    internal_prompt = next(p for name, p in provider.calls if name == "EmailDraft" and "colleague" in p)
    assert "unhappy with pricing" not in external_prompt.lower()
    assert "unhappy with pricing" in internal_prompt.lower()


def test_a_draft_that_repeats_an_internal_note_is_flagged(report):
    provider = followup_provider("Hi Rahul, we understand you seem unhappy with pricing. Best regards,")
    emails = suggest_followups(provider, report, ["rahul@client.com", "ankit@us.com"],
                               Prefs(company_domain="us.com"))
    assert "unhappy with pricing" in emails[0].warning


# Gmail

class FakeGoogle:
    """Google's token endpoint and the Gmail drafts/send API, in memory."""

    def __init__(self):
        self.drafts: dict[str, dict] = {}
        self.sent: list[dict] = []
        self.token_requests: list[dict] = []
        self.valid_access = {"access-1"}
        self.refresh_count = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == oauth.TOKEN_URL:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if form["client_secret"] != "shh":
                return httpx.Response(401, json={"error": "invalid_client"})
            if form["grant_type"] == "authorization_code":
                return httpx.Response(200, json={"access_token": "access-1", "expires_in": 3600,
                                                 "refresh_token": "refresh-1"})
            if form["refresh_token"] != "refresh-1":
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.refresh_count += 1
            token = f"access-{self.refresh_count + 1}"
            self.valid_access.add(token)
            return httpx.Response(200, json={"access_token": token, "expires_in": 3600})
        if url == oauth.REVOKE_URL:
            return httpx.Response(200)

        if request.headers.get("Authorization", "").removeprefix("Bearer ") not in self.valid_access:
            return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})
        path = request.url.path.removeprefix("/gmail/v1/users/me")
        body = json.loads(request.content) if request.content else {}
        if path == "/profile":
            return httpx.Response(200, json={"emailAddress": "vedant@gmail.com"})
        if path == "/drafts" and request.method == "POST":
            draft_id = f"r{len(self.drafts) + 1}"
            self.drafts[draft_id] = self.decode(body["message"]["raw"])
            return httpx.Response(200, json={"id": draft_id})
        if path.startswith("/drafts/") and request.method == "PUT":
            draft_id = path.rsplit("/", 1)[1]
            self.drafts[draft_id] = self.decode(body["message"]["raw"])
            return httpx.Response(200, json={"id": draft_id})
        if path == "/drafts/send":
            self.sent.append(self.drafts.pop(body["id"]))
            return httpx.Response(200, json={"id": "m1"})
        if path == "/messages/send":
            self.sent.append(self.decode(body["raw"]))
            return httpx.Response(200, json={"id": "m2"})
        return httpx.Response(404, json={"error": {"message": "not found"}})

    @staticmethod
    def decode(raw: str) -> dict:
        msg = message_from_bytes(base64.urlsafe_b64decode(raw))
        return {"to": msg["To"], "subject": msg["Subject"], "body": msg.get_payload(decode=True).decode().strip()}


@pytest.fixture
def google():
    return FakeGoogle()


def fake_browser(code="the-code", error=None):
    """Plays the user approving access: follows the consent URL straight to the redirect."""
    def open_browser(url):
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert query["code_challenge_method"] == "S256" and query["access_type"] == "offline"
        params = {"error": error} if error else {"code": code, "state": query["state"]}
        threading.Thread(target=lambda: httpx.get(query["redirect_uri"], params=params), daemon=True).start()
    return open_browser


def test_sign_in_exchanges_the_code_with_pkce(google):
    http = httpx.Client(transport=httpx.MockTransport(google.handler))
    tokens = oauth.sign_in("id.apps.googleusercontent.com", "shh", http=http, open_browser=fake_browser())
    assert tokens.refresh_token == "refresh-1"
    [exchange] = google.token_requests
    assert exchange["code"] == "the-code" and len(exchange["code_verifier"]) >= 43
    assert exchange["redirect_uri"].startswith("http://127.0.0.1:")


def test_cancelled_sign_in(google):
    http = httpx.Client(transport=httpx.MockTransport(google.handler))
    with pytest.raises(oauth.OAuthError, match="cancelled"):
        oauth.sign_in("id", "shh", http=http, open_browser=fake_browser(error="access_denied"))


def gmail_connection(google, store, secrets):
    transport = httpx.MockTransport(google.handler)
    http = httpx.Client(transport=transport)
    sign_in = lambda cid, secret, http: oauth.sign_in(cid, secret, http=http, open_browser=fake_browser())
    return GmailConnection(store, secrets=secrets, http=http, sign_in=sign_in, transport=transport)


def test_gmail_connection_flow(google):
    store, secrets = Store(":memory:"), FakeSecrets()
    conn = gmail_connection(google, store, secrets)
    assert conn.status() == "Not connected."
    with pytest.raises(GmailError, match="client ID"):
        conn.save_client("not-an-id", "shh")
    conn.save_client("123.apps.googleusercontent.com", "shh")
    assert conn.connect() == "vedant@gmail.com"
    assert "vedant@gmail.com" in conn.status()
    assert secrets.values["gmail_refresh_token"] == "refresh-1"
    assert "refresh-1" not in str(store.db.execute("SELECT * FROM prefs").fetchall())

    conn.disconnect()
    assert not conn.is_connected and conn.has_client
    with pytest.raises(GmailError, match="isn't connected"):
        conn.client()


def test_expired_access_token_is_refreshed(google):
    calls = []

    def get_tokens():
        calls.append(1)
        return oauth.refresh(httpx.Client(transport=httpx.MockTransport(google.handler)),
                             "id", "shh", "refresh-1")

    stale = Tokens("revoked-token", expires_at=9e12)
    client = GmailClient(get_tokens, tokens=stale, transport=httpx.MockTransport(google.handler))
    assert client.email_address() == "vedant@gmail.com"
    assert len(calls) == 1


def test_revoked_refresh_token_gives_a_clear_error(google):
    http = httpx.Client(transport=httpx.MockTransport(google.handler))
    client = GmailClient(lambda: oauth.refresh(http, "id", "shh", "revoked"),
                         transport=httpx.MockTransport(google.handler))
    with pytest.raises(GmailError, match="Connect Gmail again"):
        client.email_address()


def test_draft_goes_to_gmail_then_is_sent(google, report):
    store = Store(":memory:")
    conn = gmail_connection(google, store, FakeSecrets())
    conn.save_client("123.apps.googleusercontent.com", "shh")
    conn.connect()
    draft_id = store.add_draft(report.title, "", "Rahul", [], "asked for it", "Follow-up", "Hello Rahul", "external")

    with pytest.raises(ValueError, match="recipient"):
        send(store, conn.client(), draft_id)

    store.update_draft(draft_id, to_addrs=["rahul@client.com"], body="Hello Rahul, edited")
    gmail_id = push_to_gmail(store, conn.client(), draft_id)
    assert google.drafts[gmail_id] == {"to": "rahul@client.com", "subject": "Follow-up", "body": "Hello Rahul, edited"}
    assert store.draft(draft_id)["status"] == "in gmail"
    assert store.contacts() == {"Rahul": "rahul@client.com"}  # remembered for next time

    store.update_draft(draft_id, subject="Follow-up: pilot")
    send(store, conn.client(), draft_id)
    assert google.sent[0]["subject"] == "Follow-up: pilot" and google.drafts == {}
    assert store.draft(draft_id)["status"] == "sent" and store.drafts() == []


def test_sending_without_a_gmail_draft(google):
    store = Store(":memory:")
    conn = gmail_connection(google, store, FakeSecrets())
    conn.save_client("123.apps.googleusercontent.com", "shh")
    conn.connect()
    draft_id = store.add_draft("m", "", "Ankit", ["ankit@us.com"], "", "Monday", "Please check", "internal")
    send(store, conn.client(), draft_id)
    assert google.sent == [{"to": "ankit@us.com", "subject": "Monday", "body": "Please check"}]


def test_placeholders_are_removed():
    from meeting_assistant.pipeline.followups import remove_placeholders
    text = "Hello Ankit,\n\nFollowing our meeting on [insert date], here are the points.\n\nBest regards,\n[Your Name]"
    assert remove_placeholders(text) == "Hello Ankit,\n\nFollowing our meeting on, here are the points.\n\nBest regards,"
