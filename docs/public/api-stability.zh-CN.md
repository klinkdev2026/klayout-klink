# API 稳定性

> English see [api-stability.md](api-stability.md)

klink 目前是 **0.x 软件(alpha)**。本页精确说明这对兼容性意味着什么,方便你决定
版本锁定的松紧。

## 版本语义

- **次版本号变化(0.1 -> 0.2)可能破坏公开 API。** 0.x 的次版本是 klink 允许
  在设计需要时重塑某个已文档化的接口面的地方。
- **修订号变化(0.1.1 -> 0.1.2)绝不破坏已文档化的行为。** 修订版只包含
  bug 修复、文档和纯增量变更。
- 如果你想要修订级更新但不希望遇到意外破坏,把依赖锁定在某个次版本上
  (`klayout-klink>=0.1,<0.2`)。

## 什么算公开 API

以下接口面受上述保证约束:

- `klink` **命令行命令**(`klink init`、`klink update`、`klink plugin
  install`、`klink plugin status`、`klink-mcp --register`……)。
- `KLinkClient` 的**已文档化方法**(`docs/public/` 和客户端自身 docstring
  中描述的 RPC 封装调用)。
- `tools/list` 公布的 **MCP 工具名称和参数**(可通过 `klink.find_tools`
  导航)。
- `ProcessProfile` / `ConnectivitySpec` / `StackSpec` 的**构造字段和
  已文档化方法**——你的 `pdk.py` 所依赖的机制类。
- **落盘契约**:`klink.spec.json` v1、`interaction_context.jsonl`,以及
  SEND journal 格式。
- **插件 RPC 线协议**,由 `PROTOCOL_VERSION` 标注版本,并在客户端与插件
  握手时校验。

## 什么不算公开 API

以下内容不享受任何兼容性保证:

- 任何以 `_` 开头的名字(无论是模块级还是属性级)。
- `docs/public/` 未文档化的模块结构和导入路径——内部重组不算破坏性变更。
- Rust 内核 crate(`klink_boxmaze`、`klink_trackmaze`)的内部实现——它们是
  已文档化 Python API 背后的字节对齐实现细节,只要输出保持字节一致,
  可以自由重构。
- `example_template/` 及其它 starter/示例内部实现——这些设计上就是给你
  "抄了就改"的,不是可依赖的库。

## 弃用流程

1. 被弃用的命令行命令、客户端方法、MCP 工具或落盘字段,在宣布弃用的版本
   之后**再保留一个次版本**的可用期。
2. 在这一版本期间,它会给出警告,明确指出替代方案。
3. 下一个次版本才可能真正移除。移除项会记录在 `CHANGELOG.md` 的
   **Breaking** 标题下。

## 线协议

Python 客户端和 KLayout 插件在连接时基于 `PROTOCOL_VERSION` 握手。
`PROTOCOL_VERSION` 的变化永远算破坏性变更,并记录在 `CHANGELOG.md` 的
**Breaking** 标题下——这意味着旧插件无法服务新客户端,反之亦然,双方
都需要一起升级。

`klink.doctor` 会报告客户端的协议版本,连接成功后还会报告插件的协议版本,
这样版本不匹配在造成困惑的失败之前就能被看见。
