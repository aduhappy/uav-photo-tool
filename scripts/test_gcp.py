# -*- coding: utf-8 -*-
"""检查「控制点」输出目录是否满足 T3 卡的判据和闸门（不 import 软件源码，自己重新解析、重新算）。

用法：
  python scripts/test_gcp.py <控制点输出目录或其上级目录> [--metashape 路径] [--expect-aligned N]
                             [--max-rms 0.02] [--expect-swap] [--expect-verdict 自动通过|需人工核对]
                             [--accuracy 0.01] [--ref-rms 0.0085 --ref-rms-tol 0.002]

  - 给的目录名以 _控制点 结尾就只查它；否则递归找下面所有 *_控制点 目录逐个查。
  - --expect-aligned N：报告里的对齐数必须 = N（闸门 1：0802上午梯田 填 149）。
  - --max-rms：RMS3D 上限（米；闸门 1 填 0.02）。迁移模式另查报告里「参考期相机最大位移」< 1 mm。
  - --expect-swap：建参考期模式下报告必须判出「CSV 的 X/Y 对调了」；--ref-rms/--ref-rms-tol：对调后 RMS3D 与主控值之差（闸门 2）。
  - --metashape：另用 Metashape 只读打开 .psx，自己算每个控制点的三维误差、像素 RMS、照片数，按 D-17 重新判定，
    和报告的结论、RMS3D 对比；再抽查 XML 里相机中心、控制点坐标和工程是否一致。

每个目录检查：
  1. 文件齐全（.psx、.files、_BlocksExchange.xml、控制点报告.txt、metashape_日志.txt），没有 .tmp、没有「已取消.txt」。
  2. 报告：判定只能是「自动通过」或「需人工核对」；「需人工核对」时必须列出点；不许出现「判定：成功」。
  3. XML：能解析；<Photo> 数 = 对齐数；ImagePath 都存在；没有 <SRSId>/<SpatialReferenceSystems>/<Pose> 下的 <Metadata>；
     有 <ControlPoints>，每个控制点有坐标，Measurement 的 PhotoId 都对得上 <Photo>；照片不带定位（自写字节解析）。
退出码：全部通过 0，有不通过 1。
"""

import argparse
import json
import math
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

GCP_SUFFIX = "_控制点"
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


# 独立重算：只读打开 .psx，自己算控制点误差（不用软件里的任何函数）
MS_SCRIPT = r'''
import sys, json, math
import Metashape
p = json.load(open(sys.argv[-1], encoding="utf-8"))
r = {}
try:
    d = Metashape.Document(); d.open(p["psx"], read_only=True)
    c = d.chunk
    T = c.transform.matrix
    rows = []
    for m in c.markers:
        if m.reference.location is None:
            continue
        px = []
        n = 0
        for cam, pr in m.projections.items():
            if cam.transform is None:
                continue
            n += 1
            q = cam.project(m.position) if m.position is not None else None
            if q is not None:
                px.append(math.hypot(q.x - pr.coord.x, q.y - pr.coord.y))
        d3 = None
        ref = m.reference.location
        if m.position is not None and T is not None:
            e = c.crs.project(T.mulp(m.position))
            d3 = math.sqrt((e.x - ref.x) ** 2 + (e.y - ref.y) ** 2 + (e.z - ref.z) ** 2)
        rows.append({"name": m.label, "n": n, "d3": d3, "ref": [ref.x, ref.y, ref.z],
                     "px_rms": math.sqrt(sum(v * v for v in px) / len(px)) if px else None,
                     "pinned_all": all(pr.pinned for _, pr in m.projections.items())})
    cams = {}
    for cam in c.cameras:
        if cam.transform is not None:
            v = c.crs.project(T.mulp(cam.center))
            cams[cam.label] = [v.x, v.y, v.z]
    r = {"total": len(c.cameras), "aligned": len(cams), "crs": c.crs.wkt, "rows": rows, "cams": cams,
         "version": Metashape.app.version}
except Exception as ex:
    import traceback
    r = {"error": traceback.format_exc()}
json.dump(r, open(p["out"], "w", encoding="utf-8"))
Metashape.app.quit()
'''


def run_metashape(ms, psx):
    tmp = Path(tempfile.mkdtemp(prefix="test_gcp_"))
    (tmp / "s.py").write_text(MS_SCRIPT, encoding="utf-8")
    (tmp / "p.json").write_text(json.dumps({"psx": str(psx), "out": str(tmp / "r.json")}), encoding="utf-8")
    subprocess.run([ms, "-r", str(tmp / "s.py"), str(tmp / "p.json")], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=600)
    return json.loads((tmp / "r.json").read_text(encoding="utf-8"))


