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
   * The rung currently audible. BufferEngine's own accessor of the same
   * name can lag what was last asked for (a render has to be built before
   * it can be heard); here a ratio change is instant, so the two are always
   * equal and `pending` is always false. Mirrored so a caller holding
   * whichever engine `createEngine()` handed it can read "what is actually
   * playing" without branching on which — the same reason the rest of this
   * class shares BufferEngine's shape.
   */
  get speedPct() { return this._speedPct; }

  /** The shift currently audible — BufferEngine's accessor, mirrored. */
  get semitones() { return this._semitones; }

  /** @see speedPct — always equal to it on this engine. */
  get targetSpeedPct() { return this._speedPct; }

  /** @see speedPct — always equal to `semitones` on this engine. */
  get targetSemitones() { return this._semitones; }

  /** Always false: this engine's changes take effect immediately. */
  get pending() { return false; }

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
    this._announceRung();
  }

  /** @param {number} n - semitones, clamped to ±6 (tuning.MAX_SHIFT) */
  setSemitones(n) {
    const clamped = Math.min(MAX_SHIFT, Math.max(-MAX_SHIFT, Math.round(n)));
    this._semitones = clamped;
    if (this._node) {
      this._node.port.postMessage({ type: 'setPitch', scale: Math.pow(2, clamped / 12) });
    }
    this._announceRung();
  }

  /** BufferEngine's 'rung' event, mirrored — always already landed here.
   *  A screen can listen to one event name on either engine. */
  _announceRung() {
    this.dispatchEvent(new CustomEvent('rung', {
      detail: {
        speedPct: this._speedPct,
        semitones: this._semitones,
        targetSpeedPct: this._speedPct,
        targetSemitones: this._semitones,
        pending: false,
      },
    }));
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

  /**
   * Seek to the section's pre-roll start and begin a fresh pass-detection
   * window (equivalent to a seek, for pass-detection purposes).
   *
   * FOUND LIVE 2026-09-10, fixed here: this engine keeps no local playing
   * flag of its own (module doc, decision 3 -- playback state lives only in
   * the worklet and, cosmetically, in screens/practice.js's own `playing`).
   * The worklet's 'restart' handler used to hardcode `playing = true`, so a
   * restart pressed while paused made the room audibly play regardless --
   * the foot-strip icon still said "paused", but sound came out. There is
   * nothing on THIS side to read "was it playing" from, so the caller (the
   * one place that already tracks it) must say so explicitly.
   * @param {boolean} [playing] - whether playback should be running after
   *   the restart; defaults to true only for a caller that doesn't say
   *   (matches this method's behaviour before *playing* existed).
   */
  restartSection(playing) {
    this._requireNode('restartSection');
    this._node.port.postMessage({ type: 'restart', playing: playing !== false });
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
  // `clock.Render.effective_pre_roll_s`, mirrored: the server cuts
  // [max(0, start_s - pre_roll_s), end_s], so a section 0.5s into a
  // recording has 0.5s of lead-in however many beats the song asks for.
  // FOUND BY REVIEW 2026-09-07 -- without the clamp every lap of such a
  // section began late and loopEnd pointed past the end of the buffer.
  const preRollS = Math.max(0, Math.min(section.preRollS, section.startS));
  const loopStart = section.preRollEveryPass ? 0 : preRollS / speed;
  const total = (preRollS + (section.endS - section.startS)) / speed;
  const loopEnd = total - crossfadeS;
  return { speed, loopStart, loopEnd, total, lap: loopEnd - loopStart, crossfadeS, preRollS };
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
 * How many decoded renders BufferEngine keeps in memory at once.
 *
 * FOUND LIVE 2026-09-11: `_buffers` was unbounded, and a decoded render is
 * not small — tutti-in-fila's 88s "Full solo" at 40% is 226 seconds of
 * stereo float32, ~87MB, and the thirteen rungs the cache had built that
 * evening would be most of a gigabyte held live if every one were visited.
 * Four is enough that stepping a rung up and back down never refetches
 * (the common shape of a practice session) without the page carrying a
 * whole ladder's worth of PCM. Eviction is insertion-ordered and never
 * touches the buffer currently playing or the one queued at a seam.
 */
const MAX_DECODED_RENDERS = 4;

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
 * Fires 'rendering' — CustomEvent<{stage, speedPct, semitones, polls}> —
 * while the server answers 202, and once more with `stage: null` when the
 * wait ends however it ends. A cache hit fires nothing at all, so a
 * listener that shows `detail.stage` shows something exactly when there is
 * something to say.
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
    /** decoded buffers by render URL — returning to a rung never refetches.
     *  Insertion-ordered and capped at MAX_DECODED_RENDERS; see `_remember`. */
    this._buffers = new Map();
    /** In-flight `_render` promises by URL, so two presses that want the
     *  same file share one fetch and one 202 poll loop rather than racing
     *  two of each at the server. */
    this._renderWaits = new Map();
    /** Renders the server is still building, by URL — the 'rendering'
     *  event's whole state. A scalar here is what made the status bar flap
     *  between two speeds and blank while one was still going. */
    this._waits = new Map();
    /** @type {{source: AudioBufferSourceNode, gain: GainNode, anchor: any} | null} */
    this._active = null;
    /** @type {any} */
    this._pendingSwap = null;
    this._speedPct = 100;
    this._semitones = 0;
    // What was last ASKED for, latched synchronously by `_changeRender`
    // before it awaits anything. `_speedPct` is what is audible; these two
    // are where it is going. Keeping both is what lets a render that
    // finishes after a newer press be recognised as stale and dropped —
    // and what lets the screen say "playing 50%, heading for 80%" instead
    // of showing a number the room is not making.
    this._targetSpeedPct = 100;
    this._targetSemitones = 0;
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

  /** The rung currently AUDIBLE, as a percent — what the room is playing,
   *  which is not the same as what was last pressed while a render builds.
   *  Every clock on this side of the wire is derived from this one, so a
   *  caller that wants a playhead to match the music reads it, not the
   *  target below. */
  get speedPct() { return this._speedPct; }

  /** The rung last ASKED for. Equal to `speedPct` except while a render is
   *  building or a swap is queued for the next seam. */
  get targetSpeedPct() { return this._targetSpeedPct; }

  /** The shift last asked for. Same relationship as `targetSpeedPct`. */
  get targetSemitones() { return this._targetSemitones; }

  /** Whether what was asked for has yet to become audible. */
  get pending() {
    return this._speedPct !== this._targetSpeedPct || this._semitones !== this._targetSemitones;
  }

  /**
   * Where playback actually is, in PLAYBACK seconds, right now — the
   * position accessor RealtimeEngine has never had (which is why
   * screens/practice.js animates its ring from a wall-clock estimate
   * resynced at each 'pass'). Exact here rather than estimated, because a
   * native loop's position is arithmetic: where the node started, plus
   * elapsed AudioContext time, wrapped at the loop points.
   * @returns {number} playback seconds, 0 when nothing is loaded or playing
   */
  position() {
    if (!this._active) return this._position;
    if (!this._playing) return this._position;
    return playbackPositionAt(this._active.anchor, this.ctx.currentTime);
  }

  /** `position()` converted back to SOURCE seconds — the recording's own
   *  clock, what the waveform and the section boundaries are in. */
  sourcePosition() {
    // The guard comes FIRST: _clock() dereferences _section, so asking
    // before a section is loaded used to throw instead of answering 0
    // (found by review 2026-09-07).
    if (!this._section) return 0;
    const clock = this._clock();
    return this.position() * clock.speed + (this._section.startS - clock.preRollS);
  }
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
    // `_stopEverything` tore the graph down, so nothing is playing any
    // more and this must say so. FOUND BY REVIEW 2026-09-07: it did not,
    // so the play() that follows a reload hit its own "already playing"
    // guard and created no node -- toggling "Guitar only" mid-practice
    // (screens/practice.js does exactly loadSection-then-play) went
    // permanently silent, and pause() no-oped too.
    this._playing = false;
    this._section = section;
    // A new section invalidates every decoded render held for the old one
    // (they are different files, keyed by a URL that names the section), so
    // nothing here is worth carrying over -- and holding it would count
    // against MAX_DECODED_RENDERS for renders that can never be asked for
    // again from this engine.
    this._buffers.clear();
    this._buffer = await this._render(this._speedPct, this._semitones);
    this._position = 0;
    this._qualified = true;
    this._targetSpeedPct = this._speedPct;
    this._targetSemitones = this._semitones;
    this._announceRung();
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

  /**
   * Pause. Per the pass-detection contract, this disqualifies the lap in
   * flight — it will resume mid-section, not at the section's beginning.
   *
   * A swap that was scheduled but has not reached its seam yet must not be
   * silently discarded here: `_stopEverything()` below cancels it via
   * `_cancelPendingSwap()`, and nothing else ever writes `_speedPct`/
   * `_semitones` except `_adoptSwap()` at a seam — a seam a paused engine's
   * audio thread will now never reach. Left alone, the readout (driven by
   * these fields) would say the requested rung forever while play() resumed
   * the stale one. #4. Since pausing already tears the graph down, there is
   * no audio thread state to preserve either: adopt the swap's target
   * immediately instead.
   */
  pause() {
    if (!this._playing || !this._active) return;
    const activeClock = this._active.anchor.clock;
    const playbackPos = playbackPositionAt(this._active.anchor, this.ctx.currentTime);
    const swap = this._pendingSwap;
    if (swap) {
      // `playbackPos` is PLAYBACK seconds under the OLD clock, and playback
      // seconds are not comparable across a speed change — two clocks,
      // mixing them up is silent (docs/03-audio-engine.md). Convert through
      // SOURCE seconds, the same boundary crossing seek() already makes, so
      // resuming lands on the same instant in the recording rather than on
      // the same raw number re-read against a different clock.
      const sourceS = playbackPos * activeClock.speed
        + (this._section.startS - activeClock.preRollS);
      this._speedPct = swap.speedPct;
      this._semitones = swap.semitones;
      this._buffer = swap.buffer;
      const newClock = this._clock();
      const playback = (sourceS - (this._section.startS - newClock.preRollS)) / newClock.speed;
      this._position = Math.min(newClock.loopEnd, Math.max(0, playback));
    } else {
      this._position = playbackPos;
    }
    this._stopEverything();
    this._playing = false;
    this._qualified = false;
    if (swap) this._announceRung(); // the swap's rung was adopted above
  }

  /**
   * Back to sample 0 of the buffer (the lead-in included) and a fresh
   * pass-detection window. Keeps playing if it was playing, stays paused if
   * it was paused -- read off `this._playing`, which this engine already
   * tracks itself, so unlike RealtimeEngine.restartSection() the caller
   * does not need to pass it in (an argument here would just be ignored).
   */
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
    // The render's own t=0 is start_s minus the pre-roll that actually
    // exists -- clock.to_playback's exact arithmetic, via the same clamp.
    const playback = (sourceSeconds - (section.startS - clock.preRollS)) / clock.speed;
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

  /**
   * Fetch + decode the render for one (speed, semitones), polling while the
   * server answers 202 because rubberband (or Demucs before it) is still
   * working.
   *
   * Two callers wanting the same URL share ONE request and one poll loop.
   * FOUND LIVE 2026-09-11: without that, an up-down-up press sequence asked
   * the server for the same file twice and ran two independent 202 loops
   * over it, each firing its own 'rendering' events at the status bar.
   */
  _render(speedPct, semitones) {
    const url = renderUrl(this._section, speedPct, semitones);
    const cached = this._buffers.get(url);
    if (cached) {
      // Re-insert so the insertion-ordered eviction below treats a rung
      // just returned to as the freshest, not the stalest.
      this._buffers.delete(url);
      this._buffers.set(url, cached);
      return Promise.resolve(cached);
    }
    const inFlight = this._renderWaits.get(url);
    if (inFlight) return inFlight;
    const wait = this._fetchRender(url, speedPct, semitones).finally(() => {
      this._renderWaits.delete(url);
      this._endWait(url);
    });
    this._renderWaits.set(url, wait);
    return wait;
  }

  async _fetchRender(url, speedPct, semitones) {
    for (let poll = 0; poll < this.maxPolls; poll++) {
      const res = await fetch(url);
      if (res.status === 202) {
        // Say so. A render is seconds and a Demucs separation ahead of one
        // is minutes, and until this event existed, pressing play during
        // either was silence with no explanation. The server's own 202 body
        // carries the only two stages it can honestly report (`separating`
        // then `rendering` -- Demucs has no progress readout, and inventing
        // a percentage would be a measurement the tool cannot make).
        let stage = 'rendering';
        try {
          const body = await res.json();
          if (body && body.stage) stage = body.stage;
        } catch {
          // A 202 with no JSON body still means "not built yet".
        }
        this._beginWait(url, { stage, speedPct, semitones, polls: poll + 1 });
        await new Promise((resolve) => setTimeout(resolve, this.pollMs));
        continue;
      }
      if (!res.ok) {
        throw new Error(`BufferEngine: fetch ${url}: ${res.status} ${res.statusText}`);
      }
      const bytes = await res.arrayBuffer();
      const buffer = await this.ctx.decodeAudioData(bytes);
      this._remember(url, buffer);
      return buffer;
    }
    throw new Error(`BufferEngine: gave up waiting for the render at ${url}`);
  }

  /** Keep `_buffers` bounded, never evicting what is playing or queued.
   *  See MAX_DECODED_RENDERS for the measurement behind the number. */
  _remember(url, buffer) {
    this._buffers.set(url, buffer);
    for (const [key, held] of this._buffers) {
      if (this._buffers.size <= MAX_DECODED_RENDERS) break;
      if (held === buffer || held === this._buffer || held === this._pendingSwap?.buffer) continue;
      this._buffers.delete(key);
    }
  }

  /** Record that *url* is still building and re-announce the status. */
  _beginWait(url, detail) {
    this._waits.set(url, detail);
    this._announceWait();
  }

  /** *url* is no longer building, however it ended. The status only goes
   *  quiet when NOTHING is left — a wait that finishes while another is
   *  still running must not blank the bar. */
  _endWait(url) {
    if (!this._waits.delete(url)) return;
    if (this._waits.size) this._announceWait();
    else this.dispatchEvent(new CustomEvent('rendering', { detail: { stage: null } }));
  }

  /** Announce the wait that matches what was last asked for, if one does —
   *  the bar should name the press the user is waiting on, not whichever
   *  render happened to poll most recently. */
  _announceWait() {
    if (!this._waits.size) return;
    const target = renderUrl(this._section, this._targetSpeedPct, this._targetSemitones);
    const detail = this._waits.get(target) ?? [...this._waits.values()].pop();
    this.dispatchEvent(new CustomEvent('rendering', { detail: { ...detail } }));
  }

  /** Tell a listener where the engine is and where it is going. Fired
   *  whenever either moves, so a screen never has to poll to find out that
   *  the rung it asked for has become audible. */
  _announceRung() {
    this.dispatchEvent(new CustomEvent('rung', {
      detail: {
        speedPct: this._speedPct,
        semitones: this._semitones,
        targetSpeedPct: this._targetSpeedPct,
        targetSemitones: this._targetSemitones,
        pending: this.pending,
      },
    }));
  }

  async _changeRender(speedPct, semitones) {
    // Latch the target SYNCHRONOUSLY, before anything is awaited. This is
    // the fix for the whole class of bug this method used to have: it
    // compared against `_pendingSwap ?? this`, and `_pendingSwap` is only
    // assigned AFTER the await below, so N presses in rapid succession all
    // got past the guard and started N concurrent renders. Each called
    // `_scheduleSwap` (cancelling the previous) whenever ITS render
    // finished -- and the shortest output renders fastest, so the LAST
    // press came back FIRST and the FIRST press won. The engine settled on
    // a rung nobody asked for while the screen showed the one they did.
    // FOUND LIVE 2026-09-11, Paolo, on tutti-in-fila/full-solo.
    this._targetSpeedPct = speedPct;
    this._targetSemitones = semitones;
    this._announceRung();

    if (speedPct === this._speedPct && semitones === this._semitones) {
      // Asking for exactly what is already playing is "never mind": drop
      // any queued node and put the outgoing one back.
      if (this._pendingSwap) {
        this._cancelPendingSwap();
        this._announceRung();
      }
      return;
    }
    if (this._pendingSwap
      && speedPct === this._pendingSwap.speedPct
      && semitones === this._pendingSwap.semitones) {
      return; // already queued for the next seam
    }
    if (!this._section) {
      this._speedPct = speedPct;
      this._semitones = semitones;
      this._announceRung();
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
    // Stale: something newer was asked for while this was building. Drop
    // it -- it is already decoded and in `_buffers`, so pressing back to it
    // costs nothing, but adopting it now would override a later press.
    if (speedPct !== this._targetSpeedPct || semitones !== this._targetSemitones) return;
    if (speedPct === this._speedPct && semitones === this._semitones) return;

    if (!this._playing || !this._active) {
      // Stopped: adopt immediately, but carry the paused position across
      // the clock change. Playback seconds are NOT comparable across a
      // speed change -- the same conversion through source seconds that
      // pause() makes, and for the same reason (CLAUDE.md's two clocks).
      // FOUND LIVE 2026-09-11: without it, pausing at 50% and pressing up
      // to 100% resumed at an offset from the old, longer clock, past the
      // new render's loopEnd, where Web Audio plays to the end of the
      // buffer and stops rather than looping -- "the section looping for
      // the first few seconds and getting stuck".
      const oldClock = this._clock();
      const sourceS = this._position * oldClock.speed
        + (this._section.startS - oldClock.preRollS);
      this._speedPct = speedPct;
      this._semitones = semitones;
      this._buffer = buffer;
      const newClock = this._clock();
      const playback = (sourceS - (this._section.startS - newClock.preRollS)) / newClock.speed;
      this._position = Math.min(newClock.loopEnd, Math.max(0, playback));
      this._announceRung();
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

    // Clear any fade a previous (now replaced) swap left scheduled in this
    // window -- Web Audio refuses a value curve that overlaps another.
    this._active.gain.gain.cancelScheduledValues(seamTime);
    this._active.gain.gain.setValueCurveAtTime(
      equalPowerCurve(CROSSFADE_POINTS, 'out'), seamTime, crossfadeS,
    );
    // The FADE is what retires the old node: its gain reaches 0 at
    // seamTime + crossfadeS on the audio thread and holds there, so the
    // node is inaudible from that instant whatever the main thread is
    // doing. stop()/disconnect() is only cleanup, and it is deferred to a
    // timer rather than done when the seam is *observed* -- the seam clock
    // is polled every 50ms and the crossfade is 10ms, so retiring the node
    // on observation cut roughly one fade in five off partway through, at
    // ~0.98 gain. That is precisely the click Phase 2's listening gate
    // exists to catch, generated by the code meant to prevent it. FOUND BY
    // REVIEW 2026-09-07.
    const outgoing = this._active;
    const retireIn = Math.max(0, (seamTime + crossfadeS) - this.ctx.currentTime) * 1000;
    const retireTimer = setTimeout(() => this._retire(outgoing), retireIn + 20);
    if (retireTimer && typeof retireTimer.unref === 'function') retireTimer.unref();

    this._pendingSwap = {
      seamTime, source, gain, clock, buffer, speedPct, semitones,
      outgoing, retireTimer,
    };
  }

  /** Stop and unhook a node that has finished fading out. Idempotent. */
  _retire(node) {
    if (!node || node.retired) return;
    node.retired = true;
    try { node.source.stop(); } catch { /* already stopped */ }
    node.source.disconnect();
    node.gain.disconnect();
  }

  /**
   * Drop a swap that was scheduled but has not been adopted, and undo what
   * scheduling it did to the node still playing: the queued node is
   * stopped before it ever sounds, its retirement timer is cleared, and
   * the outgoing node's fade-out is cancelled and its gain put back to 1.
   *
   * That last part is why the old node is no longer *stopped* at the seam
   * (see `_scheduleSwap`): a scheduled stop cannot be reliably taken back,
   * so cancelling a swap would have left the music simply ending at a seam
   * nothing ever crossed.
   */
  _cancelPendingSwap() {
    if (!this._pendingSwap) return;
    const { source, gain, seamTime, outgoing, retireTimer } = this._pendingSwap;
    clearTimeout(retireTimer);
    try { source.stop(); } catch { /* never started, or already stopped */ }
    source.disconnect();
    gain.disconnect();
    if (outgoing && !outgoing.retired) {
      outgoing.gain.gain.cancelScheduledValues(seamTime);
      outgoing.gain.gain.setValueAtTime(1, seamTime);
    }
    this._pendingSwap = null;
  }

  /** The scheduled swap's seam has arrived: the new node is already
   *  playing (the audio thread started it), so this is bookkeeping only. */
  _adoptSwap() {
    const swap = this._pendingSwap;
    this._pendingSwap = null;
    // Deliberately does NOT tear the outgoing node down: it is still
    // fading, and `_scheduleSwap`'s own timer retires it once it has
    // finished. See that function for the click this caused.
    this._speedPct = swap.speedPct;
    this._semitones = swap.semitones;
    this._buffer = swap.buffer;
    this._active = {
      source: swap.source,
      gain: swap.gain,
      anchor: { startTime: swap.seamTime, offset: swap.clock.loopStart, clock: swap.clock },
    };
    this._nextSeam = seamTimeAt(this._active.anchor, 0);
    // The rung asked for is audible from this instant -- the one moment a
    // screen can stop showing it as pending.
    this._announceRung();
  }

  _startAt(offset) {
    const clock = this._clock();
    // Clamp into the render that is actually loaded. Web Audio, handed an
    // offset past `loopEnd`, plays to the end of the buffer and stops
    // instead of looping -- and `seamTimeAt(anchor, 0)` for such an offset
    // lands in the PAST, so `_tick` would read a lap as already complete
    // and fire 'pass' on its very first call. That writes a rep nobody
    // played into an append-only ledger, which is the one file this repo
    // cannot repair. Every caller should be passing something in range;
    // this is the belt to that brace. FOUND LIVE 2026-09-11.
    const safeOffset = Math.min(clock.loopEnd, Math.max(0, offset));
    if (safeOffset !== offset) {
      // The position handed in was not inside the render that is loaded, so
      // where playback actually resumes is a guess. A lap that begins from
      // a guess is not a lap anyone played: disqualify it and let the next
      // natural wrap re-arm the one after, exactly as a seek does.
      this._qualified = false;
    }
    const gain = this.ctx.createGain();
    const source = this.ctx.createBufferSource();
    source.buffer = this._buffer;
    source.loop = true;
    source.loopStart = clock.loopStart;
    source.loopEnd = clock.loopEnd;
    source.connect(gain).connect(this.ctx.destination);
    const startTime = this.ctx.currentTime;
    source.start(startTime, safeOffset);
    this._active = { source, gain, anchor: { startTime, offset: safeOffset, clock } };
    this._nextSeam = seamTimeAt(this._active.anchor, 0);
  }

  _stopEverything() {
    // Retire whatever is mid-fade first: a node left over from a swap that
    // was adopted moments ago is still connected on purpose, and a
    // teardown has to take it with it rather than wait for its timer.
    const retiring = this._pendingSwap?.outgoing;
    this._cancelPendingSwap();
    if (retiring && retiring !== this._active) this._retire(retiring);
    this._retire(this._active);
    this._active = null;
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
 * Factory, D0's judgement call per the plan's brief: routing construction
 * through one function rather than importing a class directly means a
 * caller never has to change its import to change engine.
 *
 * `kind` picks which of the two docs/03-audio-engine.md describes:
 * `"realtime"` (the default) for EXPLORING — dragging the speed slider,
 * scrubbing, auditioning a boundary, where instant response matters and
 * seams do not — and `"buffer"` for PRACTISING, where the seam is the
 * whole point and the speed is one of a handful of discrete rungs. The
 * table in that document is the decision; this parameter is only how it
 * gets expressed.
 * @param {AudioContext} [audioContext]
 * @param {{kind?: "realtime" | "buffer"}} [options]
 * @returns {RealtimeEngine | BufferEngine}
 */
export function createEngine(audioContext, { kind = 'realtime' } = {}) {
  return kind === 'buffer' ? new BufferEngine(audioContext) : new RealtimeEngine(audioContext);
}
