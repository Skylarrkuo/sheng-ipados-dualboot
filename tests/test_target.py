import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit_target import check_inputs, context, verify_profile


class TargetTests(unittest.TestCase):
    def test_external_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("audit_target.CORE", root), patch("audit_target.BOARD", root):
                with patch.dict(os.environ, {"NMB_PREBUILT_BRIDGE": "desktop.json"}, clear=True):
                    with self.assertRaisesRegex(ValueError, "NMB_PREBUILT_BRIDGE"):
                        check_inputs()
                with patch.dict(os.environ, {}, clear=True):
                    for filename in ("unixnu.local.toml", "local.toml"):
                        path = root / filename
                        path.touch()
                        with self.assertRaisesRegex(ValueError, filename):
                            check_inputs()
                        path.unlink()

    def test_mixed_runtime(self):
        ctx = context()
        for key, value in [("target", "macos27"), ("wifi_target", "macos27"),
                           ("bluetooth_target", "macos27"), ("audio_target", "arm64e-apple-macos27.0"),
                           ("data_seed_tool", "scripts/make-macos-data-seed.py")]:
            with self.subTest(key=key), patch.dict(ctx.board["payload"]["runtime"], {key: value}):
                with self.assertRaises(ValueError):
                    verify_profile(ctx)
