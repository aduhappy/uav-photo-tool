# -*- coding: utf-8 -*-
"""T9「影像处理」独立检查：不 import 软件源码，换一套方法（PIL 读 EXIF、pyproj 换算、rasterio 读 GeoTIFF）重算。

用法（aitools 环境：有 PIL、pyproj、rasterio、numpy）：
  python scripts/test_survey.py scan <照片上级目录> <survey-batch --plan 的汇总.csv> [--list 清单.txt]
      按卡面 🔒「工点」定义自己扫一遍（os.walk），算每个工点的照片数、带定位张数（PIL 读 EXIF GPS）、平均经度、
      最近的 3 度带，逐行和汇总表比 工点/照片数/带定位张数/坐标系（汇总表里没有的工点只列出来）；列出靠近分带线的。
  python scripts/test_survey.py xml <工点结果目录>
      XML：唯一 <SRS>（Id 1、Name、Definition = EPSG:<号>）、没有 WKT（PROJCS/GEOGCS）、<Block> 挂 SRSId 1、别处没有、
      <Pose> 下没有 <Metadata>；<Photo> 数 = 报告里的对齐数；ImagePath 都在；每张照片的 <Center> 和
      「PIL 读的 GPS 经纬度用 pyproj 换到同一带」比平面差（应是米级，不会是几十万米）。
  python scripts/test_survey.py raster <工点结果目录> [--res 0.03]
      <工点名>DOM.tif/.tfw、<工点名>DSM.tif/.tfw：坐标系（pyproj 认出的 EPSG）、分辨率、DOM 4 波段 uint8 + alpha、
      DSM 1 波段 float32、NoData −9999、Deflate、.tfw 与 GeoTIFF 变换一致。
  python scripts/test_survey.py compare <我们的 DOM.tif> <样例 DOM.tif> <我们的 DSM.tif> <样例 DSM.tif> [--step 0.25]
      和交付样例叠在一起比：重叠区按 --step 米重采样，DOM 灰度相位相关求平面偏移（米），DSM 高差（直接叠 / 按平面偏移
      对齐后各一组：中位数、平均、标准差、去掉 5% 极值后的范围）。
  python scripts/test_survey.py clip <工点结果目录>（T11/D-31 自动裁边；T12 起范围线用 geopandas 读，多个多边形、内环都算，
      线外按「离线 1 像元以外」判，报告关键词换成 T12 的）
      DOM 与 DSM 的 .tfw、行列数、变换、分辨率、坐标系完全一致；范围线（SHP，自己解析）栅格化后和线面积吻合、
      外框 = 线外接矩形外扩约 5 个像元；线外 DOM 全透明 / DSM 全 NoData、线内大部分有数据；KML 是 WGS84 经纬度、
      .prj 与 DOM 同坐标系；处理报告里有裁边、有效面积、裁剪前后外框。
  python scripts/test_survey.py gate12 <工点结果目录> --raw <没裁的 DOM.tif> [--sample <用户手工裁的样例 DOM.tif>]（T12/D-32）
      闸门 1/2：范围线（geopandas 读 SHP）外环个数 = 1；线内裁后透明而原图不透明的像元 = 0（没有新添透明斑），并列出
      线内透明斑（0.09 米块上标号）的处数、面积；沿线往里 2 m 边带（shapely 内缓冲）DOM 不透明 ≥ 99%，边带 1 米格的缺口数；
      线外透明（离线 1 像元以外 100%）、线内不透明 ≥ 99%、线内原图透明 ≤ 1%（线没伸出影像）；给了 --sample 再在 0.5 米
      网格上算样例区被保留的比例（覆盖率）和面积比（只输出、不判）。
  python scripts/test_survey.py region <工点结果目录> [--west-max <东坐标>]（T13/D-33 生成范围）
      报告里的范围、自动区域框、连接点分位数范围、± 1000 米限制；用 XML <Center> 自算已对齐相机范围，核
      范围 = (自动框 ∪ 连接点范围) ∩ 相机 ± 1000 米、「是否收过」一致；没勾裁边时核在相机 ± 200 米内；
      与 metashape_日志 的 region 事件互证；DOM/DSM 外框在范围内。
退出码：有不通过 1，否则 0。
"""
import argparse
import csv
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s  %s  %s" % ("通过  " if ok else "不通过", name, detail))


def zone_of(lon):
    cm = 3 * int(math.floor(lon / 3.0 + 0.5))
    return 4534 + (cm - 75) // 3, cm, abs(abs(lon - cm) - 1.5) < 0.1


# ---------------------------------------------------------------- scan
def pil_gps(path):
    from PIL import Image
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            g = ex.get_ifd(0x8825)
    except Exception:
        return None
    if not g or 2 not in g or 4 not in g:
        return None

    def dms(v):
        return float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
    lat, lon = dms(g[2]), dms(g[4])
    if g.get(1) == "S":
        lat = -lat
    if g.get(3) == "W":
        lon = -lon
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return None
    alt = float(g[6]) if 6 in g else None
    return lat, lon, alt


def is_hidden(p):
    try:
        return bool(os.stat(p).st_file_attributes & 2)
    except (AttributeError, OSError):
        return False


