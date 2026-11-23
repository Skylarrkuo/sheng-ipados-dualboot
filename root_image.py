"""Reconstruct the original VerifiedRoot tables and pack the two Apple volumes."""
import contextlib
import hashlib
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

from configure import BOARD, CORE, DEVICE, TARGET

MIB = 1 << 20
GIB = 1 << 30


def check_capacity(image_bytes):
    seed_pages = DEVICE["data_gib"] * GIB // 4096
    bitmap_pages = (seed_pages + 32767) // 32768
    store_bytes = (1 + bitmap_pages + seed_pages) * 4096
    minimum = image_bytes + 4 * MIB + store_bytes + DEVICE["log_reserve_mib"] * MIB
    if image_bytes <= 0 or image_bytes % 4096 or minimum > DEVICE["capacity_bytes"]:
        raise ValueError("Root, persistent Data, store metadata and log reserve must fit linux")
    return minimum


def make_table(chunks, signed, output):
    # VerifiedRoot/tools/manifest.py hashes each consecutive 4096-byte block.
    # Reuse the signed header and anchor shipped by the device repository.
    if len(signed) != 544 or struct.unpack_from("<8s4I", signed) != (b"VROOT01\0", 1, 160, 4096, 0):
        raise ValueError("Unexpected VerifiedRoot header")
    expected_size, expected_count = struct.unpack_from("<QQ", signed, 24)
    image_hash = hashlib.sha256()
    table_hash = hashlib.sha256()
    size = count = 0
    carry = b""
    with output.open("wb") as out:
        for chunk in chunks:
            image_hash.update(chunk)
            size += len(chunk)
            data = carry + chunk
            end = len(data) // 4096 * 4096
            view = memoryview(data)
            digests = b"".join(hashlib.sha256(view[i:i + 4096]).digest() for i in range(0, end, 4096))
            count += end // 4096
            out.write(digests)
            table_hash.update(digests)
            carry = data[end:]
        if carry:
            digest = hashlib.sha256(carry).digest()
            out.write(digest)
            table_hash.update(digest)
            count += 1
    if ((size, count) != (expected_size, expected_count) or image_hash.digest() != signed[56:88]
            or table_hash.digest() != signed[88:120]):
        raise ValueError("The original image or regenerated hash table differs from the signed upstream policy")


def main():
    sys.path.insert(0, str(CORE / "scripts"))
    from unixnu import userdata
    from unixnu.cli import parser
    from unixnu.config import Context
    from unixnu.ipsw import Ipsw

    os.chdir(CORE)
    args = parser().parse_args(["build", "userdata", "-D", str(BOARD), "--os", "27"])
    ctx = Context(args)
    if ctx.work != TARGET or ctx.board["ipsw"]["build"] != "24A446":
        raise ValueError("Unexpected target or IPSW build")
    source = Ipsw(ctx)
    policy = BOARD / "scripts/policies" / DEVICE["profile"]
    TARGET.mkdir(parents=True, exist_ok=True)
    expected = {}
    with tempfile.TemporaryDirectory(dir=TARGET) as scratch:
        for window in ctx.board["userdata"]["windows"]:
            name = window["name"]
            folder = policy / name
            signed = (folder / "manifest.header.bin").read_bytes()
            record = json.loads((folder / "verification.json").read_text())
            if (hashlib.sha256((folder / "trusted-key.bin").read_bytes()).digest() != signed[120:152]
                    or record["image_sha256"] != signed[56:88].hex()):
                raise ValueError(f"Unexpected upstream policy for {name}")
            dmg = source.disk_image(window["component"])
            table = Path(scratch) / (name + ".vroot")
            with contextlib.closing(userdata.disk_blocks(dmg, userdata.image_size(dmg))) as chunks:
                make_table(chunks, signed, table)
            expected[name] = signed[56:88].hex()
            expected[table.name] = signed[88:120].hex()
            print(f"Matched {name} and {table.name} against the original policy", flush=True)
            args.object = [*(args.object or []), f"{table.name}={table}"]
        image = userdata.build(ctx)
    _, _, report_path = userdata.paths(ctx)
    report = json.loads(report_path.read_text())
    actual = {item["name"]: item["sha256"] for item in report["windows"] + report["objects"]}
    if (set(actual) != {"OS", "SystemOS", "OS.vroot", "SystemOS.vroot"} or actual != expected
            or report["target_partition"] != "linux" or report["partition_bytes"] != DEVICE["capacity_bytes"]
            or report["image_bytes"] != image.stat().st_size):
        raise ValueError("Packed windows, verification tables or target differ")
    report["storage_required_bytes"] = check_capacity(report["image_bytes"])
    report["data_gib"] = DEVICE["data_gib"]
    report["flash_command"] = "fastboot flash linux linux-root.img"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Verified partition image: {report['image_bytes']} bytes, SHA-256 {report['image_sha256']}")


if __name__ == "__main__":
    main()
