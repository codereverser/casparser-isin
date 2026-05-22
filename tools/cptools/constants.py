"""URLs and other constants for the isin.db build pipeline."""

BSE_STARMF_SCHEME_MASTER_URL = "https://bsestarmf.in/RptSchemeMaster.aspx"
AMFI_NAV_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"

# captn3m0/india-isin-data switched from a CSV-in-main-branch format to a
# SQLite database published as a versioned GitHub release asset. We hit the
# GitHub API to discover the latest release, then stream-download the asset
# named ``isin.db``.
ISIN_GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/captn3m0/india-isin-data/releases/latest"
)
ISIN_ASSET_NAME = "isin.db"
