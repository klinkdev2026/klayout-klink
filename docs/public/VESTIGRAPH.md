# Vestigraph local history / 本地历史

Vestigraph is an optional companion for klink. Both remain independently installable. Use Python 3.10+; KLayout integration uses desktop 0.30.x and klink 0.6.0 or a compatible later 0.6.x release.

## Install and synchronize / 安装与同步

```console
python -m pip install "klayout-klink>=0.6.0,<0.7" "vestigraph[klink]>=0.2,<0.3"
python -m vestigraph setup
python -m vestigraph doctor --integration
```

Install in the Python that runs klink MCP. Restart KLayout, open a saved layout and click HIST. For upgrades, stop the old history service, upgrade both packages, rerun setup and restart KLayout and MCP. Keep the environment used by setup.

在运行 klink MCP 的同一个 Python 中安装。升级时先退出旧服务，升级两个包，重新运行 setup，然后重启 KLayout 和 MCP。doctor 只检查安装，记录状态以面板为准。

Standalone file history needs only `pip install vestigraph`; run `python -m vestigraph serve --open-browser`. It requires neither the KLayout desktop nor setup. 基础历史可独立使用，不要求 KLayout 桌面或 setup。

## MCP discovery / 工具发现

```json
{"tool":"klink.find_tools","arguments":{"domain":"vestigraph"}}
```

Then call `vestigraph.guide` with `{}` and follow `next_action`. The existing klink extension registry discovers the package. No extra MCP server or automatic agent startup is added. Installing packages does not configure a chat client.

已有请求用 `vestigraph.skill` 读取，再由 `vestigraph.submit` 保存草稿并检查文档结构。新请求用 `vestigraph.history` 查询用户指定区间，经 `vestigraph.refine` 固定证据后提交。用户要求导出时，`vestigraph.export` 写入本地文件。

## Enable local refinement / 启用本地技能通路

Experimental and disabled by default. Stop the old service first.

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

由 KLayout 启动伴随服务时，KLayout 需继承上述开关。自定义服务目录通过 MCP 环境中的 `VESTIGRAPH_CONTROL_FILE` 指定，不需要给 agent 提供密钥。

## Local data and verification / 本地数据与验证

History, frozen evidence, drafts, revisions and exports stay in user storage. Private skills are not shipped in either product. The adapter uses authenticated loopback HTTP and does not upload, execute or install skills. The user's agent client determines how returned information is handled.

Submission checks document structure only. Author statements are separate from platform checks. Saved-file differences do not prove GUI action order, replay, DRC/LVS or process intent. On a revision conflict, read the latest skill before changing it.

数据、技能及导出均归用户本地。结构检查不等于重放、DRC 或 LVS 验证。冲突时重新读取，多个候选由用户选择。页面的“发布”只是本地状态，不是互联网发布。
