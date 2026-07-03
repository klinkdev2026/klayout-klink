# 快速上手

klink 让 AI agent(Claude Code / Codex)驱动 KLayout 版图编辑器:画形状、放
PCell、布线、跑 LVS——用你的工艺,通过你的 agent。

> English: [getting-started.md](getting-started.md)

## 安装

1. **KLayout + klink 插件**:先安装 KLayout(桌面版下载:
   <https://www.klayout.de/build.html>),再把仓库的 `klink_plugin/` 文件夹拷进
   KLayout 的 `salt/` 目录(具体命令见 README 的"安装 KLayout 插件"一节),启动
   KLayout。插件跑一个进程内 RPC server。
2. **`pip install klayout-klink`**:装进将运行 MCP server 的那个 Python。这会带上
   klink + 它自己的 Rust 内核(Linux/macOS/Windows、CPython 3.10–3.13 预编译轮);
   不打包任何第三方库。硅光 recipe 额外需要**同一个** Python 里有 `gdsfactory`,自己装。
3. **配置 agent 的 MCP server** 启动 `python -m klink.mcp`(`klink init` 会写一份
   样例配置 `mcp.example.json`——见下)。

MCP 配置里的 `command` Python 必须是装了 klink(用硅光则还要 gdsfactory)的那
个。`klink.status` 会报解释器和能力供你核对。

## 你的第一个可跑结果(无需 GDS)

EBL 纳米器件 recipe **完全离线**就能跑——不需要 KLayout,不需要外部几何:

```bash
python -m examples_klink.public.demos.ebl_wraparound
```

实测输出(节选):`"ok": true`,40 电极,12 patch,writefield 16 fields / 11
windows / 20 crossings / **0 violations**、**0 overlaps**。

KLayout + 插件起来后,神经电极 recipe 生成并布线一个探针(无 GDS,只用 live 的
Port/Anchor PCell):

```bash
python -m examples_klink.public.demos.neural_electrode --port <会话端口> --elec-rows 4
```

实测输出(节选):`ok: True`,48 端口,24 net 全布通(12 条 `1/0` + 12 条
`3/0`),**sibling-overlap 0**、**obstacle-hit 0**。

> KLayout 的"端口"只是一个会话——任意端口都行,没有专用角色。用空的或测试用的
> 会话,别用你手动工作的标签页。

## 开始你自己的项目

用自带 CLI 起脚手架,再用 agent 打开:

```bash
klink init my-chip
```

它会写出 `pdk.py`、`custom_devices/`、`recipes/`、`example_template/`、agent 规则和一份样例
MCP 配置。描述你要做什么,agent 识别领域并从对应 recipe 起 `pdk.py` + `custom_devices/`
脚本。见
[project-model](project-model.md) 与 [recipes](recipes.md)。

## 什么能开箱跑

四个公开 demo 全部不需要你提供几何——两个完全离线(EBL wraparound、Hall
bar),两个对 live KLayout 会话跑(神经电极 harness、fit-device → P&R →
LVS,用合成 exemplar)。每个 demo 的确切命令和实测输出见
[demos.zh-CN](demos.zh-CN.md)。
