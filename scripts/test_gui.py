# T6 界面测试夹具：进程内找控件并 invoke()（和鼠标点击走同一个 command 回调），只用标准库 + customtkinter。
# 远程桌面里真鼠标点不到界面，所以用进程内 invoke()；截图用 PrintWindow（远程桌面断开也能画）。
#
# 用法 1（源码）：python scripts/test_gui.py --mode invoke|shots|batch --out <结果目录> --data <测试数据根> #                    --metashape <metashape.exe> --ref <参考期.psx> --appdata <临时 APPDATA>
#   --data 下要有：strip/T6复制/（约 20 张带定位照片）、inplace/T6原地/（8 张）、align/T6空三/（8 张）、
#   gcp/T6迁移/（8 张，不能是参考期自己的照片）；都用副本，别指向原始影像。
#   --appdata 会被清空重建，写入 metashape/gcp_ref/gcp_csv，用来验证「记住路径」，不碰真的 config.json。
# 用法 2（打包版）：PyInstaller 加 --runtime-hook scripts/test_gui.py 打一个测试用 exe，
#   用环境变量 UAV_T6_MODE/UAV_T6_OUT/UAV_T6_DATA/UAV_T6_MS/UAV_T6_REF 和 APPDATA 传参（先用用法 1 的 --appdata 建好）。
# batch 模式（T7「批量迁移」页签）：--data 指向测试上级目录（junction 指向原片期次 0802上午梯田、26.08.01植被，
#   另有 0899测试其他、0899植被梯田 两个小目录各 2 张照片副本、0899梯田_去定位、0899空目录梯田\子目录），
#   --ref 梯田参考期、--ref2 植被参考期；输出在 <结果目录>/batch_out。查扫描、筛选不清勾选、全选/全不选/反选/只勾待运行、
#   规则增删后重扫、改了规则不重扫就拒绝开始；然后真跑两期，第一期出结论后、第二期运行中点「取消」，查第一期结果保留、
#   第二期已取消、汇总表两行、界面不卡（200 ms 定时器最大间隔）；扫描后、运行中各截浅色/深色一张。
#   环境变量 UAV_T7_NO_RUN=1 时只测到重扫为止（不起 Metashape）。
# batch8 模式（T8：照片处理方式、原地确认框、跳过名单、上次已取消的期重跑、关窗口二次确认）：--data 指向放着
#   0802上午梯田、26.08.01植被 两期**真副本**的上级目录（原地模式会改它们，绝不能是 junction 或原片），--ref/--ref2 同上；
#   环境变量 UAV_T8_PHASE=1 跑全套（会真起 Metashape），UAV_T8_PHASE=2 + --keep-appdata 测重启后跳过名单还在、
#   照片处理方式回到复制，并截浅/深色图（phase 1 末尾清空了跳过名单，先跑一次 UAV_T8_PHASE=2a 填好再重启）。phase 1 最后一步点关窗口，窗口由软件自己关，harness_batch8.json 里没有 done。
# 结果：<结果目录>/harness_<mode>.json（checks 每条 [名称, 通过, 细节]），shots 模式另有 <结果目录>/截图/*.bmp。
import os
import sys

if __name__ == "__main__" and not getattr(sys, "frozen", False):  # 打包版里作为 runtime hook 时 __name__ 也是 __main__
    import argparse
    import shutil
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("invoke", "shots", "batch", "batch8", "t10", "t10long", "t9"), default="invoke")
    ap.add_argument("--out", required=True)
    ap.add_argument("--data", default="")
    ap.add_argument("--metashape", required=True)
    ap.add_argument("--ref", default="")
    ap.add_argument("--ref2", default="", help="batch 模式：植被参考期 .psx（--ref 是梯田）")
    ap.add_argument("--appdata", required=True)
    ap.add_argument("--seed-only", action="store_true", help="只建 --appdata，不跑界面（给打包版测试用）")
    ap.add_argument("--keep-appdata", action="store_true", help="不重建 --appdata（测重启后记住的设置）")
    _a = ap.parse_args()
    if not _a.keep_appdata:
        shutil.rmtree(_a.appdata, ignore_errors=True)
        os.makedirs(os.path.join(_a.appdata, "无人机照片工具"))
        import json as _json
        with open(os.path.join(_a.appdata, "无人机照片工具", "config.json"), "w", encoding="utf-8") as _f:
            _json.dump({"metashape": _a.metashape, "gcp_ref": _a.ref, "gcp_csv": "C:\记住的.csv"}, _f, ensure_ascii=False)
    if _a.seed_only:
        sys.exit(0)
    os.environ.update(APPDATA=_a.appdata, UAV_T6_MODE=_a.mode, UAV_T6_OUT=_a.out, UAV_T6_DATA=_a.data,
                      UAV_T6_MS=_a.metashape, UAV_T6_REF=_a.ref, UAV_T6_REF2=_a.ref2)

import ctypes
import ctypes.wintypes as W
import glob
import hashlib
import json
import struct
import time
import tkinter
from tkinter import filedialog, messagebox
import webbrowser

import customtkinter as ctk

MODE = os.environ.get("UAV_T6_MODE", "invoke")
OUT = os.environ["UAV_T6_OUT"]
DATA = os.environ.get("UAV_T6_DATA", "")
MS = os.environ.get("UAV_T6_MS", "")
REF = os.environ.get("UAV_T6_REF", "")
REF2 = os.environ.get("UAV_T6_REF2", "")
VERSION = os.environ.get("UAV_T6_VERSION", "1.2")
os.makedirs(OUT, exist_ok=True)

TAB_NAMES = ["剔除定位", "空三", "控制点", "批量迁移", "影像处理"]  # T9 起五个页签
R = {"mode": MODE, "frozen": bool(getattr(sys, "frozen", False)), "boxes": [], "checks": [], "browser": []}
FILE_ANSWER = {"open": None}


def rec_box(kind, ret=None):
    def f(title=None, message=None, **kw):
        R["boxes"].append([kind, title, message, round(time.time() - T0, 1)])
        return ret
    return f


for _k in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, _k, rec_box(_k))
messagebox.askyesno = rec_box("askyesno", True)
filedialog.askopenfilename = lambda **kw: (R["boxes"].append(["askopenfilename", kw.get("title"), FILE_ANSWER["open"], 0]) or FILE_ANSWER["open"] or "")
filedialog.askdirectory = lambda **kw: ""
webbrowser.open = lambda url, *a, **k: R["browser"].append(url)
os.startfile = lambda p, *a, **k: R.setdefault("startfile", []).append(str(p))  # 「打开完整日志」不真打开
ORIG_BOX = {}  # 软件自己的统一弹窗（T10）；drive() 里换成记录器前先存一份，t10 模式要用真的
if os.environ.get("UAV_T10_SCALE"):  # 模拟高分屏：CTk 窗口和控件都按这个倍数缩放
    ctk.set_window_scaling(float(os.environ["UAV_T10_SCALE"]))
    ctk.set_widget_scaling(float(os.environ["UAV_T10_SCALE"]))
T0 = time.time()


def check(name, ok, detail=""):
    R["checks"].append([name, bool(ok), str(detail)])


def save():
    with open(os.path.join(OUT, "harness_%s.json" % MODE), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1)


# ---------------- 截图（ctypes GDI，冻结版里没有 PIL） ----------------
def window_rect(root):
    hwnd = int(root.wm_frame(), 16)
    r = W.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(W.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        ctypes.windll.user32.GetWindowRect(W.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def grab_bmp(path, root):
    hwnd = int(root.wm_frame(), 16)
    rc = W.RECT()
    ctypes.windll.user32.GetWindowRect(W.HWND(hwnd), ctypes.byref(rc))
    l, t, r, b = rc.left, rc.top, rc.right, rc.bottom
    w, h = r - l, b - t
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    u.GetDC.restype = W.HDC; u.GetDC.argtypes = [W.HWND]
    u.ReleaseDC.argtypes = [W.HWND, W.HDC]
    g.CreateCompatibleDC.restype = W.HDC; g.CreateCompatibleDC.argtypes = [W.HDC]
    g.CreateCompatibleBitmap.restype = W.HBITMAP; g.CreateCompatibleBitmap.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int]
    g.SelectObject.restype = W.HGDIOBJ; g.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
    g.BitBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HDC, ctypes.c_int, ctypes.c_int, W.DWORD]
    g.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT]
    g.DeleteObject.argtypes = [W.HGDIOBJ]; g.DeleteDC.argtypes = [W.HDC]
    sdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
    R.setdefault("printwindow", []).append(u.PrintWindow(hwnd, mdc, 2))  # PW_RENDERFULLCONTENT：远程桌面断开时也能画
    hdr = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    bi = ctypes.create_string_buffer(hdr + b"\0" * 16)
    g.GetDIBits(mdc, bmp, 0, h, buf, bi, 0)
    with open(path, "wb") as f:
        f.write(b"BM" + struct.pack("<IHHI", 14 + 40 + len(buf.raw), 0, 0, 54) + hdr + buf.raw)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(None, sdc)


# ---------------- 找控件 ----------------
def walk(w):
    for c in w.winfo_children():
        yield c
        yield from walk(c)


def find(w, cls, pred=lambda x: True):
    return [x for x in walk(w) if isinstance(x, cls) and pred(x)]


def btn(w, text):
    bs = find(w, ctk.CTkButton, lambda b: b.cget("text") == text)
    assert bs, "找不到按钮 %s" % text
    return bs[0]


def form_of(tabf):
    """T10 起页签里是 PanedWindow：上格是表单（原来直接 grid 在页签上的控件），下格是日志。"""
    pws = [c for c in tabf.winfo_children() if isinstance(c, tkinter.PanedWindow)]
    return tabf.nametowidget(str(pws[0].panes()[0])) if pws else tabf


def entry_row(tabf, row):
    es = [e for e in find(tabf, ctk.CTkEntry) if e.master is form_of(tabf) and int(e.grid_info().get("row", -1)) == row]
    assert es, "第 %d 行没有输入框" % row
    return es[0]


def set_entry(e, text):
    e.delete(0, "end")
    e.insert(0, text)


def sha(d):
    return {os.path.basename(p): hashlib.sha256(open(p, "rb").read()).hexdigest() for p in sorted(glob.glob(os.path.join(d, "*.JPG")))}


def menu_entries(m):
    out = []
    for i in range((m.index("end") or 0) + 1):
        t = m.type(i)
        out.append((i, t, None if t in ("separator", "tearoff") else m.entrycget(i, "label")))
    return out


# ---------------- 步骤（生成器：yield 秒数 或 (条件, 超时)） ----------------
def steps_shots(root, nb):
    root.attributes("-topmost", True)
    root.geometry("+40+40")
    shots = os.path.join(OUT, "截图")
    os.makedirs(shots, exist_ok=True)
    t10 = getattr(root, "ui_t10", None)
    if t10:  # T10：每个页签日志里放几行各种颜色的样例（默认窗口 1000×760，表单以下全是日志区）
        for lg in t10["logs"]:
            t10["log_add"](lg, "\n".join(["开始：%s" % lg["name"], "匹配照片… 40000 关键点"] + [x for x, _ in T10_SAMPLES]))
    for mode, cn in (("light", "浅色"), ("dark", "深色")):
        ctk.set_appearance_mode(mode)
        for name in TAB_NAMES:
            nb.set(name)
            yield 1.2
            p = os.path.join(shots, "%s_%s.bmp" % (name, cn))
            grab_bmp(p, root)
            R.setdefault("shots", []).append(p)
    ctk.set_appearance_mode("light")
    root.attributes("-topmost", False)


def wait_idle(b):
    return (lambda: b.cget("state") == "normal"), 180


