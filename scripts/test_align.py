# -*- coding: utf-8 -*-
"""检查「空三」输出目录是否满足验收判据（不 import 软件源码，自己重新解析）。

用法：
  python scripts/test_align.py <空三输出目录或其上级目录> [--expect-aligned N] [--metashape 路径]

  - 给的目录名以 _空三 结尾就只查它；否则递归找下面所有 *_空三 目录逐个查。
  - --expect-aligned N：对齐数必须 ≥ N（闸门 1，样例 26.08.01梯田 填 118）。
  - --metashape：另用 Metashape 只读打开 .psx，自己数 transform 不为空的相机，和报告/XML 对比。

每个目录检查：
  1. 文件齐全（.psx、.files、_BlocksExchange.xml、空三报告.txt、metashape_日志.txt），没有残留的 .tmp，没有「已取消.txt」。
  2. 「空三跑通」：报告状态=成功、Metashape 退出码 0、进度事件里有 done；日志第一行是 Metashape 版本。
  3. 脚本兜底：refs_off 事件里位置参考、姿态参考关完后仍开着的都是 0。
  4. 闸门 2：XML 能被 xml.etree 解析；<Photo> 数 = 对齐数；每个 ImagePath 文件存在；
     没有 <SRSId>、<SpatialReferenceSystems>、<Pose> 下的 <Metadata>；
     另查：每个 Photo 都有 Rotation+Center，连接点 Measurement 的 PhotoId 都能对上 Photo，未对齐清单条数 = 总数 − 对齐数。
  5. D-07：ImagePath 指向的照片不带定位（自写字节解析：EXIF IFD0 的 0x8825、XMP drone-dji 位置字段）。
退出码：全部通过 0，有不通过 1。
"""

import argparse
import json
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ALIGN_SUFFIX = "_空三"
XMP_LOC = re.compile(rb"drone-dji:(GpsLatitude|GpsLongitude|GpsLongtitude|AbsoluteAltitude|RelativeAltitude"
                     rb"|GpsStatus|AltitudeType|Rtk\w*|LRFTarget\w*)\s*(=|>)", re.I)


def has_location(path):
    """自写解析：返回 (有无 EXIF GPS 指针, XMP 位置字段个数)。"""
    data = Path(path).read_bytes()[:1024 * 1024]
    gps, xmp = False, 0
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        m = data[i + 1]
        if m == 0xDA:
            break
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        seg = data[i + 4:i + 2 + ln]
        if m == 0xE1 and seg.startswith(b"Exif\x00\x00"):
            t = seg[6:]
            e = "<" if t[:2] == b"II" else ">"
            off = struct.unpack(e + "I", t[4:8])[0]
            n = struct.unpack(e + "H", t[off:off + 2])[0]
            for k in range(n):
                if struct.unpack(e + "H", t[off + 2 + 12 * k:off + 4 + 12 * k])[0] == 0x8825:
                    gps = True
        elif m == 0xE1 and seg.startswith(b"http://ns.adobe.com/xap/1.0/"):
            xmp += len(XMP_LOC.findall(seg))
        i += 2 + ln
    return gps, xmp


MS_COUNT_SCRIPT = r'''
import sys, json
try:
    import Metashape
except ImportError:
    import PhotoScan as Metashape
a = sys.argv[-1]
p = json.load(open(a, encoding="utf-8"))
r = {}
try:
    d = Metashape.Document(); d.open(p["psx"], read_only=True)
    c = d.chunk
    r = {"total": len(c.cameras), "aligned": sum(1 for x in c.cameras if x.transform is not None),
         "version": Metashape.app.version}
except Exception as ex:
    r = {"error": repr(ex)}
json.dump(r, open(p["out"], "w", encoding="utf-8"))
Metashape.app.quit()
'''


def metashape_count(ms, psx):
    tmp = Path(tempfile.mkdtemp(prefix="test_align_"))
    (tmp / "s.py").write_text(MS_COUNT_SCRIPT, encoding="utf-8")
    (tmp / "p.json").write_text(json.dumps({"psx": str(psx), "out": str(tmp / "o.json")}, ensure_ascii=False), encoding="utf-8")
    subprocess.run([ms, "-r", str(tmp / "s.py"), str(tmp / "p.json")], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=600)
    return json.loads((tmp / "o.json").read_text(encoding="utf-8"))


