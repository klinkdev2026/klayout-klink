# 工具栏工具

把一小段脚本变成 KLayout 工具栏按钮——点一下,一个撤销步,点击时不需要 agent 参与。

> English: [toolbar-tools.md](toolbar-tools.md)

```text
tool.json + run.py           一个文件夹:接口 + 实现
  -> klink tool install      变成工具栏按钮(presets = 下拉项)
  -> 点击                    弹出一个表单,点 OK,编辑在 KLayout 内执行
  -> Ctrl+Z                  一步撤销整次运行
```

## 你会得到什么

一个**工具(tool)**就是一个文件夹 `<id>/`,里面有 `tool.json`(叫什么、需要选中什么、
有哪些设置)和 `run.py`(做什么)。`tool install` 把它变成 KLayout **工具栏按钮**;
每个 preset 成为按钮下拉菜单里的一项。点击时工具在 KLayout 内部运行——不需要外部
Python 进程,也不需要 agent:

- 整次运行是**一个撤销步**(撤销列表里显示 `klink tool: <title>`);
- `run.py` 抛异常时,已做的部分编辑会被**回滚**,并显示错误消息;不会留下半成品;
- 每次运行(无论点击还是脚本调用)都会**记入日志**(`tool log`);
- 所有设置在**一个表单**里一次问完,就像 KLayout 自带的 Boolean 对话框。

klink **不预装任何工具**——由你的 agent 替你写。`klink init` 自带三个能直接用的示例,
位于 `example_template/tools/`:

| 示例 | 按钮 | 作用 |
|---|---|---|
| `bool_region` | `BOOL2` | 在选区范围内对两个图层做布尔运算(AND / OR / NOT / XOR 下拉项) |
| `bool_region_macro` | `MACRO` | 同样的功能,是一个原样放入、一字未改的 KLayout 宏 |
| `bool_selection` | `BOOL` | 对**选中的图形本身**做布尔运算(Union / Intersection / Subtract / XOR) |

**普通按钮**是更底层的一级:`python -m klink.cli button add <id> --label ABC` 添加一个
工具栏按钮,点击它只会写一条日志记录并发出 `button_triggered` 事件——由监听者(agent、
脚本)决定它的含义。工具则是 klink 替你执行点击动作的按钮。

## 两分钟上手

在一个 `klink init` 项目里,KLayout 已运行且已加载 klink:

```bash
python -m klink.cli tool install example_template/tools/bool_region
```

工具栏出现 `BOOL2` 按钮。选中两个位于不同图层、相互重叠的图形,点 `BOOL2`(或它的某个
下拉项),弹出一个窗口:

- **layer A** / **layer B**:选区下方各图层的下拉框,已预选两个选中图形所在的图层
  (A = 第一个,B = 第二个);
- **operation**:下拉项对应的运算,可改;
- **output layer**:版图已有图层,外加带 L/D 输入框的 `new layer…`;
- **keep what is already on the output layer**(保留输出层上已有的内容)。

点 OK:结果写到输出层,状态栏显示一行确认。**Ctrl+Z** 全部撤销,包括本次新建的图层。

在某个区域上画一个标记框也可以:框下方的图层排在前面,标记框自己的图层排在最后。

## 让 agent 给你做一个按钮

说清按钮做什么、你会选中什么(例如"在我画的框里对 1/0 和 2/0 做 AND,输出到新图层")。
agent 按项目里的 `recipes/tools.md` 执行——一条固定流水线,每一步都有闸门,过了才能往下走:

```bash
python -m klink.cli tool new <id> --min-selected 1 --title "做什么" --label ABC   # 闸门:"created .klink/tools/<id>"
python -m klink.cli tool check .klink/tools/<id>      # 闸门:"ok: 0 error(s)",params 表符合意图(不需要 KLayout)
python -m klink.cli tool install .klink/tools/<id>    # 闸门:"installed <id>",工具栏出现按钮
python -m klink.cli tool run <id> --preset p          # 闸门:"ok: <id> ...",几何核对无误,一次撤销即可还原
python -m klink.cli tool log <id> -n 3                # 闸门:你的点击以 OK 记入日志
```

`tool new --from example_template/tools/bool_region` 从示例起步;`tool new --macro` 把
你手上已经能跑的宏原样包装成工具。

**文件位置。** **源码**在你的项目里:`.klink/tools/<id>/`(`tool.json` + `run.py`
[+ `README.md`]),随项目纳入版本管理——项目的 `.gitignore` 保留了 `.klink/tools/`。
klink 从不在这里直接运行它,而是**从这里安装**到用户级 registry:

| 系统 | 安装副本位置 |
|---|---|
| Windows | `%LOCALAPPDATA%\klink\registry\tools\<id>` |
| macOS | `~/Library/Application Support/klink/registry/tools/<id>` |
| Linux | `~/.local/state/klink/registry/tools/<id>` |

`KLINK_REGISTRY_ROOT` 可覆盖 registry 根目录;`python -m klink.cli tool list` 打印该
目录和每个工具的状态。更新:改源码,再跑一遍 `check` + `install`。删除:
`python -m klink.cli tool uninstall <id>`。

