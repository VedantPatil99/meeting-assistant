"""One folder per meeting: audio tracks, notes, transcript and report live together."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .models import MeetingReport, Transcript
from .report import render_markdown

MIC_SPEAKER = "Me"
SYSTEM_SPEAKER = "Others"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "meeting"


@dataclass
class MeetingInfo:
    title: str
    meeting_type: str
    started_at: str
    duration_seconds: float = 0.0


class MeetingSession:
    def __init__(self, folder: Path):
        self.folder = folder

    @classmethod
    def create(cls, base_dir: Path, title: str, meeting_type: str, now: datetime | None = None) -> "MeetingSession":
        now = now or datetime.now()
        folder = base_dir / f"{now:%Y-%m-%d_%H%M%S}_{slugify(title)}"
        folder.mkdir(parents=True, exist_ok=False)
        session = cls(folder)
        session.save_info(MeetingInfo(title, meeting_type, now.isoformat(timespec="seconds")))
        return session

    # paths
    @property
    def mic_audio(self) -> Path:
        return self.folder / "mic.wav"

    @property
    def system_audio(self) -> Path:
        return self.folder / "system.wav"

    @property
    def anchors_file(self) -> Path:
        return self.folder / "anchors.txt"

    @property
    def transcript_file(self) -> Path:
        return self.folder / "transcript.txt"

    @property
    def report_file(self) -> Path:
        return self.folder / "report.md"

    def audio_tracks(self) -> list[tuple[Path, str]]:
        return [(self.mic_audio, MIC_SPEAKER), (self.system_audio, SYSTEM_SPEAKER)]

    # info
    def load_info(self) -> MeetingInfo:
        return MeetingInfo(**json.loads((self.folder / "meeting.json").read_text(encoding="utf-8")))

    def save_info(self, info: MeetingInfo) -> None:
        (self.folder / "meeting.json").write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")

    # content
    def save_anchors(self, text: str) -> None:
        self.anchors_file.write_text(text.strip() + "\n", encoding="utf-8")

    def load_anchors(self) -> str:
        return self.anchors_file.read_text(encoding="utf-8") if self.anchors_file.exists() else ""

    def save_transcript(self, transcript: Transcript) -> None:
        self.transcript_file.write_text(transcript.render(), encoding="utf-8")

    def save_report(self, report: MeetingReport) -> str:
        markdown = render_markdown(report)
        self.report_file.write_text(markdown, encoding="utf-8")
        self.report_file.with_suffix(".json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return markdown
