# klink 公开文档(发布版)

klink 的**正式发布文档**。小而精,面向采用这个工具的用户,与 shipped 行为
保持同步。

> English: [README.md](README.md)

## 页面

| 页面 | 内容 |
|------|------|
| [getting-started.zh-CN](getting-started.zh-CN.md) ([English](getting-started.md)) | 安装、配置、跑出第一个结果(无需 GDS) |
| [architecture.zh-CN](architecture.zh-CN.md) ([English](architecture.md)) | 三层结构、控制路径、process purity、agent 三层模型 |
| [project-model.zh-CN](project-model.zh-CN.md) ([English](project-model.md)) | 用户项目脚手架 + 识别领域的 onboarding 流程 |
| [recipes.zh-CN](recipes.zh-CN.md) ([English](recipes.md)) | 各领域起点及其几何等级 |
| [demos.zh-CN](demos.zh-CN.md) ([English](demos.md)) | 四个 demo 及各自的确切依赖 |
| [control-plane.zh-CN](control-plane.zh-CN.md) ([English](control-plane.md)) | 类型化 RPC 面、MCP 工具目录(`klink.find_tools`)、批量写入、逃生舱 |
| [interactive-workflows.zh-CN](interactive-workflows.zh-CN.md) ([English](interactive-workflows.md)) | SEND 选区记忆、多会话搬运、录制回放 |

## 发布范围(当前)

轻量首发。**画廊里四个 demo 全部不需要你提供几何**:EBL wraparound 和
Hall bar 完全离线;神经电极和 fit-device → P&R → LVS 对 live KLayout 会话跑
(P&R demo 用**合成** exemplar 拟合器件——不涉及任何器件 IP)。硅光 feature
示例用开放的 `gf.gpdk`,`pip install gdsfactory` 即可。你自己的私有 PDK 或
器件几何始终是运行时自备(bring-your-own),永不入库。
