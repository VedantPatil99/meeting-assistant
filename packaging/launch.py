"""Entry point for the packaged app (PyInstaller starts here).

`MeetingAssistant.exe --self-test` loads every library that's only used later (speech-to-text,
audio capture, the credential store) and writes the result to logs/self-test.txt, so a broken
build shows up before anyone records a meeting with it. `--self-test some.wav` also transcribes
that recording with the speech model (which must already be downloaded).
"""

import sys


def transcribe(path: str) -> str:
    from pathlib import Path

    from meeting_assistant.config import Settings
    from meeting_assistant.transcribe import transcribe_file

    transcript = transcribe_file(Path(path), Settings.from_env(), speaker="Test")
    return f"{len(transcript.segments)} segments\n" + transcript.render()


def self_test(wav: str | None) -> int:
    import traceback

    from meeting_assistant.config import Settings

    checks = {
        "speech-to-text (ctranslate2)": lambda: __import__("ctranslate2").__version__,
        "speech-to-text (faster-whisper)": lambda: __import__("faster_whisper").__version__,
        "voice detection model": lambda: __import__("faster_whisper.vad", fromlist=["x"]).get_vad_model() and "ok",
        "audio capture (PortAudio)": lambda: __import__("pyaudiowpatch").get_portaudio_version_text(),
        "credential store": lambda: type(__import__("keyring").get_keyring()).__name__,
        "SVG icons": lambda: str(__import__("PySide6.QtGui", fromlist=["x"]).QImageReader.supportedImageFormats()
                                 .count(b"svg")),
    }
    if wav:
        checks["transcribe " + wav] = lambda: transcribe(wav)
    lines, failed = [], 0
    for name, check in checks.items():
        try:
            lines.append(f"ok    {name}: {check()}")
        except Exception:
            failed += 1
            lines.append(f"FAIL  {name}:\n{traceback.format_exc()}")
    folder = Settings.from_env().meetings_dir / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "self-test.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failed else 0


if "--self-test" in sys.argv:
    rest = sys.argv[sys.argv.index("--self-test") + 1:]
    raise SystemExit(self_test(rest[0] if rest else None))

from meeting_assistant.app.main import main  # noqa: E402

raise SystemExit(main())
