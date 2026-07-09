# klink

<p align="right">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">中文</a>
</p>

klink 是一个面向 [KLayout](https://www.klayout.de/) 的 AI-native 控制面。它把正在运行的 KLayout GUI 变成一个可由外部 Python 进程、MCP 客户端和 AI agent 稳定操控的版图编辑内核。

📖 **文档网站**（中文；English 见 `/en/`）：
<https://klinkdev2026.github.io/klayout-klink/>

**图文分步教程** —— 每篇都是一个 demo 的真实运行，在 KLayout 里逐阶段画出来，每一步配截图和可运行代码：

- [Hall bar](https://klinkdev2026.github.io/klayout-klink/tutorial-hallbar.html) —— geometry-first 入门
- [EBL wraparound](https://klinkdev2026.github.io/klayout-klink/tutorial-ebl-wraparound.html) —— writefield 约束
- [神经电极阵列](https://klinkdev2026.github.io/klayout-klink/tutorial-neural-electrode.html) —— 参数化阵列 + 批量 RPC
- [gdsfactory MZI](https://klinkdev2026.github.io/klayout-klink/tutorial-gf-mzi.html) —— gdsfactory 桥 + 拖动后重布线
- [自定义器件 → P&R → LVS](https://klinkdev2026.github.io/klayout-klink/tutorial-fit-device.html) —— 把你自己的器件接入数字布局布线流
- [针卡 padframe](https://klinkdev2026.github.io/klayout-klink/tutorial-padframe.html) —— pad 环 + 电源网络，两种卡模式
- [无源器件模板](https://klinkdev2026.github.io/klayout-klink/tutorial-passives.html) —— IDC / 螺旋电感 / SAW / BAW

项目分为三层：

- `klink`: 外部 Python 客户端、MCP bridge、路由/领域逻辑和 agent 工作流。
- `klink_plugin`: 运行在 KLayout 里的薄 RPC 插件，负责暴露选定的 `pya` 和 GUI 操作。
- `examples_klink/public`、`tests/public`、`docs/public`: 示例、验证和发布文档。

核心 Python 包不引入任何第三方运行依赖——唯一声明的依赖是 klink 自己的两个 Rust 加速内核(以预编译轮子发布)。虚拟环境、缓存、构建产物和本机测试输出都不属于干净 release。

## 功能概览

- 通过本机 TCP RPC 控制 KLayout，批量方法按生成式版图设计(单次调用可写入
  数千个 shape/instance/PCell)。
- 读取 layout、cell、layer、shape、view、selection 和方法元数据。
- 创建和编辑 shape、text、cell、instance、PCell、Port 和 Anchor。
- 布线:基于 Port/Anchor 标记的 tapered/steiner/damped/channel 后端,以及
  面向自定义器件电路的详细布线 → 在线 LVS 流程。
- 通过 `klink-mcp` 把 KLayout 操作暴露成 MCP 工具,用 `klink.find_tools`
  导航;受控 `pya` 片段作为逃生舱。
- 记住你 SEND 的内容:在 KLayout 工具栏发送的选区会成为持久 id,agent 可以
  据此解析"这块区域"、"我刚发的那个"。
- 一个 bridge 驱动多个 KLayout 会话,并以先试运行再提交(dry-run → commit)
  的方式在会话间搬运几何。
- 把一段工作过程——手工编辑和 RPC 编辑——录制成可回放的 Python 脚本
  (外加一个独立的 `pya` 版本)。
- 支持 gdsfactory 相关工作流，包括 Port 标记、组件放置、routing 和兼容 klive 的 `c.show()` 显示。
- 保持 KLayout 插件很薄，把复杂逻辑放在外部 Python 中运行。

## 仓库结构

```text
klink/                  Python 客户端、MCP bridge 和核心逻辑
klink_plugin/           KLayout salt 插件
examples_klink/public/  开箱可跑的公开示例画廊
tests/public/           公开测试套(无需 KLayout)
docs/public/            发布文档
rust/                   Rust 加速 crate(klink_boxmaze、klink_trackmaze)
pyproject.toml          Python 打包配置
README.md               英文 README
README.zh-CN.md         中文 README
CLAUDE.md               Claude Code 操作规则和项目上下文
LICENSE                 Apache-2.0 license
THIRD_PARTY_NOTICES.md  第三方声明
```

## 环境要求

- **KLayout** —— klink 控制的版图编辑器，所有实时功能的核心前置条件。请先从
  <https://www.klayout.de/build.html> 安装对应操作系统的标准桌面版。klink 基于
  KLayout 0.30.x 开发和测试；任何较新的官方桌面版(宏环境带 `pya` Qt 绑定)都
  应该可用。纯离线工作流(公开测试套件和离线 demo)不需要它。
- Python 3.10 或更新版本(自带的两个 Rust 内核以 abi3 稳定 ABI 轮子发布,覆盖 CPython 3.10 及更新版本)。
- 可选: Claude Code 或其它 MCP 客户端。
- 可选: gdsfactory、`klayout` Python 包、NumPy/OpenCV 或 detector 依赖，取决于具体工作流。

## 安装 Python 包

正常使用直接从 PyPI 安装发布包:

```powershell
python -m pip install klayout-klink
```

`pip install klayout-klink` 会装上 klink **以及它自己的两个 Rust 加速内核**
(`klink-boxmaze-rs` + `klink-trackmaze-rs`——都是 klink 自己的代码),以
Linux / macOS / Windows 上 CPython 3.10+ 的**预编译 abi3 轮子**形式发布。快路径
自动到位,无需额外操作。

> **如果某平台没有预编译内核轮子**(冷门 OS / 架构 / Python),pip 会回退到**从源码
> 编译**内核,这需要 Rust 工具链(rustup)。没有工具链、或只想要最轻安装时,用
> `pip install klayout-klink --no-deps` 装**纯 Python 核心**:一切照常能跑(内核有
> 纯 Python 回退),只是大规模布局布线会慢些。

**第三方**科学库(klayout、gdsfactory、numpy…)**不打包**——需要某功能时你自己装到
同一个解释器里:

```powershell
python -m pip install klayout                          # 离线 DB / LVS 提取
python -m pip install gdsfactory                       # 硅光布线
python -m pip install numpy opencv-python-headless     # nanodevice flake
python -m pip install scipy scikit-learn scikit-image  # flake 检测器
```

某功能缺哪个库时,klink 会报错直接告诉你跑哪条 `pip install`——不用提前知道。

### 从源码安装(仅开发用)

可编辑安装(`-e`)**只在你要开发 klink 本身时用**——它让包指向你的工作树,改源码
立即生效。普通用户不需要。

Windows PowerShell:

```powershell
git clone <repo-url> klink
cd klink
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

Linux / macOS:

```bash
git clone <repo-url> klink
cd klink
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

## 开一个项目

`pip install` 会带来 `klink` 命令行。用 `klink init` 把项目脚手架铺进一个
**新的空文件夹**:

```powershell
klink init mychip     # 生成 ./mychip(pdk.py、example_template/、recipes/、agent 规则、MCP 配置)
cd mychip
```

`klink init` 会拒绝非空文件夹,所以给它一个新名字。然后用你的 agent(Claude Code /
Codex)打开这个文件夹、描述你要做什么——它会从对应 recipe 补出 `pdk.py` +
`custom_devices/`。脚手架里的 `example_template/` 是可"抄了就改"的 starter demo,
按 `nanodevice/`、`photonics/`、`passives/`、`digital/` 分类;跑其中一个:
`python example_template/<类>/<名>.py`。`digital/` 家族做 live P&R + LVS,
所以需要一个运行中的 KLayout 会话(`--port`)。

以后升级 klink 时,刷新这些自带 starter 而**不动你自己的东西**(`pdk.py`、
`custom_devices/`、`.klink/`、`out/`、`specs/` 绝不改动):

```powershell
python -m pip install -U klayout-klink
klink update mychip   # 或在项目文件夹里直接跑 `klink update`
```

## 安装 KLayout 插件

前置条件：先安装 KLayout 本体——从 <https://www.klayout.de/build.html> 下载对应
操作系统的桌面版。

`klink_plugin` 是一个 KLayout salt package。插件**随 pip 包一起发布**，
`pip install klayout-klink` 之后一条命令即可安装，无需 clone 仓库：

```bash
klink plugin install      # 把自带插件复制进 KLayout 的 salt/ 目录
klink plugin status       # 查看已装插件版本 vs 包内插件版本
```

salt 目录按操作系统自动定位（Windows 为 `%USERPROFILE%\KLayout\salt`，其余为
`~/.klayout/salt`，尊重 `KLAYOUT_HOME`）；可用 `--salt-dir` 覆盖。升级 pip 包后
重新运行 `klink plugin install`，插件即与客户端保持同版本。

安装后，KLayout 会自动运行 `pymacros/klink.lym`，并启动：

- klink RPC: `127.0.0.1:8765`。如果端口被占用，会继续尝试到 `8799`。
- klive-compatible server: `127.0.0.1:8082`，用于 gdsfactory 风格的 `c.show()` 工作流。

从仓库检出也可以手工复制文件夹——Windows:

```powershell
cd path\to\klink
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\KLayout\salt" | Out-Null
Copy-Item -Path ".\klink_plugin" -Destination "$env:USERPROFILE\KLayout\salt\" -Recurse -Force
```

Linux / macOS:

```bash
cd /path/to/klink
mkdir -p ~/.klayout/salt
cp -R klink_plugin ~/.klayout/salt/
```

两种方式最终插件都位于 `<KLayout salt 目录>/klink_plugin/`（即包含
`grain.xml` 的那个文件夹）。

安装插件后重启 KLayout。每个 KLayout 窗口运行自己的 klink session：绑定
`8765`–`8799` 中第一个空闲端口，并以 `klayout-<port>` 注册 session。同时开多个
窗口就有多个监听（`8765`、`8766`、……）。启动成功时，KLayout 控制台会打印：

```text
[klink.server] listening on 127.0.0.1:8765
```

升级时，先关闭 KLayout，重新运行 `klink plugin install`（会替换旧副本；从仓库检出则删除旧的 `salt/klink_plugin` 后重新复制），然后重启。

## 连接测试

先启动 KLayout 并确认插件已加载，然后直接用 Python 客户端连接：

```python
from klink import KLinkClient

with KLinkClient() as c:
    print(c.ping(nonce=42))
    print(c.layout_info(verbosity="summary"))
```

如果 KLayout 监听的不是默认端口：

```python
from klink import KLinkClient

with KLinkClient(port=8766) as c:
    print(c.ping())
```

想不带任何外部几何就跑出第一个端到端结果,运行任一公开 demo(见
[`docs/public/demos.md`](docs/public/demos.md)):

```bash
python -m examples_klink.public.demos.nanodevice.ebl_wraparound      # 完全离线
```

## Claude Code / MCP

安装 Python 包后，`klink-mcp` 会作为命令行入口可用。

安装 agent skills 和项目记忆文件：

```powershell
cd path\to\klink
klink-mcp --setup .
```

这会安装或更新：

```text
.claude/skills/klayout/SKILL.md
.claude/skills/klayout-gdsfactory/SKILL.md
CLAUDE.md
```

把 MCP server 注册给 Claude Code：

```powershell
claude mcp add klayout -- python -m klink.mcp --profile read,write,verify,escape --session-id project-klink
```

常用 profile：

| Profile | 用途 |
| --- | --- |
| `read` | 只读探索: layout、cell、layer、shape、view、selection 查询。 |
| `write` | 编辑工具: 创建 cell、layer、shape、instance、PCell 和 undo 记录。 |
| `verify` | DRC / LVS 验证工具。 |
| `escape` | `exec.python` 等 escape hatch。 |
| `all` | 暴露全部工具。 |

profile 可以用逗号组合：

```powershell
python -m klink.mcp --profile read,write,verify,escape
```

可选库必须安装在运行 `klink.mcp` 的同一个 Python 环境里。例如 gdsfactory 工具报缺依赖时，在同一环境直接装 gdsfactory：

```powershell
python -m pip install gdsfactory
```

可以用 MCP 工具 `klink.status` 查看当前解释器、已检测能力和 KLayout 连接状态。

## 测试

公开测试套是纯 Python 的,不需要 KLayout：

```powershell
python -m pytest -q tests/public
```

集成测试(路由、LVS、recorder)需要 live KLayout + 已加载的 `klink_plugin`,在开发仓里执行。

## 常见问题

### 用 Python 连接失败

请检查：

- KLayout 已启动。
- `klink_plugin` 已安装到 KLayout salt 目录（用 `klink plugin status` 查看）。
- 安装插件后已经重启 KLayout。
- 默认端口 `8765` 可用，或改用实际 session 端口，例如 `8766`。
- 防火墙或安全软件没有阻止 localhost TCP。

### MCP 工具有了，但调用时报 KLayout 连接错误

这说明 MCP server 在运行，但 KLayout 不可达。启动带插件的 KLayout 后，调用 `klink.reconnect` 或重启 MCP 客户端。用 `klink.status` 查看最后一次连接错误。

### gdsfactory 或 detector 工具报缺依赖

在运行脚本或 MCP server 的同一个环境里装缺的库(报错会指明是哪个)：

```powershell
python -m pip install gdsfactory
python -m pip install scipy scikit-learn scikit-image
```

### PowerShell 阻止激活 `.venv`

可以只对当前 PowerShell 进程允许脚本执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 延伸阅读

- [`docs/public/getting-started.zh-CN.md`](docs/public/getting-started.zh-CN.md): 安装、配置、第一个结果。
- [`docs/public/architecture.zh-CN.md`](docs/public/architecture.zh-CN.md): 三层结构与控制路径。
- [`docs/public/demos.zh-CN.md`](docs/public/demos.zh-CN.md): 各 demo 及其依赖。
- [`docs/public/control-plane.zh-CN.md`](docs/public/control-plane.zh-CN.md): 类型化 RPC 面、MCP 工具目录、批量写入。
- [`docs/public/interactive-workflows.zh-CN.md`](docs/public/interactive-workflows.zh-CN.md): SEND 选区记忆、多会话搬运、录制回放。
- [`docs/public/api-stability.zh-CN.md`](docs/public/api-stability.zh-CN.md): 什么算公开 API,以及 0.x 阶段的兼容性政策。
- [`docs/public/drc-lvs.zh-CN.md`](docs/public/drc-lvs.zh-CN.md): 编写并运行 DRC/LVS,含 profile 推导 deck(`profile.drc_script()`)。
- [`docs/public/drc-lvs-agent-handout.zh-CN.md`](docs/public/drc-lvs-agent-handout.zh-CN.md): 直接粘给 agent 的配方,教会 agent 写 KLayout DRC/LVS。

- [`docs/public/plugin-packages.zh-CN.md`](docs/public/plugin-packages.zh-CN.md): 用你自己的 pip 包扩展 klink(`klink.plugins` entry points)。
- [`docs/public/25d-view.zh-CN.md`](docs/public/25d-view.zh-CN.md): 用你的 StackSpec + z 表驱动原生 2.5d(3D 叠层)视图。
全部公开文档页均有中英双语,索引见
[`docs/public/README.zh-CN.md`](docs/public/README.zh-CN.md)。
- [`CLAUDE.md`](CLAUDE.md): Claude Code 操作规则和项目上下文。
- [`klink/mcp/README.md`](klink/mcp/README.md): MCP bridge 细节。

## 贡献

欢迎贡献。有几条项目特定的规则能让 klink 保持一致：

- **工艺纯净度**：`klink/` 只含机制、零工艺数据(不硬编码层号、器件、DRC 数值、
  端口或 PDK 实例)。工艺事实住在示例或用户的 `pdk.py`,并**显式传入** API。不要
  往 `klink/` 里加工艺常量。
- **一个意图 = 一次调用**:新的 agent 工具遵循单次调用对应单个用户意图、错误即指令
  (带 `next_action`)、先验证后改、状态落盘。
- **测试必须过**:提交前先跑公开套 `python -m pytest -q tests/public`。路由/LVS
  改动只有 live KLayout LVS `match=True` 才算完成。
- **字节冻结的 router**:`klink/routing/backends/flexdr/` 和 `rust/` 下的 crate 是
  字节对齐基线——Rust 内核是纯 Python 参考实现的等价加速移植,不要随意改。
- **预检**:`python -m klink.doctor` 检查解释器、插件连接和客户端/插件版本握手。

提 issue 或 PR 时请说明改了什么、怎么验证的。贡献按项目的 Apache-2.0 许可证接受。

想提 PR、讨论较大的改动或一起开发？联系维护者：**klinkdev2026@163.com**（或直接开 GitHub issue）。

## 致谢

klink 构建于并借鉴了优秀的开源工作:

- **[KLayout](https://www.klayout.de/)** —— klink 驱动并嵌入其中的版图编辑器与
  `pya`/`db` API。
- **[OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD)**(BSD-3-Clause)
  —— klink 的布线引擎包含对 OpenROAD 详细布线 `drt`(FlexDR / FlexPA / FlexGC)与
  全局布线 `grt`(FastRoute)的忠实移植与概念改写。
- **[gdsfactory](https://github.com/gdsfactory/gdsfactory)** —— 硅光工作流背后的
  光子组件与 `route_bundle` 后端。
- **[klive](https://github.com/gdsfactory/klive)**(MIT)—— klink 插件在 8082 端口
  重实现了它的显示协议,使 gdsfactory 风格的 `Component.show()` 无需改动即可工作。
- **[KlayoutClaw](https://github.com/caidish/KlayoutClaw)**(MIT)—— 纳米器件
  flake 检测先验与形态学掩膜辅助。
- **[Klayout-Router](https://github.com/Legendrexial/Klayout-Router)**(MIT)——
  writefield patch 生成背后的 EBL auto-patching 思路。

正式的第三方版权与许可证文本见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## License

Apache-2.0。见 `LICENSE`。第三方组件保留各自的许可证,见 `THIRD_PARTY_NOTICES.md`。
