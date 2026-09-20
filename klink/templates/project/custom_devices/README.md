# custom_devices/

Reusable code lives here. Task records live separately in `../runs/`
at the project root, so generated scripts and artifacts are visible beside
the other project-level working directories:

```
custom_devices/
  toolbox/          your ASSETS: reusable, verified, importable tools
    __init__.py     the index ? keep its export list current
runs/               your LEDGER, at the project root
  INDEX.md          one line per run, newest on top
  YYYY-MM-DD_example-array/
    run.py          the driver for THIS task (imports toolbox + pdk)
    out/            THIS task's artifacts
    notes.md        request, work, and real verification evidence
```

Rules that keep it tidy (the agent follows these; so should you):

1. **Every task is a run.** Start it with `klink run new <slug>` —
   the date-stamped folder, `run.py` stub, `out/`, `notes.md`
   template, and its INDEX line are created for you. Finish by
   filling `notes.md` and turning the INDEX line into a one-line
   summary + PASS/FAIL.
2. **Reusable code graduates.** When a run's code proves out and you
   (or the agent) would want it again, promote it into `toolbox/`
   with a docstring and an export entry. Writing the same function a
   second time is the graduation signal.
3. **Process facts never move in here.** `../pdk.py` stays the only
   home for layers/vias/dimensions; toolbox functions take the
   process as an argument.
4. **Commit per run.** The git history is the one record that never
   rots.

A quick throwaway probe may sit flat in `custom_devices/`, but any task that
produces an artifact worth keeping belongs in the project-root `runs/` ledger.

Every build script, wherever it lives:

1. imports `PROCESS` (and any device library) from `pdk.py`,
2. calls the relevant klink API **explicitly** with that process,
3. draws into a clearly named cell (never the user's working cell),
4. is verified with geometry/LVS queries — not screenshots.

Minimal driver shape:

```python
from pdk import PROCESS          # your process — the only home for process data
from klink import KLinkClient

with KLinkClient(port=8765).connect() as c:   # your KLayout RPC port (8765 = default)
    # ... call klink APIs, passing PROCESS explicitly ...
    ...
```

See `../recipes/README.md` for the per-domain API to call.
