# Third Party Notices

This repository includes, adapts, or interoperates with material from the
third-party projects below. Each section names the upstream project, its
copyright, and its license. No upstream source trees are bundled; the routing
derivations are independent reimplementations that retain attribution as
required by their licenses.

## KLayout

Origin: https://github.com/KLayout/klayout (https://www.klayout.de/)

Copyright (c) Matthias Köfferlein and the KLayout contributors

KLayout is **not bundled**: it is the user-installed layout editor that klink
controls. The `klink_plugin` salt package in this repository is klink's own
Apache-2.0 code, loaded by KLayout's macro environment and written against
KLayout's public `pya` scripting API; no KLayout source is copied into this
repository. KLayout itself is distributed under the GNU General Public License,
Version 3 — obtain KLayout and its full license text from the links above.

## KlayoutClaw

Origin: https://github.com/caidish/KlayoutClaw

Copyright (c) 2026 caidish

The nanodevice flake prior JSON files under
`klink/domains/nanodevice/flake/priors/` are copied from KlayoutClaw, and the
morphological mask helpers in `klink/domains/nanodevice/flake/detect.py` are
ported from KlayoutClaw `core.py`.

MIT License text:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Klayout-Router

Origin: https://github.com/Legendrexial/Klayout-Router

Copyright (c) 2025 Legendrexial

`klink/domains/nanodevice/ebl/patching.py` is a klink-oriented rewrite of the
Auto-patching idea from `Macros/Auto-patching/patching.py`: expand writefield
edges into a narrow grid, intersect with electrodes, and generate patch boxes.

MIT License text:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## OpenROAD

Origin: https://github.com/The-OpenROAD-Project/OpenROAD

Copyright (c) 2018-2025, The OpenROAD Authors
All rights reserved.

Parts of klink's routing engine are faithful ports or concept adaptations of
OpenROAD's detailed router (`drt` — FlexDR / FlexPA / FlexGC) and global router
(`grt` — FastRoute). No OpenROAD source is bundled; these are independent
Python/Rust reimplementations written against OpenROAD's published algorithms
and source, retaining attribution as required by the BSD 3-Clause License:

- `klink/routing/backends/flexdr/` — faithful port of OpenROAD `drt`/FlexDR
  (byte-frozen reference).
- `klink/routing/grid/pathfinder.py`, `gcell.py`, `capacity_grid.py`,
  `global_router.py` — `grt` / FastRoute capacity-grid and negotiated-cost
  concept adaptations.
- `klink/routing/backends/negotiated/` — PathFinder / FastRoute
  negotiated-routing concepts.

BSD 3-Clause License text:

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## gdsfactory

Origin: https://github.com/gdsfactory/gdsfactory

Copyright (c) the gdsfactory contributors

gdsfactory is an **optional runtime dependency and is not bundled**: klink's
silicon-photonics workflows (`klink/routing/backends/gdsfactory/`) are
interface code written by klink against gdsfactory's public API (`gf.Port`,
`gf.routing.route_bundle`, component instantiation); users install gdsfactory
themselves. No gdsfactory source is copied into klink. It is listed here for
attribution because klink's photonic routing is designed around, and produces
its results through, gdsfactory's MIT-licensed routing engine.

MIT License text:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## klive

Origin: https://github.com/gdsfactory/klive

Copyright (c) the klive authors (gdsfactory project)

The KLayout plugin's klive-compatible display server
(`klink_plugin/python/klink_server/klive_compat.py` and `klive_forward.py`) is
an **independent reimplementation of the klive display protocol** (JSON over
TCP on port 8082, studied from klive 0.4.1) so that gdsfactory-style
`Component.show()` calls work unchanged against a klink-loaded KLayout. No
klive source is bundled; the protocol shape and file-loading behavior were
derived from the MIT-licensed klive project, credited here.

MIT License text:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
