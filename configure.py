"""Prepare dedicated upstream checkouts for the sheng second-system build."""
import argparse
import subprocess
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
CONFIG = tomllib.loads((HERE / "build.toml").read_text(encoding="utf-8"))
DEVICE = CONFIG["device"]
BOARD = SRC / "UniXNU-sheng"
CORE = SRC / "UniXNU"
UFS = BOARD / "drivers/ShengUfs"
TARGET = CORE / "out/sheng" / DEVICE["profile"] / "sheng_ipados27"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()


def checkout(minimal=False):
    SRC.mkdir(exist_ok=True)
    for name, rev in CONFIG["source"].items():
        path = SRC / name
        if not (path / ".git").is_dir():
            path.mkdir(exist_ok=True)
            git(path, "init", "-q")
            git(path, "remote", "add", "origin", f"https://github.com/UniXNU/{name}.git")
            git(path, "fetch", "--depth=1", "origin", rev)
            git(path, "checkout", "--detach", "FETCH_HEAD")
        if git(path, "rev-parse", "HEAD") != rev:
            raise ValueError(f"{name}: expected {rev}")
        if not minimal:
            git(path, "submodule", "update", "--init", "--recursive", "--jobs", "8")
    if minimal:
        git(BOARD, "submodule", "update", "--init", "--depth", "1", "drivers/ShengUfs")


def edit(path, replacements):
    text = path.read_text(encoding="utf-8")
    for before, after in replacements:
        if text.count(after) == 1:
            continue
        if text.count(before) != 1:
            raise ValueError(f"Unexpected upstream text in {path}: {before!r}")
        text = text.replace(before, after)
    path.write_text(text, encoding="utf-8", newline="\n")


def configure():
    for name, rev in CONFIG["source"].items():
        if git(SRC / name, "rev-parse", "HEAD") != rev:
            raise ValueError(f"Wrong {name} revision")
    if git(UFS, "rev-parse", "HEAD") != git(BOARD, "rev-parse", "HEAD:drivers/ShengUfs"):
        raise ValueError("ShengUfs differs from the device gitlink")
    part = DEVICE["partition"]
    capacity = DEVICE["capacity_bytes"]
    if part != "linux" or capacity % 4096 or capacity <= 0:
        raise ValueError("Expected a separate linux partition with 4 KiB blocks")
    header = UFS / "RootLayout.h"
    edit(header, [
        ('static const char wanted[] = "userdata";', 'static const char wanted[] = "linux";'),
        ('*why = "duplicate userdata partition";', '*why = "duplicate linux partition";'),
        ('*why = "no userdata partition";', '*why = "no linux partition";'),
        ('  gRootDiagnostic.first = layout->userdataFirst;',
         f'  if (layout->userdataLast - layout->userdataFirst + 1 != {capacity // 4096}ULL) {{\n'
         '    *why = "linux partition size mismatch";\n    return false;\n  }\n'
         '  gRootDiagnostic.first = layout->userdataFirst;')])
    # This release's packer sizes single-system images from the original userdata LBAs.
    # For this board the GPT location is resolved at boot; the host needs only the capacity.
    edit(CORE / "scripts/unixnu/userdata.py", [
        ("capacity = (spec['last_lba'] - spec['first_lba'] + 1) * spec.get('lba_bytes', 4096)",
         "if spec.get('target_partition') != 'linux':\n        raise Error('expected linux target')\n"
         "    capacity = spec['capacity_bytes']"),
        ("'userdata_bytes': capacity, 'flash_command': f'fastboot flash userdata {image}'",
         "'target_partition': 'linux', 'partition_bytes': capacity, 'flash_command': f'fastboot flash linux {image}'")])
    board = BOARD / "board.toml"
    edit(board, [
        ('"first_lba" = 3094592\n"last_lba" = 64007162',
         f'target_partition = "linux"\ncapacity_bytes = {capacity}'),
        ("data_gib = 215", f"data_gib = {DEVICE['data_gib']}"),
        ('["package"]\n', f'["package"]\nboot_partition_bytes = {DEVICE["boot_b_bytes"]}\n'),
        ('"AppleKeyStoreState", "Adreno740", "AdrenoMetal"]',
         '"AppleKeyStoreState", "Adreno740", "AdrenoMetal", "QcomGeniSpi"]')])
    result = tomllib.loads(board.read_text(encoding="utf-8"))
    assert result["board"]["os_profile"] == DEVICE["profile"]
    assert result["userdata"]["target_partition"] == "linux"
    # The early static map can be used before ABL has supplied a dynamic map.
    edit(SRC / "uefi-sheng/Platforms/Xiaomi/shengPkg/Library/MemoryMapLib/MemoryMapLib.c",
         [('{"DRAM bank 1 low", 0x880000000, 0x3B300000,',
           '{"DRAM bank 1 low", 0x880000000, 0x39D00000,')])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="fetch pinned sources and their submodules")
    parser.add_argument("--minimal", action="store_true", help="root-only sources and ShengUfs")
    args = parser.parse_args()
    if args.fetch:
        checkout(args.minimal)
    configure()
