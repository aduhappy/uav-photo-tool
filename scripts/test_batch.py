# T7 批量迁移的独立检查：不 import 软件源码，只读 CSV 和报告文件。
#
# 用法 1（闸门 1，计划一致）：
#   python scripts/test_batch.py plan <脚本 -Plan 的汇总.csv> <gcp-batch --plan 的汇总.csv>
#   逐行比 期次、类型、照片数、参考期、状态 五列（另查行数、顺序和序号）；两边跳过原因的措辞不同，按下面 REASON_MAP 一一对应。
# 用法 2（汇总表字段）：
#   python scripts/test_batch.py summary <批量迁移汇总_*.csv> [--expect 期次=状态 ...] [--expect-rows N]
#   查：UTF-8 带 BOM、每格带引号、列名和顺序同脚本；状态只能是已知值；运行过的行 退出码 和 状态 对得上、耗时是数字；
#   报告列不空时文件要在，判定/RMS3D/对齐 要和报告里的对应行一致（自己解析）；判定 和 状态 不矛盾。
# 用法 3（T8 原地模式，一期）：
#   python scripts/test_batch.py inplace <原片期次目录> <副本期次目录> <批量输出目录> [--summary 汇总.csv]
#   查：输出目录里没有任何 *_去定位 目录；副本照片和原片同名同大小、SOS 之后逐字节相同、不带定位（自写解析：IFD0 没有 GPS 指针、
#   XMP 没有带值的 drone-dji 位置字段）；副本目录里有 定位备份_*.zip；给了 --summary 就查这一期 照片处理=原地抹除、定位备份 是存在的 zip。
#   （逐张更全的双源检查用 scripts/test_strip_gps.py <原片> <副本> --pixels）
# 用法 4（T8 还原）：python scripts/test_batch.py restored <原片期次目录> <副本期次目录>
#   查：每张 JPG 和原片 SHA-256 相同（用 restore-gps 还原后）。
# 用法 5（T8 重跑）：python scripts/test_batch.py rerun <批量输出目录> <期次> [--was 已取消|失败]
#   查：有一个 <期次>[_去定位]_控制点_上次<已取消|失败>_<时间> 目录，里面报告还在（判定是 已取消/失败，或有 已取消.txt）；
#   另有新的 <期次>[_去定位]_控制点，报告判定是 自动通过/需人工核对。
# 用法 2 的 summary 在 T8 起认 15 列（多「照片处理」「定位备份」）；用法 1 的 plan 也可拿 v0.8 的 --plan 汇总表和 v0.9 的比
#   （新增两列不比，跳过原因「在 --skip 里」↔「在跳过名单里」按对照表换算）。
# 退出码：全部通过 0，有不通过 1。
import argparse
import csv
import glob
import hashlib
import os
import re
import struct
import sys

FIELDS = ["序号", "期次", "类型", "照片数", "参考期", "排除点", "状态", "退出码", "判定", "RMS3D", "对齐", "耗时分钟", "报告"]
FIELDS_T8 = FIELDS + ["照片处理", "定位备份"]
PHOTO_MODES = ("复制", "原地抹除", "用已有去定位", "原片无定位", "")
PLAN_COLS = ["期次", "类型", "照片数", "参考期", "状态"]
REASON_MAP = {  # 脚本的措辞 → 软件的措辞（卡面 🔒 规定没匹配时写「跳过：没有匹配的规则」）
    "跳过：在 -Skip 里": "跳过：在 --skip 里",
    "跳过：目录名里没有「梯田」或「植被」": "跳过：没有匹配的规则",
    "跳过：是梯田，但没给 -RefTerrace": "跳过：规则「梯田」没填参考期",
    "跳过：是植被，但没给 -RefVeg": "跳过：规则「植被」没填参考期",
    "跳过：在 --skip 里": "跳过：在跳过名单里",  # v0.8 → v0.9（T8 🔒）
}
EXIT_TEXT = {"0": "自动通过", "1": "失败", "2": "拒绝", "3": "已取消", "4": "需人工核对"}
FAIL = []
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def ok(cond, msg):
    print(("  通过  " if cond else "  不通过 ") + msg)
    if not cond:
        FAIL.append(msg)


def read(path):
    raw = open(path, "rb").read()
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    return raw, rows


