# 架构

> English: [architecture.md](architecture.md)

## 三层

| 层 | 是什么 | 形态 |
|---|---|---|
| **机制层(冻结)** | `klink`(客户端 + 算法 + MCP)和 KLayout 插件 | 安装包(pip + salt)。用户从不编辑。 |
| **开发仓** | 测试、录制、开发示例、设计文档 | 开发仓库本身。不发给用户。 |
| **用户项目** | 你的 `pdk.py` + `custom_devices/` + specs + 输出 | 你自己的目录;agent 在这里写。 |

用户唯一可编辑的面是**用户项目**。机制层是安装包,边界由打包保证,不靠
自觉。

## 控制路径

```
Agent (Claude Code / Codex)
  → MCP server (python -m klink.mcp)        把 klink RPC 暴露成 MCP 工具
  → klink client  (NDJSON over TCP)
  → KLayout 插件(进程内 RPC server)         端口 8765 (RPC) + 8082 (klive 兼容)
```

**端口是等能力的会话。** 每个 KLayout 窗口绑定一个端口(8765、8766、…);
每个端口都是完整、独立的会话。没有哪个端口有"工作"或"LVS"角色——你
要哪个会话就显式指哪个。

## Process purity(工艺纯净)

`klink` 只发机制,**零工艺数据**——没有硬编码的层号、器件、DRC 数值或 PDK
实例。每个工艺事实都住在用户项目的 `pdk.py` 里,**显式**传进 klink API。
带起一个新工艺 = 写一份 `pdk.py`;你从不编辑 `klink`。不带工艺调用 klink
工具会得到一个**指导性错误**,指名下一步该做什么,而不是悄悄给默认值。

## Agent 设计——三层

agent 指引不只是某个 harness 配置文件里的几段话:

1. **工具级强制(最强,与 harness 无关)。** 工具错误就是指令(带
   `next_action`),编辑先校验再落笔,状态持久化在磁盘。弱的或不守规矩的
   agent 也会被*工具本身*引导对——拒绝坏输入并给出修法。这是真正的安全网。
2. **harness 中立的单一事实源。** lane 定义、recipe、工具设计契约放在中立
   文件里,不塞在某一个 harness 的配置里。
3. **每 harness 薄适配层。** `CLAUDE.md`(Claude Code)和 `AGENTS.md`
   (Codex)是第 2 层对单个 harness 的薄转写。

所以项目的 agent 规则应写在 `AGENTS.md`(中立),`CLAUDE.md` 指向它——但
持久的保证来自第 1 层。
