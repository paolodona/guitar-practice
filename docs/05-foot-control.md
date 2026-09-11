# Hands-free: driving Woodshed from the GX-100

The requirement is *"practise a solo on loop without taking my hands off the
guitar"*. The pedal already under your feet can do it.

## What the GX-100 transmits — from Roland, verified

Read out of `gx100/docs/reference/GX-100_GX-10_MIDI_Imple_eng02_W.pdf` (Roland,
*MIDI Implementation*, Model GX-100 / GX-10, ver. GX-100 2.04, dated
2026-03-01), section **2. Transmitted data**:

> **(-) Control Change Number #0 - #127**
> `BnH ccH vvH`
> \* When you operate a controller, the CC# specified by
> `MENU:MIDI:MIDI SETTING:NUM1 CC# - EXP2 CC#` is transmitted.
> \* You can send the CC# messages by setting ASSIGN.

> **(\*) Program Change** — `CnH ppH`
> \* Depending on the setting of `MENU:CONTROL/ASSIGN:MEMORY MIDI`, this message
> is transmitted when you switch memories.

> **(\*) Start / Stop** — `FAH` / `FCH`
> \* Transmitted at the time of controller operation when
> `MENU:CONTROL ASSIGN:CONTROL FUNCTION:***` is set to `MIDI START`.

Also transmitted: Timing Clock, when `SYNC CLOCK = INTERNAL` and `CLOCK OUT = ON`.
The transmit channel is `MENU:MIDI:MIDI SETTING:TX CHANNEL`.

So: **each footswitch and expression pedal can send a CC of your choosing**, over
USB, to whatever is listening. That is a six-button foot controller you already
own, and this is the feature that makes the whole tool work the way it is
supposed to.

Three things are still eyes-on-the-unit questions, in the sense
`gx100/CLAUDE.md` uses — settle them at the pedal in one sitting and note the
answers back into this file:

1. Exactly which controllers `NUM1 CC#` … `EXP2 CC#` covers on **this** unit at
   firmware 2.05, and what the menu calls each one.
2. Whether a CC can be sent **without** the footswitch also changing a patch —
   i.e. whether a switch can be dedicated to MIDI only, or whether every press
   also does something to your sound.
3. Whether the expression pedal's continuous CC is smooth enough, and cheap
   enough in messages, to be a usable speed knob.

Until they are answered, assume yes to (1), unknown to (2) and (3). If (2) turns
out to be no, the fallback is a €40 dedicated USB MIDI foot controller and the
app does not care which device sent the CC.

## Web MIDI, and the browser constraint

The browser reads it with the **Web MIDI API** — `navigator.requestMIDIAccess()`,
no `sysex`, one permission prompt the first time, then it is remembered.

**Chrome or Edge.** Firefox supports Web MIDI behind a per-site permission;
Safari does not support it at all. This is the one hard reason the app names a
browser. Say so in `doctor` and on the settings screen rather than letting the
foot control mysteriously not work.

The device is matched by substring on the port name (`config.midi.input`), so a
different USB port or a re-plug does not break it, and a hot-plug is picked up by
`onstatechange` without a reload.

## The default map

Six actions is the whole vocabulary. Resist a seventh.

| CC | action | when you use it |
|---|---|---|
| 80 | **play / pause** | between attempts |
| 81 | **next section** | done with this one |
| 82 | **previous section** | that was too far |
| 83 | **speed up** one ladder step | it is easy now |
| 84 | **speed down** one ladder step | it is not |
| 85 | **retract last rep** | that pass was rubbish, do not count it |

A CC counts as a press on any value ≥ 64 (momentary switches send 127/0; some
send 127 only). Debounce 150 ms. An expression pedal, if you dedicate one, maps
continuously to speed across the ladder's range and snaps to steps on release.

**One action table, three input devices.** MIDI, keyboard and mouse all dispatch
the same named actions (`play_pause`, `next_section`, …). Never let the MIDI path
grow its own logic — the moment "speed up" means something slightly different
from a pedal than from the `↑` key, both are wrong.

## Two things that follow

### Reading the patch changes — not yet built

`MEMORY MIDI` makes the pedal transmit a Program Change **when you switch
memories**. So Woodshed can watch that and:

