import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_build as ci


class CheckpointTests(unittest.TestCase):
    def test_target_and_input_keys(self):
        ctx = ci.context()
        with tempfile.TemporaryDirectory() as temp, patch.object(ci, "CACHE", Path(temp)):
            ci.write(ci.CACHE / "host.json", ["arm64", "CI toolchain"])
            ci.write(ci.CACHE / "gpu/receipt.json", {"sha256": "a" * 64})
            with patch.dict(os.environ, {"SHENG_DATA_GENERATION": "first"}):
                key = ci.key(ctx, "runtime")
                os.environ["SHENG_DATA_GENERATION"] = "next"
                self.assertNotEqual(key, ci.key(ctx, "runtime"))
                os.environ["SHENG_DATA_GENERATION"] = "first"
                self.assertEqual(key, ci.key(ctx, "runtime"))
                ci.write(ci.CACHE / "host.json", ["arm64", "new SDK"])
                self.assertNotEqual(key, ci.key(ctx, "runtime"))

    @unittest.skipUnless(shutil.which("tar") and sys.version_info >= (3, 14), "tar and Python 3.14 required")
    def test_save_restore_and_reject_damage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / "driver.a"
            with patch.object(ci, "HERE", root), patch.object(ci, "CACHE", root / "cache"), \
                 patch.object(ci, "outputs", return_value=[out]), patch.object(ci, "build") as build:
                build.side_effect = RuntimeError("failed compile")
                with self.assertRaises(RuntimeError):
                    ci.checkpoint(None, "link", "key", False)
                self.assertFalse((ci.CACHE / "link/receipt.json").exists())
                build.side_effect = lambda *_: out.write_bytes(b"compiled")
                ci.checkpoint(None, "link", "key", False)
                out.unlink()
                ci.checkpoint(None, "link", "key", True)
                self.assertEqual(out.read_bytes(), b"compiled")
                self.assertEqual(build.call_count, 2)
                with self.assertRaises(ValueError):
                    ci.checkpoint(None, "link", "different inputs", True)
                (ci.CACHE / "link/outputs.tar.zst").write_bytes(b"truncated")
                with self.assertRaises(ValueError):
                    ci.checkpoint(None, "link", "key", True)
