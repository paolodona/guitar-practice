# Original Prompt: Instant playback on the practice screen while the cached render builds
Plan: 003 | Name: instant-practice-playback | Created: 2026-09-21 | GitRef: c6f9261

## Related Files
- **Context**: `.agent_session/003_instant-practice-playback_context.md` - Research findings
- **Plan**: `.agent_session/003_instant-practice-playback_plan.md` - Implementation steps

---

## Original User Request

> I am starting to practice this: http://127.0.0.1:8420/#/practice/for-my-grana/whole-song,
> which starts at 50%. When I first load the page the playhead starts but there is no music
> as it's "Rendering at 50% speed" in the footer. This really bugs me, the song page
> (http://127.0.0.1:8420/#/song/for-my-grana) is well able to play the song at 50% speed
> (albeit at lower quality) without any delay. There is no reason the section page cannot
> do the same while the higher quality rendering is done in the background.

## Refined scope (confirmed via AskUserQuestion)

1. Whenever the render `BufferEngine` needs isn't cached, start audible playback
   immediately on `RealtimeEngine` at the requested speed/semitones/source, while the
   render builds in the background.
2. Once it lands, swap to `BufferEngine`'s sample-exact native loop with a **hard cut at
   the next loop boundary** — not a crossfade between the two different node graphs.
3. This applies everywhere a render isn't cached, not just the first section load — also
   a mid-session speed_up/speed_down/transpose press or a Guitar-only toggle landing on
   an uncached combo. This replaces today's "the old buffer render keeps playing while
   the new one builds" behavior for that specific case.

Scope: `web/player.js` and `web/screens/practice.js` (plus their tests) only — no
server-side (Python) change; `GET /api/render/...`'s 200/202 contract is unchanged.
