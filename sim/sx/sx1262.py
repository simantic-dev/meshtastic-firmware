# Semtech SX1262 sub-GHz transceiver as a scripted SPI slave (SPI.ScriptedSpiSlave).
#
# Source: DS.SX1261-2.W.APP Rev. 1.2 (June 2019). Section numbers below refer to it.
# Silicon revision measured: none yet (oracle: hil-setups/esp/s3-lora-v3/oracle). Anything
# the datasheet does not define is marked UNMEASURED and is to be filled in from silicon.
#
# Wiring (repl):  radio: SPI.ScriptedSpiSlave @ spi2
#                     file: "peers/sx1262.py"
#                     numberOfGpios: 4
#                     0 -> <mcu BUSY input>   1 -> <mcu DIO1 input>   2 -> DIO2   3 -> DIO3
#                 NSS is the slave select arrow (line 0 rising = release).
#
# Scope: LoRa modem (packet type 1), standby/FS/RX/TX/sleep, IRQ/DIO routing, buffer and
# register access, BUSY timing from Table 8-2. Not modelled: GFSK, RX duty cycle,
# continuous wave / infinite preamble (these answer "failed to execute"), NRESET (the seam
# has no input but NSS), PA ramp time, RF switch/TCXO analog behaviour.
#
# Over the air: on a scenario `lora` medium (`media: [{type: lora, connect: [a.radio, b.radio]}]`)
# a TX goes out through `ctx.lora.send` at its first preamble symbol, and the other radios get it as
# `on_lora(frame)` at the end of its sync word, with its link's (medium's) RSSI/SNR, which GetPacketStatus
# reports (section 13.5.3). A frame another on the same channel overlaps (`ctx.lora.overlapping`) fails: overlap
# before the end of its header raises HeaderErr, after it RxDone + CrcErr. MODELED SIMPLIFICATION: no capture effect,
# any overlap corrupts the frame whatever the levels. Without a medium, receive is fed from RX_SCHEDULE.
# Transmitted packets are recorded in `tx_log` either way.

import math, random

BUSY, DIO1, DIO2, DIO3 = 0, 1, 2, 3        # ctx.gpio output lines

FXTAL_HZ = 32_000_000                      # 32 MHz XTAL/TCXO, section 13.4.1
TIMEOUT_STEP_US = 15.625                   # SetTx/SetRx timeout unit, section 13.1.4

# Board facts, declared not fitted. Edit per board.
AMBIENT_RSSI_DBM = -110                    # what GetRssiInst reads with nothing on the air
RX_SCHEDULE = []                           # packets arriving over the air, see Air below

# Chip-mode field of the status byte, section 13.5.1 Table 13-76.
STBY_RC, STBY_XOSC, FS, RX, TX = 2, 3, 4, 5, 6
# Command-status field.
CS_NONE, CS_DATA, CS_TIMEOUT, CS_ERR, CS_FAIL, CS_TXDONE = 0, 2, 3, 4, 5, 6
# IRQ bits, Table 8-4.
IRQ_TXDONE, IRQ_RXDONE, IRQ_PREAMBLE, IRQ_SYNCWORD, IRQ_HEADERVALID = 1, 2, 4, 8, 16
IRQ_HEADERERR, IRQ_CRCERR, IRQ_CADDONE, IRQ_CADDET, IRQ_TIMEOUT = 32, 64, 128, 256, 512

# Table 8-2 typical switching times, us. Transitions the table does not list switch in
# T_SW only (MODELED SIMPLIFICATION, UNMEASURED).
SWITCH_US = {
    ("sleep_cold", STBY_RC): 3500, ("sleep_warm", STBY_RC): 340,
    (STBY_RC, STBY_XOSC): 31, (STBY_RC, FS): 50, (STBY_RC, RX): 83, (STBY_RC, TX): 126,
    (STBY_XOSC, FS): 40, (STBY_XOSC, TX): 105, (STBY_XOSC, RX): 62,
    (FS, RX): 41, (FS, TX): 76, (RX, FS): 15, (RX, TX): 92,
}
T_SW_US = 1                                # 600 ns max, section 8.3.1; the script clock is 1 us
CALIBRATE_ALL_US = 3500                    # section 13.1.12, all blocks; used for any calibParam

