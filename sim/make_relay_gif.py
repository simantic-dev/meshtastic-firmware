"""sim/relay.gif from the stderr of the 3-node run (MESH_AIR_LOG=1): TX start/done per node with time-on-air bars.
Modelled on the MeshCore sim's make_gif.py. Usage: uv run --with pillow sim/make_relay_gif.py air.log [out.gif]"""
import re, sys
from PIL import Image, ImageDraw
log, out = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "sim/relay.gif")
tx = []                                        # [node, bytes, start_s, end_s]
for l in open(log):
    if m := re.match(r"SX1262: TX start node(\d) (\d+)B .* t=(\d+)", l):
        tx.append([int(m[1]), int(m[2]), int(m[3]) / 1e6, None])
    elif m := re.match(r"\[radio\] SX1262 TX done .* at (\d+) us", l):
        next(x for x in tx if x[3] is None)[3] = int(m[1]) / 1e6
tx = [t for t in tx if t[1] == 34]             # the text packet and its relays (104 B frames are NodeInfo)
names = "abc"; T0, T1 = 12.0, 33.0; W, H, L = 900, 330, 80
x = lambda t: L + (t - T0) / (T1 - T0) * (W - L - 20)
Y = {0: 90, 1: 150, 2: 210}; N = 60; frames = []
for i in range(N + 1):
    tn = T0 + (T1 - T0) * i / N
    im = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(im)
    d.text((10, 10), "Meshtastic 3x RAK4631 on pyrite: text from a -> b -> c (a and c out of range), LongFast SF11 BW250", fill="black")
    for n, y in Y.items():
        d.text((10, y - 6), f"node {names[n]}", fill="black"); d.line([(L, y + 14), (W - 20, y + 14)], fill="#ddd")
    for s in range(14, 34, 4): d.text((x(s) - 8, 255), f"{s} s", fill="#666")
    for n, b, t0, t1 in tx:
        if t0 <= tn:
            d.rectangle([x(t0), Y[n] - 8, x(min(tn, t1)), Y[n] + 12], fill="#e94" if tn < t1 else "#36c")
            if tn >= t1: d.text((x(t0) + 4, Y[n] - 24), f"{b} B, {(t1 - t0) * 1e3:.0f} ms, hop @ {t0:.3f} s", fill="black")
    d.text((10, 290), f"virtual t = {tn:6.3f} s   (relayed opaquely; text NOT decoded: see README)", fill="black")
    frames.append(im)
frames[0].save(out, save_all=True, append_images=frames[1:] + [frames[-1]] * 25, duration=70, loop=0)
print(out, len(frames), tx)
