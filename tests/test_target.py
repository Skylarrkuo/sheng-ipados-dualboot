import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit_target import check_inputs, context, verify_profile
from configure import BOARD, CORE


class TargetTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "C++ preprocessor required")
    def test_ipados_link_inputs(self):
        targets = {
            BOARD / "package/BootLog.c": ["cs_native4k_" + suffix for suffix in
                                          ("chunk_ticks", "digest_bytes", "digest_cpu", "digest_wall", "record_ticks")],
            BOARD / "package/LegacyDisplayProbe.cpp": ["sheng_ethernet_probe", "sheng_software_aes_probe"],
            CORE / "drivers/Adreno740/NativeAdrenoPublish.cpp": ["iokit_physical_descriptor_close_notify"],
            CORE / "drivers/RamBlock/NativeRamBlock.cpp": ["ml_static_mfree_prepare"],
        }
        platform = ["c++", "-E", "-P", "-x", "c++", "-DXNU_TARGET_OS_IOS=1", "-DXNU_TARGET_OS_OSX=0",
                    "-D__has_feature(x)=0", "-"]
        for path, names in targets.items():
            source = re.sub(r"^\s*#\s*include\b[^\n]*", "", path.read_text(), flags=re.M)
            code = subprocess.check_output(platform, input=source, text=True)
            for name in names:
                uses = [line for line in code.splitlines() if re.search(rf"\b{name}\b", line)
                        and "extern " not in line and "weak_import" not in line]
                self.assertEqual(uses, [], name)
        display = CORE / "drivers/GopFramebuffer/NativeLegacyDisplay.cpp"
        source = re.sub(r"^\s*#\s*include\b[^\n]*", "", display.read_text(), flags=re.M)
        code = subprocess.check_output(platform, input=source, text=True)
        self.assertEqual(code.count("native_legacy_request_layout[6]={kRequestRect0"), 1)

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

    @unittest.skipUnless(shutil.which("c++"), "C++ preprocessor required")
    def test_driver_platform_guards(self):
        for file, names in {
            "KeyboardProbe.cpp": ["kClamshellLog"],
            "ShengSleep.cpp": ["kWakeEventSource", "lidArmed", "powerArmed", "counterMs"],
        }.items():
            driver = (BOARD / "package" / file).read_text(encoding="utf-8")
            # Exercise the actual conditional declarations and all their callers
            # before CI has the XNU private headers used in the full build.
            source = re.sub(r"^#include .*?$", "", driver, flags=re.M)
            for desktop in (0, 1):
                code = subprocess.check_output(["c++", "-E", "-P", "-x", "c++",
                                                f"-DXNU_TARGET_OS_OSX={desktop}", "-"], input=source, text=True)
                for name in names:
                    with self.subTest(file=file, desktop=desktop, name=name):
                        count = len(re.findall(rf"\b{name}\b", code))
                        if desktop:
                            self.assertGreaterEqual(count, 2)
                        else:
                            self.assertEqual(count, 0)
