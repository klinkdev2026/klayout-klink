# custom_devices/

Your custom-device / circuit / photonic / nanodevice build scripts live here. The agent writes them during onboarding; you can
edit them too.

A build script:

1. imports `PROCESS` (and any device library) from `../pdk.py`,
2. calls the relevant klink API **explicitly** with that process,
3. draws into a clearly named cell (never the user's working cell),
4. is verified with geometry/LVS queries — not screenshots.

Minimal shape:

```python
from pdk import PROCESS          # your process — the only home for process data
from klink import KLinkClient

with KLinkClient(port=8765).connect() as c:   # your KLayout RPC port (8765 = default)
    # ... call klink APIs, passing PROCESS explicitly ...
    ...
```

See `../recipes/README.md` for the per-domain API to call.
