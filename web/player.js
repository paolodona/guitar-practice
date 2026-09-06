/**
 * player.js — the real-time WASM engine and the pass-detection contract.
 * D4's file (RealtimeEngine's body); this run folds what the plan calls
 * D7 into D4, so no separate unit builds pass detection as its own file.
 * BufferEngine (Phase 2's cache-backed engine) is stubbed only — not built
 * this phase; see CLAUDE.md's "Practice speeds are discrete" invariant and
 * the plan's "a stated exception, not a silent breach" note.
 *
 * ---- The pass-detection contract, and who owns which half ----
 * A pass counts when playback reaches the section end having started
 * within 250ms of the section's beginning, with no seek and no pause in
 * between (plan, ~lines 916-923). RealtimeEngine — D4 — owns detecting
 * this against AudioContext.currentTime and fires it; screens/practice.js
 * — D6 — is a LISTENER ONLY:
 *
 *     engine.addEventListener('pass', (e) => { ...POST /api/rep... })
 *
 * D6 must not re-derive or second-guess "was this a pass" from its own
 * timers — the whole reason this split exists is so exactly one piece of
 * code decides that, and D4/D6 built independently (per D0's contract)
 * must not quietly duplicate or disagree about who owns it. `pass` fires
 * once per satisfying loop boundary, never once per raw timeupdate.
 *
 * How that boundary was observed through Phase 1: this engine had no
 * arbitrary seek/scrub method on its public surface (only play/pause/
 * restartSection), so every lap it played started at exactly one of two
 * positions: sample 0 of the loaded section (pre-roll included, on load
 * or restart) or `loopStartFrame` (every natural loop wrap, produced by
 * web/vendor/rubberband/worklet.js, which owns the source samples and
 * reports each wrap with the worklet's own `currentTime` — not
 * `performance.now()`, which does not exist inside AudioWorkletGlobalScope).
 * Both of those starting positions were exactly the section's beginning,
 * not merely close to it, so "started within 250ms" was structurally
 * satisfied rather than measured against a tolerance window.
 *
 * Phase 1.5's P1 adds seek(sourceSeconds) (waveform click-to-seek,
 * screens/practice.js) and that is no longer true — a seek can land
 * anywhere in the loaded slice, not only at the two qualifying positions.
 * seek() therefore disqualifies the in-flight lap exactly like pause()
 * does (this is PASS_START_TOLERANCE_S's reserved purpose, now live: see
 * the constant below), and relies on the SAME re-qualification _onBoundary()
 * already does for a post-pause resume — a fresh natural wrap re-arms the
 * next lap regardless of why the previous one was disqualified. No change
 * to _onBoundary() itself was needed for this; the state machine already
 * generalizes. What genuinely needs tracking, unchanged: pause() and now
 * seek() disqualify whatever lap is in flight; a fresh natural wrap
 * re-qualifies the next one. See _onBoundary() below.
 *
 * ---- Units ----
 * setSpeedPct takes a PERCENT (50.0 = 50%) — CLAUDE.md: "speed is a
 * percent everywhere except clock.Render.speed", and Render's fraction
 * form is server-side only. This file is entirely on the percent side of
 * that boundary and must never see or produce the 0.4-1.0 fraction.
 *
 * ---- Implementation shape ----
 * The actual AudioWorkletProcessor lives at
 * web/vendor/rubberband/worklet.js — a worklet can only be loaded from
 * its own script URL (audioWorklet.addModule), never imported here as a
 * normal ES module, and it is documented in full there (message
 * protocol, the pull-driven feed loop, the start-of-stream warm-up
 * drop). This file: decodes the section's audio, slices the span the
 * worklet needs, drives that message protocol, and turns the worklet's
 * loop-boundary reports into (or withholds) the 'pass' event.
 */

/**
 * @typedef {Object} SectionLoad
 * @property {string} sectionId
 * @property {string} audioUrl - GET /api/audio/<slug>, range-served (see
 *   server.py's _audio / _send_file — seekable via byte-range requests)
 * @property {number} startS - source seconds, section start (pre-roll NOT
 *   included — clock.py's Render.start_s)
 * @property {number} endS - source seconds, section end (Render.end_s)
 * @property {number} preRollS - source seconds of lead-in rendered ahead
 *   of startS (Render.pre_roll_s)
 * @property {boolean} [preRollEveryPass] - Phase 1, G2
 *   (clock.Render.pre_roll_every_pass, mirrored). false (default): the
 *   lead-in plays once, on load/restart, and every natural wrap loops from
 *   AFTER it (loopStartFrame = preRollS in sample frames). true: every
 *   wrap loops from sample 0 instead, replaying the lead-in each pass.
 * @property {boolean} [loop] - Phase 1.5, R1. true (default): loops
 *   forever, fires 'pass' — practice.js's engine. false: plays through
 *   once and fires 'ended' instead of wrapping — screens/song.js's plain
 *   preview player. Never fires 'pass' by construction (see worklet.js's
 *   own module doc): this is the mechanism that lets that screen's
 *   transport never write a rep, not a check either file has to remember.
 * @property {number} [clipOffsetS] - Phase 1.5, S3. 0 (default): audioUrl
 *   is the whole recording (GET /api/audio/<slug>), and startS/endS/
 *   preRollS already are absolute positions into it — the ordinary case.
 *   Non-zero: audioUrl is instead a CLIP that starts `clipOffsetS` seconds
 *   into the original recording (GET /api/stem/<slug>/<section>, the
 *   isolated-guitar clip `separate.isolate_guitar` cuts as
 *   `[start_s - pre_roll_s, end_s]` — so callers pass
 *   `clipOffsetS = max(0, section.start_s - preRollSourceSeconds)`,
 *   the same clamp `isolate_guitar` itself applies).
 *   `computeSliceFrames` (below) is the ONE place this offset is
 *   subtracted before slicing and added back before being stored as
 *   `_sliceStartS`/`_sliceEndS` — `seek(sourceSeconds)` takes an ABSOLUTE
 *   source-seconds position (the waveform's own coordinate, unaffected by
 *   which physical file backs playback), so those two fields must stay in
 *   that same absolute clock regardless of `clipOffsetS`, or a click on
 *   the waveform while playing the isolated clip would seek to the wrong
 *   place. This is CLAUDE.md's "two clocks" invariant, applied to a THIRD
 *   file-local clock (clip-relative frame position) rather than a second
 *   named one — same rule, same discipline: convert once, at this one
 *   boundary, never re-derive it elsewhere.
 */