def parse_xml(xml_path):
    info = {"photos": {}, "missing": [], "bad": {"SRSId": 0, "SpatialReferenceSystems": 0, "Pose/Metadata": 0},
            "cps": [], "has_cps": False}
    stack = []
    for event, el in ET.iterparse(str(xml_path), events=("start", "end")):
        if event == "start":
            if el.tag in ("SRSId", "SpatialReferenceSystems"):
                info["bad"][el.tag] += 1
            elif el.tag == "Metadata" and stack and stack[-1] == "Pose":
                info["bad"]["Pose/Metadata"] += 1
            elif el.tag == "ControlPoints":
                info["has_cps"] = True
            stack.append(el.tag)
            continue
        stack.pop()
        if el.tag == "Photo":
            ip = el.findtext("ImagePath") or ""
            c = el.find("Pose/Center")
            info["photos"][el.findtext("Id")] = {
                "path": ip, "center": [float(c.findtext(k)) for k in "xyz"] if c is not None else None}
            if not Path(ip).is_file():
                info["missing"].append(ip)
            el.clear()
        elif el.tag == "TiePoint":
            el.clear()
        elif el.tag == "ControlPoint":
            pos = el.find("Position")
            info["cps"].append({"name": el.findtext("Name"),
                                "pos": [float(pos.findtext(k)) for k in "xyz"] if pos is not None else None,
                                "ids": [m.findtext("PhotoId") for m in el.iter("Measurement")]})
            el.clear()
    return info


def judge(rows, acc):
    """D-17（自己再写一遍）：三维误差 ≤ 2×精度、像素 RMS ≤ 3、照片 ≥ 3 张。"""
    bad = []
    for r in rows:
        ok = r["d3"] is not None and r["d3"] <= 2 * acc and r["px_rms"] is not None and r["px_rms"] <= 3 and r["n"] >= 3
        if not ok:
            bad.append(r["name"])
    return ("自动通过" if rows and not bad else "需人工核对"), bad


