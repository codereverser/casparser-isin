# Changelog

## 2021.6.1 - 2021-06-01
- support for using custom isin database via `CASPARSER_ISIN_DB` environment variable.
- updated isin.db
- packaging fixes

## 2021.5.1 - 2021-03-02
- DB updates
  - Essel mutual funds have been renamed to NAVI
  - Dividend options of funds renamed as IDCW

## 2021.4.1 - 2021-04-01
- updated isin.db
- updated dependent package versions

## 2021.3.1 - 2021-03-02
- Switch to calendar versioning
- Fix bugs with version comparison in cli update tool
- DB files are hosted in CDN for more frequent updates via CLI. [pypi releases will be limited to major changes in codebase]