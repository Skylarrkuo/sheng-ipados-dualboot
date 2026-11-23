"""Materialize the Wi-Fi driver's locked FreeBSD commit before its own fetcher runs."""
import json
import subprocess

from configure import BOARD, git


def main():
    wifi = BOARD / "drivers/Ath12kWifi"
    source = json.loads((wifi / "tools/source-lock.json").read_text())["freebsd"]
    path = wifi / "deps/freebsd"
    if not path.exists():
        path.mkdir(parents=True)
        git(path, "init", "-q")
        git(path, "remote", "add", "origin", source["url"])
        git(path, "sparse-checkout", "init", "--cone")
        git(path, "sparse-checkout", "set", *[p.strip("/") for p in source["paths"]])
        git(path, "fetch", "--depth=1", "--filter=blob:none", "origin", source["commit"])
        git(path, "checkout", "--detach", "FETCH_HEAD")
    if git(path, "rev-parse", "HEAD") != source["commit"]:
        raise ValueError("Wi-Fi dependency does not match source-lock.json")
    subprocess.run(["python3", wifi / "tools/fetch-sources.py"], check=True)


if __name__ == "__main__":
    main()
