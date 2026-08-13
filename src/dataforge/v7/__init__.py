"""DataForge V7 clean-state platform.

This package deliberately has no dependency on legacy platform modules.  V7
starts from a fresh database and object-store namespace.
"""

from .models import Base

__all__ = ["Base"]
