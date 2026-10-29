## crsgrid: what Rust, wasm and JS projection libraries support (checked 2026-10-28, read-only)

Bottom line: the field moved a lot in the two weeks after the capability study, so parts of its matrix are already out of date.

**Sources**
- Capability study (local clone): `/perm_storage/home/mdsumner/Git/rust-proj-capability/README.md`, `capability-matrix.md`, `report.md`, `results/per-fixture.md`, `platform/README.md`.
- GitHub: 3liz/proj4rs, mdsumner/proj4rs, opengeos/terrio (`crates/terrio-crs`), visgl/math.gl, proj4js/proj4js, willcohen/clj-proj.

### 1. rust-proj-capability, measured 2026-10-15
- 93 fixtures, 11 columns, judged against PROJ 9.9.0 with network off and no grids. Each library is scored in 15 layers: 7 for reading CRS input (I1–I7) and 8 for executing transforms (E1–E8).
- Exact matches (within 1 mm) out of 93: proj-sys 88, proj 86, proj-core 33, wbprojection 32 (but 33 off), proj4rs 20 (46 when fed bound strings), geodesy 9, miniproj 8.
- **Main finding:** the datum step is the expensive gap, not the projection maths. proj4rs reproduces every method it has to 0.000 mm, forward and inverse. That includes tmerc 18° off the central meridian and the near-antipodal laea point. The one exception is eqc: PROJ 9.9 is now ellipsoidal there.
- **Silently wrong results:**
  - proj-core: inverse LCC/AEA on southern cones is out by 2880 and 3421 km.
  - miniproj: south polar stereographic is out by 287,000 km.
  - geodesy: south polar laea is out by 11,125 km.
  - wbprojection: many methods are spherical approximations.
- **WKT/PROJJSON:** no pure-Rust crate (as of the study) parses WKT or PROJJSON that lacks an EPSG ID. proj4wkt adds a spurious `towgs84=0`. proj-core rejects PROJ's own PROJJSON because of its axis order.
- **wasm:** every pure-Rust crate builds for wasm32. proj4rs needs `default-features=false`, because its aeqd compiled C. The `proj` crate does not build for wasm.
- **Stale entries:** the study tested proj4rs 0.2.1 and listed ob_tran, ortho, omerc and tpers as unsupported in pure Rust. Both of those points have since changed (sections 2 and 3).

### 2. proj4rs is moving fast
- 0.3.0 was published on crates.io on 2026-10-26. Between 10-14 and 10-21 it added poly, ortho, gnom, omerc, sinu, tpers/nsper, cass, eqdc, robin and eqearth.
- Several of those PRs came from your fork: #52, #59, #60 (projection gallery, https://mdsumner.github.io/proj4rs/) and #61 (tmerc spherical inverse fix).
- It now uses PROJ 9.2's mlfn series and PROJ 9.7's authalic series, so laea/aea inverses are accurate to double precision.
- A pure-Rust geodesic (2026-10-17) replaces the C one. That fixes aeqd under wasm and removes the `geod_*` symbol clash with libproj. There are also new PHP and Python bindings.
- The fork's branches have all been merged into upstream; the fork has nothing upstream lacks.
- **Still missing:** ob_tran, WKT2/PROJJSON, any public scale-factor/distortion API, and 3D.
- The npm package `proj4rs` is still at 0.1.12, while crates.io has 0.3.0.

### 3. terrio-crs (opengeos/terrio) is the big newcomer
- The repo was created 2026-10-14. `terrio-crs` 0.1.0 was extracted as its own crate on 2026-10-25 and is MIT licensed.
- It has EPSG, ESRI and IAU tables, WKT1/WKT2, PROJJSON v0.7 read (10-26) and write (10-27), operation selection, epochs and grids.
- It natively supports 42 of the 117 methods in its own PROJ conformance inventory; an optional `wbprojection-fallback` feature adds 46 more.
- `ob_tran` landed 2026-10-28 (`src/rotation.rs`) and handles rotated-pole CF/GRIB grids. igh, qsc, healpix/rhealpix and isea landed 10-27/28.
- It ships to the browser as the npm package `terrio-js` (`js/`).
- **Unverified:** its accuracy has not been measured independently; it is not in the capability study. Its Jacobians are internal only, with no public distortion API.