def steps_invoke(root, nb):
    cfg_path = os.path.join(os.environ["APPDATA"], "无人机照片工具", "config.json")
    cfg = lambda: json.load(open(cfg_path, encoding="utf-8"))
    R["title"] = root.title()
    check("窗口标题", root.title().endswith("v" + VERSION), root.title())
    yield 2.5  # 让启动 1.5 秒后的自动检查更新跑一下（没有新版本时不弹窗）
    check("启动自动检查更新不弹窗（无新版本）", not R["boxes"], R["boxes"])

    # ---- 菜单 ----
    menubar = root.nametowidget(root.cget("menu"))
    tops = menu_entries(menubar)
    R["menubar"] = tops
    subs = {lab: root.nametowidget(menubar.entrycget(i, "menu")) for i, t, lab in tops if t == "cascade"}
    help_m, look_m = subs["帮助"], subs["外观"]
    R["help_menu"] = menu_entries(help_m)
    R["look_menu"] = menu_entries(look_m)
    hl = {lab: i for i, t, lab in R["help_menu"] if lab}
    check("帮助菜单五项（T16 加自检）", set(hl) == {"检查更新", "自检", "启动时自动检查更新", "项目主页（GitHub）", "关于"}, list(hl))
    n_st = len(R["boxes"])
    help_m.invoke(hl["自检"])
    yield (lambda: len(R["boxes"]) > n_st), 120
    check("自检有结果框", len(R["boxes"]) > n_st, R["boxes"][n_st:])
    check("自检框里没有失败项", len(R["boxes"]) > n_st and "[失败]" not in (R["boxes"][-1][2] or ""),
          (R["boxes"][-1][2] or "")[:300] if len(R["boxes"]) > n_st else "（没弹框）")
    help_m.invoke(hl["关于"])
    check("关于", R["boxes"] and R["boxes"][-1][0] == "showinfo" and ("v" + VERSION) in R["boxes"][-1][2], R["boxes"][-1:])
    help_m.invoke(hl["项目主页（GitHub）"])
    check("项目主页", R["browser"] == ["https://github.com/aduhappy/uav-photo-tool"], R["browser"])
    n0 = len(R["boxes"])
    help_m.invoke(hl["检查更新"])
    yield (lambda: len(R["boxes"]) > n0), 30
    check("手动检查更新有结果框", len(R["boxes"]) > n0, R["boxes"][n0:])
    help_m.invoke(hl["启动时自动检查更新"])
    check("自动检查更新开关→关，写进 config", cfg().get("check_update_on_start") is False, cfg())
    help_m.invoke(hl["启动时自动检查更新"])
    check("自动检查更新开关→开", cfg().get("check_update_on_start") is True, cfg())
    ll = {lab: i for i, t, lab in R["look_menu"] if lab}
    check("外观菜单三项", list(ll) == ["跟随系统", "浅色", "深色"], list(ll))
    check("默认浅色", ctk.get_appearance_mode() == "Light", ctk.get_appearance_mode())
    look_m.invoke(ll["深色"])
    yield 0.5
    check("切深色并记进 config", ctk.get_appearance_mode() == "Dark" and cfg().get("appearance") == "dark", cfg().get("appearance"))
    look_m.invoke(ll["跟随系统"])
    check("跟随系统记进 config", cfg().get("appearance") == "system", cfg().get("appearance"))
    look_m.invoke(ll["浅色"])
    yield 0.5
    check("切回浅色", ctk.get_appearance_mode() == "Light" and cfg().get("appearance") == "light", cfg().get("appearance"))

    # ---- 页签 1：剔除定位 ----
    check("五个页签（T9 加影像处理）", list(nb._name_list) == TAB_NAMES, nb._name_list)
    check("默认页签", nb.get() == "剔除定位", nb.get())
    t1 = nb.tab("剔除定位")
    cbs = {c.cget("text"): c for c in find(form_of(t1), ctk.CTkCheckBox)}
    R["tab1_checkboxes"] = {k: c.get() for k, c in cbs.items()}
    check("页签1 勾选默认值", R["tab1_checkboxes"] == {"包含子目录": 1, "同时剔除姿态（Gimbal*/Flight*）": 0,
                                                 "原地抹除（直接改原照片，不复制）": 0, "保留定位备份（可还原）": 1}, R["tab1_checkboxes"])
    e_src, e_dst = entry_row(t1, 0), entry_row(t1, 1)
    b_start, b_stop, b_restore = btn(t1, "开始"), btn(t1, "中止"), btn(t1, "从备份还原")
    check("页签1 中止初始禁用", b_stop.cget("state") == "disabled")
    n0 = len(R["boxes"])
    b_start.invoke()
    check("空输入目录提示", R["boxes"][n0:] and "请先选择输入目录" in R["boxes"][-1][2], R["boxes"][n0:])
    src = os.path.join(DATA, "strip", "T6复制")
    set_entry(e_src, src)
    b_start.invoke()
    check("开始后 开始禁用/中止可用", b_start.cget("state") == "disabled" and b_stop.cget("state") == "normal")
    check("输出目录自动填默认", e_dst.get() == src + "_去定位", e_dst.get())
    yield 0.4
    b_stop.invoke()
    yield wait_idle(b_start)
    tb1 = find(t1, ctk.CTkTextbox)[0].get("1.0", "end")
    R["tab1_cancel_box"] = R["boxes"][-1]
    R["tab1_cancel_log_tail"] = tb1.strip().splitlines()[-3:]
    n_out = len(glob.glob(os.path.join(src + "_去定位", "*.JPG")))
    check("页签1 开始→中止：结束框 + 中止生效（输出 < 20 张）", R["boxes"][-1][2].startswith("完成：") and n_out < 20,
          "输出 %d 张；%s" % (n_out, R["boxes"][-1]))

    # 原地抹除：确认框（错名 / 取消 / 正确名）+ 从备份还原
    ip = os.path.join(DATA, "inplace", "T6原地")
    before = sha(ip)
    set_entry(e_src, ip)
    cbs["原地抹除（直接改原照片，不复制）"].toggle()
    check("勾原地后输出目录禁用", e_dst.cget("state") == "disabled", e_dst.cget("state"))
    for answer in ("错的名字", None, "T6原地"):
        n0 = len(R["boxes"])
        root.after(800, lambda a=answer: fill_dialog(root, a))
        b_start.invoke()  # 阻塞在确认框里（wait_window），fill_dialog 关掉它
        if answer == "错的名字":
            check("错名→不改文件", R["boxes"][n0:] and "确认目录名不正确" in R["boxes"][-1][2] and sha(ip) == before, R["boxes"][n0:])
        elif answer is None:
            check("取消→什么都不做", len(R["boxes"]) == n0 and sha(ip) == before and b_start.cget("state") == "normal")
        else:
            yield wait_idle(b_start)
            after = sha(ip)
            check("正确名→原地抹除完成", R["boxes"][-1][0] == "showinfo" and "成功 8" in R["boxes"][-1][2]
                  and all(after[k] != before[k] for k in before), R["boxes"][-1])
    check("确认框文案与警示色", R.get("dialog") and R["dialog"]["title"].endswith("警告：不可恢复")
          and "#C62828" in str(R["dialog"]["colors"]), R.get("dialog"))
    zips = glob.glob(os.path.join(ip, "定位备份_*.zip"))
    FILE_ANSWER["open"] = zips[0] if zips else None
    n0 = len(R["boxes"])
    b_restore.invoke()
    check("从备份还原→逐字节还原", sha(ip) == before and any("还原完成" in (b[2] or "") for b in R["boxes"][n0:]), R["boxes"][n0:])
    cbs["原地抹除（直接改原照片，不复制）"].toggle()
    check("取消原地后输出目录恢复", e_dst.cget("state") == "normal")

    # ---- 页签 2：空三 ----
    nb.set("空三")
    t2 = nb.tab("空三")
    yield 0.3
    e_ms, e_pd, e_out = (entry_row(t2, r) for r in (0, 1, 2))
    check("记住的 Metashape 路径", e_ms.get() == MS, e_ms.get())
    om = find(t2, ctk.CTkOptionMenu)[0]
    spins = [e for e in find(t2, ctk.CTkEntry) if e not in (e_ms, e_pd, e_out)]
    R["tab2_defaults"] = {"精度": om.get(), "关键点": spins[0].get(), "连接点": spins[1].get(),
                          "勾选": {c.cget("text"): c.get() for c in find(form_of(t2), ctk.CTkCheckBox)}}
    check("页签2 默认值", R["tab2_defaults"] == {"精度": "高", "关键点": "40000", "连接点": "4000",
                                            "勾选": {"包含子目录：每个含照片的子目录单独一个工程": 0, "照片带定位时先自动去定位": 1}},
          R["tab2_defaults"])
    check("精度选项", om.cget("values") == ["高", "中", "低"], om.cget("values"))
    plus = [b for b in find(t2, ctk.CTkButton) if b.cget("text") == "+"]
    minus = [b for b in find(t2, ctk.CTkButton) if b.cget("text") == "−"]
    plus[0].invoke(); v1 = spins[0].get(); minus[0].invoke(); v2 = spins[0].get()
    minus[1].invoke(); minus[1].invoke(); minus[1].invoke(); minus[1].invoke(); minus[1].invoke(); v3 = spins[1].get()
    set_entry(spins[1], "4000")
    check("微调按钮（步长/下限）", (v1, v2, v3) == ("41000", "40000", "0"), (v1, v2, v3))
    b2s, b2c = btn(t2, "开始"), btn(t2, "取消")
    set_entry(e_pd, os.path.join(DATA, "align", "T6空三"))
    set_entry(e_out, os.path.join(DATA, "align", "out_%s" % ("exe" if R["frozen"] else "src")))
    b2s.invoke()
    check("页签2 开始后 取消可用", b2c.cget("state") == "normal" and b2s.cget("state") == "disabled")
    t2box = find(t2, ctk.CTkTextbox)[0]
    yield (lambda: "Metashape" in t2box.get("1.0", "end") or b2s.cget("state") == "normal"), 60
    yield 6
    R["tab2_log_before_cancel"] = t2box.get("1.0", "end").strip().splitlines()[-4:]
    b2c.invoke()
    lbls = [l.cget("text") for l in find(t2, ctk.CTkLabel)]
    check("点取消后显示正在取消", "正在取消…" in lbls, lbls[-3:])
    yield wait_idle(b2s)
    check("页签2 开始→取消：已取消提示", R["boxes"][-1][0] == "showwarning" and "已取消" in R["boxes"][-1][2], R["boxes"][-1])
    check("取消后记住 Metashape 路径", cfg().get("metashape") == MS)

    # ---- 页签 3：控制点 ----
    nb.set("控制点")
    t3 = nb.tab("控制点")
    yield 0.3
    rows = {r: entry_row(t3, r) for r in range(1, 8)}
    check("页签3 记住的路径（Metashape/参考期/CSV）", rows[1].get() == MS and rows[2].get() == REF and rows[5].get() == "C:\\记住的.csv",
          [rows[1].get(), rows[2].get(), rows[5].get()])
    radios = {r.cget("text"): r for r in find(t3, ctk.CTkRadioButton)}
    others = [e for e in find(t3, ctk.CTkEntry) if e not in rows.values()]
    R["tab3_defaults"] = {"模式": "transfer" if radios["迁移（新一期，零刺点）"]._check_state else "?", "其他输入": [e.get() for e in others],
                          "勾选": {c.cget("text"): c.get() for c in find(form_of(t3), ctk.CTkCheckBox)}}
    check("页签3 默认值（精度 0.01 / 排除空 / 挪动 50 / 自动去定位）",
          R["tab3_defaults"]["其他输入"] == ["0.01", "", "50"] and R["tab3_defaults"]["勾选"] == {"照片带定位时先自动去定位": 1},
          R["tab3_defaults"])
    p3 = [b for b in find(t3, ctk.CTkButton) if b.cget("text") == "+"]
    p3[0].invoke(); a1 = others[0].get(); set_entry(others[0], "0.01")
    p3[1].invoke(); s1 = others[2].get(); set_entry(others[2], "50")
    check("页签3 微调（精度+0.005、挪动+5）", (a1, s1) == ("0.015", "55"), (a1, s1))
    st = lambda: {r: rows[r].cget("state") for r in rows}
    radios["建参考期（新场地第一次）"].invoke()
    s_init = st()
    radios["迁移（新一期，零刺点）"].invoke()
    s_tr = st()
    check("模式切换启用/禁用对应输入", s_init == {1: "normal", 2: "disabled", 3: "disabled", 4: "normal", 5: "normal", 6: "normal", 7: "normal"}
          and s_tr == {1: "normal", 2: "normal", 3: "normal", 4: "disabled", 5: "disabled", 6: "disabled", 7: "normal"}, (s_init, s_tr))
    b3s, b3c = btn(t3, "开始"), btn(t3, "取消")
    set_entry(rows[3], os.path.join(DATA, "gcp", "T6迁移"))
    set_entry(rows[7], os.path.join(DATA, "gcp", "out_%s" % ("exe" if R["frozen"] else "src")))
    b3s.invoke()
    check("页签3 开始后 取消可用", b3c.cget("state") == "normal" and b3s.cget("state") == "disabled")
    t3box = find(t3, ctk.CTkTextbox)[0]
    yield (lambda: "Metashape" in t3box.get("1.0", "end") or b3s.cget("state") == "normal"), 90
    yield 6
    R["tab3_log_before_cancel"] = t3box.get("1.0", "end").strip().splitlines()[-4:]
    b3c.invoke()
    yield wait_idle(b3s)
    check("页签3 开始→取消：已取消提示", R["boxes"][-1][0] == "showwarning" and "已取消" in R["boxes"][-1][2], R["boxes"][-1])
    check("开始后记住参考期路径", cfg().get("gcp_ref") == REF, cfg().get("gcp_ref"))