/**
 * The one function that knows how a SectionLoad's absolute source-second
 * bounds become frame indices into a buffer that may itself start
 * `clipOffsetS` seconds into the original recording — see the typedef
 * above. Pure and exported so it can be tested directly (this repo has no
 * DOM library to drive a real decodeAudioData/AudioWorkletNode through —
 * same trade `computeSeekPosition` in screens/practice.js already made).
 * `decodedLength` is the fetched buffer's own frame count, needed to clamp
 * `endFrame` the same way `loadSection` always has.
 * @param {SectionLoad} section
 * @param {number} sr
 * @param {number} decodedLength
 */
export function computeSliceFrames(section, sr, decodedLength) {
  const offsetS = section.clipOffsetS || 0;
  // [start_s - pre_roll_s, end_s] in SOURCE seconds -> sample indices,
  // re-based onto the fetched buffer's own t=0 (offsetS seconds later
  // than the recording's own t=0) — CLAUDE.md's two-clock rule: everything
  // in this line starts as source time, converted to slice-relative here
  // and nowhere else.
  let rawStartFrame = Math.round((section.startS - section.preRollS - offsetS) * sr);
  let loopStartFrame = Math.round(section.preRollS * sr);
  if (rawStartFrame < 0) {
    // The pre-roll would reach before the start of whatever was fetched
    // (the source file's own start when offsetS is 0; the isolated clip's
    // own start otherwise — isolate_guitar clamps identically server-side,
    // so this is the expected case for a guitar-only clip, not only an
    // edge case). Clamp the slice to what actually exists and shrink the
    // lead-in played to match, rather than reading negative indices or
    // silently moving the section's start to compensate.
    loopStartFrame += rawStartFrame; // rawStartFrame is negative here
    rawStartFrame = 0;
  }
  if (section.preRollEveryPass) {
    // G2: every wrap replays the lead-in, so it always loops from the
    // very start of what was sliced -- overrides whatever the "skip the
    // pre-roll" computation above landed on, clamped or not.
    loopStartFrame = 0;
  }
  const endFrame = Math.min(Math.round((section.endS - offsetS) * sr), decodedLength);
  return {
    rawStartFrame,
    loopStartFrame,
    endFrame,
    // Converted back to ABSOLUTE source seconds (+offsetS) so seek()'s own
    // coordinate never has to know a clip offset exists at all.
    sliceStartS: rawStartFrame / sr + offsetS,
    sliceEndS: endFrame / sr + offsetS,
  };
}

// tuning.MAX_SHIFT (Python, src/woodshed/tuning.py) mirrored here — this
// file has no way to import a Python module, and the number is a design
// constant ("range-limited to ±6"), not something worth round-tripping
// through an endpoint just to avoid saying 6 twice.
const MAX_SHIFT = 6;

// The ladder runs 50-100% (CLAUDE.md); the slider this engine drives
// during "exploring" is documented as accepting any value in that same
// span (see player.js's stub JSDoc history) rather than only the
// ladder's discrete rungs — discreteness is a cache-rendering property
// (invariant: "practice speeds are discrete, so the renders are"), not a
// restriction on the live real-time engine.
const MIN_SPEED_PCT = 40;
// 110, not 100: Paolo asked to be able to push a section faster than the
// recording on purpose (get comfortable ahead of 100%, then come back down)
// -- found live 2026-09-06. `target_speed`/the ladder's auto-advance still
// stop at 100% (ladder.py's own "nothing advances past target_speed"
// contract, mirrored client-side in screens/practice.js's nextRung()); this
// only widens how far a MANUAL speed_up press can go.
const MAX_SPEED_PCT = 110;

// Phase 1.5, P1: seek() exists now, and the module doc's pass-detection
// contract requires "no seek ... in between" independently of where the
// seek landed — the opening docstring's "and" is deliberate, not "or".
// So this constant is STILL not read in a numeric comparison anywhere:
// disqualifying every seek, unconditionally, is a simpler and correct
// reading of that contract than re-qualifying a seek that happens to land
// within tolerance of the true beginning would be — a click a few pixels
// into the section is still a seek, not a restart. Left defined (not
// deleted) because it is still the number the contract's prose names, and
// because a future "a seek onto exactly loopStartFrame behaves like
// restartSection" refinement — not asked for here — would want it.
const PASS_START_TOLERANCE_S = 0.25;

