# 控制面

> English: [control-plane.md](control-plane.md)

klink 的控制面是一份目录、两副面孔:类型化 Python 客户端(`KLinkClient`),
以及同一批方法作为 MCP 工具暴露给 agent。两者都从 live server 的方法注册表
生成——没有第二份手工维护、会漂移的工具清单。

## 靠查询,不靠背

方法目录永远在线查询,从不硬编码:

```python
from klink import KLinkClient

with KLinkClient() as c:
    print([m["name"] for m in c.methods()["methods"]])
```

MCP 一侧,`tools/list` 列出全部工具,**`klink.find_tools`** 负责导航:

- 不带参数 → domain 索引(每个领域一行)
- `domain=<token>` → 该领域的工具 + 详细用法说明
- `query=<keywords>` → 跨全部工具的排序匹配

领域按索引顺序:`connection_and_view`、`multi_session_transfer`、
`geometry_authoring`、`selection_and_send_memory`、`ports_and_anchors`、
`routing_backends`、`drc_and_lvs_verification`、`device_structdevice`、
`device_nanodevice`、`device_photonics`、`escape_hatch`。

`--profile` 沿两个正交轴过滤 MCP server 暴露的工具——**意图**(`read` /
`write` / `verify` / `escape` / `all`)和**领域**(上面任意 token)。默认
`read,write,verify,escape`;传领域 token 则收窄到该领域。`klink.status`
报告当前解释器、检测到的可选能力和 KLayout 连接状态。

## 读状态——用几何,不用像素

读取面就是 agent 的证据:`layout.info`、`cell.list` / `cell.tree`、
`layer.list`、`shape.query`、`instance.query`、`pcell.libraries` /
`pcell.list` / `pcell.info`、`selection.get`。截图(`view.screenshot`)只是
用户主动要的产物,永远不是验证步骤。

## 用批量 RPC 写入

生成式版图绝不要一个对象一次 RPC——那个循环每次调用都付 TCP、JSON、分发、
事务、GUI 记账的成本,常常慢几百倍。用批量方法:

| 工作负载 | 首选 RPC |
|---|---|
| 一个 cell/layer 上的大量 box | `shape.insert_boxes` |
| 一个 cell 里的混合形状 | `shape.insert_many`(`box`、`polygon`、`path`、`text`) |
| 大量子 cell 实例 | `instance.insert_many` |
| 大量 Basic/库 PCell 实例 | `instance.insert_pcell_many` |

单个插入方法留给调试单个对象。编辑包在事务里;`edit.undo` / `edit.redo` /
`edit.status` 对事务操作。

## 验证

`drc.run` 和 `lvs.run` 跑 KLayout 原生检查;`structdevice.*` 工具为自定义
器件流程加 netlist 驱动的 LVS。布线工具返回结构化报告(`ok`、obstacle
hits、sibling overlaps)——只有报告说成功,一条路由才算成功。

## 逃生舱——最后用,不是先用

`exec.python` 在 KLayout 里运行受控 `pya` 片段,用于没有类型化 RPC 覆盖的
情况;它仍会调度录制和版图 diff 检测。`events.channels` /
`events.subscribe` 暴露插件事件流。只要有类型化 RPC 就优先用——它校验输入、
返回带 `next_action` 的结构化错误,而且对录制器保持"意图"可见,不是一段
不透明代码。