def check_dir(d, expect, ms):
    fails, notes = [], []
    name = d.name[:-len(ALIGN_SUFFIX)]
    psx, files, xml = d / (name + ".psx"), d / (name + ".files"), d / (name + "_BlocksExchange.xml")
    rep, logf = d / "空三报告.txt", d / "metashape_日志.txt"
    for f in (psx, files, xml, rep, logf):
        if not f.exists():
            fails.append("缺文件 %s" % f.name)
    if (d / "已取消.txt").exists():
        fails.append("有「已取消.txt」")
    if list(d.glob("*.tmp")):
        fails.append("残留 .tmp：%s" % [p.name for p in d.glob("*.tmp")])
    if not rep.exists() or not logf.exists():
        return fails, notes

    rt = rep.read_text(encoding="utf-8")
    status = re.search(r"^状态：(.*)$", rt, re.M)
    status = status.group(1).strip() if status else "?"
    if status != "成功":
        fails.append("报告状态=%s" % status)
    m = re.search(r"^对齐：(\d+)/(\d+)", rt, re.M)
    aligned = total = None
    if m:
        aligned, total = int(m.group(1)), int(m.group(2))
        notes.append("报告对齐 %d/%d" % (aligned, total))
    else:
        fails.append("报告里没有对齐数")
    if expect is not None and (aligned is None or aligned < expect):
        fails.append("闸门1：对齐 %s < %d" % (aligned, expect))
    if not re.search(r"Metashape 退出码：0；完成标记：是", rt):
        fails.append("报告里退出码不是 0 或没有完成标记")
    unal = re.findall(r"^  (\S+\.(?:jpg|jpeg))$", rt, re.M | re.I)
    if aligned is not None and len(unal) != total - aligned:
        fails.append("未对齐清单 %d 条 ≠ %d" % (len(unal), total - aligned))

    lt = logf.read_text(encoding="utf-8").splitlines()
    if not lt or not lt[0].startswith("Metashape 版本：") or "未知" in lt[0]:
        fails.append("日志第一行不是 Metashape 版本")
    else:
        notes.append(lt[0])
    evs = []
    for line in lt:
        if line.startswith("{"):
            try:
                evs.append(json.loads(line))
            except ValueError:
                pass
    kinds = [e.get("ev") for e in evs]
    if "done" not in kinds:
        fails.append("进度事件里没有 done")
    if not re.search(r"^退出码：0$", "\n".join(lt[:4]), re.M):
        fails.append("日志里退出码不是 0")
    refs = [e for e in evs if e.get("ev") == "refs_off"]
    if not refs:
        fails.append("没有 refs_off 事件（脚本没关参考？）")
    elif refs[0]["location_on"] or refs[0]["rotation_on"] or (total is not None and refs[0]["total"] != total):
        fails.append("参考没关干净：%s" % refs[0])
    al = [e for e in evs if e.get("ev") == "aligned"]
    if al and (al[0]["aligned"] != aligned or al[0]["total"] != total):
        fails.append("事件对齐数 %s/%s 与报告不一致" % (al[0]["aligned"], al[0]["total"]))

    if xml.exists():
        photos, ids, tie, meas_bad, no_pose, missing, bad = 0, set(), 0, 0, 0, [], {"SRSId": 0, "SpatialReferenceSystems": 0, "Pose/Metadata": 0}
        meas_ids = []
        located = []
        stack = []
        try:
            for ev, el in ET.iterparse(str(xml), events=("start", "end")):
                if ev == "start":
                    if el.tag in ("SRSId", "SpatialReferenceSystems"):
                        bad[el.tag] += 1
                    if el.tag == "Metadata" and stack and stack[-1] == "Pose":
                        bad["Pose/Metadata"] += 1
                    stack.append(el.tag)
                    continue
                stack.pop()
                if el.tag == "Photo":
                    photos += 1
                    ids.add(el.findtext("Id"))
                    ip = el.findtext("ImagePath") or ""
                    if not Path(ip).is_file():
                        missing.append(ip)
                    else:
                        g, x = has_location(ip)
                        if g or x:
                            located.append(Path(ip).name)
                    if el.find("Pose/Rotation") is None or el.find("Pose/Center") is None:
                        no_pose += 1
                    el.clear()
                elif el.tag == "Measurement" and stack and stack[-1] == "TiePoint":
                    meas_ids.append(el.findtext("PhotoId"))
                elif el.tag == "TiePoint":
                    tie += 1
                    el.clear()
        except ET.ParseError as ex:
            fails.append("XML 解析失败：%s" % ex)
        else:
            meas_bad = sum(1 for i in meas_ids if i not in ids)
            notes.append("XML：Photo %d、TiePoint %d、Measurement %d" % (photos, tie, len(meas_ids)))
            if photos != aligned:
                fails.append("闸门2：<Photo> %d ≠ 对齐数 %s" % (photos, aligned))
            if missing:
                fails.append("闸门2：%d 个 ImagePath 不存在（如 %s）" % (len(missing), missing[0]))
            for k, v in bad.items():
                if v:
                    fails.append("闸门2：还有 %d 个 <%s>" % (v, k))
            if no_pose:
                fails.append("%d 个 Photo 没有 Pose/Rotation+Center" % no_pose)
            if meas_bad:
                fails.append("%d 个 Measurement 的 PhotoId 对不上 Photo" % meas_bad)
            if located:
                fails.append("D-07：%d 张照片还带定位（如 %s）" % (len(located), located[0]))
    if ms and psx.exists():
        r = metashape_count(ms, psx)
        if "error" in r:
            fails.append("Metashape 打开 .psx 失败：%s" % r["error"])
        else:
            notes.append("Metashape %s 重数：transform 非空 %d/%d" % (r["version"], r["aligned"], r["total"]))
            if r["aligned"] != aligned or r["total"] != total:
                fails.append("Metashape 重数 %d/%d 与报告 %s/%s 不一致" % (r["aligned"], r["total"], aligned, total))
    return fails, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--expect-aligned", type=int)
    ap.add_argument("--metashape")
    a = ap.parse_args()
    root = Path(a.path)
    dirs = [root] if root.name.endswith(ALIGN_SUFFIX) else sorted(p for p in root.rglob("*" + ALIGN_SUFFIX) if p.is_dir())
    if not dirs:
        print("没找到 *%s 目录：%s" % (ALIGN_SUFFIX, root))
        return 1
    bad = 0
    for d in dirs:
        fails, notes = check_dir(d, a.expect_aligned, a.metashape)
        print("%s  %s" % ("通过  " if not fails else "不通过", d))
        for n in notes:
            print("      " + n)
        for f in fails:
            print("      ✗ " + f)
        bad += bool(fails)
    print("共 %d 个目录，通过 %d，不通过 %d" % (len(dirs), len(dirs) - bad, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
