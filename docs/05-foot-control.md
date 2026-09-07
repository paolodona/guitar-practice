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

## Two things that follow, and are worth building

### Reading the patch changes

`MEMORY MIDI` makes the pedal transmit a Program Change **when you switch
memories**. So Woodshed can watch that and:

* show which patch you are on, next to the section's declared `patch:` — the one
  from `gx100/songs/<slug>/song.yaml` — and say when they disagree;
* optionally **follow** it: switching to your lead patch jumps to the solo
  section. Off by default, because it is delightful once and infuriating if you
  ever switch patches for any other reason.

Note the gotcha `rambass-live/docs/gx100.md` already documents: **a PC number
does not name a memory.** It names a slot, and the pedal's own `PROGRAM MAP`
decides which of the 300 memories that slot points at. If you want the display
to say `U02-3`, the mapping has to come from `config/gx100.yaml` in the
`rambass-live` repo, not from an assumption.

> **CONTESTED, 2026-09-06 — do not act on either version yet.**
> `docs/08-unification.md` reports that `gx100` tested this **on the unit** and
> found the paragraph above wrong twice over: that `PC n` is a plain identity
> (no `PROGRAM MAP` indirection to resolve), and that the `CC#0 → CC#32 → PC`
> ordering below **wedged the pedal until its power was pulled**.
> That is a second-hand report here — this repo has no pedal and cannot check it
> — so both readings are on the record and neither is settled. Two consequences
> while it stays that way: the sending half (plan Group N2) must not be built to
> send that CC pair at all until someone re-verifies it at the unit, which is one
> more reason `config.gx100.send_program_changes` defaults to false; and if the
> plain-identity finding holds, N2 gets *simpler*, because there is no mapping to
> import from anywhere. Settle it in the same sitting as the three questions
> above.

### Sending them

The reverse is just as useful and cheaper to build: Woodshed can **send** a Bank
Select + Program Change when you enter a section, so the sound changes with the
part. `rambass-live/src/rambass/gx100.py` already builds those messages
correctly, including the CC#0 → CC#32 → PC ordering and the 0–2 bank limit —
**but see the contested note above before sending any of it**: that same ordering
is reported to have wedged the pedal.

This is where the three repos finally close the loop: `gx100` designs the patch,
`rambass-live` maps it to a memory and a bar, and `woodshed` puts it under your
foot while you learn the part.

**Off by default, and behind an explicit toggle.** Sending program changes
alters your pedal's state. That is the same instinct as `gx100`'s
edit-buffer-only rule — a practice tool should not silently change the thing you
are about to play a gig on.
