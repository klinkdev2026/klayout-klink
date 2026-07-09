# 用 klink 做 DRC 和 LVS——从第一个 deck 到产线 runset

如何对 live KLayout 会话编写并运行设计规则检查(DRC)和版图-原理图对比
(LVS)——以及**产线级** runset 到底长什么样。本文以真正发布了 KLayout
产线 deck 的开源 PDK 为范本(IHP SG13G2、GlobalFoundries GF180MCU、
SkyWater SKY130,全部 Apache-2.0,值得通读)。

> English: [drc-lvs.md](drc-lvs.md)
> 要发给 agent?给它精简配方:[drc-lvs-agent-handout.zh-CN.md](drc-lvs-agent-handout.zh-CN.md)

两种检查都**在 KLayout 内部**执行——klink 不自己重新实现。DRC 脚本通过
`drc.run` RPC 跑在 KLayout 官方 DRC 引擎上;LVS 用 KLayout 原生连通性提取
和网表比较器。klink 提供传输、结构化结果,以及(对 DRC)一个从驱动布线和
LVS 的同一份 `ProcessProfile` 推导入门 deck 的生成器。

本文分层:§1 语言速成,§2 产线 runset 逐规则解剖,§3 同源入门 deck,
§4 产线 LVS,§5 klink 的 LVS 路径,§6 盲测案例研究,§7 家规。§1 扫一眼,
§2 和 §4 常驻。

---

## 1. 十行看懂 DRC 语言

DRC 脚本是 KLayout 执行的 Ruby DSL "runset"。权威参考是官方手册(*DRC
basics*、*DRC runsets*)和 `klayout.de` 的 *DRC reference* 页:

```ruby
report("my checks")                 # 打开报告数据库(必须最先)
m1 = input(101, 0)                  # 读取 101/0 层(已合并多边形)

m1.width(2.0).output("w1", "M1 width < 2.0 um")       # 最小线宽
m1.space(2.0).output("s1", "M1 space < 2.0 um")       # 最小间距
cut = input(102, 0)
cut.enclosed(m1, 0.5).output("e1", "cut enclosure in M1 < 0.5 um")

dev = input(29, 0).sized(10.0)      # 区域外扩 10 µm
errs = m1.space(2.0).polygons       # edge-pair 标记 -> 多边形
errs.outside(dev).output("s2", "器件区外的间距违例")
```

三个省几小时的事实:

- **浮点数是微米;整数是数据库单位。** `width(2.0)` 查 2 µm;`width(2)`
  查 2 dbu(常见 0.001 dbu 下 ≈ 2 nm),静默放过一切。产线 deck 写显式
  单位(`0.16.um`、`300.nm`);至少永远写小数点。
- **`report(...)` 必须在任何 `.output("名", "描述")` 之前**——字符串形式
  的 output 把违例写进 report 打开的报告数据库。
- **检查可带度量方式**:`euclidian`(默认;真最短距离,含拐角斜量)、
  `projection`(只量平行边投影)、`square`。发布中的 PDK deck 的金属规则
  显式用 `euclidian`;§3 讲什么时候 `projection` 才是对的门。

---

## 2. 产线 DRC runset 解剖

打开任何一个发布中的 KLayout deck(IHP 的 `ihp-sg13g2.drc` 约 530 行骨架
加按主题拆分的规则文件;GF180MCU 同构),你会看到同样的六段式。这正是
"随手查几个线宽"式脚本缺的东西。

### 2.1 头部:运行模式、开关、日志

产线 deck 用 `$变量`(由 runner 传入)参数化,并在开头选定执行策略:

```ruby
# 执行策略——三选一:
deep                        # 层次化:子 cell 查一次,不按实例重复查
tiles(500.um)               # 或 平铺:超大版图的内存上界
tile_borders(30.um)         #    tile 重叠带,跨界违例不漏
threads(Etc.nprocessors)    # tile 并行
flat                        # 或 平坦:最简单,小版图够用

verbose(true)               # 逐规则计时进日志
```

