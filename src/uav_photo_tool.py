# -*- coding: utf-8 -*-
"""无人机照片处理工具（单文件，只用标准库 + tkinter）。

功能页签：
  1. 剔除定位：批量剔除 JPG 里的定位信息（EXIF GPS + DJI XMP 位置字段），另存到新目录。
  2. 空三：调用用户电脑上的 Metashape Pro 对齐照片，导出不带坐标系的 Blocks Exchange XML。
  3. 控制点（D-16、D-17）：①迁移：新一期照片和已有控制点的参考期联合匹配，零刺点自动放点、细化、平差、判定；
     ②建参考期：空三工程 + 控制点 CSV（+ CC 刺点），自动判 X/Y 是否对调，再细化、平差、判定。
     迁移时细化把点挪得太远（D-20）就自动排除该点；也可以手动排除。导出的 XML 写 CC 的 Local:unit=meter（D-19）。
  4. 批量迁移（D-24、D-26、D-27）：扫描上级目录，按规则表配参考期，一期接一期跑「控制点·迁移」。
  5. 影像处理（D-29）：扫描工点、勾选，一个接一个用 Metashape 最高精度空三，导出带 CGCS2000 3 度带（不带带号，
     按工点自动选最近的带、可改）坐标的 Blocks Exchange（CC 直接导入），可选出 DOM/DSM；室外照片不去定位。
     勾了 DOM/DSM 默认自动裁边（D-32）：只认「DOM 有影像 + 不插值的密集点云真有点 + 点云够密」的地方，补缝、取最大连片、
     填小洞、往里收 3 米成范围线，DOM 和 DSM 用同一条线同一个像元网格裁（线外透明 / NoData），范围线另存 KML（WGS84）
     + SHP（同 DOM 坐标系）。「按范围线重新裁剪…」/ survey-clip：用户改了线（可挖洞、加块）后只重新导出，不重建。
     列表可按照片数排序（点列头「照片数」，处理顺序跟着列表走）。「整理交付目录」/ survey-deliver：每个工点一个文件夹，
     只放 <工点名>DOM.tif/.tfw、<工点名>DSM.tif/.tfw，复制并逐个核对 SHA-256，写 交付清单.csv；可勾「处理完自动整理」。
     「整理 CC 成果…」/ survey-cc-deliver（T17）：CC 的 Production_* 按外框和坐标系自动认出是哪个工点（认不准让用户选），
     DOM/DSM 按 D-32 的判据裁边（不重采样，线外透明 / NoData，重新生成金字塔），连同 3SM 改名复制进交付目录。

规则：
  - 字节级原位等长改写，输出大小 = 原文件，SOS 之后的数据逐字节不变，不解码图像。
  - EXIF：IFD0 删除 0x8825 GPS 指针（后面的条目和下一个 IFD 指针一起前移），GPS IFD 及其数据区清零。
  - XMP：drone-dji 的位置字段用空格等长覆盖；姿态（Gimbal*/Flight*）可选剔除，标定参数保留。
  - 原目录只读；输出目录非空时拒绝写入；只处理 .jpg/.jpeg，其他文件跳过并写日志。
  - 空三（D-05~D-08、D-10）：不打包 Metashape，把内嵌脚本和 UTF-8 参数 JSON 写到 %TEMP%，
    子进程跑 `metashape.exe -r 脚本 参数.json`，靠进度文件通信；照片带定位就不许直接空三；
    输出 `<照片目录名>_空三\`；导出的 XML 去掉全部坐标系。

命令行：
  python uav_photo_tool.py strip-gps <输入目录> <输出目录> [--no-recursive] [--strip-attitude]
  python uav_photo_tool.py align <照片目录> [--metashape 路径] [--accuracy high|medium|low]
                                 [--recursive] [--no-auto-strip] [--out 输出位置]
                                 [--keypoints N] [--tiepoints N]
  python uav_photo_tool.py gcp-transfer <照片目录> --ref <参考期.psx> [--out 输出位置] [--accuracy 0.01]
                                        [--exclude 1,2] [--max-shift 50]
  python uav_photo_tool.py gcp-init <空三.psx> --csv <控制点.csv> [--cc <CC导出.xml/.xmlz>] [--out 输出位置]
                                    [--exclude 1,2]
  python uav_photo_tool.py gcp-batch <上级目录> --out <输出> --rule 关键词=参考期.psx[:排除点] ...
  python uav_photo_tool.py survey-batch <上级目录> --out <输出> [--epsg auto|4543] [--dom-dsm] [--resolution 0.03]
                                        [--clip auto|none|范围线.kml|.shp] [--clip-inset 3] [--clip-density 50]
                                        [--include "*弃渣场*"] [--pick 1,3,5-9] [--skip 名字,...] [--plan]
                                        [--sort scan|photos-desc|photos-asc] [--deliver [--deliver-to 交付目录]]
  python uav_photo_tool.py survey-clip <工点输出目录>... [--line <KML或SHP>] [--clip-inset 3] [--clip-density 50]
  python uav_photo_tool.py survey-deliver <输出目录> [--to 交付目录] [--site 工点名,...]
  python uav_photo_tool.py survey-cc-deliver <CC工程或Productions目录>... --out <影像处理输出目录> [--to 交付目录]
                                             [--map Production_2=工点名,...] [--no-clip] [--clip-inset 3]
                                             [--clip-density 50] [--replace] [--plan] [--metashape 路径]
  python uav_photo_tool.py check-update
  python uav_photo_tool.py selftest
不带参数启动图形界面（菜单「帮助」里可以检查更新、自检）。
退出码：0 成功（控制点＝自动通过）/ 1 有失败 / 2 拒绝执行 / 3 已取消 / 4 控制点需人工核对 / 10 有新版本（check-update）。
selftest 的退出码：0 全部通过 / 1 有失败项。
"""

import argparse
import base64
import csv
import datetime
import fnmatch
import hashlib
import math
import stat
import glob
import gzip
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

APP_NAME = "无人机照片工具"
APP_VERSION = "1.6"
GITHUB_REPO = "aduhappy/uav-photo-tool"
HOMEPAGE_URL = "https://github.com/%s" % GITHUB_REPO
OUTPUT_SUFFIX = "_去定位"
JPEG_EXTS = (".jpg", ".jpeg")

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
EXIF_HEADER = b"Exif\x00\x00"
GPS_POINTER_TAG = 0x8825

# D-03：位置字段（两种拼法 GpsLongitude/GpsLongtitude 都处理）
XMP_LOCATION_FIELDS = (
    r"GpsLatitude|GpsLongitude|GpsLongtitude|AbsoluteAltitude|RelativeAltitude"
    r"|GpsStatus|AltitudeType|Rtk\w*|LRFTarget\w*"
)
# D-03：姿态字段，只有勾选「同时剔除姿态」时才剔除
XMP_ATTITUDE_FIELDS = r"Gimbal\w*|Flight\w*"

TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


# ---------------------------------------------------------------------------
# 地理库的数据路径（D-34）
#   GDAL 和 PROJ 的数据随包带，程序自己指过去；不看使用者电脑上装过的
#   GDAL / PROJ / conda / QGIS 留下的环境变量，也不依赖 PATH。
#   打包版（PyInstaller --onedir）里这些目录都在程序文件夹内；
#   源码运行时从已装好的库里找。
# ---------------------------------------------------------------------------

def _bundle_dir():
    """打包后程序文件所在目录（onedir 时是 exe 同级的 _internal）；源码运行时返回空串。"""
    return getattr(sys, "_MEIPASS", "") or ""


def geo_data_dirs():
    """返回 (GDAL_DATA 目录, rasterio 的 PROJ 数据目录, pyproj 的 PROJ 数据目录)。

    找不到的返回空串（调用方跳过）。

    注意：rasterio 自带的 PROJ（GDAL 3.10 → PROJ 9.6）和 pyproj 自带的 PROJ（9.5）
    要各自的 proj.db，两个 proj.db 的格式版本不一样，混用会报
    "DATABASE.LAYOUT.VERSION ... comes from another PROJ installation"。
    所以这里分开找：GDAL/PROJ_DATA 用 rasterio 那套，pyproj 用它自己那套。"""
    base = _bundle_dir()
    if base:
        return (os.path.join(base, "rasterio", "gdal_data"),
                os.path.join(base, "rasterio", "proj_data"),
                os.path.join(base, "pyproj", "proj_dir", "share", "proj"))
    import importlib.util

    def pkg_dir(name):
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            return ""
        if spec is None or not spec.submodule_search_locations:
            return ""
        return list(spec.submodule_search_locations)[0]

    ras = pkg_dir("rasterio")
    pyj = pkg_dir("pyproj")
    # pyproj 的数据目录，按 pyproj.datadir 自己的优先级找（不用 import pyproj，免得拖慢启动）：
    #   1) 包内 proj_dir/share/proj   2) sys.prefix/share/proj   3) sys.prefix/Library/share/proj（conda）
    pyproj_dir = ""
    for cand in (os.path.join(pyj, "proj_dir", "share", "proj") if pyj else "",
                 os.path.join(sys.prefix, "share", "proj"),
                 os.path.join(sys.prefix, "Library", "share", "proj")):
        if cand and os.path.isfile(os.path.join(cand, "proj.db")):
            pyproj_dir = cand
            break
    return (os.path.join(ras, "gdal_data") if ras else "",
            os.path.join(ras, "proj_data") if ras else "",
            pyproj_dir)


def setup_geo_env():
    """把 GDAL/PROJ 的数据指到程序自带的那套。必须在 import rasterio / pyproj 之前调用。

    忽略电脑上已有的 GDAL_DATA / PROJ_DATA / PROJ_LIB（conda、QGIS 等装的），
    免得它们指向别的版本的 proj.db。返回 pyproj 自己的 PROJ 数据目录（找不到是空串）。"""
    gdal, rast_proj, pyproj_dir = geo_data_dirs()
    if gdal and os.path.isdir(gdal):
        os.environ["GDAL_DATA"] = gdal
    if rast_proj and os.path.isdir(rast_proj):
        os.environ["PROJ_DATA"] = rast_proj
    os.environ.pop("PROJ_LIB", None)   # 老名字（PROJ 9.1 以前）会盖掉 PROJ_DATA
    return pyproj_dir if (pyproj_dir and os.path.isdir(pyproj_dir)) else ""


def subprocess_env():
    """给子进程（Metashape）用的环境：去掉我们为 GDAL/PROJ 设的变量，
    免得 Metashape 去读我们包里那套数据。"""
    env = dict(os.environ)
    for k in ("GDAL_DATA", "PROJ_DATA", "PROJ_LIB"):
        env.pop(k, None)
    return env



class JpegError(Exception):
    """JPEG 结构异常，这张照片记为失败，不写半成品。"""


# ---------------------------------------------------------------------------
# JPEG / EXIF / XMP 字节级处理
# ---------------------------------------------------------------------------

def parse_segments(buf):
    """解析 SOS 之前的段，返回 ([(marker, 段起点, 段总长含FFxx), ...], SOS 位置)。"""
    if buf[:3] != b"\xff\xd8\xff":
        raise JpegError("文件开头不是 FF D8 FF，不是 JPEG")
    segs = []
    i = 2
    n = len(buf)
    while True:
        if i + 4 > n:
            raise JpegError("没找到 SOS 就到了文件末尾")
        if buf[i] != 0xFF:
            raise JpegError("位置 %d 处不是段标记" % i)
        marker = buf[i + 1]
        if marker == 0xFF:  # 填充字节
            i += 1
            continue
        if marker == 0xDA:
            return segs, i
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            raise JpegError("SOS 之前出现了不带长度的标记 FF%02X" % marker)
        length = struct.unpack(">H", buf[i + 2:i + 4])[0]
        if length < 2 or i + 2 + length > n:
            raise JpegError("位置 %d 的段长度越界" % i)
        segs.append((marker, i, 2 + length))
        i += 2 + length


def _read_ifd(buf, base, end, off, e):
    """读 TIFF IFD，返回 (条目列表[(tag, type, count, value4, 条目绝对位置)], 下一个IFD指针的绝对位置)。"""
    p = base + off
    if off < 8 or p + 2 > end:
        raise JpegError("IFD 偏移 %d 越界" % off)
    count = struct.unpack(e + "H", buf[p:p + 2])[0]
    if p + 2 + 12 * count + 4 > end:
        raise JpegError("IFD（偏移 %d，%d 项）越界" % (off, count))
    entries = []
    for k in range(count):
        q = p + 2 + 12 * k
        tag, typ, cnt = struct.unpack(e + "HHI", buf[q:q + 8])
        entries.append((tag, typ, cnt, bytes(buf[q + 8:q + 12]), q))
    return entries, p + 2 + 12 * count


def strip_exif(buf, seg_start, seg_len):
    """在 APP1 Exif 段内原位删除 GPS。返回 True 表示原来有 GPS。"""
    base = seg_start + 4 + len(EXIF_HEADER)  # TIFF 头
    end = seg_start + seg_len
    if base + 8 > end:
        raise JpegError("Exif 段太短")
    order = bytes(buf[base:base + 2])
    if order == b"II":
        e = "<"
    elif order == b"MM":
        e = ">"
    else:
        raise JpegError("TIFF 字节序标记异常")
    if struct.unpack(e + "H", buf[base + 2:base + 4])[0] != 42:
        raise JpegError("TIFF 魔数不是 42")
    ifd0_off = struct.unpack(e + "I", buf[base + 4:base + 8])[0]
    entries, next_pos = _read_ifd(buf, base, end, ifd0_off, e)
    idx = [k for k, ent in enumerate(entries) if ent[0] == GPS_POINTER_TAG]
    if not idx:
        return False
    if len(idx) > 1:
        raise JpegError("IFD0 里有多个 GPS 指针")
    k = idx[0]
    gps_off = struct.unpack(e + "I", entries[k][3])[0]

    # 先收集 GPS IFD 及其外置数据的区间，校验后再动手
    gps_entries, gps_next_pos = _read_ifd(buf, base, end, gps_off, e)
    ranges = [(base + gps_off, gps_next_pos + 4)]
    for tag, typ, cnt, val, _ in gps_entries:
        size = TIFF_TYPE_SIZES.get(typ, 1) * cnt
        if size > 4:
            voff = struct.unpack(e + "I", val)[0]
            if voff < 8 or base + voff + size > end:
                raise JpegError("GPS 标签 0x%04X 的数据越界" % tag)
            ranges.append((base + voff, base + voff + size))
    ifd0_range = (base + ifd0_off, next_pos + 4)
    for a, b in ranges:
        if a < ifd0_range[1] and b > ifd0_range[0]:
            raise JpegError("GPS 数据区与 IFD0 重叠")

    for a, b in ranges:
        buf[a:b] = bytes(b - a)

    # IFD0：后面的条目和下一个 IFD 指针前移 12 字节，条目数减 1，末尾 12 字节补 0
    p0 = base + ifd0_off
    count = len(entries)
    entry_pos = entries[k][4]
    tail_end = next_pos + 4
    buf[entry_pos:tail_end - 12] = buf[entry_pos + 12:tail_end]
    buf[tail_end - 12:tail_end] = bytes(12)
    buf[p0:p0 + 2] = struct.pack(e + "H", count - 1)
    return True


def _xmp_field_regex(names):
    # 属性形式：drone-dji:Name="..."；元素形式：<drone-dji:Name ...>...</drone-dji:Name> 或自闭合
    attr = rb"(?<=\s)drone-dji:(?:" + names + rb")\s*=\s*(?:\"[^\"]*\"|'[^']*')"
    elem = (rb"<drone-dji:(" + names + rb")\b[^>]*?/>"
            rb"|<drone-dji:(" + names + rb")\b[^>]*>.*?</drone-dji:(?:" + names + rb")\s*>")
    return re.compile(attr + rb"|" + elem, re.IGNORECASE | re.DOTALL)


_RE_LOCATION = _xmp_field_regex(XMP_LOCATION_FIELDS.encode())
_RE_LOC_ATT = _xmp_field_regex((XMP_LOCATION_FIELDS + "|" + XMP_ATTITUDE_FIELDS).encode())
_RE_GPS_STATUS = re.compile(rb"drone-dji:GpsStatus\s*(?:=\s*[\"']([^\"']*)[\"']|>([^<]*)<)", re.IGNORECASE)


def _xmp_parses(packet):
    try:
        ET.fromstring(bytes(packet))
        return True
    except ET.ParseError:
        return False


def strip_xmp(buf, seg_start, seg_len, strip_attitude):
    """在 APP1 XMP 段内把位置字段用空格等长覆盖。返回 (被剔除字段数, 原 GpsStatus)。"""
    a = seg_start + 4 + len(XMP_HEADER)
    b = seg_start + seg_len
    packet = bytes(buf[a:b])
    m = _RE_GPS_STATUS.search(packet)
    status = (m.group(1) or m.group(2) or b"").decode("utf-8", "replace").strip() if m else ""
    rx = _RE_LOC_ATT if strip_attitude else _RE_LOCATION
    hits = list(rx.finditer(packet))
    if not hits:
        return 0, status
    new = bytearray(packet)
    for h in hits:
        new[h.start():h.end()] = b" " * (h.end() - h.start())
    if _xmp_parses(packet) and not _xmp_parses(new):
        raise JpegError("剔除后 XMP 不再是合法 XML")
    buf[a:b] = new
    return len(hits), status


def strip_jpeg_bytes(data, strip_attitude=False):
    """输入原文件字节，返回 (新字节, 信息 dict)。长度和 SOS 之后的字节保证不变。"""
    buf = bytearray(data)
    segs, sos = parse_segments(buf)
    info = {"exif_gps": False, "xmp_fields": 0, "gps_status": "", "has_exif": False, "has_xmp": False}
    for marker, start, total in segs:
        if marker != 0xE1:
            continue
        payload = buf[start + 4:start + 4 + 32]
        if payload.startswith(EXIF_HEADER):
            if info["has_exif"]:
                continue  # 只认第一个 Exif 段（主图）
            info["has_exif"] = True
            info["exif_gps"] = strip_exif(buf, start, total)
        elif payload.startswith(XMP_HEADER):
            info["has_xmp"] = True
            n, st = strip_xmp(buf, start, total, strip_attitude)
            info["xmp_fields"] += n
            info["gps_status"] = info["gps_status"] or st
    if len(buf) != len(data) or buf[sos:] != data[sos:]:
        raise JpegError("内部校验失败：大小或 SOS 之后的数据变了")
    return bytes(buf), info


# ---------------------------------------------------------------------------
# 批处理
# ---------------------------------------------------------------------------

def default_output_dir(src_dir):
    src = Path(src_dir)
    return src.parent / (src.name + OUTPUT_SUFFIX)


def check_dirs(src_dir, dst_dir):
    """返回错误信息字符串；没问题返回 None。"""
    src = Path(src_dir)
    if not src.is_dir():
        return "输入目录不存在：%s" % src
    dst = Path(dst_dir)
    try:
        s, d = src.resolve(), dst.resolve()
    except OSError as ex:
        return "路径无法解析：%s" % ex
    if s == d:
        return "输出目录不能和输入目录相同"
    if s in d.parents:
        return "输出目录不能放在输入目录里面"
    if d in s.parents:
        return "输入目录不能放在输出目录里面"
    if OUTPUT_SUFFIX not in d.name:
        return "输出目录名必须带「%s」（避免和原片混用），例如 %s" % (OUTPUT_SUFFIX, s.name + OUTPUT_SUFFIX)
    if d.exists():
        if not d.is_dir():
            return "输出路径已存在且不是目录：%s" % d
        if any(d.iterdir()):
            return "输出目录不是空的，拒绝写入（不覆盖）：%s\n请换一个目录或先清空。" % d
    return None


def collect_files(src_dir, recursive):
    src = Path(src_dir)
    if recursive:
        out = []
        for root, dirs, files in os.walk(src):
            dirs.sort()
            for f in sorted(files):
                out.append(Path(root) / f)
        return out
    return sorted(p for p in src.iterdir() if p.is_file())


def process_file(src, dst, strip_attitude=False):
    data = src.read_bytes()
    new, info = strip_jpeg_bytes(data, strip_attitude)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp_part")
    try:
        with open(tmp, "wb") as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        if tmp.stat().st_size != len(data):
            raise JpegError("写出的大小和原文件不一致")
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()
    shutil.copystat(src, dst)
    return info


def _jpeg_app1_segments(data):
    """Return original APP1 Exif/XMP segments as (kind, offset, bytes)."""
    segs, _ = parse_segments(data)
    out = []
    for marker, start, total in segs:
        if marker != 0xE1:
            continue
        payload = data[start + 4:start + 4 + 64]
        if payload.startswith(EXIF_HEADER):
            kind = "exif"
        elif payload.startswith(XMP_HEADER):
            kind = "xmp"
        else:
            continue
        out.append((kind, start, bytes(data[start:start + total])))
    return out


def _clear_inplace_temps(src, log):
    """Remove stale temporary files created by this tool, recursively."""
    removed, residual = 0, []
    for path in src.rglob("*.tmp_part"):
        try:
            path.unlink()
            removed += 1
        except Exception as ex:
            try:
                os.chmod(path, stat.S_IWRITE)
                path.unlink()
                removed += 1
            except Exception as retry_ex:
                residual.append((path, "%s; retry: %s" % (ex, retry_ex)))
    log("启动清理：已清理 %d 个本软件 .tmp_part；清理失败 %d 个" % (removed, len(residual)))
    return residual


def _write_location_backup(src, files, stamp, strip_attitude=False):
    """Create and read-back-verify the in-place restore backup before edits."""
    backup = src / ("定位备份_%s.zip" % stamp)
    tmp = backup.with_name(backup.name + ".tmp_part")
    manifest = {"version": 1, "created": stamp, "files": [], "skipped": []}
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:
            for index, path in enumerate(files):
                try:
                    data = path.read_bytes()
                    entries = _jpeg_app1_segments(data)
                    stripped, _ = strip_jpeg_bytes(data, strip_attitude)
                    rec = {"path": str(path.relative_to(src)), "before_sha256": hashlib.sha256(data).hexdigest(),
                           "after_sha256": hashlib.sha256(stripped).hexdigest(), "segments": []}
                    for seq, (kind, offset, segment) in enumerate(entries):
                        name = "segments/%06d_%s_%03d.bin" % (index, kind, seq)
                        z.writestr(name, segment)
                        rec["segments"].append({"kind": kind, "offset": offset, "size": len(segment), "zip": name})
                    manifest["files"].append(rec)
                except Exception as ex:
                    manifest["skipped"].append({"path": str(path.relative_to(src)), "reason": "%s: %s" % (type(ex).__name__, ex)})
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        with zipfile.ZipFile(tmp, "r") as z:
            check = json.loads(z.read("manifest.json").decode("utf-8"))
            for rec in check["files"]:
                for seg in rec["segments"]:
                    if len(z.read(seg["zip"])) != seg["size"]:
                        raise ValueError("备份读回校验失败：%s" % rec["path"])
        os.replace(tmp, backup)
        return backup, manifest
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                try:
                    os.chmod(tmp, stat.S_IWRITE); tmp.unlink()
                except Exception:
                    pass


def run_strip_in_place(src_dir, recursive=True, strip_attitude=False, no_backup=False,
                       confirm=None, progress=None, log=None, stop_event=None):
    """Safely strip location metadata in place, one atomically replaced JPEG at a time."""
    log = log or (lambda s: None)
    progress = progress or (lambda i, n: None)
    src = Path(src_dir)
    if not src.is_dir():
        raise ValueError("输入目录不存在：%s" % src)
    if confirm != src.name:
        raise ValueError("确认目录名不正确；必须输入：%s" % src.name)
    residual = _clear_inplace_temps(src, log)
    files = collect_files(src, recursive)
    jpegs = [p for p in files if p.suffix.lower() in JPEG_EXTS]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = None
    manifest = None
    if not no_backup:
        backup_path, manifest = _write_location_backup(src, jpegs, stamp, strip_attitude)
        log("定位备份已写入并校验：%s" % backup_path)
    log_path = src / ("原地抹除日志_%s.csv" % stamp)
    stats = {"成功": 0, "跳过": 0, "失败": 0, "残留": len(residual), "日志": str(log_path), "备份": str(backup_path) if backup_path else ""}
    stats["备份阶段跳过"] = len((manifest or {}).get("skipped", []))
    by_rel = {rec["path"]: rec for rec in (manifest or {}).get("files", [])}
    with open(log_path, "w", newline="", encoding="utf-8-sig") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["相对路径", "状态", "原GpsStatus", "备份是否写入", "说明/出错信息"])
        for skipped in (manifest or {}).get("skipped", []):
            w.writerow([skipped["path"], "备份阶段跳过", "", "否", skipped["reason"]])
        for i, path in enumerate(files, 1):
            if stop_event is not None and stop_event.is_set():
                w.writerow(["", "中止", "", "是" if backup_path else "否", "用户中止"])
                break
            rel = str(path.relative_to(src))
            if path.suffix.lower() not in JPEG_EXTS:
                stats["跳过"] += 1
                w.writerow([rel, "跳过", "", "是" if backup_path else "否", "不是 JPG"])
                progress(i, len(files)); continue
            try:
                if rel in {item["path"] for item in (manifest or {}).get("skipped", [])}:
                    raise OSError("备份阶段跳过：照片无法读取，未取得安全还原备份")
                original = path.read_bytes()
                new, info = strip_jpeg_bytes(original, strip_attitude)
                if not (info["exif_gps"] or info["xmp_fields"]):
                    stats["跳过"] += 1
                    w.writerow([rel, "无定位", info["gps_status"], "是" if backup_path else "否", "无定位信息"])
                    progress(i, len(files)); continue
                tmp = path.with_name(path.name + ".tmp_part")
                try:
                    with open(tmp, "wb") as f:
                        f.write(new); f.flush(); os.fsync(f.fileno())
                    if tmp.stat().st_size != len(original) or tmp.read_bytes() != new:
                        raise JpegError("临时文件校验失败")
                    verify, verify_info = strip_jpeg_bytes(tmp.read_bytes(), strip_attitude)
                    if verify != new or verify_info["exif_gps"] or verify_info["xmp_fields"]:
                        raise JpegError("抹除后自检失败")
                    src_stat = path.stat()
                    os.utime(tmp, (src_stat.st_atime, src_stat.st_mtime))
                    os.replace(tmp, path)
                finally:
                    if tmp.exists():
                        try:
                            tmp.unlink()
                        except Exception:
                            try:
                                os.chmod(tmp, stat.S_IWRITE); tmp.unlink()
                            except Exception as cleanup_ex:
                                residual.append((tmp, str(cleanup_ex)))
                stats["成功"] += 1
                w.writerow([rel, "成功", info["gps_status"], "是" if backup_path else "否", "EXIF GPS %s；XMP 剔除 %d 项" % ("已删" if info["exif_gps"] else "无", info["xmp_fields"])])
            except Exception as ex:
                stats["失败"] += 1
                w.writerow([rel, "失败", "", "是" if backup_path else "否", "%s: %s" % (type(ex).__name__, ex)])
                log("失败：%s —— %s" % (rel, ex))
            fcsv.flush(); progress(i, len(files))
    log("完成：成功 %d，无定位/跳过 %d，失败 %d，残留 %d" % (stats["成功"], stats["跳过"], stats["失败"], len(residual)))
    stats["残留"] = len(residual)
    stats["残留清单"] = [str(p) for p, _ in residual]
    if stats["备份阶段跳过"]:
        log("备份阶段跳过 %d 张，已按失败记账" % stats["备份阶段跳过"])
    for path, reason in residual:
        log("残留临时文件：%s —— %s" % (path, reason))
    return stats


def restore_gps(src_dir, backup_path, recursive=True, progress=None, log=None):
    """Restore only files whose current hash matches the backup's stripped hash."""
    import hashlib
    log = log or (lambda s: None); progress = progress or (lambda i, n: None)
    src = Path(src_dir)
    with zipfile.ZipFile(backup_path, "r") as z:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        records = manifest.get("files", [])
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = src / ("还原定位日志_%s.csv" % stamp)
        stats = {"成功": 0, "跳过": 0, "失败": 0, "日志": str(log_path)}
        with open(log_path, "w", newline="", encoding="utf-8-sig") as fcsv:
            w = csv.writer(fcsv); w.writerow(["相对路径", "状态", "说明/出错信息"])
            for i, rec in enumerate(records, 1):
                path = src / rec["path"]
                try:
                    if not path.is_file(): raise ValueError("照片不存在")
                    current = path.read_bytes()
                    current_hash = hashlib.sha256(current).hexdigest()
                    if current_hash == rec.get("before_sha256", ""):
                        raise ValueError("这张没有被抹除过，不需要还原")
                    if current_hash != rec.get("after_sha256", ""):
                        raise ValueError("当前文件已被其他程序修改，跳过")
                    restored = bytearray(current)
                    for seg in rec.get("segments", []):
                        blob = z.read(seg["zip"])
                        off = int(seg["offset"])
                        if len(blob) != int(seg["size"]) or off + len(blob) > len(restored): raise ValueError("备份段越界")
                        restored[off:off + len(blob)] = blob
                    if hashlib.sha256(restored).hexdigest() != rec["before_sha256"]:
                        raise ValueError("还原后哈希校验失败")
                    tmp = path.with_name(path.name + ".tmp_part")
                    try:
                        with open(tmp, "wb") as f: f.write(restored); f.flush(); os.fsync(f.fileno())
                        src_stat = path.stat()
                        os.utime(tmp, (src_stat.st_atime, src_stat.st_mtime)); os.replace(tmp, path)
                    finally:
                        if tmp.exists():
                            try:
                                tmp.unlink()
                            except Exception:
                                try:
                                    os.chmod(tmp, stat.S_IWRITE); tmp.unlink()
                                except Exception:
                                    pass
                    stats["成功"] += 1; w.writerow([rec["path"], "成功", "逐字节还原"])
                except Exception as ex:
                    stats["跳过"] += 1; w.writerow([rec["path"], "跳过", str(ex)])
                progress(i, len(records))
        return stats


def run_strip(src_dir, dst_dir, recursive=True, strip_attitude=False, progress=None, log=None, stop_event=None):
    """批量处理。progress(i, n) 和 log(str) 是回调。返回统计 dict。"""
    log = log or (lambda s: None)
    progress = progress or (lambda i, n: None)
    err = check_dirs(src_dir, dst_dir)
    if err:
        raise ValueError(err)
    src, dst = Path(src_dir), Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    files = collect_files(src, recursive)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = dst / ("去定位日志_%s.csv" % stamp)
    stats = {"成功": 0, "跳过": 0, "失败": 0, "日志": str(log_path)}
    log("输入：%s" % src)
    log("输出：%s" % dst)
    log("共 %d 个文件；包含子目录=%s；同时剔除姿态=%s" % (len(files), recursive, strip_attitude))
    with open(log_path, "w", newline="", encoding="utf-8-sig") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["相对路径", "状态", "原来有无GPS", "原GpsStatus", "说明/出错信息"])
        for i, f in enumerate(files, 1):
            if stop_event is not None and stop_event.is_set():
                log("用户中止，已处理 %d/%d" % (i - 1, len(files)))
                w.writerow(["", "中止", "", "", "用户中止，剩余 %d 个未处理" % (len(files) - i + 1)])
                break
            rel = f.relative_to(src)
            if f.suffix.lower() not in JPEG_EXTS:
                stats["跳过"] += 1
                w.writerow([str(rel), "跳过", "", "", "不是 JPG，未复制"])
                log("跳过（不是 JPG）：%s" % rel)
            else:
                try:
                    info = process_file(f, dst / rel, strip_attitude)
                    had = info["exif_gps"] or info["xmp_fields"] > 0
                    notes = []
                    if not had:
                        notes.append("无定位信息，照常复制")
                    if not info["has_exif"]:
                        notes.append("无 EXIF")
                    if not info["has_xmp"]:
                        notes.append("无 XMP")
                    notes.append("EXIF GPS %s；XMP 剔除 %d 项" % ("已删" if info["exif_gps"] else "无", info["xmp_fields"]))
                    stats["成功"] += 1
                    w.writerow([str(rel), "成功", "有" if had else "无", info["gps_status"], "；".join(notes)])
                except Exception as ex:  # 单张失败不影响其他照片
                    stats["失败"] += 1
                    w.writerow([str(rel), "失败", "", "", "%s: %s" % (type(ex).__name__, ex)])
                    log("失败：%s —— %s" % (rel, ex))
            fcsv.flush()
            progress(i, len(files))
    log("完成：成功 %d，跳过 %d，失败 %d" % (stats["成功"], stats["跳过"], stats["失败"]))
    log("日志：%s" % log_path)
    return stats


# ---------------------------------------------------------------------------
# 空三：调用 Metashape（D-05~D-08、D-10）
# ---------------------------------------------------------------------------

ALIGN_SUFFIX = "_空三"
ACCURACY_DOWNSCALE = {"high": 1, "medium": 2, "low": 4}  # D-06：默认 high
ACCURACY_LABEL = {"high": "高", "medium": "中", "low": "低"}
DEFAULT_KEYPOINTS = 40000
DEFAULT_TIEPOINTS = 4000
METASHAPE_NAMES = ("metashape.exe", "photoscan.exe")
STARTUP_TIMEOUT = 180   # 秒：Metashape 起来后这么久还没开始执行脚本（许可证/更新对话框、标准版）就判失败
STALL_TIMEOUT = 1800    # 秒：进度这么久没有任何变化就判卡死
EXIT_TIMEOUT = 300      # 秒：脚本写了完成标记后 Metashape 这么久还不退出就判失败
CONFIG_PATH = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME / "config.json"

# 内嵌脚本：运行在 Metashape 自带的 Python 里（1.7 是 3.8），只许用 Python 3.8 语法。
# 进度文件一行一个 JSON 事件；最后一行 {"ev": "done"} 是完成标记。
METASHAPE_SCRIPT = r'''# -*- coding: utf-8 -*-
# 由「无人机照片工具」生成，在 Metashape 内嵌的 Python 里运行。只用 Python 3.8 语法，兼容 Metashape 1.7 / 2.x、PhotoScan 1.4。
import json
import os
import sys
import time
import traceback

P = json.load(open(sys.argv[-1], "r", encoding="utf-8"))
_pf = open(P["progress"], "a", encoding="utf-8")


def ev(kind, **kw):
    kw["ev"] = kind
    kw["t"] = round(time.time(), 1)
    _pf.write(json.dumps(kw, ensure_ascii=False) + "\n")
    _pf.flush()


class Prog(object):
    def __init__(self, stage):
        self.stage = stage
        self.last = -1

    def __call__(self, p):
        p = int(p)
        if p != self.last:
            self.last = p
            ev("progress", stage=self.stage, pct=p)


app = None
try:
    try:
        import Metashape
    except ImportError:
        import PhotoScan as Metashape
    app = Metashape.app
    ev("start", version=str(app.version), python=sys.version.split()[0])
    print("Metashape version: %s" % app.version)

    doc = Metashape.Document()
    doc.save(P["psx"])
    chunk = doc.addChunk()
    ev("stage", stage="add")
    chunk.addPhotos(P["photos"])
    cams = list(chunk.cameras)
    if len(cams) != len(P["photos"]):
        raise RuntimeError("addPhotos added %d cameras, expected %d" % (len(cams), len(P["photos"])))

    # D-07 兜底：位置、姿态参考全部关掉
    had_loc = 0
    no_rot_attr = 0
    for c in cams:
        r = c.reference
        if r.location is not None:
            had_loc += 1
        r.enabled = False
        if hasattr(r, "location_enabled"):
            r.location_enabled = False
        if hasattr(r, "rotation_enabled"):
            r.rotation_enabled = False
        else:
            no_rot_attr += 1
    loc_on = sum(1 for c in cams if c.reference.enabled or getattr(c.reference, "location_enabled", False))
    rot_on = sum(1 for c in cams if getattr(c.reference, "rotation_enabled", False))
    ev("refs_off", total=len(cams), had_location=had_loc, location_on=loc_on, rotation_on=rot_on,
       no_rotation_attr=no_rot_attr)
    if loc_on or rot_on:
        raise RuntimeError("reference still enabled: location %d, rotation %d" % (loc_on, rot_on))

    ev("stage", stage="match")
    t0 = time.time()
    kw = dict(generic_preselection=True, reference_preselection=False,
              keypoint_limit=P["keypoint_limit"], tiepoint_limit=P["tiepoint_limit"], progress=Prog("match"))
    try:
        chunk.matchPhotos(downscale=P["downscale"], **kw)
    except TypeError:  # PhotoScan 1.4 用 accuracy 而不是 downscale
        acc = {1: Metashape.HighAccuracy, 2: Metashape.MediumAccuracy, 4: Metashape.LowAccuracy}[P["downscale"]]
        chunk.matchPhotos(accuracy=acc, **kw)
    t_match = time.time() - t0

    ev("stage", stage="align")
    t1 = time.time()
    chunk.alignCameras(progress=Prog("align"))
    t_align = time.time() - t1

    aligned = [c for c in chunk.cameras if c.transform is not None]
    unaligned = [os.path.basename(c.photo.path) for c in chunk.cameras if c.transform is None]
    tp = getattr(chunk, "tie_points", None)  # 2.x
    if tp is None:
        tp = getattr(chunk, "point_cloud", None)  # 1.x（2.x 里 point_cloud 是密集点云，所以先查 tie_points）
    n_tie = len(tp.points) if tp is not None and tp.points is not None else 0
    ev("aligned", aligned=len(aligned), total=len(chunk.cameras), unaligned=unaligned, tiepoints=n_tie,
       secs_match=round(t_match, 1), secs_align=round(t_align, 1))

    ev("stage", stage="save")
    doc.save()

    # 只读重开工程再导出；未对齐的相机从导出里去掉（否则 Metashape 会把它们按单位姿态写进 XML）
    ev("stage", stage="export")
    try:
        doc2 = Metashape.Document()
        doc2.open(P["psx"], read_only=True)
        doc, chunk = doc2, doc2.chunk
    except TypeError:  # 老版本没有 read_only 参数，就地导出（之后不再保存）
        pass
    drop = [c for c in chunk.cameras if c.transform is None]
    if drop:
        chunk.remove(drop)
    fmt = getattr(Metashape, "CamerasFormatBlocksExchange", None)
    if fmt is None:
        fmt = Metashape.CamerasFormat.CamerasFormatBlocksExchange
    chunk.exportCameras(P["xml_raw"], format=fmt)
    ev("exported", path=P["xml_raw"], cameras=len(chunk.cameras), dropped=len(drop))
    ev("done")
except Exception:
    ev("error", msg=traceback.format_exc())
finally:
    _pf.close()
    if app is not None:
        app.quit()
'''


class AlignCancelled(Exception):
    """用户取消。"""


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 检查更新：查 GitHub 上最新的 Release，只提示、不自动下载
# ---------------------------------------------------------------------------

def parse_version(s):
    """'v0.3' / '0.3.1' -> (0, 3, 1)；认不出的部分按 0。"""
    nums = re.findall(r"\d+", str(s))
    return tuple(int(n) for n in nums) or (0,)


def check_update(timeout=8):
    """返回 {"latest": 版本号, "newer": bool, "url": 下载页, "notes": 说明}；网络或接口出错时抛异常。"""
    import urllib.request
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "uav-photo-tool/%s" % APP_VERSION})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        info = json.loads(r.read().decode("utf-8"))
    tag = info.get("tag_name") or ""
    return {"latest": tag.lstrip("vV"), "newer": parse_version(tag) > parse_version(APP_VERSION),
            "url": info.get("html_url") or HOMEPAGE_URL + "/releases", "notes": (info.get("body") or "").strip()}


def find_metashape():
    """先看配置里记的路径，再查常见安装位置。找不到返回空串。"""
    p = load_config().get("metashape", "")
    if p and os.path.isfile(p):
        return p
    bases = [os.environ.get(k) for k in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")]
    for base in [b for b in bases if b]:
        for sub in ("Metashape Pro\\metashape.exe", "Metashape\\metashape.exe",
                    "PhotoScan Pro\\photoscan.exe", "PhotoScan\\photoscan.exe"):
            c = os.path.join(base, "Agisoft", sub)
            if os.path.isfile(c):
                return c
        for pat in ("*\\metashape.exe", "*\\photoscan.exe"):
            hits = sorted(glob.glob(os.path.join(base, "Agisoft", pat)))
            if hits:
                return hits[0]
    return ""


def check_metashape(path):
    """返回错误信息；没问题返回 None。"""
    if not path:
        return "没有指定 Metashape 程序路径（metashape.exe 或 photoscan.exe）"
    if not os.path.isfile(path):
        return "Metashape 程序不存在：%s" % path
    if os.path.basename(path).lower() not in METASHAPE_NAMES:
        return "这不是 Metashape 程序：%s\n请选择 Metashape Pro 安装目录里的 metashape.exe（老版本是 photoscan.exe）" % path
    return None


def photo_has_location(path):
    """照片是否还带定位（EXIF GPS IFD 或 XMP 位置字段），用和「剔除定位」相同的解析。"""
    with open(path, "rb") as f:
        head = f.read(512 * 1024)
    try:
        _, info = strip_jpeg_bytes(head)
    except JpegError:
        _, info = strip_jpeg_bytes(Path(path).read_bytes())  # 元数据段很大时读整张
    return bool(info["exif_gps"] or info["xmp_fields"])


def list_jpegs(d):
    return sorted(p for p in Path(d).iterdir() if p.is_file() and p.suffix.lower() in JPEG_EXTS)


def photo_dirs(root, recursive):
    """要空三的目录：不递归时就是 root；递归时是每个直接含 JPG 的目录（含 root 自己）。"""
    root = Path(root)
    if not recursive:
        return [root] if list_jpegs(root) else []
    out = []
    for r, dirs, files in os.walk(root):
        dirs.sort()
        if any(Path(f).suffix.lower() in JPEG_EXTS for f in files):
            out.append(Path(r))
    return out


def align_output_dir(photo_dir, top_parent, out_base):
    """D-08：<照片目录名>_空三，放在照片目录的同级；指定了输出位置时整体搬到输出位置下（保留相对结构）。"""
    rel = Path(photo_dir).relative_to(top_parent)
    return Path(out_base) / rel.parent / (rel.name + ALIGN_SUFFIX)


def _dir_empty_or_missing(d):
    d = Path(d)
    return not d.exists() or (d.is_dir() and not any(d.iterdir()))


_TOKEN_RE = re.compile(r"<!--.*?-->|<[^>]*>|[^<]+", re.S)


def fix_blocks_exchange(src, dst):
    """D-10：流式删掉 <SpatialReferenceSystems>、所有 <SRSId>、<Pose> 下的 <Metadata>，其余逐字保留。
    返回删除计数 dict。按块读，82 MB 的文件也不会一次进内存。"""
    stats = {"SpatialReferenceSystems": 0, "SRSId": 0, "Pose/Metadata": 0, "Metadata含其他内容": 0}
    stack = []          # 当前元素路径
    drop_depth = None   # 正在丢弃的元素所在的栈深度
    meta_other = False
    pending_ws = ""     # 元素前面的缩进，元素被删时一起删掉

    def tag_name(tok):
        m = re.match(r"</?\s*([^\s/>]+)", tok)
        return m.group(1) if m else ""

    with open(src, "r", encoding="utf-8") as fi, open(dst, "w", encoding="utf-8", newline="") as fo:
        buf = ""
        while True:
            chunk = fi.read(4 * 1024 * 1024)
            buf += chunk
            if chunk:
                cut = buf.rfind(">") + 1  # 只处理到最后一个完整的标签
                if cut <= 0:
                    continue
                work, buf = buf[:cut], buf[cut:]
            else:
                work, buf = buf, ""
            for m in _TOKEN_RE.finditer(work):
                tok = m.group(0)
                if not tok.startswith("<"):
                    if drop_depth is None:
                        if tok.strip():
                            fo.write(pending_ws + tok)
                            pending_ws = ""
                        else:
                            fo.write(pending_ws)
                            pending_ws = tok
                    continue
                if tok.startswith("<?") or tok.startswith("<!"):
                    if drop_depth is None:
                        fo.write(pending_ws + tok)
                        pending_ws = ""
                    continue
                name = tag_name(tok)
                closing = tok.startswith("</")
                selfclose = tok.endswith("/>")
                if not closing:
                    parent = stack[-1] if stack else ""
                    stack.append(name)
                    if drop_depth is None:
                        if name == "SpatialReferenceSystems" or name == "SRSId" or (name == "Metadata" and parent == "Pose"):
                            key = "Pose/Metadata" if name == "Metadata" else name
                            stats[key] += 1
                            drop_depth = len(stack)
                            meta_other = False
                            pending_ws = ""
                        else:
                            fo.write(pending_ws + tok)
                            pending_ws = ""
                    elif name != "SRSId" and len(stack) == drop_depth + 1 and stack[drop_depth - 1] == "Metadata":
                        meta_other = True
                    if selfclose:
                        if drop_depth == len(stack):
                            drop_depth = None
                        stack.pop()
                else:
                    if drop_depth is None:
                        fo.write(pending_ws + tok)
                        pending_ws = ""
                    elif drop_depth == len(stack):
                        if stack[-1] == "Metadata" and meta_other:
                            stats["Metadata含其他内容"] += 1
                        drop_depth = None
                    if stack:
                        stack.pop()
            if not chunk:
                break
        fo.write(pending_ws)
    return stats


def check_blocks_exchange(xml_path, expected_photos):
    """闸门 2：能解析、<Photo> 数 = 对齐数、ImagePath 都存在、没有 SRSId / SpatialReferenceSystems / Pose 下的 Metadata。
    返回 (问题列表, 信息 dict)，问题列表为空表示通过。"""
    problems = []
    info = {"photos": 0, "tiepoints": 0, "missing_images": []}
    base = Path(xml_path).parent
    stack = []
    bad = {"SRSId": 0, "SpatialReferenceSystems": 0, "Pose/Metadata": 0}
    try:
        for event, el in ET.iterparse(str(xml_path), events=("start", "end")):
            if event == "start":
                if el.tag in ("SRSId", "SpatialReferenceSystems"):
                    bad[el.tag] += 1
                elif el.tag == "Metadata" and stack and stack[-1] == "Pose":
                    bad["Pose/Metadata"] += 1
                stack.append(el.tag)
                continue
            stack.pop()
            if el.tag == "ImagePath":
                p = Path(el.text or "")
                if not p.is_absolute():
                    p = base / p
                if not p.is_file():
                    info["missing_images"].append(str(p))
            elif el.tag == "Photo":
                info["photos"] += 1
                el.clear()
            elif el.tag == "TiePoint":
                info["tiepoints"] += 1
                el.clear()
    except ET.ParseError as ex:
        problems.append("XML 解析失败：%s" % ex)
        return problems, info
    if info["photos"] != expected_photos:
        problems.append("<Photo> 有 %d 个，和对齐数 %d 不一致" % (info["photos"], expected_photos))
    if info["missing_images"]:
        problems.append("%d 个 ImagePath 指向的文件不存在（如 %s）" % (len(info["missing_images"]), info["missing_images"][0]))
    for k, v in bad.items():
        if v:
            problems.append("还有 %d 个 <%s>（D-10 要求去掉）" % (v, k))
    return problems, info


def _kill_tree(proc):
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(30)
    except Exception:
        pass


_STAGE_SPAN = {"add": (0, 2, "导入照片"), "match": (2, 70, "匹配"), "align": (70, 92, "对齐"),
               "save": (92, 94, "保存工程"), "export": (94, 98, "导出 XML"), "fix": (98, 100, "修正 XML")}


def align_one(photo_dir, out_dir, metashape, accuracy="high", keypoints=DEFAULT_KEYPOINTS,
              tiepoints=DEFAULT_TIEPOINTS, progress=None, log=None, stop_event=None, note="",
              startup_timeout=STARTUP_TIMEOUT, stall_timeout=STALL_TIMEOUT, exit_timeout=EXIT_TIMEOUT):
    """对一个照片目录跑一次空三。progress(0~1, 文字)、log(str) 是回调。返回结果 dict（"status" 为 成功/失败/已取消）。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    photo_dir, out_dir = Path(photo_dir), Path(out_dir)
    photos = list_jpegs(photo_dir)
    name = photo_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    psx = out_dir / (name + ".psx")
    xml_final = out_dir / (name + "_BlocksExchange.xml")
    xml_raw = out_dir / (name + "_BlocksExchange.未修正.xml.tmp")
    log_path = out_dir / "metashape_日志.txt"
    downscale = ACCURACY_DOWNSCALE[accuracy]
    res = {"photos_dir": str(photo_dir), "out_dir": str(out_dir), "status": "失败", "reason": "",
           "total": len(photos), "aligned": None, "unaligned": [], "tiepoints": None, "version": "",
           "returncode": None, "done": False, "xml": "", "xml_info": None, "fix_stats": None, "refs": None,
           "metashape": metashape, "accuracy": accuracy, "downscale": downscale, "keypoints": keypoints,
           "tiepoints_limit": tiepoints, "note": note, "start": datetime.datetime.now(), "secs": None}
    t_start = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="uav_align_"))
    script = tmp / "metashape_align.py"
    params = tmp / "params.json"
    prog_path = tmp / "progress.jsonl"
    console = tmp / "console.txt"
    script.write_text(METASHAPE_SCRIPT, encoding="utf-8")
    params.write_text(json.dumps({
        "photos": [str(p) for p in photos], "psx": str(psx), "xml_raw": str(xml_raw), "progress": str(prog_path),
        "downscale": downscale, "keypoint_limit": int(keypoints), "tiepoint_limit": int(tiepoints)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    prog_path.write_text("", encoding="utf-8")
    events = []

    log("—— 空三：%s（%d 张）" % (photo_dir, len(photos)))
    log("输出：%s" % out_dir)
    progress(0.0, "启动 Metashape")
    proc = None
    try:
        if not photos:
            raise RuntimeError("目录里没有 JPG")
        with open(console, "wb") as fcon:
            proc = subprocess.Popen([metashape, "-r", str(script), str(params)], stdin=subprocess.DEVNULL,
                                    stdout=fcon, stderr=subprocess.STDOUT, cwd=str(tmp), env=subprocess_env(),
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            offset = 0
            partial = ""
            last_change = time.time()
            done_at = None
            stage = ""
            while True:
                if stop_event is not None and stop_event.is_set():
                    _kill_tree(proc)
                    raise AlignCancelled()
                with open(prog_path, "r", encoding="utf-8") as fp:
                    fp.seek(offset)
                    data = fp.read()
                    offset = fp.tell()
                if data:
                    last_change = time.time()
                    partial += data
                    lines = partial.split("\n")
                    partial = lines.pop()
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            e = json.loads(line)
                        except ValueError:
                            continue
                        events.append(e)
                        kind = e.get("ev")
                        if kind == "start":
                            res["version"] = e.get("version", "")
                            log("Metashape 版本 %s（内嵌 Python %s）" % (res["version"], e.get("python", "")))
                        elif kind == "stage":
                            stage = e["stage"]
                            a, b, label = _STAGE_SPAN.get(stage, (0, 0, stage))
                            progress(a / 100.0, label)
                            log("%s…" % label)
                        elif kind == "progress":
                            a, b, label = _STAGE_SPAN.get(e["stage"], (0, 0, e["stage"]))
                            pct = e.get("pct", 0)
                            progress((a + (b - a) * pct / 100.0) / 100.0, "%s %d%%" % (label, pct))
                        elif kind == "refs_off":
                            res["refs"] = e
                            log("参考已关闭：%d 张相机的位置参考、姿态参考全部关掉（导入时带位置参考的 %d 张）"
                                % (e["total"], e["had_location"]))
                        elif kind == "aligned":
                            res["aligned"], res["unaligned"], res["tiepoints"] = e["aligned"], e["unaligned"], e["tiepoints"]
                            log("对齐 %d/%d，连接点 %d（匹配 %.0f 秒，对齐 %.0f 秒）" % (
                                e["aligned"], e["total"], e["tiepoints"], e["secs_match"], e["secs_align"]))
                            if e["unaligned"]:
                                log("未对齐：%s" % "、".join(e["unaligned"]))
                        elif kind == "exported":
                            log("已导出 Blocks Exchange（%d 张相机，去掉未对齐 %d 张）" % (e["cameras"], e["dropped"]))
                        elif kind == "done":
                            res["done"] = True
                            done_at = time.time()
                        elif kind == "error":
                            log("Metashape 脚本出错：\n%s" % e.get("msg", ""))
                rc = proc.poll()
                if rc is not None:
                    if data == "" and offset == os.path.getsize(prog_path):
                        break
                    continue
                now = time.time()
                if not res["version"] and now - t_start > startup_timeout:
                    _kill_tree(proc)
                    raise RuntimeError("Metashape 启动 %d 秒后还没开始执行脚本，已强制结束。可能原因：弹出了许可证/激活/更新对话框，"
                                       "或者这是标准版（标准版不能运行脚本）。请先手动打开 Metashape 确认能正常进入主界面。" % startup_timeout)
                if done_at is None and now - last_change > stall_timeout:
                    _kill_tree(proc)
                    raise RuntimeError("Metashape 已经 %d 秒没有进度（停在「%s」），判为卡死，已强制结束。" % (stall_timeout, stage))
                if done_at is not None and now - done_at > exit_timeout:
                    _kill_tree(proc)
                    raise RuntimeError("Metashape 完成后 %d 秒还没退出（可能卡在对话框），已强制结束。" % exit_timeout)
                time.sleep(0.5)
        res["returncode"] = proc.returncode
        errs = [e for e in events if e.get("ev") == "error"]
        if errs:
            msg = (errs[-1].get("msg", "").strip().splitlines() or ["未知错误"])[-1]
            raise RuntimeError("Metashape 脚本出错：%s（完整信息见 metashape_日志.txt）" % msg)
        if proc.returncode != 0:
            raise RuntimeError("Metashape 异常退出，退出码 %s（0x%08X）" % (proc.returncode, proc.returncode & 0xFFFFFFFF))
        if not res["done"]:
            raise RuntimeError("Metashape 退出了，但进度文件里没有完成标记（脚本没有跑完）")
        if not res["aligned"]:
            raise RuntimeError("一张照片都没有对齐")
        if not xml_raw.is_file():
            raise RuntimeError("Metashape 没有导出 Blocks Exchange XML")
        progress(0.98, "修正 XML")
        log("按 D-10 去掉 XML 里的坐标系…")
        res["fix_stats"] = fix_blocks_exchange(xml_raw, xml_final)
        xml_raw.unlink()
        res["xml"] = str(xml_final)
        problems, info = check_blocks_exchange(xml_final, res["aligned"])
        res["xml_info"] = info
        if problems:
            raise RuntimeError("XML 没通过检查：" + "；".join(problems))
        log("XML 检查通过：%d 张照片、%d 个连接点，没有坐标系" % (info["photos"], info["tiepoints"]))
        res["status"] = "成功"
        progress(1.0, "完成")
    except (AlignCancelled, KeyboardInterrupt):
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)
        res["status"] = "已取消"
        res["reason"] = "用户取消，Metashape 已被强制结束；本目录里的工程不完整，不要使用"
        log("已取消")
        (out_dir / "已取消.txt").write_text("用户在 %s 取消了这次空三，本目录里的结果不完整，不要使用。\n"
                                          % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except Exception as ex:
        res["status"] = "失败"
        res["reason"] = str(ex)
        log("失败：%s" % ex)
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)
        if proc is not None:
            res["returncode"] = proc.returncode
    finally:
        res["secs"] = time.time() - t_start
        if xml_raw.exists() and res["status"] != "成功":
            try:
                xml_raw.unlink()
            except OSError:
                pass
        _write_metashape_log(log_path, res, events, console)
        _write_align_report(out_dir / "空三报告.txt", res)
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def _write_metashape_log(path, res, events, console):
    try:
        text = console.read_bytes().decode("utf-8", "replace")
    except OSError:
        text = ""
    with open(path, "w", encoding="utf-8") as f:
        f.write("Metashape 版本：%s\n" % (res["version"] or "未知（脚本没有开始执行）"))
        f.write("程序：%s\n退出码：%s\n\n== 进度事件 ==\n" % (res["metashape"], res["returncode"]))
        for e in events:
            if e.get("ev") != "progress":
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        f.write("\n== Metashape 控制台输出 ==\n")
        f.write(text)


def _write_align_report(path, res):
    yes_no = lambda b: "是" if b else "否"
    lines = ["空三报告（%s v%s）" % (APP_NAME, APP_VERSION), "",
             "状态：%s" % res["status"]]
    if res["reason"]:
        lines.append("原因：%s" % res["reason"])
    lines += ["",
              "照片目录：%s" % res["photos_dir"],
              "输出目录：%s" % res["out_dir"],
              "Metashape：%s（版本 %s）" % (res["metashape"], res["version"] or "未知"),
              "开始时间：%s；耗时 %.0f 秒" % (res["start"].strftime("%Y-%m-%d %H:%M:%S"), res["secs"] or 0),
              "参数：精度 %s（downscale=%d）、通用预选 开、参考预选 关、关键点上限 %d、连接点上限 %d" % (
                  ACCURACY_LABEL[res["accuracy"]], res["downscale"], res["keypoints"], res["tiepoints_limit"])]
    if res["note"]:
        lines.append("去定位：%s" % res["note"])
    if res["refs"]:
        r = res["refs"]
        lines.append("参考：脚本里把 %d 张相机的位置参考、姿态参考都关了（导入时带位置参考的 %d 张；关完后仍开着的 位置 %d / 姿态 %d）"
                     % (r["total"], r["had_location"], r["location_on"], r["rotation_on"]))
    lines.append("")
    if res["aligned"] is not None:
        lines.append("对齐：%d/%d" % (res["aligned"], res["total"]))
        lines.append("未对齐照片：%s" % ("无" if not res["unaligned"] else "%d 张" % len(res["unaligned"])))
        lines += ["  " + u for u in res["unaligned"]]
        lines.append("连接点：%d" % res["tiepoints"])
    else:
        lines.append("对齐：没有完成（照片 %d 张）" % res["total"])
    lines.append("Metashape 退出码：%s；完成标记：%s" % (res["returncode"], yes_no(res["done"])))
    if res["xml"]:
        fs, xi = res["fix_stats"], res["xml_info"]
        lines.append("Blocks Exchange：%s" % res["xml"])
        lines.append("  按 D-10 去掉坐标系：删 <SpatialReferenceSystems> %d 个、<SRSId> %d 个、<Pose> 下的 <Metadata> %d 个（其中含其他内容的 %d 个）"
                     % (fs["SpatialReferenceSystems"], fs["SRSId"], fs["Pose/Metadata"], fs["Metadata含其他内容"]))
        if xi:
            lines.append("  照片 %d 张、连接点 %d 个；ImagePath 缺失 %d 个" % (xi["photos"], xi["tiepoints"], len(xi["missing_images"])))
        if res["unaligned"]:
            lines.append("  未对齐的照片没有写进 XML")
    lines += ["", "说明：结果是局部坐标、尺度任意，需控制点或比例尺定尺度。XML 不带任何坐标系，导入 ContextCapture 后再导入坐标做空三。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_align(photo_dir, metashape, out_base=None, recursive=False, auto_strip=True, accuracy="high",
              keypoints=DEFAULT_KEYPOINTS, tiepoints=DEFAULT_TIEPOINTS, progress=None, log=None, stop_event=None,
              **timeouts):
    """空三总入口。不满足条件时抛 ValueError（不动任何文件）；返回每个工程的结果列表。
    progress(0~1, 文字)：整体进度。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    err = check_metashape(metashape)
    if err:
        raise ValueError(err)
    if accuracy not in ACCURACY_DOWNSCALE:
        raise ValueError("精度只能是 high / medium / low")
    if int(keypoints) <= 0 or int(tiepoints) < 0:
        raise ValueError("关键点上限必须大于 0，连接点上限不能为负")
    src = Path(photo_dir)
    if not src.is_dir():
        raise ValueError("照片目录不存在：%s" % src)
    src = Path(os.path.abspath(src))  # 不用 resolve()：映射的网络盘会被换成 UNC 路径
    if out_base:
        out_base = Path(os.path.abspath(out_base))
    dirs = photo_dirs(src, recursive)
    if not dirs:
        raise ValueError("目录里没有 JPG%s：%s" % ("（含子目录）" if recursive else "（没勾「包含子目录」）", src))
    photos = [p for d in dirs for p in list_jpegs(d)]
    log("照片目录：%s；%d 个工程，%d 张照片" % (src, len(dirs), len(photos)))

    # D-07：逐张检查定位
    located = []
    for i, p in enumerate(photos, 1):
        if stop_event is not None and stop_event.is_set():
            raise AlignCancelled()
        try:
            if photo_has_location(p):
                located.append(p)
        except Exception as ex:
            raise ValueError("读不了照片 %s：%s" % (p, ex))
        if i % 20 == 0 or i == len(photos):
            progress(0.0, "检查定位 %d/%d" % (i, len(photos)))
    note = "照片本来就不带定位，直接空三"
    if located:
        log("有 %d/%d 张照片带定位（如 %s）" % (len(located), len(photos), located[0].name))
        if not auto_strip:
            raise ValueError("有 %d 张照片还带定位（如 %s），不许直接空三（D-07）。\n请勾选「照片带定位时先自动去定位」，"
                             "或先用「剔除定位」页签处理。" % (len(located), located[0]))
        strip_dst = (out_base or src.parent) / (src.name + OUTPUT_SUFFIX)
        first_out = align_output_dir(strip_dst, strip_dst.parent, strip_dst.parent)
        if not _dir_empty_or_missing(first_out):
            raise ValueError("空三输出目录不是空的，拒绝写入：%s" % first_out)
        err = check_dirs(src, strip_dst)
        if err:
            raise ValueError("自动去定位做不了：%s" % err)
        log("先自动去定位 → %s" % strip_dst)
        st = run_strip(src, strip_dst, recursive=recursive, log=log, stop_event=stop_event,
                       progress=lambda i, n: progress(0.0, "去定位 %d/%d" % (i, n)))
        if stop_event is not None and stop_event.is_set():
            raise AlignCancelled()
        if st["失败"]:
            raise ValueError("自动去定位有 %d 张失败，停止空三，详见 %s" % (st["失败"], st["日志"]))
        work_root = strip_dst
        dirs = photo_dirs(work_root, recursive)
        still = [p for d in dirs for p in list_jpegs(d) if photo_has_location(p)]
        if still:
            raise ValueError("去定位后仍有 %d 张带定位（如 %s），停止" % (len(still), still[0]))
        note = "原照片带定位，已先自动去定位：%s → %s" % (src, strip_dst)
        top_parent, base = work_root.parent, work_root.parent
    else:
        top_parent, base = src.parent, (out_base or src.parent)

    jobs = [(d, align_output_dir(d, top_parent, base)) for d in dirs]
    busy = [o for _, o in jobs if not _dir_empty_or_missing(o)]
    if busy:
        raise ValueError("空三输出目录不是空的，拒绝写入（不覆盖）：%s" % busy[0])

    results = []
    for k, (d, o) in enumerate(jobs):
        if stop_event is not None and stop_event.is_set():
            break
        log("")
        log("[%d/%d] %s" % (k + 1, len(jobs), d))
        r = align_one(d, o, metashape, accuracy, keypoints, tiepoints, log=log, stop_event=stop_event, note=note,
                      progress=lambda f, s, k=k: progress((k + f) / len(jobs), "[%d/%d] %s" % (k + 1, len(jobs), s)),
                      **timeouts)
        results.append(r)
        if r["status"] == "已取消":
            break
    log("")
    for r in results:
        a = "%s/%s" % (r["aligned"], r["total"]) if r["aligned"] is not None else "-"
        log("%s  对齐 %s  %s" % (r["status"], a, r["out_dir"]))
    return results


# ---------------------------------------------------------------------------
# 控制点：迁移 / 建参考期（D-16、D-17；T3 卡 🔒 节的定义和阈值写在下面的常量里，不要改）
# ---------------------------------------------------------------------------

GCP_SUFFIX = "_控制点"
DEFAULT_GCP_ACCURACY = 0.01  # 米；控制点精度，也是判定阈值的基数
GCP_PASS_FACTOR = 2.0        # D-17：三维误差 ≤ 2×控制点精度
GCP_MAX_PX_RMS = 3.0         # D-17：像素 RMS ≤ 3
GCP_MIN_PHOTOS = 3           # D-17：投影 ≥ 3 张
GCP_REJECT_MIN_PX = 3.0      # 自动剔除：像素残差 > max(3 px, 3×该点中位数)
GCP_REJECT_FACTOR = 3.0
GCP_REJECT_ROUNDS = 2        # 最多 2 轮
GCP_EDGE_MARGIN = 20         # 蓝旗离图像边缘 ≥ 20 px
GCP_SWAP_RATIO = 10.0        # 「原样」「X/Y 对调」两种 RMS 相差 > 10 倍才判定对调
GCP_MAX_SHIFT_PX = 50.0      # D-20：细化挪动 > 50 px 的投影删掉；挪动中位数 > 50 px 的点自动排除（迁移模式，可改）
GCP_MIN_POINTS = 3           # T4 🔒：参与平差的点少于 3 个判失败
# D-19：「控制点」输出的 Blocks Exchange 写 CC 自己的局部米制坐标系，<Block> 和每个 <ControlPoint> 都挂 SRSId
BX_LOCAL_SRS_ID = "1"
BX_LOCAL_SRS_NAME = "Local coordinate system (meters)"
BX_LOCAL_SRS_DEF = "Local:unit=meter"
GCP_STATUS_LABEL = {"used": "参与平差", "auto": "自动排除（细化挪动过大）", "user": "用户排除"}
# Blocks Exchange（CC 导出）的像素坐标 + 0.5 = Metashape 的像素坐标：Metashape 导出 Blocks Exchange 时
# 主点写成 w/2 + cx - 0.5（本机 1.7.2 实测 cx=36.78908 → 2676.28908），两边约定差半个像素。
CC_PIXEL_OFFSET = 0.5
GCP_MODE_LABEL = {"transfer": "迁移（零刺点）", "init": "建参考期"}

# 内嵌脚本：运行在 Metashape 自带的 Python 里（1.7 是 3.8），只许用 Python 3.8 语法。
# 进度文件一行一个 JSON 事件；最后一行 {"ev": "done"} 是完成标记；{"ev": "error", "user": true} 是给用户看的错误。
GCP_SCRIPT = r'''# -*- coding: utf-8 -*-
# 由「无人机照片工具」生成（控制点页签），在 Metashape 内嵌的 Python 里运行。只用 Python 3.8 语法，兼容 Metashape 1.7 / 2.x。
# transfer：参考期 + 新一期联合匹配 -> 只对齐新照片 -> 放蓝旗 -> 细化（参考期还在，以它的绿旗为模板）-> 去掉参考期
#           -> 投影改固定 -> updateTransform -> optimizeCameras -> 自动剔除。
# init：空三工程 + CSV（+ CC 刺点）-> 原样 / X/Y 对调各 updateTransform 一次取 RMS 小的 -> 放蓝旗 -> 细化
#           -> 投影改固定 -> updateTransform -> optimizeCameras -> 自动剔除。
import json
import math
import os
import sys
import time
import traceback

P = json.load(open(sys.argv[-1], "r", encoding="utf-8"))
_pf = open(P["progress"], "a", encoding="utf-8")
LOCAL_WKT = 'LOCAL_CS["Local Coordinates (m)",LOCAL_DATUM["Local Datum",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]]]'


def ev(kind, **kw):
    kw["ev"] = kind
    kw["t"] = round(time.time(), 1)
    _pf.write(json.dumps(kw, ensure_ascii=False) + "\n")
    _pf.flush()


class Prog(object):
    def __init__(self, stage):
        self.stage = stage
        self.last = -1

    def __call__(self, p):
        p = int(p)
        if p != self.last:
            self.last = p
            ev("progress", stage=self.stage, pct=p)


class UserError(Exception):
    """给用户看的错误：说清楚是什么问题、怎么办。"""


def median(v):
    v = sorted(v)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def to_local(chunk, v):
    return chunk.crs.project(chunk.transform.matrix.mulp(v))


def refs_off(cams):
    # D-07：相机的位置、姿态参考全部关掉，只靠影像和控制点
    for c in cams:
        r = c.reference
        r.enabled = False
        if hasattr(r, "location_enabled"):
            r.location_enabled = False
        if hasattr(r, "rotation_enabled"):
            r.rotation_enabled = False


def n_tiepoints(chunk):
    tp = getattr(chunk, "tie_points", None)  # 2.x
    if tp is None:
        tp = getattr(chunk, "point_cloud", None)  # 1.x
    return len(tp.points) if tp is not None and tp.points is not None else 0


def set_accuracy(chunk, markers):
    acc = Metashape.Vector([P["accuracy"]] * 3)
    chunk.marker_location_accuracy = acc
    for m in markers:
        m.reference.accuracy = acc
        m.reference.enabled = True


def residuals(m):
    """[(相机, 像素残差或 None)]，只算已对齐相机上的投影。"""
    out = []
    pos = m.position
    for c, pr in m.projections.items():
        if c.transform is None:
            continue
        q = c.project(pos) if pos is not None else None
        out.append((c, (q - pr.coord).norm() if q is not None else None))
    return out


def marker_rows(chunk, markers, stats=None):
    rows = []
    for m in markers:
        res = residuals(m)
        px = [e for _, e in res if e is not None]
        row = {"name": m.label, "n": len(res), "d": None, "d3": None,
               "px_rms": math.sqrt(sum(e * e for e in px) / len(px)) if px else None,
               "px_max": max(px) if px else None}
        if m.position is not None and m.reference.location is not None and chunk.transform.matrix is not None:
            d = to_local(chunk, m.position) - m.reference.location
            row["d"] = [d.x, d.y, d.z]
            row["d3"] = d.norm()
        if stats is not None:
            row.update(stats.get(m.label, {}))
        rows.append(row)
    return rows


def rms3d(rows):
    v = [r["d3"] for r in rows if r["d3"] is not None]
    return math.sqrt(sum(x * x for x in v) / len(v)) if v else None


def place_blue(cams, markers, stats):
    """放蓝旗（未固定投影）：投影在相机前方、离图像边缘 >= edge_margin 才放；已有投影的照片不动。
    返回 {点名: {相机 key: (x, y)}}，细化后用来算挪动量。"""
    margin = P["edge_margin"]
    pred = {}
    for m in markers:
        pred[m.label] = {}
        if m.position is None:
            continue
        for c in cams:
            if m.projections[c] is not None:
                continue
            if c.transform.inv().mulp(m.position).z <= 0:
                continue
            q = c.project(m.position)
            if q is None:
                continue
            w, h = c.sensor.width, c.sensor.height
            if not (margin <= q.x <= w - margin and margin <= q.y <= h - margin):
                continue
            m.projections[c] = Metashape.Marker.Projection(q, False)
            pred[m.label][c.key] = (q.x, q.y)
        stats[m.label]["placed"] = len(pred[m.label])
    return pred


def refine(chunk, markers, pred, stats):
    use = [m for m in markers if pred.get(m.label)]
    if use:
        chunk.refineMarkers(markers=use, progress=Prog("refine"))
    by_key = dict((c.key, c) for c in chunk.cameras)
    for m in markers:
        shifts = []
        removed = 0
        for k, xy in pred.get(m.label, {}).items():
            pr = m.projections[by_key[k]]
            if pr is None:
                removed += 1
            else:
                shifts.append(math.hypot(pr.coord.x - xy[0], pr.coord.y - xy[1]))
        s = stats[m.label]
        s["refine_removed"] = removed
        s["refine_kept"] = len(shifts)
        s["refine_median_px"] = median(shifts)
        s["refine_max_px"] = max(shifts) if shifts else None


def shift_guard(chunk, markers, pred, stats):
    """D-20（只在迁移模式）：细化后挪动 > max_shift 的投影删掉；挪动中位数（删之前、细化留下的全部投影）> max_shift
    的点返回去，由调用方自动排除。"""
    lim = P["max_shift"]
    by_key = dict((c.key, c) for c in chunk.cameras)
    auto = []
    for m in markers:
        n = 0
        for k, xy in pred.get(m.label, {}).items():
            c = by_key[k]
            pr = m.projections[c]
            if pr is not None and math.hypot(pr.coord.x - xy[0], pr.coord.y - xy[1]) > lim:
                m.projections[c] = None
                n += 1
        s = stats[m.label]
        s["shift_removed"] = n
        if s.get("refine_median_px") is not None and s["refine_median_px"] > lim:
            auto.append(m.label)
    return auto


def set_aside(markers, excl, stats):
    """被排除的点：参考关掉（导出成检查点），投影先拿下来不参与平差，平差完再 restore() 放回去算三维误差。"""
    kept = {}
    for m in markers:
        st = excl.get(m.label)
        stats[m.label]["status"] = st or "used"
        if not st:
            continue
        m.reference.enabled = False
        kept[m.label] = [(c, pr.coord) for c, pr in m.projections.items()]
        for c, _ in kept[m.label]:
            m.projections[c] = None
    return kept


def restore(markers, kept):
    for m in markers:
        for c, xy in kept.get(m.label, []):
            m.projections[c] = Metashape.Marker.Projection(xy, True)


def need_points(used, excl):
    if len(used) < P["min_points"]:
        raise UserError("参与平差的控制点只剩 %d 个（%s），少于 %d 个，判失败。被排除的：%s。"
                        "请在 Metashape 里打开输出的 .psx 把认错的点拖准，或换一期做参考期"
                        % (len(used), "、".join(m.label for m in used) or "无", P["min_points"],
                           "、".join("%s 号（%s）" % (k, "细化挪动过大" if v == "auto" else "用户排除")
                                    for k, v in excl.items())))


def check_exclude(markers):
    names = set(m.label for m in markers)
    bad = [x for x in P["exclude"] if x not in names]
    if bad:
        raise UserError("要排除的控制点 %s 不存在；现有的点：%s" % ("、".join(bad), "、".join(m.label for m in markers)))
    return dict((x, "user") for x in P["exclude"])


def pin_all(markers):
    for m in markers:
        for c, pr in list(m.projections.items()):
            if not pr.pinned:
                m.projections[c] = Metashape.Marker.Projection(pr.coord, True)


def optimize(chunk):
    chunk.updateTransform()
    kw = dict(fit_f=True, fit_cx=True, fit_cy=True, fit_b1=False, fit_b2=False, fit_k1=True, fit_k2=True,
              fit_k3=True, fit_k4=False, fit_p1=True, fit_p2=True)
    try:
        chunk.optimizeCameras(fit_corrections=False, adaptive_fitting=False, tiepoint_covariance=False,
                              progress=Prog("adjust"), **kw)
    except TypeError:
        chunk.optimizeCameras(**kw)


def reject(chunk, markers, stats):
    """每个控制点里像素残差 > max(3 px, 3×该点中位数) 的投影删掉后重新优化，最多 2 轮。"""
    for rnd in range(P["reject_rounds"]):
        removed = 0
        detail = {}
        for m in markers:
            res = [(c, e) for c, e in residuals(m) if e is not None]
            if not res:
                continue
            thr = max(P["reject_min_px"], P["reject_factor"] * median([e for _, e in res]))
            for c, e in res:
                if e > thr:
                    m.projections[c] = None
                    stats[m.label]["rejected"] += 1
                    detail[m.label] = detail.get(m.label, 0) + 1
                    removed += 1
        ev("reject", round=rnd + 1, removed=removed, detail=detail)
        if not removed:
            break
        optimize(chunk)


def finish(doc):
    ev("stage", stage="save")
    doc.save()
    # 只读重开工程再导出；未对齐的相机从导出里去掉（D-12）
    ev("stage", stage="export")
    try:
        doc2 = Metashape.Document()
        doc2.open(P["psx"], read_only=True)
        chunk = doc2.chunk
    except TypeError:
        chunk = doc.chunk
    drop = [c for c in chunk.cameras if c.transform is None]
    if drop:
        chunk.remove(drop)
    fmt = getattr(Metashape, "CamerasFormatBlocksExchange", None)
    if fmt is None:
        fmt = Metashape.CamerasFormat.CamerasFormatBlocksExchange
    try:
        chunk.exportCameras(P["xml_raw"], format=fmt, save_markers=True)
    except TypeError:
        chunk.exportCameras(P["xml_raw"], format=fmt)
    ev("exported", path=P["xml_raw"], cameras=len(chunk.cameras), dropped=len(drop))


def open_copy(src, what):
    """只读打开原工程，马上另存为输出工程，之后只改副本。
    chunk 必须在另存之后再取：1.7.2 里另存前拿到的 chunk 对象另存后就脱离文档了，改了也存不进去。"""
    doc = Metashape.Document()
    try:
        doc.open(src, read_only=True)
    except TypeError:
        raise UserError("这个版本的 Metashape 不能只读打开工程；为了不改动%s，停止" % what)
    except Exception as ex:
        raise UserError("打不开%s：%s（%s）" % (what, src, ex))
    doc.save(P["psx"])
    ch = doc.chunk
    if ch is None:
        raise UserError("%s里没有 chunk：%s" % (what, src))
    if len(doc.chunks) > 1:
        ev("warn", msg="%s有 %d 个 chunk，只用当前激活的「%s」" % (what, len(doc.chunks), ch.label))
    return doc, ch


def run_transfer():
    ev("stage", stage="open")
    doc, ch = open_copy(P["ref"], "参考期工程")
    if ch.crs is None or "LOCAL_CS" not in ch.crs.wkt:
        raise UserError("参考期工程的坐标系不是 Local（米），请用「建参考期」做出来的工程。现在是：%s"
                        % (ch.crs.name if ch.crs is not None else "无"))
    if ch.transform.matrix is None:
        raise UserError("参考期工程还没有用控制点定过坐标（chunk 没有变换），请先用「建参考期」处理")
    old = list(ch.cameras)
    old_aligned = [c for c in old if c.transform is not None]
    markers = [m for m in ch.markers if m.reference.location is not None]
    if len(markers) < 3:
        raise UserError("参考期工程里带坐标的控制点只有 %d 个，至少要 3 个" % len(markers))
    excl = check_exclude(markers)
    if len(old_aligned) < 2:
        raise UserError("参考期工程里已对齐的照片只有 %d 张" % len(old_aligned))
    missing = [c.photo.path for c in old_aligned if not os.path.isfile(c.photo.path)]
    if missing:
        raise UserError("参考期工程的照片有 %d 张找不到（如 %s）。联合匹配要用参考期照片，请让它们留在工程记录的位置"
                        % (len(missing), missing[0]))
    old_labels = set(c.label for c in old)
    same = [p for p in P["photos"] if os.path.splitext(os.path.basename(p))[0] in old_labels]
    if same:
        raise UserError("照片目录里有 %d 张和参考期照片同名（如 %s），是不是选成了参考期自己的照片？"
                        % (len(same), os.path.basename(same[0])))
    nopos = [m.label for m in markers if m.position is None]
    if nopos:
        ev("warn", msg="参考期里这些控制点没有刺点位置，放不了蓝旗：%s" % "、".join(nopos))
    ev("ref", cameras=len(old), aligned=len(old_aligned), markers=[m.label for m in markers])
    before = dict((c.key, to_local(ch, c.center)) for c in old_aligned)

    ev("stage", stage="add")
    old_keys = set(c.key for c in old)
    ch.addPhotos(P["photos"])
    new = [c for c in ch.cameras if c.key not in old_keys]
    if len(new) != len(P["photos"]):
        raise RuntimeError("addPhotos added %d cameras, expected %d" % (len(new), len(P["photos"])))
    refs_off(ch.cameras)

    # 联合匹配：参考期工程一般没存特征点，必须 reset_matches=True 重新匹配全部照片
    ev("stage", stage="match")
    t0 = time.time()
    ch.matchPhotos(downscale=P["downscale"], generic_preselection=True, reference_preselection=False,
                   keypoint_limit=P["keypoint_limit"], tiepoint_limit=P["tiepoint_limit"], reset_matches=True,
                   progress=Prog("match"))
    t_match = time.time() - t0
    ev("stage", stage="align")
    t1 = time.time()
    ch.alignCameras(cameras=new, reset_alignment=False, progress=Prog("align"))
    t_align = time.time() - t1
    aligned = [c for c in new if c.transform is not None]
    moved = [(to_local(ch, c.center) - before[c.key]).norm() for c in old_aligned if c.transform is not None]
    lost = [c.label for c in old_aligned if c.transform is None]
    ev("aligned", aligned=len(aligned), total=len(new), unaligned=[os.path.basename(c.photo.path) for c in new
                                                                    if c.transform is None],
       ref_moved_max=max(moved) if moved else None, ref_lost=lost, tiepoints=n_tiepoints(ch),
       secs_match=round(t_match, 1), secs_align=round(t_align, 1))
    if not aligned:
        raise UserError("新一期照片一张都没有对齐到参考期上：场景变化太大，或照片不是同一场地")

    stats = dict((m.label, {"placed": 0, "rejected": 0}) for m in markers)
    ev("stage", stage="place")
    pred = place_blue(aligned, markers, stats)
    ev("placed", counts=dict((k, len(v)) for k, v in pred.items()))
    # 细化必须在去掉参考期相机之前做：refineMarkers 以参考期的绿旗为模板
    ev("stage", stage="refine")
    refine(ch, markers, pred, stats)
    for x in shift_guard(ch, markers, pred, stats):  # D-20
        excl.setdefault(x, "auto")
    for m in markers:
        stats[m.label]["status"] = excl.get(m.label, "used")
    ev("refined", stats=stats, excluded=excl, max_shift=P["max_shift"])

    ev("stage", stage="adjust")
    ch.remove(old)
    keys = set(c.sensor.key for c in ch.cameras)
    orphan = [s for s in ch.sensors if s.key not in keys]
    if orphan:
        try:
            ch.remove(orphan)
        except Exception:
            pass
    pin_all(markers)
    set_accuracy(ch, markers)
    refs_off(ch.cameras)
    used = [m for m in markers if m.label not in excl]
    if len(used) < P["min_points"]:
        for m in markers:
            if m.label in excl:
                m.reference.enabled = False
        doc.save()  # 留下工程给用户在 Metashape 里改
        need_points(used, excl)
    kept = set_aside(markers, excl, stats)
    optimize(ch)
    ev("stage", stage="reject")
    reject(ch, used, stats)
    restore(markers, kept)
    rows = marker_rows(ch, markers, stats)
    # RMS3D 只算参与平差的点；被排除的点（检查点）逐点照报
    ev("rows", rows=rows, rms3d=rms3d([r for r in rows if r.get("status", "used") == "used"]), cameras=len(ch.cameras),
       tiepoints=n_tiepoints(ch))
    finish(doc)


def run_init():
    ev("stage", stage="open")
    doc, ch = open_copy(P["psx_in"], "空三工程")
    cams = list(ch.cameras)
    aligned = [c for c in cams if c.transform is not None]
    ev("aligned", aligned=len(aligned), total=len(cams),
       unaligned=[os.path.basename(c.photo.path) for c in cams if c.transform is None], tiepoints=n_tiepoints(ch))
    if len(aligned) < 2:
        raise UserError("空三工程里已对齐的照片只有 %d 张，请先在「空三」页签跑通" % len(aligned))
    ch.crs = Metashape.CoordinateSystem(LOCAL_WKT)
    refs_off(cams)
    csv = P["csv"]

    if P["cc"] is not None:
        if len(ch.markers):
            ev("warn", msg="空三工程里原有 %d 个标记点，按 CC 刺点重建（原工程不动）" % len(ch.markers))
            ch.remove(list(ch.markers))
        by_label = dict((c.label, c) for c in cams)
        off = P["cc_offset"]
        n_ok = 0
        miss = []
        for name, meas in P["cc"].items():
            m = ch.addMarker()
            m.label = name
            for lab, x, y in meas:
                c = by_label.get(lab)
                if c is None or c.transform is None:
                    miss.append(lab)
                    continue
                m.projections[c] = Metashape.Marker.Projection(Metashape.Vector([x + off, y + off]), True)
                n_ok += 1
        ev("cc_loaded", markers=len(P["cc"]), measurements=n_ok, missing=miss, offset=off)

    markers = [m for m in ch.markers if m.label in csv]
    labels = set(m.label for m in markers)
    excl = check_exclude(markers)
    stats = {}
    for m in markers:
        pins = sum(1 for c, pr in m.projections.items() if pr.pinned and c.transform is not None)
        stats[m.label] = {"pins": pins, "placed": 0, "rejected": 0, "status": excl.get(m.label, "used")}
    ok = [m.label for m in markers if stats[m.label]["pins"] >= 2 and m.label not in excl]
    ev("markers", used=[m.label for m in markers], ok=ok, csv_unused=[k for k in csv if k not in labels],
       extra=[m.label for m in ch.markers if m.label not in csv], excluded=excl)
    if len(ok) < 3:
        raise UserError("能用的控制点只有 %d 个（%s）：至少要 3 个点、每个点在已对齐照片上刺 >= 2 张%s。"
                        "没有 CC 刺点时，请先在 Metashape 里刺好、保存，再用本工具" % (
                            len(ok), "、".join(ok) or "无", "（不算用户排除的 %s）" % "、".join(excl) if excl else ""))
    set_accuracy(ch, markers)
    for m in markers:
        if m.label in excl:
            m.reference.enabled = False  # 用户排除的点也不参与 X/Y 判断
    used = [m for m in markers if m.label not in excl]

    # X/Y 判断：原样、对调各做一次 updateTransform，取 RMS 小的
    ev("stage", stage="swap")
    var = {}
    for key in ("asis", "swap"):
        for m in markers:
            x, y, z = csv[m.label]
            m.reference.location = Metashape.Vector([y, x, z] if key == "swap" else [x, y, z])
        ch.updateTransform()
        rows = marker_rows(ch, used)
        var[key] = {"rms3d": rms3d(rows), "rows": rows}
    a, b = var["asis"]["rms3d"], var["swap"]["rms3d"]
    use_swap = b < a
    ratio = max(a, b) / max(min(a, b), 1e-12)
    for m in markers:
        x, y, z = csv[m.label]
        m.reference.location = Metashape.Vector([y, x, z] if use_swap else [x, y, z])
    ch.updateTransform()
    dx, dy = [], []
    for m in used:
        if m.position is None:
            continue
        for c, pr in m.projections.items():
            q = c.project(m.position) if c.transform is not None else None
            if pr.pinned and q is not None:
                dx.append(q.x - pr.coord.x)
                dy.append(q.y - pr.coord.y)
    ev("swap", asis=var["asis"], swap=var["swap"], use_swap=use_swap, ratio=ratio,
       confident=ratio > P["swap_ratio"], pin_mean_dx=sum(dx) / len(dx) if dx else None,
       pin_mean_dy=sum(dy) / len(dy) if dy else None, pin_n=len(dx))

    ev("stage", stage="place")
    pred = place_blue(aligned, markers, stats)
    ev("placed", counts=dict((k, len(v)) for k, v in pred.items()))
    ev("stage", stage="refine")
    refine(ch, markers, pred, stats)
    ev("refined", stats=stats, excluded=excl, max_shift=None)
    ev("stage", stage="adjust")
    pin_all(markers)
    kept = set_aside(markers, excl, stats)
    optimize(ch)
    ev("stage", stage="reject")
    reject(ch, used, stats)
    restore(markers, kept)
    rows = marker_rows(ch, markers, stats)
    # RMS3D 只算参与平差的点；被排除的点（检查点）逐点照报
    ev("rows", rows=rows, rms3d=rms3d([r for r in rows if r.get("status", "used") == "used"]), cameras=len(ch.cameras),
       tiepoints=n_tiepoints(ch))
    finish(doc)


app = None
try:
    try:
        import Metashape
    except ImportError:
        import PhotoScan as Metashape
    app = Metashape.app
    ev("start", version=str(app.version), python=sys.version.split()[0])
    print("Metashape version: %s" % app.version)
    if not hasattr(Metashape.Chunk, "refineMarkers"):
        raise UserError("这个版本的 Metashape 没有「细化标记点」（refineMarkers），请用 1.6 及以上的 Pro 版")
    if P["mode"] == "transfer":
        run_transfer()
    else:
        run_init()
    ev("done")
except UserError as ex:
    ev("error", msg=str(ex), user=True)
except Exception:
    ev("error", msg=traceback.format_exc())
finally:
    _pf.close()
    if app is not None:
        app.quit()
'''

_GCP_SPAN = {
    "transfer": {"open": (0, 2, "打开参考期"), "add": (2, 3, "导入照片"), "match": (3, 65, "联合匹配（约需 3～5 分钟）"),
                 "align": (65, 85, "对齐新照片"), "place": (85, 86, "放蓝旗"), "refine": (86, 93, "细化标记点"),
                 "adjust": (93, 96, "平差"), "reject": (96, 97, "自动剔除"), "save": (97, 98, "保存工程"),
                 "export": (98, 99, "导出 XML"), "fix": (99, 100, "修正 XML")},
    "init": {"open": (0, 3, "打开空三工程"), "swap": (3, 8, "判断 X/Y"), "place": (8, 10, "放蓝旗"),
             "refine": (10, 70, "细化标记点"), "adjust": (70, 88, "平差"), "reject": (88, 94, "自动剔除"),
             "save": (94, 96, "保存工程"), "export": (96, 98, "导出 XML"), "fix": (98, 100, "修正 XML")},
}


def _run_ms_script(metashape, script_text, params, tmp, on_event, stop_event=None, startup_timeout=STARTUP_TIMEOUT,
                   stall_timeout=STALL_TIMEOUT, exit_timeout=EXIT_TIMEOUT):
    """把内嵌脚本和参数写进 tmp，子进程跑 `metashape -r 脚本 参数.json`；进度文件里每来一个事件就交给 on_event(e)。
    返回 (事件列表, 退出码)。取消抛 AlignCancelled；启动超时、卡死、完成后不退出抛 RuntimeError（Metashape 已被结束）。
    控制台输出在 tmp/console.txt。"""
    tmp = Path(tmp)
    script = tmp / "metashape_script.py"
    pfile = tmp / "params.json"
    prog_path = tmp / "progress.jsonl"
    script.write_text(script_text, encoding="utf-8")
    pfile.write_text(json.dumps(dict(params, progress=str(prog_path)), ensure_ascii=False, indent=1), encoding="utf-8")
    prog_path.write_text("", encoding="utf-8")
    events = []
    t_start = time.time()
    with open(tmp / "console.txt", "wb") as fcon:
        proc = subprocess.Popen([metashape, "-r", str(script), str(pfile)], stdin=subprocess.DEVNULL,
                                stdout=fcon, stderr=subprocess.STDOUT, cwd=str(tmp), env=subprocess_env(),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            offset, partial, stage = 0, "", ""
            started, done_at, last_change = False, None, time.time()
            while True:
                if stop_event is not None and stop_event.is_set():
                    _kill_tree(proc)
                    raise AlignCancelled()
                with open(prog_path, "r", encoding="utf-8") as fp:
                    fp.seek(offset)
                    data = fp.read()
                    offset = fp.tell()
                if data:
                    last_change = time.time()
                    partial += data
                    lines = partial.split("\n")
                    partial = lines.pop()
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            e = json.loads(line)
                        except ValueError:
                            continue
                        events.append(e)
                        kind = e.get("ev")
                        if kind == "start":
                            started = True
                        elif kind == "stage":
                            stage = e["stage"]
                        elif kind == "done":
                            done_at = time.time()
                        on_event(e)
                if proc.poll() is not None:
                    if data == "" and offset == os.path.getsize(prog_path):
                        break
                    continue
                now = time.time()
                if not started and now - t_start > startup_timeout:
                    _kill_tree(proc)
                    raise RuntimeError("Metashape 启动 %d 秒后还没开始执行脚本，已强制结束。可能原因：弹出了许可证/激活/更新对话框，"
                                       "或者这是标准版（标准版不能运行脚本）。请先手动打开 Metashape 确认能正常进入主界面。" % startup_timeout)
                if done_at is None and now - last_change > stall_timeout:
                    _kill_tree(proc)
                    raise RuntimeError("Metashape 已经 %d 秒没有进度（停在「%s」），判为卡死，已强制结束。" % (stall_timeout, stage))
                if done_at is not None and now - done_at > exit_timeout:
                    _kill_tree(proc)
                    raise RuntimeError("Metashape 完成后 %d 秒还没退出（可能卡在对话框），已强制结束。" % exit_timeout)
                time.sleep(0.5)
        except BaseException:
            if proc.poll() is None:
                _kill_tree(proc)
            raise
    return events, proc.returncode


def read_gcp_csv(path):
    """读控制点 CSV：每行 点名,X,Y,Z（米，局部坐标）；可以有表头（表头里写了 X/Y/Z 就按表头取列）；
    逗号、制表符、分号或空格分隔；UTF-8 或 GBK。返回 {点名: (x, y, z)}（文件顺序）；格式不对抛 ValueError。"""
    p = Path(path)
    if not p.is_file():
        raise ValueError("控制点 CSV 不存在：%s" % p)
    raw = p.read_bytes()
    text = None
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        raise ValueError("控制点 CSV 既不是 UTF-8 也不是 GBK 编码：%s" % p)
    cols = (1, 2, 3)
    pts = {}
    first = True
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = re.split(r"\s*[,\t;，]\s*", s) if re.search(r"[,\t;，]", s) else s.split()
        parts = [x.strip().strip('"').strip() for x in parts]
        if first:
            first = False
            low = [x.lower() for x in parts]
            if not _is_number(parts[1] if len(parts) > 1 else ""):
                if all(k in low for k in ("x", "y", "z")):
                    cols = (low.index("x"), low.index("y"), low.index("z"))
                continue  # 表头
        if len(parts) <= max(cols):
            raise ValueError("控制点 CSV 第 %d 行只有 %d 列，要求「点名,X,Y,Z」：%s" % (i, len(parts), s))
        try:
            xyz = tuple(float(parts[k]) for k in cols)
        except ValueError:
            raise ValueError("控制点 CSV 第 %d 行的坐标不是数字：%s" % (i, s))
        name = parts[0]
        if not name:
            raise ValueError("控制点 CSV 第 %d 行没有点名：%s" % (i, s))
        if name in pts:
            raise ValueError("控制点 CSV 里点名「%s」重复（第 %d 行）" % (name, i))
        pts[name] = xyz
    if len(pts) < 3:
        raise ValueError("控制点 CSV 里只有 %d 个点，至少要 3 个：%s" % (len(pts), p))
    return pts


def _is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def read_cc_marks(path):
    """读 ContextCapture 导出的 Blocks Exchange（.xml，或 zip 打包的 .xmlz）里的控制点刺点。
    PhotoId 经 <Photo><Id> 换成照片文件名（不带扩展名，= Metashape 相机名）。像素坐标原样返回（CC 约定），
    换算（+CC_PIXEL_OFFSET）在 Metashape 脚本里做。返回 ({点名: [(相机名, x, y), ...]}, 信息 dict)。"""
    p = Path(path)
    if not p.is_file():
        raise ValueError("CC 刺点文件不存在：%s" % p)
    zf = None
    try:
        if zipfile.is_zipfile(p):
            zf = zipfile.ZipFile(p)
            names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not names:
                raise ValueError("CC 刺点文件是压缩包，但里面没有 .xml：%s" % p)
            fp = zf.open(names[0])
        else:
            fp = open(p, "rb")
        photos, cps = {}, []
        with fp:
            for _, el in ET.iterparse(fp, events=("end",)):
                if el.tag == "Photo":
                    pid, ip = el.findtext("Id"), el.findtext("ImagePath") or ""
                    if pid is not None:
                        photos[pid.strip()] = os.path.splitext(re.split(r"[\\/]", ip.strip())[-1])[0]
                    el.clear()
                elif el.tag == "TiePoint":
                    el.clear()
                elif el.tag == "ControlPoint":
                    meas = [((m.findtext("PhotoId") or "").strip(), float(m.findtext("x")), float(m.findtext("y")))
                            for m in el.iter("Measurement")]
                    cps.append(((el.findtext("Name") or "").strip(), meas))
                    el.clear()
    except (ET.ParseError, TypeError, ValueError, zipfile.BadZipFile) as ex:
        if isinstance(ex, ValueError) and str(ex).startswith("CC 刺点文件"):
            raise
        raise ValueError("CC 刺点文件解析失败（要 ContextCapture 导出的 Blocks Exchange .xml/.xmlz）：%s（%s）" % (p, ex))
    finally:
        if zf is not None:
            zf.close()
    marks, unknown = {}, 0
    for name, meas in cps:
        lst = marks.setdefault(name, [])
        for pid, x, y in meas:
            if pid in photos:
                lst.append((photos[pid], x, y))
            else:
                unknown += 1
    n = sum(len(v) for v in marks.values())
    if not n:
        raise ValueError("CC 刺点文件里没有控制点刺点（<ControlPoint> 下的 <Measurement>）：%s" % p)
    return marks, {"photos": len(photos), "points": len(marks), "measurements": n, "unknown_photo": unknown}


def judge_gcp(rows, accuracy):
    """D-17：每个控制点 三维误差 ≤ 2×精度 且 像素 RMS ≤ 3 且 投影 ≥ 3 张 → 通过；全部通过才算自动通过。
    给 rows 每项加 "ok"、"why"；返回 (是否自动通过, [(点名, [原因…]), …])。"""
    bad = []
    lim = GCP_PASS_FACTOR * accuracy
    for r in rows:
        why = []
        if r.get("d3") is None:
            why.append("算不出三维位置（有效投影不足 2 张）")
        elif r["d3"] > lim:
            why.append("三维误差 %.1f mm > %.1f mm" % (r["d3"] * 1000, lim * 1000))
        if r.get("px_rms") is None:
            why.append("没有像素残差")
        elif r["px_rms"] > GCP_MAX_PX_RMS:
            why.append("像素 RMS %.2f > %g" % (r["px_rms"], GCP_MAX_PX_RMS))
        if r.get("n", 0) < GCP_MIN_PHOTOS:
            why.append("只有 %d 张照片 < %d" % (r.get("n", 0), GCP_MIN_PHOTOS))
        r["ok"] = not why
        r["why"] = why
        if why:
            bad.append((r["name"], why))
    return bool(rows) and not bad, bad


def judge_gcp_run(rows, accuracy, max_shift=None):
    """T4 🔒：只拿「参与平差」的点按 D-17 判；被排除的点改作检查点（加 why 说明原因，不算通过障碍）。
    参与平差的点 < GCP_MIN_POINTS → 失败；有点被 D-20 自动排除 → 至少「需人工核对」。
    返回 (结论, [(点名, [原因…]), …], 参与平差点数)。"""
    used = [r for r in rows if r.get("status", "used") == "used"]
    passed, bad = judge_gcp(used, accuracy)
    for r in rows:
        st = r.get("status", "used")
        if st == "auto":
            r["ok"] = None
            r["why"] = ["细化挪动过大，已自动排除（迁移偏差中位数 %s px > %g px），改作检查点" % (
                _fmt(r.get("refine_median_px"), 1, "%.1f"), max_shift if max_shift is not None else GCP_MAX_SHIFT_PX)]
            bad.append((r["name"], r["why"]))
        elif st == "user":
            r["ok"] = None
            r["why"] = ["用户排除，改作检查点"]
    if len(used) < GCP_MIN_POINTS:
        return "失败", bad, len(used)
    auto = any(r.get("status") == "auto" for r in rows)
    return ("自动通过" if passed and not auto else "需人工核对"), bad, len(used)


def count_bx_control_points(xml_path):
    """数 Blocks Exchange 里的 <ControlPoint> 和其下 <Measurement>。"""
    n_cp = n_meas = 0
    stack = []
    for event, el in ET.iterparse(str(xml_path), events=("start", "end")):
        if event == "start":
            stack.append(el.tag)
            continue
        stack.pop()
        if el.tag == "ControlPoint":
            n_cp += 1
            el.clear()
        elif el.tag == "Measurement" and "ControlPoint" in stack:
            n_meas += 1
        elif el.tag in ("Photo", "TiePoint"):
            el.clear()
    return n_cp, n_meas


def add_local_meter_srs(src, dst):
    """D-19：在已按 D-10 去掉坐标系的 XML（fix_blocks_exchange 的输出）上，流式加回 CC 认的局部米制坐标系：
    <Block> 前插 <SpatialReferenceSystems>（唯一一个 <SRS>，Id 1），<Block> 的 <Name> 之后插 <SRSId>1</SRSId>，
    每个 <ControlPoint> 的第一个子元素之前插 <SRSId>1</SRSId>；缩进照抄相邻元素，其余逐字保留。
    「空三」页签不调用它（那里仍不写坐标系）。返回 {"Block": 插了几处, "ControlPoint": 插了几处}。"""
    stats = {"Block": 0, "ControlPoint": 0}
    stack = []
    ws = ""                   # 上一个空白文本（下一个标签的缩进）
    need = None               # 等着插 SRSId 的元素：("Block" | "ControlPoint", 栈深度)
    srsid = "<SRSId>%s</SRSId>" % BX_LOCAL_SRS_ID

    def tag_name(tok):
        m = re.match(r"</?\s*([^\s/>]+)", tok)
        return m.group(1) if m else ""

    def indent(w):
        return w[w.rfind("\n") + 1:] if "\n" in w else w

    with open(src, "r", encoding="utf-8") as fi, open(dst, "w", encoding="utf-8", newline="") as fo:
        buf = ""
        while True:
            chunk = fi.read(4 * 1024 * 1024)
            buf += chunk
            if chunk:
                cut = buf.rfind(">") + 1
                if cut <= 0:
                    continue
                work, buf = buf[:cut], buf[cut:]
            else:
                work, buf = buf, ""
            for m in _TOKEN_RE.finditer(work):
                tok = m.group(0)
                if not tok.startswith("<"):
                    fo.write(tok)
                    ws = tok if not tok.strip() else ""
                    continue
                if tok.startswith("<?") or tok.startswith("<!"):
                    fo.write(tok)
                    ws = ""
                    continue
                name = tag_name(tok)
                closing = tok.startswith("</")
                selfclose = tok.endswith("/>")
                nl = "\n" + indent(ws) if "\n" in ws else ws
                if not closing:
                    parent = stack[-1] if stack else ""
                    if name == "SRSId" or name == "SpatialReferenceSystems":
                        raise ValueError("XML 里已经有 <%s>，要先按 D-10 去掉坐标系" % name)
                    if need is not None and len(stack) == need[1] and not (need[0] == "Block" and name == "Name"):
                        # 刚好在待插元素的下一个子元素（Block 跳过 <Name>）之前：插 SRSId，缩进同这个子元素
                        fo.write(srsid + nl)
                        stats[need[0]] += 1
                        need = None
                    if name == "Block" and parent == "BlocksExchange":
                        fo.write("<SpatialReferenceSystems>" + nl + "  <SRS>" + nl + "    <Id>%s</Id>" % BX_LOCAL_SRS_ID
                                 + nl + "    <Name>%s</Name>" % BX_LOCAL_SRS_NAME
                                 + nl + "    <Definition>%s</Definition>" % BX_LOCAL_SRS_DEF
                                 + nl + "  </SRS>" + nl + "</SpatialReferenceSystems>" + nl)
                    fo.write(tok)
                    stack.append(name)
                    if name in ("Block", "ControlPoint") and not selfclose:
                        need = (name, len(stack))
                    if selfclose:
                        stack.pop()
                else:
                    if need is not None and len(stack) == need[1]:
                        raise ValueError("<%s> 里没有可以挂 <SRSId> 的位置" % need[0])
                    fo.write(tok)
                    if stack:
                        stack.pop()
                ws = ""
            if not chunk:
                break
    return stats


def check_bx_local_srs(xml_path):
    """D-19 检查：有且只有一个 <SpatialReferenceSystems>/<SRS>（Id 1、Name、Definition 与 CC 写法一致），
    <Block> 下 1 处、每个 <ControlPoint> 下 1 处 <SRSId>1</SRSId>，别处没有 SRSId，<Pose> 下没有 <Metadata>。
    返回 (问题列表, {"control_points": n})。"""
    problems = []
    srs = []
    n_cp = 0
    block_ids, cp_ids, other_ids, pose_meta = [], [], 0, 0
    stack = []
    for event, el in ET.iterparse(str(xml_path), events=("start", "end")):
        if event == "start":
            if el.tag == "Metadata" and stack and stack[-1] == "Pose":
                pose_meta += 1
            stack.append(el.tag)
            continue
        stack.pop()
        parent = stack[-1] if stack else ""
        if el.tag == "SRSId":
            if parent == "Block":
                block_ids.append((el.text or "").strip())
            elif parent == "ControlPoint":
                cp_ids.append((el.text or "").strip())
            else:
                other_ids += 1
        elif el.tag == "SRS":
            srs.append((parent, el.findtext("Id"), el.findtext("Name"), el.findtext("Definition"), len(list(el))))
        elif el.tag == "ControlPoint":
            n_cp += 1
            if sum(1 for c in el if c.tag == "SRSId") != 1:
                problems.append("控制点 %s 下的 <SRSId> 不是 1 处" % el.findtext("Name"))
            el.clear()
        elif el.tag in ("Photo", "TiePoint"):
            el.clear()
    want = ("SpatialReferenceSystems", BX_LOCAL_SRS_ID, BX_LOCAL_SRS_NAME, BX_LOCAL_SRS_DEF, 3)
    if srs != [want]:
        problems.append("<SRS> 应该只有一个且是 %s，实际 %s" % (want[1:4], [s[1:4] for s in srs]))
    if block_ids != [BX_LOCAL_SRS_ID]:
        problems.append("<Block> 下的 <SRSId> 应该是 1 处「%s」，实际 %s" % (BX_LOCAL_SRS_ID, block_ids))
    if len(cp_ids) != n_cp or any(v != BX_LOCAL_SRS_ID for v in cp_ids):
        problems.append("控制点 %d 个，挂 <SRSId>%s</SRSId> 的 %d 处" % (
            n_cp, BX_LOCAL_SRS_ID, sum(1 for v in cp_ids if v == BX_LOCAL_SRS_ID)))
    if other_ids:
        problems.append("Block / ControlPoint 以外还有 %d 处 <SRSId>" % other_ids)
    if pose_meta:
        problems.append("<Pose> 下还有 %d 个 <Metadata>" % pose_meta)
    return problems, {"control_points": n_cp, "block_srsid": len(block_ids), "cp_srsid": len(cp_ids)}


def parse_exclude(text):
    """「排除的控制点」：逗号/顿号/分号/空格分隔的点名，如 "1,2"。返回去重后的点名列表（保持顺序）。"""
    out = []
    for x in re.split(r"[\s,，、;；]+", (text or "").strip()):
        if x and x not in out:
            out.append(x)
    return out


def _check_max_shift(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("细化挪动上限要填数字（像素），例如 50")
    if not f > 0:
        raise ValueError("细化挪动上限要大于 0（像素，默认 %g）" % GCP_MAX_SHIFT_PX)
    return f


def _check_gcp_accuracy(accuracy):
    try:
        acc = float(accuracy)
    except (TypeError, ValueError):
        raise ValueError("控制点精度要填数字（米），例如 0.01")
    if not 0 < acc < 10:
        raise ValueError("控制点精度要大于 0（单位米，例如 0.01）")
    return acc


def _check_psx(psx, what):
    p = Path(os.path.abspath(psx)) if psx else None
    if p is None or not p.is_file():
        raise ValueError("%s不存在：%s" % (what, psx))
    if p.suffix.lower() != ".psx":
        raise ValueError("%s要选 Metashape 工程 .psx：%s" % (what, p))
    if not (p.parent / (p.stem + ".files")).is_dir():
        raise ValueError("%s不完整：旁边缺 %s.files 目录：%s" % (what, p.stem, p))
    return p


def _gcp_result(mode, out_dir, metashape, accuracy, **kw):
    res = {"mode": mode, "status": "失败", "reason": "", "out_dir": str(out_dir), "metashape": metashape,
           "accuracy": accuracy, "version": "", "returncode": None, "done": False, "start": datetime.datetime.now(),
           "secs": None, "note": "", "warnings": [], "photos_dir": "", "ref": "", "psx_in": "", "csv": "", "cc": "",
           "total": None, "aligned": None, "unaligned": [], "tiepoints": None, "ref_moved_max": None, "ref_lost": [],
           "placed": {}, "swap": None, "cc_info": None, "cc_loaded": None, "markers_info": None, "rows": [],
           "rms3d": None, "rejects": [], "bad": [], "xml": "", "xml_info": None, "xml_cp": None, "fix_stats": None,
           "exclude": [], "max_shift": None, "excluded": {}, "refine_stats": {}, "srs_stats": None, "n_used": None}
    res.update(kw)
    return res


def _gcp_run(res, params, name, metashape, log, progress, stop_event, **timeouts):
    """跑一次控制点脚本并收尾：修正 XML、检查、判定、写报告。返回 res（status：自动通过/需人工核对/失败/已取消）。"""
    mode = res["mode"]
    out_dir = Path(res["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    psx = out_dir / (name + ".psx")
    xml_final = out_dir / (name + "_BlocksExchange.xml")
    xml_raw = out_dir / (name + "_BlocksExchange.未修正.xml.tmp")
    xml_d10 = out_dir / (name + "_BlocksExchange.无坐标系.xml.tmp")
    params = dict(params, mode=mode, psx=str(psx), xml_raw=str(xml_raw), accuracy=res["accuracy"],
                  edge_margin=GCP_EDGE_MARGIN, reject_min_px=GCP_REJECT_MIN_PX, reject_factor=GCP_REJECT_FACTOR,
                  reject_rounds=GCP_REJECT_ROUNDS, swap_ratio=GCP_SWAP_RATIO, exclude=list(res["exclude"]),
                  max_shift=res["max_shift"], min_points=GCP_MIN_POINTS)
    span = _GCP_SPAN[mode]
    t_start = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="uav_gcp_"))
    events = []

    def on_event(e):
        kind = e.get("ev")
        if kind == "start":
            res["version"] = e.get("version", "")
            log("Metashape 版本 %s（内嵌 Python %s）" % (res["version"], e.get("python", "")))
        elif kind == "stage":
            a, b, label = span.get(e["stage"], (0, 0, e["stage"]))
            progress(a / 100.0, label)
            log("%s…" % label)
        elif kind == "progress":
            a, b, label = span.get(e["stage"], (0, 0, e["stage"]))
            pct = e.get("pct", 0)
            progress((a + (b - a) * pct / 100.0) / 100.0, "%s %d%%" % (label, pct))
        elif kind == "warn":
            res["warnings"].append(e["msg"])
            log("注意：%s" % e["msg"])
        elif kind == "ref":
            log("参考期：%d 张照片（已对齐 %d），控制点 %s" % (e["cameras"], e["aligned"], "、".join(e["markers"])))
        elif kind == "aligned":
            res["aligned"], res["total"], res["unaligned"] = e["aligned"], e["total"], e["unaligned"]
            res["tiepoints"] = e.get("tiepoints")
            if mode == "transfer":
                res["ref_moved_max"], res["ref_lost"] = e.get("ref_moved_max"), e.get("ref_lost") or []
                mv = res["ref_moved_max"]
                log("新照片对齐 %d/%d（匹配 %.0f 秒，对齐 %.0f 秒）；参考期相机最大位移 %s%s" % (
                    e["aligned"], e["total"], e["secs_match"], e["secs_align"],
                    "%.3f mm" % (mv * 1000) if mv is not None else "-",
                    "，参考期有 %d 张掉了对齐" % len(res["ref_lost"]) if res["ref_lost"] else ""))
            else:
                log("空三工程：已对齐 %d/%d 张" % (e["aligned"], e["total"]))
            if e["unaligned"]:
                log("未对齐：%s" % "、".join(e["unaligned"]))
        elif kind == "cc_loaded":
            res["cc_loaded"] = e
            log("CC 刺点：%d 个点、%d 处搬进工程（像素坐标 +%.1f 换算），照片对不上 %d 处"
                % (e["markers"], e["measurements"], e["offset"], len(e["missing"])))
        elif kind == "markers":
            res["markers_info"] = e
            log("控制点：参与 %s；刺点够用（≥2 张）的 %d 个" % ("、".join(e["used"]), len(e["ok"])))
            if e["csv_unused"]:
                log("注意：CSV 里这些点在工程里没有刺点，没参与：%s" % "、".join(e["csv_unused"]))
            if e["extra"]:
                log("注意：工程里这些标记点 CSV 里没有，不参与：%s" % "、".join(e["extra"]))
        elif kind == "swap":
            res["swap"] = e
            log("X/Y 判断：原样 RMS3D %.4f m，对调 RMS3D %.4f m，相差 %.1f 倍 → %s" % (
                e["asis"]["rms3d"], e["swap"]["rms3d"], e["ratio"], _swap_verdict(e)))
        elif kind == "refined":
            res["refine_stats"], res["excluded"] = e["stats"], e.get("excluded") or {}
            for n, st in res["excluded"].items():
                if st == "auto":
                    log("D-20：%s 号细化挪动中位数 %.1f px > %g px，已自动排除（改作检查点）" % (
                        n, e["stats"][n]["refine_median_px"], e["max_shift"]))
                else:
                    log("%s 号由用户排除（改作检查点）" % n)
            moved = ["%s 号 %d 处" % (n, v["shift_removed"]) for n, v in e["stats"].items() if v.get("shift_removed")]
            if moved:
                log("细化挪动 > %g px 删掉的投影：%s" % (e["max_shift"], "，".join(moved)))
        elif kind == "placed":
            res["placed"] = e["counts"]
            log("放蓝旗：%s" % "，".join("%s 号 %d 张" % kv for kv in e["counts"].items()))
        elif kind == "reject":
            res["rejects"].append(e)
            log("自动剔除第 %d 轮：删掉 %d 处%s" % (e["round"], e["removed"], "（%s）" % "，".join(
                "%s 号 %d" % kv for kv in e["detail"].items()) if e["detail"] else ""))
        elif kind == "rows":
            res["rows"], res["rms3d"] = e["rows"], e["rms3d"]
            res["tiepoints"] = e.get("tiepoints", res["tiepoints"])
        elif kind == "exported":
            log("已导出 Blocks Exchange（%d 张相机，去掉未对齐 %d 张）" % (e["cameras"], e["dropped"]))
        elif kind == "done":
            res["done"] = True
        elif kind == "error":
            log("Metashape 脚本出错：\n%s" % e.get("msg", ""))

    log("—— 控制点 · %s" % GCP_MODE_LABEL[mode])
    log("输出：%s" % out_dir)
    progress(0.0, "启动 Metashape")
    try:
        events, rc = _run_ms_script(metashape, GCP_SCRIPT, params, tmp, on_event, stop_event, **timeouts)
        res["returncode"] = rc
        errs = [e for e in events if e.get("ev") == "error"]
        if errs:
            if errs[-1].get("user"):
                raise RuntimeError(errs[-1]["msg"])
            msg = (errs[-1].get("msg", "").strip().splitlines() or ["未知错误"])[-1]
            raise RuntimeError("Metashape 脚本出错：%s（完整信息见 metashape_日志.txt）" % msg)
        if rc != 0:
            raise RuntimeError("Metashape 异常退出，退出码 %s（0x%08X）" % (rc, rc & 0xFFFFFFFF))
        if not res["done"]:
            raise RuntimeError("Metashape 退出了，但进度文件里没有完成标记（脚本没有跑完）")
        if not xml_raw.is_file():
            raise RuntimeError("Metashape 没有导出 Blocks Exchange XML")
        progress(0.99, "修正 XML")
        log("按 D-19 修正 XML：去掉 Metashape 写的坐标系，改写 CC 的 %s（区块和每个控制点都挂上）…" % BX_LOCAL_SRS_DEF)
        res["fix_stats"] = fix_blocks_exchange(xml_raw, xml_d10)
        xml_raw.unlink()
        problems, info = check_blocks_exchange(xml_d10, res["aligned"])
        res["xml_info"] = info
        if problems:
            raise RuntimeError("XML 没通过检查：" + "；".join(problems))
        res["srs_stats"] = add_local_meter_srs(xml_d10, xml_final)
        xml_d10.unlink()
        res["xml"] = str(xml_final)
        problems, _ = check_bx_local_srs(xml_final)
        if problems:
            raise RuntimeError("XML 坐标系没通过检查（D-19）：" + "；".join(problems))
        res["xml_cp"] = count_bx_control_points(xml_final)
        log("XML 检查通过：%d 张照片、%d 个连接点、%d 个控制点（%d 处刺点）；坐标系 %s，区块 + %d 个控制点挂 SRSId"
            % (info["photos"], info["tiepoints"], res["xml_cp"][0], res["xml_cp"][1], BX_LOCAL_SRS_DEF,
               res["srs_stats"]["ControlPoint"]))
        res["status"], res["bad"], res["n_used"] = judge_gcp_run(res["rows"], res["accuracy"], res["max_shift"])
        if res["status"] == "失败":
            res["reason"] = "参与平差的控制点只有 %d 个，少于 %d 个" % (res["n_used"], GCP_MIN_POINTS)
        for r in res["rows"]:
            st = r.get("status", "used")
            log("  %s 号：%d 张，三维误差 %s，像素 RMS %s%s" % (
                r["name"], r["n"], "%.1f mm" % (r["d3"] * 1000) if r["d3"] is not None else "-",
                "%.2f" % r["px_rms"] if r["px_rms"] is not None else "-",
                "  ← " + "；".join(r["why"]) if st == "used" and not r["ok"] else
                "" if st == "used" else "  （%s，检查点，仅供参考）" % GCP_STATUS_LABEL[st]))
        log("RMS3D %s；判定：%s" % ("%.1f mm" % (res["rms3d"] * 1000) if res["rms3d"] is not None else "-", res["status"]))
        progress(1.0, res["status"])
    except (AlignCancelled, KeyboardInterrupt):
        res["status"] = "已取消"
        res["reason"] = "用户取消，Metashape 已被强制结束；本目录里的工程不完整，不要使用"
        log("已取消")
        (out_dir / "已取消.txt").write_text("用户在 %s 取消了这次控制点处理，本目录里的结果不完整，不要使用。\n"
                                          % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except Exception as ex:
        res["status"] = "失败"
        res["reason"] = str(ex)
        log("失败：%s" % ex)
    finally:
        res["secs"] = time.time() - t_start
        for f in (xml_raw, xml_d10):
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass
        _write_metashape_log(out_dir / "metashape_日志.txt", res, events, tmp / "console.txt")
        _write_gcp_report(out_dir / "控制点报告.txt", res)
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def _swap_verdict(sw):
    if not sw["confident"]:
        return "两种差别不到 %g 倍，判不准，按 RMS 小的「%s」用；请核对 CSV 的 X/Y" % (
            GCP_SWAP_RATIO, "X/Y 对调" if sw["use_swap"] else "原样")
    return "CSV 的 X/Y 对调了，已按对调后的坐标用" if sw["use_swap"] else "CSV 的 X/Y 没问题，按原样用"


def _fmt(v, scale=1.0, f="%.1f"):
    return f % (v * scale) if v is not None else "-"


def _write_gcp_report(path, res):
    mode = res["mode"]
    lines = ["控制点报告（%s v%s）" % (APP_NAME, APP_VERSION), "", "判定：%s" % res["status"]]
    ex = res["excluded"] or {}
    auto = [n for n, v in ex.items() if v == "auto"]
    user = [n for n, v in ex.items() if v == "user"]
    if auto:
        lines.append("%s 号已自动排除（细化挪动过大，D-20），没参与平差，改作检查点；有点被自动排除就不算自动通过"
                     % "、".join(auto))
    if user:
        lines.append("%s 号是用户排除的（--exclude），没参与平差，改作检查点" % "、".join(user))
    if res["status"] == "需人工核对":
        lines.append("需人工核对的点（在 Metashape 里打开 .psx 看这些点的投影）：")
        lines += ["  %s 号：%s" % (n, "；".join(why)) for n, why in res["bad"]]
    if res["reason"]:
        lines.append("原因：%s" % res["reason"])
    if res["status"] == "自动通过" and res["unaligned"]:
        lines.append("注意：有 %d 张照片没对齐，没有参与（清单见下）" % len(res["unaligned"]))
    if res["status"] == "需人工核对" or (res["status"] == "失败" and auto):
        lines += ["", "怎么处理："] + ["  " + x for x in _GCP_FIX_STEPS]
    lines += ["", "模式：%s" % GCP_MODE_LABEL[mode]]
    if mode == "transfer":
        lines += ["照片目录：%s" % res["photos_dir"], "参考期工程：%s（只读，没改）" % res["ref"]]
    else:
        lines += ["空三工程：%s（只读，没改）" % res["psx_in"], "控制点 CSV：%s" % res["csv"],
                  "CC 刺点：%s" % (res["cc"] or "无（用工程里已刺的点）")]
    lines += ["输出目录：%s" % res["out_dir"],
              "Metashape：%s（版本 %s）" % (res["metashape"], res["version"] or "未知"),
              "开始时间：%s；耗时 %.0f 秒" % (res["start"].strftime("%Y-%m-%d %H:%M:%S"), res["secs"] or 0),
              "判据（D-17）：每点 三维误差 ≤ %.1f mm（%g×控制点精度 %.3f m）、像素 RMS ≤ %g、照片 ≥ %d 张 → 通过；全部通过＝自动通过"
              % (GCP_PASS_FACTOR * res["accuracy"] * 1000, GCP_PASS_FACTOR, res["accuracy"], GCP_MAX_PX_RMS, GCP_MIN_PHOTOS),
              "自动剔除：像素残差 > max(%g px, %g×该点中位数) 的投影删掉后重新平差，最多 %d 轮；蓝旗离图像边缘 ≥ %d px"
              % (GCP_REJECT_MIN_PX, GCP_REJECT_FACTOR, GCP_REJECT_ROUNDS, GCP_EDGE_MARGIN),
              "平差：updateTransform + optimizeCameras（f, cx, cy, k1～k3, p1, p2）；坐标系 Local（米）；"
              "参与平差的点少于 %d 个判失败" % GCP_MIN_POINTS]
    if mode == "transfer":
        lines.append("细化挪动保护（D-20）：细化挪动 > %g px 的投影删掉；某点挪动中位数 > %g px 就自动排除该点（本次用 %g px）"
                     % (res["max_shift"], res["max_shift"], res["max_shift"]))
    lines.append("排除的控制点（用户指定）：%s" % ("、".join(res["exclude"]) if res["exclude"] else "无"))
    if mode == "transfer":
        lines.append("匹配：精度高（downscale=1）、通用预选 开、参考预选 关、关键点 %d、连接点 %d、reset_matches=True"
                     % (DEFAULT_KEYPOINTS, DEFAULT_TIEPOINTS))
    if res["note"]:
        lines.append("去定位：%s" % res["note"])
    for w in res["warnings"]:
        lines.append("注意：%s" % w)
    lines.append("")
    if res["aligned"] is not None:
        if mode == "transfer":
            lines.append("对齐：新照片 %d/%d；参考期相机最大位移 %s mm%s" % (
                res["aligned"], res["total"], _fmt(res["ref_moved_max"], 1000, "%.3f"),
                "；参考期有 %d 张掉了对齐" % len(res["ref_lost"]) if res["ref_lost"] else ""))
        else:
            lines.append("对齐：空三工程 %d/%d" % (res["aligned"], res["total"]))
        lines.append("未对齐照片：%s" % ("无" if not res["unaligned"] else "%d 张" % len(res["unaligned"])))
        lines += ["  " + u for u in res["unaligned"]]
    if res["cc_info"]:
        ci = res["cc_info"]
        lines.append("CC 刺点文件：%d 个点、%d 处刺点（PhotoId 对不上 <Photo> 的 %d 处）" % (
            ci["points"], ci["measurements"], ci["unknown_photo"]))
    if res["cc_loaded"]:
        cl = res["cc_loaded"]
        lines.append("CC 刺点搬进工程：%d 处（照片在工程里找不到或没对齐的 %d 处）；像素坐标 +%.1f 换成 Metashape 约定"
                     % (cl["measurements"], len(cl["missing"]), cl["offset"]))
    mi = res["markers_info"]
    if mi and mi["csv_unused"]:
        lines.append("CSV 里没有刺点、没参与的点：%s" % "、".join(mi["csv_unused"]))
    sw = res["swap"]
    if sw:
        lines.append("X/Y 判断：原样 RMS3D %.4f m，对调 RMS3D %.4f m，相差 %.1f 倍 → %s" % (
            sw["asis"]["rms3d"], sw["swap"]["rms3d"], sw["ratio"], _swap_verdict(sw)))
        if sw.get("pin_mean_dx") is not None:
            lines.append("  刺点平均像素残差（选定方案、只用刺点）：dx %+.2f、dy %+.2f（%d 处）" % (
                sw["pin_mean_dx"], sw["pin_mean_dy"], sw["pin_n"]))
    for rj in res["rejects"]:
        lines.append("自动剔除第 %d 轮：删掉 %d 处" % (rj["round"], rj["removed"]))
    if res["rows"]:
        n_used = sum(1 for r in res["rows"] if r.get("status", "used") == "used")
        lines += ["", "RMS3D：%s mm（%d 个参与平差的点%s）" % (
            _fmt(res["rms3d"], 1000), n_used, "；检查点不算在内" if n_used < len(res["rows"]) else ""), "逐点："]
        lines.append("  点名  状态  照片数  放蓝旗  迁移偏差（细化挪动）中位数px  细化删掉  挪动过大删掉  自动剔除"
                     "  dx/dy/dz(mm)  三维误差mm  像素RMS  像素最大  结论")
        for r in res["rows"]:
            d = r["d"] or [None] * 3
            st = r.get("status", "used")
            lines.append("  %-4s  %s  %5d  %6s  %26s  %8s  %12s  %8s  %s  %10s  %7s  %8s  %s" % (
                r["name"], GCP_STATUS_LABEL[st], r["n"], r.get("placed", "-"), _fmt(r.get("refine_median_px"), 1, "%.2f"),
                r.get("refine_removed", "-"), r.get("shift_removed", "-"), r.get("rejected", "-"),
                "/".join(_fmt(v, 1000) for v in d), _fmt(r["d3"], 1000), _fmt(r["px_rms"], 1, "%.2f"),
                _fmt(r["px_max"], 1, "%.2f"),
                ("通过" if r.get("ok") else "不通过：" + "；".join(r.get("why") or ["-"])) if st == "used"
                else "检查点，误差仅供参考"))
        lines.append(_GCP_SHIFT_NOTE)
    elif res["refine_stats"]:
        lines += ["", "没有平差结果；细化阶段的逐点统计：",
                  "  点名  状态  放蓝旗  迁移偏差（细化挪动）中位数px  细化删掉  挪动过大删掉"]
        for n, v in res["refine_stats"].items():
            lines.append("  %-4s  %s  %6s  %26s  %8s  %12s" % (
                n, GCP_STATUS_LABEL[v.get("status", "used")], v.get("placed", "-"),
                _fmt(v.get("refine_median_px"), 1, "%.2f"), v.get("refine_removed", "-"), v.get("shift_removed", "-")))
        lines.append(_GCP_SHIFT_NOTE)
    if res["xml"]:
        fs, xi, ss = res["fix_stats"], res["xml_info"], res["srs_stats"] or {}
        lines += ["", "Blocks Exchange：%s" % res["xml"],
                  "  坐标系（D-19）：去掉 Metashape 原样写的坐标系（<SpatialReferenceSystems> %d 个、<SRSId> %d 个、"
                  "<Pose> 下的 <Metadata> %d 个），改写 CC 的「%s」（%s）；<Block> 挂 SRSId %d 处、控制点挂 %d 处；坐标值保留"
                  % (fs["SpatialReferenceSystems"], fs["SRSId"], fs["Pose/Metadata"], BX_LOCAL_SRS_NAME, BX_LOCAL_SRS_DEF,
                     ss.get("Block", 0), ss.get("ControlPoint", 0))]
        if ex:
            lines.append("  被排除的点在 XML 里是检查点（<CheckPoint>true</CheckPoint>），CC 平差时不会用它们")
        if xi:
            lines.append("  照片 %d 张、连接点 %d 个；控制点 %d 个、刺点 %d 处；ImagePath 缺失 %d 个" % (
                xi["photos"], xi["tiepoints"], res["xml_cp"][0], res["xml_cp"][1], len(xi["missing_images"])))
        if res["unaligned"]:
            lines.append("  未对齐的照片没有写进 XML")
    lines += ["", "说明：坐标是控制点 CSV 的局部坐标（米）。.psx 可以直接在 Metashape 里打开核对；"
                  "%s" % ("这个工程可以当以后各期的参考期用（「迁移」模式选它）。" if mode == "init" else
                          "「需人工核对」时请看上面列出的点，不要直接拿去用。")]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


_GCP_SHIFT_NOTE = ("  迁移偏差（细化挪动）：自动放的位置和按图像细化后的位置差多少像素，越小说明迁移越准；"
                   "几十上百像素多半是认错了角点。")
_GCP_FIX_STEPS = (
    "1. 用 Metashape 打开本目录的 .psx；左边「工作区」展开「标记」（Markers），右键上面列出的点 →「按标记过滤照片」"
    "（Filter Photos by Markers），下方只剩看得到这个点的照片。",
    "2. 逐张双击打开，看旗子是不是落在这个控制点标靶的中心：偏了就按住旗子拖到中心（拖过的变成绿旗）。",
    "3. 落在别的角点上、或者看不清的，右键这面旗 →「删除标记」（Remove Marker）只删这一张上的投影；"
    "对着「参考」面板看该点误差，改完点「优化相机」（Optimize Cameras）再看一次，满意就保存。",
    "4. 只想先用其余的点：重跑本工具并排除这些点，命令行加 --exclude 1,2（界面在「排除的控制点」里填 1,2），"
    "它们会当检查点，其余点照常自动判定。",
    "5. 自己在 Metashape 里改完再导出的 Blocks Exchange 没有 D-19 的米制坐标系，导入 CC 会显示「单位」（数值仍是米）；"
    "要米制就用第 4 步重跑本工具得到的 XML。",
)


def _gcp_out_dir(base, name):
    out = Path(base) / (name + GCP_SUFFIX)
    if not _dir_empty_or_missing(out):
        raise ValueError("控制点输出目录不是空的，拒绝写入（不覆盖）：%s" % out)
    return out


def run_gcp_transfer(photo_dir, ref_psx, metashape, out_base=None, accuracy=DEFAULT_GCP_ACCURACY, auto_strip=True,
                     progress=None, log=None, stop_event=None, exclude=None, max_shift=GCP_MAX_SHIFT_PX, **timeouts):
    """模式一「迁移」：照片目录 + 参考期 .psx → <照片目录名>_控制点\\。不满足条件抛 ValueError（不动任何文件）。
    exclude：手动排除的点名（列表或 "1,2"），改作检查点；max_shift：D-20 细化挪动上限（px）。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    err = check_metashape(metashape)
    if err:
        raise ValueError(err)
    acc = _check_gcp_accuracy(accuracy)
    max_shift = _check_max_shift(max_shift)
    exclude = parse_exclude(exclude) if isinstance(exclude, str) else list(exclude or [])
    ref = _check_psx(ref_psx, "参考期工程")
    if not photo_dir or not Path(photo_dir).is_dir():
        raise ValueError("照片目录不存在：%s" % photo_dir)
    src = Path(os.path.abspath(photo_dir))  # 不用 resolve()：映射的网络盘会被换成 UNC 路径
    photos = list_jpegs(src)
    if not photos:
        raise ValueError("照片目录里没有 JPG（不含子目录）：%s" % src)
    out_base = Path(os.path.abspath(out_base)) if out_base else None
    log("照片目录：%s（%d 张）；参考期：%s" % (src, len(photos), ref))

    located = []  # D-07：逐张检查定位
    for i, p in enumerate(photos, 1):
        if stop_event is not None and stop_event.is_set():
            raise AlignCancelled()
        try:
            if photo_has_location(p):
                located.append(p)
        except Exception as ex:
            raise ValueError("读不了照片 %s：%s" % (p, ex))
        if i % 20 == 0 or i == len(photos):
            progress(0.0, "检查定位 %d/%d" % (i, len(photos)))
    note = "照片本来就不带定位"
    if located:
        log("有 %d/%d 张照片带定位（如 %s）" % (len(located), len(photos), located[0].name))
        if not auto_strip:
            raise ValueError("有 %d 张照片还带定位（如 %s），不许直接用（D-07）。\n请勾选「照片带定位时先自动去定位」，"
                             "或先用「剔除定位」页签处理。" % (len(located), located[0]))
        strip_dst = (out_base or src.parent) / (src.name + OUTPUT_SUFFIX)
        out_dir = _gcp_out_dir(strip_dst.parent, strip_dst.name)
        err = check_dirs(src, strip_dst)
        if err:
            raise ValueError("自动去定位做不了：%s" % err)
        log("先自动去定位 → %s" % strip_dst)
        st = run_strip(src, strip_dst, recursive=False, log=log, stop_event=stop_event,
                       progress=lambda i, n: progress(0.0, "去定位 %d/%d" % (i, n)))
        if stop_event is not None and stop_event.is_set():
            raise AlignCancelled()
        if st["失败"]:
            raise ValueError("自动去定位有 %d 张失败，停止，详见 %s" % (st["失败"], st["日志"]))
        work = strip_dst
        photos = list_jpegs(work)
        still = [p for p in photos if photo_has_location(p)]
        if still:
            raise ValueError("去定位后仍有 %d 张带定位（如 %s），停止" % (len(still), still[0]))
        note = "原照片带定位，已先自动去定位：%s → %s" % (src, strip_dst)
    else:
        work = src
        out_dir = _gcp_out_dir(out_base or src.parent, src.name)

    res = _gcp_result("transfer", out_dir, metashape, acc, note=note, photos_dir=str(work), ref=str(ref),
                      exclude=exclude, max_shift=max_shift)
    if exclude:
        log("用户排除的控制点：%s" % "、".join(exclude))
    params = {"ref": str(ref), "photos": [str(p) for p in photos], "downscale": ACCURACY_DOWNSCALE["high"],
              "keypoint_limit": DEFAULT_KEYPOINTS, "tiepoint_limit": DEFAULT_TIEPOINTS}
    return _gcp_run(res, params, work.name, metashape, log, progress, stop_event, **timeouts)


def run_gcp_init(psx, csv_path, metashape, cc_path=None, out_base=None, accuracy=DEFAULT_GCP_ACCURACY,
                 progress=None, log=None, stop_event=None, exclude=None, **timeouts):
    """模式二「建参考期」：空三 .psx + 控制点 CSV（+ CC 刺点）→ <名>_控制点\\（可作参考期）。
    不满足条件抛 ValueError（不动任何文件）。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    err = check_metashape(metashape)
    if err:
        raise ValueError(err)
    acc = _check_gcp_accuracy(accuracy)
    src = _check_psx(psx, "空三工程")
    pts = read_gcp_csv(csv_path)
    log("控制点 CSV：%s（%d 个点）" % (csv_path, len(pts)))
    exclude = parse_exclude(exclude) if isinstance(exclude, str) else list(exclude or [])
    bad = [x for x in exclude if x not in pts]
    if bad:
        raise ValueError("要排除的控制点 %s 在 CSV 里没有；CSV 里的点：%s" % ("、".join(bad), "、".join(pts)))
    if exclude:
        log("用户排除的控制点：%s" % "、".join(exclude))
    cc, cc_info, unknown = None, None, []
    if cc_path:
        marks, cc_info = read_cc_marks(cc_path)
        unknown = [k for k in marks if k not in pts]
        cc = dict((k, v) for k, v in marks.items() if k in pts)
        log("CC 刺点：%d 个点、%d 处（%s）" % (cc_info["points"], cc_info["measurements"], cc_path))
        if unknown:
            log("注意：CC 里这些点名 CSV 里没有，不用：%s" % "、".join(unknown))
        if not cc:
            raise ValueError("CC 刺点的点名（%s）和控制点 CSV 的点名一个都对不上" % "、".join(list(marks)[:8]))
    name = src.stem
    out_dir = _gcp_out_dir(Path(os.path.abspath(out_base)) if out_base else src.parent.parent, name)
    res = _gcp_result("init", out_dir, metashape, acc, psx_in=str(src), csv=str(csv_path),
                      cc=str(cc_path) if cc_path else "", cc_info=cc_info, exclude=exclude)
    if unknown:
        res["warnings"].append("CC 里这些点名 CSV 里没有，没用：%s" % "、".join(unknown))
    params = {"psx_in": str(src), "csv": dict((k, list(v)) for k, v in pts.items()),
              "cc": dict((k, [list(m) for m in v]) for k, v in cc.items()) if cc is not None else None,
              "cc_offset": CC_PIXEL_OFFSET}
    return _gcp_run(res, params, name, metashape, log, progress, stop_event, **timeouts)


# ---------------------------------------------------------------------------
# 批量迁移（D-24）：扫描上级目录，按规则表配参考期，一期接一期调 run_gcp_transfer
# ---------------------------------------------------------------------------

BATCH_NOT_PERIOD = (OUTPUT_SUFFIX, ALIGN_SUFFIX, GCP_SUFFIX)  # 名字这样结尾的目录不算一期
BATCH_DEFAULT_RULES = ({"key": "梯田", "psx": "", "exclude": ""}, {"key": "植被", "psx": "", "exclude": ""})
BATCH_EXIT_TEXT = {0: "自动通过", 1: "失败", 2: "拒绝", 3: "已取消", 4: "需人工核对"}
BATCH_CSV_FIELDS = ("序号", "期次", "类型", "照片数", "参考期", "排除点", "状态", "退出码", "判定", "RMS3D", "对齐",
                    "耗时分钟", "报告", "照片处理", "定位备份")  # 最后两列 T8（D-26）加
BATCH_WAIT_POLL = 30  # 秒：本机有别的 Metashape 在跑时，隔这么久再看一次
BATCH_PHOTO_MODES = ("copy", "inplace")  # D-26：复制去定位照片到输出目录（默认）/ 直接在原照片上抹除定位
BATCH_RERUN_LABEL = {"cancelled": "已取消", "failed": "失败"}


def _name_sort_key():
    """期次排序和 PowerShell 的 Sort-Object Name 一致（按系统区域的排序规则，中文按拼音）；非 Windows 退回按字符。"""
    import ctypes
    import functools
    try:
        cmp_ex = ctypes.windll.kernel32.CompareStringEx
        cmp_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p,
                           ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    except (AttributeError, OSError):
        return None
    return functools.cmp_to_key(lambda a, b: (cmp_ex(None, 0, a, -1, b, -1, None, None, None) or 2) - 2)


def _is_hidden(entry):
    return bool(getattr(entry.stat(), "st_file_attributes", 0) & 2)  # FILE_ATTRIBUTE_HIDDEN，和 Get-ChildItem 一样不看隐藏项


def _count_jpegs(d):
    return sum(1 for e in os.scandir(d) if e.is_file() and not _is_hidden(e) and os.path.splitext(e.name)[1].lower() in JPEG_EXTS)


def batch_period_dirs(root):
    """root 下每个直接含 JPG 的子目录算一期（不含 _去定位/_空三/_控制点 结尾的）。返回 [(名字, 路径, 照片数)]。"""
    names = [e.name for e in os.scandir(root) if e.is_dir() and not _is_hidden(e) and not e.name.endswith(BATCH_NOT_PERIOD)]
    key = _name_sort_key()
    names.sort(key=key) if key else names.sort()
    out = []
    for n in names:
        d = Path(root) / n
        k = _count_jpegs(d)
        if k:
            out.append((n, d, k))
    return out


def batch_find_report(out_dir, name):
    """这一期已有的控制点报告：<名>_控制点 或 <名>_去定位_控制点（照片带定位、先去定位时）。"""
    for d in (name + GCP_SUFFIX, name + OUTPUT_SUFFIX + GCP_SUFFIX):
        r = Path(out_dir) / d / "控制点报告.txt"
        if r.is_file():
            return r
    return None


def batch_result_state(out_dir, name):
    """D-27：这一期在输出目录里的结果。返回 (状态, 报告, 结果目录)：
    done（判定 自动通过/需人工核对）/ cancelled（判定 已取消 或有 已取消.txt）/ failed（判定 失败）/ None（没有报告）。
    报告里没有可识别的判定时照 T7 算 done（不动它）。"""
    rep = batch_find_report(out_dir, name)
    if rep is None:
        return None, None, None
    verdict = batch_read_report(rep)["判定"]
    if (rep.parent / "已取消.txt").exists() or verdict == "已取消":
        return "cancelled", rep, rep.parent
    if verdict == "失败":
        return "failed", rep, rep.parent
    return "done", rep, rep.parent


def batch_set_aside(result_dir, state, log=None):
    """重跑前把上次已取消/失败的结果目录改名 <原名>_上次<已取消|失败>_<时间> 保留，不删除。返回新路径。"""
    log = log or (lambda s: None)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    new = result_dir.with_name("%s_上次%s_%s" % (result_dir.name, BATCH_RERUN_LABEL[state], stamp))
    k = 2
    while new.exists():
        new = result_dir.with_name("%s_上次%s_%s_%d" % (result_dir.name, BATCH_RERUN_LABEL[state], stamp, k))
        k += 1
    try:
        os.rename(str(result_dir), str(new))
    except OSError as ex:
        raise ValueError("上次%s的结果目录改名失败（可能有文件被占用）：%s —— %s" % (BATCH_RERUN_LABEL[state], result_dir, ex))
    log("  上次%s的结果已改名保留：%s → %s" % (BATCH_RERUN_LABEL[state], result_dir.name, new.name))
    return new


def _dir_writable(d):
    """真的建一个临时文件再删掉来试（只读盘、ACL 拒绝写入时 os.access 在 Windows 上查不出来）。"""
    probe = Path(d) / (".uav写入检查_%d_%d.tmp_part" % (os.getpid(), threading.get_ident()))
    try:
        fd = os.open(str(probe), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    os.close(fd)
    try:
        os.unlink(str(probe))
    except OSError:
        pass
    return True


def check_batch_in_place(root, items, confirm):
    """D-26 原地模式开跑前检查：确认名 = 照片上级目录的名字；上级目录和要跑的各期照片目录都能写。
    不满足抛 ValueError，什么都不动（不建输出目录）。"""
    root = Path(os.path.abspath(root)) if root else None
    if root is None or not root.is_dir():
        raise ValueError("照片上级目录不存在：%s" % root)
    if not root.name:
        raise ValueError("照片上级目录是盘符根（%s），原地模式不接受，请选到具体目录" % root)
    if confirm != root.name:
        raise ValueError("确认名不正确：原地模式必须输入照片上级目录的名字「%s」（不是完整路径）" % root.name)
    dirs = [root] + [Path(it["dir"]) for it in items if it["status"].startswith("待运行")]
    for d in dirs:
        if not _dir_writable(d):
            raise ValueError("照片目录不可写（只读盘或没有写权限），原地模式整批拒绝，什么都没动：%s" % d)


class BatchItemFailed(Exception):
    """批量里这一期失败（如原地抹除有照片没抹成），不跑迁移。"""


def _batch_strip_in_place(photo_dir, n_expected, info, log, progress, stop_event):
    """D-26 原地模式的一期：原目录里有带定位的照片就调 T5 run_strip_in_place（留备份、不剔姿态、只处理这一层），
    有一张失败就抛 BatchItemFailed；全部没定位就跳过。info 里填「照片处理」「定位备份」（中途出错也填到已知的为止）。"""
    stopped = lambda: stop_event is not None and stop_event.is_set()
    src = Path(photo_dir)
    photos = list_jpegs(src)
    located, unreadable = [], []
    for i, p in enumerate(photos, 1):
        if stopped():
            raise AlignCancelled()
        try:
            if photo_has_location(p):
                located.append(p)
        except Exception as ex:
            unreadable.append("%s（%s）" % (p.name, ex))
        if i % 20 == 0 or i == len(photos):
            progress(0.0, "检查定位 %d/%d" % (i, len(photos)))
    if unreadable:
        raise BatchItemFailed("有 %d 张照片读不了，不抹除、不跑迁移：%s" % (len(unreadable), "；".join(unreadable[:5])))
    if not located:
        log("  原片无定位（%d 张都不带定位），跳过抹除" % len(photos))
        info["照片处理"] = "原片无定位"
        return
    log("  原地抹除：%d/%d 张带定位，直接改原照片（先写定位备份）→ %s" % (len(located), len(photos), src))
    info["照片处理"] = "原地抹除"
    try:
        st = run_strip_in_place(src, recursive=False, strip_attitude=False, no_backup=False, confirm=src.name,
                                progress=lambda i, n: progress(0.0, "原地抹除 %d/%d" % (i, n)), log=log,
                                stop_event=stop_event)
    except Exception as ex:  # 备份写不进去等：照片还没动
        raise BatchItemFailed("原地抹除没做成，这一期不跑迁移：%s: %s" % (type(ex).__name__, ex))
    backup = info["定位备份"] = st.get("备份", "")
    if stopped():
        log("  原地抹除中途取消：已抹的照片留着；定位备份 %s 要留着（重跑会再写一份，两份都要留）" % backup)
        raise AlignCancelled()
    if st["失败"]:
        why = []
        try:
            with open(st["日志"], encoding="utf-8-sig", newline="") as f:
                why = ["%s：%s" % (r[0], r[4]) for r in csv.reader(f) if len(r) >= 5 and r[1] == "失败"]
        except OSError:
            pass
        raise BatchItemFailed("原地抹除有 %d 张失败，这一期不跑迁移（详见 %s）：%s" % (
            st["失败"], st["日志"], "；".join(why[:5]) or "原因见日志"))
    still = [p for p in list_jpegs(src) if photo_has_location(p)]  # 交给 run_gcp_transfer 前确认已无定位，否则它会复制
    if still:
        raise BatchItemFailed("原地抹除后仍有 %d 张带定位（如 %s），这一期不跑迁移" % (len(still), still[0].name))
    n_now = len(list_jpegs(src))
    if n_now != n_expected:
        raise BatchItemFailed("原地抹除后照片数变了（%d → %d），这一期不跑迁移" % (n_expected, n_now))
    log("  原地抹除完成：抹除 %d 张、本来无定位 %d 张；定位备份 %s" % (st["成功"], st["跳过"], backup))


def batch_read_report(path):
    out = {"判定": "", "RMS3D": "", "对齐": ""}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return out
    for k in out:
        for ln in lines:
            if ln.startswith(k + "：") and len(ln) > len(k) + 1:
                out[k] = ln[len(k) + 1:]
                break
    return out


def batch_match_rule(name, rules):
    """从上到下第一条关键词出现在名字里的规则（不分大小写）；没有返回 None。"""
    for r in rules:
        if r["key"] and r["key"].lower() in name.lower():
            return r
    return None


def parse_batch_rule(text):
    """命令行 --rule：关键词=参考期.psx[:排除点]（路径里的盘符冒号不算分隔）。"""
    key, sep, rest = text.partition("=")
    m = re.match(r"^(.*?\.psx)(?::(.*))?$", rest.strip(), re.I)
    if not sep or not key.strip() or not m:
        raise ValueError("看不懂的 --rule：%s（写成 关键词=参考期.psx 或 关键词=参考期.psx:4）" % text)
    return {"key": key.strip(), "psx": m.group(1), "exclude": ",".join(parse_exclude(m.group(2) or ""))}


def parse_pick(text, n):
    """序号选择 "1,3,5-9" → 集合；越界或看不懂抛 ValueError。"""
    out = set()
    for part in re.split(r"[,，]", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            out.update(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise ValueError("看不懂的序号：%s（写成 1,3,5-9 这样）" % part)
    bad = sorted(k for k in out if k < 1 or k > n)
    if bad:
        raise ValueError("序号 %d 超出范围 1～%d" % (bad[0], n))
    return out


def check_batch_rules(rules):
    """规则表的参考期都要存在且是 .psx；返回问题清单（空 = 没问题）。没填参考期的规则允许（这类期次扫描时标跳过）。"""
    errs = []
    for r in rules:
        if r["psx"]:
            try:
                _check_psx(r["psx"], "规则「%s」的参考期" % r["key"])
            except ValueError as ex:
                errs.append(str(ex))
    return errs


def batch_scan(root, rules, out_dir, skip=()):
    """扫描：每期一个 dict（序号、期次、规则、照片数、参考期、排除点、状态 = 待运行 / 待运行（上次已取消|失败）/
    已有结果 / 跳过：原因）。"""
    if not root or not Path(root).is_dir():
        raise ValueError("照片上级目录不存在：%s" % root)
    items = []
    for i, (name, d, n) in enumerate(batch_period_dirs(root), 1):
        rule = batch_match_rule(name, rules)
        it = {"idx": i, "name": name, "dir": str(d), "n": n, "rule": rule["key"] if rule else "", "ref": "", "exclude": "",
              "status": "待运行", "report": ""}
        if name in skip:
            it["status"] = "跳过：在跳过名单里"
        elif rule is None:
            it["status"] = "跳过：没有匹配的规则"
        elif not rule["psx"]:
            it["status"] = "跳过：规则「%s」没填参考期" % rule["key"]
        else:
            it["ref"], it["exclude"] = rule["psx"], rule["exclude"]
        state, rep, _ = batch_result_state(out_dir, name) if out_dir else (None, None, None)
        if rep:
            it["report"] = str(rep)
            if it["status"] == "待运行":
                it["status"] = "已有结果" if state == "done" else "待运行（上次%s）" % BATCH_RERUN_LABEL[state]
        items.append(it)
    return items


def other_metashape_running():
    """本机有没有 metashape.exe / photoscan.exe 在跑（tasklist，不需要第三方库）。查不了就当没有。"""
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30).stdout
    except Exception:
        return False
    names = set(ln.split(",")[0].strip('"').lower() for ln in out.decode("mbcs" if os.name == "nt" else "utf-8",
                                                                         "replace").splitlines() if ln)
    return any(n in names for n in METASHAPE_NAMES)


def write_batch_summary(path, rows):
    """汇总 CSV：列和 batch_gcp_transfer.ps1 相同，UTF-8 带 BOM、每格加引号（Export-Csv 的写法）。先写临时文件再替换。"""
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(BATCH_CSV_FIELDS)
        for r in rows:
            w.writerow([r.get(k, "") for k in BATCH_CSV_FIELDS])
    os.replace(str(tmp), str(path))


def _batch_row(it):
    row = dict((k, "") for k in BATCH_CSV_FIELDS)
    row.update({"序号": it["idx"], "期次": it["name"], "类型": it["rule"] or "未知", "照片数": it["n"],
                "参考期": Path(it["ref"]).name if it["ref"] else "", "排除点": it["exclude"]})
    return row


def _fill_from_report(row, rep):
    r = batch_read_report(rep)
    row.update({"判定": r["判定"], "RMS3D": r["RMS3D"], "对齐": r["对齐"], "报告": str(rep)})


def _fmt_minutes(secs):
    return ("%.1f" % (secs / 60.0)).rstrip("0").rstrip(".")


def run_gcp_batch(items, out_dir, metashape, plan=False, log=None, progress=None, on_item=None, stop_event=None,
                  summary=None, wait_poll=BATCH_WAIT_POLL, photo_mode="copy", root=None, confirm=None, **timeouts):
    """按顺序处理 items（batch_scan 的结果里选中的那些）：状态不是「待运行…」的照写一行；待运行的串行调 run_gcp_transfer。
    每期写一次汇总 CSV。on_item(item, 阶段, 文字)：阶段 wait/start/done。取消时当前这一期按 stop_event 停下，
    剩下的不开始。photo_mode：copy（默认，复制去定位照片到输出目录）/ inplace（D-26，直接在原照片上抹除定位，
    须给 root 和 confirm = 上级目录名；检查不过抛 ValueError、什么都不动）。上次已取消/失败的期重跑前把旧结果目录改名保留（D-27）。
    返回 {"rows", "summary", "cancelled", "exit", "counts"}；exit：0 全部自动通过或跳过 / 4 有需人工核对 / 1 有失败 / 3 取消。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    on_item = on_item or (lambda it, phase, text: None)
    stopped = lambda: stop_event is not None and stop_event.is_set()
    if photo_mode not in BATCH_PHOTO_MODES:
        raise ValueError("照片处理方式只能是 %s：%s" % ("/".join(BATCH_PHOTO_MODES), photo_mode))
    in_place = photo_mode == "inplace"
    if in_place:
        check_batch_in_place(root, items, confirm)  # 在建输出目录之前：不过就什么都不动
    out_dir = Path(os.path.abspath(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = Path(summary) if summary else out_dir / ("批量迁移汇总_%s.csv" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    rows, cancelled = [], False
    log("—— 批量迁移：选中 %d 期，其中待运行 %d 期%s" % (len(items), sum(it["status"].startswith("待运行") for it in items),
                                                "（只列计划）" if plan else ""))
    log("照片处理方式：%s" % ("直接在原照片上抹除定位（不复制，定位备份写在各期照片目录里）" if in_place
                        else "复制一份去定位照片到输出目录（原片不动）"))
    log("输出目录：%s" % out_dir)
    write_batch_summary(summary, rows)
    for pos, it in enumerate(items, 1):
        tag = "[%d/%d · 序号 %d] %s" % (pos, len(items), it["idx"], it["name"])
        row = _batch_row(it)
        stripped = out_dir / (it["name"] + OUTPUT_SUFFIX)
        has_stripped = stripped.is_dir() and any(stripped.iterdir())
        prev = None  # 上次已取消/失败的结果：(状态, 结果目录)
        if it["status"].startswith("待运行") and not plan:
            # 开跑前再看一次：上一期跑的时候别的电脑可能已经做完
            state, rep, rdir = batch_result_state(out_dir, it["name"])
            if state == "done":
                it["status"], it["report"] = "已有结果", str(rep)
            elif state:
                prev = (state, rdir)
        if not it["status"].startswith("待运行"):
            row["状态"] = "跳过：已有结果" if it["status"] == "已有结果" else it["status"]
            if it["report"]:
                _fill_from_report(row, it["report"])
            log("%s → %s" % (tag, row["状态"]))
            on_item(it, "done", it["status"])
        elif plan:
            row["状态"] = "计划运行"
            row["照片处理"] = "用已有去定位" if has_stripped else "原地抹除" if in_place else "复制"
            log("%s（%s，%d 张）→ 会用参考期 %s%s；照片：%s%s" % (
                tag, it["rule"], it["n"], row["参考期"], "，排除点 %s" % it["exclude"] if it["exclude"] else "",
                row["照片处理"], "；%s，旧结果目录会改名保留后重跑" % it["status"] if it["status"] != "待运行" else ""))
        else:
            waited = False
            while not stopped() and other_metashape_running():
                if not waited:
                    log("%s：本机已有别的 Metashape 在跑，等它结束（每 %d 秒看一次）… %s" % (
                        tag, wait_poll, datetime.datetime.now().strftime("%H:%M")))
                    on_item(it, "wait", "等待其他 Metashape")
                    progress(0.0, "第 %d/%d 期 · 等待其他 Metashape" % (pos, len(items)))
                    waited = True
                if stop_event is not None:
                    stop_event.wait(wait_poll)
                else:
                    time.sleep(wait_poll)
            if stopped():
                cancelled = True
                on_item(it, "done", it["status"])
                log("已取消：%s 及之后的 %d 期都没有开始" % (it["name"], len(items) - pos + 1))
                break
            log("%s → 开始（参考期 %s%s） %s" % (tag, row["参考期"], "，排除点 %s" % it["exclude"] if it["exclude"] else "",
                                         datetime.datetime.now().strftime("%H:%M")))
            on_item(it, "start", "运行中")
            iprog = lambda f, s, pos=pos: progress(f, "第 %d/%d 期 · %s" % (pos, len(items), s))
            t0 = time.time()
            res, code = None, 1
            info = {"照片处理": "", "定位备份": ""}
            with open(out_dir / ("%s_运行日志.txt" % it["name"]), "a", encoding="utf-8") as flog:
                def plog(s):
                    log(s)
                    flog.write(s + "\n")
                    flog.flush()

                try:
                    if prev:
                        batch_set_aside(prev[1], prev[0], plog)
                    # 上次已经去定位过（输出目录里有 <名>_去定位），就直接用它，避免工具因去定位目录非空而拒绝
                    photo_dir = it["dir"]
                    if has_stripped:
                        n = _count_jpegs(stripped)
                        if n != it["n"]:
                            raise ValueError("上次去定位没做完（%s 里 %d 张，原目录 %d 张）：删掉这个目录后重跑" % (
                                stripped, n, it["n"]))
                        photo_dir = str(stripped)
                        info["照片处理"] = "用已有去定位"
                        plog("  用已去定位的照片：%s" % stripped)
                    elif in_place:
                        _batch_strip_in_place(it["dir"], it["n"], info, plog, iprog, stop_event)
                    res = run_gcp_transfer(photo_dir, it["ref"], metashape, out_dir, log=plog, stop_event=stop_event,
                                           progress=iprog, exclude=it["exclude"], **timeouts)
                    code = {"自动通过": 0, "需人工核对": 4, "已取消": 3}.get(res["status"], 1)
                except ValueError as ex:
                    plog("拒绝执行：%s" % ex)
                    code = 2
                except BatchItemFailed as ex:
                    plog("失败：%s" % ex)
                    code = 1
                except (AlignCancelled, KeyboardInterrupt):
                    plog("已取消")
                    code = 3
            if not info["照片处理"]:  # 复制模式：看 run_gcp_transfer 是先去定位了，还是原片本来就没定位
                if res is not None:
                    info["照片处理"] = "复制" if res.get("note", "").startswith("原照片带定位") else "原片无定位"
                elif stripped.is_dir():
                    info["照片处理"] = "复制"
            row.update(info)
            row["耗时分钟"] = _fmt_minutes(time.time() - t0)
            row["退出码"] = code
            row["状态"] = BATCH_EXIT_TEXT[code]
            rep = batch_find_report(out_dir, it["name"])
            if rep:
                _fill_from_report(row, rep)
            rms = " %.1f mm" % (res["rms3d"] * 1000) if res and res.get("rms3d") is not None else ""
            it["status"] = ("失败（拒绝执行）" if code == 2 else row["状态"]) + rms
            log("  → %s  %s  用时 %s 分钟" % (row["状态"], row["RMS3D"], row["耗时分钟"]))
            on_item(it, "done", it["status"])
            cancelled = code == 3 or stopped()
        rows.append(row)
        write_batch_summary(summary, rows)  # 每期写一次，中途停了也有汇总
        if cancelled:
            if pos < len(items):
                log("已取消：剩下 %d 期没有开始" % (len(items) - pos))
            break
    log("")
    log("汇总表：%s" % summary)
    counts = {}
    for r in rows:
        counts[r["状态"]] = counts.get(r["状态"], 0) + 1
    for st, c in counts.items():
        log("  %s：%d 期" % (st, c))
    backups = [r["定位备份"] for r in rows if r.get("定位备份")]
    if backups:
        log("本次写了 %d 份定位备份（在各期照片目录里；要还原用「剔除定位」页签的「从备份还原」或命令行 restore-gps）" % len(backups))
    states = set(counts)
    code = 3 if cancelled else 1 if states & {"失败", "拒绝"} else 4 if "需人工核对" in states else 0
    return {"rows": rows, "summary": str(summary), "cancelled": cancelled, "exit": code, "counts": counts}


# ---------------------------------------------------------------------------
# 影像处理（D-29，T9）：扫描工点 → 勾选 → 一个接一个用 Metashape 空三，导出带 CGCS2000 3 度带坐标的
# Blocks Exchange（CC 直接导入、不用再空三），可选出 DOM/DSM。室外照片的 GPS 有用：本页签不去定位（与其他页签相反）。
# T9 卡 🔒 节的定义和判据写在下面的常量里，不要改。
# ---------------------------------------------------------------------------

SURVEY_DOWNSCALE = 0              # 对齐精度「最高」
SURVEY_KEYPOINTS = 40000
SURVEY_TIEPOINTS = 10000
SURVEY_MIN_RATIO = 0.8            # 对齐率 < 80% 判失败
SURVEY_DEPTH_DOWNSCALE = 1        # 深度图 / 密集点云质量「超高」，深度过滤「适中」
SURVEY_RESOLUTION = 0.03          # DOM/DSM 分辨率（米）；0 = 自动（Metashape 给的原始分辨率）
SURVEY_NODATA = -9999.0           # DSM 的 NoData
SURVEY_BIGTIFF = 3800 * 1024 ** 2  # 估算不压缩超过这么大就写 BigTIFF（普通 TIFF 上限 4 GB，留余量）
SURVEY_NEAR_ZONE = 0.1            # 经度离分带线（中央经线 ±1.5°）不到这么多度：标「靠近分带线」
SURVEY_MIN_GPS = 3                # 带定位的照片少于这么多张，没法按 GPS 定坐标
SURVEY_EPSG_MIN, SURVEY_EPSG_MAX = 4534, 4554  # CGCS2000 3 度带（东坐标不带带号），中央经线 75°E～135°E
SURVEY_REGION_LIMIT = 200.0       # 米：（不裁边时）DOM/DSM 范围用 Metashape 自动的区域框；比已对齐相机的平面范围往外伸出超过这么多的边收回来
# D-33/T13（勾了自动裁边时）：平面范围 = 自动区域框 ∪ 连接点东、北各自的分位数范围，再限制在相机平面范围 ± 1000 米；
# 高程方向用自动区域框的。多出来的边角由自动裁边收回
SURVEY_REGION_LIMIT_CLIP = 1000.0
SURVEY_REGION_TIE_PCT = (0.1, 99.9)  # %：连接点分位数（去掉两头各 0.1% 的离群点）
# D-32/T12 自动裁边：0.5 米分析网格上「DOM 有影像 + 不插值的辅助 DSM 有真实点 + 点云够密」的格子 → 补缝 → 最大连片 →
# 填小洞 → 往里收 → 简化成范围线；DOM/DSM 用同一条线同像元网格裁（T11 的两趟导出、范围线文件、用户线优先照旧）
SURVEY_CLIP_INSET = 3.0           # 米：「往里收」的默认值（界面/命令行可改）
SURVEY_CLIP_INSET_MIN, SURVEY_CLIP_INSET_MAX = 0.0, 30.0
SURVEY_CLIP_HOLE = 100.0          # m²：内部面积小于这么多的洞填掉
SURVEY_CLIP_GRID = 0.5            # 米：分析网格（判有效、补缝、连片、填洞、往里收都在这上面做），原点 = 没裁的 DOM/DSM 左上角
SURVEY_CLIP_SIMPLIFY = 1.0        # 米：范围线简化的容差上限，也是相邻点最小间距
SURVEY_CLIP_MARGIN_PX = 5         # 像元：输出外框 = 范围线外接矩形外扩这么多像素
SURVEY_CLIP_DOM_FRAC = 0.9        # 判据①：格子里没裁的 DOM alpha > 0 的像元 ≥ 这么多（9 成）才算「影像有内容」
SURVEY_CLIP_AUX_RES = 0.1         # 米：不插值辅助 DSM 的分辨率（只判断、不输出；5 × 5 个子格 = 1 个分析格）
SURVEY_CLIP_BLOCK = 1.0           # 米：判据③「点云够密」按这么大的块算（10 × 10 个子格 = 2 × 2 个分析格）
SURVEY_CLIP_DENSITY = 50.0        # %：判据③ 块里有点的子格占比的门槛（「点云密度门槛」，界面/命令行可调）
SURVEY_CLIP_DENSITY_MIN, SURVEY_CLIP_DENSITY_MAX = 10.0, 90.0
SURVEY_CLIP_CLOSE = 1.0           # 米：补缝＝先往外扩这么多再往里收这么多（把细缝连起来）
SURVEY_CLIP_ANALYSIS = "_裁边分析.json.gz"  # 三条判据的分析网格缓存（只改往里收/密度门槛重新裁时不用再进 Metashape 分析）
SURVEY_CLIP_AUTO, SURVEY_CLIP_NONE = "auto", "none"
SURVEY_CLIP_INSET_TAIL = 0.25     # 像元：向 Metashape 要多 0.25 个像元（它按 int() 截断像素数），保证正好外扩 5 像素
SURVEY_RANGE_KML = "_范围线.kml"  # 范围线备份（WGS84 经纬度，可选用）
SURVEY_RANGE_SHP = "_范围线.shp"  # 范围线备份（坐标系同 DOM，带 .shx/.dbf/.prj）
SURVEY_REPORT = "处理报告.txt"
SURVEY_TODO = "待处理"
SURVEY_LOW_GPS = "定位不足"
SURVEY_CSV_FIELDS = ("序号", "工点", "照片数", "带定位张数", "坐标系", "坐标系来源", "状态", "对齐", "未对齐数",
                     "相机参考误差RMS", "DOM/DSM", "耗时分钟", "报告")
SURVEY_ACCURACY_NOTE = "平面/高程精度取决于机载 GPS（普通 GPS、没有 RTK），未用控制点，误差一般是米级；高程用照片记录的高度，没有做高程基准改正。"

# 内嵌脚本：运行在 Metashape 自带的 Python 里（1.7 是 3.8），只许用 Python 3.8 语法。
SURVEY_SCRIPT = r'''# -*- coding: utf-8 -*-
# 由「无人机照片工具」影像处理页签生成，在 Metashape 内嵌的 Python 里运行。只用 Python 3.8 语法。
import json
import math
import os
import sys
import time
import traceback

P = json.load(open(sys.argv[-1], "r", encoding="utf-8"))
_pf = open(P["progress"], "a", encoding="utf-8")


def ev(kind, **kw):
    kw["ev"] = kind
    kw["t"] = round(time.time(), 1)
    _pf.write(json.dumps(kw, ensure_ascii=False) + "\n")
    _pf.flush()


class Prog(object):
    def __init__(self, stage):
        self.stage = stage
        self.last = -1

    def __call__(self, p):
        p = int(p)
        if p != self.last:
            self.last = p
            ev("progress", stage=self.stage, pct=p)


def timed(stage, fn, *a, **kw):
    ev("stage", stage=stage)
    t0 = time.time()
    out = fn(*a, **kw)
    ev("secs", stage=stage, secs=round(time.time() - t0, 1))
    return out


def enum(owner, *names):
    for n in names:
        o = owner
        for part in n.split("."):
            o = getattr(o, part, None)
            if o is None:
                break
        if o is not None:
            return o
    raise RuntimeError("Metashape 里找不到 %s" % "/".join(names))


def region_box(chunk, crs):
    """区域框 8 个角换到投影坐标后的范围 [东min, 北min, 东max, 北max, 高min, 高max]。"""
    T = chunk.transform.matrix
    reg = chunk.region
    cs = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                v = reg.center + reg.rot * Metashape.Vector([sx * reg.size.x / 2, sy * reg.size.y / 2, sz * reg.size.z / 2])
                cs.append(crs.project(T.mulp(v)))
    return [min(c.x for c in cs), min(c.y for c in cs), max(c.x for c in cs), max(c.y for c in cs),
            min(c.z for c in cs), max(c.z for c in cs)]


def set_region_box(chunk, crs, box):
    """把区域框设成投影坐标里北朝上的长方体 box = [东min, 北min, 东max, 北max, 高min, 高max]。"""
    x0, y0, x1, y1, z0, z1 = box
    Ti = chunk.transform.matrix.inv()
    center = Metashape.Vector([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2])
    c_int = Ti.mulp(crs.unproject(center))
    axes, lens = [], []
    for d in ([1, 0, 0], [0, 1, 0], [0, 0, 1]):
        a = Ti.mulp(crs.unproject(Metashape.Vector([center.x + d[0], center.y + d[1], center.z + d[2]]))) - c_int
        lens.append(a.norm())
        axes.append(a.normalized())
    reg = chunk.region
    reg.center = c_int
    reg.rot = Metashape.Matrix([[axes[0].x, axes[1].x, axes[2].x], [axes[0].y, axes[1].y, axes[2].y],
                                [axes[0].z, axes[1].z, axes[2].z]])
    reg.size = Metashape.Vector([(x1 - x0) * lens[0], (y1 - y0) * lens[1], (z1 - z0) * lens[2]])
    chunk.region = reg


def check_region(chunk, cams, crs, limit):
    """DOM/DSM 范围用 Metashape 对齐后自动给的区域框；只有它比已对齐相机的平面范围还往外伸出 limit 米以上时
    （斜拍照片里的远山把框撑大），才把那一边收到 相机范围 ± limit。返回 (用的框, 原来的框, 是否收过)。"""
    T = chunk.transform.matrix
    cp = [crs.project(T.mulp(c.center)) for c in cams]
    cb = [min(p.x for p in cp), min(p.y for p in cp), max(p.x for p in cp), max(p.y for p in cp)]
    box = region_box(chunk, crs)
    new = [max(box[0], cb[0] - limit), max(box[1], cb[1] - limit), min(box[2], cb[2] + limit),
           min(box[3], cb[3] + limit), box[4], box[5]]
    if new[:4] != box[:4]:
        set_region_box(chunk, crs, new)
        return region_box(chunk, crs), box, True
    return box, box, False


def tie_range(chunk, crs, pct):
    """连接点（稀疏点云里有效的点）东、北坐标各自 pct[0]%～pct[1]% 分位数（最近秩）的范围 [东min, 北min, 东max, 北max]
    和点数；没有连接点返回 (None, 0)。"""
    tp = getattr(chunk, "tie_points", None)  # 2.x
    if tp is None:
        tp = getattr(chunk, "point_cloud", None)  # 1.x
    if tp is None or tp.points is None:
        return None, 0
    T = chunk.transform.matrix
    xs, ys = [], []
    for p in tp.points:
        if not p.valid:
            continue
        q = crs.project(T.mulp(Metashape.Vector([p.coord.x, p.coord.y, p.coord.z])))
        xs.append(q.x)
        ys.append(q.y)
    n = len(xs)
    if not n:
        return None, 0
    xs.sort()
    ys.sort()
    lo, hi = [int(round(f / 100.0 * (n - 1))) for f in pct]
    return [xs[lo], ys[lo], xs[hi], ys[hi]], n


def expand_region(chunk, cams, crs, limit, pct):
    """D-33（勾了自动裁边）：平面范围 = 自动区域框 ∪ 连接点分位数范围，再限制在 已对齐相机平面范围 ± limit；
    高程用自动区域框的。返回 (用的框, 原来的框, 连接点范围, 连接点数, 被 ± limit 收过的边 [西/南/东/北])。"""
    T = chunk.transform.matrix
    cp = [crs.project(T.mulp(c.center)) for c in cams]
    cb = [min(p.x for p in cp), min(p.y for p in cp), max(p.x for p in cp), max(p.y for p in cp)]
    box = region_box(chunk, crs)
    tr, n = tie_range(chunk, crs, pct)
    u = list(box[:4])
    if tr:
        u = [min(u[0], tr[0]), min(u[1], tr[1]), max(u[2], tr[2]), max(u[3], tr[3])]
    lim = [cb[0] - limit, cb[1] - limit, cb[2] + limit, cb[3] + limit]
    new = [max(u[0], lim[0]), max(u[1], lim[1]), min(u[2], lim[2]), min(u[3], lim[3])]
    sides = [s for s, a, b in zip(("西", "南", "东", "北"), u, new) if a != b]
    if new != box[:4]:
        set_region_box(chunk, crs, new + box[4:6])
        return region_box(chunk, crs), box, tr, n, sides
    return box, box, tr, n, sides


app = None
try:
    try:
        import Metashape
    except ImportError:
        import PhotoScan as Metashape
    app = Metashape.app
    ev("start", version=str(app.version), python=sys.version.split()[0])
    print("Metashape version: %s" % app.version)
    CS = Metashape.CoordinateSystem
    crs = CS("EPSG::%d" % P["epsg"])
    geo = CS("EPSG::4490")  # CGCS2000 经纬度；照片 GPS（WGS84）与 CGCS2000 只差厘米级，按同一套经纬度换算到投影坐标

    doc = Metashape.Document()
    doc.save(P["psx"])
    chunk = doc.addChunk()
    chunk.label = P["name"]
    ev("stage", stage="add")
    chunk.addPhotos(P["photos"])
    cams = list(chunk.cameras)
    if len(cams) != len(P["photos"]):
        raise RuntimeError("addPhotos added %d cameras, expected %d" % (len(cams), len(P["photos"])))

    # 照片 GPS（经纬度）→ 该工点的 CGCS2000 3 度带投影坐标，作相机参考；姿态参考不用（斜拍、云台角不当先验）
    ev("stage", stage="refs")
    located, samples, sx, sy, sz = 0, [], 0.0, 0.0, 0.0
    for c in cams:
        r = c.reference
        loc = r.location
        if loc is not None and not (abs(loc.x) < 1e-9 and abs(loc.y) < 1e-9):
            p = CS.transform(Metashape.Vector([loc.x, loc.y, loc.z]), geo, crs)
            r.location = p
            r.location_enabled = True
            located += 1
            sx, sy, sz = sx + p.x, sy + p.y, sz + p.z
            if len(samples) < 3 or c is cams[-1]:
                samples.append([os.path.basename(c.photo.path), [loc.x, loc.y, loc.z], [p.x, p.y, p.z]])
        else:
            r.location = None
            r.location_enabled = False
        r.rotation_enabled = False
    chunk.crs = crs
    if located < P["min_gps"]:
        raise RuntimeError("带定位的照片只有 %d 张（少于 %d 张），没法按 GPS 定坐标" % (located, P["min_gps"]))
    mean = [sx / located, sy / located, sz / located]
    ev("refs", total=len(cams), located=located, crs=crs.name, mean=mean, samples=samples[:4])

    kw = dict(generic_preselection=True, reference_preselection=True, keypoint_limit=P["keypoint_limit"],
              tiepoint_limit=P["tiepoint_limit"], progress=Prog("match"))
    timed("match", chunk.matchPhotos, downscale=P["downscale"], **kw)
    timed("align", chunk.alignCameras, progress=Prog("align"))
    aligned = [c for c in chunk.cameras if c.transform is not None]
    unaligned = [os.path.basename(c.photo.path) for c in chunk.cameras if c.transform is None]
    if not aligned:
        raise RuntimeError("一张照片都没有对齐")
    timed("optimize", chunk.optimizeCameras, progress=Prog("optimize"))
    aligned = [c for c in chunk.cameras if c.transform is not None]
    unaligned = [os.path.basename(c.photo.path) for c in chunk.cameras if c.transform is None]
    T = chunk.transform.matrix
    errs = []
    for c in aligned:
        if c.reference.location is not None and c.reference.location_enabled:
            errs.append(crs.project(T.mulp(c.center)) - c.reference.location)
    rms = None
    if errs:
        rx = math.sqrt(sum(e.x ** 2 for e in errs) / len(errs))
        ry = math.sqrt(sum(e.y ** 2 for e in errs) / len(errs))
        rz = math.sqrt(sum(e.z ** 2 for e in errs) / len(errs))
        rms = [rx, ry, rz, math.sqrt(rx * rx + ry * ry + rz * rz), len(errs)]
    tp = getattr(chunk, "tie_points", None)  # 2.x
    if tp is None:
        tp = getattr(chunk, "point_cloud", None)  # 1.x
    n_tie = len(tp.points) if tp is not None and tp.points is not None else 0
    ev("aligned", aligned=len(aligned), total=len(chunk.cameras), unaligned=unaligned, tiepoints=n_tie, rms=rms)
    timed("save", doc.save)
    if len(aligned) < P["min_ratio"] * len(chunk.cameras):
        ev("low_ratio")
        ev("done")
    else:
        # 只读重开工程再导出；未对齐的相机从导出里去掉（D-12），.psx 保留全部照片
        ev("stage", stage="export")
        t0 = time.time()
        doc2 = Metashape.Document()
        doc2.open(P["psx"], read_only=True)
        ch2 = doc2.chunk
        drop = [c for c in ch2.cameras if c.transform is None]
        if drop:
            ch2.remove(drop)
        ch2.exportCameras(P["xml_raw"], format=enum(Metashape, "CamerasFormatBlocksExchange",
                                                    "CamerasFormat.CamerasFormatBlocksExchange"), crs=crs)
        ev("exported", path=P["xml_raw"], cameras=len(ch2.cameras), dropped=len(drop))
        doc2 = ch2 = None
        ev("secs", stage="export", secs=round(time.time() - t0, 1))

        if P["dom_dsm"]:
            res = P["resolution"] or 0
            op = Metashape.OrthoProjection()
            op.crs = crs
            if P["clip"]:
                box, box0, tr, n_tr, sides = expand_region(chunk, aligned, crs, P["region_limit_clip"],
                                                           P["region_tie_pct"])
                ev("region", box=box, default=box0, clamped=bool(sides), mode="tie", limit=P["region_limit_clip"],
                   tie=tr, n_tie=n_tr, pct=P["region_tie_pct"], sides=sides)
            else:
                box, box0, clamped = check_region(chunk, aligned, crs, P["region_limit"])
                ev("region", box=box, default=box0, clamped=clamped, mode="limit", limit=P["region_limit"])
            timed("depth", chunk.buildDepthMaps, downscale=P["depth_downscale"],
                  filter_mode=Metashape.ModerateFiltering, progress=Prog("depth"))
            timed("dense", chunk.buildDenseCloud, point_colors=True, progress=Prog("dense"))
            timed("save", doc.save)
            timed("dem", chunk.buildDem, source_data=Metashape.DenseCloudData,
                  interpolation=Metashape.EnabledInterpolation, projection=op, resolution=res, progress=Prog("dem"))
            timed("ortho", chunk.buildOrthomosaic, surface_data=Metashape.ElevationData,
                  blending_mode=Metashape.MosaicBlending, fill_holes=True, projection=op, resolution=res,
                  progress=Prog("ortho"))
            timed("save", doc.save)
            info = {}
            # DOM、DSM 导出成同一个范围（DSM 的范围）；分辨率按投影坐标单位给（resolution 参数是地面米，
            # 高斯投影离中央经线远了会差万分之几，导出的像素就不是整 0.03）
            el = chunk.elevation
            box = Metashape.BBox()
            box.min = Metashape.Vector([el.left, el.bottom])
            box.max = Metashape.Vector([el.right, el.top])
            # 要裁边时先导出「没裁」的 DSM、DOM（拿它们定网格、判「影像有内容」），按范围线重新导出时再出正式的
            kinds = {"dsm": (Metashape.ElevationData, 1, 4), "dom": (Metashape.OrthomosaicData, 4, 1)}
            plan = (("dsm", P["dsm_uncropped"] if P["clip"] else P["dsm"]),
                    ("dom", P["dom_uncropped"] if P["clip"] else P["dom"]))
            for key, out_path in plan:
                if out_path is None:
                    continue
                src, bands, nbytes = kinds[key]
                r = chunk.elevation if key == "dsm" else chunk.orthomosaic
                px = res or r.resolution
                w, h = (el.right - el.left) / px, (el.top - el.bottom) / px
                big = w * h * bands * nbytes > P["bigtiff"]
                ic = Metashape.ImageCompression()
                ic.tiff_compression = Metashape.ImageCompression.TiffCompressionDeflate
                ic.tiff_big = big
                ic.tiff_tiled = False
                ic.tiff_overviews = False
                kw = dict(source_data=src, image_format=Metashape.ImageFormatTIFF, projection=op, region=box,
                          save_world=True, image_compression=ic, progress=Prog("export_" + key))
                if res:
                    kw.update(resolution_x=res, resolution_y=res)
                if key == "dsm":
                    kw.update(nodata_value=P["nodata"], save_alpha=False)
                else:
                    kw.update(save_alpha=True, white_background=False)
                timed("export_" + key, chunk.exportRaster, out_path, **kw)
                info[key] = {"native": r.resolution, "res": px, "left": el.left, "right": el.right, "bottom": el.bottom,
                             "top": el.top, "width": int(round(w)), "height": int(round(h)), "bigtiff": big}
            ev("rasters", info=info)
        ev("done")
except Exception:
    ev("error", msg=traceback.format_exc())
finally:
    _pf.close()
    if app is not None:
        app.quit()
'''

_SURVEY_SPAN = {
    False: {"add": (0, 1, "导入照片"), "refs": (1, 2, "GPS 换算到投影坐标"), "match": (2, 60, "匹配（最高精度）"),
            "align": (60, 85, "对齐"), "optimize": (85, 90, "优化相机"), "save": (90, 92, "保存工程"),
            "export": (92, 97, "导出 XML"), "fix": (97, 100, "修正 XML")},
    True: {"add": (0, 1, "导入照片"), "refs": (1, 1, "GPS 换算到投影坐标"), "match": (1, 12, "匹配（最高精度）"),
           "align": (12, 16, "对齐"), "optimize": (16, 17, "优化相机"), "save": (17, 17, "保存工程"),
           "export": (17, 18, "导出 XML"), "depth": (18, 48, "深度图（超高）"), "dense": (48, 70, "密集点云"),
           "dem": (70, 76, "生成 DSM"), "ortho": (76, 86, "生成 DOM"), "export_dsm": (86, 89, "导出 DSM"),
           "export_dom": (89, 92, "导出 DOM"), "clip": (92, 99, "按范围线裁边"), "fix": (99, 100, "检查结果")},
}
_SURVEY_STEP_LABEL = (("match", "匹配"), ("align", "对齐"), ("optimize", "优化相机"), ("export", "导出 XML"),
                      ("depth", "深度图"), ("dense", "密集点云"), ("dem", "DSM"), ("ortho", "DOM"),
                      ("export_dsm", "导出 DSM"), ("export_dom", "导出 DOM"), ("clip", "裁边重新导出"),
                      ("dom_raw", "导出没裁的 DOM（裁边分析）"), ("aux_dem", "建不插值辅助 DSM"),
                      ("aux_export", "导出辅助 DSM"), ("clip_dsm", "按范围线导出 DSM"), ("clip_dom", "按范围线导出 DOM"))



def survey_zone(lon):
    """照片平均经度 → (EPSG, 中央经线, 是否靠近分带线)。🔒：中央经线 = 3 × round(经度 / 3)，EPSG = 4534 + (中央经线 − 75) / 3；
    不在 75°E～135°E 的 3 度带范围里时 EPSG 为 None。"""
    cm = 3 * int(lon / 3.0 + 0.5)
    epsg = SURVEY_EPSG_MIN + (cm - 75) // 3
    near = abs(abs(lon - cm) - 1.5) < SURVEY_NEAR_ZONE
    return (epsg if SURVEY_EPSG_MIN <= epsg <= SURVEY_EPSG_MAX else None), cm, near


def survey_cm(epsg):
    return 75 + 3 * (int(epsg) - SURVEY_EPSG_MIN)


def survey_srs_label(epsg):
    """列表和汇总表里显示的坐标系，如 EPSG:4543 CM102；没有坐标系返回空串。"""
    return "EPSG:%d CM%d" % (epsg, survey_cm(epsg)) if epsg else ""


def survey_srs_name(epsg):
    return "CGCS2000 / 3-degree Gauss-Kruger CM %dE" % survey_cm(epsg)


def parse_survey_epsg(text):
    """「auto」「自动」或空 → None；「4543」「EPSG:4543」「EPSG:4543 CM102」→ 4543；不在 4534～4554 抛 ValueError。"""
    t = (text or "").strip()
    if not t or t.lower() == "auto" or t.startswith("自动"):
        return None
    m = re.match(r"^(?:EPSG\s*:{0,2}\s*)?(\d{4,5})\b", t, re.I)
    if not m:
        raise ValueError("看不懂的坐标系：%s（写 auto 或 4534～4554 的 EPSG 号，如 4543）" % text)
    e = int(m.group(1))
    if not SURVEY_EPSG_MIN <= e <= SURVEY_EPSG_MAX:
        raise ValueError("EPSG:%d 不是 CGCS2000 3 度带（不带带号）：只能是 %d～%d（中央经线 75°E～135°E）"
                         % (e, SURVEY_EPSG_MIN, SURVEY_EPSG_MAX))
    return e


def parse_survey_resolution(text):
    """DOM/DSM 分辨率：「auto」「自动」或 0 → 0（Metashape 给的原始分辨率）；否则 0.001～10 米。"""
    t = str(text if text is not None else "").strip()
    if not t or t.lower() == "auto" or t.startswith("自动"):
        return 0.0
    try:
        v = float(t)
    except ValueError:
        raise ValueError("分辨率要填数字（米，如 0.03）或「自动」：%s" % text)
    if v == 0:
        return 0.0
    if not 0.001 <= v <= 10:
        raise ValueError("分辨率要在 0.001～10 米之间（默认 %g）：%s" % (SURVEY_RESOLUTION, text))
    return v


_RE_XMP_GPS = re.compile(rb"drone-dji:(GpsLatitude|GpsLongitude|GpsLongtitude|AbsoluteAltitude)\s*=\s*[\"']([^\"']*)[\"']",
                         re.IGNORECASE)


def _exif_gps(b, base, end):
    """Exif（TIFF 头在 base）里 GPS IFD 的经纬度、高度；没有返回 None。"""
    order = bytes(b[base:base + 2])
    e = "<" if order == b"II" else ">" if order == b"MM" else None
    if e is None or base + 8 > end:
        return None
    entries, _ = _read_ifd(b, base, end, struct.unpack(e + "I", b[base + 4:base + 8])[0], e)
    ptr = [x for x in entries if x[0] == GPS_POINTER_TAG]
    if not ptr:
        return None
    gents, _ = _read_ifd(b, base, end, struct.unpack(e + "I", ptr[0][3])[0], e)
    tags = dict((t, (typ, cnt, val)) for t, typ, cnt, val, _ in gents)

    def rationals(tag):
        typ, cnt, val = tags[tag]
        if typ not in (5, 10) or cnt < 1:
            raise JpegError("GPS 标签 0x%04X 类型不对" % tag)
        p = base + struct.unpack(e + "I", val)[0]
        if p + 8 * cnt > end:
            raise JpegError("GPS 标签 0x%04X 越界" % tag)
        out = []
        for k in range(cnt):
            n, d = struct.unpack(e + ("II" if typ == 5 else "ii"), bytes(b[p + 8 * k:p + 8 * k + 8]))
            out.append(n / float(d) if d else 0.0)
        return out

    def dms(v):
        return v[0] + (v[1] if len(v) > 1 else 0) / 60.0 + (v[2] if len(v) > 2 else 0) / 3600.0

    if 2 not in tags or 4 not in tags:
        return None
    lat, lon = dms(rationals(2)), dms(rationals(4))
    if 1 in tags and tags[1][2][:1] in (b"S", b"s"):
        lat = -lat
    if 3 in tags and tags[3][2][:1] in (b"W", b"w"):
        lon = -lon
    alt = rationals(6)[0] if 6 in tags else None
    if alt is not None and 5 in tags and tags[5][2][:1] == b"\x01":
        alt = -alt
    return lat, lon, alt


def photo_gps(path, head=65536):
    """照片的 (纬度, 经度, 高度)：先读 EXIF GPS，没有再读 DJI XMP；不带定位（或是 0,0）返回 None。
    只读文件开头（默认 64 KB，元数据段更长时才多读），网络盘上也快。"""
    with open(path, "rb") as f:
        b = f.read(head)
        if b[:2] != b"\xff\xd8":
            return None
        exif = xmp = None
        i = 2
        while i + 4 <= len(b) and b[i] == 0xFF:
            m = b[i + 1]
            if m == 0xFF:
                i += 1
                continue
            if m in (0xDA, 0xD9):
                break
            seg_end = i + 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
            if m == 0xE1:
                if seg_end > len(b):
                    b += f.read(seg_end - len(b) + 4)
                payload = b[i + 4:i + 4 + 32]
                if payload.startswith(EXIF_HEADER) and exif is None:
                    try:
                        exif = _exif_gps(b, i + 4 + len(EXIF_HEADER), min(seg_end, len(b))) or False
                    except (JpegError, struct.error, IndexError, KeyError, ZeroDivisionError):
                        exif = False
                elif payload.startswith(XMP_HEADER) and xmp is None:
                    v = dict((k.lower(), s) for k, s in _RE_XMP_GPS.findall(b[i + 4:seg_end]))
                    try:
                        lon = v.get(b"gpslongitude") or v.get(b"gpslongtitude")
                        xmp = (float(v[b"gpslatitude"]), float(lon),
                               float(v[b"absolutealtitude"]) if v.get(b"absolutealtitude") else None)
                    except (KeyError, TypeError, ValueError):
                        xmp = False
            i = seg_end
    for g in (exif, xmp):
        if g and -90 <= g[0] <= 90 and -180 <= g[1] <= 180 and not (abs(g[0]) < 1e-9 and abs(g[1]) < 1e-9):
            return g
    return None


def _visible_jpeg(e):
    return e.is_file() and not _is_hidden(e) and os.path.splitext(e.name)[1].lower() in JPEG_EXTS


def _scandir_memo(d, memo):
    """同一次扫描里同一个目录只列一次（网络盘上列目录慢）。"""
    d = str(d)
    if d not in memo:
        memo[d] = list(os.scandir(d))
    return memo[d]


def survey_site_photos(d, memo=None, with_stat=False):
    """工点下所有照片（含各层子文件夹，如 正摄\），按相对路径排序；不看隐藏项（同批量迁移）。
    with_stat：返回 [(路径, 大小, 修改时间)]（用列目录时带回来的信息，不再逐个去网络盘上查）。"""
    memo = {} if memo is None else memo
    out = []
    stack = [Path(d)]
    while stack:
        cur = stack.pop()
        es = _scandir_memo(cur, memo)
        for e in es:
            if _visible_jpeg(e):
                st = e.stat()
                out.append((Path(e.path), st.st_size, st.st_mtime_ns))
        stack += [cur / e.name for e in es if e.is_dir() and not _is_hidden(e)]
    key = _name_sort_key()
    rel = lambda x: str(x[0].relative_to(d)).lower()
    out.sort(key=(lambda x: key(rel(x))) if key else rel)
    return out if with_stat else [x[0] for x in out]


def survey_site_dirs(root, memo=None):
    """🔒 工点：所选目录下每个「自己直接含 JPG、且不在另一个工点里面」的文件夹；自己不直接含 JPG 的往下找子文件夹。
    返回 [(工点名, 工点文件夹, 是否在子目录里)]。工点名一般就是这个文件夹的名字；若它的上级（在所选目录之下）里
    只有它这一个工点（如只有 正摄\ 的工点），工点名和工点文件夹都取到那一级（照片还是同一批）。重名时用相对路径拼。
    「在子目录里」＝工点文件夹不是所选目录的直接子文件夹（如 无正射影像\<子文件夹>），这类默认不勾。
    排序：直接子文件夹的工点在前，各自按相对路径（中文按拼音，同批量迁移）。"""
    memo = {} if memo is None else memo
    root = Path(os.path.abspath(root))
    key = _name_sort_key()
    found = []

    def walk(d):
        es = _scandir_memo(d, memo)
        if any(_visible_jpeg(e) for e in es):
            found.append(Path(d))
            return
        subs = [e.name for e in es if e.is_dir() and not _is_hidden(e)]
        subs.sort(key=key) if key else subs.sort()
        for n in subs:
            walk(Path(d) / n)

    walk(root)
    sites = []
    for d in found:
        cur = d
        while cur != root and cur.parent != root and sum(1 for x in found if cur.parent in x.parents) == 1:
            cur = cur.parent
        sites.append(cur)
    names = [s.name if s != root else root.name for s in sites]
    out = []
    for s, n in zip(sites, names):
        if names.count(n) > 1:
            n = "_".join(s.relative_to(root).parts) or n
        out.append((n, s, s != root and s.parent != root))
    rel = lambda x: str(x[1].relative_to(root)) if x[1] != root else ""
    out.sort(key=(lambda x: (x[2], key(rel(x)))) if key else (lambda x: (x[2], rel(x))))
    return out


def survey_result_dir(out_dir, name):
    return Path(out_dir) / name


def survey_read_report(path):
    out = {"状态": "", "对齐": "", "相机参考误差RMS": "", "DOM/DSM": ""}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return out
    for k in out:
        for ln in lines:
            if ln.startswith(k + "：") and len(ln) > len(k) + 1:
                out[k] = ln[len(k) + 1:]
                break
    return out


def survey_result_state(out_dir, name):
    """这个工点在输出目录里的结果：返回 (状态, 报告, 结果目录)。done（完成/部分对齐）/ cancelled（已取消 或有 已取消.txt）/
    failed（失败，或目录里有东西却没有报告）/ None（没有结果目录）。"""
    d = survey_result_dir(out_dir, name)
    rep = d / SURVEY_REPORT
    if not rep.is_file():
        if d.is_dir() and any(d.iterdir()):
            return "failed", None, d
        return None, None, None
    verdict = survey_read_report(rep)["状态"].split("（")[0].strip()
    if (d / "已取消.txt").exists() or verdict == "已取消":
        return "cancelled", rep, d
    if verdict == "失败":
        return "failed", rep, d
    return "done", rep, d


def survey_scan(root, out_dir=None, skip=(), epsg=None, manual=None, cache=None, progress=None, stop_event=None):
    """扫描工点：每个一个 dict（序号、工点、照片清单、照片数、带定位张数、平均经纬度、自动选的带、用的 EPSG 和来源、
    是否靠近分带线、状态）。epsg：整批统一用这个（来源记「手动」）；manual：{工点名: EPSG}（界面里手动改过的）。
    cache：{路径: (大小, 修改时间, GPS)}，重扫时没变的照片不再读。状态：待处理 / 待处理（上次失败|已取消）/ 已有结果 /
    定位不足 / 跳过：在跳过名单里。default：界面里默认勾不勾（待处理且不在子目录里的才勾）。"""
    progress = progress or (lambda i, n, s: None)
    if not root or not Path(root).is_dir():
        raise ValueError("照片上级目录不存在：%s" % root)
    if out_dir:
        r, o = Path(os.path.abspath(root)), Path(os.path.abspath(out_dir))
        if o == r or r in o.parents:
            raise ValueError("输出目录不能放在照片目录里面（原片目录只读）：%s" % o)
    manual = manual or {}
    cache = cache if cache is not None else {}
    progress(0, 0, "列工点")
    memo = {}
    sites = [(n, d, nested, survey_site_photos(d, memo, with_stat=True)) for n, d, nested in survey_site_dirs(root, memo)]
    total = sum(len(p) for _, _, _, p in sites)
    items, k = [], 0
    for i, (name, d, nested, photos) in enumerate(sites, 1):
        gps = []
        for p, size, mtime in photos:
            if stop_event is not None and stop_event.is_set():
                raise AlignCancelled()
            k += 1
            hit = cache.get(str(p))
            if hit and hit[0] == size and hit[1] == mtime:
                g = hit[2]
            else:
                try:
                    g = photo_gps(p)
                except OSError:
                    g = None
                cache[str(p)] = (size, mtime, g)
            if g:
                gps.append(g)
            if k % 25 == 0 or k == total:
                progress(k, total, name)
        lon = sum(g[1] for g in gps) / len(gps) if gps else None
        lat = sum(g[0] for g in gps) / len(gps) if gps else None
        auto, cm, near = survey_zone(lon) if gps else (None, None, False)
        use, src = auto, "自动"
        if epsg:
            use, src = epsg, "手动"
        elif manual.get(name):
            use, src = int(manual[name]), "手动"
        rel = str(Path(d).relative_to(os.path.abspath(root))) if Path(d) != Path(os.path.abspath(root)) else name
        it = {"idx": i, "name": name, "dir": str(d), "rel": rel, "n": len(photos), "nested": nested,
              "n_gps": len(gps), "lon": lon, "lat": lat, "auto_epsg": auto, "near": near, "epsg": use, "epsg_src": src,
              "status": SURVEY_TODO, "report": "", "default": False}
        state, rep, _ = survey_result_state(out_dir, name) if out_dir else (None, None, None)
        if rep:
            it["report"] = str(rep)
        if name in skip:
            it["status"] = "跳过：在跳过名单里"
        elif state == "done":
            it["status"] = "已有结果"
        elif len(gps) * 2 < len(photos):
            it["status"] = SURVEY_LOW_GPS
        elif state:
            it["status"] = "%s（上次%s）" % (SURVEY_TODO, BATCH_RERUN_LABEL[state])
        it["default"] = it["status"].startswith(SURVEY_TODO) and not nested
        items.append(it)
    return items


def survey_runnable(it):
    """勾上后会真跑的：待处理…，以及用户自己勾上的「定位不足」。"""
    return it["status"].startswith(SURVEY_TODO) or it["status"] == SURVEY_LOW_GPS


def add_epsg_srs(src, dst, epsg):
    """在已按 D-10 去掉坐标系的 XML 上，流式加回 CC 认的 EPSG 坐标系（T9 🔒）：<Block> 前插 <SpatialReferenceSystems>
    （唯一一个 <SRS>：Id 1、Name「CGCS2000 / 3-degree Gauss-Kruger CM <中央经线>E」、Definition「EPSG:<号>」），
    <Block> 的 <Name> 之后插 <SRSId>1</SRSId>；缩进照抄相邻元素，其余逐字保留。返回插了几处 <SRSId>。"""
    stack, ws, need, n = [], "", None, 0

    def tag_name(tok):
        m = re.match(r"</?\s*([^\s/>]+)", tok)
        return m.group(1) if m else ""

    def indent(w):
        return w[w.rfind("\n") + 1:] if "\n" in w else w

    with open(src, "r", encoding="utf-8") as fi, open(dst, "w", encoding="utf-8", newline="") as fo:
        buf = ""
        while True:
            chunk = fi.read(4 * 1024 * 1024)
            buf += chunk
            if chunk:
                cut = buf.rfind(">") + 1
                if cut <= 0:
                    continue
                work, buf = buf[:cut], buf[cut:]
            else:
                work, buf = buf, ""
            for m in _TOKEN_RE.finditer(work):
                tok = m.group(0)
                if not tok.startswith("<"):
                    fo.write(tok)
                    ws = tok if not tok.strip() else ""
                    continue
                if tok.startswith("<?") or tok.startswith("<!"):
                    fo.write(tok)
                    ws = ""
                    continue
                name = tag_name(tok)
                closing = tok.startswith("</")
                nl = "\n" + indent(ws) if "\n" in ws else ws
                if not closing:
                    parent = stack[-1] if stack else ""
                    if name in ("SRSId", "SpatialReferenceSystems"):
                        raise ValueError("XML 里已经有 <%s>，要先按 D-10 去掉坐标系" % name)
                    if need is not None and len(stack) == need and name != "Name":
                        fo.write("<SRSId>%s</SRSId>" % BX_LOCAL_SRS_ID + nl)
                        n += 1
                        need = None
                    if name == "Block" and parent == "BlocksExchange":
                        fo.write("<SpatialReferenceSystems>" + nl + "  <SRS>" + nl + "    <Id>%s</Id>" % BX_LOCAL_SRS_ID
                                 + nl + "    <Name>%s</Name>" % survey_srs_name(epsg)
                                 + nl + "    <Definition>EPSG:%d</Definition>" % epsg
                                 + nl + "  </SRS>" + nl + "</SpatialReferenceSystems>" + nl)
                    fo.write(tok)
                    stack.append(name)
                    if name == "Block" and not tok.endswith("/>"):
                        need = len(stack)
                    if tok.endswith("/>"):
                        stack.pop()
                else:
                    if need is not None and len(stack) == need:
                        raise ValueError("<Block> 里没有可以挂 <SRSId> 的位置")
                    fo.write(tok)
                    if stack:
                        stack.pop()
                ws = ""
            if not chunk:
                break
    return n


def check_bx_epsg(xml_path, epsg, expected_photos, ref_mean=None, tol=200.0):
    """T9 XML 自检：只有一个 <SRS>（Id 1、Name、Definition = EPSG:<号>，不是 WKT），<Block> 下 1 处 <SRSId>1</SRSId>、
    别处没有；<Pose> 下没有 <Metadata>；<Photo> 数 = 对齐数、ImagePath 都在；照片中心是投影坐标：东坐标在
    （不带带号的）3 度带范围里，平均值和相机参考（GPS 换算后）的平均值相差 < tol 米。返回 (问题列表, 信息)。"""
    problems = []
    srs, block_ids, other_ids, pose_meta, photos, missing = [], [], 0, 0, 0, []
    cx = cy = cz = 0.0
    nc = 0
    bad_e = 0
    stack = []
    base = Path(xml_path).parent
    try:
        for event, el in ET.iterparse(str(xml_path), events=("start", "end")):
            if event == "start":
                if el.tag == "Metadata" and stack and stack[-1] == "Pose":
                    pose_meta += 1
                stack.append(el.tag)
                continue
            stack.pop()
            parent = stack[-1] if stack else ""
            if el.tag == "SRSId":
                if parent == "Block":
                    block_ids.append((el.text or "").strip())
                else:
                    other_ids += 1
            elif el.tag == "SRS":
                srs.append((parent, el.findtext("Id"), el.findtext("Name"), el.findtext("Definition"), len(list(el))))
            elif el.tag == "ImagePath":
                p = Path(el.text or "")
                if not p.is_absolute():
                    p = base / p
                if not p.is_file():
                    missing.append(str(p))
            elif el.tag == "Center" and parent == "Pose":
                try:
                    x, y, z = (float(el.findtext(k)) for k in ("x", "y", "z"))
                except (TypeError, ValueError):
                    problems.append("有照片的 <Center> 不是数字")
                    continue
                cx, cy, cz, nc = cx + x, cy + y, cz + z, nc + 1
                if not 300000 < x < 700000:
                    bad_e += 1
            elif el.tag == "Photo":
                photos += 1
                el.clear()
            elif el.tag == "TiePoint":
                el.clear()
    except ET.ParseError as ex:
        return ["XML 解析失败：%s" % ex], {}
    want = ("SpatialReferenceSystems", BX_LOCAL_SRS_ID, survey_srs_name(epsg), "EPSG:%d" % epsg, 3)
    if srs != [want]:
        problems.append("<SRS> 应该只有一个且是 %s，实际 %s" % (want[1:4], [s[1:4] for s in srs]))
    if block_ids != [BX_LOCAL_SRS_ID]:
        problems.append("<Block> 下的 <SRSId> 应该是 1 处「%s」，实际 %s" % (BX_LOCAL_SRS_ID, block_ids))
    if other_ids:
        problems.append("<Block> 以外还有 %d 处 <SRSId>" % other_ids)
    if pose_meta:
        problems.append("<Pose> 下还有 %d 个 <Metadata>" % pose_meta)
    if photos != expected_photos:
        problems.append("<Photo> 有 %d 个，和对齐数 %d 不一致" % (photos, expected_photos))
    if nc != photos:
        problems.append("有 %d 张照片没有 <Pose>/<Center>" % (photos - nc))
    if missing:
        problems.append("%d 个 ImagePath 指向的文件不存在（如 %s）" % (len(missing), missing[0]))
    if bad_e:
        problems.append("%d 张照片的东坐标不在 3 度带（不带带号）范围里，可能没换算成投影坐标" % bad_e)
    mean = [cx / nc, cy / nc, cz / nc] if nc else None
    if mean and ref_mean:
        d = ((mean[0] - ref_mean[0]) ** 2 + (mean[1] - ref_mean[1]) ** 2) ** 0.5
        if d > tol:
            problems.append("照片中心平均值和 GPS 换算后的平均值差 %.0f 米（> %g 米），坐标可能没换算对" % (d, tol))
    return problems, {"photos": photos, "center_mean": mean, "missing": len(missing)}


GEOTIFF_TYPES = {1: "B", 2: "s", 3: "H", 4: "I", 5: "II", 6: "b", 7: "B", 8: "h", 9: "i", 10: "ii", 11: "f", 12: "d",
                 16: "Q", 17: "q", 18: "Q"}


def geotiff_info(path):
    """只用标准库读 GeoTIFF 第一张图的头（普通 TIFF 和 BigTIFF）：宽高、波段、位深、数据类型、压缩、像素大小、左上角、
    投影 EPSG（GeoKey 3072）、NoData（GDAL 的 42113 标签）。读不了抛 ValueError。"""
    with open(path, "rb") as f:
        h = f.read(16)
        e = "<" if h[:2] == b"II" else ">" if h[:2] == b"MM" else None
        if e is None:
            raise ValueError("不是 TIFF：%s" % path)
        magic = struct.unpack(e + "H", h[2:4])[0]
        big = magic == 43
        if magic not in (42, 43):
            raise ValueError("不是 TIFF：%s" % path)
        off = struct.unpack(e + "Q", h[8:16])[0] if big else struct.unpack(e + "I", h[4:8])[0]
        f.seek(off)
        n = struct.unpack(e + ("Q" if big else "H"), f.read(8 if big else 2))[0]
        esz = 20 if big else 12
        raw = f.read(n * esz)
        tags = {}
        for k in range(n):
            ent = raw[k * esz:(k + 1) * esz]
            if big:
                tag, typ, cnt = struct.unpack(e + "HHQ", ent[:12])
                vbytes = ent[12:20]
            else:
                tag, typ, cnt = struct.unpack(e + "HHI", ent[:8])
                vbytes = ent[8:12]
            fmt = GEOTIFF_TYPES.get(typ)
            if fmt is None:
                continue
            size = struct.calcsize(e + fmt) * cnt
            if size > len(vbytes):
                f.seek(struct.unpack(e + ("Q" if big else "I"), vbytes)[0])
                data = f.read(size)
            else:
                data = vbytes[:size]
            if typ == 2:
                tags[tag] = data.rstrip(b"\x00").decode("latin-1")
            else:
                tags[tag] = list(struct.unpack(e + fmt * cnt, data))
    geokeys = {}
    gk = tags.get(34735) or []
    for k in range(4, len(gk) - 3, 4):
        key, loc, cnt, val = gk[k:k + 4]
        if loc == 0:
            geokeys[key] = val
    sfmt = (tags.get(339) or [1])[0]
    bits = (tags.get(258) or [0])[0]
    dtype = {(1, 8): "uint8", (1, 16): "uint16", (2, 16): "int16", (3, 32): "float32", (3, 64): "float64",
             (2, 32): "int32", (1, 32): "uint32"}.get((sfmt, bits), "sfmt%d/%dbit" % (sfmt, bits))
    scale = tags.get(33550) or [None, None]
    tie = tags.get(33922) or [None] * 6
    nod = tags.get(42113)
    try:
        nod = float(nod) if nod not in (None, "") else None
    except ValueError:
        pass
    return {"width": (tags.get(256) or [0])[0], "height": (tags.get(257) or [0])[0], "bands": (tags.get(277) or [1])[0],
            "dtype": dtype, "compression": (tags.get(259) or [1])[0], "bigtiff": big, "res": scale[:2],
            "origin": (tie[3], tie[4]), "epsg": geokeys.get(3072), "nodata": nod, "extra_samples": tags.get(338)}


def read_tfw(path):
    vals = [float(x) for x in Path(path).read_text(encoding="ascii", errors="replace").split()]
    if len(vals) != 6:
        raise ValueError("TFW 应该 6 行数字：%s" % path)
    return vals


def check_survey_rasters(dom, dsm, epsg, resolution):
    """DOM/DSM 自检：文件和 .tfw 都在；坐标系 EPSG 对；分辨率 = 设定值（0 = 自动时不查）；DOM 4 波段 uint8（RGBA）、
    DSM 1 波段 float32、NoData −9999；Deflate 压缩；.tfw 的像素大小和 GeoTIFF 一致。返回 (问题列表, {dom: 信息, dsm: 信息})。"""
    problems, info = [], {}
    for key, path, bands, dtype in (("DOM", dom, 4, "uint8"), ("DSM", dsm, 1, "float32")):
        p = Path(path)
        tfw = p.with_suffix(".tfw")
        if not p.is_file():
            problems.append("没有 %s：%s" % (key, p.name))
            continue
        if not tfw.is_file():
            problems.append("%s 缺 .tfw：%s" % (key, tfw.name))
        try:
            g = geotiff_info(p)
        except (ValueError, OSError, struct.error) as ex:
            problems.append("%s 读不了：%s" % (key, ex))
            continue
        g["size"] = p.stat().st_size
        info[key] = g
        if g["epsg"] != epsg:
            problems.append("%s 坐标系是 EPSG:%s，应该是 EPSG:%d" % (key, g["epsg"], epsg))
        if g["bands"] != bands or g["dtype"] != dtype:
            problems.append("%s 是 %d 波段 %s，应该是 %d 波段 %s" % (key, g["bands"], g["dtype"], bands, dtype))
        if g["compression"] not in (8, 32946):
            problems.append("%s 压缩方式是 %s，应该是 Deflate" % (key, g["compression"]))
        if resolution and (g["res"][0] is None or abs(g["res"][0] - resolution) > 1e-6 or abs(g["res"][1] - resolution) > 1e-6):
            problems.append("%s 分辨率是 %s，应该是 %g 米" % (key, g["res"], resolution))
        if key == "DSM" and g["nodata"] != SURVEY_NODATA:
            problems.append("DSM 的 NoData 是 %s，应该是 %g" % (g["nodata"], SURVEY_NODATA))
        if tfw.is_file():
            try:
                t = read_tfw(tfw)
                g["tfw"] = t
                if g["res"][0] is not None and (abs(t[0] - g["res"][0]) > 1e-6 or abs(-t[3] - g["res"][1]) > 1e-6):
                    problems.append("%s 的 .tfw 像素大小 %s 和 GeoTIFF %s 不一致" % (key, (t[0], t[3]), g["res"]))
            except (ValueError, OSError) as ex:
                problems.append("%s 的 .tfw 读不了：%s" % (key, ex))
    return problems, info


def check_survey_raster_pair(info):
    """DOM 和 DSM 的外框、像元网格必须完全一致（后面算方量要用）。返回问题列表。"""
    a, b = info.get("DOM"), info.get("DSM")
    if not a or not b:
        return []
    problems = []
    if (a["width"], a["height"]) != (b["width"], b["height"]):
        problems.append("DOM 是 %d × %d 像元，DSM 是 %d × %d 像元，行列数不一样（算方量会错）" % (
            a["width"], a["height"], b["width"], b["height"]))
    ta, tb = a.get("tfw"), b.get("tfw")
    if ta and tb:
        if abs(ta[4] - tb[4]) > 1e-6 or abs(ta[5] - tb[5]) > 1e-6:
            problems.append("DOM 的 .tfw 左上角 (%.6f, %.6f) 和 DSM 的 (%.6f, %.6f) 不一致" % (
                ta[4], ta[5], tb[4], tb[5]))
        if abs(ta[0] - tb[0]) > 1e-6 or abs(ta[3] - tb[3]) > 1e-6:
            problems.append("DOM 和 DSM 的像元大小不一致：%.9f / %.9f" % (ta[0], tb[0]))
    elif a["origin"] != b["origin"] or a["res"] != b["res"]:
        problems.append("DOM 和 DSM 的左上角或像元大小不一致：%r %r / %r %r" % (
            a["origin"], a["res"], b["origin"], b["res"]))
    return problems


# ---------------------------------------------------------------------------
# DOM/DSM 自动裁边（D-31/T11 的范围线文件、两趟导出、用户线优先；D-32/T12 换了「有效区域怎么算」）
# 做法（🔒 D-32）：0.5 米分析网格上同时满足 ①没裁的 DOM alpha>0 的像元 ≥ 90% ②不插值辅助 DSM（0.1 米）有真实点
# ③1 米块里有点的 0.1 米子格 ≥ 点云密度门槛 的格子 → 补缝（扩 1 m 收 1 m）→ 最大 8 邻接连片 → 填内部 < 100 m² 的洞 →
# 往里收 N 米 → 折线简化（相邻点 ≥ 1 m）＝范围线；DOM、DSM 用同一条线、同一个像元网格裁（线外 DOM alpha=0 / DSM NoData），
# 外框 = 范围线外接矩形外扩 5 个像元。与飞法无关。辅助 DSM 在一个读写打开但从不保存的 Metashape 会话里建（只读打开建不上），
# 正式 DSM 不动；网格运算全部用标准库自己写（每行一个大整数做形态学，字节当计数器数点）。
# 只有「按线重新导出」这一步借 Metashape 的外边界形状（1.7.2：shape.vertices / boundary_type；内环用 InnerBoundary 挖掉）。

# ---------------------------------------------------------------------------
def survey_clip_inset(text):
    """解析「往里收」：0～30 米。"""
    t = str(text).strip().replace("，", ".")
    try:
        v = float(t)
    except ValueError:
        raise ValueError("「往里收」要填 %g～%g 之间的米数，不能是「%s」" % (SURVEY_CLIP_INSET_MIN,
                                                                 SURVEY_CLIP_INSET_MAX, text))
    if not (SURVEY_CLIP_INSET_MIN <= v <= SURVEY_CLIP_INSET_MAX):
        raise ValueError("「往里收」只能填 %g～%g 米（现在是 %g）" % (SURVEY_CLIP_INSET_MIN, SURVEY_CLIP_INSET_MAX, v))
    return v


def parse_survey_clip(text):
    """--clip：auto（默认，按 D-32 三条判据自动算）/ none（不裁，同 v1.0）/ 范围线文件（.kml 或 .shp）。
    返回 ("auto", None) / ("none", None) / ("file", 绝对路径)。"""
    t = (text or "").strip().strip('"')
    if not t or t.lower() == SURVEY_CLIP_AUTO or t.startswith("自动"):
        return SURVEY_CLIP_AUTO, None
    if t.lower() == SURVEY_CLIP_NONE or t.startswith("不裁"):
        return SURVEY_CLIP_NONE, None
    p = Path(os.path.abspath(t))
    if not p.is_file():
        raise ValueError("找不到范围线文件：%s" % p)
    if p.suffix.lower() not in (".kml", ".shp"):
        raise ValueError("范围线只认 .kml 或 .shp（点、线文件不行）：%s" % p)
    return "file", str(p)


# ---- CGCS2000 3 度带高斯投影正/反算（写 KML 用反算，读 KML 用正算；只用标准库） ----
_GK_A = 6378137.0                     # CGCS2000 长半轴
_GK_F = 1 / 298.257222101             # CGCS2000 扁率


def _gk_coeffs():
    """Krüger 级数系数（EPSG Guidance Note 7-2 的 α/β/δ），CGCS2000 椭球。"""
    n = _GK_F / (2 - _GK_F)
    big = _GK_A / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64 + n ** 6 / 256)
    al = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180 - 127 * n ** 5 / 288 + 7891 * n ** 6 / 37800,
          13 * n ** 2 / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440 + 281 * n ** 5 / 630 - 1983433 * n ** 6 / 1935360,
          61 * n ** 3 / 240 - 103 * n ** 4 / 140 + 15061 * n ** 5 / 26880 + 167603 * n ** 6 / 181440,
          49561 * n ** 4 / 161280 - 179 * n ** 5 / 168 + 6601661 * n ** 6 / 7257600,
          34729 * n ** 5 / 80640 - 3418889 * n ** 6 / 1995840,
          212378941 * n ** 6 / 319334400)
    be = (n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96 - n ** 4 / 360 - 81 * n ** 5 / 512 + 96199 * n ** 6 / 604800,
          n ** 2 / 48 + n ** 3 / 15 - 437 * n ** 4 / 1440 + 46 * n ** 5 / 105 - 1118711 * n ** 6 / 3870720,
          17 * n ** 3 / 480 - 37 * n ** 4 / 840 - 209 * n ** 5 / 4480 + 5569 * n ** 6 / 90720,
          4397 * n ** 4 / 161280 - 11 * n ** 5 / 504 - 830251 * n ** 6 / 7257600,
          4583 * n ** 5 / 161280 - 108847 * n ** 6 / 3991680,
          20648693 * n ** 6 / 638668800)
    de = (2 * n - 2 * n ** 2 / 3 - 2 * n ** 3 + 116 * n ** 4 / 45 + 26 * n ** 5 / 45 - 2854 * n ** 6 / 675,
          7 * n ** 2 / 3 - 8 * n ** 3 / 5 - 227 * n ** 4 / 45 + 2704 * n ** 5 / 315 + 2323 * n ** 6 / 945,
          56 * n ** 3 / 15 - 136 * n ** 4 / 35 - 1262 * n ** 5 / 105 + 73814 * n ** 6 / 2835,
          4279 * n ** 4 / 630 - 332 * n ** 5 / 35 - 399572 * n ** 6 / 14175,
          4174 * n ** 5 / 315 - 144838 * n ** 6 / 6237,
          601676 * n ** 6 / 22275)
    return n, big, al, be, de


_GK_N, _GK_BIG, _GK_ALPHA, _GK_BETA, _GK_DELTA = _gk_coeffs()


def survey_tm_forward(lon, lat, epsg):
    """经纬度（度）→ 该 CGCS2000 3 度带的东坐标、北坐标（米，东坐标不带带号）。"""
    lam0 = math.radians(survey_cm(epsg))
    phi, lam = math.radians(lat), math.radians(lon)
    s = math.sinh(math.atanh(math.sin(phi)) - 2 * math.sqrt(_GK_N) / (1 + _GK_N) *
                  math.atanh(2 * math.sqrt(_GK_N) / (1 + _GK_N) * math.sin(phi)))
    xi = math.atan2(s, math.cos(lam - lam0))
    eta = math.atanh(math.sin(lam - lam0) / math.sqrt(1 + s * s))
    x = _GK_BIG * (eta + sum(_GK_ALPHA[j - 1] * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j in range(1, 7)))
    y = _GK_BIG * (xi + sum(_GK_ALPHA[j - 1] * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j in range(1, 7)))
    return 500000.0 + x, y


def survey_tm_inverse(x, y, epsg):
    """东坐标、北坐标（米，东坐标不带带号）→ 经纬度（度）。WGS84 与 CGCS2000 差厘米级，KML 里直接当 WGS84。"""
    return _tm_inverse_cm(x, y, survey_cm(epsg))


def _tm_inverse_cm(x, y, cm):
    """同 survey_tm_inverse，中央经线直接给（度）。"""
    xi0 = y / _GK_BIG
    eta0 = (x - 500000.0) / _GK_BIG
    xi = xi0 - sum(_GK_BETA[j - 1] * math.sin(2 * j * xi0) * math.cosh(2 * j * eta0) for j in range(1, 7))
    eta = eta0 - sum(_GK_BETA[j - 1] * math.cos(2 * j * xi0) * math.sinh(2 * j * eta0) for j in range(1, 7))
    chi = math.asin(math.sin(xi) / math.cosh(eta))
    phi = chi + sum(_GK_DELTA[j - 1] * math.sin(2 * j * chi) for j in range(1, 7))
    lam = math.radians(cm) + math.atan2(math.sinh(eta), math.cos(xi))
    return math.degrees(lam), math.degrees(phi)


# ---- 读条带式 GeoTIFF（本软件导出的都是条带式、Deflate 压缩；只用标准库） ----
def tiff_strip_info(path):
    """读 GeoTIFF 的结构：宽高、波段、位深、压缩、每像素字节、NoData、条带偏移/字节数、左上角、像素大小。
    只支持条带式（分块式抛 ValueError）；压缩支持不压缩和 Deflate（8 / 32946）。读不了抛 ValueError。"""
    p = Path(path)
    with open(p, "rb") as f:
        h = f.read(16)
        if len(h) < 16:
            raise ValueError("不是 TIFF：%s" % p)
        e = "<" if h[:2] == b"II" else ">" if h[:2] == b"MM" else None
        if e is None:
            raise ValueError("不是 TIFF：%s" % p)
        magic = struct.unpack(e + "H", h[2:4])[0]
        big = magic == 43
        if magic not in (42, 43):
            raise ValueError("不是 TIFF：%s" % p)
        off = struct.unpack(e + "Q", h[8:16])[0] if big else struct.unpack(e + "I", h[4:8])[0]
        f.seek(off)
        n = struct.unpack(e + ("Q" if big else "H"), f.read(8 if big else 2))[0]
        esz = 20 if big else 12
        raw = f.read(n * esz)
        tags = {}
        for k in range(n):
            ent = raw[k * esz:(k + 1) * esz]
            if len(ent) < esz:
                break
            if big:
                tag, typ, cnt = struct.unpack(e + "HHQ", ent[:12])
                vb = ent[12:20]
            else:
                tag, typ, cnt = struct.unpack(e + "HHI", ent[:8])
                vb = ent[8:12]
            fmt = GEOTIFF_TYPES.get(typ)
            if fmt is None:
                continue
            size = struct.calcsize(e + fmt) * cnt
            if size > len(vb):
                f.seek(struct.unpack(e + ("Q" if big else "I"), vb)[0])
                data = f.read(size)
            else:
                data = vb[:size]
            if typ == 2:
                tags[tag] = data.rstrip(b"\x00").decode("latin-1")
            else:
                tags[tag] = list(struct.unpack(e + fmt * cnt, data))
    if tags.get(322):
        raise ValueError("这是分块（tile）式 TIFF，读不了（本软件导出的是条带式）：%s" % p)
    comp = (tags.get(259) or [1])[0]
    if comp not in (1, 8, 32946):
        raise ValueError("压缩方式是 %s，只认 Deflate 或不压缩：%s" % (comp, p))
    bits = (tags.get(258) or [0])[0]
    sfmt = (tags.get(339) or [1])[0]
    bands = (tags.get(277) or [1])[0]
    bps = bits // 8 if bits % 8 == 0 else 0
    scale = tags.get(33550) or [None, None, None]
    tie = tags.get(33922) or [None] * 6
    nod = tags.get(42113)
    nodf = None
    if nod not in (None, ""):
        try:
            nodf = float(nod)
        except ValueError:
            nodf = None
    return {"path": str(p), "width": (tags.get(256) or [0])[0], "height": (tags.get(257) or [0])[0],
            "bands": bands, "bits": bits, "bytes_per_pixel": bps * bands,
            "dtype": {(1, 8): "uint8", (1, 16): "uint16", (2, 16): "int16", (3, 32): "float32", (3, 64): "float64",
                      (2, 32): "int32", (1, 32): "uint32"}.get((sfmt, bits), "sfmt%d/%dbit" % (sfmt, bits)),
            "sample_format": sfmt, "compression": comp, "bigtiff": big, "endian": e,
            "rows_per_strip": (tags.get(278) or [0])[0] or (tags.get(257) or [0])[0],
            "strip_offsets": tags.get(273) or [], "strip_bytes": tags.get(279) or [],
            "px": (scale[0], scale[1]), "origin": (tie[3], tie[4]), "nodata": nodf,
            "nodata_bytes": (struct.pack(e + "f", nodf) if nodf is not None and bits == 32 else None)}


def iter_tiff_rows(path, info, step=1):
    """逐条带解压，交出 (行号, 该行原始字节)；只交出行号是 step 整数倍的行（解压照样做）。"""
    w, h = info["width"], info["height"]
    bpp = info["bytes_per_pixel"]
    rps = info["rows_per_strip"]
    if bpp <= 0:
        raise ValueError("看不懂的位深：%s（%d 位 %d 波段）" % (info["path"], info["bits"], info["bands"]))
    if rps > 1024:
        raise ValueError("每条约带 %d 行，太多（内存吃不消）；这不是本软件导出的 TIFF：%s" % (rps, info["path"]))
    rowbytes = w * bpp
    offs, cnts = info["strip_offsets"], info["strip_bytes"]
    with open(path, "rb") as f:
        for si in range(len(offs)):
            r0 = si * rps
            if r0 >= h:
                break
            r1 = min(r0 + rps, h)
            first = r0 + (step - r0 % step) % step
            if first >= r1:
                continue
            f.seek(offs[si])
            raw = f.read(cnts[si])
            if info["compression"] in (8, 32946):
                try:
                    raw = zlib.decompress(raw)
                except zlib.error as ex:
                    raise ValueError("解压第 %d 条带失败（%s）：%s" % (si, ex, info["path"]))
            if len(raw) < (r1 - r0) * rowbytes:
                raise ValueError("第 %d 条带只有 %d 字节，不够 %d 行：%s" % (si, len(raw), r1 - r0, info["path"]))
            for r in range(first, r1, step):
                o = (r - r0) * rowbytes
                yield r, raw[o:o + rowbytes]


def survey_clip_density(text):
    """解析「点云密度门槛」：10～90（%）。"""
    t = str(text).strip().replace("，", ".").rstrip("%").strip()
    try:
        v = float(t)
    except ValueError:
        raise ValueError("「点云密度门槛」要填 %g～%g 之间的百分数，不能是「%s」" % (
            SURVEY_CLIP_DENSITY_MIN, SURVEY_CLIP_DENSITY_MAX, text))
    if not (SURVEY_CLIP_DENSITY_MIN <= v <= SURVEY_CLIP_DENSITY_MAX):
        raise ValueError("「点云密度门槛」只能填 %g～%g（%%，现在是 %g）" % (
            SURVEY_CLIP_DENSITY_MIN, SURVEY_CLIP_DENSITY_MAX, v))
    return v


def survey_clip_grid(width, height, px, cell=SURVEY_CLIP_GRID):
    """没裁的 DOM/DSM（width × height 像元、像素 px 米）对应的分析网格：列数、行数（偶数，凑整 1 米块）。"""
    nx = int(math.ceil(width * px / cell - 1e-9))
    ny = int(math.ceil(height * px / cell - 1e-9))
    return nx + nx % 2, ny + ny % 2


def _grid_bounds(n_px, px, cell, n_cells):
    """每格包含哪些像元（按像元中心落在哪一格）：返回 n_cells+1 个边界像元号。"""
    return [min(n_px, max(0, int(math.ceil(k * cell / px - 0.5 - 1e-9)))) for k in range(n_cells + 1)]


_ZERO_RUN = re.compile(b"\x00+")


def survey_dom_coverage(dom_path, nx, ny, cell=SURVEY_CLIP_GRID, log=None):
    """判据①：读没裁的 DOM（RGBA，条带式），每个分析格里 alpha > 0 的像元占比 ≥ SURVEY_CLIP_DOM_FRAC 记 1。
    网格原点 = DOM 左上角。逐行读、按透明像元的连续段记账（不一次载入整幅）。返回 bytearray（nx × ny，行向下）。"""
    log = log or (lambda s: None)
    info = tiff_strip_info(dom_path)
    if info["bands"] != 4 or info["dtype"] != "uint8":
        raise ValueError("DOM 应该是 4 波段 uint8（RGBA），这个是 %d 波段 %s：%s" % (info["bands"], info["dtype"], dom_path))
    w, h, px = info["width"], info["height"], info["px"][0]
    xb, yb = _grid_bounds(w, px, cell, nx), _grid_bounds(h, px, cell, ny)
    widths = [xb[k + 1] - xb[k] for k in range(nx)]
    col_cell = [0] * w
    for k in range(nx):
        for i in range(xb[k], xb[k + 1]):
            col_cell[i] = k
    row_cell = [0] * h
    for k in range(ny):
        for j in range(yb[k], yb[k + 1]):
            row_cell[j] = k
    ok = bytearray(nx * ny)
    t0 = time.time()
    cur, nrow, acc = -1, 0, [0] * nx

    def flush(cy):
        base = cy * nx
        for k in range(nx):
            tot = nrow * widths[k]
            if tot and acc[k] >= SURVEY_CLIP_DOM_FRAC * tot:
                ok[base + k] = 1

    add = int.__add__
    for r, line in iter_tiff_rows(dom_path, info):
        cy = row_cell[r]
        if cy != cur:
            if cur >= 0:
                flush(cur)
            cur, nrow, acc = cy, 0, [0] * nx
        a = line[3::4]
        z = a.count(0)
        if z == 0:
            acc = list(map(add, acc, widths))
        elif z < w:
            row = list(widths)
            for m in _ZERO_RUN.finditer(a):
                s, e = m.span()
                k0, k1 = col_cell[s], col_cell[e - 1]
                if k0 == k1:
                    row[k0] -= e - s
                else:
                    row[k0] -= xb[k0 + 1] - s
                    for k in range(k0 + 1, k1):
                        row[k] = 0
                    row[k1] -= e - xb[k1]
            acc = list(map(add, acc, row))
        nrow += 1
    if cur >= 0:
        flush(cur)
    log("判据① 影像有内容：读没裁的 DOM %d × %d 像元，%d × %d 个 %g 米格，%d 格达标（每格 alpha > 0 的像元 ≥ %d%%），用 %.0f 秒" % (
        w, h, nx, ny, cell, ok.count(1), int(SURVEY_CLIP_DOM_FRAC * 100), time.time() - t0))
    return ok


def survey_aux_counts(aux_path, nx, ny, log=None):
    """判据②③ 的原始数：读不插值辅助 DSM（0.1 米、原点 = 分析网格原点、不压缩或 Deflate 条带式、NoData 已知），
    返回 (cnt, blk)：cnt[每个 0.5 米格] = 有值的 0.1 米子格数（0～25）；blk[每个 1 米块] = 有值的子格数（0～100）。
    逐行读；每行用「字节当计数器」的大整数加法一次数完（每个字节 ≤ 100，不会进位）。"""
    log = log or (lambda s: None)
    info = tiff_strip_info(aux_path)
    if info["dtype"] != "float32" or info["bands"] != 1:
        raise ValueError("辅助 DSM 应该是 1 波段 float32：%s" % aux_path)
    pat = info["nodata_bytes"]
    if pat is None:
        raise ValueError("辅助 DSM 里没有 NoData 值：%s" % aux_path)
    sub = int(round(SURVEY_CLIP_GRID / SURVEY_CLIP_AUX_RES))
    if abs(info["px"][0] - SURVEY_CLIP_AUX_RES) > 1e-6:
        raise ValueError("辅助 DSM 的像素是 %s 米，应该是 %g 米：%s" % (info["px"][0], SURVEY_CLIP_AUX_RES, aux_path))
    w, h = info["width"], info["height"]
    wp = nx * sub
    use = min(w, wp)
    tabs = []
    for b in pat:
        t = bytearray(256)
        t[b] = 1
        tabs.append(bytes(t))
    ones = int.from_bytes(b"\x01" * use, "big")
    cnt = bytearray(nx * ny)
    acc, t0 = 0, time.time()
    cur = -1
    for r, line in iter_tiff_rows(aux_path, info):
        cy = r // sub
        if cy >= ny:
            break
        if cy != cur:
            if cur >= 0:
                cnt[cur * nx:(cur + 1) * nx] = acc.to_bytes(nx, "big")
            cur, acc = cy, 0
        line = line[:use * 4]
        nod = (int.from_bytes(line[0::4].translate(tabs[0]), "big") & int.from_bytes(line[1::4].translate(tabs[1]), "big")
               & int.from_bytes(line[2::4].translate(tabs[2]), "big") & int.from_bytes(line[3::4].translate(tabs[3]), "big"))
        v = (nod ^ ones).to_bytes(use, "big") + bytes(wp - use)
        for k in range(sub):
            acc += int.from_bytes(v[k::sub], "big")
    if cur >= 0:
        cnt[cur * nx:(cur + 1) * nx] = acc.to_bytes(nx, "big")
    bx, by = nx // 2, ny // 2
    blk = bytearray(bx * by)
    for j in range(by):
        s = (int.from_bytes(cnt[2 * j * nx:(2 * j + 1) * nx], "big") +
             int.from_bytes(cnt[(2 * j + 1) * nx:(2 * j + 2) * nx], "big")).to_bytes(nx, "big")
        blk[j * bx:(j + 1) * bx] = (int.from_bytes(s[0::2], "big") + int.from_bytes(s[1::2], "big")).to_bytes(bx, "big")
    log("判据②③ 真实点云：读不插值辅助 DSM %d × %d 个 %g 米子格（有值 %.1f%%），用 %.0f 秒" % (
        w, h, SURVEY_CLIP_AUX_RES, 100.0 * sum(cnt) / max(1, w * h), time.time() - t0))
    return cnt, blk


def survey_write_analysis(path, an):
    """分析网格存成 gzip 的 JSON（字节数组用 base64）。"""
    d = dict((k, v) for k, v in an.items() if k not in ("dom_ok", "cnt", "blk"))
    for k in ("dom_ok", "cnt", "blk"):
        d[k] = base64.b64encode(bytes(an[k])).decode("ascii")
    tmp = Path(str(path) + ".tmp")
    with gzip.open(str(tmp), "wt", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(str(tmp), str(path))


def survey_read_analysis(path):
    with gzip.open(str(path), "rt", encoding="utf-8") as f:
        d = json.load(f)
    for k in ("dom_ok", "cnt", "blk"):
        d[k] = bytearray(base64.b64decode(d[k]))
    nx, ny = d["nx"], d["ny"]
    if len(d["dom_ok"]) != nx * ny or len(d["cnt"]) != nx * ny or len(d["blk"]) != (nx // 2) * (ny // 2):
        raise ValueError("裁边分析文件大小对不上：%s" % path)
    return d


# ---- 分析网格上的形态学运算：每行存成一个大整数（第 x 位 = 第 x 列），扩/收按圆盘做 ----
_B01 = bytes.maketrans(b"\x00\x01", b"01")
_B10 = bytes.maketrans(b"01", b"\x00\x01")


def _mask_rows(mask, nx, ny):
    rows = []
    for y in range(ny):
        s = bytes(mask[y * nx:(y + 1) * nx]).translate(_B01)
        rows.append(int(s[::-1], 2) if b"1" in s else 0)
    return rows


def _rows_mask(rows, nx):
    out = bytearray()
    zero = bytes(nx)
    for v in rows:
        out += format(v, "0%db" % nx)[::-1].encode("ascii").translate(_B10) if v else zero
    return out


def _disk_widths(radius, strict):
    """圆盘每一行（dy = 0, 1, …）的半宽：dx² + dy² <（strict）或 ≤ radius² 的最大 dx。"""
    out, r2 = [], radius * radius
    dy = 0
    while True:
        w = int(math.floor(math.sqrt(max(0.0, r2 - dy * dy)))) + 1
        while w >= 0 and (w * w + dy * dy >= r2 if strict else w * w + dy * dy > r2 + 1e-9):
            w -= 1
        if w < 0:
            return out
        out.append(w)
        dy += 1


def _hdilate(v, w, full):
    span, cur = 0, v
    while span < w:
        st = min(span + 1, w - span)
        cur = cur | (cur << st) | (cur >> st)
        span += st
    return cur & full


def _dilate_rows(rows, nx, radius, strict=False):
    """往外扩：格子到最近的 1 格（中心距）< / ≤ radius 格就置 1（网格外不算）。"""
    ws = _disk_widths(radius, strict)
    if not ws:
        return [0] * len(rows)
    full = (1 << nx) - 1
    hs = dict((w, [_hdilate(v, w, full) if v else 0 for v in rows]) for w in set(ws))
    ny, out = len(rows), [0] * len(rows)
    for y in range(ny):
        acc = 0
        for dy, w in enumerate(ws):
            hr = hs[w]
            if y - dy >= 0:
                acc |= hr[y - dy]
            if dy and y + dy < ny:
                acc |= hr[y + dy]
        out[y] = acc
    return out


def _erode_rows(rows, nx, radius, strict=False):
    """往里收：离最近的 0 格（中心距）< / ≤ radius 格就清掉；网格外当 0（贴着网格边的也往里收）。"""
    ws = _disk_widths(radius, strict)
    if not ws:
        return list(rows)
    pad = max(len(ws), max(ws)) + 1
    wide = nx + 2 * pad
    full = (1 << wide) - 1
    comp = [full] * pad + [~(v << pad) & full for v in rows] + [full] * pad
    d = _dilate_rows(comp, wide, radius, strict)
    keep = (1 << nx) - 1
    return [((~d[y + pad] & full) >> pad) & keep for y in range(len(rows))]


def _fix_pinches(rows, nx):
    """两个格子只在角上相碰（2 × 2 里对角两个 1）时把旁边一格补上，让外环、内环都不在一个点上自相接触。"""
    keep = (1 << nx) - 1
    changed, guard = True, 0
    while changed and guard < 50:
        changed, guard = False, guard + 1
        for y in range(len(rows) - 1):
            a, b = rows[y], rows[y + 1]
            p1 = a & (b >> 1) & ~(a >> 1) & ~b          # a[x]、b[x+1] 是 1，a[x+1]、b[x] 是 0 → 补 a[x+1]
            p2 = (a >> 1) & b & ~a & ~(b >> 1)          # a[x+1]、b[x] 是 1，a[x]、b[x+1] 是 0 → 补 a[x]
            if p1 or p2:
                rows[y] = (a | (p1 << 1) | p2) & keep
                changed = True
    return rows


def _mask_runs(mask, nx, ny):
    """按行找 mask 里为 1 的连续段：[(行号, 起列, 止列（不含）)]。"""
    runs = []
    one = re.compile(b"\x01+")
    for y in range(ny):
        row = bytes(mask[y * nx:(y + 1) * nx])
        if b"\x01" in row:
            runs.extend((y, m.start(), m.end()) for m in one.finditer(row))
    return runs


def _runs_components(runs, ny):
    """8 邻接把连续段并成连片（并查集），返回 (每段的根, {根: 像元数})。"""
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    byrow = {}
    for i, (y, c0, c1) in enumerate(runs):
        byrow.setdefault(y, []).append(i)
    for y in range(1, ny):
        a, b = byrow.get(y - 1, []), byrow.get(y, [])
        i = j = 0
        while i < len(a) and j < len(b):
            a0, a1 = runs[a[i]][1], runs[a[i]][2]
            b0, b1 = runs[b[j]][1], runs[b[j]][2]
            if a1 < b0:                     # 8 邻接：相邻行的段错开一格也算连着
                i += 1
            elif b1 < a0:
                j += 1
            else:
                union(a[i], b[j])
                if a1 < b1:
                    i += 1
                else:
                    j += 1
    roots = [find(i) for i in range(len(runs))]
    area = {}
    for i, rt in enumerate(roots):
        area[rt] = area.get(rt, 0) + (runs[i][2] - runs[i][1])
    return roots, area


def _runs_mask(runs, roots, want, nx, ny):
    """把根 == want 的段填进新掩膜。"""
    m = bytearray(nx * ny)
    for i, (y, c0, c1) in enumerate(runs):
        if roots[i] == want:
            base = y * nx
            m[base + c0:base + c1] = b"\x01" * (c1 - c0)
    return m


def survey_mask_largest(mask, nx, ny):
    """取面积最大的 8 邻接连片。返回 (新掩膜, 连片数, 最大连片像元数, 第二大连片像元数)。"""
    runs = _mask_runs(mask, nx, ny)
    if not runs:
        return bytearray(nx * ny), 0, 0, 0
    roots, area = _runs_components(runs, ny)
    order = sorted(area.items(), key=lambda kv: -kv[1])
    return (_runs_mask(runs, roots, order[0][0], nx, ny), len(order), order[0][1],
            order[1][1] if len(order) > 1 else 0)


def survey_mask_fill_holes(mask, nx, ny, cell):
    """填掉内部面积 < SURVEY_CLIP_HOLE 的洞（洞 = 不碰边界的一块空格子）。返回 (新掩膜, 填了几个, 填掉多少像元)。"""
    inv = bytearray(1 - v for v in mask)
    runs = _mask_runs(inv, nx, ny)
    if not runs:
        return mask, 0, 0
    roots, area = _runs_components(runs, ny)
    border = set()                          # 碰到边界的空格子不算洞
    for i, (y, c0, c1) in enumerate(runs):
        if y == 0 or y == ny - 1 or c0 == 0 or c1 >= nx:
            border.add(roots[i])
    by_root = {}
    for i, rt in enumerate(roots):
        by_root.setdefault(rt, []).append(i)
    out = bytearray(mask)
    n, filled = 0, 0
    for root, a in area.items():
        if root in border or a * cell * cell >= SURVEY_CLIP_HOLE:
            continue
        n += 1
        filled += a
        for i in by_root[root]:
            y, c0, c1 = runs[i]
            base = y * nx
            out[base + c0:base + c1] = b"\x01" * (c1 - c0)
    return out, n, filled


def _simplify_dp(pts, tol):
    """Douglas-Peucker 简化折线（pts 首尾相同）。"""
    if len(pts) <= 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        (xa, ya), (xb, yb) = pts[a], pts[b]
        dx, dy = xb - xa, yb - ya
        norm = math.hypot(dx, dy)
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            dist = math.hypot(px - xa, py - ya) if norm < 1e-12 else abs(dy * (px - xa) - dx * (py - ya)) / norm
            if dist > best:
                best, bi = dist, i
        if best > tol and bi > 0:
            keep[bi] = True
            stack.append((a, bi))
            stack.append((bi, b))
    return [p for i, p in enumerate(pts) if keep[i]]


def survey_polygon_area(pts):
    """多边形面积（m²，鞋带公式；闭合与否都行，返回正值）。"""
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for i in range(len(pts) - 1):
        s += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    if pts[0] != pts[-1]:
        s += pts[-1][0] * pts[0][1] - pts[0][0] * pts[-1][1]
    return abs(s) / 2.0


def _bits(v):
    """大整数里为 1 的位号。"""
    s = bin(v)[:1:-1]
    i = s.find("1")
    while i >= 0:
        yield i
        i = s.find("1", i + 1)


def survey_mask_rings(rows, nx):
    """分析网格（每行一个大整数）→ 所有边界环（格角点坐标 (列, 行)，行向下，首尾相同）。掩膜总在前进方向右手边：
    换到 y 向上的投影坐标后外环顺时针、洞逆时针（同 ESRI 的约定）。先过 _fix_pinches，每个角点只有一条出边，环都是简单环。"""
    nxt = {}
    keep = (1 << nx) - 1
    ny = len(rows)
    for y, r in enumerate(rows):
        if not r:
            continue
        up = rows[y - 1] if y else 0
        dn = rows[y + 1] if y + 1 < ny else 0
        for x in _bits(r & ~up):
            nxt.setdefault((x, y), []).append((x + 1, y))
        for x in _bits(r & ~(r >> 1) & keep):
            nxt.setdefault((x + 1, y), []).append((x + 1, y + 1))
        for x in _bits(r & ~dn):
            nxt.setdefault((x + 1, y + 1), []).append((x, y + 1))
        for x in _bits(r & ~(r << 1)):
            nxt.setdefault((x, y + 1), []).append((x, y))
    loops = []
    while nxt:
        start = next(iter(nxt))
        loop, cur = [start], start
        while True:
            ends = nxt.get(cur)
            if not ends:
                break
            e = ends.pop()
            if not ends:
                del nxt[cur]
            loop.append(e)
            cur = e
            if cur == start:
                break
        if cur == start and len(loop) >= 5:
            loops.append(loop)
    return loops


def survey_ring_signed_area(pts):
    """有向面积（y 向上的投影坐标里逆时针为正）。"""
    s = 0.0
    n = len(pts)
    for i in range(n - 1):
        s += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    if n and pts[0] != pts[-1]:
        s += pts[-1][0] * pts[0][1] - pts[0][0] * pts[-1][1]
    return s / 2.0


def _simplify_ring(pts):
    """首尾相同的环：Douglas-Peucker（容差 SURVEY_CLIP_SIMPLIFY 的一半）+ 相邻点间距 ≥ SURVEY_CLIP_SIMPLIFY。不够 3 点返回 []。"""
    simp = _simplify_dp(pts, SURVEY_CLIP_SIMPLIFY / 2.0)
    out = []
    for p in simp:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= SURVEY_CLIP_SIMPLIFY:
            out.append(p)
    while len(out) > 3 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) < SURVEY_CLIP_SIMPLIFY:
        out.pop()
    if len(out) < 3:
        return []
    out.append(out[0])
    return out


def survey_polygons_area(polys):
    """[(外环, [内环…])…] 的面积（外环减内环）。"""
    return sum(survey_polygon_area(o) - sum(survey_polygon_area(h) for h in hs) for o, hs in polys)


def survey_point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def survey_valid_cells(an, density=SURVEY_CLIP_DENSITY):
    """三条判据（🔒 D-32）同时满足的分析格 → (掩膜, 统计)。统计里是按 ①→②→③ 依次淘汰的格数。"""
    nx, ny = an["nx"], an["ny"]
    bx = nx // 2
    ok, cnt, blk = an["dom_ok"], an["cnt"], an["blk"]
    t2 = bytes([0] + [1] * 255)
    thr = int(math.ceil(density * 100 / 100.0 - 1e-9))   # 1 米块 = 100 个子格：有值的 ≥ density% 个
    t3 = bytes([1 if v >= thr else 0 for v in range(256)])
    valid = bytearray(nx * ny)
    n12 = 0
    for y in range(ny):
        a = ok[y * nx:(y + 1) * nx]
        b = bytes(cnt[y * nx:(y + 1) * nx]).translate(t2)
        bb = bytes(blk[(y // 2) * bx:(y // 2 + 1) * bx]).translate(t3)
        c = bytearray(nx)
        c[0::2] = bb
        c[1::2] = bb
        ia, ib = int.from_bytes(a, "big"), int.from_bytes(b, "big")
        n12 += (ia & ib).to_bytes(nx, "big").count(1)
        valid[y * nx:(y + 1) * nx] = (ia & ib & int.from_bytes(c, "big")).to_bytes(nx, "big")
    n1 = ok.count(1)
    nv = valid.count(1)
    return valid, {"cells": nx * ny, "fail1": nx * ny - n1, "fail2": n1 - n12, "fail3": n12 - nv, "valid": nv,
                   "density": density, "thr": thr}


def survey_range_from_analysis(an, inset=SURVEY_CLIP_INSET, density=SURVEY_CLIP_DENSITY, log=None, fail_text=None):
    """分析网格 → 范围线（🔒 D-32 的顺序）：有效格 → 补缝（扩 1 米再收 1 米）→ 最大 8 邻接连片 → 填内部 < 100 m² 的洞 →
    往里收 inset 米 → 简化（相邻点 ≥ 1 米）。返回 dict：polygons [(外环, [内环…])]（投影坐标、首尾闭合）、各步面积等。
    fail_text：只换日志里②③那半句的说法（T17：工点没有点云时 cnt 里放的是「CC DSM 有值」，格式里一个 %.0f 面积）。"""
    log = log or (lambda s: None)
    nx, ny, cell = an["nx"], an["ny"], an["cell"]
    area_of = lambda n: n * cell * cell
    t0 = time.time()
    valid, st = survey_valid_cells(an, density)
    if fail_text:
        log(("有效格子：%d 格（%.0f m²）。依次淘汰：①影像不足 %.0f m²、" + fail_text) % (
            st["valid"], area_of(st["valid"]), area_of(st["fail1"]), area_of(st["fail2"])))
    else:
        log("有效格子：%d 格（%.0f m²）。按 ①→②→③ 依次淘汰：①影像不足 %.0f m²、②没有真实点 %.0f m²、③点云不够密（1 米块里有点的 "
            "0.1 米子格 < %g%%）%.0f m²" % (st["valid"], area_of(st["valid"]), area_of(st["fail1"]), area_of(st["fail2"]),
                                          density, area_of(st["fail3"])))
    if not st["valid"]:
        raise RuntimeError("一个有效格子都没有（没有影像、没有真实点云，或点云密度门槛太高），画不出范围线")
    rc = SURVEY_CLIP_CLOSE / cell
    rows = _erode_rows(_dilate_rows(_mask_rows(valid, nx, ny), nx, rc), nx, rc)
    mask = _rows_mask(rows, nx)
    closed = mask.count(1)
    mask, ncomp, big, second = survey_mask_largest(mask, nx, ny)
    log("补缝（扩 %g 米再收 %g 米）后 %.0f m²；最大连片 %.0f m²（连片共 %d 片，其余 %.0f m² 舍掉）" % (
        SURVEY_CLIP_CLOSE, SURVEY_CLIP_CLOSE, area_of(closed), area_of(big), ncomp, area_of(closed - big)))
    mask, nhole, filled = survey_mask_fill_holes(mask, nx, ny, cell)
    if nhole:
        log("填掉 %d 个内部小于 %g m² 的洞（共 %.0f m²）" % (nhole, SURVEY_CLIP_HOLE, area_of(filled)))
    before = mask.count(1)
    rows = _erode_rows(_mask_rows(mask, nx, ny), nx, inset / cell + 1.0, strict=True) if inset > 0 else \
        _mask_rows(mask, nx, ny)
    mask = _rows_mask(rows, nx)
    if not mask.count(1):
        raise RuntimeError("往里收 %g 米之后一格都不剩（有效区域只有 %.0f m²，太窄），请把「往里收」调小" % (inset, area_of(before)))
    mask, ncomp2, big2, _ = survey_mask_largest(mask, nx, ny)
    if ncomp2 > 1:
        log("往里收之后断成 %d 片，只留最大的一片（%.0f m²）" % (ncomp2, area_of(big2)))
    rows = _fix_pinches(_mask_rows(mask, nx, ny), nx)
    loops = survey_mask_rings(rows, nx)
    x0, top = an["left"], an["top"]
    outers, holes = [], []
    for lp in loops:
        ring = _simplify_ring([(x0 + i * cell, top - j * cell) for i, j in lp])
        if not ring:
            continue
        (outers if survey_ring_signed_area(ring) < 0 else holes).append(ring)   # 外环在投影坐标里顺时针
    if not outers:
        raise RuntimeError("范围线画不出来（有效区域的边界不成圈）")
    outer = max(outers, key=survey_polygon_area)
    holes = [h for h in holes if survey_point_in_ring(h[0][0], h[0][1], outer)]
    polys = [(outer, holes)]
    line_area = survey_polygons_area(polys)
    log("往里收 %g 米，范围线 1 个外环 %d 个点%s、面积 %.0f m²（收之前 %.0f m²），算范围线用 %.0f 秒" % (
        inset, len(outer) - 1, "、%d 个内环（挖掉的大洞）" % len(holes) if holes else "", line_area, area_of(before),
        time.time() - t0))
    return {"polygons": polys, "valid_area": area_of(before), "line_area": line_area, "cell": cell,
            "grid": (nx, ny), "components": ncomp, "second_area": area_of(second), "holes": nhole,
            "holes_area": area_of(filled), "inset": inset, "density": density, "points": len(outer) - 1,
            "inner_rings": len(holes), "stats": st, "valid_cells_area": area_of(st["valid"]),
            "fail_area": (area_of(st["fail1"]), area_of(st["fail2"]), area_of(st["fail3"])),
            "closed_area": area_of(closed), "largest_area": area_of(big), "pieces_after_inset": ncomp2}


# ---- 范围线文件：KML（WGS84 经纬度）、SHP（同 DOM 坐标系，带 .shx/.dbf/.prj）；都支持多个多边形和内环（内环＝挖掉） ----
def _shp_esri_wkt(epsg):
    """该带的 ESRI WKT（写 .prj 用）。"""
    cm = survey_cm(epsg)
    return ('PROJCS["CGCS2000_3_Degree_GK_CM_%dE",GEOGCS["GCS_China_Geodetic_Coordinate_System_2000",'
            'DATUM["D_China_2000",SPHEROID["CGCS2000",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
            'UNIT["Degree",0.0174532925199433]],PROJECTION["Gauss_Kruger"],PARAMETER["False_Easting",500000.0],'
            'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",%g],PARAMETER["Scale_Factor",1.0],'
            'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]' % (cm, cm))


def _as_polygons(polygon):
    """范围线统一成 [(外环, [内环…])…]；也接受 T11 的单个点列。"""
    if polygon and isinstance(polygon[0], (list, tuple)) and len(polygon[0]) == 2 and \
            isinstance(polygon[0][0], (int, float)):
        return [(list(polygon), [])]
    return [(list(o), [list(h) for h in hs]) for o, hs in polygon]


def _closed(ring):
    ring = [tuple(p) for p in ring]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def survey_write_range_kml(path, polygon, epsg, name=""):
    """写 KML：WGS84 经纬度（由不带带号的高斯投影反算；WGS84 与 CGCS2000 差厘米级）。多个多边形用 MultiGeometry，
    内环写 innerBoundaryIs。"""
    def coords(ring):
        return "\n".join("%.9f,%.9f,0" % survey_tm_inverse(x, y, epsg) for x, y in _closed(ring))

    polys = _as_polygons(polygon)
    geo = []
    for o, hs in polys:
        geo.append('<Polygon><tessellate>1</tessellate>\n<outerBoundaryIs><LinearRing><coordinates>\n%s\n'
                   '</coordinates></LinearRing></outerBoundaryIs>\n%s</Polygon>\n' % (coords(o), "".join(
                       '<innerBoundaryIs><LinearRing><coordinates>\n%s\n</coordinates></LinearRing></innerBoundaryIs>\n'
                       % coords(h) for h in hs)))
    body = geo[0] if len(geo) == 1 else "<MultiGeometry>\n%s</MultiGeometry>\n" % "".join(geo)
    Path(path).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
        '<name>%s</name>\n<description>无人机照片工具 DOM/DSM 裁剪范围线（%s，WGS84 经纬度）</description>\n'
        '<Placemark>\n<name>范围线</name>\n<Style><LineStyle><color>ff0000ff</color><width>2</width></LineStyle>'
        '<PolyStyle><color>190000ff</color></PolyStyle></Style>\n%s</Placemark>\n</Document>\n</kml>\n' % (
            name or "范围线", survey_srs_name(epsg), body), encoding="utf-8")
    return Path(path)


def survey_write_range_shp(base, polygon, epsg, name=""):
    """写范围线 SHP（一条多边形记录，外环顺时针、内环逆时针）+ .shx + .dbf + .prj；坐标系同 DOM（如 EPSG:4543）。
    返回写出的文件列表。"""
    s = str(base)
    if s.lower().endswith(".shp"):
        s = s[:-4]
    polys = _as_polygons(polygon)
    parts = []
    for o, hs in polys:
        for k, ring in enumerate([o] + hs):
            ring = _closed(ring)
            if (survey_ring_signed_area(ring) > 0) == (k == 0):   # ESRI：外环顺时针（有向面积为负），内环逆时针
                ring.reverse()
            parts.append(ring)
    area = survey_polygons_area(polys)
    pts = [p for ring in parts for p in ring]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bb = (min(xs), min(ys), max(xs), max(ys))
    offs, n = [], 0
    for ring in parts:
        offs.append(n)
        n += len(ring)
    content = struct.pack("<i4d2i", 5, bb[0], bb[1], bb[2], bb[3], len(parts), len(pts)) + \
        struct.pack("<%di" % len(parts), *offs) + b"".join(struct.pack("<2d", p[0], p[1]) for p in pts)

    def header(nbytes):
        return (struct.pack(">6i", 9994, 0, 0, 0, 0, 0) + struct.pack(">i", nbytes // 2) +
                struct.pack("<2i", 1000, 5) + struct.pack("<4d", bb[0], bb[1], bb[2], bb[3]) +
                struct.pack("<4d", 0.0, 0.0, 0.0, 0.0))
    Path(s + ".shp").write_bytes(header(100 + 8 + len(content)) + struct.pack(">2i", 1, len(content) // 2) + content)
    Path(s + ".shx").write_bytes(header(100 + 8) + struct.pack(">2i", 50, len(content) // 2))
    fields = (("NAME", "C", 60, 0), ("AREA_M2", "N", 18, 3), ("EPSG", "N", 7, 0))
    now = datetime.date.today()
    hdr_len = 32 + 32 * len(fields) + 1
    rec_len = 1 + sum(f[2] for f in fields)
    hdr = struct.pack("<4Bihh", 3, now.year - 1900, now.month, now.day, 1, hdr_len, rec_len) + b"\x00" * 20
    for fname, ftype, flen, fdec in fields:
        hdr += fname.encode("ascii")[:11].ljust(11, b"\x00") + ftype.encode("ascii") + b"\x00" * 4 + \
            bytes([flen, fdec]) + b"\x00" * 14
    hdr += b"\r"
    try:
        nm = (name or Path(s).name).encode("gbk", "replace")[:60]
    except LookupError:
        nm = (name or Path(s).name).encode("ascii", "replace")[:60]
    rec = (b" " + nm.ljust(60, b" ") + ("%.3f" % area).encode("ascii").rjust(18, b" ") +
           str(int(epsg)).encode("ascii").rjust(7, b" "))
    Path(s + ".dbf").write_bytes(hdr + rec + b"\x1a")
    Path(s + ".prj").write_text(_shp_esri_wkt(epsg), encoding="ascii")
    return [Path(s + ".shp"), Path(s + ".shx"), Path(s + ".dbf"), Path(s + ".prj")]


def _rings_to_polygons(rings):
    """一堆环（不管顺/逆时针）按嵌套层数分外环和内环：被偶数个更大的环包着的是外环，奇数个的是紧挨着包它的外环的内环。"""
    rings = [r for r in (_closed(r) for r in rings) if len(r) >= 4]
    rings.sort(key=lambda r: -survey_polygon_area(r))
    depth, parent = [0] * len(rings), [None] * len(rings)
    for i, r in enumerate(rings):
        x, y = r[0]
        for j in range(i):
            if survey_point_in_ring(x, y, rings[j]):
                depth[i] += 1
                parent[i] = j            # 面积从大到小，最后一个包住它的就是最紧的那个
    out, index = [], {}
    for i, r in enumerate(rings):
        if depth[i] % 2 == 0:
            index[i] = len(out)
            out.append((r, []))
        elif parent[i] in index:
            out[index[parent[i]]][1].append(r)
    return out


def _prj_transform(prj_text, epsg):
    """看 .prj：返回把文件坐标换到本工点投影坐标的函数；看不懂抛 ValueError。认：经纬度（WGS84/CGCS2000）、
    CGCS2000/WGS84 的高斯-克吕格或横轴墨卡托投影（任意中央经线、东坐标带不带带号、比例因子）。"""
    t = prj_text.upper()
    if "PROJCS" not in t:
        if "GEOGCS" in t:
            return lambda x, y: survey_tm_forward(x, y, epsg)
        raise ValueError("看不懂 .prj 里的坐标系")
    if "MERCATOR" not in t and "GAUSS" not in t:
        raise ValueError("只认高斯-克吕格 / 横轴墨卡托投影的 .prj，这个是：%s" % prj_text[:120])

    def par(key, default):
        m = re.search(r'PARAMETER\["%s",\s*([-0-9.eE+]+)\]' % key, prj_text, re.I)
        return float(m.group(1)) if m else default
    cm, fe, k0 = par("Central_Meridian", None), par("False_Easting", 500000.0), par("Scale_Factor", 1.0)
    fn = par("False_Northing", 0.0)
    if cm is None:
        raise ValueError(".prj 里没有中央经线")
    if abs(cm - survey_cm(epsg)) < 1e-9 and abs(fe - 500000.0) < 1e-6 and abs(k0 - 1.0) < 1e-12 and abs(fn) < 1e-6:
        return lambda x, y: (x, y)

    def f(x, y):
        lon, lat = _tm_inverse_cm((x - fe) / k0 + 500000.0, (y - fn) / k0, cm)
        return survey_tm_forward(lon, lat, epsg)
    return f


def survey_read_range_shp(path, epsg=None):
    """读 SHP 里所有多边形记录的所有环 → [(外环, [内环…])…]（本工点投影坐标）。有 .prj 按它换算，没有当作与 DOM 同坐标系。"""
    data = Path(path).read_bytes()
    if len(data) < 100 or struct.unpack(">i", data[:4])[0] != 9994:
        raise ValueError("不是 SHP 文件：%s" % path)
    kind = struct.unpack("<i", data[32:36])[0]
    if kind not in (5, 15, 25):
        raise ValueError("SHP 里的不是多边形（类型 %d）：%s" % (kind, path))
    prj = Path(str(path)[:-4] + ".prj")
    conv = None
    if epsg and prj.is_file():
        try:
            conv = _prj_transform(prj.read_text(encoding="utf-8", errors="replace"), epsg)
        except ValueError as ex:
            raise ValueError("%s：%s" % (ex, prj))
    off, rings = 100, []
    while off + 8 <= len(data):
        words = struct.unpack(">i", data[off + 4:off + 8])[0]
        body = data[off + 8:off + 8 + words * 2]
        off += 8 + words * 2
        if len(body) < 44 or struct.unpack("<i", body[:4])[0] not in (5, 15, 25):
            continue
        nparts, npts = struct.unpack("<2i", body[36:44])
        if npts < 3 or nparts < 1:
            continue
        parts = list(struct.unpack("<%di" % nparts, body[44:44 + 4 * nparts])) + [npts]
        p0 = 44 + 4 * nparts
        for k in range(nparts):
            ring = [struct.unpack("<2d", body[p0 + 16 * i:p0 + 16 * i + 16]) for i in range(parts[k], parts[k + 1])]
            if conv:
                ring = [conv(x, y) for x, y in ring]
            if len(ring) >= 3:
                rings.append(ring)
    polys = _rings_to_polygons(rings)
    if not polys:
        raise ValueError("SHP 里没有可用的多边形：%s" % path)
    return polys


def _kml_ring(el, epsg):
    pts = []
    for tok in (el.text or "").split():
        v = tok.split(",")
        if len(v) >= 2:
            try:
                pts.append(survey_tm_forward(float(v[0]), float(v[1]), epsg))
            except ValueError:
                continue
    return pts


def survey_read_range_kml(path, epsg):
    """读 KML 里所有 Polygon（外环 + 内环，WGS84 经纬度）→ [(外环, [内环…])…]（本工点投影坐标）。
    没有 Polygon 时退回去找第一个点数 ≥ 3 的坐标串当外环。"""
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as ex:
        raise ValueError("KML 读不了（%s）：%s" % (ex, path))
    tag = lambda e: e.tag.split("}")[-1]
    polys = []
    for el in root.iter():
        if tag(el) != "Polygon":
            continue
        outer, inner = None, []
        for b in el:
            if tag(b) not in ("outerBoundaryIs", "innerBoundaryIs"):
                continue
            for c in b.iter():
                if tag(c) == "coordinates":
                    ring = _kml_ring(c, epsg)
                    if len(ring) >= 3:
                        if tag(b) == "outerBoundaryIs":
                            outer = ring
                        else:
                            inner.append(ring)
        if outer:
            polys.append((_closed(outer), [_closed(h) for h in inner]))
    if not polys:
        for el in root.iter():
            if tag(el) == "coordinates":
                ring = _kml_ring(el, epsg)
                if len(ring) >= 3:
                    polys.append((_closed(ring), []))
                    break
    if not polys:
        raise ValueError("KML 里没有可用的多边形（要 lon,lat 至少 3 个点）：%s" % path)
    return polys


def survey_read_range_line(path, epsg):
    """读范围线文件（.shp 或 .kml）→ [(外环, [内环…])…]（本工点投影坐标，环都首尾闭合）。"""
    p = Path(path)
    if p.suffix.lower() == ".shp":
        polys = survey_read_range_shp(p, epsg)
    elif p.suffix.lower() == ".kml":
        polys = survey_read_range_kml(p, epsg)
    else:
        raise ValueError("范围线只认 .kml 或 .shp：%s" % p)
    out = []
    for o, hs in polys:
        o = _closed(o)
        if len(o) >= 4 and survey_polygon_area(o) > 0:
            out.append((o, [_closed(h) for h in hs if len(h) >= 3]))
    if not out:
        raise ValueError("范围线里没有面积大于 0 的多边形：%s" % p)
    return out


def survey_user_range_line(site_dir, name, report):
    """重跑时用户改过的范围线优先：`<工点名>_范围线.shp/.kml` 比 处理报告.txt 新就返回它的路径，否则 None。"""
    rep = Path(report)
    if not rep.is_file():
        return None
    try:
        mt = rep.stat().st_mtime
    except OSError:
        return None
    for suf in (SURVEY_RANGE_SHP, SURVEY_RANGE_KML):   # .shp 在前：坐标系同 DOM，不用换算
        p = Path(site_dir) / (name + suf)
        try:
            if p.is_file() and p.stat().st_mtime > mt:
                return str(p)
        except OSError:
            pass
    return None


def survey_site_range_line(site_dir, name):
    """工点目录里的范围线（「按范围线重新裁剪」选「用工点目录里的」时）：.shp、.kml 哪个新用哪个；都没有返回 None。"""
    best = None
    for suf in (SURVEY_RANGE_SHP, SURVEY_RANGE_KML):
        p = Path(site_dir) / (name + suf)
        if p.is_file() and (best is None or p.stat().st_mtime > best.stat().st_mtime):
            best = p
    return str(best) if best else None


def survey_set_aside_range_lines(site_dir, name, stamp):
    """旧的范围线文件改名保留成 <工点名>_范围线_上次_<时间>.kml/.shp（.shx/.dbf/.prj 跟着改）。返回改了名的新路径。"""
    moved = []
    for suf in (".kml", ".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = Path(site_dir) / ("%s_范围线%s" % (name, suf))
        if p.is_file():
            q = p.with_name("%s_范围线_上次_%s%s" % (name, stamp, suf))
            os.replace(str(p), str(q))
            moved.append(q)
    return moved


def survey_clip_window(polygon, origin, px, margin=SURVEY_CLIP_MARGIN_PX):
    """范围线（所有外环）外接矩形外扩 margin 个像元，并对齐到原 GeoTIFF 的像元网格（左上角 origin、像素 px）。
    返回 (region [x0, y0, x1, y1], 列数, 行数)。region 高宽多要 0.25 个像元：Metashape 按 int() 截断像素数。"""
    ox, oy = origin
    pts = [p for o, hs in _as_polygons(polygon) for p in o]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    c0 = int(math.floor((min(xs) - ox) / px)) - margin
    c1 = int(math.ceil((max(xs) - ox) / px)) + margin
    r0 = int(math.floor((oy - max(ys)) / px)) - margin
    r1 = int(math.ceil((oy - min(ys)) / px)) + margin
    ncol, nrow = c1 - c0, r1 - r0
    x0 = ox + c0 * px
    x1 = x0 + (ncol + SURVEY_CLIP_INSET_TAIL) * px
    y1 = oy - r0 * px
    y0 = y1 - (nrow + SURVEY_CLIP_INSET_TAIL) * px
    return [x0, y0, x1, y1], ncol, nrow


# 内嵌脚本：裁边分析用（D-32）。读写打开工程但**不保存**：（可选）先导出没裁的 DOM，再用密集点云建一张**不插值**的辅助 DSM
# （0.1 米，只判断、不输出）导出成不压缩的 GeoTIFF。正式 DSM 留在工程文件里不动（从不调用 save）；建辅助 DSM 时 Metashape
# 在 .files 里新写的数据目录由调用方事后删掉。只用 Python 3.8 语法。
SURVEY_AUX_SCRIPT = r'''# -*- coding: utf-8 -*-
# 由「无人机照片工具」影像处理页签生成：裁边分析（不插值辅助 DSM），在 Metashape 内嵌的 Python 里运行。不保存工程。
import json
import sys
import time
import traceback

P = json.load(open(sys.argv[-1], "r", encoding="utf-8"))
_pf = open(P["progress"], "a", encoding="utf-8")


def ev(kind, **kw):
    kw["ev"] = kind
    kw["t"] = round(time.time(), 1)
    _pf.write(json.dumps(kw, ensure_ascii=False) + "\n")
    _pf.flush()


class Prog(object):
    def __init__(self, stage):
        self.stage = stage
        self.last = -1

    def __call__(self, p):
        p = int(p)
        if p != self.last:
            self.last = p
            ev("progress", stage=self.stage, pct=p)


def compression(big, deflate):
    ic = Metashape.ImageCompression()
    ic.tiff_compression = (Metashape.ImageCompression.TiffCompressionDeflate if deflate
                           else Metashape.ImageCompression.TiffCompressionNone)
    ic.tiff_big = big
    ic.tiff_tiled = False
    ic.tiff_overviews = False
    return ic


app = None
try:
    try:
        import Metashape
    except ImportError:
        import PhotoScan as Metashape
    app = Metashape.app
    ev("start", version=str(app.version), python=sys.version.split()[0])
    crs = Metashape.CoordinateSystem("EPSG::%d" % P["epsg"])
    ev("stage", stage="open")
    doc = Metashape.Document()
    doc.open(P["psx"], read_only=False)      # 辅助 DSM 只读打开建不上；读写打开但从不保存
    ch = doc.chunk
    if ch.dense_cloud is None:
        raise RuntimeError("工程里没有密集点云，判断不了「真实点云」（要勾 DOM/DSM 跑过的工程）")
    el = ch.elevation
    if el is None:
        raise RuntimeError("工程里没有 DSM")
    L, T, R, B = el.left, el.top, el.right, el.bottom
    ev("elevation", left=L, top=T, right=R, bottom=B, res=el.resolution)
    op = Metashape.OrthoProjection()
    op.crs = crs
    if P.get("dom_raw"):                      # 没裁的 DOM（判据①），范围、像元网格同第 1 趟（正式 DSM 的范围）
        if ch.orthomosaic is None:
            raise RuntimeError("工程里没有 DOM")
        res = P["resolution"] or ch.orthomosaic.resolution
        box = Metashape.BBox()
        box.min = Metashape.Vector([L, B])
        box.max = Metashape.Vector([R, T])
        big = (R - L) / res * (T - B) / res * 4 > P["bigtiff"]
        kw = dict(source_data=Metashape.OrthomosaicData, image_format=Metashape.ImageFormatTIFF, projection=op,
                  region=box, save_world=True, image_compression=compression(big, True), save_alpha=True,
                  white_background=False, progress=Prog("dom_raw"))
        if P["resolution"]:
            kw.update(resolution_x=res, resolution_y=res)
        ev("stage", stage="dom_raw")
        t0 = time.time()
        ch.exportRaster(P["dom_raw"], **kw)
        ev("secs", stage="dom_raw", secs=round(time.time() - t0, 1))
    ev("stage", stage="aux_dem")
    t0 = time.time()
    ch.buildDem(source_data=Metashape.DenseCloudData, interpolation=Metashape.DisabledInterpolation, projection=op,
                resolution=P["aux_res"], progress=Prog("aux_dem"))
    ev("secs", stage="aux_dem", secs=round(time.time() - t0, 1))
    r = P["aux_res"]
    cols = int(round((R - L) / r)) + 10
    rows = int(round((T - B) / r)) + 10
    box = Metashape.BBox()
    box.min = Metashape.Vector([L, T - (rows + 0.25) * r])
    box.max = Metashape.Vector([L + (cols + 0.25) * r, T])
    ev("stage", stage="aux_export")
    t0 = time.time()
    ch.exportRaster(P["aux"], source_data=Metashape.ElevationData, image_format=Metashape.ImageFormatTIFF, projection=op,
                    region=box, resolution_x=r, resolution_y=r, nodata_value=P["nodata"], save_alpha=False,
                    save_world=True, image_compression=compression(cols * rows * 4 > P["bigtiff"], False),
                    progress=Prog("aux_export"))
    ev("secs", stage="aux_export", secs=round(time.time() - t0, 1))
    ev("aux", cols=cols, rows=rows, interpolation="DisabledInterpolation")
    ev("done")
except Exception:
    ev("error", msg=traceback.format_exc())
finally:
    _pf.close()
    if app is not None:
        app.quit()
'''


def _psx_frame_dirs(psx):
    """工程数据目录 <名>.files/<chunk>/<frame>/ 下现有的子目录（建辅助 DSM 前后比，删掉新多出来的）。"""
    base = Path(str(psx)[:-4] + ".files")
    out = set()
    if base.is_dir():
        for c in base.iterdir():
            if c.is_dir():
                for f in c.iterdir():
                    if f.is_dir():
                        for d in f.iterdir():
                            if d.is_dir():
                                out.add(str(d))
    return out


def _psx_fingerprint(psx):
    """工程文件本身（.psx、各 chunk.zip / frame.zip）的哈希：辅助 DSM 那一趟前后必须一样（证明没保存、正式 DSM 没被换掉）。"""
    psx = Path(psx)
    files = [psx] + sorted(Path(str(psx)[:-4] + ".files").glob("*/*.zip")) + \
        sorted(Path(str(psx)[:-4] + ".files").glob("*/*/*.zip"))
    fp = {}
    for f in files:
        if f.is_file() and f.parent.name not in ("elevation", "orthomosaic", "dense_cloud", "depth_maps", "point_cloud",
                                                 "thumbnails") and not f.parent.name.startswith("elevation"):
            fp[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
    return fp


def survey_clip_analyze(psx, metashape, epsg, resolution, dom_raw, site_dir, name, export_dom=False, log=None,
                        progress=None, stop_event=None, **timeouts):
    """裁边分析：Metashape 里建不插值辅助 DSM（export_dom=True 时顺便导出没裁的 DOM 到 dom_raw）→ 标准库读出三条判据的
    分析网格 → 存 <工点名>_裁边分析.json.gz。辅助 DSM 用完就删，Metashape 新写的数据目录也删。返回分析 dict。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    site_dir = Path(site_dir)
    tmp = Path(tempfile.mkdtemp(prefix="uav_clipan_"))
    aux = site_dir / (name + "_辅助DSM.不插值.tmp.tif")
    events, steps = [], []
    before_dirs, before_fp = _psx_frame_dirs(psx), _psx_fingerprint(psx)

    def on_event(e):
        k = e.get("ev")
        if k == "start":
            log("Metashape 版本 %s（裁边分析：建不插值的辅助 DSM，只判断、不输出，工程不保存）" % e.get("version", ""))
        elif k == "stage":
            progress({"open": 0.02, "dom_raw": 0.05, "aux_dem": 0.35, "aux_export": 0.9}.get(e["stage"], 0.0),
                     {"open": "打开工程", "dom_raw": "导出没裁的 DOM", "aux_dem": "建不插值辅助 DSM",
                      "aux_export": "导出辅助 DSM"}.get(e["stage"], e["stage"]))
        elif k == "progress":
            a, b, lab = {"dom_raw": (0.05, 0.35, "导出没裁的 DOM"), "aux_dem": (0.35, 0.9, "建不插值辅助 DSM"),
                         "aux_export": (0.9, 0.98, "导出辅助 DSM")}.get(e.get("stage"), (0, 0, ""))
            if lab:
                progress(a + (b - a) * e.get("pct", 0) / 100.0, "%s %d%%" % (lab, e.get("pct", 0)))
        elif k == "secs":
            steps.append((e["stage"], e["secs"]))
        elif k == "error":
            log("Metashape 脚本出错：\n%s" % e.get("msg", ""))

    params = {"psx": str(psx), "epsg": int(epsg), "resolution": float(resolution or 0), "nodata": SURVEY_NODATA,
              "bigtiff": SURVEY_BIGTIFF, "aux": str(aux), "aux_res": SURVEY_CLIP_AUX_RES,
              "dom_raw": str(dom_raw) if export_dom else ""}
    try:
        try:
            events, rc = _run_ms_script(metashape, SURVEY_AUX_SCRIPT, params, tmp, on_event, stop_event, **timeouts)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            for d in sorted(_psx_frame_dirs(psx) - before_dirs):   # Metashape 给辅助 DSM 新建的数据目录（工程里没引用）
                if Path(d).name.startswith("elevation"):
                    shutil.rmtree(d, ignore_errors=True)
            if _psx_fingerprint(psx) != before_fp:
                raise RuntimeError("裁边分析那一趟之后工程文件变了（不应该保存），请检查工程：%s" % psx)
        errs = [e for e in events if e.get("ev") == "error"]
        if errs:
            raise RuntimeError("裁边分析时 Metashape 出错：%s" % (
                errs[-1].get("msg", "").strip().splitlines() or ["未知错误"])[-1])
        if rc != 0:
            raise RuntimeError("裁边分析时 Metashape 异常退出，退出码 %s（0x%08X）" % (rc, rc & 0xFFFFFFFF))
        if not any(e.get("ev") == "done" for e in events) or not aux.is_file():
            raise RuntimeError("裁边分析没跑完（进度文件里没有完成标记或没有辅助 DSM）")
        el = ([e for e in events if e.get("ev") == "elevation"] or [None])[0]
        progress(0.98, "统计三条判据")
        t = tiff_strip_info(dom_raw)
        if el and (abs(t["origin"][0] - el["left"]) > 1e-6 or abs(t["origin"][1] - el["top"]) > 1e-6):
            raise RuntimeError("没裁的 DOM 左上角 (%.6f, %.6f) 和工程里 DSM 的 (%.6f, %.6f) 对不上" % (
                t["origin"][0], t["origin"][1], el["left"], el["top"]))
        nx, ny = survey_clip_grid(t["width"], t["height"], t["px"][0])
        ok = survey_dom_coverage(dom_raw, nx, ny, log=log)
        cnt, blk = survey_aux_counts(aux, nx, ny, log=log)
        an = {"version": 1, "nx": nx, "ny": ny, "cell": SURVEY_CLIP_GRID, "left": t["origin"][0], "top": t["origin"][1],
              "px": t["px"][0], "dom_w": t["width"], "dom_h": t["height"], "epsg": int(epsg),
              "aux_res": SURVEY_CLIP_AUX_RES, "aux_interpolation": "DisabledInterpolation",
              "aux_valid": sum(cnt) / float(max(1, nx * ny * 25)), "made": datetime.datetime.now().isoformat(" ", "seconds"),
              "steps": steps, "dom_ok": ok, "cnt": cnt, "blk": blk}
        survey_write_analysis(site_dir / (name + SURVEY_CLIP_ANALYSIS), an)
        return an
    finally:
        for f in (aux, aux.with_suffix(".tfw")):
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass


# 内嵌脚本：只读重开工程，把范围线加成外边界（内环加成内边界＝挖掉），DOM、DSM 用同一个窗口同一条线重新导出。只用 Python 3.8 语法。
SURVEY_CLIP_SCRIPT = r'''# -*- coding: utf-8 -*-
# 由「无人机照片工具」影像处理页签生成：只重新导出（不重建），在 Metashape 内嵌的 Python 里运行。
import json
import sys
import time
import traceback

P = json.load(open(sys.argv[-1], "r", encoding="utf-8"))
_pf = open(P["progress"], "a", encoding="utf-8")


def ev(kind, **kw):
    kw["ev"] = kind
    kw["t"] = round(time.time(), 1)
    _pf.write(json.dumps(kw, ensure_ascii=False) + "\n")
    _pf.flush()


class Prog(object):
    def __init__(self, stage):
        self.stage = stage
        self.last = -1

    def __call__(self, p):
        p = int(p)
        if p != self.last:
            self.last = p
            ev("progress", stage=self.stage, pct=p)


app = None
try:
    try:
        import Metashape
    except ImportError:
        import PhotoScan as Metashape
    app = Metashape.app
    ev("start", version=str(app.version), python=sys.version.split()[0])
    CS = Metashape.CoordinateSystem
    crs = CS("EPSG::%d" % P["epsg"])
    ev("stage", stage="open")
    doc = Metashape.Document()
    doc.open(P["psx"], read_only=True)
    ch = doc.chunk
    if ch.shapes is None:
        ch.shapes = Metashape.Shapes()
    for old in list(ch.shapes):                # 工程里原有的形状不参与裁剪（只读打开，不会存回去）
        old.boundary_type = Metashape.Shape.NoBoundary
    ch.shapes.crs = crs
    n_out = n_in = 0
    area = 0.0
    for k, ring in enumerate(P["rings"]):
        sh = ch.shapes.addShape()
        sh.type = Metashape.Shape.Type.Polygon
        sh.has_z = False
        sh.label = "DOM/DSM 范围线 %d" % (k + 1)
        sh.vertices = [Metashape.Vector([p[0], p[1], 0.0]) for p in ring["pts"]]
        if ring["outer"]:
            sh.boundary_type = Metashape.Shape.OuterBoundary
            n_out += 1
            area += sh.area()
        else:
            sh.boundary_type = Metashape.Shape.InnerBoundary
            n_in += 1
            area -= sh.area()
    ev("shape", outer=n_out, inner=n_in, area=area)
    el = ch.elevation
    if el is None or ch.orthomosaic is None:
        raise RuntimeError("工程里没有 DSM 或 DOM，没法按范围线重新导出")
    res = P["resolution"] or 0
    px = P["px"]
    ox, oy = P["origin"]
    c0, r0, c1, r1 = P["window"]                 # 列/行号（相对原点），右下不含；不超出工程里 DSM 的范围
    c0 = max(c0, int(round((el.left - ox) / px)))
    c1 = min(c1, int(round((el.right - ox) / px)))
    r0 = max(r0, int(round((oy - el.top) / px)))
    r1 = min(r1, int(round((oy - el.bottom) / px)))
    if c1 <= c0 or r1 <= r0:
        raise RuntimeError("范围线和工程的 DSM 范围不相交")
    box = Metashape.BBox()
    box.min = Metashape.Vector([ox + c0 * px, oy - (r1 + P["tail"]) * px])
    box.max = Metashape.Vector([ox + (c1 + P["tail"]) * px, oy - r0 * px])
    ev("window", window=[c0, r0, c1, r1], region=[box.min.x, box.min.y, box.max.x, box.max.y])
    op = Metashape.OrthoProjection()
    op.crs = crs
    info = {}
    for key, src, bands, nbytes in (("dsm", Metashape.ElevationData, 1, 4), ("dom", Metashape.OrthomosaicData, 4, 1)):
        r = ch.elevation if key == "dsm" else ch.orthomosaic
        w, h = c1 - c0, r1 - r0
        ic = Metashape.ImageCompression()
        ic.tiff_compression = Metashape.ImageCompression.TiffCompressionDeflate
        ic.tiff_big = w * h * bands * nbytes > P["bigtiff"]
        ic.tiff_tiled = False
        ic.tiff_overviews = False
        kw = dict(source_data=src, image_format=Metashape.ImageFormatTIFF, projection=op, region=box,
                  save_world=True, image_compression=ic, clip_to_boundary=True, progress=Prog("clip_" + key))
        if res:
            kw.update(resolution_x=res, resolution_y=res)
        if key == "dsm":
            kw.update(nodata_value=P["nodata"], save_alpha=False)
        else:
            kw.update(save_alpha=True, white_background=False)
        t0 = time.time()
        ch.exportRaster(P[key], **kw)
        ev("secs", stage="clip_" + key, secs=round(time.time() - t0, 1))
        info[key] = {"native": r.resolution, "res": res or r.resolution, "width": w, "height": h, "bigtiff": ic.tiff_big}
    ev("rasters", info=info)
    ev("done")
except Exception:
    ev("error", msg=traceback.format_exc())
finally:
    _pf.close()
    if app is not None:
        app.quit()
'''


def survey_clip_export(metashape, psx, epsg, polygon, resolution, dom, dsm, nodata=SURVEY_NODATA,
                       bigtiff=SURVEY_BIGTIFF, origin=None, log=None, progress=None, stop_event=None, **timeouts):
    """只重新导出：只读重开工程 → 范围线加成外边界（内环＝内边界，挖掉）→ DOM、DSM 用同一个窗口同一条线导出
    （线外透明 / NoData）。dom、dsm 是导出到的路径（调用者负责换名）。origin = 像元网格的左上角（没裁的 DOM/DSM 的，
    或已裁过的同一网格上的）。返回 dict：region、ncol、nrow、steps、rasters。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    if not origin:
        raise ValueError("缺少原 GeoTIFF 的左上角，算不了像元网格")
    polys = _as_polygons(polygon)
    px = float(resolution)
    region, ncol, nrow = survey_clip_window(polys, origin, px)
    c0 = int(round((region[0] - origin[0]) / px))
    r0 = int(round((origin[1] - region[3]) / px))
    rings = []
    for o, hs in polys:
        rings.append({"outer": True, "pts": [list(p) for p in o]})
        rings.extend({"outer": False, "pts": [list(p) for p in h]} for h in hs)
    tmp = Path(tempfile.mkdtemp(prefix="uav_clip_"))
    events, stage_secs = [], []

    def on_event(e):
        k = e.get("ev")
        if k == "start":
            log("Metashape 版本 %s（按范围线重新导出，不重建）" % e.get("version", ""))
        elif k == "stage":
            progress(0.05, "只读重开工程")
        elif k == "progress":
            if e.get("stage") == "clip_dom":
                progress(min(0.97, 0.2 + 0.75 * e.get("pct", 0) / 100.0), "按范围线导出 DOM %d%%" % e.get("pct", 0))
            elif e.get("stage") == "clip_dsm":
                progress(min(0.19, 0.02 + 0.16 * e.get("pct", 0) / 100.0), "按范围线导出 DSM %d%%" % e.get("pct", 0))
        elif k == "shape":
            log("范围线已加成裁剪边界：外环 %d 个%s，面积 %.1f m²" % (
                e["outer"], "、内环（挖掉）%d 个" % e["inner"] if e["inner"] else "", e["area"]))
        elif k == "window":
            w = e["window"]
            log("裁边窗口：东 %.2f～%.2f、北 %.2f～%.2f（%d × %d 像元，%g 米）" % (
                e["region"][0], e["region"][2], e["region"][1], e["region"][3], w[2] - w[0], w[3] - w[1], px))
        elif k == "secs":
            stage_secs.append((e["stage"], e["secs"]))
        elif k == "error":
            log("Metashape 脚本出错：\n%s" % e.get("msg", ""))

    params = {"psx": str(psx), "epsg": int(epsg), "rings": rings, "window": [c0, r0, c0 + ncol, r0 + nrow],
              "origin": [origin[0], origin[1]], "px": px, "tail": SURVEY_CLIP_INSET_TAIL,
              "resolution": float(resolution or 0), "nodata": nodata, "bigtiff": bigtiff, "dom": str(dom), "dsm": str(dsm)}
    try:
        events, rc = _run_ms_script(metashape, SURVEY_CLIP_SCRIPT, params, tmp, on_event, stop_event, **timeouts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    errs = [e for e in events if e.get("ev") == "error"]
    if errs:
        raise RuntimeError("按范围线重新导出时 Metashape 出错：%s" % (
            errs[-1].get("msg", "").strip().splitlines() or ["未知错误"])[-1])
    if rc != 0:
        raise RuntimeError("按范围线重新导出时 Metashape 异常退出，退出码 %s（0x%08X）" % (rc, rc & 0xFFFFFFFF))
    if not any(e.get("ev") == "done" for e in events):
        raise RuntimeError("按范围线重新导出没跑完（进度文件里没有完成标记）")
    win = ([e for e in events if e.get("ev") == "window"] or [{"region": region}])[0]
    return {"region": win["region"], "ncol": ncol, "nrow": nrow, "steps": stage_secs,
            "rasters": ([e["info"] for e in events if e.get("ev") == "rasters"] or [{}])[0]}


def _raster_box(path):
    t = tiff_strip_info(path)
    return {"left": t["origin"][0], "top": t["origin"][1], "w": t["width"], "h": t["height"], "px": t["px"][0],
            "right": t["origin"][0] + t["width"] * t["px"][0], "bottom": t["origin"][1] - t["height"] * t["px"][1]}


def _survey_clip_info(mode, inset, density):
    return {"mode": mode, "inset": inset, "density": density, "source": "", "line_file": "", "kml": "", "shp": "",
            "polygon": [], "before": None, "after": None, "valid_area": None, "line_area": None, "holes": 0,
            "components": 0, "second_area": 0.0, "steps": [], "points": 0, "inner_rings": 0, "region": None,
            "rasters": {}, "fail_area": None, "valid_cells_area": None, "closed_area": None, "analysis": "",
            "aux_valid": None, "when": "", "set_aside": [], "reclip": False, "secs": None}


def survey_auto_range(psx, metashape, epsg, resolution, dom_raw, site_dir, name, info, inset, density,
                      export_dom=False, use_cache=False, log=None, progress=None, stop_event=None, **timeouts):
    """自动范围线：有分析缓存且 use_cache 就直接用，否则进 Metashape 做裁边分析；再按 🔒 D-32 算线，结果填进 info。"""
    log = log or (lambda s: None)
    cache = Path(site_dir) / (name + SURVEY_CLIP_ANALYSIS)
    an = None
    if use_cache and cache.is_file():
        try:
            an = survey_read_analysis(cache)
            log("用上次的裁边分析（%s，%s 做的），不用再进 Metashape 分析" % (cache.name, an.get("made", "")))
        except (ValueError, OSError, EOFError) as ex:
            log("裁边分析文件读不了（%s），重新分析" % ex)
            an = None
    if an is None:
        an = survey_clip_analyze(psx, metashape, epsg, resolution, dom_raw, site_dir, name, export_dom=export_dom,
                                 log=log, progress=progress, stop_event=stop_event, **timeouts)
        info["steps"].extend(an.get("steps") or [])
    rl = survey_range_from_analysis(an, inset, density, log=log)
    info.update(polygon=rl["polygons"], valid_area=rl["valid_area"], line_area=rl["line_area"], holes=rl["holes"],
                components=rl["components"], second_area=rl["second_area"], points=rl["points"],
                inner_rings=rl["inner_rings"], fail_area=rl["fail_area"], valid_cells_area=rl["valid_cells_area"],
                closed_area=rl["closed_area"], largest_area=rl["largest_area"], analysis=str(cache),
                aux_valid=an.get("aux_valid"), source="自动")

    return info


def survey_clip_rasters(psx, metashape, epsg, resolution, dom, dsm, mode=SURVEY_CLIP_AUTO, clip_file=None,
                        inset=SURVEY_CLIP_INSET, density=SURVEY_CLIP_DENSITY, site_dir=None, name="", report=None,
                        before_dsm=None, before_dom=None, log=None, progress=None, stop_event=None, **timeouts):
    """（整套处理里的）裁边：算范围线（自动 / 用户改过的 / 指定的文件）→ 写 KML + SHP → 只重新导出并按线裁。
    dom、dsm 是要写出来的两张图；没裁的 DSM / DOM 由 before_dsm / before_dom 给（第 1 趟导出的）。返回给报告用的 dict。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    site_dir = Path(site_dir if site_dir is not None else Path(dom).parent)
    before_dsm = Path(before_dsm) if before_dsm else Path(dsm)
    info = _survey_clip_info(mode, inset, density)
    info["before"] = _raster_box(before_dsm)
    user_line = clip_file
    if not user_line and mode == SURVEY_CLIP_AUTO and report:
        user_line = survey_user_range_line(site_dir, name, report)
        if user_line:
            log("发现比 处理报告.txt 新的范围线文件，按用户的线裁：%s" % user_line)
    if user_line:
        info["polygon"] = survey_read_range_line(user_line, epsg)
        info["line_file"] = str(user_line)
        info["source"] = "用户范围线"
        info["line_area"] = survey_polygons_area(info["polygon"])
        log("用范围线文件：%s（%d 个多边形、面积 %.0f m²；原样裁，不再往里收）" % (
            user_line, len(info["polygon"]), info["line_area"]))
    else:
        survey_auto_range(psx, metashape, epsg, resolution, before_dom, site_dir, name, info, inset, density, log=log,
                          progress=lambda f, s: progress(0.5 * f, s), stop_event=stop_event, **timeouts)
        kml = site_dir / (name + SURVEY_RANGE_KML)
        shp = site_dir / (name + SURVEY_RANGE_SHP)
        survey_write_range_kml(kml, info["polygon"], epsg, name)
        survey_write_range_shp(shp, info["polygon"], epsg, name)
        info["kml"], info["shp"] = str(kml), str(shp)
        log("范围线已另存（想手改可以用任何 GIS 软件打开，改完用「按范围线重新裁剪…」只重新导出）：%s；%s（+.shx/.dbf/.prj）" % (
            kml.name, shp.name))
    if not info["polygon"]:
        raise RuntimeError("没有范围线，裁不了")
    tmp_dom = Path(str(dom) + ".裁边中.tif")
    tmp_dsm = Path(str(dsm) + ".裁边中.tif")
    try:
        res = survey_clip_export(metashape, psx, epsg, info["polygon"], resolution, tmp_dom, tmp_dsm,
                                 origin=(info["before"]["left"], info["before"]["top"]), log=log,
                                 progress=lambda f, s: progress(0.5 + 0.5 * f, s), stop_event=stop_event, **timeouts)
        info["steps"].extend(res["steps"])
        info["rasters"] = res.get("rasters") or {}
        info["region"] = res["region"]
        for tmp_path, final in ((tmp_dom, Path(dom)), (tmp_dsm, Path(dsm))):
            tw = tmp_path.with_suffix(".tfw")
            if not tmp_path.is_file() or not tw.is_file():
                raise RuntimeError("按范围线重新导出的文件不在：%s" % tmp_path.name)
            if final.exists():
                final.unlink()
            os.replace(str(tmp_path), str(final))
            os.replace(str(tw), str(final.with_suffix(".tfw")))
        info["after"] = _raster_box(dsm)
        _log_clip_box(log, info["after"])
    finally:
        for f in (tmp_dom, tmp_dsm, tmp_dom.with_suffix(".tfw"), tmp_dsm.with_suffix(".tfw")):
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass
    return info


def _log_clip_box(log, a):
    log("按范围线裁完：外框 东 %.2f～%.2f、北 %.2f～%.2f（%.0f × %.0f 米，%d × %d 像元）" % (
        a["left"], a["right"], a["bottom"], a["top"], a["right"] - a["left"], a["top"] - a["bottom"], a["w"], a["h"]))


SURVEY_RECLIP_SOURCES = ("dir", "file", "auto", "smart")   # 工点目录里的线 / 用户选的文件 / 不换线、按参数重新自动算 /
# smart（命令行不给 --line 时）：工点目录里的线比 处理报告.txt 新（用户改过）就用它，否则重新自动算


def survey_reclip_site(site_dir, metashape, source="dir", line_file=None, inset=SURVEY_CLIP_INSET,
                       density=SURVEY_CLIP_DENSITY, log=None, progress=None, stop_event=None, **timeouts):
    """「按范围线重新裁剪」（D-32，给用户留手动裁的余地）：一个已完成的工点只重新导出 DOM/DSM，不碰空三和建模。
    source：dir（工点目录里的 <工点名>_范围线.shp/.kml，哪个新用哪个）/ file（line_file，任意 KML 或 SHP）/
    auto（不换线，按 inset、density 重新自动算；有分析缓存就不用再进 Metashape 分析）。
    先导到临时名，检查通过才替换 DOM/DSM；旧范围线改名 <工点名>_范围线_上次_<时间>.*，新线按原名写；报告的裁边段改写。
    返回 dict（status：完成 / 失败 / 已取消，reason，clip）。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    site = Path(os.path.abspath(site_dir))
    name = site.name
    t0 = time.time()
    when = datetime.datetime.now()
    stamp = when.strftime("%Y%m%d_%H%M%S")
    res = {"name": name, "dir": str(site), "status": "失败", "reason": "", "clip": None, "secs": None}
    psx, dom, dsm, rep = site / (name + ".psx"), site / (name + "DOM.tif"), site / (name + "DSM.tif"), site / SURVEY_REPORT
    tmp_dom, tmp_dsm = Path(str(dom) + ".裁边中.tif"), Path(str(dsm) + ".裁边中.tif")
    dom_raw = site / (name + "DOM.未裁.tmp.tif")
    log("—— 按范围线重新裁剪：%s（%s）" % (name, {"dir": "用工点目录里的范围线", "file": "用选的文件 %s" % line_file,
                                            "auto": "重新自动算，往里收 %g 米、点云密度门槛 %g%%" % (inset, density),
                                            "smart": "命令行没给 --line"}.get(source, source)))
    try:
        if source not in SURVEY_RECLIP_SOURCES:
            raise ValueError("范围线来源只能是 %s" % "/".join(SURVEY_RECLIP_SOURCES))
        if source == "smart":
            source = "dir" if survey_user_range_line(site, name, rep) else "auto"
            log("工点目录里%s改过的范围线 → %s" % ("有" if source == "dir" else "没有",
                                            "用它" if source == "dir" else "按往里收 %g 米、点云密度门槛 %g%% 重新自动算" % (
                                                inset, density)))
        for f, what in ((psx, "工程 .psx"), (dom, "DOM"), (dsm, "DSM"), (rep, SURVEY_REPORT)):
            if not f.is_file():
                raise ValueError("不是已完成的工点（缺%s）：%s" % (what, f))
        state = survey_read_report(rep).get("状态", "")
        if state not in ("完成", "部分对齐"):
            raise ValueError("处理报告的状态是「%s」，只有完成 / 部分对齐的工点能重新裁剪" % state)
        g = geotiff_info(dom)
        epsg, px = g["epsg"], g["res"][0]
        if not epsg or not (SURVEY_EPSG_MIN <= int(epsg) <= SURVEY_EPSG_MAX):
            raise ValueError("DOM 的坐标系是 EPSG:%s，不是 CGCS2000 3 度带" % epsg)
        origin = g["origin"]                    # 已裁过的 DOM 和没裁的在同一个像元网格上，用它的左上角对齐
        info = _survey_clip_info(SURVEY_CLIP_AUTO if source == "auto" else "file", inset, density)
        info["reclip"], info["when"] = True, when.strftime("%Y-%m-%d %H:%M:%S")
        info["before"] = _raster_box(dsm)
        if source == "auto":
            survey_auto_range(psx, metashape, int(epsg), px, dom_raw, site, name, info, inset, density,
                              export_dom=True, use_cache=True, log=log, progress=lambda f, s: progress(0.5 * f, s),
                              stop_event=stop_event, **timeouts)
            info["source"] = "自动重新算"

        else:
            line = survey_site_range_line(site, name) if source == "dir" else line_file
            if not line or not Path(line).is_file():
                raise ValueError("找不到范围线文件：%s" % (line or (site / (name + SURVEY_RANGE_SHP))))
            info["polygon"] = survey_read_range_line(line, int(epsg))
            info["line_area"] = survey_polygons_area(info["polygon"])
            info["line_file"] = str(Path(line).resolve())
            info["source"] = "工点目录里改过的线" if source == "dir" else "用户选的文件"
            info["inner_rings"] = sum(len(hs) for o, hs in info["polygon"])
            log("范围线：%s（%d 个多边形、%d 个内环（挖掉）、面积 %.0f m²；原样裁，不再往里收、不按点云判据删减）" % (
                line, len(info["polygon"]), info["inner_rings"], info["line_area"]))
        r = survey_clip_export(metashape, psx, int(epsg), info["polygon"], px, tmp_dom, tmp_dsm, origin=origin, log=log,
                               progress=lambda f, s: progress(0.5 + 0.45 * f if source == "auto" else 0.95 * f, s),
                               stop_event=stop_event, **timeouts)
        info["steps"].extend(r["steps"])
        info["rasters"], info["region"] = r.get("rasters") or {}, r["region"]
        for f in (tmp_dom, tmp_dsm):
            for x in (f, f.with_suffix(".tfw")):
                if not x.is_file():
                    raise RuntimeError("按范围线重新导出的文件不在：%s" % x.name)
        progress(0.97, "检查结果")
        problems, rinfo = check_survey_rasters(tmp_dom, tmp_dsm, int(epsg), px)
        problems += check_survey_raster_pair(rinfo)
        if problems:
            raise RuntimeError("重新导出的 DOM/DSM 没通过检查（原来的没动）：" + "；".join(problems))
        # 检查都过了才动原文件：旧线改名保留 → 写新线 → 换 DOM/DSM → 改报告
        info["set_aside"] = [str(p) for p in survey_set_aside_range_lines(site, name, stamp)]
        if info["set_aside"]:
            log("旧范围线改名保留：%s" % "、".join(Path(p).name for p in info["set_aside"]))
        kml, shp = site / (name + SURVEY_RANGE_KML), site / (name + SURVEY_RANGE_SHP)
        survey_write_range_kml(kml, info["polygon"], int(epsg), name)
        survey_write_range_shp(shp, info["polygon"], int(epsg), name)
        info["kml"], info["shp"] = str(kml), str(shp)
        for tmp_path, final in ((tmp_dom, dom), (tmp_dsm, dsm)):
            os.replace(str(tmp_path), str(final))
            os.replace(str(tmp_path.with_suffix(".tfw")), str(final.with_suffix(".tfw")))
        info["after"] = _raster_box(dsm)
        _log_clip_box(log, info["after"])
        log(_reclip_box_note(info["before"], info["after"]).strip())
        info["secs"] = time.time() - t0
        problems, rinfo = check_survey_rasters(dom, dsm, int(epsg), px)
        _survey_report_replace_clip(rep, info, rinfo)
        res.update(status="完成", clip=info)
        log("重新裁剪完成：%s，用时 %.1f 分钟（只重新导出，没重建）" % (name, info["secs"] / 60.0))
    except (AlignCancelled, KeyboardInterrupt):
        res.update(status="已取消", reason="用户取消；原来的 DOM/DSM、范围线都没动")
        log("已取消（原来的 DOM/DSM、范围线都没动）")
    except Exception as ex:
        res.update(status="失败", reason=str(ex))
        log("失败（原来的 DOM/DSM、范围线都没动）：%s" % ex)
    finally:
        res["secs"] = time.time() - t0
        for f in (tmp_dom, tmp_dsm, dom_raw):
            for x in (f, f.with_suffix(".tfw")):
                if x.exists():
                    try:
                        x.unlink()
                    except OSError:
                        pass
    return res


def survey_reclip_sites(out_dir):
    """输出目录里能「按范围线重新裁剪」的工点（有 .psx、DOM、DSM、处理报告且状态完成/部分对齐）：[(工点名, 目录)]。"""
    out = []
    try:
        subs = sorted((d for d in Path(out_dir).iterdir() if d.is_dir()), key=lambda d: d.name)
    except OSError:
        return out
    for d in subs:
        n = d.name
        if all((d / f).is_file() for f in (n + ".psx", n + "DOM.tif", n + "DSM.tif", SURVEY_REPORT)):
            if survey_read_report(d / SURVEY_REPORT).get("状态", "") in ("完成", "部分对齐"):
                out.append((n, str(d)))
    return out


def run_survey_clip(site_dirs, metashape, source="dir", line_file=None, inset=SURVEY_CLIP_INSET,
                    density=SURVEY_CLIP_DENSITY, log=None, progress=None, on_item=None, stop_event=None, **timeouts):
    """多个工点依次「按范围线重新裁剪」；本机有别的 Metashape 在跑就等。返回 {"rows", "exit"}：exit 0 全部成功 / 1 有失败 /
    3 取消。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    on_item = on_item or (lambda d, phase, text: None)
    stopped = lambda: stop_event is not None and stop_event.is_set()
    rows, cancelled = [], False
    for pos, d in enumerate(site_dirs, 1):
        waited = False
        while not stopped() and other_metashape_running():
            if not waited:
                log("本机已有别的 Metashape 在跑，等它结束（每 %d 秒看一次）…" % BATCH_WAIT_POLL)
                waited = True
            if stop_event is not None:
                stop_event.wait(BATCH_WAIT_POLL)
            else:
                time.sleep(BATCH_WAIT_POLL)
        if stopped():
            cancelled = True
            break
        on_item(d, "start", "运行中")
        r = survey_reclip_site(d, metashape, source, line_file, inset, density, log=log,
                               progress=lambda f, s, pos=pos: progress(f, "第 %d/%d 个 · %s" % (pos, len(site_dirs), s)),
                               stop_event=stop_event, **timeouts)
        rows.append(r)
        on_item(d, "done", r["status"])
        if r["status"] == "已取消":
            cancelled = True
            break
    log("")
    for r in rows:
        log("  %s：%s%s（%.1f 分钟）" % (r["name"], r["status"], "，" + r["reason"] if r["reason"] else "", (r["secs"] or 0) / 60.0))
    code = 3 if cancelled else 1 if any(r["status"] != "完成" for r in rows) or len(rows) < len(site_dirs) else 0
    return {"rows": rows, "exit": code, "cancelled": cancelled}


# ---------------------------------------------------------------------------
# 整理交付目录（T14 的做法做进软件）：<交付目录>\<工点名>\ 只放 <工点名>DOM.tif/.tfw、<工点名>DSM.tif/.tfw 四个文件，
# 复制不移动（原结果目录只读，以后还要按范围线重新裁剪），每个文件先写临时名、读回核对 SHA-256 一致才换上，只带修改时间；
# 根目录写 交付清单.csv（UTF-8 带 BOM）。再整理一次时大小、修改时间都没变的文件不重复复制。
# ---------------------------------------------------------------------------
SURVEY_DELIVER_SUFFIX = "_交付"     # 默认交付目录 = 输出目录同级的 <输出目录名>_交付
SURVEY_DELIVER_FILES = ("DOM.tif", "DOM.tfw", "DSM.tif", "DSM.tfw")
SURVEY_DELIVER_CSV = "交付清单.csv"
SURVEY_DELIVER_FIELDS = ("工点", "文件名", "大小(字节)", "SHA-256", "坐标系", "分辨率(米)", "行×列", "DOM与DSM行列一致",
                         "处理状态", "来源", "来源标识", "判据")
# 来源（T17）：Metashape / CC:<Production 目录名>；来源标识（R1）：判断「是不是同一个来源」的稳定 ID
# （CC = Production 目录的完整规范路径），判据（R1）：这次用了哪几组裁边判据
SURVEY_DELIVER_SOURCE_MS = "Metashape"
SURVEY_DELIVER_ID_MS = "Metashape"      # Metashape 出的成果，来源标识就是这个（不是文件路径，但唯一）
# 清单里同一工点各文件的顺序（Metashape 的四个 + CC 的金字塔 .ovr、3SM）
SURVEY_DELIVER_ORDER = ("DOM.tif", "DOM.tfw", "DOM.tif.ovr", "DSM.tif", "DSM.tfw", "DSM.tif.ovr", ".3sm")
SURVEY_DELIVER_CHUNK = 8 * 1024 * 1024
SURVEY_SORTS = ("scan", "photos-desc", "photos-asc")   # 工点顺序：扫描顺序 / 照片数从多到少 / 从少到多（也是处理顺序）


def survey_sort_items(items, how="scan"):
    """按 how 排工点（照片数一样的按扫描序号），返回新列表；序号不变（--pick 仍按扫描序号）。"""
    if how == "photos-desc":
        return sorted(items, key=lambda it: (-it["n"], it["idx"]))
    if how == "photos-asc":
        return sorted(items, key=lambda it: (it["n"], it["idx"]))
    return sorted(items, key=lambda it: it["idx"])


def survey_deliver_default(out_dir):
    p = Path(os.path.abspath(out_dir))
    return p.parent / (p.name + SURVEY_DELIVER_SUFFIX)


def survey_report_srs(path):
    """处理报告「坐标系：」那一行去掉来源说明，如「EPSG:4543 CGCS2000 / 3-degree Gauss-Kruger CM 102E（东坐标不带带号）」。"""
    try:
        for ln in Path(path).read_text(encoding="utf-8-sig").splitlines():
            if ln.startswith("坐标系："):
                return " ".join(ln[len("坐标系："):].split("；来源：")[0].split())
    except OSError:
        pass
    return ""


def survey_deliver_sites(out_dir):
    """输出目录里能交付的工点 [(工点名, 目录, 处理状态)]，和完成了却没有齐 DOM/DSM 的 [(工点名, 原因)]。
    失败、已取消、没有报告的目录（含改名保留的 _上次失败/_上次已取消）不算，不列。"""
    ok, skipped = [], []
    try:
        subs = sorted((d for d in Path(out_dir).iterdir() if d.is_dir()), key=lambda d: d.name)
    except OSError:
        return ok, skipped
    for d in subs:
        n = d.name
        rep = d / SURVEY_REPORT
        if not rep.is_file() or (d / "已取消.txt").exists():
            continue
        verdict = survey_read_report(rep).get("状态", "").split("（")[0].strip()
        if verdict not in ("完成", "部分对齐"):
            continue
        miss = [n + f for f in SURVEY_DELIVER_FILES if not (d / (n + f)).is_file()]
        if len(miss) == len(SURVEY_DELIVER_FILES):
            skipped.append((n, "没有 DOM/DSM（处理时没勾）"))
        elif miss:
            skipped.append((n, "缺 %s" % "、".join(miss)))
        else:
            ok.append((n, str(d), verdict))
    return ok, skipped


def _file_sha256(path, stopped, on_bytes):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if stopped():
                raise AlignCancelled()
            b = f.read(SURVEY_DELIVER_CHUNK)
            if not b:
                return h.hexdigest()
            h.update(b)
            on_bytes(len(b))


def _deliver_copy(src, dst, stopped, on_bytes):
    """src 复制成 dst：先写 <dst>.tmp_part，只带修改时间（不带只读属性），读回 SHA-256 与边复制边算的源文件一致才换上。
    返回 SHA-256；对不上抛 ValueError（dst 原样不动）。"""
    tmp = Path(str(dst) + ".tmp_part")
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                if stopped():
                    raise AlignCancelled()
                b = fi.read(SURVEY_DELIVER_CHUNK)
                if not b:
                    break
                h.update(b)
                fo.write(b)
                on_bytes(len(b))
        st = os.stat(src)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        got = _file_sha256(tmp, stopped, lambda k: None)
        if got != h.hexdigest():
            raise ValueError("复制后 SHA-256 对不上（%s），没换上" % Path(dst).name)
        os.replace(tmp, dst)
        return got
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _read_deliver_csv(path):
    rows = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("工点") and r.get("文件名"):
                    if not (r.get("来源") or "").strip():   # v1.4 的清单没有「来源」列，那时只有 Metashape 的
                        r["来源"] = SURVEY_DELIVER_SOURCE_MS
                    if not (r.get("来源标识") or "").strip() and r["来源"] == SURVEY_DELIVER_SOURCE_MS:
                        r["来源标识"] = SURVEY_DELIVER_ID_MS
                    rows[(r["工点"], r["文件名"])] = r
    except (OSError, csv.Error, UnicodeDecodeError):
        pass
    return rows


def _write_deliver_csv(dest, old, new_rows, keep=()):
    """写 交付清单.csv：这次整理的工点（new_rows）+ 上次清单里还在交付目录里的别的工点；这次失败的不列，
    keep 里的工点（来源不同、没覆盖的）照旧保留上次的行。返回 (所有行, 工点数)。"""
    dest = Path(dest)
    gone = set(new_rows)
    allrows = {}
    for (n, fn), r in old.items():
        if (n not in gone or n in keep) and (dest / n / fn).is_file():
            allrows.setdefault(n, []).append(r)
    allrows.update(new_rows)
    order = {f: i for i, f in enumerate(SURVEY_DELIVER_ORDER)}
    out_rows = []
    for n in sorted(allrows):
        out_rows += sorted(allrows[n], key=lambda r, n=n: (order.get(r["文件名"][len(n):], 9), r["文件名"]))
    csv_path = dest / SURVEY_DELIVER_CSV
    tmp = Path(str(csv_path) + ".tmp_part")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fcsv:
        w = csv.DictWriter(fcsv, fieldnames=SURVEY_DELIVER_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    os.replace(tmp, csv_path)
    return out_rows, len(allrows)


def _survey_deliver_criteria(site_dir):
    """Metashape 成果用了哪组裁边判据（R1⑤：清单要写明）。从工点的 处理报告.txt 的裁边段读。"""
    try:
        text = (Path(site_dir) / SURVEY_REPORT).read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("裁边："):
            if "用户范围线" in s:
                return "按用户范围线裁（不按点云判据）"
            if s.startswith("裁边：自动"):
                return "自动裁边（①②③真实点云）"
            return "按范围线裁剪"
    return ""


def survey_deliver(out_dir, dest=None, sites=None, log=None, progress=None, stop_event=None):
    """把输出目录里完成/部分对齐、DOM/DSM 齐的工点整理到交付目录（默认 <输出目录>_交付）。sites=None：全部能交付的；
    给工点名列表就只整理这些（交付目录里别的工点照旧留在清单里）。交付目录里别的文件不动、不删。
    返回 {"dest", "csv", "sites", "skipped", "failed", "copied", "same", "bytes", "cancelled", "exit"}；
    exit：0 全部成功 / 1 有工点没做成 / 3 取消。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    stopped = lambda: stop_event is not None and stop_event.is_set()
    out = Path(os.path.abspath(out_dir))
    dest = Path(os.path.abspath(dest)) if dest else survey_deliver_default(out)
    if not out.is_dir():
        raise ValueError("输出目录不存在：%s" % out)
    if dest == out or out in dest.parents or dest in out.parents:
        raise ValueError("交付目录不能是输出目录本身，也不能在它里面或包含它：%s" % dest)
    ok, skipped = survey_deliver_sites(out)
    if sites is not None:
        want = set(sites)
        ok = [x for x in ok if x[0] in want]
        skipped = [x for x in skipped if x[0] in want]
    csv_path = dest / SURVEY_DELIVER_CSV
    old = _read_deliver_csv(csv_path)
    dest.mkdir(parents=True, exist_ok=True)
    log("—— 整理交付目录：%s → %s（%d 个工点，每个只放 DOM/DSM 四个文件，复制、不动原结果）" % (out, dest, len(ok)))
    for n, why in skipped:
        log("  跳过 %s：%s" % (n, why))
    total = sum((Path(d) / (n + f)).stat().st_size for n, d, _ in ok for f in SURVEY_DELIVER_FILES) or 1
    done_bytes = [0]

    def on_bytes(k):
        done_bytes[0] += k
        progress(min(done_bytes[0] / float(total), 1.0), "整理交付 %.0f/%.0f MB" % (done_bytes[0] / 1048576.0,
                                                                                total / 1048576.0))

    new_rows, failed, copied, same, cancelled = {}, [], 0, 0, False
    kept = set()
    for n, d, verdict in ok:
        if stopped():
            cancelled = True
            break
        cc = sorted({old[(n, n + f)]["来源"] for f in SURVEY_DELIVER_FILES if (n, n + f) in old and
                     (dest / n / (n + f)).is_file() and old[(n, n + f)]["来源"] != SURVEY_DELIVER_SOURCE_MS})
        if cc:   # T17 🔒：交付目录里已是别的来源（CC 成果）的同名文件，不覆盖
            kept.add(n)
            failed.append((n, "交付目录里是 %s 的成果，没覆盖" % "、".join(cc)))
            log("  %s：交付目录里这个工点的 DOM/DSM 来源是 %s，没覆盖（要换回 Metashape 的，先把交付目录里这几个文件挪走）" % (
                n, "、".join(cc)))
            continue
        try:
            sd = dest / n
            sd.mkdir(exist_ok=True)
            info = {}
            for kind in ("DOM", "DSM"):
                g = geotiff_info(Path(d) / (n + kind + ".tif"))
                g["tfw"] = read_tfw(Path(d) / (n + kind + ".tfw"))
                info[kind] = g
            pair = check_survey_raster_pair(info)
            srs = survey_report_srs(Path(d) / SURVEY_REPORT)
            rows, k_copy, k_same = [], 0, 0
            for f in SURVEY_DELIVER_FILES:
                src, dst = Path(d) / (n + f), sd / (n + f)
                s_st = src.stat()
                sha = None
                if dst.is_file():
                    d_st = dst.stat()
                    if d_st.st_size == s_st.st_size and d_st.st_mtime_ns == s_st.st_mtime_ns:
                        prev = old.get((n, dst.name))
                        if prev and prev.get("大小(字节)") == str(s_st.st_size) and prev.get("SHA-256"):
                            sha = prev["SHA-256"]        # 上次复制时核对过，大小和修改时间都没变
                            on_bytes(s_st.st_size)
                        else:
                            a = _file_sha256(src, stopped, on_bytes)
                            if a == _file_sha256(dst, stopped, lambda k: None):
                                sha = a
                if sha:
                    k_same += 1
                else:
                    sha = _deliver_copy(src, dst, stopped, on_bytes)
                    k_copy += 1
                g = info[f[:3]]
                rows.append({"工点": n, "文件名": dst.name, "大小(字节)": s_st.st_size, "SHA-256": sha, "坐标系": srs,
                             "分辨率(米)": "%g" % abs(g["tfw"][0]),
                             "行×列": "%d×%d" % (g["height"], g["width"]) if f.endswith(".tif") else "",
                             "DOM与DSM行列一致": "否" if pair else "是", "处理状态": verdict,
                             "来源": SURVEY_DELIVER_SOURCE_MS, "来源标识": SURVEY_DELIVER_ID_MS,
                             "判据": _survey_deliver_criteria(d)})
            new_rows[n] = rows
            copied += k_copy
            same += k_same
            extra = sorted(x.name for x in sd.iterdir() if x.name not in {n + f for f in SURVEY_DELIVER_FILES})
            log("  %s：复制 %d 个、已是最新 %d 个；%s；%s 米；%s 像元%s" % (
                n, k_copy, k_same, srs or "坐标系（报告里没写）", rows[0]["分辨率(米)"], rows[0]["行×列"],
                "" if verdict == "完成" else "（%s）" % verdict))
            for p in pair:
                log("    注意：%s" % p)
            if extra:
                log("    注意：交付目录里这个工点下还有别的文件，没动：%s" % "、".join(extra))
        except AlignCancelled:
            cancelled = True
            break
        except (OSError, ValueError, struct.error) as ex:
            failed.append((n, str(ex)))
            log("  %s：失败：%s" % (n, ex))
    # 清单：这次整理的 + 交付目录里还在、上次清单里有的别的工点（这次失败的不列；来源不同没覆盖的照旧列）
    for n, _ in failed:
        if n not in kept:
            new_rows.pop(n, None)
    out_rows, n_sites = _write_deliver_csv(dest, {k: v for k, v in old.items() if k[0] not in
                                                  {n for n, _ in failed if n not in kept}}, new_rows, kept)
    nbytes = sum(int(r.get("大小(字节)") or 0) for r in out_rows)
    log("交付目录：%s；清单里 %d 个工点、%d 个文件，共 %.1f MB；这次复制 %d 个、已是最新 %d 个%s%s" % (
        dest, n_sites, len(out_rows), nbytes / 1048576.0, copied, same,
        "；失败 %d 个工点" % len(failed) if failed else "", "；已取消，剩下的没整理" if cancelled else ""))
    code = 3 if cancelled else 1 if failed else 0
    return {"dest": str(dest), "csv": str(csv_path), "sites": sorted(new_rows), "skipped": skipped, "failed": failed,
            "copied": copied, "same": same, "bytes": nbytes, "cancelled": cancelled, "exit": code}


# ---------------------------------------------------------------------------
# 整理 CC 成果（T17）：CC（iTwin Capture Modeler）的 Productions\Production_* 目录名不带工点名，按外框和坐标系认出是哪个
# 工点（🔒：成果外框和工点范围的重叠面积 ÷ 成果外框面积，EPSG 必须相同；最高 ≥ 0.5 且第二名 < 最高 × 0.5 才自动认，
# 否则让用户选）→ DOM/DSM 按 D-32 的判据裁边（不重采样，留在 CC 的像元网格上，线外 DOM 透明 / DSM NoData，金字塔重新
# 生成）→ 连同 3SM（逐字节复制、改名）放进 v1.4 的交付目录。栅格读写、裁剪、金字塔用 rasterio（D-34，只在函数里 import），
# 按 512 行一条的窗口读写，不整张读进内存。
# ---------------------------------------------------------------------------
CC_DOM_TAIL = "_ortho_merge.tif"     # CC 合并后的正射
CC_DSM_TAIL = "_DSM_merge.tif"       # CC 合并后的 DSM
CC_KIND_ORTHO = "正射/DSM"
CC_KIND_3SM = "3SM"
CC_AUTO_MIN = 0.5                    # 🔒 最高分 ≥ 0.5
CC_AUTO_RATIO = 0.5                  # 🔒 且第二名 < 最高分 × 0.5 才自动认定
CC_RANGE_KML = "_CC范围线.kml"       # 范围线放在输出目录的工点目录里（不进交付目录），和 v1.3 的 _范围线 不冲突
CC_RANGE_SHP = "_CC范围线.shp"
CC_AUX_TMP = "_CC辅助DSM.不插值.tmp.tif"
CC_DSM_FRAC = 0.9                    # 判据④（工点没有点云时）：格子里 CC DSM 有值（≠ NoData）的像元 ≥ 9 成
CC_STRIP_ROWS = 512                  # 按这么多行一条读写（= CC 和输出的分块高度）
CC_OVR_MIN = 256                     # 金字塔一直做到长边 < 这么多像元
CC_SKIP_LABEL = "（不整理）"


class CCRejected(Exception):
    """扫 Production 时发现的问题（坐标系冲突等）：这个成果不整理，理由写在清单和报告里。"""


CC_CLIP_STAGE = {"cover_dom": "判据① DOM 有影像", "cover_dsm": "判据④ DSM 有值", "cloud": "判据②③ 真实点云",
                 "range": "算范围线", "dom": "裁 DOM", "dsm": "裁 DSM", "ovr": "金字塔"}


def cc_source(prod):
    """清单里显示的来源列（会重名）：CC:<Production 目录名>。"""
    return "CC:" + prod


def cc_source_id(pdir):
    """来源标识（R1）：判断「是不是同一个来源」的稳定 ID = Production 目录的完整规范路径。
    跨工程同名（两个工程里都有 Production_2）靠这个区分；不是路径的（Metashape）用常量。"""
    return os.path.normcase(os.path.abspath(str(pdir)))


def _deliver_row_id(row):
    """清单里一行的来源标识：老清单没有这一列时，Metashape 的按 Metashape 算，CC 的算「不明」（宁可拒绝覆盖）。"""
    if not row:
        return ""
    sid = (row.get("来源标识") or "").strip()
    if sid:
        return sid
    src = (row.get("来源") or "").strip()
    if src == SURVEY_DELIVER_SOURCE_MS:
        return SURVEY_DELIVER_ID_MS
    return ""


def _cc_epsg_of(text):
    m = re.fullmatch(r"\s*EPSG\s*:+\s*(\d+)\s*", text or "", re.I)
    return int(m.group(1)) if m else None


def _cc_is_production(d):
    try:
        return any(f.is_file() and f.name.lower().endswith((CC_DOM_TAIL.lower(), CC_DSM_TAIL.lower(), ".3sm"))
                   for f in Path(d).iterdir())
    except OSError:
        return False


def cc_production_dirs(paths):
    """用户给的目录 → Production 目录列表（去重，保持顺序）。可以给 CC 工程目录（找它下面的 Productions\\）、
    Productions 目录，或某个 Production_* 目录本身。找不到抛 ValueError。"""
    out = []
    for p in paths:
        p = Path(os.path.abspath(str(p).strip().strip('"')))
        if not p.is_dir():
            raise ValueError("目录不存在：%s" % p)
        if (p / "Productions").is_dir():
            base = p / "Productions"
        elif p.name.lower() == "productions":
            base = p
        elif _cc_is_production(p):
            out.append(p)
            continue
        else:
            raise ValueError("这里既不是 CC 工程目录（下面没有 Productions），也不是 Productions 或 Production 目录：%s" % p)
        subs = sorted((d for d in base.iterdir() if d.is_dir()), key=lambda d: d.name)
        if not subs:
            raise ValueError("Productions 下面没有成果目录：%s" % base)
        out += subs
    seen, uniq = set(), []
    for d in out:
        k = os.path.normcase(str(d))
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq


def cc_3sm_extent_from_con(con):
    """3SM（SQLite）根节点外框：SMNodeHeader 表 NodeId=0 的 Extent = 6 个小端 double（xmin, ymin, zmin, xmax, ymax, zmax）。"""
    row = con.execute("SELECT Extent FROM SMNodeHeader WHERE NodeId=0").fetchone()
    if not row or row[0] is None or len(row[0]) < 48:
        raise ValueError("3SM 里没有根节点外框（SMNodeHeader 表 NodeId=0 的 Extent）")
    v = struct.unpack("<6d", bytes(row[0][:48]))
    if not all(math.isfinite(x) for x in v) or v[3] <= v[0] or v[4] <= v[1]:
        raise ValueError("3SM 根节点外框不对：%s" % (v,))
    return v


def cc_read_3sm_extent(path):
    """只读打开 3SM（URI mode=ro&immutable=1，不会在 CC 目录里写 -journal），读根节点外框。"""
    import sqlite3
    from urllib.parse import quote
    uri = "file:%s?mode=ro&immutable=1" % quote(os.path.abspath(path).replace("\\", "/"), safe="/:")
    con = sqlite3.connect(uri, uri=True)
    try:
        return cc_3sm_extent_from_con(con)
    except sqlite3.Error as ex:
        raise ValueError("3SM 读不了（%s）：%s" % (ex, path))
    finally:
        con.close()


def _cc_tif_grid(tif):
    """CC 的 tif：外框按 .tfw（像元中心）+ 行列数（GeoTIFF 头）算。返回 dict：left、top、px、w、h、box、epsg（GeoTIFF 键）。"""
    tif = Path(tif)
    tfw = tif.with_suffix(".tfw")
    if not tfw.is_file():
        raise ValueError("缺 .tfw：%s" % tfw.name)
    t = read_tfw(tfw)
    if abs(t[1]) > 1e-12 or abs(t[2]) > 1e-12 or t[0] <= 0 or t[3] >= 0:
        raise ValueError(".tfw 有旋转或像元方向不对：%s" % tfw.name)
    if abs(t[0] + t[3]) > 1e-12:
        raise ValueError(".tfw 的像元不是正方形：%s" % tfw.name)
    g = geotiff_info(tif)
    left, top = t[4] - t[0] / 2.0, t[5] - t[3] / 2.0
    w, h = g["width"], g["height"]
    return {"left": left, "top": top, "px": t[0], "w": w, "h": h, "epsg": g["epsg"], "bands": g["bands"],
            "dtype": g["dtype"], "box": (left, top - h * t[0], left + w * t[0], top)}


def cc_scan_production(pdir):
    """一个 Production 目录 → 成果行（dict）列表。status 不空的行不处理（未合并、坐标系不明、读不了……）。"""
    pdir = Path(pdir)
    proj = pdir.parent.parent.name if pdir.parent.name.lower() == "productions" else pdir.parent.name
    base = {"key": "%s/%s" % (proj, pdir.name), "prod": pdir.name, "project": proj, "dir": str(pdir),
            "source": cc_source(pdir.name), "source_id": cc_source_id(pdir),
            "kind": "", "dom": "", "dsm": "", "sm": "", "epsg": None, "box": None,
            "grid": None, "status": "", "scores": [], "score": 0.0, "second": None, "site": "", "how": "", "why": "",
            "map": ""}
    try:
        files = sorted((f for f in pdir.iterdir() if f.is_file()), key=lambda f: f.name)
    except OSError as ex:
        return [dict(base, status="读不了目录：%s" % ex)]
    doms = [f for f in files if f.name.lower().endswith(CC_DOM_TAIL.lower())]
    dsms = [f for f in files if f.name.lower().endswith(CC_DSM_TAIL.lower())]
    sms = [f for f in files if f.suffix.lower() == ".3sm"]
    rows = []
    if doms or dsms:
        r = dict(base, kind=CC_KIND_ORTHO, dom=str(doms[0]) if doms else "", dsm=str(dsms[0]) if dsms else "")
        rows.append(r)
        try:
            if len(doms) > 1 or len(dsms) > 1:
                raise ValueError("一个 Production 里有 %d 个 *%s、%d 个 *%s，不知道用哪个" % (
                    len(doms), CC_DOM_TAIL, len(dsms), CC_DSM_TAIL))
            grids = {}
            for k in ("dom", "dsm"):
                if r[k]:
                    grids[k] = _cc_tif_grid(r[k])
            if len(grids) == 2:
                a, b = grids["dom"], grids["dsm"]
                if (a["w"], a["h"]) != (b["w"], b["h"]) or abs(a["left"] - b["left"]) > 1e-6 or \
                        abs(a["top"] - b["top"]) > 1e-6 or abs(a["px"] - b["px"]) > 1e-9:
                    raise ValueError("DOM 和 DSM 不在同一个像元网格上（左上角、像元或行列数不同）")
            if r["dom"]:
                g = grids["dom"]
                if g["dtype"] != "uint8" or g["bands"] not in (3, 4):
                    raise ValueError("DOM 应该是 3 或 4 波段 8 位，这个是 %d 波段 %s" % (g["bands"], g["dtype"]))
            if r["dsm"] and (grids["dsm"]["dtype"] != "float32" or grids["dsm"]["bands"] != 1):
                raise ValueError("DSM 应该是 1 波段 float32")
            g = grids.get("dom") or grids["dsm"]
            r.update(grid=g, box=g["box"])
            cs = pdir / "orthoPhoto" / "ContextScene.json"
            ej = None
            if cs.is_file():
                try:
                    j = json.loads(cs.read_text(encoding="utf-8-sig"))
                    es = {_cc_epsg_of((v or {}).get("Definition")) for v in
                          (j.get("SpatialReferenceSystems") or {}).values()}
                    if len(es) == 1:
                        ej = es.pop()
                except (ValueError, AttributeError, OSError):
                    ej = None
            # R1③：多个 TIFF 的 EPSG 分「缺失 / 一致 / 冲突」三种状态，不能压成 None 混同「读不出」；
            # 已知冲突直接拒绝（不整理），ContextScene.json 只补缺失，不覆盖 GeoTIFF 里的值。
            et = {g2["epsg"] for g2 in grids.values() if g2["epsg"]}
            if len(et) > 1:
                raise CCRejected("坐标系冲突（DOM / DSM 的 GeoTIFF 里 EPSG 不一样：%s）" % "、".join(
                    "EPSG:%s" % e for e in sorted(et)))
            et = et.pop() if len(et) == 1 else None
            if ej and et and ej != et:
                raise CCRejected("坐标系冲突（ContextScene.json 写 EPSG:%s、GeoTIFF 写 EPSG:%s）" % (ej, et))
            if not (ej or et):
                r["status"] = "坐标系不明（ContextScene.json 和 GeoTIFF 都读不出 EPSG）"
            else:
                r["epsg"] = et if et else ej
        except CCRejected as ex:
            r["status"] = str(ex)
        except (ValueError, OSError, struct.error) as ex:
            r["status"] = "读不了：%s" % ex
    for f in sms:
        r = dict(base, kind=CC_KIND_3SM, sm=str(f))
        rows.append(r)
        try:
            v = cc_read_3sm_extent(f)
            r["box"] = (v[0], v[1], v[3], v[4])
            md = pdir / "metadata.xml"
            e = None
            if md.is_file():
                try:
                    e = _cc_epsg_of(ET.parse(str(md)).getroot().findtext("SRS"))
                except ET.ParseError:
                    e = None
            if e:
                r["epsg"] = e
            else:
                r["status"] = "坐标系不明（metadata.xml 里读不出 EPSG）"
        except (ValueError, OSError) as ex:
            r["status"] = "读不了：%s" % ex
    if not rows:
        tiles = False
        try:
            for k, f in enumerate(pdir.rglob("*")):
                if f.suffix.lower() in (".tif", ".tiff"):
                    tiles = True
                    break
                if k > 20000:
                    break
        except OSError:
            pass
        rows.append(dict(base, status="未合并，跳过（只有分块，没有 *_merge.tif）" if tiles else
                         "不是正射/DSM 或 3SM 成果，跳过"))
    return rows


_CC_BX_CACHE = {}


def cc_bx_extent(xml_path):
    """工点的 Blocks Exchange：Block 用的坐标系 EPSG、已对齐相机（XML 里只写已对齐的，D-12）照片中心的平面外接矩形。
    读到 <TiePoints> 就停（照片在前面），几百 MB 的 XML 也只读开头。返回 (epsg 或 None, (x0, y0, x1, y1), 相机数)。"""
    st = os.stat(xml_path)
    key = (os.path.normcase(os.path.abspath(xml_path)), st.st_size, st.st_mtime_ns)
    if key in _CC_BX_CACHE:
        return _CC_BX_CACHE[key]
    srs, block_srs, xs, ys = {}, None, [], []
    stack = []
    for event, el in ET.iterparse(str(xml_path), events=("start", "end")):
        if event == "start":
            if el.tag == "TiePoints":
                break
            stack.append(el.tag)
            continue
        stack.pop()
        parent = stack[-1] if stack else ""
        if el.tag == "SRS":
            srs[(el.findtext("Id") or "").strip()] = el.findtext("Definition") or ""
        elif el.tag == "SRSId" and parent == "Block":
            block_srs = (el.text or "").strip()
        elif el.tag == "Center" and parent == "Pose":
            try:
                xs.append(float(el.findtext("x")))
                ys.append(float(el.findtext("y")))
            except (TypeError, ValueError):
                pass
        elif el.tag == "Photo":
            el.clear()
    if not xs:
        raise ValueError("XML 里没有已对齐相机的照片中心")
    epsg = _cc_epsg_of(srs.get(block_srs)) if block_srs is not None else None
    res = (epsg, (min(xs), min(ys), max(xs), max(ys)), len(xs))
    _CC_BX_CACHE[key] = res
    return res


def _psx_has_dense_cloud(psx):
    """.psx 工程里有没有密集点云（看各 frame.zip 的 doc.xml 里有没有 <dense_cloud>），不用开 Metashape。"""
    base = Path(str(psx)[:-4] + ".files")
    for z in sorted(base.glob("*/*/frame.zip")):
        try:
            with zipfile.ZipFile(str(z)) as zf:
                if b"<dense_cloud" in zf.read("doc.xml"):
                    return True
        except (OSError, KeyError, zipfile.BadZipFile):
            continue
    return False


def cc_candidate_sites(out_dir, log=None):
    """候选工点（🔒）：影像处理输出目录里状态为完成 / 部分对齐、有 _BlocksExchange.xml 的工点；
    范围 = XML 里已对齐相机的平面外接矩形。返回 [dict(name, dir, epsg, box, cams, verdict, psx)]。"""
    log = log or (lambda s: None)
    out = []
    try:
        subs = sorted((d for d in Path(out_dir).iterdir() if d.is_dir()), key=lambda d: d.name)
    except OSError:
        return out
    for d in subs:
        n = d.name
        rep, xml = d / SURVEY_REPORT, d / (n + "_BlocksExchange.xml")
        if not rep.is_file() or not xml.is_file() or (d / "已取消.txt").exists():
            continue
        verdict = survey_read_report(rep).get("状态", "").split("（")[0].strip()
        if verdict not in ("完成", "部分对齐"):
            continue
        try:
            epsg, box, ncam = cc_bx_extent(xml)
        except (ValueError, OSError, ET.ParseError) as ex:
            log("  候选工点 %s 的 XML 读不了，不参与：%s" % (n, ex))
            continue
        psx = d / (n + ".psx")
        out.append({"name": n, "dir": str(d), "epsg": epsg, "box": box, "cams": ncam, "verdict": verdict,
                    "psx": str(psx) if psx.is_file() else ""})
    return out


def cc_overlap_score(box, site_box):
    """🔒 打分 = 成果外框与工点范围的重叠面积 ÷ 成果外框面积。"""
    w = min(box[2], site_box[2]) - max(box[0], site_box[0])
    h = min(box[3], site_box[3]) - max(box[1], site_box[1])
    area = (box[2] - box[0]) * (box[3] - box[1])
    if w <= 0 or h <= 0 or area <= 0:
        return 0.0
    return w * h / area


def parse_cc_map(text):
    """--map「P=工点,P2=工点2」（可以给多次）→ {P: 工点}。P 是 Production 目录名、「工程目录名/Production 名」或完整路径。"""
    out = {}
    for item in (text if isinstance(text, (list, tuple)) else [text]):
        for part in re.split(r"[,，]", item or ""):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                raise ValueError("--map 要写成 Production名=工点名，这个不对：%s" % part)
            k, v = part.split("=", 1)
            k, v = k.strip().strip('"'), v.strip().strip('"')
            if not k or not v:
                raise ValueError("--map 要写成 Production名=工点名，这个不对：%s" % part)
            out[k] = v
    return out


def cc_assign(rows, sites, mapping=None):
    """按 🔒 打分认工点；mapping（用户指定，界面下拉或 --map）优先。改 rows 里的 site / how / why / scores。
    用户指定的键对不上成果、工点不是候选、两个同类成果都指定给同一工点 → ValueError。"""
    by_name = {s["name"]: s for s in sites}
    for r in rows:
        r.update(site="", how="", why="", scores=[], score=0.0, second=None, map="")
    for k, v in (mapping or {}).items():
        hits = [r for r in rows if r["kind"] and (k in (r["key"], r["prod"], r["dir"]) or
                                                   os.path.normcase(os.path.abspath(k)) == os.path.normcase(r["dir"]))]
        if not hits:
            raise ValueError("指定的「%s」没对上任何成果（写 Production 目录名，多个工程同名时写 工程目录名/Production 名）" % k)
        if len({r["dir"] for r in hits}) > 1:
            raise ValueError("指定的「%s」对上了多个工程的成果（%s），请写成 工程目录名/Production 名" % (
                k, "、".join(sorted({r["key"] for r in hits}))))
        if v != CC_SKIP_LABEL and v not in by_name:
            raise ValueError("指定的工点「%s」不在输出目录的候选工点里（要完成 / 部分对齐、有 _BlocksExchange.xml）" % v)
        for r in hits:
            r["map"] = v
    for r in rows:
        if not r["kind"] or r["status"]:
            continue
        r["scores"] = sorted(((s["name"], cc_overlap_score(r["box"], s["box"])) for s in sites if s["epsg"] == r["epsg"]),
                             key=lambda x: -x[1])
        best = r["scores"][0] if r["scores"] else ("", 0.0)
        r["score"] = best[1]
        r["second"] = r["scores"][1] if len(r["scores"]) > 1 else None
        sec = r["second"][1] if r["second"] else 0.0
        if r["map"]:
            if r["map"] == CC_SKIP_LABEL:
                r.update(how="用户指定不整理", why="用户指定不整理")
                continue
            s = by_name[r["map"]]
            if s["epsg"] != r["epsg"]:
                raise ValueError("「%s」是 EPSG:%s，指定的工点「%s」是 EPSG:%s，坐标系不同，不能放一起" % (
                    r["key"], r["epsg"], s["name"], s["epsg"]))
            r.update(site=s["name"], how="用户指定")
        elif not r["scores"]:
            r["why"] = "没有同坐标系（EPSG:%s）的候选工点" % r["epsg"]
        elif best[1] >= CC_AUTO_MIN and sec < best[1] * CC_AUTO_RATIO:
            r.update(site=best[0], how="自动")
        elif best[1] < CC_AUTO_MIN:
            r["why"] = "最高分 %.2f < %g，认不准" % (best[1], CC_AUTO_MIN)
        else:
            r["why"] = "第二名「%s」%.2f ≥ 最高分 %.2f 的一半，认不准" % (r["second"][0], sec, best[1])
    groups = {}
    for r in rows:
        if r["site"]:
            groups.setdefault((r["site"], r["kind"]), []).append(r)
    for (site, kind), rs in groups.items():
        if len(rs) < 2:
            continue
        if sum(r["how"] == "用户指定" for r in rs) > 1:
            raise ValueError("%d 个%s成果都指定给了「%s」，一个工点只能放一个：%s" % (
                len(rs), kind, site, "、".join(r["key"] for r in rs)))
        for r in rs:
            if r["how"] == "自动":   # 🔒 同一工点认到多个同类成果：不猜
                r.update(site="", how="", why="同一工点「%s」认到 %d 个%s成果，不知道用哪个" % (site, len(rs), kind))
    return rows


def cc_row_state(r):
    """表格/日志里的状态文字（R2-F5：只写结果，阈值公式和判定解释进 README / 报告 / 日志）。"""
    if r["status"]:
        return r["status"]
    if r["how"] == "用户指定不整理":
        return "不整理（用户指定）"
    if not r["site"]:
        return "需选择工点"
    return "待整理（%s%s）" % (r["how"], "，得分 %.2f" % r["score"] if r["how"] == "自动" else "")


def cc_box_text(box):
    return "东 %.0f～%.0f、北 %.0f～%.0f" % (box[0], box[2], box[1], box[3]) if box else ""


def cc_targets(r, site):
    """这个成果要放进交付目录的文件：[(交付文件名, 不裁时的源文件)]。"""
    out = []
    if r["kind"] == CC_KIND_3SM:
        out.append((site + ".3sm", r["sm"]))
    else:
        for k, src in (("DOM", r["dom"]), ("DSM", r["dsm"])):
            if src:
                out += [(site + k + ".tif", src), (site + k + ".tfw", str(Path(src).with_suffix(".tfw"))),
                        (site + k + ".tif.ovr", src + ".ovr")]
    return out


def cc_conflicts(rows, dest):
    """🔒 交付目录里该工点已有同名文件、但来源不同（如 v1.4 放进去的 Metashape DOM）→ [(行, 文件名, 原来源)]。

    R1：判「来源不同」不再比会重名的显示文本（CC:Production_2），改比稳定的「来源标识」
    （CC = Production 目录的完整规范路径，Metashape = 常量）。清单里没有来源标识的老行、
    或者来源标识读不出来 → 一律当作「来源不明」，不覆盖（🔒：不同来源不覆盖，宁可停下问）。"""
    dest = Path(dest)
    old = _read_deliver_csv(dest / SURVEY_DELIVER_CSV)
    out = []
    for r in rows:
        if not r["site"] or r["status"]:
            continue
        rid = r.get("source_id") or ""
        for fn, _ in cc_targets(r, r["site"]):
            p = dest / r["site"] / fn
            if not p.is_file():
                continue
            row = old.get((r["site"], fn))
            src = (row or {}).get("来源") or "（清单里没有，来源不明）"
            old_id = _deliver_row_id(row)
            if not rid or not old_id:
                out.append((r, fn, "%s（来源不明）" % src))
            elif os.path.normcase(old_id) != os.path.normcase(rid):
                out.append((r, fn, src))
    return out


def cc_plan(paths, out_dir, mapping=None, log=None):
    """扫描：成果 + 候选工点 + 认定。返回 {"rows", "sites", "prods"}。"""
    prods = cc_production_dirs(paths)
    sites = cc_candidate_sites(out_dir, log=log)
    rows = []
    for p in prods:
        rows += cc_scan_production(p)
    cc_assign(rows, sites, mapping)
    return {"rows": rows, "sites": sites, "prods": [str(p) for p in prods]}


def cc_plan_lines(plan):
    """计划表（日志 / --plan 用）。"""
    lines = ["候选工点 %d 个（输出目录里完成 / 部分对齐、有 _BlocksExchange.xml 的）：" % len(plan["sites"])]
    for s in plan["sites"]:
        lines.append("  %s：EPSG:%s，%d 台已对齐相机，%s" % (s["name"], s["epsg"], s["cams"], cc_box_text(s["box"])))
    lines.append("CC 成果 %d 个：" % len(plan["rows"]))
    for r in plan["rows"]:
        lines.append("  %s %s：%s%s；%s" % (
            r["key"], r["kind"] or "-", "EPSG:%s，" % r["epsg"] if r["epsg"] else "", cc_box_text(r["box"]) or "-",
            ("要选工点" if (not r["site"] and not r["status"]) else cc_row_state(r))))
        if not r["site"] and r["why"]:
            lines.append("    说明：%s" % r["why"])
        if r["site"]:
            lines.append("    → %s" % r["site"])
        if r["scores"]:
            lines.append("    得分：%s" % "；".join("%s %.3f" % x for x in r["scores"][:3]))
    return lines


# ---- 裁边：分析网格（🔒 同 D-32：0.5 米格，原点 = CC DOM 左上角）----
def _cc_cell_index(n_px, px, cell, n_cells):
    """每个像元落在哪一格（按像元中心，同 _grid_bounds）、每格多少个像元、每格第一个像元号。"""
    import numpy as np
    b = _grid_bounds(n_px, px, cell, n_cells)
    idx = np.zeros(n_px, dtype=np.int64)
    for k in range(n_cells):
        idx[b[k]:b[k + 1]] = k
    return idx, np.diff(np.array(b, dtype=np.int64)), np.array(b[:-1], dtype=np.int64)


def cc_cell_ok(path, nx, ny, frac, test, stopped=None, progress=None):
    """按 512 行一条读 path，每个分析格里 test 为真的像元占比 ≥ frac 记 1。返回 bytearray（nx × ny，行向下）。"""
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    stopped = stopped or (lambda: False)
    progress = progress or (lambda f: None)
    with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(str(path)) as ds:
        w, h, px = ds.width, ds.height, abs(ds.transform.a)
        cx, wid, starts = _cc_cell_index(w, px, SURVEY_CLIP_GRID, nx)
        cy, hei, _ = _cc_cell_index(h, px, SURVEY_CLIP_GRID, ny)
        ne = np.nonzero(wid > 0)[0]
        acc = np.zeros((ny, len(ne)), dtype=np.int64)
        for r0 in range(0, h, CC_STRIP_ROWS):
            if stopped():
                raise AlignCancelled()
            n = min(CC_STRIP_ROWS, h - r0)
            v = test(ds.read(window=Window(0, r0, w, n)), ds).view(np.uint8)
            cs = np.add.reduceat(v, starts[ne], axis=1, dtype=np.int64)
            ry = cy[r0:r0 + n]
            for k in np.unique(ry):          # 一条里只有几个格行，按格行加
                acc[k] += cs[ry == k].sum(axis=0)
            progress((r0 + n) / float(h))
    tot = hei[:, None] * wid[ne][None, :]
    ok = np.zeros((ny, nx), dtype=np.uint8)
    ok[:, ne] = ((tot > 0) & (acc >= frac * tot)).astype(np.uint8)
    return bytearray(ok.tobytes())


def _cc_dom_has_image(a, ds):
    """判据①（🔒 锁定）：RGB 不是全 0 的像元才算「有影像」。有 alpha 时 alpha 只能再加限制（alpha = 0 也不算），
    不能反过来用 alpha 取代 RGB 判据（R1④）。"""
    import numpy as np
    ok = np.any(a[:3] != 0, axis=0)
    if a.shape[0] >= 4 and ds.colorinterp[3].name == "alpha":
        ok &= a[3] > 0
    return ok


def _cc_dsm_valid(a, ds):
    """判据④：CC DSM 有值（≠ NoData、不是 NaN）。"""
    import numpy as np
    v = a[0]
    ok = np.isfinite(v)
    if ds.nodata is not None:
        ok &= v != ds.nodata
    return ok


# 内嵌脚本：CC 裁边的判据②③（不插值辅助 DSM），网格原点 = CC DOM 左上角。读写打开工程但不保存（同 SURVEY_AUX_SCRIPT）。
# 只用 Python 3.8 语法。
CC_AUX_SCRIPT = r'''# -*- coding: utf-8 -*-
# 由「无人机照片工具」整理 CC 成果生成：裁边判据②③（不插值辅助 DSM），在 Metashape 内嵌的 Python 里运行。不保存工程。
import json
import sys
import time
import traceback

P = json.load(open(sys.argv[-1], "r", encoding="utf-8"))
_pf = open(P["progress"], "a", encoding="utf-8")


def ev(kind, **kw):
    kw["ev"] = kind
    kw["t"] = round(time.time(), 1)
    _pf.write(json.dumps(kw, ensure_ascii=False) + "\n")
    _pf.flush()


class Prog(object):
    def __init__(self, stage):
        self.stage = stage
        self.last = -1

    def __call__(self, p):
        p = int(p)
        if p != self.last:
            self.last = p
            ev("progress", stage=self.stage, pct=p)


app = None
try:
    try:
        import Metashape
    except ImportError:
        import PhotoScan as Metashape
    app = Metashape.app
    ev("start", version=str(app.version), python=sys.version.split()[0])
    crs = Metashape.CoordinateSystem("EPSG::%d" % P["epsg"])
    ev("stage", stage="open")
    doc = Metashape.Document()
    doc.open(P["psx"], read_only=False)      # 辅助 DSM 只读打开建不上；读写打开但从不保存
    ch = doc.chunk
    if ch.dense_cloud is None:
        raise RuntimeError("工程里没有密集点云")
    op = Metashape.OrthoProjection()
    op.crs = crs
    r = P["aux_res"]
    ev("stage", stage="aux_dem")
    t0 = time.time()
    ch.buildDem(source_data=Metashape.DenseCloudData, interpolation=Metashape.DisabledInterpolation, projection=op,
                resolution=r, progress=Prog("aux_dem"))
    ev("secs", stage="aux_dem", secs=round(time.time() - t0, 1))
    L, T, cols, rows = P["left"], P["top"], P["cols"], P["rows"]
    box = Metashape.BBox()
    box.min = Metashape.Vector([L, T - (rows + 0.25) * r])
    box.max = Metashape.Vector([L + (cols + 0.25) * r, T])
    ic = Metashape.ImageCompression()
    ic.tiff_compression = Metashape.ImageCompression.TiffCompressionNone
    ic.tiff_big = cols * rows * 4 > P["bigtiff"]
    ic.tiff_tiled = False
    ic.tiff_overviews = False
    ev("stage", stage="aux_export")
    t0 = time.time()
    ch.exportRaster(P["aux"], source_data=Metashape.ElevationData, image_format=Metashape.ImageFormatTIFF, projection=op,
                    region=box, resolution_x=r, resolution_y=r, nodata_value=P["nodata"], save_alpha=False,
                    save_world=True, image_compression=ic, progress=Prog("aux_export"))
    ev("secs", stage="aux_export", secs=round(time.time() - t0, 1))
    ev("done")
except Exception:
    ev("error", msg=traceback.format_exc())
finally:
    _pf.close()
    if app is not None:
        app.quit()
'''


def cc_cloud_counts(psx, metashape, epsg, left, top, nx, ny, site_dir, name, log=None, progress=None, stop_event=None,
                    **timeouts):
    """判据②③ 的原始数：用工点的 .psx 在 Metashape 里建不插值辅助 DSM（0.1 米，原点 = CC DOM 左上角），读成 (cnt, blk)
    （同 survey_aux_counts）。辅助 DSM 用完就删，Metashape 新写的数据目录也删，工程文件前后必须一样。返回 (cnt, blk, steps)。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    sub = int(round(SURVEY_CLIP_GRID / SURVEY_CLIP_AUX_RES))
    aux = Path(site_dir) / (name + CC_AUX_TMP)
    tmp = Path(tempfile.mkdtemp(prefix="uav_ccaux_"))
    before_dirs, before_fp = _psx_frame_dirs(psx), _psx_fingerprint(psx)
    steps = []

    def on_event(e):
        k = e.get("ev")
        if k == "start":
            log("    Metashape 版本 %s（建不插值的辅助 DSM，只判断、不输出，工程不保存）" % e.get("version", ""))
        elif k == "progress":
            a, b, lab = {"aux_dem": (0.0, 0.85, "建不插值辅助 DSM"), "aux_export": (0.85, 0.97, "导出辅助 DSM")}.get(
                e.get("stage"), (0, 0, ""))
            if lab:
                progress(a + (b - a) * e.get("pct", 0) / 100.0, "%s %d%%" % (lab, e.get("pct", 0)))
        elif k == "secs":
            steps.append((e["stage"], e["secs"]))
        elif k == "error":
            log("    Metashape 脚本出错：\n%s" % e.get("msg", ""))

    params = {"psx": str(psx), "epsg": int(epsg), "aux": str(aux), "aux_res": SURVEY_CLIP_AUX_RES, "left": left,
              "top": top, "cols": nx * sub, "rows": ny * sub, "nodata": SURVEY_NODATA, "bigtiff": SURVEY_BIGTIFF}
    try:
        try:
            events, rc = _run_ms_script(metashape, CC_AUX_SCRIPT, params, tmp, on_event, stop_event, **timeouts)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            for d in sorted(_psx_frame_dirs(psx) - before_dirs):
                if Path(d).name.startswith("elevation"):
                    shutil.rmtree(d, ignore_errors=True)
            if _psx_fingerprint(psx) != before_fp:
                raise RuntimeError("建辅助 DSM 之后工程文件变了（不应该保存），请检查工程：%s" % psx)
        errs = [e for e in events if e.get("ev") == "error"]
        if errs:
            raise RuntimeError("建辅助 DSM 时 Metashape 出错：%s" % (
                errs[-1].get("msg", "").strip().splitlines() or ["未知错误"])[-1])
        if rc != 0:
            raise RuntimeError("建辅助 DSM 时 Metashape 异常退出，退出码 %s（0x%08X）" % (rc, rc & 0xFFFFFFFF))
        if not any(e.get("ev") == "done" for e in events) or not aux.is_file():
            raise RuntimeError("建辅助 DSM 没跑完（进度文件里没有完成标记或没有辅助 DSM）")
        t = tiff_strip_info(aux)
        if abs(t["origin"][0] - left) > 1e-3 or abs(t["origin"][1] - top) > 1e-3:
            raise RuntimeError("辅助 DSM 左上角 (%.4f, %.4f) 和 CC DOM 的 (%.4f, %.4f) 对不上" % (
                t["origin"][0], t["origin"][1], left, top))
        cnt, blk = survey_aux_counts(aux, nx, ny, log=lambda s: log("    " + s))
        return cnt, blk, steps
    finally:
        _cc_unlink(aux, aux.with_suffix(".tfw"))


def _cc_wait_metashape(log, stop_event):
    waited = False
    while not (stop_event is not None and stop_event.is_set()) and other_metashape_running():
        if not waited:
            log("    本机已有别的 Metashape 在跑，等它结束（每 %d 秒看一次）…" % BATCH_WAIT_POLL)
            waited = True
        if stop_event is not None:
            stop_event.wait(BATCH_WAIT_POLL)
        else:
            time.sleep(BATCH_WAIT_POLL)
    if stop_event is not None and stop_event.is_set():
        raise AlignCancelled()


def cc_clip_range(r, site, inset, density, metashape=None, log=None, progress=None, stop_event=None, **timeouts):
    """CC 成果的范围线（🔒）：0.5 米网格上 ①CC DOM 有影像（RGB 不是全 0 的像元 ≥ 90%）+ ②③（工点 .psx 有密集点云时，
    同 D-32）或 ④CC DSM 有值（没有点云时）→ 补缝 → 最大连片 → 填 < 100 m² 洞 → 往里收 → 简化（全同 D-32）。
    返回 dict（polygons、各步面积、用的判据、耗时）。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    stopped = lambda: stop_event is not None and stop_event.is_set()
    g = r["grid"]
    nx, ny = survey_clip_grid(g["w"], g["h"], g["px"])
    info = {"cloud": False, "cloud_note": "", "steps": []}
    t0 = time.time()
    if r["dom"]:
        ok = cc_cell_ok(r["dom"], nx, ny, SURVEY_CLIP_DOM_FRAC, _cc_dom_has_image, stopped,
                        lambda f: progress(0.25 * f, "%s %d%%" % (CC_CLIP_STAGE["cover_dom"], f * 100)))
        info["steps"].append(("cover_dom", round(time.time() - t0, 1)))
        log("    判据① DOM 有影像：读 CC DOM %d × %d 像元，%d × %d 个 %g 米格，%d 格达标（每格 RGB 不是全 0 的像元 ≥ %d%%），"
            "用 %.0f 秒" % (g["w"], g["h"], nx, ny, SURVEY_CLIP_GRID, ok.count(1), int(SURVEY_CLIP_DOM_FRAC * 100),
                          time.time() - t0))
    else:
        ok = bytearray(b"\x01" * (nx * ny))
        log("    没有 DOM，判据①不用")
    psx = site.get("psx") or ""
    dense = bool(psx) and _psx_has_dense_cloud(psx)
    t1 = time.time()
    if dense:
        if not metashape or check_metashape(metashape):
            raise ValueError("工点工程里有密集点云，要用 Metashape 算判据②③，但 Metashape 程序不可用：%s" % (
                metashape or "（没给）"))
        _cc_wait_metashape(log, stop_event)
        cnt, blk, steps = cc_cloud_counts(psx, metashape, r["epsg"], g["left"], g["top"], nx, ny, site["dir"],
                                          site["name"], log=log, progress=lambda f, s: progress(0.25 + 0.45 * f, s),
                                          stop_event=stop_event, **timeouts)
        info["steps"] += steps
        info["steps"].append(("cloud", round(time.time() - t1, 1)))
        info["cloud"] = True
        info["cloud_note"] = "用工点工程 %s 的密集点云算判据②③" % Path(psx).name
        info["criteria"] = "①DOM有影像+②③真实点云"
        info["criteria_note"] = "用了点云判据（%s）" % Path(psx).name
        fail_text = None
    else:
        why = "工点目录里没有 .psx" if not psx else "工点工程 %s 里没有密集点云" % Path(psx).name
        info["cloud_note"] = "未用点云判据（%s），用 ④CC DSM 有值代替②③" % why
        info["criteria"] = "①DOM有影像+④DSM有值"
        info["criteria_note"] = "未用点云判据（%s）" % why
        log("    %s" % info["cloud_note"])
        if r["dsm"]:
            d_ok = cc_cell_ok(r["dsm"], nx, ny, CC_DSM_FRAC, _cc_dsm_valid, stopped,
                              lambda f: progress(0.25 + 0.45 * f, "%s %d%%" % (CC_CLIP_STAGE["cover_dsm"], f * 100)))
            info["steps"].append(("cover_dsm", round(time.time() - t1, 1)))
            log("    判据④ DSM 有值：%d 格达标（每格 ≠ NoData 的像元 ≥ %d%%），用 %.0f 秒" % (
                d_ok.count(1), int(CC_DSM_FRAC * 100), time.time() - t1))
        else:
            d_ok = bytearray(b"\x01" * (nx * ny))
        cnt = bytearray(25 * v for v in d_ok)
        blk = bytearray(b"\x64" * ((nx // 2) * (ny // 2)))
        fail_text = "④CC DSM 无值 %.0f m²（没有点云，②③不用）"
    an = {"nx": nx, "ny": ny, "cell": SURVEY_CLIP_GRID, "left": g["left"], "top": g["top"], "dom_ok": ok, "cnt": cnt,
          "blk": blk}
    progress(0.72, CC_CLIP_STAGE["range"])
    t2 = time.time()
    rl = survey_range_from_analysis(an, inset, density, log=lambda s: log("    " + s), fail_text=fail_text)
    info["steps"].append(("range", round(time.time() - t2, 1)))
    info.update(rl)
    info["analysis_secs"] = time.time() - t0
    return info


def _cc_ovr_factors(w, h):
    f, out = 2, []
    while max(w, h) // f >= CC_OVR_MIN or not out:
        out.append(f)
        f *= 2
        if f > 1 << 20:
            break
    return out


def cc_build_ovr(path, kind):
    """给 path 建外部 .ovr 金字塔（GDAL TIFF_USE_OVR，主 tif 一个字节不动）。返回各级倍数。"""
    import rasterio
    from rasterio.enums import Resampling
    with rasterio.Env(TIFF_USE_OVR=True, COMPRESS_OVERVIEW="DEFLATE", BIGTIFF_OVERVIEW="IF_SAFER",
                      GDAL_TIFF_OVR_BLOCKSIZE=512, GDAL_PAM_ENABLED="NO"):
        with rasterio.open(str(path), "r+") as ds:
            fs = _cc_ovr_factors(ds.width, ds.height)
            ds.build_overviews(fs, Resampling.average if kind == "DOM" else Resampling.nearest)
    return fs


def cc_ovr_levels(path):
    """读金字塔的真实层级（R1②：按实际 IFD 的尺寸，不按公式算）。返回 [(倍数, 宽, 高)]；没有金字塔返回 []。"""
    import rasterio
    out = []
    with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(str(path)) as ds:
        fs = list(ds.overviews(1))
    for k, f in enumerate(fs):
        try:
            with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(str(path), OVERVIEW_LEVEL=k) as lv:
                out.append((f, lv.width, lv.height))
        except Exception:
            out.append((f, 0, 0))
    return out


def _cc_open_level(path, level=None):
    """打开 path 的某一级金字塔（level=None 是主图；level=0 是第一级缩览）。"""
    import rasterio
    if level is None:
        return rasterio.open(str(path))
    return rasterio.open(str(path), OVERVIEW_LEVEL=level)

def _cc_valid_level_pixels(lv, nodata, lw, lh, want=6, tile=64, max_tiles=4000):
    """在某一级的图里找几个「确实有值」的像元（列, 行）。按块扫，够 want 个就停。

    R2-F4：有效区是窄斜带之类时，固定那几个抽查点可能全落在 NoData 里；
    这时到这一级里去找真的有值的地方，别因为「没抽到样点」就说文件坏了。
    只按窗口读（tile × tile），不整层读进内存。"""
    import numpy as np
    from rasterio.windows import Window
    out, tiles = [], 0
    for r0 in range(0, lh, tile):
        for c0 in range(0, lw, tile):
            tiles += 1
            if tiles > max_tiles:
                return out
            w, h = min(tile, lw - c0), min(tile, lh - r0)
            if w <= 0 or h <= 0:
                continue
            try:
                blk = lv.read(window=Window(c0, r0, w, h))
            except Exception:
                continue
            for b in range(blk.shape[0]):
                mask = blk[b]
                if nodata is not None:
                    mask = mask != float(nodata)
                mask = np.isfinite(mask)
                idx = np.argwhere(mask)
                if idx.size:
                    rr, cc = idx[0]
                    out.append((c0 + int(cc), r0 + int(rr)))
                    break
            if len(out) >= want:
                return out
    return out


def _cc_ovr_range_check(path, level, lw, lh, mw, mh, max_block=4000000):
    """抽查金字塔读值（R1②）：第 k 级的某个像元应该落在**主图**里对应那一块的取值范围内
    （平均 / 最近邻重采样都成立；重采样核偶尔略微过冲，给 2% 的余量）。
    金字塔和主图不是一套（换了图、旧 .ovr 留着）就对不上。

    注意：比的是**主图**，不是上一级 —— GDAL 建每一级都是自己从原图算的
    （CC 的 .ovr 级数倍数是 2/4/…/64/127/252/…，127 不是 128，各级之间不是逐级相减的），
    拿上一级当基准会误判（金字塔没问题也被说成有问题）。

    只按窗口读，不整层读进内存；对应块太大的最深几级跳过读值抽查。
    R2-F4：固定抽查点全落在 NoData（窄斜带）时，到这一级的有效像元里再找样点；
    实在没抽到**不等于**文件损坏（主图本身没有有效数据时本来就没什么可比的）。
    返回 (问题列表, 抽查条数, 是否跳过)。"""
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    probs, n = [], 0
    if lw <= 0 or lh <= 0 or mw <= 0 or mh <= 0:
        return ["金字塔某一级的尺寸读不出来"], 0, False
    if (mw / float(lw)) * (mh / float(lh)) > max_block:
        return [], 0, True
    try:
        with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(str(path)) as ds:
            nodata = ds.nodata
            with rasterio.Env(GDAL_PAM_ENABLED="NO"), _cc_open_level(path, level) as lv:
                sx, sy = mw / float(lw), mh / float(lh)

                def compare(px, py):
                    """比一个金字塔像元（px, py）和主图里对应那一块。返回 1（比了）、0（没法比）。"""
                    c0, r0 = int(px * sx), int(py * sy)
                    c1, r1 = max(c0 + 1, int((px + 1) * sx)), max(r0 + 1, int((py + 1) * sy))
                    c1, r1 = min(c1, mw), min(r1, mh)
                    if c1 <= c0 or r1 <= r0:
                        return 0
                    val = lv.read(window=Window(px, py, 1, 1))
                    blk = ds.read(window=Window(c0, r0, c1 - c0, r1 - r0))
                    for b in range(min(blk.shape[0], val.shape[0])):
                        v = float(val[b][0][0])
                        if nodata is not None and v == float(nodata):
                            continue                       # 这一格是 NoData，不比
                        seg = blk[b].astype("float64")
                        if nodata is not None:
                            seg = seg[seg != float(nodata)]
                        seg = seg[np.isfinite(seg)]
                        if seg.size == 0:
                            continue                       # 主图那一块没有有效值，没法比
                        lo, hi = float(seg.min()), float(seg.max())
                        tol = max(1.0, (hi - lo) * 0.02)
                        if v < lo - tol or v > hi + tol:
                            probs.append("第 %d 级的读值 %g 不在主图对应那块（%g～%g）里" % (level + 1, v, lo, hi))
                        return 1
                    return 0

                # 先按固定的几点抽（抽查够快）
                spots = [(int(lw * fx), int(lh * fy)) for fx, fy in
                         ((0.23, 0.31), (0.71, 0.17), (0.47, 0.66), (0.86, 0.83))]
                for px, py in spots:
                    px = min(max(px, 0), lw - 1)
                    py = min(max(py, 0), lh - 1)
                    n += compare(px, py)
                # R2-F4：一个都没比成（固定点全在 NoData 里，有效区是窄斜带之类）→
                # 到这一级的有效像元里再找样点；**没抽到不等于损坏**。
                if n == 0:
                    for px, py in _cc_valid_level_pixels(lv, nodata, lw, lh):
                        n += compare(px, py)
                        if n >= 4:
                            break
    except Exception as ex:
        return ["金字塔抽查读值失败：%s" % ex], n, False
    return probs, n, False


def cc_check_ovr(path, log=None):
    """金字塔可用、层级真实尺寸、和 tif 是同一套（R1②）。返回 (问题列表, [(倍数, 宽, 高)])。

    查三件事：①有金字塔；②每一级的真实尺寸（按实际 IFD 读）和「倍数 × 主图尺寸」对得上；
    ③抽查读值——金字塔某像元落在**主图**里对应那一块的取值范围内（换了图、旧 .ovr 留着就会对不上）。
    全程按窗口读，不整层读进内存。"""
    probs = []
    name = Path(path).name
    import rasterio
    levels = cc_ovr_levels(path)
    if not levels:
        return ["%s 没有金字塔" % name], []
    with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(str(path)) as ds:
        w, h = ds.width, ds.height
    for k, (f, lw, lh) in enumerate(levels):
        if lw <= 0 or lh <= 0:
            probs.append("%s 第 %d 级金字塔尺寸读不出来" % (name, k + 1))
            continue
        # 尺寸按倍数核：GDAL 的倍数不一定正好是 2 的幂（CC 的 .ovr 有 127、252、504…），
        # 每级真实尺寸就是「主图尺寸 ÷ 倍数」四舍五入到的整数，所以差 1 个像元以内都算对。
        if abs(lw - w / float(f)) > 1.5 or abs(lh - h / float(f)) > 1.5:
            probs.append("%s 第 %d 级金字塔（%d × %d）和主图（%d × %d，%d 倍）对不上" % (
                name, k + 1, lw, lh, w, h, f))
    # 读值抽查：每一级都对主图
    for k, (f, lw, lh) in enumerate(levels):
        if lw <= 0 or lh <= 0:
            break
        p, n, skipped = _cc_ovr_range_check(path, k, lw, lh, w, h)
        probs += ["%s %s" % (name, x) for x in p]
        # R2-F4：没抽到读值**不等于**文件坏了 —— 主图本来就没有有效数据（比如有效区是窄斜带、
        # 抽查点全落在 NoData 里）时本来就没什么可比的。只有「读出值来但对不上」才是真问题。
        if n == 0 and not skipped and log:
            log("    %s 第 %d 级没抽到可比的读值（主图这级上没有有效数据），不据此判损坏" % (name, k + 1))
    return probs, levels


def cc_ovr_build_if_needed(tif, kind, log=None):
    """交付目录里的 tif 换成了新的一份以后，保证它带着和它自己匹配的金字塔（R1②：tif/tfw/ovr 当一套）。

    已有 .ovr 但和这张 tif 对不上（旧成果留下的）→ 删掉重建；没有金字塔 → 建一个（tif 一个字节不动）。
    返回 (建了没有, 问题列表)。"""
    log = log or (lambda s: None)
    tif, ovr = Path(tif), Path(str(tif) + ".ovr")
    if not tif.is_file():
        return False, []
    probs, _ = cc_check_ovr(tif, log=log)
    if not probs:
        return False, []
    if ovr.is_file():
        try:
            ovr.unlink()
            log("    %s 的旧金字塔和这张图对不上，删掉重建" % tif.name)
        except OSError as ex:
            raise RuntimeError("删不掉旧的金字塔 %s：%s" % (ovr.name, ex))
    before = _file_sha256(tif, lambda: False, lambda n: None)
    cc_build_ovr(tif, kind)
    if _file_sha256(tif, lambda: False, lambda n: None) != before:
        raise RuntimeError("建金字塔时 %s 被改了" % tif.name)
    probs, levels = cc_check_ovr(tif, log=log)
    if probs:
        raise RuntimeError("%s 的金字塔不对：%s" % (tif.name, "；".join(probs)))
    log("    %s 没有匹配的金字塔，重建了（%d 级）" % (tif.name, len(levels)))
    return True, []



def cc_write_clipped(src, dst, kind, polys, win, stopped=None, progress=None, epsg=None):
    """把 src（CC 的 DOM / DSM）win = (列, 行, 宽, 高) 这块原样写到 dst（🔒 不重采样）：DOM → RGBA 8 位，线外 alpha = 0
    （RGB 也写 0）；DSM → float32，线外和原来的 NoData 都写 −9999。Deflate、512 分块、估算 > 3.8 GB 写 BigTIFF；
    坐标系写进 GeoTIFF，另写 .tfw。线内外按像元中心判断。

    R2-F3：epsg 是**已经核定过的成果坐标系**（GeoTIFF 里读不出、ContextScene.json 补出来的那种）。
    写出的 tif 用它当投影（只是补坐标系的标签，像元网格和像素值一个不动、不重投影），
    不用源文件那个可能读不出来的 s.crs。"""
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.transform import Affine
    from rasterio.windows import Window
    from shapely.geometry import MultiPolygon, Polygon, mapping
    stopped = stopped or (lambda: False)
    progress = progress or (lambda f: None)
    geom = mapping(MultiPolygon([Polygon(o, hs) for o, hs in _as_polygons(polys)]))
    c0, r0, w, h = win
    crs = None
    if epsg:
        try:
            from rasterio.crs import CRS
            crs = CRS.from_epsg(int(epsg))
        except Exception:
            crs = None
    with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(str(src)) as s:
        T = s.transform
        tr = Affine(T.a, 0.0, T.c + c0 * T.a, 0.0, T.e, T.f + r0 * T.e)
        dom = kind == "DOM"
        bands, dtype, nbytes = (4, "uint8", 1) if dom else (1, "float32", 4)
        big = float(w) * h * bands * nbytes > SURVEY_BIGTIFF
        prof = dict(driver="GTiff", width=w, height=h, count=bands, dtype=dtype, crs=crs or s.crs, transform=tr, tiled=True,
                    blockxsize=512, blockysize=512, compress="deflate", BIGTIFF="YES" if big else "NO")
        has_alpha = False
        if dom:
            prof.update(photometric="RGB", alpha="YES")
            has_alpha = s.count >= 4 and s.colorinterp[3].name == "alpha"
        else:
            prof.update(nodata=SURVEY_NODATA)
        with rasterio.open(str(dst), "w", **prof) as d:
            for y in range(0, h, CC_STRIP_ROWS):
                if stopped():
                    raise AlignCancelled()
                n = min(CC_STRIP_ROWS, h - y)
                a = s.read(window=Window(c0, r0 + y, w, n))
                m = geometry_mask([geom], out_shape=(n, w), transform=Affine(T.a, 0.0, tr.c, 0.0, T.e, tr.f + y * T.e),
                                  invert=True)
                if dom:
                    out = np.zeros((4, n, w), dtype=np.uint8)
                    out[:3] = np.where(m, a[:3], 0)
                    out[3] = np.where(m, a[3] if has_alpha else 255, 0)
                else:
                    v = a[0].astype(np.float32, copy=True)
                    bad = ~m | ~np.isfinite(v)
                    if s.nodata is not None:
                        bad |= v == s.nodata
                    v[bad] = SURVEY_NODATA
                    out = v[None]
                d.write(out, window=Window(0, y, w, n))
                progress((y + n) / float(h))
    Path(str(dst)[:-4] + ".tfw").write_text("".join("%.10f\n" % v for v in (
        tr.a, 0.0, 0.0, tr.e, tr.c + tr.a / 2.0, tr.f + tr.e / 2.0)), encoding="ascii")
    return {"left": tr.c, "top": tr.f, "w": w, "h": h, "px": tr.a, "right": tr.c + w * tr.a, "bottom": tr.f + h * tr.e,
            "bigtiff": big}


def cc_clip_window(polys, g):
    """🔒 外框 = 范围线外接矩形外扩 5 个像元并对齐到 CC 网格（不超出 CC 原图）。返回 (列, 行, 宽, 高)。"""
    region, ncol, nrow = survey_clip_window(polys, (g["left"], g["top"]), g["px"])
    c0 = int(round((region[0] - g["left"]) / g["px"]))
    r0 = int(round((g["top"] - region[3]) / g["px"]))
    c1, r1 = min(g["w"], c0 + ncol), min(g["h"], r0 + nrow)
    c0, r0 = max(0, c0), max(0, r0)
    if c1 <= c0 or r1 <= r0:
        raise RuntimeError("范围线不在 CC 成果的范围里")
    return c0, r0, c1 - c0, r1 - r0


def _cc_grid_of(path):
    """输出 tif 的头和 .tfw（交付清单、核对用）。"""
    gi = geotiff_info(path)
    gi["tfw"] = read_tfw(Path(str(path)[:-4] + ".tfw"))
    return gi


def _cc_unlink(*paths):
    for p in paths:
        try:
            if p and Path(p).exists():
                Path(p).unlink()
        except OSError:
            pass


def cc_deliver_ortho_clipped(r, site, sd, inset, density, metashape, log, progress, stop_event, **timeouts):
    """裁一个正射/DSM 成果：范围线 → 写临时 tif（不重采样）→ 金字塔 → 自检 → 换上。返回 (交付文件 [(名, 路径)], 裁边信息)。"""
    stopped = lambda: stop_event is not None and stop_event.is_set()
    name = site["name"]
    g = r["grid"]
    t0 = time.time()
    info = cc_clip_range(r, site, inset, density, metashape, log, lambda f, s: progress(0.5 * f, s), stop_event,
                         **timeouts)
    polys = info["polygons"]
    sdir = Path(site["dir"])
    kml, shp = sdir / (name + CC_RANGE_KML), sdir / (name + CC_RANGE_SHP)
    survey_write_range_kml(kml, polys, r["epsg"], name + "（CC 成果 %s）" % r["prod"])
    survey_write_range_shp(shp, polys, r["epsg"], name)
    info["kml"], info["shp"] = str(kml), str(shp)
    log("    范围线另存（在输出目录的工点目录里，不进交付目录）：%s；%s（+.shx/.dbf/.prj）" % (kml.name, shp.name))
    win = cc_clip_window(polys, g)
    info["before"] = {"left": g["left"], "top": g["top"], "w": g["w"], "h": g["h"], "px": g["px"],
                      "right": g["left"] + g["w"] * g["px"], "bottom": g["top"] - g["h"] * g["px"]}
    kinds = [k for k in ("DOM", "DSM") if r[k.lower()]]
    tmps, out, boxes = {}, [], {}
    try:
        for i, k in enumerate(kinds):
            final = sd / (name + k + ".tif")
            tmp = Path(str(final) + ".裁边中.tif")
            tmps[k] = tmp
            _cc_unlink(tmp, str(tmp) + ".ovr", str(tmp)[:-4] + ".tfw")
            t1 = time.time()
            boxes[k] = cc_write_clipped(r[k.lower()], tmp, k, polys, win, stopped,
                                        lambda f, i=i, k=k: progress(0.5 + 0.4 * (i + f) / len(kinds),
                                                                     "%s %d%%" % (CC_CLIP_STAGE[k.lower()], f * 100)),
                                        epsg=r["epsg"])
            info["steps"].append((k.lower(), round(time.time() - t1, 1)))
            t1 = time.time()
            progress(0.5 + 0.4 * (i + 1) / len(kinds), "%s（%s）" % (CC_CLIP_STAGE["ovr"], k))
            fs = cc_build_ovr(tmp, k)
            info["steps"].append(("ovr_" + k.lower(), round(time.time() - t1, 1)))
            log("    %s：%d × %d 像元，写 %.0f 秒；金字塔 %d 级（%s），%.0f 秒" % (
                k, boxes[k]["w"], boxes[k]["h"], info["steps"][-2][1], len(fs), "/".join(str(f) for f in fs),
                info["steps"][-1][1]))
        progress(0.93, "检查结果")
        problems, rinfo = [], {}
        if len(kinds) == 2:
            problems, rinfo = check_survey_rasters(tmps["DOM"], tmps["DSM"], r["epsg"], g["px"])
            problems += check_survey_raster_pair(rinfo)
        for k in kinds:
            p, _ = cc_check_ovr(tmps[k], log=log)
            problems += p
            gi = geotiff_info(tmps[k])
            dc = (gi["origin"][0] - g["left"]) / g["px"]
            dr = (g["top"] - gi["origin"][1]) / g["px"]
            if abs(dc - round(dc)) > 1e-6 or abs(dr - round(dr)) > 1e-6 or abs(gi["res"][0] - g["px"]) > 1e-12:
                problems.append("%s 的像元网格和 CC 原图对不上（偏移 %.6f、%.6f 个像元）" % (k, dc, dr))
        if problems:
            raise RuntimeError("裁出来的 DOM/DSM 没通过检查：" + "；".join(problems))
        for k in kinds:                       # 检查都过了才换上：tif、.tfw、.tif.ovr 一起
            final = sd / (name + k + ".tif")
            for a, b in ((tmps[k], final), (Path(str(tmps[k])[:-4] + ".tfw"), final.with_suffix(".tfw")),
                         (Path(str(tmps[k]) + ".ovr"), Path(str(final) + ".ovr"))):
                os.replace(str(a), str(b))
                out.append((b.name, b))
    finally:
        for t in tmps.values():
            _cc_unlink(t, str(t) + ".ovr", str(t)[:-4] + ".tfw")
    info["after"] = boxes.get("DOM") or boxes.get("DSM")
    info["secs"] = time.time() - t0
    return out, info


def cc_deliver_copy(r, site, sd, old, stopped, on_bytes, log):
    """不裁：CC 的 tif / tfw / tif.ovr（或 3SM）改名逐字节复制（先写临时文件、读回 SHA-256 一致才换上、保留修改时间）；
    大小、修改时间没变、清单里有哈希的不重复复制。

    R1② / R2-F1：tif、.tfw、.tif.ovr 当同一套换 —— **主图一换，交付目录里的旧 .ovr 一律作废**：
    源有 .ovr 就把它一起复制过去（它和源 tif 是一套）；源没有 .ovr（或源那份没跟着换）就重建
    （🔒 交付的 tif 必须带和它本身匹配的金字塔）。抽查读值只用来验**新的一套**对不对，
    不能当作旧 .ovr 还能继续用的凭据（局部改动避开抽查点就能绕过去）。
    R2-F2：金字塔重建过的那一行，清单里的 SHA-256 作废 → 后面按实际文件统一重算。"""
    name = site["name"]
    out = []
    written = set()                              # 这次真的重新复制过的交付文件名
    for fn, src in cc_targets(r, name):
        if not Path(src).is_file():
            continue
        dst = sd / fn
        s_st = os.stat(src)
        sha = None
        if dst.is_file():
            d_st = dst.stat()
            prev = old.get((name, fn))
            if d_st.st_size == s_st.st_size and d_st.st_mtime_ns == s_st.st_mtime_ns and prev and \
                    prev.get("大小(字节)") == str(s_st.st_size) and prev.get("SHA-256") and \
                    prev.get("来源") == r["source"] and \
                    os.path.normcase(prev.get("来源标识") or "") == os.path.normcase(r.get("source_id") or ""):
                sha = prev["SHA-256"]           # 上次复制时核对过，大小和修改时间都没变
                on_bytes(s_st.st_size)
        if not sha:
            sha = _deliver_copy(src, dst, stopped, on_bytes)
            log("    复制 %s → %s" % (Path(src).name, fn))
            written.add(fn)
        out.append((fn, dst, sha))
    if r["kind"] == CC_KIND_ORTHO:
        for k in ("DOM", "DSM"):
            if not r[k.lower()]:
                continue
            tif = sd / (name + k + ".tif")
            if not tif.is_file():
                continue
            ovr = Path(str(tif) + ".ovr")
            # R2-F1：主图换了（这次真的重新复制过）而源没给 .ovr → 交付目录里上一版的 .ovr
            # 一定是跟新主图对不上的，一律作废重建；不能靠「抽查几个点还对得上」就沿用。
            if (name + k + ".tif") in written and not Path(r[k.lower()] + ".ovr").is_file() and ovr.is_file():
                try:
                    ovr.unlink()
                    log("    %s 的主图换了、源没有对应的金字塔，删掉上一版的重建" % tif.name)
                except OSError as ex:
                    raise RuntimeError("删不掉旧的金字塔 %s：%s" % (ovr.name, ex))
            # 🔒 交付的 tif 必须带和它本身匹配的金字塔；旧的跟新图对不上就重建（CC 没给就自己建，tif 一个字节不动）
            cc_ovr_build_if_needed(tif, k, log=log)
            if ovr.is_file() and not any(fn == ovr.name for fn, _, _ in out):
                out.append((ovr.name, ovr, None))
    return out


def _cc_clip_report_lines(c):
    """处理报告「CC 成果整理」段里的裁边几行（四个空格缩进，和 v1.3 裁边段的行首不撞，重新裁剪时不会被换掉）。"""
    fa = c.get("fail_area") or (0, 0, 0)
    lines = ["    裁边：自动（同 D-32）：%s → 补缝 → 最大连片 → 填洞 → 往里收 %g 米；不重采样，留在 CC 的像元网格上" % (
        "①CC DOM 有影像 + ②③真实点云够密" if c["cloud"] else "①CC DOM 有影像 + ④CC DSM 有值", c["inset"]),
        "    判据：①每个 %g 米格里 CC DOM 的 RGB 不是全 0 的像元 ≥ %d%%；%s" % (
            SURVEY_CLIP_GRID, int(SURVEY_CLIP_DOM_FRAC * 100),
            "②不插值辅助 DSM（%g 米，只判断、不输出，用完已删）在格里有真实点；③%g 米块里有点的 %g 米子格 ≥ 点云密度门槛 %g%%" % (
                SURVEY_CLIP_AUX_RES, SURVEY_CLIP_BLOCK, SURVEY_CLIP_AUX_RES, c["density"]) if c["cloud"] else
            "④格里 CC DSM ≠ NoData 的像元 ≥ %d%%" % int(CC_DSM_FRAC * 100)),
        "    点云：%s" % c["cloud_note"],
        "    各判据淘汰（%g 米网格上依次算）：①影像不足 %.0f m²、%s；都满足 %.0f m²" % (
            SURVEY_CLIP_GRID, fa[0], "②没有真实点 %.0f m²、③点云不够密 %.0f m²" % (fa[1], fa[2]) if c["cloud"] else
            "④DSM 无值 %.0f m²" % fa[1], c.get("valid_cells_area") or 0),
        "    补缝（扩 %g 米再收 %g 米）后 %.0f m²；最大连片（共 %s 片）、填掉 %d 个内部 < %g m² 的洞后 %.0f m²；"
        "往里收后范围线 %.0f m²（外环 %d 个点%s）" % (
            SURVEY_CLIP_CLOSE, SURVEY_CLIP_CLOSE, c.get("closed_area") or 0, c.get("components") or "-",
            c.get("holes") or 0, SURVEY_CLIP_HOLE, c.get("valid_area") or 0, c.get("line_area") or 0,
            c.get("points") or 0, "，内环 %d 个" % c["inner_rings"] if c.get("inner_rings") else ""),
        "    范围线另存：%s、%s（+.shx/.dbf/.prj）" % (Path(c["kml"]).name, Path(c["shp"]).name)]
    bf, af = c["before"], c["after"]
    lines.append("    裁剪前外框（CC 原图）：东 %.2f～%.2f、北 %.2f～%.2f（%.0f × %.0f 米，%d × %d 像元，%g 米）" % (
        bf["left"], bf["right"], bf["bottom"], bf["top"], bf["right"] - bf["left"], bf["top"] - bf["bottom"],
        bf["w"], bf["h"], bf["px"]))
    lines.append("    裁剪后外框：东 %.2f～%.2f、北 %.2f～%.2f（%.0f × %.0f 米，%d × %d 像元）；DOM 与 DSM 左上角、像元、"
                 "行列数一致，像元网格和 CC 原图对齐（已自动核对）" % (
                     af["left"], af["right"], af["bottom"], af["top"], af["right"] - af["left"],
                     af["top"] - af["bottom"], af["w"], af["h"]))
    lines.append("    裁边用时：%.0f 秒（%s）" % (c["secs"], "，".join(
        "%s %.0f 秒" % (CC_CLIP_STAGE[k] if k in CC_CLIP_STAGE else "金字塔（%s）" % k[4:].upper(), s)
        for k, s in c["steps"] if k in CC_CLIP_STAGE or k.startswith("ovr_"))))
    return lines


def _cc_report_append(site, r, dest):
    """在工点的 处理报告.txt 末尾追加一段「CC 成果整理」（四个空格缩进，不影响报告里原来的「状态：」等字段）。"""
    lines = ["", "—— CC 成果整理（%s，%s v%s）" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), APP_NAME,
                                               APP_VERSION),
             "    来源目录：%s（%s，EPSG:%s，%s）" % (r["dir"], r["kind"], r["epsg"], cc_box_text(r["box"])),
             "    认定：%s" % ("自动（得分 %.3f%s）" % (r["score"], "；第二名「%s」%.3f" % r["second"] if r["second"] else
                                               "；没有别的同坐标系候选") if r["how"] == "自动" else "用户指定")]
    if r["kind"] == CC_KIND_ORTHO:
        lines += _cc_clip_report_lines(r["clip"]) if r.get("clip") else ["    裁边：不裁（CC 原图、金字塔改名原样复制）"]
    else:
        lines.append("    3SM：逐字节复制 + 改名，不裁、不改内容")
    lines.append("    交付：%s\\：%s" % (Path(dest) / site["name"], "、".join(fn for fn, _, _ in r["files"])))
    lines.append("    清单里的来源：%s（来源标识 %s）" % (r["source"], r.get("source_id") or "不明"))
    if r["kind"] == CC_KIND_ORTHO:
        crit = r["clip"].get("criteria") if r.get("clip") else None
        note = r["clip"].get("criteria_note") if r.get("clip") else None
        lines.append("    裁边判据：%s%s" % (crit or "不裁边（CC 原图原样复制）",
                                         "（%s）" % note if note else ""))
    rep = Path(site["dir"]) / SURVEY_REPORT
    try:
        with open(rep, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def run_cc_deliver(plan, out_dir, dest=None, clip=True, inset=SURVEY_CLIP_INSET, density=SURVEY_CLIP_DENSITY,
                   replace=False, metashape=None, log=None, progress=None, stop_event=None, **timeouts):
    """按 cc_plan 的认定把 CC 成果整理进交付目录（🔒 见 T17 卡）。一个成果一个成果串行；取消停在当前文件、临时文件清掉。
    返回 {"rows", "dest", "csv", "exit", "cancelled", "secs"}；exit：0 成功 / 1 有失败或有成果没认出 / 3 取消。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    stopped = lambda: stop_event is not None and stop_event.is_set()
    t_all = time.time()
    out = Path(os.path.abspath(out_dir))
    dest = Path(os.path.abspath(dest)) if dest else survey_deliver_default(out)
    if not out.is_dir():
        raise ValueError("输出目录不存在：%s" % out)
    if dest == out or out in dest.parents or dest in out.parents:
        raise ValueError("交付目录不能是输出目录本身，也不能在它里面或包含它：%s" % dest)
    for p in plan["prods"]:
        pp = Path(p)
        if dest == pp or pp in dest.parents or dest in pp.parents:
            raise ValueError("交付目录不能是 CC 成果目录，也不能在它里面或包含它：%s" % dest)
    sites = {s["name"]: s for s in plan["sites"]}
    rows = plan["rows"]
    todo = [r for r in rows if r["site"] and not r["status"]]
    blocked = {}
    if not replace:
        for r, fn, src in cc_conflicts(todo, dest):
            blocked.setdefault(id(r), []).append("%s（原来源 %s）" % (fn, src))
    old = _read_deliver_csv(dest / SURVEY_DELIVER_CSV)
    dest.mkdir(parents=True, exist_ok=True)
    log("—— 整理 CC 成果：%d 个成果 → %s（%s；输出目录 %s）" % (
        len(rows), dest, "自动裁边，往里收 %g 米、点云密度门槛 %g%%" % (inset, density) if clip else "不裁边", out))
    for ln in cc_plan_lines(plan):
        log(ln)
    for r in rows:
        r.update(result="", reason="", files=[], clip=None, shas={})
        if not r["site"]:
            r.update(result="跳过", reason=cc_row_state(r))
    total = sum(os.path.getsize(src) for r in todo for _, src in cc_targets(r, r["site"]) if Path(src).is_file()) or 1
    done_bytes = [0]

    def on_bytes(k):
        done_bytes[0] += k
        progress(min(done_bytes[0] / float(total), 1.0), "复制 %.0f/%.0f MB" % (done_bytes[0] / 1048576.0,
                                                                            total / 1048576.0))

    new_rows, cancelled = {}, False
    for pos, r in enumerate(todo, 1):
        name = r["site"]
        site = sites[name]
        if stopped():
            cancelled = True
            break
        if id(r) in blocked:
            r.update(result="没整理", reason="交付目录里已有来源不同的同名文件，没覆盖：%s；要换掉请确认替换（命令行加 --replace）" %
                     "、".join(blocked[id(r)]))
            log("  %s → %s：%s" % (r["key"], name, r["reason"]))
            continue
        log("")
        log("—— [%d/%d] %s（%s，%s）→ %s（%s%s）" % (
            pos, len(todo), r["key"], r["kind"], cc_box_text(r["box"]), name, r["how"],
            "，得分 %.3f" % r["score"] if r["how"] == "自动" else ""))
        sd = dest / name
        try:
            sd.mkdir(exist_ok=True)
            if r["kind"] == CC_KIND_ORTHO and clip:
                files, c = cc_deliver_ortho_clipped(
                    r, site, sd, inset, density, metashape, log,
                    lambda f, s, pos=pos: progress(f, "第 %d/%d 个 · %s" % (pos, len(todo), s)), stop_event, **timeouts)
                c.update(inset=inset, density=density)
                r["clip"] = c
                r["files"] = [(fn, p, None) for fn, p in files]
            else:
                r["files"] = cc_deliver_copy(r, site, sd, old, stopped, on_bytes, log)
            # R2-F2：清单里的 SHA-256 一律按**最终落定的文件**重算。
            # 复制时记下的哈希是中间状态：金字塔可能被删掉重建（哈希就变了），
            # 不能拿旧值写进清单。
            for fn, p, sha in r["files"]:
                r["shas"][fn] = _file_sha256(p, stopped, lambda n: None)
            r["result"] = "完成"
        except (AlignCancelled, KeyboardInterrupt):
            r.update(result="已取消", reason="用户取消；这个成果的临时文件已清掉，交付目录里原来的没动")
            log("    已取消（临时文件已清掉）")
            cancelled = True
            break
        except Exception as ex:
            r.update(result="失败", reason=str(ex))
            log("    失败：%s" % ex)
            continue
        srs = survey_report_srs(Path(site["dir"]) / SURVEY_REPORT) or "EPSG:%s %s" % (r["epsg"], survey_srs_name(r["epsg"]))
        info = {}
        if r["kind"] == CC_KIND_ORTHO:
            for k in ("DOM", "DSM"):
                p = sd / (name + k + ".tif")
                if r[k.lower()] and p.is_file():
                    info[k] = _cc_grid_of(p)
        pair = check_survey_raster_pair(info)
        lst = new_rows.setdefault(name, {})
        if r["kind"] == CC_KIND_ORTHO and r.get("clip"):
            crit = "%s（%s）" % (r["clip"].get("criteria") or "", r["clip"].get("criteria_note") or "")
        elif r["kind"] == CC_KIND_ORTHO:
            crit = "不裁边（CC 原图原样复制）"
        else:
            crit = "3SM（逐字节复制，不裁）"
        for fn, p, _ in r["files"]:
            g = info.get(fn[len(name):len(name) + 3])
            lst[fn] = {"工点": name, "文件名": fn, "大小(字节)": os.path.getsize(p), "SHA-256": r["shas"][fn], "坐标系": srs,
                       "分辨率(米)": "%g" % abs(g["tfw"][0]) if g else "",
                       "行×列": "%d×%d" % (g["height"], g["width"]) if g and fn.endswith(".tif") else "",
                       "DOM与DSM行列一致": ("否" if pair else "是") if g else "",
                       "处理状态": site["verdict"], "来源": r["source"], "来源标识": r.get("source_id") or "",
                       "判据": crit}
        log("    交付：%s" % "、".join(fn for fn, _, _ in r["files"]))
        _cc_report_append(site, r, dest)
    # 清单：这次整理的工点原来的别的行（别的来源 / 别的文件）照旧留着，只换这次写的文件
    merged = {}
    for name, lst in new_rows.items():
        keep = [v for (n, fn), v in old.items() if n == name and fn not in lst and (dest / n / fn).is_file()]
        merged[name] = keep + list(lst.values())
    out_rows, n_sites = _write_deliver_csv(dest, old, merged)
    secs = time.time() - t_all
    log("")
    log("CC 成果整理汇总（%.1f 分钟）：" % (secs / 60.0))
    for r in rows:
        log("  %s %s：%s%s" % (r["key"], r["kind"] or "-", r["result"] + (" → " + r["site"] if r["result"] == "完成"
                                                                        else ""),
                             "（%s）" % r["reason"] if r["reason"] else ""))
    log("交付目录：%s；清单 %s（%d 个工点、%d 个文件）" % (dest, dest / SURVEY_DELIVER_CSV, n_sites, len(out_rows)))
    bad = [r for r in rows if r["result"] != "完成" and r.get("how") != "用户指定不整理"]
    code = 3 if cancelled else 1 if bad else 0
    return {"rows": rows, "dest": str(dest), "csv": str(dest / SURVEY_DELIVER_CSV), "exit": code,
            "cancelled": cancelled, "secs": secs}


def _survey_result(it, out_dir, metashape, dom_dsm, resolution, keypoints, tiepoints, clip=SURVEY_CLIP_AUTO,
                   clip_inset=SURVEY_CLIP_INSET, clip_file=None, clip_density=SURVEY_CLIP_DENSITY):
    return {"name": it["name"], "dir": it["dir"], "out_dir": str(out_dir), "status": "失败", "reason": "",
            "metashape": metashape, "version": "", "returncode": None, "done": False,
            "start": datetime.datetime.now(), "secs": None, "total": None, "n_gps": it.get("n_gps"), "located": None,
            "aligned": None, "unaligned": [], "tiepoints": None, "rms": None, "epsg": it.get("epsg"),
            "epsg_src": it.get("epsg_src", ""), "lon": it.get("lon"), "lat": it.get("lat"), "near": it.get("near"),
            "auto_epsg": it.get("auto_epsg"), "samples": [], "ref_mean": None, "steps": [], "xml": "", "xml_info": None,
            "fix_stats": None, "dom_dsm": dom_dsm, "resolution": resolution, "rasters": None, "raster_info": None,
            "region": None, "dom": "", "dsm": "", "keypoints": keypoints, "tiepoints_limit": tiepoints, "warnings": [],
            "exported": False, "low_ratio": False, "clip_mode": clip if dom_dsm else SURVEY_CLIP_NONE,
            "clip_inset": clip_inset, "clip_density": clip_density, "clip_file": clip_file or "", "clip": None}


def survey_one(it, out_dir, metashape, dom_dsm=False, resolution=SURVEY_RESOLUTION, keypoints=SURVEY_KEYPOINTS,
               tiepoints=SURVEY_TIEPOINTS, log=None, progress=None, stop_event=None, clip=SURVEY_CLIP_AUTO,
               clip_inset=SURVEY_CLIP_INSET, clip_file=None, clip_density=SURVEY_CLIP_DENSITY, **timeouts):
    """处理一个工点，结果全部写在 out_dir（<输出>\\<工点名>\\）：.psx、XML、（DOM/DSM）、处理报告.txt、metashape_日志.txt。
    clip：auto（D-32 三条判据自动裁边，默认）/ none（不裁，和 v1.0 一样）/ file（按 clip_file 的范围线裁）。
    返回结果 dict（status：完成 / 部分对齐 / 失败 / 已取消）。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    name = it["name"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    psx = out / (name + ".psx")
    xml_final = out / (name + "_BlocksExchange.xml")
    xml_raw = out / (name + "_BlocksExchange.未修正.xml.tmp")
    xml_d10 = out / (name + "_BlocksExchange.无坐标系.xml.tmp")
    dom, dsm = out / (name + "DOM.tif"), out / (name + "DSM.tif")
    dsm_raw = out / (name + "DSM.未裁.tmp.tif")   # 裁边时先导出的「没裁」的 DSM、DOM，裁完就删
    dom_raw = out / (name + "DOM.未裁.tmp.tif")
    res = _survey_result(it, out, metashape, dom_dsm, resolution, keypoints, tiepoints, clip, clip_inset, clip_file,
                         clip_density)
    do_clip = bool(dom_dsm) and clip != SURVEY_CLIP_NONE
    span = _SURVEY_SPAN[bool(dom_dsm)]
    t_start = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="uav_survey_"))
    events = []

    def on_event(e):
        kind = e.get("ev")
        if kind == "start":
            res["version"] = e.get("version", "")
            log("Metashape 版本 %s（内嵌 Python %s）" % (res["version"], e.get("python", "")))
        elif kind == "stage":
            a, b, label = span.get(e["stage"], (0, 0, e["stage"]))
            progress(a / 100.0, label)
            log("%s…  %s" % (label, datetime.datetime.now().strftime("%H:%M")))
        elif kind == "progress":
            a, b, label = span.get(e["stage"], (0, 0, e["stage"]))
            pct = e.get("pct", 0)
            progress((a + (b - a) * pct / 100.0) / 100.0, "%s %d%%" % (label, pct))
        elif kind == "secs":
            if e["stage"] != "save":
                res["steps"].append((e["stage"], e["secs"]))
        elif kind == "refs":
            res["located"], res["ref_mean"], res["samples"] = e["located"], e["mean"], e["samples"]
            log("相机参考：%d/%d 张照片带 GPS，已从经纬度换算到 %s（如 %s：%.7f, %.7f → %.2f, %.2f）" % (
                e["located"], e["total"], e["crs"], e["samples"][0][0], e["samples"][0][1][0], e["samples"][0][1][1],
                e["samples"][0][2][0], e["samples"][0][2][1]) if e["samples"] else
                "相机参考：%d/%d 张照片带 GPS" % (e["located"], e["total"]))
        elif kind == "aligned":
            res["aligned"], res["total"], res["unaligned"] = e["aligned"], e["total"], e["unaligned"]
            res["tiepoints"], res["rms"] = e["tiepoints"], e["rms"]
            log("对齐 %d/%d，连接点 %d；相机参考误差 RMS %s" % (
                e["aligned"], e["total"], e["tiepoints"], _survey_rms_text(e["rms"])))
            if e["unaligned"]:
                log("未对齐：%s" % "、".join(e["unaligned"]))
        elif kind == "low_ratio":
            res["low_ratio"] = True
        elif kind == "exported":
            res["exported"] = True
            log("已导出 Blocks Exchange（%d 张相机，去掉未对齐 %d 张）" % (e["cameras"], e["dropped"]))
        elif kind == "region":
            res["region"] = e
            b = e["box"]
            if e.get("mode") == "tie":
                d = e["default"]
                log("DOM/DSM 范围：东 %.1f～%.1f、北 %.1f～%.1f（%.0f × %.0f 米）＝自动区域框（%.0f × %.0f 米）∪ 连接点 %g%%～%g%% "
                    "分位范围%s" % (b[0], b[2], b[1], b[3], b[2] - b[0], b[3] - b[1], d[2] - d[0], d[3] - d[1],
                                 e["pct"][0], e["pct"][1], "；%s边超出相机范围 ± %g 米，已收回" % ("、".join(e["sides"]), e["limit"])
                                 if e["sides"] else ""))
            else:
                log("DOM/DSM 范围：东 %.1f～%.1f、北 %.1f～%.1f（%.0f × %.0f 米）%s" % (
                    b[0], b[2], b[1], b[3], b[2] - b[0], b[3] - b[1],
                    "；Metashape 自动的框伸出相机范围 %g 米以上，已收回" % SURVEY_REGION_LIMIT if e["clamped"] else ""))
        elif kind == "rasters":
            res["rasters"] = e["info"]
        elif kind == "done":
            res["done"] = True
        elif kind == "error":
            log("Metashape 脚本出错：\n%s" % e.get("msg", ""))

    def finish_xml():
        progress(0.99 if dom_dsm else 0.97, "修正 XML")
        res["fix_stats"] = fix_blocks_exchange(xml_raw, xml_d10)
        xml_raw.unlink()
        problems, _ = check_blocks_exchange(xml_d10, res["aligned"])
        problems = [p for p in problems if "D-10" not in p]  # 坐标系下一步再加；这里只看照片数、路径和 Pose/Metadata
        if problems:
            raise RuntimeError("XML 没通过检查：" + "；".join(problems))
        add_epsg_srs(xml_d10, xml_final, res["epsg"])
        xml_d10.unlink()
        res["xml"] = str(xml_final)
        problems, info = check_bx_epsg(xml_final, res["epsg"], res["aligned"], res["ref_mean"])
        res["xml_info"] = info
        if problems:
            raise RuntimeError("XML 没通过检查：" + "；".join(problems))
        log("XML 检查通过：%d 张照片，坐标系 EPSG:%d（%s），照片中心平均 %.1f, %.1f" % (
            info["photos"], res["epsg"], survey_srs_name(res["epsg"]), info["center_mean"][0], info["center_mean"][1]))

    log("—— 影像处理：%s（%d 张，%s，%s）" % (name, it["n"], survey_srs_label(it.get("epsg")) or "没有坐标系",
                                         "出 XML + DOM/DSM" if dom_dsm else "只出 XML"))
    log("输出：%s" % out)
    progress(0.0, "启动 Metashape")
    try:
        if not res["epsg"]:
            raise ValueError("没有坐标系：照片不带定位，自动选不了带；请在列表里给这个工点手动选一个 CGCS2000 3 度带")
        photos = survey_site_photos(it["dir"])
        res["total"] = len(photos)
        if not photos:
            raise ValueError("工点里没有照片：%s" % it["dir"])
        if len(photos) != it["n"]:
            res["warnings"].append("扫描时 %d 张，开跑时 %d 张，按开跑时的用" % (it["n"], len(photos)))
            log("注意：%s" % res["warnings"][-1])
        params = {"name": name, "photos": [str(p) for p in photos], "psx": str(psx), "xml_raw": str(xml_raw),
                  "epsg": int(res["epsg"]), "downscale": SURVEY_DOWNSCALE, "keypoint_limit": int(keypoints),
                  "tiepoint_limit": int(tiepoints), "min_ratio": SURVEY_MIN_RATIO, "min_gps": SURVEY_MIN_GPS,
                  "dom_dsm": bool(dom_dsm), "resolution": float(resolution or 0), "depth_downscale": SURVEY_DEPTH_DOWNSCALE,
                  "nodata": SURVEY_NODATA, "bigtiff": SURVEY_BIGTIFF, "region_limit": SURVEY_REGION_LIMIT,
                  "region_limit_clip": SURVEY_REGION_LIMIT_CLIP, "region_tie_pct": list(SURVEY_REGION_TIE_PCT),
                  "dom": str(dom), "dsm": str(dsm), "clip": do_clip, "dsm_uncropped": str(dsm_raw),
                  "dom_uncropped": str(dom_raw)}
        events, rc = _run_ms_script(metashape, SURVEY_SCRIPT, params, tmp, on_event, stop_event, **timeouts)
        res["returncode"] = rc
        errs = [e for e in events if e.get("ev") == "error"]
        if errs:
            if res["exported"] and xml_raw.is_file():  # DOM/DSM 出错时 XML 已经导出：照样修正好留给用户
                try:
                    finish_xml()
                except Exception as ex:
                    log("XML 也没弄好：%s" % ex)
            msg = (errs[-1].get("msg", "").strip().splitlines() or ["未知错误"])[-1]
            raise RuntimeError("Metashape 脚本出错：%s（完整信息见 metashape_日志.txt）%s" % (
                msg, "；XML 已导出可用，DOM/DSM 没做完" if res["xml"] else ""))
        if rc != 0:
            raise RuntimeError("Metashape 异常退出，退出码 %s（0x%08X）" % (rc, rc & 0xFFFFFFFF))
        if not res["done"]:
            raise RuntimeError("Metashape 退出了，但进度文件里没有完成标记（脚本没有跑完）")
        if res["low_ratio"]:
            raise RuntimeError("对齐率 %.0f%%（%d/%d）低于 %.0f%%，判失败；没有导出 XML" % (
                100.0 * res["aligned"] / res["total"], res["aligned"], res["total"], SURVEY_MIN_RATIO * 100))
        if not xml_raw.is_file():
            raise RuntimeError("Metashape 没有导出 Blocks Exchange XML")
        finish_xml()
        if dom_dsm:
            if do_clip:   # D-32：三条判据算范围线，DOM/DSM 用同一条线、同一个窗口重新导出（不重建）
                progress(0.96, "按范围线裁边")
                log("")
                log("—— 自动裁边：%s" % ("按 %s 的范围线" % Path(clip_file).name if clip == "file" else
                                         "DOM 有影像 + 不插值的密集点云真有点 + 点云够密（门槛 %g%%），往里收 %g 米" % (
                                             clip_density, clip_inset)))
                res["clip"] = survey_clip_rasters(psx, metashape, res["epsg"], resolution, dom, dsm, mode=clip,
                                                  clip_file=clip_file, inset=clip_inset, density=clip_density,
                                                  site_dir=out, name=name, report=out / SURVEY_REPORT,
                                                  before_dsm=dsm_raw, before_dom=dom_raw, log=log,
                                                  progress=lambda f, s: progress(0.96 + 0.038 * f, s),
                                                  stop_event=stop_event, **timeouts)
                res["steps"].extend(res["clip"]["steps"])
                if res["clip"].get("rasters"):
                    res["rasters"] = res["clip"]["rasters"]
            problems, info = check_survey_rasters(dom, dsm, res["epsg"], resolution)
            res["raster_info"] = info
            res["dom"], res["dsm"] = str(dom), str(dsm)
            problems += check_survey_raster_pair(info)
            if problems:
                raise RuntimeError("DOM/DSM 没通过检查：" + "；".join(problems))
            for k in ("DOM", "DSM"):
                g = info[k]
                log("%s：%d × %d，%g 米，%s%s，%.0f MB%s" % (
                    k, g["width"], g["height"], g["res"][0], g["dtype"], "×%d" % g["bands"] if g["bands"] > 1 else "",
                    g["size"] / 1048576.0, "，BigTIFF" if g["bigtiff"] else ""))
            if do_clip and res["clip"]:
                res["warnings"].append("裁边用的是%s范围线，DOM/DSM 线外透明 / NoData，外框和像元网格一致"
                                       % ("用户" if res["clip"]["source"] == "用户范围线" else "自动算的"))
        res["status"] = "完成" if res["aligned"] == res["total"] else "部分对齐"
        log("%s：对齐 %d/%d%s" % (res["status"], res["aligned"], res["total"],
                                "（未对齐的没写进 XML，清单见报告）" if res["unaligned"] else ""))
        progress(1.0, res["status"])
    except (AlignCancelled, KeyboardInterrupt):
        res["status"] = "已取消"
        res["reason"] = "用户取消，Metashape 已被强制结束；本目录里的结果不完整，不要使用"
        log("已取消")
        (out / "已取消.txt").write_text("用户在 %s 取消了这个工点，本目录里的结果不完整，不要使用。\n"
                                       % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except Exception as ex:
        res["status"] = "失败"
        res["reason"] = str(ex)
        log("失败：%s" % ex)
    finally:
        res["secs"] = time.time() - t_start
        for f in (xml_raw, xml_d10):
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass
        kept = []
        for raw, final in ((dsm_raw, dsm), (dom_raw, dom)):   # 裁边没做成：把没裁的 DSM/DOM 留下（改名成正式名字），别让工作白做
            if raw.exists() and not final.exists():
                try:
                    os.replace(str(raw), str(final))
                    if raw.with_suffix(".tfw").exists():
                        os.replace(str(raw.with_suffix(".tfw")), str(final.with_suffix(".tfw")))
                    kept.append(final.name)
                except OSError:
                    pass
            for f in (raw, raw.with_suffix(".tfw")):
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass
        if kept:
            res["warnings"].append("裁边没做成，保留的是没裁的 %s（可以之后用「按范围线重新裁剪…」再裁）" % "、".join(kept))

        _write_metashape_log(out / "metashape_日志.txt", res, events, tmp / "console.txt")
        _write_survey_report(out / SURVEY_REPORT, res)
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def _survey_rms_text(rms):
    if not rms:
        return "-"
    return "%.2f 米（X %.2f、Y %.2f、Z %.2f，%d 张）" % (rms[3], rms[0], rms[1], rms[2], rms[4])


def _survey_raster_lines(raster_info, rasters, paths):
    lines = []
    for k, key in (("DOM", "dom"), ("DSM", "dsm")):
        g = (raster_info or {}).get(k)
        r = (rasters or {}).get(key) or {}
        if g:
            ox, oy = g["origin"]
            lines.append("  %s：%s；%d × %d 像素，%g 米（Metashape 原始 %.4f 米），左上角 %.3f, %.3f，%s × %d 波段，%.0f MB%s" % (
                k, paths[key], g["width"], g["height"], g["res"][0], r.get("native") or 0, ox, oy, g["dtype"],
                g["bands"], g["size"] / 1048576.0, "，BigTIFF" if g["bigtiff"] else ""))
    return lines


def _survey_clip_lines(c):
    """报告的裁边段（整套处理和「按范围线重新裁剪」共用）。"""
    lines = []
    auto = c["source"].startswith("自动")
    if c.get("reclip"):
        lines.append("  重新裁剪：%s（%s v%s「按范围线重新裁剪」，只重新导出 DOM/DSM，没重建；用时 %.1f 分钟）" % (
            c["when"], APP_NAME, APP_VERSION, (c.get("secs") or 0) / 60.0))
    if auto:
        lines.append("  裁边：%s：DOM 有影像 + 不插值的密集点云真有点 + 点云够密 → 补缝 → 最大连片 → 填洞 → 往里收 %g 米" % (
            c["source"], c["inset"]))
        lines.append("  判据：①每个 %g 米格里没裁的 DOM alpha > 0 的像元 ≥ %d%%；②不插值（DisabledInterpolation）辅助 DSM"
                     "（%g 米，只判断、不输出，用完已删）在格里有真实点；③%g 米块里有点的 %g 米子格 ≥ 点云密度门槛" % (
                         SURVEY_CLIP_GRID, int(SURVEY_CLIP_DOM_FRAC * 100), SURVEY_CLIP_AUX_RES, SURVEY_CLIP_BLOCK,
                         SURVEY_CLIP_AUX_RES))
        lines.append("  点云密度门槛：%g%%；往里收：%g 米" % (c["density"], c["inset"]))
        fa = c.get("fail_area")
        if fa:
            lines.append("  三条各淘汰（%g 米网格上按 ①→②→③ 依次算）：①影像不足 %.0f m²、②没有真实点 %.0f m²、③点云不够密 %.0f m²；"
                         "三条都满足 %.0f m²" % (SURVEY_CLIP_GRID, fa[0], fa[1], fa[2], c.get("valid_cells_area") or 0))
            lines.append("  补缝（扩 %g 米再收 %g 米）后 %.0f m²；最大连片（共 %s 片%s）、填掉 %d 个内部 < %g m² 的洞后 %.0f m²" % (
                SURVEY_CLIP_CLOSE, SURVEY_CLIP_CLOSE, c.get("closed_area") or 0, c.get("components") or "-",
                "，其余 %.0f m² 舍掉" % ((c.get("closed_area") or 0) - (c.get("largest_area") or 0)) if
                (c.get("components") or 0) > 1 else "", c.get("holes") or 0, SURVEY_CLIP_HOLE, c.get("valid_area") or 0))
        if c.get("analysis"):
            lines.append("  裁边分析：%s（只改往里收 / 点云密度门槛重新裁时直接用它，不用再进 Metashape 分析）" % Path(c["analysis"]).name)
    else:
        src = c["source"]
        lines.append("  裁边：%s（直接用这条线裁，不再往里收、不按点云判据删减）" % (
            "用户范围线" if src == "用户范围线" else "用户改的范围线：" + src))
    if c.get("line_file"):
        lines.append("  范围线来源：%s" % c["line_file"])
    polys = _as_polygons(c["polygon"]) if c.get("polygon") else []
    lines.append("  范围线面积：%.0f m²（%d 个多边形，外环 %d 个点%s）" % (
        c.get("line_area") or 0, len(polys), sum(len(o) - 1 for o, hs in polys),
        "，内环（挖掉）%d 个" % sum(len(hs) for o, hs in polys) if any(hs for o, hs in polys) else ""))
    if c.get("kml"):
        lines.append("  范围线另存：%s、%s（+.shx/.dbf/.prj；想手改就用任何 GIS 软件改，改完点「按范围线重新裁剪…」）" % (
            Path(c["kml"]).name, Path(c["shp"]).name))
    if c.get("set_aside"):
        lines.append("  旧范围线改名保留：%s" % "、".join(Path(p).name for p in c["set_aside"]))
    bf, af = c.get("before"), c.get("after")
    if bf:
        lines.append("  裁剪前外框：东 %.2f～%.2f、北 %.2f～%.2f（%.0f × %.0f 米，%d × %d 像素）" % (
            bf["left"], bf["right"], bf["bottom"], bf["top"], bf["right"] - bf["left"],
            bf["top"] - bf["bottom"], bf["w"], bf["h"]))
    if af:
        lines.append("  裁剪后外框：东 %.2f～%.2f、北 %.2f～%.2f（%.0f × %.0f 米，%d × %d 像素）" % (
            af["left"], af["right"], af["bottom"], af["top"], af["right"] - af["left"],
            af["top"] - af["bottom"], af["w"], af["h"]))
        dx = (bf["right"] - bf["left"]) - (af["right"] - af["left"]) if bf else 0
        dy = (bf["top"] - bf["bottom"]) - (af["top"] - af["bottom"]) if bf else 0
        lines.append("  外框变化：东西%s %.1f 米、南北%s %.1f 米；DOM 与 DSM 同一条线、同一个像元网格"
                     "（左上角、像元大小、行列数都一致，已自动核对）" % (
                         "缩了" if dx >= 0 else "大了", abs(dx), "缩了" if dy >= 0 else "大了", abs(dy)))
        if c.get("reclip") and bf:
            lines.append(_reclip_box_note(bf, af))
    return lines


def _reclip_box_note(bf, af):
    """「按范围线重新裁剪」后外框会变（T12 复核建议②）：叠新旧图时别以为软件错了。"""
    if (bf["w"], bf["h"]) == (af["w"], af["h"]) and abs(bf["left"] - af["left"]) < 1e-6 and abs(bf["top"] - af["top"]) < 1e-6:
        return "  外框没变：%d×%d 像元" % (af["w"], af["h"])
    return "  外框已随范围线改变：旧 %d×%d → 新 %d×%d 像元（左上角 东 %.2f → %.2f、北 %.2f → %.2f；像元网格不变）" % (
        bf["w"], bf["h"], af["w"], af["h"], bf["left"], af["left"], bf["top"], af["top"])


def _survey_report_replace_clip(path, c, raster_info):
    """「按范围线重新裁剪」后改写 处理报告.txt：DOM/DSM 两行和裁边段换成新的（其余原样），注意行里的裁边说明也换掉。"""
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    keys = ("  DOM：", "  DSM：", "  裁边", "  重新裁剪：", "  判据：", "  点云密度门槛：", "  三条各淘汰", "  补缝",
            "  裁边分析：", "  范围线", "  旧范围线", "  有效面积：", "  算法：", "  裁剪前外框：", "  裁剪后外框：", "  外框变化：",
            "  外框已随范围线改变：", "  外框没变：")
    idx = [i for i, ln in enumerate(lines) if ln.startswith(keys)]
    new = _survey_raster_lines(raster_info, c.get("rasters"), {"dom": str(path.parent / (path.parent.name + "DOM.tif")),
                                                               "dsm": str(path.parent / (path.parent.name + "DSM.tif"))})
    new += _survey_clip_lines(c)
    if idx:
        lines = lines[:idx[0]] + new + [ln for i, ln in enumerate(lines[idx[0]:], idx[0]) if i not in idx]
    else:
        k = next((i for i, ln in enumerate(lines) if ln.startswith("Metashape 退出码：")), len(lines))
        lines = lines[:k] + new + lines[k:]
    note = "注意：裁边用的是%s范围线（%s 按范围线重新裁剪），DOM/DSM 线外透明 / NoData，外框和像元网格一致" % (
        "自动算的" if c["source"].startswith("自动") else "用户", c["when"])
    lines = [note if ln.startswith("注意：裁边用的是") else ln for ln in lines]
    if not any(ln.startswith("注意：裁边用的是") for ln in lines):
        k = next((i for i, ln in enumerate(lines) if ln.startswith("状态：")), 0)
        lines.insert(k + 1, note)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def _write_survey_report(path, res):
    lines = ["影像处理报告（%s v%s）" % (APP_NAME, APP_VERSION), "",
             "状态：%s" % res["status"]]
    if res["reason"]:
        lines.append("原因：%s" % res["reason"])
    for w in res["warnings"]:
        lines.append("注意：%s" % w)
    if res["aligned"] is not None and res["total"]:
        lines.append("对齐：%d/%d" % (res["aligned"], res["total"]))
    lines.append("相机参考误差RMS：%s" % _survey_rms_text(res["rms"]))
    lines.append("DOM/DSM：%s" % ("是（%s）" % ("分辨率 %g 米" % res["resolution"] if res["resolution"] else "分辨率自动")
                                 if res["dom_dsm"] else "否"))
    lines += ["", "工点：%s" % res["name"], "照片目录：%s（只读，没改）" % res["dir"], "输出目录：%s" % res["out_dir"],
              "Metashape：%s（版本 %s）" % (res["metashape"], res["version"] or "未知"),
              "开始时间：%s；总耗时 %.1f 分钟" % (res["start"].strftime("%Y-%m-%d %H:%M:%S"), (res["secs"] or 0) / 60.0)]
    if res["steps"]:
        lab = dict(_SURVEY_STEP_LABEL)
        lines.append("各步耗时：" + "，".join("%s %.1f 分钟" % (lab.get(k, k), s / 60.0) for k, s in res["steps"]))
    epsg = res["epsg"]
    if epsg:
        auto = "自动（带定位照片平均经度 %.4f°，中央经线 = 3 × round(经度 / 3)）" % res["lon"] if res["lon"] is not None else "自动"
        lines.append("坐标系：EPSG:%d  %s（东坐标不带带号）；来源：%s%s" % (
            epsg, survey_srs_name(epsg), auto if res["epsg_src"] == "自动" else "手动指定" + (
                "（自动会选 %s）" % survey_srs_label(res["auto_epsg"]) if res["auto_epsg"] and res["auto_epsg"] != epsg else ""),
            "；靠近分带线（离 %.1f° 不到 %g°）" % (survey_cm(res["auto_epsg"] or epsg) + (1.5 if res["lon"] > survey_cm(
                res["auto_epsg"] or epsg) else -1.5), SURVEY_NEAR_ZONE) if res["near"] and res["lon"] is not None else ""))
        lines.append("换算：照片 GPS 经纬度（WGS84，与 CGCS2000 只差厘米级）按 CGCS2000 经纬度（EPSG:4490）"
                     "用 Metashape 换算到 EPSG:%d 作相机参考；姿态参考没用；没有去定位" % epsg)
        for s in res["samples"]:
            lines.append("  %s：经度 %.8f、纬度 %.8f、高 %.2f → 东 %.3f、北 %.3f、高 %.2f" % (
                s[0], s[1][0], s[1][1], s[1][2], s[2][0], s[2][1], s[2][2]))
    lines.append("参数：对齐精度 最高（downscale=%d）、通用预选 开、参考预选 开、关键点 %d、连接点 %d；对齐后用相机参考 optimizeCameras 一次"
                 % (SURVEY_DOWNSCALE, res["keypoints"], res["tiepoints_limit"]))
    lines.append("照片：%s 张，带定位 %s 张%s" % (res["total"] if res["total"] is not None else "-",
                                          res["located"] if res["located"] is not None else res["n_gps"],
                                          "（扫描时 %s 张）" % res["n_gps"] if res["located"] is not None else ""))
    if res["aligned"] is not None:
        lines.append("对齐率：%.1f%%（低于 %.0f%% 判失败）" % (100.0 * res["aligned"] / res["total"], SURVEY_MIN_RATIO * 100))
        lines.append("未对齐照片：%s" % ("无" if not res["unaligned"] else "%d 张（没写进 XML，.psx 里保留）" % len(res["unaligned"])))
        lines += ["  " + u for u in res["unaligned"]]
        lines.append("连接点：%s" % res["tiepoints"])
    if res["xml"]:
        lines.append("Blocks Exchange：%s" % res["xml"])
        lines.append("  坐标系写 <SRS>：Id 1 / %s / EPSG:%d，<Block> 挂 <SRSId>1</SRSId>；去掉 Metashape 写的坐标系和 <Pose> 下的 "
                     "<Metadata>（%d 个）；照片路径是原片的绝对路径" % (survey_srs_name(epsg), epsg,
                                                     (res["fix_stats"] or {}).get("Pose/Metadata", 0)))
        if res["xml_info"]:
            lines.append("  照片 %d 张；ImagePath 缺失 %d 个" % (res["xml_info"]["photos"], res["xml_info"]["missing"]))
    if res["dom_dsm"]:
        lines.append("DOM/DSM 参数：深度图 超高（downscale=%d）、深度过滤 适中；DSM 由密集点云生成（插值开），DOM 以 DSM 为表面；"
                     "Deflate 压缩，DSM NoData %g，DOM RGBA（透明区 alpha=0）" % (SURVEY_DEPTH_DOWNSCALE, SURVEY_NODATA))
        rg = res["region"]
        if rg and rg.get("mode") == "tie":
            b, d, t = rg["box"], rg["default"], rg.get("tie")
            box_text = lambda v: "东 %.2f～%.2f、北 %.2f～%.2f（%.0f × %.0f 米）" % (
                v[0], v[2], v[1], v[3], v[2] - v[0], v[3] - v[1])
            lines.append("  范围：%s；勾了自动裁边：平面范围 = 自动区域框 ∪ 连接点分位数范围，限制在已对齐相机范围 ± %g 米以内，"
                         "高程用自动区域框的（D-33）" % (box_text(b), rg["limit"]))
            lines.append("  自动区域框：%s" % box_text(d))
            lines.append("  连接点范围：%s" % ("东、北各自 %g%%～%g%% 分位数 %s，共 %d 个连接点" % (
                rg["pct"][0], rg["pct"][1], box_text(t), rg["n_tie"]) if t else "没有连接点，只用自动区域框"))
            lines.append("  ± %g 米限制：%s" % (rg["limit"], "%s边超出，收到相机范围 ± %g 米" % (
                "、".join(rg["sides"]), rg["limit"]) if rg["sides"] else "没收过"))
        elif rg:
            b = res["region"]["box"]
            lines.append("  范围：东 %.2f～%.2f、北 %.2f～%.2f（%.0f × %.0f 米）；%s" % (
                b[0], b[2], b[1], b[3], b[2] - b[0], b[3] - b[1],
                "Metashape 自动的区域框伸出已对齐相机范围 %g 米以上，那一边收到 相机范围 ± %g 米" % (
                    SURVEY_REGION_LIMIT, SURVEY_REGION_LIMIT) if res["region"]["clamped"] else "用 Metashape 对齐后自动给的区域框"))
        lines += _survey_raster_lines(res["raster_info"], res["rasters"], res)
        if res.get("clip"):
            lines += _survey_clip_lines(res["clip"])
        elif res["dom_dsm"]:
            lines.append("  裁边：没裁（同 v1.0；整幅导出）")
    lines.append("Metashape 退出码：%s；完成标记：%s" % (res["returncode"], "是" if res["done"] else "否"))
    lines += ["", "精度说明：" + SURVEY_ACCURACY_NOTE,
              "在 ContextCapture 里：新建工程 → 导入区块（Import blocks）选上面的 XML；照片都应找到、坐标系显示 %s、"
              "不用再空三。" % (survey_srs_name(epsg) if epsg else "CGCS2000 3 度带")]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_survey_summary(path, rows):
    """汇总 CSV：UTF-8 带 BOM、每格加引号（同批量迁移）。先写临时文件再替换。"""
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(SURVEY_CSV_FIELDS)
        for r in rows:
            w.writerow([r.get(k, "") for k in SURVEY_CSV_FIELDS])
    os.replace(str(tmp), str(path))


def _survey_row(it, dom_dsm):
    row = dict((k, "") for k in SURVEY_CSV_FIELDS)
    row.update({"序号": it["idx"], "工点": it["name"], "照片数": it["n"], "带定位张数": it["n_gps"],
                "坐标系": survey_srs_label(it["epsg"]), "坐标系来源": it["epsg_src"] if it["epsg"] else "",
                "DOM/DSM": "是" if dom_dsm else "否"})
    return row


def _survey_fill(row, rep):
    r = survey_read_report(rep)
    row.update({"对齐": r["对齐"], "相机参考误差RMS": r["相机参考误差RMS"].split("（")[0].strip(), "报告": str(rep)})
    try:
        lines = Path(rep).read_text(encoding="utf-8-sig").splitlines()
        un = [ln for ln in lines if ln.startswith("未对齐照片：")]
        row["未对齐数"] = 0 if un and un[0].endswith("无") else int(re.search(r"(\d+) 张", un[0]).group(1)) if un else ""
        dd = [ln for ln in lines if ln.startswith("DOM/DSM：")]
        if dd:
            row["DOM/DSM"] = "是" if dd[0][len("DOM/DSM："):].startswith("是") else "否"
    except (OSError, AttributeError, IndexError):
        pass


def run_survey_batch(items, out_dir, metashape, plan=False, dom_dsm=False, resolution=SURVEY_RESOLUTION,
                     keypoints=SURVEY_KEYPOINTS, tiepoints=SURVEY_TIEPOINTS, log=None, progress=None, on_item=None,
                     stop_event=None, summary=None, wait_poll=BATCH_WAIT_POLL, clip=SURVEY_CLIP_AUTO,
                     clip_inset=SURVEY_CLIP_INSET, clip_file=None, clip_density=SURVEY_CLIP_DENSITY, **timeouts):
    """按顺序处理 items（survey_scan 的结果里选中的）：已有结果 / 跳过的照写一行；待处理和用户勾上的定位不足串行调 survey_one。
    本机有别的 Metashape 在跑就等；上次失败/已取消的工点重跑前旧结果目录改名保留（同 D-27）；取消时当前工点停下、剩下的不开始。
    clip：auto（默认，DOM/DSM 自动裁边）/ none（同 v1.0）/ file（按 clip_file 的范围线裁）。
    每个工点写一次汇总 CSV。返回 {"rows", "summary", "cancelled", "exit", "counts"}；exit：0 全部完成/部分对齐/跳过 / 1 有失败 / 3 取消。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    on_item = on_item or (lambda it, phase, text: None)
    stopped = lambda: stop_event is not None and stop_event.is_set()
    out_dir = Path(os.path.abspath(out_dir))
    for it in items:
        r = Path(os.path.abspath(it["dir"]))
        if out_dir == r or r in out_dir.parents:
            raise ValueError("输出目录不能放在照片目录里面（原片目录只读）：%s" % out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = Path(summary) if summary else out_dir / ("影像处理汇总_%s.csv" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    rows, cancelled = [], False
    todo = [it for it in items if survey_runnable(it)]
    log("—— 影像处理：选中 %d 个工点，其中要处理 %d 个%s；%s" % (
        len(items), len(todo), "（只列计划）" if plan else "",
        "出 XML + DOM/DSM（%s%s）" % ("分辨率 %g 米" % resolution if resolution else "分辨率自动",
                                    "，自动裁边，往里收 %g 米、点云密度门槛 %g%%" % (clip_inset, clip_density) if dom_dsm and clip == SURVEY_CLIP_AUTO
                                    else "，不裁边" if dom_dsm else "") if dom_dsm else "只出 XML"))
    log("不去定位：照片 GPS 换算到各工点的 CGCS2000 3 度带作相机参考（%s）" % SURVEY_ACCURACY_NOTE)
    log("输出目录：%s" % out_dir)
    write_survey_summary(summary, rows)
    for pos, it in enumerate(items, 1):
        tag = "[%d/%d · 序号 %d] %s" % (pos, len(items), it["idx"], it["name"])
        row = _survey_row(it, dom_dsm)
        prev = None
        if survey_runnable(it) and not plan:
            state, rep, rdir = survey_result_state(out_dir, it["name"])  # 开跑前再看一次
            if state == "done":
                it["status"], it["report"] = "已有结果", str(rep)
            elif state:
                prev = (state, rdir)
        if not survey_runnable(it):
            row["状态"] = "跳过：已有结果" if it["status"] == "已有结果" else it["status"]
            if it["report"]:
                _survey_fill(row, it["report"])
            log("%s → %s" % (tag, row["状态"]))
            on_item(it, "done", it["status"])
        elif plan:
            row["状态"] = "计划运行"
            log("%s（%d 张，带定位 %d）→ %s%s%s" % (
                tag, it["n"], it["n_gps"], survey_srs_label(it["epsg"]) or "没有坐标系（会失败，请手动指定）",
                "（%s）" % it["epsg_src"] if it["epsg"] else "", "；%s，旧结果目录会改名保留后重跑" % it["status"]
                if it["status"] != SURVEY_TODO else ""))
        else:
            waited = False
            while not stopped() and other_metashape_running():
                if not waited:
                    log("%s：本机已有别的 Metashape 在跑，等它结束（每 %d 秒看一次）… %s" % (
                        tag, wait_poll, datetime.datetime.now().strftime("%H:%M")))
                    on_item(it, "wait", "等待其他 Metashape")
                    progress(0.0, "第 %d/%d 个 · 等待其他 Metashape" % (pos, len(items)))
                    waited = True
                if stop_event is not None:
                    stop_event.wait(wait_poll)
                else:
                    time.sleep(wait_poll)
            if stopped():
                cancelled = True
                on_item(it, "done", it["status"])
                log("已取消：%s 及之后的 %d 个工点都没有开始" % (it["name"], len(items) - pos + 1))
                break
            log("")
            log("%s → 开始（%d 张，%s）%s %s" % (tag, it["n"], survey_srs_label(it["epsg"]) or "没有坐标系",
                                          "，定位不足（用户勾选）" if it["status"] == SURVEY_LOW_GPS else "",
                                          datetime.datetime.now().strftime("%H:%M")))
            on_item(it, "start", "运行中")
            iprog = lambda f, s, pos=pos: progress(f, "第 %d/%d 个 · %s" % (pos, len(items), s))
            t0 = time.time()
            res = None
            try:
                if prev:
                    batch_set_aside(prev[1], prev[0], log)
                res = survey_one(it, survey_result_dir(out_dir, it["name"]), metashape, dom_dsm, resolution, keypoints,
                                 tiepoints, log=log, progress=iprog, stop_event=stop_event, clip=clip,
                                 clip_inset=clip_inset, clip_file=clip_file, clip_density=clip_density, **timeouts)
                status = res["status"]
            except ValueError as ex:
                log("失败：%s" % ex)
                status = "失败"
            except (AlignCancelled, KeyboardInterrupt):
                log("已取消")
                status = "已取消"
            row["耗时分钟"] = _fmt_minutes(time.time() - t0)
            row["状态"] = status
            rep = survey_result_dir(out_dir, it["name"]) / SURVEY_REPORT
            if rep.is_file():
                _survey_fill(row, rep)
            it["status"] = status
            log("  → %s  对齐 %s  相机参考误差 %s  用时 %s 分钟" % (status, row["对齐"] or "-", row["相机参考误差RMS"] or "-",
                                                      row["耗时分钟"]))
            on_item(it, "done", it["status"])
            cancelled = status == "已取消" or stopped()
        rows.append(row)
        write_survey_summary(summary, rows)
        if cancelled:
            if pos < len(items):
                log("已取消：剩下 %d 个工点没有开始" % (len(items) - pos))
            break
    log("")
    log("汇总表：%s" % summary)
    counts = {}
    for r in rows:
        counts[r["状态"]] = counts.get(r["状态"], 0) + 1
    for st, c in counts.items():
        log("  %s：%d 个" % (st, c))
    code = 3 if cancelled else 1 if "失败" in counts else 0
    return {"rows": rows, "summary": str(summary), "cancelled": cancelled, "exit": code, "counts": counts}


# ---------------------------------------------------------------------------
# 自检（selftest，D-34）
#   检查随包带的 numpy / rasterio（GDAL）/ shapely / pyproj / sqlite3 能不能用。
#   只在跑 selftest 时才 import 这些库，其它命令不受影响、启动不变慢。
# ---------------------------------------------------------------------------

SELFTEST_EPSG = 4543        # CGCS2000 / 3 度带，中央经线 102°E
SELFTEST_TOL_MM = 1.0       # 坐标换算允许的误差：1 毫米


def run_selftest(log=None):
    """自检。在临时目录里写读几种 GeoTIFF、换算一次坐标、做一次几何运算、开一次 sqlite3。

    返回 (是否全部通过, [(名字, 是否通过, 细节), ...])。临时文件跑完删掉。"""
    import shutil
    import tempfile

    if log is None:
        def log(_s):
            pass

    results = []

    def add(name, ok, detail=""):
        results.append((name, bool(ok), str(detail)))
        log("%s %s%s" % ("通过" if ok else "失败", name, ("：" + str(detail)) if detail else ""))
        return bool(ok)

    tmp = tempfile.mkdtemp(prefix="uav_selftest_")
    try:
        # ---- 数据目录和两份 proj.db 在不在程序里 ----
        gdal, rast_proj, pyproj_dir = geo_data_dirs()
        rasterio_db = os.path.join(rast_proj, "proj.db") if rast_proj else ""
        pyproj_db = os.path.join(pyproj_dir, "proj.db") if pyproj_dir else ""
        add("GDAL/PROJ 数据目录及两份 proj.db 随包带",
            bool(gdal) and os.path.isdir(gdal)
            and bool(rast_proj) and os.path.isdir(rast_proj) and os.path.isfile(rasterio_db)
            and bool(pyproj_dir) and os.path.isdir(pyproj_dir) and os.path.isfile(pyproj_db),
            "GDAL_DATA=%s；rasterio proj.db=%s；pyproj proj.db=%s" % (
                gdal or "（没找到）", rasterio_db or "（没找到）", pyproj_db or "（没找到）"))

        # ---- numpy / rasterio ----
        try:
            import numpy
            import rasterio
            from rasterio import _env as rasterio_env
            from rasterio.enums import Resampling
            from rasterio.transform import from_origin
            from rasterio.warp import transform as rasterio_transform
        except Exception as ex:
            add("导入 numpy / rasterio", False, "%s: %s" % (type(ex).__name__, ex))
            return False, results
        add("导入 numpy / rasterio", True,
            "numpy %s；rasterio %s（GDAL %s）" % (numpy.__version__, rasterio.__version__,
                                                rasterio.__gdal_version__))

        transform = from_origin(500000.0, 3900000.0, 1.0, 1.0)

        def coordinate_error_mm(lon, lat):
            dx = abs(lon - 102.5) * 111319.49079327358 * math.cos(math.radians(34.5)) * 1000.0
            dy = abs(lat - 34.5) * 110574.3885578 * 1000.0
            return dx, dy

        def planar_error_mm(x, y):
            # 已知值：按 CGCS2000（GRS80）高斯-克吕格自己算的，不经过 pyproj
            #   经度 102.5°、纬度 34.5° -> 东 545920.2014、北 3819238.3878
            return abs(x - 545920.2014) * 1000.0, abs(y - 3819238.3878) * 1000.0

        def verify_proj_db(db_path):
            """只读验证指定的 proj.db 包含本自检要用到的 EPSG:4543 定义。"""
            import sqlite3
            from urllib.parse import quote

            if not db_path or not os.path.isfile(db_path):
                raise FileNotFoundError("找不到 proj.db：%s" % (db_path or "（路径未找到）"))
            uri = "file:%s?mode=ro" % quote(os.path.abspath(db_path).replace("\\", "/"), safe="/:")
            con = sqlite3.connect(uri, uri=True)
            try:
                layout = con.execute(
                    "SELECT value FROM metadata WHERE key='DATABASE.LAYOUT.VERSION.MAJOR'"
                ).fetchone()
                row = con.execute(
                    "SELECT p.name, p.geodetic_crs_code, c.method_name, "
                    "c.param2_value, c.param4_value "
                    "FROM projected_crs p JOIN conversion c "
                    "ON p.conversion_auth_name=c.auth_name "
                    "AND p.conversion_code=c.code "
                    "WHERE p.auth_name='EPSG' AND p.code='4543'"
                ).fetchone()
                if not layout or layout[0] != "1" or not row or row[1] != 4490 \
                        or row[2] != "Transverse Mercator" or row[3] != 102.0 \
                        or row[4] != 500000.0:
                    raise ValueError("proj.db 缺少或损坏 EPSG:4543 定义：%r" % (row,))
                return row[0]
            finally:
                con.close()

        def only_data_dir(actual, expected):
            """数据目录必须就是指定的那一份，不许悄悄回退到别的（哪怕也在包内）。"""
            if not actual:
                return False
            want = os.path.normcase(os.path.realpath(expected))
            for p in actual:
                got = os.path.normcase(os.path.realpath(p))
                if got != want:
                    return False
            return True

        # ---- 1) RGBA + Deflate + 512 分块 + 金字塔 ----
        try:
            tif = os.path.join(tmp, "rgba.tif")
            data = numpy.zeros((4, 64, 64), dtype="uint8")
            data[0, :, :] = 10
            data[1, :, :] = 20
            data[2, :, :] = 30
            data[3, :, :] = 255
            data[0, 5:10, 5:10] = 200
            prof = dict(driver="GTiff", width=64, height=64, count=4, dtype="uint8",
                        crs="EPSG:%d" % SELFTEST_EPSG, transform=transform,
                        tiled=True, blockxsize=512, blockysize=512, compress="deflate")
            with rasterio.open(tif, "w", **prof) as ds:
                ds.write(data)
            with rasterio.open(tif, "r+") as ds:
                ds.build_overviews([2, 4], Resampling.nearest)
                ds.update_tags(ns="rio_overview", resampling="nearest")
            with rasterio.open(tif) as ds:
                ok_crs = ds.crs is not None and ds.crs.to_epsg() == SELFTEST_EPSG
                ok_px = numpy.array_equal(ds.read(1), data[0]) and numpy.array_equal(ds.read(4), data[3])
                ovr = list(ds.overviews(1))
                ok_ovr = len(ovr) >= 2
                ok_comp = ds.compression is not None and ds.compression.name.upper() == "DEFLATE"
                ok_tile = tuple(ds.block_shapes[0]) == (512, 512)
                add("写读 RGBA GeoTIFF（EPSG:4543 / Deflate / 512 分块 / 金字塔）",
                    ok_crs and ok_px and ok_ovr and ok_comp and ok_tile,
                    "坐标系=%s；像素一致=%s；金字塔=%s；压缩=%s；分块=%s" % (
                        ds.crs.to_string() if ds.crs else None, ok_px, ovr,
                        ds.compression.name if ds.compression else None, tuple(ds.block_shapes[0])))
        except Exception as ex:
            add("写读 RGBA GeoTIFF（EPSG:4543 / Deflate / 512 分块 / 金字塔）", False,
                "%s: %s" % (type(ex).__name__, ex))

        # ---- 2) float32 + NoData -9999 ----
        try:
            tif = os.path.join(tmp, "dsm.tif")
            d = numpy.full((1, 32, 32), -9999.0, dtype="float32")
            d[0, 10:20, 10:20] = 12.5
            prof = dict(driver="GTiff", width=32, height=32, count=1, dtype="float32",
                        crs="EPSG:%d" % SELFTEST_EPSG, transform=transform, nodata=-9999.0)
            with rasterio.open(tif, "w", **prof) as ds:
                ds.write(d)
            with rasterio.open(tif) as ds:
                got = ds.read(1)          # count=1，读出来是二维 (行, 列)
                ok = (ds.nodata == -9999.0 and numpy.array_equal(got, d[0])
                      and float(got[15, 15]) == 12.5 and float(got[0, 0]) == -9999.0)
                add("写读 float32 GeoTIFF（NoData -9999）", ok,
                    "nodata=%s；中间像元=%s；边角像元=%s" % (ds.nodata, got[15, 15], got[0, 0]))
        except Exception as ex:
            add("写读 float32 GeoTIFF（NoData -9999）", False, "%s: %s" % (type(ex).__name__, ex))

        # ---- 3) BigTIFF ----
        try:
            tif = os.path.join(tmp, "big.tif")
            prof = dict(driver="GTiff", width=64, height=64, count=1, dtype="uint16",
                        crs="EPSG:%d" % SELFTEST_EPSG, transform=transform, BIGTIFF="YES")
            with rasterio.open(tif, "w", **prof) as ds:
                ds.write(numpy.zeros((1, 64, 64), dtype="uint16"))
            with rasterio.open(tif) as ds:
                ok_raster = ds.width == 64 and ds.height == 64
            with open(tif, "rb") as f:
                head = f.read(4)
            is_bigtiff = head[:2] == b"II" and head[2] == 43
            add("写读 BigTIFF", ok_raster and is_bigtiff,
                "文件头版本号=%d（43 是 BigTIFF，42 是普通 TIFF）；大小=%d 字节；大小可读=%s" % (
                    head[2], os.path.getsize(tif), ok_raster))
        except Exception as ex:
            add("写读 BigTIFF", False, "%s: %s" % (type(ex).__name__, ex))

        # ---- 4) rasterio/GDAL 真正使用自己的 proj.db 做坐标换算 ----
        try:
            db_crs = verify_proj_db(rasterio_db)
            rasterio_env.set_proj_data_search_path(rast_proj)
            paths = rasterio_env.get_proj_data_search_paths()
            if not only_data_dir(paths, rast_proj):
                raise RuntimeError("rasterio PROJ 搜索路径不是包内目录：%r" % (paths,))
            # 4326 -> 4543：和独立算的已知平面值比
            x, y = rasterio_transform(4326, 4543, [102.5], [34.5])
            dx, dy = planar_error_mm(x[0], y[0])
            # 4543 -> 4490：和已知经纬度比
            lon, lat = rasterio_transform(4543, 4490, [545920.2014], [3819238.3878])
            dlon, dlat = coordinate_error_mm(lon[0], lat[0])
            ok = (dx <= SELFTEST_TOL_MM and dy <= SELFTEST_TOL_MM
                  and dlon <= SELFTEST_TOL_MM and dlat <= SELFTEST_TOL_MM)
            add("rasterio/GDAL 使用 rasterio proj.db（坐标换算，误差 ≤ 1 毫米）", ok,
                "db 中 CRS=%s；搜索路径=%s；4326→4543 差 %.4f / %.4f 毫米；"
                "4543→4490 差 %.4f / %.4f 毫米" % (
                    db_crs, paths[0], dx, dy, dlon, dlat))
        except Exception as ex:
            add("rasterio/GDAL 使用 rasterio proj.db（坐标换算，误差 ≤ 1 毫米）",
                False, "%s: %s" % (type(ex).__name__, ex))

        # ---- 5) pyproj 真正使用自己的 proj.db 做坐标换算 ----
        try:
            db_crs = verify_proj_db(pyproj_db)
            import pyproj
            pyproj.datadir.set_data_dir(pyproj_dir)
            actual_dir = pyproj.datadir.get_data_dir()
            if not only_data_dir([actual_dir], pyproj_dir):
                raise RuntimeError("pyproj PROJ 数据目录不是包内目录：%s" % actual_dir)
            # 4326 -> 4543：和独立算的已知平面值比
            x, y = pyproj.Transformer.from_crs(4326, 4543, always_xy=True).transform(102.5, 34.5)
            dx, dy = planar_error_mm(x, y)
            # 4543 -> 4490：和已知经纬度比
            lon, lat = pyproj.Transformer.from_crs(4543, 4490, always_xy=True).transform(
                545920.2014, 3819238.3878)
            dlon, dlat = coordinate_error_mm(lon, lat)
            ok = (dx <= SELFTEST_TOL_MM and dy <= SELFTEST_TOL_MM
                  and dlon <= SELFTEST_TOL_MM and dlat <= SELFTEST_TOL_MM)
            add("pyproj 使用 pyproj proj.db（坐标换算，误差 ≤ 1 毫米）", ok,
                "db 中 CRS=%s；数据目录=%s；4326→4543 差 %.4f / %.4f 毫米；"
                "4543→4490 差 %.4f / %.4f 毫米（pyproj %s，PROJ %s）" % (
                    db_crs, actual_dir, dx, dy, dlon, dlat,
                    pyproj.__version__, pyproj.proj_version_str))
        except Exception as ex:
            add("pyproj 使用 pyproj proj.db（坐标换算，误差 ≤ 1 毫米）",
                False, "%s: %s" % (type(ex).__name__, ex))

        # ---- 6) shapely：buffer / simplify ----
        try:
            from shapely.geometry import LineString, Point
            buf = Point(100.0, 100.0).buffer(10.0)
            ok_buf = abs(buf.area - math.pi * 100.0) < 1.0
            line = LineString([(0, 0), (1, 0.001), (2, 0), (3, 0.002), (4, 0)])
            simp = line.simplify(0.1)
            ok_sim = len(simp.coords) < len(line.coords)
            add("shapely 几何运算（buffer / simplify）", ok_buf and ok_sim,
                "buffer 面积=%.2f（正圆 %.2f）；simplify 后 %d 个点（原 %d 个）" % (
                    buf.area, math.pi * 100.0, len(simp.coords), len(line.coords)))
        except Exception as ex:
            add("shapely 几何运算（buffer / simplify）", False, "%s: %s" % (type(ex).__name__, ex))

        # ---- 6) sqlite3：内存库 ----
        try:
            import sqlite3
            con = sqlite3.connect(":memory:")
            con.execute("create table t (a integer, b text)")
            con.execute("insert into t values (?, ?)", (1, "x"))
            row = con.execute("select a, b from t").fetchone()
            con.close()
            add("sqlite3（内存库）", row == (1, "x"), "建表插入后查到 %s" % (row,))
        except Exception as ex:
            add("sqlite3（内存库）", False, "%s: %s" % (type(ex).__name__, ex))

        # ---- 8) 3SM 外框（T17）：按 CC 3SM 的 SMNodeHeader 表结构写一个根节点外框，再用整理 CC 成果的函数读回 ----
        try:
            import sqlite3
            want = (634973.29, 3997750.42, 2100.5, 635801.36, 3998362.27, 2400.25)
            con = sqlite3.connect(":memory:")
            con.execute("CREATE TABLE SMNodeHeader (NodeId INTEGER PRIMARY KEY,ParentNodeId INTEGER,Resolution INTEGER,"
                        "Filtered INTEGER,Extent BLOB,ContentExtent BLOB,TotalCount INTEGER,NodeCount INTEGER)")
            con.execute("INSERT INTO SMNodeHeader (NodeId, ParentNodeId, Extent) VALUES (0, -1, ?)",
                        (struct.pack("<6d", *want),))
            con.execute("INSERT INTO SMNodeHeader (NodeId, ParentNodeId, Extent) VALUES (1, 0, ?)",
                        (struct.pack("<6d", 0, 0, 0, 1, 1, 1),))
            got = cc_3sm_extent_from_con(con)
            con.close()
            add("3SM 外框（SMNodeHeader 根节点 Extent 写入再读回）", tuple(got) == want, "读回 %s" % (tuple(got),))
        except Exception as ex:
            add("3SM 外框（SMNodeHeader 根节点 Extent 写入再读回）", False, "%s: %s" % (type(ex).__name__, ex))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok_all = all(r[1] for r in results)
    return ok_all, results


# ---------------------------------------------------------------------------
# 图形界面
# ---------------------------------------------------------------------------

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox
    try:
        import customtkinter as ctk  # D-23：界面库只在这里 import，命令行不依赖它
    except ImportError:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(APP_NAME, "界面需要 CustomTkinter（命令行功能不受影响）：\n"
                                       "%s -m pip install -r requirements-gui.txt" % sys.executable)
        r.destroy()
        raise

    # ---- 外观：浅色默认，可选跟随系统 / 深色（记在 config.json）；主色 Blue；中文用微软雅黑 ----
    appearance_label = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
    appearance = load_config().get("appearance", "light")
    if appearance not in appearance_label:
        appearance = "light"
    ctk.set_appearance_mode(appearance)
    ctk.set_default_color_theme("blue")
    ctk.ThemeManager.theme["CTkFont"]["family"] = "Microsoft YaHei UI"
    secondary = dict(fg_color=("gray82", "gray28"), hover_color=("gray72", "gray36"), text_color=("gray10", "gray92"),
                     text_color_disabled=("gray60", "gray50"))
    hint_color = ("gray35", "gray65")
    danger = ("#C62828", "#EF5350")  # Material Red 800 / 400
    danger_hover = ("#B71C1C", "#E53935")
    label_color = ctk.ThemeManager.theme["CTkLabel"]["text_color"]
    label_color_disabled = ctk.ThemeManager.theme["CTkCheckBox"]["text_color_disabled"]

    # CustomTkinter 自己做 DPI 感知和缩放（创建 CTk 时调 SetProcessDpiAwareness），这里不再重复设置
    root = ctk.CTk()
    root.title("%s v%s" % (APP_NAME, APP_VERSION))
    def gui_exception(exc_type, exc_value, exc_tb):
        import traceback
        text = "发生未处理错误，界面已恢复：\n%s" % "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            ui_box.showerror(APP_NAME, text)
        except Exception:  # 统一弹窗本身出错时退回系统弹窗
            messagebox.showerror(APP_NAME, text)
    root.report_callback_exception = gui_exception
    root.minsize(700, 540)
    cfg0 = load_config()

    # ---- 窗口（T10，D-30）：默认 1000×760、不超过屏幕可用区 90%；记住大小、位置、是否最大化 ----
    # CTk 的 geometry() 读出来的宽高已去掉 DPI 缩放（逻辑尺寸），设进去时再乘缩放；位置是屏幕像素。
    # 所以原样记 geometry() 的字符串，高分屏上不会越开越大。
    import ctypes
    from ctypes import wintypes

    def work_area():
        """主屏可用区（屏幕像素，去掉任务栏）：(左, 上, 右, 下)。"""
        try:
            r = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):  # SPI_GETWORKAREA
                return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()

    def on_screen(x, y, w):
        """标题栏中间那一点在某个显示器上，才算没跑到屏幕外。"""
        try:
            f = ctypes.windll.user32.MonitorFromPoint
            f.argtypes, f.restype = [wintypes.POINT, wintypes.DWORD], ctypes.c_void_p
            return bool(f(wintypes.POINT(int(x + w / 2), int(y + 12)), 0))  # MONITOR_DEFAULTTONULL
        except Exception:
            l, t, r, b = work_area()
            return l <= x + w / 2 < r and t <= y + 12 < b

    win_scale = root._get_window_scaling()
    wl, wt, wr, wb = work_area()
    m_geo = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", str((cfg0.get("window") or {}).get("geometry", "")))
    if m_geo:  # 记住的大小：最多铺满可用区
        w0, h0 = min(int(m_geo.group(1)), int((wr - wl) / win_scale)), min(int(m_geo.group(2)), int((wb - wt) / win_scale))
    else:
        w0, h0 = min(1000, int((wr - wl) * 0.9 / win_scale)), min(760, int((wb - wt) * 0.9 / win_scale))
    w0, h0 = max(w0, 700), max(h0, 540)
    if m_geo and on_screen(int(m_geo.group(3)), int(m_geo.group(4)), w0 * win_scale):
        x0, y0 = int(m_geo.group(3)), int(m_geo.group(4))
    else:  # 第一次打开，或记住的位置跑到屏幕外了（拔了副屏等）：居中
        x0, y0 = wl + max(0, int((wr - wl - w0 * win_scale) / 2)), wt + max(0, int((wb - wt - h0 * win_scale) / 2) - 20)
    win_state = {"normal": "%dx%d+%d+%d" % (w0, h0, x0, y0)}  # 菜单栏挂上之后再设（见「菜单」后面），否则高度被菜单栏吃掉一截

    def track_geometry(ev):  # 记下没最大化时的大小位置；最大化着关窗也存这个
        if ev.widget is root and root.state() == "normal":
            win_state["normal"] = root.geometry()

    warn = ("#E65100", "#EF6C00")  # 运行中可点的「取消」「中止」：橙色
    warn_hover = ("#BF360C", "#E65100")
    btn_colors = {
        "primary": (ctk.ThemeManager.theme["CTkButton"]["fg_color"], ctk.ThemeManager.theme["CTkButton"]["hover_color"],
                    ctk.ThemeManager.theme["CTkButton"]["text_color"]),
        "secondary": (secondary["fg_color"], secondary["hover_color"], secondary["text_color"]),
        "stop": (warn, warn_hover, ("white", "white")),
    }
    btn_off = {"primary": ("gray74", "gray33"), "stop": ("gray74", "gray33"), "secondary": ("gray90", "gray20")}
    btn_off_text = ("gray50", "gray55")

    def button(parent, text, command=None, primary=False, role=None, **kw):
        """role：primary（蓝，「开始」等）/ secondary（灰）/ stop（「取消」「中止」，可点时橙色）。"""
        kw.setdefault("width", 72 if text == "浏览…" else 88)
        on = kw.pop("state", "normal") != "disabled"
        b = ctk.CTkButton(parent, text=text, command=command, **kw)
        b.role = role or ("primary" if primary else "secondary")
        set_btn(b, on)
        return b

    def set_btn(b, on):
        """按钮可点/不可点一眼看得出：不可点时底色变灰、字变浅（T10 小毛病 1）。"""
        fg, hover, text = btn_colors[b.role]
        b.configure(state="normal" if on else "disabled", fg_color=fg if on else btn_off[b.role], hover_color=hover,
                    text_color=text, text_color_disabled=btn_off_text)

    def spinbox(parent, var, from_, to, increment, width):
        """代替 ttk.Spinbox：输入框 + −/+ 按钮（上下方向键同样加减），范围和步长同原来。"""
        f = ctk.CTkFrame(parent, fg_color="transparent")
        e = ctk.CTkEntry(f, textvariable=var, width=width, justify="right")

        def step(d):
            try:
                v = float(var.get())
            except ValueError:
                v = from_
            v = min(max(v + d, from_), to)
            var.set("%g" % round(v, 6))

        b1 = button(f, "−", lambda: step(-increment), width=26)
        b2 = button(f, "+", lambda: step(increment), width=26)
        e.bind("<Up>", lambda ev: step(increment))
        e.bind("<Down>", lambda ev: step(-increment))
        e.pack(side="left")
        b1.pack(side="left", padx=(4, 0))
        b2.pack(side="left", padx=(2, 0))
        f.parts = (e, b1, b2)
        return f

    def set_enabled(w, on):
        if isinstance(w, ctk.CTkLabel):
            w.configure(text_color=label_color if on else label_color_disabled)
        elif isinstance(w, ctk.CTkButton):
            set_btn(w, on)
        else:
            w.configure(state="normal" if on else "disabled")

    # ---- 折行（T10 小毛病 4）：按像素宽度自己折，中文任意两字之间可断，英文词、数字、路径片段不拆 ----
    wrap_tok = re.compile(r"[（《「『“‘(\[]*(?:[A-Za-z0-9_\-.,/\\:*%+#@&=~'\"|<>]+|\S)[，。；：、）》」』”’！？,.;:)\]…]*|\s+")

    def wrap_text(text, font, width):
        measure = lambda s: int(root.tk.call("font", "measure", font, s))
        out = []
        for para in text.split("\n"):
            line = ""
            for tok in wrap_tok.findall(para):
                if line.strip() and measure(line + tok) > width:
                    out.append(line.rstrip())
                    line = tok.lstrip()
                else:
                    line += tok
                while len(line) > 1 and measure(line) > width:  # 一个词比一行还长（长路径）：只好按字断
                    k = len(line) - 1
                    while k > 1 and measure(line[:k]) > width:
                        k -= 1
                    out.append(line[:k])
                    line = line[k:]
            out.append(line.rstrip())
        return "\n".join(out)

    def set_wrapped(lbl, text=None, width=None):
        """CTkLabel 显示按宽度折好的文字；width 是屏幕像素，不给就用上次的。"""
        if text is not None:
            lbl.raw_text = text
        if width is not None:
            lbl.wrap_px = max(int(200 * win_scale), width)
        lbl.configure(text=wrap_text(lbl.raw_text, lbl._label.cget("font"), lbl.wrap_px))
        sync_forms()  # 说明文字行数变了，表单自然高度跟着变

    def hint(parent, text=""):
        lbl = ctk.CTkLabel(parent, text="", text_color=hint_color, justify="left", anchor="w")
        set_wrapped(lbl, text, int(640 * win_scale))

        def on_resize(ev):
            w = ev.width - int(24 * win_scale)
            if abs(w - lbl.wrap_px) > 2:
                set_wrapped(lbl, width=w)
        parent.bind("<Configure>", on_resize, add="+")
        return lbl

    # ---- 统一弹窗（T10 小毛病 5）：信息/警告/错误/询问同一套 CTk 弹窗：左边圆形图标，右边文字，按钮靠右 ----
    dialog_icons = {"info": (("#1565C0", "#42A5F5"), "i"), "warning": (("#E65100", "#FFA726"), "!"),
                    "error": (("#C62828", "#EF5350"), "×"), "question": (("#1565C0", "#42A5F5"), "?")}

    def dialog_icon(parent, kind):
        color, sym = dialog_icons[kind]
        return ctk.CTkLabel(parent, text=sym, width=34, height=34, corner_radius=17, fg_color=color,
                            text_color="white", font=ctk.CTkFont(size=18, weight="bold"))

    def show_modal(dlg, focus=None):
        """弹窗放在主窗口中间偏上，模态，等它关掉。"""
        dlg.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + max(0, (root.winfo_height() - dlg.winfo_reqheight()) // 3)
        dlg.geometry("+%d+%d" % (x, y))
        target = getattr(focus, "_entry", focus or dlg)

        def take_focus():  # CTkToplevel 刚出来时会闪一下（换标题栏颜色/图标）把焦点弄丢，前一秒内多给几次
            if dlg.winfo_exists() and dlg.focus_get() is not target:
                dlg.lift()
                target.focus_force()
        for ms in (150, 400, 800):
            dlg.after(ms, take_focus)
        try:
            dlg.wait_visibility()
            dlg.grab_set()
        except tk.TclError:
            pass
        root.wait_window(dlg)

    def dialog(kind, title, message, buttons, default=0, cancel=0):
        """返回按下的按钮序号；关窗、Esc 返回 cancel；回车按 default。按钮从左到右照 buttons 的顺序，靠右放。"""
        dlg = ctk.CTkToplevel(root)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(root)
        res = {"i": cancel}
        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(18, 10))
        dialog_icon(body, kind).pack(side="left", anchor="n", padx=(0, 14))
        text = str(message)
        if text.count("\n") > 22 or len(text) > 1600:  # 很长（如出错堆栈）：放可滚动的文本框
            tb = ctk.CTkTextbox(body, width=520, height=320, wrap="word")
            tb.insert("1.0", text)
            tb.configure(state="disabled")
            tb.pack(side="left", fill="both", expand=True)
        else:
            lbl = ctk.CTkLabel(body, text="", justify="left", anchor="nw")
            set_wrapped(lbl, text, int(440 * win_scale))
            lbl.pack(side="left", anchor="n", pady=(6, 0))
        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 16))

        def done(i):
            res["i"] = i
            dlg.destroy()

        bs = []
        for i, t in enumerate(buttons):
            bs.append(ctk.CTkButton(row, text=t, width=88, command=lambda i=i: done(i), **({} if i == default else secondary)))
        for b in reversed(bs):
            b.pack(side="right", padx=(8, 0))
        dlg.bind("<Return>", lambda ev: done(default))
        dlg.bind("<Escape>", lambda ev: done(cancel))
        dlg.protocol("WM_DELETE_WINDOW", lambda: done(cancel))
        dlg.ui_kind, dlg.ui_buttons = kind, bs  # 给 scripts/test_gui.py 看
        show_modal(dlg)
        return res["i"]

    import types
    ui_box = types.SimpleNamespace(  # 用法同 tkinter.messagebox（title, message）；scripts/test_gui.py 换成记录器
        showinfo=lambda title, message, **kw: dialog("info", title, message, ["确定"]) and None,
        showwarning=lambda title, message, **kw: dialog("warning", title, message, ["确定"]) and None,
        showerror=lambda title, message, **kw: dialog("error", title, message, ["确定"]) and None,
        askyesno=lambda title, message, icon="question", **kw: dialog(icon, title, message, ["是", "否"], 0, 1) == 0,
    )
    root.ui_box = ui_box

    # ---- 日志区（T10，D-30）：每个页签上方表单、下方日志，中间一条可拖动的分隔条；窗口变大多出的高度全给日志 ----
    LOG_MAX_LINES = 5000  # 日志框最多显示这么多行，超出的从头删；完整日志照旧写文件
    log_tags = (("bad", ("失败", "拒绝"), ("#C62828", "#E57373")),
                ("warn", ("跳过", "需人工核对"), ("#E65100", "#FFB74D")),
                ("good", ("自动通过", "完成"), ("#2E7D32", "#81C784")))
    log_zero = re.compile(r"(失败|跳过|拒绝)[^\d\n，,；;]{0,3}0(?![\d.])")  # 「失败 0」「跳过：0」这种不算
    sash_color = ("gray78", "gray32")
    log_font = ctk.CTkFont(size=min(18, max(10, int(cfg0.get("log_font_size") or 12))))
    logs, panes = [], {}
    pane_cfg = cfg0.get("panes") or {}

    def log_tag(line):
        s = log_zero.sub("", line)
        for tag, words, _ in log_tags:
            if any(w in s for w in words):
                return tag
        return None

    def log_add(lg, s):
        tb = lg["tb"]
        for line in str(s).split("\n"):
            tag = log_tag(line)
            if tag:
                tb.insert("end", line + "\n", tag)
            else:
                tb.insert("end", line + "\n")
        n = int(tb.index("end-1c").split(".")[0]) - 1
        if n > LOG_MAX_LINES:
            tb.delete("1.0", "%d.0" % (n - LOG_MAX_LINES + 1))
        if lg["auto"].get() and not lg["see"]:  # 一批日志只滚一次
            lg["see"] = True
            tb.after_idle(lambda: (lg.__setitem__("see", False), tb.see("end")))

    def log_clear(lg):
        lg["tb"].delete("1.0", "end")

    def log_copy(lg):
        root.clipboard_clear()
        root.clipboard_append(lg["tb"].get("1.0", "end-1c"))

    def log_set_file(lg, path):
        lg["file"] = str(path) if path else None
        log_refresh_open(lg)

    def log_refresh_open(lg):
        on = bool(lg["file"]) and os.path.isfile(lg["file"])
        if on != (lg["b_open"].cget("state") == "normal"):
            set_btn(lg["b_open"], on)

    def log_open(lg):
        if lg["file"] and os.path.isfile(lg["file"]):
            os.startfile(lg["file"])

    def log_min_height():
        return (logs[0]["bar"].winfo_reqheight() if logs else int(30 * win_scale)) + \
            int(3 * log_font.metrics("linespace") * ctk.ScalingTracker.get_widget_scaling(root)) + int(14 * win_scale)

    def log_font_step(d):
        n = min(18, max(10, log_font.cget("size") + d))
        log_font.configure(size=n)
        for lg in logs:
            lg["size"].configure(text=str(n))
        for p in panes.values():
            p["pw"].paneconfigure(p["log"], minsize=log_min_height())

    def apply_mode(mode=None):
        i = 1 if (mode or ctk.get_appearance_mode()).lower() == "dark" else 0
        try:
            for p in panes.values():
                p["pw"].configure(bg=sash_color[i])
            for lg in logs:
                for tag, _, c in log_tags:
                    lg["tb"].tag_config(tag, foreground=c[i])
        except tk.TclError:  # 窗口已经关了
            pass

    sync_state = {"pending": False}

    def sync_forms():
        """没被用户拖过分隔条的页签：表单那一格始终等于表单的自然高度（窗口变窄说明文字多折一行时不裁掉底下的按钮）。"""
        def run():
            sync_state["pending"] = False
            root.update_idletasks()  # 先让说明文字的新行数算进表单的自然高度
            for p in panes.values():
                nat = p["form"].winfo_reqheight()
                if p["h"] is None and nat > 1 and str(p["pw"].panecget(p["form"], "height")) != str(nat):
                    p["pw"].paneconfigure(p["form"], height=nat)
        if not sync_state["pending"]:
            sync_state["pending"] = True
            root.after(30, run)

    def paned_tab(name):
        """建一个页签：返回 (表单 frame, 日志 dict)。表单照旧用 grid 排；日志在分隔条下面。"""
        t = nb.add(name)
        bg = nb.cget("fg_color")  # 浅/深两色；t.cget 拿到的是当前模式的单色，换深色时不跟着变
        pw = tk.PanedWindow(t, orient="vertical", bd=0, sashwidth=int(7 * win_scale), sashrelief="flat",
                            sashcursor="sb_v_double_arrow", opaqueresize=True, showhandle=False)
        pw.pack(fill="both", expand=True)
        form = ctk.CTkFrame(pw, fg_color=bg, corner_radius=0)
        logf = ctk.CTkFrame(pw, fg_color=bg, corner_radius=0)
        lg = {"name": name, "file": None, "auto": tk.BooleanVar(value=True), "see": False}
        bar = ctk.CTkFrame(logf, fg_color="transparent")
        bar.pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(bar, text="日志", text_color=hint_color).pack(side="left", padx=(0, 8))
        button(bar, "复制全部", lambda: log_copy(lg), width=72, height=24).pack(side="left")
        button(bar, "清空", lambda: log_clear(lg), width=48, height=24).pack(side="left", padx=(4, 0))
        lg["b_open"] = button(bar, "打开完整日志", lambda: log_open(lg), width=100, height=24, state="disabled")
        lg["b_open"].pack(side="left", padx=(4, 0))
        ctk.CTkCheckBox(bar, text="自动滚到底", variable=lg["auto"], checkbox_width=18, checkbox_height=18,
                        command=lambda: lg["auto"].get() and lg["tb"].see("end")).pack(side="left", padx=(12, 0))
        button(bar, "+", lambda: log_font_step(1), width=26, height=24).pack(side="right")
        lg["size"] = ctk.CTkLabel(bar, text=str(log_font.cget("size")), width=26)
        lg["size"].pack(side="right")
        button(bar, "−", lambda: log_font_step(-1), width=26, height=24).pack(side="right")
        ctk.CTkLabel(bar, text="字号", text_color=hint_color).pack(side="right", padx=(0, 4))
        lg["bar"] = bar
        lg["tb"] = ctk.CTkTextbox(logf, wrap="none", font=log_font)
        lg["tb"].pack(fill="both", expand=True)
        logs.append(lg)
        pw.add(form, stretch="never", minsize=int(30 * win_scale))
        pw.add(logf, stretch="always", minsize=log_min_height())
        h = pane_cfg.get(name)
        h = h if isinstance(h, (int, float)) and h > 0 else None
        if h:  # 记住的分隔条位置（逻辑像素，即表单那一格的高度）
            pw.paneconfigure(form, height=int(h * ctk.ScalingTracker.get_widget_scaling(root)))
        p = {"pw": pw, "form": form, "log": logf, "h": h}  # h：用户拖定的表单高度（逻辑像素）；None＝跟着表单内容走

        def on_sash_release(ev):  # 拖完分隔条：记下表单高度；拖回表单自然高度附近就当没拖过
            if not pw.winfo_ismapped():
                return
            form.update_idletasks()
            y, nat = pw.sash_coord(0)[1], form.winfo_reqheight()
            p["h"] = None if abs(y - nat) <= int(4 * win_scale) else round(y / ctk.ScalingTracker.get_widget_scaling(root))
            sync_forms()
        pw.bind("<ButtonRelease-1>", on_sash_release, add="+")
        panes[name] = p
        apply_mode()
        sync_forms()
        return form, lg

    def remember_window():
        """关窗前把窗口大小、位置、是否最大化、各页签分隔条、日志字号写进 config.json。"""
        cfg = load_config()
        cfg["window"] = {"geometry": root.geometry() if root.state() == "normal" else win_state["normal"],
                         "zoomed": root.state() == "zoomed"}
        cfg["panes"] = {name: p["h"] for name, p in panes.items() if p["h"]}
        cfg["log_font_size"] = log_font.cget("size")
        save_config(cfg)

    root.ui_t10 = {"logs": logs, "panes": panes, "log_add": log_add}  # 给 scripts/test_gui.py 查日志区和分隔条

    # ---- 菜单：外观（跟随系统 / 浅色 / 深色）；帮助 → 检查更新 / 项目主页 ----
    import webbrowser
    var_auto_update = tk.BooleanVar(value=load_config().get("check_update_on_start", True))
    var_appearance = tk.StringVar(value=appearance)

    def start_update_check(manual=False):
        q = queue.Queue()

        def worker():
            try:
                q.put((check_update(), None))
            except Exception as ex:  # 没联网、GitHub 打不开、限流等
                q.put((None, ex))

        def poll_update():
            try:
                info, err = q.get_nowait()
            except queue.Empty:
                root.after(300, poll_update)
                return
            if err is not None:
                if manual:
                    ui_box.showwarning(APP_NAME, "检查更新失败（可能没联网或打不开 GitHub）：\n%s" % err)
            elif info["newer"]:
                msg = "发现新版本 v%s（当前 v%s）。\n\n%s\n\n打开下载页面？" % (info["latest"], APP_VERSION, info["notes"][:600])
                if ui_box.askyesno(APP_NAME + " - 有新版本", msg):
                    webbrowser.open(info["url"])
            elif manual:
                ui_box.showinfo(APP_NAME, "已经是最新版本（v%s）。" % APP_VERSION)

        threading.Thread(target=worker, daemon=True).start()
        root.after(300, poll_update)

    def toggle_auto_update():
        cfg = load_config()
        cfg["check_update_on_start"] = bool(var_auto_update.get())
        save_config(cfg)

    def set_appearance():
        mode = var_appearance.get()
        ctk.set_appearance_mode(mode)
        cfg = load_config()
        cfg["appearance"] = mode
        save_config(cfg)

    menubar = tk.Menu(root)
    m_look = tk.Menu(menubar, tearoff=0)
    for mode in ("system", "light", "dark"):
        m_look.add_radiobutton(label=appearance_label[mode], value=mode, variable=var_appearance, command=set_appearance)
    menubar.add_cascade(label="外观", menu=m_look)
    def start_selftest():
        q2 = queue.Queue()

        def worker():
            try:
                q2.put((run_selftest(), None))
            except Exception as ex:
                q2.put((None, ex))

        def poll():
            try:
                res, err = q2.get_nowait()
            except queue.Empty:
                root.after(200, poll)
                return
            if err is not None:
                ui_box.showerror(APP_NAME, "自检失败：\n%s" % err)
                return
            ok, rows = res
            lines = ["自检%s（共 %d 项）：" % ("全部通过" if ok else "有失败项", len(rows)), ""]
            for name, passed, detail in rows:
                lines.append("%s %s%s" % ("[通过]" if passed else "[失败]", name,
                                          ("：" + detail) if detail else ""))
            (ui_box.showinfo if ok else ui_box.showwarning)(APP_NAME, "\n".join(lines))

        threading.Thread(target=worker, daemon=True).start()
        root.after(200, poll)

    m_help = tk.Menu(menubar, tearoff=0)
    m_help.add_command(label="检查更新", command=lambda: start_update_check(True))
    m_help.add_command(label="自检", command=start_selftest)
    m_help.add_checkbutton(label="启动时自动检查更新", variable=var_auto_update, command=toggle_auto_update)
    m_help.add_separator()
    m_help.add_command(label="项目主页（GitHub）", command=lambda: webbrowser.open(HOMEPAGE_URL))
    m_help.add_command(label="关于", command=lambda: ui_box.showinfo(
        APP_NAME, "%s v%s\n开源许可：MIT\n界面库：CustomTkinter（MIT）\n%s" % (APP_NAME, APP_VERSION, HOMEPAGE_URL)))
    menubar.add_cascade(label="帮助", menu=m_help)
    root.config(menu=menubar)
    root.update_idletasks()  # 先让菜单栏占好位置再设大小，不然高度少一截、每次打开越来越矮
    root.geometry(win_state["normal"])
    root.bind("<Configure>", track_geometry, add="+")
    if (cfg0.get("window") or {}).get("zoomed"):
        root.after(300, lambda: root.state("zoomed"))
    if var_auto_update.get():
        root.after(1500, start_update_check)

    nb = ctk.CTkTabview(root, anchor="nw")
    nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    ctk.AppearanceModeTracker.add(apply_mode, root)  # 换浅色/深色时分隔条和日志着色跟着换

    # ---- 页签 1：剔除定位 ----
    tab, lg1 = paned_tab("剔除定位")
    tab.columnconfigure(1, weight=1)

    v_src = tk.StringVar()
    v_dst = tk.StringVar()
    v_rec = tk.BooleanVar(value=True)
    v_att = tk.BooleanVar(value=False)
    v_inplace = tk.BooleanVar(value=False)
    v_backup = tk.BooleanVar(value=True)
    state = {"dst_auto": True, "worker": None, "stop": None}
    q = queue.Queue()

    def pick_src():
        d = filedialog.askdirectory(title="选择原始照片目录（只读，不会被修改）")
        if d:
            v_src.set(os.path.normpath(d))
            if state["dst_auto"] or not v_dst.get():
                v_dst.set(str(default_output_dir(d)))
                state["dst_auto"] = True

    def pick_dst():
        d = filedialog.askdirectory(title="选择输出目录（须为空，目录名须带 %s）" % OUTPUT_SUFFIX)
        if d:
            v_dst.set(os.path.normpath(d))
            state["dst_auto"] = False

    ctk.CTkLabel(tab, text="输入目录：").grid(row=0, column=0, sticky="w", pady=3)
    ctk.CTkEntry(tab, textvariable=v_src).grid(row=0, column=1, sticky="ew", pady=3)
    button(tab, "浏览…", pick_src).grid(row=0, column=2, padx=(6, 0), pady=3)
    ctk.CTkLabel(tab, text="输出目录：").grid(row=1, column=0, sticky="w", pady=3)
    e_dst = ctk.CTkEntry(tab, textvariable=v_dst)
    e_dst.grid(row=1, column=1, sticky="ew", pady=3)
    e_dst.bind("<Key>", lambda ev: state.__setitem__("dst_auto", False))
    button(tab, "浏览…", pick_dst).grid(row=1, column=2, padx=(6, 0), pady=3)

    opts = ctk.CTkFrame(tab, fg_color="transparent")
    opts.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 2))
    ctk.CTkCheckBox(opts, text="包含子目录", variable=v_rec).grid(row=0, column=0, sticky="w", pady=2)
    ctk.CTkCheckBox(opts, text="同时剔除姿态（Gimbal*/Flight*）", variable=v_att).grid(
        row=0, column=1, sticky="w", padx=(16, 0), pady=2)
    ctk.CTkCheckBox(opts, text="原地抹除（直接改原照片，不复制）", variable=v_inplace, text_color=danger,
                    fg_color=danger, hover_color=danger_hover).grid(row=1, column=0, sticky="w", pady=2)
    ctk.CTkCheckBox(opts, text="保留定位备份（可还原）", variable=v_backup).grid(
        row=1, column=1, sticky="w", padx=(16, 0), pady=2)

    def update_inplace_state(*_):
        enabled = not v_inplace.get()
        e_dst.configure(state="normal" if enabled else "disabled")
    v_inplace.trace_add("write", update_inplace_state)

    hint(tab, text="说明：剔除 EXIF GPS 和 DJI XMP 位置字段（经纬度、高度、RTK、激光测距目标）；"
                   "相机标定参数保留；图像数据逐字节不变；原地模式会先备份并要求输入目录名确认。").grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(2, 6))

    bar = ctk.CTkFrame(tab, fg_color="transparent")
    bar.grid(row=4, column=0, columnspan=3, sticky="ew")
    bar.columnconfigure(0, weight=1)
    pb = ctk.CTkProgressBar(bar, mode="determinate")
    pb.set(0)
    pb.grid(row=0, column=0, sticky="ew")
    lbl_prog = ctk.CTkLabel(bar, text="", width=100)
    lbl_prog.grid(row=0, column=1, padx=6)
    btn_start = button(bar, "开始", primary=True)
    btn_start.grid(row=0, column=2)
    btn_stop = button(bar, "中止", role="stop", state="disabled")
    btn_stop.grid(row=0, column=3, padx=(6, 0))
    btn_restore = button(bar, "从备份还原", width=100)
    btn_restore.grid(row=0, column=4, padx=(6, 0))

    def append_log(s):
        log_add(lg1, s)

    def poll():
        try:
            for _ in range(500):  # 一次最多处理这么多条，别让界面卡住
                kind, val = q.get_nowait()
                if kind == "log":
                    append_log(val)
                elif kind == "prog":
                    i, n = val
                    pb.set(i / max(n, 1))
                    lbl_prog.configure(text="%d / %d" % (i, n))
                elif kind == "done":
                    set_btn(btn_start, True)
                    set_btn(btn_stop, False)
                    if isinstance(val, Exception):
                        append_log("出错：%s" % val)
                        ui_box.showerror(APP_NAME, str(val))
                    else:
                        log_set_file(lg1, val.get("日志"))
                        residual_count = val.get("残留", 0)
                        msg = "完成：成功 %d，跳过 %d，失败 %d 张、残留 %d 个文件\n日志路径：%s" % (val["成功"], val["跳过"], val.get("失败", 0), residual_count, val["日志"])
                        (ui_box.showwarning if val.get("失败", 0) or residual_count else ui_box.showinfo)(APP_NAME, msg)
        except queue.Empty:
            pass
        root.after(100, poll)

    def ask_inplace_confirm(count, message=None, prompt=None):
        """原地抹除确认框（警示色）：须输入照片目录名；取消/关窗返回 None，确认返回输入的文字（同 askstring）。
        message/prompt 不给就用「剔除定位」页签的文字。版式同统一弹窗（图标在左、按钮靠右）；
        焦点在输入框，回车不确认，必须点「确认抹除」（T10 小毛病 2）。"""
        dlg = ctk.CTkToplevel(root)
        dlg.title(APP_NAME + " - 警告：不可恢复")
        dlg.resizable(False, False)
        dlg.transient(root)
        result = {"text": None}
        v_name = tk.StringVar()
        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(18, 10))
        dialog_icon(body, "error").pack(side="left", anchor="n", padx=(0, 14))
        col = ctk.CTkFrame(body, fg_color="transparent")
        col.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(col, text="⚠ 警告：不可恢复！", text_color=danger,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", pady=(0, 4))
        lbl = ctk.CTkLabel(col, text="", text_color=danger, font=ctk.CTkFont(weight="bold"), justify="left", anchor="w")
        set_wrapped(lbl, message or "会直接修改 %d 张原照片，定位信息删除后不能从照片里找回。" % count, int(440 * win_scale))
        lbl.pack(anchor="w", pady=4)
        ctk.CTkLabel(col, text=prompt or "请输入照片目录的名字（不是完整路径）以确认：").pack(anchor="w", pady=(8, 2))
        ent = ctk.CTkEntry(col, textvariable=v_name, width=440, border_color=danger)
        ent.pack(anchor="w")

        def ok():
            result["text"] = v_name.get()
            dlg.destroy()

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(row, text="取消", width=88, command=dlg.destroy, **secondary).pack(side="right", padx=(8, 0))
        ctk.CTkButton(row, text="确认抹除", width=100, command=ok, fg_color=danger,
                      hover_color=danger_hover).pack(side="right", padx=(8, 0))
        ent.bind("<Return>", lambda ev: "break")  # 回车不确认
        dlg.bind("<Escape>", lambda ev: dlg.destroy())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        show_modal(dlg, ent)
        return result["text"]

    def start():
        src, dst = v_src.get().strip(), v_dst.get().strip()
        if not src:
            ui_box.showwarning(APP_NAME, "请先选择输入目录")
            return
        rec, att, inplace, backup = v_rec.get(), v_att.get(), v_inplace.get(), v_backup.get()
        confirm = None
        if inplace:
            count = sum(1 for p in collect_files(src, rec) if p.suffix.lower() in JPEG_EXTS)
            if not v_backup.get():
                if not ui_box.askyesno(APP_NAME + " - 警告：不可恢复", "已关闭定位备份，抹除后无法还原。仍要继续吗？", icon="warning"):
                    return
            confirm = ask_inplace_confirm(count)
            if confirm is None:
                return
            if confirm != Path(src).name:
                ui_box.showwarning(APP_NAME, "确认目录名不正确，未修改任何文件")
                return
        else:
            if not dst:
                dst = str(default_output_dir(src)); v_dst.set(dst)
            err = check_dirs(src, dst)
            if err:
                ui_box.showwarning(APP_NAME, err); return
        log_clear(lg1); log_set_file(lg1, None); pb.set(0)
        set_btn(btn_start, False); set_btn(btn_stop, True)
        stop = threading.Event(); state["stop"] = stop

        def work():
            try:
                if inplace:
                    res = run_strip_in_place(src, rec, att, not backup, confirm,
                                             progress=lambda i, n: q.put(("prog", (i, n))),
                                             log=lambda s: q.put(("log", s)), stop_event=stop)
                else:
                    res = run_strip(src, dst, rec, att,
                                    progress=lambda i, n: q.put(("prog", (i, n))),
                                    log=lambda s: q.put(("log", s)), stop_event=stop)
                q.put(("done", res))
            except Exception as ex:
                q.put(("done", ex))

        t = threading.Thread(target=work, daemon=True)
        state["worker"] = t
        t.start()

    def restore_start():
        src = v_src.get().strip()
        if not src:
            ui_box.showwarning(APP_NAME, "请先选择照片目录"); return
        backup = filedialog.askopenfilename(title="选择定位备份", filetypes=[("ZIP", "*.zip")])
        if not backup: return
        if not ui_box.askyesno(APP_NAME + " - 确认还原", "将直接修改原照片并写回定位信息，确定继续吗？", icon="warning"):
            return
        try:
            res = restore_gps(src, backup, v_rec.get(), log=append_log)
            log_set_file(lg1, res["日志"])
            msg = "还原完成：成功 %d，跳过 %d，失败 %d\n日志：%s" % (res["成功"], res["跳过"], res["失败"], res["日志"])
            (ui_box.showwarning if res["失败"] else ui_box.showinfo)(APP_NAME, msg)
        except Exception as ex:
            ui_box.showerror(APP_NAME, str(ex))
    btn_start.configure(command=start)
    btn_stop.configure(command=lambda: state["stop"] and state["stop"].set())
    btn_restore.configure(command=restore_start)

    # ---- 页签 2：空三 ----
    tab2, lg2 = paned_tab("空三")
    tab2.columnconfigure(1, weight=1)
    v_ms = tk.StringVar(value=find_metashape())
    v_pdir = tk.StringVar()
    v_out = tk.StringVar()
    v_arec = tk.BooleanVar(value=False)
    v_auto = tk.BooleanVar(value=True)
    v_acc = tk.StringVar(value="高")
    v_kp = tk.StringVar(value=str(DEFAULT_KEYPOINTS))
    v_tp = tk.StringVar(value=str(DEFAULT_TIEPOINTS))
    st2 = {"stop": None}
    q2 = queue.Queue()

    def pick_ms():
        f = filedialog.askopenfilename(title="选择 Metashape Pro 程序（metashape.exe / photoscan.exe）",
                                       filetypes=[("Metashape", "metashape.exe photoscan.exe"), ("程序", "*.exe")])
        if f:
            v_ms.set(os.path.normpath(f))

    def pick_pdir():
        d = filedialog.askdirectory(title="选择照片目录")
        if d:
            v_pdir.set(os.path.normpath(d))

    def pick_out():
        d = filedialog.askdirectory(title="选择输出位置（留空 = 照片目录所在的上级目录）")
        if d:
            v_out.set(os.path.normpath(d))

    rows = (("Metashape 程序：", v_ms, pick_ms), ("照片目录：", v_pdir, pick_pdir), ("输出位置（可空）：", v_out, pick_out))
    for r, (label, var, cmd) in enumerate(rows):
        ctk.CTkLabel(tab2, text=label).grid(row=r, column=0, sticky="w", pady=3)
        ctk.CTkEntry(tab2, textvariable=var).grid(row=r, column=1, sticky="ew", pady=3)
        button(tab2, "浏览…", cmd).grid(row=r, column=2, padx=(6, 0), pady=3)

    o1 = ctk.CTkFrame(tab2, fg_color="transparent")
    o1.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 2))
    ctk.CTkCheckBox(o1, text="包含子目录：每个含照片的子目录单独一个工程", variable=v_arec).pack(side="left")
    ctk.CTkCheckBox(o1, text="照片带定位时先自动去定位", variable=v_auto).pack(side="left", padx=(16, 0))
    o2 = ctk.CTkFrame(tab2, fg_color="transparent")
    o2.grid(row=4, column=0, columnspan=3, sticky="w", pady=2)
    ctk.CTkLabel(o2, text="精度：").pack(side="left")
    ctk.CTkOptionMenu(o2, variable=v_acc, values=["高", "中", "低"], width=70).pack(side="left")
    ctk.CTkLabel(o2, text="    关键点上限：").pack(side="left")
    spinbox(o2, v_kp, 1000, 200000, 1000, 80).pack(side="left")
    ctk.CTkLabel(o2, text="    连接点上限：").pack(side="left")
    spinbox(o2, v_tp, 0, 100000, 1000, 80).pack(side="left")

    hint(tab2, text="说明：需要 Metashape Pro（标准版不能运行脚本）。输出到「<照片目录名>_空三」：工程 .psx、"
                    "不带坐标系的 Blocks Exchange XML、空三报告.txt、Metashape 日志。结果是局部坐标、尺度任意。"
                    "输出位置留空时放在照片目录旁边；照片在只读盘上时请指定输出位置。").grid(
        row=5, column=0, columnspan=3, sticky="w", pady=(2, 6))

    bar2 = ctk.CTkFrame(tab2, fg_color="transparent")
    bar2.grid(row=6, column=0, columnspan=3, sticky="ew")
    bar2.columnconfigure(0, weight=1)
    pb2 = ctk.CTkProgressBar(bar2, mode="determinate")
    pb2.set(0)
    pb2.grid(row=0, column=0, sticky="ew")
    lbl2 = ctk.CTkLabel(bar2, text="", width=190)
    lbl2.grid(row=0, column=1, padx=6)
    btn2_start = button(bar2, "开始", primary=True)
    btn2_start.grid(row=0, column=2)
    btn2_stop = button(bar2, "取消", role="stop", state="disabled")
    btn2_stop.grid(row=0, column=3, padx=(6, 0))

    def poll2():
        try:
            for _ in range(500):
                kind, val = q2.get_nowait()
                if kind == "log":
                    log_add(lg2, val)
                elif kind == "prog":
                    pb2.set(int(val[0] * 1000) / 1000)
                    lbl2.configure(text=val[1][:30])
                elif kind == "done":
                    set_btn(btn2_start, True)
                    set_btn(btn2_stop, False)
                    if isinstance(val, AlignCancelled):
                        lbl2.configure(text="已取消")
                        ui_box.showwarning(APP_NAME, "已取消。输出目录里的结果不完整，不要使用。")
                    elif isinstance(val, Exception):
                        lbl2.configure(text="失败")
                        log_add(lg2, "出错：%s" % val)
                        ui_box.showerror(APP_NAME, str(val))
                    else:
                        ok = [r for r in val if r["status"] == "成功"]
                        logs2 = [Path(r["out_dir"]) / "metashape_日志.txt" for r in val if r.get("out_dir")]
                        log_set_file(lg2, next((f for f in reversed(logs2) if f.is_file()), None))
                        lines = []
                        for r in val:
                            a = "，对齐 %s/%s" % (r["aligned"], r["total"]) if r["aligned"] is not None else ""
                            lines.append("%s%s：%s%s" % (r["status"], a, Path(r["out_dir"]).name,
                                                       "\n  " + r["reason"] if r["reason"] else ""))
                        full = len(ok) == len(val) and all(r["aligned"] == r["total"] for r in ok)
                        lbl2.configure(text="完成" if len(ok) == len(val) else "有失败")
                        (ui_box.showinfo if full else ui_box.showwarning)(
                            APP_NAME, "成功 %d / 共 %d 个工程\n\n%s" % (len(ok), len(val), "\n".join(lines)))
        except queue.Empty:
            pass
        root.after(200, poll2)

    def start2():
        ms, pdir, out = v_ms.get().strip(), v_pdir.get().strip(), v_out.get().strip()
        err = check_metashape(ms)
        if err:
            ui_box.showwarning(APP_NAME, err)
            return
        if not pdir:
            ui_box.showwarning(APP_NAME, "请先选择照片目录")
            return
        try:
            kp, tp = int(v_kp.get()), int(v_tp.get())
        except ValueError:
            ui_box.showwarning(APP_NAME, "关键点上限、连接点上限要填整数")
            return
        cfg = load_config()
        cfg["metashape"] = ms
        save_config(cfg)
        acc = {"高": "high", "中": "medium", "低": "low"}[v_acc.get()]
        log_clear(lg2)
        log_set_file(lg2, None)
        pb2.set(0)
        lbl2.configure(text="")
        set_btn(btn2_start, False)
        set_btn(btn2_stop, True)
        stop = threading.Event()
        st2["stop"] = stop
        rec, auto = v_arec.get(), v_auto.get()

        def work():
            try:
                res = run_align(pdir, ms, out or None, rec, auto, acc, kp, tp,
                                progress=lambda f, s: q2.put(("prog", (f, s))),
                                log=lambda s: q2.put(("log", s)), stop_event=stop)
                if stop.is_set() and (not res or res[-1]["status"] == "已取消"):
                    raise AlignCancelled()
                q2.put(("done", res))
            except Exception as ex:
                q2.put(("done", ex))

        threading.Thread(target=work, daemon=True).start()

    def stop2():
        if st2["stop"] is not None:
            st2["stop"].set()
            lbl2.configure(text="正在取消…")

    btn2_start.configure(command=start2)
    btn2_stop.configure(command=stop2)
    root.after(200, poll2)

    # ---- 页签 3：控制点（D-16、D-17） ----
    tab3, lg3 = paned_tab("控制点")
    tab3.columnconfigure(1, weight=1)
    cfg3 = load_config()
    v3_mode = tk.StringVar(value="transfer")
    v3_ms = tk.StringVar(value=find_metashape())
    v3_ref = tk.StringVar(value=cfg3.get("gcp_ref", ""))
    v3_pdir = tk.StringVar()
    v3_psx = tk.StringVar()
    v3_csv = tk.StringVar(value=cfg3.get("gcp_csv", ""))
    v3_cc = tk.StringVar()
    v3_out = tk.StringVar()
    v3_acc = tk.StringVar(value=str(DEFAULT_GCP_ACCURACY))
    v3_auto = tk.BooleanVar(value=True)
    v3_excl = tk.StringVar()
    v3_shift = tk.StringVar(value="%g" % GCP_MAX_SHIFT_PX)
    st3 = {"stop": None}
    q3 = queue.Queue()

    def pick_file(var, title, types):
        f = filedialog.askopenfilename(title=title, filetypes=types)
        if f:
            var.set(os.path.normpath(f))

    def pick_dir(var, title):
        d = filedialog.askdirectory(title=title)
        if d:
            var.set(os.path.normpath(d))

    psx_types = [("Metashape 工程", "*.psx")]
    rows3 = (
        ("Metashape 程序：", v3_ms, lambda: pick_file(v3_ms, "选择 Metashape Pro 程序（metashape.exe）",
                                                   [("Metashape", "metashape.exe photoscan.exe"), ("程序", "*.exe")]), None),
        ("参考期工程 .psx：", v3_ref, lambda: pick_file(v3_ref, "选择参考期工程（已有控制点，只读）", psx_types), "transfer"),
        ("新一期照片目录：", v3_pdir, lambda: pick_dir(v3_pdir, "选择新一期照片目录"), "transfer"),
        ("空三工程 .psx：", v3_psx, lambda: pick_file(v3_psx, "选择「空三」页签产出的 .psx（只读）", psx_types), "init"),
        ("控制点 CSV：", v3_csv, lambda: pick_file(v3_csv, "选择控制点 CSV（点名,X,Y,Z，米）",
                                                [("CSV", "*.csv *.txt"), ("全部", "*.*")]), "init"),
        ("CC 刺点（可空）：", v3_cc, lambda: pick_file(v3_cc, "选择 ContextCapture 导出的 Blocks Exchange（含控制点刺点）",
                                                   [("Blocks Exchange", "*.xml *.xmlz"), ("全部", "*.*")]), "init"),
        ("输出位置（可空）：", v3_out, lambda: pick_dir(v3_out, "选择输出位置（留空 = 默认位置）"), None),
    )
    mode_widgets = []
    mf = ctk.CTkFrame(tab3, fg_color="transparent")
    mf.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
    ctk.CTkLabel(mf, text="模式：").pack(side="left")
    ctk.CTkRadioButton(mf, text="迁移（新一期，零刺点）", value="transfer", variable=v3_mode).pack(side="left")
    ctk.CTkRadioButton(mf, text="建参考期（新场地第一次）", value="init", variable=v3_mode).pack(side="left", padx=(16, 0))
    for r, (label, var, cmd, mode) in enumerate(rows3, 1):
        w1 = ctk.CTkLabel(tab3, text=label)
        w1.grid(row=r, column=0, sticky="w", pady=2)
        w2 = ctk.CTkEntry(tab3, textvariable=var)
        w2.grid(row=r, column=1, sticky="ew", pady=2)
        w3 = button(tab3, "浏览…", cmd)
        w3.grid(row=r, column=2, padx=(6, 0), pady=2)
        if mode:
            mode_widgets.append((mode, (w1, w2, w3)))
    o3 = ctk.CTkFrame(tab3, fg_color="transparent")
    o3.grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 2))
    ctk.CTkLabel(o3, text="控制点精度（米）：").pack(side="left")
    spinbox(o3, v3_acc, 0.001, 1.0, 0.005, 70).pack(side="left")
    cb_auto3 = ctk.CTkCheckBox(o3, text="照片带定位时先自动去定位", variable=v3_auto)
    cb_auto3.pack(side="left", padx=(16, 0))
    mode_widgets.append(("transfer", (cb_auto3,)))
    o3b = ctk.CTkFrame(tab3, fg_color="transparent")
    o3b.grid(row=9, column=0, columnspan=3, sticky="w", pady=(2, 2))
    ctk.CTkLabel(o3b, text="排除的控制点（可空，如 1,2）：").pack(side="left")
    ctk.CTkEntry(o3b, textvariable=v3_excl, width=100).pack(side="left")
    lbl3_shift = ctk.CTkLabel(o3b, text="细化挪动上限（px）：")
    lbl3_shift.pack(side="left", padx=(16, 0))
    sp3_shift = spinbox(o3b, v3_shift, 5, 500, 5, 60)
    sp3_shift.pack(side="left")
    mode_widgets.append(("transfer", (lbl3_shift,) + sp3_shift.parts))
    lbl3_help = hint(tab3)
    lbl3_help.grid(row=10, column=0, columnspan=3, sticky="w", pady=(2, 4))
    helps = {
        "transfer": "迁移：新一期照片和参考期一起匹配，自动放控制点、细化、平差、按 D-17 判定。参考期的照片要留在原位置。"
                    "某点细化挪动中位数超过上限（默认 50 px，多半认错角点）会自动排除、改作检查点，结论为「需人工核对」。"
                    "输出「<照片目录名>_控制点」：.psx、Blocks Exchange XML（局部坐标，米）、控制点报告.txt。一次约 5 分钟。",
        "init": "建参考期：用「空三」页签的 .psx + 控制点 CSV；有 CC 刺点就选上（没有就先在 Metashape 里给 ≥3 个点各刺 ≥2 张并保存）。"
                "会自动判断 CSV 的 X/Y 是否对调。输出的 .psx 以后当参考期用。",
    }

    def on_mode(*_):
        m = v3_mode.get()
        for mode, ws in mode_widgets:
            for w in ws:
                set_enabled(w, mode == m)
        set_wrapped(lbl3_help, helps[m])

    v3_mode.trace_add("write", on_mode)
    on_mode()

    bar3 = ctk.CTkFrame(tab3, fg_color="transparent")
    bar3.grid(row=11, column=0, columnspan=3, sticky="ew")
    bar3.columnconfigure(0, weight=1)
    pb3 = ctk.CTkProgressBar(bar3, mode="determinate")
    pb3.set(0)
    pb3.grid(row=0, column=0, sticky="ew")
    lbl3 = ctk.CTkLabel(bar3, text="", width=230)
    lbl3.grid(row=0, column=1, padx=6)
    btn3_start = button(bar3, "开始", primary=True)
    btn3_start.grid(row=0, column=2)
    btn3_stop = button(bar3, "取消", role="stop", state="disabled")
    btn3_stop.grid(row=0, column=3, padx=(6, 0))

    def poll3():
        try:
            for _ in range(500):
                kind, val = q3.get_nowait()
                if kind == "log":
                    log_add(lg3, val)
                elif kind == "prog":
                    pb3.set(int(val[0] * 1000) / 1000)
                    lbl3.configure(text=val[1][:30])
                elif kind == "done":
                    set_btn(btn3_start, True)
                    set_btn(btn3_stop, False)
                    if isinstance(val, AlignCancelled):
                        lbl3.configure(text="已取消")
                        ui_box.showwarning(APP_NAME, "已取消。输出目录里的结果不完整，不要使用。")
                    elif isinstance(val, Exception):
                        lbl3.configure(text="失败")
                        log_add(lg3, "出错：%s" % val)
                        ui_box.showerror(APP_NAME, str(val))
                    else:
                        r = val
                        if r.get("out_dir") and (Path(r["out_dir"]) / "metashape_日志.txt").is_file():
                            log_set_file(lg3, Path(r["out_dir"]) / "metashape_日志.txt")
                        lbl3.configure(text=r["status"])
                        head = "判定：%s" % r["status"]
                        body = ["输出：%s" % r["out_dir"]]
                        if r["aligned"] is not None:
                            body.insert(0, "对齐：%d/%d%s" % (r["aligned"], r["total"],
                                                            "（%d 张没对齐）" % len(r["unaligned"]) if r["unaligned"] else ""))
                        if r["rms3d"] is not None:
                            body.insert(1, "RMS3D：%.1f mm（%d 个点）" % (r["rms3d"] * 1000, len(r["rows"])))
                        if r["swap"] and (r["swap"]["use_swap"] or not r["swap"]["confident"]):
                            body.insert(0, "X/Y：%s" % _swap_verdict(r["swap"]))
                        ex = r["excluded"] or {}
                        auto = [n for n, v in ex.items() if v == "auto"]
                        user = [n for n, v in ex.items() if v == "user"]
                        if user:
                            body.insert(0, "%s 号是你排除的（改作检查点）" % "、".join(user))
                        if auto:
                            body.insert(0, "%s 号细化挪动过大，已自动排除（改作检查点）" % "、".join(auto))
                        if r["status"] == "自动通过" and not r["unaligned"]:
                            ui_box.showinfo(APP_NAME, "\n".join([head] + body))
                        elif r["status"] in ("自动通过", "需人工核对"):
                            extra = ["%s 号：%s" % (n, "；".join(w)) for n, w in r["bad"]]
                            if extra:
                                extra.insert(0, "\n需人工核对的点（处理步骤见 控制点报告.txt）：")
                            ui_box.showwarning(APP_NAME + " - " + r["status"], "\n".join([head] + body + extra))
                        else:
                            ui_box.showerror(APP_NAME, "%s\n%s\n\n%s" % (head, r["reason"], "\n".join(body)))
        except queue.Empty:
            pass
        root.after(200, poll3)

    def start3():
        mode = v3_mode.get()
        ms = v3_ms.get().strip()
        err = check_metashape(ms)
        if err:
            ui_box.showwarning(APP_NAME, err)
            return
        try:
            acc = _check_gcp_accuracy(v3_acc.get().strip())
            shift = _check_max_shift(v3_shift.get().strip())
        except ValueError as ex:
            ui_box.showwarning(APP_NAME, str(ex))
            return
        excl = parse_exclude(v3_excl.get())
        ref, pdir, psx, csvp, ccp, out = (v.get().strip() for v in (v3_ref, v3_pdir, v3_psx, v3_csv, v3_cc, v3_out))
        if mode == "transfer" and (not ref or not pdir):
            ui_box.showwarning(APP_NAME, "请选择参考期工程和新一期照片目录")
            return
        if mode == "init" and (not psx or not csvp):
            ui_box.showwarning(APP_NAME, "请选择空三工程和控制点 CSV")
            return
        cfg = load_config()
        cfg["metashape"] = ms
        if mode == "transfer":
            cfg["gcp_ref"] = ref
        else:
            cfg["gcp_csv"] = csvp
        save_config(cfg)
        log_clear(lg3)
        log_set_file(lg3, None)
        pb3.set(0)
        lbl3.configure(text="")
        set_btn(btn3_start, False)
        set_btn(btn3_stop, True)
        stop = threading.Event()
        st3["stop"] = stop
        auto = v3_auto.get()
        kw = dict(progress=lambda f, s: q3.put(("prog", (f, s))), log=lambda s: q3.put(("log", s)), stop_event=stop)

        def work():
            try:
                if mode == "transfer":
                    res = run_gcp_transfer(pdir, ref, ms, out or None, acc, auto, exclude=excl, max_shift=shift, **kw)
                else:
                    res = run_gcp_init(psx, csvp, ms, ccp or None, out or None, acc, exclude=excl, **kw)
                if res["status"] == "已取消":
                    raise AlignCancelled()
                q3.put(("done", res))
            except Exception as ex:
                q3.put(("done", ex))

        threading.Thread(target=work, daemon=True).start()

    def stop3():
        if st3["stop"] is not None:
            st3["stop"].set()
            lbl3.configure(text="正在取消…")

    btn3_start.configure(command=start3)
    btn3_stop.configure(command=stop3)
    root.after(200, poll3)

    # ---- 页签 4：批量迁移（D-24）：扫描上级目录 → 按规则表配参考期 → 筛选勾选 → 一期接一期跑 ----
    tab4, lg4 = paned_tab("批量迁移")
    tab4.columnconfigure(1, weight=1)
    cfg4 = load_config()
    v4_root = tk.StringVar(value=cfg4.get("batch_root", ""))
    v4_out = tk.StringVar(value=cfg4.get("batch_out", ""))
    v4_ms = tk.StringVar(value=find_metashape())
    v4_filter = tk.StringVar()
    v4_mode = tk.StringVar(value="copy")  # D-26：不记进 config.json，每次启动都是「复制」
    v4_skip = tk.StringVar(value=cfg4.get("batch_skip", ""))
    st4 = {"stop": None, "items": [], "rows": [], "scanned": None, "running": False, "closing": False}
    q4 = queue.Queue()
    rows4 = (
        ("照片上级目录：", v4_root, lambda: pick_dir(v4_root, "选择照片上级目录（每个直接含照片的子目录算一期，只读）")),
        ("输出目录：", v4_out, lambda: pick_dir(v4_out, "选择输出目录（每期的去定位照片、结果和汇总表都写在这里）")),
        ("Metashape 程序：", v4_ms, lambda: pick_file(v4_ms, "选择 Metashape Pro 程序（metashape.exe）",
                                                   [("Metashape", "metashape.exe photoscan.exe"), ("程序", "*.exe")])),
    )
    for r, (label, var, cmd) in enumerate(rows4):
        ctk.CTkLabel(tab4, text=label).grid(row=r, column=0, sticky="w", pady=2)
        ctk.CTkEntry(tab4, textvariable=var).grid(row=r, column=1, sticky="ew", pady=2)
        button(tab4, "浏览…", cmd).grid(row=r, column=2, padx=(6, 0), pady=2)

    # 参考期规则表：关键词 + 参考期 .psx + 排除点；从上到下匹配第一条；记进 config.json
    rf = ctk.CTkFrame(tab4)
    rf.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 2))
    rf.columnconfigure(1, weight=1)
    for c, t in enumerate(("关键词", "参考期 .psx（从上到下匹配第一条）", "", "排除点")):
        ctk.CTkLabel(rf, text=t, text_color=hint_color, height=20).grid(row=0, column=c, sticky="w", padx=(6, 4))
    rules4 = []

    def show_tail(e):  # 路径太长时让输入框显示尾部（文件名）；正在编辑的不动
        try:
            if e.focus_get() is not e._entry:
                e.xview_moveto(1.0)
        except (tk.TclError, KeyError):
            pass

    def tooltip(w, text_of):
        tip = {"w": None}

        def show(ev):
            text = text_of()
            if not text or tip["w"] is not None:
                return
            tw = tk.Toplevel(w)
            tw.wm_overrideredirect(True)
            tw.wm_geometry("+%d+%d" % (ev.x_root + 12, ev.y_root + 18))
            tk.Label(tw, text=text, background="#FFFFE0", foreground="black", relief="solid", borderwidth=1,
                     padx=4, pady=2).pack()
            tip["w"] = tw

        def hide(ev=None):
            if tip["w"] is not None:
                tip["w"].destroy()
                tip["w"] = None

        w.bind("<Enter>", show, add="+")
        w.bind("<Leave>", hide, add="+")
        w.bind("<ButtonPress>", hide, add="+")

    def regrid_rules():
        for i, r in enumerate(rules4, 1):
            for c, w in enumerate(r["widgets"]):
                w.grid(row=i, column=c, sticky="ew" if c == 1 else "w", padx=(6, 4) if c == 0 else (0, 4), pady=(0, 3))

    def add_rule(key="", psx="", exclude=""):
        r = {"key": tk.StringVar(value=key), "psx": tk.StringVar(value=psx), "exclude": tk.StringVar(value=exclude)}
        e_psx = ctk.CTkEntry(rf, textvariable=r["psx"])
        r["widgets"] = (
            ctk.CTkEntry(rf, textvariable=r["key"], width=80),
            e_psx,
            button(rf, "浏览…", lambda: pick_file(r["psx"], "选择规则「%s」的参考期工程" % r["key"].get(), psx_types)),
            ctk.CTkEntry(rf, textvariable=r["exclude"], width=60),
            button(rf, "删除", lambda: del_rule(r), width=52),
        )
        r["psx"].trace_add("write", lambda *_: e_psx.after_idle(lambda: show_tail(e_psx)))
        e_psx.bind("<FocusOut>", lambda ev: show_tail(e_psx), add="+")
        e_psx.bind("<Configure>", lambda ev: show_tail(e_psx), add="+")
        tooltip(e_psx, lambda: r["psx"].get().strip())
        rules4.append(r)
        regrid_rules()
        e_psx.after_idle(lambda: show_tail(e_psx))

    def del_rule(r):
        for w in r["widgets"]:
            w.destroy()
        rules4.remove(r)
        regrid_rules()

    def rules_now():
        return [{"key": r["key"].get().strip(), "psx": r["psx"].get().strip(),
                 "exclude": ",".join(parse_exclude(r["exclude"].get()))} for r in rules4]

    button(rf, "＋ 添加规则", lambda: add_rule(), width=96).grid(row=0, column=4, padx=(0, 4), pady=(3, 3))
    for r in cfg4.get("batch_rules") or BATCH_DEFAULT_RULES:
        add_rule(r.get("key", ""), r.get("psx", ""), r.get("exclude", ""))
    of4 = ctk.CTkFrame(tab4, fg_color="transparent")
    of4.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(2, 0))
    of4.columnconfigure(2, weight=1)
    ctk.CTkLabel(of4, text="照片处理方式：").grid(row=0, column=0, sticky="w")
    ctk.CTkRadioButton(of4, text="复制一份去定位照片到输出目录（原片不动）", variable=v4_mode, value="copy",
                       radiobutton_width=18, radiobutton_height=18).grid(row=0, column=1, sticky="w", padx=(0, 12))
    ctk.CTkRadioButton(of4, text="直接在原照片上抹除定位（不复制，留定位备份）", variable=v4_mode, value="inplace",
                       text_color=danger, fg_color=danger, hover_color=danger_hover, radiobutton_width=18,
                       radiobutton_height=18).grid(row=0, column=2, sticky="w")
    ctk.CTkLabel(of4, text="跳过这些期次（逗号分隔）：").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
    ctk.CTkEntry(of4, textvariable=v4_skip).grid(row=1, column=1, columnspan=2, sticky="ew", pady=(4, 0), padx=(150, 0))
    hint(tab4, "直接含照片的子目录算一期。判定为自动通过/需人工核对的期算已有结果、跳过；上次已取消或失败的期显示"
               "「待运行（上次…）」，重跑前旧结果目录改名保留。已有「<名>_去定位」的直接用。复制方式每期多占一份照片的空间；"
               "原地方式直接改原照片的定位，定位备份写在各期照片目录里（可还原）。判定会有波动，需人工核对的期可以先重跑一次"
               "（把它的 _控制点 目录改名后重扫）。筛选只影响显示；四个勾选按钮只作用于当前显示的行。"
               ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 2))

    tb4 = ctk.CTkFrame(tab4, fg_color="transparent")
    tb4.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(2, 2))
    btn4_scan = button(tb4, "扫描", primary=True)
    btn4_scan.pack(side="left")
    ctk.CTkLabel(tb4, text="筛选：").pack(side="left", padx=(12, 0))
    ctk.CTkEntry(tb4, textvariable=v4_filter, width=120).pack(side="left")
    bulk4 = []
    for t in ("全选", "全不选", "反选", "只勾待运行"):
        b = button(tb4, t, width=56 if len(t) <= 3 else 88)
        b.pack(side="left", padx=(6, 0))
        bulk4.append(b)
    lbl4_count = ctk.CTkLabel(tb4, text="", text_color=hint_color)
    lbl4_count.pack(side="left", padx=(10, 0))

    lst4 = ctk.CTkScrollableFrame(tab4, height=90)  # 默认矮一点把高度让给日志；往下拖分隔条时列表跟着变高（这一行 weight=1）
    lst4._scrollbar.configure(height=60)  # CTk 竖滚动条默认要 200 高，会把列表撑高（customtkinter 版本钉死 5.2.2）
    lst4.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(2, 4))
    lst4.columnconfigure(6, weight=1)
    tab4.rowconfigure(7, weight=1)
    cols4 = (("", 28, "w"), ("序号", 36, "e"), ("期次", 150, "w"), ("匹配规则", 64, "w"), ("照片数", 52, "e"),
             ("参考期", 210, "w"), ("状态", 120, "w"))
    for c, (t, w, a) in enumerate(cols4):
        if t:
            ctk.CTkLabel(lst4, text=t, width=w, anchor=a, text_color=hint_color, height=20).grid(row=0, column=c, sticky="ew",
                                                                                             padx=(0, 6))
    status_color = {"待运行（": ("#6A1B9A", "#CE93D8"), "自动通过": ("#2E7D32", "#81C784"),
                    "需人工核对": ("#E65100", "#FFB74D"), "失败": danger,
                    "已取消": hint_color, "运行中": ("#1565C0", "#64B5F6"), "等待其他 Metashape": ("#1565C0", "#64B5F6")}

    def color_of(text):
        for k, c in status_color.items():
            if text.startswith(k):
                return c
        return hint_color if text.startswith("跳过") or text == "已有结果" else label_color

    def visible(row):
        return v4_filter.get().strip().lower() in row["item"]["name"].lower()

    def update_count(*_):
        rs = st4["rows"]
        sel = [r for r in rs if r["var"].get()]
        lbl4_count.configure(text="已勾 %d 期（待运行 %d）；显示 %d/%d" % (
            len(sel), sum(r["item"]["status"].startswith("待运行") for r in sel), sum(visible(r) for r in rs), len(rs)))

    def apply_filter(*_):  # 只改显示；勾选状态放在每行自己的变量里，藏起来也不变
        for r in st4["rows"]:
            for w in r["widgets"]:
                if visible(r):
                    w.grid()
                else:
                    w.grid_remove()
        update_count()

    v4_filter.trace_add("write", apply_filter)

    def bulk(kind):
        for r in st4["rows"]:
            if visible(r):
                r["var"].set({"全选": True, "全不选": False, "反选": not r["var"].get(),
                              "只勾待运行": r["item"]["status"].startswith("待运行")}[kind])
        update_count()

    for b in bulk4:
        b.configure(command=lambda t=b.cget("text"): bulk(t))

    def set_row_status(row, text):
        row["status"].configure(text=text, text_color=color_of(text))

    def fill_list(items):
        for r in st4["rows"]:
            for w in r["widgets"]:
                w.destroy()
        st4["rows"] = []
        for i, it in enumerate(items, 1):
            var = tk.BooleanVar(value=it["status"].startswith("待运行"))
            cells = (ctk.CTkCheckBox(lst4, text="", width=28, checkbox_width=20, checkbox_height=20, variable=var,
                                     command=update_count),
                     str(it["idx"]), it["name"], it["rule"] or "—", str(it["n"]), Path(it["ref"]).name if it["ref"] else "",
                     it["status"])
            ws = [cells[0]]
            for c, (t, w, a) in enumerate(cols4[1:], 1):
                ws.append(ctk.CTkLabel(lst4, text=cells[c], width=w, anchor=a, height=22))
            for c, w in enumerate(ws):
                w.grid(row=i, column=c, sticky="ew", padx=(0, 6))
            row = {"item": it, "var": var, "widgets": ws, "status": ws[-1]}
            set_row_status(row, it["status"])
            st4["rows"].append(row)
        apply_filter()

    def scan_key():
        return (v4_root.get().strip(), os.path.abspath(v4_out.get().strip()) if v4_out.get().strip() else "",
                json.dumps(rules_now(), ensure_ascii=False), tuple(_split_names(v4_skip.get())))

    def save4():
        cfg = load_config()
        cfg.update(batch_root=v4_root.get().strip(), batch_out=v4_out.get().strip(), batch_rules=rules_now(),
                   batch_skip=",".join(_split_names(v4_skip.get())))  # 照片处理方式不记（D-26）
        if v4_ms.get().strip():
            cfg["metashape"] = v4_ms.get().strip()
        save_config(cfg)

    def scan4():
        root_dir, out = v4_root.get().strip(), v4_out.get().strip()
        if not root_dir or not out:
            ui_box.showwarning(APP_NAME, "请选择照片上级目录和输出目录")
            return
        rules = rules_now()
        if not any(r["key"] for r in rules):
            ui_box.showwarning(APP_NAME, "规则表至少要有一条带关键词的规则")
            return
        errs = check_batch_rules(rules)
        if errs:
            ui_box.showwarning(APP_NAME, "\n".join(errs))
            return
        try:
            items = batch_scan(root_dir, rules, os.path.abspath(out), skip=_split_names(v4_skip.get()))
        except (ValueError, OSError) as ex:
            ui_box.showwarning(APP_NAME, str(ex))
            return
        save4()
        st4["items"], st4["scanned"] = items, scan_key()
        fill_list(items)
        cnt = {}
        for it in items:
            cnt[it["status"]] = cnt.get(it["status"], 0) + 1
        q4.put(("log", "扫描 %s：共 %d 期；%s" % (root_dir, len(items), "，".join("%s %d" % kv for kv in cnt.items()))))
        odd = []
        for it in items:
            if it["status"] == "已有结果":
                v = batch_read_report(it["report"])["判定"]
                if v != "自动通过":
                    odd.append("%s（%s）" % (it["name"], v or "报告里没有判定"))
        if odd:
            q4.put(("log", "注意：这些期已有结果但不是自动通过，会跳过；判定会有波动，想重跑一次就把输出目录里它的 _控制点 "
                           "目录改名后再扫描：%s" % "、".join(odd)))
        again = [it["name"] for it in items if it["status"].startswith("待运行（")]
        if again:
            q4.put(("log", "这些期上次已取消或失败，重跑前会把旧结果目录改名保留：%s" % "、".join(again)))

    btn4_scan.configure(command=scan4)

    bar4 = ctk.CTkFrame(tab4, fg_color="transparent")
    bar4.grid(row=8, column=0, columnspan=3, sticky="ew")
    bar4.columnconfigure(0, weight=1)
    pb4 = ctk.CTkProgressBar(bar4, mode="determinate")
    pb4.set(0)
    pb4.grid(row=0, column=0, sticky="ew")
    lbl4 = ctk.CTkLabel(bar4, text="", width=260)
    lbl4.grid(row=0, column=1, padx=6)
    btn4_start = button(bar4, "开始", primary=True)
    btn4_start.grid(row=0, column=2)
    btn4_stop = button(bar4, "取消", role="stop", state="disabled")
    btn4_stop.grid(row=0, column=3, padx=(6, 0))

    def poll4():
        try:
            for _ in range(500):  # 一次最多处理这么多条，别让界面卡住
                kind, val = q4.get_nowait()
                if kind == "log":
                    log_add(lg4, val)  # 日志框只留最近 5000 行，完整日志在输出目录的 批量迁移日志_<时间>.txt
                    log_refresh_open(lg4)
                elif kind == "prog":
                    pb4.set(int(val[0] * 1000) / 1000)
                    lbl4.configure(text=val[1][:44])
                elif kind == "item":
                    it, text = val
                    for r in st4["rows"]:
                        if r["item"] is it:
                            set_row_status(r, text)
                    update_count()
                elif kind == "done":
                    set_btn(btn4_start, True)
                    set_btn(btn4_scan, True)
                    set_btn(btn4_stop, False)
                    st4["scanned"] = None  # 状态都变了，再跑要先重新扫描
                    st4["running"] = False
                    if st4["closing"]:  # 用户点了关窗口：当前这一期已停好，现在关
                        remember_window()
                        root.destroy()
                        return
                    if isinstance(val, Exception):
                        lbl4.configure(text="失败")
                        log_add(lg4, "出错：%s" % val)
                        ui_box.showerror(APP_NAME, str(val))
                        continue
                    res, logf = val
                    body = ["%s：%d 期" % kv for kv in res["counts"].items()]
                    for st_name in ("需人工核对", "失败", "拒绝"):
                        names = [r["期次"] for r in res["rows"] if r["状态"] == st_name]
                        if names:
                            body.append("%s：%s" % (st_name, "、".join(names)))
                    nb_backup = sum(1 for r in res["rows"] if r.get("定位备份"))
                    if nb_backup:
                        body.append("本次写了 %d 份定位备份，在各期照片目录里（定位备份_<时间>.zip），都要留着。" % nb_backup)
                    body += ["", "汇总表：%s" % res["summary"], "完整日志：%s" % logf]
                    if res["cancelled"]:
                        lbl4.configure(text="已取消")
                        last = res["rows"][-1] if res["rows"] else None
                        if last and last["状态"] == "已取消":
                            body.append("\n被取消的「%s」结果不完整，不要使用；重新扫描会显示「待运行（上次已取消）」，"
                                        "重跑时旧结果目录改名保留。" % last["期次"])
                        ui_box.showwarning(APP_NAME + " - 已取消",
                                               "已取消：已完成的期次结果都保留，剩下的没有开始。\n\n" + "\n".join(body))
                    else:
                        lbl4.configure(text="完成")
                        bad = res["exit"] != 0
                        (ui_box.showwarning if bad else ui_box.showinfo)(
                            APP_NAME + " - 批量迁移完成", ("有需人工核对或失败的期次，详见汇总表和各期的控制点报告.txt。\n\n"
                                                    if bad else "") + "\n".join(body))
        except queue.Empty:
            pass
        root.after(200, poll4)

    def start4():
        if not st4["items"] or st4["scanned"] is None:
            ui_box.showwarning(APP_NAME, "请先点「扫描」")
            return
        if scan_key() != st4["scanned"]:
            ui_box.showwarning(APP_NAME, "目录或规则表在扫描后改过了，请先重新扫描")
            return
        ms = v4_ms.get().strip()
        err = check_metashape(ms)
        if err:
            ui_box.showwarning(APP_NAME, err)
            return
        chosen = [r["item"] for r in st4["rows"] if r["var"].get()]
        if not any(it["status"].startswith("待运行") for it in chosen):
            ui_box.showwarning(APP_NAME, "勾选的期次里没有「待运行」的")
            return
        out = os.path.abspath(v4_out.get().strip())
        mode, root_dir, confirm = v4_mode.get(), v4_root.get().strip(), None
        if mode == "inplace":
            todo = [it for it in chosen if it["status"].startswith("待运行") and not (
                (Path(out) / (it["name"] + OUTPUT_SUFFIX)).is_dir() and any((Path(out) / (it["name"] + OUTPUT_SUFFIX)).iterdir()))]
            confirm = ask_inplace_confirm(
                sum(it["n"] for it in todo),
                message="将直接修改 %d 期、共 %d 张原照片的定位，备份写在各期照片目录里（定位备份_<时间>.zip）。"
                        "本来就没定位的照片不会改。" % (len(todo), sum(it["n"] for it in todo)),
                prompt="请输入照片上级目录的名字（不是完整路径）以确认：")
            if confirm is None:
                return
            if confirm != Path(os.path.abspath(root_dir)).name:
                ui_box.showwarning(APP_NAME, "确认名不正确，未修改任何文件")
                return
            try:
                check_batch_in_place(root_dir, chosen, confirm)
            except ValueError as ex:
                ui_box.showwarning(APP_NAME, str(ex))
                return
        save4()
        log_clear(lg4)
        pb4.set(0)
        lbl4.configure(text="")
        set_btn(btn4_start, False)
        set_btn(btn4_scan, False)
        set_btn(btn4_stop, True)
        stop = threading.Event()
        st4["stop"] = stop
        st4["running"] = True
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_set_file(lg4, Path(out) / ("批量迁移日志_%s.txt" % stamp))  # 文件建好后「打开完整日志」变可点

        def work():
            logf = Path(out) / ("批量迁移日志_%s.txt" % stamp)
            try:
                Path(out).mkdir(parents=True, exist_ok=True)
                with open(logf, "a", encoding="utf-8") as flog:
                    def log(s):
                        flog.write(s + "\n")
                        flog.flush()
                        q4.put(("log", s))

                    res = run_gcp_batch(chosen, out, ms, log=log, progress=lambda f, s: q4.put(("prog", (f, s))),
                                        on_item=lambda it, phase, text: q4.put(("item", (it, text))), stop_event=stop,
                                        summary=Path(out) / ("批量迁移汇总_%s.csv" % stamp), photo_mode=mode,
                                        root=root_dir, confirm=confirm)
                q4.put(("done", (res, str(logf))))
            except Exception as ex:
                q4.put(("done", ex))

        threading.Thread(target=work, daemon=True).start()

    def stop4():
        if st4["stop"] is not None:
            st4["stop"].set()
            lbl4.configure(text="正在取消…（当前这一期停下，剩下的不开始）")

    def on_close():  # 跑批时误点关窗口：先问，确认后停在当前这一期，停好再关
        if not st4["running"]:
            remember_window()
            root.destroy()
            return
        if st4["closing"]:
            return
        if ui_box.askyesno(APP_NAME + " - 批量迁移正在运行",
                               "批量迁移正在运行。关掉窗口会停在当前这一期：当前这一期取消（结果不完整，下次扫描显示"
                               "「待运行（上次已取消）」可重跑），剩下的不开始。\n\n确定要停止并关闭吗？", icon="warning"):
            st4["closing"] = True
            stop4()
            lbl4.configure(text="正在停止当前这一期，停好后自动关闭…")

    btn4_start.configure(command=start4)
    btn4_stop.configure(command=stop4)
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(200, poll4)

    # ---- 页签 5：影像处理（D-29，T9）：扫描工点 → 勾选、改坐标系 → 一个接一个空三，出 CGCS2000 XML（可选 DOM/DSM） ----
    tab5, lg5 = paned_tab("影像处理")
    tab5.columnconfigure(1, weight=1)
    cfg5 = load_config()
    v5_root = tk.StringVar(value=cfg5.get("survey_root", ""))
    v5_out = tk.StringVar(value=cfg5.get("survey_out", ""))
    v5_ms = tk.StringVar(value=find_metashape())
    v5_filter = tk.StringVar()
    v5_skip = tk.StringVar(value=cfg5.get("survey_skip", ""))
    v5_domdsm = tk.BooleanVar(value=False)  # 卡面：默认不勾，不记 config
    v5_res = tk.StringVar(value="%g" % SURVEY_RESOLUTION)
    v5_kp = tk.StringVar(value=str(SURVEY_KEYPOINTS))
    v5_tp = tk.StringVar(value=str(SURVEY_TIEPOINTS))
    v5_clip = tk.BooleanVar(value=bool(cfg5.get("survey_clip", True)))   # D-31：自动裁边，默认勾，记 config
    v5_inset = tk.StringVar(value=str(cfg5.get("survey_inset") or "%g" % SURVEY_CLIP_INSET))
    v5_density = tk.StringVar(value=str(cfg5.get("survey_density") or "%g" % SURVEY_CLIP_DENSITY))   # D-32，记 config
    v5_sort = tk.StringVar(value=cfg5.get("survey_sort") if cfg5.get("survey_sort") in SURVEY_SORTS else "scan")
    v5_deliver = tk.BooleanVar(value=bool(cfg5.get("survey_deliver", True)))   # 处理完自动整理交付目录，记 config
    v5_deliver_to = tk.StringVar(value=cfg5.get("survey_deliver_to", ""))       # 空 = <输出目录>_交付
    srs_choices = [survey_srs_label(e) for e in range(SURVEY_EPSG_MIN, SURVEY_EPSG_MAX + 1)]
    v5_unify = tk.StringVar(value=survey_srs_label(4543))
    st5 = {"stop": None, "items": [], "rows": [], "scanned": None, "running": False, "scanning": False, "closing": False,
           "cache": {}}
    q5 = queue.Queue()
    rows5 = (
        ("照片上级目录：", v5_root, lambda: pick_dir(v5_root, "选择照片上级目录（每个直接含照片的文件夹算一个工点，只读）")),
        ("输出目录：", v5_out, lambda: pick_dir(v5_out, "选择输出目录（每个工点的工程、XML、DOM/DSM 和汇总表都写在这里）")),
        ("Metashape 程序：", v5_ms, lambda: pick_file(v5_ms, "选择 Metashape Pro 程序（metashape.exe）",
                                                   [("Metashape", "metashape.exe photoscan.exe"), ("程序", "*.exe")])),
    )
    for r, (label, var, cmd) in enumerate(rows5):
        ctk.CTkLabel(tab5, text=label).grid(row=r, column=0, sticky="w", pady=2)
        ctk.CTkEntry(tab5, textvariable=var).grid(row=r, column=1, sticky="ew", pady=2)
        button(tab5, "浏览…", cmd).grid(row=r, column=2, padx=(6, 0), pady=2)

    of5 = ctk.CTkFrame(tab5, fg_color="transparent")
    of5.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))
    cb5_domdsm = ctk.CTkCheckBox(of5, text="同时出 DOM、DSM（超高质量，很慢）", variable=v5_domdsm)
    cb5_domdsm.grid(row=0, column=0, sticky="w")
    lbl5_res = ctk.CTkLabel(of5, text="分辨率（米，填「自动」＝原始）：")
    lbl5_res.grid(row=0, column=1, sticky="w", padx=(16, 4))
    e5_res = ctk.CTkEntry(of5, textvariable=v5_res, width=64)
    e5_res.grid(row=0, column=2, sticky="w")
    ctk.CTkLabel(of5, text="关键点：").grid(row=0, column=3, sticky="w", padx=(16, 4))
    ctk.CTkEntry(of5, textvariable=v5_kp, width=64).grid(row=0, column=4, sticky="w")
    ctk.CTkLabel(of5, text="连接点：").grid(row=0, column=5, sticky="w", padx=(10, 4))
    ctk.CTkEntry(of5, textvariable=v5_tp, width=64).grid(row=0, column=6, sticky="w")
    cb5_clip = ctk.CTkCheckBox(of5, text="自动裁边（背景透明）", variable=v5_clip)   # D-31
    cb5_clip.grid(row=1, column=0, sticky="w", pady=(4, 0))
    lbl5_inset = ctk.CTkLabel(of5, text="往里收（0～30 米）：")
    lbl5_inset.grid(row=1, column=1, sticky="w", padx=(16, 4), pady=(4, 0))
    e5_inset = ctk.CTkEntry(of5, textvariable=v5_inset, width=64)
    e5_inset.grid(row=1, column=2, sticky="w", pady=(4, 0))
    lbl5_density = ctk.CTkLabel(of5, text="点云密度门槛（10～90 %）：")
    lbl5_density.grid(row=1, column=3, columnspan=2, sticky="w", padx=(16, 4), pady=(4, 0))
    e5_density = ctk.CTkEntry(of5, textvariable=v5_density, width=64)
    e5_density.grid(row=1, column=5, columnspan=2, sticky="w", pady=(4, 0))

    def update_res_state(*_):
        for w in (lbl5_res, e5_res):
            set_enabled(w, v5_domdsm.get())
        set_enabled(cb5_clip, v5_domdsm.get())          # 勾选框本身只看 DOM/DSM：不然一取消勾就再也勾不回来
        set_enabled(cb5_deliver, v5_domdsm.get())       # 只出 XML 时没有 DOM/DSM 可交付
        for w in (lbl5_inset, e5_inset, lbl5_density, e5_density):
            set_enabled(w, v5_domdsm.get() and v5_clip.get())
    v5_domdsm.trace_add("write", update_res_state)
    v5_clip.trace_add("write", update_res_state)

    uf5 = ctk.CTkFrame(tab5, fg_color="transparent")
    uf5.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(4, 0))
    uf5.columnconfigure(4, weight=1)
    ctk.CTkLabel(uf5, text="坐标系全部统一为：").grid(row=0, column=0, sticky="w")
    om5_unify = ctk.CTkOptionMenu(uf5, variable=v5_unify, values=["自动（按工点）"] + srs_choices, width=170)
    om5_unify.grid(row=0, column=1, sticky="w")
    btn5_unify = button(uf5, "统一", width=56)
    btn5_unify.grid(row=0, column=2, sticky="w", padx=(6, 0))
    ctk.CTkLabel(uf5, text="跳过这些工点（逗号分隔）：").grid(row=0, column=3, sticky="w", padx=(16, 4))
    ctk.CTkEntry(uf5, textvariable=v5_skip).grid(row=0, column=4, sticky="ew")
    df5 = ctk.CTkFrame(uf5, fg_color="transparent")
    df5.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(4, 0))
    cb5_deliver = ctk.CTkCheckBox(df5, text="处理完自动整理交付目录", variable=v5_deliver)
    cb5_deliver.pack(side="left")
    ctk.CTkLabel(df5, text="交付目录（空＝<输出目录>_交付）：").pack(side="left", padx=(16, 4))
    btn5_deliver = button(df5, "整理交付目录…", width=120)
    btn5_deliver.pack(side="right")
    button(df5, "浏览…", lambda: pick_dir(v5_deliver_to, "选择交付目录（每个工点一个文件夹，只放 DOM/DSM）")).pack(
        side="right", padx=(6, 6))
    ctk.CTkEntry(df5, textvariable=v5_deliver_to).pack(side="left", fill="x", expand=True)
    update_res_state()
    hint(tab5, "每个直接含照片的文件夹算一个工点，下面的子文件夹（如 正摄）并进来，所有照片都用。本页签不去定位：照片 GPS "
               "换算到工点的 CGCS2000 3 度带（不带带号）作相机参考，按照片平均经度自动选最近的带，可在列表里逐个改，"
               "改过的记住；精度取决于机载 GPS（米级），没用控制点。带定位不到一半的、在子目录里的（如 无正射影像 下面的）"
               "默认不勾。完成/部分对齐的工点算已有结果、跳过；上次失败或已取消的重跑前旧结果目录改名保留。"
               "输出：<输出目录>\\<工点名>\\ 里的 .psx、<工点名>_BlocksExchange.xml（CC 直接导入）、处理报告.txt；"
               "勾了 DOM/DSM 再加 <工点名>DOM.tif/.tfw、<工点名>DSM.tif/.tfw。"
               "勾了 DOM/DSM 时默认自动裁边：只留「DOM 有影像 + 密集点云真有点（不靠插值）+ 点云够密」的地方，"
               "补上细缝、取最大的一片、填掉小洞、再往里收一点。「往里收」是沿边界往里缩多少米（0 不缩、默认 3 米）；"
               "「点云密度门槛」是每 1 米见方里有真实点的 0.1 米小格要占多少（默认 50 %，调低留得多、调高裁得狠，"
               "拉花处点又稀又乱会被裁掉）。DOM 和 DSM 用同一条线、同一个像元网格裁，线外 DOM 透明、DSM 是 NoData。"
               "范围线另存 <工点名>_范围线.kml（WGS84 经纬度）和 <工点名>_范围线.shp（同 DOM 坐标系）；想手动改，"
               "用任何 GIS 软件改了线（可以挖洞、加块），点「按范围线重新裁剪…」只重新导出，几分钟。跟正摄还是斜飞无关。"
               "筛选只影响显示；四个勾选按钮只作用于当前显示的行，「只勾待处理」不勾子目录里的。"
               "点列头「照片数」排序，勾选的工点按列表顺序处理。交付目录每个工点只放 DOM/DSM 四个文件"
               "（复制、核对 SHA-256），写 交付清单.csv。").grid(
        row=5, column=0, columnspan=3, sticky="w", pady=(0, 2))

    tb5 = ctk.CTkFrame(tab5, fg_color="transparent")
    tb5.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(2, 2))
    btn5_scan = button(tb5, "扫描", primary=True)
    btn5_scan.pack(side="left")
    ctk.CTkLabel(tb5, text="筛选：").pack(side="left", padx=(12, 0))
    ctk.CTkEntry(tb5, textvariable=v5_filter, width=120).pack(side="left")
    bulk5 = []
    for t in ("全选", "全不选", "反选", "只勾待处理"):
        b = button(tb5, t, width=56 if len(t) <= 3 else 88)
        b.pack(side="left", padx=(6, 0))
        bulk5.append(b)
    lbl5_count = ctk.CTkLabel(tb5, text="", text_color=hint_color)
    lbl5_count.pack(side="left", padx=(10, 0))
    btn5_reclip = button(tb5, "按范围线重新裁剪…", width=150)   # D-32：给用户留手动裁的余地
    btn5_reclip.pack(side="right")
    btn5_cc = button(tb5, "整理 CC 成果…", width=120)          # T17：CC 的 Production 自动认工点、裁边、进交付目录
    btn5_cc.pack(side="right", padx=(0, 6))

    lst5 = ctk.CTkScrollableFrame(tab5, height=90)  # 同批量迁移：默认矮，往下拖分隔条列表跟着变高
    lst5._scrollbar.configure(height=60)
    lst5.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(2, 4))
    lst5.columnconfigure(2, weight=1)
    tab5.rowconfigure(7, weight=1)
    cols5 = (("", 28, "w"), ("序号", 36, "e"), ("工点", 260, "w"), ("照片数", 64, "e"), ("带定位", 52, "e"),
             ("坐标系", 200, "w"), ("状态", 120, "w"), ("备注", 90, "w"))
    head5 = {}
    for c, (t, w, a) in enumerate(cols5):
        if t:
            head5[t] = ctk.CTkLabel(lst5, text=t, width=w, anchor=a, text_color=hint_color, height=20)
            head5[t].grid(row=0, column=c, sticky="ew", padx=(0, 6))
    sort_next5 = {"scan": "photos-desc", "photos-desc": "photos-asc", "photos-asc": "scan"}

    def sort_heads5():
        how = v5_sort.get()
        head5["照片数"].configure(text={"photos-desc": "照片数 ▼", "photos-asc": "照片数 ▲"}.get(how, "照片数 ⇅"),
                                 text_color=label_color if how != "scan" else hint_color)
        head5["序号"].configure(text_color=label_color if how == "scan" else hint_color)

    def sort5(how=None):
        """按 v5_sort 重排列表（只挪位置，不重建控件）；勾选的工点按列表顺序处理。"""
        if how is not None:
            if st5["running"] or st5["scanning"]:
                return
            v5_sort.set(how)
            cfg = load_config()
            cfg["survey_sort"] = how
            save_config(cfg)
        order = {id(it): k for k, it in enumerate(survey_sort_items([r["item"] for r in st5["rows"]], v5_sort.get()))}
        st5["rows"].sort(key=lambda r: order[id(r["item"])])
        for i, r in enumerate(st5["rows"], 1):
            for c, w in enumerate(r["widgets"]):
                w.grid(row=i, column=c, sticky="ew", padx=(0, 6), pady=(0, 1))
        sort_heads5()
        apply_filter5()

    for t, nxt in (("照片数", lambda: sort_next5[v5_sort.get()]), ("序号", lambda: "scan")):
        head5[t].configure(cursor="hand2")
        head5[t].bind("<Button-1>", lambda ev, nxt=nxt: sort5(nxt()))
    tooltip(head5["照片数"], lambda: "点一下按照片数排序：多→少 → 少→多 → 扫描顺序（勾选的工点按列表顺序处理）")
    status_color5 = {"待处理（": ("#6A1B9A", "#CE93D8"), "完成": ("#2E7D32", "#81C784"), "部分对齐": ("#E65100", "#FFB74D"),
                     "定位不足": ("#E65100", "#FFB74D"), "失败": danger, "已取消": hint_color,
                     "运行中": ("#1565C0", "#64B5F6"), "等待其他 Metashape": ("#1565C0", "#64B5F6")}

    def color_of5(text):
        for k, c in status_color5.items():
            if text.startswith(k):
                return c
        return hint_color if text.startswith("跳过") or text == "已有结果" else label_color

    def visible5(row):
        f = v5_filter.get().strip().lower()
        return f in row["item"]["rel"].lower()

    def update_count5(*_):
        rs = st5["rows"]
        sel = [r for r in rs if r["var"].get()]
        lbl5_count.configure(text="已勾 %d 个（要处理 %d）；显示 %d/%d" % (
            len(sel), sum(survey_runnable(r["item"]) for r in sel), sum(visible5(r) for r in rs), len(rs)))

    def apply_filter5(*_):
        for r in st5["rows"]:
            for w in r["widgets"]:
                if visible5(r):
                    w.grid()
                else:
                    w.grid_remove()
        update_count5()

    v5_filter.trace_add("write", apply_filter5)

    def bulk5_do(kind):
        for r in st5["rows"]:
            if visible5(r):
                it = r["item"]
                r["var"].set({"全选": True, "全不选": False, "反选": not r["var"].get(),
                              "只勾待处理": it["status"].startswith(SURVEY_TODO) and not it["nested"]}[kind])
        update_count5()

    for b in bulk5:
        b.configure(command=lambda t=b.cget("text"): bulk5_do(t))

    def set_row_status5(row, text):
        row["status"].configure(text=text, text_color=color_of5(text))

    def srs_text(it):
        return ("自动 " + survey_srs_label(it["auto_epsg"]) if it["auto_epsg"] else "自动（无定位）") \
            if it["epsg_src"] == "自动" else survey_srs_label(it["epsg"])

    def save_manual5(name, epsg):
        cfg = load_config()
        m = dict(cfg.get("survey_epsg") or {})
        if epsg:
            m[name] = int(epsg)
        else:
            m.pop(name, None)
        cfg["survey_epsg"] = m
        save_config(cfg)

    def set_srs5(row, choice, save=True):
        """列表里改了某个工点的坐标系：选「自动…」回到自动，选具体的带记为手动（按工点名记进 config）。"""
        it = row["item"]
        if choice.startswith("自动"):
            it["epsg"], it["epsg_src"] = it["auto_epsg"], "自动"
            if save:
                save_manual5(it["name"], None)
        else:
            it["epsg"], it["epsg_src"] = parse_survey_epsg(choice), "手动"
            if save:
                save_manual5(it["name"], it["epsg"])
        row["srs_var"].set(srs_text(it))
        row["srs"].configure(text_color=("#1565C0", "#64B5F6") if it["epsg_src"] == "手动" else label_color)

    def unify5():
        if not st5["rows"]:
            ui_box.showwarning(APP_NAME, "请先点「扫描」")
            return
        choice = v5_unify.get()
        cfg = load_config()
        m = dict(cfg.get("survey_epsg") or {})
        for r in st5["rows"]:
            set_srs5(r, choice, save=False)
            if choice.startswith("自动"):
                m.pop(r["item"]["name"], None)
            else:
                m[r["item"]["name"]] = r["item"]["epsg"]
        cfg["survey_epsg"] = m
        save_config(cfg)
        q5.put(("log", "坐标系：%d 个工点全部%s" % (len(st5["rows"]), "改回自动（按工点）" if choice.startswith("自动")
                                                  else "统一为 %s（记为手动）" % choice)))

    btn5_unify.configure(command=unify5)

    def note_of(it):
        return "、".join(x for x, on in (("靠近分带线", it["near"]), ("子目录", it["nested"])) if on)

    def make_row5(i, it):
        var = tk.BooleanVar(value=it["default"])
        row = {"item": it, "var": var, "srs_var": tk.StringVar()}
        ws = [ctk.CTkCheckBox(lst5, text="", width=28, checkbox_width=20, checkbox_height=20, variable=var,
                              command=update_count5)]
        for c, text in ((1, str(it["idx"])), (2, it["rel"]), (3, str(it["n"])), (4, str(it["n_gps"]))):
            t, w, a = cols5[c]
            ws.append(ctk.CTkLabel(lst5, text=text, width=w, anchor=a, height=22))
        om = ctk.CTkOptionMenu(lst5, variable=row["srs_var"], width=cols5[5][1], height=22, dynamic_resizing=False,
                               values=[("自动 " + survey_srs_label(it["auto_epsg"])) if it["auto_epsg"] else "自动（无定位）"]
                               + srs_choices, command=lambda choice, row=row: set_srs5(row, choice))
        ws.append(om)
        ws.append(ctk.CTkLabel(lst5, text="", width=cols5[6][1], anchor="w", height=22))
        ws.append(ctk.CTkLabel(lst5, text=note_of(it), width=cols5[7][1], anchor="w", height=22, text_color=hint_color))
        for c, w in enumerate(ws):
            w.grid(row=i, column=c, sticky="ew", padx=(0, 6), pady=(0, 1))
        tooltip(ws[2], lambda it=it: it["dir"])
        row.update(widgets=ws, status=ws[6], srs=om)
        row["srs_var"].set(srs_text(it))
        om.configure(text_color=("#1565C0", "#64B5F6") if it["epsg_src"] == "手动" else label_color)
        set_row_status5(row, it["status"])
        st5["rows"].append(row)

    def fill_list5(items, done):
        """换列表内容：几百个 CTk 控件一次建完界面会卡一两秒，所以每次只删/建 80 毫秒，分批用 after 接着做；做完调 done()。"""
        old, todo = st5["rows"], list(enumerate(items, 1))
        st5["rows"] = []

        def step():
            t0 = time.time()
            while old and time.time() - t0 < 0.08:
                for w in old.pop()["widgets"]:
                    w.destroy()
            while not old and todo and time.time() - t0 < 0.08:
                make_row5(*todo.pop(0))
            if old or todo:
                root.after(1, step)
                return
            sort5()
            done()
        step()

    def scan_key5():
        return (v5_root.get().strip(), os.path.abspath(v5_out.get().strip()) if v5_out.get().strip() else "",
                tuple(_split_names(v5_skip.get())))

    def save5():
        cfg = load_config()
        cfg.update(survey_root=v5_root.get().strip(), survey_out=v5_out.get().strip(),
                   survey_skip=",".join(_split_names(v5_skip.get())), survey_clip=bool(v5_clip.get()),
                   survey_inset=v5_inset.get().strip(), survey_density=v5_density.get().strip(),
                   survey_deliver=bool(v5_deliver.get()), survey_deliver_to=v5_deliver_to.get().strip())
        if v5_ms.get().strip():
            cfg["metashape"] = v5_ms.get().strip()
        save_config(cfg)

    def set_busy5(busy):
        """扫描或运行时：扫描、开始、统一、各行坐标系不可点，取消可点。"""
        set_btn(btn5_scan, not busy)
        set_btn(btn5_start, not busy)
        set_btn(btn5_unify, not busy)
        set_btn(btn5_reclip, not busy)
        set_btn(btn5_deliver, not busy)
        set_btn(btn5_cc, not busy)
        set_btn(btn5_stop, busy)
        for r in st5["rows"]:
            r["srs"].configure(state="disabled" if busy else "normal")

    def scan5():
        root_dir, out = v5_root.get().strip(), v5_out.get().strip()
        if not root_dir or not out:
            ui_box.showwarning(APP_NAME, "请选择照片上级目录和输出目录")
            return
        if not os.path.isdir(root_dir):
            ui_box.showwarning(APP_NAME, "照片上级目录不存在：%s" % root_dir)
            return
        save5()
        key, skip = scan_key5(), _split_names(v5_skip.get())
        manual = dict(load_config().get("survey_epsg") or {})
        stop = threading.Event()
        st5["stop"], st5["scanning"] = stop, True
        set_busy5(True)
        pb5.set(0)
        lbl5.configure(text="扫描中…")
        q5.put(("log", "扫描 %s …（第一次要读每张照片开头的定位，网络盘上几千张约一两分钟）" % root_dir))

        def work():
            try:
                items = survey_scan(root_dir, os.path.abspath(out), skip=skip, manual=manual, cache=st5["cache"],
                                    progress=lambda i, n, s: q5.put(("prog", (i / float(n) if n else 0,
                                                                              "扫描 %d/%d 张 · %s" % (i, n, s)))),
                                    stop_event=stop)
                q5.put(("scanned", (items, key)))
            except AlignCancelled:
                q5.put(("scanned", None))
            except Exception as ex:
                q5.put(("scanned", ex))

        threading.Thread(target=work, daemon=True).start()

    btn5_scan.configure(command=scan5)

    bar5 = ctk.CTkFrame(tab5, fg_color="transparent")
    bar5.grid(row=8, column=0, columnspan=3, sticky="ew")
    bar5.columnconfigure(0, weight=1)
    pb5 = ctk.CTkProgressBar(bar5, mode="determinate")
    pb5.set(0)
    pb5.grid(row=0, column=0, sticky="ew")
    lbl5 = ctk.CTkLabel(bar5, text="", width=260)
    lbl5.grid(row=0, column=1, padx=6)
    btn5_start = button(bar5, "开始", primary=True)
    btn5_start.grid(row=0, column=2)
    btn5_stop = button(bar5, "取消", role="stop", state="disabled")
    btn5_stop.grid(row=0, column=3, padx=(6, 0))

    def scanned5(val):
        pb5.set(0)
        if val is None or isinstance(val, Exception):
            st5["scanning"] = False
            set_busy5(False)
            lbl5.configure(text="扫描已取消" if val is None else "")
            log_add(lg5, "扫描已取消" if val is None else "扫描出错：%s" % val)
            if val is not None:
                ui_box.showwarning(APP_NAME, str(val))
            return
        items, key = val
        lbl5.configure(text="正在列出 %d 个工点…" % len(items))
        fill_list5(items, lambda: listed5(items, key))

    def listed5(items, key):
        st5["items"], st5["scanned"] = items, key
        st5["scanning"] = False
        set_busy5(False)
        lbl5.configure(text="扫描完成：%d 个工点" % len(items))
        cnt = {}
        for it in items:
            cnt[it["status"]] = cnt.get(it["status"], 0) + 1
        log_add(lg5, "扫描完成：共 %d 个工点（在子目录里、默认不勾的 %d 个）；%s" % (
            len(items), sum(it["nested"] for it in items), "，".join("%s %d" % kv for kv in cnt.items())))
        zones = {}
        for it in items:
            if it["epsg"]:
                zones.setdefault(survey_srs_label(it["epsg"]), []).append(it["name"])
        log_add(lg5, "坐标系：%s" % "；".join("%s %d 个" % (k, len(v)) for k, v in sorted(zones.items())))
        near = [it["name"] for it in items if it["near"]]
        if near:
            log_add(lg5, "注意：这些工点的照片平均经度离分带线不到 %g°，自动选的带可能和以往成果不同，请核对（可在列表里改，"
                         "或用「坐标系全部统一为」）：%s" % (SURVEY_NEAR_ZONE, "、".join(near)))
        nogps = [it["name"] for it in items if not it["epsg"]]
        if nogps:
            log_add(lg5, "注意：这些工点没有定位、选不了带，要处理就先手动选坐标系：%s" % "、".join(nogps))
        again = [it["name"] for it in items if it["status"].startswith(SURVEY_TODO + "（")]
        if again:
            log_add(lg5, "这些工点上次失败或已取消，重跑前会把旧结果目录改名保留：%s" % "、".join(again))

    def poll5():
        try:
            for _ in range(500):  # 一次最多处理这么多条，别让界面卡住
                kind, val = q5.get_nowait()
                if kind == "log":
                    log_add(lg5, val)
                    log_refresh_open(lg5)
                elif kind == "prog":
                    pb5.set(int(val[0] * 1000) / 1000)
                    lbl5.configure(text=val[1][:44])
                elif kind == "scanned":
                    scanned5(val)
                elif kind == "item":
                    it, text = val
                    for r in st5["rows"]:
                        if r["item"] is it:
                            set_row_status5(r, text)
                    update_count5()
                elif kind == "deliver_done":
                    set_busy5(False)
                    st5["running"] = False
                    if st5["closing"]:
                        if not st4["running"]:
                            remember_window()
                            root.destroy()
                        return
                    if isinstance(val, Exception):
                        lbl5.configure(text="失败")
                        log_add(lg5, "出错：%s" % val)
                        ui_box.showerror(APP_NAME, str(val))
                        continue
                    res, logf = val
                    lbl5.configure(text="已取消" if res["cancelled"] else "完成")
                    (ui_box.showwarning if res["exit"] else ui_box.showinfo)(
                        APP_NAME + " - 整理交付目录", deliver_note5(res) + "\n\n清单：%s\n日志：%s" % (res["csv"], logf))
                elif kind == "cc_done":
                    set_busy5(False)
                    st5["running"] = False
                    if st5["closing"]:
                        if not st4["running"]:
                            remember_window()
                            root.destroy()
                        return
                    if isinstance(val, Exception):
                        lbl5.configure(text="失败")
                        log_add(lg5, "出错：%s" % val)
                        ui_box.showerror(APP_NAME, str(val))
                        continue
                    res, logf = val
                    lbl5.configure(text="已取消" if res["cancelled"] else "完成")
                    body = ["%s（%s）：%s%s" % (r["key"], r["kind"] or "-", r["result"] +
                                               (" → " + r["site"] if r["result"] == "完成" else ""),
                                               "\n  " + r["reason"] if r["reason"] else "") for r in res["rows"]]
                    body += ["", "交付目录：%s" % res["dest"], "清单：%s" % res["csv"], "完整日志：%s" % logf]
                    (ui_box.showwarning if res["exit"] else ui_box.showinfo)(
                        APP_NAME + " - 整理 CC 成果", ("有没整理成的成果（见下）。\n\n" if res["exit"] else
                                                 "CC 成果已整理进交付目录（用时 %.1f 分钟）。\n\n" % (res["secs"] / 60.0)) +
                        "\n".join(body))
                elif kind == "reclip_done":
                    set_busy5(False)
                    st5["running"] = False
                    if st5["closing"]:
                        if not st4["running"]:
                            remember_window()
                            root.destroy()
                        return
                    if isinstance(val, Exception):
                        lbl5.configure(text="失败")
                        log_add(lg5, "出错：%s" % val)
                        ui_box.showerror(APP_NAME, str(val))
                        continue
                    res, logf = val
                    lbl5.configure(text="已取消" if res["cancelled"] else "完成")
                    body = ["%s：%s（%.1f 分钟）%s" % (r["name"], r["status"], (r["secs"] or 0) / 60.0,
                                                    "\n  " + r["reason"] if r["reason"] else "") for r in res["rows"]]
                    if res.get("deliver"):
                        body += ["", deliver_note5(res["deliver"])]
                    body += ["", "完整日志：%s" % logf]
                    (ui_box.showwarning if res["exit"] else ui_box.showinfo)(
                        APP_NAME + " - 按范围线重新裁剪", ("有没做成的工点（原来的 DOM/DSM 没动）。\n\n" if res["exit"] else
                                                   "只重新导出了 DOM/DSM（没重建）；旧范围线改名保留。\n\n") + "\n".join(body))
                elif kind == "done":
                    set_busy5(False)
                    st5["scanned"] = None  # 状态都变了，再跑要先重新扫描
                    st5["running"] = False
                    if st5["closing"]:  # 用户点了关窗口：当前工点已停好，现在关
                        if not st4["running"]:
                            remember_window()
                            root.destroy()
                        return
                    if isinstance(val, Exception):
                        lbl5.configure(text="失败")
                        log_add(lg5, "出错：%s" % val)
                        ui_box.showerror(APP_NAME, str(val))
                        continue
                    res, logf, dres = val
                    body = ["%s：%d 个" % kv for kv in res["counts"].items()]
                    if dres:
                        body.append(deliver_note5(dres))
                    for st_name in ("失败", "部分对齐"):
                        names = [r["工点"] for r in res["rows"] if r["状态"] == st_name]
                        if names:
                            body.append("%s：%s" % (st_name, "、".join(names)))
                    body += ["", "汇总表：%s" % res["summary"], "完整日志：%s" % logf]
                    if res["cancelled"]:
                        lbl5.configure(text="已取消")
                        last = res["rows"][-1] if res["rows"] else None
                        if last and last["状态"] == "已取消":
                            body.append("\n被取消的「%s」结果不完整，不要使用；重新扫描会显示「待处理（上次已取消）」，"
                                        "重跑时旧结果目录改名保留。" % last["工点"])
                        ui_box.showwarning(APP_NAME + " - 已取消",
                                           "已取消：已完成的工点结果都保留，剩下的没有开始。\n\n" + "\n".join(body))
                    else:
                        lbl5.configure(text="完成")
                        bad = res["exit"] != 0 or bool(dres and (dres.get("error") or dres.get("exit")))
                        (ui_box.showwarning if bad else ui_box.showinfo)(
                            APP_NAME + " - 影像处理完成", ("有失败的工点，详见汇总表和各工点的处理报告.txt。\n\n"
                                                    if bad else "") + "\n".join(body))
        except queue.Empty:
            pass
        root.after(200, poll5)

    def start5():
        if st4["running"]:
            ui_box.showwarning(APP_NAME, "「批量迁移」正在运行，等它跑完再开始（两个都要用 Metashape）")
            return
        if not st5["items"] or st5["scanned"] is None:
            ui_box.showwarning(APP_NAME, "请先点「扫描」")
            return
        if scan_key5() != st5["scanned"]:
            ui_box.showwarning(APP_NAME, "目录或跳过名单在扫描后改过了，请先重新扫描")
            return
        ms = v5_ms.get().strip()
        err = check_metashape(ms)
        if err:
            ui_box.showwarning(APP_NAME, err)
            return
        try:
            kp, tp = int(v5_kp.get().strip()), int(v5_tp.get().strip())
            if kp <= 0 or tp < 0:
                raise ValueError
        except ValueError:
            ui_box.showwarning(APP_NAME, "关键点上限要填大于 0 的整数，连接点上限要填不小于 0 的整数")
            return
        dom_dsm = v5_domdsm.get()
        resolution = SURVEY_RESOLUTION
        clip, clip_file, clip_inset, clip_density = SURVEY_CLIP_NONE, None, SURVEY_CLIP_INSET, SURVEY_CLIP_DENSITY
        if dom_dsm:
            try:
                resolution = parse_survey_resolution(v5_res.get())
            except ValueError as ex:
                ui_box.showwarning(APP_NAME, str(ex))
                return
            if v5_clip.get():
                try:
                    clip_inset = survey_clip_inset(v5_inset.get())
                    clip_density = survey_clip_density(v5_density.get())
                except ValueError as ex:
                    ui_box.showwarning(APP_NAME, str(ex))
                    return
                clip = SURVEY_CLIP_AUTO
        chosen = [r["item"] for r in st5["rows"] if r["var"].get()]
        todo = [it for it in chosen if survey_runnable(it)]
        if not todo:
            ui_box.showwarning(APP_NAME, "勾选的工点里没有要处理的（待处理 / 定位不足）")
            return
        nosrs = [it["name"] for it in todo if not it["epsg"]]
        if nosrs:
            ui_box.showwarning(APP_NAME, "这些工点没有坐标系（照片不带定位），请先在列表里给它们选一个 CGCS2000 3 度带，"
                                         "或者不勾：\n" + "\n".join(nosrs))
            return
        out = os.path.abspath(v5_out.get().strip())
        deliver = dom_dsm and v5_deliver.get()
        dest = None
        if deliver:
            try:
                dest = deliver_dest5(out)
            except ValueError as ex:
                ui_box.showwarning(APP_NAME, str(ex))
                return
        save5()
        log_clear(lg5)
        pb5.set(0)
        lbl5.configure(text="")
        set_busy5(True)
        stop = threading.Event()
        st5["stop"] = stop
        st5["running"] = True
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        logf = Path(out) / ("影像处理日志_%s.txt" % stamp)
        log_set_file(lg5, logf)

        def work():
            try:
                Path(out).mkdir(parents=True, exist_ok=True)
                with open(logf, "a", encoding="utf-8") as flog:
                    def log(s):
                        flog.write(s + "\n")
                        flog.flush()
                        q5.put(("log", s))

                    if v5_sort.get() != "scan":
                        log("处理顺序：按列表（%s）" % {"photos-desc": "照片数从多到少",
                                                    "photos-asc": "照片数从少到多"}[v5_sort.get()])
                    for it in chosen:
                        if survey_runnable(it):
                            log("%s：%s（%s）" % (it["name"], survey_srs_label(it["epsg"]), it["epsg_src"]))
                    res = run_survey_batch(chosen, out, ms, dom_dsm=dom_dsm, resolution=resolution, keypoints=kp,
                                           tiepoints=tp, log=log, progress=lambda f, s: q5.put(("prog", (f, s))),
                                           on_item=lambda it, phase, text: q5.put(("item", (it, text))), stop_event=stop,
                                           clip=clip, clip_inset=clip_inset, clip_file=clip_file,
                                           clip_density=clip_density,
                                           summary=Path(out) / ("影像处理汇总_%s.csv" % stamp))
                    dres = None
                    if deliver and not res["cancelled"]:
                        log("")
                        try:
                            dres = survey_deliver(out, dest, sites=[it["name"] for it in chosen], log=log,
                                                  progress=lambda f, s: q5.put(("prog", (f, s))), stop_event=stop)
                        except (ValueError, OSError) as ex:
                            log("整理交付目录没做成：%s" % ex)
                            dres = {"error": str(ex)}
                q5.put(("done", (res, str(logf), dres)))
            except Exception as ex:
                q5.put(("done", ex))

        threading.Thread(target=work, daemon=True).start()

    def stop5():
        if st5["stop"] is not None:
            st5["stop"].set()
            lbl5.configure(text="正在取消…" if st5["scanning"] else "正在取消…（当前工点停下，剩下的不开始）")

    def on_close5():  # 影像处理在跑时误点关窗口：先问，确认后停在当前工点，停好再关；其余照旧（批量迁移的确认）
        if not st5["running"]:
            on_close()
            return
        if st5["closing"]:
            return
        if ui_box.askyesno(APP_NAME + " - 影像处理正在运行",
                           "影像处理正在运行。关掉窗口会停在当前工点：当前工点取消（结果不完整，下次扫描显示"
                           "「待处理（上次已取消）」可重跑），剩下的不开始。\n\n确定要停止并关闭吗？", icon="warning"):
            st5["closing"] = True
            stop5()
            if st4["running"]:
                st4["closing"] = True
                stop4()
            lbl5.configure(text="正在停止当前工点，停好后自动关闭…")

    def reclip5():
        """「按范围线重新裁剪…」：选输出目录里已完成的工点 + 范围线来源，只重新导出 DOM/DSM（不碰空三和建模）。"""
        if st4["running"]:
            ui_box.showwarning(APP_NAME, "「批量迁移」正在运行，等它跑完再重新裁剪（两个都要用 Metashape）")
            return
        out = v5_out.get().strip()
        if not out or not os.path.isdir(out):
            ui_box.showwarning(APP_NAME, "请先在「输出目录」里选处理结果所在的目录（每个工点一个子目录）")
            return
        sites = survey_reclip_sites(out)
        if not sites:
            ui_box.showwarning(APP_NAME, "输出目录里没有能重新裁剪的工点（要勾了 DOM/DSM、已完成的：有 .psx、"
                                         "<工点名>DOM.tif、<工点名>DSM.tif 和 处理报告.txt）：\n%s" % out)
            return
        dlg = ctk.CTkToplevel(root)
        dlg.title(APP_NAME + " - 按范围线重新裁剪")
        dlg.transient(root)
        res = {"go": False}
        v_src = tk.StringVar(value="dir")
        v_file = tk.StringVar()
        v_ins = tk.StringVar(value=v5_inset.get().strip() or "%g" % SURVEY_CLIP_INSET)
        v_den = tk.StringVar(value=v5_density.get().strip() or "%g" % SURVEY_CLIP_DENSITY)
        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(14, 6))
        lbl = ctk.CTkLabel(body, text="", text_color=hint_color, justify="left", anchor="w")
        set_wrapped(lbl, "只重新导出 DOM/DSM，不碰空三和建模（一个工点几分钟）。先导到临时文件，检查通过才替换；"
                         "旧的范围线改名保留成 <工点名>_范围线_上次_<时间>.kml/.shp，处理报告的裁边段改写。"
                         "自己画的线可以有多个多边形（都保留）和内环（挖掉那一块），原样裁、不再往里收。", int(560 * win_scale))
        lbl.pack(anchor="w")
        ctk.CTkLabel(body, text="选工点（%s）：" % out).pack(anchor="w", pady=(8, 2))
        lst = ctk.CTkScrollableFrame(body, height=150, width=int(560 * win_scale))
        lst.pack(fill="both", expand=True)
        rows = []
        for n, d in sites:
            edited = survey_user_range_line(d, n, Path(d) / SURVEY_REPORT)
            v = tk.BooleanVar(value=False)
            ctk.CTkCheckBox(lst, text=n + ("（范围线改过）" if edited else ""), variable=v, checkbox_width=20,
                            checkbox_height=20).pack(anchor="w", pady=1)
            rows.append((v, d))
        ctk.CTkLabel(body, text="范围线来源：").pack(anchor="w", pady=(8, 2))
        ctk.CTkRadioButton(body, text="用工点目录里的 <工点名>_范围线.kml / .shp（用 GIS 软件改过的；两个都在用较新的）",
                           variable=v_src, value="dir").pack(anchor="w", pady=1)
        fr = ctk.CTkFrame(body, fg_color="transparent")
        fr.pack(fill="x", pady=1)
        ctk.CTkRadioButton(fr, text="用这个文件（KML 或 SHP）：", variable=v_src, value="file").pack(side="left")
        e_file = ctk.CTkEntry(fr, textvariable=v_file)
        e_file.pack(side="left", fill="x", expand=True, padx=(4, 0))
        button(fr, "浏览…", lambda: (pick_file(v_file, "选择范围线（KML：WGS84 经纬度；SHP：按 .prj，没有 .prj 当作与 DOM "
                                                    "同坐标系）", [("范围线", "*.kml *.shp")]), v_src.set("file"))).pack(
            side="left", padx=(6, 0))
        fa = ctk.CTkFrame(body, fg_color="transparent")
        fa.pack(fill="x", pady=1)
        ctk.CTkRadioButton(fa, text="不换线，按这两个参数重新自动算：", variable=v_src, value="auto").pack(side="left")
        ctk.CTkLabel(fa, text="往里收（米）").pack(side="left", padx=(4, 4))
        ctk.CTkEntry(fa, textvariable=v_ins, width=56).pack(side="left")
        ctk.CTkLabel(fa, text="点云密度门槛（%）").pack(side="left", padx=(10, 4))
        ctk.CTkEntry(fa, textvariable=v_den, width=56).pack(side="left")
        bar = ctk.CTkFrame(dlg, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=(4, 14))

        def go():
            chosen = [d for v, d in rows if v.get()]
            if not chosen:
                ui_box.showwarning(APP_NAME, "请至少勾一个工点")
                return
            try:
                if v_src.get() == "file":
                    kind, f = parse_survey_clip(v_file.get())
                    if kind != "file":
                        raise ValueError("请选一个 .kml 或 .shp 范围线文件")
                    res["file"] = f
                res["inset"] = survey_clip_inset(v_ins.get())
                res["density"] = survey_clip_density(v_den.get())
            except ValueError as ex:
                ui_box.showwarning(APP_NAME, str(ex))
                return
            if v_src.get() == "dir":
                nol = [Path(d).name for d in chosen if not survey_site_range_line(d, Path(d).name)]
                if nol:
                    ui_box.showwarning(APP_NAME, "这些工点目录里没有范围线文件：\n" + "\n".join(nol))
                    return
            res.update(go=True, sites=chosen, src=v_src.get())
            dlg.destroy()

        ctk.CTkButton(bar, text="关闭", width=88, command=dlg.destroy, **secondary).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bar, text="开始重新裁剪", width=120, command=go).pack(side="right")
        dlg.bind("<Escape>", lambda ev: dlg.destroy())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.ui_reclip = {"rows": rows, "src": v_src, "file": v_file, "inset": v_ins, "density": v_den, "go": go}
        root.ui_reclip_dlg = dlg                                 # 给 scripts/test_gui.py 看
        show_modal(dlg)
        if not res.get("go"):
            return
        ms = v5_ms.get().strip()
        err = check_metashape(ms)
        if err:
            ui_box.showwarning(APP_NAME, err)
            return
        try:
            dest = deliver_dest5(out) if v5_deliver.get() else None
        except ValueError:
            dest = None
        save5()
        log_clear(lg5)
        pb5.set(0)
        lbl5.configure(text="")
        set_busy5(True)
        stop = threading.Event()
        st5["stop"], st5["running"] = stop, True
        logf = Path(out) / ("重新裁剪日志_%s.txt" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        log_set_file(lg5, logf)

        def work():
            try:
                with open(logf, "a", encoding="utf-8") as flog:
                    def log(s):
                        flog.write(s + "\n")
                        flog.flush()
                        q5.put(("log", s))
                    r = run_survey_clip(res["sites"], ms, res["src"], res.get("file"), res["inset"], res["density"],
                                        log=log, progress=lambda f, s: q5.put(("prog", (f, s))), stop_event=stop)
                    names = [x["name"] for x in r["rows"] if x["status"] == "完成"]
                    if dest and names and (Path(dest) / SURVEY_DELIVER_CSV).is_file() and not r["cancelled"]:
                        log("")
                        log("交付目录里已经有清单，把重新裁剪过的工点也更新过去：")
                        try:
                            r["deliver"] = survey_deliver(out, dest, sites=names, log=log, stop_event=stop,
                                                          progress=lambda f, s: q5.put(("prog", (f, s))))
                        except (ValueError, OSError) as ex:
                            log("整理交付目录没做成：%s" % ex)
                            r["deliver"] = {"error": str(ex)}
                q5.put(("reclip_done", (r, str(logf))))
            except Exception as ex:
                q5.put(("reclip_done", ex))

        threading.Thread(target=work, daemon=True).start()

    def deliver_dest5(out):
        """交付目录：框里填了用它，空就是 <输出目录>_交付；不能放在照片上级目录里（原片只读）。"""
        dest = Path(os.path.abspath(v5_deliver_to.get().strip())) if v5_deliver_to.get().strip() \
            else survey_deliver_default(out)
        src = v5_root.get().strip()
        if src:
            r = Path(os.path.abspath(src))
            if dest == r or r in dest.parents:
                raise ValueError("交付目录不能放在照片上级目录里面（原片目录只读）：%s" % dest)
        o = Path(os.path.abspath(out))
        if dest == o or o in dest.parents or dest in o.parents:
            raise ValueError("交付目录不能是输出目录本身，也不能在它里面或包含它：%s" % dest)
        return dest

    def deliver_note5(d):
        if d.get("error"):
            return "整理交付目录没做成：%s" % d["error"]
        lines = ["交付目录：%s（%d 个工点，这次复制 %d 个文件、已是最新 %d 个，清单共 %.1f MB）" % (
            d["dest"], len(d["sites"]), d["copied"], d["same"], d["bytes"] / 1048576.0)]
        if d["failed"]:
            lines.append("没整理成的：%s" % "、".join("%s（%s）" % x for x in d["failed"]))
        if d["skipped"]:
            lines.append("没有 DOM/DSM、没放进去的：%s" % "、".join(n for n, _ in d["skipped"]))
        if d["cancelled"]:
            lines.append("整理到一半取消了，剩下的没放进去")
        return "\n".join(lines)

    def deliver5():
        """「整理交付目录…」：把输出目录里完成且有 DOM/DSM 的工点复制到交付目录（只放 4 个文件 + 交付清单.csv）。"""
        out = v5_out.get().strip()
        if not out or not os.path.isdir(out):
            ui_box.showwarning(APP_NAME, "请先在「输出目录」里选处理结果所在的目录（每个工点一个子目录）")
            return
        try:
            dest = deliver_dest5(out)
        except ValueError as ex:
            ui_box.showwarning(APP_NAME, str(ex))
            return
        ok, skipped = survey_deliver_sites(out)
        if not ok:
            ui_box.showwarning(APP_NAME, "输出目录里没有能交付的工点（要完成/部分对齐、有 <工点名>DOM.tif/.tfw 和 "
                                         "<工点名>DSM.tif/.tfw）：\n%s" % out)
            return
        size = sum((Path(d) / (n + f)).stat().st_size for n, d, _ in ok for f in SURVEY_DELIVER_FILES)
        msg = ("把 %d 个工点（共 %.1f MB）整理到：\n%s\n\n每个工点一个文件夹，只放 <工点名>DOM.tif/.tfw、<工点名>DSM.tif/.tfw；"
               "是复制，处理结果原样不动；每个文件核对 SHA-256，根目录写 交付清单.csv。已经在里面、没变过的文件不重复复制；"
               "交付目录里别的文件不动。" % (len(ok), size / 1048576.0, dest))
        if skipped:
            msg += "\n\n没有 DOM/DSM、不放进去的 %d 个：%s" % (len(skipped), "、".join(n for n, _ in skipped))
        if not ui_box.askyesno(APP_NAME + " - 整理交付目录", msg + "\n\n继续吗？"):
            return
        save5()
        log_clear(lg5)
        pb5.set(0)
        lbl5.configure(text="")
        set_busy5(True)
        stop = threading.Event()
        st5["stop"], st5["running"] = stop, True
        logf = Path(out) / ("整理交付日志_%s.txt" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        log_set_file(lg5, logf)

        def work():
            try:
                with open(logf, "a", encoding="utf-8") as flog:
                    def log(s):
                        flog.write(s + "\n")
                        flog.flush()
                        q5.put(("log", s))
                    r = survey_deliver(out, dest, log=log, progress=lambda f, s: q5.put(("prog", (f, s))),
                                       stop_event=stop)
                q5.put(("deliver_done", (r, str(logf))))
            except Exception as ex:
                q5.put(("deliver_done", ex))

        threading.Thread(target=work, daemon=True).start()

    def cc5():
        """「整理 CC 成果…」（T17）：选 CC 工程 / Productions 目录 → 扫描，按外框和坐标系认工点（认不准的下拉选）→
        裁边（可关）→ 连同 3SM 整理进交付目录。"""
        if st4["running"]:
            ui_box.showwarning(APP_NAME, "「批量迁移」正在运行，等它跑完再整理（可能要用 Metashape 算点云判据）")
            return
        out = v5_out.get().strip()
        if not out or not os.path.isdir(out):
            ui_box.showwarning(APP_NAME, "请先在「输出目录」里选影像处理的输出目录（候选工点从这里找：完成 / 部分对齐、"
                                         "有 _BlocksExchange.xml 的）")
            return
        try:
            dest = deliver_dest5(out)
        except ValueError as ex:
            ui_box.showwarning(APP_NAME, str(ex))
            return
        cfg = load_config()
        dlg = ctk.CTkToplevel(root)
        dlg.title(APP_NAME + " - 整理 CC 成果")
        dlg.transient(root)
        res = {"go": False, "plan": None}
        qd = queue.Queue()
        v_clip = tk.BooleanVar(value=bool(cfg.get("survey_cc_clip", True)))
        v_ins = tk.StringVar(value=v5_inset.get().strip() or "%g" % SURVEY_CLIP_INSET)
        v_den = tk.StringVar(value=v5_density.get().strip() or "%g" % SURVEY_CLIP_DENSITY)
        pick_text = "（请选工点）"
        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(14, 6))
        ctk.CTkLabel(body, text="CC 成果整理（交付目录：%s）：" % dest, anchor="w").pack(anchor="w")
        ctk.CTkLabel(body, text="CC 目录：").pack(anchor="w", pady=(8, 2))
        fd = ctk.CTkFrame(body, fg_color="transparent")
        fd.pack(fill="x")
        tb_dirs = ctk.CTkTextbox(fd, height=64)
        tb_dirs.pack(side="left", fill="x", expand=True)
        tb_dirs.insert("1.0", "\n".join(cfg.get("survey_cc_dirs") or []))
        fb = ctk.CTkFrame(fd, fg_color="transparent")
        fb.pack(side="left", padx=(6, 0), anchor="n")

        def add_dir():
            d = filedialog.askdirectory(parent=dlg, title="选择 CC 工程目录（或 Productions / Production 目录，只读）")
            if d:
                cur = tb_dirs.get("1.0", "end").strip()
                tb_dirs.delete("1.0", "end")
                tb_dirs.insert("1.0", (cur + "\n" if cur else "") + os.path.normpath(d))

        button(fb, "添加目录…", add_dir, width=96).pack(pady=(0, 4))
        b_scan = button(fb, "扫描", width=96, primary=True)
        b_scan.pack()
        lbl_st = ctk.CTkLabel(body, text="", text_color=hint_color, anchor="w")
        lbl_st.pack(anchor="w", pady=(6, 0))
        lst = ctk.CTkScrollableFrame(body, height=170, width=int(860 * win_scale))
        lst.pack(fill="both", expand=True, pady=(2, 0))
        cols = (("成果", 150), ("类型", 64), ("外框", 230), ("工点", 300), ("得分", 48), ("状态", 220))
        for c, (t, w) in enumerate(cols):
            ctk.CTkLabel(lst, text=t, width=w, anchor="w", text_color=hint_color, height=20).grid(
                row=0, column=c, sticky="w", padx=(0, 6))
        rows_ui = []
        fo = ctk.CTkFrame(body, fg_color="transparent")
        fo.pack(fill="x", pady=(8, 0))
        ctk.CTkCheckBox(fo, text="自动裁边", variable=v_clip).pack(side="left")
        ctk.CTkLabel(fo, text="往里收（米）").pack(side="left", padx=(16, 4))
        ctk.CTkEntry(fo, textvariable=v_ins, width=56).pack(side="left")
        ctk.CTkLabel(fo, text="点云密度门槛（%）").pack(side="left", padx=(12, 4))
        ctk.CTkEntry(fo, textvariable=v_den, width=56).pack(side="left")
        bar = ctk.CTkFrame(dlg, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=(4, 14))

        def show_plan(plan):
            for r_ in rows_ui:
                for w in r_["widgets"]:
                    w.destroy()
            rows_ui.clear()
            sites = plan["sites"]
            for i, r in enumerate(plan["rows"], 1):
                ws = [ctk.CTkLabel(lst, text=r["key"], width=cols[0][1], anchor="w"),
                      ctk.CTkLabel(lst, text=r["kind"] or "-", width=cols[1][1], anchor="w"),
                      ctk.CTkLabel(lst, text=("EPSG:%s " % r["epsg"] if r["epsg"] else "") + cc_box_text(r["box"]),
                                   width=cols[2][1], anchor="w")]
                var = tk.StringVar(value=r["site"] or (pick_text if r["kind"] and not r["status"] else ""))
                if r["kind"] and not r["status"]:
                    sc = dict(r["scores"])
                    names = [s["name"] for s in sorted((s for s in sites if s["epsg"] == r["epsg"]),
                                                       key=lambda s: -sc.get(s["name"], 0))]
                    om = ctk.CTkOptionMenu(lst, variable=var, values=[pick_text] + names + [CC_SKIP_LABEL],
                                           width=cols[3][1], dynamic_resizing=False)
                    ws.append(om)
                else:
                    ws.append(ctk.CTkLabel(lst, text="", width=cols[3][1]))
                ws.append(ctk.CTkLabel(lst, text="%.2f" % r["score"] if r["scores"] else "", width=cols[4][1],
                                       anchor="e"))
                st = cc_row_state(r)
                ws.append(ctk.CTkLabel(lst, text=st, width=cols[5][1], anchor="w", wraplength=int(cols[5][1] * win_scale),
                                       justify="left", text_color=label_color if r["site"] else
                                       ("#E65100", "#FFB74D") if r["kind"] and not r["status"] else hint_color))
                for c, w in enumerate(ws):
                    w.grid(row=i, column=c, sticky="w", padx=(0, 6), pady=(0, 1))
                rows_ui.append({"row": r, "var": var, "widgets": ws})
            n_auto = sum(1 for r in plan["rows"] if r["how"] == "自动")
            n_pick = sum(1 for r in plan["rows"] if r["kind"] and not r["status"] and not r["site"])
            lbl_st.configure(text="候选工点 %d 个；CC 成果 %d 个：自动认出 %d 个，要选 %d 个，不能处理 %d 个" % (
                len(sites), len(plan["rows"]), n_auto, n_pick, sum(1 for r in plan["rows"] if r["status"])))
            set_btn(b_go, any(r["kind"] and not r["status"] for r in plan["rows"]))

        def scan():
            dirs = [d.strip().strip('"') for d in tb_dirs.get("1.0", "end").splitlines() if d.strip()]
            if not dirs:
                ui_box.showwarning(APP_NAME, "请先添加 CC 目录")
                return
            set_btn(b_scan, False)
            set_btn(b_go, False)
            lbl_st.configure(text="扫描中…")

            def work():
                try:
                    qd.put(("plan", cc_plan(dirs, out)))
                except Exception as ex:
                    qd.put(("plan", ex))
            res["dirs"] = dirs
            threading.Thread(target=work, daemon=True).start()

        def poll_dlg():
            if not dlg.winfo_exists():
                return
            try:
                while True:
                    k, v = qd.get_nowait()
                    set_btn(b_scan, True)
                    if isinstance(v, Exception):
                        lbl_st.configure(text="扫描出错")
                        ui_box.showwarning(APP_NAME, str(v))
                    else:
                        res["plan"] = v
                        show_plan(v)
            except queue.Empty:
                pass
            dlg.after(150, poll_dlg)

        def go():
            plan = res["plan"]
            if not plan:
                return
            mapping, unpicked = {}, []
            for r_ in rows_ui:
                r, ch = r_["row"], r_["var"].get()
                if not r["kind"] or r["status"]:
                    continue
                if ch == pick_text:
                    unpicked.append(r["key"])
                elif ch != r["site"] or r["how"] == "用户指定":
                    if mapping.get(r["dir"], ch) != ch:
                        ui_box.showwarning(APP_NAME, "同一个 Production（%s）里的两个成果选了不同的工点" % r["key"])
                        return
                    mapping[r["dir"]] = ch
            if unpicked:
                ui_box.showwarning(APP_NAME, "这些成果还没选工点（不想整理就选「%s」）：\n%s" % (
                    CC_SKIP_LABEL, "\n".join(unpicked)))
                return
            try:
                inset, density = survey_clip_inset(v_ins.get()), survey_clip_density(v_den.get())
                cc_assign(plan["rows"], plan["sites"], mapping)
            except ValueError as ex:
                ui_box.showwarning(APP_NAME, str(ex))
                return
            todo = [r for r in plan["rows"] if r["site"] and not r["status"]]
            if not todo:
                ui_box.showwarning(APP_NAME, "没有要整理的成果")
                return
            conf = cc_conflicts(todo, dest)
            replace = False
            if conf:
                replace = ui_box.askyesno(APP_NAME + " - 交付目录里已有别的来源的文件", "交付目录里这些文件已经有了、来源不同：\n%s"
                                          "\n\n换成 CC 的吗？（选「否」就不覆盖，这几个成果不整理）" % "\n".join(
                                              "%s\\%s（原来源 %s → %s）" % (r["site"], fn, src, r["source"])
                                              for r, fn, src in conf[:12]) + ("\n…" if len(conf) > 12 else ""),
                                          icon="warning")
            ms = None
            if v_clip.get() and any(r["kind"] == CC_KIND_ORTHO for r in todo):
                sites = {s["name"]: s for s in plan["sites"]}
                if any(sites[r["site"]]["psx"] and _psx_has_dense_cloud(sites[r["site"]]["psx"]) for r in todo
                       if r["kind"] == CC_KIND_ORTHO):
                    ms = v5_ms.get().strip()
                    err = check_metashape(ms)
                    if err:
                        ui_box.showwarning(APP_NAME, "工点工程里有密集点云，裁边要用 Metashape 算点云判据：" + err)
                        return
            res.update(go=True, inset=inset, density=density, replace=replace, ms=ms, clip=bool(v_clip.get()))
            dlg.destroy()

        b_scan.configure(command=scan)
        b_go = button(bar, "开始整理", go, width=120, primary=True, state="disabled")
        ctk.CTkButton(bar, text="关闭", width=88, command=dlg.destroy, **secondary).pack(side="right", padx=(8, 0))
        b_go.pack(side="right")
        dlg.bind("<Escape>", lambda ev: dlg.destroy())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.ui_cc = {"dirs": tb_dirs, "scan": scan, "go": go, "rows": rows_ui, "clip": v_clip, "inset": v_ins,
                     "density": v_den, "res": res, "pick": pick_text}
        root.ui_cc_dlg = dlg                                    # 给 scripts/test_gui.py 看
        dlg.after(150, poll_dlg)
        if res.get("dirs") is None and tb_dirs.get("1.0", "end").strip():
            dlg.after(300, scan)                                # 上次的目录还在：打开就先扫一遍
        show_modal(dlg)
        if not res.get("go"):
            return
        cfg = load_config()
        cfg.update(survey_cc_dirs=res["dirs"], survey_cc_clip=res["clip"])
        save_config(cfg)
        save5()
        log_clear(lg5)
        pb5.set(0)
        lbl5.configure(text="")
        set_busy5(True)
        stop = threading.Event()
        st5["stop"], st5["running"] = stop, True
        logf = Path(out) / ("整理CC成果日志_%s.txt" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        log_set_file(lg5, logf)
        plan = res["plan"]

        def work():
            try:
                with open(logf, "a", encoding="utf-8") as flog:
                    def log(s):
                        flog.write(s + "\n")
                        flog.flush()
                        q5.put(("log", s))
                    r = run_cc_deliver(plan, out, dest, clip=res["clip"], inset=res["inset"], density=res["density"],
                                       replace=res["replace"], metashape=res["ms"], log=log,
                                       progress=lambda f, s: q5.put(("prog", (f, s))), stop_event=stop)
                q5.put(("cc_done", (r, str(logf))))
            except Exception as ex:
                q5.put(("cc_done", ex))

        threading.Thread(target=work, daemon=True).start()

    btn5_cc.configure(command=cc5)
    btn5_reclip.configure(command=reclip5)
    btn5_deliver.configure(command=deliver5)
    btn5_start.configure(command=start5)
    btn5_stop.configure(command=stop5)
    root.protocol("WM_DELETE_WINDOW", on_close5)
    root.ui_t9 = {"st5": st5, "q5": q5, "rows": lambda: st5["rows"], "set_srs": set_srs5, "reclip": reclip5,
                  "sort": sort5, "sort_var": v5_sort, "head": head5, "deliver": deliver5, "deliver_var": v5_deliver,
                  "deliver_to": v5_deliver_to, "cc": cc5}  # 给测试脚本看

    root.after(200, poll5)

    # 后续功能在这里加新页签：tab = nb.add("...")
    nb.set("剔除定位")
    root.after(100, poll)
    root.mainloop()


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def _print(s):
    if sys.stdout is not None:  # --windowed 打包后没有控制台
        try:
            print(s, flush=True)
        except UnicodeEncodeError:
            print(s.encode("gbk", "replace").decode("gbk"), flush=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    setup_geo_env()   # D-34：在 import rasterio / pyproj 之前把 GDAL/PROJ 数据指到包里
    if not argv:
        try:
            run_gui()
        except Exception as ex:
            _print("界面启动失败：%s" % ex)
            return 1
        return 0
    ap = argparse.ArgumentParser(prog="uav_photo_tool", description="%s v%s" % (APP_NAME, APP_VERSION))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("strip-gps", help="剔除定位信息（默认复制，可选原地抹除）")
    p.add_argument("src", help="输入目录")
    p.add_argument("dst", nargs="?", help="复制模式输出目录（须为空，名字带 %s）" % OUTPUT_SUFFIX)
    p.add_argument("--no-recursive", action="store_true", help="不处理子目录")
    p.add_argument("--strip-attitude", action="store_true", help="同时剔除姿态 Gimbal*/Flight*")
    p.add_argument("--in-place", action="store_true", help="原地抹除，直接修改照片（必须配 --confirm）")
    p.add_argument("--confirm", help="原地模式确认目录名（不是完整路径）")
    p.add_argument("--no-backup", action="store_true", help="原地模式不保留定位备份")
    r = sub.add_parser("restore-gps", help="用定位备份逐字节还原照片")
    r.add_argument("src", help="照片目录")
    r.add_argument("--backup", required=True, help="定位备份 zip")
    r.add_argument("--no-recursive", action="store_true", help="兼容参数，不处理目录外文件")
    a = sub.add_parser("align", help="调用 Metashape 空三并导出 Blocks Exchange XML")
    a.add_argument("photos", help="照片目录")
    a.add_argument("--metashape", help="metashape.exe / photoscan.exe 路径（默认用上次界面里记的或常见安装位置）")
    a.add_argument("--accuracy", choices=sorted(ACCURACY_DOWNSCALE), default="high", help="精度，默认 high")
    a.add_argument("--recursive", action="store_true", help="每个含照片的子目录单独一个工程")
    a.add_argument("--no-auto-strip", action="store_true", help="照片带定位时不自动去定位（会拒绝空三）")
    a.add_argument("--out", help="输出位置（默认照片目录所在的上级目录）")
    a.add_argument("--keypoints", type=int, default=DEFAULT_KEYPOINTS, help="关键点上限，默认 %d" % DEFAULT_KEYPOINTS)
    a.add_argument("--tiepoints", type=int, default=DEFAULT_TIEPOINTS, help="连接点上限，默认 %d" % DEFAULT_TIEPOINTS)
    a.add_argument("--log", help="把日志另存到这个文件（打包的 exe 没有控制台时用）")
    g = sub.add_parser("gcp-transfer", help="控制点·迁移：从参考期把控制点自动放到新一期照片上并平差"
                       "（退出码 0 自动通过 / 1 失败 / 2 拒绝 / 3 取消 / 4 需人工核对）")
    g.add_argument("photos", help="新一期照片目录（带定位时自动先去定位）")
    g.add_argument("--ref", required=True, help="参考期工程 .psx（只读，不会被修改）")
    g2 = sub.add_parser("gcp-init", help="控制点·建参考期：空三工程 + 控制点 CSV（+ CC 刺点），自动判 X/Y 对调"
                        "（退出码 0 自动通过 / 1 失败 / 2 拒绝 / 3 取消 / 4 需人工核对）")
    g2.add_argument("psx", help="「空三」页签产出的 .psx（只读，不会被修改）")
    g2.add_argument("--csv", required=True, help="控制点 CSV：点名,X,Y,Z（米）")
    g2.add_argument("--cc", help="ContextCapture 导出的 Blocks Exchange（.xml/.xmlz），里面的控制点刺点；不给就用工程里已刺的点")
    for x in (g, g2):
        x.add_argument("--out", help="输出位置（默认：迁移＝照片目录所在的上级目录；建参考期＝空三输出目录所在的上级目录）")
        x.add_argument("--accuracy", type=float, default=DEFAULT_GCP_ACCURACY, help="控制点精度（米），默认 %g" % DEFAULT_GCP_ACCURACY)
        x.add_argument("--metashape", help="metashape.exe 路径（默认用上次界面里记的或常见安装位置）")
        x.add_argument("--log", help="把日志另存到这个文件（打包的 exe 没有控制台时用）")
    g.add_argument("--no-auto-strip", action="store_true", help="照片带定位时不自动去定位（会拒绝执行）")
    g.add_argument("--max-shift", type=float, default=GCP_MAX_SHIFT_PX,
                   help="D-20 细化挪动上限（像素），默认 %g：挪动更大的投影删掉，挪动中位数更大的点自动排除" % GCP_MAX_SHIFT_PX)
    for x in (g, g2):
        x.add_argument("--exclude", default="", help="排除的控制点，逗号分隔，如 1,2：不参与平差，改作检查点")
    bt = sub.add_parser("gcp-batch", help="批量迁移：上级目录下每期按名字配参考期，一期接一期跑 gcp-transfer"
                        "（退出码 0 全部自动通过或跳过 / 4 有需人工核对 / 1 有失败 / 2 参数错误 / 3 取消）")
    bt.add_argument("root", help="照片上级目录：每个直接含 JPG 的子目录算一期（只读）")
    bt.add_argument("--out", required=True, help="输出目录：每期的去定位照片、结果和汇总表都写在这里")
    bt.add_argument("--rule", action="append", required=True, metavar="关键词=参考期.psx[:排除点]",
                    help="参考期规则，可给多次，从前往后匹配第一条，如 --rule 梯田=D:\梯田.psx:4 --rule 植被=D:\植被.psx")
    bt.add_argument("--include", default="", help="只要名字匹配的期（通配符，逗号分隔，如 \"*梯田*\"）")
    bt.add_argument("--pick", default="", help="按扫描序号选（如 1,3,5-9；先用 --plan 看序号）")
    bt.add_argument("--skip", default="", help="跳过名单：不处理的期次名，逗号分隔（如参考期本身）")
    bt.add_argument("--in-place", action="store_true",
                    help="直接在原照片上抹除定位（不复制，定位备份写在各期照片目录里；必须配 --confirm）；默认复制去定位照片到 --out")
    bt.add_argument("--confirm", help="原地模式确认：照片上级目录的名字（不是完整路径）")
    bt.add_argument("--plan", action="store_true", help="只列计划、写汇总表，不运行")
    bt.add_argument("--metashape", help="metashape.exe 路径（默认用上次界面里记的或常见安装位置）")
    bt.add_argument("--log", help="把日志另存到这个文件（打包的 exe 没有控制台时用）")
    sv = sub.add_parser("survey-batch", help="影像处理（D-29）：扫描工点，一个接一个用 Metashape 最高精度空三，导出带 CGCS2000 "
                        "3 度带坐标的 Blocks Exchange（CC 直接导入），可选出 DOM/DSM；不去定位"
                        "（退出码 0 全部完成或跳过 / 1 有失败 / 2 参数错误 / 3 取消）")
    sv.add_argument("root", help="照片上级目录（只读）：每个直接含 JPG 的文件夹算一个工点，下面的子文件夹（如 正摄）并进来")
    sv.add_argument("--out", required=True, help="输出目录：每个工点一个 <工点名> 子目录，汇总表也写在这里；不能在照片目录里面")
    sv.add_argument("--epsg", default="auto", help="坐标系：auto（默认，每个工点按照片平均经度选最近的 CGCS2000 3 度带）"
                    "或 4534～4554 的 EPSG 号（整批统一，如 4543）")
    sv.add_argument("--dom-dsm", action="store_true", help="同时出 DOM、DSM（超高质量，很慢）")
    sv.add_argument("--clip", default="auto", help="DOM/DSM 裁边：auto＝默认，只认「DOM 有影像 + 不插值的密集点云真有点 + "
                    "点云够密」的地方，补缝、取最大连片、填掉内部 < 100 m² 的洞、往里收、简化成范围线，DOM 和 DSM 用同一条线"
                    "同一个像元网格裁；none＝不裁（同 v1.0）；也可以给一个 .kml/.shp 范围线文件，按它裁（原样，不再往里收）")
    sv.add_argument("--clip-inset", default="%g" % SURVEY_CLIP_INSET,
                    help="自动裁边往里收多少米（0～%g），默认 %g" % (SURVEY_CLIP_INSET_MAX, SURVEY_CLIP_INSET))
    sv.add_argument("--clip-density", default="%g" % SURVEY_CLIP_DENSITY,
                    help="自动裁边的点云密度门槛（%%%%，%g～%g）：1 米块里有真实点的 0.1 米子格占比够这么多才算有效，默认 %g" % (
                        SURVEY_CLIP_DENSITY_MIN, SURVEY_CLIP_DENSITY_MAX, SURVEY_CLIP_DENSITY))
    sv.add_argument("--resolution", default="%g" % SURVEY_RESOLUTION,
                    help="DOM/DSM 分辨率（米），默认 %g；auto＝Metashape 给的原始分辨率" % SURVEY_RESOLUTION)
    sv.add_argument("--include", default="", help="只要名字匹配的工点（通配符，逗号分隔，如 \"*弃渣场*\"）")
    sv.add_argument("--pick", default="", help="按扫描序号选（如 1,3,5-9；先用 --plan 看序号）")
    sv.add_argument("--skip", default="", help="跳过名单：不处理的工点名，逗号分隔")
    sv.add_argument("--plan", action="store_true", help="只列计划、写汇总表，不运行")
    sv.add_argument("--sort", default="scan", choices=SURVEY_SORTS, help="处理顺序：scan＝扫描顺序（默认）/ "
                    "photos-desc＝照片数从多到少 / photos-asc＝从少到多；序号仍按扫描顺序")
    sv.add_argument("--deliver", action="store_true", help="（配 --dom-dsm）跑完把完成的工点整理到交付目录："
                    "每个工点一个文件夹，只放 DOM/DSM 四个文件 + 交付清单.csv")
    sv.add_argument("--deliver-to", help="交付目录，默认是输出目录同级的 <输出目录名>_交付")
    sv.add_argument("--keypoints", type=int, default=SURVEY_KEYPOINTS, help="关键点上限，默认 %d" % SURVEY_KEYPOINTS)
    sv.add_argument("--tiepoints", type=int, default=SURVEY_TIEPOINTS, help="连接点上限，默认 %d" % SURVEY_TIEPOINTS)
    sv.add_argument("--metashape", help="metashape.exe 路径（默认用上次界面里记的或常见安装位置）")
    sv.add_argument("--log", help="把日志另存到这个文件（打包的 exe 没有控制台时用）")
    sc = sub.add_parser("survey-clip", help="按范围线重新裁剪（D-32）：已完成的工点只重新导出 DOM/DSM，不碰空三和建模"
                        "（退出码 0 成功 / 1 有失败 / 2 参数错误 / 3 取消）")
    sc.add_argument("sites", nargs="+", help="工点输出目录（<输出目录>\<工点名>，里面有 .psx、DOM/DSM 和 处理报告.txt），可给多个")
    sc.add_argument("--line", help="范围线 KML（WGS84）或 SHP（按 .prj，没有 .prj 当作与 DOM 同坐标系）：多个多边形都保留，"
                    "内环＝挖掉，原样裁、不再往里收。不给：工点目录里的 <工点名>_范围线.shp/.kml 比报告新（改过）就用它，"
                    "否则按 --clip-inset / --clip-density 重新自动算")
    sc.add_argument("--clip-inset", help="重新自动算时往里收多少米（0～%g），默认 %g；给了就重新自动算" % (
        SURVEY_CLIP_INSET_MAX, SURVEY_CLIP_INSET))
    sc.add_argument("--clip-density", help="重新自动算时的点云密度门槛（%%%%，%g～%g），默认 %g；给了就重新自动算" % (
        SURVEY_CLIP_DENSITY_MIN, SURVEY_CLIP_DENSITY_MAX, SURVEY_CLIP_DENSITY))
    sc.add_argument("--metashape", help="metashape.exe 路径（默认用上次界面里记的或常见安装位置）")
    sc.add_argument("--log", help="把日志另存到这个文件（打包的 exe 没有控制台时用）")
    sd = sub.add_parser("survey-deliver", help="整理交付目录：输出目录里完成且有 DOM/DSM 的工点，每个一个文件夹，只放 "
                        "<工点名>DOM.tif/.tfw、<工点名>DSM.tif/.tfw（复制，逐个核对 SHA-256），根目录写 交付清单.csv"
                        "（退出码 0 成功 / 1 有失败 / 2 参数错误 / 3 取消）")
    sd.add_argument("out", help="影像处理的输出目录（每个工点一个子目录，只读）")
    sd.add_argument("--to", help="交付目录，默认是输出目录同级的 <输出目录名>_交付")
    sd.add_argument("--site", default="", help="只整理这些工点（工点名，逗号分隔）；默认全部")
    sd.add_argument("--log", help="把日志另存到这个文件（打包的 exe 没有控制台时用）")
    scc = sub.add_parser("survey-cc-deliver", help="整理 CC 成果（T17）：CC 的 Production_* 按外框和坐标系自动认工点，"
                         "DOM/DSM 裁边（不重采样）、连同 3SM 改名复制进交付目录（退出码 0 成功 / 1 有失败或有成果没认出 / "
                         "2 参数错误 / 3 取消）")
    scc.add_argument("prods", nargs="+", help="CC 工程目录（自动找下面的 Productions）、Productions 目录或某个 Production "
                     "目录，可给多个（只读）")
    scc.add_argument("--out", required=True, help="影像处理的输出目录：候选工点 = 里面完成 / 部分对齐、有 _BlocksExchange.xml "
                     "的工点；范围线和处理报告的「CC 成果整理」段写在各工点目录里")
    scc.add_argument("--to", help="交付目录，默认是输出目录同级的 <输出目录名>_交付")
    scc.add_argument("--map", action="append", default=[], help="认不准时指定：Production 目录名=工点名（多个工程同名时写 "
                     "工程目录名/Production 名），逗号分隔或给多次")
    scc.add_argument("--no-clip", action="store_true", help="不裁边：CC 的 tif/tfw/tif.ovr 改名原样复制")
    scc.add_argument("--clip-inset", default="%g" % SURVEY_CLIP_INSET,
                     help="裁边往里收多少米（0～%g），默认 %g" % (SURVEY_CLIP_INSET_MAX, SURVEY_CLIP_INSET))
    scc.add_argument("--clip-density", default="%g" % SURVEY_CLIP_DENSITY,
                     help="点云密度门槛（%%%%，%g～%g，工点工程有密集点云时用），默认 %g" % (
                         SURVEY_CLIP_DENSITY_MIN, SURVEY_CLIP_DENSITY_MAX, SURVEY_CLIP_DENSITY))
    scc.add_argument("--replace", action="store_true", help="交付目录里已有来源不同的同名文件（如 Metashape 的 DOM）时换掉；"
                     "不给就不覆盖、报出来")
    scc.add_argument("--plan", action="store_true", help="只扫描、列出认定结果，不写任何文件")
    scc.add_argument("--metashape", help="metashape.exe 路径（工点工程有密集点云、要算点云判据时用）")
    scc.add_argument("--log", help="把日志另存到这个文件（打包的 exe 没有控制台时用）")
    sub.add_parser("check-update", help="查 GitHub 上有没有新版本（退出码 0 已是最新 / 10 有新版本 / 1 查询失败）")
    st = sub.add_parser("selftest", help="自检：检查随包带的 GDAL/PROJ/numpy/shapely/sqlite3 能不能用"
                        "（退出码 0 全部通过 / 1 有失败项）")
    st.add_argument("--log", help="把自检结果另存到这个文件（打包的 exe 没有控制台时用）")
    args = ap.parse_args(argv)
    if args.cmd == "survey-deliver":
        return _cli_survey_deliver(args)
    if args.cmd == "survey-cc-deliver":
        return _cli_survey_cc_deliver(args)
    if args.cmd == "survey-batch":
        return _cli_survey(args)
    if args.cmd == "survey-clip":
        return _cli_survey_clip(args)
    if args.cmd == "gcp-batch":
        return _cli_batch(args)
    if args.cmd == "align":
        return _cli_align(args)
    if args.cmd in ("gcp-transfer", "gcp-init"):
        return _cli_gcp(args)
    if args.cmd == "selftest":
        flog = open(args.log, "a", encoding="utf-8") if args.log else None

        def slog(t):
            _print(t)
            if flog:
                flog.write(t + "\n")
                flog.flush()

        try:
            ok, res = run_selftest(log=slog)
            slog("自检结果：%s（%d 项）" % ("全部通过" if ok else "有失败项", len(res)))
        finally:
            if flog:
                flog.close()
        return 0 if ok else 1
    if args.cmd == "check-update":
        try:
            info = check_update()
        except Exception as ex:
            _print("检查更新失败：%s" % ex)
            return 1
        if info["newer"]:
            _print("有新版本 v%s（当前 v%s）：%s" % (info["latest"], APP_VERSION, info["url"]))
            return 10
        _print("已经是最新版本（当前 v%s，GitHub 最新 v%s）" % (APP_VERSION, info["latest"]))
        return 0
    if args.cmd == "restore-gps":
        try:
            res = restore_gps(args.src, args.backup, not args.no_recursive, log=_print)
        except (ValueError, OSError, zipfile.BadZipFile) as ex:
            _print("拒绝执行：%s" % ex)
            return 2
        return 1 if res["失败"] else 0
    if args.in_place:
        if args.dst is not None or args.confirm != Path(args.src).name:
            _print("拒绝执行：原地模式必须使用正确的 --confirm 目录名，且不能给输出目录")
            return 2
        try:
            res = run_strip_in_place(args.src, not args.no_recursive, args.strip_attitude,
                                     args.no_backup, args.confirm, log=_print)
        except ValueError as ex:
            _print("拒绝执行：%s" % ex)
            return 2
        return 1 if res["失败"] else 0
    if args.confirm is not None or args.no_backup:
        _print("拒绝执行：--confirm/--no-backup 只能用于 --in-place")
        return 2
    dst = args.dst or str(default_output_dir(args.src))
    try:
        res = run_strip(args.src, dst, not args.no_recursive, args.strip_attitude,
                        progress=lambda i, n: None, log=_print)
    except ValueError as ex:
        _print("拒绝执行：%s" % ex)
        return 2
    return 1 if res["失败"] else 0


def _cli_align(args):
    flog = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(s):
        _print(s)
        if flog:
            flog.write(s + "\n")
            flog.flush()

    ms = args.metashape or find_metashape()
    try:
        res = run_align(args.photos, ms, args.out, args.recursive, not args.no_auto_strip, args.accuracy,
                        args.keypoints, args.tiepoints, log=log)
    except ValueError as ex:
        log("拒绝执行：%s" % ex)
        return 2
    except (AlignCancelled, KeyboardInterrupt):
        log("已取消")
        return 3
    finally:
        if flog:
            flog.close()
    if any(r["status"] == "已取消" for r in res):
        return 3
    return 0 if all(r["status"] == "成功" for r in res) else 1


def _cli_gcp(args):
    """退出码：0 自动通过 / 4 需人工核对 / 1 失败 / 2 拒绝执行 / 3 已取消。"""
    flog = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(s):
        _print(s)
        if flog:
            flog.write(s + "\n")
            flog.flush()

    ms = args.metashape or find_metashape()
    try:
        if args.cmd == "gcp-transfer":
            res = run_gcp_transfer(args.photos, args.ref, ms, args.out, args.accuracy, not args.no_auto_strip, log=log,
                                   exclude=args.exclude, max_shift=args.max_shift)
        else:
            res = run_gcp_init(args.psx, args.csv, ms, args.cc, args.out, args.accuracy, log=log, exclude=args.exclude)
    except ValueError as ex:
        log("拒绝执行：%s" % ex)
        return 2
    except (AlignCancelled, KeyboardInterrupt):
        log("已取消")
        return 3
    finally:
        if flog:
            flog.close()
    return {"自动通过": 0, "需人工核对": 4, "已取消": 3}.get(res["status"], 1)


def _split_names(text):
    return [x.strip() for x in re.split(r"[,，]", text or "") if x.strip()]


def _cli_batch(args):
    """退出码：0 全部自动通过或跳过 / 4 有需人工核对 / 1 有失败 / 2 参数错误 / 3 取消。"""
    flog = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(s):
        _print(s)
        if flog:
            flog.write(s + "\n")
            flog.flush()

    try:
        try:
            if args.confirm is not None and not args.in_place:
                raise ValueError("--confirm 只能和 --in-place 一起用")
            rules = [parse_batch_rule(r) for r in args.rule]
            errs = check_batch_rules(rules)
            if errs:
                raise ValueError("；".join(errs))
            ms = args.metashape or find_metashape()
            if not args.plan:
                err = check_metashape(ms)
                if err:
                    raise ValueError(err)
            items = batch_scan(args.root, rules, os.path.abspath(args.out), skip=_split_names(args.skip))
            log("共找到 %d 期照片目录。" % len(items))
            chosen = items
            inc = _split_names(args.include)
            if inc:
                chosen = [it for it in chosen if any(fnmatch.fnmatchcase(it["name"].lower(), x.lower()) for x in inc)]
                log("按 --include 筛选后剩 %d 期。" % len(chosen))
            if args.pick:
                pick = parse_pick(args.pick, len(items))
                chosen = [it for it in chosen if it["idx"] in pick]
                log("按 --pick 选中 %d 期。" % len(chosen))
        except (ValueError, OSError) as ex:  # OSError：上级目录读不了等
            log("拒绝执行：%s" % ex)
            return 2
        if not chosen:
            log("没有选中任何期次，结束。")
            return 0
        try:
            res = run_gcp_batch(chosen, args.out, ms, plan=args.plan, log=log,
                                photo_mode="inplace" if args.in_place else "copy", root=args.root, confirm=args.confirm)
        except ValueError as ex:  # 原地模式开跑前检查没过：什么都没动
            log("拒绝执行：%s" % ex)
            return 2
        except KeyboardInterrupt:
            log("已取消")
            return 3
        return res["exit"]
    finally:
        if flog:
            flog.close()


def _cli_survey(args):
    """退出码：0 全部完成/部分对齐或跳过 / 1 有失败 / 2 参数错误 / 3 取消。
    不给 --include / --pick 时只处理「默认勾」的工点（同界面：待处理、且不在子目录里）；给了就从全部工点里选。"""
    flog = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(s):
        _print(s)
        if flog:
            flog.write(s + "\n")
            flog.flush()

    try:
        try:
            epsg = parse_survey_epsg(args.epsg)
            resolution = parse_survey_resolution(args.resolution)
            clip, clip_file = parse_survey_clip(args.clip)
            clip_inset = survey_clip_inset(args.clip_inset)
            clip_density = survey_clip_density(args.clip_density)
            if not args.dom_dsm and (clip != SURVEY_CLIP_AUTO or args.clip_inset != "%g" % SURVEY_CLIP_INSET or
                                     args.clip_density != "%g" % SURVEY_CLIP_DENSITY):
                log("注意：没勾 DOM/DSM，--clip/--clip-inset/--clip-density 不起作用")
            if args.keypoints <= 0 or args.tiepoints < 0:
                raise ValueError("关键点上限必须大于 0，连接点上限不能为负")
            ms = args.metashape or find_metashape()
            if not args.plan:
                err = check_metashape(ms)
                if err:
                    raise ValueError(err)
            log("扫描 %s …" % args.root)
            items = survey_scan(args.root, os.path.abspath(args.out), skip=_split_names(args.skip), epsg=epsg,
                                progress=lambda i, n, s: (i % 1000 == 0 and i) and log("  已读 %d/%d 张照片的定位" % (i, n)))
            log("共找到 %d 个工点（在子目录里、默认不处理的 %d 个）：" % (len(items), sum(it["nested"] for it in items)))
            for it in items:
                log("  %2d. %s：%d 张，带定位 %d，平均经度 %s → %s（%s）%s；%s%s" % (
                    it["idx"], it["rel"], it["n"], it["n_gps"], "%.4f°" % it["lon"] if it["lon"] is not None else "-",
                    survey_srs_label(it["epsg"]) or "没有坐标系", it["epsg_src"], "，靠近分带线" if it["near"] else "",
                    it["status"], "" if it["default"] else "（默认不处理）"))
            inc = _split_names(args.include)
            if args.pick or inc:
                chosen = items
                if inc:
                    chosen = [it for it in chosen if any(fnmatch.fnmatchcase(it["name"].lower(), x.lower()) for x in inc)]
                    log("按 --include 筛选后剩 %d 个。" % len(chosen))
                if args.pick:
                    pick = parse_pick(args.pick, len(items))
                    chosen = [it for it in chosen if it["idx"] in pick]
                    log("按 --pick 选中 %d 个。" % len(chosen))
            else:
                chosen = [it for it in items if it["default"] or not survey_runnable(it)]
            if args.sort != "scan":
                chosen = survey_sort_items(chosen, args.sort)
                log("处理顺序：%s" % {"photos-desc": "照片数从多到少", "photos-asc": "照片数从少到多"}[args.sort])
            if args.deliver and not args.dom_dsm:
                log("注意：没勾 DOM/DSM，--deliver 不起作用")
            if args.deliver_to and not args.deliver:
                log("注意：没给 --deliver，--deliver-to 不起作用")
            dest = None
            if args.deliver and args.dom_dsm and not args.plan:
                dest = Path(os.path.abspath(args.deliver_to)) if args.deliver_to else survey_deliver_default(args.out)
                o, r = Path(os.path.abspath(args.out)), Path(os.path.abspath(args.root))
                if dest == o or o in dest.parents or dest in o.parents:
                    raise ValueError("交付目录不能是输出目录本身，也不能在它里面或包含它：%s" % dest)
                if dest == r or r in dest.parents:
                    raise ValueError("交付目录不能放在照片上级目录里面（原片目录只读）：%s" % dest)
        except (ValueError, OSError) as ex:
            log("拒绝执行：%s" % ex)
            return 2
        if not chosen:
            log("没有选中任何工点，结束。")
            return 0
        try:
            res = run_survey_batch(chosen, args.out, ms, plan=args.plan, dom_dsm=args.dom_dsm, resolution=resolution,
                                   keypoints=args.keypoints, tiepoints=args.tiepoints, log=log, clip=clip,
                                   clip_inset=clip_inset, clip_file=clip_file, clip_density=clip_density)
            if dest is not None and not res["cancelled"]:
                log("")
                try:
                    d = survey_deliver(args.out, dest, sites=[it["name"] for it in chosen], log=log)
                except (ValueError, OSError) as ex:
                    log("整理交付目录没做成：%s" % ex)
                    return max(res["exit"], 1)
                if d["exit"] == 3:
                    return 3
                return max(res["exit"], d["exit"])
        except ValueError as ex:
            log("拒绝执行：%s" % ex)
            return 2
        except KeyboardInterrupt:
            log("已取消")
            return 3
        return res["exit"]
    finally:
        if flog:
            flog.close()


def _cli_survey_clip(args):
    """survey-clip：退出码 0 成功 / 1 有失败 / 2 参数错误 / 3 取消。"""
    flog = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(s):
        _print(s)
        if flog:
            flog.write(s + "\n")
            flog.flush()

    try:
        try:
            line = None
            if args.line:
                if args.clip_inset is not None or args.clip_density is not None:
                    raise ValueError("--line 给的范围线原样裁、不再往里收，不能再给 --clip-inset / --clip-density")
                kind, line = parse_survey_clip(args.line)
                if kind != "file":
                    raise ValueError("--line 要给一个 .kml 或 .shp 文件")
            inset = survey_clip_inset(args.clip_inset if args.clip_inset is not None else SURVEY_CLIP_INSET)
            density = survey_clip_density(args.clip_density if args.clip_density is not None else SURVEY_CLIP_DENSITY)
            source = "file" if line else "auto" if (args.clip_inset is not None or args.clip_density is not None) \
                else "smart"
            sites = []
            for d in args.sites:
                p = Path(os.path.abspath(d))
                n = p.name
                miss = [f for f in (n + ".psx", n + "DOM.tif", n + "DSM.tif", SURVEY_REPORT) if not (p / f).is_file()]
                if miss:
                    raise ValueError("不是已完成的工点目录（缺 %s）：%s" % ("、".join(miss), p))
                sites.append(str(p))
            ms = args.metashape or find_metashape()
            err = check_metashape(ms)
            if err:
                raise ValueError(err)
        except (ValueError, OSError) as ex:
            log("拒绝执行：%s" % ex)
            return 2
        try:
            res = run_survey_clip(sites, ms, source, line, inset, density, log=log)
        except KeyboardInterrupt:
            log("已取消")
            return 3
        return res["exit"]
    finally:
        if flog:
            flog.close()


def _cli_survey_deliver(args):
    """survey-deliver：退出码 0 成功 / 1 有失败 / 2 参数错误 / 3 取消。"""
    flog = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(s):
        _print(s)
        if flog:
            flog.write(s + "\n")
            flog.flush()

    try:
        names = _split_names(args.site) or None
        try:
            if not os.path.isdir(args.out):
                raise ValueError("输出目录不存在：%s" % args.out)
            if names:
                have = {n for n, _, _ in survey_deliver_sites(args.out)[0]}
                miss = [n for n in names if n not in have]
                if miss:
                    raise ValueError("这些工点不是完成且有 DOM/DSM 的：%s" % "、".join(miss))
            res = survey_deliver(args.out, args.to, sites=names, log=log)
        except (ValueError, OSError) as ex:
            log("拒绝执行：%s" % ex)
            return 2
        except KeyboardInterrupt:
            log("已取消")
            return 3
        return res["exit"]
    finally:
        if flog:
            flog.close()


def _cli_survey_cc_deliver(args):
    """survey-cc-deliver：退出码 0 成功 / 1 有失败或有成果没认出 / 2 参数错误 / 3 取消。"""
    flog = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(s):
        _print(s)
        if flog:
            flog.write(s + "\n")
            flog.flush()

    try:
        try:
            if not os.path.isdir(args.out):
                raise ValueError("输出目录不存在：%s" % args.out)
            inset = survey_clip_inset(args.clip_inset)
            density = survey_clip_density(args.clip_density)
            mapping = parse_cc_map(args.map)
            plan = cc_plan(args.prods, args.out, mapping, log=log)
            if not plan["sites"]:
                raise ValueError("输出目录里没有候选工点（要完成 / 部分对齐、有 _BlocksExchange.xml）：%s" % args.out)
            dest = Path(os.path.abspath(args.to)) if args.to else survey_deliver_default(args.out)
            ms = args.metashape or find_metashape()
        except (ValueError, OSError) as ex:
            log("拒绝执行：%s" % ex)
            return 2
        if args.plan:
            for ln in cc_plan_lines(plan):
                log(ln)
            for r, fn, src in cc_conflicts(plan["rows"], dest):
                log("注意：交付目录里 %s\\%s 已有，来源是 %s；整理时%s" % (r["site"], fn, src,
                                                               "会换掉（--replace）" if args.replace else "不覆盖，要换掉加 --replace"))
            log("（--plan：只列计划，没写任何文件）")
            return 0 if all(r["site"] for r in plan["rows"]) else 1
        try:
            res = run_cc_deliver(plan, args.out, dest, clip=not args.no_clip, inset=inset, density=density,
                                 replace=args.replace, metashape=ms, log=log)
        except ValueError as ex:
            log("拒绝执行：%s" % ex)
            return 2
        except KeyboardInterrupt:
            log("已取消")
            return 3
        return res["exit"]
    finally:
        if flog:
            flog.close()


if __name__ == "__main__":
    sys.exit(main())

