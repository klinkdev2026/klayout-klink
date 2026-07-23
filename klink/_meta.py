"""Single source of truth for klink's package + wire-protocol versions.

Kept dependency-free so any module (client, bridge, doctor) can import it
without a circular import. The KLayout plugin declares its own
``PROTOCOL_VERSION`` in ``klink_server/methods/meta_m.py``; these two must
agree for a client and a plugin to be compatible.
"""

from __future__ import annotations

__version__ = "0.1.5"

# Wire protocol the client speaks. Bump when the RPC contract changes in a
# way that an older plugin cannot serve. Compared against the plugin's
# ``hello().protocol`` during the handshake.
PROTOCOL_VERSION = 1
