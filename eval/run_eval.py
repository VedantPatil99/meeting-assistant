"""Score the pipeline against the scripted meetings in eval/meetings.

    python eval/run_eval.py --label 7b
    python eval/run_eval.py --label split --extract-model qwen2.5:3b
    python eval/run_eval.py --label 3b --model qwen2.5:3b

Each meeting folder has the transcript, the notes typed during it, and expected.json with what
a correct result contains. Matching is by keywords, so it doesn't depend on exact wording:
a fact passes when the summary contains one alternative from every keyword group.

Scored:
  facts      key facts (including every flagged note) that appear in the final summary
  owners     action items assigned to the right person
  conflicts  notes the meeting contradicts, flagged as conflicts (precision and recall)
  features   feature requests found (precision and recall; complaints and pricing don't count)
  tracker    new requests linked to the right tracker entry, or correctly left as new
  ask        (with --ask) questions across all the meetings: right meeting cited, right answer,
             and "not found" for a question the meetings don't cover

Results go to eval/results/<label>.json; the table is printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))

from meeting_assistant.anchors import parse_anchors  # noqa: E402
from meeting_assistant.config import Settings  # noqa: E402
from meeting_assistant.llm import OllamaProvider  # noqa: E402
from meeting_assistant.models import ConflictVerdict, FeatureRequest  # noqa: E402
from meeting_assistant.pipeline import process_meeting  # noqa: E402
from meeting_assistant.pipeline.features import link_to_tracker  # noqa: E402
from meeting_assistant.transcript import parse_transcript  # noqa: E402


class Timed:
    """Wraps a provider and records how long each call takes, by schema."""

    def __init__(self, provider, log: list):
        self.provider, self.log, self.name, self.model = provider, log, provider.name, provider.model

    def complete_json(self, system, prompt, schema):
        start = time.perf_counter()
        try:
            return self.provider.complete_json(system, prompt, schema)
        finally:
            self.log.append((self.model, schema.__name__, time.perf_counter() - start))


def hits(text: str, groups: list[list[str]]) -> bool:
    text = text.lower()
    return all(any(alt.lower() in text for alt in group) for group in groups)


def score_meeting(folder: Path, main, reader) -> dict:
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    transcript = parse_transcript((folder / "transcript.txt").read_text(encoding="utf-8"))
    anchors = parse_anchors((folder / "anchors.txt").read_text(encoding="utf-8"))

    start = time.perf_counter()
    report = process_meeting(main, transcript, anchors, title=expected["title"],
                             meeting_type=expected["type"], extract_provider=reader)
    seconds = time.perf_counter() - start

    summary = "\n".join(report.summary.lines())
    facts = {f["name"]: hits(summary, f["all"]) for f in expected["facts"]}

    owners = {}
    for o in expected["owners"]:
        items = [a for a in report.summary.action_items if hits(a.task, o["task"])]
        owners[f'{o["owner"]}: {o["task"][0][0]}'] = any(o["owner"].lower() in (a.owner or "").lower() for a in items)

    by_id = {a.id: a.text for a in anchors}
    flagged = [by_id[c.anchor_id] for c in report.conflicts if c.verdict == ConflictVerdict.CONTRADICTS]
    wanted = expected["conflicts"]
    conflicts = {
        "expected": len(wanted),
        "found": len(flagged),
        "correct": sum(any(w in f for w in wanted) for f in flagged),
        "false_alarms": [f for f in flagged if not any(w in f for w in wanted)],
        "missed": [w for w in wanted if not any(w in f for f in flagged)],
    }

    found = [f"{r.title} {r.detail}" for r in report.feature_requests]
    features = {
        "expected": len(expected["features"]),
        "found": len(found),
        "recalled": [e["name"] for e in expected["features"] if any(hits(f, e["all"]) for f in found)],
        "extra": [f for f in found if not any(hits(f, e["all"]) for e in expected["features"])],
    }
    coverage = defaultdict(int)
    for c in report.coverage:
        coverage[c.status.value] += 1
    return {"seconds": round(seconds, 1), "facts": facts, "owners": owners, "conflicts": conflicts,
            "features": features, "coverage": dict(coverage), "summary": summary,
            "found_requests": found}


def score_tracker(main) -> dict:
    data = json.loads((ROOT / "tracker_cases.json").read_text(encoding="utf-8"))
    tracked = [tuple(t) for t in data["tracker"]]
    results = []
    for case in data["cases"]:
        log: list = []
        request = FeatureRequest(title=case["title"], detail=case["detail"])
        [link] = link_to_tracker(Timed(main, log), [request], tracked)
        results.append({**case, "got": link, "ok": link == case["expected"],
                        "decided_by": "model" if log else "word overlap"})
    return {"cases": results}


def score_ask(main, settings) -> dict:
    """Index the scripted meetings (transcripts only) and ask the questions in ask_cases.json.
    A case passes when the answer cites the right meeting and contains the expected words; the
    unanswerable one passes when the answer is "not found"."""
    import tempfile
    from datetime import date, datetime, timedelta

    from meeting_assistant.ask import MeetingIndex, ask
    from meeting_assistant.llm import OllamaEmbedder
    from meeting_assistant.session import MeetingSession

    tmp = Path(tempfile.mkdtemp())
    names = {}
    for i, folder in enumerate(sorted(p for p in (ROOT / "meetings").iterdir() if p.is_dir())):
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        s = MeetingSession.create(tmp / "meetings", expected["title"], expected["type"],
                                  now=datetime(2026, 10, 1, 10) + timedelta(days=3 * i))
        s.save_transcript(parse_transcript((folder / "transcript.txt").read_text(encoding="utf-8")))
        names[str(s.folder)] = folder.name
    index = MeetingIndex(tmp / "index.sqlite", OllamaEmbedder(settings))
    start = time.perf_counter()
    index.update([MeetingSession(Path(f)) for f in names])
    index_seconds = time.perf_counter() - start
    cases = json.loads((ROOT / "ask_cases.json").read_text(encoding="utf-8"))["cases"]
    results = []
    for case in cases:
        start = time.perf_counter()
        r = ask(main, index, case["question"], today=date(2026, 11, 15))
        cited = sorted({names[s.folder] for s in r.sources})
        if case["meeting"] is None:
            ok = not r.found
        else:
            ok = r.found and case["meeting"] in cited and hits(r.answer, case["all"])
        results.append({"question": case["question"], "ok": ok, "found": r.found, "answer": r.answer,
                        "cited": cited, "seconds": round(time.perf_counter() - start, 1)})
    return {"index_seconds": round(index_seconds, 1), "cases": results}


def ratio(a: int, b: int) -> str:
    return f"{a}/{b}" + (f" ({a / b:.0%})" if b else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--model", help="main model (default: OLLAMA_MODEL or qwen2.5:7b)")
    parser.add_argument("--extract-model", help="lighter model for per-chunk reading")
    parser.add_argument("--only", nargs="*", help="meeting folder names to run")
    parser.add_argument("--ask", action="store_true", help="score questions across meetings (ask_cases.json) only")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.model:
        settings = replace(settings, ollama_model=args.model)
    log: list = []
    main_provider = Timed(OllamaProvider(settings), log)
    reader = Timed(OllamaProvider(replace(settings, ollama_model=args.extract_model)), log) \
        if args.extract_model else None
    if not main_provider.provider.is_available():
        print(f"Ollama isn't running or {settings.ollama_model} isn't installed.")
        return 1
    if args.ask:
        result = score_ask(main_provider, settings)
        out = ROOT / "results" / f"{args.label}-ask.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        for c in result["cases"]:
            print(f'{"ok  " if c["ok"] else "MISS"} {c["seconds"]:5}s  {c["question"]}\n           -> {c["answer"]}  {c["cited"]}')
        passed = sum(c["ok"] for c in result["cases"])
        print(f'\nask: {ratio(passed, len(result["cases"]))}, indexing 12 meetings took {result["index_seconds"]}s')
        return 0

    folders = sorted(p for p in (ROOT / "meetings").iterdir() if p.is_dir())
    if args.only:
        folders = [f for f in folders if f.name in args.only]

    meetings = {}
    for folder in folders:
        print(f"{folder.name} ...", end=" ", flush=True)
        meetings[folder.name] = score_meeting(folder, main_provider, reader)
        print(f'{meetings[folder.name]["seconds"]}s', flush=True)
    tracker = score_tracker(main_provider) if not args.only else {"cases": []}

    facts = [ok for m in meetings.values() for ok in m["facts"].values()]
    owners = [ok for m in meetings.values() for ok in m["owners"].values()]
    c = {k: sum(m["conflicts"][k] for m in meetings.values()) for k in ("expected", "found", "correct")}
    f_exp = sum(m["features"]["expected"] for m in meetings.values())
    f_found = sum(m["features"]["found"] for m in meetings.values())
    f_rec = sum(len(m["features"]["recalled"]) for m in meetings.values())
    f_extra = sum(len(m["features"]["extra"]) for m in meetings.values())
    t = tracker["cases"]
    statuses = defaultdict(int)
    for m in meetings.values():
        for k, v in m["coverage"].items():
            statuses[k] += v
    time_by_step = defaultdict(float)
    for model, schema, secs in log:
        time_by_step[f"{schema} ({model})"] += secs

    totals = {
        "model": settings.ollama_model, "extract_model": args.extract_model,
        "facts": ratio(sum(facts), len(facts)),
        "owners": ratio(sum(owners), len(owners)),
        "conflict recall": ratio(c["correct"], c["expected"]),
        "conflict precision": ratio(c["correct"], c["found"]),
        "feature recall": ratio(f_rec, f_exp),
        "feature precision": ratio(f_found - f_extra, f_found),
        "tracker": ratio(sum(x["ok"] for x in t), len(t)),
        "coverage statuses": dict(statuses),
        "seconds total": round(sum(m["seconds"] for m in meetings.values()), 1),
        "seconds per meeting": round(sum(m["seconds"] for m in meetings.values()) / max(len(meetings), 1), 1),
        "seconds by step": {k: round(v, 1) for k, v in sorted(time_by_step.items(), key=lambda kv: -kv[1])},
    }
    out = ROOT / "results" / f"{args.label}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"totals": totals, "meetings": meetings, "tracker": tracker}, indent=2),
                   encoding="utf-8")
    print()
    for k, v in totals.items():
        print(f"{k:20} {v}")
    print(f"\nDetails: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
