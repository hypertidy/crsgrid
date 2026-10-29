"""Lint crsgrid specs: the "why 5621452.01998?" layer.

    python3 tools/lint.py examples/*.json            # text report
    python3 tools/lint.py --json examples/equi7grid.json

Each lint is a function spec -> findings; a finding has a code, a severity
(error, warning, info), where it applies, a message, and optionally a fix.
Reuses the spec reader in check_fixtures.py. A first pass, driven by what
the test grids in examples/ turned up; see brief.md "What the test grids
taught".
"""
import json
import math
import warnings
import re
import sys
from pathlib import Path

import pyproj

warnings.filterwarnings("ignore", message="You will likely lose important projection information")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_fixtures import FIELD, Grid, fmt, polygon, project  # noqa: E402

LINTS = []


def lint(fn):
    LINTS.append(fn)
    return fn


def finding(code, severity, where, message, fix=None):
    return {"code": code, "severity": severity, "where": where, "message": message, **({"fix": fix} if fix else {})}


def instances(g):
    """(key, ctx) for every enumerable instance; formula families give none."""
    try:
        return [(c["instance"], c) for c in g.instances()]
    except NotImplementedError:
        return []


def crs_numbers(g, ctx):
    """Numeric CRS parameters for an instance, from the PROJ template (or the CRS itself)."""
    c = g.spec["family"]["crs"]
    if "proj" in c:
        s = fmt(c["proj"], ctx)
    else:
        s = g.crs(ctx).to_proj4()
    return {k: float(v) for k, v in re.findall(r"\+(\w+)=(-?[\d.]+(?:e-?\d+)?)\b", s)}


def derived_bounds(url, crs):
    """Bounds of a GDAL-readable polygon source, projected (densified) into crs."""
    import pyogrio
    import shapely
    df = pyogrio.read_dataframe("/vsicurl/" + url if url.startswith("http") else url)
    geom = shapely.union_all(df.geometry.values)
    src = pyproj.CRS(df.crs)
    geom = shapely.segmentize(geom, 0.1 if src.is_geographic else 10000)
    return project(geom, pyproj.Transformer.from_crs(src, crs, always_xy=True)).bounds


def decimals(v):
    s = repr(float(v))
    return 0 if s.endswith(".0") else len(s.split(".")[1])


# -- lints ---------------------------------------------------------------------

@lint
def auto_params(g):
    """CRS parameters with more precision than a person would choose, and why."""
    out = []
    for key, ctx in instances(g):
        nums = crs_numbers(g, ctx)
        for k, v in nums.items():
            if k in ("a", "b", "rf", "k", "k_0") or decimals(v) <= 3:
                continue
            why = ""
            der = g.spec["family"].get("crs_derived_from")
            if k in ("x_0", "y_0") and der and der["rule"] == "bbox_min_at_zero":
                lo = derived_bounds(fmt(der["polygons"], ctx), g.crs(ctx))[0 if k == "x_0" else 1]
                why = (f" Derived, as recorded (crs_derived_from): it puts the source polygons' projected minimum {k[0]} at "
                       f"{lo:.6f} m." if abs(lo) < 1e-3 else
                       f" crs_derived_from says it puts the minimum {k[0]} at 0, but it is {lo:.3f} m.")
                sev = "info" if abs(lo) < 1e-3 else "error"
                out.append(finding("auto-param", sev, f"instance {key}: +{k}", f"{k} = {v} has {decimals(v)} decimal places.{why}"))
                continue
            if k in ("x_0", "y_0") and "polygons" in g.spec.get("domain", {}):
                b = g.zone_xy(key).bounds
                lo = b[0] if k == "x_0" else b[1]
                if abs(lo) < 1:
                    why = (f" It looks derived: it puts the domain polygon's projected minimum {k[0]} at {lo:.3f} m, "
                           "i.e. the zone's bounding box starts at 0.")
            out.append(finding("auto-param", "info" if why else "warning", f"instance {key}: +{k}",
                               f"{k} = {v} has {decimals(v)} decimal places.{why}",
                               None if why else f"round {k}, or record why it has this precision"))
    return out


