## Survey: mdsumner's web map apps built without maplibre or deck.gl

I made no changes to any repo. Local clones were read in place. The remote repos (rangefinder, which lives at **hypertidy/rangefinder**, plus lunamap, lunashade, sidebyside, listmap3d, tasmap.org.live and rema-ovr) were shallow-cloned to /tmp/crsg, read, then deleted. I did not open any live Pages sites; what I say about them comes from READMEs and source. The bundle has one head, `refs/heads/pages-and-centred-crs` (b1a480a), and that branch already exists in the local ortho-cog-viewer clone.

### Per app

**ortho-cog-viewer** (local): a COG/XYZ/WMTS viewer that reprojects on the GPU into any display CRS.
- Stack: TypeScript + Vite, raw WebGL2 (`src/main.ts:1519`, `src/engine/MeshRenderer.ts`), geotiff.js, proj4js, proj-wasm (PROJ 9 with the EPSG database, about 15 MB, lazy-loaded in a worker, `src/core/projwasm.ts`), and rwarp-wasm (Rust, proj4rs backend, `vendor/rwarp-wasm/`).
- Technique is **mesh warping**, not a per-pixel shader. A regular mesh (64-wide by default) sits in display space. Each vertex is inverse-projected on the CPU to a source UV, and the GPU interpolates. Vertices that fail the round-trip check are masked out, which handles horizons and poles (`engine/uv.ts`, `meshEngine.ts`).
- Two view modes:
  - "fixed": a static CRS with a camera over it.
  - "centred": the CRS is a template with `{lon_0}`/`{lat_0}`. Panning moves the projection centre, so azimuthal projections spin like a globe with no special handling of poles or the antimeridian (`core/centred.ts`).
- Notable:
  - A `RasterSource` interface (CRS, extent, levels, fetch a window) makes COGs and tile pyramids interchangeable (`core/source.ts`).
  - Level choice is driven by the viewport: the UV pass reports the source bbox and its span in screen pixels, then picks the coarsest level at about 1 source px per screen px, padded 25%.
  - Two executors: proj4js runs synchronously; PROJ-wasm runs batched in a worker for projections proj4js lacks.
  - Five extra projections are added as proj4js plugins: eck4, natearth, hammer, wintri, igh (`core/projections.ts`).
  - The CRS resolution chain (`core/crs.ts`) goes: table, then zone arithmetic, then PROJ-wasm, then epsg.io. It is checked against pyproj by `tools/check-crs.py`.
  - World repeats are detected numerically (`core/wrap.ts`).
  - The fragment shader does colormaps and hillshade from float textures.
  - An exact "warp engine" option runs rwarp in a worker (`engine/warpEngine.ts`).
  - All state lives in the URL.

**wgis** (local): ortho-cog-viewer's core plus vector layers and editing.
- Notable:
  - **Adaptive mesh refinement**: a cell splits when interpolated UV error exceeds 0.5 screen px (measured through the Jacobian), down to cells about 2 px across. Map edges and limbs are clipped exactly by bisection (`src/engine/refine.ts`, `docs/mesh-refinement.md`).
  - Vector edges are halved until the projected midpoint is within half a pixel of the chord, with horizon and antimeridian cuts (`src/vector/densify.ts`).
  - A starting "scope" picker chooses the CRS: Global gives Equal Earth; Hemispheric gives a polar or facing globe; Local gives the UTM/MGA zone (`src/start/scopes.ts`).
  - `docs/foundations.md` compares OpenLayers, MapLibre and deck.gl and explains why it avoids them. Read it.

**rangefinder** (hypertidy/rangefinder): a static explorer over STAC, VRT, COG, XYZ/WMTS and Zarr sources, with compositing done in the browser.
- Stack: Leaflet 1.9 with a custom proj4 CRS (`lib/mapcrs.js`), proj4, geotiff.js, hyparquet, zarrita, and healpix-geo wasm (`lib/dggs.js`). No build step.
- Technique: **CPU warp onto an output grid**. `readWarped` (`lib/cog.js:315`) projects a lattice (every 16 px) into the source CRS, picks the overview, and resamples with bilinear interpolation of coordinates inside each lattice cell. The map simply *is* the CRS; there is no live reprojection while panning. Basemap tiles go through the same warp.
- Notable: a byte-budgeted chunk cache (`lib/chunks.js`), a curvilinear-grid rasteriser, and GeoTIFF/VRT export.

**rema-ovr** (the example you flagged; Pages UI is `docs/app.js`, about 2100 lines): an explorer for the REMA v2 Antarctic DEM.
- Stack: plain canvas 2D, proj4, geotiff.js with its worker Pool, and fzstd. No map library at all.
- Technique: **native-CRS tiled viewer, no reprojection**.
  - The display is its own tile matrix in EPSG:3031: 256 px tiles, origin ±4194304, resolution 8192/2^z, which is NASA GIBS's polar grid. GIBS imagery therefore lines up with no warping (`app.js:10,560`).
  - It parses the VRT `<Overview>` chain itself (`parseVrtOverviews`, `buildIndex`). For each tile it picks a pyramid level, then the best internal COG overview.
  - Tile loading uses a priority queue, eviction, and a **nearest-ready-ancestor overzoom fallback** (`app.js:1000`).
  - It patches geotiff.js for sparse tiles and ZSTD (`app.js:36-68`).
  - A **network ledger** wraps `fetch` to count requests and bytes per view (`app.js:70`).
  - Also: true ground distance from the polar-stereographic scale factor k(lat) (`app.js:167-177`), hillshade and contours computed per tile with a 1 px gutter, swipe comparison, GDAL/R/Python recipes for a selected box, and URL-hash state.