def steps_batch(root, nb):
    """T7「批量迁移」页签。"""
    cfg_path = os.path.join(os.environ["APPDATA"], "无人机照片工具", "config.json")
    cfg = lambda: json.load(open(cfg_path, encoding="utf-8"))
    out = os.path.join(OUT, "batch_out")
    shots = os.path.join(OUT, "截图")
    os.makedirs(shots, exist_ok=True)
    root.geometry("+40+40")
    check("窗口标题", root.title().endswith("v" + VERSION), root.title())
    check("五个页签（T9 加影像处理）", list(nb._name_list) == TAB_NAMES, nb._name_list)
    nb.set("批量迁移")
    t4 = nb.tab("批量迁移")
    yield 0.5
    e_root, e_out, e_ms = (entry_row(t4, r) for r in range(3))
    check("Metashape 自动填上", e_ms.get() == MS, e_ms.get())
    rf = [f for f in find(t4, ctk.CTkFrame) if f.master is form_of(t4) and int(f.grid_info().get("row", -1)) == 3][0]

    def rules():
        es = sorted(find(rf, ctk.CTkEntry), key=lambda e: (int(e.grid_info()["row"]), int(e.grid_info()["column"])))
        return [es[i:i + 3] for i in range(0, len(es), 3)]

    check("默认两条规则（梯田、植被）", [r[0].get() for r in rules()] == ["梯田", "植被"], [r[0].get() for r in rules()])
    set_entry(e_root, DATA)
    set_entry(e_out, out)
    set_entry(rules()[0][1], REF)
    set_entry(rules()[1][1], REF2)
    lst = find(t4, ctk.CTkScrollableFrame)[0]

    def table():
        rows = []
        for cb in find(lst, ctk.CTkCheckBox):
            gi = cb.grid_info()
            if gi:
                r = int(gi["row"])
                cells = [w.cget("text") for w in sorted(cb.master.grid_slaves(row=r), key=lambda w: int(w.grid_info()["column"]))
                         if isinstance(w, ctk.CTkLabel)]
            else:
                cells = getattr(cb, "_t7_cells", [])
            cb._t7_cells = cells
            rows.append({"cb": cb, "shown": bool(gi), "on": bool(cb.get()), "cells": cells})
        return rows

    def by_name():
        return {r["cells"][1]: r for r in table() if r["cells"]}

    btn(t4, "开始").invoke()
    check("没扫描就开始 → 提示先扫描", R["boxes"] and "扫描" in R["boxes"][-1][2], R["boxes"][-1:])
    btn(t4, "扫描").invoke()
    yield 0.5
    tb = by_name()
    R["scan1"] = {k: [v["on"]] + v["cells"] for k, v in tb.items()}
    want = {"0802上午梯田": ("梯田", "149", "待运行"), "0899测试其他": ("—", "2", "跳过：没有匹配的规则"),
            "0899植被梯田": ("梯田", "2", "待运行"), "26.08.01植被": ("植被", "97", "待运行")}
    got = {k: (v["cells"][2], v["cells"][3], v["cells"][5]) for k, v in tb.items()}
    check("扫描结果（4 期；_去定位 结尾和不直接含照片的不算；两个关键词都有按第一条；没匹配的跳过）", got == want, got)
    check("序号按名字排序", tb["0802上午梯田"]["cells"][0] == "1" and tb["0899测试其他"]["cells"][0] == "2"
          and tb["0899植被梯田"]["cells"][0] == "3" and tb["26.08.01植被"]["cells"][0] == "4", got)
    check("扫描后默认只勾待运行", {k: v["on"] for k, v in tb.items()} ==
          {"0802上午梯田": True, "0899测试其他": False, "0899植被梯田": True, "26.08.01植被": True})
    check("参考期列是文件名", tb["26.08.01植被"]["cells"][4] == os.path.basename(REF2), tb["26.08.01植被"]["cells"][4])
    check("扫描后记住规则和目录", [r["psx"] for r in cfg()["batch_rules"]] == [REF, REF2] and cfg().get("batch_root") == DATA,
          cfg().get("batch_rules"))
    for mode, cn in (("light", "浅色"), ("dark", "深色")):
        ctk.set_appearance_mode(mode)
        yield 1.0
        p = os.path.join(shots, "批量迁移_扫描后_%s.bmp" % cn)
        grab_bmp(p, root)
        R.setdefault("shots", []).append(p)
    ctk.set_appearance_mode("light")

    # 筛选只影响显示，不清勾选
    of4 = [f for f in find(t4, ctk.CTkFrame) if f.master is form_of(t4) and int(f.grid_info().get("row", -1)) == 4]  # T8 加的选项行
    e_f = [e for e in find(t4, ctk.CTkEntry) if e.master is not rf and e.master not in of4 and e not in (e_root, e_out, e_ms)][0]
    set_entry(e_f, "植被")
    yield 0.3
    tb = by_name()
    check("筛选「植被」只显示 2 行", sorted(k for k, v in tb.items() if v["shown"]) == ["0899植被梯田", "26.08.01植被"],
          {k: v["shown"] for k, v in tb.items()})
    btn(t4, "全不选").invoke()
    set_entry(e_f, "")
    yield 0.3
    tb = by_name()
    check("全不选只作用于显示的行；清空筛选后其他行的勾还在", {k: v["on"] for k, v in tb.items()} ==
          {"0802上午梯田": True, "0899测试其他": False, "0899植被梯田": False, "26.08.01植被": False}
          and all(v["shown"] for v in tb.values()), {k: v["on"] for k, v in tb.items()})
    set_entry(e_f, "zzz")
    yield 0.3
    check("筛选不匹配时一行不显示", not any(v["shown"] for v in by_name().values()))
    set_entry(e_f, "")
    yield 0.3
    check("筛选过「zzz」再清空，勾选不变", {k: v["on"] for k, v in by_name().items()} ==
          {"0802上午梯田": True, "0899测试其他": False, "0899植被梯田": False, "26.08.01植被": False})
    btn(t4, "反选").invoke()
    check("反选", {k: v["on"] for k, v in by_name().items()} ==
          {"0802上午梯田": False, "0899测试其他": True, "0899植被梯田": True, "26.08.01植被": True})
    btn(t4, "全选").invoke()
    check("全选", all(v["on"] for v in by_name().values()))
    btn(t4, "只勾待运行").invoke()
    check("只勾待运行", {k: v["on"] for k, v in by_name().items()} ==
          {"0802上午梯田": True, "0899测试其他": False, "0899植被梯田": True, "26.08.01植被": True})
    by_name()["0899植被梯田"]["cb"].toggle()
    check("单独点一行的勾选框", not by_name()["0899植被梯田"]["on"])

    # 规则表加一行 → 重扫：0899测试其他 匹配上；改了规则不重扫就开始：拒绝；删掉 → 重扫：又变回跳过
    btn(rf, "＋ 添加规则").invoke()
    yield 0.3
    check("加规则后 3 行", len(rules()) == 3, len(rules()))
    set_entry(rules()[2][0], "其他")
    set_entry(rules()[2][1], REF)
    set_entry(rules()[2][2], "4")
    btn(t4, "开始").invoke()
    check("改了规则不重扫就开始 → 提示重新扫描", "重新扫描" in R["boxes"][-1][2], R["boxes"][-1:])
    btn(t4, "扫描").invoke()
    yield 0.5
    tb = by_name()
    check("加「其他」规则后重扫：0899测试其他 待运行、规则 其他", tb["0899测试其他"]["cells"][2] == "其他" and
          tb["0899测试其他"]["cells"][5] == "待运行", tb["0899测试其他"]["cells"])
    check("新规则记进 config", [r["key"] for r in cfg()["batch_rules"]] == ["梯田", "植被", "其他"]
          and cfg()["batch_rules"][2]["exclude"] == "4", cfg()["batch_rules"])
    dels = sorted([b for b in find(rf, ctk.CTkButton) if b.cget("text") == "删除"], key=lambda b: int(b.grid_info()["row"]))
    dels[2].invoke()
    yield 0.3
    check("删规则后 2 行", [r[0].get() for r in rules()] == ["梯田", "植被"], [r[0].get() for r in rules()])
    btn(t4, "扫描").invoke()
    yield 0.5
    tb = by_name()
    check("删规则后重扫：0899测试其他 变回跳过", tb["0899测试其他"]["cells"][5] == "跳过：没有匹配的规则", tb["0899测试其他"]["cells"])
    if os.environ.get("UAV_T7_NO_RUN"):
        return

    # 真跑：只勾 0802上午梯田、26.08.01植被；第一期出结论后、第二期运行中取消
    set_entry(e_f, "0899")
    yield 0.3
    btn(t4, "全不选").invoke()
    set_entry(e_f, "")
    yield 0.3
    R["run_checked"] = sorted(k for k, v in by_name().items() if v["on"])
    check("开跑前勾的是两期", R["run_checked"] == ["0802上午梯田", "26.08.01植被"], R["run_checked"])
    gaps = {"last": time.time(), "max": 0.0, "n": 0}

    def probe():  # 界面线程每 200 ms 一次；最大间隔就是界面最长卡了多久
        now = time.time()
        gaps["max"] = max(gaps["max"], now - gaps["last"])
        gaps["last"] = now
        gaps["n"] += 1
        if not R.get("stop_probe"):
            root.after(200, probe)

    nboxes = len(R["boxes"])
    bs, bc = btn(t4, "开始"), btn(t4, "取消")
    bs.invoke()
    gaps["last"] = time.time()
    root.after(200, probe)
    check("开始后：开始/扫描禁用、取消可用", bs.cget("state") == "disabled" and btn(t4, "扫描").cget("state") == "disabled"
          and bc.cget("state") == "normal")
    st = lambda n: by_name()[n]["cells"][5]
    yield (lambda: st("0802上午梯田") in ("运行中", "等待其他 Metashape")), 60
    R["status_running"] = st("0802上午梯田")
    yield 20
    for mode, cn in (("light", "浅色"), ("dark", "深色")):
        ctk.set_appearance_mode(mode)
        yield 1.0
        p = os.path.join(shots, "批量迁移_运行中_%s.bmp" % cn)
        grab_bmp(p, root)
        R.setdefault("shots", []).append(p)
    ctk.set_appearance_mode("light")
    yield (lambda: st("26.08.01植被") == "运行中" or bs.cget("state") == "normal"), 3600
    R["first_status"] = st("0802上午梯田")
    check("第一期出结论（自动通过/需人工核对 + RMS3D）", R["first_status"].split(" ")[0] in ("自动通过", "需人工核对")
          and "mm" in R["first_status"], R["first_status"])
    t4box = find(t4, ctk.CTkTextbox)[0]
    yield (lambda: "匹配" in "\n".join(t4box.get("1.0", "end").splitlines()[-15:]) or bs.cget("state") == "normal"), 900
    yield 20
    R["log_before_cancel"] = t4box.get("1.0", "end").strip().splitlines()[-6:]
    bc.invoke()
    check("点取消后显示正在取消", any("正在取消" in l.cget("text") for l in find(t4, ctk.CTkLabel)))
    yield wait_idle(bs)
    R["stop_probe"] = True
    R["ui_max_gap_s"] = round(gaps["max"], 2)
    R["ui_ticks"] = gaps["n"]
    check("运行期间界面不卡（200 ms 定时器最大间隔 < 2 s）", gaps["max"] < 2.0, "最大间隔 %.2f s，%d 次" % (gaps["max"], gaps["n"]))
    tb = by_name()
    R["final"] = {k: v["cells"][5] for k, v in tb.items()}
    check("取消后：第一期保留结论、第二期已取消", tb["0802上午梯田"]["cells"][5] == R["first_status"]
          and tb["26.08.01植被"]["cells"][5] == "已取消", R["final"])
    new = R["boxes"][nboxes:]
    check("取消后弹「已取消」提示", new and new[-1][0] == "showwarning" and "已取消" in new[-1][2], new)
    sums = sorted(glob.glob(os.path.join(out, "批量迁移汇总_*.csv")))
    R["summary"] = sums[-1] if sums else ""
    import csv as _csv
    rows = list(_csv.DictReader(open(R["summary"], encoding="utf-8-sig"))) if sums else []
    R["summary_rows"] = rows
    check("汇总表写到取消为止：两行，第一期结论、第二期已取消", [(r["期次"], r["状态"]) for r in rows] ==
          [("0802上午梯田", R["first_status"].split(" ")[0]), ("26.08.01植被", "已取消")], [(r["期次"], r["状态"]) for r in rows])
    rep1 = glob.glob(os.path.join(out, "0802上午梯田*_控制点", "控制点报告.txt"))
    check("第一期的控制点报告保留", len(rep1) == 1 and
          ("判定：%s" % R["first_status"].split(" ")[0]) in open(rep1[0], encoding="utf-8-sig").read(), rep1)
    check("日志文件在", glob.glob(os.path.join(out, "批量迁移日志_*.txt")) != [])


