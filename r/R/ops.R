id_numeric <- function(g) {
  c("res", "col", "row", "xmin", "ymin", names(which(param_types(g) != "string")), names(g$ids$fields))
}

#' Tile IDs from tile indices
#'
#' @param g a spec (see [crsgrid_spec()])
#' @param instance instance key(s)
#' @param col,row tile indices
#' @param res resolution (for `tile_px` grids, and IDs that encode it)
#' @param tiling tiling name (for `tile_sizes` grids)
#' @param lat latitude, for MGRS IDs (band of the point); default the cell centre
#' @param band MGRS band letter(s), overriding `lat`
#' @return character vector of IDs
#' @export
tile_id <- function(g, instance, col, row, res = NULL, tiling = NULL, lat = NULL, band = NULL) {
  g <- crsgrid_spec(g)
  n <- max(length(instance), length(col), length(row))
  instance <- rep_len(instance, n); col <- rep_len(col, n); row <- rep_len(row, n)
  ts <- tile_size(g, res, tiling)
  ext <- cell_extent(g, col, row, ts)
  vapply(seq_len(n), function(i) {
    ctx <- inst_ctx(g, instance[i])
    env <- c(ctx, list(res = res %||% NA, tiling = tiling %||% NA, col = col[i], row = row[i],
                       xmin = ext[i, "xmin"], ymin = ext[i, "ymin"],
                       name = names(Filter(function(v) identical(v, res), lat_(g)$resolutions$names %||% list()))[1] %||% ""))
    if (identical(g$ids$codec, "mgrs")) {
      b <- if (!is.null(band)) rep_len(band, n)[i] else band_of(
        if (!is.null(lat)) rep_len(lat, n)[i] else to_lonlat(g, ctx, ext[i, "xmin"] + ts / 2, ext[i, "ymin"] + ts / 2)$lat)
      env <- c(env, list(band = b), mgrs_encode(as.numeric(ctx$zone), col[i], row[i]))
    }
    for (k in names(g$ids$fields)) env[[k]] <- id_expr(g$ids$fields[[k]], env)
    tmpl_format(g$ids$template, env)
  }, "")
}

#' Tiles containing points given in an instance's CRS
#'
#' @inheritParams crsgrid_params
#' @param x,y coordinates in the instance CRS
#' @export
tile_at <- function(g, x, y, instance, res = NULL, tiling = NULL) {
  g <- crsgrid_spec(g)
  us <- lat_us(g)
  ts <- tile_size(g, res, tiling)
  ix <- tile_index(g, x * us, y * us, ts)
  data.frame(col = ix$col, row = ix$row, id = tile_id(g, instance, ix$col, ix$row, res, tiling))
}

#' Tiles containing lon/lat points, in each point's primary instance
#'
#' @inheritParams crsgrid_params
#' @export
tile_at_lonlat <- function(g, lon, lat, res = NULL, tiling = NULL) {
  g <- crsgrid_spec(g)
  keys <- assign_keys(g, lon, lat)
  us <- lat_us(g)
  ts <- tile_size(g, res, tiling)
  out <- data.frame(instance = keys, x = NA_real_, y = NA_real_, col = NA_real_, row = NA_real_, id = NA_character_)
  for (k in unique(keys)) {
    i <- which(keys == k)
    xy <- to_xy(g, inst_ctx(g, k), lon[i], lat[i])
    ix <- tile_index(g, xy$x * us, xy$y * us, ts)
    out[i, c("x", "y", "col", "row")] <- cbind(xy$x, xy$y, ix$col, ix$row)
    out$id[i] <- tile_id(g, k, ix$col, ix$row, res, tiling, lat = lat[i])
  }
  out
}

#' Parse tile IDs
#'
#' @inheritParams crsgrid_params
#'
#' @param domain check that each tile is in the spec's domain
#' @return data.frame: instance, res, tiling, col, row, band, canonical id
#' @export
parse_tile_id <- function(g, id, domain = TRUE) {
  g <- crsgrid_spec(g)
  do.call(rbind, lapply(id, function(s) as.data.frame(parse_one(g, s, domain)[c("instance", "res", "tiling", "col", "row", "band", "id")])))
}

