import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from root_image import GIB, check_capacity, make_table


class ImageTests(unittest.TestCase):
    def test_store(self):
        self.assertGreater(check_capacity(16 * GIB), 97 * GIB)
        for size in (0, 4097, 40 * GIB):
            with self.assertRaises(ValueError):
                check_capacity(size)

    def test_original_tables(self):
        raw = bytes(range(256)) * 32 + b"last"
        table = b"".join(hashlib.sha256(raw[i:i + 4096]).digest() for i in range(0, len(raw), 4096))
        signed = bytearray(544)
        struct.pack_into("<8s4I2Q", signed, 0, b"VROOT01\0", 1, 160, 4096, 0, len(raw), 3)
        signed[56:88] = hashlib.sha256(raw).digest()
        signed[88:120] = hashlib.sha256(table).digest()
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "hashes"
            make_table([raw[:5200], raw[5200:]], signed, path)
            self.assertEqual(path.read_bytes(), table)
            for changed in (raw + b"!", raw[:-1], b"x" + raw[1:]):
                with self.assertRaises(ValueError):
                    make_table([changed], signed, path)
            signed[88] ^= 1
            with self.assertRaises(ValueError):
                make_table([raw], signed, path)