def steps_batch8(root, nb):
    """T8「批量迁移」：照片处理方式（复制/原地）、原地确认框、跳过名单、上次已取消的期重跑、关窗口二次确认。
    UAV_T8_PHASE=1：--data 下要有两期照片的**真副本**（0802上午梯田、26.08.01植被；原地模式会改它们）；
    跳过名单填 0802上午梯田 → 扫描；原地确认框 错名/取消/对名；对名后跑 26.08.01植被、匹配中取消；
    重扫显示「待运行（上次已取消）」→ 再跑到完、旧结果目录改名保留；最后清空跳过名单、复制模式跑 0802上午梯田，
    运行中点关窗口 → 二次确认 → 停在当前这一期后自动关窗。
    UAV_T8_PHASE=2（不重建 APPDATA）：重启后跳过名单还在、照片处理方式回到复制；扫描后截浅/深色图。"""
    phase = os.environ.get("UAV_T8_PHASE", "1")
    cfg_path = os.path.join(os.environ["APPDATA"], "无人机照片工具", "config.json")
    cfg = lambda: json.load(open(cfg_path, encoding="utf-8"))
    out = os.environ.get("UAV_T8_OUT2") or os.path.join(OUT, "batch_out")
    shots = os.path.join(OUT, "截图")
    os.makedirs(shots, exist_ok=True)
    root.geometry("+40+40")
    check("窗口标题", root.title().endswith("v" + VERSION), root.title())
    nb.set("批量迁移")
    t4 = nb.tab("批量迁移")
    yield 0.5
    e_root, e_out, e_ms = (entry_row(t4, r) for r in range(3))
    rf = [f for f in find(t4, ctk.CTkFrame) if f.master is form_of(t4) and int(f.grid_info().get("row", -1)) == 3][0]
    of4 = [f for f in find(t4, ctk.CTkFrame) if f.master is form_of(t4) and int(f.grid_info().get("row", -1)) == 4][0]
    radios = {r.cget("value"): r for r in find(of4, ctk.CTkRadioButton)}
    e_skip = find(of4, ctk.CTkEntry)[0]
    lst = find(t4, ctk.CTkScrollableFrame)[0]
    check("照片处理方式两个单选", sorted(radios) == ["copy", "inplace"] and "复制" in radios["copy"].cget("text")
          and "原照片上抹除" in radios["inplace"].cget("text"), {k: r.cget("text") for k, r in radios.items()})
    mode_var = radios["copy"].cget("variable")
    check("启动时照片处理方式是「复制」", root.getvar(str(mode_var)) == "copy", root.getvar(str(mode_var)))

    def rules():
        es = sorted(find(rf, ctk.CTkEntry), key=lambda e: (int(e.grid_info()["row"]), int(e.grid_info()["column"])))
        return [es[i:i + 3] for i in range(0, len(es), 3)]

    def table():
        rows = {}
        for cb in find(lst, ctk.CTkCheckBox):
            gi = cb.grid_info()
            if not gi:
                continue
            r = int(gi["row"])
            cells = [w.cget("text") for w in sorted(cb.master.grid_slaves(row=r), key=lambda w: int(w.grid_info()["column"]))
                     if isinstance(w, ctk.CTkLabel)]
            rows[cells[1]] = {"cb": cb, "on": bool(cb.get()), "cells": cells}
        return rows

    def snapshot():
        return {os.path.relpath(p, DATA): (os.path.getsize(p), os.stat(p).st_mtime_ns)
                for p in glob.glob(os.path.join(DATA, "**", "*"), recursive=True) if os.path.isfile(p)}

    if phase == "2a":  # 只填跳过名单并扫描（记进 config）后退出，给 phase 2 测重启
        set_entry(e_skip, "0802上午梯田")
        btn(t4, "扫描").invoke()
        yield 0.8
        check("2a：跳过名单记进 config", cfg().get("batch_skip") == "0802上午梯田", cfg().get("batch_skip"))
        return
    if phase == "2":
        check("重启后跳过名单还在（config 里记着）", e_skip.get() == "0802上午梯田" and cfg().get("batch_skip") == "0802上午梯田",
              (e_skip.get(), cfg().get("batch_skip")))
        check("config 里没有照片处理方式", not any("mode" in k or "inplace" in k for k in cfg()), sorted(cfg()))
        btn(t4, "扫描").invoke()
        yield 0.8
        tb = table()
        R["scan_phase2"] = {k: [v["on"]] + v["cells"] for k, v in tb.items()}
        check("重启后扫描：0802上午梯田 跳过名单、不勾", tb["0802上午梯田"]["cells"][5] == "跳过：在跳过名单里"
              and not tb["0802上午梯田"]["on"], R["scan_phase2"])
        # 参考期框显示尾部 + 悬停提示完整路径
        e_psx = rules()[1][1]
        R["psx_xview"] = list(e_psx._entry.xview())
        check("参考期输入框显示路径尾部（文件名）", R["psx_xview"][1] >= 0.999, R["psx_xview"])
        e_psx._entry.event_generate("<Enter>", x=5, y=5)
        yield 0.3
        tips = [w for w in e_psx.winfo_children() if isinstance(w, tkinter.Toplevel)] + \
               [w for w in e_psx._entry.winfo_children() if isinstance(w, tkinter.Toplevel)]
        texts = [c.cget("text") for t in tips for c in t.winfo_children()]
        check("悬停参考期框弹出完整路径", REF2 in texts, texts)
        e_psx._entry.event_generate("<Leave>", x=5, y=5)
        radios["inplace"].invoke()
        for mode, cn in (("light", "浅色"), ("dark", "深色")):
            ctk.set_appearance_mode(mode)
            yield 1.0
            p = os.path.join(shots, "批量迁移_T8_%s.bmp" % cn)
            grab_bmp(p, root)
            R.setdefault("shots", []).append(p)
        ctk.set_appearance_mode("light")
        radios["copy"].invoke()
        return

    set_entry(e_root, DATA)
    set_entry(e_out, out)
    set_entry(rules()[0][1], REF)
    set_entry(rules()[1][1], REF2)
    check("跳过名单默认空", e_skip.get() == "", e_skip.get())
    set_entry(e_skip, "0802上午梯田")
    btn(t4, "扫描").invoke()
    yield 0.8
    tb = table()
    R["scan1"] = {k: [v["on"]] + v["cells"] for k, v in tb.items()}
    check("跳过名单里的期：「跳过：在跳过名单里」、默认不勾", tb["0802上午梯田"]["cells"][5] == "跳过：在跳过名单里"
          and not tb["0802上午梯田"]["on"] and tb["26.08.01植被"]["cells"][5] == "待运行" and tb["26.08.01植被"]["on"],
          R["scan1"])
    check("跳过名单记进 config", cfg().get("batch_skip") == "0802上午梯田", cfg().get("batch_skip"))
    radios["inplace"].invoke()
    check("选原地后 config 里没记照片处理方式", not any("mode" in k or "inplace" in k for k in cfg()), sorted(cfg()))

    # 原地确认框：错名 / 取消 → 什么都不动、不建输出目录；对名 → 开跑
    before = snapshot()
    bs, bc = btn(t4, "开始"), btn(t4, "取消")
    for answer, why in (("26.08.01植被", "错名（填了期次名）"), (DATA, "错名（完整路径）"), (None, "取消")):
        n0 = len(R["boxes"])
        R.pop("dialog", None)
        root.after(800, lambda a=answer: fill_dialog(root, a))
        bs.invoke()
        yield 0.5
        new = R["boxes"][n0:]
        check("原地确认框 %s → 不开始、原片不动、不建输出目录" % why,
              bs.cget("state") == "normal" and snapshot() == before and not os.path.exists(out)
              and (answer is None and not new or answer is not None and new and "确认名不正确" in new[-1][2]), new)
    d = R.get("dialog") or {}
    R["dialog_batch"] = d
    check("确认框写明期数、张数、备份位置，警示色", any("1 期、共 97 张" in t and "备份" in t for t in d.get("texts", []))
          and "#C62828" in str(d.get("colors")) and any("上级目录的名字" in t for t in d.get("texts", [])), d)

    # 对名 → 跑 26.08.01植被，匹配中取消
    def st(n):
        return table()[n]["cells"][5]

    gaps = {"last": time.time(), "max": 0.0}

    def probe():
        now = time.time()
        gaps["max"] = max(gaps["max"], now - gaps["last"])
        gaps["last"] = now
        if not R.get("stop_probe"):
            root.after(200, probe)

    n0 = len(R["boxes"])
    root.after(800, lambda: fill_dialog(root, os.path.basename(DATA)))
    bs.invoke()
    gaps["last"] = time.time()
    root.after(200, probe)
    check("对名后开跑", bs.cget("state") == "disabled" and bc.cget("state") == "normal")
    lbl4 = [l for l in find(t4, ctk.CTkLabel) if int(l.cget("width")) == 260][0]
    t4box = find(t4, ctk.CTkTextbox)[0]
    yield (lambda: "匹配" in "\n".join(t4box.get("1.0", "end").splitlines()[-15:]) or bs.cget("state") == "normal"), 1200
    R["progress_label"] = lbl4.cget("text")
    check("进度显示「第 i/N 期」", R["progress_label"].startswith("第 1/1 期"), R["progress_label"])
    yield 15
    zips1 = sorted(glob.glob(os.path.join(DATA, "26.08.01植被", "定位备份_*.zip")))
    check("原地模式：没有 _去定位 目录，备份在照片目录里", not glob.glob(os.path.join(out, "*_去定位")) and len(zips1) == 1,
          (glob.glob(os.path.join(out, "*")), zips1))
    bc.invoke()
    yield wait_idle(bs)
    R["after_cancel"] = st("26.08.01植被")
    check("取消后这一期「已取消」", R["after_cancel"] == "已取消", R["after_cancel"])
    new = R["boxes"][n0:]
    R["cancel_box"] = new
    check("取消弹窗提示重扫会显示待运行（上次已取消）、备份要留", new and "待运行（上次已取消）" in new[-1][2]
          and "定位备份" in new[-1][2], new)

    # 重扫 → 待运行（上次已取消）、默认勾上 → 再跑到完；旧结果目录改名保留
    btn(t4, "扫描").invoke()
    yield 0.8
    tb = table()
    check("重扫：上次被取消的期显示「待运行（上次已取消）」且勾上", tb["26.08.01植被"]["cells"][5] == "待运行（上次已取消）"
          and tb["26.08.01植被"]["on"], tb["26.08.01植被"]["cells"])
    old = glob.glob(os.path.join(out, "26.08.01植被*_控制点"))
    n0 = len(R["boxes"])
    root.after(800, lambda: fill_dialog(root, os.path.basename(DATA)))
    bs.invoke()
    yield 3
    yield (lambda: bs.cget("state") == "normal"), 3600
    R["stop_probe"] = True
    R["ui_max_gap_s"] = round(gaps["max"], 2)
    R["rerun_status"] = st("26.08.01植被")
    check("重跑做完（自动通过/需人工核对 + RMS3D）", R["rerun_status"].split(" ")[0] in ("自动通过", "需人工核对")
          and "mm" in R["rerun_status"], R["rerun_status"])
    kept = glob.glob(os.path.join(out, "26.08.01植被_控制点_上次已取消_*"))
    R["kept"] = kept
    check("旧结果目录改名保留（_上次已取消_<时间>），里面的报告和 已取消.txt 还在", len(kept) == 1 and
          os.path.isfile(os.path.join(kept[0], "控制点报告.txt")) and os.path.isfile(os.path.join(kept[0], "已取消.txt"))
          and old == [os.path.join(out, "26.08.01植被_控制点")], (old, kept))
    zips = sorted(glob.glob(os.path.join(DATA, "26.08.01植被", "定位备份_*.zip")))
    R["zips"] = zips
    check("重跑后仍没有 _去定位 目录", not glob.glob(os.path.join(out, "*_去定位")), glob.glob(os.path.join(out, "*")))
    new = R["boxes"][n0:]
    R["done_box"] = new
    check("完成弹窗", new and "批量迁移完成" in new[-1][1] and "汇总表" in new[-1][2], new)
    check("运行期间界面不卡（< 2 s）", gaps["max"] < 2.0, gaps["max"])
    sums = sorted(glob.glob(os.path.join(out, "批量迁移汇总_*.csv")))
    import csv as _csv
    rows1 = list(_csv.DictReader(open(sums[0], encoding="utf-8-sig")))
    rows = list(_csv.DictReader(open(sums[-1], encoding="utf-8-sig")))
    R["summary_rows"] = [rows1, rows]
    check("第一次（被取消）汇总表新两列：照片处理=原地抹除、定位备份=那份 zip", len(sums) == 2 and rows1
          and rows1[-1]["状态"] == "已取消" and rows1[-1]["照片处理"] == "原地抹除" and rows1[-1]["定位备份"] == zips1[0],
          rows1)
    # 取消时如果抹除已经做完，重跑时原片已无定位 → 不再写备份；取消落在抹除中途 → 重跑再写一份（两份都要留）
    again = rows and rows[-1]["照片处理"] == "原地抹除"
    check("重跑那次汇总表：照片处理 %s、定位备份和备份文件数对得上" % (rows[-1]["照片处理"] if rows else "?"),
          rows and rows[-1]["照片处理"] in ("原地抹除", "原片无定位") and zips1[0] in zips
          and len(zips) == (2 if again else 1) and (rows[-1]["定位备份"] == (zips[-1] if again else "")), (rows, zips))

    # 关窗口二次确认：清空跳过名单、复制模式跑 0802上午梯田，运行中点关窗口
    radios["copy"].invoke()
    set_entry(e_skip, "")
    btn(t4, "扫描").invoke()
    yield 0.8
    tb = table()
    if tb["26.08.01植被"]["on"]:
        tb["26.08.01植被"]["cb"].toggle()
    check("清空跳过名单后 0802上午梯田 待运行", table()["0802上午梯田"]["cells"][5] == "待运行", table()["0802上午梯田"]["cells"])
    bs.invoke()
    yield (lambda: st("0802上午梯田") == "运行中"), 120
    yield 3
    n0 = len(R["boxes"])
    R["close_test"] = "started"
    save()
    root.tk.call(root.protocol("WM_DELETE_WINDOW"))
    R["close_box"] = R["boxes"][n0:]
    R["close_label"] = lbl4.cget("text")
    check("跑批时点关窗口 → 二次确认，写明停在当前这一期", R["close_box"] and R["close_box"][0][0] == "askyesno"
          and "当前这一期" in R["close_box"][0][2], R["close_box"])
    check("确认后显示正在停止、窗口还在", root.winfo_exists() and "停" in R["close_label"], R["close_label"])
    save()
    yield 3600  # 当前这一期停好后程序自己关窗口；关窗后这里不会再执行


# ---------------- T10：日志区、分隔条、窗口记忆、工具条、着色、按钮颜色、统一弹窗、折行 ----------------
T10_NAMES = TAB_NAMES
T10_SAMPLES = [("失败：DJI_0001.JPG —— JpegError: 坏文件", "bad"), ("拒绝执行：输出目录非空", "bad"),
               ("跳过（不是 JPG）：说明.txt", "warn"), ("  → 需人工核对  6.2 mm  用时 5.1 分钟", "warn"),
               ("  → 自动通过  5.3 mm  用时 4.8 分钟", "good"), ("完成：成功 20，跳过 0，失败 0", "good"),
               ("完成：成功 18，跳过 0，失败 2", "bad"), ("完成：成功 5，无定位/跳过 3，失败 0，残留 0", "warn"),
               ("启动清理：已清理 0 个本软件 .tmp_part；清理失败 0 个", None), ("匹配照片… 40000 关键点", None)]
T10_COLORS = {"bad": ("#C62828", "#E57373"), "warn": ("#E65100", "#FFB74D"), "good": ("#2E7D32", "#81C784")}


def work_area():
    r = W.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def parse_geo(g):
    import re
    m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", g)
    return tuple(int(x) for x in m.groups()) if m else None


def tab_parts(root, nb, name):
    t = nb.tab(name)
    pw = [c for c in t.winfo_children() if isinstance(c, tkinter.PanedWindow)][0]
    form, logf = (t.nametowidget(str(x)) for x in pw.panes())
    lg = [l for l in root.ui_t10["logs"] if l["name"] == name][0]
    return t, pw, form, logf, lg


def visible_lines(tb):
    t = tb._textbox
    t.update_idletasks()
    h = t.winfo_height()
    first = int(t.index("@0,0").split(".")[0])
    n = 0
    for i in range(first, first + 60):
        d = t.dlineinfo("%d.0" % i)
        if d and d[1] + d[3] <= h:
            n += 1
    return n


def probe_dialog(root, action, store):
    tops = [w for w in root.winfo_children() if isinstance(w, ctk.CTkToplevel) and w.winfo_exists()]
    if not tops:
        root.after(200, lambda: probe_dialog(root, action, store))
        return
    d = tops[-1]
    if not d.winfo_viewable():  # 弹窗还没画出来，位置读不准
        root.after(100, lambda: probe_dialog(root, action, store))
        return
    d.update_idletasks()
    labels = find(d, ctk.CTkLabel)
    bs = getattr(d, "ui_buttons", None) or find(d, ctk.CTkButton)
    store.append({"title": d.title(), "kind": getattr(d, "ui_kind", None),
                  "icon": [labels[0].cget("text"), str(labels[0].cget("fg_color"))] if labels else None,
                  "texts": [l.cget("text") for l in labels],
                  "buttons": [b.cget("text") for b in bs], "btn_x": [b.winfo_rootx() for b in bs],
                  "icon_x": labels[0].winfo_rootx() if labels else None,
                  "text_x": labels[1].winfo_rootx() if len(labels) > 1 else None,
                  "textbox": bool(find(d, ctk.CTkTextbox)),
                  "focus": str(d.focus_get())})
    root.after(500, lambda: action(d))  # 软件 150 ms 后才把焦点放到输入框


