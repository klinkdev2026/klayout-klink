"""Domain-specific helpers layered on top of klink core semantics."""

import pkgutil

# The domain subpackages actually present (varies by build: e.g. a release may
# ship only a subset). Computed so `from klink.domains import *` never names an
# absent domain.
__all__ = sorted(m.name for m in pkgutil.iter_modules(__path__))
