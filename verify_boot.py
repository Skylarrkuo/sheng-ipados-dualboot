import hashlib
import json
from pathlib import Path
import re
import runpy
import struct
import subprocess
import tempfile

from audit_target import context, verify_profile
from configure import CORE, DEVICE, TARGET

boot = TARGET / "boot/boot.img"
record = json.loads((TARGET / "package.manifest.json").read_text())
ctx = context()
if (record["device"], record["os_profile"], record["configuration"]) != ("sheng", DEVICE["profile"], ctx.config_name):
    raise ValueError("Wrong build profile")
with boot.open("rb") as stream:
    header = stream.read(44)
    stream.seek(0)
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
if (header[:8] != b"ANDROID!" or struct.unpack_from("<I", header, 16)[0] == 0
        or struct.unpack_from("<I", header, 40)[0] != 0 or boot.stat().st_size > DEVICE["boot_b_bytes"]
        or digest != record["outputs"]["boot.img"]["sha256"]):
    raise ValueError("Boot header, ramdisk, slot capacity or SHA-256 mismatch")
with (TARGET / "runtime/data.dbr").open("rb") as stream:
    magic, version, page, size = struct.unpack("<8sIIQ", stream.read(24))
    stream.seek(0)
    if hashlib.file_digest(stream, "sha256").hexdigest() != record["runtime"]["inputs"]["data_ramdisk"]:
        raise ValueError("Data seed differs from the kernel's build manifest")
if (magic, version, page, size) != (b"DBRDISK1", 1, 4096, DEVICE["data_gib"] << 30):
    raise ValueError("Data seed has incorrect type or logical capacity")

# Inspect the exact addon named and hashed by the release manifest. This also
# checks the iPadOS install paths, which differ from macOS bundle paths.
addon = Path(json.loads((TARGET / "runtime/inputs.json").read_text())["inputs"]["gpu_addon"]["path"])
with addon.open("rb") as stream:
    if hashlib.file_digest(stream, "sha256").hexdigest() != record["runtime"]["inputs"]["gpu_addon"]:
        raise ValueError("Runtime addon differs from the release manifest")
files = runpy.run_path(str(CORE / "scripts/addon-add-dir.py"))["read"](addon)
required = {
    "addons/NativeAdrenoMetal.bundle/NativeAdrenoMetal": True,
    "addons/NativeAdrenoMetal.bundle/libvulkan_freedreno.dylib": False,
    "audio/ShengAudio.driver/ShengAudio": True,
    "wifi-bin/unixnu-wpa_supplicant": False,
    "bluetooth-bin/bluetoothd": True,
    "bluetooth-bin/BlueTool": True,
    "bluetooth-lib/QcaBTType0.dylib": True,
}
binaries = {}
with tempfile.TemporaryDirectory() as directory:
    binary = Path(directory) / "input"
    for name, _, data in files:
        if name not in required:
            continue
        binary.write_bytes(data)
        archs = subprocess.check_output(["xcrun", "lipo", "-archs", binary], text=True).split()
        if not archs or set(archs) - {"arm64", "arm64e"} or (required[name] and "arm64e" not in archs):
            raise ValueError(f"Unexpected architecture for {name}: {archs}")
        for arch in archs:
            info = subprocess.check_output(["xcrun", "vtool", "-arch", arch, "-show-build", binary], text=True)
            platforms = re.findall(r"^\s*platform\s+(\S+)", info, re.M)
            if platforms not in (["IOS"], ["2"]) and not (not platforms and "LC_VERSION_MIN_IPHONEOS" in info):
                raise ValueError(f"Unexpected Apple platform for {name} ({arch}): {info}")
        if name in binaries:
            raise ValueError(f"Duplicate runtime binary: {name}")
        binaries[name] = {"platform": "iOS", "architectures": archs, "sha256": hashlib.sha256(data).hexdigest()}
if required.keys() != binaries.keys():
    raise ValueError(f"Missing iPadOS runtime binaries: {required.keys() - binaries.keys()}")
(TARGET / "target-audit.json").write_text(json.dumps({"profile": verify_profile(ctx), "binaries": binaries}, indent=2) + "\n")