def steps_t10(root, nb):
    phase = os.environ.get("UAV_T10_PHASE", "1")
    cfg_path = os.path.join(os.environ["APPDATA"], "无人机照片工具", "config.json")
    cfg = lambda: json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
    expect_path = os.path.join(OUT, "t10_expect.json")
    s = root._get_window_scaling()
    sw = ctk.ScalingTracker.get_widget_scaling(root)
    wa = work_area()
    R.update(phase=phase, scale=s, widget_scale=sw, work_area=wa, geometry_at_start=root.geometry(), cfg_at_start=cfg())
    check("窗口标题", root.title().endswith("v" + VERSION), root.title())
    yield 0.8

    if phase == "1":
        g = parse_geo(root.geometry())
        exp_w, exp_h = min(1000, int((wa[2] - wa[0]) * 0.9 / s)), min(760, int((wa[3] - wa[1]) * 0.9 / s))
        check("默认窗口 1000×760（不超过可用区 90%%；可用区 %d×%d，缩放 %.2f）" % (wa[2] - wa[0], wa[3] - wa[1], s),
              abs(g[0] - exp_w) <= 1 and abs(g[1] - exp_h) <= 1, (g, exp_w, exp_h))
        check("默认居中、在可用区内", wa[0] <= g[2] and g[2] + g[0] * s <= wa[2] and wa[1] <= g[3] < wa[3], g)
        check("minsize 不变（700×540）", (root._min_width, root._min_height) == (700, 540), (root._min_width, root._min_height))

        # ---- 闸门 2：窗口从最小拉到很大/最大化，多出的高度全给日志区 ----
        R["grow"] = {}
        for name in T10_NAMES:
            nb.set(name)
            t, pw, form, logf, lg = tab_parts(root, nb, name)
            meas = lambda: {"win": root.winfo_height(), "form": form.winfo_height(), "log": logf.winfo_height(),
                            "tb": lg["tb"].winfo_height(), "form_req": form.winfo_reqheight()}
            root.geometry("700x540")
            yield 1.0
            a = meas()
            root.geometry("700x%d+%d+%d" % (int((wa[3] - wa[1]) / s) - 60, wa[0], wa[1]))  # 只拉高
            yield 1.0
            b = meas()
            root.state("zoomed")
            yield 1.2
            c = meas()
            root.state("normal")
            yield 0.8
            log_min = int(pw.panecget(logf, "minsize"))
            R["grow"][name] = [a, b, c, log_min]
            # 表单永远不多拿（≤ 自然高度）；表单没到自然高度（窗口太矮）时日志只留最小 3 行；只拉高时多出的全给日志
            rule = all(m["form"] <= m["form_req"] + 1 and (m["form"] >= m["form_req"] - 1 or m["log"] <= log_min + 2)
                       for m in (a, b, c))
            if a["form"] >= a["form_req"] - 1:
                rule = rule and abs(b["form"] - a["form"]) <= 1 and abs((b["log"] - a["log"]) - (b["win"] - a["win"])) <= 2
            check("%s：窗口变大，表单不多拿、多出的高度全给日志（窗口 %d→%d→最大化 %d px，日志 %d→%d→%d，表单 %d/%d）"
                  % (name, a["win"], b["win"], c["win"], a["log"], b["log"], c["log"], c["form"], c["form_req"]),
                  rule and c["log"] >= b["log"] > a["log"] or rule and b["form"] < b["form_req"] - 1, [a, b, c, log_min])

        # ---- 闸门 2：分隔条拉到日志占 80% 以上、压到 3 行；再放到记忆用的位置 ----
        root.geometry("%dx%d+%d+%d" % (exp_w, exp_h, wa[0] + 60, wa[1] + 40))
        yield 1.0
        want = {}
        R["sash"] = {}
        for name, target in zip(T10_NAMES, (150, 170, 190, 330)):
            nb.set(name)
            t, pw, form, logf, lg = tab_parts(root, nb, name)
            yield 0.6
            H = pw.winfo_height()
            pw.sash_place(0, 0, int(H * 0.08))
            pw.event_generate("<ButtonRelease-1>", x=3, y=3)  # 拖完松手（sash_place 不发这个事件）
            yield 0.6
            ratio = logf.winfo_height() / t.winfo_height()
            check("%s：分隔条往上拉，日志占页签 %.0f%%（≥80%%）" % (name, ratio * 100), ratio >= 0.8,
                  (logf.winfo_height(), t.winfo_height()))
            root.ui_t10["log_add"](lg, "\n".join("第 %d 行" % i for i in range(1, 61)))
            lg["tb"].see("1.0")
            pw.sash_place(0, 0, H)
            pw.event_generate("<ButtonRelease-1>", x=3, y=3)  # 拖完松手（sash_place 不发这个事件）
            yield 0.6
            lg["tb"].see("1.0")
            yield 0.3
            n = visible_lines(lg["tb"])
            check("%s：分隔条压到底，日志还能看 3 行（实际 %d 行）" % (name, n), 3 <= n <= 4, n)
            pw.sash_place(0, 0, int(target * sw))
            pw.event_generate("<ButtonRelease-1>", x=3, y=3)  # 拖完松手（sash_place 不发这个事件）
            yield 0.6
            want[name] = round(form.winfo_height() / sw)
            R["sash"][name] = {"ratio_up": round(ratio, 3), "lines_down": n, "want": want[name],
                               "recorded": root.ui_t10["panes"][name]["h"]}
            check("%s：分隔条位置记下（逻辑 %s px）" % (name, want[name]), root.ui_t10["panes"][name]["h"] == want[name],
                  R["sash"][name])
            lg["tb"].delete("1.0", "end")

        # 批量迁移：分隔条往下拖，多出来的给扫描列表（列表那一行 weight=1）
        t, pw, form, logf, lg = tab_parts(root, nb, "批量迁移")
        nb.set("批量迁移")
        geo_keep = root.geometry()
        root.geometry("%dx%d" % (exp_w, int((wa[3] - wa[1]) / s) - 60))  # 拉高窗口，留出往下拖的余地
        yield 0.8
        lst = find(t, ctk.CTkScrollableFrame)[0]
        lst_outer = lst._parent_frame if hasattr(lst, "_parent_frame") else lst
        pw.sash_place(0, 0, form.winfo_reqheight())
        yield 0.5
        h_a = lst_outer.winfo_height()
        pw.sash_place(0, 0, form.winfo_reqheight() + int(150 * sw))
        yield 0.5
        h_b = lst_outer.winfo_height()
        R["batch_list_drag"] = [h_a, h_b]
        room = pw.winfo_height() - int(pw.panecget(logf, "minsize")) - form.winfo_reqheight()
        R["batch_list_drag"] = [h_a, h_b, room]
        if room >= int(160 * sw):
            check("批量迁移：分隔条往下拖 150 px，扫描列表跟着变高（%d→%d）" % (h_a, h_b), h_b - h_a >= int(140 * sw), R["batch_list_drag"])
        else:  # 屏幕太矮（如模拟 1.875 倍缩放时只有 821 逻辑像素高），表单已占满，没有往下拖的余地
            R["batch_list_drag_skipped"] = "屏幕太矮，没有往下拖的余地（%d px）" % room
        root.geometry(geo_keep)
        yield 0.8
        pw.sash_place(0, 0, int(want["批量迁移"] * sw))
        pw.event_generate("<ButtonRelease-1>", x=3, y=3)
        yield 0.5
        nb.set("剔除定位")

        # ---- 工具条（四个页签都有）+ 着色 + 5000 行 ----
        for name in T10_NAMES:
            t, pw, form, logf, lg = tab_parts(root, nb, name)
            items = [w.cget("text") for w in find(logf, ctk.CTkButton)] + [w.cget("text") for w in find(logf, ctk.CTkCheckBox)]
            check("%s：工具条（复制全部/清空/打开完整日志/自动滚到底/字号 −+）" % name,
                  all(x in items for x in ("复制全部", "清空", "打开完整日志", "自动滚到底", "−", "+")), items)
            check("%s：「打开完整日志」没有日志文件时不可点、「自动滚到底」默认勾" % name,
                  btn(logf, "打开完整日志").cget("state") == "disabled" and find(logf, ctk.CTkCheckBox)[0].get() == 1)
        nb.set("剔除定位")
        t, pw, form, logf, lg = tab_parts(root, nb, "剔除定位")
        tb = lg["tb"]
        yield 0.5
        for line, _ in T10_SAMPLES:
            root.ui_t10["log_add"](lg, line)
        got = [(tb.get("%d.0" % i, "%d.end" % i), [x for x in tb._textbox.tag_names("%d.0" % i) if x in T10_COLORS])
               for i in range(1, len(T10_SAMPLES) + 1)]
        R["tags"] = got
        check("着色：失败/拒绝红、跳过/需人工核对橙、自动通过/完成绿，「失败 0」不算",
              [g[1] for g in got] == [[w] if w else [] for _, w in T10_SAMPLES], got)
        fg = lambda: {k: tb._textbox.tag_cget(k, "foreground") for k in T10_COLORS}
        R["tag_fg_light"] = fg()
        ctk.set_appearance_mode("dark")
        yield 0.8
        R["tag_fg_dark"] = fg()
        R["sash_bg"] = [pw.cget("bg")]
        ctk.set_appearance_mode("light")
        yield 0.8
        R["sash_bg"].append(pw.cget("bg"))
        check("着色颜色：浅色/深色各一套（深色更浅）", R["tag_fg_light"] == {k: v[0] for k, v in T10_COLORS.items()}
              and R["tag_fg_dark"] == {k: v[1] for k, v in T10_COLORS.items()}, (R["tag_fg_light"], R["tag_fg_dark"]))
        check("分隔条颜色随浅/深色变", R["sash_bg"][0] != R["sash_bg"][1], R["sash_bg"])
        btn(logf, "复制全部").invoke()
        check("复制全部 → 剪贴板", root.clipboard_get() == tb.get("1.0", "end-1c"), len(root.clipboard_get()))
        btn(logf, "清空").invoke()
        check("清空", tb.get("1.0", "end-1c") == "")
        t0 = time.time()
        root.ui_t10["log_add"](lg, "\n".join("批量第 %d 行 跳过" % i if i % 7 == 0 else "批量第 %d 行" % i for i in range(1, 6001)))
        R["add_6000_s"] = round(time.time() - t0, 3)
        nlines = int(tb.index("end-1c").split(".")[0]) - 1
        check("5000 行上限：一次加 6000 行后剩 5000 行、最早的删掉（用时 %.2f s）" % R["add_6000_s"],
              nlines == 5000 and tb.get("1.0", "1.end").startswith("批量第 1001 行") and tb.get("5000.0", "5000.end") == "批量第 6000 行", nlines)
        t0 = time.time()
        for i in range(500):
            root.ui_t10["log_add"](lg, "再来一行 %d 完成" % i)
        R["add_500_single_s"] = round(time.time() - t0, 3)
        check("满 5000 行后再逐行加 500 行（= poll 一次最多处理的条数）用时 < 0.5 s（%.3f s）" % R["add_500_single_s"],
              R["add_500_single_s"] < 0.5 and int(tb.index("end-1c").split(".")[0]) - 1 == 5000)
        yield 0.5
        check("自动滚到底：在最下面", tb._textbox.yview()[1] >= 0.999, tb._textbox.yview())
        find(logf, ctk.CTkCheckBox)[0].toggle()
        tb.see("1.0")
        root.ui_t10["log_add"](lg, "不该滚下去")
        yield 0.5
        check("关掉自动滚到底：加日志不滚", tb._textbox.yview()[0] == 0.0, tb._textbox.yview())
        find(logf, ctk.CTkCheckBox)[0].toggle()
        yield 0.3
        check("重新勾上：滚到底", tb._textbox.yview()[1] >= 0.999, tb._textbox.yview())
        size_lbls = [l["size"] for l in root.ui_t10["logs"]]
        for _ in range(6):
            btn(logf, "−").invoke()
        lo = [x.cget("text") for x in size_lbls]
        for _ in range(12):
            btn(logf, "+").invoke()
        hi = [x.cget("text") for x in size_lbls]
        f_hi = tb._textbox.cget("font")
        for _ in range(4):
            btn(logf, "−").invoke()
        check("字号 −/+：10～18，四个页签一起变", lo == ["10"] * len(TAB_NAMES) and hi == ["18"] * len(TAB_NAMES) and [x.cget("text") for x in size_lbls] == ["14"] * len(TAB_NAMES),
              (lo, hi, f_hi))
        btn(logf, "清空").invoke()

        # ---- 按钮颜色 + 打开完整日志（真跑一次剔除定位） ----
        theme_fg = ctk.ThemeManager.theme["CTkButton"]["fg_color"]
        b_start, b_stop = btn(form, "开始"), btn(form, "中止")
        R["btn_idle"] = [b_start.cget("fg_color"), b_stop.cget("fg_color"), b_stop.cget("state")]
        check("空闲：「开始」蓝、「中止」不可点且底色灰", tuple(b_start.cget("fg_color")) == tuple(theme_fg)
              and tuple(b_stop.cget("fg_color")) == ("gray74", "gray33") and b_stop.cget("state") == "disabled", R["btn_idle"])
        src = os.path.join(DATA, "strip", "T6复制")
        set_entry(entry_row(nb.tab("剔除定位"), 0), src)
        set_entry(entry_row(nb.tab("剔除定位"), 1), os.path.join(OUT, "strip%s_去定位" % time.strftime("%H%M%S")))
        b_start.invoke()
        R["btn_run"] = [b_start.cget("fg_color"), b_stop.cget("fg_color"), b_stop.cget("state")]
        check("运行中：「开始」不可点且灰、「中止」橙色可点", tuple(b_start.cget("fg_color")) == ("gray74", "gray33")
              and b_start.cget("state") == "disabled" and tuple(b_stop.cget("fg_color")) == ("#E65100", "#EF6C00")
              and b_stop.cget("state") == "normal", R["btn_run"])
        yield (lambda: b_start.cget("state") == "normal"), 120
        check("跑完：「打开完整日志」可点、点了用系统程序打开日志 CSV", btn(logf, "打开完整日志").cget("state") == "normal"
              and lg["file"] and lg["file"].endswith(".csv"), lg["file"])
        btn(logf, "打开完整日志").invoke()
        check("打开完整日志 → os.startfile(日志)", R.get("startfile") == [lg["file"]], R.get("startfile"))
        R["strip_log"] = tb.get("1.0", "end-1c").splitlines()[-3:]
        R["strip_log_tags"] = [list(tb._textbox.tag_names("%d.0" % i)) for i in range(1, int(tb.index("end-1c").split(".")[0]))][-3:]

        # 其他页签的「取消」初始灰、批量页签「扫描」可点
        for name in ("空三", "控制点", "批量迁移"):
            f = form_of(nb.tab(name))
            bc = btn(f, "取消")
            check("%s：「取消」空闲时不可点、底色灰；「开始」蓝" % name, bc.cget("state") == "disabled"
                  and tuple(bc.cget("fg_color")) == ("gray74", "gray33") and tuple(btn(f, "开始").cget("fg_color")) == tuple(theme_fg))
        f3 = form_of(nb.tab("控制点"))
        radios = {r.cget("text"): r for r in find(f3, ctk.CTkRadioButton)}
        radios["建参考期（新场地第一次）"].invoke()
        browse = [b for b in find(f3, ctk.CTkButton) if b.cget("text") == "浏览…"]
        R["browse_init"] = [(b.cget("state"), str(b.cget("fg_color"))) for b in browse]
        radios["迁移（新一期，零刺点）"].invoke()
        check("控制点：模式切换后不可点的「浏览…」底色变浅", any(st == "disabled" and "gray90" in fgc for st, fgc in R["browse_init"])
              and any(st == "normal" and "gray82" in fgc for st, fgc in R["browse_init"]), R["browse_init"])

        # ---- 统一弹窗：用软件自己的弹窗（不是记录器），查标题、图标、按钮位置、回车/Esc ----
        dl = R.setdefault("dialogs", [])
        root.after(300, lambda: probe_dialog(root, lambda d: d.event_generate("<Return>"), dl))
        ORIG_BOX["showinfo"]("标题甲", "信息：输出到「<照片目录名>_空三」，Blocks Exchange XML 不带坐标系。")
        root.after(300, lambda: probe_dialog(root, lambda d: d.event_generate("<Escape>"), dl))
        ORIG_BOX["showwarning"]("标题乙", "警告文字")
        root.after(300, lambda: probe_dialog(root, lambda d: d.ui_buttons[0].invoke(), dl))
        ORIG_BOX["showerror"]("标题丙", "错误：\n" + "\n".join("堆栈第 %d 行" % i for i in range(40)))
        root.after(300, lambda: probe_dialog(root, lambda d: d.event_generate("<Return>"), dl))
        yes1 = ORIG_BOX["askyesno"]("标题丁", "确定吗？")
        root.after(300, lambda: probe_dialog(root, lambda d: d.event_generate("<Escape>"), dl))
        yes2 = ORIG_BOX["askyesno"]("标题戊", "危险操作，确定吗？", icon="warning")
        root.after(300, lambda: probe_dialog(root, lambda d: d.ui_buttons[1].invoke(), dl))
        yes3 = ORIG_BOX["askyesno"]("标题己", "点「否」")
        R["ask_results"] = [yes1, yes2, yes3]
        kinds = [d["kind"] for d in dl]
        check("统一弹窗：信息/警告/错误/询问 四种图标", kinds == ["info", "warning", "error", "question", "warning", "question"]
              and [d["icon"][0] for d in dl] == ["i", "!", "×", "?", "!", "?"], [(d["kind"], d["icon"]) for d in dl])
        check("统一弹窗：标题照传、图标在文字左边、按钮靠右且「是」在「否」左边",
              [d["title"] for d in dl] == ["标题甲", "标题乙", "标题丙", "标题丁", "标题戊", "标题己"]
              and all(d["icon_x"] < (d["text_x"] or 10 ** 6) for d in dl if not d["textbox"])
              and dl[3]["buttons"] == ["是", "否"] and dl[3]["btn_x"][0] < dl[3]["btn_x"][1] and dl[0]["buttons"] == ["确定"], dl)
        check("统一弹窗：很长的文字放进可滚动文本框", dl[2]["textbox"] and not dl[0]["textbox"])
        check("询问框：回车=是、Esc=否、点「否」=否", R["ask_results"] == [True, False, False], R["ask_results"])
        check("信息框里英文词不拆开", "Blocks Exchange" in dl[0]["texts"][1].replace("\n", " ") or "Blocks\nExchange" in dl[0]["texts"][1],
              dl[0]["texts"])

        # 原地确认框：焦点在输入框、回车不确认、按钮靠右
        f1 = form_of(nb.tab("剔除定位"))
        ip = os.path.join(DATA, "inplace", "T6原地")
        before = sha(ip)
        set_entry(entry_row(nb.tab("剔除定位"), 0), ip)
        cbs = {c.cget("text"): c for c in find(f1, ctk.CTkCheckBox)}
        cbs["原地抹除（直接改原照片，不复制）"].toggle()
        st_ip = {}

        def ip_action(d):
            ent = find(d, ctk.CTkEntry)[0]
            # 测试进程拿不到前台时 focus_get() 是 None；focus_lastfor() 是这个窗口一拿到焦点就给谁（即默认焦点）
            st_ip["focus_get"], st_ip["focus_lastfor"] = str(d.focus_get()), str(d.focus_lastfor())
            st_ip["t_probe"] = round(time.time() - T0, 3)
            st_ip["focus_is_entry"] = d.focus_get() is ent._entry or d.focus_get() is None and d.focus_lastfor() is ent._entry
            ent.insert(0, os.path.basename(ip))
            ent._entry.event_generate("<Return>")
            d.update()
            st_ip["alive_after_return"] = bool(d.winfo_exists())
            st_ip["buttons"] = [(b.cget("text"), b.winfo_rootx()) for b in find(d, ctk.CTkButton)]
            btn(d, "取消").invoke()

        n0 = len(R["boxes"])
        R["focus_log"] = []
        root.bind_all("<FocusIn>", lambda e: R["focus_log"].append([round(time.time() - T0, 3), str(e.widget)]), "+")
        root.after(400, lambda: probe_dialog(root, ip_action, dl))
        btn(f1, "开始").invoke()
        st_ip["boxes"] = R["boxes"][n0:]
        R["inplace_dialog"] = st_ip
        check("原地确认框：默认焦点在输入框；输对名字按回车也不确认；取消后照片没动",
              st_ip.get("focus_is_entry") and st_ip.get("alive_after_return") and sha(ip) == before and not st_ip["boxes"], st_ip)
        bx = dict(st_ip.get("buttons", []))
        check("原地确认框：按钮靠右，「确认抹除」在「取消」左边", bx and bx["确认抹除"] < bx["取消"], st_ip.get("buttons"))
        cbs["原地抹除（直接改原照片，不复制）"].toggle()

        # ---- 说明文字折行：没有不换行空格、英文词不拆、每行不超宽 ----
        bad = []
        hints = [l for l in find(root, ctk.CTkLabel) if hasattr(l, "raw_text")]
        for l in hints:
            text = l.cget("text")
            if " " in text:
                bad.append(("nbsp", l.raw_text[:20]))
            lines = text.split("\n")
            for a, b in zip(lines, lines[1:]):
                if a and b and a[-1].isascii() and a[-1].isalnum() and b[0].isascii() and b[0].isalnum():
                    bad.append(("拆词", a[-10:], b[:10]))
            if "".join(text.split()) != "".join(l.raw_text.split()):
                bad.append(("文字变了", l.raw_text[:20]))
            font = l._label.cget("font")
            for ln in lines:
                if int(root.tk.call("font", "measure", font, ln)) > l.wrap_px + 1:
                    bad.append(("超宽", ln[:20]))
        R["hints"] = [(l.raw_text[:16], l.cget("text").count("\n") + 1) for l in hints]
        check("说明文字：%d 段（四个页签各一段），按宽度折行、无不换行空格、英文词不拆、文字不变" % len(hints),
              len(hints) == len(TAB_NAMES) and not bad, bad)
        nb.set("空三")
        yield 0.8
        t2_, pw2, f2_, lf2, lg2_ = tab_parts(root, nb, "空三")
        pw2.sash_place(0, 0, f2_.winfo_reqheight() + 2)  # 拖回表单自然高度附近 → 当没拖过（跟着内容走）
        pw2.event_generate("<ButtonRelease-1>", x=3, y=3)
        yield 0.5
        check("分隔条拖回表单自然高度附近 → 不再记这个页签（跟着内容走）", root.ui_t10["panes"]["空三"]["h"] is None,
              root.ui_t10["panes"]["空三"]["h"])
        h2 = [l for l in hints if l.winfo_ismapped()][0]
        wide = (h2.cget("text").count("\n") + 1, h2.winfo_width())
        root.geometry("720x600")
        yield 1.0
        narrow = (h2.cget("text").count("\n") + 1, h2.winfo_width())
        over = [ln for ln in h2.cget("text").split("\n")
                if int(root.tk.call("font", "measure", h2._label.cget("font"), ln)) > h2.winfo_width() + 2]
        R["hint_wide_narrow"] = [wide, narrow, h2.cget("text")]
        f2, b2 = form_of(nb.tab("空三")), btn(form_of(nb.tab("空三")), "开始")
        R["narrow_form"] = [f2.winfo_height(), f2.winfo_reqheight(), b2.winfo_rooty() + b2.winfo_height() - f2.winfo_rooty()]
        check("窗口变窄、说明多折一行后表单跟着变高，「开始」按钮没被裁掉", abs(f2.winfo_height() - f2.winfo_reqheight()) <= 1
              and R["narrow_form"][2] <= f2.winfo_height(), R["narrow_form"])
        check("窗口变窄后说明文字重新折行（%d 行→%d 行）、不超出" % (wide[0], narrow[0]), narrow[0] > wide[0] and not over,
              R["hint_wide_narrow"])
        pw2.sash_place(0, 0, int(want["空三"] * sw))  # 放回记忆测试用的位置
        pw2.event_generate("<ButtonRelease-1>", x=3, y=3)
        yield 0.5
        nb.set("剔除定位")

        # ---- 关窗：记住窗口、分隔条、字号 ----
        root.geometry("1050x700+%d+%d" % (wa[0] + 130, wa[1] + 90))
        yield 1.0
        exp = {"geometry": root.geometry(), "panes": {k: root.ui_t10["panes"][k]["h"] for k in T10_NAMES}, "font": 14}
        check("各页签分隔条位置没被拉窗口改掉", all(exp["panes"][k] == want[k] for k in T10_NAMES), (exp["panes"], want))
        json.dump(exp, open(expect_path, "w", encoding="utf-8"), ensure_ascii=False)
        R["expect"] = exp
        save()
        root.tk.call(root.protocol("WM_DELETE_WINDOW"))  # 软件自己的关窗：先记 config 再关
        c = cfg()
        R["cfg_after_close"] = c
        check("关窗后 config 记了窗口/分隔条/字号", c.get("window", {}).get("geometry") == exp["geometry"]
              and c.get("panes") == exp["panes"] and c.get("log_font_size") == 14 and c["window"]["zoomed"] is False, c)
        R["done"] = True
        save()
        yield 3600

    if phase == "loop":  # 反复开关（不动窗口）：大小位置、分隔条一点都不能变（防 DPI 缩放重复、菜单栏吃高度）
        c0 = cfg()
        check("开关一次：窗口大小位置不变（%s）" % (c0.get("window") or {}).get("geometry"),
              root.geometry() == (c0.get("window") or {}).get("geometry", root.geometry()), (root.geometry(), c0.get("window")))
        save()
        root.tk.call(root.protocol("WM_DELETE_WINDOW"))
        c1 = cfg()
        R["cfg_after_close"] = c1
        check("开关一次：config 里窗口和分隔条不变", c1.get("window") == c0.get("window") and c1.get("panes") == c0.get("panes"),
              (c0.get("window"), c1.get("window"), c0.get("panes"), c1.get("panes")))
        R["done"] = True
        save()
        yield 3600
    exp = json.load(open(expect_path, encoding="utf-8"))
    if phase == "2":  # 重开：恢复大小、位置、分隔条、字号；再最大化后关
        check("重开：窗口大小和位置恢复（%s）" % exp["geometry"], root.geometry() == exp["geometry"],
              (root.geometry(), exp["geometry"]))
        got = {}
        for name in T10_NAMES:
            nb.set(name)
            yield 0.6
            t, pw, form, logf, lg = tab_parts(root, nb, name)
            got[name] = round(form.winfo_height() / sw)
        R["panes_restored"] = got
        check("重开：四个页签分隔条位置恢复（±2 px）", all(abs(got[k] - exp["panes"][k]) <= 2 for k in T10_NAMES), (got, exp["panes"]))
        check("重开：日志字号恢复 14", all(l["size"].cget("text") == "14" for l in root.ui_t10["logs"]))
        nb.set("剔除定位")
        root.state("zoomed")
        yield 1.2
        save()
        root.tk.call(root.protocol("WM_DELETE_WINDOW"))
        c = cfg()
        R["cfg_after_close"] = c
        check("最大化着关：记 zoomed=True，大小位置仍是没最大化时的", c["window"]["zoomed"] is True
              and c["window"]["geometry"] == exp["geometry"], c.get("window"))
        R["done"] = True
        save()
        yield 3600
    if phase == "3":  # 重开：最大化恢复；还原后回到原大小；再把位置改到屏幕外
        yield 0.8
        check("重开：最大化恢复", root.state() == "zoomed", root.state())
        root.state("normal")
        yield 0.8
        check("取消最大化后回到记住的大小位置", root.geometry() == exp["geometry"], (root.geometry(), exp["geometry"]))
        c = cfg()
        c["window"] = {"geometry": "1000x700+30000+30000", "zoomed": False}
        json.dump(c, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False)
        return
    if phase == "4":  # 位置在屏幕外 → 居中
        g = parse_geo(root.geometry())
        cx, cy = g[2] + g[0] * s / 2, g[3] + g[1] * s / 2
        check("记住的位置在屏幕外 → 居中显示（大小照记住的）", (g[0], g[1]) == (1000, 700) and abs(cx - (wa[0] + wa[2]) / 2) < 3
              and abs(cy - (wa[1] + wa[3]) / 2) < 60, (g, wa))
        return


