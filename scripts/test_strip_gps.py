# -*- coding: utf-8 -*-
"""「剔除定位」自检：逐张检查三条判据（定位剔净 / 图像未变 / 其他元数据未伤）。

双源交叉：
  - 源 A：本脚本自写的字节解析（不 import src/uav_photo_tool.py，避免同一份 bug 自证）。
  - 源 B：PIL 读取 EXIF（getexif / get_ifd）和像素解码比对（--pixels 时）。
  两边结论不一致记 UNKNOWN。

用法：
  python scripts/test_strip_gps.py <原片目录> <输出目录> [--strip-attitude] [--pixels] [--csv 报告.csv]
递归匹配：输出目录下按相对路径找对应原片。
"""

import argparse
import csv
import hashlib
import io
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

LOC_RE = re.compile(rb"drone-dji:(GpsLatitude|GpsLongitude|GpsLongtitude|AbsoluteAltitude|RelativeAltitude"
                    rb"|GpsStatus|AltitudeType|Rtk\w*|LRFTarget\w*)\b", re.I)
ATT_RE = re.compile(rb"drone-dji:(Gimbal\w*|Flight\w*)\b", re.I)
ATTR_RE = re.compile(rb"drone-dji:(\w+)=\"([^\"]*)\"")
ELEM_RE = re.compile(rb"<drone-dji:(\w+)>([^<]*)</drone-dji:\1>")
KEEP_EXIF = {0x9003: "DateTimeOriginal", 0x010F: "Make", 0x0110: "Model", 0x920A: "FocalLength", 0x829A: "ExposureTime"}
SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


def segments(b):
    i, segs = 2, []
    while b[i + 1] != 0xDA:
        if b[i + 1] == 0xFF:
            i += 1
            continue
        L = struct.unpack(">H", b[i + 2:i + 4])[0]
        segs.append((b[i + 1], i, i + 2 + L))
        i += 2 + L
    return segs, i


def find_app1(b, header):
    segs, _ = segments(b)
    for m, s, e in segs:
        if m == 0xE1 and b[s + 4:s + 4 + len(header)] == header:
            return s, e
    return None


class Tiff:
    def __init__(self, b):
        r = find_app1(b, b"Exif\x00\x00")
        self.ok = r is not None
        if not self.ok:
            return
        self.base, self.end = r[0] + 10, r[1]
        self.t = b[self.base:self.end]
        self.e = "<" if self.t[:2] == b"II" else ">"

    def ifd(self, off):
        t, e = self.t, self.e
        n = struct.unpack(e + "H", t[off:off + 2])[0]
        ents = {}
        for k in range(n):
            q = off + 2 + 12 * k
            tag, typ, cnt = struct.unpack(e + "HHI", t[q:q + 8])
            size = SIZES.get(typ, 1) * cnt
            if size <= 4:
                raw = t[q + 8:q + 8 + size]
            else:
                vo = struct.unpack(e + "I", t[q + 8:q + 12])[0]
                raw = t[vo:vo + size]
            ents[tag] = (typ, cnt, raw, t[q + 8:q + 12])
        nxt = struct.unpack(e + "I", t[off + 2 + 12 * n:off + 6 + 12 * n])[0]
        return ents, nxt

    def ifd0(self):
        return self.ifd(struct.unpack(self.e + "I", self.t[4:8])[0])


def xmp_packet(b):
    r = find_app1(b, b"http://ns.adobe.com/xap/1.0/\x00")
    return b[r[0] + 4 + 29:r[1]] if r else None


def thumb_bytes(tf):
    """IFD1 原样字节 + 缩略图 JPEG 字节。"""
    _, nxt = tf.ifd0()
    if not nxt:
        return None
    ents, _ = tf.ifd(nxt)
    off = struct.unpack(tf.e + "I", ents[0x0201][2])[0]
    ln = struct.unpack(tf.e + "I", ents[0x0202][2])[0]
    return {k: (v[0], v[1], v[2]) for k, v in ents.items()}, tf.t[off:off + ln]


def pil_has_gps(b):
    im = Image.open(io.BytesIO(b))
    ex = im.getexif()
    return 0x8825 in ex or bool(ex.get_ifd(0x8825))


