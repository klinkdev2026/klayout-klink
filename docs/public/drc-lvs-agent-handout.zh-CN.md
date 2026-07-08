# Agent 手册:通过 klink 编写 KLayout DRC 和 LVS

> English: [drc-lvs-agent-handout.md](drc-lvs-agent-handout.md)
> 给人读的完整教程是 [drc-lvs.zh-CN.md](drc-lvs.zh-CN.md)。本页是为
> **整篇粘进 agent 上下文**而写的,自包含。

你正在通过 klink(MCP 工具或 `KLinkClient` Python 客户端)操作一个 live
KLayout 会话。严格按本配方执行。不要发明 DRC 方法——下面的白名单就是你
可用词汇的全部;每一条都有 KLayout 官方 DRC 参考文档记载,并在 live 会话
上验证过。

## 压倒一切的规则

1. **尺寸永远写小数点。** `width(2.0)` = 2 µm。`width(2)` = 2 个*数据库
   单位*(≈ 2 nm)——它放过一切、什么都不告诉你。写了整数尺寸就是犯错。
2. **`report("名字")` 永远是 deck 第一行。** `.output("类别", "描述")` 把
   违例写进它;output 在 report 之前会失败。
3. **判定全有或全无。** DRC 只有响应里 `exception` 为 `None` **且**
   `total_items` == 0 才算过。LVS 只有 `match=True` 才算过。其余情况绝不
   报成功;绝不为了变绿而放宽规则。必须限定范围时,明说并给理由。
4. **绝不用截图验证。** RPC 结果就是证据。
5. 需要白名单之外的构件时,停下来问,或先跑一个两行的探针 deck 在
   live 引擎上确认语法。

## DRC 构件白名单

| 构件 | 含义 | 示例 |
|---|---|---|
| `report("名")` | 打开报告数据库(第一行) | `report("checks")` |
| `report("名", $output_rdb)` | 同上,服务端替换 rdb 路径 | — |
| `input(L, D)` | 读 L/D 层(已合并多边形) | `m1 = input(101, 0)` |
| `.width(x.0 [, 度量])` | 最小线宽 | `m1.width(2.0, projection)` |
| `.space(x.0 [, 度量])` | 最小间距 | `m1.space(2.0, projection)` |
| `a.enclosed(b, x.0)` | a 必须内缩于 b ≥ x | `cut.enclosed(m1, 0.5)` |
| `a.enclosing(b, x.0)` | a 必须外扩超过 b ≥ x | — |
| `a.separation(b, x.0)` | 两层间最小距离 | — |
| `.sized(x.0)` | 区域外扩 | `dev = input(29,0).sized(10.0)` |
| `.polygons` | 错误标记 → 多边形 | `errs = m1.space(2.0).polygons` |
| `.outside(区域)` | 只保留完全在区域外的标记 | `errs.outside(dev)` |
| `.output("类别", "描述")` | 违例入库归类 | — |
| `&`、`.and()`、`.not()` | 层间布尔 | `gate = active & poly` |
| 度量名 | `projection`(只量平行边)、`euclidian`(默认,含拐角斜量)、`square` | — |

## 配方:跑一个 DRC deck

1. 先查层(`layer.list` / `layer_list`)——绝不猜层号。
2. 写 deck:`report(...)` 第一行,每条规则一个 `.output(...)`,所有尺寸
   带小数点。
3. 带报告文件运行并读摘要:

```python
res = client.drc_run(deck, output_rdb="<路径>.lyrdb", result_mode="summary")
# 门:
ok = res["exception"] is None and res["rdb_summary"]["total_items"] == 0
```

4. 有违例时:逐类报出名称和数量;**不要**改小数字重跑。用形状查询排查
   几何。
5. `projection` vs `euclidian`:格点曼哈顿布线按路由器承诺的语义判——
   `projection`。被要求更严格的 fab 式量法时用 `euclidian`,并预期直角
   拐角类发现。

## 配方:profile 推导 deck(有 ProcessProfile 时优先)

项目在用 `ProcessProfile` 布线时不要手写规则——生成器从路由器和 LVS 读
的同一份 profile 推导:

```python
from klink.routing.grid.profile_drc import run_drc
res = run_drc(client, profile)          # 可选:
#   metrics="euclidian"
#   exclude_around=(profile.channel_layer, <外扩µm>)   # 器件区
assert res["ok"], res["categories"]
```

`exclude_around` 是把 width/space 从器件内部几何上移开的**唯一**被认可
方式(器件间隙归器件规则管,不归布线规则管)。用它时必须写明豁免层和
外扩量。via enclosure 检查永不豁免。

带正、负对照的可跑参考(跑一遍就知道健康的门长什么样):

```bash
python -m examples_klink.public.features.profile_drc_gate --port <端口>
```

## 配方:LVS

```python
from klink.domains.structdevice.orchestrators import lvs_check
res = lvs_check(client, top_cell,
                declared=declared_nets,                  # [{"net": ..., "terminals": [...]}]
                mode="lvsdb",
                connectivity=profile.connectivity_spec(),
                terminal_provider=..., placement=..., device_terms=...)
ok = res["ok"] and res["match"]
```

- `declared` 来自给你的网表——绝不捏造网。
- `connectivity` 来自 profile——有 profile 时绝不手写导体/过孔清单。
- `match` 为 False 时:报出哪些网/器件不匹配(结果里带);修的是几何或
  声明,不是 connectivity spec。
- `mode="lvsdb"` 会写出数据库,人可用 `view.show_lvsdb` 工具打开——
  主动提供,不要截图。

## 工具报错时

klink 的错误就是指令:读 message 和 `next_action` 字段,照做(通常是缺
参数、缺依赖并给出精确 pip 命令、或 live 会话前置条件)。不要原样重试,
也不要绕过被点名的前置条件自创变通。
