"""Generate fixtures for VRT-indexed tile schemes, using the VRT as the oracle.

    python3 tools/fixtures-vrt.py rema   [path-or-url to REMA-2m_dem_ovr.vrt]
    python3 tools/fixtures-vrt.py cop30  [path-or-url to COP30_hh.vrt]

gdalbuildvrt wrote each source's placement from the tile's own geotransform,
so a VRT records where every tile really is. Expected footprints here are
read from the VRT (DstRect, SrcRect, SourceProperties and the VRT
geotransform), not computed from any rule.
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = {"rema": "https://raw.githubusercontent.com/mdsumner/rema-ovr/f96576d/REMA-2m_dem_ovr.vrt",
           "cop30": "https://opentopography.s3.sdsc.edu/raster/COP30/COP30_hh.vrt"}
ID = {"rema": re.compile(r"2m/\d+_\d+/(\d+_\d+_\d_\d)_2m_v2\.0_dem\.tif$"),
      "cop30": re.compile(r"(Copernicus_DSM_10_[NS]\d\d_00_[EW]\d{3}_00_DEM)\.tif$")}


def load(src):
    if src.startswith("http"):
        cache = Path.home() / ".cache" / "crsgrid" / re.sub(r"[^\w.]+", "_", src)
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.exists():
            urllib.request.urlretrieve(src, cache)
        src = cache
    return ET.parse(src).getroot()


def footprints(root, idrx):
    gt = [float(v) for v in root.find("GeoTransform").text.split(",")]
    x0, rx, _, y0, _, ry = gt
    out = {}
    for s in root.iter():
        if s.tag not in ("SimpleSource", "ComplexSource"):
            continue
        m = idrx.search(s.find("SourceFilename").text)
        if not m:
            continue
        sp, sr, dr = s.find("SourceProperties"), s.find("SrcRect"), s.find("DstRect")
        w, h = int(sp.get("RasterXSize")), int(sp.get("RasterYSize"))
        sxo, syo, sxs, sys_ = (float(sr.get(k)) for k in ("xOff", "yOff", "xSize", "ySize"))
        dxo, dyo, dxs, dys = (float(dr.get(k)) for k in ("xOff", "yOff", "xSize", "ySize"))
        px, py = dxs / sxs * rx, dys / sys_ * ry          # source pixel size in CRS units (py < 0)
        west = x0 + dxo * rx - sxo * px
        north = y0 + dyo * ry - syo * py
        out[m.group(1)] = {"extent": [west, west + w * px, north + h * py, north], "dim": [w, h], "res": [px, -py]}
    return out


def main(which, src=None):
    root = load(src or DEFAULT[which])
    fp = footprints(root, ID[which])
    ids = sorted(fp)
    cases = []
    if which == "rema":
        regular = [i for i in ids if fp[i]["dim"] == [25100, 25100]]
        odd = [i for i in ids if fp[i]["dim"] != [25100, 25100]]
        sample = regular[::25] + odd
        tol = {}
    else:
        def lat(i):
            m = re.search(r"_([NS])(\d\d)_", i)
            return int(m.group(2)) * (1 if m.group(1) == "N" else -1)
        by_w = {}
        for i in ids:
            by_w.setdefault((fp[i]["dim"][0], lat(i) < 0), []).append(i)
        sample = sorted({j for v in by_w.values() for j in v[:4]} | set(ids[::250]) |
                        {i for i in ids if "_W180_" in i or "_E179_" in i})
        tol = {"extent": 1e-7}
    for i in sample:
        e = {"extent": [round(v, 9) for v in fp[i]["extent"]], "dim": fp[i]["dim"]}
        c = {"op": "tile_extent", "input": {"id": i}, "expected": e, "source": "VRT placement"}
        if tol:
            c["tolerance"] = tol
        if which == "rema" and fp[i]["dim"] != [25100, 25100]:
            c["known_difference"] = {
                "value": {"extent": [e["extent"][0], e["extent"][1] - 2, e["extent"][2] + 2, e["extent"][3]], "dim": [25100, 25100]},
                "reason": "this PGC tile is 25101 px, one pixel more than every other tile; the spec's rule gives 25100"}
        cases.append(c)
    cases.append({"op": "domain_size", "input": {}, "expected": {"count": len(ids)}, "source": "VRT sources"})
    absent = {"rema": ["99_99_1_1", "06_37_3_1"], "cop30": ["Copernicus_DSM_10_N89_00_E000_00_DEM"]}[which]
    for i in ids[:2] + absent:
        cases.append({"op": "in_domain", "input": {"id": i}, "expected": {"value": i in fp}, "source": "VRT sources"})
    spec = {"rema": "examples/rema-2m.json", "cop30": "examples/cop30.json"}[which]
    out = {"spec": spec, "generator": f"tools/fixtures-vrt.py {which}",
           "oracle": {"package": f"GDAL VRT placement of {len(ids)} tiles ({src or DEFAULT[which]})", "proj": "n/a (no transforms)"},
           "cases": cases}
    path = ROOT / "fixtures" / (Path(spec).stem + ".json")
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"{len(cases)} cases from {len(ids)} tiles -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main(*sys.argv[1:])
