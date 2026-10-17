# -*- coding: utf-8 -*-
"""无人机照片处理工具（单文件，只用标准库 + tkinter）。

功能页签：
  1. 剔除定位：批量剔除 JPG 里的定位信息（EXIF GPS + DJI XMP 位置字段），另存到新目录。
  2. 空三：调用用户电脑上的 Metashape Pro 对齐照片，导出不带坐标系的 Blocks Exchange XML。
  3. 控制点（D-16、D-17）：①迁移：新一期照片和已有控制点的参考期联合匹配，零刺点自动放点、细化、平差、判定；
     ②建参考期：空三工程 + 控制点 CSV（+ CC 刺点），自动判 X/Y 是否对调，再细化、平差、判定。
     迁移时细化把点挪得太远（D-20）就自动排除该点；也可以手动排除。导出的 XML 写 CC 的 Local:unit=meter（D-19）。

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
  python uav_photo_tool.py check-update
不带参数启动图形界面（菜单「帮助」里可以检查更新）。
退出码：0 成功（控制点＝自动通过）/ 1 有失败 / 2 拒绝执行 / 3 已取消 / 4 控制点需人工核对 / 10 有新版本（check-update）。
"""

import argparse
import csv
import datetime
import fnmatch
import hashlib
import stat
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
import zipfile
from pathlib import Path

APP_NAME = "无人机照片工具"
APP_VERSION = "0.8"
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
                                stdout=fcon, stderr=subprocess.STDOUT, cwd=str(tmp),
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
                    "耗时分钟", "报告")
