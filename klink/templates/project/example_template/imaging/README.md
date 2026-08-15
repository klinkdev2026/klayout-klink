# imaging starters — 一份声明,所有成像出口 / one declaration, every exit

Cross-sections, per-step process films, 3D models with a self-contained
web viewer, SEM-style views and Blender figures — all driven by TWO
files YOU own (klink ships mechanism only, zero process data):

| 你的文件 / yours | 是什么 |
|---|---|
| `demo_stack.py` | `klink_visual_stack_v1` 声明:每层 z0_um/z1_um、颜色、SEM 灰阶、材质类别(solid/lattice/dielectric)、对应配方变量名。**改成你的工艺** |
| `demo.pyxs` | 工艺配方(受信 Python,进程内执行):`# klink-step: <名字>` 注释标记每个工艺步。**改成你的流程** |

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

要点 / notes:
- 剖面切线 `cut_um=[[x1,y1],[x2,y2]]` 必须显式给(µm);
- 输出永不静默覆盖(`overwrite=True` 才替换),每次运行都写
  `*.klink_imaging.json` 机读 sidecar;
- 二维材料层用 `kind="lattice", motif="mos2"|"graphene"`,Blender 图里
  直接长出原子晶格;
- 配方是**受信代码**——像对待自己的脚本一样对待 .pyxs 文件。