/**
 * The compiled Rubber Band WASM module, shared across every
 * RealtimeEngine instance in the page — compiling it is real work
 * (parsing 265KB of WASM) and the bytes never change between sections or
 * engines, so there is exactly one compile no matter how many sections
 * get practised in one session.
 * @type {Promise<WebAssembly.Module> | null}
 */
let wasmModulePromise = null;

function getWasmModule() {
  if (!wasmModulePromise) {
    const url = new URL('./vendor/rubberband/rubberband.wasm', import.meta.url);
    wasmModulePromise = fetch(url)
      .then((res) => {
        if (!res.ok) {
          throw new Error(`player.js: fetch rubberband.wasm: ${res.status} ${res.statusText}`);
        }
        return res.arrayBuffer();
      })
      .then((bytes) => WebAssembly.compile(bytes));
  }
  return wasmModulePromise;
}

/**
 * `audioWorklet.addModule()` may be called only once per processor name
 * per BaseAudioContext (a second call throws). Track readiness per
 * context rather than globally, since a caller is free to construct more
 * than one AudioContext (each RealtimeEngine can own its own, or share
 * one passed in).
 * @type {WeakMap<AudioContext, Promise<void>>}
 */
const workletReady = new WeakMap();

function ensureWorkletModule(ctx) {
  let ready = workletReady.get(ctx);
  if (!ready) {
    const url = new URL('./vendor/rubberband/worklet.js', import.meta.url);
    ready = ctx.audioWorklet.addModule(url);
    workletReady.set(ctx, ready);
  }
  return ready;
}

/**
 * The real-time engine: WASM Rubber Band worklet (rubberband-wasm@3.3.0,
 * per docs/03-audio-engine.md's measured build choice), speed settable
 * live for dragging the slider. This is NOT the practice cache
 * (BufferEngine, below) — invariant 9 is knowingly not yet satisfied by
 * this engine; Phase 0's manual gate loops on it anyway (see the plan's
 * "a stated exception, not a silent breach").
 *
 * D4 must respect three constraints measured in docs/03-audio-engine.md:
 * the worklet instantiates its WebAssembly.Module synchronously in the
 * processor constructor (no glue code, no async init); preferredStartPad/
 * startDelay (2048 frames on R3, 2170 with a pitch shift, 1024 on R2)
 * belong in clock.py's conversion, not in this file; and `performance`
 * does not exist inside AudioWorkletGlobalScope.
 *
 * Fires:
 *   - 'pass' — CustomEvent<{sectionId: string}> — see the module doc's
 *     pass-detection contract above. Only ever fires for a `loop: true`
 *     section (the default; SectionLoad's own doc).
 *   - 'ended' — CustomEvent<{sectionId: string}> — Phase 1.5, R1: fires
 *     once when a `loop: false` section's single playthrough reaches its
 *     end. Structurally separate from 'pass' (the worklet's 'boundary'
 *     message, the only thing _onBoundary() reads, is never posted for a
 *     non-looping section — see worklet.js's own module doc) — a preview
 *     player cannot produce a 'pass' by forgetting a check here, because
 *     there is no check to forget.
 *   - 'error' — CustomEvent<{error: Error}> — NOT in D0's fixed contract;
 *     added here so a worklet crash (an AudioWorkletProcessor throwing
 *     after construction) is observable rather than silent. D6 is not
 *     required to listen to it — nothing about the fixed contract
 *     changes if it never does.
 * @extends EventTarget
 */
export class RealtimeEngine extends EventTarget {
  /** @param {AudioContext} [audioContext] - reused if supplied, else created */
  constructor(audioContext) {
    super();
    this._ownsContext = !audioContext;
    this.ctx = audioContext || new AudioContext();
    /** @type {AudioWorkletNode | null} */
    this._node = null;
    /** @type {GainNode | null} */
    this._gain = null;
    /** @type {SectionLoad | null} */
    this._section = null;
    this._speedPct = 100;
    this._semitones = 0;
    // Does the lap currently in flight still qualify as a pass? See the
    // module doc and _onBoundary() below for the full state machine.
    this._qualified = false;
    this._destroyed = false;
  }

