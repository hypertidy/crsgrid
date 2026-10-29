# Generate conformance fixtures for examples/aatgrid.json, using the
# aatgrid package itself as the oracle.
#
#   Rscript tools/fixtures-aatgrid.R [path/to/aatgrid]
#
# Writes fixtures/aatgrid.json. Every expected value comes from an
# aatgrid function; nothing here re-implements the grid.

args <- commandArgs(trailingOnly = TRUE)
aat_path <- if (length(args)) args[1] else "~/Git/aatgrid"
suppressMessages(pkgload::load_all(aat_path, quiet = TRUE, export_all = TRUE))

aat_commit <- system2("git", c("-C", aat_path, "rev-parse", "--short", "HEAD"), stdout = TRUE)
zone_id <- function(zone) sprintf("%02dS", zone)
zone_for_lon <- function(lon) floor((lon + 180) / 6) %% 60 + 1

cases <- list()
add <- function(op, input, expected, note = NULL) {
  cases[[length(cases) + 1]] <<- c(list(op = op, input = input, expected = expected),
                                   if (!is.null(note)) list(note = note))
}

## tile_at in CRS coordinates, including points exactly on tile edges and one
## quantum either side (half-open edge rule)
for (res in c(60, 20, 10, 30)) {
  ts <- tile_size(res)
  x_edge <- GRID_ORIGIN[["x"]] + 12 * ts
  y_edge <- GRID_ORIGIN[["y"]] + 100 * ts
  pts <- rbind(
    c(x_edge, y_edge, "both edges exactly"),
    c(x_edge - 1, y_edge - 1, "one quantum below both edges"),
    c(x_edge + 1, y_edge + 1, "one quantum above both edges"),
    c(x_edge + ts / 2, y_edge + ts / 2, "tile centre"),
    c(GRID_ORIGIN[["x"]], GRID_ORIGIN[["y"]], "grid origin")
  )
  for (i in seq_len(nrow(pts))) {
    x <- as.numeric(pts[i, 1]); y <- as.numeric(pts[i, 2])
    idx <- utm_to_tile_index(x, y, res)
    add("tile_at",
        list(instance = "43S", x = x, y = y, res = res),
        list(col = idx$col, row = idx$row, id = make_tile_id("43S", res, idx$col, idx$row)),
        note = paste0(pts[i, 3], if (res == 30) "; res 30 is not listed (open resolutions)"))
  }
}

## tile_extent: id -> extent and pixel dimension
for (id in c("43S_R0060_0006_0114", "55S_R0010_0040_0700", "43S_R0020_0003_0001")) {
  p <- parse_tile_id(id)
  e <- tile_index_to_extent(p$col, p$row, p$res)
  add("tile_extent", list(id = id),
      list(extent = unname(unlist(e)), dim = c(PIXELS_PER_TILE, PIXELS_PER_TILE),
           crs_authority = sprintf("EPSG:327%02d", p$zone_number)))
}

## tile_at_lonlat: lon/lat -> primary instance -> id (needs PROJ)
places <- list(
  list(name = "Heard Island", lon = 73.5, lat = -53.1),
  list(name = "Macquarie Island", lon = 158.87, lat = -54.62),
  list(name = "Davis", lon = 77.97, lat = -68.58),
  list(name = "Mawson", lon = 62.87, lat = -67.60),
  list(name = "Casey", lon = 110.53, lat = -66.28)
)
for (pl in places) {
  zone <- zone_for_lon(pl$lon)
  xy <- lonlat_to_utm(pl$lon, pl$lat, zone_id(zone))
  for (res in c(60, 10)) {
    idx <- utm_to_tile_index(xy$x, xy$y, res)
    add("tile_at_lonlat", list(lon = pl$lon, lat = pl$lat, res = res),
        list(instance = zone_id(zone), x = round(xy$x, 3), y = round(xy$y, 3),
             col = idx$col, row = idx$row,
             id = make_tile_id(zone_id(zone), res, idx$col, idx$row)),
        note = pl$name)
  }
}

## parent / children across resolution pairs
for (pr in list(c(60, 10), c(60, 20), c(20, 10))) {
  res_p <- pr[1]; res_c <- pr[2]
  kids <- get_child_tiles(6, 114, res_parent = res_p, res_child = res_c)
  kids <- kids[order(kids$row, kids$col), ]
  add("children", list(id = make_tile_id("43S", res_p, 6, 114), res = res_c),
      list(count = nrow(kids), ids = make_tile_id("43S", res_c, kids$col, kids$row)),
      note = sprintf("nesting factor %d; ids ordered by row then col", nesting_factor(res_p, res_c)))
  last <- kids[nrow(kids), ]
  par <- get_parent_tile(last$col, last$row, res_child = res_c, res_parent = res_p)
  add("parent", list(id = make_tile_id("43S", res_c, last$col, last$row), res = res_p),
      list(id = make_tile_id("43S", res_p, par$col, par$row)))
}
add("children", list(id = "43S_R0060_0006_0114", res = 25),
    list(error = "nesting"), note = "60 / 25 is not an integer")

## parse, including legacy level-name aliases
for (id in c("43S_R0060_0006_0114", "43S_L1_0006_0114", "43S_L2_0040_0700", "07S_R0010_0001_0002")) {
  p <- parse_tile_id(id)
  add("parse", list(id = id),
      list(instance = p$zone_id, zone = p$zone_number, res = p$res, col = p$col, row = p$row,
           canonical = make_tile_id(p$zone_id, p$res, p$col, p$row)))
}

out <- list(
  spec = "examples/aatgrid.json",
  generator = "tools/fixtures-aatgrid.R",
  oracle = list(
    package = sprintf("aatgrid %s (%s)", as.character(utils::packageVersion("aatgrid")), aat_commit),
    proj = gdalraster::proj_version()$name,
    terra = as.character(utils::packageVersion("terra"))
  ),
  cases = cases
)
jsonlite::write_json(out, "fixtures/aatgrid.json", auto_unbox = TRUE, pretty = TRUE, digits = NA)
message(length(cases), " cases written to fixtures/aatgrid.json")
