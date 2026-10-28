# Meeting Assistant

An offline AI meeting assistant for Windows. During a meeting you type quick **anchors**, the
points you know matter. Afterwards the assistant summarizes the transcript and **verifies** that
every anchor made it into the summary. It also flags anchors that contradict what was actually said.

Everything runs locally and costs nothing: a local LLM through [Ollama](https://ollama.com) and
local speech-to-text through [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
Meeting audio never leaves your computer.

## Why anchors?
Generic meeting summarizers don't know the context you have. They can miss the point that
actually mattered, or quietly get a date wrong. Anchors let you tell the assistant what matters:

| Prefix | Meaning |
|---|---|
| `!` or no prefix | Must appear in the summary |
| `ctx:` | Background that shapes interpretation, never quoted |
| `internal:` | Must appear internally, never in client-facing output |
| `fr:` | Feature request the client raised |

An optional `[HH:MM:SS]` at the start ties an anchor to a moment in the meeting.

## How the pipeline works
1. **Summarize** (map-reduce): the transcript is split into chunks small enough for a 7B model,
   facts are extracted from each chunk, then merged into decisions, action items, open questions
   and key points, each with a timestamp.
2. **Conflict detection:** each fact-stating anchor (`!`, `internal:`, `fr:`) is compared with
   what the meeting *finally* agreed. The result is consistent, contradicts or not mentioned, with
   a quote and timestamp. The model states the facts before it picks a verdict; with the verdict
   first, the 7B model answered "consistent" while its own explanation described the contradiction.
   `ctx:` notes are background and are not checked.
3. **Coverage guarantee** for each remaining must-include anchor:
   check the summary → if it's missing, run a repair pass grounded in nearby transcript lines →
   check again → if it's still missing, insert the note verbatim and mark it.
   Anchors the meeting contradicts are never inserted; they are listed as conflicts for you to resolve.
   The report records how many anchors were covered **without** repair. That number is the honest
   measure of the model's quality (50% on the sample meeting with `qwen2.5:7b`).

The LLM sits behind a small `LLMProvider` interface (`src/meeting_assistant/llm/`), so a
hosted model can replace Ollama without changing the pipeline.

## Setup (Windows)
1. Install [Ollama](https://ollama.com), start it and pull the model:
   ```bash
   ollama pull qwen2.5:7b
   ```
2. Create a virtual environment with Python 3.11 and install:
   ```bash
   py -3.11 -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[transcribe,app,dev]"
   ```
3. Optionally copy `.env.example` to `.env` to change the model or the Whisper settings.

## Usage
```bash
meeting-assistant check
meeting-assistant process -t samples/client_feedback/transcript.txt -a samples/client_feedback/anchors.txt --title "Dashboard pilot feedback" --type "client feedback"
meeting-assistant transcribe recording.wav -o transcript.txt --speaker Me
meeting-assistant-app
```
The sample meeting contains a deliberate conflict: the user noted go-live as 15 December, but
the meeting agreed on 20 December.

## Desktop app
`meeting-assistant-app` opens the window and puts an icon in the system tray.

1. **Start recording.** Your mic and the call audio (everything your speakers play) are recorded
   as two tracks. That gives a "Me" vs "Others" speaker split without extra models. Windows
   loopback sends nothing during silence, so both tracks are padded against the clock and stay
   aligned. Audio is stored as 16 kHz mono, about 115 MB per hour per track.
2. **Add notes from any app** with `Ctrl+Shift+Space`. A small box opens over Zoom or Teams and
   each note is stamped with the meeting time.
3. **Stop, review your notes, then Process meeting.** Both tracks are transcribed, merged by time,
   and run through the pipeline.

Each meeting is saved in its own folder under `%USERPROFILE%\MeetingAssistant`: audio, notes,
transcript and report. Closing the window while recording keeps recording from the tray.

Speech-to-text uses the GPU when NVIDIA's cuBLAS and cuDNN libraries are installed, and falls
back to the CPU otherwise.

## Automations (no code)
The **Automations** page lets you build rules from dropdowns. Each rule is one sentence:

> **When** a meeting is processed · **only if** the type is *client feedback* and there are
> external attendees · **then** draft an email to the external attendees · **how:** wait for my approval

| Part | Options |
|---|---|
| When | A meeting is processed · an action item is found · a note conflicts with the meeting |
| Only if | Meeting type, title, external attendees, conflicts, feature requests, number of action items, action item owner or text |
| Then | Draft an email from a template · create tasks · show a notification · save the summary to a folder |
| How | Run automatically · wait for my approval · only tell me what it would do |

- **Templates:** five ready-made rules, such as a client recap email or tasks from every meeting.
- **Test run:** runs a rule against a past meeting and shows exactly what it would do, without
  sending, saving or creating anything.
- **Approvals inbox:** items from "wait for my approval" rules. Approved emails are saved as unsent
  `.eml` drafts that Outlook or Windows Mail opens ready to send.
- **Internal notes stay internal:** when any recipient is outside your company domain (set in
  Settings), summary lines that came from `internal:` notes and the conflicts list are removed.
- **Safety:** saving an automatic rule that emails outsiders asks for confirmation, and automatic
  rules pause after 20 runs in an hour. Every run is recorded in the Activity tab.

Rules are stored as JSON in a local SQLite database (`assistant.db` in the meetings folder),
so the editor is a form over data rather than generated code.

## Tests
```bash
pytest
```
The tests use a fake provider, so they run without Ollama.

## Roadmap
- [x] Core pipeline: summary, coverage guarantee, conflict detection
- [x] CLI
- [x] Desktop app: mic and call-audio recording, tray icon, global note hotkey, meeting folders
- [x] Automation builder: no-code rules, templates, test run, approvals inbox, activity log, tasks
- [ ] Notion sync: meeting pages and Tasks database
- [ ] Feature-request detection and deduplication into a Notion tracker
- [ ] Follow-up email drafts (internal notes filtered out) through Gmail, with approval
- [ ] Evaluation set: coverage %, action-item precision/recall, dedup accuracy
