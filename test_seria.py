import argparse
import sys
import types
import unittest


# pyserial が未インストール環境でもユーティリティ関数をテストできるように
# 最小限の serial モジュールをスタブする。
serial_stub = types.ModuleType("serial")
serial_stub.FIVEBITS = 5
serial_stub.SIXBITS = 6
serial_stub.SEVENBITS = 7
serial_stub.EIGHTBITS = 8
serial_stub.PARITY_NONE = "N"
serial_stub.PARITY_EVEN = "E"
serial_stub.PARITY_ODD = "O"
serial_stub.PARITY_MARK = "M"
serial_stub.PARITY_SPACE = "S"
serial_stub.STOPBITS_ONE = 1
serial_stub.STOPBITS_ONE_POINT_FIVE = 1.5
serial_stub.STOPBITS_TWO = 2
serial_stub.SerialException = Exception
serial_stub.Serial = object

list_ports_stub = types.ModuleType("serial.tools.list_ports")
list_ports_stub.comports = lambda: []

list_ports_common_stub = types.ModuleType("serial.tools.list_ports_common")
list_ports_common_stub.ListPortInfo = object

serial_tools_stub = types.ModuleType("serial.tools")
serial_tools_stub.list_ports = list_ports_stub
serial_tools_stub.list_ports_common = list_ports_common_stub

serial_stub.tools = serial_tools_stub

sys.modules.setdefault("serial", serial_stub)
sys.modules.setdefault("serial.tools", serial_tools_stub)
sys.modules.setdefault("serial.tools.list_ports", list_ports_stub)
sys.modules.setdefault("serial.tools.list_ports_common", list_ports_common_stub)

from seria import ReadMode, chunk_stats, parse_baudrates, parse_encodings, resolve_read_mode


class SeriaUnitTests(unittest.TestCase):
    def test_parse_baudrates_valid(self):
        self.assertEqual(parse_baudrates("9600, 115200"), [9600, 115200])

    def test_parse_baudrates_invalid(self):
        with self.assertRaises(ValueError):
            parse_baudrates("9600,")
        with self.assertRaises(ValueError):
            parse_baudrates("abc")

    def test_resolve_read_mode_delimiter_hex(self):
        args = argparse.Namespace(delimiter="0D0A", chunk=None)
        mode = resolve_read_mode(args)
        self.assertEqual(mode.mode, "delimiter")
        self.assertEqual(mode.delimiter, b"\r\n")

    def test_resolve_read_mode_invalid_delimiter(self):
        args = argparse.Namespace(delimiter="GG", chunk=None)
        with self.assertRaises(ValueError):
            resolve_read_mode(args)

    def test_parse_encodings_invalid(self):
        with self.assertRaises(ValueError):
            parse_encodings("utf-8,not_a_real_codec")

    def test_chunk_stats_delimiter_removes_terminator_for_decoding(self):
        mode = ReadMode(mode="delimiter", delimiter=b"\xff")
        stats = chunk_stats(b"abc\xff", mode, encodings=["utf-8"])
        self.assertEqual(stats["payload_bytes"], 3)
        self.assertEqual(stats["decoded"], "abc")
        self.assertEqual(stats["encoding"], "utf-8")


if __name__ == "__main__":
    unittest.main()
