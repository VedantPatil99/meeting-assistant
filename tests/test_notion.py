"""Notion sync against a simulated Notion API (httpx.MockTransport), so no account is needed."""

import json
import re

import httpx
import pytest

from meeting_assistant.automations import Action, ActionType, Engine, Mode, Rule, Store, Trigger, events_from_report
from meeting_assistant.models import (
    ActionItem,
    ConflictResult,
    ConflictVerdict,
    CoverageResult,
    CoverageStatus,
    MeetingReport,
    Point,
    Summary,
)
from meeting_assistant.anchors import parse_anchors
from meeting_assistant.notion import NotionClient, NotionError, NotionSync
from meeting_assistant.notion.connection import NotionConnection

TOKEN = "secret_test"


def plain(rich_text):
    return "".join(t["text"]["content"] for t in rich_text)


class FakeNotion:
    """Just enough of the Notion API for this app, with real-looking responses."""

    def __init__(self):
        self.databases: dict[str, dict] = {}
        self.pages: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_next: list[int] = []
        self.shared_pages = [{"object": "page", "id": "parent-1",
                              "properties": {"title": {"type": "title", "title": [{"plain_text": "Work"}]}}}]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"message": "API token is invalid."})
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), headers={"Retry-After": "0"}, json={"message": "slow down"})
        body = json.loads(request.content) if request.content else {}
        path = request.url.path.removeprefix("/v1")

        if path == "/users/me":
            return httpx.Response(200, json={"object": "user", "name": "Meeting Assistant"})
        if path == "/search":
            return httpx.Response(200, json={"results": self.shared_pages})
        if path == "/databases" and request.method == "POST":
            db_id = f"db-{len(self.databases) + 1}"
            self.databases[db_id] = {"id": db_id, "title": plain(body["title"]), "properties": body["properties"]}
            return httpx.Response(200, json=self.databases[db_id])
        if m := re.fullmatch(r"/databases/([\w-]+)", path):
            db = self.databases.get(m.group(1))
            return httpx.Response(200, json=db) if db else httpx.Response(404, json={"message": "Could not find database"})
        if m := re.fullmatch(r"/databases/([\w-]+)/query", path):
            rows = [p for p in self.pages.values() if p["parent"]["database_id"] == m.group(1)]
            return httpx.Response(200, json={"results": [p for p in rows if self.matches(p, body.get("filter"))]})
        if path == "/pages":
            page_id = f"page-{len(self.pages) + 1}"
            page = {"id": page_id, "url": f"https://notion.so/{page_id}", "parent": body["parent"],
                    "properties": body["properties"], "children": list(body.get("children", []))}
            self.pages[page_id] = page
            return httpx.Response(200, json=page)
        if m := re.fullmatch(r"/blocks/([\w-]+)/children", path):
            self.pages[m.group(1)]["children"] += body["children"]
            return httpx.Response(200, json={"results": body["children"]})
        return httpx.Response(400, json={"message": f"unexpected {request.method} {path}"})

    def matches(self, page: dict, filt: dict | None) -> bool:
        if not filt:
            return True
        if "and" in filt:
            return all(self.matches(page, f) for f in filt["and"])
        prop = page["properties"].get(filt["property"], {})
        if "title" in filt:
            return plain(prop.get("title", [])) == filt["title"]["equals"]
        if "date" in filt:
            return prop.get("date", {}).get("start") == filt["date"]["equals"]
        if "relation" in filt:
            return {"id": filt["relation"]["contains"]} in prop.get("relation", [])
        raise AssertionError(f"filter not simulated: {filt}")

    def rows(self, db_id):
        return [p for p in self.pages.values() if p["parent"]["database_id"] == db_id]


@pytest.fixture
def notion():
    return FakeNotion()


def client(notion, token=TOKEN):
    return NotionClient(token, transport=httpx.MockTransport(notion.handler), min_interval=0)


@pytest.fixture
def report():
    return MeetingReport(
        title="Dashboard pilot feedback",
        meeting_type="client feedback",
        anchors=parse_anchors("! go-live 15 December\nfr: export to Excel"),
        summary=Summary(
            decisions=[Point(text="Go-live on 20 December", timestamp="00:02:24")],
            action_items=[ActionItem(task="Send licence breakdown", owner="Vedant", due="Friday"),
                          ActionItem(task="Investigate Monday slowness", owner="Ankit")],
        ),
        coverage=[CoverageResult(anchor_id=1, status=CoverageStatus.CONFLICT),
                  CoverageResult(anchor_id=2, status=CoverageStatus.COVERED, evidence="x")],
        conflicts=[ConflictResult(anchor_id=1, verdict=ConflictVerdict.CONTRADICTS,
                                  explanation="The meeting agreed 20 December",
                                  transcript_quote="Agreed, go-live moves to 20 December.", timestamp="00:02:24")],
    )


def setup_sync(notion):
    c = client(notion)
    meetings, tasks = NotionSync.setup(c, "parent-1")
    return NotionSync(c, meetings, tasks)


def test_setup_creates_linked_databases(notion):
    sync = setup_sync(notion)
    assert notion.databases[sync.meetings_db]["title"] == "Meetings"
    tasks = notion.databases[sync.tasks_db]
    assert tasks["properties"]["Meeting"]["relation"]["database_id"] == sync.meetings_db


