# T6 界面测试夹具：进程内找控件并 invoke()（和鼠标点击走同一个 command 回调），只用标准库 + customtkinter。
# 远程桌面里真鼠标点不到界面，所以用进程内 invoke()；截图用 PrintWindow（远程桌面断开也能画）。
#
# 用法 1（源码）：python scripts/test_gui.py --mode invoke|shots --out <结果目录> --data <测试数据根> #                    --metashape <metashape.exe> --ref <参考期.psx> --appdata <临时 APPDATA>
#   --data 下要有：strip/T6复制/（约 20 张带定位照片）、inplace/T6原地/（8 张）、align/T6空三/（8 张）、
#   gcp/T6迁移/（8 张，不能是参考期自己的照片）；都用副本，别指向原始影像。
#   --appdata 会被清空重建，写入 metashape/gcp_ref/gcp_csv，用来验证「记住路径」，不碰真的 config.json。
# 用法 2（打包版）：PyInstaller 加 --runtime-hook scripts/test_gui.py 打一个测试用 exe，
#   用环境变量 UAV_T6_MODE/UAV_T6_OUT/UAV_T6_DATA/UAV_T6_MS/UAV_T6_REF 和 APPDATA 传参（先用用法 1 的 --appdata 建好）。
# 结果：<结果目录>/harness_<mode>.json（checks 每条 [名称, 通过, 细节]），shots 模式另有 <结果目录>/截图/*.bmp。
import os
import sys

if __name__ == "__main__" and not getattr(sys, "frozen", False):  # 打包版里作为 runtime hook 时 __name__ 也是 __main__
    import argparse
    import shutil
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("invoke", "shots"), default="invoke")
    ap.add_argument("--out", required=True)
    ap.add_argument("--data", default="")
    ap.add_argument("--metashape", required=True)
    ap.add_argument("--ref", default="")
    ap.add_argument("--appdata", required=True)
    ap.add_argument("--seed-only", action="store_true", help="只建 --appdata，不跑界面（给打包版测试用）")
    _a = ap.parse_args()
    shutil.rmtree(_a.appdata, ignore_errors=True)
    os.makedirs(os.path.join(_a.appdata, "无人机照片工具"))
    import json as _json
    with open(os.path.join(_a.appdata, "无人机照片工具", "config.json"), "w", encoding="utf-8") as _f:
        _json.dump({"metashape": _a.metashape, "gcp_ref": _a.ref, "gcp_csv": "C:\记住的.csv"}, _f, ensure_ascii=False)
    if _a.seed_only:
        sys.exit(0)
    os.environ.update(APPDATA=_a.appdata, UAV_T6_MODE=_a.mode, UAV_T6_OUT=_a.out, UAV_T6_DATA=_a.data,
                      UAV_T6_MS=_a.metashape, UAV_T6_REF=_a.ref)

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
        for name in ("剔除定位", "空三", "控制点"):
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
    check("窗口标题", root.title().endswith("v0.7"), root.title())
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
    check("关于", R["boxes"] and R["boxes"][-1][0] == "showinfo" and "v0.7" in R["boxes"][-1][2], R["boxes"][-1:])
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
    check("三个页签", list(nb._name_list) == ["剔除定位", "空三", "控制点"], nb._name_list)
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
    gen = steps_shots(root, nb) if MODE == "shots" else steps_invoke(root, nb)

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
