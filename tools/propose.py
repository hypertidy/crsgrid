"""Propose a crsgrid spec from an area of interest.

    python3 tools/propose.py --bbox 143.5,148.5,-43.7,-39.5 --res 10,20,60 [--property conformal]
    python3 tools/propose.py --bbox 44,160,-90,-60 --res 1000 --property equal_area --name aat-laea

Steps: describe the AOI (spherical centroid, spans, polar/mid-latitude,
aspect; after gproj::params), pick a projection family (after gproj's
auto_family and projwiz), round its parameters to the coarsest step that
costs (almost) nothing in distortion over the AOI, compare well-known CRSs,
then build a spec (lattice at CRS (0, 0), domain snapped out to whole
tiles) and lint it. Every decision is written into provenance.

Distortion is the maximum length scale error over the AOI: max(a, 1/b) - 1,
with a, b the Tissot semi-axes, sampled on the densified boundary and an
interior grid.
"""
import argparse
import json
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pyproj

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore", message="You will likely lose important projection information")

KNOWN = [3031, 3413, 3976, 3995, 3035, 3034, 3577, 3112, 5070, 6931, 6932, 6933, 3857, 2193, 3994]
STEPS = [10, 5, 1, 0.5, 0.25, 0.1, 0.05, 0.01]  # natural steps only: nobody centres a projection on an even 2 degrees
TILE_PX = [512, 600, 720, 1000, 1024, 1200, 1440, 2048, 2400, 3600]


# -- AOI -----------------------------------------------------------------------

def aoi_points(bbox, n=24):
    """Boundary (densified along parallels and meridians) and interior sample of a lon/lat bbox."""
    w, e, s, n_ = bbox
    if e < w:
        e += 360
    lons, lats = np.linspace(w, e, n), np.linspace(s, n_, n)
    edge = np.vstack([np.column_stack([lons, np.full(n, s)]), np.column_stack([lons, np.full(n, n_)]),
                      np.column_stack([np.full(n, w), lats]), np.column_stack([np.full(n, e), lats])])
    gx, gy = np.meshgrid(np.linspace(w, e, 9), np.linspace(s, n_, 9))
    pts = np.vstack([edge, np.column_stack([gx.ravel(), gy.ravel()])])
    pts[:, 0] = (pts[:, 0] + 180) % 360 - 180
    pts[:, 1] = np.clip(pts[:, 1], -89.999, 89.999)
    return pts


def describe(bbox):
    pts = aoi_points(bbox)
    lam, phi = np.radians(pts[:, 0]), np.radians(pts[:, 1])
    v = np.column_stack([np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)]).mean(axis=0)
    lon_c = math.degrees(math.atan2(v[1], v[0]))
    lat_c = math.degrees(math.atan2(v[2], math.hypot(v[0], v[1])))
    w, e, s, n = bbox
    lon_span = (e - w) % 360 or 360
    lat_span = n - s
    pole = "south" if s <= -89.9 or (lat_c < -60 and lon_span > 90) else "north" if n >= 89.9 or (lat_c > 60 and lon_span > 90) else None
    width_km = lon_span * 111.32 * math.cos(math.radians(lat_c))
    return {"lon_c": lon_c, "lat_c": lat_c, "lon_span": lon_span, "lat_span": lat_span, "pole": pole,
            "width_km": width_km, "height_km": lat_span * 110.57,
            "aspect": "wide" if width_km > 1.5 * lat_span * 110.57 else "tall" if lat_span * 110.57 > 1.5 * width_km else "square"}


# -- distortion ----------------------------------------------------------------

def scale_error(crs, pts):
    f = pyproj.Proj(crs).get_factors(pts[:, 0], pts[:, 1])
    a, b = np.asarray(f.tissot_semimajor), np.asarray(f.tissot_semiminor)
    return float(np.nanmax(np.maximum(a, 1 / b)) - 1)


# -- family choice -------------------------------------------------------------

