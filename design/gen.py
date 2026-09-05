import math, json

def rnd(i, seed):
    x = math.sin((i + 1) * 12.9898 + seed * 78.233) * 43758.5453
    return x - math.floor(x)

def wave(n, width, height, seed=1.0, energy=None, gap=1.6):
    """Vertical-bar waveform as one SVG path, mirrored about the mid line."""
    step = width / n
    bw = max(1.2, step - gap)
    mid = height / 2
    parts = []
    for i in range(n):
        t = i / (n - 1)
        e = energy(t) if energy else 1.0
        # transient-ish peaks plus body
        body = 0.34 + 0.42 * rnd(i, seed)
        peak = 1.0 if rnd(i, seed + 9) > 0.93 else 0.0
        env = 0.55 + 0.45 * math.sin(t * math.pi * 17 + seed)
        a = e * (body * (0.7 + 0.3 * env) + peak * 0.28)
        h = max(0.8, min(1.0, a) * mid * 0.96)
        x = round(i * step + bw / 2 + 0.5, 1)
        parts.append(f"M{x} {round(mid-h,1)}V{round(mid+h,1)}")
    return "".join(parts), round(bw, 2)

def song_energy(t):
    # intro / verse / chorus / verse / solo / outro
    pts = [(0.00,0.55),(0.08,0.72),(0.16,0.60),(0.30,0.95),(0.40,0.62),
           (0.52,0.98),(0.60,0.70),(0.72,1.00),(0.86,0.88),(0.95,0.72),(1.0,0.20)]
    for (a,va),(b,vb) in zip(pts, pts[1:]):
        if a <= t <= b:
            k = (t-a)/(b-a) if b > a else 0
            return va + (vb-va)*k
    return 0.6

def solo_energy(t):
    return 0.62 + 0.34 * math.sin(t * math.pi * 3.1) ** 2 + (0.12 if t > 0.9 else 0)

out = {}
d, bw = wave(420, 1180, 132, seed=3.1, energy=song_energy, gap=1.4)
out["WAVE_SONG"] = d; out["WAVE_SONG_BW"] = bw
d, bw = wave(300, 1776, 104, seed=7.7, energy=solo_energy, gap=2.6)
out["WAVE_SECTION"] = d; out["WAVE_SECTION_BW"] = bw
d, bw = wave(150, 1180, 64, seed=4.2, energy=solo_energy, gap=2.2)
out["WAVE_INSPECT"] = d; out["WAVE_INSPECT_BW"] = bw

# sparklines: speed over time, monotone-ish rising with plateaus
def spark(vals, w, h, pad=2):
    n = len(vals)
    lo, hi = 40, 100
    pts = []
    for i, v in enumerate(vals):
        x = pad + (w - 2*pad) * i / (n - 1)
        y = pad + (h - 2*pad) * (1 - (v - lo) / (hi - lo))
        pts.append((round(x,1), round(y,1)))
    d = "M" + " L".join(f"{x} {y}" for x, y in pts)
    return d, pts[-1]

series = {
 "SPARK_INTRO":[50,55,55,60,65,65,70,75,80,85,90,95,100],
 "SPARK_VERSE":[50,55,60,60,65,70,75,80,85,90,90,95,95],
 "SPARK_SOLO1":[50,50,55,55,55,60,60,60,65,65,70,70,75],
 "SPARK_SOLO2":[50,50,50,55,55,55,55,60,60,60,60,60,65],
 "SPARK_OUTRO":[55,60,65,70,75,80,85,90,95,95,100,100,100],
 "SPARK_CHORUS":[50,55,60,65,70,75,80,85,85,90,95,95,100],
}
for k, v in series.items():
    d, last = spark(v, 148, 40)
    out[k] = d; out[k+"_X"] = last[0]; out[k+"_Y"] = last[1]

# readiness over time area, 0..100 across 26 weeks
ready = [8,10,13,15,15,19,22,24,27,29,29,33,36,40,43,45,48,50,54,57,59,61,64,66,69,74]
w, h, pad = 1180, 150, 6
pts = []
for i, v in enumerate(ready):
    x = pad + (w-2*pad)*i/(len(ready)-1)
    y = pad + (h-2*pad)*(1 - v/100)
    pts.append((round(x,1), round(y,1)))
out["READY_LINE"] = "M" + " L".join(f"{x} {y}" for x,y in pts)
out["READY_AREA"] = out["READY_LINE"] + f" L{pts[-1][0]} {h} L{pts[0][0]} {h} Z"
out["READY_END_X"], out["READY_END_Y"] = pts[-1]

json.dump(out, open("gen.json","w"))
print("keys:", len(out))
print("song len", len(out["WAVE_SONG"]), "section len", len(out["WAVE_SECTION"]))