官方约束:**`deep` 与 `tiles` 互斥**——开平铺就关层次化,反之亦然。真
deck 把选择暴露成开关(`$run_mode` = `deep`/`tiling`/`flat`)、默认
`deep`;还把规则组关在"表"开关后面(`$no_beol`、`$no_offgrid`、
`$tables = "metal1 via1"`),迭代期跑单节、签核时跑全套。

### 2.2 规则数值住在规则外面

deck 里是规则*逻辑*;*数字*来自按规则号索引的数据文件(IHP 加载一份
`{"M1_a": 0.16, "M1_b": 0.18, ...}` 的 JSON)。这是 deck 可审查的关键:
工艺微调是数据 diff,不是代码 diff。klink 的 profile 推导 deck(§3)是
同一思想——profile 就是那份数据文件。

### 2.3 规则号对齐设计规则手册(DRM)

每个检查按 DRM 章节命名,描述里嵌着数值。发布 deck 的原样形态:

```ruby
# Rule M1.a: Min. Metal1 width is 0.16µm
m1_a_l = metal1_drw.width(0.16.um, euclidian)
m1_a_l.output('M1.a', '5.16. M1.a: Min. Metal1 width: 0.16 μm.')
m1_a_l.forget
```

照抄三个习惯:DRM 编号做类别名(`M1.a`)、带数值的人话描述、**每条规则
后 `.forget`**——大 deck 逐条显式释放中间层,否则内存膨胀。

### 2.4 派生层:检查之前先做布尔

真规则很少直接作用在绘制层上;它们作用在专门段落里先算好的*派生层*上:

```ruby
nactiv  = activ_drw.not(psd_drw.join(nsd_block))    # n 型有源
pactiv  = activ_drw.and(psd_drw)                    # p 型有源
ngate   = nactiv.and(pwell).and(gatpoly_drw)        # NMOS 栅 = 有源 & 多晶
poly_con = gatpoly_drw.not(res_mk)                  # 导电的多晶(非电阻)
CHIP    = extent.sized(0.0)                         # 版图外廓本身
```

`and/not/join(即或)/xor`、`sized`、`holes`、`with_holes`、`interacting`、
`covering`、`not_outside`、`texts(模式)` 是工作词汇。器件识别也发生在
派生段(标记层 + 文本标签 → 器件区)——同一批派生层随后喂 LVS 提取,
所以产线的 DRC 和 LVS deck 共享派生文件。

### 2.5 完整规则税目

产线 deck 覆盖的远不止 width/space。每类的官方 idiom(取自发布 deck):

**线宽/间距/凹口**(单层):

```ruby
m1.width(0.16.um, euclidian).output('M1.a', '...')
m1.space(0.18.um, euclidian).output('M1.b', '...')   # space 含凹口
m1.isolated(0.18.um).output(...)                     # 只查不同多边形之间
m1.notch(0.18.um).output(...)                        # 只查多边形内部凹口
```

**两层关系**:

```ruby
via1.enclosed(m1, 0.05.um).output('V1.d', '...')     # via 内缩于金属
m1.enclosing(via1, 0.05.um).output(...)              # 同一规则换视角
gatpoly.separation(cont, 0.11.um).output(...)        # 两层最小距离
a.overlap(b, 0.2.um).output(...)                     # 最小交叠深度
```

**间距表 / 平行走线长度(PRL,"宽金属要更大间距"家族)**——产线 idiom
是先用缩放-回涨派生宽金属子集,再带走线长度限定查 separation:

```ruby
# 宽(> 0.3 µm)Metal1 线之间平行走线超过 1.0 µm 时,间距 ≥ 0.22 µm
wide_m1 = metal1_drw.sized(-0.15.um).sized(0.15.um)      # 只剩宽金属
wide_m1.space(0.22.um,
              projection_limits(1.001.um, nil)).output('M1.e', '...')
```

