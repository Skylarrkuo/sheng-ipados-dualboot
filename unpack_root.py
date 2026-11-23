"""Restore the split Actions artifact on Windows, macOS or Linux with Python 3.14."""
import argparse
import hashlib
import json
import os
import tempfile
from compression import zstd
from pathlib import Path

from configure import DEVICE


def unpack(folder, output):
    report = json.loads((folder / "linux-root.json").read_text())
    if (report["target_partition"] != "linux" or report["partition_bytes"] != DEVICE["capacity_bytes"]
            or not 0 < report["image_bytes"] <= report["storage_required_bytes"] <= report["partition_bytes"]):
        raise ValueError("The root manifest does not match this device")
    sums = {}
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if name in sums or Path(name).name != name:
            raise ValueError(f"Unexpected or duplicate checksum name: {name}")
        sums[name] = digest
    parts = sorted(folder.glob("linux-root.img.zst.???"))
    if not parts or set(sums) != {"linux-root.json", *(p.name for p in parts)}:
        raise ValueError("Missing or unexpected root artifact files")
    if hashlib.sha256((folder / "linux-root.json").read_bytes()).hexdigest() != sums["linux-root.json"]:
        raise ValueError("Root report checksum mismatch")
    if output.exists():
        raise FileExistsError(output)
    decoder = zstd.ZstdDecompressor()
    raw_hash = hashlib.sha256()
    # On Windows the temp file must be closed before rename.
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as tmp:
        partial = Path(tmp.name)
    try:
        with partial.open("wb") as target:
            for part in parts:
                part_hash = hashlib.sha256()
                with part.open("rb") as src:
                    while chunk := src.read(1 << 20):
                        part_hash.update(chunk)
                        if decoder.eof:
                            raise ValueError("Unexpected data after zstd frame")
                        data = chunk
                        while True:
                            raw = decoder.decompress(data, 8 << 20)
                            if target.tell() + len(raw) > report["image_bytes"]:
                                raise ValueError("Decompressed image exceeds the manifest")
                            target.write(raw)
                            raw_hash.update(raw)
                            if decoder.eof or decoder.needs_input:
                                break
                            data = b""
                if part_hash.hexdigest() != sums[part.name]:
                    raise ValueError(f"Checksum mismatch: {part.name}")
            if (not decoder.eof or decoder.unused_data or target.tell() != report["image_bytes"]
                    or raw_hash.hexdigest() != report["image_sha256"]):
                raise ValueError("Incomplete or mismatched root image")
        # Publish only a complete image, without overwriting another concurrent unpack.
        if os.name == "nt":
            partial.rename(output)
        else:
            os.link(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    print(f"{output}: {report['image_bytes']} bytes, SHA-256 {raw_hash.hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    unpack(args.artifact, args.output)
