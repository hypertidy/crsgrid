# Template grammar shared by IDs, CRS strings and instance keys:
#   {name}  {name/D}  {name:0N}  {name!sign=P,M}  {name!map=K1:V1,K2:V2}
# (D divides and must give an integer; :0N zero-pads to width N, sign
# counting toward the width as in C printf %0Nd; !sign writes P or M and
# then the absolute value).

field_rx <- "\\{(\\w+)(?:/(\\d+(?:\\.\\d+)?))?(?::0(\\d+))?(?:!sign=(\\w),(\\w))?(?:!map=([^}]*))?\\}"

tmpl_pieces <- function(template) {
  m <- gregexpr(field_rx, template, perl = TRUE)[[1]]
  if (m[1] == -1) return(list(literals = template, fields = list()))
  starts <- as.integer(m)
  ends <- starts + attr(m, "match.length") - 1L
  lits <- substring(template, c(1L, ends + 1L), c(starts - 1L, nchar(template)))
  caps <- regmatches(template, regexec(field_rx, template, perl = TRUE))
  all_caps <- lapply(regmatches(template, gregexpr(field_rx, template, perl = TRUE))[[1]],
                     function(f) regmatches(f, regexec(field_rx, f, perl = TRUE))[[1]])
  fields <- lapply(all_caps, function(x) {
    list(name = x[2], div = if (nzchar(x[3])) as.numeric(x[3]) else NA,
         width = if (nzchar(x[4])) as.integer(x[4]) else NA,
         pos = x[5], neg = x[6], map = if (nzchar(x[7])) x[7] else NA)
  })
  list(literals = lits, fields = fields)
}

num_chr <- function(v) {
  if (is.character(v)) return(v)
  ifelse(v == round(v), sprintf("%.0f", v), as.character(v))
}

fmt_field <- function(f, v) {
  if (!is.na(f$map)) {
    kv <- strsplit(strsplit(f$map, ",", fixed = TRUE)[[1]], ":", fixed = TRUE)
    lut <- stats::setNames(vapply(kv, function(x) if (length(x) > 1) x[2] else "", ""),
                           vapply(kv, `[`, "", 1))
    return(unname(lut[as.character(v)]))
  }
  if (!is.na(f$div)) {
    q <- v / f$div
    if (any(q != round(q))) stop_spec("not_aligned")
    v <- round(q)
  }
  letter <- ""
  if (nzchar(f$pos)) {
    letter <- ifelse(v >= 0, f$pos, f$neg)
    v <- abs(v)
  }
  body <- if (!is.na(f$width)) formatC(v, width = f$width, flag = "0", format = "d") else num_chr(v)
  paste0(letter, body)
}

#' Format a template over vectors of field values
#' @param template template string
#' @param env named list of field vectors (recycled)
#' @return character vector
#' @keywords internal
tmpl_format <- function(template, env) {
  p <- tmpl_pieces(template)
  if (!length(p$fields)) return(rep(template, max(1L, lengths(env))))
  parts <- lapply(p$fields, function(f) {
    if (is.null(env[[f$name]])) stop("template field {", f$name, "} has no value")
    fmt_field(f, env[[f$name]])
  })
  n <- max(lengths(parts))
  out <- rep(p$literals[1], n)
  for (i in seq_along(parts)) out <- paste0(out, parts[[i]], p$literals[i + 1])
  out
}

#' Parse strings against a template
#' @param numeric field names that are (signed) integers
#' @param letters field names that are single capital letters
#' @return data.frame with one column per field (NA rows where no match)
#' @keywords internal
tmpl_parse <- function(template, s, numeric = character(), letters = character()) {
  p <- tmpl_pieces(template)
  esc <- function(x) gsub("([][{}()*+?.\\\\^$|])", "\\\\\\1", x)
  rx <- esc(p$literals[1])
  for (i in seq_along(p$fields)) {
    f <- p$fields[[i]]
    grp <- if (nzchar(f$pos)) sprintf("(?<%s__s>[%s%s])(?<%s>\\d+)", f$name, f$pos, f$neg, f$name)
           else if (f$name %in% numeric) sprintf("(?<%s>-?\\d+)", f$name)
           else if (f$name %in% letters) sprintf("(?<%s>[A-Z])", f$name)
           else sprintf("(?<%s>[A-Za-z0-9-]+?)", f$name)
    rx <- paste0(rx, grp, esc(p$literals[i + 1]))
  }
  rx <- paste0("^", rx, "$")
  m <- regexpr(rx, s, perl = TRUE)
  st <- attr(m, "capture.start"); ln <- attr(m, "capture.length")
  cap <- function(nm) ifelse(m > 0, substring(s, st[, nm], st[, nm] + ln[, nm] - 1L), NA_character_)
  out <- list()
  for (f in p$fields) {
    v <- cap(f$name)
    if (f$name %in% numeric || nzchar(f$pos)) {
      v <- as.numeric(v)
      if (nzchar(f$pos)) v <- ifelse(cap(paste0(f$name, "__s")) == f$neg, -v, v)
      if (!is.na(f$div)) v <- v * f$div
    }
    out[[f$name]] <- v
  }
  out <- as.data.frame(out, stringsAsFactors = FALSE)
  out$.matched <- m > 0
  out
}

# Integer expressions for ids.fields / ids.recover: + - * // % and
# parentheses over field names, translated to R and evaluated with
# column vectors.
id_expr <- function(src, env) {
  r <- gsub("//", "%/%", src, fixed = TRUE)
  r <- gsub("(?<![%/])%(?![%/])", "%%", r, perl = TRUE)
  e <- str2lang(r)
  ok <- function(x) {
    if (is.call(x)) {
      fn <- as.character(x[[1]])
      if (!fn %in% c("+", "-", "*", "%/%", "%%", "(")) stop("unsupported expression: ", src)
      all(vapply(as.list(x)[-1], ok, TRUE))
    } else is.numeric(x) || is.name(x)
  }
  ok(e)
  eval(e, env, baseenv())
}

stop_spec <- function(code) {
  stop(structure(class = c("crsgrid_spec_error", "error", "condition"),
                 list(message = code, call = NULL)))
}