# LoRa BW code -> kHz, Table 13-48.
BW_KHZ = {0x00: 7.81, 0x08: 10.42, 0x01: 15.63, 0x09: 20.83, 0x02: 31.25, 0x0A: 41.67,
          0x03: 62.5, 0x04: 125.0, 0x05: 250.0, 0x06: 500.0}

# UNMEASURED: register 0x0320 is not in the datasheet. RadioLib compares its first six
# characters with "SX1262".
VERSION_STRING = b"SX1261 V2B".ljust(16, b"\x00")

REG_DEFAULTS = {
    0x0580: 0x00, 0x0583: 0x00, 0x0584: 0x00, 0x0585: 0x00, 0x06B8: 0x01, 0x06B9: 0x00,
    0x06BC: 0x1D, 0x06BD: 0x0F, 0x06BE: 0x10, 0x06BF: 0x21, 0x06CD: 0x00, 0x06CE: 0x00,
    0x0736: 0x0D, 0x0740: 0x14, 0x0741: 0x24, 0x0889: 0x01, 0x08AC: 0x94, 0x08D8: 0xC8,
    0x08E7: 0x38, 0x0902: 0x00, 0x0911: 0x05, 0x0912: 0x05, 0x0920: 0x01, 0x0944: 0x00,
}


def time_on_air_us(sf, bw_khz, cr, preamble, payload_len, explicit, crc, ldro):
    """Section 6.1.4. cr is 1..4 for 4/5..4/8."""
    n_crc = 16 if crc else 0
    n_hdr = 20 if explicit else 0
    if sf <= 6:
        n_sym = preamble + 6.25 + 8 + math.ceil(
            max(8 * payload_len + n_crc - 4 * sf + n_hdr, 0) / (4 * sf)) * (cr + 4)
    else:
        den = 4 * (sf - 2) if ldro else 4 * sf
        n_sym = preamble + 4.25 + 8 + math.ceil(
            max(8 * payload_len + n_crc - 4 * sf + 8 + n_hdr, 0) / den) * (cr + 4)
    return n_sym * (1 << sf) / bw_khz * 1000.0


def symbol_us(sf, bw_khz):
    return (1 << sf) / bw_khz * 1000.0


class Air:
    """A LoRa frame on the air, as the transmitter set it up."""
    def __init__(self, at_us, payload, rssi_dbm=-60.0, snr_db=10.0, freq_hz=915_000_000,
                 sf=7, bw_khz=125.0, cr=1, preamble=8, explicit=True, crc=True,
                 crc_ok=True, sync=0x1424, invert_iq=False, signal_rssi_dbm=None):
        self.at_us, self.payload = at_us, bytes(payload)
        self.rssi_dbm, self.snr_db = rssi_dbm, snr_db
        self.signal_rssi_dbm = rssi_dbm if signal_rssi_dbm is None else signal_rssi_dbm
        self.freq_hz, self.sf, self.bw_khz, self.cr = freq_hz, sf, bw_khz, cr
        self.preamble, self.explicit, self.crc, self.crc_ok = preamble, explicit, crc, crc_ok
        self.sync, self.invert_iq = sync, invert_iq