@lint
def crs_forms_agree(g):
    """The authority code and the PROJ string must describe the same CRS."""
    c = g.spec["family"]["crs"]
    if not ("authority" in c and "proj" in c):
        return []
    out = []
    for key, ctx in instances(g) or sample_formula_instances(g):
        a, p = pyproj.CRS(fmt(c["authority"], ctx)), pyproj.CRS(fmt(c["proj"], ctx))
        ta = pyproj.Transformer.from_crs("OGC:CRS84", a, always_xy=True)
        tp = pyproj.Transformer.from_crs("OGC:CRS84", p, always_xy=True)
        lon0, lat0 = crs_numbers(g, ctx).get("lon_0", 0), crs_numbers(g, ctx).get("lat_0", 0)
        d = 0.0
        for dl in (0, 10, -10):
            for dp in (0, 5, -5):
                lat = max(min(lat0 + dp, 89), -89)
                xa, ya = ta.transform(lon0 + dl, lat)
                xp, yp = tp.transform(lon0 + dl, lat)
                d = max(d, math.hypot(xa - xp, ya - yp))
        if d > 1e-6:
            diffs = differing_params(a, p)
            out.append(finding("crs-forms-differ", "warning", f"instance {key}",
                               f"{fmt(c['authority'], ctx)} and the PROJ string differ by up to {d * 1000:.2f} mm near the centre"
                               + (f" ({diffs})" if diffs else "") + ".",
                               "pick one as canonical (projjson) and record the other as a hint"))
    return out


def sample_formula_instances(g):
    """A few instances of a formula family, for checks that need concrete CRSs."""
    out = []
    params = g.params
    if set(params) == {"zone"}:
        out = [g.instance_from_params(zone=z) for z in (1, 30, 43, 60)]
    elif set(params) == {"zone", "hemisphere"}:
        out = [g.instance_from_params(zone=z, hemisphere=h) for z in (1, 32, 60) for h in ("N", "S")]
    return [(c["instance"], c) for c in out]


def differing_params(a, b):
    pa, pb = a.to_dict(), b.to_dict()
    keys = sorted(k for k in set(pa) | set(pb) if k not in ("no_defs", "type") and pa.get(k) != pb.get(k))
    return ", ".join(f"{k}: {pa.get(k)} vs {pb.get(k)}" for k in keys)


@lint
def superseded(g):
    out = []
    for key, ctx in instances(g):
        if ctx.get("superseded_by"):
            out.append(finding("superseded", "info", f"instance {key}",
                               f"superseded by {ctx['superseded_by']} (steward's guidance, recorded in the spec).",
                               f"use {ctx['superseded_by']} for new data"))
        try:
            if g.crs(ctx).is_deprecated:
                out.append(finding("crs-deprecated", "warning", f"instance {key}", "the CRS is deprecated in the EPSG dataset."))
        except Exception:  # noqa: BLE001
            pass
    return out


@lint
def same_numbers_different_crs(g):
    """Instances with identical lattice/extent numbers but different CRSs are different grids."""
    insts = instances(g)
    if not insts or "extent" not in g.spec.get("domain", {}):
        return []
    out = []
    for i, (ka, ca) in enumerate(insts):
        for kb, cb in insts[i + 1:]:
            ea, eb = g.domain_extent(ca), g.domain_extent(cb)
            if ea != eb or g.crs(ca) == g.crs(cb) or not datum_only(g.crs(ca), g.crs(cb)):
                continue
            tr = pyproj.Transformer.from_crs(g.crs(ca), g.crs(cb), always_xy=True)
            d = max(math.hypot(*(p - q for p, q in zip(tr.transform(x, y), (x, y))))
                    for x in ea[:2] for y in ea[2:])
            out.append(finding("same-numbers-different-crs", "warning", f"instances {ka} / {kb}",
                               f"same extent numbers in different CRSs ({g.crs(ca).name} vs {g.crs(cb).name}): "
                               f"a point at a grid corner moves up to {d:.1f} m between them, so their pixels are not aligned.",
                               "don't mix data on these two grids without resampling"))
    return out


DATUM_KEYS = {"datum", "ellps", "towgs84", "a", "b", "rf", "f", "R", "nadgrids", "no_defs", "type"}


def datum_only(a, b):
    """True when two projected CRSs share method and parameters and differ only in datum/ellipsoid."""
    pa, pb = ({k: v for k, v in c.to_dict().items() if k not in DATUM_KEYS} for c in (a, b))
    return pa == pb


