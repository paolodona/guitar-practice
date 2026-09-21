/**
 * metronome.js — a click on the band's own beats, scheduled live.
 *
 * The toggle on the practice screen. Its whole reason for existing is
 * practising at 40%, or against the isolated guitar, where the other
 * instruments are either smeared or simply not there and there is nothing
 * left to keep time against.
 *
 * ---- Where the beats come from ----
 * `GET /api/beats/<slug>/<section>` — a list of SOURCE seconds, fitted per
 * section by `src/woodshed/beatfit.py`. Not `tempo.bpm` + `grid_offset_s`:
 * that is one tempo for a whole recording, and a commercial take is not
 * one tempo. MEASURED on tutti-in-fila — the stored 116.04 against
 * sections that fit 116.09 / 116.59 / 117.42, so a grid extended from the
 * song's own t=0 arrives at the solo about 0.8s late, which is a click you
 * cannot play to. `beatfit.py`'s module doc carries the rest.
 *
 * ---- Why the click is scheduled and not rendered ----
 * Three reasons, and the first is the strongest:
 *
 *  1. A click baked into audio is unremovable — `rambass-live/src/rambass/
 *     click.py` opens with that rule and it is the one mistake in this
 *     area that cannot be undone later. The render cache is the audio.
 *  2. It has to toggle instantly. A rendered click would mean a second
 *     cache entry per (section, speed, shift), built on the first press.
 *  3. Speed changes then cost nothing: the beats are in source seconds and
 *     only the CLOCK changes, so the same list re-times itself for free at
 *     every rung. Nothing is re-fetched and nothing is re-fitted.
 *
 * ---- Why it can be sample-accurate anyway ----
 * `BufferEngine` plays a decoded buffer with a native, sample-exact loop
 * (docs/03-audio-engine.md, trap 3), and a native loop fires no boundary
 * event — the browser splices in the audio thread and tells nobody. So the
 * position is arithmetic (`player.playbackPositionAt`), and every click is
 * scheduled ahead on the SAME AudioContext clock the music is playing on,
 * in a 100ms/400ms look-ahead loop. The `setInterval` only decides how far
 * ahead the work is done; nothing audible depends on when it runs.
 *
 * On `RealtimeEngine` (the fallback with no render cache) there is no
 * honest position at all — see screens/practice.js's decision 3 — so the
 * metronome refuses rather than clicking confidently in the wrong place.
 *
 * ---- What it deliberately does NOT do ----
 * No accent on beat 1. `beatfit` measures the phase of the pulse, which is
 * a real measurement; WHICH beat is bar 1 beat 1 is a musical question it
 * cannot answer (`tempofit.find_grid_anchor` says so in as many words),
 * and an accent in the wrong place is worse than no accent. This is the
 * same rule as CLAUDE.md's "the tool never judges the playing": do not
 * invent a measurement the tool cannot make.
 *
 * This is NOT the count-in overlay removed in #5. There is no overlay, no
 * pre-count, no server-rendered WAV, and nothing plays before the music —
 * the pre-roll audio the render already contains is untouched, and the
 * click simply also plays over it.
 */

/**
 * Beat positions in SOURCE seconds -> PLAYBACK seconds in the rendered,
 * stretched section. CLAUDE.md's two-clock rule: this is the boundary, and
 * it is the only place in the metronome where the conversion happens.
 *
 * Mirrors `clock.to_playback` exactly — `(t - (start_s -
 * effective_pre_roll_s)) / speed` — with `clock.preRollS` already the
 * EFFECTIVE (clamped) lead-in, which is what `player.renderClock` returns.
 *
 * A beat earlier than the rendered lead-in comes back negative rather than
 * clamped: it names audio that was never rendered, and the scheduler drops
 * it. Clamping would pile those beats onto sample 0 as a flam.
 *
 * @param {number[]} beatsSourceS
 * @param {{startS: number}} section
 * @param {{speed: number, preRollS: number}} clock - player.renderClock()'s
 * @returns {number[]} playback seconds, same order
 */
export function beatsToPlayback(beatsSourceS, section, clock) {
  const origin = section.startS - clock.preRollS;
  return beatsSourceS.map((t) => (t - origin) / clock.speed);
}

/**
 * Double (or otherwise subdivide) a fitted beat list WITHOUT re-fitting —
 * Paolo, 2026-09-12: a slow section (55bpm) makes a distant click hard to
 * lock onto, and the fix is not a second, faster fit. `beatfit.py` already
 * measured the true pulse; a 2x click is just that same pulse subdivided,
 * so it stays exactly in phase with the 1x click and costs nothing to
 * toggle (no fetch, no re-fit, same list of SOURCE seconds retimed).
 *
 * Only the INTERNAL gaps get a midpoint — the gap from the last fitted beat
 * back to the first one, across the loop wrap, is not (that wrap is
 * `clicksInWindow`'s job, and it does not have the beat interval to hand);
 * one un-subdivided gap per lap is a rare, minor loss, not a wrong click.
 *
 * @param {number[]} beatsSourceS
 * @param {number} factor - 1 leaves the list untouched
 * @returns {number[]} source seconds, ascending, same order as the input
 */
