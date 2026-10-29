## crsgrid: survey of the mdsumner/hypertidy grid, warp and projection ecosystem

Everything below was checked against the local clones under `/perm_storage/home/mdsumner/Git/` and against GitHub (`gh api`) on 2026-10-28. The caller asked about wxylib, rwarp, controlledburn-rs and bigcurve. The most important find is a crate outside that list: **vaster-rs**, which is the shared grid core all of them sit on.

### What each project is

**gproj** (`/perm_storage/home/mdsumner/Git/gproj`, github.com/mdsumner/gproj). An R "projection concierge" built on S7 and reproj. Last commit 2026-02-06, so it is dormant.
- `params(lon, lat)` describes the input: spherical centroid, lon/lat extent, span, hemisphere class (polar, mid or equatorial, north or south), aspect (wide, tall or square), and a PCA of the points as 3D unit vectors giving azimuth, tilt and variance ratio. It also gives secant latitudes.
- `proj(p, family="auto")` turns that into a PROJ string through `auto_family()` (`R/gproj.R:302`). The decision tree is: small area → stere; polar → stere; wide mid-latitude → lcc; elongated (variance ratio > 0.8, tilt > 20) → omerc; otherwise laea. It also accepts equal_area, conformal or equidistant.
- `gproj_region()` and `grid_spec()` turn a centre plus radius into an extent and dimensions.
- `detect_rectilinear`, `test_mercator_spacing` and `test_regular_spacing` find hidden regularity. `design.md` plans a "Phase 2 detect_grid": candidate CRS → regularity test → snap resolution → recover the half-cell extent → match to an EPSG code.
- **For crsgrid:** this is the seed of the proposer. Two weaknesses: it rounds lon_0/lat_0 to only 6 decimal places, which is exactly the "auto-generated lon_0" smell crsgrid wants to lint, and it has no lattice or alignment logic. Port the heuristics to Rust; don't depend on the package.

**Blog posts** (source in `quarto-blog/posts/2026-02-20_map-extent-projection/index.qmd` and `.../2026-02-07_cryptic-curvilinear-detection/cryptic-curvilinear.qmd`).
- *Map extent:* it is not about choosing a projection, as the brief assumed. It is about how a projected rectangle's footprint in another CRS goes wrong if you transform only the corners. The fix is to densify the edges first (`GDALSuggestedWarpOutput`, `reproj_extent`), and to reproject the empty target grid rather than the data. For crsgrid this means domain↔lon/lat bboxes, "tiles covering an AOI" and the explorer's grid outlines must all densify, which is a use for bigcurve.
- *Cryptic curvilinear:* recovers EPSG:3412 at 25 km, extent ±3950000 / 4350000, from 2D lon/lat arrays to within 33 m. It also finds AVISO's Mercator latitude spacing (deviation 1e-12). This is the algorithm for crsgrid's input type 3, "propose the grid a dataset is nearly on", and for the snap-to-round-numbers lint.

**wxylib** (GitHub org `wxylib`: core, design, fixtures, web, docs, py, r; site wxylib.org). A Rust ecosystem that splits GDAL's ideas into separate crates, with GDAL used as the test oracle. Very active: many PRs on 2026-10-28.
- `wxy-domain` holds the data model. A `Domain` is a set of index dims plus `Mapping`s. Positions sit on a "rung" ladder: R0 index only, R1 regular/affine, R2 rectilinear, R3 curvilinear, and R4 implicit meshes such as DGGS. The CRS is an opaque `Frame::Crs(String)`, and its index space uses GDAL's pixel-edge convention.
- `wxy-domain::normalise` already contains the cryptic-curvilinear detector in Rust: `regular_axis`, `regular_axis_through` (the Mercator case) and `regular_grid_through` (the NSIDC case). Each returns a `Report` with a tolerance, and the caller supplies the frame-change function.
- Other crates: `wxy-warp` (built on rwarp and controlledburn), `wxy-geozarr`, `wxy-tiff`, `wxy-zarr`, and `wxy` CLI.
- `wxylib/web` runs COG warps compiled to wasm in the browser.
- Design note m20 (`wxylib/design/docs/m20-movement-crates.md`) states the architecture explicitly: rwarp, controlledburn and bigcurve form a "movement layer" on top of vaster, they meet in index space, they do no I/O, and they stay in hypertidy.

**rwarp** (`/perm_storage/home/mdsumner/Git/rwarp`, v0.3.1, released 2026-10-28). GDAL's warp pipeline in pure Rust: CRS, approximate, GCP and geolocation transformers, source windows, a serialisable `WarpPlan` / chunk planner, and resampling kernels including exact area weights.
- CRS handling is behind a `crs::CrsTransform` trait, with libproj as the default feature and `proj4rs` as the option that builds for wasm.
- `rwarp-wasm/` is a working wasm-bindgen crate.
- Depends on vaster 0.2, controlledburn 0.2.3 and proj4rs 0.3.

