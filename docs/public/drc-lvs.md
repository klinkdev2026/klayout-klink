# DRC and LVS with klink

How to write and run design-rule checks (DRC) and layout-versus-schematic
(LVS) against a live KLayout session — by hand, from a `ProcessProfile`, or
through an agent.

> 中文见 [drc-lvs.zh-CN.md](drc-lvs.zh-CN.md)
> Handing this to an agent? Give it the condensed recipe instead:
> [drc-lvs-agent-handout.md](drc-lvs-agent-handout.md)

Both checks run **inside KLayout** — klink does not reimplement them. DRC
scripts execute on KLayout's official DRC engine through the `drc.run` RPC;
LVS uses KLayout's native connectivity extraction through `lvs_check`. klink
adds the transport, the structured results, and (for DRC) a generator that
derives the deck from the same `ProcessProfile` that drives routing and LVS.

## 1. The KLayout DRC language in ten lines

A DRC script is a Ruby-DSL "runset" executed by KLayout. The authoritative
references are the official manual pages — *DRC basics* and the *DRC
reference* (`klayout.de` → Documentation). Everything klink generates or this
page shows uses only constructs documented there:

```ruby
report("my checks")                 # open a report database (do this FIRST)
m1 = input(101, 0)                  # read layer 101/0 (merged polygons)

m1.width(2.0).output("w1", "M1 width < 2.0 um")       # min width
m1.space(2.0).output("s1", "M1 space < 2.0 um")       # min spacing
cut = input(102, 0)
cut.enclosed(m1, 0.5).output("e1", "cut enclosure in M1 < 0.5 um")

dev = input(29, 0).sized(10.0)      # grow a region by 10 um
errs = m1.space(2.0).polygons       # edge-pair markers -> polygons
errs.outside(dev).output("s2", "spacing outside device regions")
```

Three facts that save hours:

- **A floating-point number means micrometers; an integer means database
  units.** `width(2.0)` checks 2 µm; `width(2)` checks 2 dbu (≈ 2 nm at the
  common 0.001 dbu) and silently passes everything. Always write the decimal
  point.
- **`report(...)` must come before any `.output("name", "desc")`** — string
  outputs file violations into the report database that `report` opens.
- **Checks accept a metric**: `width(2.0, projection)` measures parallel edge
  overlap only (the promise a Manhattan router makes); `euclidian` (the
  default) also measures diagonally across corners and will flag right-angle
  corner artifacts in dense grid-routed metal. Both are official; choose
  deliberately.

## 2. Running a deck through klink

`drc.run` executes the script inside the live session and returns structured
results — stdout, an exception (or `None`), and a violation summary when you
ask for a report database:

```python
from klink import KLinkClient

deck = """\
report("quick checks", $output_rdb)
m1 = input(101, 0)
m1.width(2.0, projection).output("w1", "M1 width < 2.0 um")
"""

with KLinkClient() as c:
    res = c.drc_run(deck, output_rdb="/tmp/quick.lyrdb", result_mode="summary")
    assert res["exception"] is None
    print(res["rdb_summary"])   # {"total_items": N, "categories": [...]}
```

`$output_rdb` is substituted server-side with the `output_rdb` argument, so
the deck and the Python call always agree on the file. The verdict discipline
is all-or-nothing: **a run passes only when `exception` is `None` and
`total_items` is 0.** A deck that errors out did not pass; a nonzero count is
a finding, not noise.

## 3. The same-source deck: DRC from your ProcessProfile

If you already route with a `ProcessProfile`, you do not need to write the
deck above by hand — the profile knows the numbers:

```python
from klink.routing.grid.profile_drc import run_drc

res = run_drc(c, profile)               # width/space per routing layer +
print(res["ok"], res["total"])          # cut enclosure per via, all from
for cat in res["categories"]:           # the SAME profile the router and
    print(cat["name"], cat["count"])    # LVS read
```

Generated rules (inspect them with `profile.drc_script()`):

| rule | value | source field |
|---|---|---|
| width per routing layer | ≥ `wire_width_um` | the drawn wire width |
| space per routing layer | ≥ `wire_clear_um` | different-net clearance |
| cut enclosure per via, both metals | ≥ `litho_tol_um` | via cut inset |

Two knobs matter in practice:

- `metrics="projection"` is the default — it checks exactly what the grid
  router promises. Switch to `euclidian` for the stricter fab-style measure
  and expect corner findings in dense metal.
- `exclude_around=(layer_spec, size_um)` suppresses width/space markers that
  touch device regions (grown from a marker layer such as the profile's
  channel layer). Device-internal geometry — a source/drain gap smaller than
  the wire clearance — follows *device* rules, not routing rules; real PDKs
  scope their metal decks the same way. Via-enclosure checks are never
  excluded. Declare the exclusion in your example so reviewers see it.

Runnable end-to-end proof (positive control, negative control, and the full
deck over the fit-device starter's layout):

```bash
python -m examples_klink.public.features.profile_drc_gate --port <session-port> [--check-demo]
```

Measured output on a live session:

```text
[positive control] legal scene: ok=True violations=0
[negative control] bad scene: violations=1 fired=['space_21_0']
[demo gate] DEMO_ADD4: ok=True violations=0
RESULT: PASS (deck passes legal geometry, catches the planted violation)
```

With this, one profile instance feeds all three gates: the router draws to
it, the DRC deck measures it, LVS extracts with it — same numbers, same
layers, by construction.

## 4. LVS: declared nets versus extracted geometry

klink's LVS flow compares what you *declared* (which terminals belong to the
same electrical node) against what KLayout's native extractor finds in the
drawn geometry:

```python
from klink.domains.structdevice.orchestrators import lvs_check

res = lvs_check(
    c, "MY_TOP",
    declared=[{"net": "n1", "terminals": ["X1.S", "X2.G"]}, ...],
    mode="lvsdb",
    connectivity=profile.connectivity_spec(),   # same profile again
    terminal_provider=...,                       # where each terminal sits
    placement=..., device_terms=...,
)
assert res["ok"] and res["match"]
```

- `connectivity` says which layers conduct and which via cuts bridge them —
  derived from the same profile (`connectivity_spec()`), so the judge reads
  the same process declaration as the router. The *extraction itself* is
  KLayout's, not klink's: the router cannot grade its own homework.
- `mode="lvsdb"` also writes a native `.lvsdb`; open it with the
  `view.show_lvsdb` RPC and KLayout's Netlist Browser cross-probes layout ↔
  netlist both ways.
- The gate is `match=True` — nets AND devices reconciled. Anything else is a
  failure to investigate, not to explain away.

Measured output of the fit-device starter (the flow this page's demo checks):

```text
[public] FlexDR ok=True routed=94/94 markers=0
[public] LVS ok=True match=True devices=173
```

## 5. House rules (they keep the gates honest)

1. **All-or-nothing verdicts.** DRC passes at zero violations with no
   exception; LVS passes at `match=True`. There is no "close enough".
2. **Never delete a rule to make a run pass.** Scope it (`exclude_around`,
   `layers=[...]`) and say why in the example — an undeclared exclusion is a
   silent hole in your signoff.
3. **Fix the geometry or the declaration, not the judge.** A real finding
   means the drawing or the declared netlist is wrong; editing the deck is
   the last resort and needs a written reason.
4. **Structured evidence only.** The numbers above come from RPC results, not
   screenshots; screenshots are for humans who ask, never for verification.