def utm_zone(lon):
    return int((lon + 180) // 6) % 60 + 1


def choose(d, prop, bbox):
    """(family, exact params, reason)."""
    w, e, s, n = bbox
    z = utm_zone(d["lon_c"])
    cm = -183 + 6 * z
    if prop == "conformal" and not d["pole"] and abs(d["lat_c"]) < 84:
        lon_lo, lon_hi = ((w - cm + 180) % 360 - 180, (e - cm + 180) % 360 - 180)
        if -3.5 <= lon_lo and lon_hi <= 3.5 and d["lon_span"] <= 7:
            return "utm", {"zone": z, "south": d["lat_c"] < 0}, f"AOI lies within UTM zone {z} (central meridian {cm}, +-3.5 degrees)"
        if d["lon_span"] <= 12 and d["aspect"] != "wide":
            return "tmerc", {"lon_0": d["lon_c"], "lat_0": 0}, "small and not wide: transverse Mercator on the AOI's central meridian"
    if d["pole"]:
        lat0 = -90 if d["pole"] == "south" else 90
        fam = {"conformal": "stere", "equal_area": "laea", "equidistant": "aeqd"}[prop]
        p = {"lat_0": lat0, "lon_0": 0}
        if fam == "stere":
            p["lat_ts"] = -71 if lat0 < 0 else 70
        return fam, p, (f"polar ({d['pole']}): {fam} on the pole. lon_0 only rotates a polar map (distortion is unchanged), "
                        f"so it is 0 as in EPSG:3031/3413-style grids; use {round(d['lon_c'] / 5) * 5:g} instead to put the AOI upright")
    small = max(d["width_km"], d["height_km"]) < 1000
    if not small and d["aspect"] == "wide" and 20 <= abs(d["lat_c"]) <= 70 and prop in ("conformal", "equal_area"):
        fam = "lcc" if prop == "conformal" else "aea"
        return fam, {"lon_0": d["lon_c"], "lat_0": (s + n) / 2, "lat_1": s + d["lat_span"] / 6, "lat_2": n - d["lat_span"] / 6}, \
            f"wide at mid-latitude: {fam} with standard parallels at 1/6 and 5/6 of the latitude range"
    fam = {"conformal": "stere", "equal_area": "laea", "equidistant": "aeqd"}[prop]
    size = "small (under 1000 km)" if small else f"{d['aspect']}"
    return fam, {"lat_0": d["lat_c"], "lon_0": d["lon_c"]}, f"{size} region: {fam} on the AOI's spherical centroid"


def proj_string(fam, p):
    if fam == "utm":
        return f"+proj=utm +zone={p['zone']}{' +south' if p['south'] else ''} +datum=WGS84 +units=m +no_defs"
    body = " ".join(f"+{k}={fmtnum(v)}" for k, v in p.items())
    return f"+proj={fam} {body} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"


def fmtnum(v):
    v = round(v, 10)
    return str(int(v)) if float(v).is_integer() else repr(v)


def round_params(fam, p, step):
    q = dict(p)
    for k in ("lon_0", "lat_0", "lat_1", "lat_2"):
        if k in q and not (k == "lat_0" and abs(q[k]) == 90):
            q[k] = round(q[k] / step) * step
    if "lon_0" in q:
        q["lon_0"] = (q["lon_0"] + 180) % 360 - 180 if q["lon_0"] != 180 else 180
    return q


def choose_rounding(fam, p, pts, floor=0.0001, tol_rel=0.5, cap=0.001):
    """Coarsest step whose distortion is within tolerance of the exact parameters':
    50% of the exact error, but at least 0.01 and at most 0.1 percentage points."""
    exact = scale_error(proj_string(fam, p), pts)
    table = []
    for step in STEPS:
        q = round_params(fam, p, step)
        if "lat_1" in q and q["lat_1"] == q["lat_2"]:
            continue  # rounding collapsed the two standard parallels
        table.append((step, q, scale_error(proj_string(fam, q), pts)))
    limit = exact + max(floor, min(tol_rel * exact, cap))
    chosen = next(t for t in table if t[2] <= limit) if any(t[2] <= limit for t in table) else table[-1]
    return exact, table, chosen


def has_property(crs, pts, prop, tol=1e-6):
    f = pyproj.Proj(crs).get_factors(pts[:, 0], pts[:, 1])
    a, b = np.asarray(f.tissot_semimajor), np.asarray(f.tissot_semiminor)
    ok = np.isfinite(a) & np.isfinite(b)
    if prop == "conformal":
        return bool(np.all(np.abs(a[ok] - b[ok]) < tol * 100))
    if prop == "equal_area":
        return bool(np.all(np.abs(a[ok] * b[ok] - 1) < tol * 100))
    return True


def known_matches(pts, best, prop):
    out = []
    for code in KNOWN:
        crs = pyproj.CRS.from_epsg(code)
        aou = crs.area_of_use
        lon, lat = pts[:, 0], pts[:, 1]
        if aou and not np.all((lat >= aou.south - 1) & (lat <= aou.north + 1)):
            continue
        try:
            if not has_property(crs, pts, prop):
                continue
            err = scale_error(crs, pts)
        except Exception:  # noqa: BLE001
            continue
        if np.isfinite(err):
            out.append((err, code, crs.name))
    out.sort()
    return [m for m in out if m[0] <= best + max(0.0005, 0.02 * best)], out[:3]


# -- spec ----------------------------------------------------------------------

def tile_px_for(res):
    """Smallest standard tile pixel count >= 512 that keeps every tile edge a whole metre at every resolution."""
    for n in TILE_PX:
        if all(float(n * r).is_integer() for r in res):
            return n
    return 1024


def build(name, bbox, crs_def, authority, res, pad_frac, notes):
    crs = pyproj.CRS(authority or crs_def)
    tr = pyproj.Transformer.from_crs("OGC:CRS84", crs, always_xy=True)
    pts = aoi_points(bbox, 48)
    x, y = tr.transform(pts[:, 0], pts[:, 1])
    xmin, xmax, ymin, ymax = float(np.min(x)), float(np.max(x)), float(np.min(y)), float(np.max(y))
    px, py = (xmax - xmin) * pad_frac, (ymax - ymin) * pad_frac
    tpx = tile_px_for(res)
    t = tpx * max(res)
    snap = lambda v, f: f(v / t) * t  # noqa: E731
    ext = [snap(xmin - px, math.floor), snap(xmax + px, math.ceil), snap(ymin - py, math.floor), snap(ymax + py, math.ceil)]
    ext = [int(v) if float(v).is_integer() else v for v in ext]
    fam_crs = {"authority": authority} if authority else {"proj": crs_def}
    if authority and crs_def:
        fam_crs["proj"] = crs_def
    return {
        "crsgrid": "0.1", "name": name,
        "title": f"Proposed grid for {bbox}",
        "family": {"crs": fam_crs, "assign": {"method": "single"}},
        "lattice": {"units": "m", "quantum": 1, "origin": [0, 0],
                    "index": {"row_direction": "up", "edge_rule": "half_open", "negative": True},
                    "tiling": {"tile_px": tpx},
                    "resolutions": {"listed": sorted(res, reverse=True)}},
        "domain": {"extent": ext},
        "ids": {"template": "R{res}_{col:04!sign=E,W}_{row:04!sign=N,S}"},
        "provenance": {"derived_from": "tools/propose.py", "notes": notes},
    }


def align_to(spec, target_path, res, prop):
    """Move the proposed lattice origin onto the target's pixel lattice (same CRS), and say what happened."""
    from align import concrete_instances, pixel_lattice
    from check_fixtures import Grid
    tg = Grid(json.loads(Path(target_path).read_text()))
    crs = pyproj.CRS(spec["family"]["crs"].get("authority") or spec["family"]["crs"]["proj"])
    matches = [c for _, c in concrete_instances(tg) if tg.crs(c) == crs]
    notes = spec["provenance"]["notes"]
    name = tg.spec["name"]
    if not matches:
        what = crs.name if crs.name != "unknown" else (spec["family"]["crs"].get("proj") or crs.to_string())
        msg = (f"--align {name}: the proposed CRS ({what}) is not one of {name}'s; pixels can't align without resampling. "
               f"Use one of {name}'s CRSs (if it has the property you need) to align.")
        print("  " + msg)
        notes.append(msg)
        return
    coarse = max(res)
    tres = [r for r in tg.spec["lattice"]["resolutions"]["listed"] if r <= coarse] or tg.spec["lattice"]["resolutions"]["listed"]
    r = max(tres)
    x0, y0, rx, ry = pixel_lattice(tg, matches[0], r)
    ox, oy = float(x0 % coarse), float(y0 % coarse)
    ox, oy = (int(v) if v.is_integer() else v for v in (ox, oy))
    spec["lattice"]["origin"] = [ox, oy]
    t = spec["lattice"]["tiling"]["tile_px"] * coarse
    e = spec["domain"]["extent"]
    spec["domain"]["extent"] = [ox + math.floor((e[0] - ox) / t) * t, ox + math.ceil((e[1] - ox) / t) * t,
                                oy + math.floor((e[2] - oy) / t) * t, oy + math.ceil((e[3] - oy) / t) * t]
    spec.setdefault("alignment", []).append({"target": name, "resolutions": res, "offset": [0, 0],
                                             "note": f"origin moved onto {name}'s {r:g} m pixel lattice"})
    msg = (f"--align {name}: origin moved to ({ox}, {oy}), {name}'s {r:g} m pixel lattice ({matches[0]['instance']}) "
           f"reduced modulo {coarse:g} m; domain re-snapped to whole tiles from that origin.")
    print("  " + msg)
    notes.append(msg)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", required=True, help="lon_min,lon_max,lat_min,lat_max")
    ap.add_argument("--res", default="100", help="comma-separated resolutions in metres")
    ap.add_argument("--property", default="conformal", choices=["conformal", "equal_area", "equidistant"])
    ap.add_argument("--pad", type=float, default=0.05, help="domain padding, fraction of the AOI's projected size")
    ap.add_argument("--name", default="proposed")
    ap.add_argument("--align", help="a crsgrid spec to align pixels with (same CRS instance required)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    bbox = [float(v) for v in a.bbox.split(",")]
    res = [float(v) if not float(v).is_integer() else int(float(v)) for v in a.res.split(",")]
    pts = aoi_points(bbox)
    d = describe(bbox)
    fam, p, why = choose(d, a.property, bbox)
    notes = [f"AOI {bbox}: spherical centroid {d['lon_c']:.4f}, {d['lat_c']:.4f}; spans {d['lon_span']:g} x {d['lat_span']:g} degrees "
             f"(~{d['width_km']:.0f} x {d['height_km']:.0f} km, {d['aspect']}{', polar ' + d['pole'] if d['pole'] else ''}); property {a.property}.",
             f"family: {why}."]
    print("\n".join(notes))
    if fam == "utm":
        crs_def = proj_string(fam, p)
        authority = f"EPSG:{(32700 if p['south'] else 32600) + p['zone']}"
        err = scale_error(authority, pts)
        print(f"  UTM zone {p['zone']}{'S' if p['south'] else 'N'} ({authority}): max scale error over the AOI {err * 100:.3f}%")
        notes.append(f"UTM zone {p['zone']}{'S' if p['south'] else 'N'}: max scale error {err * 100:.3f}% over the AOI.")
        best = err
    else:
        exact, table, (step, q, err) = choose_rounding(fam, p, pts)
        print(f"  exact parameters: {', '.join(f'{k}={v:.6g}' for k, v in p.items())} -> max scale error {exact * 100:.3f}%")
        print("  rounding step   parameters                                        max scale error")
        for s_, q_, e_ in table:
            mark = "  <- chosen" if s_ == step else ""
            print(f"  {s_:>8g} deg   {', '.join(f'{k}={fmtnum(v)}' for k, v in q_.items()):48} {e_ * 100:7.3f}%{mark}")
        crs_def, authority, best = proj_string(fam, q), None, err
        changed = ", ".join(f"{k} {p[k]:.4f} -> {fmtnum(q[k])}" for k in q if k in p and isinstance(p[k], float) and q[k] != p[k])
        notes.append(f"rounded to {step:g} degree steps ({changed}): max scale error {err * 100:.3f}% vs {exact * 100:.3f}% exact; "
                     "the coarsest step within 50% of exact, capped at 0.1 and floored at 0.01 percentage points."
                     + (" A rounded centre does better than the centroid: the centroid is not the minimax centre." if any(e_ < exact - 1e-6 for _, _, e_ in table) else ""))
    good, top = known_matches(pts, best, a.property)
    if good:
        err_k, code, cname = good[0]
        print(f"  known CRS EPSG:{code} ({cname}) does as well over this AOI: {err_k * 100:.3f}% -> use the code")
        notes.append(f"EPSG:{code} ({cname}) does as well over the AOI ({err_k * 100:.3f}%), so the spec uses it.")
        authority, crs_def = f"EPSG:{code}", None
    else:
        print(f"  no well-known {a.property} CRS does as well; closest: " + ("; ".join(f"EPSG:{c} {e * 100:.2f}%" for e, c, _ in top) or "none"))
    spec = build(a.name, bbox, crs_def, authority, res, a.pad, notes)
    spec["provenance"]["notes"].append(
        f"domain: AOI's projected bounds padded {a.pad:.0%} and snapped out to whole {spec['lattice']['tiling']['tile_px']} px "
        f"tiles at {max(res)} m; tile_px {spec['lattice']['tiling']['tile_px']} keeps every tile edge a whole metre at {res}.")
    if a.align:
        align_to(spec, a.align, res, a.property)
    out = json.dumps(spec, indent=2) + "\n"
    if a.out:
        Path(a.out).write_text(out)
        print(f"  wrote {a.out}")
        from lint import run as lint_run
        _, found = lint_run(a.out)
        for f in found:
            print(f"  lint {f['severity']:7} {f['code']}: {f['message']}")
    else:
        print(out)


if __name__ == "__main__":
    main(sys.argv[1:])