export function subdivideBeats(beatsSourceS, factor) {
  if (!beatsSourceS || beatsSourceS.length === 0) return [];
  if (!Number.isInteger(factor) || factor <= 1) return beatsSourceS.slice();
  const out = [];
  for (let i = 0; i < beatsSourceS.length; i++) {
    out.push(beatsSourceS[i]);
    if (i + 1 < beatsSourceS.length) {
      const a = beatsSourceS[i];
      const b = beatsSourceS[i + 1];
      for (let k = 1; k < factor; k++) out.push(a + ((b - a) * k) / factor);
    }
  }
  return out;
}

/**
 * Which beats fall between two AudioContext times, and exactly when.
 *
 * Playback is at `position` (playback seconds) at AudioContext time
 * `ctxNow`, and advances in real time, wrapping from `clock.loopEnd` back
 * to `clock.loopStart` for as long as the window lasts. Returns the
 * absolute AudioContext times of every beat strictly after `from` and at
 * or before `to`.
 *
 * `from` is normally the previous call's `to`, which is what stops a beat
 * being scheduled twice — and a double-scheduled beat is not a harmless
 * duplicate: two nodes a few milliseconds apart is an audible flam.
 *
 * Everything outside `[loopStart, loopEnd)` is skipped on a wrapped lap,
 * which is what makes the lead-in click once (on the first pass, from
 * wherever playback actually starts) and not on every lap — unless
 * `pre_roll_every_pass` moved `loopStart` to 0, in which case it clicks
 * every lap, for free, because that is what the audio does too.
 *
 * @param {{beats: number[], clock: {loopStart: number, loopEnd: number, lap: number},
 *          position: number, ctxNow: number, from: number, to: number}} args
 * @returns {number[]} AudioContext times, ascending
 */
export function clicksInWindow({ beats, clock, position, ctxNow, from, to }) {
  const out = [];
  if (!beats || beats.length === 0 || to <= from) return out;

  const { loopStart, loopEnd } = clock;
  const lap = loopEnd - loopStart;
  let pos = position;
  let clockTime = ctxNow;
  // A degenerate loop region (lap <= 0) can still schedule the first
  // segment; it just cannot wrap, which is what this bound enforces
  // without a special case. 512 laps is far past any real look-ahead.
  const MAX_SEGMENTS = 512;

  for (let segment = 0; segment < MAX_SEGMENTS && clockTime <= to; segment++) {
    const segmentEnd = loopEnd;
    if (segmentEnd <= pos) break;
    for (const beat of beats) {
      // Strictly after the current position on the pass already in
      // flight (a beat level with `position` has just gone by), but AT or
      // after it on every wrapped lap: playback lands exactly on
      // `loopStart`, and a beat sitting exactly there is the first beat
      // of the section -- the single most important click of the lap, and
      // silently missing from every lap but the first until a test went
      // looking for it. Nothing double-schedules it: the `from`/`to`
      // bounds below are what stop that, on any lap.
      if (segment === 0 ? beat <= pos : beat < pos) continue;
      if (beat > segmentEnd) continue;
      // Only the first segment can contain beats before loopStart: those
      // are the lead-in, and playback is inside it only on a first pass.
      const at = clockTime + (beat - pos);
      if (at > from + 1e-9 && at <= to + 1e-9) out.push(at);
    }
    clockTime += segmentEnd - pos;
    pos = loopStart;
    if (lap <= 0) break;
  }
  return out.sort((a, b) => a - b);
}

/**
 * How far ahead clicks are handed to Web Audio, and how often. The audio
 * thread honours the times; this only bounds how late a NEWLY correct
 * schedule can be applied after a speed change or a seek.
 * ("A Tale of Two Clocks" — the same shape every Web Audio metronome uses.)
 */
const LOOKAHEAD_S = 0.4;
const TICK_MS = 100;

/** Click level. Was -9dBFS (0.35), the same level `rambass-live`'s own
 *  rehearsal click renders at; raised to -6dBFS, Paolo, 2026-09-12: at -9 it
 *  was getting lost against the music, not just quiet against it. Still a
 *  sine with a little second harmonic, which cuts through a slowed, smeared
 *  mix without having to be loud — this only moves how loud "without being
 *  loud" is allowed to mean. */
const DEFAULT_GAIN = 0.5;

/** The click's voice. Mirrors `rambass-live/src/rambass/click.py`'s `_tick`
 *  — a sine at 1kHz plus 0.3 of its second harmonic under a fast
 *  exponential decay — reimplemented rather than imported, because that one
 *  renders a WAV in numpy and this one fills an AudioBuffer, and CLAUDE.md's
 *  rule for the sibling repos is cross-reference, never copy. */
const CLICK_HZ = 1000;
const CLICK_MS = 28;

/**
 * Build the click as a tiny AudioBuffer, once per context. A buffer rather
 * than an oscillator pair per beat: identical every time (so a metronome
 * cannot develop a character), and one node to start instead of three to
 * start, ramp and stop.
 * @param {BaseAudioContext} ctx
 */
