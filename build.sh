#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
project=$PWD
mode=${1:?Choose root or boot}
profile=ipados-27.0.1-24A446
target="$project/src/UniXNU/out/sheng/$profile/sheng_ipados27"
output="$project/dist/$mode"
mkdir -p "$output"
case "$mode" in
  root)
    python3 configure.py --fetch --minimal
    python3 root_image.py
    cp "$target/userdata/userdata-root.json" "$output/linux-root.json"
    zstd -T0 -3 -c "$target/userdata/userdata-root.img" | split -b 1024m -a 3 - "$output/linux-root.img.zst."
    expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image_sha256"])' "$output/linux-root.json")
    actual=$(cat "$output"/linux-root.img.zst.* | zstd -dc | shasum -a 256)
    test "${actual%% *}" = "$expected"
    ;;
  boot)
    python3 configure.py --fetch
    # A stable lab ADT identity; the public build uses no physical tablet identifier.
    export UNIXNU_UNIQUE_CHIP_ID=${UNIXNU_UNIQUE_CHIP_ID:-53483653}
    export DEVELOPER_DIR=${DEVELOPER_DIR:-/Applications/Xcode_26.6.app/Contents/Developer}
    xcodebuild -version
    python3 src/UniXNU-sheng/scripts/extract-files.py
    python3 prepare_wifi.py
    (cd src/UniXNU-sheng && bash scripts/package.sh --os 27 --uefi "$project/src/uefi-sheng" --init-data)
    python3 verify_boot.py
    cp "$target/boot/boot.img" "$target/package.manifest.json" "$target/runtime/data.dbr" "$output/"
    ;;
  *) echo 'Choose root or boot' >&2; exit 2 ;;
esac
case "$mode" in
  root) (cd "$output" && shasum -a 256 linux-root.json linux-root.img.zst.* > SHA256SUMS) ;;
  boot) (cd "$output" && shasum -a 256 boot.img data.dbr package.manifest.json > SHA256SUMS) ;;
esac
