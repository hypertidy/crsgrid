#' Run crsgrid conformance fixtures
#'
#' Reads a fixture file (spec + cases with expected outputs, from an
#' independent oracle) and runs every case through this runtime.
#'
#' @param path fixture JSON file
#' @param root repository root, against which the fixture's `spec` path is resolved
#' @return data.frame: case, op, status (pass, known, fail, skip), want, got
#' @export
check_fixtures <- function(path, root = dirname(dirname(path))) {
  fx <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  g <- crsgrid_spec(file.path(root, fx$spec))
  rows <- lapply(seq_along(fx$cases) - 1L, function(n) {
    case <- fx$cases[[n + 1]]
    got <- tryCatch(run_case(g, case),
                    crsgrid_spec_error = function(e) list(error = conditionMessage(e)),
                    crsgrid_unsupported = function(e) NULL)
    tol <- case$tolerance %||% list()
    status <- if (is.null(got)) "skip"
              else if (fx_match(got, case$expected, tol)) "pass"
              else if (!is.null(case$known_difference) && fx_match(got, case$known_difference$value, tol)) "known"
              else "fail"
    data.frame(case = n, op = case$op, status = status,
               want = as.character(jsonlite::toJSON(case$expected, auto_unbox = TRUE, digits = NA)),
               got = if (is.null(got)) "" else as.character(jsonlite::toJSON(got, auto_unbox = TRUE, digits = NA)))
  })
  do.call(rbind, rows)
}

unsupported <- function(op) {
  stop(structure(class = c("crsgrid_unsupported", "error", "condition"),
                 list(message = paste("not implemented in R:", op), call = NULL)))
}

fx_match <- function(got, want, tol) {
  if (!setequal(names(got), names(want))) return(FALSE)
  for (k in names(want)) {
    w <- unlist(want[[k]]); v <- unlist(got[[k]])
    if (!is.null(tol[[k]])) {
      if (length(v) != length(w) || any(abs(as.numeric(v) - as.numeric(w)) > tol[[k]])) return(FALSE)
    } else if (k == "ids") {
      if (!setequal(v, w) || length(v) != length(w)) return(FALSE)
    } else if (is.numeric(w) || is.logical(w)) {
      if (length(v) != length(w) || !isTRUE(all(v == w))) return(FALSE)
    } else if (!identical(as.character(v), as.character(w))) return(FALSE)
  }
  TRUE
}

level_args <- function(i) list(res = i$res, tiling = i$tiling)

run_case <- function(g, case) {
  i <- case$input
  if (!is.list(i) || is.null(names(i))) i <- list()
  want <- case$expected
  lv <- level_args(i)
  switch(case$op,
    tile_at = {
      r <- tile_at(g, i$x, i$y, i$instance, lv$res, lv$tiling)
      list(col = r$col, row = r$row, id = r$id)
    },
    tile_at_lonlat = {
      r <- tile_at_lonlat(g, i$lon, i$lat, lv$res, lv$tiling)
      if (is.null(want$x)) list(id = r$id)
      else list(instance = r$instance, x = round(r$x, 3), y = round(r$y, 3), col = r$col, row = r$row, id = r$id)
    },
    lonlat_to_xy = {
      k <- assign_keys(g, i$lon, i$lat)
      xy <- to_xy(g, inst_ctx(g, k), i$lon, i$lat)
      list(instance = k, x = xy$x, y = xy$y)
    },
    xy_to_lonlat = {
      ll <- to_lonlat(g, inst_ctx(g, i$instance), i$x, i$y)
      list(lon = ll$lon, lat = ll$lat)
    },
    tile_extent = , tile_footprint = {
      r <- tile_extent(g, i$id, i$res, domain = case$op == "tile_extent")
      out <- list(extent = unname(r$extent[1, ]), dim = unname(r$dim[1, ]))
      if (!is.null(want$crs_authority)) out$crs_authority <- inst_crs(g, parse_one(g, i$id, FALSE)$ctx)
      out
    },
    children = {
      ids <- tile_children(g, i$id, lv$res, lv$tiling)
      list(count = length(ids), ids = ids)
    },
    parent = list(id = tile_parent(g, i$id, lv$res, lv$tiling)),
    parse = {
      t <- parse_one(g, i$id)
      if (isTRUE(t$untiled)) return(list(instance = t$instance, res = t$res))
      ctx <- t$ctx[setdiff(names(t$ctx), "instance")]
      c(list(instance = t$instance), ctx, list(res = t$res, col = t$col, row = t$row, canonical = t$id))
    },
    square_centre = {
      t <- parse_one(g, i$id, FALSE)
      e <- cell_extent(g, t$col, t$row, t$ts)[1, ]
      list(x = (e[[1]] + e[[2]]) / 2, y = (e[[3]] + e[[4]]) / 2, crs_authority = inst_crs(g, t$ctx))
    },
    domain_size = list(count = length(domain_ids(g))),
    in_domain = list(value = i$id %in% domain_ids(g)),
    corner_lonlat = {
      ctx <- inst_ctx(g, i$instance)
      e <- domain_extent(g, ctx)
      xy <- switch(i$corner, upper_left = c(e[1], e[4]), upper_right = c(e[2], e[4]),
                   lower_right = c(e[2], e[3]), lower_left = c(e[1], e[3]))
      ll <- to_lonlat(g, ctx, xy[1], xy[2])
      list(lon = ll$lon %% 360, lat = ll$lat)
    },
    scale_factor = {
      k <- assign_keys(g, i$lon, i$lat)
      list(instance = k, k = tissot(g, k, i$lon, i$lat)$a)
    },
    crs_deprecated = list(value = crs_is_deprecated(g, i$instance)),
    tiles_covering_lonlat_bbox = {
      ids <- tiles_covering_lonlat_bbox(g, unlist(i$bbox), i$res, i$tiling)
      if (!is.null(want$count)) list(count = length(ids)) else list(ids = ids)
    },
    unsupported(case$op))
}
