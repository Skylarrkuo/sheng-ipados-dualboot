"""Checkpoint the expensive steps of the pinned sheng iPadOS release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import sys

from audit_target import check_inputs, verify_profile
from configure import BOARD, CONFIG, CORE, DEVICE, HERE, SRC
from kernel_port import BUFFERS

sys.path.insert(0, str(CORE / "scripts"))
from unixnu import cli, feeds, firmware, kernel, package, patches, payload, pipeline, runtime, toolchain
from unixnu.config import Context

CACHE = HERE / ".checkpoints"
STAGES = ("gpu", "runtime", "archives", "reference", "link", "prelink", "package", "payload", "firmware")


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def context():
    check_inputs()
    args = cli.parser().parse_args(["release", "-D", str(BOARD), "--os", DEVICE["profile"],
                                    "--uefi", str(SRC / "uefi-sheng"), "--init-data"])
    ctx = Context(args)
    verify_profile(ctx)
    return ctx


def prepare(ctx):
    ctx.args.check = True
    cli.cmd_release(ctx)
    firmware.preflight_toolchain(ctx)
    cli.prepare(ctx, cli.STEPS)
    kernel.preflight(ctx)
    commands = [["xcodebuild", "-version"], ["xcrun", "--sdk", "iphoneos", "--show-sdk-build-version"],
                ["xcrun", "clang", "--version"], ["rustc", "+1.98.0", "-Vv"],
                ["brew", "list", "--versions", "llvm", "lld", "llvm@22", "lld@22", "cmake", "ninja",
                 "glslang", "shaderc", "spirv-tools", "bison", "pkgconf", "dtc", "mtools", "ipsw"]]
    versions = [subprocess.check_output(cmd, text=True).strip() for cmd in commands]
    write(CACHE / "host.json", [platform.machine(), platform.mac_ver()[0], sys.version, str(HERE), versions])


def plugin(ctx):
    return ctx.tree / "build/adreno-metal-plugin27"


def inputs(ctx):
    return read(ctx.work / "runtime/inputs.json")


def bind(ctx, verify=False):
    record = inputs(ctx)
    for name, option in (("data_ramdisk", "data_ramdisk"), ("trust_cache", "owner_trust_cache"), ("gpu_addon", "gpu_addon")):
        item = record["inputs"][name]
        path = Path(item["path"])
        if path.parent.resolve() != (ctx.work / "runtime").resolve() or not path.is_file():
            raise ValueError(f"unexpected runtime input: {path}")
        if verify and digest(path) != item["sha256"]:
            raise ValueError(f"corrupt runtime input: {name}")
        setattr(ctx.args, option, str(path))
    if verify:
        identity = runtime.bind_seed(ctx, Path(ctx.args.data_ramdisk), kernel_build=True)
        if identity != record["data_volume_uuid"]:
            raise ValueError("Data UUID differs from the cached runtime manifest")
    elif ctx.need_config().get("XNU_OWNER_DATA_VOLUME_UUID") != record["data_volume_uuid"]:
        raise ValueError("kernel configuration and Data have different volume UUIDs")
    ctx.args.init_data = False
    ctx._runtime_prepared = True


def receipt(stage):
    return read(CACHE / stage / "receipt.json")["sha256"]


def key(ctx, stage):
    base = [DEVICE["profile"], read(CACHE / "host.json"), CONFIG["source"]]
    if stage == "gpu":
        files = ["build-boot-runtime.py", "build-turnip-darwin.py", "build-adreno-metal-plugin.py",
                 "build-adreno-metal-bridge.py", "data/metal2vulkan-ios.patch"]
        dependencies = [digest(ctx.tree / "scripts" / path) for path in files]
    elif stage == "runtime":
        dependencies = [receipt("gpu"), ctx.board["payload"]["runtime"], ctx.board["ipsw"],
                        digest(HERE / "prepare_wifi.py"), os.environ["SHENG_DATA_GENERATION"]]
    elif stage == "archives":
        dependencies = [CONFIG, digest(ctx.config_path), inputs(ctx)["inputs"]["data_ramdisk"]["sha256"],
                        digest(HERE / "configure.py"), digest(HERE / "kernel_port.py"), digest(BUFFERS),
                        digest(BOARD / "unixnu.lock")]
    elif stage == "reference":
        folder = ctx.tree / "build"
        libs = ["corecrypto-2023/libcorecrypto_kernel_noasm-namespaced.a", "libpthread-kernel/libpthread_kernel.a",
                "armdt-platform/libArmDTPlatform.a", "armcpu/libArmCpu.a"]
        tree_hash = runpy.run_path(str(ctx.tree / "scripts/build-fileset-release-reference.py"))["tree_hash"]
        dependencies = [digest(ctx.config_path), [digest(folder / name) for name in libs],
                        tree_hash(ctx.tree / ".cache/xnu-work"), tree_hash(ctx.tree / ".cache/fakeroot")]
    elif stage == "link":
        dependencies = [receipt("archives"), digest(ctx.config_path)]
    elif stage == "prelink":
        dependencies = [receipt("link"), receipt("reference"), ctx.board["kernel"]["prelink"]]
    elif stage == "package":
        dependencies = [ctx.args.target, digest(BOARD / "unixnu.lock")]
    elif stage == "payload":
        dependencies = [receipt("prelink"), receipt("runtime"), receipt("archives"), receipt("package"),
                        ctx.board["payload"], ctx.board["adt"], os.environ["UNIXNU_UNIQUE_CHIP_ID"]]
    else:
        dependencies = [receipt("package"), receipt("archives"), ctx.board.get("firmware"), ctx.args.target]
    return f"sheng-24A446-arm64e-v1-{stage}-{fingerprint([base, dependencies])}"


def build(ctx, stage):
    modules = feeds.resolve(ctx)
    if stage == "gpu":
        os.chdir(ctx.tree)
        sys.path.insert(0, str(ctx.tree / "scripts"))
        runpy.run_path(str(ctx.tree / "scripts/build-boot-runtime.py"))["build_plugin"]("ios27")
    elif stage == "runtime":
        subprocess.run([sys.executable, HERE / "prepare_wifi.py"], check=True)
        os.environ["SHENG_CI_GPU_READY"] = "1"
        runtime.prepare(ctx, kernel_build=True)
    elif stage in ("archives", "reference", "link", "prelink"):
        bind(ctx)
        if stage == "archives":
            # A restored runtime carries binaries only. A driver rebuild still
            # needs the locked Wi-Fi source checkout on this fresh runner.
            subprocess.run([sys.executable, HERE / "prepare_wifi.py"], check=True)
            write(CACHE / "pre-archives.json", sorted(p.name for p in (ctx.tree / "build").iterdir()))
        with toolchain.environment(ctx):
            if stage == "prelink":
                kernel.prelink(ctx)
            else:
                kernel.build(ctx, modules, phase=stage)
    elif stage == "package":
        package.build(ctx, modules, ctx.args.target)
    elif stage == "payload":
        bind(ctx)
        ctx.uefi = cli.need_uefi(ctx)
        payload.begin_build(ctx)
        payload.build(ctx)
        payload.finish_build(ctx)
    elif stage == "firmware":
        firmware.build(ctx, modules, cli.need_uefi(ctx), package.edk2_dir(ctx, modules), ctx.args.target)


def outputs(ctx, stage):
    if stage == "gpu":
        return [plugin(ctx)]
    if stage == "runtime":
        directory = ctx.work / "runtime"
        return [directory / "inputs.json", *(Path(v["path"]) for v in inputs(ctx)["inputs"].values())]
    if stage == "archives":
        previous = set(read(CACHE / "pre-archives.json"))
        return [ctx.tree / ".cache/xnu-work", ctx.tree / ".cache/fakeroot",
                *(p for p in sorted((ctx.tree / "build").iterdir()) if p.name not in previous)]
    if stage == "reference":
        binary = ctx.tree / "build/xnu/fileset-reference/mach.release.bcm2837.unstripped.noctf"
        return [binary, binary.with_suffix(".json")]
    if stage == "link":
        return [ctx.tree / ctx.board["kernel"]["prelink"]["kernel"]]
    if stage == "prelink":
        return [ctx.work / "kernel"]
    if stage == "package":
        return [package.output_dir(ctx.tree, ctx.args.target)]
    if stage == "payload":
        _, image, report = payload.paths(ctx)
        return [image, report]
    return [firmware.fd_path(ctx, cli.need_uefi(ctx), ctx.args.target), ctx.tree / ctx.board["bootimg"]["dtb"]]


def check_stage(ctx, stage):
    if stage == "gpu":
        bundle = plugin(ctx) / "NativeAdrenoMetal.bundle"
        for path in (bundle, bundle / "libvulkan_freedreno.dylib"):
            subprocess.run(["codesign", "--verify", "--strict", path], check=True)
    elif stage == "runtime":
        bind(ctx, verify=True)
    elif stage == "reference":
        binary, record = outputs(ctx, stage)
        if digest(binary) != read(record)["sha256"]:
            raise ValueError("release reference digest mismatch")


def checkpoint(ctx, stage, cache_key, hit):
    from compression import zstd  # The CI workflow pins Python 3.14.
    directory = CACHE / stage
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "outputs.tar.zst"
    manifest = directory / "receipt.json"
    if hit:
        record = read(manifest)
        if record["key"] != cache_key or record["sha256"] != digest(archive):
            raise ValueError(f"{stage}: incorrect key or corrupt checkpoint")
        with zstd.open(archive, "rb") as source, subprocess.Popen(
            ["tar", "-xpf", "-", "-C", HERE], stdin=subprocess.PIPE
        ) as tar:
            with tar.stdin:
                shutil.copyfileobj(source, tar.stdin, 1024 * 1024)
            if tar.wait():
                raise ValueError(f"{stage}: could not extract checkpoint")
        check_stage(ctx, stage)
        return
    build(ctx, stage)
    check_stage(ctx, stage)
    paths = outputs(ctx, stage)
    if not paths or any(not path.exists() for path in paths):
        raise ValueError(f"{stage}: missing stage outputs: {paths}")
    # bsdtar retains modes, links and timestamps. Keep all git configuration outside public caches.
    names = [str(path.relative_to(HERE)) for path in paths]
    with subprocess.Popen(["tar", "-cf", "-", "--exclude", ".git", "-C", HERE, *names], stdout=subprocess.PIPE) as tar:
        with tar.stdout, zstd.open(archive, "wb", level=1) as target:
            shutil.copyfileobj(tar.stdout, target, 1024 * 1024)
        if tar.wait():
            raise RuntimeError(f"{stage}: tar failed")
    write(manifest, {"key": cache_key, "sha256": digest(archive), "paths": names})


def finish(ctx):
    modules = feeds.resolve(ctx)
    if ctx.soc:
        modules["soc", ctx.soc.name] = ctx.soc
    ctx.args.fd = ctx.args.ramdisk = ctx.args.output = ctx.args.dtb = None
    cli.cmd_bootimg(ctx)
    files = pipeline.outputs(ctx)
    manifest = pipeline.write_manifest(ctx, modules, patches.plan(ctx, modules), files)
    pipeline.verify(ctx, files, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "key", "build", "restore", "finish"))
    parser.add_argument("stage", choices=STAGES, nargs="?")
    parser.add_argument("--key")
    args = parser.parse_args()
    ctx = context()
    if args.command == "prepare":
        prepare(ctx)
    elif args.command == "finish":
        finish(ctx)
    elif args.command == "key":
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print("key=" + key(ctx, args.stage), file=output)
    else:
        checkpoint(ctx, args.stage, args.key, args.command == "restore")


if __name__ == "__main__":
    main()
