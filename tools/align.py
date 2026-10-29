"""Are two crsgrid specs aligned? Compare their pixel lattices.

    python3 tools/align.py examples/aatgrid.json examples/s2mgrs.json
    python3 tools/align.py examples/rema-2m.json examples/gibs-polar.json --b epsg3031

For each pair of instances with the same CRS (or the named pair), and each
pair of listed resolutions, reports the resolution ratio and the offset of
B's pixel lattice from A's, modulo the finer resolution, normalised to
(-r/2, r/2]. Pixels nest exactly when the CRSs are equal, the ratio is an
integer and the offset is (0, 0). Instances in different CRSs are never
aligned; the report says how far apart the lattices are on the ground.
"""
import json
import math
import sys
import warnings
from fractions import Fraction
from pathlib import Path

import pyproj

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_fixtures import Grid, fmt  # noqa: E402

warnings.filterwarnings("ignore", message="You will likely lose important projection information")


def concrete_instances(g):
    """(key, ctx) pairs; formula families give a sample covering both hemispheres."""
    try:
        return [(c["instance"], c) for c in g.instances()]
    except NotImplementedError:
        pass
    if set(g.params) == {"zone"}:
        ctxs = [g.instance_from_params(zone=z) for z in range(1, 61)]
    elif set(g.params) == {"zone", "hemisphere"}:
        ctxs = [g.instance_from_params(zone=z, hemisphere=h) for z in range(1, 61) for h in "NS"]
    else:
        ctxs = []
    return [(c["instance"], c) for c in ctxs]


def pixel_lattice(g, ctx, res):
    """(x0, y0, rx, ry) in CRS units: one pixel corner and the pixel size, for instance ctx at res."""
    lat = g.spec["lattice"]
    us = lat.get("unit_scale", 1)
    if "tiling" not in lat:
        xmin, _, _, ymax = g.domain_extent(ctx)
        return Fraction(xmin), Fraction(ymax), Fraction(res), Fraction(res)
    x0, y0 = (Fraction(v) for v in lat["origin"])
    snap = lat.get("footprint", {}).get("snap")
    if snap and Fraction(snap["step"]) % Fraction(res) == 0:
        x0, y0 = (Fraction(float(fmt(v, ctx))) if isinstance(v, str) else Fraction(v) for v in snap["origin"])
    rx = ry = Fraction(res)
    if lat.get("x_scale"):
        rx = None  # varies by band
    if lat.get("registration") == "point":
        x0, y0 = x0 - (rx or ry) / 2, y0 - ry / 2
    return x0 / us, y0 / us, (rx / us if rx else None), ry / us


def wrap(d, r):
    d = d % r
    return d - r if d > r / 2 else d


def crs_gap(ca, cb, ext):
    """Max ground distance between the same coordinates in two CRSs, over an extent's corners and centre."""
    tr = pyproj.Transformer.from_crs(ca, cb, always_xy=True)
    xs, ys = (ext[0], (ext[0] + ext[1]) / 2, ext[1]), (ext[2], (ext[2] + ext[3]) / 2, ext[3])
    return max(math.hypot(*(p - q for p, q in zip(tr.transform(x, y), (x, y)))) for x in xs for y in ys)


def compare(ga, ca, ra, gb, cb, rb):
    crs_a, crs_b = ga.crs(ca), gb.crs(cb)
    out = {"a": ca["instance"], "b": cb["instance"], "res": [ra, rb]}
    if crs_a != crs_b:
        ext = ga.domain_extent(ca) if "extent" in ga.spec.get("domain", {}) else None
        out.update(same_crs=False, aligned=False,
                   note=f"different CRSs ({crs_a.name} / {crs_b.name})"
                   + (f"; the same coordinates are up to {crs_gap(crs_a, crs_b, ext):.1f} m apart on the ground" if ext else ""))
        return out
    xa, ya, rxa, rya = pixel_lattice(ga, ca, ra)
    xb, yb, rxb, ryb = pixel_lattice(gb, cb, rb)
    if rxa is None or rxb is None:
        out.update(same_crs=True, aligned=None, note="x resolution varies by band; compare per band")
        return out
    fine_x, fine_y = min(rxa, rxb), min(rya, ryb)
    ratio = max(rxa, rxb) / fine_x
    off = [wrap(xb - xa, fine_x), wrap(yb - ya, fine_y)]
    out.update(same_crs=True, ratio=float(ratio), integer_ratio=ratio.denominator == 1,
               offset=[float(o) for o in off], aligned=ratio.denominator == 1 and off == [0, 0])
    ta, tb = tile_lattice(ga, ca, ra), tile_lattice(gb, cb, rb)
    if ta and tb:
        fine = min(ta[2], tb[2])
        tr = max(ta[2], tb[2]) / fine
        toff = [wrap(tb[0] - ta[0], fine), wrap(tb[1] - ta[1], fine)]
        out["tiles_nest"] = tr.denominator == 1 and toff == [0, 0]
    return out


