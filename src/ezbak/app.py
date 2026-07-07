"""Backward-compatible shim. Superseded by ezbak.core."""

from ezbak.core import EZBak, ezbak

# Historical name for the merged core.
EZBakApp = EZBak

__all__ = ["EZBak", "EZBakApp", "ezbak"]
