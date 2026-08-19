# imaging starters — 一份声明,所有成像出口 / one declaration, every exit

Cross-sections, per-step process films, 3D models with a self-contained
web viewer, SEM-style views and Blender figures — all driven by THREE
files YOU own (klink ships mechanism only, zero process data):

| 你的文件 / yours | 是什么 |
|---|---|
| `demo_layout.py` | 版图:四管 CMOS 单元行(左两个 NMOS 在 p 衬底、右两个 PMOS 在 n 阱),poly 栅横跨有源区,每个源漏都有接触孔,metal-1 引线 + 上下电源轨;并给出剖面切线 `CUT_UM`。**换成你的版图/GDS** |
| `demo.pyxs` | 工艺配方(受信 Python,进程内执行):简化平面 CMOS 十一步,`# klink-step: <名字>` 注释标记每个工艺步。**改成你的流程** |
| `demo_stack.py` | `klink_visual_stack_v1` 声明:掩膜层的 z0_um/z1_um、颜色、SEM 灰阶、材质类别(solid/lattice/dielectric)、对应配方变量名,外加 `recipe_styles`(只存在于配方里的材料:衬底/各种氧化层/注入区/钨塞)。**改成你的工艺** |

配方的十一步 / the eleven steps: p-substrate → n-well implant → LOCOS
field oxide → gate oxide → poly gate + silicide → LDD implants → spacer
→ source/drain implants → ILD deposition → contact etch + W plug + CMP
→ metal-1 damascene。胶片每步一帧。

## 三步走 / three steps

```bat
python xsection_demo.py    # 剖面 + 分步工艺胶片(PNG 条 + GIF)
python render3d_demo.py    # GLB + 自含网页查看器(双击离线打开,逐层调色)
python sem_demo.py         # SEM 风格顶视(灰度+假彩色+逐掩膜序列)
python blender_demo.py     # 论文级渲染 + 可手改 .blend(可选,需 bpy)
```

依赖按需装,klink 的报错会给出精确 pip 命令 / errors name the exact
pip command:

```bat
pip install klayout klayout-pyxs==0.1.13            # 剖面引擎
pip install trimesh shapely mapbox-earcut           # 3D
pip install numpy scipy pillow                      # SEM/胶片渲染
pip install bpy                                     # Blender(~300MB,可选)
```

MCP 工具(连着 KLayout/klink-mcp 时):`imaging.xsection_run` /
`imaging.render3d` / `imaging.sem_top` / `imaging.blender` —— 参数和
这些脚本一一对应,`klink.find_tools domain=imaging` 看全部用法。

**你的工艺文件放哪 / where YOUR process files live**:
`example_template/` 归 klink 包所有——`klink update` 会把它刷回随包
状态(新增/修复 starter,并清理不再随包的旧文件;demo 的 `_generated/`
输出不受影响)。所以**自己的 stack/配方要拷出去改**,放项目根或你自己
的目录(和 `pdk.py` 一个待遇),别直接改这里的文件。Copy starters OUT
to your project root before editing — `klink update` refreshes this
directory back to the shipped state (your `_generated/` outputs are
left alone).

要点 / notes:
- **Windows 终端里中文步骤名显示成 `?`/乱码 ≠ 数据坏了。** 那是控制台
  codepage(GBK)显示不了 UTF-8,文件本身是好的——打开生成的 PNG(标题
  是真字形)或用 UTF-8 读 sidecar JSON 核对即可;真正的字体缺字会记进
  sidecar 的 `font_warnings`,不会静默。Garbled CJK in the *terminal* is
  a console-codepage display issue, not a data bug: check the rendered
  PNG or the sidecar; real missing glyphs are reported in
  `font_warnings`.
- 剖面切线 `cut_um=[[x1,y1],[x2,y2]]` 必须显式给(µm);
- 光栅取景:`z_window_um=(下,上)` 把画面框在器件上(引擎的衬底有好几
  微米深,不框就是一大片体硅),`axis=True` 画 z 标尺和比例尺;
- 配方里以 `_` 开头的变量算中间产物(如 LOCOS 的上下两半),不会被当成
  独立材料自动输出;
- 输出永不静默覆盖(`overwrite=True` 才替换),每次运行都写
  `*.klink_imaging.json` 机读 sidecar;
- 二维材料层用 `kind="lattice", motif="mos2"|"graphene"`,Blender 图里
  直接长出原子晶格;
- 配方是**受信代码**——像对待自己的脚本一样对待 .pyxs 文件。