def steps_t10long(root, nb):
    """T10 长任务：「剔除定位」复制方式跑 --data 下的 长任务\\（几百张照片副本 + 几千个非 JPG + 1 张坏 JPG），
    日志 6000 多行：查 5000 行上限、着色、「打开完整日志」、运行中界面最大卡顿；运行中、跑完各截浅/深色。"""
    shots = os.path.join(OUT, "截图")
    os.makedirs(shots, exist_ok=True)
    root.geometry("+40+40")
    check("窗口标题", root.title().endswith("v" + VERSION), root.title())
    nb.set("剔除定位")
    t, pw, form, logf, lg = tab_parts(root, nb, "剔除定位")
    tb = lg["tb"]
    yield 0.5
    src = os.path.join(DATA, "长任务")
    dst = os.path.join(OUT, "长任务_去定位")
    set_entry(entry_row(nb.tab("剔除定位"), 0), src)
    set_entry(entry_row(nb.tab("剔除定位"), 1), dst)
    gaps = {"last": time.time(), "max": 0.0, "n": 0}

    def probe():
        now = time.time()
        gaps["max"] = max(gaps["max"], now - gaps["last"])
        gaps["last"] = now
        gaps["n"] += 1
        if not R.get("stop_probe"):
            root.after(100, probe)

    b_start = btn(form, "开始")
    t0 = time.time()
    b_start.invoke()
    gaps["last"] = time.time()
    root.after(100, probe)
    yield (lambda: int(tb.index("end-1c").split(".")[0]) > 1500 or b_start.cget("state") == "normal"), 600
    R["lines_mid"] = int(tb.index("end-1c").split(".")[0]) - 1
    for mode, cn in (("light", "浅色"), ("dark", "深色")):
        ctk.set_appearance_mode(mode)
        yield 0.6
        p = os.path.join(shots, "长任务_运行中_%s.bmp" % cn)
        grab_bmp(p, root)
        R.setdefault("shots", []).append(p)
    ctk.set_appearance_mode("light")
    yield (lambda: b_start.cget("state") == "normal"), 3600
    R["elapsed_run_s"] = round(time.time() - t0, 1)
    R["stop_probe"] = True
    R["ui_max_gap_s"] = round(gaps["max"], 3)
    R["ui_ticks"] = gaps["n"]
    yield 0.5
    n = int(tb.index("end-1c").split(".")[0]) - 1
    text = tb.get("1.0", "end-1c").splitlines()
    tags = {}
    for i in range(1, n + 1):
        for x in tb._textbox.tag_names("%d.0" % i):
            if x in T10_COLORS:
                tags[x] = tags.get(x, 0) + 1
    R["log_lines"], R["log_tags"], R["log_head"], R["log_tail"] = n, tags, text[:2], text[-3:]
    R["boxes_end"] = R["boxes"][-1:]
    csvp = lg["file"]
    import csv as _csv
    rows = list(_csv.reader(open(csvp, encoding="utf-8-sig"))) if csvp and os.path.isfile(csvp) else []
    R["csv"], R["csv_rows"] = csvp, len(rows)
    check("跑完（%.0f 秒）：完成弹窗写 成功 354、失败 1" % R["elapsed_run_s"], R["boxes"] and "成功 354" in R["boxes"][-1][2]
          and "失败 1" in R["boxes"][-1][2], R["boxes"][-1:])
    check("日志框正好 5000 行（这次日志 6000 多行，最早的删掉了）", n == 5000 and not text[0].startswith("启动清理"), (n, text[:2]))
    check("完整日志 CSV 每个文件都有一行（%d 行）" % len(rows), len(rows) >= 6355, len(rows))
    done_i = max(i for i, x in enumerate(text, 1) if x.startswith("完成："))
    check("着色：跳过行橙、「完成：…失败 1」红", tags.get("warn", 0) >= 4990 and "bad" in tb._textbox.tag_names("%d.0" % done_i),
          (tags, text[done_i - 1]))
    check("「打开完整日志」可点", btn(logf, "打开完整日志").cget("state") == "normal")
    check("运行中界面不卡（100 ms 定时器最大间隔 %.2f s < 1 s）" % gaps["max"], gaps["max"] < 1.0, gaps)
    for mode, cn in (("light", "浅色"), ("dark", "深色")):
        ctk.set_appearance_mode(mode)
        yield 0.6
        p = os.path.join(shots, "长任务_跑完_%s.bmp" % cn)
        grab_bmp(p, root)
        R.setdefault("shots", []).append(p)
    ctk.set_appearance_mode("light")


