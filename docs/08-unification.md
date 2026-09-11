# Unification with the sibling repos — a proposal, not a decision

> **Status: PROPOSED. Nothing has been agreed and no work has started.**

The full proposal lives in **`rambass-live/docs/unification.md`**. It is written
once, in one place, deliberately — writing it three times would reproduce the
exact problem it is about.

## Why it concerns this repo

**This repo is the reason the question is worth asking now, and the reason part
of it is time-limited.**

- **The lifts are exemplary and still cannot self-update.** There are 67
  cross-repo path references to the siblings here, 18 of them carrying a line
  number (13 in source files)
  (`src/woodshed/cli.py:69`, `server.py:134`, `library.py:28,48`,
  `tempofit.py:10`, ...). Every one is careful, reasoned and
  correctly attributed. None of them can tell you when the original moves, and
  no test spans the boundary.

- **One fact had already arrived here wrong, and is now fixed.** `docs/
  05-foot-control.md` used to repeat `rambass-live`'s program-map claim — *"a
  PC number does not name a memory"* — and call its `CC#0 → CC#32 → PC`
  ordering correct, inferred from a 2023 gig project's Reaper MIDI items and
  never checked against the unit. The `gx100` repo tested it directly on
  2026-09-06 and found both wrong: `PC n` is a plain identity (no `PROGRAM
  MAP` indirection), and that CC pair wedged the pedal until its power was
  pulled. `docs/05-foot-control.md` now carries the corrected, hardware-
  verified version (a bare Program Change, range 0–127, never a Bank Select
  pair) — the kind of drift this whole section is about, caught here because
  someone finally had the unit in hand, not because a test spanned the
  boundary.

- **`songs/` is empty, and that is the clock.** The proposal's shared song
  identity is free to adopt today and becomes a three-way migration once this
  repo and `gx100` are populated. Nothing else in the proposal is time-limited.

What this repo would give the others: `web/tokens.css` is the only real design
system across the three, and it is portable as-is.

**None of that is decided.** The proposal's own "Reasons to say no" section
argues the other side — including this repo's own roadmap warning about
"practising the tool instead of the guitar", which applies to a unification
project more than to anything else. Declining is a legitimate outcome. Do not
cite anything from it as settled.
