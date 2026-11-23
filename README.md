# 小米平板 6S Pro 的 HyperOS 与 iPadOS 双系统构建

本仓库从官方 UniXNU、UniXNU-sheng 和 uefi-sheng 源码构建 iPadOS 27.0.1（24A446），适配小米平板 6S Pro 12.4（sheng、SM8550）。目标设备已划出 124,748,038,144 字节的 `linux` 分区，`boot_b` 为 192 MiB。该配置用于保留 A 槽 HyperOS 及其 `userdata`。安装将替换原第二系统。

## 构建

在仓库根目录用 GitHub CLI 运行以下命令。工作流先在 Ubuntu 上测试存储驱动，然后在独立的 macOS runner 上分别构建启动镜像和根镜像。可用 `-f stage=root` 或 `-f stage=boot` 单独重建一个部分。

```sh
gh workflow run images.yml -f stage=both
gh run list --workflow images.yml
gh run watch RUN_ID --exit-status
gh run download RUN_ID --dir images
```

本地 Apple Silicon Mac 可使用与 `.github/workflows/images.yml` 相同的 Xcode 26.6 和依赖，分别运行 `bash build.sh root` 和 `bash build.sh boot`。源码版本、分区容量和 Data 逻辑大小记录在 [build.toml](build.toml)。公开构建使用固定的测试平台身份。首次安装生成新的 iPadOS Data 种子，同一安装后续维护应保存原始 `data.dbr`、启动镜像和清单。该流程没有设置 macOS 管理员账号或预置 iPad 解锁密码。

## 分区与镜像

| 产物 | 用途与校验 |
| --- | --- |
| `sheng-24A446-linux-root` | zstd 根镜像分卷，目标是 GPT 名为 `linux` 的分区。包含 OS、SystemOS 以及上游签名头对应的两份逐块哈希表。 |
| `sheng-24A446-boot` | `boot.img`、此次首装的 `data.dbr`、上游清单、目标平台检查结果 `target-audit.json` 与 SHA-256。启动镜像需通过 Android v0 镜像头、有效 ramdisk 和 192 MiB 上限校验。 |

GitHub Actions 的产物保留七天。在 Windows 上，从本仓库目录运行 `python unpack_root.py images/sheng-24A446-linux-root linux-root.img`（Python 3.14、NTFS），可边解压边核对分卷和整份根镜像。在 macOS 或 Linux 上也可在根镜像目录运行以下命令。

```sh
shasum -a 256 -c SHA256SUMS
cat linux-root.img.zst.* | zstd -d -o linux-root.img
python3 -c 'import hashlib,json; f=open("linux-root.img","rb"); assert hashlib.file_digest(f,"sha256").hexdigest()==json.load(open("linux-root.json"))["image_sha256"]'
```

启动镜像与根卷应取自同一版本和构建配置。实机安装前核对产品 `sheng`、`partition-size:linux`、`partition-size:boot_b`、当前槽，以及现有第二系统的备份。首次验证用 Fastboot 单次加载启动镜像；它仍需要已安装在 `linux` 的同版本根卷。常驻 B 槽、触控、Data 跨重启写入以及回到 HyperOS 均需在目标平板上验证。

## 双系统适配

官方分区工具用于把位于 GPT 末尾的 F2FS `userdata` 缩小，并建立名为 `unixnu` 的分区。本机已有独立的 `linux` 分区，构建流程直接使用现有布局。驱动只接受精确匹配且容量符合配置的 `linux`；未找到、重名、大小有误或 GPT 分区重叠时停止。存储驱动通过已选分区的边界计算 Data 和日志位置。打包器单独校验根卷、80 GiB Data、位图与头部、4 MiB 间隔和 1 GiB 日志预留。

UEFI 使用锁定版本的官方 sheng 内存图和 ABL 探测逻辑。构建前校验该文件与上游提交一致。12 GiB 型号的实际可用内存和启动稳定性需要实机验证。

两个入口都显式选择 `ipados-27.0.1-24A446`。构建前检查 UniXNU 合并后的 iPad 型号、defconfig、根卷、Data、音频、Wi-Fi 和蓝牙目标，并在固定的 Apple XNU 源码上顺序试用全部补丁。首次安装请使用新检出目录；构建会检查本地覆盖配置和已有的 Data 种子。启动镜像打包后，从清单指定的 addon 读取实际签入的七个图形、音频和无线二进制文件，核对其 Mach-O 平台及架构。

参考 [设备支持记录](https://unixnu.com/devices/sheng)、[双系统分区](https://unixnu.com/porting/dual-system-partition)、[UFS 存储](https://unixnu.com/porting/ufs)、[根镜像布局](https://unixnu.com/guide/userdata)、[构建与启动](https://unixnu.com/building)。
