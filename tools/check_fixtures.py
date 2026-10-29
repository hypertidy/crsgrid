"""Check a crsgrid spec reproduces its fixtures, using only the spec.

    python3 tools/check_fixtures.py fixtures/aatgrid.json

A throwaway reference reader for spec v0.1, not a runtime: it exists to
show the spec carries enough information. Needs jsonschema and pyproj.
"""
import json
import math
import re
import sys
from pathlib import Path

import jsonschema
import pyproj

ROOT = Path(__file__).resolve().parent.parent
FIELD = re.compile(r"\{(\w+)(?::0(\d+))?\}")


def fmt(template, **values):
    def sub(m):
        v = values[m.group(1)]
        return f"{int(v):0{m.group(2)}d}" if m.group(2) else str(v)
    return FIELD.sub(sub, template)


def template_regex(template):
    pos, out = 0, []
    for m in FIELD.finditer(template):
        out.append(re.escape(template[pos:m.start()]))
        out.append(rf"(?P<{m.group(1)}>-?\w+?)")
        pos = m.end()
    out.append(re.escape(template[pos:]))
    return re.compile("^" + "".join(out) + "$")


class Grid:
    def __init__(self, spec):
        self.spec = spec
        fam, lat = spec["family"], spec["lattice"]
        self.q = lat["quantum"]
        self.x0, self.y0 = lat["origin"]
        self.up = lat["index"]["row_direction"] == "up"
        self.negative = lat["index"].get("negative", True)
        self.tiling = lat["tiling"]
        self.res = lat["resolutions"]
        self.names = self.res.get("names", {})
        (self.pname, self.param), = fam["params"].items()  # one-param families only, for now
        self.key_regex = template_regex(fam["instance_key"])

    # -- lattice, in integer quanta
    def _q(self, v):
        n = v / self.q
        if n != round(n):
            raise ValueError(f"{v} is not a multiple of quantum {self.q}")
        return round(n)

    def check_res(self, res):
        if res in self.res["listed"]:
            return
        if not self.res.get("open", False):
            raise ValueError(f"resolution {res} not listed")
        self._q(res)

    def tile_size(self, res):
        self.check_res(res)
        if "tile_px" in self.tiling:
            return self.tiling["tile_px"] * res
        return self.tiling["tile_size"]

    def tile_px(self, res):
        return self.tiling.get("tile_px") or self.tiling["tile_size"] / res

    def tile_at(self, x, y, res):
        ts = self._q(self.tile_size(res))
        # coordinates may be off-lattice (they are points): floor in quanta space
        col = math.floor((x / self.q - self._q(self.x0)) / ts)
        dy = (y / self.q - self._q(self.y0))
        row = math.floor(dy / ts) if self.up else math.floor(-dy / ts)
        if not self.negative and (col < 0 or row < 0):
            raise ValueError("negative tile index")
        return col, row

    def tile_extent(self, col, row, res):
        ts = self.tile_size(res)
        xmin = self.x0 + col * ts
        ymin = self.y0 + row * ts if self.up else self.y0 - (row + 1) * ts
        return [xmin, xmin + ts, ymin, ymin + ts]

    def nest(self, res_parent, res_child):
        f = res_parent / res_child
        if f != round(f) or f < 1:
            raise ValueError("nesting")
        return round(f)

    # -- instances
    def key(self, pval):
        return fmt(self.spec["family"]["instance_key"], **{self.pname: pval})

    def pvalue(self, key):
        return int(self.key_regex.match(key).group(self.pname))

    def assign(self, lon, lat):
        a = self.spec["family"]["assign"]
        assert a["method"] == "lon_bands"
        lon = (lon - a["lon_start"]) % 360
        return math.floor(lon / a["width"]) + a["first"]

    def crs(self, pval):
        c = self.spec["family"]["crs"]
        return pyproj.CRS(fmt(c["authority"], **{self.pname: pval}) if "authority" in c
                          else fmt(c["proj"], **{self.pname: pval}))

    # -- ids
    def make_id(self, pval, res, col, row):
        name = {v: k for k, v in self.names.items()}.get(res, "")
        return fmt(self.spec["ids"]["template"], instance=self.key(pval),
                   res=res, name=name, col=col, row=row, **{self.pname: pval})

    def parse_id(self, id_):
        ids = self.spec["ids"]
        for t in [ids["template"]] + ids.get("parse_aliases", []):
            m = template_regex(t).match(id_)
            if not m:
                continue
            d = m.groupdict()
            if "res" in d:
                res = int(d["res"])
            elif d.get("name") in self.names:
                res = self.names[d["name"]]
            else:
                continue
            return {"instance": d["instance"], "zone": self.pvalue(d["instance"]),
                    "res": res, "col": int(d["col"]), "row": int(d["row"])}
        raise ValueError(f"unparseable id {id_}")


def run(case, g):
    op, i = case["op"], case["input"]
    if op == "tile_at":
        col, row = g.tile_at(i["x"], i["y"], i["res"])
        return {"col": col, "row": row, "id": g.make_id(g.pvalue(i["instance"]), i["res"], col, row)}
    if op == "tile_extent":
        p = g.parse_id(i["id"])
        px = g.tile_px(p["res"])
        return {"extent": g.tile_extent(p["col"], p["row"], p["res"]), "dim": [px, px],
                "crs_authority": fmt(g.spec["family"]["crs"]["authority"], zone=p["zone"])}
    if op == "tile_at_lonlat":
        z = g.assign(i["lon"], i["lat"])
        tr = pyproj.Transformer.from_crs("EPSG:4326", g.crs(z), always_xy=True)
        x, y = tr.transform(i["lon"], i["lat"])
        col, row = g.tile_at(x, y, i["res"])
        return {"instance": g.key(z), "x": round(x, 3), "y": round(y, 3),
                "col": col, "row": row, "id": g.make_id(z, i["res"], col, row)}
    if op in ("children", "parent"):
        p = g.parse_id(i["id"])
        try:
            if op == "children":
                f = g.nest(p["res"], i["res"])
                kids = [g.make_id(p["zone"], i["res"], p["col"] * f + c, p["row"] * f + r)
                        for r in range(f) for c in range(f)]
                return {"count": len(kids), "ids": kids}
            f = g.nest(i["res"], p["res"])
            return {"id": g.make_id(p["zone"], i["res"], p["col"] // f, p["row"] // f)}
        except ValueError as e:
            return {"error": str(e)}
    if op == "parse":
        p = g.parse_id(i["id"])
        return {**p, "canonical": g.make_id(p["zone"], p["res"], p["col"], p["row"])}
    raise NotImplementedError(op)


def main(path):
    fx = json.loads(Path(path).read_text())
    spec = json.loads((ROOT / fx["spec"]).read_text())
    schema = json.loads((ROOT / "schema/crsgrid.schema.json").read_text())
    jsonschema.validate(spec, schema, cls=jsonschema.Draft202012Validator)
    g = Grid(spec)
    fails = 0
    for n, case in enumerate(fx["cases"]):
        got = run(case, g)
        if got != case["expected"]:
            fails += 1
            print(f"FAIL {n} {case['op']} {case['input']}\n  want {case['expected']}\n  got  {got}")
    print(f"{len(fx['cases']) - fails}/{len(fx['cases'])} cases pass "
          f"({fx['spec']}, oracle {fx['oracle']['package']}, PROJ {fx['oracle']['proj']}; here pyproj {pyproj.__version__}, PROJ {pyproj.proj_version_str})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
