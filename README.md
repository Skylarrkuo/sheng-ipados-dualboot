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

手动运行默认只构建 `boot`。GPU、运行时与 Data、XNU 对象与驱动、RELEASE LTO 参考内核、DEVELOPMENT 链接、预链接、EFI 包、payload、UEFI 固件和打包 ramdisk 的主机工具 TianoCompress 分别保存缓存。每项成功就上传，后续阶段失败也可复用。Actions 摘要逐项显示实际编译或恢复及耗时。恢复时核对目标、输入和缓存包 SHA-256，打包后仍执行镜像校验。工具链或该阶段输入变化会重新编译。

相同 `data_generation` 在运行时缓存命中时沿用同一份首装种子。缓存缺失时，本工作流的首装模式会生成新种子，并按新种子的 SHA-256 和卷 UUID 重新选择内核缓存。给已经安装的设备做升级时须保留并使用原始 `data.dbr`。GitHub 可以回收构建缓存，安装种子应与发布产物一起另存。

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

官方分区工具用于把位于 GPT 末尾的 F2FS `userdata` 缩小，并建立名为 `unixnu` 的分区。本机的 A、B 槽 GPT 备份都显示 `userdata` 与 `linux` 相邻，各为 124,748,038,144 字节；只按容量无法区分两者。驱动校验 GPT 的 CRC、分区名 `linux` 和容量。未找到、重名、大小有误或分区重叠时停止。Data 和日志位置按选中分区的边界计算。打包器校验根卷、80 GiB Data、位图与头部、4 MiB 间隔和 1 GiB 日志预留。

UEFI 使用锁定版本的官方 sheng 内存图和 ABL 探测逻辑。构建前校验该文件与上游提交一致。12 GiB 型号的实际可用内存和启动稳定性需要实机验证。

iPadOS 24A446 的内核集合给原生代码和数据段预留了固定空间。板级 C/C++ 对象使用 `-Oz`，Wi-Fi 驱动使用 `-Os`，视频与 Wi-Fi 的大缓冲区在设备首次使用时分配。预链接采用设备维护者针对该 kernelcache 和函数内容 SHA-256 核验过的 `ml_at_interrupt_context` 符号清单。[Wi-Fi 尺寸优化](https://github.com/Soneoylys/UniXNU-sheng/commit/cd2e12f0f15f) · [板级对象尺寸优化](https://github.com/Soneoylys/UniXNU-sheng/commit/c59747242e86) · [驱动缓冲区](https://github.com/Soneoylys/UniXNU-sheng/commit/9b0fdfd4d997) · [符号清单](https://github.com/Soneoylys/UniXNU-sheng/commit/5b9df78c3835)

两个入口都显式选择 `ipados-27.0.1-24A446`。构建前检查 UniXNU 合并后的 iPad 型号、defconfig、根卷、Data、音频、Wi-Fi 和蓝牙目标，并在固定的 Apple XNU 源码上顺序试用全部补丁。首次安装请使用新检出目录；构建会检查本地覆盖配置和已有的 Data 种子。启动镜像打包后，从清单指定的 addon 读取实际签入的七个图形、音频和无线二进制文件，核对其 Mach-O 平台及架构。

参考 [设备支持记录](https://unixnu.com/devices/sheng)、[双系统分区](https://unixnu.com/porting/dual-system-partition)、[UFS 存储](https://unixnu.com/porting/ufs)、[根镜像布局](https://unixnu.com/guide/userdata)、[构建与启动](https://unixnu.com/building)。
