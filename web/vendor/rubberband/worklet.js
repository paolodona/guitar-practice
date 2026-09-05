/**
 * worklet.js — the pull-driven Rubber Band AudioWorkletProcessor that
 * actually plays a practice section for web/player.js's RealtimeEngine
 * (plan unit D4). It lives beside rubberband.wasm because the two are
 * one unit — this file's whole job is driving that binary correctly —
 * and separately from player.js because a worklet processor can only be
 * loaded from its own script URL (`audioWorklet.addModule(...)`), never
 * imported as a normal ES module into the main thread.
 *
 * Adapted from tools/rb-probe/rb-worklet.js, the measured prototype
 * (docs/03-audio-engine.md, 2026-09-05) that proved this exact
 * pull-driven shape survives ratio 2.0 in real time on this build. This
 * version drops the probe's timing/stats instrumentation — that
 * question is already answered — and adds what a practice loop needs
 * and a throughput probe never had to: play/pause gating, looping the
 * section indefinitely, live ratio/pitch changes, a restart, and
 * correctly dropping the engine's start-of-stream warm-up output.
 *
 * ---- Why pull-driven, not push-driven ----
 * A processor that takes one quantum from an upstream node and hands one
 * quantum back cannot hold any ratio but 1.0: at ratio 2.0 the stretcher
 * emits two output frames per input frame, so a quantum-in/quantum-out
 * shape accumulates a frame of surplus per frame of input and its
 * latency grows without bound (below 1.0 it starves instead). That is
 * why the published `rubberband-web` worklet does not work here, and why
 * this processor takes no audio input at all (`numberOfInputs: 0`) and
 * instead owns the section's decoded source samples itself, pulling
 * exactly as many as `rb_get_samples_required()` asks for until
 * `rb_available()` covers the quantum. See docs/03-audio-engine.md.
 *
 * ---- Looping the section ----
 * The section — pre-roll included — lives in `this.source`, one
 * Float32Array per channel, decoded and sliced by player.js on the main
 * thread (this processor never touches an AudioBuffer or the network).
 * `readPos` is the next source frame to feed the stretcher. The first
 * lap runs [0, sourceLen) — pre-roll included, per CLAUDE.md's "pre-roll
 * is part of the buffer, not a separate source". Every lap after that
 * runs [loopStartFrame, sourceLen) — the pre-roll is a lead-in played
 * once, not on every pass. Reaching `sourceLen` never sets `done`: it
 * wraps `readPos` back to `loopStartFrame` and keeps feeding the same
 * never-finalized Rubber Band stream (rb_process's `final` argument is
 * always 0 here — a continuous stream is exactly what "loop" means to a
 * pull-driven stretcher). Each wrap posts `{type:'boundary', contextTime}`
 * to the main thread; player.js's RealtimeEngine turns that into a
 * `pass` event, or withholds one, per the pass-detection contract in its
 * own module doc — this file only reports "reached the end", it does
 * not decide whether the lap counts.
 *
 * A phase vocoder's analysis window has no idea the content just looped,
 * so the seam lands wherever the window happens to be — not sample-exact
 * — and that is the expected, already-documented Phase 0 defect (a tick
 * once per loop; docs/03-audio-engine.md, trap 3). Fixing that means a
 * pre-rendered AudioBuffer with a native, sample-exact loop, which is
 * Phase 2's BufferEngine, not this one — CLAUDE.md's "practice speeds
 * are discrete" invariant and the plan's "a stated exception, not a
 * silent breach" cover why that is deliberate here.
 *
 * ---- The start-of-stream warm-up, and why it is handled here ----
 * R3 requires `preferredStartPad` frames of silence fed before the first
 * real input so its analysis windows have context, and reports
 * `startDelay` frames of output that correspond to that pad and must be
 * discarded before what comes out means anything (measured
 * 2026-09-05: 2048/2048 frames on R3, 2170 with a pitch shift, 1024/1024
 * on R2 — docs/03-audio-engine.md). That accounting is mechanical — it
 * is how the WASM API is driven correctly at all, on every reset of the
 * stream (construction, and every restart()) — and is a different thing
 * from the source-time/playback-time mapping clock.py owns for the
 * offline-rendered cache (Phase 2): this engine has no rendered file and
 * makes no claim about mapping its output frames back to source seconds.
 * `toDrop` below is this file's own bookkeeping for the mechanical half;
 * nothing here recomputes or second-guesses the clock.py half.
 *
 * ---- What this file must never do ----
 * Call `performance.now()`. `performance` does not exist in
 * AudioWorkletGlobalScope (measured, docs/03-audio-engine.md) — there is
 * no timing instrumentation left in this file for exactly that reason.
 * The one clock this scope has, and the one `boundary` messages carry,
 * is `currentTime` — the global that already mirrors the owning
 * AudioContext's currentTime, with no `performance` involved.
 *
 * ---- Message protocol (AudioWorkletNode.port) ----
 * Main -> worklet: {type:'play'} {type:'pause'} {type:'restart'}
 *   {type:'setRatio', ratio} {type:'setPitch', scale} {type:'destroy'}
 * Worklet -> main: {type:'ready'} (once, after construction succeeds)
 *   {type:'boundary', contextTime} (once per completed lap)
 */