### 4. math.gl
- There is no `@math.gl/proj4` any more. v5.0.0-beta.1 (2026-10-28) ships `@math.gl/geospatial/projection`, a native TypeScript engine with 37 algorithms including ob-tran, laea, stere, aeqd and lcc. The proj4js wrapper was removed.
- It reads WKT1, WKT2, ESRI WKT and PROJJSON (`modules/geospatial/src/crs/`).
- **Distortion:** `ProjectionAnalysis` gives Jacobian, scale factors, convergence and distortion within a declared domain (`docs/modules/geospatial/projection/api-reference/projection-analysis.md`). This is the only non-PROJ library I found with a public distortion API.
- It can resolve CRS definitions through proj-wasm (willcohen/clj-proj, npm 0.1.0, 2026-10-01): full PROJ including proj.db, compiled to wasm.
- There is no GPU or WGSL projection code in math.gl, only worker parallelism.
- proj4js 2.22.0 (2026-08-31) has 37 projections, including ob_tran, but its WKT2/PROJJSON support is limited.

### Capability table (forward and inverse)

| family | PROJ | proj4rs 0.3 | proj4js | math.gl v5 | terrio-crs | proj-core |
|---|---|---|---|---|---|---|
| tmerc/utm | Y | Y exact | Y | Y | Y | Y |
| laea | Y | Y exact | Y | Y | Y | Y |
| stere/ups | Y | Y exact | Y | Y | Y | Y (UPS-S 5.6 km off) |
| aeqd | Y | Y (wasm-safe now) | Y | Y | Y | listed, but refused in the fixture run |
| lcc / aea | Y | Y exact | Y | Y | Y | inverse wrong in the southern hemisphere |
| ortho, omerc, tpers | Y | Y (new) | Y | Y | Y | omerc only |
| ob_tran | Y | **N** | Y | Y | Y (10-28) | N |
| scale/distortion | `proj_factors` | N | N | Y | internal only | N |
| WKT2 / PROJJSON | Y | N | partial | Y | Y | only with an EPSG ID |
| wasm | proj-wasm (heavy) | Y | native JS | native JS | Y | Y |

Accuracy for proj4js, math.gl and terrio-crs comes from their own claims; I did not verify it. math.gl publishes its own scorecard and parity audit (`docs/modules/geospatial/projection/scorecard.md`).

### What this means for crsgrid
1. **Keep the transform maths in the host library.** Three to four browser engines now cover tmerc, laea, stere, aeqd and lcc forward and inverse. Writing your own projection code would duplicate them.
2. **The Rust proposer/linter can read WKT2/PROJJSON through terrio-crs.** It is the only pure-Rust crate that does this without an EPSG ID. The trade-off is a large crate with embedded registries. Alternatively, keep the linter on PROJJSON only, with no registry, and let hosts (pyproj, sf, gdalraster, proj-wasm) resolve codes to PROJJSON.
3. **Use proj4rs 0.3 as the lean wasm executor**, except for ob_tran, which matters for rotated-pole grids. Either contribute ob_tran to proj4rs (it is a small rotation; terrio's `rotation.rs` and math.gl's `ob-tran.ts` are references) or special-case it in the lattice core.
4. **Scale-factor checks need numerical Jacobians computed by crsgrid.** Finite differences of forward(x, y) work against any backend. Compare them with PROJ's `proj_factors` in tests, and with math.gl's `ProjectionAnalysis` in TypeScript.
5. **Make PROJJSON the spec's canonical CRS encoding.** Every layer except proj4rs reads it. Keep a PROJ string as an optional hint for proj4rs.
6. **Use the rust-proj-capability fixture harness as crsgrid's conformance oracle**, and re-run it against proj4rs 0.3 and terrio-crs, since neither has been measured yet.