def check_format(path, raw, rows):
    ok(raw.startswith(b"\xef\xbb\xbf"), "%s：UTF-8 带 BOM" % os.path.basename(path))
    ok(rows and rows[0] in (FIELDS, FIELDS_T8), "列名和顺序：%s" % (rows[0] if rows else "空文件"))
    lines = raw.decode("utf-8-sig").splitlines()
    unq = [i + 1 for i, ln in enumerate(lines) if ln and not (ln.startswith('"') and ln.endswith('"'))]
    ok(not unq, "每行每格都带引号（不带的行：%s）" % unq[:5])
    ok(b"\r\n" in raw and raw.count(b"\r\n") == raw.count(b"\n"), "换行是 CRLF")


def cmd_plan(a):
    raw_s, rs = read(a.script)
    raw_p, rp = read(a.tool)
    print("== 闸门 1：%s 对 %s" % (a.script, a.tool))
    check_format(a.script, raw_s, rs)
    check_format(a.tool, raw_p, rp)
    hs, hp = rs[0], rp[0]
    ds = [dict(zip(hs, r)) for r in rs[1:]]
    dp = [dict(zip(hp, r)) for r in rp[1:]]
    ok(len(ds) == len(dp), "行数：脚本 %d，软件 %d" % (len(ds), len(dp)))
    diffs = []
    mapped = 0
    for i, (x, y) in enumerate(zip(ds, dp), 1):
        if x["序号"] != y["序号"]:
            diffs.append("第 %d 行序号 %s ≠ %s" % (i, x["序号"], y["序号"]))
        for c in PLAN_COLS:
            vx, vy = x[c], y[c]
            if c == "状态" and vx in REASON_MAP and vx != vy:
                mapped += 1
                while vx in REASON_MAP and vx != vy:  # 脚本 → v0.8 → v0.9 可能要换两步
                    vx = REASON_MAP[vx]
            if vx != vy:
                diffs.append("第 %d 行 %s：脚本「%s」，软件「%s」" % (i, c, x[c], vy))
    ok(not diffs, "逐行五列一致（%d 行；其中 %d 行跳过原因按措辞对照表换算）%s" % (
        len(ds), mapped, "" if not diffs else "：\n      " + "\n      ".join(diffs[:20])))
    for c in ("排除点", "退出码", "判定", "RMS3D", "对齐", "耗时分钟", "报告"):
        d = [i for i, (x, y) in enumerate(zip(ds, dp), 1) if x[c] != y[c]]
        ok(not d, "附带比 %s 列：%s" % (c, "一致" if not d else "第 %s 行不同" % d[:5]))
    cnt = {}
    for y in dp:
        cnt[y["状态"]] = cnt.get(y["状态"], 0) + 1
    print("  软件计划统计：%s" % "，".join("%s %d" % kv for kv in cnt.items()))


def report_lines(path):
    out = {}
    for ln in open(path, encoding="utf-8-sig").read().splitlines():
        for k in ("判定", "RMS3D", "对齐"):
            if k not in out and ln.startswith(k + "：") and len(ln) > len(k) + 1:
                out[k] = ln[len(k) + 1:]
    return out


