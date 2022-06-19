from importlib.metadata import version, PackageNotFoundError

FALLBACK_VERSION = "2022.6.19"


def get_version():
    try:
        return version("casparser_isin")
    except PackageNotFoundError:
        return FALLBACK_VERSION  # local development version


__version__ = get_version()