**controlledburn-rs** (local clone, v0.2.3). Sparse scanline rasterisation of polygons, lines and points, with exact coverage fractions (a port of exactextract's algorithm).
- No dependencies, builds for wasm.
- `GridSpec{xmin,ymin,xmax,ymax,ncol,nrow}` with row 0 at the top (`src/grid.rs`).
- `window.rs` adds `CoverageWindow` and `RowPrefix`.
- `design/grid-frontends.md` makes the rule that the engine works only in index space and every other grid type is a front end.
- **For crsgrid,** `tiles_covering(polygon)` falls out directly. Burn the AOI on a `GridSpec` where each tile is one cell (domain extent, ncol/nrow = tile counts). You get sparse runs of whole tiles plus exact partial fractions for the edge tiles. If the AOI is in another CRS, densify it first.

**bigcurve** (R, `hypertidy/bigcurve` v0.6.0, CRAN submission in progress) and **bigcurve-rs** (`hypertidy/bigcurve-rs`, stage 1, 2026-10-20, unpublished). Adaptive densification in the style of d3-geo: an edge model, a forward map and a tolerance in target-plane units. Zero dependencies, and shared edges get identical new vertices. Stage 2 (an edge-model trait including a `Planar` model, and a batched forward map) is still pending, so the API is not stable yet.

**vaster-rs** (`/perm_storage/home/mdsumner/Git/vaster-rs`, crate `vaster` 0.2.0, 2026-09-08). Grid logic with no data and no dependencies: geotransforms, cell/xy indexing, `vcrop` (snap a box to grid alignment), and `TileScheme`.
- A `TileScheme` is a CRS string (carried, never interpreted), an origin, the zoom-0 tile size, `tile_px`, `tiles_in_extent`, parent/children, and `tile_matrix_set_json` (export to OGC TileMatrixSet 2.0).
- Its hierarchy is a **quadtree only**: nesting ratio 2, signed indices, one instance per scheme.
- It has R (`hypertidy/vaster`) and Python (`pyvaster`) siblings.

### How they depend on each other
```
            wxylib (I/O, CLI, web/wasm, GeoZarr)  wxy-domain(normalise) ── vaster
                 │ consumes
   rwarp ──► controlledburn (0 deps)
     │  └──► vaster (0 deps)
     └──► proj | proj4rs (wasm)
   bigcurve-rs (0 deps; planned consumer: rwarp)
   gproj (R; reproj/PROJ)  — heuristics only, no Rust
```
There is a shared Rust core taking shape: **vaster** for lattice arithmetic, with rwarp, controlledburn and bigcurve meeting in index space. All of it builds for wasm and is dual licensed MIT/Apache (controlledburn is Apache-2.0 only).

### Recommendations for crsgrid
1. **Build crsgrid's Rust layer on vaster rather than writing new lattice code.** Use its geotransform, `vcrop` and extent/dimension functions, and match its conventions: extent ordered `[xmin, xmax, ymin, ymax]`, row 0 at the top, CRS as an opaque string. crsgrid's own contribution is family/instance templating, N×N nesting, multiple resolution ladders, the ID scheme and the canonical hash. Two options for where that lives:
   - Propose generalising `TileScheme` upstream (`nest: u32`, resolution ladder) so that `crsgrid-core` is a thin spec, ID and family layer over vaster.
   - Or keep the generalisation in crsgrid. Either way, emit OGC TMS through vaster's exporter or a superset of it.
2. **Proposer:** port gproj's `params` / `auto_family` to Rust and add rounding and alignment on top. For the "nearly on a grid" path, reuse `wxy-domain::normalise` (`regular_grid_through`, its tolerance enums and `Report`), either as a dependency or by aligning the names. Its `Report` already has the shape of a lint result: test, tolerance, accepted, why.
3. **Tiles covering an AOI:** controlledburn on a tile-as-cell `GridSpec`, with bigcurve-rs to densify the polygon or domain edges, following the blog's rule of densifying before transforming.
4. **Explorer in wasm:** transforms through rwarp's `CrsTransform` with the proj4rs feature, matching wxylib so you don't get two copies of proj4rs in one wasm build (`wxy-warp/Cargo.toml` notes the wasm-bindgen `Point` clash). An optional "preview a COG on this grid" feature could reuse wxylib/web's wasm warp. Don't build a warper.
5. **Where crsgrid fits:** a spec and policy layer above vaster and beside wxylib. wxylib could consume crsgrid specs as warp targets (GeoZarr `spatial:`/`proj:`), and wxylib/fixtures shows the conformance-oracle pattern (a `manifest.json` per case, with GDAL as the oracle) worth copying.
6. **Naming:** keep "pixel-edge index space" (GDAL convention), "extent", "dimension" and "resolution", following vaster and wxy-domain. Avoid a new `GridSpec` type: controlledburn already has one with a different shape, and the odc/xarray `GridSpec` is a third.

**Risks:**
- Everything here is pre-1.0 and changing daily; bigcurve-rs is unpublished and its API is unstable.
- vaster's quadtree assumption is a poor fit for 6×6 nesting.
- proj4rs only takes PROJ strings and EPSG codes, with no WKT2, so the "known-CRS match" lint needs a host library, which another agent is covering.
- gproj's centroid is a column mean that is not safe across the antimeridian (its own TODO). The newer code uses a spherical centroid, but check before porting.

**Not verified:** I didn't read the full source of vaster `TileScheme` (I read the README and signatures) or the wxy-domain internals beyond lib.rs and normalise.rs.
