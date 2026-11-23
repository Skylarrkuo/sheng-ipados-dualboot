"""Validate UniXNU's resolved target before compiling or packaging."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from configure import BOARD, CORE, DEVICE, TARGET


def check_inputs():
    local = [path for path in (CORE / "unixnu.local.toml", BOARD / "local.toml") if path.exists()]
    external = sorted(key for key, value in os.environ.items() if value and
                      ((key.startswith("UNIXNU_") and key not in {"UNIXNU_IPSW_JOBS", "UNIXNU_UNIQUE_CHIP_ID"})
                       or key in {"SDKROOT", "MACOSX_DEPLOYMENT_TARGET", "NMB_PREBUILT_BRIDGE",
                                  "QCA_BLUETOOTH_DIR", "ADRENO_PLUGIN_DIR", "ADRENO_TURNIP_DIR"}))
    if local or external:
        raise ValueError(f"External target overrides detected: {[*map(str, local), *external]}")


def context():
    check_inputs()
    sys.path.insert(0, str(CORE / "scripts"))
    from unixnu.cli import parser
    from unixnu.config import Context

    args = parser().parse_args(["build", "userdata", "-D", str(BOARD), "--os", DEVICE["profile"]])
    ctx = Context(args)
    verify_profile(ctx)
    return ctx


def verify_profile(ctx):
    board = ctx.board
    runtime = board["payload"]["runtime"]
    ipsw = board["ipsw"]
    root = board["adt"]["root"]["string"]
    layout = board["userdata"]
    expected = {
        "device": (ctx.name, "sheng"), "output": (ctx.work, TARGET),
        "profile": (board["os"]["name"], DEVICE["profile"]),
        "architecture": (board["os"]["arch"], "arm64e"),
        "defconfig": (ctx.config_name, "sheng_ipados27_defconfig"),
        "kernel fragments": (board["kernel"]["config_fragments"], ["arch:configs/common.config", "os:configs/ipados.config"]),
        "system": ((ipsw["product"], ipsw["device_class"], ipsw["version"], ipsw["build"]),
                   ("iPad17,3", "j820ap", "27.0.1", "24A446")),
        "ADT": ((root["model"], root["target-type"]), ("iPad17,3", "J820")),
        "runtime": ((runtime["target"], runtime["build"], runtime["audio_target"], runtime["wifi_target"],
                     runtime["bluetooth_target"]), ("ios27", "24A446", "arm64e-apple-ios27.0", "ios27", "ios27")),
        "Data GiB": (runtime["data_gib"], DEVICE["data_gib"]),
        "desktop seed or shims": (bool(runtime.get("data_seed_tool") or runtime.get("boot_shims")), False),
        "root volumes": ([(w["name"], w["component"]) for w in layout["windows"]],
                         [("OS", "OS"), ("SystemOS", "Cryptex1,SystemOS")]),
        "partition": ((layout["target_partition"], layout["capacity_bytes"], layout["lba_bytes"]),
                      ("linux", DEVICE["capacity_bytes"], 4096)),
        "fixed Android LBAs": ("first_lba" in layout or "last_lba" in layout, False),
        "boot capacity": (board["package"]["boot_partition_bytes"], DEVICE["boot_b_bytes"]),
        "kernel root UUID": (board["payload"]["adt"]["root_match_volume_uuid"], board["root"]["os_volume_uuid"]),
    }
    for key, (actual, wanted) in expected.items():
        if actual != wanted:
            raise ValueError(f"{key}: expected {wanted!r}, got {actual!r}")
    return {key: str(value) if isinstance(value, Path) else value for key, (value, _) in expected.items()}


def check_patch_series(ctx):
    from unixnu import feeds, patches
    modules = feeds.resolve(ctx)
    xnu = modules["refer", "xnu"].source
    revision = ctx.board["os"]["xnu_revision"]
    with tempfile.TemporaryDirectory() as directory:
        args = ["git", "-C", directory]
        subprocess.run([*args, "init", "-q"], check=True)
        subprocess.run([*args, "fetch", "-q", "--depth=1", xnu["git"], revision], check=True)
        subprocess.run([*args, "checkout", "-q", "--detach", revision], check=True)
        series = patches.plan(ctx, modules)
        for path in series:
            subprocess.run([*args, "apply", "--whitespace=nowarn", "-"],
                           input=path.read_bytes().replace(b"\r\n", b"\n"), check=True)
        print(f"Applied all {len(series)} patches to Apple XNU {revision}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patches", action="store_true")
    args = parser.parse_args()
    ctx = context()
    print(json.dumps(verify_profile(ctx), indent=2), flush=True)
    if args.patches:
        check_patch_series(ctx)