  /**
   * Fetch, decode and prepare *section* for looped playback. Resolves once
   * the worklet graph exists and play() can be called.
   * @param {SectionLoad} section
   * @returns {Promise<void>}
   */
  async loadSection(section) {
    if (this._destroyed) {
      throw new Error('RealtimeEngine.loadSection: engine already destroyed');
    }

    // A section change is a hard cut in this engine, not a crossfaded
    // hand-off — that is Phase 2's BufferEngine ("queue the new buffer,
    // start it at the exact currentTime the current loop ends"), not
    // this one's job.
    this._teardownNode();

    const res = await fetch(section.audioUrl);
    if (!res.ok) {
      throw new Error(`RealtimeEngine.loadSection: fetch ${section.audioUrl}: ${res.status} ${res.statusText}`);
    }
    const arrayBuffer = await res.arrayBuffer();
    const decoded = await this.ctx.decodeAudioData(arrayBuffer);

    // [start_s - pre_roll_s, end_s] in SOURCE seconds -> sample indices —
    // CLAUDE.md's two-clock rule (plus S3's third, clip-local one when
    // section.clipOffsetS is set — see computeSliceFrames's own doc).
    const sr = decoded.sampleRate;
    const { rawStartFrame, loopStartFrame, endFrame, sliceStartS, sliceEndS } =
      computeSliceFrames(section, sr, decoded.length);
    if (endFrame <= rawStartFrame) {
      throw new Error(
        'RealtimeEngine.loadSection: section end is at or before its start once clamped to the audio'
      );
    }

    // seek()'s coordinate conversion (Phase 1.5, P1): sourceSeconds is
    // absolute — position in the original file, CLAUDE.md's SourceSeconds
    // — and this is the only place that knows the mapping from that to a
    // frame offset within the worklet's own slice (rawStartFrame/sr,
    // fixed above, including the pre-roll-ran-off-the-start clamp). Store
    // the slice's own bounds in source seconds so seek() can clamp against
    // what is actually loaded, not the section's nominal, unclamped span.
    this._sampleRate = sr;
    this._sliceStartS = sliceStartS;
    this._sliceEndS = sliceEndS;

    const channels = decoded.numberOfChannels;
    const source = [];
    for (let c = 0; c < channels; c++) {
      source.push(decoded.getChannelData(c).slice(rawStartFrame, endFrame));
    }

    await ensureWorkletModule(this.ctx);
    const module = await getWasmModule();

    if (this.ctx.state === 'suspended') {
      await this.ctx.resume();
    }

    const speedFraction = this._speedPct / 100;
    const timeRatio = 1 / speedFraction; // output/input duration -- Rubber Band's own units, independent of pitchScale
    const pitchScale = Math.pow(2, this._semitones / 12);

    const node = new AudioWorkletNode(this.ctx, 'woodshed-rubberband', {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [channels],
      processorOptions: { module, source, channels, loopStartFrame, timeRatio, pitchScale, loop: section.loop },
    });
    const gain = this.ctx.createGain();
    node.connect(gain).connect(this.ctx.destination);

    const ready = new Promise((resolve, reject) => {
      node.port.onmessage = (e) => this._onWorkletMessage(e.data, resolve);
      node.onprocessorerror = () => {
        const err = new Error('RealtimeEngine: AudioWorkletProcessor crashed');
        reject(err); // no-op if `ready` already settled
        this.dispatchEvent(new CustomEvent('error', { detail: { error: err } }));
      };
    });

    this._node = node;
    this._gain = gain;
    this._section = section;
    // Nothing can produce a boundary before play() is called (the
    // worklet gates on its own `playing` flag), so it is safe to arm the
    // very first lap here rather than in play().
    this._qualified = true;

    await ready;
  }

  /**
   * @param {number} pct - percent, 50.0 means 50%; range 40-110
   *   (CLAUDE.md: "Practice speeds are discrete" governs the CACHE, not
   *   this slider — the real-time engine may take any value in range).
   */
  setSpeedPct(pct) {
    const clamped = Math.min(MAX_SPEED_PCT, Math.max(MIN_SPEED_PCT, pct));
    this._speedPct = clamped;
    if (this._node) {
      this._node.port.postMessage({ type: 'setRatio', ratio: 1 / (clamped / 100) });
    }
  }

  /** @param {number} n - semitones, clamped to ±6 (tuning.MAX_SHIFT) */
  setSemitones(n) {
    const clamped = Math.min(MAX_SHIFT, Math.max(-MAX_SHIFT, Math.round(n)));
    this._semitones = clamped;
    if (this._node) {
      this._node.port.postMessage({ type: 'setPitch', scale: Math.pow(2, clamped / 12) });
    }
  }

  /** Resume or start playback from the current position. */
  play() {
    this._requireNode('play');
    this._node.port.postMessage({ type: 'play' });
    // Deliberately does not touch _qualified: a first play() after
    // loadSection is already armed (see loadSection); a resume after
    // pause() stays disqualified until the next natural loop boundary
    // re-arms it (see _onBoundary) because it resumed mid-lap, not at
    // the section's beginning.
  }

  /** Pause. Per the pass-detection contract, a pause before the section
   *  end disqualifies the pass in progress. */
  pause() {
    this._requireNode('pause');
    this._node.port.postMessage({ type: 'pause' });
    this._qualified = false;
  }

  /** Seek to the section's pre-roll start and begin a fresh pass-detection
   *  window (equivalent to a seek, for pass-detection purposes). */
  restartSection() {
    this._requireNode('restartSection');
    this._node.port.postMessage({ type: 'restart' });
    this._qualified = true; // back at the true beginning -- a fresh window starts now
  }

  /**
   * Jump to *sourceSeconds* (CLAUDE.md's SourceSeconds — absolute position
   * in the original file, the same clock section.startS/endS are in), and
   * disqualify the in-flight lap: see the module doc's pass-detection
   * contract and PASS_START_TOLERANCE_S's comment above for why this is
   * unconditional rather than tolerance-gated. Clamps to the loaded
   * slice's own bounds rather than throwing on an out-of-range value —
   * screens/practice.js's click math is expected to stay in range, but a
   * stray click just past either edge should still do something sane.
   * @param {number} sourceSeconds
   */
  seek(sourceSeconds) {
    this._requireNode('seek');
    const clampedS = Math.min(this._sliceEndS, Math.max(this._sliceStartS, sourceSeconds));
    const frame = Math.round((clampedS - this._sliceStartS) * this._sampleRate);
    this._node.port.postMessage({ type: 'seek', frame });
    this._qualified = false;
  }

  /** Tear down the audio graph and worklet. No further events fire after this. */
  destroy() {
    if (this._destroyed) return;
    this._destroyed = true;
    this._teardownNode();
    if (this._ownsContext) {
      this.ctx.close().catch(() => {
        // already closed or never resumed -- nothing left to clean up
      });
    }
  }