def cmd_summary(a):
    raw, rows = read(a.csv)
    print("== 汇总表：%s" % a.csv)
    check_format(a.csv, raw, rows)
    d = [dict(zip(rows[0], r)) for r in rows[1:]]
    if a.expect_rows is not None:
        ok(len(d) == a.expect_rows, "行数 %d（应为 %d）" % (len(d), a.expect_rows))
    for r in d:
        tag = "序号 %s %s" % (r["序号"], r["期次"])
        st = r["状态"]
        ran = r["退出码"] != ""
        ok(st in EXIT_TEXT.values() or st == "计划运行" or st.startswith("跳过："), "%s：状态「%s」是已知值" % (tag, st))
        if "照片处理" in r:
            ok(r["照片处理"] in PHOTO_MODES, "%s：照片处理「%s」是已知值" % (tag, r["照片处理"]))
            if r["定位备份"]:
                ok(os.path.isfile(r["定位备份"]) and os.path.basename(r["定位备份"]).startswith("定位备份_"),
                   "%s：定位备份文件在 %s" % (tag, r["定位备份"]))
                ok(r["照片处理"] == "原地抹除", "%s：有定位备份时照片处理是原地抹除" % tag)
        if ran:
            ok(EXIT_TEXT.get(r["退出码"]) == st, "%s：退出码 %s 对 状态 %s" % (tag, r["退出码"], st))
            try:
                float(r["耗时分钟"])
                good = True
            except ValueError:
                good = False
            ok(good, "%s：耗时分钟「%s」是数字" % (tag, r["耗时分钟"]))
        else:
            ok(r["耗时分钟"] == "", "%s：没运行的行没有耗时" % tag)
        if r["报告"]:
            ok(os.path.isfile(r["报告"]), "%s：报告文件在 %s" % (tag, r["报告"]))
            if os.path.isfile(r["报告"]):
                rl = report_lines(r["报告"])
                same = all(rl.get(k, "") == r[k] for k in ("判定", "RMS3D", "对齐"))
                ok(same, "%s：判定/RMS3D/对齐 和报告一致（报告：%s）" % (tag, rl))
                folder = os.path.basename(os.path.dirname(r["报告"]))
                ok(folder in (r["期次"] + "_控制点", r["期次"] + "_去定位_控制点"), "%s：报告目录名 %s 对应这一期" % (tag, folder))
                if r.get("照片处理") in ("原地抹除", "原片无定位"):
                    ok(folder == r["期次"] + "_控制点", "%s：原地/无定位时结果目录不带 _去定位（%s）" % (tag, folder))
            if ran and st in ("自动通过", "需人工核对"):
                ok(r["判定"] == st, "%s：判定「%s」和状态一致" % (tag, r["判定"]))
        elif ran:
            ok(st in ("拒绝", "已取消", "失败"), "%s：没有报告时状态只能是拒绝/已取消/失败（%s）" % (tag, st))
    for e in a.expect or []:
        name, _, want = e.partition("=")
        got = [r["状态"] for r in d if r["期次"] == name]
        ok(got == [want], "预期 %s 的状态是「%s」（实际 %s）" % (name, want, got))


def jpeg_info(b):
    """自写解析：返回 (SOS 偏移, IFD0 有没有 GPS 指针, XMP 里带值的位置字段名列表)。"""
    i, sos, gps, xmp_loc = 2, None, False, []
    while i + 4 <= len(b):
        if b[i] != 0xFF:
            break
        m = b[i + 1]
        if m == 0xDA:
            sos = i
            break
        n = struct.unpack(">H", b[i + 2:i + 4])[0]
        seg = b[i + 4:i + 2 + n]
        if m == 0xE1 and seg.startswith(b"Exif\x00\x00"):
            tiff = seg[6:]
            e = "<" if tiff[:2] == b"II" else ">"
            off = struct.unpack(e + "I", tiff[4:8])[0]
            cnt = struct.unpack(e + "H", tiff[off:off + 2])[0]
            for k in range(cnt):
                tag = struct.unpack(e + "H", tiff[off + 2 + 12 * k:off + 4 + 12 * k])[0]
                gps = gps or tag == 0x8825
        elif m == 0xE1 and seg.startswith(b"http://ns.adobe.com/xap/1.0/"):
            for mm in re.finditer(rb'drone-dji:(GpsLatitude|GpsLongitude|GpsLongtitude|AbsoluteAltitude|RelativeAltitude'
                                  rb'|GpsStatus|AltitudeType|Rtk\w*|LRFTarget\w*)\s*=\s*"([^"]*)"', seg):
                if mm.group(2).strip():
                    xmp_loc.append(mm.group(1).decode())
        i += 2 + n
    return sos, gps, xmp_loc


