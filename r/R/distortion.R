#' Maximum length scale (Tissot semi-major axis) at lon/lat points
#'
#' Computed by central finite differences of the instance's forward
#' projection, scaled by the ellipsoid's meridional (M) and prime vertical
#' (N cos phi) radii; no PROJ factors are used, so this is an independent
#' check on them.
#'
#' @param g spec
#' @param instance instance key
#' @param lon,lat points (degrees)
#' @param h finite-difference step (degrees)
#' @return data.frame with a and b (Tissot semi-axes) per point
#' @export
tissot <- function(g, instance, lon, lat, h = 1e-5) {
  g <- crsgrid_spec(g)
  ctx <- inst_ctx(g, instance)
  ell <- crs_ellipsoid(inst_crs(g, ctx))
  e2 <- ell$f * (2 - ell$f)
  phi <- lat * pi / 180
  w <- sqrt(1 - e2 * sin(phi)^2)
  M <- ell$a * (1 - e2) / w^3
  Npar <- ell$a / w * cos(phi)
  fw <- function(dl, dp) to_xy(g, ctx, lon + dl, lat + dp)
  pl <- fw(h, 0); ml <- fw(-h, 0); pp <- fw(0, h); mp <- fw(0, -h)
  d <- 2 * h * pi / 180
  xl <- (pl$x - ml$x) / d / Npar; yl <- (pl$y - ml$y) / d / Npar
  xp <- (pp$x - mp$x) / d / M;    yp <- (pp$y - mp$y) / d / M
  # singular values of [[xl, xp], [yl, yp]]
  s1 <- xl^2 + xp^2 + yl^2 + yp^2
  s2 <- sqrt((xl^2 + yl^2 - xp^2 - yp^2)^2 + 4 * (xl * xp + yl * yp)^2)
  data.frame(a = sqrt((s1 + s2) / 2), b = sqrt(pmax((s1 - s2) / 2, 0)))
}

crs_ellipsoid <- function(srs) {
  j <- jsonlite::fromJSON(gdalraster::srs_to_projjson(srs), simplifyVector = FALSE)
  base <- j$base_crs %||% j
  el <- base[["datum"]][["ellipsoid"]] %||% base[["datum_ensemble"]][["ellipsoid"]]
  a <- el$semi_major_axis
  f <- if (!is.null(el$inverse_flattening)) 1 / el$inverse_flattening else 1 - el$semi_minor_axis / a
  list(a = a, f = f)
}

#' Is an instance's CRS deprecated in the EPSG dataset?
#'
#' @inheritParams crsgrid_params
#' @export
crs_is_deprecated <- function(g, instance) {
  g <- crsgrid_spec(g)
  code <- inst_crs(g, inst_ctx(g, instance))
  auth <- sub(":.*", "", code); num <- sub(".*:", "", code)
  db <- .cache[[paste("db", auth)]] %||% (.cache[[paste("db", auth)]] <- gdalraster::srs_info_from_db(auth))
  isTRUE(db$is_deprecated[db$obj_code == num & db$obj_type %in% c("projected_crs", "geographic_2d_crs")][1])
}

#' Tiles intersecting a lon/lat bounding box, in every instance whose zone it touches
#'
#' For polygon-assigned families (each instance's zone clips the box) and
#' single-instance grids.
#' @inheritParams crsgrid_params
#' @param bbox c(west, south, east, north)
#' @export
tiles_covering_lonlat_bbox <- function(g, bbox, res = NULL, tiling = NULL) {
  g <- crsgrid_spec(g)
  box <- box_wkt(c(bbox[1], bbox[3], bbox[2], bbox[4]))
  ts <- tile_size(g, res, tiling)
  us <- lat_us(g)
  out <- character()
  for (k in inst_keys(g)) {
    ctx <- inst_ctx(g, k)
    part <- if (identical(g$family$assign$method, "polygons"))
      gdalraster::g_intersection(box, zone_polygon(tmpl_format(g$family$assign$source, ctx))) else box
    if (gdalraster::g_is_empty(part)) next
    p <- gdalraster::g_transform(gdalraster::g_segmentize(part, 0.1), "OGC:CRS84", inst_crs(g, ctx))
    env <- gdalraster::bbox_from_wkt(gdalraster::g_wk2wk(p)) * us   # xmin, ymin, xmax, ymax
    lo <- tile_index(g, env[1], env[2], ts); hi <- tile_index(g, env[3], env[4], ts)
    cr <- expand.grid(col = seq(min(lo$col, hi$col), max(lo$col, hi$col)),
                      row = seq(min(lo$row, hi$row), max(lo$row, hi$row)))
    ext <- cell_extent(g, cr$col, cr$row, ts)
    keep <- vapply(seq_len(nrow(cr)), function(j) {
      e <- ext[j, ]
      gdalraster::g_intersects(box_wkt(e / us), p) &&
        isTRUE(tryCatch(check_domain(g, ctx, e), crsgrid_spec_error = function(err) FALSE))
    }, TRUE)
    if (any(keep)) out <- c(out, tile_id(g, k, cr$col[keep], cr$row[keep], res, tiling))
  }
  sort(out)
}
