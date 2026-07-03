# 交互工作流

> English: [interactive-workflows.md](interactive-workflows.md)

三个能力让"人 + agent + live KLayout"的闭环真正可用:SEND 选区记忆
("这块区域"指什么)、多会话注册表 + 跨会话搬运、录制回放。三者都是普通
工具——除了插件本身,不需要任何额外设置。

## "这块区域"——SEND 选区记忆

你在 KLayout 里选中几何、点插件的 **SEND** 工具栏按钮(或 agent 调
`selection.send_context`)时,该选区会以稳定 id(如 `sel_0006`)记进会话级
记忆(`.klink/sessions/<session-id>/interaction_context.jsonl`)。此后,
"刚发的"、"这块区域"、"那个"这类话会解析成确切几何,而不是一张截图。

agent 侧工具:

```text
interaction.selection.recent   -> 最近存储的选区(默认最近 5 条)
interaction.selection.latest   -> 最近一条存储选区
interaction.selection.get      -> 按 id 精确取
interaction.selection.label    -> 给重要 id 附名字/描述
interaction.context            -> 当前 live 选区 + 最近记忆一起返回
```

`selection.get` 是 *live* 当前选区;`interaction.*` 是显式发送过的东西的持久
记忆。记忆按顺序和条数解析,不按年龄——从一次 SEND 到提到它的那句话之间,
版图操作可能隔了好几分钟。

SEND 是持久的:插件在广播事件*之前*先把每次 SEND 连同单调序号写进 journal,
所以没有 agent 在听时按下的 SEND 不会丢——bridge 下次连上时从 journal 补
账并按序号去重。

## 多个 KLayout,一个 bridge——会话与搬运

每个运行中的 KLayout 窗口绑定一个端口(8765、8766、…)并注册为一个会话。
会话是**平等对等体**——没有哪个端口有特权角色;你要哪个会话就显式说哪个。

```text
klink.session_list      -> 枚举运行中的会话
klink.session_label     -> 给会话附人类可读标签/别名
klink.session_resolve   -> 把标签、别名或活动 cell 解析成会话 id
klink.session_use       -> 把 bridge 的主 RPC 目标切过去
```

跨会话搬几何是两阶段、确认安全的:`klink.transfer_prepare` 构建一个包
(`flat_selection` 合并形状,`shallow_instance` 搬实例),**先对目标会话
试运行(dry-run)**,再持久化为 pending;`klink.transfer_commit` 才真正
写入。commit 之前目标里什么都不会落地,选错目标在 dry-run 阶段就被拦住。

## 录制——把编辑变成脚本

录制器把一段工作过程——手工 GUI 编辑和 agent RPC 编辑一视同仁——变成可回放
的脚本:

```text
recorder.start    -> 开始录制(可指定输出路径)
recorder.status   -> 是否在录、目前多少事件/动作
recorder.stop     -> 写出脚本,返回统计 + wrote=true/false
```

停止时写出**两个产物**:一个 `KLinkClient` 回放脚本(`<name>.py`,带
`# user command:` 注释标注每步来自哪个菜单动作)和一个独立 `pya` 版本
(`<name>_pya.py`,在没装 klink 的 KLayout 里也能跑)。

录制器是**回放脚本生成器,不是逐调用日志**:它录的是能重建最终版图状态的
动作,所以一次批量 RPC 或一段 `exec.python` 可能展开成逐对象动作。自己开录
之前先查 `recorder.status`,永远不要覆盖别人正在进行的录制。
