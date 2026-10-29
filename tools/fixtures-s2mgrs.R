# Generate conformance fixtures for examples/s2mgrs.json from two
# independent oracles:
#
#   - tile footprints: real Sentinel-2 L2A items (proj:transform,
#     proj:shape, proj:epsg) in the starc store, one per distinct MGRS
#     code, i.e. what ESA actually produced
#   - lon/lat -> MGRS 100 km square, and square -> centre: GeographicLib
#     (hypertidy/geographiclib)
#
#   Rscript tools/fixtures-s2mgrs.R [~/starc-store] [valid_mgrs_sentinel2.parquet]
#
# Writes fixtures/s2mgrs.json.

suppressMessages(library(dplyr))
args <- commandArgs(trailingOnly = TRUE)
store <- if (length(args) > 0) args[1] else "~/starc-store"
valid_path <- if (length(args) > 1) args[2] else "~/Git/starc/inst/extdata/valid_mgrs_sentinel2.parquet"

## ---- footprints from the archive
a <- arrow::open_dataset(file.path(store, "acquisitions")) |>
  select(acquisition_id, tile) |> collect() |> distinct()
p <- arrow::open_dataset(file.path(store, "products")) |>
  filter(collection == "sentinel-2-c1-l2a") |>
  select(product_id, acquisition_id) |> collect() |>
  distinct(product_id, .keep_all = TRUE)
one <- inner_join(p, a, by = "acquisition_id") |>
  filter(!is.na(tile)) |>
  group_by(tile) |> slice(1) |> ungroup() |> arrange(tile)

read_raw <- function(product_id) {
  f <- file.path(store, "raw", "sentinel-2-c1-l2a", sprintf("%s.json.gz", product_id))
  if (!file.exists(f)) return(NULL)
  jsonlite::fromJSON(gzfile(f), simplifyVector = FALSE)
}

## extent as c(xmin, xmax, ymin, ymax) and epsg, per band resolution
footprint <- function(item, band) {
  b <- item$assets[[band]]
  if (is.null(b)) return(NULL)
  tr <- unlist(b[["proj:transform"]]); sh <- unlist(b[["proj:shape"]])
  epsg <- b[["proj:epsg"]] %||% item$properties[["proj:epsg"]]
  if (is.null(epsg) && !is.null(b[["proj:code"]])) epsg <- as.integer(sub("EPSG:", "", b[["proj:code"]]))
  if (is.null(epsg) && !is.null(item$properties[["proj:code"]])) epsg <- as.integer(sub("EPSG:", "", item$properties[["proj:code"]]))
  if (is.null(tr) || is.null(sh)) return(NULL)
  list(res = tr[1], epsg = epsg,
       extent = c(tr[3], tr[3] + tr[1] * sh[2], tr[6] + tr[5] * sh[1], tr[6]),
       dim = c(sh[2], sh[1]))
}
`%||%` <- function(a, b) if (is.null(a)) b else a

cases <- list()
add <- function(op, input, expected, note = NULL) {
  cases[[length(cases) + 1]] <<- c(list(op = op, input = input, expected = expected),
                                   if (!is.null(note)) list(note = note))
}

valid <- arrow::read_parquet(path.expand(valid_path))$mgrs0
bands <- c(`10` = "red", `20` = "rededge1", `60` = "coastal")
n_items <- 0
for (i in seq_len(nrow(one))) {
  it <- read_raw(one$product_id[i])
  if (is.null(it)) next
  n_items <- n_items + 1
  for (res in names(bands)) {
    fp <- footprint(it, bands[[res]])
    if (is.null(fp)) next
    expected <- list(extent = fp$extent, dim = fp$dim, crs_authority = sprintf("EPSG:%d", fp$epsg))
    note <- sprintf("archive: %s (%s band)", one$product_id[i], bands[[res]])
    add("tile_extent", list(id = one$tile[i], res = as.numeric(res)), expected, note = note)
    if (!one$tile[i] %in% valid) {
      cases[[length(cases)]]$known_difference <- list(
        value = list(error = "outside_domain"),
        reason = "produced by ESA (in the archive) but missing from the published valid list the spec uses as its domain")
      add("tile_footprint", list(id = one$tile[i], res = as.numeric(res)), expected,
          note = paste(note, "- footprint only, without the domain check"))
    }
  }
}

## ---- GeographicLib: lon/lat -> 100 km square, across zones, bands, hemispheres
library(geographiclib)
pts <- rbind(
  c(73.5, -53.1),      # Heard Island
  c(158.87, -54.62),   # Macquarie Island
  c(77.97, -68.58),    # Davis
  c(147.33, -42.88),   # Hobart
  c(2.35, 48.86),      # Paris
  c(-0.1, 51.5),       # London, just west of the 0 meridian (zone 30)
  c(5.5, 60.0),        # Norway exception: 32V widened west to 3E
  c(20.0, 75.0),       # Svalbard exception: zone 33X / 35X only
  c(-77.04, 38.90),    # Washington DC
  c(-70.6, -33.4),     # Santiago
  c(179.9, -16.5),     # Fiji, just west of the antimeridian
  c(-179.9, -16.5),    # Fiji, just east of it
  c(10.0, 0.0001),     # equator, north side
  c(10.0, -0.0001),    # equator, south side
  c(30.0, 83.9),       # band X northern limit
  c(-60.0, -79.9)      # band C southern limit
)
fw <- mgrs_fwd(pts, precision = 0)
for (k in seq_len(nrow(pts))) {
  add("tile_at_lonlat", list(lon = pts[k, 1], lat = pts[k, 2]),
      list(id = sub("^0", "", fw[k])),
      note = sprintf("GeographicLib mgrs_fwd(precision = 0) gives %s; Sentinel-2 writes the zone unpadded", fw[k]))
}
codes <- unique(c(fw, "43DDE", "55GEN", "31UDQ", "32VKM", "33XVG", "01CDN", "60CWT"))
rv <- mgrs_rev(codes)
for (k in seq_along(codes)) {
  add("square_centre", list(id = codes[k]),
      list(x = rv$x[k], y = rv$y[k], crs_authority = rv$crs[k]),
      note = "GeographicLib mgrs_rev: centre of the 100 km square, in its zone CRS")
}

## ---- domain: the published valid list
add("domain_size", structure(list(), names = character(0)), list(count = length(valid)),
    note = "rows in starc's valid_mgrs_sentinel2.parquet (from ESA's tiling grid KML)")
for (code in c("43DDE", "01CDN", "99ZZZ", "43DZZ")) {
  add("in_domain", list(id = code), list(value = code %in% valid))
}

out <- list(
  spec = "examples/s2mgrs.json",
  generator = "tools/fixtures-s2mgrs.R",
  oracle = list(
    package = sprintf("Sentinel-2 L2A archive items (%d codes, starc store) + geographiclib %s",
                      n_items, as.character(utils::packageVersion("geographiclib"))),
    proj = gdalraster::proj_version()$name
  ),
  cases = cases
)
jsonlite::write_json(out, "fixtures/s2mgrs.json", auto_unbox = TRUE, pretty = TRUE, digits = NA)
message(length(cases), " cases (", n_items, " archive codes) written to fixtures/s2mgrs.json")