def check_one(orig_p, out_p, strip_att, pixels):
    o, n = orig_p.read_bytes(), out_p.read_bytes()
    fails, notes = [], []

    # ---- 判据 2：图像未变 ----
    if len(o) != len(n):
        fails.append("大小不等 %d≠%d" % (len(o), len(n)))
    _, sos_o = segments(o)
    _, sos_n = segments(n)
    if sos_o != sos_n or o[sos_o:] != n[sos_n:]:
        fails.append("SOS 之后字节不同")
    allowed = [find_app1(o, b"Exif\x00\x00"), find_app1(o, b"http://ns.adobe.com/xap/1.0/\x00")]
    if len(o) == len(n):
        diffs = [i for i in range(sos_o) if o[i] != n[i]]
        outside = [i for i in diffs if not any(r and r[0] + 4 <= i < r[1] for r in allowed)]
        if outside:
            fails.append("Exif/XMP 段外有 %d 字节不同（首个 @%d）" % (len(outside), outside[0]))
    if pixels:
        if Image.open(io.BytesIO(o)).tobytes() != Image.open(io.BytesIO(n)).tobytes():
            fails.append("PIL 像素不同")

    # ---- 判据 1：定位剔净 ----
    to, tn = Tiff(o), Tiff(n)
    gps_o = to.ok and 0x8825 in to.ifd0()[0]
    a_gps = tn.ok and 0x8825 in tn.ifd0()[0]
    b_gps = pil_has_gps(n)
    if a_gps != b_gps:
        fails.append("UNKNOWN：自写解析与 PIL 对 GPS 是否存在结论不一致")
    elif a_gps:
        fails.append("输出 IFD0 仍有 0x8825")
    xo, xn = xmp_packet(o), xmp_packet(n)
    if xn is not None:
        left = sorted(set(m.group(1).decode() for m in LOC_RE.finditer(xn)))
        if left:
            fails.append("XMP 残留位置字段 %s" % ",".join(left))
        if strip_att:
            left = sorted(set(m.group(1).decode() for m in ATT_RE.finditer(xn)))
            if left:
                fails.append("XMP 残留姿态字段 %s" % ",".join(left))
    needles = []
    if xo is not None:
        for k, v in ATTR_RE.findall(xo) + ELEM_RE.findall(xo):
            if k.lower() in (b"gpslatitude", b"gpslongitude", b"gpslongtitude"):
                needles.append(v.lstrip(b"+-"))
    if gps_o:
        g, _ = to.ifd(struct.unpack(to.e + "I", to.ifd0()[0][0x8825][3])[0])
        for tag in (2, 4):  # 纬度、经度有理数原始字节
            if tag in g:
                needles.append(g[tag][2])
    for nd in needles:
        if nd and nd in n:
            fails.append("输出里仍能搜到原定位 %r" % nd[:24])
    status = ""
    if xo is not None:
        m = re.search(rb'GpsStatus="([^"]*)"', xo)
        status = m.group(1).decode() if m else ""

    # ---- 判据 3：其他元数据未伤 ----
    if to.ok:
        e0, _ = to.ifd0()
        e1, _ = tn.ifd0()
        exo_ent = to.ifd(struct.unpack(to.e + "I", e0[0x8769][3])[0])[0] if 0x8769 in e0 else {}
        exn_ent = tn.ifd(struct.unpack(tn.e + "I", e1[0x8769][3])[0])[0] if 0x8769 in e1 else {}
        for tag, name in KEEP_EXIF.items():
            vo = (e0.get(tag) or exo_ent.get(tag) or (None, None, None))[2]
            vn = (e1.get(tag) or exn_ent.get(tag) or (None, None, None))[2]
            if vo != vn:
                fails.append("EXIF %s 变了" % name)
        for tag in e0:
            if tag != 0x8825 and (e0[tag][:3] != e1.get(tag, (None,) * 3)[:3]):
                fails.append("IFD0 标签 0x%04X 变了" % tag)
        tbo, tbn = thumb_bytes(to), thumb_bytes(tn)
        if tbo != tbn:
            fails.append("缩略图 IFD1 变了")
        # PIL 交叉：缩略图仍能解码、DateTimeOriginal 一致
        im_o, im_n = Image.open(io.BytesIO(o)), Image.open(io.BytesIO(n))
        if im_o.getexif().get_ifd(0x8769).get(0x9003) != im_n.getexif().get_ifd(0x8769).get(0x9003):
            fails.append("PIL：DateTimeOriginal 不一致")
        if tbn:
            try:
                Image.open(io.BytesIO(tbn[1])).load()
            except Exception as ex:
                fails.append("缩略图无法解码 %s" % ex)
    if xn is not None:
        try:
            ET.fromstring(xn)
        except ET.ParseError as ex:
            fails.append("XMP 不能被 xml.etree 解析：%s" % ex)
        ao = dict(ATTR_RE.findall(xo) + ELEM_RE.findall(xo))
        an = dict(ATTR_RE.findall(xn) + ELEM_RE.findall(xn))
        for k, v in ao.items():
            loc = LOC_RE.match(b"drone-dji:" + k)
            att = ATT_RE.match(b"drone-dji:" + k)
            if loc or (att and strip_att):
                continue
            if an.get(k) != v:
                fails.append("XMP 保留字段 %s 变了" % k.decode())
    return fails, {"orig_gps": gps_o, "status": status, "needles": len(needles)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("orig")
    ap.add_argument("out")
    ap.add_argument("--strip-attitude", action="store_true")
    ap.add_argument("--pixels", action="store_true", help="另用 PIL 解码比较像素（慢）")
    ap.add_argument("--csv")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    orig, out = Path(a.orig), Path(a.out)
    rows, nfail, n = [], 0, 0
    for op in sorted(p for p in orig.rglob("*") if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")):
        rel = op.relative_to(orig)
        np_ = out / rel
        n += 1
        if not np_.exists():
            fails, info = ["输出缺失"], {}
        else:
            try:
                fails, info = check_one(op, np_, a.strip_attitude, a.pixels)
            except Exception as ex:
                fails, info = ["检查异常 %s: %s" % (type(ex).__name__, ex)], {}
        verdict = "UNKNOWN" if any(f.startswith("UNKNOWN") for f in fails) else ("通过" if not fails else "不通过")
        nfail += verdict != "通过"
        rows.append([str(rel), verdict, info.get("status", ""), info.get("orig_gps", ""), info.get("needles", ""), "；".join(fails)])
        print("%-40s %-6s %s" % (rel, verdict, "；".join(fails)))
    print("合计 %d 张：通过 %d，不通过/UNKNOWN %d" % (n, n - nfail, nfail))
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["相对路径", "结论", "原GpsStatus", "原EXIF有GPS", "搜索的原定位串数", "问题"])
            w.writerows(rows)
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
