import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with a single structured stream handler."""
    root = logging.getLogger()
    if root.handlers:
        # Already configured (e.g. re-imported in tests) - avoid duplicate handlers.
        root.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s"
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)
