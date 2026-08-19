# Region 与可执行版图意图

在 KLayout 里圈出一块区域，让 agent 安全地在里面生成内容——带预览，
改主意后还能重新生成。

> English: [layout-intent.md](layout-intent.md)

```text
拖标尺圈一块区域              (KLayout 自带的标尺工具：box/ellipse)
  -> region.claim             标尺合并成一个 Region 标记 PCell
  -> intent.prepare           分析 + 规划 + 校验（不写入任何东西）
  -> intent.apply             一次事务，一次 Ctrl+Z
  -> intent.regenerate        改编号/间距，原子替换输出
```

## Region

**Region** 是一块已认领的区域：保留标记层（默认 `999/10`，可用
`region.set_layer` 配置）上的一个 `klink_Region` PCell。它活在**你的版图
里**，随 GDS 一起走、重启后还在，也能像其它对象一样被点击 + SEND 给
agent。标尺只是输入手势——认领成功会消耗掉它们（撤销会同时带回标尺和
认领前的状态）。

一次认领可以组合多把标尺，各带三种角色之一：

| 角色 | 含义 | 例子 |
|---|---|---|
| `include` | 并集（默认） | 两个 box → L 形 |
| `clip` | 交集 | 椭圆 ∩ box → 半圆 |
| `exclude` | 差集（挖出的洞永不可写） | 圆 − 圆 → 圆环 |

椭圆的离散化是保守的：include/clip 用内切，exclude 用外切——可写区域
只会缩小，绝不会超出你画的范围。结果必须是一个连通分量；不连通的孤岛
会被拒绝，并把各自的 bbox 报回来，方便你分开认领。

单独使用 Region 也有意义，不必接生成流程：

- `region.get` 返回多边形——把 `hull_um` 喂给 `cell.fill_region`
- `region.occupancy` 报告区域内部有什么：逐层障碍物、命名的障碍 cell、
  空闲面积
- `view.zoom_box` 用 `bbox_um` 导航过去

## 可执行意图：编号阵列

`intent.prepare` 在一个 Region 里规划**任意现有 cell** 的间距网格，
**每个副本都带唯一的物理编号标签**（真实多边形文字，预览与最终应用
效果完全一致）。在你用 `intent.apply` 确认之前不会写入任何东西；输出
落在一个身份稳定的专属 `KLINK_I_*` 容器 cell 里，所以 `intent.regenerate`
之后可以原子地替换它——如果你手动改过输出，会检测到偏离并拒绝，绝不
静默覆盖。

一切都是显式的——klink 不带任何工艺默认值：

- **障碍物由你声明**：`obstacle_layers`（你的设计层）、`obstacle_cells`
  （命名的器件/blackbox cell——每个实例出现都按 bbox 计入）、
  `extra_obstacles_um`（自由形式多边形），加上 `clearance_um`。什么都不
  声明需要显式传 `allow_empty_obstacles: true`。
- **是实例，不是多边形**：阵列出来的是你 cell 的一个实例，支持
  `rotation_deg`（0/90/180/270）和 `mirror`。
- **标签**有两种模式：
  - *offset*：`{layer, height_um, offset_um}`，相对每个 footprint 中心
    的偏移；
  - *slot*（推荐）：在**单元 cell 内部**圈一小块区域，表示"编号写在
    这里"，然后传 `{layer, slot_region: "R00X"}`。每个副本的编号都会
    自动适配字号、精确嵌进自己的 slot；slot 会跟着实例一起移动、旋转。
    拖动 slot 再重新生成——所有编号都跟着走。
- **编号**有两种模式：
  - *prefix*：`{prefix: "S", width: 3, start: 1, order: "top_down"}`
    → S001, S002, …
  - *pattern*：你项目用的任何网格记法——
    `"{row}+{col}"` → 1+2、`"{row}-{col}"` → 1-3、`"R{row}C{col}"`、
    `"{row:A}{col}"` → A1（字母），每根轴各带
    `{start, step, order}`。被拒绝的位点永远不会在编号里留空档。

每份计划在你看到之前都会被校验：footprint 和标签的包含关系、障碍物
净空、重叠、重复编号。有问题的计划无法应用；如果版图在 prepare 和
apply 之间发生了变化，apply 会被拒绝（重新 prepare 即可）。

## 交付：clean export

Region/Port/Anchor 标记是工作用的辅助标记，画在保留层上——它们绝不能
进入最终 mask。`layout.export_clean` 是 fail-closed 的出口：

```text
layout.export_clean {path: "out.gds", allowlist_layers: ["10/0", "20/0"]}
```

它按 PCell 类型（而不是靠猜层号）移除每一个 klink 标记，只写出你显式
声明的层白名单，剥离 PCell 上下文，重新读回输出文件做校验，通过后才
把它替换到位。live 版图本身从不被触碰。`layout.save_file` 仍然是完整
的工作存档。

## 位点引擎（fabrication 域）

确定性网格/编号内核与 fabrication 域共享，也可以直接使用：

- `generate_grid_sites` / `generate_circular_die_sites` —— 在一个 bbox
  或圆形 die 上生成位点网格
- `number_sites` —— rowcol / sequential / prefix / serpentine 几种编号
  方案，带 `order` 和 `start`
- `pattern_site_ids` —— 编号 pattern 背后的网格记法引擎

可运行 demo（对 live KLayout）：

```bash
python -m examples_klink.public.features.region_claim_fill
python -m examples_klink.public.features.region_array_labeled
python -m examples_klink.public.features.fabrication_sites
```