BATCH_WAIT_POLL = 30  # 秒：本机有别的 Metashape 在跑时，隔这么久再看一次


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
    """扫描：每期一个 dict（序号、期次、规则、照片数、参考期、排除点、状态 = 待运行 / 已有结果 / 跳过：原因）。"""
    if not root or not Path(root).is_dir():
        raise ValueError("照片上级目录不存在：%s" % root)
    items = []
    for i, (name, d, n) in enumerate(batch_period_dirs(root), 1):
        rule = batch_match_rule(name, rules)
        it = {"idx": i, "name": name, "dir": str(d), "n": n, "rule": rule["key"] if rule else "", "ref": "", "exclude": "",
              "status": "待运行", "report": ""}
        if name in skip:
            it["status"] = "跳过：在 --skip 里"
        elif rule is None:
            it["status"] = "跳过：没有匹配的规则"
        elif not rule["psx"]:
            it["status"] = "跳过：规则「%s」没填参考期" % rule["key"]
        else:
            it["ref"], it["exclude"] = rule["psx"], rule["exclude"]
        rep = batch_find_report(out_dir, name) if out_dir else None
        if rep:
            it["report"] = str(rep)
            if it["status"] == "待运行":
                it["status"] = "已有结果"
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
                  summary=None, wait_poll=BATCH_WAIT_POLL, **timeouts):
    """按顺序处理 items（batch_scan 的结果里选中的那些）：状态不是「待运行」的照写一行；待运行的串行调 run_gcp_transfer。
    每期写一次汇总 CSV。on_item(item, 阶段, 文字)：阶段 wait/start/done。取消时当前这一期按 stop_event 停下，
    剩下的不开始。返回 {"rows", "summary", "cancelled", "exit", "counts"}；
    exit：0 全部自动通过或跳过 / 4 有需人工核对 / 1 有失败 / 3 取消。"""
    log = log or (lambda s: None)
    progress = progress or (lambda f, s: None)
    on_item = on_item or (lambda it, phase, text: None)
    stopped = lambda: stop_event is not None and stop_event.is_set()
    out_dir = Path(os.path.abspath(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = Path(summary) if summary else out_dir / ("批量迁移汇总_%s.csv" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    rows, cancelled = [], False
    log("—— 批量迁移：选中 %d 期，其中待运行 %d 期%s" % (len(items), sum(it["status"] == "待运行" for it in items),
                                                "（只列计划）" if plan else ""))
    log("输出目录：%s" % out_dir)
    write_batch_summary(summary, rows)
    for pos, it in enumerate(items, 1):
        tag = "[%d/%d · 序号 %d] %s" % (pos, len(items), it["idx"], it["name"])
        row = _batch_row(it)
        if it["status"] == "待运行" and not plan:
            rep = batch_find_report(out_dir, it["name"])  # 开跑前再看一次：上一期跑的时候别的电脑可能已经做完
            if rep:
                it["status"], it["report"] = "已有结果", str(rep)
        if it["status"] != "待运行":
            row["状态"] = "跳过：已有结果" if it["status"] == "已有结果" else it["status"]
            if it["report"]:
                _fill_from_report(row, it["report"])
            log("%s → %s" % (tag, row["状态"]))
            on_item(it, "done", it["status"])
        elif plan:
            row["状态"] = "计划运行"
            log("%s（%s，%d 张）→ 会用参考期 %s%s" % (tag, it["rule"], it["n"], row["参考期"],
                                               "，排除点 %s" % it["exclude"] if it["exclude"] else ""))
        else:
            waited = False
            while not stopped() and other_metashape_running():
                if not waited:
                    log("%s：本机已有别的 Metashape 在跑，等它结束（每 %d 秒看一次）… %s" % (
                        tag, wait_poll, datetime.datetime.now().strftime("%H:%M")))
                    on_item(it, "wait", "等待其他 Metashape")
                    progress(0.0, "等待其他 Metashape")
                    waited = True
                if stop_event is not None:
                    stop_event.wait(wait_poll)
                else:
                    time.sleep(wait_poll)
            if stopped():
                cancelled = True
                on_item(it, "done", "待运行")
                log("已取消：%s 及之后的 %d 期都没有开始" % (it["name"], len(items) - pos + 1))
                break
            log("%s → 开始（参考期 %s%s） %s" % (tag, row["参考期"], "，排除点 %s" % it["exclude"] if it["exclude"] else "",
                                         datetime.datetime.now().strftime("%H:%M")))
            on_item(it, "start", "运行中")
            t0 = time.time()
            res, code = None, 1
            with open(out_dir / ("%s_运行日志.txt" % it["name"]), "a", encoding="utf-8") as flog:
                def plog(s):
                    log(s)
                    flog.write(s + "\n")
                    flog.flush()

                try:
                    # 上次已经去定位过（输出目录里有 <名>_去定位），就直接用它，避免工具因去定位目录非空而拒绝
                    photo_dir = it["dir"]
                    stripped = out_dir / (it["name"] + OUTPUT_SUFFIX)
                    if stripped.is_dir() and any(stripped.iterdir()):
                        n = _count_jpegs(stripped)
                        if n != it["n"]:
                            raise ValueError("上次去定位没做完（%s 里 %d 张，原目录 %d 张）：删掉这个目录后重跑" % (
                                stripped, n, it["n"]))
                        photo_dir = str(stripped)
                        plog("  用已去定位的照片：%s" % stripped)
                    res = run_gcp_transfer(photo_dir, it["ref"], metashape, out_dir, log=plog, stop_event=stop_event,
                                           progress=progress, exclude=it["exclude"], **timeouts)
                    code = {"自动通过": 0, "需人工核对": 4, "已取消": 3}.get(res["status"], 1)
                except ValueError as ex:
                    plog("拒绝执行：%s" % ex)
                    code = 2
                except (AlignCancelled, KeyboardInterrupt):
                    plog("已取消")
                    code = 3
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
    states = set(counts)
    code = 3 if cancelled else 1 if states & {"失败", "拒绝"} else 4 if "需人工核对" in states else 0
    return {"rows": rows, "summary": str(summary), "cancelled": cancelled, "exit": code, "counts": counts}


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
        messagebox.showerror(APP_NAME, "发生未处理错误，界面已恢复：\n%s" % "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    root.report_callback_exception = gui_exception
    root.geometry("820x620")  # CTk 按屏幕缩放自动放大
    root.minsize(700, 540)

    def button(parent, text, command=None, primary=False, **kw):
        kw.setdefault("width", 72 if text == "浏览…" else 88)
        return ctk.CTkButton(parent, text=text, command=command, **({} if primary else secondary), **kw)

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
        else:
            w.configure(state="normal" if on else "disabled")

    def log_box(parent, height):
        return ctk.CTkTextbox(parent, height=height, wrap="none", font=ctk.CTkFont(size=12))

    def nbsp(s):  # Tk 只在空格处折行，中文长句会整段挤到下一行；换成不换行空格后按字折行
        return s.replace(" ", " ")

    def hint(parent, text=""):
        lbl = ctk.CTkLabel(parent, text=nbsp(text), text_color=hint_color, wraplength=640, justify="left", anchor="w")
        parent.bind("<Configure>", lambda ev: lbl.configure(
            wraplength=max(300, int(ev.width / lbl._get_widget_scaling()) - 24)), add="+")
        return lbl

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
    m_help = tk.Menu(menubar, tearoff=0)
    m_help.add_command(label="检查更新", command=lambda: start_update_check(True))
    m_help.add_checkbutton(label="启动时自动检查更新", variable=var_auto_update, command=toggle_auto_update)
    m_help.add_separator()
    m_help.add_command(label="项目主页（GitHub）", command=lambda: webbrowser.open(HOMEPAGE_URL))
    m_help.add_command(label="关于", command=lambda: messagebox.showinfo(
        APP_NAME, "%s v%s\n开源许可：MIT\n界面库：CustomTkinter（MIT）\n%s" % (APP_NAME, APP_VERSION, HOMEPAGE_URL)))
    menubar.add_cascade(label="帮助", menu=m_help)
    root.config(menu=menubar)
    if var_auto_update.get():
        root.after(1500, start_update_check)

    nb = ctk.CTkTabview(root, anchor="nw")
    nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ---- 页签 1：剔除定位 ----
    tab = nb.add("剔除定位")
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
    btn_stop = button(bar, "中止", state="disabled")
    btn_stop.grid(row=0, column=3, padx=(6, 0))
    btn_restore = button(bar, "从备份还原", width=100)
    btn_restore.grid(row=0, column=4, padx=(6, 0))

    txt = log_box(tab, 220)
    txt.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
    tab.rowconfigure(5, weight=1)

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
                    pb.set(i / max(n, 1))
                    lbl_prog.configure(text="%d / %d" % (i, n))
                elif kind == "done":
                    btn_start.configure(state="normal")
                    btn_stop.configure(state="disabled")
                    if isinstance(val, Exception):
                        append_log("出错：%s" % val)
                        messagebox.showerror(APP_NAME, str(val))
                    else:
                        residual_count = val.get("残留", 0)
                        msg = "完成：成功 %d，跳过 %d，失败 %d 张、残留 %d 个文件\n日志路径：%s" % (val["成功"], val["跳过"], val.get("失败", 0), residual_count, val["日志"])
                        (messagebox.showwarning if val.get("失败", 0) or residual_count else messagebox.showinfo)(APP_NAME, msg)
        except queue.Empty:
            pass
        root.after(100, poll)

    def ask_inplace_confirm(count):
        """原地抹除确认框（警示色）：须输入照片目录名；取消/关窗返回 None，确认返回输入的文字（同 askstring）。"""
        dlg = ctk.CTkToplevel(root)
        dlg.title(APP_NAME + " - 警告：不可恢复")
        dlg.resizable(False, False)
        dlg.transient(root)
        result = {"text": None}
        v_name = tk.StringVar()
        ctk.CTkLabel(dlg, text="⚠ 警告：不可恢复！", text_color=danger,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(dlg, text="会直接修改 %d 张原照片，定位信息删除后不能从照片里找回。" % count, text_color=danger,
                     font=ctk.CTkFont(weight="bold"), wraplength=440, justify="left").pack(anchor="w", padx=20, pady=4)
        ctk.CTkLabel(dlg, text="请输入照片目录的名字（不是完整路径）以确认：").pack(anchor="w", padx=20, pady=(8, 2))
        ent = ctk.CTkEntry(dlg, textvariable=v_name, width=440, border_color=danger)
        ent.pack(padx=20, pady=(0, 12))

        def ok():
            result["text"] = v_name.get()
            dlg.destroy()

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 18))
        button(row, "取消", dlg.destroy).pack(side="right")
        ctk.CTkButton(row, text="确认抹除", width=100, command=ok, fg_color=danger,
                      hover_color=danger_hover).pack(side="right", padx=(0, 8))
        ent.bind("<Return>", lambda ev: ok())
        dlg.bind("<Escape>", lambda ev: dlg.destroy())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.after(150, lambda: (dlg.lift(), dlg.focus_force(), ent.focus_set()))
        try:
            dlg.wait_visibility()
            dlg.grab_set()
        except tk.TclError:
            pass
        root.wait_window(dlg)
        return result["text"]

    def start():
        src, dst = v_src.get().strip(), v_dst.get().strip()
        if not src:
            messagebox.showwarning(APP_NAME, "请先选择输入目录")
            return
        rec, att, inplace, backup = v_rec.get(), v_att.get(), v_inplace.get(), v_backup.get()
        confirm = None
        if inplace:
            count = sum(1 for p in collect_files(src, rec) if p.suffix.lower() in JPEG_EXTS)
            if not v_backup.get():
                if not messagebox.askyesno(APP_NAME + " - 警告：不可恢复", "已关闭定位备份，抹除后无法还原。仍要继续吗？"):
                    return
            confirm = ask_inplace_confirm(count)
            if confirm is None:
                return
            if confirm != Path(src).name:
                messagebox.showwarning(APP_NAME, "确认目录名不正确，未修改任何文件")
                return
        else:
            if not dst:
                dst = str(default_output_dir(src)); v_dst.set(dst)
            err = check_dirs(src, dst)
            if err:
                messagebox.showwarning(APP_NAME, err); return
        txt.delete("1.0", "end"); pb.set(0)
        btn_start.configure(state="disabled"); btn_stop.configure(state="normal")
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
            messagebox.showwarning(APP_NAME, "请先选择照片目录"); return
        backup = filedialog.askopenfilename(title="选择定位备份", filetypes=[("ZIP", "*.zip")])
        if not backup: return
        if not messagebox.askyesno(APP_NAME + " - 确认还原", "将直接修改原照片并写回定位信息，确定继续吗？"):
            return
        try:
            res = restore_gps(src, backup, v_rec.get(), log=append_log)
            msg = "还原完成：成功 %d，跳过 %d，失败 %d\n日志：%s" % (res["成功"], res["跳过"], res["失败"], res["日志"])
            (messagebox.showwarning if res["失败"] else messagebox.showinfo)(APP_NAME, msg)
        except Exception as ex:
            messagebox.showerror(APP_NAME, str(ex))
    btn_start.configure(command=start)
    btn_stop.configure(command=lambda: state["stop"] and state["stop"].set())
    btn_restore.configure(command=restore_start)

    # ---- 页签 2：空三 ----
    tab2 = nb.add("空三")
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
    btn2_stop = button(bar2, "取消", state="disabled")
    btn2_stop.grid(row=0, column=3, padx=(6, 0))

    txt2 = log_box(tab2, 180)
    txt2.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
    tab2.rowconfigure(7, weight=1)

    def poll2():
        try:
            while True:
                kind, val = q2.get_nowait()
                if kind == "log":
                    txt2.insert("end", val + "\n")
                    txt2.see("end")
                elif kind == "prog":
                    pb2.set(int(val[0] * 1000) / 1000)
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
        pb2.set(0)
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

    # ---- 页签 3：控制点（D-16、D-17） ----
    tab3 = nb.add("控制点")
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
        lbl3_help.configure(text=nbsp(helps[m]))

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
    btn3_stop = button(bar3, "取消", state="disabled")
    btn3_stop.grid(row=0, column=3, padx=(6, 0))
    txt3 = log_box(tab3, 110)
    txt3.grid(row=12, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
    tab3.rowconfigure(12, weight=1)

    def poll3():
        try:
            while True:
                kind, val = q3.get_nowait()
                if kind == "log":
                    txt3.insert("end", val + "\n")
                    txt3.see("end")
                elif kind == "prog":
                    pb3.set(int(val[0] * 1000) / 1000)
                    lbl3.configure(text=val[1][:30])
                elif kind == "done":
                    btn3_start.configure(state="normal")
                    btn3_stop.configure(state="disabled")
                    if isinstance(val, AlignCancelled):
                        lbl3.configure(text="已取消")
                        messagebox.showwarning(APP_NAME, "已取消。输出目录里的结果不完整，不要使用。")
                    elif isinstance(val, Exception):
                        lbl3.configure(text="失败")
                        txt3.insert("end", "出错：%s\n" % val)
                        txt3.see("end")
                        messagebox.showerror(APP_NAME, str(val))
                    else:
                        r = val
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
                            messagebox.showinfo(APP_NAME, "\n".join([head] + body))
                        elif r["status"] in ("自动通过", "需人工核对"):
                            extra = ["%s 号：%s" % (n, "；".join(w)) for n, w in r["bad"]]
                            if extra:
                                extra.insert(0, "\n需人工核对的点（处理步骤见 控制点报告.txt）：")
                            messagebox.showwarning(APP_NAME + " - " + r["status"], "\n".join([head] + body + extra))
                        else:
                            messagebox.showerror(APP_NAME, "%s\n%s\n\n%s" % (head, r["reason"], "\n".join(body)))
        except queue.Empty:
            pass
        root.after(200, poll3)

    def start3():
        mode = v3_mode.get()
        ms = v3_ms.get().strip()
        err = check_metashape(ms)
        if err:
            messagebox.showwarning(APP_NAME, err)
            return
        try:
            acc = _check_gcp_accuracy(v3_acc.get().strip())
            shift = _check_max_shift(v3_shift.get().strip())
        except ValueError as ex:
            messagebox.showwarning(APP_NAME, str(ex))
            return
        excl = parse_exclude(v3_excl.get())
        ref, pdir, psx, csvp, ccp, out = (v.get().strip() for v in (v3_ref, v3_pdir, v3_psx, v3_csv, v3_cc, v3_out))
        if mode == "transfer" and (not ref or not pdir):
            messagebox.showwarning(APP_NAME, "请选择参考期工程和新一期照片目录")
            return
        if mode == "init" and (not psx or not csvp):
            messagebox.showwarning(APP_NAME, "请选择空三工程和控制点 CSV")
            return
        cfg = load_config()
        cfg["metashape"] = ms
        if mode == "transfer":
            cfg["gcp_ref"] = ref
        else:
            cfg["gcp_csv"] = csvp
        save_config(cfg)
        txt3.delete("1.0", "end")
        pb3.set(0)
        lbl3.configure(text="")
        btn3_start.configure(state="disabled")
        btn3_stop.configure(state="normal")
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
    tab4 = nb.add("批量迁移")
    tab4.columnconfigure(1, weight=1)
    cfg4 = load_config()
    v4_root = tk.StringVar(value=cfg4.get("batch_root", ""))
    v4_out = tk.StringVar(value=cfg4.get("batch_out", ""))
    v4_ms = tk.StringVar(value=find_metashape())
    v4_filter = tk.StringVar()
    st4 = {"stop": None, "items": [], "rows": [], "scanned": None}
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

    def regrid_rules():
        for i, r in enumerate(rules4, 1):
            for c, w in enumerate(r["widgets"]):
                w.grid(row=i, column=c, sticky="ew" if c == 1 else "w", padx=(6, 4) if c == 0 else (0, 4), pady=(0, 3))

    def add_rule(key="", psx="", exclude=""):
        r = {"key": tk.StringVar(value=key), "psx": tk.StringVar(value=psx), "exclude": tk.StringVar(value=exclude)}
        r["widgets"] = (
            ctk.CTkEntry(rf, textvariable=r["key"], width=80),
            ctk.CTkEntry(rf, textvariable=r["psx"]),
            button(rf, "浏览…", lambda: pick_file(r["psx"], "选择规则「%s」的参考期工程" % r["key"].get(), psx_types)),
            ctk.CTkEntry(rf, textvariable=r["exclude"], width=60),
            button(rf, "删除", lambda: del_rule(r), width=52),
        )
        rules4.append(r)
        regrid_rules()

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
    hint(tab4, "直接含照片的子目录算一期。已有「控制点报告.txt」的期跳过，已有「<名>_去定位」的直接用。勾上且「待运行」的期"
               "一期接一期地跑，本机有别的 Metashape 在跑就先等。筛选只影响显示；四个勾选按钮只作用于当前显示的行。"
               ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 2))

    tb4 = ctk.CTkFrame(tab4, fg_color="transparent")
    tb4.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(2, 2))
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

    lst4 = ctk.CTkScrollableFrame(tab4, height=150)
    lst4.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(2, 4))
    lst4.columnconfigure(6, weight=1)
    tab4.rowconfigure(6, weight=1)
    cols4 = (("", 28, "w"), ("序号", 36, "e"), ("期次", 150, "w"), ("匹配规则", 64, "w"), ("照片数", 52, "e"),
             ("参考期", 210, "w"), ("状态", 120, "w"))
    for c, (t, w, a) in enumerate(cols4):
        if t:
            ctk.CTkLabel(lst4, text=t, width=w, anchor=a, text_color=hint_color, height=20).grid(row=0, column=c, sticky="ew",
                                                                                             padx=(0, 6))
    status_color = {"自动通过": ("#2E7D32", "#81C784"), "需人工核对": ("#E65100", "#FFB74D"), "失败": danger,
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
            len(sel), sum(r["item"]["status"] == "待运行" for r in sel), sum(visible(r) for r in rs), len(rs)))

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
                              "只勾待运行": r["item"]["status"] == "待运行"}[kind])
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
            var = tk.BooleanVar(value=it["status"] == "待运行")
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
                json.dumps(rules_now(), ensure_ascii=False))

    def save4():
        cfg = load_config()
        cfg.update(batch_root=v4_root.get().strip(), batch_out=v4_out.get().strip(), batch_rules=rules_now())
        if v4_ms.get().strip():
            cfg["metashape"] = v4_ms.get().strip()
        save_config(cfg)

    def scan4():
        root_dir, out = v4_root.get().strip(), v4_out.get().strip()
        if not root_dir or not out:
            messagebox.showwarning(APP_NAME, "请选择照片上级目录和输出目录")
            return
        rules = rules_now()
        if not any(r["key"] for r in rules):
            messagebox.showwarning(APP_NAME, "规则表至少要有一条带关键词的规则")
            return
        errs = check_batch_rules(rules)
        if errs:
            messagebox.showwarning(APP_NAME, "\n".join(errs))
            return
        try:
            items = batch_scan(root_dir, rules, os.path.abspath(out))
        except (ValueError, OSError) as ex:
            messagebox.showwarning(APP_NAME, str(ex))
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
            q4.put(("log", "注意：这些期已有结果但不是自动通过，会跳过；想重跑就删掉输出目录里它的 _控制点 目录再扫描：%s"
                    % "、".join(odd)))

    btn4_scan.configure(command=scan4)

    bar4 = ctk.CTkFrame(tab4, fg_color="transparent")
    bar4.grid(row=7, column=0, columnspan=3, sticky="ew")
    bar4.columnconfigure(0, weight=1)
    pb4 = ctk.CTkProgressBar(bar4, mode="determinate")
    pb4.set(0)
    pb4.grid(row=0, column=0, sticky="ew")
    lbl4 = ctk.CTkLabel(bar4, text="", width=260)
    lbl4.grid(row=0, column=1, padx=6)
    btn4_start = button(bar4, "开始", primary=True)
    btn4_start.grid(row=0, column=2)
    btn4_stop = button(bar4, "取消", state="disabled")
    btn4_stop.grid(row=0, column=3, padx=(6, 0))
    txt4 = log_box(tab4, 70)
    txt4.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
    LOG4_KEEP = 1500  # 日志框只留最近这么多行，完整日志在输出目录的 批量迁移日志_<时间>.txt

    def poll4():
        try:
            for _ in range(500):  # 一次最多处理这么多条，别让界面卡住
                kind, val = q4.get_nowait()
                if kind == "log":
                    txt4.insert("end", val + "\n")
                    n = int(txt4.index("end-1c").split(".")[0])
                    if n > LOG4_KEEP:
                        txt4.delete("1.0", "%d.0" % (n - LOG4_KEEP))
                    txt4.see("end")
                elif kind == "prog":
                    pb4.set(int(val[0] * 1000) / 1000)
                    lbl4.configure(text=val[1][:36])
                elif kind == "item":
                    it, text = val
                    for r in st4["rows"]:
                        if r["item"] is it:
                            set_row_status(r, text)
                    update_count()
                elif kind == "done":
                    btn4_start.configure(state="normal")
                    btn4_scan.configure(state="normal")
                    btn4_stop.configure(state="disabled")
                    st4["scanned"] = None  # 状态都变了，再跑要先重新扫描
                    if isinstance(val, Exception):
                        lbl4.configure(text="失败")
                        txt4.insert("end", "出错：%s\n" % val)
                        txt4.see("end")
                        messagebox.showerror(APP_NAME, str(val))
                        continue
                    res, logf = val
                    body = ["%s：%d 期" % kv for kv in res["counts"].items()] + ["", "汇总表：%s" % res["summary"],
                                                                              "完整日志：%s" % logf]
                    if res["cancelled"]:
                        lbl4.configure(text="已取消")
                        last = res["rows"][-1] if res["rows"] else None
                        if last and last["状态"] == "已取消":
                            body.append("\n被取消的「%s」结果不完整，不要使用；要重跑就先删掉输出目录里它的 _控制点 目录。" % last["期次"])
                        messagebox.showwarning(APP_NAME + " - 已取消",
                                               "已取消：已完成的期次结果都保留，剩下的没有开始。\n\n" + "\n".join(body))
                    else:
                        lbl4.configure(text="完成")
                        bad = res["exit"] != 0
                        (messagebox.showwarning if bad else messagebox.showinfo)(
                            APP_NAME + " - 批量迁移完成", ("有需人工核对或失败的期次，详见汇总表和各期的控制点报告.txt。\n\n"
                                                    if bad else "") + "\n".join(body))
        except queue.Empty:
            pass
        root.after(200, poll4)

    def start4():
        if not st4["items"] or st4["scanned"] is None:
            messagebox.showwarning(APP_NAME, "请先点「扫描」")
            return
        if scan_key() != st4["scanned"]:
            messagebox.showwarning(APP_NAME, "目录或规则表在扫描后改过了，请先重新扫描")
            return
        ms = v4_ms.get().strip()
        err = check_metashape(ms)
        if err:
            messagebox.showwarning(APP_NAME, err)
            return
        chosen = [r["item"] for r in st4["rows"] if r["var"].get()]
        if not any(it["status"] == "待运行" for it in chosen):
            messagebox.showwarning(APP_NAME, "勾选的期次里没有「待运行」的")
            return
        save4()
        out = os.path.abspath(v4_out.get().strip())
        txt4.delete("1.0", "end")
        pb4.set(0)
        lbl4.configure(text="")
        btn4_start.configure(state="disabled")
        btn4_scan.configure(state="disabled")
        btn4_stop.configure(state="normal")
        stop = threading.Event()
        st4["stop"] = stop
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

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
                                        summary=Path(out) / ("批量迁移汇总_%s.csv" % stamp))
                q4.put(("done", (res, str(logf))))
            except Exception as ex:
                q4.put(("done", ex))

        threading.Thread(target=work, daemon=True).start()

    def stop4():
        if st4["stop"] is not None:
            st4["stop"].set()
            lbl4.configure(text="正在取消…（当前这一期停下，剩下的不开始）")

    btn4_start.configure(command=start4)
    btn4_stop.configure(command=stop4)
    root.after(200, poll4)

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
    bt.add_argument("--skip", default="", help="不处理的期次名，逗号分隔（如参考期本身）")
    bt.add_argument("--plan", action="store_true", help="只列计划、写汇总表，不运行")
    bt.add_argument("--metashape", help="metashape.exe 路径（默认用上次界面里记的或常见安装位置）")
    bt.add_argument("--log", help="把日志另存到这个文件（打包的 exe 没有控制台时用）")
    sub.add_parser("check-update", help="查 GitHub 上有没有新版本（退出码 0 已是最新 / 10 有新版本 / 1 查询失败）")
    args = ap.parse_args(argv)
    if args.cmd == "gcp-batch":
        return _cli_batch(args)
    if args.cmd == "align":
        return _cli_align(args)
    if args.cmd in ("gcp-transfer", "gcp-init"):
        return _cli_gcp(args)
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
        except ValueError as ex:
            log("拒绝执行：%s" % ex)
            return 2
        if not chosen:
            log("没有选中任何期次，结束。")
            return 0
        try:
            res = run_gcp_batch(chosen, args.out, ms, plan=args.plan, log=log)
        except KeyboardInterrupt:
            log("已取消")
            return 3
        return res["exit"]
    finally:
        if flog:
            flog.close()


if __name__ == "__main__":
    sys.exit(main())
