"""Prepare dedicated upstream checkouts for the sheng second-system build."""
import argparse
import json
import shutil
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
            git(path, "submodule", "update", "--init", "--recursive", "--depth", "1", "--jobs", "8")
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


def ipados_patch_series(spec):
    directory = BOARD / "patches/xnu-12377"
    source_name = "sheng-startup-failure-log.patch"
    patched_name = "ipados-startup-failure-log.patch"
    source = (directory / source_name).read_text(encoding="utf-8")
    old = " #else\n \tint data_error = native_owner_data_prepare(p, (user_addr_t)scratch_addr);\n #endif\n"
    new = (" #if defined(XNU_OWNER_DATA_MOUNT) && XNU_OWNER_DATA_MOUNT\n"
           " \tboot_progress_stage(\"Data mount enter\");\n"
           " \tint data_error = native_owner_data_prepare(p, (user_addr_t)scratch_addr);\n")
    if source.count(old) != 1:
        raise ValueError("Unexpected XNU startup log patch")
    (directory / patched_name).write_text(source.replace(old, new), encoding="utf-8", newline="\n")
    order = (directory / "series").read_text(encoding="utf-8").splitlines()
    if order.count(source_name) != 1:
        raise ValueError("Unexpected sheng XNU patch order")
    order[order.index(source_name)] = patched_name
    (directory / "series-ipados-dualboot").write_text("\n".join(order) + "\n", encoding="utf-8", newline="\n")
    refs = list(spec["kernel"]["patch_series"])
    index = refs.index("device:patches/xnu-12377/series")
    refs[index] += "-ipados-dualboot"
    return refs


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
        ('Find the partition named "userdata"', 'Find the partition named "linux"'),
        ('static const char wanted[] = "userdata";', 'static const char wanted[] = "linux";'),
        ('*why = "duplicate userdata partition";', '*why = "duplicate linux partition";'),
        ('*why = "no userdata partition";', '*why = "no linux partition";'),
        ('*why = "userdata first block read";', '*why = "linux first block read";'),
        ('  gRootDiagnostic.first = layout->userdataFirst;',
         f'  if (layout->userdataLast - layout->userdataFirst + 1 != {capacity // 4096}ULL) {{\n'
         '    *why = "linux partition size mismatch";\n    return false;\n  }\n'
         '  gRootDiagnostic.first = layout->userdataFirst;')])
    edit(UFS / "RootDiagnostics.h", [("GPT/read lba=%llx userdata=", "GPT/read lba=%llx linux=")])
    edit(UFS / "NativeUfs.cpp", [
        ("UFS LUN 0 userdata window", "UFS LUN 0 linux window"),
        ("userdata first=%llx last=%llx\\n", "linux first=%llx last=%llx\\n"),
        ("userdata first=%llx last=%llx manifest", "linux first=%llx last=%llx manifest"),
        ("no valid root manifest in userdata", "no valid root manifest in linux"),
    ])
    # This release's packer sizes single-system images from the original userdata LBAs.
    # For this board the GPT location is resolved at boot; the host needs only the capacity.
    edit(CORE / "scripts/unixnu/userdata.py", [
        ("capacity = (spec['last_lba'] - spec['first_lba'] + 1) * spec.get('lba_bytes', 4096)",
         "if spec.get('target_partition') != 'linux':\n        raise Error('expected linux target')\n"
         "    capacity = spec['capacity_bytes']"),
        ("'userdata_bytes': capacity, 'flash_command': f'fastboot flash userdata {image}'",
         "'target_partition': 'linux', 'partition_bytes': capacity, 'flash_command': f'fastboot flash linux {image}'")])
    # Apply the device geometry only to the pinned iPadOS profile. Use the
    # original component order and drop the unpartitioned Android geometry.
    board = BOARD / "board.toml"
    original = subprocess.check_output(["git", "-C", BOARD, "show", "HEAD:board.toml"]).decode()
    spec = tomllib.loads(original)
    profile = DEVICE["profile"]
    if spec["board"]["os_profile"] != profile:
        raise ValueError("Unexpected upstream default board profile")
    components = list(dict.fromkeys([*spec["components"]["include"], "QcomGeniSpi"]))
    kernel_series = ipados_patch_series(spec)
    prefix = f'os_overrides."{profile}"'
    overlay = (f'\n[{prefix}]\nremove = ["userdata.first_lba", "userdata.last_lba"]\n'
               f'\n[{prefix}.userdata]\ntarget_partition = "linux"\ncapacity_bytes = {capacity}\n'
               f'\n[{prefix}.package]\nboot_partition_bytes = {DEVICE["boot_b_bytes"]}\n'
               f'\n[{prefix}.payload.runtime]\ndata_gib = {DEVICE["data_gib"]}\n'
               f'\n[{prefix}.kernel]\npatch_series = {json.dumps(kernel_series)}\n'
               f'\n[{prefix}.components]\ninclude = {json.dumps(components)}\n')
    if board.read_text(encoding="utf-8") not in (original, original + overlay):
        raise ValueError("Unexpected edits to the dedicated upstream board.toml")
    board.write_text(original + overlay, encoding="utf-8", newline="\n")
    # This port uses the firmware repository's original static and ABL maps.
    subprocess.run(["git", "-C", SRC / "uefi-sheng", "diff", "--quiet", "HEAD", "--",
                    "Platforms/Xiaomi/shengPkg/Library/MemoryMapLib/MemoryMapLib.c"], check=True)
    # These declarations are referenced exclusively by the macOS root-domain
    # notification handlers. The board compiles every driver with -Werror.
    desktop_guard = "#if defined(XNU_TARGET_OS_OSX) && XNU_TARGET_OS_OSX\n"
    clamshell = ('static const char kClamshellLog[] KEYBOARD_CONST = '
                 '"sheng keyboard: clamshell %s sent to the power manager\\n";')
    edit(BOARD / "package/KeyboardProbe.cpp", [(clamshell, desktop_guard + clamshell + "\n#endif")])
    wake_option = "constexpr IOOptionBits kWakeEventSource = 0x1;   // kIOPMWakeEventSource (IOPMPrivate.h)"
    edit(BOARD / "package/ShengSleep.cpp", [
        (wake_option, desktop_guard + wake_option + "\n#endif"),
        ("bool lidArmed() {", desktop_guard + "bool lidArmed() {"),
        (desktop_guard + "IOReturn sleepWake(", "/* macOS root-domain notification */\nIOReturn sleepWake("),
    ])
    # Apply after the two official translator patches, through the upstream tree-identity check.
    # The existing Mach implementation operates only on the calling task and checks the returned size.
    shutil.copyfile(HERE / "patches/metal2vulkan-ios.patch", CORE / "scripts/data/metal2vulkan-ios.patch")
    edit(CORE / "scripts/build-adreno-metal-bridge.py", [
        ("'0064-copies-leave-unreachable-pages-alone.patch']]",
         "'0064-copies-leave-unreachable-pages-alone.patch']]\n"
         "PATCHES.append(ROOT / 'scripts/data/metal2vulkan-ios.patch')")])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="fetch pinned sources and their submodules")
    parser.add_argument("--minimal", action="store_true", help="root-only sources and ShengUfs")
    args = parser.parse_args()
    if args.fetch:
        checkout(args.minimal)
    configure()