def tile_lattice(g, ctx, res):
    """(x0, y0, size) of the tile lattice in CRS units, for footprint-free square tiles; else None."""
    lat = g.spec["lattice"]
    t = lat.get("tiling")
    if not t or lat.get("footprint") or lat.get("registration") == "point":
        return None
    us = lat.get("unit_scale", 1)
    size = t["tile_px"] * res if "tile_px" in t else t.get("tile_size")
    if size is None:
        return None
    x0, y0 = (Fraction(v) for v in lat["origin"])
    return x0 / us, y0 / us, Fraction(size) / us


def align(path_a, path_b, inst_a=None, inst_b=None, res_a=None, res_b=None):
    ga, gb = (Grid(json.loads(Path(p).read_text())) for p in (path_a, path_b))
    ia, ib = concrete_instances(ga), concrete_instances(gb)
    if inst_a:
        ia = [(k, c) for k, c in ia if k == inst_a]
    if inst_b:
        ib = [(k, c) for k, c in ib if k == inst_b]
    named = bool(inst_a and inst_b)
    pairs = [(a, b) for a in ia for b in ib if named or ga.crs(a[1]) == gb.crs(b[1])]
    if not pairs and ia and ib:   # nothing shares a CRS: say so for the first pair
        pairs = [(ia[0], ib[0])]
    rs_a = res_a or ga.spec["lattice"]["resolutions"]["listed"]
    rs_b = res_b or gb.spec["lattice"]["resolutions"]["listed"]
    return [compare(ga, ca, ra, gb, cb, rb) for (_, ca), (_, cb) in pairs for ra in rs_a for rb in rs_b]


def summarise(results):
    """Collapse identical results across instance pairs."""
    groups = {}
    for r in results:
        key = json.dumps({k: v for k, v in r.items() if k not in ("a", "b")}, sort_keys=True)
        groups.setdefault(key, []).append(f"{r['a']}~{r['b']}")
    return [(json.loads(k), v) for k, v in groups.items()]


def main(args):
    opts = {"--a": None, "--b": None}
    paths = []
    it = iter(args)
    for a in it:
        if a in opts:
            opts[a] = next(it)
        else:
            paths.append(a)
    res = align(paths[0], paths[1], opts["--a"], opts["--b"])
    for r, pairs in summarise(res):
        where = pairs[0] if len(pairs) == 1 else f"{len(pairs)} instance pairs ({pairs[0]} ... {pairs[-1]})"
        ra, rb = r["res"]
        if not r["same_crs"]:
            print(f"  {ra:>8g} vs {rb:<8g} NOT ALIGNED  {r['note']}  [{where}]")
        elif r["aligned"] is None:
            print(f"  {ra:>8g} vs {rb:<8g} ?            {r['note']}  [{where}]")
        else:
            tag = "ALIGNED    " if r["aligned"] else "not aligned"
            why = "" if r["aligned"] else ("(ratio not integer)" if not r["integer_ratio"] else "")
            tiles = {True: "; tiles nest", False: "; tiles don't nest"}.get(r.get("tiles_nest"), "")
            print(f"  {ra:>8g} vs {rb:<8g} {tag}  ratio {r['ratio']:g}, offset {r['offset']} {why}{tiles}  [{where}]")


if __name__ == "__main__":
    main(sys.argv[1:])
