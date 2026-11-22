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
![boot milestones, from a recorded run](boot.gif)

Rerun 2026-11-22 (rebuilt, sim 0.10.6, `--record`; `sim/make_gif.py <rec_dir>` makes the GIF from the bundle): rung 1 and 2 hold.
- Rung 1, boots (rerun: fault at 7.943 s, 64 Minst/s, 15.6 ns/insn): banner, I2C scan, NodeDB, LittleFS init, GPS/PowerFSM, no fault up to ~7.9 s. ~64 Minst/s guest (15.6 ns/insn).
- Rung 2, radio (rerun: `SX126x init result 0` at 4.155 s, `SX1262 init success` at 4.166 s): `SX126x init result 0`, `SX1262 init success` at 4.16 s. 10 s run: 264 M guest insns, 3.8 s wall.
- Needed two edits to the sim-fixtures SX1262 peer (`sim/sx/sx1262.py`, copy):
  1. RadioLib wakes the chip with a 1-byte NOP (0x00); the peer flagged it as a command error and the sticky error made every
     SetStandby fail the status check. NOP is now ignored.
  2. RadioLib's SX1262 class looks for version string `SX1261` (RADIOLIB_SX1262_CHIP_TYPE); peer returned `SX1262 V2B`.
     Peer string is datasheet-derived, unmeasured on silicon.
- Fault at 7.94 s with BLE: after "Init the Bluefruit", S140 executes `svc 255` (SD assert, escalates to HardFault inside TIMER0 IRQ8).
  The SD is inside a 512-word `sd_flash_write` (Adafruit `fal_program` half page) polling NVMC READY, 512 x 41 us = 21 ms.
  Cause, from a tWRITE sweep (board-file override `nvmc: program_word_ns: <v>`, same ELF, 12 s): 5, 10, 20, 30, 35 us run
  clean (BLE init at 6.9-7.7 s); 41 us (the default) faults at 7.943 s. The SD's limit is therefore between 17.9 and 21.0 ms for that write.
  nRF52840 PS v1.11 §4.3.10.1 gives tWRITE only as a 41 us max (no typ); pyrite charges the max for every word. Adafruit already writes
  half pages because S140 6.1.1 asserts on whole-page writes on silicon (devzone 40088), so silicon words are faster than the max.
  Verdict: pessimistic model constant, not firmware. Not measured on silicon (needs a DWT cycle count around a 512-word write).
  Evidence commented on pyrite#363. Same fault with the one-machine `quantum: 0.00001` scenario form (`sim/one.yaml`, needs a merged hex), so not pyrite#4051.
- With `-DMESHTASTIC_EXCLUDE_BLUETOOTH=1` (`EXTRA=... sim/build.sh`): no fault; the USB log stops after 4.82 s (last line cut mid-line at 4.8196 s).
  Rerun, 8 s with `--trace-symbol` on TinyUSB: the last CDC IN transfer (EP 0x82, 28 bytes) is armed at 4.819559 s and its completion event
  reaches `dcd_event_handler` at 4.819577 s; USBD IRQ (IRQ39) never fires again, `usbd_edpt_xfer` is never called again, `tud_task_ext` waits
  forever. Meanwhile `tud_cdc_n_write_flush` is called 32321 times in the next 1.2 s and 171 k SVCall/PendSV pairs follow by 8 s: the firmware
  spins writing the log into a CDC FIFO that nothing drains. Cause of the missing re-arm after that transfer is open (TinyUSB device side vs
  pyrite USB host side not yet separated); the BLE build keeps logging to 7.9 s, so the path differs with the SD absent. Region is UNSET, so the radio idles.
- Node id reads 0xffffff4f (FICR DEVICEADDR is the same on every instance), see pyrite skill known gaps.

## Not reached
Rung 3: the SX1262 peer has no shared air (RX only from a fixed schedule, TX only logged), and the serial API needs the USB log/CDC
path examined first.