def steps_t9(root, nb):
    """T9「影像处理」页签：--data 指向照片上级目录（只读，某个月的照片目录），结果写 <结果目录>/gui_out。
    扫描（界面不卡）、默认勾选、筛选、逐个改坐标系 / 全部统一 / 记 config / 重扫沿用、DOM/DSM 勾选框，
    再真跑一个工点（只出 XML）：运行中取消 → 重扫「待处理（上次已取消）」→ 重跑做完、旧结果目录改名保留 → 重扫「已有结果」；
    浅/深色截图；最后跑另一个工点时点关窗口（软件自己关，harness_t9.json 里没有 done，记到 close_sent 为止）。
    环境变量 UAV_T9_SITES 指向本地工点配置 JSON（真实工点名和张数不进仓库，键：site、close_site、boundary_site、ortho_only_site、n=[三个工点张数]、filter），
    UAV_T9_NO_RUN=1 只测到截图（不起 Metashape）。"""
    cfg_path = os.path.join(os.environ["APPDATA"], "无人机照片工具", "config.json")
    cfg = lambda: json.load(open(cfg_path, encoding="utf-8"))
    out = os.path.join(OUT, "gui_out")
    shots = os.path.join(OUT, "截图")
    os.makedirs(shots, exist_ok=True)
    sites = json.load(open(os.environ["UAV_T9_SITES"], encoding="utf-8"))  # 本地工点配置，真实工点名不进仓库
    site, close_site, xigu2 = sites["site"], sites["close_site"], sites["boundary_site"]
    ortho_only, n_exp, filt = sites["ortho_only_site"], sites["n"], sites.get("filter", sites["close_site"])
    root.geometry("+40+40")
    check("窗口标题", root.title().endswith("v" + VERSION), root.title())
    check("五个页签", list(nb._name_list) == TAB_NAMES, nb._name_list)
    nb.set("影像处理")
    t5 = nb.tab("影像处理")
    ui = root.ui_t9
    yield 0.5
    e_root, e_out, e_ms = (entry_row(t5, r) for r in range(3))
    check("Metashape 自动填上", e_ms.get() == MS, e_ms.get())
    btn(t5, "开始").invoke()
    check("没扫描就开始 → 提示先扫描", R["boxes"] and "扫描" in R["boxes"][-1][2], R["boxes"][-1:])
    set_entry(e_root, DATA)
    set_entry(e_out, os.path.join(DATA, "输出"))
    btn(t5, "扫描").invoke()
    yield (lambda: not ui["st5"]["scanning"]), 600
    check("输出目录在照片目录里 → 拒绝（原片只读），没建目录", "照片目录里面" in (R["boxes"][-1][2] or "")
          and not os.path.exists(os.path.join(DATA, "输出")), R["boxes"][-1:])
    set_entry(e_out, out)
    gaps = {"last": time.time(), "max": 0.0, "on": True}

    def probe():  # 界面线程每 200 ms 一次；最大间隔就是界面最长卡了多久；超过 0.5 s 的记下时刻，好对上是哪一步
        now = time.time()
        gaps["max"] = max(gaps["max"], now - gaps["last"])
        if now - gaps["last"] > 0.5:
            R.setdefault("t9_gaps", []).append([round(gaps["last"] - T0, 1), round(now - gaps["last"], 2)])
        gaps["last"] = now
        if gaps["on"]:
            root.after(200, probe)

    def mark(name):
        R.setdefault("t9_events", []).append([round(time.time() - T0, 1), name])
    root.after(200, probe)
    t0 = time.time()
    mark("扫描开始")
    btn(t5, "扫描").invoke()
    yield 0.3
    check("扫描中：扫描/开始不可点、取消可点", btn(t5, "扫描").cget("state") == "disabled"
          and btn(t5, "开始").cget("state") == "disabled" and btn(t5, "取消").cget("state") == "normal")
    yield (lambda: not ui["st5"]["scanning"]), 900
    check("扫描完成、界面不卡（200 ms 定时器最大间隔 < 1 s）", not ui["st5"]["scanning"] and gaps["max"] < 1.0,
          "用时 %.1f s，最大间隔 %.2f s" % (time.time() - t0, gaps["max"]))
    rows = ui["rows"]()
    R["t9_scan"] = [[r["item"]["idx"], r["item"]["rel"], r["item"]["n"], r["item"]["n_gps"], r["srs_var"].get(),
                     r["status"].cget("text"), bool(r["var"].get()), r["widgets"][7].cget("text")] for r in rows]
    by = {r["item"]["name"]: r for r in rows}
    top = [r for r in rows if not r["item"]["nested"]]
    check("工点 46 个：直接子文件夹 23 个（默认勾）+ 无正射影像 下 23 个（默认不勾）",
          len(rows) == 46 and len(top) == 23 and all(r["var"].get() for r in top)
          and not any(r["var"].get() for r in rows if r["item"]["nested"]), (len(rows), len(top)))
    check("照片数 = 根目录 + 正摄（第三个工点只有正摄）",
          [by[k]["item"]["n"] for k in (site, close_site, ortho_only)] == n_exp,
          [by[k]["item"]["n"] for k in (site, close_site, ortho_only)])
    check("坐标系列：自动 EPSG:4543 CM102；靠近分带线的标在备注", by[site]["srs_var"].get() == "自动 EPSG:4543 CM102"
          and "靠近分带线" in by[xigu2]["widgets"][7].cget("text") and by[xigu2]["srs_var"].get() == "自动 EPSG:4544 CM105",
          (by[site]["srs_var"].get(), by[xigu2]["srs_var"].get(), by[xigu2]["widgets"][7].cget("text")))
    # 筛选 + 勾选按钮只作用于显示的行
    flt = [e for e in find(t5, ctk.CTkEntry) if e.cget("textvariable") and e.master.master is form_of(t5)
           and e.cget("width") == 120][0]
    set_entry(flt, filt)
    yield 0.3
    shown = [r for r in rows if r["widgets"][0].grid_info()]
    btn(t5, "全不选").invoke()
    check("筛选只显示 1 行；全不选只动显示的行", len(shown) == 1 and not by[close_site]["var"].get()
          and by[site]["var"].get(), len(shown))
    set_entry(flt, "")
    yield 0.3
    btn(t5, "全选").invoke()
    btn(t5, "只勾待处理").invoke()
    check("只勾待处理：23 个直接子文件夹的工点", sum(r["var"].get() for r in rows) == 23
          and all(r["var"].get() for r in top), sum(r["var"].get() for r in rows))
    # 逐个改坐标系（下拉菜单选择 = _dropdown_callback）、记 config、重扫沿用、改回自动
    by[close_site]["srs"]._dropdown_callback("EPSG:4544 CM105")
    yield 0.3
    check("逐个改：一个工点 → EPSG:4544（手动），记进 config", by[close_site]["item"]["epsg"] == 4544
          and by[close_site]["item"]["epsg_src"] == "手动" and cfg().get("survey_epsg", {}).get(close_site) == 4544,
          cfg().get("survey_epsg"))
    btn(t5, "扫描").invoke()
    yield 0.3
    yield (lambda: not ui["st5"]["scanning"]), 300
    rows = ui["rows"]()
    by = {r["item"]["name"]: r for r in rows}
    check("重扫：手动改的沿用（EPSG:4544 CM105，手动）", by[close_site]["srs_var"].get() == "EPSG:4544 CM105"
          and by[close_site]["item"]["epsg_src"] == "手动", by[close_site]["srs_var"].get())
    by[close_site]["srs"]._dropdown_callback(by[close_site]["srs"].cget("values")[0])
    yield 0.3
    check("改回自动：从 config 删掉", by[close_site]["item"]["epsg"] == 4543
          and close_site not in cfg().get("survey_epsg", {}), cfg().get("survey_epsg"))
    unify = [w for w in find(t5, ctk.CTkOptionMenu) if w.cget("width") == 170][0]
    unify._dropdown_callback("EPSG:4544 CM105")
    btn(t5, "统一").invoke()
    yield 0.3
    check("全部统一为 EPSG:4544：46 个都改成手动、都记 config", all(r["item"]["epsg"] == 4544 and r["item"]["epsg_src"] == "手动"
                                                     for r in rows) and len(cfg().get("survey_epsg", {})) == 46)
    unify._dropdown_callback("自动（按工点）")
    btn(t5, "统一").invoke()
    yield 0.3
    check("全部改回自动：config 清空、各回各的带", cfg().get("survey_epsg") == {} and by[site]["item"]["epsg"] == 4543
          and by[xigu2]["item"]["epsg"] == 4544)
    cb = [c for c in find(t5, ctk.CTkCheckBox) if c.cget("text").startswith("同时出 DOM")][0]
    e_res = [e for e in find(t5, ctk.CTkEntry) if e.get() == "0.03"][0]
    check("DOM/DSM 默认不勾、分辨率框不可编辑", not cb.get() and e_res.cget("state") == "disabled", (cb.get(), e_res.cget("state")))
    # T11/D-31 自动裁边：勾选框默认勾、往里收默认 3 米、不勾 DOM/DSM 时都禁用
    cb_clip = [c for c in find(t5, ctk.CTkCheckBox) if c.cget("text").startswith("自动裁边")][0]
    e_inset = [e for e in find(t5, ctk.CTkEntry) if e.get() in ("3", "3.0")][0]
    check("自动裁边勾选框默认勾、往里收默认 3 米；没勾 DOM/DSM 时两个都禁用",
          cb_clip.get() and e_inset.get() in ("3", "3.0") and cb_clip.cget("state") == "disabled"
          and e_inset.cget("state") == "disabled",
          (cb_clip.get(), e_inset.get(), cb_clip.cget("state"), e_inset.cget("state")))
    cb.toggle()
    yield 0.2
    check("勾上 DOM/DSM：分辨率框可编辑，默认 0.03；裁边勾选框和往里收可编辑", e_res.cget("state") == "normal"
          and e_res.get() == "0.03" and cb_clip.cget("state") == "normal" and e_inset.cget("state") == "normal")
    cb_clip.toggle()
    yield 0.2
    check("不勾自动裁边：往里收框变灰", e_inset.cget("state") == "disabled")
    cb_clip.toggle()
    yield 0.2
    check("再勾上自动裁边：往里收框又能编辑", e_inset.cget("state") == "normal")
    set_entry(e_inset, "5.5")
    yield 0.2
    btn(t5, "扫描").invoke()
    yield 0.3
    yield (lambda: not ui["st5"]["scanning"]), 300
    check("往里收改 5.5 记进 config（survey_inset）", cfg().get("survey_inset") == "5.5", cfg().get("survey_inset"))
    set_entry(e_inset, "3")
    yield 0.2
    # T12/D-32：点云密度门槛（默认 50、跟往里收一起变灰、记 config）和「按范围线重新裁剪…」
    e_den = [e for e in find(t5, ctk.CTkEntry) if e.get() in ("50", "50.0")][0]
    check("点云密度门槛默认 50；勾着 DOM/DSM + 自动裁边时可编辑", e_den.cget("state") == "normal", e_den.get())
    cb_clip.toggle()
    yield 0.2
    check("不勾自动裁边：点云密度门槛也变灰", e_den.cget("state") == "disabled")
    cb_clip.toggle()
    yield 0.2
    set_entry(e_den, "40")
    btn(t5, "扫描").invoke()
    yield 0.3
    yield (lambda: not ui["st5"]["scanning"]), 300
    check("点云密度门槛改 40 记进 config（survey_density）", cfg().get("survey_density") == "40", cfg().get("survey_density"))
    set_entry(e_den, "50")
    b_re = btn(t5, "按范围线重新裁剪…")
    check("有「按范围线重新裁剪…」按钮、空闲时可点", b_re.cget("state") == "normal")
    fake = os.path.join(OUT, "reclip_fake")
    for n, st in (("工点甲", "完成"), ("工点乙", "失败"), ("工点丙", "部分对齐")):
        d = os.path.join(fake, n)
        os.makedirs(d, exist_ok=True)
        for f in (n + ".psx", n + "DOM.tif", n + "DSM.tif"):
            open(os.path.join(d, f), "wb").close()
        open(os.path.join(d, "处理报告.txt"), "w", encoding="utf-8").write("影像处理报告\n\n状态：%s\n" % st)
    open(os.path.join(fake, "工点甲", "工点甲_范围线.kml"), "w", encoding="utf-8").write("<kml/>")
    nbox = len(R["boxes"])
    set_entry(e_out, os.path.join(OUT, "没有这个目录"))
    b_re.invoke()
    check("输出目录不存在 → 提示先选输出目录", len(R["boxes"]) > nbox and "输出目录" in R["boxes"][-1][2], R["boxes"][-1:])
    set_entry(e_out, fake)
    seen = {}

    def inspect():
        dlg = getattr(root, "ui_reclip_dlg", None)
        if dlg is None or not dlg.winfo_exists():
            root.after(200, inspect)
            return
        u = dlg.ui_reclip
        seen["sites"] = [os.path.basename(d) for v, d in u["rows"]]
        seen["texts"] = [c.cget("text") for c in find(dlg, ctk.CTkCheckBox)]
        seen["src"] = u["src"].get()
        dlg.update()
        shot = os.path.join(shots, "影像处理_按范围线重新裁剪对话框.bmp")
        grab_bmp(shot, dlg)
        R.setdefault("shots", []).append(shot)
        n0 = len(R["boxes"])
        u["go"]()                                                   # 一个都没勾
        seen["none"] = R["boxes"][n0:]
        u["rows"][0][0].set(True)
        u["src"].set("auto")
        u["density"].set("95")
        n0 = len(R["boxes"])
        u["go"]()                                                   # 门槛超范围
        seen["bad_density"] = R["boxes"][n0:]
        u["src"].set("file")
        u["file"].set(os.path.join(fake, "工点甲", "工点甲.psx"))
        u["density"].set("50")
        n0 = len(R["boxes"])
        u["go"]()                                                   # 不是 KML/SHP
        seen["bad_file"] = R["boxes"][n0:]
        seen["still_open"] = dlg.winfo_exists()
        dlg.destroy()                                               # 关闭 = 不跑
    root.after(1500, inspect)
    b_re.invoke()
    yield 0.5
    check("重新裁剪对话框只列完成 / 部分对齐的工点（失败的不列），改过线的标出来；默认来源 = 工点目录里的线",
          seen.get("sites") == ["工点丙", "工点甲"] and seen.get("src") == "dir"
          and any("工点甲" in t for t in seen.get("texts", [])), seen)
    check("对话框：没勾工点 / 门槛 95 / 选了非 KML/SHP 都提示、不关窗口",
          "至少勾一个" in str(seen.get("none")) and "点云密度门槛" in str(seen.get("bad_density"))
          and ".kml" in str(seen.get("bad_file")) and seen.get("still_open"), seen)
    check("关掉对话框不跑：界面空闲", not ui["st5"]["running"] and b_re.cget("state") == "normal")
    set_entry(e_out, out)
    yield 0.2

    cb.toggle()
    for mode, cn in (("light", "浅色"), ("dark", "深色")):
        ctk.set_appearance_mode(mode)
        yield 1.2
        p = os.path.join(shots, "影像处理_扫描后_%s.bmp" % cn)
        grab_bmp(p, root)
        R.setdefault("shots", []).append(p)
    ctk.set_appearance_mode("light")
    yield 2  # 切回浅色要重画几百个控件，等它画完再开始量运行中的卡顿
    if os.environ.get("UAV_T9_NO_RUN"):
        gaps["on"] = False
        return

    # ---- 真跑一个工点（只出 XML）：运行中取消 → 重扫 → 重跑 ----
    def only(name):
        btn(t5, "全不选").invoke()
        for r in ui["rows"]():
            if r["item"]["name"] == name:
                r["var"].set(True)

    only(site)
    gaps["max"] = 0.0
    btn(t5, "开始").invoke()
    yield (lambda: by[site]["status"].cget("text") == "运行中"), 120
    check("开始后：该行显示运行中、取消可点、开始/扫描不可点", by[site]["status"].cget("text") == "运行中"
          and btn(t5, "取消").cget("state") == "normal" and btn(t5, "开始").cget("state") == "disabled",
          by[site]["status"].cget("text"))
    yield 45
    gap_run = gaps["max"]
    mark("截图开始")  # 截图要切浅/深色、重画几百个控件，那一下的卡顿是测试自己造成的，不算
    for mode, cn in (("light", "浅色"), ("dark", "深色")):
        ctk.set_appearance_mode(mode)
        yield 1.2
        p = os.path.join(shots, "影像处理_运行中_%s.bmp" % cn)
        grab_bmp(p, root)
        R.setdefault("shots", []).append(p)
    ctk.set_appearance_mode("light")
    yield 1
    mark("截图结束")
    gaps["max"] = 0.0
    yield 10
    n_box = len(R["boxes"])
    mark("点取消")
    btn(t5, "取消").invoke()
    yield 0.3
    yield (lambda: not ui["st5"]["running"]), 180
    mark("取消完成")
    gaps["max"] = max(gaps["max"], gap_run)
    d = os.path.join(out, site)
    check("取消：停在当前工点，状态已取消、有 已取消.txt、弹「已取消」框", by[site]["status"].cget("text") == "已取消"
          and os.path.isfile(os.path.join(d, "已取消.txt")) and any("已取消" in (b[1] or "") for b in R["boxes"][n_box:]),
          (by[site]["status"].cget("text"), R["boxes"][n_box:]))
    check("运行中、取消时界面不卡（最大间隔 < 1 s；不含截图切换浅/深色那一下）", gaps["max"] < 1.0, "%.2f s" % gaps["max"])
    btn(t5, "扫描").invoke()
    yield 0.3
    yield (lambda: not ui["st5"]["scanning"]), 300
    rows = ui["rows"]()
    by = {r["item"]["name"]: r for r in rows}
    check("重扫：待处理（上次已取消）、默认勾", by[site]["status"].cget("text") == "待处理（上次已取消）" and by[site]["var"].get(),
          by[site]["status"].cget("text"))
    only(site)
    n_box = len(R["boxes"])
    gaps["max"] = 0.0
    t1 = time.time()
    btn(t5, "开始").invoke()
    yield 1
    yield (lambda: not ui["st5"]["running"]), 3600
    R["t9_rerun_secs"] = round(time.time() - t1)
    olds = glob.glob(os.path.join(out, site + "_上次已取消_*"))
    rep = os.path.join(out, site, "处理报告.txt")
    st_line = [ln for ln in open(rep, encoding="utf-8").read().splitlines() if ln.startswith("状态：")] if os.path.isfile(rep) else []
    check("重跑：旧结果目录改名保留、新结果完成/部分对齐、弹完成框", len(olds) == 1 and os.path.isfile(os.path.join(olds[0], "已取消.txt"))
          and bool(st_line) and st_line[0][3:] in ("完成", "部分对齐") and any("完成" in (b[1] or "") for b in R["boxes"][n_box:]),
          (olds, st_line, R["boxes"][n_box:], "%.0f s" % (time.time() - t1)))
    check("重跑时界面不卡（最大间隔 < 1 s）", gaps["max"] < 1.0, "%.2f s" % gaps["max"])
    sums = sorted(glob.glob(os.path.join(out, "影像处理汇总_*.csv")))
    check("汇总表两份（取消那次 + 重跑那次），UTF-8 BOM", len(sums) == 2
          and all(open(x, "rb").read(3) == b"\xef\xbb\xbf" for x in sums), sums)
    check("「打开完整日志」可点", btn(t5, "打开完整日志").cget("state") == "normal")
    btn(t5, "扫描").invoke()
    yield 0.3
    yield (lambda: not ui["st5"]["scanning"]), 300
    rows = ui["rows"]()
    by = {r["item"]["name"]: r for r in rows}
    check("再扫：已有结果、默认不勾", by[site]["status"].cget("text") == "已有结果" and not by[site]["var"].get(),
          by[site]["status"].cget("text"))
    # ---- 跑批时关窗口：二次确认（记录器回答「是」），停在当前工点，软件自己关窗 ----
    only(close_site)
    btn(t5, "开始").invoke()
    yield (lambda: by[close_site]["status"].cget("text") == "运行中"), 120
    yield 20
    R["close_sent"] = round(time.time() - T0, 1)
    gaps["on"] = False
    save()
    root.tk.call(root.protocol("WM_DELETE_WINDOW"))
    yield 600  # 软件停好当前工点后自己关窗；关了之后这里不会再执行


