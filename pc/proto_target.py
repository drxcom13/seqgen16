"""proto_target.py - virtual device on connector J6, used without hardware by emulator.py (command level)
and fw_sim.py (proto_io register level), so both give the same answers:

    SPI : MOSI looped back to MISO   -> every received byte = the byte sent
    UART: TX looped back to RX       -> every byte sent is received (no error flag)
    I2C : EEPROM at address 0x50 (256 bytes, 8-bit pointer; write: pointer, data...; read from pointer),
          other addresses do not acknowledge.

Input: TX-queue entries of proto_io (see quartus/ip/proto_io/proto_io.vhd), output: RX-queue entries.
"""

MODE_OFF, MODE_SPI, MODE_I2C, MODE_UART = 0, 1, 2, 3
EEPROM_ADDR = 0x50


class ProtoTarget:
    def __init__(self):
        self.eeprom = bytearray(256)
        self.reset()

    def reset(self):
        self.i2c_state = "idle"        # idle | addr | write | read | ignore
        self.i2c_first = False
        self.ptr = 0

    def entry(self, mode: int, e: int):
        """Process one TX entry; returns an RX entry (int) or None."""
        if mode in (MODE_SPI, MODE_UART):
            return e & 0xFF
        if mode != MODE_I2C:
            return None
        op = (e >> 8) & 3
        if op == 3:                                   # control
            if e & 1:
                self.i2c_state = "addr"
            elif e & 2:
                self.i2c_state = "idle"
            return None
        if op == 0:                                   # write byte -> [8] = NACK
            b = e & 0xFF
            if self.i2c_state == "addr":
                if b >> 1 == EEPROM_ADDR:
                    self.i2c_state = "read" if b & 1 else "write"
                    self.i2c_first = True
                    return 0
                self.i2c_state = "ignore"
                return 0x100
            if self.i2c_state == "write":
                if self.i2c_first:
                    self.ptr = b
                    self.i2c_first = False
                else:
                    self.eeprom[self.ptr] = b
                    self.ptr = (self.ptr + 1) & 0xFF
                return 0
            return 0x100                              # nobody listening
        # read (op 1 = ACK, op 2 = NACK): the master releases SDA -> 0xFF when no device sends
        if self.i2c_state == "read":
            b = self.eeprom[self.ptr]
            self.ptr = (self.ptr + 1) & 0xFF
            if op == 2:
                self.i2c_state = "ignore"
            return b
        return 0xFF
