# 用 klink 做 DRC 和 LVS

如何对一个 live KLayout 会话编写并运行设计规则检查(DRC)和版图-原理图
对比(LVS)——手写、从 `ProcessProfile` 自动推导、或交给 agent 来做。

> English: [drc-lvs.md](drc-lvs.md)
> 要发给 agent?给它精简版配方:[drc-lvs-agent-handout.zh-CN.md](drc-lvs-agent-handout.zh-CN.md)

两种检查都**在 KLayout 内部**执行——klink 不自己重新实现。DRC 脚本通过
`drc.run` RPC 跑在 KLayout 官方 DRC 引擎上;LVS 通过 `lvs_check` 用
KLayout 原生连通性提取。klink 提供的是传输、结构化结果,以及(对 DRC)
一个从驱动布线和 LVS 的**同一份** `ProcessProfile` 推导 deck 的生成器。

## 1. 十行看懂 KLayout DRC 语言

DRC 脚本是 KLayout 执行的 Ruby DSL "runset"。权威参考是官方手册页——
*DRC basics* 和 *DRC reference*(klayout.de → Documentation)。klink 生成的
和本页展示的一切,只用那里记载的构件:

```ruby
report("my checks")                 # 打开报告数据库(必须最先)
m1 = input(101, 0)                  # 读取 101/0 层(已合并的多边形)

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
  查 2 dbu(常见 0.001 dbu 下 ≈ 2 nm),会静默放过一切。永远写小数点。
- **`report(...)` 必须在任何 `.output("名", "描述")` 之前**——字符串形式的
  output 把违例写进 report 打开的报告数据库。
- **检查可带度量方式**:`width(2.0, projection)` 只量平行边投影(曼哈顿
  布线器实际承诺的语义);`euclidian`(默认)还会斜跨直角量,在致密的
  格点布线金属上会报直角拐角伪影。两者都是官方的;要有意识地选。

## 2. 通过 klink 跑 deck

`drc.run` 在 live 会话里执行脚本并返回结构化结果——stdout、异常(或
`None`)、以及(在要求报告数据库时)违例摘要:

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

`$output_rdb` 由服务端用 `output_rdb` 参数替换,脚本和 Python 调用永远
指向同一个文件。判定纪律是全有或全无:**只有 `exception` 为 `None` 且
`total_items` 为 0 才算过。** 报错的 deck 不算过;非零计数是发现,不是噪声。

## 3. 同源 deck:从你的 ProcessProfile 推导 DRC

如果你已经在用 `ProcessProfile` 布线,上面那种 deck 不用手写——profile
知道那些数字:

```python
from klink.routing.grid.profile_drc import run_drc

res = run_drc(c, profile)               # 每个布线层的 width/space +
print(res["ok"], res["total"])          # 每个 via 的 cut enclosure,
for cat in res["categories"]:           # 全部来自路由器和 LVS 读的
    print(cat["name"], cat["count"])    # 同一份 profile
```

生成的规则(用 `profile.drc_script()` 可直接查看):

| 规则 | 值 | 来源字段 |
|---|---|---|
| 每布线层最小线宽 | ≥ `wire_width_um` | 绘制线宽 |
| 每布线层最小间距 | ≥ `wire_clear_um` | 异网净空 |
| 每 via 的 cut 在上下金属内的 enclosure | ≥ `litho_tol_um` | via cut 内缩 |

实践中重要的两个旋钮:

- `metrics="projection"` 是默认——它检查的正是格点布线器承诺的东西。要
  更严格的 fab 式量法就换 `euclidian`,并预期致密金属出直角拐角类发现。
- `exclude_around=(层, 外扩µm)` 抑制触碰器件区(从标记层如 profile 的
  channel 层外扩得到)的 width/space 标记。器件内部几何——比如小于布线
  净空的源漏间隙——归*器件*规则管,不归布线规则管;真实 PDK 的金属
  deck 也是这样限定范围的。via enclosure 检查永不豁免。豁免要写在你的
  example 里,让 review 的人看得见。

端到端可跑的证明(正对照、负对照、以及对 fit-device starter 版图跑全
deck):

```bash
python -m examples_klink.public.features.profile_drc_gate --port <会话端口> [--check-demo]
```

live 会话实测输出:

```text
[positive control] legal scene: ok=True violations=0
[negative control] bad scene: violations=1 fired=['space_21_0']
[demo gate] DEMO_ADD4: ok=True violations=0
RESULT: PASS (deck passes legal geometry, catches the planted violation)
```

至此,一份 profile 实例喂三道门:路由器照它画、DRC deck 照它量、LVS 照
它提取——同一组数字、同一组层,构造上保证一致。

## 4. LVS:声明的网 vs 提取的几何

klink 的 LVS 流程对比你*声明*的(哪些端子属于同一电气节点)和 KLayout
原生提取器从画好的几何里*找到*的:

```python
from klink.domains.structdevice.orchestrators import lvs_check

res = lvs_check(
    c, "MY_TOP",
    declared=[{"net": "n1", "terminals": ["X1.S", "X2.G"]}, ...],
    mode="lvsdb",
    connectivity=profile.connectivity_spec(),   # 还是同一份 profile
    terminal_provider=...,                       # 每个端子的位置
    placement=..., device_terms=...,
)
assert res["ok"] and res["match"]
```

- `connectivity` 声明哪些层导电、哪些 via cut 桥接它们——由同一份
  profile 推导(`connectivity_spec()`),裁判读的工艺声明和路由器一致。
  但*提取本身*是 KLayout 的,不是 klink 的:路由器不能给自己打分。
- `mode="lvsdb"` 还会写出原生 `.lvsdb`;用 `view.show_lvsdb` RPC 打开,
  KLayout 的 Netlist Browser 支持版图 ↔ 网表双向交叉探查。
- 门是 `match=True`——网络和器件都对上。其余任何结果都是要排查的失败,
  不是可以解释过去的东西。

fit-device starter(本页 demo 检查的那条流程)的实测输出:

```text
[public] FlexDR ok=True routed=94/94 markers=0
[public] LVS ok=True match=True devices=173
```

## 5. 家规(它们让门保持诚实)

1. **全有或全无。** DRC 零违例且无异常才过;LVS `match=True` 才过。没有
   "差不多"。
2. **绝不为了让 run 变绿而删规则。** 用 `exclude_around`、`layers=[...]`
   限定范围,并在 example 里写明原因——没有声明的豁免就是签核里的暗洞。
3. **修几何或修声明,不修裁判。** 真发现意味着画的或声明的网表错了;改
   deck 是最后手段,且要写下理由。
4. **只认结构化证据。** 上面的数字全部来自 RPC 结果,不是截图;截图给
   要看的人看,永远不用于验证。
