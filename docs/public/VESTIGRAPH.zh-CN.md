<p align="right">
  <a href="VESTIGRAPH.md">English</a> | <a href="VESTIGRAPH.zh-CN.md">中文</a>
</p>

# Vestigraph 本地历史

需要在运行 `klink-mcp` 的同一个 Python 环境安装 Vestigraph，才能使用 klink + Vestigraph 的完整功能，包括本地文件与已保存版图历史、HIST 和 agent 历史工具。需要 Python 3.10+、KLayout 桌面 0.30.x，以及 Klink 0.6.0 或兼容的后续 0.6.x。

## 安装与同步

```console
python -m pip install "vestigraph>=0.2,<0.3"
klink plugin install
# 重启运行 klink-mcp 的 MCP 客户端
# 重启 KLayout，打开已保存的 GDS/OASIS 版图，然后点击 HIST
python -m vestigraph doctor --integration
```

`pip install vestigraph` 会安装兼容的 `klayout-klink` 和 `vestigraph-scan-core` 依赖，原生模块可用时自动使用 Rust 扫描。`klink plugin install` 安装或升级 KLayout 插件。重启 MCP 后，现有 Klink 扩展注册表会发现 Vestigraph，并为该 Python 环境登记 companion。不需要额外 MCP server。安装包不会配置聊天客户端。

升级时，如果旧 Vestigraph 服务正在运行，先停止它；升级包，重新运行 `klink plugin install`，然后重启 MCP 和 KLayout。Vestigraph、Klink MCP 和 companion 登记应使用同一个 Python 环境。

## 工具发现

```json
{"tool":"klink.find_tools","arguments":{"domain":"vestigraph"}}
```

然后调用 `vestigraph.guide` 并按 `next_action` 操作。已有请求用 `vestigraph.skill` 读取，再用 `vestigraph.submit` 保存草稿并检查文档结构。新请求用 `vestigraph.history`、`vestigraph.refine`，然后 `vestigraph.submit`。用户要求导出时，`vestigraph.export` 写入本地文件。

## 启用本地技能通路

该功能为实验功能，默认关闭。先停止旧服务。

PowerShell：

```powershell
$env:VESTIGRAPH_EXPERIMENTAL_SKILLS = "1"
python -m vestigraph serve --control-file --open-browser
```

POSIX shell：

```sh
VESTIGRAPH_EXPERIMENTAL_SKILLS=1 python -m vestigraph serve --control-file --open-browser
```

由 KLayout 启动 companion 服务时，KLayout 需继承上述环境。自定义服务状态时，在 MCP 环境中设置 `VESTIGRAPH_CONTROL_FILE` 指向该服务的本地控制文件。不要把密钥粘贴到 agent 提示中。

## 本地数据与验证

历史、冻结证据、草稿、修订和导出均保存在用户本地。两个产品都不附带私人技能。适配层使用经过认证的 loopback HTTP，不上传、执行或安装技能。返回信息如何处理取决于用户选择的 agent 客户端。

提交检查只验证文档结构。作者陈述不同于平台检查。保存文件差异不证明 GUI 操作顺序、重放、DRC/LVS 或工艺意图。遇到修订冲突时，先读取最新技能再修改。
