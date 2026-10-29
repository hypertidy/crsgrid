# crsgrid — brief

*Draft, 2026-10-28; ecosystem surveys merged 2026-10-29. Seed: generalise [aatgrid](https://github.com/mdsumner/aatgrid) from "UTM + offset/scale" to "any CRS".*

## The idea in one paragraph

aatgrid showed a nice triangle: **(1)** a systematic grid with unique IDs, **(2)** an interactive explorer that shows it on a map, and **(3)** package code that implements the scheme. All three come from a handful of numbers (UTM zone, origin `ox/oy`, tile px, resolution ladder 10/20/60). crsgrid makes that handful of numbers a first-class, portable **grid spec**, gets you there from *any* CRS (or from an AOI, a dataset, or a snippet of someone's code), lints it for sanity, and then drives the same triangle — explorer, ID/snap software, nesting — in whatever language you work in.

```
 AOI / map click / raster / code snippet / CRS string
                │  (interpret + propose)
                ▼
        draft spec ──► lint/heuristics ──► user edits (in explorer)
                ▼
          grid spec (YAML/JSON, versioned, hashable)
                ▼
  ┌─────────────┬──────────────────┬────────────────┬──────────────┐
  explorer     runtime libs        GDAL recipes     interop exports
  (JS, static) (R, Py, JS, CLI)    (-te/-tr, VRT)   (OGC TMS, STAC, GeoZarr)
```

## Core concepts

**Grid family vs grid instance.** The key generalisation hiding in aatgrid: every UTM zone is *the same grid* with one free parameter (zone). So:

- a **family** = a parameterised CRS template + a shared lattice (origin, resolutions, tile size, nesting, ID template). e.g. `tmerc, lon_0 = -183 + 6*zone, k=0.9996, +10e6 false northing`.
- an **instance** = a family with its parameters bound (zone 43S). IDs carry the instance key (`43S_L1_0006_0114`).

UTM is a family with 120 instances. A single LAEA for one site is a family with one instance. "LAEA centred every 10° of longitude at lat -90" is a family too. This is the thing that makes crsgrid more than "a TileMatrixSet with a nicer UI".

**Lattice.** origin (x0, y0) in CRS units, a resolution ladder, tile size in pixels, and level nesting ratios. Constraint: every tile edge is a whole number of pixels at every resolution in the ladder, and every level's tile edge is an integer multiple of the next. (aatgrid: 720 px, res {10,20,60} → L1 = 43.2 km, L2 = 7.2 km, 6×6 nesting. 720 is chosen because it's highly composite — that choice should be *explained* by the tool, not just made.)

**Domain.** The region where the grid is valid/usable: an aligned extent in CRS units, plus an optional *validity envelope* (e.g. max scale-factor `k`, like the explorer's `kmax` 1.05–1.4 option, or max angular distortion). Domain is always snapped to the coarsest tile. **No hardcoded limits:** the lattice is unbounded, and a domain is *seeded heuristically from the user's motivation* (an AOI, a purpose, a dataset) by the proposer, then edited. A spec may carry a domain as a record of that choice, never as a built-in cap. (aatgrid's hardcoded zone list went stale, which is the cautionary tale.)

**ID scheme.** Template string over `{instance}{level}{col}{row}`, with zero-pad width derived from domain size, row direction (north-up vs south-up count) explicit, and the option of a compact/hierarchical encoding (quadkey-like, but base-N for N×N nesting).

## The spec (strawman)

> **Superseded by `schema/crsgrid.schema.json` (v0.1 draft)**, worked through four test grids in `examples/`; see [What the test grids taught](#what-the-test-grids-taught) below. Below is the original sketch, for the record.

```yaml
crsgrid: 0.1
name: aat-utm
family:
  crs: "+proj=utm +zone={zone} +south +datum=WGS84"   # template; canonical form is PROJJSON (see Languages)
  params:
    zone: { type: int, range: [1, 60] }
  instance_key: "{zone}S"
lattice:
  origin: [140000, 20000]          # CRS units
  tile_px: 720
  resolutions: [10, 20, 60]
  levels:
    - { id: L1, res: 60, nest: 1 }
    - { id: L2, res: 10, nest: 6 }  # 6x6 children per L1
domain:
  extent: [xmin, xmax, ymin, ymax]  # per instance or derived from envelope
  envelope: { max_scale_factor: 1.2 }
ids:
  template: "{instance}_{level}_{col:04d}_{row:04d}"
  row_origin: top                   # explicit!
provenance:
  derived_from: "AOI click 2026-10-28, rounded lon_0 from 145.2827 → 145"
```

Spec is canonicalisable (sorted keys, normalised CRS) → stable hash → a grid has a fingerprint you can put in file metadata and check later ("is this COG on grid `aat-utm@3f9a…`?").

## What the test grids taught

Six grids are expressed as specs, each with fixtures from an independent oracle, and `tools/check_fixtures.py` reproduces them from the spec alone (0 failures; every known difference is recorded in the fixture with its reason).

| grid | oracle | cases | what it forced into the schema |
|---|---|---|---|
| aatgrid | aatgrid's own R functions | 44 | no fixed levels: `tile_px` invariant (tile = 720 × res for any res), nesting = resolution ratio; resolution in the ID |
| Equi7Grid | upstream test suite (269fdb6) | 16 | `instances` table (named family), `assign: polygons`, named `tile_sizes`, coordinate-coded IDs (`{xmin/100000:03}`, `!sign=N,S`), polygon domain + per-instance `min_corner` |
| Sentinel-2 MGRS | 132 real L2A footprints (starc store) + GeographicLib | 468 | `footprint` separate from the lattice cell (pad + snap one corner), named ID `codec: mgrs`, `assign: utm_standard`, `!map=` templates, enumerated `domain.list` |
| NSIDC polar stereo | NSIDC guide, Tables 5–7 | 27 | untiled grids (one raster per instance and resolution), per-instance `domain.extent` |
| REMA v2 2 m (PGC) | placement of all 5788 tiles in the rema-ovr VRT | 244 | symmetric overlap (`footprint.pad` 100 m), hierarchical IDs via `ids.fields` / `ids.recover` expressions, VRT-listed domain |
| Copernicus GLO-30 (OpenTopography) | placement of all 26475 tiles in the COP30 VRT | 211 | `unit_scale` (lattice in arcseconds), `registration: point`, `x_scale` bands by latitude |

Findings worth keeping:

- **Lattices live in CRS units, everywhere so far.** Equi7's continents share one lattice at CRS (0, 0); per-instance limits belong in the domain.
- **"Funny" parameters are often derived from data, possibly from older data.** Equi7's five-decimal false eastings put each *original V14* zone's projected bbox at exactly (0, 0) (all 7 continents, to micrometres). The v1.1 zones were redrawn with the CRSs kept, so AS, EU and OC no longer start at 0, and upstream's per-continent `min_xy` exists to absorb that. The spec records the derivation (`family.crs_derived_from`) and the lint checks and explains it.
- **Authority codes and PROJ strings for "the same" CRS can disagree.** EPSG:27701–27707 round Equi7's false origins (under 1 mm). The known-CRS lint reports the difference rather than equating them.
- **Same numbers, different datum = different grid.** NSIDC's grid numbers under Hughes 1980 (EPSG:3411) and WGS 84 (3413) put the corners up to 150 m apart on the ground. The published corners are the Hughes values.
- **"Deprecated" needs a source.** NSIDC supersedes 3411/3412 for its products; EPSG (v11.022) does not deprecate them. Specs record the steward's guidance (`superseded_by`).
- **Overlapping tiles are a footprint, not a lattice.** Sentinel-2 tiles are 100 km MGRS cells whose 109.8 km footprints have the upper-left snapped outward to the 60 m lattice (anchored at the equator, so 40 mod 60 in the south). Not "padded 4900 m on every side", as one comment in starc says.
- **Published tile lists lag production.** 10 Antarctic tiles (band C) exist in the Sentinel-2 archive but not in the valid list used as the domain.
- **ID conventions differ between stewards of the same scheme.** Sentinel-2 writes zones unpadded (`8CMC`), MGRS/GeographicLib pads (`08CMC`); the spec makes one canonical and parses the rest.
- **Distortion needs a definition.** crsgrid uses the ellipsoidal maximum length scale (Tissot semi-major axis). Equi7's spherical k is 8.6e-4 lower at its Atlantic edge, where it reaches 14.4% (k ≈ 1.144 at 29°W 24°N): the horrific angular width.
- **Overlap is real practice, so describe it exactly and lint it.** REMA 2 m tiles are 50 km cells delivered 100 m larger on every side; 7 of 5788 are one pixel larger still. Its 32 m product pads 96 m, because 32 m doesn't divide 100 m.
- **"GDAL just works" is alignment, not a spec.** The COP30 VRT upsamples every tile to 1 arcsec with fractional DstRects (0.00004 px residue) and clips the 21 antimeridian tiles with fractional SrcRects. Underneath is a clean rule: 1° cells, pixel-is-point (n + 1 pixels), x pixel 1, 1.5, 2, 3, 5, 10 arcsec by |lat| band, exact in arcseconds.
- **Oracle tolerances are part of the oracle.** Upstream Equi7 asserts with numpy's default rtol 1e-7, about 0.5 m at x ≈ 5e6; fixtures record tolerances, not just values.
- **Rust has no maintained GeographicLib MGRS.** `geographiclib-rs` (georust) is geodesic-only; `geoconvert` (partial GeoConvert port) is dormant since 2024-01; `mgrs-convert` is new and unproven. The 100 km codec is small, so crsgrid implements it and keeps GeographicLib (R `geographiclib`) as the oracle.

## Lint pass (first cut)

`tools/lint.py` runs functions over a spec and reports findings (code, severity, where, message, fix). On the six test grids it currently reports:

| lint | fires on | what it says |
|---|---|---|
| `auto-param` | Equi7 | 14 five-decimal false origins, each *explained*: puts the recorded V14 zone's bbox minimum at 0 (residual under 0.5 mm) |
| `crs-forms-differ` | Equi7 | EPSG:27701–27707 vs the PROJ strings: 0.03–0.52 mm, naming the rounded parameters |
| `same-numbers-different-crs` | NSIDC | Hughes vs WGS 84 instances with identical numbers: up to 149 m (north), 125 m (south) at grid corners |
| `superseded` | NSIDC | 3411/3412 superseded by the steward, not deprecated by EPSG |
| `overlapping-footprints` | REMA, Sentinel-2 | 200 m and 9.8 km overlaps |
| `shared-edge-pixels`, `lonlat-pixel-shape` | COP30 | pixel-is-point edges; ground pixel aspect per band (0.44–1.03) |
| `res-not-cell` | Sentinel-2 | 60 m divides the 109.8 km footprint but not the 100 km cell |
| `max-scale` | Equi7 | maximum length scale per continent over its zone: 15.5% (EU) to 92.1% (OC, on its Pacific boundary) |
| `unpinned-domain` | COP30 | the tile list is a live URL |

Still to come: alignment between two specs (aatgrid vs Sentinel-2 at 60 m, NSIDC vs a GIBS polar matrix), ID width vs index range, the proposer's rounding suggestions, and a land-only variant of `max-scale`.

## Getting to a spec: interpretation + proposal

Inputs, roughly in order of ambition:

1. **Explicit CRS + extent** — easy path.
2. **AOI on the map** (click/drag/polygon) — tool proposes a CRS: LAEA/TM/oblique-stereo centred on the AOI, polar stereo near the poles, conic for wide-in-longitude mid-lat. Picks parameters, rounds them, sizes the domain "a little wider", sane aspect ratio.
3. **A dataset** — read a raster's extent/res/CRS (GDAL), propose the grid it's *nearly* on, report misalignment.
4. **Code / spec fragments** — paste a `gdalwarp -t_srs ... -te ... -tr ...`, an R `terra::rast(...)`, an xarray/odc `GridSpec`, a STAC item's `proj:` fields, an OGC TMS JSON — heuristically pull out CRS/extent/res and turn it into a draft spec. (This is where an LLM-assisted parser is genuinely useful, but the output is always a spec the user can see and lint, never magic.)

## Linting / heuristics (the "why 145.2827373400999?" layer)

Checks with a severity and a suggested fix:

- **Auto-generated-looking parameters**: lon_0/lat_0/standard parallels with >3–4 decimal places or float noise → "round to 145, 145.25, or 145.3? distortion change over your domain is <0.01%."
- **Known-CRS match**: "this is EPSG:3031 / 3412 / an existing UTM zone — use the code".
- **Lattice arithmetic**: origin not a multiple of the coarsest tile; resolution doesn't divide tile edge; resolution ladder whose LCM gives ugly tile sizes → suggest a tile_px (720, 600, 512, 1024, 3600…) and show the trade-offs.
- **Non-round numbers in CRS units**: false easting/northing, extent edges, resolution like 9.99 m.
- **Distortion**: scale factor / area / angular distortion over the domain exceeds envelope → suggest recentring or a different projection class.
- **Aspect ratio & size**: extremely thin domains, domains far bigger than the AOI, tile counts that blow up ID widths.
- **Datum/axis gotchas**: WGS84 ensemble vs realisation, lat/lon axis order, feet units, lon_0 outside [-180,180], `+south` vs negative northing.
- **Alignment with ecosystems**: is it aligned with Sentinel-2 MGRS (aatgrid already has an MGRS bridge), Landsat, ESA WorldCover, an existing ODC product? Report "aligned / offset by N px / incompatible".

Lints are just functions over the spec, so they're implementable in every target language and runnable in CI.

## What a spec drives (the operations)

The minimal portable API every runtime should implement — all pure index arithmetic, no geometry libraries required except CRS transforms:

| op | in → out |
|---|---|
| `tile_at(x, y, level)` / `tile_at_lonlat` | point → ID |
| `tile_extent(id)` | ID → extent (+ dim at a given res) |
| `snap(extent, level, mode = in/out/near)` | arbitrary extent → aligned extent |
| `tiles_covering(extent | polygon, level)` | → IDs |
| `parent(id)`, `children(id)`, `neighbours(id)` | nesting navigation |
| `instance_for(lon, lat)` | family → which instance (zone) |
| `gdal_args(id, res)` | → `-t_srs -te -tr` / VRT |
| `validate(spec)` | → lint report |

Instances overlap at edges (UTM zones do); the spec should say how a point is assigned (primary instance rule) vs. which instances *can* represent it.

## Delivery: runtime vs codegen

Prefer **thin runtime libraries that read the spec** over generated code: one implementation per language, and specs are data. Offer **codegen** only as "bake this spec into a dependency-free file" (a single R/Py/JS file with constants inlined), which is useful for embedding and for archival. A conformance test suite (spec + expected IDs/extents as JSON fixtures) keeps the runtimes honest.

Where crsgrid sits: **a spec and policy layer above vaster, beside wxylib.** vaster does lattice arithmetic. crsgrid adds families/instances, N×N nesting, resolution ladders, IDs, the canonical hash, lints and the proposer. wxylib, rwarp and controlledburn can then consume a crsgrid spec as a warp or rasterise target, for example through GeoZarr `spatial:`/`proj:`.

## The explorer

It generalises aatgrid's `explorer/`: load any spec (URL param, drag-drop, paste), render tiles at the right levels, a reprojected imagery basemap, coastline, distortion overlay and family-instance boundaries; hover gives the ID, click gives the extent and GDAL command. There is also an *authoring* mode: draw an AOI, accept the proposal, tweak params with live lint feedback, export the spec. It is a static site with no server, like aatgrid's Pages deployment.

Most of the rendering already exists in the non-maplibre/deck.gl apps (details in `notes/survey-web-map-apps.md`):

1. **Native-CRS mode (build this first).** When the display CRS is the grid's own CRS, the spec *is* the display tile matrix. Do what rema-ovr does: canvas 2D, no reprojection, a priority queue with a nearest-ready-ancestor fallback. If the matrix origin aligns with GIBS polar or Web Mercator tile sets, the basemap comes for free. This is close to what aatgrid already does.
2. **Any-CRS mode.** Lift ortho-cog-viewer/wgis `core/` + `engine/`: a mesh in display space, CPU inverse projection per vertex, WebGL2 interpolating textures, a round-trip validity mask, and wgis `refine.ts` adaptive refinement for limbs and edges. Projection maths stays in JS, so any proj4/PROJ CRS works. The "centred" mode (panning moves `{lon_0}`/`{lat_0}`) makes polar and antimeridian-crossing grids painless.
3. **Grid overlays.** A tile is (CRS, extent, dim). Densify its edges in the grid's CRS and project them to the display (wgis `vector/densify.ts`, or bigcurve-rs in wasm). Densify before transforming, as the map-extent-projection post argues.
4. **Reusable pieces.** The `RasterSource` interface and WMTS parser, viewport-driven level choice (about 1 source px per screen px), the `crsDefinition()` resolution chain (table → zone arithmetic → PROJ-wasm → epsg.io) with `check-crs.py` against pyproj, rema-ovr's request/byte ledger and GDAL/R/Python recipe panel, all state in the URL, and wgis's scope picker (global / hemispheric / local) as the authoring entry point.
5. **Optional "preview a COG on this grid".** Reuse wxylib/web's wasm warp, or ortho-cog-viewer's rwarp worker. Don't write a new warper.

**Precondition:** these apps share code by copying (wgis forked ortho-cog-viewer at 3e00588). Extract a small shared JS rendering core before building the explorer, or crsgrid becomes a third fork.

## Languages: how to be cross-language

**The contract is the spec plus the test suite, not any one codebase.** If the spec's JSON Schema and a set of JSON fixtures (spec → input → expected output) are the source of truth, any implementation that passes the fixtures is correct. Everything below is about where to put implementation effort.

**Split the work by how hard it is.**

| layer | what | needs PROJ? | build on |
|---|---|---|---|
| lattice core | ID ↔ extent, snap, cover, parent/children, ID parse/format, arithmetic lints | **no**: works on coordinates already in the CRS | vaster (Rust, R, Python), align's API; native TS |
| CRS layer | lon/lat → instance, transforms, distortion/envelope, known-CRS matching | yes | the host language's library (table below) |
| proposer / interpreters | AOI → CRS + rounded params + domain; parse gdal/STAC/TMS/GridSpec | yes, plus heuristics | ports of gproj + projwiz, wxy-domain `normalise` |

### Lattice core: extend vaster, don't start fresh

*This changes the earlier plan to write a new dependency-free core in each language.* **vaster** already exists as a zero-dependency grid core in Rust (`vaster` 0.2.0), R (`hypertidy/vaster`) and Python (`pyvaster`). It has geotransforms, cell/xy indexing, `vcrop` (snap a box to the grid) and a `TileScheme` with parent/children and OGC TileMatrixSet 2.0 export. rwarp, controlledburn and wxylib all meet in its index space.

- Match its conventions: extent ordered `[xmin, xmax, ymin, ymax]`, row 0 at the top, pixel-edge index space (GDAL), CRS carried as an opaque string, and the names "extent", "dimension", "resolution".
- The gap is that `TileScheme` is **quadtree only** (nesting 2, one instance per scheme), which can't express aatgrid's 6×6. **Decision (2026-10-29): the generalisation (N×N nesting, resolution ladders, families) lives in crsgrid for now**, on top of vaster's primitives, with TMS export as a superset of vaster's. Once it settles, move it to vaster (`nest: u32`, a ladder) or elsewhere, if that's clearly where it belongs. Keep the API close to vaster's naming so a later move is cheap. See open question 8.
- align (R) is the API reference: `vast`, `snap`, `is_aligned`, `offset`, `tiles`, and its refusal to operate on misaligned grids (the "alignment prison"). crsgrid is essentially align plus a CRS. Its refusal stance belongs in the linter.
- TypeScript has no vaster; write a small native port for the explorer, held to the fixtures.

### CRS layer: don't own it

Each language calls what it already has. The core API takes a transform function or pre-transformed coordinates. The landscape as of 2026-10-28 (details in `notes/survey-rust-wasm-proj.md`):

| | role for crsgrid | notes |
|---|---|---|
| PROJ (gdalraster/sf, pyproj, `proj` crate) | reference oracle; R/Python hosts | `proj_factors` for distortion; the `proj` crate doesn't build for wasm |
| proj4rs 0.3 (Rust, wasm) | lean wasm executor | ortho, omerc, tpers and a wasm-safe aeqd landed 10-14..10-26; methods it has match PROJ to 0.000 mm. **No ob_tran, no WKT2/PROJJSON, no distortion API.** npm is still 0.1.12 |
| terrio-crs (Rust, npm `terrio-js`) | WKT2/PROJJSON reader for the Rust linter | the only pure-Rust crate that parses WKT2/PROJJSON without an EPSG ID; ob_tran landed 10-28. Large (embedded registries); accuracy **not independently measured** |
| math.gl v5 `@math.gl/geospatial` (TS) | explorer distortion overlay | native TS, 37 methods including ob_tran; `ProjectionAnalysis` gives Jacobian, scale factors and distortion, the only non-PROJ public distortion API |
| proj4js 2.22 | explorer synchronous executor | 37 projections; weak WKT2/PROJJSON; returns NaN without explicit `+x_0`/`+y_0` |
| proj-wasm (clj-proj) | lazy full-PROJ resolver in the browser | about 15 MB, load in a worker |
| proj-core, miniproj, geodesy | avoid | silently wrong on southern cones and south polar cases |

Consequences:

- **PROJJSON is the spec's canonical CRS encoding.** Every layer except proj4rs reads it. Keep a PROJ string as an optional hint for proj4rs, and the `{param}` template for families. The strawman spec's `crs:` field should become `crs: { projjson: …, proj_hint: "…" }` with the template alongside.
- **crsgrid computes scale factors by finite differences** of forward(x, y), so the distortion lint works with any backend. Test against PROJ's `proj_factors` and math.gl's `ProjectionAnalysis`.
- **ob_tran (rotated-pole grids)** works in terrio-crs, math.gl and proj4js but not proj4rs. Either contribute it to proj4rs (a small rotation; terrio's `rotation.rs` and math.gl's `ob-tran.ts` are references) or special-case it.
- **The conformance oracle is rust-proj-capability's fixture harness** (93 fixtures against PROJ 9.9). Re-run it on proj4rs 0.3 and terrio-crs, neither of which has been measured yet. Copy wxylib/fixtures' `manifest.json`-per-case pattern, with GDAL/PROJ as the oracle, for crsgrid's own fixtures.

### Proposer and linter: once, in Rust

It changes often and has many heuristics, so you don't want four copies drifting apart. Compile it to wasm for the explorer, build it as a CLI, ship a PyO3/maturin wheel for Python, and let R call the CLI (extendr is optional; it adds CRAN friction).

- **CRS choice:** port gproj's `params` / `auto_family` decision tree (small or polar → stere; wide mid-latitude → lcc; elongated → omerc; else laea) and projwiz's `pw_suggest(extent, property)` candidates. Add the rounding and lattice alignment neither has. gproj rounds lon_0/lat_0 to 6 dp, exactly the smell crsgrid lints, and its older centroid is a column mean that breaks across the antimeridian. Use the spherical centroid.
- **"Nearly on a grid" (datasets):** reuse `wxy-domain::normalise` (`regular_grid_through`, its tolerance enums). Its `Report` (test, tolerance, accepted, why) is already the shape of a lint result; align names with it. gproj's planned `detect_grid` (candidate CRS → regularity test → snap resolution → recover half-cell extent → match EPSG) and the cryptic-curvilinear-detection post describe the same pipeline.
- **Tiles covering an AOI:** controlledburn on a tile-as-cell grid, with bigcurve-rs densifying polygon and domain edges before transforming.
- **wasm builds:** route transforms through rwarp's `CrsTransform` with the proj4rs feature, matching wxylib, so a combined build doesn't contain two proj4rs copies (`wxy-warp/Cargo.toml` notes the wasm-bindgen `Point` clash).

**A CLI that reads and writes JSON is the universal fallback.** For example `crsgrid tile-at spec.json --lonlat 73.5,-53.1`, `crsgrid lint spec.json`, `crsgrid gdal-args spec.json 43S_L2_0040_0700`. Any language, shell script or Makefile can call it.

### Details that cause cross-language bugs (put these in the spec, not in the code)

- **Floating-point snapping.** Python, R, JS and Rust can disagree on `floor((x - x0)/res)` when x sits on a tile edge, or when res is 0.1. The spec should require integer lattice arithmetic. Either store origin and resolutions as integers in a stated unit (e.g. mm), or as exact decimals, and define the edge rule (half-open `[min, max)` with a stated epsilon). Fixtures must include coordinates exactly on edges.
- **Canonical form for hashing.** JSON is canonical, canonicalised with RFC 8785 (JCS) before hashing. YAML is fine for writing specs by hand but is never hashed.
- **CRS normalisation.** PROJJSON as above. A PROJ string alone round-trips differently across PROJ versions, and runtimes ship different PROJ versions (spilhaus-tracker's Pyodide check shows it), so record the PROJ version used to produce fixtures.
- **Columnar API.** Operations take and return arrays (x[], y[] → id[]), not one point at a time. That suits R, numpy, Arrow and Rust iterators, and keeps wasm calls cheap.
- **ID format strings.** No printf/Python format syntax in the spec. Use a tiny template grammar of its own (`{instance}_{level}_{col:4}_{row:4}`), with a fixture for every format.
- **Naming.** Don't add another `GridSpec` type: controlledburn has one, and odc/xarray has a differently shaped third. "Spec" is the document; the lattice types follow vaster.

**Suggested order:** schema + fixtures → R runtime on vaster/gdalraster (reusing aatgrid code) → TS lattice core + native-CRS explorer → Rust core on vaster + CLI + lint → Python (pyvaster, then the Rust wheel for lint/proposal) → wasm proposer and any-CRS mode in the explorer.

## Prior art to position against (and interoperate with)

- **OGC 2D TileMatrixSet v2** (and `morecantile` in Python): the closest standard. It supports arbitrary CRS and per-level cell sizes, so 6×6 nesting is expressible. It lacks families/parameterised CRS, linting and authoring. crsgrid **exports instances as TMS JSON** (vaster already has an exporter) and imports TMS.
- **ODC `GridSpec` / `odc-geo` GeoBox**: a very similar lattice model. Import/export.
- **align** and **projwiz** (your R packages): the lattice API and CRS-choice step respectively; see above.
- **Equi7Grid** (in `~/Git/equi7grid`): a hand-designed family (seven continental azimuthal equidistant instances with the odd central params). A good test of whether crsgrid can express named-instance families.
- **MGRS / Sentinel-2 tiling, Landsat WRS, EASE-Grid 2.0, NSIDC polar stereo, NASA GIBS polar tile matrices**: test cases and alignment targets.
- **DGGS (H3, rHEALPix, ISEA)**: a different philosophy (equal-area global cells, no CRS choice). Out of scope, though rangefinder already uses healpix-geo wasm and terrio-crs now has healpix/rhealpix/isea.
- **STAC `proj:` extension, GeoZarr / multiscales**: places to write the grid fingerprint and per-asset alignment.
- **wildtiles** states the doctrine crsgrid generalises: "no hand-typed extents: all geometry derives from (zone, res, col, row)".

## Scope guardrails

- Rectilinear grids in a projected (or lon/lat) CRS. Not DGGS, not curvilinear model grids. Rotated-pole grids are "just a CRS" with `ob_tran`, so they come in once the backend supports it (see the CRS table).
- The spec is the product; the explorer and libraries are reference implementations.
- No server state. Everything is reproducible from the spec.
- Don't build a projection engine or a warper; the ecosystem has both.

## Risks

- **Everything underneath is pre-1.0 and moving daily**: proj4rs (0.3 released 10-26), terrio-crs (crate extracted 10-25), math.gl v5 (beta 10-28), bigcurve-rs (unpublished, stage 2 API pending), vaster 0.2. Pin versions in fixtures, and keep crsgrid's dependency surface small.
- vaster's quadtree assumption is a poor fit for 6×6 nesting until it is generalised.
- terrio-crs and math.gl accuracy figures are their own claims.
- Copy-forked JS rendering code (see the explorer precondition).
- PROJ-wasm's ~15 MB, CORS failures on some buckets, and the polar hole in Web Mercator basemaps all affect the explorer.

## Open questions

1. Family parameter types beyond integer zone: continuous params snapped to a step (lon_0 every 10°)? (Named instances are now an `instances` table, from Equi7.)
2. ~~Same lattice across instances with different false eastings: CRS units or per-instance origin?~~ Lattice in CRS units, for both aatgrid and Equi7. Per-instance limits go in the domain (`min_corner`), not the origin.
3. Row direction and level naming: pick one default and make it explicit, or follow TMS (top-left origin, zoom 0 = coarsest)? vaster already uses row 0 at the top.
4. ID scheme: human-readable (aatgrid style), compact hierarchical, or both (alias)?
5. Where does the distortion envelope come from by default: a scale-factor threshold, or "the AOI plus X%"?
6. How much of "interpret arbitrary code" is rule-based parsing vs LLM-assisted? (Proposal: rules for GDAL/STAC/TMS/GridSpec; an LLM as optional assist that must emit a spec.)
7. Relationship to aatgrid: does aatgrid become `crsgrid` + one spec file? (A good acceptance test.)
8. *Deferred:* the nesting/ladder/family generalisation starts in crsgrid. When does it graduate to vaster's `TileScheme` (or somewhere else), and what's the signal: a second consumer such as wxylib or rwarp needing it, or the API surviving all the test-case grids unchanged?
9. Rust linter CRS input: depend on terrio-crs for WKT2/PROJJSON, or accept PROJJSON only, with no registry, and let hosts resolve EPSG codes?
10. Which shared JS rendering core gets extracted from ortho-cog-viewer/wgis, and where does it live?

## Suggested first steps

1. Write the spec schema (JSON Schema, PROJJSON CRS) and express **aatgrid**, **Sentinel-2 MGRS**, **Equi7Grid**, **NSIDC polar stereo 25 km**, a **GIBS polar tile matrix** and **one ad-hoc LAEA site grid** in it. If any don't fit, fix the schema before writing code.
2. Sketch crsgrid's own tile-scheme type (vaster primitives + `nest` + a ladder + families), named to match vaster's `TileScheme` so it could move there later.
3. Reference runtime in R on vaster + gdalraster (reusing aatgrid's index-first functions) implementing the op table + `validate()`.
4. Conformance fixtures (JSON, `manifest.json` per case) generated from the R runtime with PROJ as the oracle. Re-run rust-proj-capability against proj4rs 0.3 and terrio-crs.
5. Explorer, native-CRS mode: port aatgrid's explorer to load a spec instead of hardcoded constants (rema-ovr style). Then add authoring + a lint panel.
6. TMS JSON export/import; Python runtime on pyvaster against the fixtures.
7. Rust proposer/linter (gproj + projwiz heuristics, wxy-domain normalise) as CLI + wasm; then the any-CRS explorer mode on the extracted ortho-cog-viewer/wgis core.

Background surveys behind this brief (2026-10-28): `notes/survey-rust-wasm-proj.md`, `notes/survey-web-map-apps.md`, `notes/survey-hypertidy-grid-warp.md`.
