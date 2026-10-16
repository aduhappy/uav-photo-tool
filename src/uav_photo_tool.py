# -*- coding: utf-8 -*-
"""无人机照片处理工具（单文件，只用标准库 + tkinter）。

功能页签：
  1. 剔除定位：批量剔除 JPG 里的定位信息（EXIF GPS + DJI XMP 位置字段），另存到新目录。
  2. 空三：调用用户电脑上的 Metashape Pro 对齐照片，导出不带坐标系的 Blocks Exchange XML。

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
  python uav_photo_tool.py check-update
不带参数启动图形界面（菜单「帮助」里可以检查更新）。
"""

import argparse
import csv
import datetime
import glob
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
from pathlib import Path

APP_NAME = "无人机照片工具"
APP_VERSION = "0.3"
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
                                    stdout=fcon, stderr=subprocess.STDOUT, cwd=str(tmp),
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
# 图形界面
# ---------------------------------------------------------------------------

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    if sys.platform == "win32":  # 高分屏下不模糊
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    root.title("%s v%s" % (APP_NAME, APP_VERSION))
    k = max(1.0, root.winfo_fpixels("1i") / 96.0)
    root.geometry("%dx%d" % (760 * k, 540 * k))
    root.minsize(int(640 * k), int(420 * k))

    # ---- 菜单：帮助 → 检查更新 / 项目主页 ----
    import webbrowser
    var_auto_update = tk.BooleanVar(value=load_config().get("check_update_on_start", True))

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
                    messagebox.showwarning(APP_NAME, "检查更新失败（可能没联网或打不开 GitHub）：\n%s" % err)
            elif info["newer"]:
                msg = "发现新版本 v%s（当前 v%s）。\n\n%s\n\n打开下载页面？" % (info["latest"], APP_VERSION, info["notes"][:600])
                if messagebox.askyesno(APP_NAME + " - 有新版本", msg):
                    webbrowser.open(info["url"])
            elif manual:
                messagebox.showinfo(APP_NAME, "已经是最新版本（v%s）。" % APP_VERSION)

        threading.Thread(target=worker, daemon=True).start()
        root.after(300, poll_update)

    def toggle_auto_update():
        cfg = load_config()
        cfg["check_update_on_start"] = bool(var_auto_update.get())
        save_config(cfg)

    menubar = tk.Menu(root)
    m_help = tk.Menu(menubar, tearoff=0)
    m_help.add_command(label="检查更新", command=lambda: start_update_check(True))
    m_help.add_checkbutton(label="启动时自动检查更新", variable=var_auto_update, command=toggle_auto_update)
    m_help.add_separator()
    m_help.add_command(label="项目主页（GitHub）", command=lambda: webbrowser.open(HOMEPAGE_URL))
    m_help.add_command(label="关于", command=lambda: messagebox.showinfo(
        APP_NAME, "%s v%s\n开源许可：MIT\n%s" % (APP_NAME, APP_VERSION, HOMEPAGE_URL)))
    menubar.add_cascade(label="帮助", menu=m_help)
    root.config(menu=menubar)
    if var_auto_update.get():
        root.after(1500, start_update_check)

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    # ---- 页签 1：剔除定位 ----
    tab = ttk.Frame(nb, padding=10)
    nb.add(tab, text="剔除定位")
    tab.columnconfigure(1, weight=1)

    v_src = tk.StringVar()
    v_dst = tk.StringVar()
    v_rec = tk.BooleanVar(value=True)
    v_att = tk.BooleanVar(value=False)
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

    ttk.Label(tab, text="输入目录：").grid(row=0, column=0, sticky="w", pady=3)
    ttk.Entry(tab, textvariable=v_src).grid(row=0, column=1, sticky="ew", pady=3)
    ttk.Button(tab, text="浏览…", command=pick_src).grid(row=0, column=2, padx=(6, 0), pady=3)
    ttk.Label(tab, text="输出目录：").grid(row=1, column=0, sticky="w", pady=3)
    e_dst = ttk.Entry(tab, textvariable=v_dst)
    e_dst.grid(row=1, column=1, sticky="ew", pady=3)
    e_dst.bind("<Key>", lambda ev: state.__setitem__("dst_auto", False))
    ttk.Button(tab, text="浏览…", command=pick_dst).grid(row=1, column=2, padx=(6, 0), pady=3)

    opts = ttk.Frame(tab)
    opts.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 2))
    ttk.Checkbutton(opts, text="包含子目录", variable=v_rec).pack(side="left")
    ttk.Checkbutton(opts, text="同时剔除姿态（Gimbal*/Flight*）", variable=v_att).pack(side="left", padx=(16, 0))

    ttk.Label(tab, foreground="#555", wraplength=int(700 * k),
              text="说明：剔除 EXIF GPS 和 DJI XMP 位置字段（经纬度、高度、RTK、激光测距目标）；"
                   "相机标定参数保留；图像数据逐字节不变；原片不会被修改。").grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(2, 6))

    bar = ttk.Frame(tab)
    bar.grid(row=4, column=0, columnspan=3, sticky="ew")
    bar.columnconfigure(0, weight=1)
    pb = ttk.Progressbar(bar, mode="determinate")
    pb.grid(row=0, column=0, sticky="ew")
    lbl_prog = ttk.Label(bar, text="", width=12)
    lbl_prog.grid(row=0, column=1, padx=6)
    btn_start = ttk.Button(bar, text="开始")
    btn_start.grid(row=0, column=2)
    btn_stop = ttk.Button(bar, text="中止", state="disabled")
    btn_stop.grid(row=0, column=3, padx=(6, 0))

    logf = ttk.Frame(tab)
    logf.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
    tab.rowconfigure(5, weight=1)
    txt = tk.Text(logf, height=12, wrap="none")
    sb = ttk.Scrollbar(logf, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    def append_log(s):
        txt.insert("end", s + "\n")
        txt.see("end")

    def poll():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == "log":
                    append_log(val)
                elif kind == "prog":
                    i, n = val
                    pb.configure(maximum=max(n, 1), value=i)
                    lbl_prog.configure(text="%d / %d" % (i, n))
                elif kind == "done":
                    btn_start.configure(state="normal")
                    btn_stop.configure(state="disabled")
                    if isinstance(val, Exception):
                        append_log("出错：%s" % val)
                        messagebox.showerror(APP_NAME, str(val))
                    else:
                        msg = "完成：成功 %d，跳过 %d，失败 %d\n日志：%s" % (val["成功"], val["跳过"], val["失败"], val["日志"])
                        (messagebox.showwarning if val["失败"] else messagebox.showinfo)(APP_NAME, msg)
        except queue.Empty:
            pass
        root.after(100, poll)

    def start():
        src, dst = v_src.get().strip(), v_dst.get().strip()
        if not src:
            messagebox.showwarning(APP_NAME, "请先选择输入目录")
            return
        if not dst:
            dst = str(default_output_dir(src))
            v_dst.set(dst)
        err = check_dirs(src, dst)
        if err:
            messagebox.showwarning(APP_NAME, err)
            return
        txt.delete("1.0", "end")
        pb.configure(value=0)
        btn_start.configure(state="disabled")
        btn_stop.configure(state="normal")
        stop = threading.Event()
        state["stop"] = stop
        rec, att = v_rec.get(), v_att.get()

        def work():
            try:
                res = run_strip(src, dst, rec, att,
                                progress=lambda i, n: q.put(("prog", (i, n))),
                                log=lambda s: q.put(("log", s)), stop_event=stop)
                q.put(("done", res))
            except Exception as ex:
                q.put(("done", ex))

        t = threading.Thread(target=work, daemon=True)
        state["worker"] = t
        t.start()

    btn_start.configure(command=start)
    btn_stop.configure(command=lambda: state["stop"] and state["stop"].set())

    # ---- 页签 2：空三 ----
    tab2 = ttk.Frame(nb, padding=10)
    nb.add(tab2, text="空三")
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
        ttk.Label(tab2, text=label).grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(tab2, textvariable=var).grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(tab2, text="浏览…", command=cmd).grid(row=r, column=2, padx=(6, 0), pady=3)

    o1 = ttk.Frame(tab2)
    o1.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 2))
    ttk.Checkbutton(o1, text="包含子目录：每个含照片的子目录单独一个工程", variable=v_arec).pack(side="left")
    ttk.Checkbutton(o1, text="照片带定位时先自动去定位", variable=v_auto).pack(side="left", padx=(16, 0))
    o2 = ttk.Frame(tab2)
    o2.grid(row=4, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Label(o2, text="精度：").pack(side="left")
    ttk.Combobox(o2, textvariable=v_acc, values=("高", "中", "低"), width=4, state="readonly").pack(side="left")
    ttk.Label(o2, text="    关键点上限：").pack(side="left")
    ttk.Spinbox(o2, textvariable=v_kp, from_=1000, to=200000, increment=1000, width=8).pack(side="left")
    ttk.Label(o2, text="    连接点上限：").pack(side="left")
    ttk.Spinbox(o2, textvariable=v_tp, from_=0, to=100000, increment=1000, width=8).pack(side="left")

    ttk.Label(tab2, foreground="#555", wraplength=int(700 * k),
              text="说明：需要 Metashape Pro（标准版不能运行脚本）。输出到「<照片目录名>_空三」：工程 .psx、"
                   "不带坐标系的 Blocks Exchange XML、空三报告.txt、Metashape 日志。结果是局部坐标、尺度任意。"
                   "输出位置留空时放在照片目录旁边；照片在只读盘上时请指定输出位置。").grid(
        row=5, column=0, columnspan=3, sticky="w", pady=(2, 6))

    bar2 = ttk.Frame(tab2)
    bar2.grid(row=6, column=0, columnspan=3, sticky="ew")
    bar2.columnconfigure(0, weight=1)
    pb2 = ttk.Progressbar(bar2, mode="determinate", maximum=1000)
    pb2.grid(row=0, column=0, sticky="ew")
    lbl2 = ttk.Label(bar2, text="", width=24)
    lbl2.grid(row=0, column=1, padx=6)
    btn2_start = ttk.Button(bar2, text="开始")
    btn2_start.grid(row=0, column=2)
    btn2_stop = ttk.Button(bar2, text="取消", state="disabled")
    btn2_stop.grid(row=0, column=3, padx=(6, 0))

    logf2 = ttk.Frame(tab2)
    logf2.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
    tab2.rowconfigure(7, weight=1)
    txt2 = tk.Text(logf2, height=10, wrap="none")
    sb2 = ttk.Scrollbar(logf2, orient="vertical", command=txt2.yview)
    txt2.configure(yscrollcommand=sb2.set)
    txt2.pack(side="left", fill="both", expand=True)
    sb2.pack(side="right", fill="y")

    def poll2():
        try:
            while True:
                kind, val = q2.get_nowait()
                if kind == "log":
                    txt2.insert("end", val + "\n")
                    txt2.see("end")
                elif kind == "prog":
                    pb2.configure(value=int(val[0] * 1000))
                    lbl2.configure(text=val[1][:30])
                elif kind == "done":
                    btn2_start.configure(state="normal")
                    btn2_stop.configure(state="disabled")
                    if isinstance(val, AlignCancelled):
                        lbl2.configure(text="已取消")
                        messagebox.showwarning(APP_NAME, "已取消。输出目录里的结果不完整，不要使用。")
                    elif isinstance(val, Exception):
                        lbl2.configure(text="失败")
                        txt2.insert("end", "出错：%s\n" % val)
                        txt2.see("end")
                        messagebox.showerror(APP_NAME, str(val))
                    else:
                        ok = [r for r in val if r["status"] == "成功"]
                        lines = []
                        for r in val:
                            a = "，对齐 %s/%s" % (r["aligned"], r["total"]) if r["aligned"] is not None else ""
                            lines.append("%s%s：%s%s" % (r["status"], a, Path(r["out_dir"]).name,
                                                       "\n  " + r["reason"] if r["reason"] else ""))
                        full = len(ok) == len(val) and all(r["aligned"] == r["total"] for r in ok)
                        lbl2.configure(text="完成" if len(ok) == len(val) else "有失败")
                        (messagebox.showinfo if full else messagebox.showwarning)(
                            APP_NAME, "成功 %d / 共 %d 个工程\n\n%s" % (len(ok), len(val), "\n".join(lines)))
        except queue.Empty:
            pass
        root.after(200, poll2)

    def start2():
        ms, pdir, out = v_ms.get().strip(), v_pdir.get().strip(), v_out.get().strip()
        err = check_metashape(ms)
        if err:
            messagebox.showwarning(APP_NAME, err)
            return
        if not pdir:
            messagebox.showwarning(APP_NAME, "请先选择照片目录")
            return
        try:
            kp, tp = int(v_kp.get()), int(v_tp.get())
        except ValueError:
            messagebox.showwarning(APP_NAME, "关键点上限、连接点上限要填整数")
            return
        cfg = load_config()
        cfg["metashape"] = ms
        save_config(cfg)
        acc = {"高": "high", "中": "medium", "低": "low"}[v_acc.get()]
        txt2.delete("1.0", "end")
        pb2.configure(value=0)
        lbl2.configure(text="")
        btn2_start.configure(state="disabled")
        btn2_stop.configure(state="normal")
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

    # 后续功能在这里加新页签：nb.add(ttk.Frame(nb), text="...")
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
    if not argv:
        run_gui()
        return 0
    ap = argparse.ArgumentParser(prog="uav_photo_tool", description="%s v%s" % (APP_NAME, APP_VERSION))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("strip-gps", help="剔除定位信息并复制到新目录")
    p.add_argument("src", help="输入目录（只读）")
    p.add_argument("dst", nargs="?", help="输出目录（须为空，名字带 %s；默认 <源目录名>%s）" % (OUTPUT_SUFFIX, OUTPUT_SUFFIX))
    p.add_argument("--no-recursive", action="store_true", help="不处理子目录")
    p.add_argument("--strip-attitude", action="store_true", help="同时剔除姿态 Gimbal*/Flight*")
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
    sub.add_parser("check-update", help="查 GitHub 上有没有新版本（退出码 0 已是最新 / 10 有新版本 / 1 查询失败）")
    args = ap.parse_args(argv)
    if args.cmd == "align":
        return _cli_align(args)
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


if __name__ == "__main__":
    sys.exit(main())
