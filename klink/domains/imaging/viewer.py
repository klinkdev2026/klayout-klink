"""Self-contained 3D viewer pages for the GLB files ``mesh3d`` builds.

ONE portable html file: the vendored @google/model-viewer bundle
(BSD-3/MIT; header notices preserved — see THIRD_PARTY_NOTICES.md) is
inlined and the GLB embedded as a data URI, so the page opens by
double-click, offline, behind firewalls, with no sibling-file fetches.
The right-hand panel gives manual control: background, exposure, tone
mapping (agx = the Blender default look), shadows, per-material
color/metallic/roughness, PNG export.
"""

from __future__ import annotations

import base64
import html as _html
import importlib.resources
import os


class ViewerError(ValueError):
    pass


def _mv_js() -> str:
    ref = importlib.resources.files("klink.domains.imaging") \
        / "_assets" / "model-viewer.min.js"
    js = ref.read_text(encoding="utf-8")
    if "</script>" in js:
        raise ViewerError(
            "vendored model-viewer bundle contains '</script>' and "
            "cannot be inlined safely — do not modify the asset")
    return js


def build_viewer_html(glb_path: str, html_path: str, style, *,
                      title: str = "klink 3D",
                      overwrite: bool = False) -> str:
    """Write the self-contained viewer page.

    ``style`` is a
    :class:`~klink.domains.imaging.viewer_style.ViewerStyle`. Every
    colour in the page and its starting exposure come from it — klink
    ships no palette."""
    if os.path.exists(html_path) and not overwrite:
        raise ViewerError(
            f"{html_path} exists; pass overwrite=True to replace it")
    with open(glb_path, "rb") as fh:
        glb_b64 = base64.b64encode(fh.read()).decode("ascii")
    # title is data, not markup — escape it; material names are handled
    # in-page via textContent (never innerHTML); the CSP blocks network
    # egress so injected content could not beacon anywhere anyway.
    # Style colours go through the schema's validator, so only #RRGGBB
    # can ever reach the page's CSS.
    html = _TEMPLATE.replace("__TITLE__",
                             _html.escape(str(title), quote=True))
    for token, key in (("__PAGE__", "page"), ("__PANEL__", "panel"),
                       ("__PANEL_TEXT__", "panel_text"),
                       ("__BUTTON__", "button"),
                       ("__BUTTON_HOVER__", "button_hover"),
                       ("__BUTTON_TEXT__", "button_text")):
        html = html.replace(token, style.css(key))
    for token, key in (("__EXPOSURE__", "exposure"),
                       ("__SHADOW__", "shadow_intensity")):
        html = html.replace(token, "%g" % float(style.viewer[key]))
    html = html.replace("__MV_JS__", _mv_js()) \
        .replace("__GLB_B64__", glb_b64)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return html_path


_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none';\
 script-src 'unsafe-inline'; style-src 'unsafe-inline';\
 img-src data: blob:; media-src data: blob:; font-src data:;\
 connect-src data: blob:; worker-src blob:;">
<script type="module">__MV_JS__</script>
<style>
html,body{margin:0;height:100%;background:__PAGE__;font-family:sans-serif}
#wrap{display:flex;height:100%}
model-viewer{flex:1;height:100%;background:__PAGE__}
#panel{width:230px;background:__PANEL__;color:__PANEL_TEXT__;padding:12px;
 overflow-y:auto;font-size:12px}
#panel h3{margin:4px 0 10px;font-size:13px}
.row{display:flex;align-items:center;justify-content:space-between;
 margin:6px 0}
.row label{flex:1;margin-right:8px;overflow:hidden;white-space:nowrap}
input[type=color]{width:44px;height:22px;border:none;background:none;
 padding:0}
input[type=range]{width:110px}
button{width:100%;margin-top:10px;padding:6px;background:__BUTTON__;
 color:__BUTTON_TEXT__;border:none;border-radius:4px;cursor:pointer}
button:hover{background:__BUTTON_HOVER__}
</style></head>
<body><div id="wrap">
<model-viewer id="mv" src="data:model/gltf-binary;base64,__GLB_B64__"
 camera-controls auto-rotate exposure="__EXPOSURE__" shadow-intensity="__SHADOW__"
 camera-orbit="30deg 65deg auto"></model-viewer>
