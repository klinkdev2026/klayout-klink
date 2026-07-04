# Demo 及各自依赖

> English: [demos.md](demos.md)

公开画廊在 `examples_klink/public/demos/` 下有八个承重 demo。**八个全部开箱
即跑**——没有一个需要你的保密几何。两个完全离线;六个需要 live KLayout 会话
(但同样不需要外部 GDS);其中一个(gdsfactory 接管)还需要同一解释器里有
gdsfactory。本页对每个都说实话。

所有器件/工艺相关的东西都住在示例自己里;`klink` 发布零工艺常数。抄一个
demo、把数字改成你自己的工艺——流程完全相同。

## 离线可跑(无 KLayout、无 GDS)

### EBL 纳米器件 wraparound

```bash
python -m examples_klink.public.demos.ebl_wraparound          # [--live] [--keep]
```

参数化电子束光刻 wraparound 生成器。离线打印生成的 bundle;`--live` 写进
KLayout 会话。实测输出:`"ok": true`,40 电极,12 patch,writefield 16
fields / 11 windows / 20 crossings / **0 violations**、**0 overlaps**。

### Hall bar 纳米器件

```bash
python -m examples_klink.public.demos.hallbar                 # [--live] [--keep]
```

参数化 Hall bar 生成器。离线打印语义 bundle 加布线结果;`--live` 写一个用完
即弃的 KLayout cell(除非 `--keep`,否则删除)。

## Live 跑(KLayout + 插件,仍无需 GDS)

### 神经电极 harness

```bash
python -m examples_klink.public.demos.neural_electrode --port <会话端口> --elec-rows 4
```

自包含探针生成器:定义 pad/via 几何和 Port/Anchor 资源,然后调 tapered-hybrid
路由器。实测输出(4 行):`ok: True`,48 端口,24 net 布通(12 条 `1/0` +
12 条 `3/0`),**sibling-overlap 0**、**obstacle-hit 0**。用空的或测试用的会话。

### 拟合器件 → 数字布局布线 → LVS

```bash
python -m examples_klink.public.demos.fit_device_pnr_lvs --port <会话端口>   # [--draw-only]
```

完整的自包含数字流程,不含 IP:从**合成** exemplar 几何拟合参数化器件
PCell,放置,跑详细布线,live LVS 验证。实测输出:94/94 布通,**LVS
`match=True`**,173 个器件。换成你自己采集的 exemplar box 即可拟合你的真实
器件——流程不变。

### 手写网表 → lint 校验 → 布局布线 → LVS

```bash
python -m examples_klink.public.demos.chat_to_netlist_pnr --port <会话端口>
```

"聊天里说清楚,拿到验证过的版图"流程:三级环形振荡器网表**纯手写**
(想象对话里的每句需求对应几行显式网表),先过 `lint_netlist` 校验(任何
结构错误在几何存在之前就拿到修法提示),然后放置、布线、LVS 验证,每个
节点以带标签的裸走线引到外围。实测输出:lint 0 错,3/3 布通,**LVS
`match=True`**,6 个器件,3 个观察点全部提取验证 CONNECTED。网表就是普通
数据——agent(或你)可以为**任意**拓扑手写一份,不需要逻辑综合器。

### 大规模多层布局布线

```bash
python -m examples_klink.public.demos.multilayer_pnr_lvs --port <会话端口>
```

规模型 demo:一份内置的 766 器件合成网表(一个玩具级 4 位 ALU,268 个
门,由开源逻辑综合器生成网表,再映射到本画廊的合成拟合器件上)先过
lint 校验,再用内置的层数需求顾问在两套示例工艺栈上做对比——上面"拟合
器件"demo 用的公开 3 层工艺,以及本 demo 自己定义的第二套 7 层示例工艺栈
(器件端子之上 2 条垂直 + 2 条水平的干净信号层)。顾问会打印出每套工艺栈
的核心面积代价,让你看清楚为什么这个规模的设计需要额外的层,而上面几个
小 demo 用 3 层就很舒服。接着放置、把设计的全部 20 个主端口都引出来
(西侧 13 个输入、东侧 7 个输出),再用多层布线引擎布线。实测输出:405/405
个 net 布通,**LVS `match=True`**,766 个器件,20 个端口全部提取验证
CONNECTED,端到端约 17 秒。抄这个文件、把 `PUBLIC_MULTILAYER` 改成你自己的
层栈——流程不变。

### 针卡优先布局布线

```bash
python -m examples_klink.public.demos.padframe_pnr_lvs --port <会话端口>   # [--no-card]
```

