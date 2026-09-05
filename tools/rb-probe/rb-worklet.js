// A pull-driven Rubber Band real-time stretcher in an AudioWorklet.
//
// This is the probe that answers "does the WASM build survive a ratio of 2.0 in a
// worklet", and it is also the shape `web/player.js` (plan unit D4) has to take:
// **the worklet owns the source samples and feeds the stretcher on demand.**
//
// The obvious alternative — take 128 frames from an upstream node, give 128 frames
// back, once per quantum — cannot work at any ratio but 1.0. At a time ratio of 2.0
// the stretcher emits two output frames for every one consumed, so a quantum-in /
// quantum-out processor accumulates a frame of surplus per frame of input and its
// latency grows without bound; below 1.0 it starves instead. That is the flaw in the
// published `rubberband-web` worklet and it is why we do not use it.
//
// Instantiated directly from the standalone .wasm — no Emscripten JS glue. The build
// imports nine WASI symbols that Rubber Band never calls on this path, so they are
// stubbed. See ./README.md for provenance.

const OPT = {
  ProcessRealTime: 0x00000001,
  ThreadingNever: 0x00010000,
  FormantPreserved: 0x01000000,
  PitchHighQuality: 0x02000000,
  EngineFiner: 0x20000000,        // R3; absent means R2 ("faster")
};