def test_meeting_page_has_properties_and_report_body(notion, report):
    sync = setup_sync(notion)
    url = sync.save_meeting(report, "2026-10-28T15:00:00", ["rahul@client.com"])
    [page] = notion.rows(sync.meetings_db)
    assert url == page["url"]
    props = page["properties"]
    assert plain(props["Name"]["title"]) == "Dashboard pilot feedback"
    assert props["Date"]["date"]["start"] == "2026-10-28"
    assert props["Type"]["select"]["name"] == "client feedback"
    assert (props["Action items"]["number"], props["Conflicts"]["number"]) == (2, 1)
    assert props["Covered without repair"]["number"] == 1.0
    kinds = [b["type"] for b in page["children"]]
    assert "to_do" in kinds and "callout" in kinds
    texts = [plain(b[b["type"]]["rich_text"]) for b in page["children"]]
    assert "Go-live on 20 December [00:02:24]" in texts
    assert "Send licence breakdown (Vedant, due Friday)" in texts


def test_saving_the_same_meeting_twice_does_not_duplicate(notion, report):
    sync = setup_sync(notion)
    first = sync.save_meeting(report, "2026-10-28T15:00:00", [])
    second = sync.save_meeting(report, "2026-10-28T15:00:00", [])
    assert first == second and len(notion.rows(sync.meetings_db)) == 1


def test_tasks_link_to_meeting_and_skip_duplicates(notion, report):
    sync = setup_sync(notion)
    sync.save_meeting(report, "2026-10-28T15:00:00", [])
    [meeting] = notion.rows(sync.meetings_db)
    assert sync.save_task("Send licence breakdown", "Vedant", "Friday", report.title, "2026-10-28T15:00:00")
    assert sync.save_task("Send licence breakdown", "Vedant", "Friday", report.title, "2026-10-28T15:00:00") is None
    [task] = notion.rows(sync.tasks_db)
    assert task["properties"]["Meeting"]["relation"] == [{"id": meeting["id"]}]
    assert task["properties"]["Status"]["select"]["name"] == "To do"


def test_long_pages_are_sent_in_batches_of_100(notion):
    c = client(notion)
    db = c.create_database("parent-1", "Meetings", {"Name": {"title": {}}})
    blocks = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}] * 250
    page = c.create_page(db, {"Name": {"title": []}}, blocks)
    assert len(notion.pages[page["id"]]["children"]) == 250
    assert notion.calls.count(("PATCH", f"/v1/blocks/{page['id']}/children")) == 2


def test_rate_limit_is_retried(notion):
    notion.fail_next = [429, 503]
    assert client(notion).me()["name"] == "Meeting Assistant"


def test_bad_token_gives_a_clear_error(notion):
    with pytest.raises(NotionError, match="rejected the token"):
        client(notion, token="wrong").me()


class FakeSecrets:
    def __init__(self):
        self.values = {}

    def get_secret(self, name):
        return self.values.get(name)

    def set_secret(self, name, value):
        self.values[name] = value

    def delete_secret(self, name):
        self.values.pop(name, None)


def test_connection_flow(notion):
    store, secrets = Store(":memory:"), FakeSecrets()
    conn = NotionConnection(store, client_factory=lambda t: client(notion, t), secrets=secrets)
    assert conn.sync() is None and conn.status() == "Not connected."

    with pytest.raises(NotionError):
        conn.connect("wrong")
    assert secrets.values == {}  # a bad token is never saved

    assert conn.connect(TOKEN) == "Meeting Assistant"
    assert conn.pages() == [("parent-1", "Work")]
    conn.create_databases("parent-1", "Work")
    assert conn.sync() is not None and "Work" in conn.status()
    assert store.get_pref("notion_meetings_db") and TOKEN not in str(store.db.execute("SELECT * FROM prefs").fetchall())

    conn.disconnect()
    assert conn.sync() is None and secrets.values == {}


def test_notion_rule_runs_through_the_engine(notion, report, tmp_path):
    store = Store(":memory:")
    sync = setup_sync(notion)
    rule = Rule(name="Save meetings to Notion", trigger=Trigger.MEETING_PROCESSED,
                actions=[Action(type=ActionType.NOTION_PAGE), Action(type=ActionType.NOTION_TASKS)], mode=Mode.AUTO)
    store.save_rule(rule)
    events = events_from_report(report, "2026-10-28T15:00:00", [], tmp_path)

    outcomes = Engine(store, tmp_path, notion=lambda: sync).handle(events)
    assert [o.status for o in outcomes] == ["done"]
    assert len(notion.rows(sync.meetings_db)) == 1 and len(notion.rows(sync.tasks_db)) == 2

    Engine(store, tmp_path, notion=lambda: sync).handle(events)  # processing again adds nothing
    assert len(notion.rows(sync.meetings_db)) == 1 and len(notion.rows(sync.tasks_db)) == 2


def test_notion_rule_fails_cleanly_when_not_connected(report, tmp_path):
    store = Store(":memory:")
    store.save_rule(Rule(name="To Notion", trigger=Trigger.MEETING_PROCESSED,
                         actions=[Action(type=ActionType.NOTION_PAGE)], mode=Mode.AUTO))
    [outcome] = Engine(store, tmp_path).handle(events_from_report(report, folder=tmp_path))
    assert outcome.status == "failed"
    assert "isn't connected" in store.runs()[0]["detail"]


def test_approval_stays_pending_if_notion_fails(report, tmp_path):
    store = Store(":memory:")
    store.save_rule(Rule(name="To Notion", trigger=Trigger.MEETING_PROCESSED,
                         actions=[Action(type=ActionType.NOTION_PAGE)], mode=Mode.REVIEW))
    engine = Engine(store, tmp_path)
    engine.handle(events_from_report(report, folder=tmp_path))
    pending_id = store.pending()[0]["id"]
    with pytest.raises(RuntimeError):
        engine.approve(pending_id)
    assert len(store.pending()) == 1