`sized(-w/2).sized(w/2)` 抹掉一切窄于 `w` 的东西——这是标准宽金属派生。
`projection_limits(lmin, lmax)` 把检查限定在平行走线长度落在区间内的边对
上——即 LEF SPACINGTABLE PARALLELRUNLENGTH 语义的原生表达。发布 deck 里
还有单侧变体 `metal1_drw.sep(wide_m1, v, projection_limits(...))`("至少
一根线宽");我们在 KLayout 0.30.x 上的 live 验证中,该形式对一对宽-宽
违例**不触发**,而 `wide.space(...)` 形式能抓到——用之前先在你的
KLayout 版本上对埋好的违例验证你选的变体(§6 演示做法)。

**角度相关规则**——先选边,再检查:

```ruby
bent45 = metal1_drw.edges.with_angle(45, absolute).with_length(0.501.um, nil)
bent45.width(0.20.um, euclidian).output('M1.g', '45 度弯折线宽')
```

**最小面积**——`with_area` 按面积区间选多边形;低于下限的就是违例集:

```ruby
metal1_drw.with_area(0, 0.09).output('M1.d', 'Min. Metal1 area 0.09 um^2')
```

**离格(off-grid)**——所有顶点落在制造网格上:

```ruby
metal1_drw.ongrid(0.005).output('M1_Offgrid', '偏离 5 nm 绘制网格')
```

**密度**——窗口式金属密度用 `with_density(区间, tile_size, tile_step,
boundary)`;发布 deck 会再包一层补边界窗口,但原语是官方的。密度通常是
**单独的 deck**(它强制平铺模式)。

**天线**——先建连通性(从栅到各层金属逐层 `connect(...)`),再
`antenna_check(gate, metal, ratio, [diodes])`。实践中也是单独 deck。

**依赖连通性的 DRC**——有些规则只对网有意义(闩锁效应到阱接触的距离、
保护环规则)。deck 在中途声明 `connect(...)` 后用网感知选择;注意
**没有现成的网感知 `space` 检查**——真需要"异网间距"超出几何规则时,
要用 `nets` + 属性约束布尔自行构造。

### 2.6 豁免(waiver)

fab 通过标记层豁免特定违例。deck 把标记区从特定规则里减掉——**逐规则、
显式、绝不全局**:

```ruby
waived = input(63, 99)                               # 豁免标记层
viol = m1.space(0.18.um, euclidian).polygons
viol.outside(waived).output('M1.b', '...')
```

每个豁免层都写进 deck 头部文档;没写文档的豁免是签核里的洞,不是便利。

---

## 3. 同源入门 deck:从你的 ProcessProfile 推导 DRC

klink 的 profile 推导 deck 就是 §2 架构的第 0 天规模:规则数值外置(在
profile 里)、类别按来源字段命名、豁免显式。只要你用 `ProcessProfile`
布线,一个正确的入门 deck 是白送的:

```python
from klink.routing.grid.profile_drc import run_drc

res = run_drc(c, profile)               # 每布线层 width/space +
print(res["ok"], res["total"])          # 每 via 的 cut enclosure,
for cat in res["categories"]:           # 全部来自路由器和 LVS 读的
    print(cat["name"], cat["count"])    # 同一份 profile
```

| 规则 | 值 | 来源字段 |
|---|---|---|
| 每布线层最小线宽 | ≥ `wire_width_um` | 绘制线宽 |
| 每布线层最小间距 | ≥ `wire_clear_um` | 异网净空 |
| 每 via 的 cut enclosure(上下金属) | ≥ `litho_tol_um` | via cut 内缩 |

两个旋钮,以及背后的道理:

- `metrics="projection"`(默认)检查的恰是曼哈顿格点路由器承诺的东西
  ——平行边净空——不会在直角拐角伪影上误报。产线金属规则用
  `euclidian`(§2.3);当你的几何也要满足拐角对拐角规则时切过去,并预期
  致密金属里出现路由器从未承诺规避的发现。两种跑法只差一个关键词;都跑
  一遍,你就知道每个发现属于哪一类。
- `exclude_around=(层, 外扩µm)` 把 width/space 从器件区(由标记层如
  profile 的 channel 层外扩得到)移开。器件内部几何——小于布线净空的
  源漏间隙——归*器件*规则管;产线 deck 同样围着器件标记限定金属规则
  (§2.6)。via enclosure 检查永不豁免。豁免写在你的 example 里,让
  review 的人看得见。

端到端可跑证明(正对照、负对照、对 fit-device starter 版图跑全 deck):

```bash
python -m examples_klink.public.features.profile_drc_gate --port <会话端口> [--check-demo]
```

live 实测:

```text
[positive control] legal scene: ok=True violations=0
[negative control] bad scene: violations=1 fired=['space_21_0']
[demo gate] DEMO_ADD4: ok=True violations=0
RESULT: PASS (deck passes legal geometry, catches the planted violation)
```

**入门 deck 的边界**:它不知道最小面积、密度、天线、角度、离格、间距表
和你的器件规则——那些需要 profile 不携带的工艺事实。随工艺成熟,沿 §2
手工扩展 deck(profile 推导的规则保留为布线节;按 §2.3–2.5 增加带 DRM
编号的段落)。只要布线节持续从 profile 推导,"路由器、DRC、LVS 读同一份
工艺声明"的三门性质就保持成立。

### 通过 klink 跑任何 deck

```python
res = c.drc_run(deck_text, output_rdb="<路径>.lyrdb", result_mode="summary")
ok = res["exception"] is None and res["rdb_summary"]["total_items"] == 0
```

deck 里的 `$output_rdb` 由服务端用 `output_rdb` 参数替换。
`result_mode="full"` 还返回逐条详情。判定纪律全有或全无;抛了异常的
deck 不算过。

---

## 4. 产线 LVS:完整流水线

KLayout 的 LVS 脚本(`.lvs`,同一 Ruby DSL 外加网表函数)在每个发布 PDK
里都走同一条正典流水线。把 IHP 的 `sg13g2.lvs` 读一遍,别家的就都能读:

```text
source(版图) ──> 派生层 ──> connect() ──> extract_devices()
        ──> align ──> 网表归约选项 ──> compare(原理图) ──> report_lvs
```

**1. 派生**——与 DRC §2.4 相同的布尔层代数,DRC 和 LVS deck 共享派生
文件,两个裁判看到完全一致的器件区。

**2. 连通性**:

```ruby
connect(poly_con, cont_drw)          # 层对层(过孔/接触)
connect(metal1_con, via1_drw)
connect_global(psub, 'VSS')          # 全局网:衬底处处是 VSS
connect_implicit('VDD*')             # 同名标签的网无几何也算连通
```

注意 *_con 派生:产线 deck 连的是 `metal1.not(metal1_res)`——被电阻标记
的金属**不是**导体,是器件。连通性正确与否占 LVS 排障的八成。

**3. 器件提取**——几何 → 带端子的器件实例:

```ruby
extract_devices(mos4('sg13_lv_nmos'),
                { 'SD' => nsd_fet,     # 源漏扩散
                  'G'  => ngate_lv,    # 栅区(有源 & 多晶)
                  'tS' => nsd_fet,     # 端子识别层
                  'tD' => nsd_fet,
                  'tG' => poly_con,
                  'W'  => pwell })     # 体/阱(第 4 端)
```

官方提取器类:`mos3/mos4`、`dmos3/dmos4`、`resistor(名, 方阻)`、
`capacitor(名, 单位面积电容)`、`diode`、`bjt3/bjt4`。提取器从几何算出
器件参数(W、L、A、P)——后面 `tolerance(...)` 比的就是它们。

**4. 原理图 + 对齐 + 归约**——读参考网表,比较前把两侧规范化:

```ruby
schematic('my_block.cir')       # SPICE 参考
align                           # 摊平只在一侧存在的 cell
netlist.simplify                # 正典归约
netlist.combine_devices        # 合并串/并联器件(多指!)
netlist.make_top_level_pins
netlist.purge                   # 丢浮空/未用
max_res(1e9)                    # 忽略寄生级极端元件
min_caps(1e-18)
```

**5. 比较 + 逃生口**:

```ruby
success = compare && flag_missing_ports   # 严格:顶层端口必须有标签
tolerance('sg13_lv_nmos', 'W', absolute: 5.nm)   # 参数比较容差
same_nets('TOP', 'VDD', 'VDD!')           # 声明网等价
equivalent_pins('MY_MACRO', 'A', 'B')     # 可互换引脚
blank_circuit('SRAM_*')                   # IP 黑盒:只比接口
```

**6. 判定**——`compare` 返回布尔;产线 runner 打出无歧义的 PASS/FAIL
行、不匹配时非零退出。`report_lvs(路径)` 写出交叉探查数据库(KLayout
Netlist Browser 打开)。

层次化注记:产线 LVS 跑 `deep`——cell 内器件提取一次而非按实例重复,
比较器按 circuit 工作。`blank_circuit` 黑盒也因此才可能。

---

## 5. klink 的 LVS 路径(布线流程的裁判)

klink 布出来的电路通常没有 SPICE 原理图——你有的是*声明的网*(哪些端子
属于同一节点,来自你的网表)。klink 的 `lvs_check` 把这份声明与 KLayout
对画好几何的原生提取对账:

```python
from klink.domains.structdevice.orchestrators import lvs_check

res = lvs_check(
    c, "MY_TOP",
    declared=[{"net": "n1", "terminals": ["X1.S", "X2.G"]}, ...],
    mode="lvsdb",
    connectivity=profile.connectivity_spec(),   # 导体+过孔,同一份 profile
    terminal_provider=...,                       # 每个端子的位置
    placement=..., device_terms=...,
)
assert res["ok"] and res["match"]
```

其内部用 profile 的导体/过孔清单搭建与 §4 第 2 步相同的 `connect(...)`
图,并对网络和器件都做对账——提取是 KLayout 的,路由器不能给自己打分。
`mode="lvsdb"` 写出原生 `.lvsdb`;用 `view.show_lvsdb` RPC 打开做版图 ↔
网表双向交叉探查。

需要晶体管级器件识别(真 MOS 提取、W/L 参数对 SPICE 比较)时,就是 §4
——写真正的 `.lvs` deck 用 `extract_devices`;你为 DRC 建立的派生纪律
原样沿用。

fit-device starter(本页 demo 检查的那条流程)实测:

```text
[public] FlexDR ok=True routed=94/94 markers=0
[public] LVS ok=True match=True devices=173
```

---

## 6. 案例研究:agent 只靠手册能写出这个 deck 吗?

只被人类读过的文档等于没测过。我们用验证 deck 本身的方式验证本指南的
配套手册:埋好已知违例、真值不出题人之手,让一个**小模型 agent**(我们
可用的最弱档)只带手册上场编写并运行签核 deck。手册在那里能用,就在哪
里都能用。

### 场景(可复现)

一个 cell `BUSFAB_M2_BLOCK`,三个工艺层加一个豁免标记——每处埋的违例
都是某条规则的教科书样例:

| 几何(盒,µm) | 层 | 埋点 |
|---|---|---|
| `[0,0,30,2]`、`[0,4,30,6]` | 31/0 | 合法线(宽 2.0、间距 2.0) |
| `[28,0,30,2]` + cut `[28.5,0.5,29.5,1.5]` | 33/0 + 32/0 | 合法 via(enclosure 0.5) |
| `[28,4,30,6]` + cut `[28.2,4.2,29.8,5.8]` | 33/0 + 32/0 | **VA.1**:enclosure 0.2 |
| `[0,7,30,9]` | 31/0 | **MA.2**:与下方线间距 1.0 |
| `[0,11,20,12.5]` | 31/0 | **MA.1**:宽 1.5 |
| `[24,11,26,13.2]` | 31/0 | **MA.4**:面积 4.4 µm²(宽度合法) |
| `[35,0,38.003,2]` | 31/0 | **MA.5**:顶点偏离 5 nm 网格 |
| `[0,16,30,21]`、`[0,23.5,30,28.5]` | 31/0 | **MA.3**:都 5.0 宽、间距 2.5、平行 30 |
| `[45,0,65,2]`、`[45,3,65,5]` | 31/0 | 间距 1.0——但**已豁免**: |
| `[44,-1,66,6]` | 63/99 | 覆盖该间隙的豁免标记 |

真值在任何 agent 看到题目之前,由出题人自己的 deck(§2 idiom)**先行**
确立:MA.1=1、MA.2=1(豁免对必须不出现;不豁免则为 2)、MA.3=1、
MA.4=1、MA.5=2、MB.1=MB.2=0、VA.1=3+4 个边标记——共 13。

### 给 agent 的任务上下文(原文)

agent 拿到的只有手册路径、MCP `drc_run` 工具、和下面这段 DRM 风格摘录
——没有语法提示,不透露埋了什么、有多少违例:

```text
Layers: metalA = 31/0, viaA cut = 32/0, metalB = 33/0, waiver marker = 63/99
MA.1  Min. metalA width: 2.0 µm
MA.2  Min. metalA space: 2.0 µm. Violations lying entirely inside a
      waiver-marker (63/99) region are pre-approved and must not be
      reported. The waiver applies to MA.2 ONLY.
MA.3  Two metalA lines that are BOTH wider than 4.0 µm and run in parallel
      for more than 5.0 µm must be spaced >= 3.0 µm.
MA.4  Min. metalA polygon area: 5.0 µm².
MA.5  All metalA geometry must be on the 0.005 µm manufacturing grid.
MB.1  Min. metalB width: 2.0 µm
MB.2  Min. metalB space: 2.0 µm
VA.1  viaA cut must be enclosed by BOTH metalA and metalB by >= 0.5 µm.

Write ONE DRC deck implementing exactly these rules with the DRM rule IDs
as the report categories, run it, report every category with its count
(including zeros) and your verdict per the handout's verdict rules.
```

### 小模型 agent 交出了什么

首跑、零重试、五次工具调用。deck 遵循了手册的产线骨架(规则号类别、
每规则 `.forget`、`deep`、所有尺寸带小数点),两条难题也写对了:

```ruby
# MA.3 —— 半宽推导是它自己做的("宽于 4.0" -> sized ±2.0)
wide_metalA = metalA.sized(-2.0).sized(2.0)
r = wide_metalA.space(3.0, projection_limits(5.001, nil))
r.output("MA.3", "...")

# MA.2 —— 豁免只作用于这一条规则
r = metalA.space(2.0)
r = r.outside(waiver)
r.output("MA.2", "...")
```

结果:**13/13——每个类别的计数与保留的真值完全一致**(MA.1=1、MA.2=1
且豁免对被正确吸收、MA.3=1、MA.4=1、MA.5=2、MB 双零、VA.1 合并 7),
判定 FAIL 并引用手册判定规则原文。与手册的一处偏差:它在检查结果上直接
`.outside(waiver)` 而没有 `.polygons`;引擎接受该形式,且在本场景给出了
正确的豁免后计数——可接受的变体,如实记录于此。

### 这个练习抓到了什么(为什么你应该重复它)

同一场景的**第一次**运行败得很有价值:当时手册展示的是发布 deck 的 PRL
形式 `metal.sep(wide, v, projection_limits(...))`,在我们的 KLayout
0.30.x 上它对埋好的宽-宽违例**不触发**——已验证的 `wide.space(...)`
形式才能抓到(§2.5)。带埋点违例的盲测,恰好能抓出"能解析但不咬人"的
文档化 idiom。这套配方可重复且便宜:

1. 画一个场景,每条规则埋一个教科书违例,外加合法的相似结构和一个
   豁免用例;
2. 先跑**你自己的** deck,把计数冻结为真值;
3. 只给 agent 手册 + DRM 摘录——永远不给真值;
4. 逐类判分;对埋了违例的场景报"全净",说明坏的是文档(或 idiom),
   不是版图。

## 7. 家规(它们让门保持诚实)

1. **全有或全无。** DRC 零违例且无异常才过;LVS `match=True` / `compare`
   为真才过。没有"差不多"。
2. **绝不为了变绿而删规则。** 用豁免层、`exclude_around`、`layers=[...]`
   限定范围,理由写在旁边——没有声明的豁免是签核里的暗洞。
3. **规则号和数值是数据,不是散文。** 类别按你的 DRM(或 profile 字段)
   命名,数字集中一处,描述里嵌数值,报告自解释。
4. **修几何或修声明,不修裁判。** 真发现意味着画的或声明的网表错了;改
   deck 是最后手段,且要写下理由。
5. **只认结构化证据。** 判定来自 RPC 结果和报告数据库,不是截图。
6. **读大师之作。** IHP SG13G2 / GF180MCU / SKY130 的 KLayout deck 是开源
   且产线级的;对某个 idiom 拿不准时,先去那里找,再考虑自己发明。