# Parameter-count limits per write opcode: name -> (min, max). Reads are handled in _miso.
WRITE_CMDS = {
    0x80: ("SetStandby", 1, 1), 0x84: ("SetSleep", 1, 1), 0xC1: ("SetFs", 0, 0),
    0x83: ("SetTx", 3, 3), 0x82: ("SetRx", 3, 3), 0x9F: ("StopTimerOnPreamble", 1, 1),
    0x94: ("SetRxDutyCycle", 6, 6), 0xC5: ("SetCad", 0, 0), 0xD1: ("SetTxContinuousWave", 0, 0),
    0xD2: ("SetTxInfinitePreamble", 0, 0), 0x96: ("SetRegulatorMode", 1, 1),
    0x89: ("Calibrate", 1, 1), 0x98: ("CalibrateImage", 2, 2), 0x95: ("SetPaConfig", 4, 4),
    0x93: ("SetRxTxFallbackMode", 1, 1), 0x0D: ("WriteRegister", 3, 258),
    0x0E: ("WriteBuffer", 2, 256), 0x08: ("SetDioIrqParams", 8, 8), 0x02: ("ClearIrqStatus", 2, 2),
    0x9D: ("SetDIO2AsRfSwitchCtrl", 1, 1), 0x97: ("SetDIO3AsTcxoCtrl", 4, 4),
    0x86: ("SetRfFrequency", 4, 4), 0x8A: ("SetPacketType", 1, 1), 0x8E: ("SetTxParams", 2, 2),
    0x8B: ("SetModulationParams", 4, 8), 0x8C: ("SetPacketParams", 6, 9),
    0x88: ("SetCadParams", 7, 7), 0x8F: ("SetBufferBaseAddress", 2, 2),
    0xA0: ("SetLoRaSymbNumTimeout", 1, 1), 0x07: ("ClearDeviceErrors", 1, 2),
    0x00: ("ResetStats", 6, 6),
}
READ_CMDS = {0xC0, 0x12, 0x13, 0x14, 0x15, 0x17, 0x11, 0x10, 0x1D, 0x1E}
NOT_MODELLED = {0x94, 0xD1, 0xD2}


import builtins, os, sys
# One count shared by every radio of a scenario (the interpreter is shared), so each gets its own
# noise seed in machine order. Not an air: frames travel on the medium.
_INSTANCES = builtins.__dict__.setdefault("_sx126x_instances", [0])


def _log(m):
    if os.environ.get('MESH_AIR_LOG'):
        print('SX1262:', m, file=sys.stderr, flush=True)


