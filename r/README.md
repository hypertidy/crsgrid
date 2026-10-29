# crsgrid (R)

Reference R runtime for [crsgrid](https://github.com/hypertidy/crsgrid) specs, on gdalraster (transforms, GEOS predicates) and jsonlite; arrow for Parquet tile lists and zone polygons. No terra or sf.

```r
# install.packages("remotes"); remotes::install_github("hypertidy/crsgrid", subdir = "r")
library(crsgrid)
aat <- crsgrid_spec("examples/aatgrid.json")
tile_at_lonlat(aat, lon = c(73.5, 158.87, 77.97), lat = c(-53.1, -54.62, -68.58), res = 60)
#>   instance        x       y col row                  id
#> 1      43S 399570.4 4115554   6  94 43S_R0060_0006_0094
#> 2      57S 491605.7 3947486   8  90 57S_R0060_0008_0090
#> 3      43S 621006.2 2389548  11  54 43S_R0060_0011_0054
tile_children(aat, "43S_R0060_0006_0094", res = 20)   # 3 x 3: 60 / 20

s2 <- crsgrid_spec("examples/s2mgrs.json")
tile_extent(s2, c("55GEN", "8CMC"), res = 10)$extent  # real Sentinel-2 footprints
#>        xmin   xmax    ymin    ymax
#> [1,] 499980 609780 5190220 5300020
#> [2,] 399960 509760 1690240 1800040

e7 <- crsgrid_spec("examples/equi7grid.json")
tile_at_lonlat(e7, lon = c(15.1, -178.5, 173), lat = c(45.3, 67.75, 53), tiling = "T6")$id
#> "EU_E048N012T6" "AS_E066N090T6" "NA_E036N084T6"   # across the antimeridian by zone polygon
tissot(e7, "AF", -29.1, 24)                            # finite differences, no PROJ factors
#>          a b
#> 1 1.144159 1
```

## Conformance

`check_fixtures()` runs the repository's JSON fixtures (cases with expected outputs from independent oracles). From the repository root:

```r
for (f in Sys.glob("fixtures/*.json")) print(table(check_fixtures(f, ".")$status))
```

| fixtures | pass | known difference | fail |
|---|---|---|---|
| aatgrid | 44 | 0 | 0 |
| cop30 | 211 | 0 | 0 |
| equi7grid | 15 | 1 | 0 |
| gibs-polar | 60 | 0 | 0 |
| nsidc-polar-stereo | 23 | 4 | 0 |
| rema-2m | 237 | 7 | 0 |
| s2mgrs | 438 | 30 | 0 |

Identical to the Python reader (`tools/check_fixtures.py`), case for case. `testthat::test_local("r")` runs them as tests; outside the repository the fixture tests skip.
