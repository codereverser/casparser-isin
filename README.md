# CASParser-ISIN

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![GitHub](https://img.shields.io/github/license/codereverser/casparser)](https://github.com/codereverser/casparser/blob/main/LICENSE)
![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/codereverser/casparser-isin/run-pytest.yml?branch=main)
[![codecov](https://codecov.io/gh/codereverser/casparser-isin/branch/main/graph/badge.svg?token=MQ8ZEVTG1B)](https://codecov.io/gh/codereverser/casparser-isin)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/casparser-isin)

ISIN Database for [casparser](https://github.com/codereverser/casparser).

## Installation
```bash
pip install -U casparser-isin
```

## Usage

### Mutual fund ISIN search

```python
from casparser_isin import MFISINDb
with MFISINDb() as db:
    scheme_data = db.isin_lookup("Axis Long Term Equity Fund - Growth",  # scheme name
                                 "KFINTECH", # RTA
                                 "128TSDGG", # Scheme RTA code
                                 )
print(scheme_data)
```
```
SchemeData(name="axis long term equity fund - direct growth",
           isin="INF846K01EW2",
           amfi_code="120503",
           score=100.0)
```

### Generic ISIN search

```python
from casparser_isin import ISINDb
with ISINDb() as db:
    isin_data = db.isin_lookup('INE009A01021')
```

```
ISINData(
    isin='INE009A01021',
    name='INFOSYS LIMITED EQ FV RS 5',
    issuer='INFOSYS LIMITED',
    type='EQUITY SHARES',
    status='ACTIVE'
)
```

### 31-Jan-2018 NAV search

The database also contains NAV values on 31-Jan-2018 for all funds, which can be used for
taxable LTCG computation for units purchased before the same date.

```
from casparser_isin import MFISINDb
with MFISINDb() as db:
    nav = db.nav_lookup("INF846K01EW2")
print(nav)
```
```
Decimal('44.8938')
```




## Notes

- casparser-isin is shipped with a local database which may get obsolete over time. The local
database can be updated via the cli tool

```shell
casparser-isin --update
```

- casparser-isin will try to use the file provided by `CASPARSER_ISIN_DB` environment variable; if present, and the file exists
