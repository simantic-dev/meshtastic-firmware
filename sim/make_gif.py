"""Render sim/boot.gif from a `sim run --record` bundle: boot milestones on a virtual-time axis, USB CDC log lines, and the fault.
Usage: uv run --with pillow sim/make_gif.py <rec_dir> [out.gif]"""
import json, re, sys
from PIL import Image, ImageDraw

rec, out = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "sim/boot.gif")
load = lambda k: [json.loads(l) for l in open(f"{rec}/{k}.jsonl")]
ansi = re.compile(r"\x1b\[[0-9;]*m")
lines = [(e["t"], ansi.sub("", e["text"]).strip()) for e in load("uart")]
lines = [(t, s) for t, s in lines if "|" in s]
fault = load("fault")[0]["t"]
MILE = [("Scan for i2c devices", "I2C scan"), ("Init NodeDB", "NodeDB"), ("Start meshradio init", "radio init"),
        ("SX1262 init success", "SX1262 up"), ("PowerFSM init", "PowerFSM"), ("Init the Bluef", "Bluefruit init")]
ms = [(next(t for t, s in lines if k in s), n) for k, n in MILE] + [(fault, "S140 assert")]
T1 = 9.0
W, H, L = 900, 330, 40
x = lambda t: L + t / T1 * (W - 2 * L)
Y = 150
frames = []
N = 60
for i in range(N + 1):
    tn = T1 * i / N
    im = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(im)
    d.text((10, 10), "Meshtastic RAK4631 (nRF52840 + SX1262) on pyrite sim - boot, virtual time", fill="black")
    d.line([(x(0), Y), (x(T1), Y)], fill="#999", width=2)
    for s in range(10):
        d.line([(x(s), Y - 4), (x(s), Y + 4)], fill="#999"); d.text((x(s) - 6, Y + 8), f"{s} s", fill="#666")
    n = sum(1 for t, _ in lines if t <= tn)
    d.rectangle([x(0), Y - 6, x(min(tn, T1)), Y + 6], fill="#8bd")
    for j, (t, name) in enumerate(ms):
        if t <= tn:
            col = "#c33" if name == "S140 assert" else "#2b7"
            d.line([(x(t), Y), (x(t), Y - 30 - 22 * (j % 3))], fill=col, width=2)
            d.text((x(t) + 4 if j < 5 else x(t) - 110, Y - 44 - 22 * (j % 3)), f"{name} {t:.3f}s", fill=col)
    d.text((10, 270), f"virtual t = {tn:5.2f} s    USB CDC log lines = {n}/{len(lines)}", fill="black")
    d.text((10, 290), f"fault at {fault:.3f} s (S140 6.1.1 svc 255 in a 512-word sd_flash_write at tWRITE 41 us)", fill="#c33" if tn >= fault else "#bbb")
    frames.append(im)
frames[0].save(out, save_all=True, append_images=frames[1:] + [frames[-1]] * 25, duration=70, loop=0)
print(out, len(frames))
