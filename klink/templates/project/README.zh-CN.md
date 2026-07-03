# 你的 klink 项目

这是一个 **klink 用户项目**脚手架。你通过跟 AI agent(Claude Code / Codex)
对话来驱动它;agent 把**你所在领域**的版图生成代码写进这个项目。你不改 klink 本身。

> English: [README.md](README.md)

## 你改什么 vs 装好的别动

| 你改(本项目) | 装好的,永不改 |
|---|---|
| `pdk.py` —— 你的工艺(层/via/尺寸) | `klink`(pip 包:机制 + 算法 + MCP) |
| `custom_devices/` —— 你的 build 脚本(agent 写) | klink **KLayout 插件**(用 KLayout 包管理器装) |
| `specs/` —— 你的 `.klink` spec | |
| `out/` —— 生成的 GDS/结果 | |

klink 只发布**机制**、**零工艺数据**。所有工艺事实(层号、器件库、via 栈、DRC
数值)都住在本项目的 `pdk.py`,你的 build 代码把它**显式传入** klink API。

项目还自带只读的 **`example_template/`** —— 可直接跑的**自包含**示例脚本(神经电极、EBL
纳米器件、Hall bar),只 import `klink`(无工艺文件、无 PDK)。先原样跑一个,再拷进
`custom_devices/` 改成你的。

## 怎么开始一个项目

你**不用**一开始就选领域。**告诉 agent 你在做什么**("我做 EBL 纳米器件" /
"神经电极探针" / "从 Verilog 做数字块" / "开源或自有 PDK 上的硅光电路")。
agent 会:

1. 访谈你、识别领域,
2. 从对应 **recipe**(见 [`recipes/README.md`](recipes/README.md))起
   `pdk.py` + 第一个 `custom_devices/` 脚本,
3. 跑通并用结构化几何/LVS 查询验证结果。

你描述的领域**就成为**这个项目的默认项目——没有写死的默认。

## 安装

1. `pip install klayout-klink`(装进运行 MCP server 的同一个 Python)。
2. 用 KLayout 包管理器装 klink 插件,然后启动 KLayout。
3. 把 `mcp.example.json` 拷进 agent 的 MCP 配置并改路径。
4. 用 agent 打开本文件夹,描述你要做什么。

## 几何从哪来(分 recipe)

各 recipe 需要的版图数据不同,别假设都要你的私有文件:

- **自包含**(EBL、神经):全部由 `pdk.py` + 代码生成,无外部依赖。
- **开源或自有**(硅光):可直接在**开源 gdsfactory PDK** 上开箱跑,也可以指向
  你自己的**专有 foundry PDK**。
- **自带**(P&R):需要一份**你自己的、机密的**晶体管版图。

模板**绝不发布 GDS**,你也**绝不要**把专有 GDS/PDK 提交到这里。用到私有几何的
recipe 只搭好**代码**;运行时你指向自己的文件。开源 PDK 可以依赖,但同样不该进
版本控制。
