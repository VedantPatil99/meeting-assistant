# Meeting Assistant

An offline AI meeting assistant for Windows. It records the meeting, transcribes it, and turns it
into a summary, **scheduled tasks**, **follow-up email drafts** for the right people, and a
**feature-request tracker** that counts how often clients ask for the same thing.

During the meeting you type quick **anchors**, the points you know matter, and can mark any of
them **internal** so they never reach people outside your company. Afterwards the assistant
**verifies** that every anchor made it into the summary and flags anchors that contradict what was
actually said.

Everything works inside the app. **Gmail** (send follow-ups) and **Notion** (keep meetings,
tasks and feature requests there too) are optional connectors.

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
   and key points, each with a timestamp. The meeting type picks a **template** that adds its own
   sections (`pipeline/templates.py`): updates and blockers for a standup; pain points, budget and
   decision process for a sales call; root cause and impact for an incident review. The sections
   become required fields in the JSON schema the model fills, so an empty one means "not
   discussed" rather than "forgotten".
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
| Meetings | Every past meeting: summary, full transcript, your notes (internal ones highlighted), search, reprocess with edited notes. **Every timestamp plays the recording from that moment** and highlights the line being played |
| Ask | Questions across all your meetings ("what did Rahul say about pricing last month?"), answered with the moments they come from |
| Tasks | Action items become **suggested** tasks with real due dates. Schedule or dismiss them, add your own, mark them done. Tasks due today trigger a tray reminder |
| Drafts | One follow-up email per suggested person, with the reason they were picked. Edit, copy, put in Gmail Drafts, or send |
| Feature requests | Every request clients made, most asked first, with who asked, when, and its status |
| Automations | No-code rules (below) |
| Settings | Your email and company domain, Gmail, Notion, models |

The look is a light and a dark theme built from one set of colour tokens (`app/theme.py`):
widgets get a role such as `primary` or `card` and the stylesheet styles the role, so no page
hard-codes a colour. The switch at the bottom of the sidebar picks Light, Dark or Match Windows.
It uses fonts that ship with Windows 11 (Segoe UI Variable and Segoe Fluent Icons for the icons).

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

### Feature requests
Each meeting is searched for things a client asked the product to do that it doesn't do yet
(bug reports, slowness and pricing are left out). The model quotes the line first, then names
the request; the speaker and timestamp are taken from the closest transcript line. Every `fr:`
note ends up in the list even if the model missed it.

New requests are matched against the tracker so a repeat is counted instead of added twice.
Word overlap settles the clear cases for free. A request is linked automatically only when most
of its words are shared **both ways**, because one-way overlap called "Export reports to Excel"
a repeat of "Export reports as PDF". A possible match goes to the model with at most three
candidates, and it counts extra options ("by email or Teams") as the same need but a different
core capability as new. The **Feature requests** page lists requests by how often they were
asked, by which clients and when, with a status (new, planned, in progress, shipped, declined),
merge and an optional Notion database.

### Ask your meetings
Each meeting's transcript is cut into short overlapping passages and its summary points are
added too. A small local embedding model (`nomic-embed-text`, about 270 MB, downloaded from the
Ask page the first time) turns each passage into a vector that represents its meaning; the
vectors live in one SQLite file next to your meetings, and only new or changed meetings are
embedded again. A question is matched by meaning plus a bonus for sharing its exact words, since
names and terms like "CSV" matter. "Last month", "last week" or "in October" narrow the search to
those dates, worked out in code like due dates are.

The local model gets the best passages and must quote the lines it uses before it writes the
answer. Each quote is checked against the passage it cites; if none checks out, the answer is
"I couldn't find that in your meetings" instead of a guess. Clicking a source opens that meeting
and plays the recording from that line.

### Playing the recording
Both tracks are padded against the wall clock, so the same second in each is the same moment.
`playback.py` reads both from the clicked time, mixes them, and streams them to the speakers
with PortAudio, the library already used for recording; Qt's media module would have added
FFmpeg (about 67 MB) back to the build.

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
creates linked **Meetings** and **Tasks** databases and a **Feature requests** database there.
Use the **Add to Notion** button on a task or feature request, or the "Save to Notion"
automation actions. Saving the same meeting, task or request twice doesn't duplicate it; a
feature request already in Notion is updated with its latest count and status.

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

## Sharing a test build
```powershell
powershell -ExecutionPolicy Bypass -File packaginguild.ps1
```
builds `dist\MeetingAssistant\MeetingAssistant.exe` with PyInstaller and zips it (about 90 MB)
with a "READ ME FIRST" for testers. Testers don't need Python: on first start a setup window
checks for [Ollama](https://ollama.com) (free; it can't be bundled because it's a separate
program), links to its installer, and downloads the AI model (a lighter one is offered for
smaller laptops) and the speech model with progress bars.