def check_dir(d, args):
    fails, notes = [], []
    psxs = list(d.glob("*.psx"))
    xmls = list(d.glob("*_BlocksExchange.xml"))
    rep = d / "控制点报告.txt"
    if len(psxs) != 1 or not (d / (psxs[0].stem + ".files")).is_dir():
        fails.append("没有唯一的 .psx + .files")
    if len(xmls) != 1:
        fails.append("没有唯一的 _BlocksExchange.xml")
    for f in (rep, d / "metashape_日志.txt"):
        if not f.is_file():
            fails.append("缺 %s" % f.name)
    if (d / "已取消.txt").exists():
        fails.append("有 已取消.txt")
    if list(d.glob("*.tmp")):
        fails.append("残留 .tmp")
    if fails:
        return fails, notes
    text = rep.read_text(encoding="utf-8")
    m = re.search(r"^判定：(\S+)", text, re.M)
    verdict = m.group(1) if m else None
    notes.append("报告判定：%s" % verdict)
    if verdict not in ("自动通过", "需人工核对"):
        fails.append("报告判定不是 自动通过/需人工核对：%s" % verdict)
    if re.search(r"^判定：成功", text, re.M):
        fails.append("报告写了「判定：成功」")
    if verdict == "需人工核对" and "需人工核对的点" not in text:
        fails.append("需人工核对但没列点")
    if args.expect_verdict and verdict != args.expect_verdict:
        fails.append("判定 %s ≠ 期望 %s" % (verdict, args.expect_verdict))
    m = re.search(r"^RMS3D：([\d.]+) mm", text, re.M)
    rep_rms = float(m.group(1)) / 1000 if m else None
    notes.append("报告 RMS3D：%s m" % rep_rms)
    m = re.search(r"^对齐：(?:新照片|空三工程) (\d+)/(\d+)", text, re.M)
    aligned = int(m.group(1)) if m else None
    notes.append("报告对齐：%s" % (m.group(0) if m else None))
    transfer = "模式：迁移" in text
    if transfer:
        m = re.search(r"参考期相机最大位移 ([\d.]+) mm", text)
        mv = float(m.group(1)) if m else None
        notes.append("参考期相机最大位移：%s mm" % mv)
        if mv is None or mv >= 1.0:
            fails.append("参考期相机位移 %s mm（要 < 1 mm）" % mv)
    else:
        m = re.search(r"^X/Y 判断：原样 RMS3D ([\d.]+) m，对调 RMS3D ([\d.]+) m.*?→ (.*)$", text, re.M)
        if not m:
            fails.append("报告里没有 X/Y 判断")
        else:
            a, b, v = float(m.group(1)), float(m.group(2)), m.group(3)
            notes.append("X/Y：原样 %.4f / 对调 %.4f → %s" % (a, b, v))
            if args.expect_swap and "X/Y 对调了" not in v:
                fails.append("没判出 X/Y 对调")
            if args.ref_rms is not None and abs(b - args.ref_rms) >= args.ref_rms_tol:
                fails.append("对调后 RMS3D %.4f 与参考值 %.4f 差 ≥ %.4f" % (b, args.ref_rms, args.ref_rms_tol))
    if args.expect_aligned is not None and aligned != args.expect_aligned:
        fails.append("对齐 %s ≠ %d" % (aligned, args.expect_aligned))
    if args.max_rms is not None and (rep_rms is None or rep_rms > args.max_rms):
        fails.append("RMS3D %s > %s" % (rep_rms, args.max_rms))

    x = parse_xml(xmls[0])
    notes.append("XML：照片 %d、控制点 %d、刺点 %d" % (len(x["photos"]), len(x["cps"]), sum(len(c["ids"]) for c in x["cps"])))
    if aligned is not None and len(x["photos"]) != aligned:
        fails.append("XML <Photo> %d ≠ 对齐 %d" % (len(x["photos"]), aligned))
    if x["missing"]:
        fails.append("%d 个 ImagePath 不存在" % len(x["missing"]))
    for k, v in x["bad"].items():
        if v:
            fails.append("XML 还有 %d 个 %s" % (v, k))
    if not x["has_cps"] or not x["cps"]:
        fails.append("XML 里没有控制点")
    for c in x["cps"]:
        if c["pos"] is None:
            fails.append("控制点 %s 没坐标" % c["name"])
        if any(i not in x["photos"] for i in c["ids"]):
            fails.append("控制点 %s 的 PhotoId 对不上 <Photo>" % c["name"])
    located = [p["path"] for p in x["photos"].values() if any(has_location(p["path"]))]
    if located:
        fails.append("%d 张照片还带定位（如 %s）" % (len(located), located[0]))

    if args.metashape:
        r = run_metashape(args.metashape, psxs[0])
        if "error" in r:
            fails.append("Metashape 重开失败：%s" % r["error"].strip().splitlines()[-1])
            return fails, notes
        if "LOCAL_CS" not in r["crs"]:
            fails.append("工程坐标系不是 Local")
        if aligned is not None and r["aligned"] != aligned:
            fails.append("工程里对齐 %d ≠ 报告 %d" % (r["aligned"], aligned))
        v = [row["d3"] for row in r["rows"] if row["d3"] is not None]
        rms = math.sqrt(sum(a * a for a in v) / len(v)) if v else None
        my_verdict, bad = judge(r["rows"], args.accuracy)
        notes.append("独立重算：RMS3D %s m，判定 %s%s" % ("%.4f" % rms if rms is not None else None, my_verdict,
                                                   "（%s）" % "、".join(bad) if bad else ""))
        for row in r["rows"]:
            notes.append("  %s 号 n=%d d3=%s px_rms=%s" % (row["name"], row["n"],
                                                       "%.4f" % row["d3"] if row["d3"] is not None else None,
                                                       "%.2f" % row["px_rms"] if row["px_rms"] is not None else None))
            if not row["pinned_all"]:
                fails.append("%s 号还有未固定的投影" % row["name"])
        if rms is None or rep_rms is None or abs(rms - rep_rms) > 0.00015:
            fails.append("独立重算 RMS3D %s 和报告 %s 对不上" % (rms, rep_rms))
        if my_verdict != verdict:
            fails.append("独立判定 %s ≠ 报告 %s" % (my_verdict, verdict))
        # XML 坐标值 = 工程里的局部坐标
        by_label = dict((Path(p["path"]).stem, p["center"]) for p in x["photos"].values())
        diffs = [math.dist(by_label[k], c) for k, c in r["cams"].items() if k in by_label and by_label[k]]
        notes.append("XML 相机中心 vs 工程：比了 %d 张，最大差 %.2e m" % (len(diffs), max(diffs) if diffs else -1))
        if not diffs or max(diffs) > 1e-4:
            fails.append("XML 相机中心和工程局部坐标对不上")
        refs = dict((row["name"], row["ref"]) for row in r["rows"])
        for c in x["cps"]:
            if c["name"] in refs and c["pos"] and math.dist(refs[c["name"]], c["pos"]) > 1e-6:
                fails.append("XML 控制点 %s 坐标和工程不一致" % c["name"])
    return fails, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--metashape")
    ap.add_argument("--expect-aligned", type=int)
    ap.add_argument("--max-rms", type=float)
    ap.add_argument("--expect-swap", action="store_true")
    ap.add_argument("--expect-verdict")
    ap.add_argument("--accuracy", type=float, default=0.01)
    ap.add_argument("--ref-rms", type=float)
    ap.add_argument("--ref-rms-tol", type=float, default=0.002)
    args = ap.parse_args()
    root = Path(args.path)
    dirs = [root] if root.name.endswith(GCP_SUFFIX) else sorted(p for p in root.rglob("*" + GCP_SUFFIX) if p.is_dir())
    if not dirs:
        print("没找到 *%s 目录：%s" % (GCP_SUFFIX, root))
        return 1
    bad = 0
    for d in dirs:
        fails, notes = check_dir(d, args)
        print("%s  %s" % ("通过" if not fails else "不通过", d))
        for n in notes:
            print("    " + n)
        for f in fails:
            print("    ✗ " + f)
        bad += bool(fails)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
