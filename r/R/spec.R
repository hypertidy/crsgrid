#' Read a crsgrid spec
#'
#' @param x path or URL to a spec JSON file, or a parsed list
#' @return a `crsgrid_spec` (the parsed JSON with a class)
#' @export
#' @examples
#' \dontrun{
#' g <- crsgrid_spec("examples/aatgrid.json")
#' }
crsgrid_spec <- function(x) {
  if (inherits(x, "crsgrid_spec")) return(x)
  s <- if (is.character(x)) jsonlite::fromJSON(x, simplifyVector = FALSE) else x
  structure(s, class = "crsgrid_spec")
}

#' @export
print.crsgrid_spec <- function(x, ...) {
  cat(sprintf("<crsgrid_spec %s> %s\n", x$name, x$title %||% ""))
  invisible(x)
}

`%||%` <- function(a, b) if (is.null(a)) b else a

lat_ <- function(g) g$lattice
num_ <- function(x) as.numeric(unlist(x))

# -- instances -----------------------------------------------------------------

family_kind <- function(g) {
  if (!is.null(g$family$instances)) "table"
  else if (length(g$family$params)) "formula"
  else "single"
}

param_types <- function(g) vapply(g$family$params %||% list(), function(p) p$type, "")

#' Instance context: the instance key plus the fields usable in templates
#' @keywords internal
inst_ctx <- function(g, key) {
  switch(family_kind(g),
    single = list(instance = key %||% ""),
    table = {
      a <- g$family$instances[[key]]
      if (is.null(a)) stop_spec("unknown_instance")
      c(list(instance = key), a)
    },
    formula = {
      nums <- names(which(param_types(g) != "string"))
      v <- tmpl_parse(g$family$instance_key, key, numeric = nums)
      if (!v$.matched[1]) stop_spec("unknown_instance")
      c(list(instance = key), as.list(v[1, setdiff(names(v), ".matched"), drop = FALSE]))
    })
}

inst_from_params <- function(g, params) {
  key <- tmpl_format(g$family$instance_key, params)
  lapply(seq_along(key), function(i) c(list(instance = key[i]), lapply(params, function(p) p[(i - 1) %% length(p) + 1])))
}

inst_keys <- function(g) {
  switch(family_kind(g),
    single = "",
    table = unlist(g$family$assign$order %||% names(g$family$instances)),
    formula = NULL)
}

inst_crs <- function(g, ctx) {
  c <- g$family$crs
  if (!is.null(c$authority)) tmpl_format(c$authority, ctx) else tmpl_format(c$proj, ctx)
}

to_xy <- function(g, ctx, lon, lat) {
  xy <- gdalraster::transform_xy(cbind(lon, lat), srs_from = "OGC:CRS84", srs_to = inst_crs(g, ctx))
  list(x = xy[, 1], y = xy[, 2])
}

to_lonlat <- function(g, ctx, x, y) {
  ll <- gdalraster::transform_xy(cbind(x, y), srs_from = inst_crs(g, ctx), srs_to = "OGC:CRS84")
  list(lon = ll[, 1], lat = ll[, 2])
}

utm_standard <- function(lon, lat) {
  lon <- (lon + 180) %% 360 - 180
  zone <- pmin(floor((lon + 180) / 6) + 1, 60)
  zone[lat >= 56 & lat < 64 & lon >= 3 & lon < 12] <- 32
  sv <- lat >= 72 & lat < 84 & lon >= 0 & lon < 42
  zone[sv] <- ifelse(lon[sv] < 9, 31, ifelse(lon[sv] < 21, 33, ifelse(lon[sv] < 33, 35, 37)))
  list(zone = zone, hemisphere = ifelse(lat >= 0, "N", "S"))
}