  /**
   * A lap ended (the worklet's readPos reached the section end and
   * wrapped). Count it if nothing disqualified it since the last
   * boundary/restart/load, then re-arm: the lap that just began starts
   * at exactly loopStartFrame, by construction of this engine's looping
   * (see the module doc), so it is unconditionally eligible until
   * something disqualifies it in turn.
   */
  _onBoundary() {
    if (this._qualified && this._section) {
      this.dispatchEvent(new CustomEvent('pass', { detail: { sectionId: this._section.sectionId } }));
    }
    this._qualified = true;
  }

  /**
   * Dispatch table for the worklet's port messages, pulled out of
   * loadSection() so it can be driven directly in a synthetic-worklet
   * test without a real AudioWorkletNode (see web/tests/test_ended.mjs and
   * test_seek.mjs, which do exactly that for _onBoundary/seek already).
   * @param {{type: string, [key: string]: any}} msg
   * @param {() => void} onReady - resolves loadSection's `ready` promise;
   *   only ever called once (the worklet posts 'ready' exactly once).
   */
  _onWorkletMessage(msg, onReady) {
    if (msg.type === 'ready') {
      onReady();
    } else if (msg.type === 'boundary') {
      this._onBoundary();
    } else if (msg.type === 'ended') {
      // 'ended' never touches _qualified/_onBoundary -- see the class
      // doc's Fires list and worklet.js's own module doc: a `loop: false`
      // section cannot produce a 'pass' event, because nothing here reads
      // 'ended' as a boundary. This section is done; a fresh loadSection()
      // is required to play again (screens/song.js's ensureEngine reloads
      // per press, matching its own selection, which can change between
      // presses).
      this.dispatchEvent(new CustomEvent('ended', { detail: { sectionId: this._section.sectionId } }));
    }
  }

  _requireNode(method) {
    if (!this._node) {
      throw new Error(`RealtimeEngine.${method}: call loadSection() first`);
    }
  }

  _teardownNode() {
    if (this._node) {
      try {
        this._node.port.postMessage({ type: 'destroy' });
      } catch {
        // port already closed -- node is on its way out anyway
      }
      this._node.port.onmessage = null;
      this._node.onprocessorerror = null;
      this._node.disconnect();
    }
    if (this._gain) {
      this._gain.disconnect();
    }
    this._node = null;
    this._gain = null;
  }
}

/**
 * The clock for one rendered section, in PLAYBACK seconds — the exact
 * mirror of `clock.Render`'s three properties (src/woodshed/clock.py),
 * which is the module CLAUDE.md invariant 3 owns. Mirrored rather than
 * fetched for the same reason screens/practice.js mirrors ladder.py's
 * rung maths: this file cannot import a Python module, and the arithmetic
 * is four lines that a round trip would only make later, not safer. If
 * clock.py changes, this changes with it — tests/test_clock.py and
 * web/tests/test_buffer_engine.mjs assert the same numbers from the two
 * sides.
 *
 * `loopEnd` is measured from the SECTION's own end, never from `loopStart`
 * — see clock.py's own note: a lap that replays the lead-in begins earlier
 * and therefore lasts longer; the section does not end any sooner for it.
 *
 * @param {SectionLoad & {crossfadeMs?: number}} section
 * @param {number} speedPct - percent (55 means 55%)
 */
export function renderClock(section, speedPct) {
  const speed = speedPct / 100;
  const crossfadeS = (section.crossfadeMs ?? 10) / 1000;
  const loopStart = section.preRollEveryPass ? 0 : section.preRollS / speed;
  const total = (section.preRollS + (section.endS - section.startS)) / speed;
  const loopEnd = total - crossfadeS;
  return { speed, loopStart, loopEnd, total, lap: loopEnd - loopStart, crossfadeS };
}

/**
 * `GET /api/render/<slug>/<section>?speed=&semitones=&source=` — server.py's
 * `_render`, which either serves the cache file (range-served FLAC) or
 * answers 202 `{"rendering": true}` while it is still being built.
 * @param {SectionLoad & {slug: string, source?: string}} section
 * @param {number} speedPct
 * @param {number} semitones
 */
export function renderUrl(section, speedPct, semitones) {
  const q = new URLSearchParams({
    speed: String(speedPct),
    semitones: String(semitones),
    source: section.source ?? 'mix',
  });
  return `/api/render/${encodeURIComponent(section.slug)}`
    + `/${encodeURIComponent(section.sectionId)}?${q}`;
}

/**
 * J3's arithmetic, exposed so it can be asserted directly. With a NATIVE
 * loop there is no boundary event to listen for — the browser splices in
 * the audio thread and tells nobody — so the *n*th seam is computed:
 *
 *     startTime + (loopEnd - offset) + n * (loopEnd - loopStart)
 *
 * `offset` is where in the buffer the current node was started, which is 0
 * for a first pass (the lead-in is part of the buffer), `loopStart` for a
 * node that joined at a swap, and anything at all after a seek. Everything
 * else is a property of the render, not of when anyone looked.
 * @param {{startTime: number, offset: number, clock: ReturnType<typeof renderClock>}} anchor
 * @param {number} n
 */
export function seamTimeAt(anchor, n) {
  return anchor.startTime + (anchor.clock.loopEnd - anchor.offset) + n * anchor.clock.lap;
}

/**
 * Where in the buffer a node started at `anchor` has reached by
 * AudioContext time `t`, accounting for however many native wraps have
 * happened since. PLAYBACK seconds.
 */
