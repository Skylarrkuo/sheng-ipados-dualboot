import hashlib
import json
import struct

from configure import DEVICE, TARGET

boot = TARGET / "boot/boot.img"
record = json.loads((TARGET / "package.manifest.json").read_text())
if record["device"] != "sheng" or record["os_profile"] != DEVICE["profile"]:
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
if (magic, version, page, size) != (b"DBRDISK1", 1, 4096, DEVICE["data_gib"] << 30):
    raise ValueError("Data seed has incorrect type or logical capacity")