#' Primary instance key for each lon/lat point
#' @keywords internal
assign_keys <- function(g, lon, lat) {
  a <- g$family$assign
  switch(a$method,
    single = rep(inst_keys(g)[1], length(lon)),
    lon_bands = {
      v <- floor(((lon - a$lon_start) %% 360) / a$width) + a$first
      tmpl_format(g$family$instance_key, stats::setNames(list(v), a$param))
    },
    utm_standard = {
      u <- utm_standard(lon, lat)
      tmpl_format(g$family$instance_key, stats::setNames(list(u$zone, u$hemisphere), c(a$zone, a$hemisphere)))
    },
    polygons = {
      out <- rep(NA_character_, length(lon))
      pts <- sprintf("POINT (%.12g %.12g)", lon, lat)
      for (k in inst_keys(g)) {
        poly <- zone_polygon(tmpl_format(a$source, inst_ctx(g, k)))
        todo <- is.na(out)
        if (!any(todo)) break
        hit <- vapply(pts[todo], function(p) any(gdalraster::g_contains(poly, p)), TRUE)
        out[which(todo)[hit]] <- k
      }
      out
    },
    stop("assign method not implemented: ", a$method))
}

# -- lattice -------------------------------------------------------------------

lat_us <- function(g) lat_(g)$unit_scale %||% 1
lat_origin <- function(g) num_(lat_(g)$origin)
row_up <- function(g) lat_(g)$index$row_direction == "up"

check_res <- function(g, res, size = NULL) {
  r <- lat_(g)$resolutions
  if (!res %in% num_(r$listed)) {
    if (!isTRUE(r$open)) stop_spec("res_not_listed")
    if (res / lat_(g)$quantum != round(res / lat_(g)$quantum)) stop_spec("not_on_quantum")
  }
  if (!is.null(size) && size %% res != 0) stop_spec("res_does_not_divide_tile")
  invisible(TRUE)
}

#' Tile size in lattice units for a resolution or tiling name
#' @keywords internal
tile_size <- function(g, res = NULL, tiling = NULL) {
  t <- lat_(g)$tiling   # [[ ]] not $: tile_size would partially match tile_sizes
  if (!is.null(t[["tile_px"]])) {
    check_res(g, res)
    return(t[["tile_px"]] * res)
  }
  if (!is.null(t[["tile_size"]])) return(t[["tile_size"]])
  if (is.null(t[["tile_sizes"]][[tiling %||% ""]])) stop_spec("unknown_tiling")
  t[["tile_sizes"]][[tiling]]
}

#' Tile index of points in lattice units
#' @keywords internal
tile_index <- function(g, x, y, ts) {
  q <- lat_(g)$quantum
  o <- lat_origin(g)
  col <- floor((x / q - o[1] / q) / (ts / q))
  dy <- y / q - o[2] / q
  row <- if (row_up(g)) floor(dy / (ts / q)) else floor(-dy / (ts / q))
  if (isFALSE(lat_(g)$index$negative) && any(col < 0 | row < 0)) stop_spec("negative_index")
  list(col = col, row = row)
}

#' Cell extent (xmin, xmax, ymin, ymax) in lattice units
#' @keywords internal
cell_extent <- function(g, col, row, ts) {
  o <- lat_origin(g)
  xmin <- o[1] + col * ts
  ymin <- if (row_up(g)) o[2] + row * ts else o[2] - (row + 1) * ts
  cbind(xmin = xmin, xmax = xmin + ts, ymin = ymin, ymax = ymin + ts)
}

pixel_size <- function(g, ext, res) {
  b <- lat_(g)$x_scale$bands
  fx <- 1
  if (!is.null(b)) {
    yc <- abs((ext[, "ymin"] + ext[, "ymax"]) / 2)
    fx <- vapply(yc, function(y) {
      for (band in b) if (band[[1]] <= y && y < band[[2]]) return(band[[3]])
      NA_real_
    }, 1)
  }
  cbind(px = res * fx, py = res)
}