export function playbackPositionAt(anchor, t) {
  const { clock } = anchor;
  const raw = anchor.offset + (t - anchor.startTime);
  if (raw < clock.loopEnd || clock.lap <= 0) return raw;
  return clock.loopStart + ((raw - clock.loopEnd) % clock.lap);
}

/**
 * One half of an equal-power crossfade, sampled at `points` values:
 * `sin(theta)` rising, `cos(theta)` falling, over the same theta 0..pi/2 —
 * so the two squares sum to 1 at every point and perceived loudness holds
 * through the middle of the fade (a linear pair dips there). The same
 * curve `render._bake_crossfade` bakes into the file, applied here to the
 * two source nodes that overlap across a rung change.
 * @param {number} points
 * @param {"in" | "out"} direction
 * @returns {Float32Array}
 */
export function equalPowerCurve(points, direction) {
  const curve = new Float32Array(points);
  for (let i = 0; i < points; i++) {
    const theta = (i / (points - 1)) * (Math.PI / 2);
    curve[i] = direction === 'in' ? Math.sin(theta) : Math.cos(theta);
  }
  return curve;
}

/** How many points the crossfade curves are sampled at — 128 over 10ms is
 *  far finer than the ear or the sample rate needs, and costs nothing. */
const CROSSFADE_POINTS = 128;

/** How far ahead of `currentTime` a swap must be scheduled to be reliable.
 *  Web Audio schedules on the audio thread, but a seam already inside the
 *  current render quantum cannot be honoured, so a swap that close waits
 *  for the next seam instead. */
const SWAP_MIN_LEAD_S = 0.05;

/** Slack when comparing a polled `currentTime` against a computed seam.
 *  The tick interval is coarser than this by two orders of magnitude; the
 *  epsilon only guards float equality at the exact boundary. */
const SEAM_EPS = 1e-6;

/** How often the seam clock is polled. Nothing audible depends on it — the
 *  loop and the swap are both scheduled on the audio thread — so this only
 *  bounds how late a rep is COUNTED, and 50ms is imperceptible for that. */
const TICK_MS = 50;

/**
 * Phase 2's cache-backed engine, and CLAUDE.md invariant 9 made real: "a
 * loop plays from a pre-rendered, decoded AudioBuffer with a native
 * sample-exact loop — never from a real-time stretcher, which cannot put
 * the seam in the same place twice". docs/03-audio-engine.md's Trap 3 is
 * the whole argument; this class is the answer to it.
 *
 * Shares RealtimeEngine's shape (constructor, loadSection/setSpeedPct/
 * setSemitones/play/pause/restartSection/seek/destroy, 'pass'/'error'
 * events) so a caller holding whichever engine createEngine() handed it
 * never branches on which. Two differences are real and deliberate:
 *
 *  - `setSpeedPct`/`setSemitones` are ASYNC here and take effect at the
 *    next loop boundary, never mid-loop (docs/03-audio-engine.md: "Speed
 *    changes at the boundary, never mid-loop"). They resolve once the new
 *    buffer is decoded and its node is scheduled — not once it is audible.
 *  - A speed the cache has not rendered yet costs a fetch and a render
 *    (the endpoint answers 202 while rubberband works), which is why the
 *    server pre-renders the next rung. That is a property of the
 *    architecture, not a bug to smooth over with `playbackRate`: NEVER
 *    `playbackRate` a stretched buffer (trap 1 on top of a correct
 *    render), and a lint test in tests/test_web_lint.py greps this whole
 *    directory to keep it that way.
 *
 * The 'pass' contract is unchanged from RealtimeEngine's — only the clock
 * it reads. A native loop fires no boundary event, so the seam is computed
 * (`seamTimeAt`) and polled; pause() and seek() disqualify the lap in
 * flight, and the next natural wrap re-arms the one after it.
 * @extends EventTarget
 */
export class BufferEngine extends EventTarget {
  /** @param {AudioContext} [audioContext] - reused if supplied, else created */
  constructor(audioContext) {
    super();
    this._ownsContext = !audioContext;
    this.ctx = audioContext || new AudioContext();
    /** @type {(SectionLoad & {slug: string, crossfadeMs?: number}) | null} */
    this._section = null;
    /** @type {AudioBuffer | null} */
    this._buffer = null;
    /** decoded buffers by render URL — returning to a rung never refetches. */
    this._buffers = new Map();
    /** @type {{source: AudioBufferSourceNode, gain: GainNode, anchor: any} | null} */
    this._active = null;
    /** @type {any} */
    this._pendingSwap = null;
    this._speedPct = 100;
    this._semitones = 0;
    this._playing = false;
    this._position = 0; // playback seconds, where a play() would resume from
    this._qualified = false;
    this._nextSeam = null;
    this._timer = null;
    this._destroyed = false;
    /** Poll interval while the server answers 202. Overridden in tests. */
    this.pollMs = 400;
    /** Give up after this many polls — long enough for a Demucs separation
     *  (minutes) rather than only a render (seconds), since `source:
     *  "guitar"` renders behind one. */
    this.maxPolls = 900;
  }

  /** The rung currently loaded, as a percent. */
  get speedPct() { return this._speedPct; }
  /** The shift currently loaded, in semitones. */
  get semitones() { return this._semitones; }

