# Original Prompt: Loudness matching across songs
Plan: 004 | Name: loudness-matching | Created: 2026-09-22 | GitRef: 9f46103

## Related Files
- **Context**: `.agent_session/004_loudness-matching_context.md` - Research findings
- **Plan**: `.agent_session/004_loudness-matching_plan.md` - Implementation steps

---

## Original User Request

> I also think we should change the program to rescale all audio to loudness
> match. I cannot guarantee all inputs will be the same and I do not want to
> fiddle with volume levels when practicing

This followed directly from capturing "snow" (a live loopback capture) and
noticing its waveform sat well below full scale (measured live: -21.6 dBFS
peak) next to normally-mastered songs already in the library, with the
follow-up "shouldnt that happen automatically on import" (asked about tempo
auto-detection at the time, but framing the same automatic-by-default
expectation this feature follows).

## Refined Prompt

Three decisions were made interactively with Paolo (via AskUserQuestion)
before the plan was written, and are binding on implementation:

1. **Gain is applied at playback time only** — never baked into
   `audio/*.flac` or the render `cache/`. Both stay bit-exact transforms of
   the source.
2. **Loudness is measured automatically as part of `analyze_after_bind`**,
   the same moment tempo is auto-detected, so a captured/bound song gets it
   for free with no separate command.
3. **Pure-numpy K-weighted integrated loudness** (ITU-R BS.1770-style), no
   new dependency — mirrors this repo's existing `beatfit.py`/`tempofit.py`
   precedent of hand-implementing DSP instead of adding a library, and
   avoids the exact "silently skipped because a package was missing" bug
   that had just been found and fixed for tempo (librosa was promoted from
   an extra to a core dependency in this same session, after "snow" landed
   with `bpm: 0.0`).
4. **Target -16 LUFS integrated, -1 dBTP peak ceiling.**
5. **Both playback surfaces** (practice screen's cached loop, song page's
   preview/audition transport) get the gain — not just one.