#' Footprint of tiles: cell, padded and snapped, or padded by half a pixel when point registered
#' @keywords internal
footprint <- function(g, ctx, ext, res) {
  l <- lat_(g)
  if (identical(l$registration, "point")) {
    p <- pixel_size(g, ext, res)
    return(cbind(xmin = ext[, 1] - p[, 1] / 2, xmax = ext[, 2] + p[, 1] / 2,
                 ymin = ext[, 3] - p[, 2] / 2, ymax = ext[, 4] + p[, 2] / 2))
  }
  fp <- l$footprint
  if (is.null(fp)) return(ext)
  pad <- num_(fp$pad %||% list(0, 0, 0, 0))
  e <- cbind(ext[, 1] - pad[1], ext[, 2] + pad[2], ext[, 3] - pad[3], ext[, 4] + pad[4])
  if (!is.null(fp$snap)) {
    s <- fp$snap
    o <- vapply(s$origin, function(v) if (is.character(v)) as.numeric(tmpl_format(v, ctx)) else v, 1)
    w <- e[, 2] - e[, 1]; h <- e[, 4] - e[, 3]
    e[, 1] <- floor((e[, 1] - o[1]) / s$step) * s$step + o[1]
    e[, 2] <- e[, 1] + w
    if (s$corner == "top_left") {
      e[, 4] <- ceiling((e[, 4] - o[2]) / s$step) * s$step + o[2]
      e[, 3] <- e[, 4] - h
    } else {
      e[, 3] <- floor((e[, 3] - o[2]) / s$step) * s$step + o[2]
      e[, 4] <- e[, 3] + h
    }
  }
  colnames(e) <- c("xmin", "xmax", "ymin", "ymax")
  e
}

domain_extent <- function(g, ctx) {
  vapply(g$domain$extent, function(v) if (is.character(v)) as.numeric(tmpl_format(v, ctx)) else v, 1)
}

# -- MGRS codec (published rules; GeographicLib is the oracle) ------------------

mgrs_letters24 <- strsplit("ABCDEFGHJKLMNPQRSTUVWXYZ", "")[[1]]
mgrs_rows20 <- strsplit("ABCDEFGHJKLMNPQRSTUV", "")[[1]]
mgrs_bands <- strsplit("CDEFGHJKLMNPQRSTUVWX", "")[[1]]

mgrs_colset <- function(zone) {
  i <- zone %% 3
  lapply(i, function(k) mgrs_letters24[switch(as.character(k), "1" = 1:8, "2" = 9:16, "0" = 17:24)])
}

band_of <- function(lat) mgrs_bands[pmin(pmax(floor((lat + 80) / 8), 0), 19) + 1]

band_range <- function(band) {
  i <- match(band, mgrs_bands) - 1
  cbind(-80 + 8 * i, ifelse(band == "X", 84, -72 + 8 * i))
}

mgrs_encode <- function(zone, col, row) {
  e <- vapply(seq_along(zone), function(i) mgrs_colset(zone[i])[[1]][col[i]], "")
  n <- mgrs_rows20[(row + ifelse(zone %% 2 == 0, 5, 0)) %% 20 + 1]
  list(e100k = e, n100k = n)
}

mgrs_decode <- function(g, zone, band, e, n) {
  hemi <- ifelse(band >= "N", "N", "S")
  res <- lapply(seq_along(zone), function(i) {
    col <- match(e[i], mgrs_colset(zone[i])[[1]])
    r <- (match(n[i], mgrs_rows20) - 1 - ifelse(zone[i] %% 2 == 0, 5, 0)) %% 20
    cand <- r + 20 * (0:4)
    ctx <- inst_from_params(g, list(zone = zone[i], hemisphere = hemi[i]))[[1]]
    ll <- to_lonlat(g, ctx, rep((col + 0.5) * 1e5, 5), (cand + 0.5) * 1e5)
    br <- band_range(band[i])
    miss <- ifelse(ll$lat >= br[1] & ll$lat <= br[2], 0, pmin(abs(ll$lat - br[1]), abs(ll$lat - br[2])))
    c(col = col, row = cand[which.min(miss)])
  })
  list(col = vapply(res, `[`, 1, "col"), row = vapply(res, `[`, 1, "row"), hemisphere = hemi)
}

