"""Check a crsgrid spec reproduces its fixtures, using only the spec.

    python3 tools/check_fixtures.py fixtures/aatgrid.json fixtures/equi7grid.json

A throwaway reference reader for spec v0.1, not a runtime: it exists to
show the spec carries enough information. Needs jsonschema and pyproj;
specs with polygon assign/domain also need shapely and pyarrow, and
download the polygons once into ~/.cache/crsgrid.
"""
import json
import math
import re
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path

import jsonschema
import pyproj

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path.home() / ".cache" / "crsgrid"
FIELD = re.compile(r"\{(\w+)(?:/(\d+(?:\.\d+)?))?(?::0(\d+))?(?:!sign=(\w),(\w))?(?:!map=([^}]*))?\}")


class SpecError(ValueError):
    """A request the spec says is invalid; str() is the fixture error code."""

# A case may carry "known_difference": {"value": {...}, "reason": "..."} when the
# oracle is known to differ from what the spec defines; it is reported, not failed.


# -- templates -----------------------------------------------------------------

def num(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def fmt(template, ctx):
    def sub(m):
        name, div, width, pos, neg, mapping = m.groups()
        v = ctx[name]
        if mapping is not None:
            return dict(kv.split(":", 1) for kv in mapping.split(","))[str(v)]
        if div:
            q = v / float(div)
            if q != round(q):
                raise SpecError("not_aligned")
            v = round(q)
        if pos:
            letter, v = (pos if v >= 0 else neg), abs(v)
            return letter + (f"{int(v):0{width}d}" if width else str(v))
        return f"{int(v):0{width}d}" if width else str(num(v))
    return FIELD.sub(sub, template)


def template_parser(template, numeric, letters=()):
    """Regex for a template plus a function turning a match into field values."""
    pos, out, fields = 0, [], []
    for m in FIELD.finditer(template):
        name, div, _, sp, sn, _ = m.groups()
        out.append(re.escape(template[pos:m.start()]))
        if sp:
            out.append(rf"(?P<{name}__s>[{sp}{sn}])(?P<{name}>\d+)")
        elif name in numeric:
            out.append(rf"(?P<{name}>-?\d+)")
        elif name in letters:
            out.append(rf"(?P<{name}>[A-Z])")
        else:
            out.append(rf"(?P<{name}>[A-Za-z0-9-]+?)")
        fields.append((name, div, sn))
        pos = m.end()
    out.append(re.escape(template[pos:]))
    rx = re.compile("^" + "".join(out) + "$")

    def parse(s):
        m = rx.match(s)
        if not m:
            return None
        vals = {}
        for name, div, sn in fields:
            v = m.group(name)
            if name in numeric or sn:
                v = int(v) * (-1 if sn and m.group(name + "__s") == sn else 1)
                v = v * float(div) if div else v
            vals[name] = v
        return vals
    return parse


# -- ID field expressions -----------------------------------------------------

def expr(src, env):
    import ast
    ops = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
           ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.Name):
            return env[n.id]
        raise ValueError(f"unsupported expression {src!r}")
    return ev(ast.parse(src, mode="eval"))


# -- polygons ------------------------------------------------------------------

@lru_cache(None)
def polygon(url):
    import pyarrow.parquet as pq
    import shapely
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / re.sub(r"[^\w.]+", "_", url)
    if not path.exists():
        urllib.request.urlretrieve(url, path)
    geoms = shapely.from_wkb(pq.read_table(path).column("geometry").to_pylist())
    return shapely.union_all(geoms)


def project(geom, transformer):
    import numpy as np
    import shapely
    return shapely.transform(geom, lambda c: np.column_stack(transformer.transform(c[:, 0], c[:, 1])))


# -- MGRS (from the published rules; GeographicLib is the oracle, not the source)

LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"          # 24, no I or O
COL_SETS = {1: LETTERS[0:8], 2: LETTERS[8:16], 0: LETTERS[16:24]}
ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUV"           # 20, cycle of 2000 km
BANDS = "CDEFGHJKLMNPQRSTUVWX"                 # 8 degrees from 80S; X is 72-84N