def jpgs_direct(d):
    return [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg"))
            and os.path.isfile(os.path.join(d, f)) and not is_hidden(os.path.join(d, f))]


def my_sites(root):
    """卡面 🔒：直接含 JPG、且不在另一个工点里面的文件夹；子文件夹的 JPG 并进来。"""
    sites = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not is_hidden(os.path.join(dp, d))]
        if any(f.lower().endswith((".jpg", ".jpeg")) for f in fns if not is_hidden(os.path.join(dp, f))):
            sites.append(dp)
            dns[:] = []  # 下面的子文件夹并进这个工点，不再单独算
    out = []
    for s in sites:
        photos = []
        for dp, dns, fns in os.walk(s):
            dns[:] = [d for d in dns if not is_hidden(os.path.join(dp, d))]
            photos += [os.path.join(dp, f) for f in fns if f.lower().endswith((".jpg", ".jpeg"))
                       and not is_hidden(os.path.join(dp, f))]
        out.append((s, photos))
    return out


def cmd_scan(a):
    sites = my_sites(a.root)
    rows = {}
    with open(a.csv, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows[r["工点"]] = r
    lines = []
    matched = 0
    for d, photos in sites:
        gps = [g for g in (pil_gps(p) for p in photos) if g]
        lon = sum(g[1] for g in gps) / len(gps) if gps else None
        epsg, cm, near = zone_of(lon) if gps else (None, None, False)
        rel = os.path.relpath(d, a.root)
        parts = rel.split(os.sep)
        # 软件的工点名：文件夹名；只有 正摄 这种（上级只有它一个工点）时取上级的名字
        name = parts[-1]
        if len(parts) >= 2 and sum(1 for x, _ in sites if os.path.dirname(x) == os.path.dirname(d)) == 1 \
                and os.path.dirname(d) != os.path.abspath(a.root):
            name = parts[-2]
        r = rows.get(name)
        want = "EPSG:%d CM%d" % (epsg, cm) if epsg else ""
        line = "%s | %s | 照片 %d | 带定位 %d | 平均经度 %s | 应选 %s%s" % (
            rel, name, len(photos), len(gps), "%.4f" % lon if lon is not None else "-", want, " | 靠近分带线" if near else "")
        lines.append(line)
        if r is None:
            print("（汇总表里没有）" + line)
            continue
        matched += 1
        ok = int(r["照片数"]) == len(photos) and int(r["带定位张数"]) == len(gps) and r["坐标系"] == want
        check("工点 %s" % name, ok, "表：%s 张 / 定位 %s / %s；自算：%d / %d / %s" % (
            r["照片数"], r["带定位张数"], r["坐标系"], len(photos), len(gps), want))
    check("汇总表每一行都对上了自算的工点", matched == len(rows), "汇总表 %d 行，对上 %d" % (len(rows), matched))
    print("自算工点 %d 个" % len(sites))
    if a.list:
        with open(a.list, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- xml
def site_files(d):
    name = os.path.basename(os.path.normpath(d))
    return name, os.path.join(d, name + "_BlocksExchange.xml"), os.path.join(d, "处理报告.txt")


def cmd_xml(a):
    from pyproj import Transformer
    name, xml, rep = site_files(a.dir)
    text = open(xml, encoding="utf-8").read()
    rep_text = open(rep, encoding="utf-8").read()
    m = re.search(r"^对齐：(\d+)/(\d+)", rep_text, re.M)
    aligned = int(m.group(1)) if m else None
    check("XML 里没有 WKT（PROJCS / GEOGCS / LOCAL_CS）", not re.search(r"PROJCS|GEOGCS|LOCAL_CS", text))
    root = ET.fromstring(text.encode("utf-8"))
    srs = root.findall("SpatialReferenceSystems/SRS")
    defs = [(s.findtext("Id"), s.findtext("Name"), s.findtext("Definition")) for s in srs]
    m = re.match(r"EPSG:(\d+)$", defs[0][2] or "") if len(defs) == 1 else None
    epsg = int(m.group(1)) if m else None
    cm = 75 + 3 * (epsg - 4534) if epsg else None
    check("唯一 <SRS>：Id 1、Name、Definition EPSG:<号>（4534～4554）", epsg and 4534 <= epsg <= 4554 and defs == [
        ("1", "CGCS2000 / 3-degree Gauss-Kruger CM %dE" % cm, "EPSG:%d" % epsg)], defs)
    block = root.find("Block")
    check("<Block> 下 <SRSId>1</SRSId>", [x.text for x in block.findall("SRSId")] == ["1"])
    all_ids = len(root.findall(".//SRSId"))
    check("别处没有 <SRSId>", all_ids == 1, all_ids)
    meta = len(root.findall(".//Pose/Metadata"))
    check("<Pose> 下没有 <Metadata>", meta == 0, meta)
    photos = root.findall(".//Photo")
    check("<Photo> 数 = 报告的对齐数", len(photos) == aligned, "%d / %s" % (len(photos), aligned))
    missing = [p.findtext("ImagePath") for p in photos if not os.path.isfile(p.findtext("ImagePath") or "")]
    check("ImagePath 都存在", not missing, missing[:3])
    if not epsg:
        return
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    d2, dz, n_nogps = [], [], 0
    for p in photos:
        g = pil_gps(p.findtext("ImagePath"))
        c = p.find("Pose/Center")
        if g is None or c is None:
            n_nogps += 1
            continue
        x, y = tr.transform(g[1], g[0])
        cx, cy, cz = (float(c.findtext(k)) for k in ("x", "y", "z"))
        d2.append(math.hypot(cx - x, cy - y))
        if g[2] is not None:
            dz.append(cz - g[2])
    d2.sort()
    med = d2[len(d2) // 2] if d2 else None
    check("照片中心 vs PIL 的 GPS 用 pyproj 换到 EPSG:%d：平面差中位数 < 20 米（换算对了是米级）" % epsg,
          med is not None and med < 20, "中位数 %.2f 米、最大 %.2f 米（%d 张）；高差中位数 %.2f 米" % (
              med, d2[-1], len(d2), sorted(dz)[len(dz) // 2] if dz else float("nan")) if d2 else "没有可比的")


# ---------------------------------------------------------------- raster
def cmd_raster(a):
    import rasterio
    from pyproj import CRS
    name = os.path.basename(os.path.normpath(a.dir))
    rep = open(os.path.join(a.dir, "处理报告.txt"), encoding="utf-8").read()
    m = re.search(r"坐标系：EPSG:(\d+)", rep)
    epsg = int(m.group(1)) if m else None
    for kind, bands, dtype in (("DOM", 4, "uint8"), ("DSM", 1, "float32")):
        tif = os.path.join(a.dir, name + kind + ".tif")
        tfw = os.path.join(a.dir, name + kind + ".tfw")
        check("%s 文件名 <工点名>%s.tif 和 .tfw 都在" % (kind, kind), os.path.isfile(tif) and os.path.isfile(tfw))
        if not os.path.isfile(tif):
            continue
        with rasterio.open(tif) as d:
            crs = CRS.from_wkt(d.crs.to_wkt())
            got = crs.to_epsg(min_confidence=70)
            # Metashape（和 CC 交付样例一样）写的 WKT 不带 TOWGS84，pyproj 常认不出 EPSG 号；那就比投影参数
            keys = ("proj", "lon_0", "lat_0", "k", "x_0", "y_0", "ellps", "units")
            want = CRS.from_epsg(epsg).to_dict() if epsg else {}
            have = crs.to_dict()
            same = got == epsg or (want and all(str(have.get(k)) == str(want.get(k)) for k in keys))
            check("%s 坐标系 = 报告的 EPSG:%s（pyproj 认出 %s；%s；投影参数 %s）" % (
                kind, epsg, got, crs.name, {k: have.get(k) for k in keys}), same)
            check("%s 分辨率 %g 米" % (kind, a.res), abs(d.res[0] - a.res) < 1e-9 and abs(d.res[1] - a.res) < 1e-9, d.res)
            check("%s %d 波段 %s" % (kind, bands, dtype), d.count == bands and all(t == dtype for t in d.dtypes), (d.count, d.dtypes))
            check("%s Deflate 压缩" % kind, (d.profile.get("compress") or "").lower() == "deflate", d.profile.get("compress"))
            if kind == "DSM":
                check("DSM NoData −9999", d.nodata == -9999, d.nodata)
            else:
                ci = [c.name for c in d.colorinterp]
                check("DOM RGBA（第 4 波段是 alpha）", ci == ["red", "green", "blue", "alpha"], ci)
            if os.path.isfile(tfw):
                t = [float(x) for x in open(tfw).read().split()]
                T = d.transform
                ok = abs(t[0] - T.a) < 1e-9 and abs(t[3] - T.e) < 1e-9 and abs(t[4] - (T.c + T.a / 2)) < 1e-6 \
                    and abs(t[5] - (T.f + T.e / 2)) < 1e-6
                check("%s .tfw 与 GeoTIFF 变换一致（像素中心）" % kind, ok, (t, tuple(T)[:6]))
            print("  %s：%d × %d，范围 %s，%.0f MB%s" % (kind, d.width, d.height, tuple(round(v, 2) for v in d.bounds),
                                                   os.path.getsize(tif) / 1048576.0, "，BigTIFF" if open(tif, "rb").read(4)[2] == 43 else ""))


# ---------------------------------------------------------------- compare
def cmd_compare(a):
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject
    from rasterio.transform import from_origin
    with rasterio.open(a.dom) as d1, rasterio.open(a.sdom) as d2:
        crs = d1.crs
        l, b = max(d1.bounds.left, d2.bounds.left), max(d1.bounds.bottom, d2.bounds.bottom)
        r, t = min(d1.bounds.right, d2.bounds.right), min(d1.bounds.top, d2.bounds.top)
    if r <= l or t <= b:
        check("有重叠区", False)
        return
    s = a.step
    w, h = int((r - l) / s), int((t - b) / s)
    T = from_origin(l, t, s, s)
    print("重叠区：东 %.1f～%.1f、北 %.1f～%.1f（%.0f × %.0f 米），按 %g 米重采样 %d × %d" % (l, r, b, t, r - l, t - b, s, w, h))

    def grab(path, band, nodata_out, rs):
        with rasterio.open(path) as d:
            arr = np.full((h, w), nodata_out, dtype="float32")
            reproject(rasterio.band(d, band), arr, src_transform=d.transform, src_crs=d.crs, dst_transform=T,
                      dst_crs=crs, resampling=rs, src_nodata=d.nodata, dst_nodata=nodata_out)
            return arr

    def gray(path):
        rgb = [grab(path, k, 0, Resampling.average) for k in (1, 2, 3)]
        alpha = grab(path, 4, 0, Resampling.min)
        g = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        return g, alpha > 0

    g1, m1 = gray(a.dom)
    g2, m2 = gray(a.sdom)
    m = m1 & m2
    check("DOM 重叠区有效像素 > 10%", m.mean() > 0.1, "%.0f%%" % (100 * m.mean()))

    def phase_shift(x, y, mask):
        x = np.where(mask, x - x[mask].mean(), 0)
        y = np.where(mask, y - y[mask].mean(), 0)
        win = np.outer(np.hanning(x.shape[0]), np.hanning(x.shape[1]))
        F = np.fft.fft2(x * win) * np.conj(np.fft.fft2(y * win))
        c = np.fft.ifft2(F / (np.abs(F) + 1e-9)).real
        iy, ix = np.unravel_index(np.argmax(c), c.shape)
        if iy > c.shape[0] // 2:
            iy -= c.shape[0]
        if ix > c.shape[1] // 2:
            ix -= c.shape[1]
        return ix, iy, c.max()

    ix, iy, peak = phase_shift(g1, g2, m)
    # 我们的影像要往 东 dx、北 dy 挪才能和样例重合（行号向下＝向南）
    dx, dy = -ix * s, iy * s
    print("DOM 平面偏移（相位相关，%g 米一格）：我们相对样例 东 %+.2f 米、北 %+.2f 米，合 %.2f 米（峰值 %.3f）" % (
        s, -dx, -dy, math.hypot(dx, dy), peak))
    RESULTS.append(("DOM 平面偏移（米）", True, json.dumps({"east": -dx, "north": -dy, "dist": math.hypot(dx, dy)})))
    z1 = grab(a.dsm, 1, np.nan, Resampling.average)
    z2 = grab(a.sdsm, 1, np.nan, Resampling.average)
    for arr, p in ((z1, a.dsm), (z2, a.sdsm)):
        with rasterio.open(p) as d:
            if d.nodata is not None:
                arr[arr == d.nodata] = np.nan
            arr[arr < -1000] = np.nan

    def stats(dz):
        v = dz[np.isfinite(dz)]
        if v.size == 0:
            return None
        lo, hi = np.percentile(v, [5, 95])
        return {"n": int(v.size), "median": float(np.median(v)), "mean": float(v.mean()), "std": float(v.std()),
                "p5": float(lo), "p95": float(hi)}
    st0 = stats(z1 - z2)
    sh = np.roll(np.roll(z1, iy, axis=0), ix, axis=1)  # 把我们的 DSM 按 DOM 偏移挪到和样例重合
    st1 = stats(sh - z2)
    print("DSM 高差（我们 − 样例），直接叠：%s" % json.dumps(st0, ensure_ascii=False))
    print("DSM 高差（我们 − 样例），按 DOM 平面偏移对齐后：%s" % json.dumps(st1, ensure_ascii=False))
    RESULTS.append(("DSM 高差（直接叠）", True, json.dumps(st0)))
    RESULTS.append(("DSM 高差（平面对齐后）", True, json.dumps(st1)))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"overlap": [l, b, r, t], "step": s, "shift_east": -dx, "shift_north": -dy,
                       "shift": math.hypot(dx, dy), "peak": float(peak), "dz_direct": st0, "dz_aligned": st1}, f,
                      ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- clip（T11/D-31）
def read_shp_ring(path):
    """自己解析 SHP 的第一个多边形（不 import 软件源码）。"""
    import struct
    data = open(path, "rb").read()
    if len(data) < 100 or struct.unpack(">i", data[:4])[0] != 9994:
        raise ValueError("不是 SHP：%s" % path)
    off, kind = 100, struct.unpack("<i", data[32:36])[0]
    while off + 8 <= len(data):
        words = struct.unpack(">i", data[off + 4:off + 8])[0]
        body = data[off + 8:off + 8 + words * 2]
        off += 8 + words * 2
        if len(body) < 44 or struct.unpack("<i", body[:4])[0] not in (5, 15, 25):
            continue
        nparts, npts = struct.unpack("<2i", body[36:44])
        parts = list(struct.unpack("<%di" % nparts, body[44:44 + 4 * nparts]))
        p0 = 44 + 4 * nparts
        end = parts[1] if nparts > 1 else npts
        ring = [struct.unpack("<2d", body[p0 + 16 * i:p0 + 16 * i + 16]) for i in range(parts[0], end)]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) >= 3:
            return [tuple(p) for p in ring], kind
    raise ValueError("SHP 里没有多边形：%s" % path)


def cmd_clip(a):
    import numpy as np
    import rasterio
    from rasterio import features
    from rasterio.transform import Affine
    from pyproj import CRS
    name = os.path.basename(os.path.normpath(a.dir))
    rep = open(os.path.join(a.dir, "处理报告.txt"), encoding="utf-8-sig").read()
    m = re.search(r"坐标系：EPSG:(\d+)", rep)
    epsg = int(m.group(1)) if m else None
    dom = os.path.join(a.dir, name + "DOM.tif")
    dsm = os.path.join(a.dir, name + "DSM.tif")
    kml = os.path.join(a.dir, name + "_范围线.kml")
    shp = os.path.join(a.dir, name + "_范围线.shp")
    check("范围线文件（KML + SHP + .shx/.dbf/.prj）都在",
          all(os.path.isfile(p) for p in (kml, shp, shp[:-4] + ".shx", shp[:-4] + ".dbf", shp[:-4] + ".prj")),
          "%s 等" % os.path.basename(shp))
    if not (os.path.isfile(dom) and os.path.isfile(dsm)):
        check("DOM/DSM 在", False, a.dir)
        return

    def tfw(p):
        return [float(x) for x in open(p, encoding="ascii").read().split()]
    td, ts = tfw(dom[:-4] + ".tfw"), tfw(dsm[:-4] + ".tfw")
    check("DOM / DSM 的 .tfw 逐项相同（像元大小、旋转、左上角）",
          all(abs(x - y) < 1e-9 for x, y in zip(td, ts)), "[%.9f, %.9f, %.9f]" % (td[0], td[4], td[5]))
    with rasterio.open(dom) as d1, rasterio.open(dsm) as d2:
        same = (d1.width, d1.height, d1.transform, d1.res, d1.crs) == (d2.width, d2.height, d2.transform, d2.res, d2.crs)
        check("DOM / DSM 行列数、变换、分辨率、坐标系完全一致", same,
              "%d × %d，%r，%s" % (d1.width, d1.height, d1.res, d1.crs))
        check("DOM 仍是 4 波段 uint8（RGBA）", d1.count == 4 and all(t == "uint8" for t in d1.dtypes), (d1.count, d1.dtypes))
        check("DSM 仍是 1 波段 float32、NoData −9999", d2.count == 1 and d2.dtypes[0] == "float32" and d2.nodata == -9999,
              (d2.count, d2.dtypes, d2.nodata))
        W, H, T, nod = d1.width, d1.height, d1.transform, d2.nodata
    import geopandas as gpd                                     # T12：多个多边形、内环都算（geopandas 读）
    from shapely.geometry import mapping
    from scipy import ndimage
    geom = gpd.read_file(shp).geometry.union_all()
    ring = [p for g in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]) for p in g.exterior.coords]
    area = geom.area
    inside = features.rasterize([mapping(geom)], out_shape=(H, W), transform=T, all_touched=False, dtype="uint8", fill=0)
    n_in = int(inside.sum())
    check("范围线栅格化面积 与 线面积 吻合", abs(n_in * T.a * -T.e - area) / area < 0.01,
          "栅格 %.0f m² / 线 %.0f m²（%d 个点）" % (n_in * T.a * -T.e, area, len(ring) - 1))
    # 外框 = 范围线外接矩形外扩 5 个像元？
    pad_l = (min(p[0] for p in ring) - T.c) / T.a
    pad_t = (T.f - max(p[1] for p in ring)) / -T.e
    pad_r = (T.c + W * T.a - max(p[0] for p in ring)) / T.a
    pad_b = (min(p[1] for p in ring) - (T.f + H * T.e)) / -T.e
    check("输出外框 = 范围线外接矩形外扩约 5 个像元",
          all(4.0 - 1e-6 <= v <= 6.0 + 1e-6 for v in (pad_l, pad_t, pad_r, pad_b)),  # 线顶点正好在像元边上时是 6.000000002
          "左 %.2f、上 %.2f、右 %.2f、下 %.2f 像元" % (pad_l, pad_t, pad_r, pad_b))
    # 线外全透明 / NoData；线内大部分有数据
    bad_a = bad_z = n_out = 0
    near = ndimage.binary_dilation(inside > 0, structure=np.ones((3, 3)), iterations=1)
    with rasterio.open(dom) as d1, rasterio.open(dsm) as d2:
        for r0 in range(0, H, 2000):
            nb = min(2000, H - r0)
            w = rasterio.windows.Window(0, r0, W, nb)
            alpha = d1.read(4, window=w)
            dem = d2.read(1, window=w)
            out = near[r0:r0 + nb] == 0                       # 离线 1 个像元以外（线上那一圈是像元量化）
            n_out += int(out.sum())
            bad_a += int(((alpha > 0) & out).sum())
            bad_z += int(((dem != nod) & out).sum())
            sub = slice(0, H, max(1, H // 60)), slice(0, W, max(1, W // 60))
        alpha = d1.read(4)[sub]
        dem = d2.read(1)[sub]
    ins = inside[sub] > 0
    check("线外（离线 1 像元以外）DOM 全部透明（alpha = 0）", bad_a == 0, "线外 %d 像元，不透明 %d 个" % (n_out, bad_a))
    check("线外（离线 1 像元以外）DSM 全部 NoData（−9999）", bad_z == 0, "线外有高程 %d 个" % bad_z)
    check("线内 DOM 有影像（抽样 > 90%）", ins.any() and (alpha[ins] > 0).mean() > 0.9,
          "%.1f%%" % (100 * (alpha[ins] > 0).mean()) if ins.any() else "线内没有抽样点")
    check("线内 DSM 有高程（抽样 > 90%）", ins.any() and (dem[ins] != nod).mean() > 0.9,
          "%.1f%%" % (100 * (dem[ins] != nod).mean()) if ins.any() else "线内没有抽样点")
    # 坐标系：KML 经纬度、SHP .prj 与 DOM 同带
    txt = open(kml, encoding="utf-8").read()
    coords = txt.split("<coordinates>")[1].split("</coordinates>")[0].split()
    lon = [float(t.split(",")[0]) for t in coords]
    lat = [float(t.split(",")[1]) for t in coords]
    check("KML 是 WGS84 经纬度（不是投影坐标）", all(70 < v < 140 for v in lon) and all(0 < v < 60 for v in lat),
          "经度 %.6f～%.6f、纬度 %.6f～%.6f" % (min(lon), max(lon), min(lat), max(lat)))
    prj = open(shp[:-4] + ".prj", encoding="ascii").read()
    c = CRS.from_wkt(prj)
    with rasterio.open(dom) as d1:
        dc = CRS.from_wkt(d1.crs.to_wkt())
    keys = ("proj", "lon_0", "lat_0", "k", "x_0", "y_0", "ellps", "units")
    same = all(str(c.to_dict().get(k)) == str(dc.to_dict().get(k)) for k in keys)
    check("SHP 的 .prj 与 DOM 同坐标系（%s）" % (c.to_dict().get("proj"),), same,
          "CM%s / DOM CM%s" % (c.to_dict().get("lon_0"), dc.to_dict().get("lon_0")))
    # 报告里该有的几句
    for key in ("裁边：", "范围线面积：", "裁剪前外框：", "裁剪后外框：", "范围线另存"):
        check("处理报告里有「%s」" % key, key in rep)


# ---------------------------------------------------------------- gate12（T12/D-32 闸门 1、2）
def cmd_gate12(a):
    """T12 闸门 1/2：范围线一整块、线内没有新添的透明斑、沿线往里 2 m 边带不透明 ≥ 99%、线外全透明、线不伸出影像；
    和用户手工裁的样例 DOM 比覆盖率、面积比（只输出不判）。范围线用 geopandas 读（不用软件的解析器）。"""
    import numpy as np
    import rasterio
    import geopandas as gpd
    from rasterio import features
    from rasterio.enums import Resampling
    from scipy import ndimage
    from shapely.geometry import mapping
    name = os.path.basename(os.path.normpath(a.dir))
    dom = os.path.join(a.dir, name + "DOM.tif")
    shp = os.path.join(a.dir, name + "_范围线.shp")
    g = gpd.read_file(shp)
    geom = g.geometry.union_all() if hasattr(g.geometry, "union_all") else g.geometry.unary_union
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    n_outer, n_inner = len(polys), sum(len(p.interiors) for p in polys)
    check("① 范围线是一整块（只有 1 个外环，没有飞地）", n_outer == 1,
          "外环 %d 个、内环 %d 个、面积 %.0f m²、%d 个点、几何有效 %s" % (
              n_outer, n_inner, geom.area, sum(len(p.exterior.coords) - 1 for p in polys), geom.is_valid))
    with rasterio.open(dom) as d:
        W, H, T = d.width, d.height, d.transform
        alpha = d.read(4) > 0
    px = T.a
    inside = features.rasterize([mapping(geom)], out_shape=(H, W), transform=T, fill=0, dtype="uint8") > 0
    core = ndimage.binary_erosion(inside, iterations=2)          # 离线 2 个像元以内的算边界量化误差
    with rasterio.open(a.raw) as r:
        col0 = int(round((T.c - r.transform.c) / px))
        row0 = int(round((r.transform.f - T.f) / px))
        win = rasterio.windows.Window(col0, row0, W, H)
        raw = r.read(4, window=win, boundless=True, fill_value=0) > 0
    new_holes = int((core & ~alpha & raw).sum())
    check("② 线内透明的地方原影像本来就透明（裁剪没有新添透明斑）", new_holes == 0,
          "线内（离线 2 像元以外）裁后透明而原图不透明的像元 %d 个" % new_holes)
    hole = core & ~alpha                                        # 只看离线 2 像元以外（线上那一圈是裁剪的像元量化）
    edge = int((inside & ~core & ~alpha).sum())
    f = 3                                                       # 0.09 米块上数透明斑（全分辨率标号太占内存）
    hb = hole[:H // f * f, :W // f * f].reshape(H // f, f, W // f, f).any(axis=(1, 3))
    lab, n = ndimage.label(hb, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel())[1:] * (f * px) ** 2 if n else np.array([0.0])
    print("      线内（离线 2 像元以外）透明斑：%d 处，合计 %.2f m²，最大 %.2f m²（原图同位置透明 %.2f m²）；"
          "紧贴范围线 2 像元内的透明像元 %d 个（%.2f m²，裁剪边上的像元量化，不算斑）" % (
              n, sizes.sum() if n else 0, sizes.max() if n else 0, (core & ~raw).sum() * px * px, edge, edge * px * px))
    band_geom = geom.difference(geom.buffer(-2.0))
    band = features.rasterize([mapping(band_geom)], out_shape=(H, W), transform=T, fill=0, dtype="uint8") > 0
    band &= inside
    op = float(alpha[band].mean()) if band.any() else 0.0
    cell = int(round(1.0 / px))                                 # 边带按 1 米格看缺口（锯齿）
    hh, ww = H // cell * cell, W // cell * cell
    bc = band[:hh, :ww].reshape(hh // cell, cell, ww // cell, cell).sum(axis=(1, 3))
    oc = (band & alpha)[:hh, :ww].reshape(hh // cell, cell, ww // cell, cell).sum(axis=(1, 3))
    sel = bc >= cell * cell // 4
    notch = int(((oc[sel] / bc[sel]) < 0.9).sum())
    check("③ 沿范围线往里 2 m 的边带 DOM 不透明 ≥ 99%", op >= 0.99,
          "边带 %.0f m²，不透明 %.3f%%；边带 1 米格里不透明 < 90%% 的（缺口）%d 格 / %d 格" % (
              band.sum() * px * px, 100 * op, notch, int(sel.sum())))
    near = ndimage.binary_dilation(inside, iterations=1)
    out_op = int((alpha & ~inside).sum())
    out_far = int((alpha & ~near).sum())
    check("线外 DOM 透明（离线 1 像元以外 100%）", out_far == 0,
          "线外不透明 %d 个像元（都在线外 1 像元以内的 %d 个）" % (out_op, out_op - out_far))
    check("线内 DOM 不透明 ≥ 99%", alpha[inside].mean() >= 0.99, "%.3f%%" % (100 * alpha[inside].mean()))
    outside_img = int((inside & ~raw).sum())
    check("范围线没伸到影像外面（线内原图透明 ≤ 线内面积 1%）", outside_img <= 0.01 * inside.sum(),
          "线内原图透明 %.1f m²（%.3f%%）" % (outside_img * px * px, 100.0 * outside_img / max(1, inside.sum())))
    if a.sample:                                                # 和用户手工裁的样例比：0.5 米网格，只输出不判
        step = 0.5
        with rasterio.open(a.sample) as s:
            sb = s.bounds
            sf = max(1, int(round(step / s.res[0])))
            sa = s.read(4, out_shape=(s.height // sf, s.width // sf), resampling=Resampling.nearest) > 0
            st = s.transform * s.transform.scale(s.width / sa.shape[1], s.height / sa.shape[0])
        x0, y1 = min(sb.left, geom.bounds[0]), max(sb.top, geom.bounds[3])
        x1, y0 = max(sb.right, geom.bounds[2]), min(sb.bottom, geom.bounds[1])
        gt = rasterio.transform.from_origin(x0, y1, step, step)
        gw, gh = int(math.ceil((x1 - x0) / step)), int(math.ceil((y1 - y0) / step))
        ours = features.rasterize([mapping(geom)], out_shape=(gh, gw), transform=gt, fill=0, dtype="uint8") > 0
        samp = np.zeros((gh, gw), bool)
        rr, cc = np.nonzero(sa)
        xs, ys = st * (cc + 0.5, rr + 0.5)
        gc = ((np.asarray(xs) - x0) / step).astype(int)
        gr = ((y1 - np.asarray(ys)) / step).astype(int)
        ok = (gc >= 0) & (gc < gw) & (gr >= 0) & (gr < gh)
        samp[gr[ok], gc[ok]] = True
        inter = (ours & samp).sum()
        print("      【参考】和用户手工裁的样例比（0.5 米网格）：样例 %.0f m²、我们 %.0f m²；样例区被我们保留 %.1f%%（覆盖率），"
              "我们是样例的 %.1f%%（面积比），我们有而样例没有 %.0f m²、样例有而我们没有 %.0f m²" % (
                  samp.sum() * step * step, ours.sum() * step * step, 100.0 * inter / max(1, samp.sum()),
                  100.0 * ours.sum() / max(1, samp.sum()), (ours & ~samp).sum() * step * step,
                  (samp & ~ours).sum() * step * step))


# ---------------------------------------------------------------- region（T13/D-33）
def cmd_region(a):
    name, xml, rep = site_files(a.dir)
    rep_text = open(rep, encoding="utf-8").read()
    num = r"(-?[\d.]+)"
    box_re = r"东 %s～%s、北 %s～%s" % (num, num, num, num)

    def rbox(prefix):
        m = re.search(r"^  %s：%s" % (prefix, box_re), rep_text, re.M)
        return [float(m.group(i)) for i in (1, 3, 2, 4)] if m else None  # [东min, 北min, 东max, 北max]
    used, auto = rbox("范围"), rbox("自动区域框")
    m = re.search(r"^  连接点范围：东、北各自 ([\d.]+)%～([\d.]+)% 分位数 " + box_re + r"（[^）]*），共 (\d+) 个", rep_text, re.M)
    tie = [float(m.group(i)) for i in (3, 5, 4, 6)] if m else None
    m = re.search(r"^  ± ([\d.]+) 米限制：(.*)$", rep_text, re.M)
    limit, lim_text = (float(m.group(1)), m.group(2)) if m else (None, "")
    clip_mode = auto is not None
    check("报告里有生成范围（米）", used is not None, used)
    if not used:
        return
    # 已对齐相机的平面范围：XML <Center>（只含对齐的相机）
    root = ET.parse(xml).getroot()
    cs = [(float(c.findtext("x")), float(c.findtext("y"))) for c in root.iter("Center")]
    cb = [min(x for x, y in cs), min(y for x, y in cs), max(x for x, y in cs), max(y for x, y in cs)]
    # 与 Metashape 日志里的 region 事件互证（报告是两位小数）
    ev = None
    log_path = os.path.join(a.dir, "metashape_日志.txt")
    if os.path.isfile(log_path):
        for ln in open(log_path, encoding="utf-8", errors="replace"):
            if '"ev": "region"' in ln:
                ev = json.loads(ln)
    check("报告的范围 = metashape_日志 里 region 事件的 box（差 < 0.01 米）",
          ev is not None and max(abs(p - q) for p, q in zip(used, ev["box"][:4])) < 0.01, ev and ev["box"][:4])
    tol = 0.05
    if clip_mode:
        check("勾了自动裁边：报告有自动区域框、连接点分位数范围、± 1000 米限制", auto and tie and limit == 1000.0,
              (auto, tie, limit))
        if not (auto and tie and limit):
            return
        u = [min(auto[0], tie[0]), min(auto[1], tie[1]), max(auto[2], tie[2]), max(auto[3], tie[3])]
        lim = [cb[0] - limit, cb[1] - limit, cb[2] + limit, cb[3] + limit]
        want = [max(u[0], lim[0]), max(u[1], lim[1]), min(u[2], lim[2]), min(u[3], lim[3])]
        sides = [s for s, p, q in zip(("西", "南", "东", "北"), u, want) if abs(p - q) > tol]
        check("范围 = (自动框 ∪ 连接点范围) ∩ 相机范围 ± 1000 米（自算，差 < %g 米）" % tol,
              max(abs(p - q) for p, q in zip(used, want)) < tol, "报告 %s / 自算 %s" % (
                  [round(v, 2) for v in used], [round(v, 2) for v in want]))
        check("报告「是否收过」与自算一致", (lim_text == "没收过") == (not sides) and all(s in lim_text for s in sides),
              "报告：%s；自算收过的边：%s" % (lim_text, sides or "无"))
        check("连接点范围在相机范围附近（没算成别的坐标/单位）",
              all(abs(t - c) < 5000 for t, c in zip(tie, cb)), "相机 %s" % [round(v, 1) for v in cb])
    else:
        lim = [cb[0] - 200, cb[1] - 200, cb[2] + 200, cb[3] + 200]
        check("没勾自动裁边：范围在相机范围 ± 200 米以内（同 v1.2）",
              used[0] >= lim[0] - tol and used[1] >= lim[1] - tol and used[2] <= lim[2] + tol and used[3] <= lim[3] + tol,
              "范围 %s / 相机 %s" % ([round(v, 1) for v in used], [round(v, 1) for v in cb]))
    if a.west_max is not None:
        check("生成范围西边 ≤ 东 %g" % a.west_max, used[0] <= a.west_max, "%.2f" % used[0])
    import rasterio
    for kind in ("DOM", "DSM"):
        p = os.path.join(a.dir, name + kind + ".tif")
        if os.path.isfile(p):
            with rasterio.open(p) as d:
                b = d.bounds
                px = d.res[0]
            check("%s 外框在生成范围以内（容差 1 像元）" % kind, b.left >= used[0] - px and b.bottom >= used[1] - px and
                  b.right <= used[2] + px and b.top <= used[3] + px, tuple(round(v, 2) for v in b))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("root")
    s.add_argument("csv")
    s.add_argument("--list")
    x = sub.add_parser("xml")
    x.add_argument("dir")
    r = sub.add_parser("raster")
    r.add_argument("dir")
    r.add_argument("--res", type=float, default=0.03)
    c = sub.add_parser("compare")
    c.add_argument("dom")
    c.add_argument("sdom")
    c.add_argument("dsm")
    c.add_argument("sdsm")
    c.add_argument("--step", type=float, default=0.25)
    c.add_argument("--json")
    cl = sub.add_parser("clip")
    cl.add_argument("dir")
    g12 = sub.add_parser("gate12")
    g12.add_argument("dir")
    g12.add_argument("--raw", required=True, help="同一工点没裁的 DOM（判线内透明斑是不是原来就有、线有没有伸出影像）")
    g12.add_argument("--sample", help="用户手工裁的交付样例 DOM（只读；只输出覆盖率、面积比）")
    rg = sub.add_parser("region")
    rg.add_argument("dir")
    rg.add_argument("--west-max", type=float, help="生成范围西边不得超过这个东坐标（用于验收：如样例成果的西边东坐标）")
    a = ap.parse_args()
    {"scan": cmd_scan, "xml": cmd_xml, "raster": cmd_raster, "compare": cmd_compare, "clip": cmd_clip,
     "gate12": cmd_gate12, "region": cmd_region}[a.cmd](a)
    bad = [x for x in RESULTS if not x[1]]
    print("\n合计 %d 项，不通过 %d 项" % (len(RESULTS), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