贴合硬件现实的**反序**流程:针卡 / pad 环**先存在**(位置很久以前就冻结
了),电路必须来迁就它——哪怕针卡内腔装不下整个器件块。用和"拟合器件"demo
相同的合成 4 位加法器与拟合器件,先过 lint,然后造一个 20-pad 的替身针卡再用
`pads_from_gds` **收割回来**(真实流程里跳过造卡这步,直接收割你自己的卡文件)。
一张普通的 net→pad 表把全部 14 个主端口 + VDD + GND 都指派好(4 个冗余 pad
留空不用);因为针卡内腔只装得下一半的行,`place_grid(forbid_y_bands=…)` 把
器件块沿针卡底排 pad **半内半外**劈开,`pdn_split_bands` 每个区域各织一张电源
网、再用脊柱 strap 桥接。实测输出:94/94 布通,**LVS `match=True`**,173 个
器件,半内半外 85 内 / 80 外,16 个已指派 pad 全部提取验证 CONNECTED、4 个
冗余 pad 全部隔离。`--no-card` 彻底去掉针卡:每个端口以带标签的裸线头引到
外围(输入在西、输出在东,吸附到路由通道中心),电源走引擎自动标注的 PDN
系带轨——94/94 布通,**LVS `match=True`**,14 个线头全部 CONNECTED。抄这个
文件、把 pad 表改成你自己的卡。

> KLayout 的"端口"只是一个会话——任意端口都行,没有专用角色。用空的或
> 测试用的会话,别用你手动工作的标签页。

## 硅光(gdsfactory 桥)

### gdsfactory 接管 → 可编辑的光子模块

```bash
python -m examples_klink.public.demos.gf_mzi_module --port <会话端口>
```

一个完整的热光 MZI——倾斜光纤 GC → 1×2 MMI 分束 → 两条热相移臂(下臂镜像)
→ 2×2 MMI 合束 → 横向偏置输出 GC,外加加热器 pad 排和一对光纤环回——**用一段
普通 gdsfactory 脚本**写成,然后一句 `import_gf_component` 整体接管。一张持久
网表随后装下每一种 net:脚本自己的光路(klink 重画)、偏置输出组改成 `sbend`、
Manhattan 路由器够不到的倾斜 GC(`all_angle`)、环回对(`dubins` 圆弧)、以及
加热器→pad 的**电学** net(金属层)。一句 `photonics.reroute` 把它们全部重画
——于是你**在 KLayout GUI 里拖动任意器件后,一次 reroute 就把光路和金属一起
重新布线**。这个拖动→重布线闭环正是重点:版图始终 live 可编辑,不是一次性冻结。
纯命令行下用 `--reroute` 标志重布线:

```bash
# ... 在 KLayout 里拖动一个器件 ...
python -m examples_klink.public.demos.gf_mzi_module --port <会话端口> --reroute
```

`--reroute` 从**拖动后的位置**重布线、**不重建**,所以保留你的编辑。**不带**任何
标志重跑脚本会从 gdsfactory 源**重建**模块、把每个器件弹回原始位置、抹掉你的
拖动——这是新手最常踩的坑,这个标志就是解药。(带 MCP 工具的 agent 可直接调
`photonics.reroute`,无需重跑脚本。)实测输出:import ok,6 条光路 net / 13 实例
/ 5 器件 cell;reroute ok,12 条布线,**0 crossings、0 device-hits**。

这个 demo 需要**跑它的那个解释器里有 gdsfactory**(它在客户端先建好模块再推给
KLayout)。demo 锁定在已测版本线——`pip install "klayout-klink[photonics]"` 直接
装到已知良好的 gdsfactory。如果 gdsfactory 已经在别的 venv 里,就把 klink 装进
**那个** venv(`<那个venv>/python -m pip install klayout-klink`)再从那里跑——
**别**把仓库 sys.path 插进一个外来解释器(那条路会撞版本不匹配 + 几何差 1000×)。
完整规则见 demo 自己的 `## Requirements` 头块。

### 更底层的桥示例

gdsfactory 端口布线示例在 `examples_klink/public/features/` 下(如
`24_gdsfactory_route_ports.py`、`30_gdsfactory_routing_zoo.py`)。它们用开放
的 `gf.gpdk`——同一解释器规则同上,不需要私有 PDK。

## 参考

- [getting-started.zh-CN](getting-started.zh-CN.md) —— 安装、配置、第一个结果。
- [recipes.zh-CN](recipes.zh-CN.md) —— 各领域起点。
- [project-model.zh-CN](project-model.zh-CN.md) —— `klink init` 项目脚手架。
