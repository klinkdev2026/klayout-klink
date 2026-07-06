# 快速上手

klink 让 AI agent(Claude Code / Codex)驱动 KLayout 版图编辑器:画形状、放
PCell、布线、跑 LVS——用你的工艺,通过你的 agent。

> English: [getting-started.md](getting-started.md)

## 安装

1. **KLayout + klink 插件**:先安装 KLayout(桌面版下载:
   <https://www.klayout.de/build.html>),再把仓库的 `klink_plugin/` 文件夹拷进
   KLayout 的 `salt/` 目录(具体命令见 README 的"安装 KLayout 插件"一节),启动
   KLayout。插件跑一个进程内 RPC server。
2. **`pip install klayout-klink`**:装进一个 Python(称它*klink 解释器*)。

   ```bash
   pip install klayout-klink
   ```

   klink 的两个 Rust 内核都以预编译轮发布(Linux/macOS/Windows、CPython 3.10–3.13),
   且都是运行时依赖,所以这一条命令就把 klink + 两个加速核(单栈 + 多层 P&R)全装好。
   它们只管速度——有纯 Python 兜底,`pip install --no-deps klayout-klink` 只装纯 Python
   核。不打包任何第三方库——硅光 recipe 额外需要**同一个** Python 里有 `gdsfactory`
   (`pip install "klayout-klink[photonics]"` 会装一个已测版本)。
3. **把 klink MCP server 注册进你的 agent,然后重启 agent。** klink 自带这个 server;
   唯一因 agent 而异的是**怎么登记**。让 klink 替你把精确命令写好:

   ```bash
   klink-mcp --register
   ```

   它会打印 **Claude Code、Codex、Cursor、Windsurf、VS Code、Zed** 的可复制注册方式
   ——外加 Claude Desktop、Trae、Cline 等多数 MCP agent 都吃的标准 `mcpServers` JSON
   块(具体配置文件位置见你 agent 自己的文档)——而且**你的 klink 解释器路径已自动
   填好**(这正是 agent 最容易填错的地方)。比如 Claude Code 和 Codex 各一行:

   ```bash
   claude mcp add klayout -- <klink-python> -m klink.mcp --profile read,write,verify,escape --session-id project-klink
   codex  mcp add klayout -- <klink-python> -m klink.mcp --profile read,write,verify,escape --session-id project-klink
   ```

   **然后重启你的 agent**——MCP server 是 agent 启动时加载的,运行中的会话不重启就
   看不到它。之后 `klink.status` 会报解释器和能力供你核对。

## 版本支持

| 组件 | 底线版本 | 说明 |
|---|---|---|
| `klayout`(pip) | >= 0.28 | 不支持 0.27:klink 的器件抽取用到的 `GenericDeviceExtractor` 重载,pya 自己的文档写明是 0.28 才引入的。 |
| `gdsfactory`(`[photonics]`) | >= 9.0, < 10 | 9.x 全线覆盖。gdsfactory 8.x 在测试中能跑,但还未纳入 pin——当作实验性支持。 |
| KLayout 应用程序本体 | 尚未实测底线 | 插件在 GUI 内的 pya 接口(区别于 `klayout` pip 模块)的版本覆盖计划放到后续阶段。 |

> **跑例子不需要 MCP。** 每个例子都是普通 `python -m ...` 脚本(本页各处都有确切命令),
> 直接通过插件端口和 KLayout 对话——装好 klink 跑就行,不需要 MCP。MCP 是让你的
> *agent* 把 klink 当**常驻工具**调用的那一层(比反复重跑脚本更快更顺)。两条路都用
> 同一个 `pip install klayout-klink`。

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

公开示例都不需要你提供几何。其中八个是**starter**、随 wheel 打包、按类分组,
`pip install` 用户直接从铺好的 `example_template/` 跑
(`python example_template/<类>/<名>.py`):`nanodevice/`(ebl_wraparound、
hallbar、neural_electrode)、`photonics/`(gf_mzi_module)、`passives/`
(idc_capacitor、spiral_inductor、saw_idt_filter、baw_fbar_planview)。另外四个
数字 P&R demo(fit-device → P&R → LVS、手写网表 → P&R、多层 P&R、针卡 padframe)
读内置网表且互相 import,只能从仓库克隆里跑。每个 demo 的确切命令和实测输出见
[demos.zh-CN](demos.zh-CN.md)——**跑起来都不需要 MCP**。