# -- domains -------------------------------------------------------------------

cache_get <- function(url) {
  dir <- tools::R_user_dir("crsgrid", "cache")
  dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  f <- file.path(dir, gsub("[^A-Za-z0-9.]+", "_", url))
  if (!file.exists(f)) utils::download.file(url, f, mode = "wb", quiet = TRUE)
  f
}

.cache <- new.env(parent = emptyenv())

#' IDs of a list domain (Parquet column or GDAL VRT source names)
#'
#' @inheritParams crsgrid_params
#' @export
domain_ids <- function(g) {
  g <- crsgrid_spec(g)
  dl <- g$domain$list
  if (is.null(dl)) return(NULL)
  key <- paste("ids", dl$source)
  if (!is.null(.cache[[key]])) return(.cache[[key]])
  f <- cache_get(dl$source)
  ids <- if (identical(dl$format, "gdal_vrt")) {
    src <- regmatches(readLines(f, warn = FALSE), regexpr("<SourceFilename[^>]*>[^<]*", readLines(f, warn = FALSE)))
    src <- sub("<SourceFilename[^>]*>", "", src)
    m <- regexpr(dl$pattern, src, perl = TRUE)
    st <- attr(m, "capture.start")[, "id"]; ln <- attr(m, "capture.length")[, "id"]
    unique(substring(src, st, st + ln - 1)[m > 0])
  } else {
    as.character(arrow::read_parquet(f, col_select = dl$column)[[1]])
  }
  .cache[[key]] <- ids
  ids
}

zone_polygon <- function(url) {
  key <- paste("poly", url)
  if (!is.null(.cache[[key]])) return(.cache[[key]])
  tb <- arrow::read_parquet(cache_get(url), as_data_frame = FALSE)
  wkb <- as.vector(tb$geometry)
  wkb <- lapply(wkb, as.raw)
  u <- if (length(wkb) > 1) gdalraster::g_unary_union(gdalraster::g_build_collection(wkb)) else wkb[[1]]
  .cache[[key]] <- u
  u
}

zone_xy <- function(g, ctx) {
  key <- paste("zonexy", g$name, ctx$instance)
  if (!is.null(.cache[[key]])) return(.cache[[key]])
  p <- zone_polygon(tmpl_format(g$domain$polygons, ctx))
  p <- gdalraster::g_transform(gdalraster::g_segmentize(p, 0.1), "OGC:CRS84", inst_crs(g, ctx))
  .cache[[key]] <- p
  p
}

box_wkt <- function(e) sprintf("POLYGON ((%1$.10g %3$.10g, %2$.10g %3$.10g, %2$.10g %4$.10g, %1$.10g %4$.10g, %1$.10g %3$.10g))",
                               e[1], e[2], e[3], e[4])

#' Check one tile against the spec's domain; signals crsgrid_spec_error "outside_domain"
#' @keywords internal
check_domain <- function(g, ctx, ext, id = NULL) {
  d <- g$domain
  if (!is.null(d$list) && !is.null(id) && !id %in% domain_ids(g)) stop_spec("outside_domain")
  if (!is.null(d$extent) && !is.null(lat_(g)$tiling)) {
    de <- domain_extent(g, ctx)
    if (ext[1] >= de[2] || ext[2] <= de[1] || ext[3] >= de[4] || ext[4] <= de[3]) stop_spec("outside_domain")
  }
  if (!is.null(d$min_corner)) {
    m <- vapply(d$min_corner, function(v) if (is.character(v)) as.numeric(tmpl_format(v, ctx)) else v, 1)
    if (ext[1] < m[1] || ext[3] < m[2]) stop_spec("outside_domain")
  }
  if (!is.null(d$polygons) && !gdalraster::g_intersects(box_wkt(ext), zone_xy(g, ctx))) stop_spec("outside_domain")
  invisible(TRUE)
}