export function clickBuffer(ctx) {
  const frames = Math.max(4, Math.round((CLICK_MS / 1000) * ctx.sampleRate));
  const buffer = ctx.createBuffer(1, frames, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < frames; i++) {
    const t = i / ctx.sampleRate;
    const envelope = Math.exp(-t * (4000 / CLICK_MS));
    data[i] = (Math.sin(2 * Math.PI * CLICK_HZ * t)
      + 0.3 * Math.sin(4 * Math.PI * CLICK_HZ * t)) * envelope;
  }
  return buffer;
}

/**
 * The click, riding an engine's own clock.
 *
 * Deliberately knows nothing about fetching, toggling or the screen: it is
 * handed beats (source seconds), a section and a clock, and something that
 * can say where playback is. screens/practice.js owns the rest.
 */
export class Metronome {
  /**
   * @param {BaseAudioContext} ctx - the ENGINE's context, never a second
   *   one: two contexts have two clocks and no way to align them.
   * @param {() => (number | null)} positionFn - playback seconds, or null
   *   when playback is not running or the engine cannot say (the real-time
   *   engine, which never can).
   */
  constructor(ctx, positionFn) {
    this.ctx = ctx;
    this._position = positionFn;
    this._gain = ctx.createGain();
    this._gain.gain.value = DEFAULT_GAIN;
    this._gain.connect(ctx.destination);
    this._buffer = clickBuffer(ctx);
    /** @type {number[]} playback seconds */
    this._beats = [];
    /** @type {any} */
    this._clock = null;
    this._timer = null;
    this._scheduledUntil = 0;
    /** Nodes already handed to the audio thread, so a resync can silence
     *  the ones that have not sounded yet. */
    this._nodes = new Set();
  }

  /** Whether there is anything to click. */
  get ready() { return this._beats.length > 0 && this._clock !== null; }

  /**
   * Point the click at a section: `beats` in SOURCE seconds, converted
   * here and held in playback seconds.
   * @param {number[]} beatsSourceS
   * @param {{startS: number}} section
   * @param {{speed: number, preRollS: number, loopStart: number, loopEnd: number, lap: number}} clock
   * @param {number} [multiplier] - 1 (the fitted pulse) or 2 (subdivided,
   *   for a slow section where the plain click is too far apart to lock
   *   onto — see subdivideBeats' own doc). Never re-fits; the beats are the
   *   same measurement either way.
   */
  setBeats(beatsSourceS, section, clock, multiplier = 1) {
    this._clock = clock;
    const effective = subdivideBeats(beatsSourceS || [], multiplier);
    this._beats = beatsToPlayback(effective, section, clock)
      .filter((t) => t >= 0 && t <= clock.loopEnd);
    this.resync();
  }

  /**
   * Re-time against a clock that has changed — a new rung, a new shift, a
   * different source. The BEATS do not change (they are source seconds);
   * only where they land does, which is the whole economy of scheduling
   * the click instead of rendering it.
   * @param {any} clock - player.renderClock()'s, for the new speed
   * @param {{startS: number}} section
   * @param {number[]} beatsSourceS
   * @param {number} [multiplier]
   */
  retime(beatsSourceS, section, clock, multiplier = 1) {
    this.setBeats(beatsSourceS, section, clock, multiplier);
  }

  /** Start clicking. Idempotent. */
  start() {
    if (this._timer !== null) return;
    this._scheduledUntil = this.ctx.currentTime;
    this._tick();
    this._timer = setInterval(() => this._tick(), TICK_MS);
  }

  /** Stop clicking, and silence anything already scheduled. */
  stop() {
    if (this._timer !== null) {
      clearInterval(this._timer);
      this._timer = null;
    }
    this.resync();
  }

  /** Drop every click that has not sounded yet and re-arm from now.
   *  Called whenever the mapping from playback time to wall time moves:
   *  a pause, a seek, a rung swap, a source swap. */
  resync() {
    for (const node of this._nodes) {
      try { node.stop(); } catch { /* already finished; nothing to stop */ }
    }
    this._nodes.clear();
    this._scheduledUntil = this.ctx.currentTime;
  }

  /** Release the graph. The context belongs to the engine and is left alone. */
  destroy() {
    this.stop();
    try { this._gain.disconnect(); } catch { /* already gone */ }
  }

  _tick() {
    if (!this.ready) return;
    const position = this._position();
    if (position === null || position === undefined) {
      // Not playing (or an engine that cannot say): schedule nothing, and
      // keep the horizon at now so resuming does not replay a backlog.
      this._scheduledUntil = this.ctx.currentTime;
      return;
    }
    const now = this.ctx.currentTime;
    const from = Math.max(now, this._scheduledUntil);
    const to = now + LOOKAHEAD_S;
    if (to <= from) return;
    const times = clicksInWindow({
      beats: this._beats, clock: this._clock, position, ctxNow: now, from, to,
    });
    for (const at of times) this._fire(at);
    this._scheduledUntil = to;
  }

  _fire(at) {
    const node = this.ctx.createBufferSource();
    node.buffer = this._buffer;
    node.connect(this._gain);
    node.onended = () => this._nodes.delete(node);
    node.start(at);
    this._nodes.add(node);
  }
}