**listmap3d**: a 3D viewer for the Tasmania 2 m DEM.
- `maplibre/` uses a custom `cogdem://` protocol (8×8 control grid, then Terrarium encoding).
- `native/` uses three.js and is the relevant one: a quadtree in the view CRS, 128² meshes with skirts, splitting by screen-space error, about 17k proj4 calls per tile when not in MGA55, and overlays rasterised into per-tile canvases.
- Imagery comes from ArcGIS `export` with `imageSR` set to the view CRS, so the server does the reprojection.

**sidebyside**: comparison pages for the "warp" approach (an image warped to the view) versus the "cell" approach (grid cells placed directly, as in DKRZ's gridlook). The pages give crsgrid useful framing. It includes `gearbox/`, a single-file reader that counts requests and bytes under different warp policies.

**lunamap / lunashade**: Leaflet + proj4 apps for Tasmania, fixed to Web Mercator for display. lunamap uses MapLibre only for tilted 3D, and SlimSAM via Transformers.js on WebGPU for outlining features on aerial photos (`js/outline.js`). lunashade does shadow and viewshed analysis in workers. Neither has CRS-rendering tech crsgrid needs.

**Low relevance:**
- tasmap.org.live is MapLibre.
- spilhaus-tracker is a marimo notebook plus a Pyodide export (`spilhaus_wasm.py`) that asks which PROJ version each runtime ships. The lesson: PROJ versions vary across runtimes.
- wallmap is a design for a printed wall map (R/QGIS).
- wildtiles is a pipeline on the aatgrid tile scheme. Its doctrine applies: "no hand-typed extents: all geometry derives from (zone, res, col, row)".

### What crsgrid's explorer can reuse

1. **Imagery basemap in any CRS:** lift ortho-cog-viewer/wgis `core/` and `engine/` (display-space mesh, CPU inverse projection, WebGL2 texture interpolation, validity mask), with wgis `refine.ts` for limbs and edges. This is the proven route. It is not a per-pixel shader, so projection maths stays in JS and any proj4 or PROJ CRS works.
2. **Grid overlays:** a crsgrid tile is (CRS, extent, dim). Draw its outline by densifying edges in the grid's CRS into the display CRS (wgis `vector/densify.ts`), which gives lines that are both correct and cheap. The "centred" mode makes polar and antimeridian-crossing grids painless.
3. **Native-CRS mode, the cheap path:** when the display CRS equals a grid's CRS, do what rema-ovr does. Treat the crsgrid spec itself as the display tile matrix, render on canvas 2D with ancestor fallback, and skip reprojection entirely. Aligning the matrix origin to GIBS-style polar or Web Mercator sets gives a free basemap.
4. **Source abstraction:** reuse `RasterSource` and the WMTS parser; crsgrid levels map naturally onto a tile matrix set.
5. **CRS handling:** proj4js as the synchronous executor, PROJ-wasm as the lazy resolver, `crsDefinition()` as the single authority, and `check-crs.py` as a conformance harness that could also test crsgrid implementations in other languages.
6. **UX patterns:** the request/byte ledger (rema-ovr), all state in the URL, and the scope picker (wgis start/).

**Gaps and risks:**
- These apps share code by copying (wgis was copied from ortho-cog-viewer at commit 3e00588); there is no shared package. crsgrid would add a third fork unless the code is extracted.
- PROJ-wasm costs about 15 MB.
- proj4rs, used by rwarp, lacks ortho and aeqd.
- proj4js projection coverage is limited, it returns NaN without explicit `+x_0`/`+y_0`, and Winkel Tripel without an explicit `+lat_1` is ambiguous between consumers.
- Remote data needs CORS (some buckets fail it), and Web Mercator basemaps leave a hole at the pole.
- Nothing uses WebGPU for rendering; it appears only in lunamap's ML outlining. GPU work everywhere else is WebGL2.
- Cost: one proj4 round trip per vertex per frame per layer.

### Prior art

- **align** (R; `/perm_storage/home/mdsumner/Git/align`): "Grid specification primitive". A `vast` is dimension (ncol, nrow) plus extent (xmin, xmax, ymin, ymax): six numbers, with resolution, coordinates and cells all derived. API: `vast()`, `vast_res()`, `as_vast()`, `res`, `xy_from_cell`, `cell_from_xy`, the row/col helpers, `crop`/`expand`, `shrink`/`grow`, `resize`, `snap`, `is_aligned`, `offset`, `tiles(g, c(256,256))`. It refuses operations on misaligned grids (the "alignment prison"). It deliberately does **not** handle CRS, I/O or values. crsgrid is essentially align plus a CRS.
- **projwiz** (R; `/perm_storage/home/mdsumner/Git/projwiz`): a port of Projection Wizard (Savric/Jenny). It derives a projection and its parameters from a lon/lat extent and a property (equalarea, conformal, equidistant, compromise). Exports: `pw_suggest(extent, property)` returns a data.frame of candidates (name, proj, lon_0, lat_0, lat_1/2, k_0, PROJ definition) with `class_of_map` (world, hemisphere, regional) and `scale` as attributes; `pw_crs()` returns the PROJ string; `pw_scale()` returns the area-fraction scale. It overlaps with wgis's scope picker and is a natural "choose a CRS for this region" step for crsgrid.

Not checked: live Pages behaviour; how far the bundle branch has diverged from the local main; the internals of sidebyside gearbox, beyond seeing from CDN imports that it uses geotiff.js, proj4, zarrita and hyparquet.
