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
 */

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
 *     pass-detection contract above.
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
    // CLAUDE.md's two-clock rule: everything on this line is source
    // time, and loopStartFrame is the one place it becomes an index into
    // this particular slice (never reused as a general-purpose clock).
    const sr = decoded.sampleRate;
    let rawStartFrame = Math.round((section.startS - section.preRollS) * sr);
    let loopStartFrame = Math.round(section.preRollS * sr);
    if (rawStartFrame < 0) {
      // The pre-roll would reach before the source file's own start.
      // Clamp the slice to what actually exists and shrink the lead-in
      // played to match, rather than reading negative indices or
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
    const endFrame = Math.min(Math.round(section.endS * sr), decoded.length);
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
    this._sliceStartS = rawStartFrame / sr;
    this._sliceEndS = endFrame / sr;

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
      processorOptions: { module, source, channels, loopStartFrame, timeRatio, pitchScale },
    });
    const gain = this.ctx.createGain();
    node.connect(gain).connect(this.ctx.destination);

    const ready = new Promise((resolve, reject) => {
      node.port.onmessage = (e) => {
        const msg = e.data;
        if (msg.type === 'ready') {
          resolve();
        } else if (msg.type === 'boundary') {
          this._onBoundary();
        }
      };
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
 * Phase 2's cache-backed engine (CLAUDE.md invariant 9: "a loop plays from
 * a pre-rendered, decoded AudioBuffer with a native sample-exact loop —
 * never from a real-time stretcher, which cannot put the seam in the same
 * place twice"). Deliberately not built this phase. Shares RealtimeEngine's
 * EventTarget shape (constructor, loadSection/setSpeedPct/setSemitones/
 * play/pause/restartSection/destroy, a 'pass' event) so a caller holding
 * whichever engine createEngine() handed it never needs to branch on which.
 * @extends EventTarget
 */
export class BufferEngine extends EventTarget {
  constructor() {
    super();
    throw new Error('not implemented — Phase 2, not this phase');
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