class Slave:
    def __init__(self, ctx):
        self.ctx = ctx
        _INSTANCES[0] += 1
        self.node = _INSTANCES[0] - 1
        self.air_busy_until = 0
        # MODELED SIMPLIFICATION: section 13.4 regs 0x0819-0x081C return wideband-noise random bits on silicon;
        # here a PRNG seeded by instance order, so each node gets its own entropy (and so its own MeshCore identity).
        self.noise = random.Random(0x5EED + _INSTANCES[0])
        self.pins = {}
        self.events = []                   # (due_us, seq, fn)
        self.seq = 0
        self.tx_log = []
        self.txn = None
        self.asleep = False
        self.sleep_warm = False
        self.waking = False
        self.busy_until = 0
        self.mode = STBY_RC
        self.cmd_status = CS_NONE
        self.fallback_mode = STBY_RC
        self.device_errors = 0
        self.stats = [0, 0, 0]             # received, crc errors, header errors
        self.buffer = bytearray(256)
        self._reset_config()
        for p in sorted(RX_SCHEDULE, key=lambda a: a.at_us):
            self._at(p.at_us, lambda p=p: self._air_start(p))

    # ---- state -----------------------------------------------------------------
    def _reset_config(self):
        self.regs = dict(REG_DEFAULTS)
        self.irq = 0
        self.irq_mask = 0
        self.dio_mask = [0, 0, 0]
        self.packet_type = 0               # GFSK after reset, section 13.4.2
        self.freq_hz = 0
        self.sf, self.bw_khz, self.cr, self.ldro = 7, 125.0, 1, 0
        self.preamble, self.explicit, self.payload_len, self.crc, self.invert_iq = 8, True, 0, False, False
        self.tx_base = self.rx_base = 0
        self.rx_len = self.rx_start = 0
        self.rssi_raw = self.snr_raw = self.signal_raw = 0
        self.dio2_rf_switch = False
        self.tx_power = 14
        self.rx_continuous = False
        self.rx_locked = False
        self.rx_deadline = None
        self._set_pin_levels()

    def status(self):
        return (self.mode << 4) | (self.cmd_status << 1)

    def now(self):
        return self.ctx.now_us

    # ---- timers: the seam gives one timer per slave, so keep our own queue --------
    def _at(self, due_us, fn):
        self.seq += 1
        self.events.append((due_us, self.seq, fn))
        self._arm()
        return self.seq

    def _cancel(self, token):
        self.events = [e for e in self.events if e[1] != token]

    def _arm(self):
        if self.events:
            nxt = min(e[0] for e in self.events)
            self.ctx.schedule_oneshot(max(1, int(nxt - self.now())), self._fire)

    def _fire(self):
        now = self.now()
        while True:
            due = sorted(e for e in self.events if e[0] <= now)
            if not due:
                break
            self.events = [e for e in self.events if e not in due[:1]]
            due[0][2]()
        self._arm()

    # ---- pins ----------------------------------------------------------------
    def _pin(self, line, level):
        level = bool(level)
        if self.pins.get(line) != level:
            self.pins[line] = level
            self.ctx.gpio(line).set(level)

    def _set_pin_levels(self):
        for n in range(3):
            self._pin(DIO1 + n, bool(self.irq & self.irq_mask & self.dio_mask[n]))

    def _raise_irq(self, bits):
        self.irq |= bits
        self._set_pin_levels()

    def _busy_for(self, dur_us):
        until = self.now() + max(T_SW_US, dur_us)
        self.busy_until = max(self.busy_until, until)
        self._pin(BUSY, True)
        self._at(until, self._busy_release)

    def _busy_release(self):
        if self.now() >= self.busy_until and not self.asleep:
            self._pin(BUSY, False)

    # ---- SPI byte level --------------------------------------------------------
    def on_select(self):
        self.txn = []
        self._begin_wake()

    def _begin_wake(self):
        # NSS falling is the wake event, section 8.2.2; BUSY stays high until STBY_RC.
        if self.asleep and not self.waking:
            self.waking = True
            key = "sleep_warm" if self.sleep_warm else "sleep_cold"
            self._at(self.now() + SWITCH_US[(key, STBY_RC)], self._wake_done)

    def _wake_done(self):
        if not self.sleep_warm:
            self._reset_config()
        self.asleep = self.waking = False
        self.mode = STBY_RC
        self._pin(BUSY, False)

    def on_transmit(self, b):
        if self.txn is None:               # controllers that never signal a select
            self.txn = []
            self._begin_wake()
        pos = len(self.txn)
        self.txn.append(b)
        if self.asleep:
            return 0x00
        return self._miso(pos)

    def on_deselect(self):
        self._end_txn()

    def on_finish(self):
        self._end_txn()

    def _miso(self, pos):
        if pos == 0:
            return 0x00                    # RFU, section 13.2
        op, st = self.txn[0], self.status()
        if op == 0x12 and pos <= 3:        # GetIrqStatus
            return (st, self.irq >> 8, self.irq & 0xFF)[pos - 1]
        if op == 0x13 and pos <= 3:        # GetRxBufferStatus
            return (st, self.rx_len, self.rx_start)[pos - 1]
        if op == 0x14 and pos <= 4:        # GetPacketStatus, LoRa: RssiPkt, SnrPkt, SignalRssiPkt
            return (st, self.rssi_raw, self.snr_raw, self.signal_raw)[pos - 1]
        if op == 0x15 and pos <= 2:        # GetRssiInst
            return (st, 0 if self.mode != RX else min(255, round(-AMBIENT_RSSI_DBM * 2)))[pos - 1]
        if op == 0x17 and pos <= 3:        # GetDeviceErrors
            return (st, self.device_errors >> 8, self.device_errors & 0xFF)[pos - 1]
        if op == 0x11 and pos <= 2:        # GetPacketType
            return (st, self.packet_type)[pos - 1]
        if op == 0x10 and pos <= 7:        # GetStats
            w = self.stats[(pos - 2) // 2] if pos >= 2 else 0
            return st if pos == 1 else (w >> 8 if pos % 2 == 0 else w & 0xFF)
        if op == 0x1D:                     # ReadRegister: addr hi, addr lo, NOP, then data
            if pos < 4:
                return st
            addr = (self.txn[1] << 8 | self.txn[2]) + (pos - 4) if len(self.txn) > 2 else 0
            if 0x0320 <= addr < 0x0330:
                return VERSION_STRING[addr - 0x0320]
            if 0x0819 <= addr <= 0x081C:
                return self.noise.getrandbits(8)
            return self.regs.get(addr, 0x00)
        if op == 0x1E:                     # ReadBuffer: offset, NOP, then data
            if pos < 3:
                return st
            return self.buffer[(self.txn[1] + pos - 3) & 0xFF] if len(self.txn) > 1 else 0
        return st

    def _end_txn(self):
        txn, self.txn = self.txn, None
        if not txn or self.asleep:
            return
        op, params = txn[0], txn[1:]
        if op in READ_CMDS or (op == 0x00 and not params):   # 0x00 = NOP, RadioLib's wake pulse
            return
        spec = WRITE_CMDS.get(op)
        if spec is None or not spec[1] <= len(params) <= spec[2]:
            self.ctx.warning(f"SX1262: opcode 0x{op:02X} with {len(params)} parameters: command error")
            self.cmd_status = CS_ERR
            return
        self.cmd_status = CS_NONE
        if self.now() < self.busy_until:
            self.ctx.debug(f"SX1262: {spec[0]} while BUSY high (or script clock lag)")
        self._busy_for(0)
        getattr(self, "_c_" + spec[0])(params)

    # ---- mode changes -------------------------------------------------------------
    def _switch(self, to):
        frm = self.mode
        dur = SWITCH_US.get((frm, to), 0)
        self._busy_for(dur)
        return self.now() + max(T_SW_US, dur)

    def _enter(self, mode, at_us, then=None):
        def go():
            self.mode = mode
            if then:
                then()
        self._at(at_us, go)

    def _c_SetStandby(self, p):
        if p[0] not in (0, 1):
            self.cmd_status = CS_ERR
            return
        self._abort_rf()
        self._enter(STBY_XOSC if p[0] else STBY_RC, self._switch(STBY_XOSC if p[0] else STBY_RC))

    def _c_SetSleep(self, p):
        self._abort_rf()
        self.sleep_warm = bool(p[0] & 0x04)
        self.asleep, self.mode = True, STBY_RC
        self._pin(BUSY, True)              # held high for the whole sleep, section 13.1.1

    def _c_SetFs(self, p):
        self._abort_rf()
        self._enter(FS, self._switch(FS))

    def _c_SetRxTxFallbackMode(self, p):
        self.fallback_mode = {0x40: FS, 0x30: STBY_XOSC, 0x20: STBY_RC}.get(p[0], STBY_RC)

    # ---- TX ---------------------------------------------------------------------
    def _c_SetTx(self, p):
        if self.packet_type != 1:
            return self._fail("SetTx outside LoRa packet type")
        timeout = p[0] << 16 | p[1] << 8 | p[2]
        began = self.now()
        start = self._switch(TX)           # BUSY drops when preamble starts, section 8.3.1
        self._abort_rf()
        self.rx_locked = False
        n = self.payload_len
        data = bytes(self.buffer[(self.tx_base + i) & 0xFF] for i in range(n))
        toa = time_on_air_us(self.sf, self.bw_khz, self.cr, self.preamble, n,
                             self.explicit, self.crc, self.ldro)
        done = start + toa
        limit = began + timeout * TIMEOUT_STEP_US if timeout else None

        def tx_started():
            self.mode = TX
            _log('  tx payload ' + data.hex())
            _log(f'TX start node{self.node} {len(data)}B sf{self.sf} bw{self.bw_khz} {self.freq_hz} t={self.now()}')
            if self._on_medium():
                self.ctx.lora.send(data, freq_hz=self.freq_hz, sf=self.sf, bw_hz=round(self.bw_khz * 1000),
                                   cr=self.cr, sync_word=self.regs[0x0740] << 8 | self.regs[0x0741],
                                   invert_iq=self.invert_iq, preamble=self.preamble, explicit=self.explicit,
                                   crc=self.crc, ldro=bool(self.ldro), power_dbm=self.tx_power)
            if self.dio2_rf_switch:
                self._pin(DIO2, True)
        self._at(start, tx_started)
        if limit is not None and limit < done:
            self._at(limit, lambda: self._tx_end(IRQ_TIMEOUT, None))
        else:
            self._at(done, lambda: self._tx_end(IRQ_TXDONE, data))

    def _tx_end(self, irq, data):
        if self.mode != TX:
            return
        if data is not None:
            self.tx_log.append(Air(self.now(), data, freq_hz=self.freq_hz, sf=self.sf,
                                   bw_khz=self.bw_khz, cr=self.cr, preamble=self.preamble,
                                   explicit=self.explicit, crc=self.crc,
                                   sync=self.regs[0x0740] << 8 | self.regs[0x0741]))
            self.cmd_status = CS_TXDONE
            self.ctx.info(f"SX1262 TX done {len(data)} B sf{self.sf} bw{self.bw_khz} "
                          f"{self.freq_hz} Hz at {self.now()} us")
        self.mode = self.fallback_mode
        self._pin(DIO2, False)
        self._raise_irq(irq)

    def _abort_rf(self):
        if self.mode == TX:
            self._pin(DIO2, False)
        self.rx_deadline = None
        self.rx_locked = False

    # ---- RX ----------------------------------------------------------------------
    def _c_SetRx(self, p):
        if self.packet_type != 1:
            return self._fail("SetRx outside LoRa packet type")
        timeout = p[0] << 16 | p[1] << 8 | p[2]
        self._abort_rf()
        self.rx_continuous = timeout == 0xFFFFFF
        ready = self._switch(RX)
        self._enter(RX, ready)
        if 0 < timeout < 0xFFFFFF:
            token = self._at(ready + timeout * TIMEOUT_STEP_US, self._rx_timeout)
            self.rx_deadline = token

    def _rx_timeout(self):
        if self.mode == RX and not self.rx_locked and self.rx_deadline is not None:
            self.rx_deadline = None
            self.mode = self.fallback_mode
            self._raise_irq(IRQ_TIMEOUT)

    def _matches(self, a):
        return (self.mode == RX and self.packet_type == 1 and a.sf == self.sf
                and a.bw_khz == self.bw_khz and a.cr == self.cr
                and a.sync == self.regs[0x0740] << 8 | self.regs[0x0741]
                and a.invert_iq == self.invert_iq and a.explicit == self.explicit
                and (a.explicit or len(a.payload) == self.payload_len)
                # MODELED SIMPLIFICATION, UNMEASURED: LoRa receive frequency tolerance.
                and abs(a.freq_hz - self.freq_hz) <= a.bw_khz * 1000 / 4)

    def _on_medium(self):
        lora = getattr(self.ctx, "lora", None)
        return lora is not None and lora.attached

    def on_lora(self, f):
        """A frame from the medium, at the end of its sync word."""
        a = Air(f["start_ns"] / 1000, f["payload"], rssi_dbm=f["rssi_dbm"], snr_db=f["snr_db"],
                freq_hz=f["freq_hz"], sf=f["sf"], bw_khz=f["bw_hz"] / 1000, cr=f["cr"],
                preamble=f["preamble"], explicit=f["explicit"], crc=f["crc"], sync=f["sync_word"],
                invert_iq=f["invert_iq"])
        a.span_ns = (f["start_ns"], f["end_ns"])
        self._air_start(a, f["end_ns"] / 1000)

    def _air_start(self, a, end_us=None):
        """`a` started at a.at_us; RX_SCHEDULE calls this then, the medium at its sync word."""
        toa = time_on_air_us(a.sf, a.bw_khz, a.cr, a.preamble, len(a.payload), a.explicit, a.crc, self.ldro)
        end_us = a.at_us + toa if end_us is None else end_us
        if (a.sf, a.bw_khz) == (self.sf, self.bw_khz) and abs(a.freq_hz - self.freq_hz) <= a.bw_khz * 1000 / 4:
            self.air_busy_until = end_us
        if self.rx_locked or not self._matches(a):
            return
        self.rx_locked = True
        sym = symbol_us(a.sf, a.bw_khz)
        t0, now = a.at_us, self.now()
        # MODELED SIMPLIFICATION, UNMEASURED: PreambleDetected at the end of the preamble (on a medium the
        # frame arrives after its sync word, so this is raised on arrival).
        self._at(max(now, t0 + a.preamble * sym), lambda: self._raise_irq(IRQ_PREAMBLE))
        done = None
        if a.explicit:
            def header():
                if self._overlapped(a, self.now()):
                    # UNMEASURED: after HeaderErr the chip drops the frame with no RxDone and keeps listening.
                    self._cancel(done)
                    self.rx_locked = False
                    self.stats[2] += 1
                    _log(f'  rx header err node{self.node}')
                    self._raise_irq(IRQ_HEADERERR)
                else:
                    self._raise_irq(IRQ_HEADERVALID)
            self._at(max(now, t0 + (a.preamble + 4.25 + 8) * sym), header)
        done = self._at(max(now, end_us), lambda: self._air_done(a))

    def _overlapped(self, a, until_us):
        """Another frame on a's channel was on the air between a's start and until_us."""
        if not hasattr(a, "span_ns") or not self._on_medium():
            return False
        spans = self.ctx.lora.overlapping(a.span_ns[0], round(until_us * 1000), a.freq_hz, a.sf, round(a.bw_khz * 1000))
        spans.remove(a.span_ns)
        return bool(spans)

    def _air_done(self, a):
        _log('  rx payload ' + a.payload.hex())
        self.rx_locked = False
        self.rx_deadline = None
        for i, byte in enumerate(a.payload):
            self.buffer[(self.rx_base + i) & 0xFF] = byte
        self.rx_len, self.rx_start = len(a.payload), self.rx_base
        self.rssi_raw = max(0, min(255, round(-a.rssi_dbm * 2)))
        self.snr_raw = round(a.snr_db * 4) & 0xFF
        self.signal_raw = max(0, min(255, round(-a.signal_rssi_dbm * 2)))
        self.stats[0] += 1
        irq = IRQ_RXDONE
        if a.crc and self._overlapped(a, a.span_ns[1] / 1000 if hasattr(a, "span_ns") else 0):
            a.crc_ok = False
            _log(f'  rx crc err node{self.node}')
        if a.crc and not a.crc_ok:
            irq |= IRQ_CRCERR
            self.stats[1] += 1
        else:
            self.cmd_status = CS_DATA
        if not self.rx_continuous:
            self.mode = self.fallback_mode
        self._raise_irq(irq)

    def _fail(self, why):
        self.ctx.warning(f"SX1262: {why}")
        self.cmd_status = CS_FAIL

    # ---- configuration commands --------------------------------------------------
    def _c_SetPacketType(self, p):
        self.packet_type = p[0]

    def _c_SetRfFrequency(self, p):
        reg = p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]
        self.freq_hz = round(reg * FXTAL_HZ / (1 << 25))

    def _c_SetModulationParams(self, p):
        if self.packet_type == 1:
            self.sf, self.cr, self.ldro = p[0], p[2], p[3]
            self.bw_khz = BW_KHZ.get(p[1], 125.0)

    def _c_SetPacketParams(self, p):
        if self.packet_type == 1:
            self.preamble = p[0] << 8 | p[1]
            self.explicit = p[2] == 0
            self.payload_len = p[3]
            self.crc = bool(p[4])
            self.invert_iq = bool(p[5])

    def _c_SetBufferBaseAddress(self, p):
        self.tx_base, self.rx_base = p[0], p[1]

    def _c_SetDioIrqParams(self, p):
        w = [p[i] << 8 | p[i + 1] for i in (0, 2, 4, 6)]
        self.irq_mask, self.dio_mask = w[0], w[1:]
        self._set_pin_levels()

    def _c_ClearIrqStatus(self, p):
        self.irq &= ~(p[0] << 8 | p[1])
        self._set_pin_levels()

    def _c_WriteRegister(self, p):
        addr = p[0] << 8 | p[1]
        for i, byte in enumerate(p[2:]):
            self.regs[addr + i] = byte

    def _c_WriteBuffer(self, p):
        for i, byte in enumerate(p[1:]):
            self.buffer[(p[0] + i) & 0xFF] = byte

    def _c_SetDIO2AsRfSwitchCtrl(self, p):
        self.dio2_rf_switch = bool(p[0])

    def _c_Calibrate(self, p):
        self._busy_for(CALIBRATE_ALL_US)

    def _c_CalibrateImage(self, p):
        pass                               # UNMEASURED: duration not in the datasheet

    def _c_ClearDeviceErrors(self, p):
        self.device_errors = 0

    def _c_ResetStats(self, p):
        self.stats = [0, 0, 0]

    def _c_StopTimerOnPreamble(self, p): pass
    def _c_SetRegulatorMode(self, p): pass
    def _c_SetPaConfig(self, p): pass
    def _c_SetDIO3AsTcxoCtrl(self, p): pass
    def _c_SetTxParams(self, p): self.tx_power = p[0] - 256 if p[0] > 127 else p[0]
    def _c_SetCadParams(self, p): self.cad_symbols = 1 << min(p[0], 4)   # CadSymbolNum 0..4 = 1,2,4,8,16 symbols
    def _c_SetLoRaSymbNumTimeout(self, p): pass

    def _c_SetRxDutyCycle(self, p): self._fail("SetRxDutyCycle not modelled")
    def _c_SetCad(self, p):
        # CAD_ONLY exit (RadioLib/Meshtastic): scan, raise CadDone (+CadDetected if a same-modulation frame is on the air), standby.
        # MODELED SIMPLIFICATION, UNMEASURED: scan time = (CadSymbolNum + 1) symbols, detection = a frame with this SF/BW/freq in flight.
        if self.packet_type != 1:
            return self._fail("SetCad outside LoRa packet type")
        self._abort_rf()
        ready = self._switch(RX)
        end = ready + (getattr(self, "cad_symbols", 2) + 1) * symbol_us(self.sf, self.bw_khz)
        self._enter(RX, ready)
        def done():
            if self._on_medium():
                busy = self.ctx.lora.on_air(self.freq_hz, self.sf, round(self.bw_khz * 1000))
            else:
                busy = self.air_busy_until > self.now()
            self.mode = STBY_RC
            self._raise_irq(IRQ_CADDONE | (IRQ_CADDET if busy else 0))
        self._at(end, done)
    def _c_SetTxContinuousWave(self, p): self._fail("SetTxContinuousWave not modelled")
    def _c_SetTxInfinitePreamble(self, p): self._fail("SetTxInfinitePreamble not modelled")
