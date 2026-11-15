"""The feature-request tracker: finding requests, matching them across meetings, storing and syncing."""

from meeting_assistant.after_meeting import client_names, save_features
from meeting_assistant.anchors import parse_anchors
from meeting_assistant.automations import Prefs, Store
from meeting_assistant.models import FeatureRequest, MeetingReport, Segment, Summary, Transcript
from meeting_assistant.notion.sync import NotionSync
from meeting_assistant.pipeline.features import (
    FoundRequest,
    FoundRequests,
    MatchChoice,
    dedupe,
    find_feature_requests,
    link_to_tracker,
)
from meeting_assistant.report import render_markdown
from meeting_assistant.textmatch import similarity

from .conftest import FakeProvider
from .test_notion import client, notion  # noqa: F401  (fixture)

TRANSCRIPT = Transcript(segments=[
    Segment(start=60, speaker="Rahul", text="We'd love to export the reports straight to Excel."),
    Segment(start=200, speaker="Rahul", text="The dashboard is slow on Monday mornings."),
])


def found(*items):
    return lambda prompt: FoundRequests(requests=[FoundRequest(**i) for i in items])


def test_similarity_ignores_small_words_and_plurals():
    assert similarity("Export reports to Excel", "export the report straight to Excel") == 1.0
    assert similarity("Export reports to Excel", "Alerts when a trade fails") == 0.0
    assert similarity("", "anything") == 0.0


def test_requests_found_twice_are_merged():
    merged = dedupe([FeatureRequest(title="Export reports to Excel"),
                     FeatureRequest(title="Excel export of reports", requested_by="Rahul", quote="q")])
    assert len(merged) == 1
    assert merged[0].requested_by == "Rahul" and merged[0].quote == "q"


def test_find_marks_flagged_request_and_adds_missed_note():
    provider = FakeProvider({FoundRequests: found(
        {"quote": "We'd love to export the reports straight to Excel.", "timestamp": "00:01:00",
         "requested_by": "Rahul", "title": "Export reports to Excel", "detail": "For month-end packs"})})
    anchors = parse_anchors("fr: excel export of reports\nfr: alerts when a trade fails to book")
    requests = find_feature_requests(provider, TRANSCRIPT, anchors)

    assert [r.title for r in requests] == ["Export reports to Excel", "Alerts when a trade fails to book"]
    assert all(r.flagged for r in requests)
    assert requests[0].requested_by == "Rahul"
    # the second came only from a note; who asked and when are filled from the transcript where possible
    assert requests[1].requested_by is None
    assert "alerts when a trade fails" in provider.calls[0][1]  # the notes are shown to the model


def test_shortened_quote_still_finds_speaker_and_time():
    provider = FakeProvider({FoundRequests: found(
        {"quote": "export the reports straight to Excel", "title": "Export reports to Excel"})})
    [request] = find_feature_requests(provider, TRANSCRIPT, [])
    assert (request.requested_by, request.timestamp) == ("Rahul", "00:01:00")


def test_find_with_nothing_asked():
    provider = FakeProvider({FoundRequests: found()})
    assert find_feature_requests(provider, TRANSCRIPT, []) == []


TRACKED = [(1, "Export reports to Excel", "Month-end packs"), (2, "Single sign-on", "")]


def test_clear_repeat_links_without_the_model():
    provider = FakeProvider({})
    links = link_to_tracker(provider, [FeatureRequest(title="Excel export for reports")], TRACKED)
    assert links == [1]
    assert provider.calls == []


def test_contained_title_is_not_a_sure_match():
    # every word of "Export reports as PDF" is in the new request, but it's a different export
    provider = FakeProvider({MatchChoice: lambda p: MatchChoice(reasoning="Excel is not PDF", match=0)})
    tracked = [(3, "Export reports as PDF", "")]
    assert link_to_tracker(provider, [FeatureRequest(title="Export reports directly to Excel")], tracked) == [None]
    assert len(provider.calls) == 1


def test_unrelated_request_is_new_without_the_model():
    provider = FakeProvider({})
    assert link_to_tracker(provider, [FeatureRequest(title="Alerts when a trade fails")], TRACKED) == [None]
    assert provider.calls == []


