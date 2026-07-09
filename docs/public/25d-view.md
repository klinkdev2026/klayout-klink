# The 2.5d view — your layer stack in 3D, from the same process declaration

KLayout ships a native 2.5d viewer (Tools → 2.5d View) that extrudes layout
polygons into a rotatable 3D stack. klink drives it with one call: the same
`StackSpec` your routing and LVS read, plus a z table you own, becomes the
extruded view of exactly the layout the tools reason about.

> 中文见 [25d-view.zh-CN.md](25d-view.zh-CN.md)

## 1. What the native viewer is (official mechanics)

Everything here builds on documented KLayout features — the 2.5d view
chapter of the official manual, and the `D25View` API class (official since
KLayout 0.28). Two facts to know up front:

- **OpenGL required.** The 2.5d view only exists in KLayout builds compiled
  with OpenGL (the standard desktop builds are). klink returns an
  instructive error instead of crashing when it is absent.
- KLayout's own workflow is script-based: Tools → 2.5d View → New 2.5d
  Script opens a DRC-flavored script where layers are extruded with the
  official `z(...)` / `zz(...)` declarations:

  ```ruby
  z(input(1, 0), zstart: 0.1.um, height: 200.nm)   # one extruded sheet
  zz(name: "GATE", like: "1/0") do                 # a named material group
    z(layer1, zstart: 0.0, height: 10.nm)
    z(layer2, height: 10.nm)                       # zstart defaults to the
  end                                              # previous z's top
  ```

  `z` accepts `zstart`, `zstop` or `height`, and display options
  (`color`/`frame`/`fill` hex triplets, `like: "7/0"` to borrow the layout
  view's colors, `name`). That script path remains fully available; klink's
  RPC below is for when the stack should come from your process
  declaration instead of a hand-written script.

## 2. The klink path: one call from your StackSpec

klink ships **no z heights** — layer thickness and elevation are process
facts you own, exactly like layer numbers and clearances. You provide a z
table; klink combines it with your `StackSpec` (which already names every
conductor and via layer) into a display list and feeds KLayout's native
viewer:

```python
from klink import KLinkClient
from klink.process_stack import StackSpec
from klink.stack25d import stack_displays

stack = StackSpec.from_dict({
    "conductors": [{"layer": "31/0", "role": "metalA"},
                   {"layer": "33/0", "role": "metalB"}],
    "vias": [{"from": "31/0", "via_layer": "32/0", "to": "33/0",
              "via_cell": "VIA_A"}],
})
displays = stack_displays(stack, z_um={     # YOUR process facts
    "31/0": (0.00, 0.50),                   # zstart, zstop in microns
    "32/0": (0.50, 1.00),
    "33/0": (1.00, 1.50),
}, colors={"31/0": 0x2B6CB0})

with KLinkClient() as c:
    res = c.show_25d(displays, cell="MY_TOP")
    assert res["ok"] and not res["empty_layers"]
```

What the pieces guarantee:

- `stack_displays` covers **every** stack conductor and via layer; a missing
  z entry is an instructive error naming the layer, never a guess. Names
  default to the conductor's declared role; `extra_layers` adds
  device/marker layers that are not conductors (they need z entries too).
- The `view.show_25d` RPC validates the whole display list (layer format,
  z ranges) **before** touching the window, reads the named cell's geometry
  hierarchically, and returns a structured verdict: how many materials were
  displayed and which source layers were empty (`empty_layers` — an empty
  layer usually means a wrong layer number, worth reading, not ignoring).
- Display entries accept `color` (0xRRGGBB, used for frame and fill),
  or separate `frame_color`/`fill_color`; omitted colors use the viewer's
  defaults.

Runnable, self-contained (draws its own two-metal + via scene):

```bash
python -m examples_klink.public.features.stack_25d_view --port <session-port>
```

Measured output on a live session:

```text
display list derived from the stack:
   31/0   metalA           z 0.00 -> 0.50 um
   32/0   via 31/0<->33/0  z 0.50 -> 1.00 um
   33/0   metalB           z 1.00 -> 1.50 um
2.5d window: ok=True cell=PUB_STACK_25D displays=3 empty_layers=[]
RESULT: PASS (native 2.5d stack shown — orbit it with the mouse)
```

## 3. A real block in 3D: the add4 case study

The same call scales to a full digital block. `--demo-add4` renders the
layout built by the fit-device starter — 173 fitted devices, 94 routed nets,
the power grid, and both via families — as a six-material stack. The z table
models the starter's synthetic **backgate** device: the gate plate is the
*bottom* conductor, the semiconductor channel floats above it across a thin
dielectric, source/drain metal lands on the channel, and two cut families
climb to the top routing metal:

```text
   101/0  gate metal (bottom)    z 0.00 -> 0.10 um
   102/0  via 101/0<->104/0      z 0.10 -> 0.16 um
   103/0  channel                z 0.12 -> 0.16 um
   104/0  source/drain metal     z 0.16 -> 0.28 um
   105/0  via 104/0<->106/0      z 0.28 -> 0.40 um
   106/0  top routing metal      z 0.40 -> 0.55 um
```

```bash
python -m examples_klink.public.demos.digital.fit_device_pnr_lvs --port <session-port>
python -m examples_klink.public.features.stack_25d_view --port <session-port> --demo-add4
```

Measured output:

```text
2.5d window: ok=True cell=DEMO_ADD4 displays=6 empty_layers=[]
RESULT: PASS (full add4 block in 3D — 173 devices, PDN, both via families;
orbit with the mouse)
```

What you see, and why it is more than a pretty picture: the horizontal
source/drain channels and the vertical gate/top-metal wiring separate into
distinct planes, every via family becomes a visible column field between its
two metals, and the PDN ring/straps read as the thick top frame. Wrong stack
declarations jump out here — a via family at the wrong z, a channel layer
missing from the stack, a rail on the wrong metal — precisely because this
view renders **the same declaration** the router and LVS consumed, not a
separate hand-maintained description. When the picture surprises you, the
declaration (or the layout) is wrong; treat it like any other gate.

The channel (103/0) is deliberately listed via `extra_layers`: it is a
device layer, not a conductor, so it lives in the display list without ever
entering routing or LVS connectivity — the same device/routing boundary the
DRC guide draws with `exclude_around`.

## 4. Escape hatch (and what the RPC hides)

The RPC wraps the official `D25View` API (`begin` → `open_display` →
`entry(region, dbu, zstart, zstop)` → `close_display` → `finish`). Driving
it by hand over `exec.python` works but carries real arcana: the dialog
**cannot be constructed directly** (`D25View()` exists but `begin` refuses
foreign instances) — it must be created by KLayout's own factory via the
Tools → 2.5d View → Open Window menu action and then located among the
top-level widgets. `view.show_25d` encapsulates exactly that dance, plus
validation and the structured verdict. Use the escape hatch only for things
the RPC does not expose (camera control, image export), and prefer extending
the RPC when a need recurs.

## 5. Troubleshooting

| symptom | meaning / fix |
|---|---|
| error mentions OpenGL / D25View missing | this KLayout build lacks the 2.5d viewer — install a standard desktop build (0.28+) |
| `empty_layers` lists a layer | that layer has no geometry in the chosen cell — check the layer number and the cell |
| `cell ... not in the active layout` | activate the tab holding your layout, or pass `cell=` explicitly |
| window vanished after a KLayout restart | nothing is persisted — rerun the call; it is one line |
| stack looks interleaved / wrong order | the z table is yours: fix the declaration, don't massage the view |
