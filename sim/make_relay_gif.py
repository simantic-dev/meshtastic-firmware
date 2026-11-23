"""sim/relay.gif: scene 1 text a -> b -> c, scene 2 a and c collide at b. Every number is parsed from the two runs' logs
(virtual time). Usage, ANSI already stripped from the stdouts:
  uv run --with pillow sim/make_relay_gif.py relay.air relay.out collide.air collide.out sim/relay.gif
*.air = stderr with MESH_AIR_LOG=1 (peer TX start/done), *.out = stdout ([b]/[c] firmware logs)."""
import re, sys
from PIL import Image, ImageDraw, ImageFont
air1, out1, air2, out2, gif = sys.argv[1:6]
NAMES = "abc"


def txs(path):                                  # [node, bytes, start_s, end_s]
    tx = []
    for l in open(path):
        if m := re.match(r"SX1262: TX start node(\d) (\d+)B .* t=(\d+)", l):
            tx.append([int(m[1]), int(m[2]), int(m[3]) / 1e6, None])
        elif m := re.match(r"\[sx1262\] SX1262 TX done (\d+) B .* at (\d+) us", l):
            next(x for x in tx if x[3] is None and x[1] == int(m[1]))[3] = int(m[2]) / 1e6
    return [x for x in tx if x[3]]


def first(path, node, pat):                     # (time_s, match) of the first matching log line of node
    for l in open(path, errors="ignore"):
        if (m := re.match(rf"\[{node}\] \[([\d.]+)s\] \(usbd\) (.*)", l)) and (g := re.search(pat, m[2])):
            return float(m[1]), g
    sys.exit(f"no {pat!r} from {node} in {path}")


LVL = r"fr=(0x[0-9a-f]+).*rxSNR=(\S+) rxRSSI=(\S+)"
_, ab = first(out1, "b", r"Lora RX .*" + LVL)    # a -> b, as b's firmware reads it
_, bc = first(out1, "c", r"Lora RX .*" + LVL)    # b -> c
t_rx, rx = first(out1, "c", r"Received text msg from=(\S+), id=\S+ msg=(.*\S)")
t_err, err = first(out2, "b", r"(Ignore rx packet, error=-?\d+)")
tx1 = [t for t in txs(air1) if t[1] == 34 and t[2] <= t_rx]       # the text and b's relay of it
tx2 = [t for t in txs(air2) if t[0] != 1 and t[2] <= t_err < t[3] + 1.5]   # a's and c's frames that b was hearing

W, H = 900, 400
f12, f14, f18 = (ImageFont.load_default(s) for s in (12, 14, 18))
P = {0: (130, 150), 1: (450, 150), 2: (770, 150)}


def base(title, t, t0, t1, tx, marks):
    im = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(im)
    d.text((16, 12), title, fill="black", font=f18)
    d.text((16, 36), "Meshtastic, 3x RAK4631 (nRF52840 + SX1262) on pyrite, LongFast SF11 BW250, stock build", fill="#555", font=f12)
    for i, j, lv in ((0, 1, ab), (1, 2, bc)):
        d.line([P[i], P[j]], fill="#9ab", width=3)
        d.text(((P[i][0] + P[j][0]) // 2 - 75, P[i][1] - 32), f"RSSI {lv[3]} dBm  SNR {lv[2]} dB", fill="#246", font=f12)
    d.arc([P[0][0], 70, P[2][0], 230], 200, 340, fill="#d99", width=1)
    d.text((W // 2 - 55, 70), "a - c: out of range", fill="#c55", font=f12)
    d.text((W // 2 - 140, 240), "link levels: declared in three.yaml, not measured", fill="#888", font=f12)
    for n, (x, y) in P.items():
        d.ellipse([x - 18, y - 18, x + 18, y + 18], fill="#eef", outline="#336", width=2)
        d.text((x - 5, y - 11), NAMES[n], fill="black", font=f18)
    L, R, Y0 = 60, W - 20, 285                  # timeline strip
    X = lambda s: L + (s - t0) / (t1 - t0) * (R - L)
    for n in range(3):
        d.text((16, Y0 + n * 22), f"{NAMES[n]} TX", fill="black", font=f12)
        d.line([(L, Y0 + n * 22 + 7), (R, Y0 + n * 22 + 7)], fill="#eee")
    for n, b, s, e in tx:
        if s <= t:
            d.rectangle([X(s), Y0 + n * 22, X(min(t, e)), Y0 + n * 22 + 14], fill="#e94" if t < e else "#36c")
            if t >= e: d.text((X(e) + 4, Y0 + n * 22 + 1), f"{s:.3f}-{e:.3f} s, {b} B", fill="#36c", font=f12)
    for ts, col in marks:
        if ts <= t: d.line([(X(ts), Y0 - 4), (X(ts), Y0 + 62)], fill=col, width=2)
    d.line([(X(t), Y0 - 6), (X(t), Y0 + 64)], fill="#888")
    d.text((16, H - 22), f"virtual t = {t:7.3f} s   (every number on screen is parsed from the run logs)", fill="black", font=f12)
    return im, d


def dot(d, x, col):
    d.ellipse([x - 8, P[0][1] - 8, x + 8, P[0][1] + 8], fill=col)


frames, durs = [], []
t0, t1 = tx1[0][2] - 0.4, t_rx + 0.8            # scene 1: relay
for k in range(61):
    t = t0 + (t1 - t0) * k / 60
    im, d = base("1. text from a reaches c only through b", t, t0, t1, tx1, [(t_rx, "#080")])
    for n, b, s, e in tx1:
        if s <= t < e: dot(d, P[n][0] + (P[n + 1][0] - P[n][0]) * (t - s) / (e - s), "#e94")
    if t >= t_rx:
        d.text((P[2][0] - 170, 192), f"c @ {t_rx:.3f} s: \"{rx[2]}\"", fill="#080", font=f14)
        d.text((P[2][0] - 170, 210), f"from {rx[1]} (a), rebroadcast by b", fill="#080", font=f12)
    frames.append(im); durs.append(70)
durs[-1] = 2500
t0, t1 = min(t[2] for t in tx2) - 0.4, t_err + 1.0   # scene 2: collision
both = max(t[2] for t in tx2)
for k in range(61):
    t = t0 + (t1 - t0) * k / 60
    im, d = base("2. hidden terminal: a and c send at once, the frames collide at b", t, t0, t1, tx2, [(t_err, "#c00")])
    for n, b, s, e in tx2:
        if s <= t < e:
            dot(d, P[n][0] + (P[1][0] - P[n][0]) * min(1, (t - s) / (e - s)), "#d22" if t >= both else "#e94")
    if t >= t_err:
        d.text((P[1][0] - 200, 192), f"b @ {t_err:.3f} s: {err[1]}", fill="#c00", font=f14)
        d.text((P[1][0] - 200, 210), "RadioLib CRC mismatch: the frame is dropped at b", fill="#c00", font=f12)
    frames.append(im); durs.append(70)
durs[-1] = 3000
frames[0].save(gif, save_all=True, append_images=frames[1:], duration=durs, loop=0, optimize=True)
print(gif, len(frames), tx1, tx2, t_rx, t_err)
