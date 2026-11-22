#!/bin/sh
# Build rak4631 (optionally BT-less) and make sim/bl0.hex (MBR + S140 6.1.1 + bootloader, UICR records dropped).
# Run from the repo root. Outputs: sim/fw.elf, sim/bl0.hex (not committed: Nordic blobs / build output).
set -e
PLATFORMIO_BUILD_FLAGS="$EXTRA" pio run -e rak4631
cp .pio/build/rak4631/firmware-rak4631-*.elf sim/fw.elf
python3 -I - <<'P'
import glob, os
src = glob.glob(os.path.expanduser("~/.platformio/packages/framework-arduinoadafruitnrf52/bootloader/feather_nrf52840_express/*s140_6.1.1.hex"))[0]
out, skip = [], False
for l in open(src):
    l = l.strip()
    if l[7:9] == "04": skip = l[9:13] == "1000"   # UICR segment is not loadable
    if skip and l[7:9] in ("00", "04"): continue
    out.append(l)
open("sim/bl0.hex", "w").write("\n".join(out) + "\n")
P
# run: sim NRF52840QIAA run --board sim/rak.board --elf sim/bl0.hex --elf sim/fw.elf --timeout 12 --usb-cdc-console usbd --ascii
