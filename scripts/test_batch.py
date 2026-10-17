# T7 批量迁移的独立检查：不 import 软件源码，只读 CSV 和报告文件。
#
# 用法 1（闸门 1，计划一致）：
#   python scripts/test_batch.py plan <脚本 -Plan 的汇总.csv> <gcp-batch --plan 的汇总.csv>
#   逐行比 期次、类型、照片数、参考期、状态 五列（另查行数、顺序和序号）；两边跳过原因的措辞不同，按下面 REASON_MAP 一一对应。
# 用法 2（汇总表字段）：
#   python scripts/test_batch.py summary <批量迁移汇总_*.csv> [--expect 期次=状态 ...] [--expect-rows N]
#   查：UTF-8 带 BOM、每格带引号、列名和顺序同脚本；状态只能是已知值；运行过的行 退出码 和 状态 对得上、耗时是数字；
#   报告列不空时文件要在，判定/RMS3D/对齐 要和报告里的对应行一致（自己解析）；判定 和 状态 不矛盾。
# 退出码：全部通过 0，有不通过 1。
import argparse
import csv
import os
import sys

FIELDS = ["序号", "期次", "类型", "照片数", "参考期", "排除点", "状态", "退出码", "判定", "RMS3D", "对齐", "耗时分钟", "报告"]
PLAN_COLS = ["期次", "类型", "照片数", "参考期", "状态"]
REASON_MAP = {  # 脚本的措辞 → 软件的措辞（卡面 🔒 规定没匹配时写「跳过：没有匹配的规则」）
    "跳过：在 -Skip 里": "跳过：在 --skip 里",
    "跳过：目录名里没有「梯田」或「植被」": "跳过：没有匹配的规则",
    "跳过：是梯田，但没给 -RefTerrace": "跳过：规则「梯田」没填参考期",
    "跳过：是植被，但没给 -RefVeg": "跳过：规则「植被」没填参考期",
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
    ok(rows and rows[0] == FIELDS, "列名和顺序：%s" % (rows[0] if rows else "空文件"))
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
            if c == "状态" and vx in REASON_MAP:
                mapped += 1
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
            if ran and st in ("自动通过", "需人工核对"):
                ok(r["判定"] == st, "%s：判定「%s」和状态一致" % (tag, r["判定"]))
        elif ran:
            ok(st in ("拒绝", "已取消", "失败"), "%s：没有报告时状态只能是拒绝/已取消/失败（%s）" % (tag, st))
    for e in a.expect or []:
        name, _, want = e.partition("=")
        got = [r["状态"] for r in d if r["期次"] == name]
        ok(got == [want], "预期 %s 的状态是「%s」（实际 %s）" % (name, want, got))


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
    a = ap.parse_args()
    (cmd_plan if a.cmd == "plan" else cmd_summary)(a)
    print("结果：%s" % ("全部通过" if not FAIL else "%d 项不通过" % len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
