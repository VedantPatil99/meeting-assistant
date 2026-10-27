"""Command-line entry point.

    meeting-assistant check
    meeting-assistant transcribe recording.wav -o transcript.txt
    meeting-assistant process -t transcript.txt -a anchors.txt -o output/report.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .anchors import parse_anchors
from .config import Settings
from .llm import LLMError, OllamaProvider
from .pipeline import process_meeting
from .report import render_markdown
from .transcript import parse_transcript


def cmd_check(settings: Settings, _args) -> int:
    provider = OllamaProvider(settings)
    if provider.is_available():
        print(f"OK: Ollama is running and '{settings.ollama_model}' is installed.")
        return 0
    print(
        f"Ollama is not running or '{settings.ollama_model}' is missing.\n"
        f"Start the Ollama app, then run: ollama pull {settings.ollama_model}"
    )
    return 1


def cmd_transcribe(settings: Settings, args) -> int:
    from .transcribe import transcribe_file

    transcript = transcribe_file(
        args.audio, settings, speaker=args.speaker,
        on_progress=lambda s: print(f"\r  transcribed {s:,.0f}s", end="", flush=True),
    )
    print()
    Path(args.output).write_text(transcript.render(), encoding="utf-8")
    print(f"Wrote {len(transcript.segments)} segments to {args.output}")
    return 0


def cmd_process(settings: Settings, args) -> int:
    transcript = parse_transcript(Path(args.transcript).read_text(encoding="utf-8"))
    anchors = parse_anchors(Path(args.anchors).read_text(encoding="utf-8")) if args.anchors else []
    provider = OllamaProvider(settings)
    try:
        report = process_meeting(
            provider, transcript, anchors,
            title=args.title, meeting_type=args.type,
            on_progress=lambda msg: print(f"- {msg}", flush=True),
        )
    except LLMError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(report), encoding="utf-8")
    out.with_suffix(".json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"Report written to {out} (and {out.with_suffix('.json').name})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meeting-assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify Ollama and the model are ready")

    t = sub.add_parser("transcribe", help="transcribe an audio file with faster-whisper")
    t.add_argument("audio")
    t.add_argument("-o", "--output", default="transcript.txt")
    t.add_argument("--speaker", help="label for every segment, e.g. 'Me'")

    p = sub.add_parser("process", help="summarize a transcript using your anchors")
    p.add_argument("-t", "--transcript", required=True)
    p.add_argument("-a", "--anchors")
    p.add_argument("--title", default="Meeting")
    p.add_argument("--type", default="general", help="e.g. client feedback, standup, sprint planning")
    p.add_argument("-o", "--output", default="output/report.md")

    args = parser.parse_args(argv)
    settings = Settings.from_env()
    handler = {"check": cmd_check, "transcribe": cmd_transcribe, "process": cmd_process}[args.command]
    return handler(settings, args)


if __name__ == "__main__":
    raise SystemExit(main())