<div id="panel">
 <h3>Manual controls</h3>
 <div class="row"><label>background</label>
  <input type="color" id="bg" value="__PAGE__"></div>
 <div class="row"><label>exposure</label>
  <input type="range" id="exp" min="0.2" max="2.5" step="0.05"
   value="__EXPOSURE__"></div>
 <div class="row"><label>tone</label>
  <select id="tone"><option value="neutral">neutral</option>
   <option value="aces">aces (film)</option>
   <option value="agx">agx (blender)</option>
   <option value="commerce">commerce</option></select></div>
 <div class="row"><label>shadow</label>
  <input type="range" id="shi" min="0" max="2" step="0.1"
   value="__SHADOW__"></div>
 <div class="row"><label>softness</label>
  <input type="range" id="sho" min="0" max="1" step="0.05" value="1"></div>
 <div class="row"><label>auto-rotate</label>
  <input type="checkbox" id="rot" checked></div>
 <h3>Layers (color / metal / rough)</h3>
 <div id="mats"></div>
 <button id="png">Export PNG</button>
 <button id="reset">Reset</button>
</div></div>
<script>
const mv = document.getElementById('mv');
const defaults = [];
function hex(f){return '#'+f.slice(0,3).map(
  v=>Math.round(v*255).toString(16).padStart(2,'0')).join('');}
function rgba(h,a){return [parseInt(h.slice(1,3),16)/255,
  parseInt(h.slice(3,5),16)/255, parseInt(h.slice(5,7),16)/255, a];}
mv.addEventListener('load', () => {
  const box = document.getElementById('mats');
  box.textContent = '';
  mv.model.materials.forEach((m, i) => {
    const pbr = m.pbrMetallicRoughness;
    const c = pbr.baseColorFactor;
    defaults[i] = {c: [...c], m: pbr.metallicFactor,
                   r: pbr.roughnessFactor};
    // material names come from user DATA (stack/GLB): build the panel
    // with textContent/DOM APIs only — never innerHTML interpolation
    const row = document.createElement('div');
    const r1 = document.createElement('div');
    r1.className = 'row';
    const lab = document.createElement('label');
    lab.textContent = m.name || ('mat' + i);
    lab.title = m.name || '';
    const colInp = document.createElement('input');
    colInp.type = 'color'; colInp.className = 'col';
    colInp.value = hex(c);
    r1.append(lab, colInp);
    const r2 = document.createElement('div');
    r2.className = 'row'; r2.style.marginTop = '-2px';
    const tag = t => { const s = document.createElement('span');
      s.style.opacity = .6; s.textContent = t; return s; };
    const slider = (cls, val) => { const s = document.createElement('input');
      s.type = 'range'; s.className = cls; s.min = 0; s.max = 1;
      s.step = 0.05; s.value = val; s.style.width = '70px'; return s; };
    const met = slider('met', pbr.metallicFactor);
    const rgh = slider('rgh', pbr.roughnessFactor);
    r2.append(tag('M'), met, tag('R'), rgh);
    row.append(r1, r2);
    box.appendChild(row);
    colInp.addEventListener('input', e =>
      pbr.setBaseColorFactor(rgba(e.target.value, defaults[i].c[3])));
    met.addEventListener('input', e =>
      pbr.setMetallicFactor(parseFloat(e.target.value)));
    rgh.addEventListener('input', e =>
      pbr.setRoughnessFactor(parseFloat(e.target.value)));
  });
});
document.getElementById('bg').addEventListener('input', e => {
  mv.style.background = e.target.value;
  document.body.style.background = e.target.value;
});
document.getElementById('exp').addEventListener('input',
  e => mv.exposure = e.target.value);
document.getElementById('tone').addEventListener('change',
  e => mv.setAttribute('tone-mapping', e.target.value));
document.getElementById('shi').addEventListener('input',
  e => mv.setAttribute('shadow-intensity', e.target.value));
document.getElementById('sho').addEventListener('input',
  e => mv.setAttribute('shadow-softness', e.target.value));
document.getElementById('rot').addEventListener('change',
  e => e.target.checked ? mv.setAttribute('auto-rotate','')
                        : mv.removeAttribute('auto-rotate'));
document.getElementById('png').addEventListener('click', () => {
  const a = document.createElement('a');
  a.href = mv.toDataURL('image/png');
  a.download = 'klink3d.png'; a.click();
});
document.getElementById('reset').addEventListener('click', () => {
  mv.model.materials.forEach((m, i) => {
    const d = defaults[i], pbr = m.pbrMetallicRoughness;
    pbr.setBaseColorFactor([...d.c]);
    pbr.setMetallicFactor(d.m);
    pbr.setRoughnessFactor(d.r);
  });
  document.querySelectorAll('#mats .col').forEach((inp, i) =>
    inp.value = hex(defaults[i].c));
  document.querySelectorAll('#mats .met').forEach((inp, i) =>
    inp.value = defaults[i].m);
  document.querySelectorAll('#mats .rgh').forEach((inp, i) =>
    inp.value = defaults[i].r);
  document.getElementById('bg').value = '__PAGE__';
  mv.style.background = '__PAGE__';
  document.body.style.background = '__PAGE__';
  mv.exposure = __EXPOSURE__; document.getElementById('exp').value = __EXPOSURE__;
});
</script></body></html>
"""