## tool.json 速览

| 键 | 含义 |
|---|---|
| `id` | 必填;`^[a-z][a-z0-9_]{0,39}$`,与文件夹名相同 |
| `title` | 必填;表单标题 + 撤销条目 `klink tool: <title>` |
| `label` | 工具栏文字,显示前 6 个字符 |
| `description` | 自由文本 |
| `icon` | 可选,放在 `run.py` 旁边的 `.png` / `.svg` 文件;没有则按钮只显示文字 |
| `inputs.selection.min` | 至少选中 N 个对象(图形或实例),否则在任何编辑之前拒绝 |
| `inputs.selection.min_shapes` | 至少选中 N 个**图形**(实例不算) |
| `params` | 名称 -> `{"type", "default"?, "title"?, "doc"?, "unit"?, "min"?, "max"?, "choices"?}`;类型 `int`、`number`、`string`、`bool`、`enum`(需要 `choices`)、`layer`(在 `run.py` 里永远是 `"L/D"`) |
| 图层 `from` | `"region"`(选区下方的图层)、`"selection"`(选中对象所在的图层)、`"layout"`(全部图层):表单里显示下拉框而非文本框 |
| 图层 `exclude` | 排除其他图层 param 已选的值(`layer_b` 排除 `layer_a`) |
| 图层 `allow_new` | 增加 `new layer…`(询问 L/D,预填 `default`)——用于输出层 |
| 图层 `allow_same` | 接受值 `"same"`(含义由 `run.py` 决定) |
| `presets` | 名称 -> `{"title"?, "params": {...}}`;每个 preset = 一个下拉项 |
| `prompt` | `"missing"`(默认:询问下拉项没有固定的 param)、`"always"`、`"never"` |

没有 `default` 的 param 是必填的。输入层用 `from`,不要写死图层号。参考结构见
`example_template/tools/bool_region/tool.json`。

## 十行看懂 run.py

```python
def run(ctx, params):      # 每次点击调用一次,已处于"一个撤销步"之内
    # ctx.view, ctx.layout, ctx.cell(在这里插入), ctx.dbu, ctx.selection(选中的图形),
    # ctx.selected(全部选中对象), ctx.layers(scope), ctx.tool_dir, ctx.log, ctx.pya
    # params 已校验并转换好类型(int / float / bool / str / enum;图层是 "1/0")
    # 坐标用数据库单位:int(round(um / ctx.dbu))
    # 先读取并校验全部输入,再编辑
    # 用户错误:raise ValueError("哪里错; 怎么办")  -> 原样显示,不做任何改动
    # 不开自己的事务、不弹自己的对话框:klink 都已提供
    print("一行摘要")          # 显示在状态栏
    return {"count": 0}        # 一个可 JSON 序列化的小 dict,记入日志
```

## 从脚本和 agent 调用

```bash
python -m klink.cli tool run <id> --preset p --param name=value
python -m klink.cli tool run <id> --interactive    # 改为在 KLayout 里弹出表单
```

非交互运行什么都不问:只有一个候选的图层 param 自动填入;有多个候选时运行停止并返回
`[ERR_TOOL_PARAMS]`,其 `next:` 行列出可传的候选(`--param layer_a=1/0`)。

MCP 侧的 `tools` 域提供 `tool.list`、`tool.get`、`tool.run`(默认非交互)、
`buttons.list` 和 `buttons.reload`(手工改过已安装的 `tool.json` 之后用);
`klink.find_tools domain=tools` 列出它们及用法。`new`、`check`、`install`、`log`、
`export`、`uninstall` 只在 CLI 中提供。

## 分享

```bash
python -m klink.cli tool export <id> <id>.zip     # 把已安装的工具打成 zip
python -m klink.cli tool install <id>.zip         # 接收方安装
```

## 故障排查

| 现象 | 处理 |
|---|---|
| 安装后看不到按钮 | KLayout 没运行或没加载 klink:启动它,或调用 `buttons.reload`;无效工具在 `tool list` 里显示 `UNUSABLE: <原因>` |
| `check` 警告自开事务 / 对话框 / `sys.exit` | 删掉它;警告不阻止安装,但运行会出问题 |
| `tool run` 报 `[ERR_TOOL_PARAMS] ... pick ...` | 有多个候选图层:按 `next:` 行传 `--param name=L/D`,或用 `--interactive` |
| "needs at least N selected object(s)" | 多选一些,或调低 `inputs.selection.min` |
| "layer X does not exist" | 默认值与当前版图的图层不符:改掉它或改用 `from` param,然后重新安装 |
| 点 OK 表单关不掉 | 某个字段没通过校验(红字写明原因):改正它 |
| 一次点击撤销的内容比预期多/少 | `run.py` 自己开了事务——删掉 |
| `invalid choice: 'tool'` | `python` 导入的 `klink` 太旧:`python -m pip install -U klayout-klink` |
| `tool log` 显示 `no runs recorded` | 点击没到达 klink:插件未加载,或 CLI 读的是另一个 registry 根目录(`KLINK_REGISTRY_ROOT`;`tool list` 会显示是哪个) |