const OPT = {
  ProcessRealTime: 0x00000001,
  ThreadingNever: 0x00010000,
  FormantPreserved: 0x01000000,
  EngineFiner: 0x20000000, // R3 -- CLAUDE.md: "Rubber Band, --fine, --formant"
};

class RubberBandLooper extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = options.processorOptions;
    this.channels = o.channels;
    this.source = o.source; // one Float32Array per channel, pre-roll included
    this.sourceLen = o.source[0].length;
    this.loopStartFrame = o.loopStartFrame; // index into `source` where the pre-roll ends
    this.blockSize = o.blockSize || 1024;
    this.playing = false; // gate: while false, process() emits silence and consumes nothing
    this.destroyed = false;

    // Standalone instantiation, no Emscripten glue: nine imports, none of
    // which this path ever actually calls (see web/vendor/README.md for
    // provenance). `module` arrived through processorOptions -- it is
    // structured-cloneable, so there is no fetch and no async gap here.
    const inst = new WebAssembly.Instance(o.module, {
      env: { emscripten_notify_memory_growth: () => this._refresh() },
      wasi_snapshot_preview1: {
        fd_close: () => 0, fd_write: () => 0, fd_seek: () => 0, fd_read: () => 0,
        environ_sizes_get: () => 0, environ_get: () => 0, clock_time_get: () => 0,
        proc_exit: () => { throw new Error('proc_exit'); },
      },
    });
    this.x = inst.exports;
    if (this.x._initialize) this.x._initialize();
    this._refresh();

    const rbOpts = OPT.ProcessRealTime | OPT.ThreadingNever | OPT.FormantPreserved | OPT.EngineFiner;
    this.rb = this.x.rb_new(sampleRate, this.channels, rbOpts, o.timeRatio, o.pitchScale || 1.0);
    this.x.rb_set_max_process_size(this.rb, this.blockSize);

    // Scratch in WASM memory: one pointer array plus one buffer per
    // channel, each direction (input to Rubber Band, output from it).
    this.inPtrs = this.x.wasm_malloc(this.channels * 4);
    this.outPtrs = this.x.wasm_malloc(this.channels * 4);
    this.inBuf = [];
    this.outBuf = [];
    for (let c = 0; c < this.channels; c++) {
      this.inBuf.push(this.x.wasm_malloc(this.blockSize * 4));
      this.outBuf.push(this.x.wasm_malloc(this.blockSize * 4));
    }
    this._refresh();
    for (let c = 0; c < this.channels; c++) {
      this.HEAPU32[(this.inPtrs >> 2) + c] = this.inBuf[c];
      this.HEAPU32[(this.outPtrs >> 2) + c] = this.outBuf[c];
    }

    this.startDelay = this.x.rb_get_start_delay(this.rb);
    this.preferredStartPad = this.x.rb_get_preferred_start_pad(this.rb);
    this.readPos = 0;
    this.toDrop = this.startDelay; // output frames still to discard before real audio begins
    this._feedSilence(this.preferredStartPad);

    this.port.onmessage = (e) => this._onMessage(e.data);
    this.port.postMessage({ type: 'ready' });
  }

  _refresh() {
    const b = this.x.memory.buffer;
    this.HEAPF32 = new Float32Array(b);
    this.HEAPU32 = new Uint32Array(b);
  }

  // ALLOW_MEMORY_GROWTH can detach the buffer views; re-derive them
  // whenever the underlying ArrayBuffer has been replaced.
  _heaps() {
    if (this.HEAPF32.buffer !== this.x.memory.buffer) this._refresh();
  }

  _feedSilence(n) {
    while (n > 0) {
      const k = Math.min(n, this.blockSize);
      this._heaps();
      for (let c = 0; c < this.channels; c++) {
        this.HEAPF32.fill(0, this.inBuf[c] >> 2, (this.inBuf[c] >> 2) + k);
      }
      this.x.rb_process(this.rb, this.inPtrs, k, 0);
      n -= k;
    }
  }

  /**
   * Feed up to n frames from `source`, wrapping `readPos` to
   * `loopStartFrame` (never back to 0 -- the pre-roll is a first-lap-only
   * lead-in) each time it reaches `sourceLen`, and posting the boundary
   * that is this file's half of the pass-detection contract.
   */
  _feedSource(n) {
    while (n > 0) {
      const k = Math.min(n, this.blockSize, this.sourceLen - this.readPos);
      this._heaps();
      for (let c = 0; c < this.channels; c++) {
        const src = this.source[c].subarray(this.readPos, this.readPos + k);
        this.HEAPF32.set(src, this.inBuf[c] >> 2);
      }
      this.readPos += k;
      this.x.rb_process(this.rb, this.inPtrs, k, 0); // never final -- see module doc on looping
      n -= k;
      if (this.readPos >= this.sourceLen) {
        this.readPos = this.loopStartFrame;
        this.port.postMessage({ type: 'boundary', contextTime: currentTime });
      }
    }
  }

  _onMessage(msg) {
    switch (msg.type) {
      case 'play':
        this.playing = true;
        break;
      case 'pause':
        this.playing = false;
        break;
      case 'restart':
        // Back to the true beginning, pre-roll included, with a fresh
        // analysis state -- rb_reset clears the phase-vocoder history so
        // the old position's content cannot smear into the new one.
        this.x.rb_reset(this.rb);
        this.readPos = 0;
        this.toDrop = this.startDelay;
        this._feedSilence(this.preferredStartPad);
        this.playing = true;
        break;
      case 'setRatio':
        this.x.rb_set_time_ratio(this.rb, msg.ratio);
        break;
      case 'setPitch':
        this.x.rb_set_pitch_scale(this.rb, msg.scale);
        break;
      case 'destroy':
        this._teardown();
        break;
      default:
        break;
    }
  }

  _teardown() {
    if (this.destroyed) return;
    this.destroyed = true;
    for (const p of this.inBuf) this.x.wasm_free(p);
    for (const p of this.outBuf) this.x.wasm_free(p);
    this.x.wasm_free(this.inPtrs);
    this.x.wasm_free(this.outPtrs);
    this.x.rb_delete(this.rb);
  }

  process(inputs, outputs) {
    if (this.destroyed) return false;
    const out = outputs[0];
    const need = out[0].length;

    if (!this.playing) {
      for (let c = 0; c < out.length; c++) out[c].fill(0);
      return true;
    }

    let filled = 0;
    let guard = 0;
    // Two things this loop can be doing on any given iteration: discarding
    // warm-up output (toDrop > 0) or filling real output (toDrop == 0) --
    // both pull from the same rb_retrieve, so one loop covers both, and
    // "ensure something is available" (feeding the stretcher from `source`)
    // is common to either. guard bounds it against ever spinning forever.
    while ((this.toDrop > 0 || filled < need) && guard++ < 512) {
      while (this.x.rb_available(this.rb) <= 0 && guard++ < 512) {
        const req = this.x.rb_get_samples_required(this.rb);
        this._feedSource(Math.min(this.blockSize, Math.max(req, 128)));
      }
      const avail = this.x.rb_available(this.rb);
      if (avail <= 0) break; // should not happen (the section loops forever) -- guard, not expected
      const want = this.toDrop > 0
        ? Math.min(avail, this.blockSize, this.toDrop)
        : Math.min(avail, this.blockSize, need - filled);
      const got = this.x.rb_retrieve(this.rb, this.outPtrs, want);
      this._heaps();
      if (this.toDrop > 0) {
        this.toDrop -= got; // warm-up output, discarded -- see module doc
      } else {
        for (let c = 0; c < this.channels; c++) {
          const dst = out[Math.min(c, out.length - 1)];
          const base = this.outBuf[c] >> 2;
          for (let i = 0; i < got; i++) dst[filled + i] = this.HEAPF32[base + i];
        }
        filled += got;
      }
    }
    for (let c = 0; c < out.length; c++) {
      for (let i = filled; i < need; i++) out[c][i] = 0;
    }
    return true;
  }
}

registerProcessor('woodshed-rubberband', RubberBandLooper);