@lint
def overlap(g):
    lat = g.spec["lattice"]
    out = []
    fp = lat.get("footprint")
    if fp and any(fp.get("pad", [0, 0, 0, 0])):
        l, r, b, t = fp["pad"]
        out.append(finding("overlapping-footprints", "warning", "lattice.footprint",
                           f"tiles are padded [{l}, {r}, {b}, {t}] beyond their cells, so neighbours overlap by "
                           f"{l + r} in x and {b + t} in y (CRS units); points in the overlap have 2 (or 4) tiles.",
                           "prefer footprint = cell; if overlap is needed for processing, read it from neighbours"))
    if lat.get("registration") == "point":
        out.append(finding("shared-edge-pixels", "info", "lattice.registration",
                           "pixel-is-point: adjacent tiles share their edge row and column (n + 1 pixels per edge), "
                           "and footprints overlap by one pixel."))
    return out


@lint
def lattice_arithmetic(g):
    lat = g.spec["lattice"]
    out = []
    q = lat["quantum"]
    for v, what in [(lat["origin"][0], "origin x"), (lat["origin"][1], "origin y")]:
        if (v / q) != round(v / q):
            out.append(finding("off-quantum", "error", f"lattice.{what}", f"{v} is not a multiple of quantum {q}."))
    t = lat.get("tiling", {})
    sizes = ([t["tile_size"]] if "tile_size" in t else list(t.get("tile_sizes", {}).values()))
    fp = lat.get("footprint")
    for res in lat["resolutions"]["listed"]:
        for ts in sizes:
            if ts % res:
                if fp:
                    l, r, b, tp = fp.get("pad", [0, 0, 0, 0])
                    w = ts + l + r
                    msg = (f"resolution {res} does not divide the {ts} cell; it does divide the {w} footprint."
                           if w % res == 0 else f"resolution {res} divides neither the {ts} cell nor the {w} footprint.")
                    out.append(finding("res-not-cell", "info" if w % res == 0 else "error", "lattice.resolutions", msg))
                else:
                    out.append(finding("res-not-tile", "error", "lattice.resolutions",
                                       f"resolution {res} does not divide tile size {ts}."))
    if lat["resolutions"].get("open"):
        rule = ("tile = tile_px x res" if "tile_px" in t else "it must divide the tile" if t
                else "it must divide each instance's extent")
        out.append(finding("open-resolutions", "info", "lattice.resolutions", f"any resolution is allowed ({rule})."))
    return out


@lint
def lonlat_pixels(g):
    """Ground shape of pixels on a lon/lat grid, per x_scale band."""
    lat = g.spec["lattice"]
    sample = instances(g) or sample_formula_instances(g)
    if not g.crs(sample[0][1]).is_geographic:
        return []
    bands = lat.get("x_scale", {}).get("bands") or [[0, 90 * lat.get("unit_scale", 1), 1]]
    us = lat.get("unit_scale", 1)
    parts = []
    for lo, hi, f in bands:
        a_lo, a_hi = (f * math.cos(math.radians(v / us)) for v in (lo, hi))
        parts.append(f"{lo / us:g}-{hi / us:g}: {a_hi:.2f}-{a_lo:.2f}")
    return [finding("lonlat-pixel-shape", "info", "lattice.x_scale",
                    "pixel width / height on the ground by |lat| band (1 = square): " + "; ".join(parts) + ".")]


@lint
def distortion(g):
    """Maximum length scale (Tissot semi-major) per instance over its polygon domain."""
    dom = g.spec.get("domain", {})
    if "polygons" not in dom:
        return []
    import numpy as np
    import shapely
    out = []
    for key, ctx in instances(g):
        poly = shapely.segmentize(polygon(fmt(dom["polygons"], ctx)), 0.5)
        xy = shapely.get_coordinates(poly)
        f = pyproj.Proj(g.crs(ctx)).get_factors(xy[:, 0], xy[:, 1])
        a = np.asarray(f.tissot_semimajor)
        i = int(np.nanargmax(a))
        lim = dom.get("envelope", {}).get("max_scale_factor")
        sev = "warning" if lim and a[i] > lim else "info"
        out.append(finding("max-scale", sev, f"instance {key}",
                           f"maximum length scale over the domain is {a[i]:.4f} ({(a[i] - 1) * 100:.1f}% error) "
                           f"at {xy[i, 0]:.2f}, {xy[i, 1]:.2f} (on the zone boundary, land or sea)" + (f"; envelope allows {lim}" if lim else "") + "."))
    return out