parse_one <- function(g, s, domain = TRUE) {
  letters <- if (identical(g$ids$codec, "mgrs")) c("band", "e100k", "n100k") else character()
  for (t in c(g$ids$template, unlist(g$ids$parse_aliases))) {
    v <- tmpl_parse(t, s, numeric = id_numeric(g), letters = letters)
    if (!v$.matched[1]) next
    v <- as.list(v[1, , drop = FALSE])
    if (is.null(v$res) && !is.null(v$name)) v$res <- lat_(g)$resolutions$names[[v$name]] %||% NULL
    if (identical(g$ids$codec, "mgrs")) {
      d <- mgrs_decode(g, v$zone, v$band, v$e100k, v$n100k)
      ctx <- inst_from_params(g, list(zone = v$zone, hemisphere = d$hemisphere))[[1]]
      v$col <- d$col; v$row <- d$row
    } else {
      ctx <- inst_ctx(g, v$instance %||% "")
    }
    if (is.null(lat_(g)$tiling)) {   # untiled: the ID names one raster per instance and resolution
      check_res(g, v$res)
      e <- domain_extent(g, ctx)
      if ((e[2] - e[1]) %% v$res || (e[4] - e[3]) %% v$res) stop_spec("not_aligned")
      return(list(ctx = ctx, instance = ctx$instance, res = v$res, tiling = NA, col = NA, row = NA,
                  band = NA, id = s, untiled = TRUE, extent = e))
    }
    for (k in names(g$ids$recover)) v[[k]] <- id_expr(g$ids$recover[[k]], v)
    ts <- tile_size(g, v$res, v$tiling)
    if (is.null(v$col)) {
      o <- lat_origin(g)
      dx <- v$xmin - o[1]; dy <- v$ymin - o[2]
      if (dx %% ts || dy %% ts) stop_spec("not_aligned")
      v$col <- dx / ts
      v$row <- if (row_up(g)) dy / ts else -dy / ts - 1
    }
    canonical <- if (identical(g$ids$codec, "mgrs")) tmpl_format(g$ids$template, v)
                 else tile_id(g, ctx$instance, v$col, v$row, v$res, v$tiling)
    tile <- list(ctx = ctx, instance = ctx$instance, res = v$res %||% NA, tiling = v$tiling %||% NA,
                 col = v$col, row = v$row, band = v$band %||% NA, id = canonical, ts = ts, untiled = FALSE)
    if (domain) check_domain(g, ctx, cell_extent(g, v$col, v$row, ts)[1, ], canonical)
    return(tile)
  }
  stop_spec("unparseable")
}

#' Extent and pixel dimension of tiles
#'
#' @inheritParams crsgrid_params
#'
#' @param res resolution, when the ID does not carry one
#' @param domain check the domain
#' @return list with `extent` (matrix xmin, xmax, ymin, ymax in CRS units) and `dim` (matrix ncol, nrow)
#' @export
tile_extent <- function(g, id, res = NULL, domain = TRUE) {
  g <- crsgrid_spec(g)
  parts <- lapply(id, function(s) {
    t <- parse_one(g, s, domain)
    r <- if (!is.na(t$res)) t$res else res %||% num_(lat_(g)$resolutions$listed)[1]
    if (isTRUE(t$untiled)) {
      e <- t$extent
      return(list(e = e, d = c((e[2] - e[1]) / r, (e[4] - e[3]) / r)))
    }
    cell <- cell_extent(g, t$col, t$row, t$ts)
    e <- footprint(g, t$ctx, cell, r)[1, ]
    if (identical(lat_(g)$registration, "point")) {
      p <- pixel_size(g, cell, r)
      d <- c(round(t$ts / p[1, 1]) + 1, round(t$ts / p[1, 2]) + 1)
    } else if (!is.null(lat_(g)$footprint)) {
      check_res(g, r, e[2] - e[1])
      d <- c((e[2] - e[1]) / r, (e[4] - e[3]) / r)
    } else {
      check_res(g, r, t$ts)
      d <- c(t$ts / r, t$ts / r)
    }
    list(e = e / lat_us(g), d = d)
  })
  ext <- do.call(rbind, lapply(parts, `[[`, "e"))
  colnames(ext) <- c("xmin", "xmax", "ymin", "ymax")
  dim <- do.call(rbind, lapply(parts, function(p) unname(p$d)))
  colnames(dim) <- c("ncol", "nrow")
  list(extent = ext, dim = dim)
}

nest_factor <- function(ts_parent, ts_child) {
  f <- ts_parent / ts_child
  if (f != round(f) || f < 1) stop_spec("nesting")
  round(f)
}

#' Child tiles at a finer resolution or tiling
#'
#' @inheritParams crsgrid_params
#' @export
tile_children <- function(g, id, res = NULL, tiling = NULL) {
  g <- crsgrid_spec(g)
  t <- parse_one(g, id)
  f <- nest_factor(t$ts, tile_size(g, res, tiling))
  rc <- expand.grid(c = 0:(f - 1), r = 0:(f - 1))
  tile_id(g, t$instance, t$col * f + rc$c, t$row * f + rc$r, res, tiling)
}

#' Parent tile at a coarser resolution or tiling
#'
#' @inheritParams crsgrid_params
#' @export
tile_parent <- function(g, id, res = NULL, tiling = NULL) {
  g <- crsgrid_spec(g)
  t <- parse_one(g, id)
  f <- nest_factor(tile_size(g, res, tiling), t$ts)
  tile_id(g, t$instance, t$col %/% f, t$row %/% f, res, tiling)
}

#' Is each ID a tile in the spec's domain?
#'
#' @inheritParams crsgrid_params
#' @export
in_domain <- function(g, id) {
  g <- crsgrid_spec(g)
  vapply(id, function(s) tryCatch({ parse_one(g, s, TRUE); TRUE }, crsgrid_spec_error = function(e) FALSE), TRUE,
         USE.NAMES = FALSE)
}