def utm_standard(lon, lat):
    lon = (lon + 180) % 360 - 180
    zone = min(int((lon + 180) // 6) + 1, 60)
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    if 72 <= lat < 84 and 0 <= lon < 42:
        zone = 31 if lon < 9 else 33 if lon < 21 else 35 if lon < 33 else 37
    return zone, ("N" if lat >= 0 else "S")


def band_of(lat):
    return BANDS[min(max(int((lat + 80) // 8), 0), 19)]


def band_range(band):
    i = BANDS.index(band)
    return -80 + 8 * i, (84 if band == "X" else -72 + 8 * i)


def mgrs_letters(zone, col, row):
    e = COL_SETS[zone % 3][col - 1]
    n = ROW_LETTERS[(row + (5 if zone % 2 == 0 else 0)) % 20]
    return e, n


def mgrs_cell(zone, band, e, n, inverse):
    """(col, row) of a 100 km square; inverse(x, y) -> (lon, lat) in the zone CRS."""
    col = COL_SETS[zone % 3].index(e) + 1
    r = (ROW_LETTERS.index(n) - (5 if zone % 2 == 0 else 0)) % 20
    lo, hi = band_range(band)

    def miss(row):
        _, lat = inverse((col + 0.5) * 1e5, (row + 0.5) * 1e5)
        return 0 if lo <= lat <= hi else min(abs(lat - lo), abs(lat - hi))
    return col, min((r + 20 * k for k in range(5)), key=miss)


@lru_cache(None)
def id_list(url, column=None, format="parquet", pattern=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / re.sub(r"[^\w.]+", "_", url)
    if not path.exists():
        urllib.request.urlretrieve(url, path)
    if format == "gdal_vrt":
        rx = re.compile(pattern)
        names = re.findall(r"<SourceFilename[^>]*>([^<]*)<", path.read_text())
        return frozenset(m.group("id") for m in map(rx.search, names) if m)
    import pyarrow.parquet as pq
    return frozenset(pq.read_table(path).column(column).to_pylist())


def domain_ids(dl):
    return id_list(dl["source"], dl.get("column"), dl.get("format", "parquet"), dl.get("pattern"))


# -- the grid ------------------------------------------------------------------

class Grid:
    NUMERIC = {"res", "col", "row", "xmin", "ymin"}

    def __init__(self, spec):
        self.spec = spec
        fam, lat = spec["family"], spec["lattice"]
        self.q = lat["quantum"]
        self.us = lat.get("unit_scale", 1)
        self.point = lat.get("registration", "area") == "point"
        self.xbands = lat.get("x_scale", {}).get("bands")
        self.x0, self.y0 = lat["origin"]
        self.up = lat["index"]["row_direction"] == "up"
        self.negative = lat["index"].get("negative", True)
        self.tiling = lat.get("tiling", {})
        self.res = lat["resolutions"]
        self.names = self.res.get("names", {})
        self.table = fam.get("instances")
        self.params = fam.get("params", {})
        self.single = not self.table and not self.params
        if not self.table and not self.single:
            self.key_parse = template_parser(fam["instance_key"],
                                             {k for k, p in self.params.items() if p["type"] != "string"})
        ids = spec["ids"]
        numeric = (self.NUMERIC | {k for k, p in self.params.items() if p["type"] != "string"}
                   | set(ids.get("fields", {})))
        self.codec = ids.get("codec")
        letters = {"band", "e100k", "n100k"} if self.codec == "mgrs" else ()
        self.id_parsers = [template_parser(t, numeric, letters) for t in [ids["template"]] + ids.get("parse_aliases", [])]

    # instances: a context dict of template fields
    def instance(self, key=""):
        if self.single:
            return {"instance": key}
        if self.table:
            return {"instance": key, **self.table[key]}
        vals = self.key_parse(key)
        if vals is None:
            raise SpecError("unknown_instance")
        return {"instance": key, **vals}

    def instance_from_params(self, **params):
        key = fmt(self.spec["family"]["instance_key"], params)
        return {"instance": key, **params}

    def instances(self):
        if self.single:
            return [self.instance()]
        if self.table:
            return [self.instance(k) for k in self.spec["family"]["assign"].get("order", self.table)]
        raise NotImplementedError("enumerating formula instances")

    def crs(self, ctx):
        c = self.spec["family"]["crs"]
        return pyproj.CRS(fmt(c["authority"], ctx) if "authority" in c else fmt(c["proj"], ctx))

    def to_xy(self, ctx):
        return pyproj.Transformer.from_crs("OGC:CRS84", self.crs(ctx), always_xy=True)

    def assign(self, lon, lat):
        a = self.spec["family"]["assign"]
        if a["method"] == "lon_bands":
            v = math.floor(((lon - a["lon_start"]) % 360) / a["width"]) + a["first"]
            return self.instance_from_params(**{a["param"]: v})
        if a["method"] == "utm_standard":
            zone, hemi = utm_standard(lon, lat)
            return self.instance_from_params(**{a["zone"]: zone, a["hemisphere"]: hemi})
        if a["method"] == "polygons":
            import shapely
            pt = shapely.Point(lon, lat)
            for ctx in self.instances():
                if polygon(fmt(a["source"], ctx)).contains(pt):
                    return ctx
            raise SpecError("no_instance")
        return self.instances()[0]

    # tile sizes: a tile "level" is a tile size, picked by res or by tiling name
    def check_res(self, res, ts=None):
        if res not in self.res["listed"]:
            if not self.res.get("open", False):
                raise SpecError("res_not_listed")
            self.quanta(res)
        if ts is not None and ts % res:
            raise SpecError("res_does_not_divide_tile")

    def tile_size(self, res=None, tiling=None):
        t = self.tiling
        if "tile_px" in t:
            self.check_res(res)
            return t["tile_px"] * res
        if "tile_size" in t:
            return t["tile_size"]
        if tiling not in t["tile_sizes"]:
            raise SpecError("unknown_tiling")
        return t["tile_sizes"][tiling]

    def tile_px(self, ts, res):
        self.check_res(res, ts)
        return ts // res

    def quanta(self, v):
        n = v / self.q
        if n != round(n):
            raise SpecError("not_on_quantum")
        return round(n)

    # lattice
    def index(self, x, y, ts):
        tq = self.quanta(ts)
        col = math.floor((x / self.q - self.quanta(self.x0)) / tq)
        dy = y / self.q - self.quanta(self.y0)
        row = math.floor(dy / tq) if self.up else math.floor(-dy / tq)
        if not self.negative and (col < 0 or row < 0):
            raise SpecError("negative_index")
        return col, row

    def extent(self, col, row, ts):
        xmin = self.x0 + col * ts
        ymin = self.y0 + row * ts if self.up else self.y0 - (row + 1) * ts
        return [num(xmin), num(xmin + ts), num(ymin), num(ymin + ts)]

    # tiles are dicts: ctx (instance fields), ts, col, row, res, tiling
    def tile(self, ctx, col, row, res=None, tiling=None):
        return {"ctx": ctx, "ts": self.tile_size(res, tiling), "col": col, "row": row, "res": res, "tiling": tiling}

    def make_id(self, t, lat=None):
        xmin, _, ymin, _ = self.extent(t["col"], t["row"], t["ts"])
        extra = {}
        if self.codec == "mgrs":
            zone = t["ctx"]["zone"]
            e, n = mgrs_letters(zone, t["col"], t["row"])
            if lat is None and "band" in t:
                lat = sum(band_range(t["band"])) / 2
            if lat is None:
                _, lat = self.to_xy(t["ctx"]).transform(xmin + t["ts"] / 2, ymin + t["ts"] / 2, direction="INVERSE")
            extra = {"band": band_of(lat), "e100k": e, "n100k": n}
        name = {v: k for k, v in self.names.items()}.get(t["res"], "")
        env = {**t["ctx"], "res": t["res"], "name": name, "tiling": t["tiling"],
               "col": t["col"], "row": t["row"], "xmin": xmin, "ymin": ymin, **extra}
        for k, e in self.spec["ids"].get("fields", {}).items():
            env[k] = expr(e, env)
        return fmt(self.spec["ids"]["template"], env)

    def parse_id(self, s, domain=True):
        for parse in self.id_parsers:
            v = parse(s)
            if v is None:
                continue
            if "res" not in v and v.get("name") in self.names:
                v["res"] = self.names[v["name"]]
            if self.codec == "mgrs":
                hemi = "N" if v["band"] >= "N" else "S"
                ctx = self.instance_from_params(zone=v["zone"], hemisphere=hemi)
                tr = self.to_xy(ctx)
                v["col"], v["row"] = mgrs_cell(v["zone"], v["band"], v["e100k"], v["n100k"],
                                               lambda x, y: tr.transform(x, y, direction="INVERSE"))
                v["instance"] = ctx["instance"]
            else:
                ctx = self.instance(v.get("instance", ""))
            if "tiling" not in self.spec["lattice"]:   # untiled: the ID names an instance raster
                self.check_res(v["res"])
                ext = self.domain_extent(ctx)
                if (ext[1] - ext[0]) % v["res"] or (ext[3] - ext[2]) % v["res"]:
                    raise SpecError("not_aligned")
                return {"ctx": ctx, "res": v["res"], "untiled": True, "extent": ext}
            ts = self.tile_size(v.get("res"), v.get("tiling"))
            for k, e in self.spec["ids"].get("recover", {}).items():
                v[k] = expr(e, v)
            if "col" in v:
                col, row = v["col"], v["row"]
            else:
                dx, dy = v["xmin"] - self.x0, v["ymin"] - self.y0
                if dx % ts or dy % ts:
                    raise SpecError("not_aligned")
                col, row = round(dx / ts), round(dy / ts if self.up else -dy / ts - 1)
            t = self.tile(ctx, col, row, v.get("res"), v.get("tiling"))
            if self.codec == "mgrs":
                t["band"] = v["band"]
                t["id"] = fmt(self.spec["ids"]["template"], v)
            else:
                t["id"] = self.make_id(t)
            if domain:
                self.check_domain(t)
            return t
        raise SpecError("unparseable")

    # domain
    @lru_cache(None)
    def zone_xy(self, instance):
        import shapely
        ctx = self.instance(instance)
        g = shapely.segmentize(polygon(fmt(self.spec["domain"]["polygons"], ctx)), 0.1)
        return project(g, self.to_xy(ctx))

    def check_domain(self, t):
        dom = self.spec.get("domain", {})
        if "list" in dom and t.get("id") is not None:
            if t["id"] not in domain_ids(dom["list"]):
                raise SpecError("outside_domain")
        if "min_corner" in dom:
            mx, my = (float(fmt(v, t["ctx"])) if isinstance(v, str) else v for v in dom["min_corner"])
            xmin, _, ymin, _ = self.extent(t["col"], t["row"], t["ts"])
            if xmin < mx or ymin < my:
                raise SpecError("outside_domain")
        if "polygons" not in dom:
            return
        import shapely
        if not shapely.box(*self._bounds(t)).intersects(self.zone_xy(t["ctx"]["instance"])):
            raise SpecError("outside_domain")

    def domain_extent(self, ctx):
        return [float(fmt(v, ctx)) if isinstance(v, str) else v for v in self.spec["domain"]["extent"]]

    def pixel(self, t, res):
        """(x, y) pixel size in lattice units for tile t at base resolution res."""
        fx = 1
        if self.xbands:
            _, _, ymin, ymax = self.extent(t["col"], t["row"], t["ts"])
            yc = abs((ymin + ymax) / 2)
            fx = next(f for lo, hi, f in self.xbands if lo <= yc < hi)
        return res * fx, res

    def footprint(self, t, res=None):
        xmin, xmax, ymin, ymax = self.extent(t["col"], t["row"], t["ts"])
        fp = self.spec["lattice"].get("footprint")
        if self.point:
            px, py = self.pixel(t, res)
            return [xmin - px / 2, xmax + px / 2, ymin - py / 2, ymax + py / 2]
        if not fp:
            return [xmin, xmax, ymin, ymax]
        l, r, b, tp = fp.get("pad", [0, 0, 0, 0])
        xmin, xmax, ymin, ymax = xmin - l, xmax + r, ymin - b, ymax + tp
        if "snap" in fp:
            s = fp["snap"]
            step = s["step"]
            ox, oy = (float(fmt(v, t["ctx"])) if isinstance(v, str) else v for v in s["origin"])
            w, h = xmax - xmin, ymax - ymin
            xmin = math.floor((xmin - ox) / step) * step + ox
            if s["corner"] == "top_left":
                ymax = math.ceil((ymax - oy) / step) * step + oy
                ymin = ymax - h
            else:
                ymin = math.floor((ymin - oy) / step) * step + oy
                ymax = ymin + h
            xmax = xmin + w
        return [num(float(v)) for v in (xmin, xmax, ymin, ymax)]

    def _bounds(self, t):
        xmin, xmax, ymin, ymax = self.extent(t["col"], t["row"], t["ts"])
        return xmin, ymin, xmax, ymax

    def nest(self, ts_parent, ts_child):
        f = ts_parent / ts_child
        if f != round(f) or f < 1:
            raise SpecError("nesting")
        return round(f)

    def covering(self, geom_xy, ctx, tiling=None, res=None):
        import shapely
        ts = self.tile_size(res, tiling)
        x0, y0, x1, y1 = geom_xy.bounds
        c0, r0 = self.index(x0, y0, ts)
        c1, r1 = self.index(x1, y1, ts)
        out = []
        for col in range(min(c0, c1), max(c0, c1) + 1):
            for row in range(min(r0, r1), max(r0, r1) + 1):
                t = self.tile(ctx, col, row, res, tiling)
                if shapely.box(*self._bounds(t)).intersects(geom_xy):
                    try:
                        self.check_domain(t)
                    except SpecError:
                        continue
                    out.append(t)
        return out


# -- operations ----------------------------------------------------------------

def run(case, g):
    op, i = case["op"], case["input"]
    level = {"res": i.get("res"), "tiling": i.get("tiling")}
    try:
        if op == "tile_at":
            ctx = g.instance(i["instance"])
            col, row = g.index(i["x"], i["y"], g.tile_size(**level))
            return {"col": col, "row": row, "id": g.make_id(g.tile(ctx, col, row, **level))}
        if op == "tile_at_lonlat":
            ctx = g.assign(i["lon"], i["lat"])
            x, y = g.to_xy(ctx).transform(i["lon"], i["lat"])
            col, row = g.index(x, y, g.tile_size(**level))
            tid = g.make_id(g.tile(ctx, col, row, **level), lat=i["lat"])
            if "x" not in case["expected"]:
                return {"id": tid}
            return {"instance": ctx["instance"], "x": round(x, 3), "y": round(y, 3),
                    "col": col, "row": row, "id": tid}
        if op == "lonlat_to_xy":
            ctx = g.assign(i["lon"], i["lat"])
            x, y = g.to_xy(ctx).transform(i["lon"], i["lat"])
            return {"instance": ctx["instance"], "x": x, "y": y}
        if op == "xy_to_lonlat":
            lon, lat = g.to_xy(g.instance(i["instance"])).transform(i["x"], i["y"], direction="INVERSE")
            return {"lon": lon, "lat": lat}
        if op in ("tile_extent", "tile_footprint"):
            t = g.parse_id(i["id"], domain=(op == "tile_extent"))
            if t.get("untiled"):
                e = [num(v) for v in t["extent"]]
                return {"extent": e, "dim": [round((e[1] - e[0]) / t["res"]), round((e[3] - e[2]) / t["res"])]}
            res = t["res"] or i.get("res") or g.res["listed"][0]
            ext = g.footprint(t, res)
            if g.point:
                px, py = g.pixel(t, res)
                dim = [round(t["ts"] / px) + 1, round(t["ts"] / py) + 1]
            elif g.spec["lattice"].get("footprint"):
                g.check_res(res, ext[1] - ext[0])   # pixels must fit the footprint, not the cell
                dim = [round((ext[1] - ext[0]) / res), round((ext[3] - ext[2]) / res)]
            else:
                dim = [g.tile_px(t["ts"], res)] * 2
            out = {"extent": [num(float(v) / g.us) if g.us != 1 else v for v in ext], "dim": dim}
            if "crs_authority" in case["expected"]:
                out["crs_authority"] = fmt(g.spec["family"]["crs"]["authority"], t["ctx"])
            return out
        if op == "children":
            t = g.parse_id(i["id"])
            ts = g.tile_size(**level)
            f = g.nest(t["ts"], ts)
            kids = [g.make_id(g.tile(t["ctx"], t["col"] * f + c, t["row"] * f + r, **level))
                    for r in range(f) for c in range(f)]
            return {"count": len(kids), "ids": kids}
        if op == "parent":
            t = g.parse_id(i["id"])
            f = g.nest(g.tile_size(**level), t["ts"])
            return {"id": g.make_id(g.tile(t["ctx"], t["col"] // f, t["row"] // f, **level))}
        if op == "parse":
            t = g.parse_id(i["id"])
            if t.get("untiled"):
                return {"instance": t["ctx"]["instance"], "res": t["res"]}
            ctx = {k: v for k, v in t["ctx"].items() if k != "instance"}
            return {"instance": t["ctx"]["instance"], **ctx, "res": t["res"],
                    "col": t["col"], "row": t["row"], "canonical": g.make_id(t)}
        if op == "tiles_covering_lonlat_bbox":
            import shapely
            box = shapely.box(*[i["bbox"][k] for k in (0, 1, 2, 3)])
            ids = []
            for ctx in g.instances():
                part = box.intersection(polygon(fmt(g.spec["family"]["assign"]["source"], ctx)))
                if part.is_empty:
                    continue
                part_xy = project(shapely.segmentize(part, 0.1), g.to_xy(ctx))
                ids += [g.make_id(t) for t in g.covering(part_xy, ctx, **level)]
            return {"count": len(ids)} if "count" in case["expected"] else {"ids": sorted(ids)}
        if op == "corner_lonlat":
            ctx = g.instance(i["instance"])
            xmin, xmax, ymin, ymax = g.domain_extent(ctx)
            x, y = {"upper_left": (xmin, ymax), "upper_right": (xmax, ymax),
                    "lower_right": (xmax, ymin), "lower_left": (xmin, ymin)}[i["corner"]]
            lon, lat = g.to_xy(ctx).transform(x, y, direction="INVERSE")
            return {"lon": lon % 360, "lat": lat}
        if op == "crs_deprecated":
            return {"value": g.crs(g.instance(i["instance"])).is_deprecated}
        if op == "square_centre":
            t = g.parse_id(i["id"], domain=False)  # an MGRS property, not a Sentinel-2 one
            xmin, xmax, ymin, ymax = g.extent(t["col"], t["row"], t["ts"])
            return {"x": (xmin + xmax) / 2, "y": (ymin + ymax) / 2,
                    "crs_authority": fmt(g.spec["family"]["crs"]["authority"], t["ctx"])}
        if op in ("domain_size", "in_domain"):
            ids = domain_ids(g.spec["domain"]["list"])
            return {"count": len(ids)} if op == "domain_size" else {"value": i["id"] in ids}
        if op == "scale_factor":
            ctx = g.assign(i["lon"], i["lat"])
            f = pyproj.Proj(g.crs(ctx)).get_factors(i["lon"], i["lat"])
            return {"instance": ctx["instance"], "k": f.tissot_semimajor}  # maximum length scale
    except SpecError as e:
        return {"error": str(e)}
    raise NotImplementedError(op)


def matches(got, want, tol):
    if set(got) != set(want):
        return False
    for k, w in want.items():
        v = got[k]
        if k in tol:
            if isinstance(w, list):
                if len(v) != len(w) or any(abs(a - b) > tol[k] for a, b in zip(v, w)):
                    return False
            elif abs(v - w) > tol[k]:
                return False
        elif k == "ids" and isinstance(w, list):
            if sorted(v) != sorted(w):
                return False
        elif v != w:
            return False
    return True


def main(paths):
    schema = json.loads((ROOT / "schema/crsgrid.schema.json").read_text())
    total_fail = 0
    for path in paths:
        fx = json.loads(Path(path).read_text())
        spec = json.loads((ROOT / fx["spec"]).read_text())
        jsonschema.validate(spec, schema, cls=jsonschema.Draft202012Validator)
        g = Grid(spec)
        fails = known_n = 0
        for n, case in enumerate(fx["cases"]):
            got = run(case, g)
            tol = case.get("tolerance", {})
            if matches(got, case["expected"], tol):
                continue
            known = case.get("known_difference")
            if known and matches(got, known["value"], tol):
                known_n += 1
                print(f"KNOWN {n} {case['op']} {case['input']}: oracle {case['expected']}, here {got}\n  {known['reason']}")
                continue
            fails += 1
            print(f"FAIL {n} {case['op']} {case['input']}\n  want {case['expected']}\n  got  {got}")
        print(f"{len(fx['cases']) - fails - known_n}/{len(fx['cases'])} pass, {known_n} known difference, {fails} fail ({fx['spec']}; oracle {fx['oracle']['package']}; "
              f"here PROJ {pyproj.proj_version_str})")
        total_fail += fails
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
