"""arvio: label eval items by hand, with the judge hidden until the session ends."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("arvio")
except PackageNotFoundError:
    __version__ = "unknown"
