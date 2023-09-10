# Changelog

## 2023.9.10
- Fallback to old lookup when direct isin search fails
- update database

## 2023.9.3
- Lookup scheme via isin
- update database

## 2023.8.18
- fix issues with hdfc mutual fund lookups
- update database

## 2023.1.16
- DB updates

## 2021.7.21 - 2021-07-21
- better support for Franklin Templeton funds
- support new CAS pdf files after migration of funds from FTAMIL RTA to CAMS

## 2021.7.1 - 2021-07-01
- add scheme type (`EQUITY`/`DEBT`) to `SchemeData`
- add nav table for looking up scheme nav for 31-Jan-2018

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
