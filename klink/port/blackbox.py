"""Compatibility shim for klink.port.blackbox.

Moved to klink.domains.photonics.blackbox; this shim re-exports.
"""

from klink.domains.photonics.blackbox import *  # noqa: F401,F403
from klink.domains.photonics.blackbox import (  # noqa: F401
    harvest_instance_ports,
    mark_ports,
    stub_template,
)
