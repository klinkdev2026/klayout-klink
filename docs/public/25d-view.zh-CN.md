# 2.5d 视图——用同一份工艺声明把层叠画成 3D

KLayout 自带原生 2.5d 查看器(Tools → 2.5d View),把版图多边形挤出成可
旋转的 3D 叠层。klink 用一次调用驱动它:布线和 LVS 读的同一份
`StackSpec`,加上一张你自己拥有的 z 表,就变成"工具所推理的那个版图"的
挤出视图。

> English: [25d-view.md](25d-view.md)

## 1. 原生查看器是什么(官方机制)

本页一切都建立在有文档记载的 KLayout 特性上——官方手册的 2.5d view 章
节,以及 `D25View` API 类(KLayout 0.28 起官方提供)。先知道两件事:

- **需要 OpenGL。** 2.5d 视图只存在于带 OpenGL 编译的 KLayout 构建里
  (标准桌面版都带)。缺失时 klink 返回指令性错误,不会崩。
- KLayout 自己的工作流是脚本式的:Tools → 2.5d View → New 2.5d Script
  打开一个 DRC 风格脚本,用官方 `z(...)` / `zz(...)` 声明做挤出:

  ```ruby
  z(input(1, 0), zstart: 0.1.um, height: 200.nm)   # 一片挤出层
  zz(name: "GATE", like: "1/0") do                 # 命名材料组
    z(layer1, zstart: 0.0, height: 10.nm)
    z(layer2, height: 10.nm)                       # zstart 默认接上一片顶部
  end
  ```

  `z` 接受 `zstart`、`zstop` 或 `height`,以及显示选项(`color`/`frame`/
  `fill` 十六进制、`like: "7/0"` 借用版图层色、`name`)。脚本路径始终可
  用;下面的 klink RPC 用于"叠层应来自你的工艺声明而非手写脚本"的场合。

## 2. klink 路径:从 StackSpec 一次调用

klink **不带任何 z 高度**——层厚和海拔是你拥有的工艺事实,和层号、净空
一样。你提供 z 表;klink 把它与你的 `StackSpec`(已命名每个导体和过孔
层)合成显示清单,喂给 KLayout 原生查看器:

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
displays = stack_displays(stack, z_um={     # 你的工艺事实
    "31/0": (0.00, 0.50),                   # zstart, zstop(微米)
    "32/0": (0.50, 1.00),
    "33/0": (1.00, 1.50),
}, colors={"31/0": 0x2B6CB0})

with KLinkClient() as c:
    res = c.show_25d(displays, cell="MY_TOP")
    assert res["ok"] and not res["empty_layers"]
```

各部件的保证:

- `stack_displays` 覆盖 stack 的**每个**导体和过孔层;缺 z 条目是点名该
  层的指令性错误,绝不瞎猜。名字默认取导体声明的 role;`extra_layers`
  用来加非导体的器件/标记层(它们同样要 z 条目)。
- `view.show_25d` RPC 在碰窗口**之前**校验整份显示清单(层格式、z 区
  间),按层次读取指定 cell 的几何,并返回结构化判定:显示了几种材料、
  哪些源层是空的(`empty_layers`——空层通常意味着层号写错,要读,别
  忽略)。
- 显示条目接受 `color`(0xRRGGBB,同时用于边框和填充),或分开的
  `frame_color`/`fill_color`;不给则用查看器默认。

可跑、自包含(自己画双金属+过孔场景):

```bash
python -m examples_klink.public.features.stack_25d_view --port <会话端口>
```

live 实测输出:

```text
display list derived from the stack:
   31/0   metalA           z 0.00 -> 0.50 um
   32/0   via 31/0<->33/0  z 0.50 -> 1.00 um
   33/0   metalB           z 1.00 -> 1.50 um
2.5d window: ok=True cell=PUB_STACK_25D displays=3 empty_layers=[]
RESULT: PASS (native 2.5d stack shown — orbit it with the mouse)
```

## 3. 真实电路块的 3D:add4 案例

同一个调用直接扩展到完整数字块。`--demo-add4` 把 fit-device starter 画
出的版图——173 个拟合器件、94 条布通网、电源网格、两个 via 家族——渲染
成六材料叠层。z 表按 starter 的合成**背栅**器件建模:栅板是*最底部*导
体,半导体沟道隔薄介质悬在其上,源漏金属落在沟道上,两个 cut 家族向上
爬到顶层布线金属:

```text
   101/0  gate metal (bottom)    z 0.00 -> 0.10 um
   102/0  via 101/0<->104/0      z 0.10 -> 0.16 um
   103/0  channel                z 0.12 -> 0.16 um
   104/0  source/drain metal     z 0.16 -> 0.28 um
   105/0  via 104/0<->106/0      z 0.28 -> 0.40 um
   106/0  top routing metal      z 0.40 -> 0.55 um
```

```bash
python -m examples_klink.public.demos.digital.fit_device_pnr_lvs --port <会话端口>
python -m examples_klink.public.features.stack_25d_view --port <会话端口> --demo-add4
```

实测输出:

```text
2.5d window: ok=True cell=DEMO_ADD4 displays=6 empty_layers=[]
RESULT: PASS (full add4 block in 3D — 173 devices, PDN, both via families;
orbit with the mouse)
```

你会看到什么,以及它为什么不只是好看:横向的源漏通道与纵向的栅/顶层
布线分离成清晰的平面;每个 via 家族在它的两层金属之间变成可见的柱阵;
PDN 环和 strap 读作粗壮的顶部边框。错误的叠层声明在这里会跳出来——via
家族放错 z、沟道层漏declare、电源轨在错误金属上——恰恰因为这个视图渲染
的是路由器和 LVS 消费的**同一份声明**,不是另一份手工维护的描述。画面
让你意外时,错的是声明(或版图);像对待其它门一样对待它。

沟道(103/0)刻意走 `extra_layers`:它是器件层不是导体,所以进显示清单
但永远不进布线和 LVS 连通性——与 DRC 指南用 `exclude_around` 划的器件/
布线边界是同一条线。

## 4. 逃生舱(以及 RPC 藏起来的东西)

RPC 封装的是官方 `D25View` API(`begin` → `open_display` →
`entry(region, dbu, zstart, zstop)` → `close_display` → `finish`)。用
`exec.python` 手动驱动可行,但有真实的巫术:对话框**不能直接构造**
(`D25View()` 存在,但 `begin` 拒绝外来实例)——必须由 KLayout 自己的
工厂通过 Tools → 2.5d View → Open Window 菜单动作创建,再从顶层部件里
找到实例。`view.show_25d` 封装的正是这套舞步,外加校验和结构化判定。
逃生舱只用于 RPC 未暴露的事(相机控制、图像导出);需求反复出现时,
优先扩展 RPC。

## 5. 排障

| 症状 | 含义 / 修法 |
|---|---|
| 报错提到 OpenGL / 没有 D25View | 这个 KLayout 构建没有 2.5d 查看器——装标准桌面版(0.28+) |
| `empty_layers` 列出某层 | 该层在所选 cell 里没有几何——查层号和 cell |
| `cell ... not in the active layout` | 激活持有你版图的 tab,或显式传 `cell=` |
| KLayout 重启后窗口消失 | 不持久化任何东西——重跑那一行调用即可 |
| 叠层交错 / 顺序不对 | z 表是你的:修声明,别去揉视图 |
