# Vestigraph local history / 本地历史

Vestigraph adds local file and KLayout history to a Klink installation. Use Python 3.10+, KLayout desktop 0.30.x, and Klink 0.6.0 or a compatible later 0.6.x release.

Vestigraph 为 Klink 安装加入本地文件与 KLayout 历史。需要 Python 3.10+、KLayout 桌面 0.30.x，以及 Klink 0.6.0 或兼容的后续 0.6.x。

## Install and synchronize / 安装与同步

```console
python -m pip install "vestigraph>=0.2,<0.3"
klink plugin install
# restart the MCP client that runs klink-mcp
# restart KLayout, open a saved GDS/OASIS layout, then click HIST
python -m vestigraph doctor --integration
```

`pip install vestigraph` installs compatible Klink dependencies. `klink plugin install` installs or upgrades the KLayout plugin. Restarting MCP lets the existing Klink extension registry discover Vestigraph and register its companion for that Python environment. No extra MCP server is needed. Installing packages does not configure a chat client.

`pip install vestigraph` 会安装兼容的 Klink 依赖。`klink plugin install` 安装或升级 KLayout 插件。重启 MCP 后，现有 Klink 扩展注册表会发现 Vestigraph，并为该 Python 环境登记 companion。不需要额外 MCP server。安装包不会配置聊天客户端。

For upgrades, stop the old Vestigraph service if one is running, upgrade packages, rerun `klink plugin install`, then restart MCP and KLayout. Keep Vestigraph, Klink MCP, and companion registration in the same Python environment.

升级时，如果旧 Vestigraph 服务正在运行，先停止它；升级包，重新运行 `klink plugin install`，然后重启 MCP 和 KLayout。Vestigraph、Klink MCP 和 companion 登记应使用同一个 Python 环境。

## MCP discovery / 工具发现

```json
{"tool":"klink.find_tools","arguments":{"domain":"vestigraph"}}
```

Then call `vestigraph.guide` with `{}` and follow `next_action`. Existing requests use `vestigraph.skill` to read, then `vestigraph.submit` to save a draft and check document structure. New requests use `vestigraph.history`, `vestigraph.refine`, then `vestigraph.submit`. When the user asks for an export, `vestigraph.export` writes a local file.

然后调用 `vestigraph.guide` 并按 `next_action` 操作。已有请求用 `vestigraph.skill` 读取，再用 `vestigraph.submit` 保存草稿并检查文档结构。新请求用 `vestigraph.history`、`vestigraph.refine`，然后 `vestigraph.submit`。用户要求导出时，`vestigraph.export` 写入本地文件。

## Enable local refinement / 启用本地技能通路

Experimental and disabled by default. Stop the old service first.

该功能为实验功能，默认关闭。先停止旧服务。

PowerShell:

```powershell
$env:VESTIGRAPH_EXPERIMENTAL_SKILLS = "1"
python -m vestigraph serve --control-file --open-browser
```

POSIX shell:

```sh
VESTIGRAPH_EXPERIMENTAL_SKILLS=1 python -m vestigraph serve --control-file --open-browser
```

For companion startup, KLayout must inherit this environment. For a custom service state, configure `VESTIGRAPH_CONTROL_FILE` in the MCP environment to point at its local control file. Never paste the secret into an agent prompt.

由 KLayout 启动 companion 服务时，KLayout 需继承上述环境。自定义服务状态时，在 MCP 环境中设置 `VESTIGRAPH_CONTROL_FILE` 指向该服务的本地控制文件。不要把密钥粘贴到 agent 提示中。

## Local data and verification / 本地数据与验证

History, frozen evidence, drafts, revisions and exports stay in user storage. Private skills are not shipped in either product. The adapter uses authenticated loopback HTTP and does not upload, execute or install skills. The user's agent client determines how returned information is handled.

Submission checks document structure only. Author statements are separate from platform checks. Saved-file differences do not prove GUI action order, replay, DRC/LVS or process intent. On a revision conflict, read the latest skill before changing it.

历史、冻结证据、草稿、修订和导出均保存在用户本地。两个产品都不附带私人技能。适配层使用经过认证的 loopback HTTP，不上传、执行或安装技能。返回信息如何处理取决于用户选择的 agent 客户端。

提交检查只验证文档结构。作者陈述不同于平台检查。保存文件差异不证明 GUI 操作顺序、重放、DRC/LVS 或工艺意图。遇到修订冲突时，先读取最新技能再修改。