def fill_dialog(root, answer):
    tops = [w for w in root.winfo_children() if isinstance(w, ctk.CTkToplevel) and w.winfo_exists()]
    if not tops:
        root.after(300, lambda: fill_dialog(root, answer))
        return
    d = tops[-1]
    R["dialog"] = {"title": d.title(), "texts": [l.cget("text") for l in find(d, ctk.CTkLabel)],
                   "colors": [l.cget("text_color") for l in find(d, ctk.CTkLabel)][:2],
                   "buttons": [b.cget("text") for b in find(d, ctk.CTkButton)]}
    if answer is None:
        btn(d, "取消").invoke()
    else:
        set_entry(find(d, ctk.CTkEntry)[0], answer)
        btn(d, "确认抹除").invoke()


def drive(root):
    nb = find(root, ctk.CTkTabview)[0]
    box = getattr(root, "ui_box", None)  # T10 起软件的提示框都走 root.ui_box（统一 CTk 弹窗），换成记录器
    if box is not None:
        ORIG_BOX.update(vars(box))
        for k in ("showinfo", "showwarning", "showerror"):
            setattr(box, k, rec_box(k))
        box.askyesno = rec_box("askyesno", True)
    gen = {"shots": steps_shots, "batch": steps_batch, "batch8": steps_batch8, "t10": steps_t10, "t10long": steps_t10long,
           "t9": steps_t9}.get(MODE, steps_invoke)(root, nb)

    def tick(cond=None, deadline=0):
        if cond is not None and not cond() and time.time() < deadline:
            root.after(200, lambda: tick(cond, deadline))
            return
        try:
            y = next(gen)
        except StopIteration:
            R["done"] = True
            finish()
            return
        except Exception as ex:
            import traceback
            R["error"] = traceback.format_exc()
            finish()
            return
        if isinstance(y, tuple):
            tick(y[0], time.time() + y[1])
        else:
            root.after(int(y * 1000), tick)

    def finish():
        R["elapsed"] = round(time.time() - T0, 1)
        save()
        root.after(300, root.destroy)

    tick()


_orig_mainloop = ctk.CTk.mainloop


def _mainloop(self, *a, **k):
    self.after(1200, lambda: drive(self))
    _orig_mainloop(self, *a, **k)


ctk.CTk.mainloop = _mainloop


if __name__ == "__main__" and not getattr(sys, "frozen", False):  # 打包版里作为 runtime hook 时 __name__ 也是 __main__
    import runpy
    _src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "uav_photo_tool.py")
    sys.argv = [_src]
    runpy.run_path(_src, run_name="__main__")

