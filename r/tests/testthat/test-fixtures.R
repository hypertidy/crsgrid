root <- normalizePath(test_path("..", "..", ".."), mustWork = FALSE)
fixtures <- Sys.glob(file.path(root, "fixtures", "*.json"))

for (f in fixtures) {
  test_that(paste("fixtures:", basename(f)), {
    skip_if(length(fixtures) == 0, "fixtures not found (run from the crsgrid repository)")
    r <- check_fixtures(f, root)
    bad <- r[r$status == "fail", ]
    if (nrow(bad)) {
      msg <- paste(sprintf("case %d %s\n  want %s\n  got  %s", bad$case, bad$op, bad$want, bad$got), collapse = "\n")
      fail(paste0(nrow(bad), " failing case(s) in ", basename(f), ":\n", msg))
    }
    expect_true(any(r$status %in% c("pass", "known")))
  })
}