@lint
def negative_ids(g):
    """IDs whose numeric fields can go negative without a sign letter (e.g. E-06)."""
    t = g.spec["ids"]["template"]
    if not g.spec["lattice"]["index"].get("negative", True):
        return []
    dom = g.spec.get("domain", {})
    out = []
    for m in FIELD.finditer(t):
        name, _, width, sign, _, _ = m.groups()
        if name in ("col", "row", "xmin", "ymin") and width and not sign:
            axis = 0 if name in ("col", "xmin") else 1
            guarded = "min_corner" in dom and all(
                float(fmt(v, c)) >= g.spec["lattice"]["origin"][axis] if isinstance(v, str) else v >= g.spec["lattice"]["origin"][axis]
                for _, c in instances(g) for v in [dom["min_corner"][axis]])
            if not guarded:
                out.append(finding("unsigned-negative-id", "warning", f"ids.template {{{m.group(0)[1:-1]}}}",
                                   f"{name} can be negative but has no !sign letter, giving IDs like '-06' padded inside the field.",
                                   "add !sign=P,M, set index.negative false, or bound the domain"))
    return out


@lint
def declared_alignment(g):
    """Each declared alignment entry must match the offset computed from both specs."""
    from align import align
    out = []
    for a in g.spec.get("alignment", []):
        here = Path(g.path).resolve().parent
        target = next((d / f"{a['target']}.json" for d in (here, here.parent, here.parent.parent)
                       if (d / f"{a['target']}.json").exists()), here / f"{a['target']}.json")
        if not target.exists():
            out.append(finding("alignment-unchecked", "info", f"alignment -> {a['target']}",
                               "no spec of that name next to this one, so the declared offset can't be checked."))
            continue
        for r in a.get("resolutions") or g.spec["lattice"]["resolutions"]["listed"]:
            res = align(g.path, target, res_a=[r], res_b=a.get("target_resolutions") or [r])
            w = a.get("where", "")
            if "southern" in w:
                res = [x for x in res if x["a"].endswith("S")]
            elif "northern" in w:
                res = [x for x in res if x["a"].endswith("N")]
            elif w:
                res = [x for x in res if x["a"] == w]
            got = {tuple(x["offset"]) for x in res if x.get("same_crs")}
            want = tuple(float(v) for v in a.get("offset", [0, 0]))
            rb = a.get("target_resolutions")
            at = f"{r}" + (f" vs {rb[0]}..{rb[-1]}" if rb else "")
            if got == {want}:
                out.append(finding("alignment-verified", "info", f"alignment -> {a['target']} at {at}",
                                   f"offset {list(want)} confirmed for {len({(x['a'], x['b']) for x in res})} instance pair(s)"
                                   + (f" x {len(rb)} target resolutions." if rb else ".")))
            else:
                out.append(finding("alignment-mismatch", "error", f"alignment -> {a['target']} at {at}",
                                   f"declared offset {list(want)}, computed {sorted(got)}."))
    return out


@lint
def domain_pinning(g):
    dl = g.spec.get("domain", {}).get("list")
    if not dl:
        return []
    pinned = bool(re.search(r"/[0-9a-f]{7,40}/", dl["source"]))
    return [] if pinned else [finding("unpinned-domain", "info", "domain.list.source",
                                      "the tile list is a live URL, not pinned to a version; the domain can change under the spec.",
                                      "pin to a release/commit, or record a hash of the list")]


# -- driver --------------------------------------------------------------------

def run(path):
    spec = json.loads(Path(path).read_text())
    g = Grid(spec)
    g.path = str(path)
    found = []
    for fn in LINTS:
        try:
            found += fn(g)
        except Exception as e:  # noqa: BLE001  a lint must never stop the others
            found.append(finding("lint-crashed", "error", fn.__name__, f"{type(e).__name__}: {e}"))
    return spec["name"], found


def main(args):
    as_json = "--json" in args
    paths = [a for a in args if a != "--json"]
    report = {}
    for p in paths:
        name, found = run(p)
        report[name] = found
        if not as_json:
            print(f"== {name} ({p})")
            for f in found or [finding("clean", "info", "-", "no findings")]:
                print(f"  {f['severity']:7} {f['code']:28} {f['where']}: {f['message']}" + (f"\n          fix: {f['fix']}" if "fix" in f else ""))
    if as_json:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