  /**
   * Fetch the render for *section* at the current speed/semitones, decode
   * it, and be ready to play. Does not start playback.
   * @param {SectionLoad & {slug: string, crossfadeMs?: number, source?: string}} section
   */
  async loadSection(section) {
    if (this._destroyed) {
      throw new Error('BufferEngine.loadSection: engine already destroyed');
    }
    this._stopEverything();
    this._section = section;
    this._buffer = await this._render(this._speedPct, this._semitones);
    this._position = 0;
    this._qualified = true;
  }

  /** Start (or resume) playback. */
  play() {
    this._require('play');
    if (this._playing) return;
    if (this.ctx.state === 'suspended') this.ctx.resume();
    this._startAt(this._position);
    this._playing = true;
    this._startTimer();
  }

  /** Pause. Per the pass-detection contract, this disqualifies the lap in
   *  flight — it will resume mid-section, not at the section's beginning. */
  pause() {
    if (!this._playing || !this._active) return;
    this._position = playbackPositionAt(this._active.anchor, this.ctx.currentTime);
    this._stopEverything();
    this._playing = false;
    this._qualified = false;
  }

  /** Back to sample 0 of the buffer (the lead-in included) and a fresh
   *  pass-detection window. */
  restartSection() {
    this._require('restartSection');
    const wasPlaying = this._playing;
    this._stopEverything();
    this._position = 0;
    this._qualified = true;
    if (wasPlaying) {
      this._startAt(0);
      this._playing = true;
    }
  }

  /**
   * Jump to *sourceSeconds* (CLAUDE.md's SourceSeconds — absolute position
   * in the original recording) and disqualify the lap in flight, exactly
   * as pause() does and for the same reason. This is `clock.to_playback`,
   * the one conversion between the two clocks on this side of the wire.
   * @param {number} sourceSeconds
   */
  seek(sourceSeconds) {
    this._require('seek');
    const clock = this._clock();
    const section = this._section;
    const playback = (sourceSeconds - (section.startS - section.preRollS)) / clock.speed;
    const clamped = Math.min(clock.loopEnd, Math.max(0, playback));
    const wasPlaying = this._playing;
    this._stopEverything();
    this._position = clamped;
    this._qualified = false;
    if (wasPlaying) {
      this._startAt(clamped);
      this._playing = true;
    }
  }

  /**
   * Move to a different rung. Takes effect at the next loop boundary when
   * playing (queueing the newly decoded buffer and starting it at the exact
   * `currentTime` the current loop ends, the two overlapping for one
   * crossfade); immediately, with nothing to swap at, when stopped.
   * @param {number} pct - percent, 50 means 50%
   * @returns {Promise<void>} resolves once the new buffer is scheduled
   */
  setSpeedPct(pct) {
    const clamped = Math.min(MAX_SPEED_PCT, Math.max(MIN_SPEED_PCT, pct));
    return this._changeRender(clamped, this._semitones);
  }

  /**
   * Move to a different shift. Same boundary discipline as setSpeedPct —
   * a shift change is a different rendered FILE here, not a live parameter.
   * @param {number} n - semitones, clamped to ±6 (tuning.MAX_SHIFT)
   * @returns {Promise<void>}
   */
  setSemitones(n) {
    const clamped = Math.min(MAX_SHIFT, Math.max(-MAX_SHIFT, Math.round(n)));
    return this._changeRender(this._speedPct, clamped);
  }

  /** Tear down the audio graph. No further events fire after this. */
  destroy() {
    if (this._destroyed) return;
    this._destroyed = true;
    this._stopEverything();
    this._stopTimer();
    this._buffers.clear();
    if (this._ownsContext) {
      this.ctx.close().catch(() => {
        // already closed or never resumed -- nothing left to clean up
      });
    }
  }

  // ── internals ─────────────────────────────────────────────────────────

  _clock(speedPct = this._speedPct) {
    return renderClock(this._section, speedPct);
  }

  /** Fetch + decode the render for one (speed, semitones), polling while
   *  the server answers 202 because rubberband (or Demucs before it) is
   *  still working. Cached by URL. */
  async _render(speedPct, semitones) {
    const url = renderUrl(this._section, speedPct, semitones);
    const cached = this._buffers.get(url);
    if (cached) return cached;
    for (let poll = 0; poll < this.maxPolls; poll++) {
      const res = await fetch(url);
      if (res.status === 202) {
        await new Promise((resolve) => setTimeout(resolve, this.pollMs));
        continue;
      }
      if (!res.ok) {
        throw new Error(`BufferEngine: fetch ${url}: ${res.status} ${res.statusText}`);
      }
      const bytes = await res.arrayBuffer();
      const buffer = await this.ctx.decodeAudioData(bytes);
      this._buffers.set(url, buffer);
      return buffer;
    }
    throw new Error(`BufferEngine: gave up waiting for the render at ${url}`);
  }

  async _changeRender(speedPct, semitones) {
    if (speedPct === this._speedPct && semitones === this._semitones) return;
    if (!this._section) {
      this._speedPct = speedPct;
      this._semitones = semitones;
      return;
    }
    let buffer;
    try {
      buffer = await this._render(speedPct, semitones);
    } catch (error) {
      // A rung whose render will not build must not take practice down
      // with it: keep playing what is already loaded and say so once.
      this.dispatchEvent(new CustomEvent('error', { detail: { error } }));
      return;
    }
    if (this._destroyed) return;

    if (!this._playing || !this._active) {
      this._speedPct = speedPct;
      this._semitones = semitones;
      this._buffer = buffer;
      return;
    }
    this._scheduleSwap(buffer, speedPct, semitones);
  }

