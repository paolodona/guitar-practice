# Prompt 002 — foot control (Group L), and what is left after it

Written 2026-09-07 at the end of the session that finished Phase 2 Groups J/K,
Phase 3 Groups M/N1, and a full backlog sweep + code review. Paste the block
below into a fresh session. The rest of this file is the same text.

---

Continue the plan at `.agent_session/001_woodshed-implementation_plan.md` in the
guitar-practice repo (`paolodona/guitar-practice`, work on `main`). You are running
unattended in a cloud sandbox: no audio device, no browser to look at, no MIDI
hardware, and no human available to confirm anything by ear or by eye. Read that
constraint into every decision below.

## Before touching anything

1. Read `CLAUDE.md` in full — binding project instructions, not background.
2. Read `docs/05-foot-control.md` in full. It is the reference this work exists to
   satisfy, and it contains the three unanswered questions discussed below — plus
   a CONTESTED note about the program map, added 2026-09-07, which you must read
   before touching anything that sends MIDI.
3. Read the plan's `## Phase 3` section (search for it) and the `### The front end
   — web/` part of "Module map, with the signatures fixed now". Group L's contract
   is fixed there; implement against it rather than re-deriving it.
4. Read the plan's "Run 3", "Run 4" and "Run 5" notes at the top (under
   "## Implementation progress") — they say what the last three sessions built,
   what they decided, and what the review found. `.agent_session/BACKLOG.md` is the
   companion: everything open is in its first two sections, with the reasoning.
5. `uv sync --extra dev` then `uv run woodshed doctor`. **This sandbox starts with
   no ffmpeg, no rubberband and no librosa**; the previous session installed them
   with `apt-get update && apt-get install -y --no-install-recommends ffmpeg
   rubberband-cli` and `uv sync --extra analyze --extra dev`, which is what makes
   the suite fully green rather than partially skipped. Do the same before you
   trust a baseline count.

Baseline as of `6af9758`: `uv run --extra dev --extra analyze pytest` is **1129
passed, 0 failed**; `uv run --extra dev ruff check .` clean.

## Scope for this run, in order

### L1 — `web/midi.js` (the whole point of the project)

`web/midi.js` is a stub today: it exports `CC_MAP = {}` and an `attach()` that
throws. Its contract is already fixed by the plan and by the file's own docstring:

- `navigator.requestMIDIAccess({sysex: false})`.
- Substring match on `config.midi.input` against each input port's name.
- `onstatechange` for hot-plug — a pedal plugged in after the page loaded must work.
- **CC value ≥ 64 counts as a press**, and only the press (not the release) fires.
- 150 ms debounce per action.
- The file may contain **only** an input → action-name map plus a call to
  `dispatch(name, 'midi')`. No behaviour of its own. That is CLAUDE.md's "one
  action table" rule, and `web/keys.js` is the worked example of what it looks
  like when obeyed — read it first, including its two documented exceptions and
  why they are not exceptions to the rule that matters.

Things already in place that you must use rather than duplicate:

- `web/actions.js` — `ACTIONS`, with `cc: 80…85` on the six foot actions and
  `null` on the other eleven, plus `on`/`dispatch`. `CC_MAP` is **derived** from
  that table (the derivation is written out in midi.js's own docstring); never a
  second hand-typed copy of the numbers.
- `web/app.js` calls `attach(window)` from `keys.js` at startup — midi.js's
  `attach()` belongs beside it, and must not throw or block startup when the
  browser has no Web MIDI at all (Firefox behind a permission, Safari not at all).
- `GET /api/config` already serves `config.midi` (`input`, `channel`,
  `map: {int: action-name}`) to the browser.

**One contract question the plan does not settle, and you should:** there are two
possible sources for CC → action — `ACTIONS`' own `cc` field (the artboard's
80–85) and `config.midi.map` from `config.yaml`. Recommendation, for you to
confirm or overrule with reasoning in your report: `config.midi.map` **overrides**
the defaults per CC, because which CC each footswitch actually sends is question
(1) at the pedal and must be settable without editing JS — while `ACTIONS` stays
the only place an action *name* is defined, so a config naming an unknown action
is refused loudly rather than silently ignored.

**Testing.** Drive it from a fake `navigator.requestMIDIAccess` in a
`web/tests/test_midi.mjs` node script — the same discipline
`web/tests/test_buffer_engine.mjs` uses for a fake `AudioContext`, and it covers
everything that can go wrong here (port matching, hot-plug, the ≥ 64 rule, the
debounce, that a release fires nothing). `tests/test_web_lint.py` already runs
every `web/tests/*.mjs` under pytest, so a new file is picked up automatically.
Phase 3's own gate also asks for **a lint-style test that midi.js contains no
behaviour beyond the map and the debounce** — model it on that file's existing
`playbackRate` scan, including the comment-and-string stripping.

### L2 — the foot legend, and the browser note

Read what exists before building: `screens/practice.js`'s foot strip **already**
renders the six actions with their CC numbers, in `ACTIONS`' own table order. What
is missing is any sign of whether a pedal is actually *connected*, which is the
thing you cannot tell by looking at the strip today. A quiet indicator driven by
midi.js's own connection state is the useful half of L2.

The plan also names "the settings screen naming Chrome/Edge". **There is no
settings screen and no settings artboard** (`design/` has nine artboards; none of
them is one), and `doctor` already prints the browser note. Do not invent a whole
screen for one sentence — put the note where a person meets the problem (the foot
legend when no device matched, say) and record the decision in the plan.

### If L1 and L2 land clean, with room to spare

`.agent_session/BACKLOG.md`'s "Open — real work, nobody blocked on it" section has
one item: a capture segment that reported an overflow is refused for binding
(correctly), but there is no way to re-record just that segment from the review
screen — the whole pass has to be redone. Read `capture_session.py` and
`screens/capture.js` before deciding whether that is a small change or a design
question, and say which.

## Explicitly out of scope — do not touch

- **L3 (expression pedal → speed).** It is the one part genuinely blocked on the
  pedal: question (3) in `docs/05-foot-control.md` is whether the continuous CC is
  smooth enough and cheap enough in messages to be a speed knob, and that is
  measured at the unit. Do not guess at it.
- **N2 (Program Change send).** Blocked twice over — see the BACKLOG entry — and
  now on a contested fact as well. It needs MIDI *output*, and the program-map
  question is unsettled: `docs/05-foot-control.md` says a PC names a slot to be
  resolved through the pedal's `PROGRAM MAP`, while `docs/08-unification.md`
  reports `gx100` testing it on the unit and finding `PC n` a plain identity —
  and that the `CC#0 → CC#32 → PC` ordering **wedged the pedal until its power
  was pulled**. Do not build anything that sends that CC pair. `gx100.py`
  deliberately contains no slot→PC arithmetic and a test asserts that; it is
  correct under either reading, so keep it that way.
- **Both manual gates.** Phase 2's ("loop a real solo at 55% for twenty passes and
  listen for a tick at the seam") and Phase 3's ("practise ten minutes without
  touching the keyboard or mouse"). Build everything that leads up to them, run
  every automated check, then say plainly that the human half is still open. Do
  not mark either closed.
- Anything claiming to verify real WASAPI/audio-interface or real MIDI-device
  behaviour. There is no hardware here.

## A gate to read honestly, not to ignore

The plan says Group L's "Before starting" is to settle the three
eyes-on-the-unit questions at the pedal first. Last session's read — recorded
here so you neither ignore it silently nor refuse to start — is that this gate is
more conservative than the actual dependency:

- Question (1), *which* CCs the unit sends, is config-driven (`config.midi.input`,
  `config.midi.map`), so L1 does not depend on the answer.
- Question (2), whether a switch can send a CC *without* also changing a patch,
  has a stated fallback in that same document (a dedicated USB MIDI foot
  controller, "and the app does not care which device sent the CC"), so it changes
  the hardware, not this code.
- Question (3) blocks **L3 only**, which is why L3 is out of scope above.

If reading `docs/05-foot-control.md` yourself leads you to a different conclusion,
follow your reading and say so in your report — do not build past a gate you
believe is real.

## Process

Match the discipline the plan doc already shows (read a few completed Groups'
entries before you start):

- **Test-first**, including when a change looks too small to need one.
- One commit per unit. Sentence-case imperative subjects, no `type(scope):`
  prefix — see this session's own log for examples.
- After each unit: `uv run --extra dev ruff check .`, `uv run --extra dev
  --extra analyze pytest`, `node --check` on every touched `web/*.js`, and the
  no-extras gate:
  `uv run --no-project --with pytest --with numpy --with pyyaml --with pydantic
  pytest tests/ -m "not needs_rubberband and not needs_librosa and not needs_device"`.
  **Check the exit status, not just the tail of the output** — piping pytest into
  `tail` hides a failure, and it hid one for a whole commit last session.
- After each unit, edit the plan file and add a "Done, <date>" note under that
  unit's own bullet, in the same voice and level of detail the existing entries
  use (what was built, any judgement call, test counts). Direct edit — no separate
  summary file, no plan-management skill.
- Log anything genuinely out of scope or needing Paolo's own call to
  `.agent_session/BACKLOG.md`, matching the "Open"/"Resolved" format it now uses.
- Work directly on `main` and push as you go. Never force-push, never rewrite
  history.
- If you hit something genuinely ambiguous or blocked, stop, write it down
  precisely (plan note + BACKLOG entry), commit that, and move to the next unit
  rather than guessing past it.

## Report back with

What got built; full suite pass count before and after; what is still open in
Phase 3; and anything you deferred or flagged, with the reasoning rather than
just the fact.
