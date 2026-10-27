"""Render a MeetingReport as Markdown."""

from __future__ import annotations

from .models import ConflictVerdict, CoverageStatus, MeetingReport, Point

COVERAGE_ICON = {
    CoverageStatus.COVERED: "✅ covered",
    CoverageStatus.REPAIRED: "🛠️ added by repair pass",
    CoverageStatus.FORCED: "⚠️ inserted verbatim",
    CoverageStatus.CONFLICT: "❌ left out, contradicts the transcript (see conflicts)",
}


def _point(p: Point) -> str:
    ts = f" `[{p.timestamp}]`" if p.timestamp else ""
    return f"- {p.text}{ts}"


def render_markdown(report: MeetingReport) -> str:
    s = report.summary
    anchors = {a.id: a for a in report.anchors}
    out = [f"# {report.title}", f"*Meeting type: {report.meeting_type}*", ""]

    sections = [
        ("Decisions", s.decisions),
        ("Open questions", s.open_questions),
        ("Key points", s.key_points),
    ]
    for heading, points in sections:
        out.append(f"## {heading}")
        out += [_point(p) for p in points] or ["- (none)"]
        out.append("")

    out.append("## Action items")
    if s.action_items:
        for a in s.action_items:
            meta = ", ".join(x for x in [a.owner and f"owner: {a.owner}", a.due and f"due: {a.due}"] if x)
            ts = f" `[{a.timestamp}]`" if a.timestamp else ""
            out.append(f"- [ ] {a.task}" + (f" ({meta})" if meta else "") + ts)
    else:
        out.append("- (none)")
    out.append("")

    out.append("## Coverage of your flagged points")
    if report.coverage:
        for c in report.coverage:
            out.append(f"- {COVERAGE_ICON[c.status]}: \"{anchors[c.anchor_id].text}\"")
        out.append(f"\nCovered without repair: {report.coverage_rate_before_repair:.0%}")
    else:
        out.append("- (no must-include points)")
    out.append("")

    conflicts = [c for c in report.conflicts if c.verdict == ConflictVerdict.CONTRADICTS]
    out.append("## Conflicts between your notes and the transcript")
    if conflicts:
        for c in conflicts:
            where = f" at {c.timestamp}" if c.timestamp else ""
            quote = f" Transcript{where}: \"{c.transcript_quote}\"" if c.transcript_quote else ""
            out.append(f"- ⚠️ \"{anchors[c.anchor_id].text}\": {c.explanation}{quote}")
    else:
        out.append("- None found")
    out.append("")
    return "\n".join(out)