- `MeetingAssistant.exe --self-test` loads the speech, audio and credential libraries inside the
  packaged app and writes `%USERPROFILE%\MeetingAssistant\logs\self-test.txt`.
- Unexpected errors are written to `logs\crash.log` and shown, so testers can send the file back.
- The build leaves out PyAV (FFmpeg, 67 MB): recordings are WAV, read by `transcribe.read_wav`.
  It also drops Qt files a widgets-only English app never loads (software OpenGL, Qt Quick/QML,
  translations).
- The build isn't code-signed (that costs money), so Windows SmartScreen asks testers to click
  **More info > Run anyway** the first time.

## Evaluation
`eval/` holds 12 scripted meetings (standup, sprint planning, sales discovery, incident review,
vendor call, pricing negotiation, two long reviews that need the map-reduce merge, and more).
Each has the notes typed during it and an `expected.json` with the facts the summary must
contain, who owns each task, which notes the meeting contradicts, and the feature requests,
plus traps (complaints, bugs and pricing that are not feature requests). It also has 16
tracker-matching cases and 14 questions across all the meetings, one of which they don't answer.
Matching is by keywords, so wording doesn't matter.

```bash
python eval/run_eval.py --label 7b
python eval/run_eval.py --label split --extract-model qwen2.5:3b
python eval/run_eval.py --label 7b --ask
```

Results on an RTX 3060 laptop GPU (6 GB):

| | qwen2.5:7b + fixes | qwen2.5:7b before | 3b reads, 7b checks | qwen2.5:3b |
|---|---|---|---|---|
| Key facts in the summary | **35/36 (97%)** | 33/36 (92%) | 25/36 (69%) | 25/36 (69%) |
| Tasks with the right owner | **18/27 (67%)** | 16/27 (59%) | 11/27 (41%) | 10/27 (37%) |
| Contradicted notes caught | **6/6** | 6/6 | 6/6 | 5/6 |
| Conflict flags that were right | 6/10 (60%) | 6/10 | 6/10 | 5/20 (25%) |
| Feature requests found | 9/14 (64%) | 9/14 | 9/14 | 9/14 |
| Found requests that were real | **10/10** | 10/10 | 9/10 | 9/11 |
| Flagged notes in the first draft | 23/31 (74%) | 27/31 (87%) | | |
| Tracker matches right | 14/16 (88%) | 14/16 | 13/16 | 8/16 (50%) |
| Seconds per meeting | 67 | 59 | 71 | 39 |

Questions across meetings (7B): **11/14**, including "not found" for the one the meetings
don't cover. Indexing all 12 meetings took 4.5 s.

What the numbers say:
- **The 7B model stays the default.** Splitting the work was slower, not faster: both models
  don't fit in 6 GB of video memory together, so Ollama swaps them on every switch. The 3B model
  alone is 34% faster but misses far more, and flags twice as many false conflicts.
- **Speed is mostly the GPU.** The shortest meeting took 108 s when Ollama had silently fallen
  back to the CPU after the laptop slept, and 18 s once restarting Ollama put it back on the GPU.
- **Fixes from the first run:** in one meeting the summary repeated the user's contradicted note
  as fact; a summary line that repeats a contradicted note is now replaced with what the meeting
  settled on. Blank task owners are now filled from the source line ("I'll ..." gives the speaker,
  "Ankit will ..." gives Ankit). Facts went from 92% to 97% and owners from 59% to 67%.
- **The trade-off:** the summary prompt now says the transcript wins over the user's notes, so
  the first draft repeats notes less (74% vs 87% covered before repair). The repair step makes up
  the difference, at about 8 s more per meeting; two notes had to be inserted verbatim.
- **Still weak:** the two tracker misses are synonyms ("night theme" vs "dark mode"), which word
  overlap can't see, and a third of owners are still missed or wrong.
- **Ask:** the search found the right meeting for every question; the three misses are the 7B
  model misreading a passage it was given.

The cases were written for this project and the same set was used while building it, so treat
the numbers as a regression check, not a benchmark against other tools.

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
- [x] Feature-request detection and deduplication into a tracker
- [x] Evaluation set: 12 scripted meetings, tracker and question cases (`eval/`)
- [x] Summary templates per meeting type
- [x] Click a timestamp to hear that moment
- [x] Ask questions across all meetings, with checked quotes