  /** J2: queue the next buffer and start it at the exact AudioContext time
   *  the current loop ends, the two overlapping for one crossfade. */
  _scheduleSwap(buffer, speedPct, semitones) {
    this._cancelPendingSwap();
    const anchor = this._active.anchor;
    const now = this.ctx.currentTime;
    let n = 0;
    while (seamTimeAt(anchor, n) < now + SWAP_MIN_LEAD_S) n++;
    const seamTime = seamTimeAt(anchor, n);

    const clock = this._clock(speedPct);
    const crossfadeS = clock.crossfadeS;
    const gain = this.ctx.createGain();
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    source.loopStart = clock.loopStart;
    source.loopEnd = clock.loopEnd;
    source.connect(gain).connect(this.ctx.destination);
    gain.gain.value = 0;
    gain.gain.setValueCurveAtTime(equalPowerCurve(CROSSFADE_POINTS, 'in'), seamTime, crossfadeS);
    // The new node joins at its own loop start: a rung change mid-practice
    // is not a restart, so the lead-in does not replay for it.
    source.start(seamTime, clock.loopStart);

    this._active.gain.gain.setValueCurveAtTime(
      equalPowerCurve(CROSSFADE_POINTS, 'out'), seamTime, crossfadeS,
    );
    // The old node keeps looping into its own (already crossfaded) head for
    // the length of the fade, and stops there — two nodes overlap for
    // exactly one crossfade, never longer.
    this._active.source.stop(seamTime + crossfadeS);

    this._pendingSwap = { seamTime, source, gain, clock, buffer, speedPct, semitones };
  }

  _cancelPendingSwap() {
    if (!this._pendingSwap) return;
    const { source, gain } = this._pendingSwap;
    try { source.stop(); } catch { /* never started, or already stopped */ }
    source.disconnect();
    gain.disconnect();
    this._pendingSwap = null;
  }

  /** The scheduled swap's seam has arrived: the new node is already
   *  playing (the audio thread started it), so this is bookkeeping only. */
  _adoptSwap() {
    const swap = this._pendingSwap;
    this._pendingSwap = null;
    this._active.source.disconnect();
    this._active.gain.disconnect();
    this._speedPct = swap.speedPct;
    this._semitones = swap.semitones;
    this._buffer = swap.buffer;
    this._active = {
      source: swap.source,
      gain: swap.gain,
      anchor: { startTime: swap.seamTime, offset: swap.clock.loopStart, clock: swap.clock },
    };
    this._nextSeam = seamTimeAt(this._active.anchor, 0);
  }

  _startAt(offset) {
    const clock = this._clock();
    const gain = this.ctx.createGain();
    const source = this.ctx.createBufferSource();
    source.buffer = this._buffer;
    source.loop = true;
    source.loopStart = clock.loopStart;
    source.loopEnd = clock.loopEnd;
    source.connect(gain).connect(this.ctx.destination);
    const startTime = this.ctx.currentTime;
    source.start(startTime, offset);
    this._active = { source, gain, anchor: { startTime, offset, clock } };
    this._nextSeam = seamTimeAt(this._active.anchor, 0);
  }

  _stopEverything() {
    this._cancelPendingSwap();
    if (this._active) {
      const { source, gain } = this._active;
      try { source.stop(); } catch { /* not started */ }
      source.disconnect();
      gain.disconnect();
      this._active = null;
    }
    this._nextSeam = null;
  }

  _startTimer() {
    if (this._timer) return;
    this._timer = setInterval(() => this._tick(), TICK_MS);
    // Node (the tests) hands back a Timeout, not a number; an unref'd one
    // does not hold the process open. No-op in a browser.
    if (this._timer && typeof this._timer.unref === 'function') this._timer.unref();
  }

  _stopTimer() {
    if (!this._timer) return;
    clearInterval(this._timer);
    this._timer = null;
  }

  /**
   * J3: count the seams the computed clock says have passed. Exposed
   * (rather than closed over inside the interval) so a test can drive it
   * against a synthetic AudioContext — the whole reason the seam is
   * arithmetic and not an event is that there IS no event to wait for.
   */
  _tick() {
    if (!this._playing || !this._active || this._nextSeam === null) return;
    const now = this.ctx.currentTime;
    let guard = 0;
    while (this._nextSeam !== null && now >= this._nextSeam - SEAM_EPS && guard++ < 1000) {
      const seam = this._nextSeam;
      if (this._qualified) {
        this.dispatchEvent(
          new CustomEvent('pass', { detail: { sectionId: this._section.sectionId } }),
        );
      }
      // Whatever disqualified the last lap, the one starting now begins at
      // loopStart by construction of a native loop — so it is eligible.
      this._qualified = true;
      if (this._pendingSwap && this._pendingSwap.seamTime <= seam + SEAM_EPS) {
        this._adoptSwap();
      } else {
        this._nextSeam = seam + this._active.anchor.clock.lap;
      }
    }
  }

  _require(method) {
    if (!this._section || !this._buffer) {
      throw new Error(`BufferEngine.${method}: call loadSection() first`);
    }
  }
}

/**
 * Factory, D0's judgement call per the plan's brief: screens/practice.js
 * always wants RealtimeEngine this phase (BufferEngine has no body yet),
 * but routing construction through one function rather than importing the
 * class directly means Phase 2 can switch the default engine without
 * every caller's import changing.
 * @param {AudioContext} [audioContext]
 * @returns {RealtimeEngine}
 */
export function createEngine(audioContext) {
  return new RealtimeEngine(audioContext);
}