def test_possible_match_is_decided_by_the_model():
    request = FeatureRequest(title="Download reports as PDF")
    yes = FakeProvider({MatchChoice: lambda p: MatchChoice(reasoning="same", match=1)})
    no = FakeProvider({MatchChoice: lambda p: MatchChoice(reasoning="different format", match=0)})
    wild = FakeProvider({MatchChoice: lambda p: MatchChoice(reasoning="?", match=7)})
    assert link_to_tracker(yes, [request], TRACKED) == [1]
    assert "Export reports to Excel" in yes.calls[0][1]
    assert link_to_tracker(no, [request], TRACKED) == [None]
    assert link_to_tracker(wild, [request], TRACKED) == [None]  # a number outside the list is no match


def test_empty_tracker():
    assert link_to_tracker(FakeProvider({}), [FeatureRequest(title="Anything")], []) == [None]


def meeting(title, *requests):
    return MeetingReport(title=title, meeting_type="client feedback", anchors=[], summary=Summary(), coverage=[], conflicts=[],
                         feature_requests=list(requests))


def test_tracker_counts_meetings_and_clients(tmp_path):
    store = Store(tmp_path / "a.db")
    prefs = Prefs(company_domain="ourco.com")
    excel = FeatureRequest(title="Export reports to Excel", requested_by="Rahul", quote="q", flagged=True)

    new, repeats = save_features(store, meeting("Pilot review", excel), "2026-10-20", None,
                                 ["rahul@finedge.com", "me@ourco.com"], prefs, [None])
    assert (new, repeats) == (1, 0)
    feature_id = store.features()[0]["id"]

    new, repeats = save_features(store, meeting("Quarterly check-in", excel), "2026-10-27", None,
                                 ["ana@brightbank.io"], prefs, [feature_id])
    assert (new, repeats) == (0, 1)
    # processing the same meeting again doesn't count it twice
    save_features(store, meeting("Quarterly check-in", excel), "2026-10-27", None, [], prefs, [feature_id])

    row = store.feature(feature_id)
    assert row["mentions"] == 2
    assert set(row["clients"].split(",")) == {"finedge.com", "brightbank.io"}
    assert row["last_asked"] == "2026-10-27" and row["status"] == "new"
    assert [m["meeting"] for m in store.mentions(feature_id)] == ["Quarterly check-in", "Pilot review"]


def test_link_to_a_deleted_feature_adds_it_again(tmp_path):
    store = Store(tmp_path / "a.db")
    new, _ = save_features(store, meeting("M", FeatureRequest(title="SSO")), "2026-10-20", None, [], Prefs(), [99])
    assert new == 1


def test_merge_and_status(tmp_path):
    store = Store(tmp_path / "a.db")
    a = store.add_feature("Export to Excel")
    b = store.add_feature("Excel download")
    store.add_mention(a, "M1", "", "2026-10-01", "Rahul", "", "", "finedge.com")
    store.add_mention(b, "M2", "", "2026-10-02", "Ana", "", "", "brightbank.io")
    store.merge_features(keep=a, drop=b)
    assert [r["id"] for r in store.features()] == [a]
    assert store.feature(a)["mentions"] == 2

    store.update_feature(a, status="planned")
    assert store.feature(a)["status"] == "planned"
    try:
        store.update_feature(a, status="maybe")
        raise AssertionError("an unknown status should be refused")
    except ValueError:
        pass
    store.delete_feature(a)
    assert store.features() == []


def test_client_names_are_outside_domains():
    prefs = Prefs(company_domain="ourco.com")
    assert client_names(["me@ourco.com", "Rahul <x>", "rahul@FinEdge.com", "b@finedge.com"], prefs) == "finedge.com"
    no_domain_yet = Prefs(my_email="me@ourco.com")
    assert client_names(["me@ourco.com", "rahul@finedge.com"], no_domain_yet) == "finedge.com"


def test_report_lists_feature_requests():
    report = meeting("Pilot review", FeatureRequest(title="Export reports to Excel", requested_by="Rahul",
                                                    timestamp="00:01:00", flagged=True))
    text = render_markdown(report)
    assert "## Feature requests" in text
    assert "Export reports to Excel" in text and "Rahul" in text and "you flagged this" in text


def test_notion_feature_row_is_created_then_updated(notion):  # noqa: F811
    api = client(notion)
    db = NotionSync.setup_features(api, "parent-1")
    sync = NotionSync(api, "db-m", "db-t", db)
    url = sync.save_feature("Export reports to Excel", "Month-end packs", "new", 1, "finedge.com", "2026-10-20")
    again = sync.save_feature("Export reports to Excel", "Month-end packs", "planned", 2,
                              "finedge.com, brightbank.io", "2026-10-27")
    assert url == again
    [row] = notion.rows(db)
    assert row["properties"]["Status"]["select"]["name"] == "planned"
    assert row["properties"]["Times asked"]["number"] == 2
