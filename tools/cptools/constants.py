"""URLs and other constants for the isin.db build pipeline."""

BSE_STARMF_SCHEME_MASTER_URL = "https://bsestarmf.in/RptSchemeMaster.aspx"
AMFI_NAV_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"

# NSE publishes the full list of listed equities as a CSV at a stable archive
# URL. We join it on ISIN to attach an NSE trading symbol to each equity in the
# ``isin`` table -- the captn3m0 feed has names/types but no exchange ticker,
# and price feeds (Yahoo ``.NS``, the NSE quote API) key on the symbol.
NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# captn3m0/india-isin-data switched from a CSV-in-main-branch format to a
# SQLite database published as a versioned GitHub release asset. We hit the
# GitHub API to discover the latest release, then stream-download the asset
# named ``isin.db``.
ISIN_GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/captn3m0/india-isin-data/releases/latest"
)
ISIN_ASSET_NAME = "isin.db"
