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
    ap.add_argument("--mode", choices=("invoke", "shots", "batch", "batch8"), default="invoke")
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
VERSION = os.environ.get("UAV_T6_VERSION", "0.9")
os.makedirs(OUT, exist_ok=True)

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


def entry_row(tabf, row):
    es = [e for e in find(tabf, ctk.CTkEntry) if e.master is tabf and int(e.grid_info().get("row", -1)) == row]
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
    for mode, cn in (("light", "浅色"), ("dark", "深色")):
        ctk.set_appearance_mode(mode)
        for name in ("剔除定位", "空三", "控制点", "批量迁移"):
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
    check("帮助菜单四项", set(hl) == {"检查更新", "启动时自动检查更新", "项目主页（GitHub）", "关于"}, list(hl))
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
    check("四个页签", list(nb._name_list) == ["剔除定位", "空三", "控制点", "批量迁移"], nb._name_list)
    check("默认页签", nb.get() == "剔除定位", nb.get())
    t1 = nb.tab("剔除定位")
    cbs = {c.cget("text"): c for c in find(t1, ctk.CTkCheckBox)}
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
                          "勾选": {c.cget("text"): c.get() for c in find(t2, ctk.CTkCheckBox)}}
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
                          "勾选": {c.cget("text"): c.get() for c in find(t3, ctk.CTkCheckBox)}}
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
    check("四个页签", list(nb._name_list) == ["剔除定位", "空三", "控制点", "批量迁移"], nb._name_list)
    nb.set("批量迁移")
    t4 = nb.tab("批量迁移")
    yield 0.5
    e_root, e_out, e_ms = (entry_row(t4, r) for r in range(3))
    check("Metashape 自动填上", e_ms.get() == MS, e_ms.get())
    rf = [f for f in find(t4, ctk.CTkFrame) if f.master is t4 and int(f.grid_info().get("row", -1)) == 3][0]

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
    of4 = [f for f in find(t4, ctk.CTkFrame) if f.master is t4 and int(f.grid_info().get("row", -1)) == 4]  # T8 加的选项行
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
    rf = [f for f in find(t4, ctk.CTkFrame) if f.master is t4 and int(f.grid_info().get("row", -1)) == 3][0]
    of4 = [f for f in find(t4, ctk.CTkFrame) if f.master is t4 and int(f.grid_info().get("row", -1)) == 4][0]
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
    gen = {"shots": steps_shots, "batch": steps_batch, "batch8": steps_batch8}.get(MODE, steps_invoke)(root, nb)

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