def jpgs(d):
    return sorted(f for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg")))


def cmd_inplace(a):
    print("== 原地模式：原片 %s，副本 %s，输出 %s" % (a.orig, a.copy, a.out))
    dz = [d for d in glob.glob(os.path.join(a.out, "*")) if os.path.isdir(d) and d.endswith("_去定位")]
    ok(not dz, "输出目录里没有 *_去定位 目录（%s）" % dz)
    fo, fc = jpgs(a.orig), jpgs(a.copy)
    ok(fo == fc, "副本和原片同名、同张数（%d / %d）" % (len(fo), len(fc)))
    bad_size, bad_sos, still, orig_loc = [], [], [], 0
    for f in fo:
        bo, bc = open(os.path.join(a.orig, f), "rb").read(), open(os.path.join(a.copy, f), "rb").read()
        if len(bo) != len(bc):
            bad_size.append(f)
            continue
        so, g0, l0 = jpeg_info(bo)
        orig_loc += bool(g0 or l0)
        sc, gps, loc = jpeg_info(bc)
        if so is None or so != sc or bo[so:] != bc[sc:]:
            bad_sos.append(f)
        if gps or loc:
            still.append("%s（GPS 指针 %s，XMP %s）" % (f, gps, loc[:3]))
    print("  原片里带定位的 %d/%d 张" % (orig_loc, len(fo)))
    ok(not bad_size, "大小都和原片相同（不同：%s）" % bad_size[:5])
    ok(not bad_sos, "SOS 之后逐字节和原片相同（不同：%s）" % bad_sos[:5])
    ok(not still, "定位都剔净了（还有：%s）" % still[:5])
    zips = glob.glob(os.path.join(a.copy, "定位备份_*.zip"))
    ok(zips, "副本目录里有定位备份：%s" % [os.path.basename(z) for z in zips])
    if a.summary:
        name = os.path.basename(os.path.normpath(a.copy))
        rows = [r for r in csv.DictReader(open(a.summary, encoding="utf-8-sig")) if r["期次"] == name]
        ok(len(rows) == 1, "汇总表里有一行 %s" % name)
        if rows:
            r = rows[0]
            ok(r.get("照片处理") == "原地抹除", "照片处理=原地抹除（%s）" % r.get("照片处理"))
            ok(r.get("定位备份") and os.path.normcase(r["定位备份"]) in [os.path.normcase(z) for z in zips],
               "定位备份列指向副本目录里的 zip（%s）" % r.get("定位备份"))


def cmd_restored(a):
    print("== 还原：原片 %s，副本 %s" % (a.orig, a.copy))
    fo, fc = jpgs(a.orig), jpgs(a.copy)
    ok(fo == fc, "同名、同张数（%d / %d）" % (len(fo), len(fc)))
    h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
    diff = [f for f in fo if h(os.path.join(a.orig, f)) != h(os.path.join(a.copy, f))]
    ok(not diff, "每张和原片 SHA-256 相同（%d 张；不同：%s）" % (len(fo), diff[:5]))


def cmd_rerun(a):
    print("== 重跑：%s / %s" % (a.out, a.name))
    pat = re.compile(r"^%s(_去定位)?_控制点_上次(已取消|失败)_\d{8}_\d{6}(_\d+)?$" % re.escape(a.name))
    kept = [d for d in os.listdir(a.out) if pat.match(d) and os.path.isdir(os.path.join(a.out, d))]
    ok(kept, "旧结果目录改名保留：%s" % kept)
    for d in kept:
        why = pat.match(d).group(2)
        if a.was:
            ok(why == a.was, "%s：上次是 %s" % (d, a.was))
        rep = os.path.join(a.out, d, "控制点报告.txt")
        ok(os.path.isfile(rep), "%s：里面的报告还在" % d)
        if os.path.isfile(rep):
            v = report_lines(rep).get("判定", "")
            ok(v in ("已取消", "失败") or os.path.isfile(os.path.join(a.out, d, "已取消.txt")),
               "%s：旧报告判定「%s」（或有 已取消.txt）" % (d, v))
    new = [d for d in (a.name + "_控制点", a.name + "_去定位_控制点") if os.path.isfile(os.path.join(a.out, d, "控制点报告.txt"))]
    ok(len(new) == 1, "新结果目录：%s" % new)
    if new:
        v = report_lines(os.path.join(a.out, new[0], "控制点报告.txt")).get("判定", "")
        ok(v in ("自动通过", "需人工核对"), "新报告判定「%s」" % v)
        ok(not os.path.exists(os.path.join(a.out, new[0], "已取消.txt")), "新结果目录里没有 已取消.txt")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("script")
    p.add_argument("tool")
    s = sub.add_parser("summary")
    s.add_argument("csv")
    s.add_argument("--expect", action="append")
    s.add_argument("--expect-rows", type=int)
    ip = sub.add_parser("inplace")
    ip.add_argument("orig")
    ip.add_argument("copy")
    ip.add_argument("out")
    ip.add_argument("--summary")
    rs = sub.add_parser("restored")
    rs.add_argument("orig")
    rs.add_argument("copy")
    rr = sub.add_parser("rerun")
    rr.add_argument("out")
    rr.add_argument("name")
    rr.add_argument("--was", choices=("已取消", "失败"))
    a = ap.parse_args()
    {"plan": cmd_plan, "summary": cmd_summary, "inplace": cmd_inplace, "restored": cmd_restored, "rerun": cmd_rerun}[a.cmd](a)
    print("结果：%s" % ("全部通过" if not FAIL else "%d 项不通过" % len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
