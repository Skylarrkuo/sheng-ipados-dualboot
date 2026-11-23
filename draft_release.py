"""Publish a verified boot artifact and matching split root on the GitHub runner."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

from configure import DEVICE, HERE

ROOT = HERE / "dist/release-root"
BOOT = HERE / "dist/release-boot"
ROOT_SHA = "981110dacccc9ee7fc6778b2b13a83473856665770aea929edf933b499677694"
BOOT_FILES = {"boot.img", "data.dbr", "package.manifest.json", "target-audit.json"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True).strip()


def source_job(number, job):
    if not re.fullmatch(r"[0-9]+", number):
        raise ValueError("expected numeric workflow run ID")
    repo = os.environ["GH_REPO"]
    run = json.loads(gh("api", f"repos/{repo}/actions/runs/{number}"))
    jobs = json.loads(gh("run", "view", number, "--json", "jobs"))["jobs"]
    if (run["head_repository"]["full_name"] != repo or run["path"] != ".github/workflows/images.yml"
            or run["event"] not in {"push", "workflow_dispatch"} or run["status"] != "completed"
            or (job == "boot" and run["conclusion"] != "success")
            or not all(any(j["name"] == name and j["conclusion"] == "success" for j in jobs)
                       for name in ("storage-tests", job))):
        raise ValueError(f"run {number}: required image and storage jobs must succeed")
    return run["head_sha"]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_files(folder, names):
    sums = {}
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split(maxsplit=1)
        if name not in names or name in sums or not re.fullmatch("[0-9a-f]{64}", checksum):
            raise ValueError(f"invalid checksum entry: {name}")
        sums[name] = checksum
    if sums.keys() != names or {p.name for p in folder.iterdir()} != names | {"SHA256SUMS"}:
        raise ValueError(f"unexpected artifact layout in {folder}")
    for name, expected in sums.items():
        path = folder / name
        if not 0 < path.stat().st_size < 2**31 or digest(path) != expected:
            raise ValueError(f"asset size or digest mismatch: {name}")
    return sums


def verify():
    root = read(ROOT / "linux-root.json")
    if ((root["target_partition"], root["partition_bytes"], root["data_gib"], root["image_sha256"])
            != ("linux", DEVICE["capacity_bytes"], DEVICE["data_gib"], ROOT_SHA)):
        raise ValueError("root image does not match the verified sheng dual-boot build")
    chunks = {p.name for p in ROOT.glob("linux-root.img.zst.[a-z][a-z][a-z]")}
    if not chunks:
        raise ValueError("no compressed root chunks in artifact")
    root_sums = verify_files(ROOT, chunks | {"linux-root.json"})
    boot_sums = verify_files(BOOT, BOOT_FILES)
    manifest = read(BOOT / "package.manifest.json")
    report = read(BOOT / "target-audit.json")
    with (BOOT / "boot.img").open("rb") as stream:
        header = stream.read(44)
    if ((manifest["device"], manifest["os_profile"], manifest["outputs"]["boot.img"]["sha256"],
         manifest["runtime"]["inputs"]["data_ramdisk"], report["profile"]["partition"])
            != ("sheng", DEVICE["profile"], boot_sums["boot.img"], boot_sums["data.dbr"], ["linux", DEVICE["capacity_bytes"]])
            or not report["binaries"] or any(v["platform"] != "iOS" for v in report["binaries"].values())
            or header[:8] != b"ANDROID!" or struct.unpack_from("<I", header, 16)[0] == 0
            or (BOOT / "boot.img").stat().st_size > DEVICE["boot_b_bytes"]):
        raise ValueError("boot image, packaged runtime or Data seed differs from the verified target")
    (BOOT / "SHA256SUMS").rename(BOOT / "boot-SHA256SUMS.txt")
    assets = {**root_sums, **boot_sums, "SHA256SUMS": digest(ROOT / "SHA256SUMS"),
              "boot-SHA256SUMS.txt": digest(BOOT / "boot-SHA256SUMS.txt")}
    (HERE / "dist/release-assets.json").write_text(json.dumps(assets))


def upload():
    boot, root = os.environ["BOOT_RUN"], os.environ["ROOT_RUN"]
    repo = os.environ["GH_REPO"]
    commit = source_job(boot, "boot")
    tag = f"sheng-ipados-27.0.1-24A446-{boot}"
    base = f"https://github.com/{repo}"
    notes = HERE / "dist/draft-notes.md"
    notes.write_text(f"""小米平板 6S Pro 12.4（sheng）双系统开发镜像。A 槽为 HyperOS，iPadOS 使用 `boot_b` 和现有 `linux` 分区。镜像已通过构建校验，实机启动仍待测试。

下载 `boot.img` 和全部 `linux-root.img.zst.*` 分卷。保存与内核配套的 `data.dbr`。根镜像校验清单为 `SHA256SUMS`，启动文件校验清单为 `boot-SHA256SUMS.txt`。根镜像 SHA-256 为 `{ROOT_SHA}`。

[启动镜像构建]({base}/actions/runs/{boot}) · [根镜像构建]({base}/actions/runs/{root}) · [解包说明]({base}/blob/{commit}/README.md)
""", encoding="utf-8")
    # Listing releases distinguishes an absent tag from a transient API error.
    releases = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"))
    release = next((r for page in releases for r in page if r["tag_name"] == tag), None)
    if release is None:
        gh("release", "create", tag, "--draft", "--target", commit, "--title",
           "小米平板 6S Pro · iPadOS 27.0.1 (24A446) 双系统", "--notes-file", str(notes))
    elif not release["draft"] or release["target_commitish"] != commit:
        raise ValueError("existing release has a different target or has been published")

    def state():
        release = json.loads(gh("release", "view", tag, "--json", "isDraft,url,databaseId"))
        if not release["isDraft"]:
            raise ValueError("release must remain a draft")
        assets = json.loads(gh("api", f"repos/{repo}/releases/{release['databaseId']}/assets?per_page=100"))
        return release, {a["name"]: a for a in assets}

    manifest = read(HERE / "dist/release-assets.json")
    _, existing = state()
    for name, checksum in manifest.items():
        if name in existing:
            if existing[name]["digest"] != "sha256:" + checksum:
                raise ValueError(f"different asset already exists in draft: {name}")
            continue
        path = ROOT / name if (ROOT / name).is_file() else BOOT / name
        gh("release", "upload", tag, str(path))
    release, assets = state()
    if any(assets.get(name, {}).get("digest") != "sha256:" + expected for name, expected in manifest.items()):
        raise ValueError("GitHub did not report matching checksums for every release asset")
    print(f"Verified {len(manifest)} draft assets: {release['url']}")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        print(f"[Open the release draft]({release['url']}) · {len(manifest)} verified assets", file=summary)


if __name__ == "__main__":
    if sys.argv[1] == "check-runs":
        source_job(os.environ["BOOT_RUN"], "boot")
        source_job(os.environ["ROOT_RUN"], "root")
    elif sys.argv[1] == "verify":
        verify()
    elif sys.argv[1] == "upload":
        upload()
    else:
        raise SystemExit("expected check-runs, verify or upload")
