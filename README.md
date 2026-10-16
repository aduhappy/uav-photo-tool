# 无人机照片工具（uav-photo-tool）

一个**单文件、免安装**的 Windows 小工具，用来预处理无人机航测照片。双击 exe 就能用，不需要装 Python。

最初是为**室内航测**写的：室内 GPS 信号差，同一架次里有的照片坐标是 0,0，有的在漂，有的又是 RTK 固定解。空三软件把这些坐标当作先验，结果互相打架，对齐不上。这个工具先把定位信息剥掉，再做只靠影像匹配的空三。

> English: A single-file, zero-dependency Windows tool for preprocessing drone survey photos. It strips GPS/location metadata from DJI JPEGs **byte-for-byte without re-encoding the image**, and can drive Agisoft Metashape Pro to align the photos and export a Blocks Exchange XML that ContextCapture / iTwin Capture Modeler can import. For repeated surveys of the same site it can also transfer ground control points from a reference epoch with zero manual tie-pointing, adjust, self-check, and export the block in local metric coordinates.

## 功能

### 1. 剔除定位

把一个目录（可含子目录）里的 JPG 照片去掉定位信息，默认另存到新目录，**原片不动**。也可明确选择「原地抹除」直接改原照片；该模式默认先写定位备份，之后可逐字节还原。定位一旦从照片中抹掉，不能仅靠照片本身找回，只能依靠备份。

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

**原地抹除（谨慎使用）**：命令行用 `strip-gps <照片目录> --in-place --confirm <目录名>`，确认名必须是目录名而不是完整路径；默认在第一张照片改动前写入并校验 `定位备份_<时间>.zip`，`--no-backup` 可关闭。每张照片先写同目录临时文件并自检，通过后才原子替换。用 `restore-gps <照片目录> --backup <定位备份.zip>` 还原；只有当前文件哈希仍等于抹除后的哈希才会写回，其他修改过的文件跳过。界面勾选「原地抹除」后会要求输入目录名确认。

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

### 3. 控制点（需要 Metashape Pro）

给空三结果加上控制点、平差到真实坐标（局部坐标，单位米），导出 ContextCapture 能直接用的 Blocks Exchange XML。适合**同一场地反复航测**（比如每期框架、控制点都不动）：第一次刺一次点，以后每期都不用刺。

有两种模式：

- **建参考期**（新场地第一次用）：
  - 输入：「空三」页签产出的 `.psx` + 控制点 CSV。
  - 刺点来源二选一：ContextCapture 导出的 Blocks Exchange（`.xml` 或 `.xmlz`，里面有你在 CC 里刺的点）；或者先在 Metashape 里给至少 3 个点、每点至少 2 张照片刺好并保存。
  - 软件把其余照片上的点自动补上、细化、平差，输出的 `.psx` 以后当「参考期」用。
- **迁移**（以后每一期，零刺点）：
  - 输入：新一期照片目录 + 参考期 `.psx`。
  - 软件把新照片和参考期一起匹配，按参考期的控制点自动放点、细化、平差。参考期的照片要留在原来的位置。
  - 一次约 5 分钟，其中联合匹配占 3～5 分钟。
