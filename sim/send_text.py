"""Host side of the demo: types one text message into node A through the Meshtastic serial API over USB CDC.
usage: uv run --with meshtastic python3 -I sim/send_text.py <port> <log-out> [text] [at_us]    (sim started with --usb-raw a.usbd:<port>)
With at_us the frame is written once virtual time reaches at_us (the collision scene in sim/README.md uses it to make
two nodes send close together); without it, 3 s of wall time after the handshake.
It is the USB host: enumerates (SET_ADDRESS, SET_CONFIGURATION, DTR), copies A's CDC log to <log-out>, and once the radio
is configured writes one ToRadio frame (0x94 0xC3 len16 + protobuf): a broadcast TEXT_MESSAGE_APP packet. The frame holds
a NUL byte, which `--usb-cdc-console NAME=text` cannot carry (the text is a UTF-8 argv string), hence --usb-raw."""
import json, socket, struct, sys, time
from meshtastic.protobuf import mesh_pb2, portnums_pb2

port, out = int(sys.argv[1]), sys.argv[2]
text = sys.argv[3] if len(sys.argv) > 3 else "Hello from A"
at_us = int(sys.argv[4]) if len(sys.argv) > 4 else None
t = mesh_pb2.ToRadio()
p = t.packet
p.to, p.id, p.hop_limit = 0xFFFFFFFF, 0x5A5A0001, 3
p.decoded.portnum, p.decoded.payload = portnums_pb2.TEXT_MESSAGE_APP, text.encode()
b = t.SerializeToString()
def fr(b): return b"\x94\xc3" + struct.pack(">H", len(b)) + b
frame = fr(b)
w = mesh_pb2.ToRadio(); w.want_config_id = 0x1234           # the app handshake first: the API treats the sender as a client from here
hello = fr(w.SerializeToString())

for _ in range(300):
    try:
        s = socket.create_connection(("127.0.0.1", port)); break
    except OSError:
        time.sleep(1)
f = s.makefile("rw")
def req(**r):
    f.write(json.dumps(r) + "\n"); f.flush()
    while True:
        m = json.loads(f.readline())
        if "event" not in m or m["event"] != "in":
            return m
        log(m)
t0 = time.time()
logf = open(out, "wb")
tl = open(out + ".t", "w")      # virtual time (us) and size of every CDC IN chunk
buf = b""
def log(m):
    global buf
    d = bytes.fromhex(m["data"]); logf.write(d); logf.flush(); buf += d
    tl.write("%s %d\n" % (m.get("t_us"), len(d))); tl.flush()
req(op="reset"); time.sleep(1)
print(req(op="setup", bmRequestType=0, bRequest=5, wValue=1, wIndex=0, wLength=0, data=""))
print(req(op="setup", bmRequestType=0, bRequest=9, wValue=1, wIndex=0, wLength=0, data=""))
print(req(op="setup", bmRequestType=0x21, bRequest=0x22, wValue=3, wIndex=0, wLength=0, data=""))   # DTR+RTS
print(req(op="subscribe", ep=0x82))
sent = False
while time.time() - t0 < 900:
    line = f.readline()
    if not line:
        break
    m = json.loads(line)
    if m.get("event") not in (None, "in"): print(m)
    if m.get("event") == "in":
        log(m)
    if not sent and b"Set radio: region=US" in buf:
        time.sleep(3)
        for a in ((0, 9, 1, 0), (0x21, 0x22, 3, 0)):   # the node reboots once after saving its first config: configure again
            req(op="setup", bmRequestType=a[0], bRequest=a[1], wValue=a[2], wIndex=a[3], wLength=0, data="")
        print(req(op="out", ep=1, data=hello.hex()))
        if at_us:   # poll virtual time with a harmless SET_CONTROL_LINE_STATE (the log goes quiet once the API is up)
            while req(op="setup", bmRequestType=0x21, bRequest=0x22, wValue=3, wIndex=0, wLength=0, data="")["t_us"] < at_us:
                time.sleep(0.01)
        else:
            time.sleep(3)
        for i in range(0, len(frame), 64):
            print(req(op="out", ep=1, data=frame[i:i + 64].hex()))
        logf.write(b"\n[send_text] wrote ToRadio frame, %d bytes, text=%r\n" % (len(frame), text.encode())); logf.flush()
        sent = True
