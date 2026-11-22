# Meshtastic on pyrite (nRF52840, RAK4631): learning pass

Variant `rak4631` (nRF52840 + SX1262 on SPIM3), firmware 2.8.3.f584291, sim 0.10.6, model `NRF52840QIAA` (reported "incomplete").
Not for upstream.

## Run
`sim/build.sh`, then
`sim NRF52840QIAA run --board sim/rak.board --elf sim/bl0.hex --elf sim/fw.elf --timeout 12 --usb-cdc-console usbd --ascii --on-fault stop`

The Adafruit-core app expects Nordic's MBR + S140 6.1.1 SoftDevice below 0x26000. `bl0.hex` is that blob taken from the
PlatformIO Adafruit package (bootloader hex, UICR records stripped). The real SoftDevice runs in pyrite: MBR -> SD -> app, SVCall
forwarding, Bluefruit/LittleFS flash ops all work. Logs come over USB CDC (`usbd`), not a UART.

## Results (virtual time unless noted)
- Rung 1, boots: banner, I2C scan, NodeDB, LittleFS init, GPS/PowerFSM, no fault up to ~7.9 s. ~64 Minst/s guest (15.6 ns/insn).
- Rung 2, radio: `SX126x init result 0`, `SX1262 init success` at 4.16 s. 10 s run: 264 M guest insns, 3.8 s wall.
- Needed two edits to the sim-fixtures SX1262 peer (`sim/sx/sx1262.py`, copy):
  1. RadioLib wakes the chip with a 1-byte NOP (0x00); the peer flagged it as a command error and the sticky error made every
     SetStandby fail the status check. NOP is now ignored.
  2. RadioLib's SX1262 class looks for version string `SX1261` (RADIOLIB_SX1262_CHIP_TYPE); peer returned `SX1262 V2B`.
     Peer string is datasheet-derived, unmeasured on silicon.
- Fault at 7.94 s with BLE: after "Init the Bluefruit", S140 executes `svc 255` (SD assert, escalates to HardFault inside TIMER0 IRQ8).
  The SD was inside a 512-word `sd_flash_write` (Adafruit `fal_program` half page) polling NVMC READY, 21 ms = 512 x 41 us (pyrite
  NVMC uses tWRITE max 41 us, declared assumption); the assert compares RTC0 COUNTER to a stored deadline. Adafruit's own source
  notes S140 6.1.1 asserts on page-size flash writes on silicon (devzone 40088). Cause unconfirmed; not filed as a model gap.
  Same fault with the one-machine `quantum: 0.00001` scenario form (`sim/one.yaml`, needs a merged hex), so not pyrite#4051.
- With `-DMESHTASTIC_EXCLUDE_BLUETOOTH=1` (`EXTRA=... sim/build.sh`): 20 s virtual, 1.27 G insns, 34 s wall, no fault; but the USB log
  goes quiet after 4.82 s (cause not examined). Region is UNSET, so the radio idles.
- Node id reads 0xffffff4f (FICR DEVICEADDR is the same on every instance), see pyrite skill known gaps.

## Not reached
Rung 3: the SX1262 peer has no shared air (RX only from a fixed schedule, TX only logged), and the serial API needs the USB log/CDC
path examined first.