- 两种模式都输出到 `<名字>_控制点\`：`.psx`、Blocks Exchange XML、`控制点报告.txt`、Metashape 日志。原工程只读，不会被改。

**控制点 CSV 格式**：每行 `点名,X,Y,Z`，单位米。

- 可以有表头；表头里写了 X/Y/Z，就按表头取列。
- 分隔符可以是逗号、制表符、分号或空格；编码 UTF-8 或 GBK 都行。
- 全站仪常用「X 北、Y 东」，和软件要的 X/Y 正好相反。建参考期时软件会把原样、X/Y 对调各算一次，相差 10 倍以上就自动判定，并在报告里写明（如「CSV 的 X/Y 对调了，已按对调后的坐标用」）。差别不到 10 倍时只提示你自己核对 CSV。
- CC 刺点的像素坐标比 Metashape 的小 0.5 像素（两边像素原点的约定不同），软件读 CC 刺点时已经自动加上了。

**怎么判定**（每个控制点）：

- 三维误差 ≤ 2×控制点精度（默认精度 0.01 m，也就是 20 mm）；
- 像素 RMS ≤ 3；
- 至少 3 张照片。

全部通过就是「自动通过」，否则是「需人工核对」，报告里会列出是哪些点、为什么，并附处理步骤。另外：

- 像素残差特别大的投影会先自动删掉，再重新平差。
- 迁移时，「细化标记点」把某个点挪动超过 50 像素，多半是认错了角点（比如降雨前后场景变了）：
  - 只是个别投影挪得远：把这几处投影删掉；
  - 这个点挪动的中位数超过 50 像素：整点自动排除，不参与平差，改作检查点（误差照报，仅供参考）。
  - 只要有点被自动排除，结论就是「需人工核对」。
  - 上限可以改：界面「细化挪动上限」，命令行 `--max-shift`。
- 报告里的「迁移偏差（细化挪动）」一栏，是自动放的位置和按图像细化后的位置相差多少像素，越小说明迁移越准。
- 你也可以手动排除某些点（界面「排除的控制点」填 `1,2`，命令行 `--exclude 1,2`）。被排除的点同样当检查点，不影响「自动通过」。
  - 已知问题：「迁移」模式里点名写错（比如参考期里没有 9 号却填了 `9`）时，要等 Metashape 启动后才报错，结论是「失败」（退出码 1），并留下一个不完整的输出目录。删掉这个目录、改对点名重跑即可。「建参考期」模式会在开跑前直接拒绝（退出码 2）。以后会统一成开跑前拒绝。
- 参与平差的点少于 3 个，判「失败」。

**导出的 XML** 写的是 ContextCapture 自己的局部米制坐标系（`Local:unit=meter`），区块和每个控制点都挂了坐标系，所以导入 CC 后单位显示为「米」。被排除的点在 XML 里标成检查点，CC 平差时不会用它们。「空三」页签的输出不受影响，仍然不带坐标系。

**批量迁移**：`scripts/batch_gcp_transfer.ps1` 会把一个上级目录下的每一期照片挨个迁移，按目录名自动选参考期（名字带「梯田」用梯田参考期，带「植被」用植被参考期），最后出一张汇总表（CSV，Excel 可直接打开）。已有结果的期次自动跳过，中途停了重新运行会接着做。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\batch_gcp_transfer.ps1 `
    -Tool <工具 exe> -Source <照片上级目录> -Out <输出目录> `
    [-RefTerrace <梯田参考期.psx>] [-RefVeg <植被参考期.psx>] `
    [-Select [-NoGui]] [-Include "*梯田*"] [-Pick 1,3,5-9] `
    [-Skip 名字1,名字2] [-ExcludeTerrace 4] [-ExcludeVeg ...] [-Plan]
```

选择要跑哪些期（可以组合，先按 `-Include` 筛，再按 `-Pick` / `-Select` 选）：

- `-Select`：扫描后弹出一个表格窗口，列出序号、期次、类型、照片数、参考期、状态。在窗口顶上的筛选框输入「梯田」等文字即可过滤，按住 Ctrl 或 Shift 多选，点「确定」后只跑选中的期。加 `-NoGui` 则不弹窗，改为在命令行里输入序号。
- `-Include "*梯田*"`：只要名字匹配的期（通配符，多个用逗号隔开）。
- `-Pick 1,3,5-9`：按扫描列表的序号选（先用 `-Plan` 看序号）。
- 只给 `-RefTerrace`、不给 `-RefVeg`，就等于只跑梯田。适合一台电脑跑梯田、另一台跑植被。

其他参数：

