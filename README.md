# Meeting Assistant

An offline AI meeting assistant for Windows. It records the meeting, transcribes it, and turns it
into a summary, **scheduled tasks** and **follow-up email drafts** for the right people.

During the meeting you type quick **anchors**, the points you know matter, and can mark any of
them **internal** so they never reach people outside your company. Afterwards the assistant
**verifies** that every anchor made it into the summary and flags anchors that contradict what was
actually said.

Everything works inside the app. **Gmail** (send follow-ups) and **Notion** (keep meetings and
tasks there too) are optional connectors.

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

Tick **Internal** next to the note box (or press `Ctrl+I` in the quick-note popup) to make a note
internal. It still shapes your summary and internal emails, but it is kept out of anything sent
outside your company.

| Page | What it does |
|---|---|
| Meeting | Record, take notes, process |
| Meetings | Every past meeting: summary, full transcript, your notes (internal ones highlighted), search, reprocess with edited notes |
| Tasks | Action items become **suggested** tasks with real due dates. Schedule or dismiss them, add your own, mark them done. Tasks due today trigger a tray reminder |
| Drafts | One follow-up email per suggested person, with the reason they were picked. Edit, copy, put in Gmail Drafts, or send |
| Automations | No-code rules (below) |
| Settings | Your email and company domain, Gmail, Notion, models |

### Tasks and due dates
The model copies the due date as said ("by Friday", "end of next week", "15/12"); a small
deterministic parser (`scheduling.py`) turns it into a calendar date counted from the meeting
day. "Friday" means the coming Friday and "next Friday" means Friday of next week, every time.

### Follow-up drafts
After the summary, the model picks who needs a follow-up (at most four people) and says why,
then writes one email per person. Names are matched to addresses from the attendee list and
from contacts the app learns when you fill in an address. Your company domain decides who is
external, whatever the model guessed.

For external recipients, the model drafts from a summary with every line that came from an
internal note removed, and never sees the internal notes. A final check compares each external
draft against your internal notes and shows a warning if one seems to have slipped through.

### Gmail (optional)
Uses your own free Google Cloud OAuth client, so nothing is shared with a third party:
1. In [Google Cloud Console](https://console.cloud.google.com), create a project and enable the **Gmail API**.
2. **OAuth consent screen:** user type External; add your address under **Test users**.
3. **Credentials > Create credentials > OAuth client ID > Desktop app.** Paste the client ID and
   secret into Settings > Gmail, then press **Connect Gmail** and sign in in the browser.

The app asks only for `gmail.compose` (create drafts and send; it can't read your inbox). Sign-in
uses OAuth with PKCE and a loopback redirect, so your password never touches the app. The refresh
token is stored in Windows Credential Manager. While the Google project is in **Testing** mode,
Google expires the sign-in after 7 days and you press Connect again.

### Notion (optional)
Settings > Notion: paste an internal integration secret, pick a page shared with it, and the app
creates linked **Meetings** and **Tasks** databases there. Use the **Add to Notion** button on a
task, or the "Save to Notion" automation actions. Saving the same meeting or task twice doesn't
duplicate it.

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
| Then | Draft an email from a template · create tasks · add the meeting or its tasks to Notion · show a notification · save the summary to a folder |
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
- [x] Notion sync: meeting pages and Tasks database
- [x] Meeting library, internal-note toggle
- [x] Tasks with parsed due dates and reminders
- [x] Follow-up drafts for suggested recipients, internal notes filtered and leak-checked
- [x] Gmail connector: drafts and send
- [ ] Feature-request detection and deduplication into a tracker
- [ ] Evaluation set: coverage %, action-item precision/recall, dedup accuracy
