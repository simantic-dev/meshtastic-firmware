#!/bin/sh
# Build rak4631 and make sim/bl0.hex (MBR + S140 6.1.1 + bootloader, UICR records dropped), plus one merged hex per node.
# Run from the repo root. Not committed: Nordic blobs / build output.
#   EXTRA="-D..." sim/build.sh            -> sim/fw.elf, sim/bl0.hex (single node: --elf sim/bl0.hex --elf sim/fw.elf)
#   NODES="a b c" EXTRA="-D..." sim/build.sh -> also sim/merged_<n>.hex: one build per node, -DSIM_MAC_LSB=<index+1>
#                                              (a scenario takes one elf, hence the merged MBR+S140+app hex)
set -e
OC=$(ls ~/.platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-objcopy)
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
if [ -z "$NODES" ]; then
  PLATFORMIO_BUILD_FLAGS="$EXTRA" pio run -e rak4631
  cp .pio/build/rak4631/firmware-rak4631-*.elf sim/fw.elf
  exit 0
fi
i=1
for n in $NODES; do
  PLATFORMIO_BUILD_FLAGS="$EXTRA -DSIM_MAC_LSB=$i" pio run -e rak4631
  cp .pio/build/rak4631/firmware-rak4631-*.elf sim/fw_$n.elf
  $OC -O ihex sim/fw_$n.elf sim/app.hex
  (grep -v '^:00000001FF' sim/bl0.hex; cat sim/app.hex) > sim/merged_$n.hex; rm sim/app.hex
  i=$((i+1))
done