class RbProbe extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = options.processorOptions;
    this.channels = o.channels;
    this.source = o.source;                     // one Float32Array per channel
    this.sourceLen = o.source[0].length;
    this.blockSize = o.blockSize || 1024;
    this.ratioSchedule = o.ratioSchedule || []; // [{atOutputFrame, ratio}]

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

    let opts = OPT.ProcessRealTime | OPT.ThreadingNever | OPT.FormantPreserved;
    if (o.engine === 'finer') opts |= OPT.EngineFiner;
    if (o.pitchHighQuality) opts |= OPT.PitchHighQuality;

    this.rb = this.x.rb_new(sampleRate, this.channels, opts, o.timeRatio, o.pitchScale || 1.0);
    this.x.rb_set_max_process_size(this.rb, this.blockSize);

    // Scratch in wasm memory: a pointer array plus one buffer per channel, each way.
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

    this.stats = {
      options: opts,
      preferredStartPad: this.x.rb_get_preferred_start_pad(this.rb),
      startDelay: this.x.rb_get_start_delay(this.rb),
      latency: this.x.rb_get_latency(this.rb),
      quanta: 0, underrunQuanta: 0, outFrames: 0, inFrames: 0,
      nanQuanta: 0, silentQuantaAfterPrime: 0, processCalls: 0,
      maxRetrieveShortfall: 0, ratioChanges: [],
    };

    this.readPos = 0;                     // next source frame to feed
    this.toDrop = this.stats.startDelay;  // output frames the engine says to discard
    this.done = false;

    // `performance` is NOT exposed in AudioWorkletGlobalScope in Chrome, so the
    // per-quantum histogram falls back to Date.now and is only good to 1 ms. The
    // decisive number is not in here — it is whether a real-time context keeps up
    // over 30 s, which the page measures from outside.
    this.hasPerf = typeof performance !== 'undefined' && !!performance.now;
    this.clock = this.hasPerf ? () => performance.now() : () => Date.now();
    this.stats.hasPerformanceNow = this.hasPerf;
    this.stats.timing = {
      bins: [0, 0, 0, 0, 0, 0], max: 0, maxSteady: 0,
      overBudgetSteady: 0, overBudgetAt: [], totalMs: 0,
    };
    this.binEdges = [0.25, 0.5, 1, 2, 2.9];  // 2.9 ms = one 128-frame quantum at 44.1k

    // Rubber Band asks for a pad of silence up front so that the first real output
    // frame lines up with the first input frame. Feed it, then drop `startDelay`
    // frames of output. Both numbers matter to clock.py's source<->playback mapping.
    this._feedSilence(this.stats.preferredStartPad);
  }

  _refresh() {
    const b = this.x.memory.buffer;
    this.HEAPF32 = new Float32Array(b);
    this.HEAPU32 = new Uint32Array(b);
  }

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
      this.stats.processCalls++;
      n -= k;
    }
  }

  _feedSource(n) {
    while (n > 0) {
      const k = Math.min(n, this.blockSize, this.sourceLen - this.readPos);
      if (k <= 0) { this.done = true; return; }
      this._heaps();
      for (let c = 0; c < this.channels; c++) {
        const src = this.source[c].subarray(this.readPos, this.readPos + k);
        this.HEAPF32.set(src, this.inBuf[c] >> 2);
      }
      this.readPos += k;
      this.x.rb_process(this.rb, this.inPtrs, k, this.readPos >= this.sourceLen ? 1 : 0);
      this.stats.processCalls++;
      this.stats.inFrames += k;
      n -= k;
    }
  }

  process(inputs, outputs) {
    const out = outputs[0];
    const need = out[0].length;
    const t0 = this.clock();
    this.stats.quanta++;

    // Scheduled ratio changes, standing in for a dragged speed slider.
    while (this.ratioSchedule.length &&
           this.stats.outFrames >= this.ratioSchedule[0].atOutputFrame) {
      const ev = this.ratioSchedule.shift();
      this.x.rb_set_time_ratio(this.rb, ev.ratio);
      this.stats.ratioChanges.push({ at: this.stats.outFrames, ratio: ev.ratio });
    }

    // Pull-drive: ask the stretcher what it wants, until it can hand back a quantum.
    let guard = 0;
    while (this.x.rb_available(this.rb) < need && !this.done && guard++ < 64) {
      const req = this.x.rb_get_samples_required(this.rb);
      this._feedSource(Math.min(this.blockSize, Math.max(req, 128)));
    }

    const avail = this.x.rb_available(this.rb);
    if (avail < need && !this.done) {
      this.stats.underrunQuanta++;
      this.stats.maxRetrieveShortfall =
        Math.max(this.stats.maxRetrieveShortfall, need - avail);
    }

    const got = this.x.rb_retrieve(this.rb, this.outPtrs, Math.min(need, Math.max(avail, 0)));
    this._heaps();
    let nan = false, peak = 0;
    for (let c = 0; c < this.channels; c++) {
      const dst = out[Math.min(c, out.length - 1)];
      const base = this.outBuf[c] >> 2;
      for (let i = 0; i < got; i++) {
        const v = this.HEAPF32[base + i];
        if (!Number.isFinite(v)) nan = true;
        const a = v < 0 ? -v : v;
        if (a > peak) peak = a;
        dst[i] = v;
      }
      for (let i = got; i < need; i++) dst[i] = 0;
    }
    if (nan) this.stats.nanQuanta++;
    this.stats.outFrames += got;
    if (this.toDrop <= 0 && peak === 0 && got > 0) this.stats.silentQuantaAfterPrime++;
    this.toDrop -= got;

    const dt = this.clock() - t0;
    const T = this.stats.timing;
    T.totalMs += dt;
    if (dt > T.max) T.max = dt;
    let b = 0;
    while (b < this.binEdges.length && dt >= this.binEdges[b]) b++;
    T.bins[b]++;
    if (this.stats.quanta > 200) {         // past JIT and wasm warm-up
      if (dt > T.maxSteady) T.maxSteady = dt;
      if (dt >= 2.9) {
        T.overBudgetSteady++;
        if (T.overBudgetAt.length < 20) T.overBudgetAt.push([this.stats.quanta, dt]);
      }
    }

    if (this.done && this.x.rb_available(this.rb) <= 0) {
      this.port.postMessage({ type: 'stats', stats: this.stats });
      return false;
    }
    return true;
  }
}

registerProcessor('rb-probe', RbProbe);