- `-Plan`：只列出扫描结果和每一期会用哪个参考期，不实际运行，建议先跑一次看看。
- `-Skip`：不处理的期次，比如参考期本身。
- `-ExcludeTerrace` / `-ExcludeVeg`：给梯田 / 植被的每一期统一排除某些控制点（改作检查点，误差照报）。
- 想重跑某一期，删掉输出目录里这一期的 `<名>_去定位_控制点` 再运行即可，已去定位的照片会直接复用。
- 每期都会在输出目录里存一份去定位后的照片，空间按原照片大小估算。

## 下载

到 [Releases](https://github.com/aduhappy/uav-photo-tool/releases) 下载最新的 `uav-photo-tool_v*.exe`，双击运行即可。

软件启动时会在后台查一次有没有新版本，有的话会弹窗提示并给出下载链接，不会自动下载或替换。不想要这个提示，可以在菜单「帮助 → 启动时自动检查更新」里关掉。

## 系统要求

- Windows 10 / 11，64 位。
- 「剔除定位」不需要安装任何东西。
- 「空三」「控制点」需要 Metashape **Pro** 版（标准版不支持脚本，用不了）。目前只在 Metashape Pro 1.7.2 上实测过，2.x 和 PhotoScan 1.4 没有测过，欢迎反馈。
- 已测试的相机：DJI M3E（Mavic 3 Enterprise）。其他大疆机型的照片结构相近，应该也能用；遇到问题请提 Issue。

## 命令行

exe 和 Python 源码都可以用命令行调用，方便批量处理：

```text
uav_photo_tool.py strip-gps <输入目录> [输出目录] [--no-recursive] [--strip-attitude]
uav_photo_tool.py strip-gps <照片目录> --in-place --confirm <目录名> [--no-backup] [--no-recursive] [--strip-attitude]
uav_photo_tool.py restore-gps <照片目录> --backup <定位备份.zip>
uav_photo_tool.py align <照片目录> [--metashape 路径] [--accuracy high|medium|low]
                        [--recursive] [--no-auto-strip] [--out 输出位置]
                        [--keypoints N] [--tiepoints N] [--log 日志文件]
uav_photo_tool.py gcp-transfer <新一期照片目录> --ref <参考期.psx> [--out 输出位置] [--accuracy 0.01]
                               [--exclude 1,2] [--max-shift 50] [--no-auto-strip] [--metashape 路径] [--log 日志文件]
uav_photo_tool.py gcp-init <空三.psx> --csv <控制点.csv> [--cc <CC导出.xml/.xmlz>] [--out 输出位置]
                           [--accuracy 0.01] [--exclude 1,2] [--metashape 路径] [--log 日志文件]
uav_photo_tool.py check-update
```

不带参数就启动图形界面。

exe 打包时没有带控制台窗口，所以用 exe 跑命令行时看不到输出，请看返回的退出码、输出目录里的日志，或加 `--log` 参数另存日志。

退出码含义：

| 命令 | 0 | 1 | 2 | 3 | 4 | 10 |
|---|---|---|---|---|---|---|
| `strip-gps` | 全部成功 | 有照片失败 | 拒绝执行（如输出目录非空） | | | |
| `align` | 全部成功 | 有工程失败 | 拒绝执行（如照片带定位、Metashape 路径不对） | 已取消 | | |
| `gcp-transfer`、`gcp-init` | 自动通过 | 失败（含参与平差的点少于 3 个） | 拒绝执行（如 CSV 格式不对、输出目录非空、参考期不存在） | 已取消 | 需人工核对（含有点被自动排除） | |
| `check-update` | 已是最新 | 查询失败 | | | | 有新版本 |

批量脚本里请把 4 当作「没通过」处理，不要当成功。

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
  python scripts/test_gcp.py <控制点输出目录> [--expect-aligned N] [--max-rms 0.02] [--metashape 路径]
  ```

## 许可

[MIT](LICENSE)。Metashape 是 Agisoft 的商业软件，本工具不包含它，只调用你电脑上已安装的版本。
