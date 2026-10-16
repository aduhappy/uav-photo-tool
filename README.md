# 无人机照片工具（uav-photo-tool）

一个**单文件、免安装**的 Windows 小工具，用来预处理无人机航测照片。双击 exe 就能用，不需要装 Python。

最初是为**室内航测**写的：室内 GPS 信号差，同一架次里有的照片坐标是 0,0，有的在漂，有的又是 RTK 固定解。空三软件把这些坐标当作先验，结果互相打架，对齐不上。这个工具先把定位信息剥掉，再做只靠影像匹配的空三。

> English: A single-file, zero-dependency Windows tool for preprocessing drone survey photos. It strips GPS/location metadata from DJI JPEGs **byte-for-byte without re-encoding the image**, and can drive Agisoft Metashape Pro to align the photos and export a Blocks Exchange XML that ContextCapture / iTwin Capture Modeler can import.

## 功能

### 1. 剔除定位

把一个目录（可含子目录）里的 JPG 照片去掉定位信息，另存到新目录。**原片不动。**

- 删除的内容：
  - EXIF 里的 GPS 信息；
  - 大疆 XMP 里的位置字段（`GpsLatitude`、`GpsLongitude`、`AbsoluteAltitude`、`RelativeAltitude`、`GpsStatus`、`Rtk*`、`LRFTarget*` 等）。
- 可选：同时剔除姿态，也就是云台和机身角度（`Gimbal*`、`Flight*`）。
- 保留的内容：相机标定参数（`DewarpData`、`Calibrated*`）、拍摄时间、相机型号、缩略图。
- **只改元数据，不重新压缩图像**：
  - 输出文件和原文件大小相同；
  - 图像数据逐字节不变；
  - 文件名和修改时间也保持不变。
- 默认输出到同级目录 `<原目录名>_去定位`。输出目录不是空的就拒绝写入，避免覆盖。
- 非 JPG 文件（如 `.MRK`、`Thumbs.db`）会跳过，并记进日志 CSV。

### 2. 空三（需要 Metashape Pro）

调用你电脑上已安装的 **Agisoft Metashape Pro** 对齐照片，导出 **Blocks Exchange XML**。

- 第一次使用时选择 `metashape.exe` 的位置（老版本是 `photoscan.exe`），之后会记住。
- 照片还带定位时，默认先自动去定位再空三。Metashape 里也会关掉位置和姿态参考。
- 默认参数：高精度、关键点 40000、连接点 4000。精度和两个上限可以在界面上改。
- 结果输出到 `<照片目录名>_空三\`，包括：
  - Metashape 工程（`.psx`）；
  - Blocks Exchange XML；
  - 空三报告：写明对齐了几张、没对齐的是哪几张；
  - Metashape 日志。
- **导出的 XML 不带坐标系**（局部坐标，尺度任意），可以直接导入 ContextCapture。之后在 CC 里加控制点再做一次空三，就能得到真实坐标。
- 只对齐了一部分照片时，XML 只写已对齐的相机，工程文件里保留全部照片。
- 可以批量处理：每个含照片的子目录单独做一个工程。

## 下载

到 [Releases](https://github.com/aduhappy/uav-photo-tool/releases) 下载最新的 `uav-photo-tool_v*.exe`，双击运行即可。

软件启动时会在后台查一次有没有新版本，有的话会弹窗提示并给出下载链接，不会自动下载或替换。不想要这个提示，可以在菜单「帮助 → 启动时自动检查更新」里关掉。

## 系统要求

- Windows 10 / 11，64 位。
- 「剔除定位」不需要安装任何东西。
- 「空三」需要 Metashape **Pro** 版（标准版不支持脚本，用不了）。目前只在 Metashape Pro 1.7.2 上实测过，2.x 和 PhotoScan 1.4 没有测过，欢迎反馈。
- 已测试的相机：DJI M3E（Mavic 3 Enterprise）。其他大疆机型的照片结构相近，应该也能用；遇到问题请提 Issue。

## 命令行

exe 和 Python 源码都可以用命令行调用，方便批量处理：

```text
uav_photo_tool.py strip-gps <输入目录> [输出目录] [--no-recursive] [--strip-attitude]
uav_photo_tool.py align <照片目录> [--metashape 路径] [--accuracy high|medium|low]
                        [--recursive] [--no-auto-strip] [--out 输出位置]
                        [--keypoints N] [--tiepoints N] [--log 日志文件]
uav_photo_tool.py check-update
```

不带参数就启动图形界面。

exe 打包时没有带控制台窗口，所以用 exe 跑命令行时看不到输出，请看返回的退出码、输出目录里的日志，或加 `--log` 参数另存日志。

退出码含义：

| 命令 | 0 | 1 | 2 | 3 | 10 |
|---|---|---|---|---|---|
| `strip-gps` | 全部成功 | 有照片失败 | 拒绝执行（如输出目录非空） | | |
| `align` | 全部成功 | 有工程失败 | 拒绝执行（如照片带定位、Metashape 路径不对） | 已取消 | |
| `check-update` | 已是最新 | 查询失败 | | | 有新版本 |

## 从源码运行和打包

- 源码只有一个文件 `src/uav_photo_tool.py`，只用 Python 标准库（含 tkinter）。需要 Python 3.8 或更高版本。

  ```powershell
  python src/uav_photo_tool.py            # 启动界面
  ```

- 打包成单文件 exe 需要先装 PyInstaller，输出在 `发布\` 目录：

  ```powershell
  python -m pip install pyinstaller
  pwsh -File scripts/build_exe.ps1 -Python python
  ```

- 测试脚本（需要 Pillow）：

  ```powershell
  python scripts/test_strip_gps.py <原片目录> <去定位输出目录> [--pixels]
  python scripts/test_align.py <空三输出目录> [--expect-aligned N] [--metashape 路径]
  ```

## 许可

[MIT](LICENSE)。Metashape 是 Agisoft 的商业软件，本工具不包含它，只调用你电脑上已安装的版本。
