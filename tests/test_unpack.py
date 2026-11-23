import hashlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from compression import zstd
except ImportError:
    zstd = None


@unittest.skipUnless(zstd, "cross-platform unpacker uses Python 3.14")
class UnpackTest(unittest.TestCase):
    def test_verify_before_publishing(self):
        from configure import DEVICE
        from unpack_root import unpack
        raw = b"SHNGROOT" * 2_000_000
        packed = zstd.compress(raw)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            split = len(packed) // 2
            files = {"linux-root.img.zst.aaa": packed[:split], "linux-root.img.zst.aab": packed[split:]}
            report = dict(target_partition="linux", partition_bytes=DEVICE["capacity_bytes"],
                          image_sha256=hashlib.sha256(raw).hexdigest(), image_bytes=len(raw),
                          storage_required_bytes=len(raw) + 4096)
            files["linux-root.json"] = json.dumps(report).encode()
            for name, data in files.items():
                (folder / name).write_bytes(data)
            (folder / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in files.items()))
            output = folder / "linux-root.img"
            unpack(folder, output)
            self.assertEqual(output.read_bytes(), raw)
            with self.assertRaises(FileExistsError):
                unpack(folder, output)
            (folder / "linux-root.img.zst.aab").write_bytes(packed[split:-1])
            other = folder / "incomplete.img"
            with self.assertRaises(ValueError):
                unpack(folder, other)
            self.assertFalse(other.exists())