* show which patch you are on, next to what the song's own `patch_changes`
  timeline (#3) says should be active at this position, and say when they
  disagree;
* optionally **follow** it: switching to your lead patch jumps to the solo
  section. Off by default, because it is delightful once and infuriating if you
  ever switch patches for any other reason.

`rambass-live/docs/gx100.md`'s own model — a PC number names a *slot*, resolved
through the pedal's `PROGRAM MAP` to one of 300 memories — was inferred from a
2023 gig project's Reaper MIDI items, never re-verified against the unit
itself. It turned out wrong for this hardware.

> **RESOLVED, 2026-09-06 — `gx100` tested it on the unit; trust that over the
> inference.**
> `gx100/docs/protocol/unknowns.md` #28 and `cc-map.md` carry the full story:
> `PC n` loads memory `n` directly — **a plain identity, no `PROGRAM MAP`
> indirection at all** — confirmed by watching the display follow ten
> alternating changes. Two conditions gated every earlier failure, both now
> pinned down: the pedal must be on its **play screen** (not a `MENU`), and
> `MAP SELECT` must be `FIX`, not `PROG`.
>
> **Bank Select is not just unnecessary here, it is dangerous**: sending
> `CC#0`/`CC#32` — exactly what the 2023 gig project's own MIDI items did —
> left the unit unresponsive to SysEx until its power was pulled. A bare
> Program Change alone never did that. **Never send CC#0 or CC#32 to this
> pedal.**
>
> The real cost of skipping Bank Select: a plain Program Change is a 7-bit
> value, so it can only address memories **0–127** (`U01-1` through `U32-4`).
> Everything from `U33-1` onward, and every `P`-bank preset, is unreachable
> this way. `gx100`'s own SysEx-based `select_patch` has no such ceiling and
> doesn't care what screen the unit is on — but SysEx is a bigger step
> (`web/midi.js` deliberately opens with `{sysex: false}` today) and is not
> what this section builds. Keep every gig patch this feature will switch to
> inside the first 128 memories, or extend to SysEx later.

### Sending them — built (#3, Group N2)

Woodshed **sends** a bare Program Change when you enter a section, so the
sound changes with the part.

* **`song.yaml`'s `patch_changes:`** is a song-level timeline — `at_s` (a
  position in the immutable recording, CLAUDE.md's seconds-not-bars
  invariant) plus `patch` (a memory name, `U01-1`..`U32-4`). "The patch for
  section `[X, Y)`" resolves to the last entry with `at_s <= X`, falling
  back to `U01-1` when the list is empty or nothing qualifies
  (`web/gx100.js`'s `resolvePatchAt`, mirrored by `src/woodshed/gx100.py`'s
  `memory_to_index`/`index_to_memory` — see docs/02-data-model.md for the
  exact shape). Set from the song screen's own patch-change lane (a thin
  strip under the waveform: click empty space to drop one, click an
  existing dot to change or delete it), committed through
  `POST /api/patch-change`.
* **`config/gx100.yaml`** is the local, human-maintained patch-name list
  the lane's popup offers as suggestions (`memory`/`name` pairs, plus the
  MIDI `channel` Woodshed sends on — must match the pedal's own RX
  CHANNEL) — read by `GET /api/gx100/patches`, and re-read on demand by the
  popup's own refresh icon. Replaces the sibling-repo cross-reference
  (Phase 3, N1) entirely: "one song-level timeline of program changes
  replaces N-per-section free text."
* Both the song screen's preview and the practice screen's section load
  resolve the applicable patch and send it, gated on
  `config.gx100.send_program_changes` (default `false`) — checked inside
  `sendProgramChange` itself, the one place that calls `output.send()`, so
  no future call site can bypass it. A bare `0xC0`, never a Bank Select
  pair, range-checked to 0–127 before anything goes out.

This is where the three repos finally close the loop: `gx100` designs the
patch and confirms what the wire protocol actually is, `rambass-live` showed
the shape of the problem first (and got the wire protocol wrong, harmlessly,
since nothing here ever sent its CC pair), and `woodshed` puts a plain PC
under your foot while you learn the part.

**Off by default, and behind an explicit toggle.** Sending program changes
alters your pedal's state. That is the same instinct as `gx100`'s
edit-buffer-only rule — a practice tool should not silently change the thing
you are about to play a gig on. `MAP SELECT: FIX` and the play screen stay a
discipline this tool cannot verify for you (`gx100`'s own `select_patch` note:
"does not depend on what the unit is showing" is the *SysEx* method's
advantage, not this one's) — the same way a manual PROGRAM MAP alignment used
to be, just for a different reason now.
