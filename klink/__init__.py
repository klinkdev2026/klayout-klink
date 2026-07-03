"""
klink - Python client for the KLink RPC server running inside KLayout.

Install the server side as a KLayout salt package from the `klink_plugin`
directory. Then:

    from klink import KLinkClient

    with KLinkClient() as c:
        print(c.hello())
        print(c.layout_info())

The client speaks an NDJSON-over-TCP protocol. See the project README
and `meta.methods` (callable via `c.methods()`) for the full method
catalogue.
"""

from ._meta import PROTOCOL_VERSION, __version__
from .client import KLinkClient
from .errors import KLinkError, KLinkServerError, KLinkTransportError
from .handshake import evaluate_handshake

__all__ = [
    "KLinkClient",
    "KLinkError",
    "KLinkServerError",
    "KLinkTransportError",
    "evaluate_handshake",
    "__version__",
    "PROTOCOL_VERSION",
]
