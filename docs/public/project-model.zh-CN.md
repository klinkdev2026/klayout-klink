# 用户项目模型

> English: [project-model.md](project-model.md)

你在**自己的项目**里工作,不在 klink 里。用 `klink init <dir>` 起脚手架
(模板打包在安装包里)。

## 可编辑面

```
your-project/
  pdk.py        你的工艺——工艺事实唯一的家
  custom_devices/ agent 写的构建脚本 / 器件生成器
  specs/        .klink spec
  out/          生成的 GDS / 结果   (GDS 永不入库)
  AGENTS.md     agent 规则(CLAUDE.md 指向它)
  mcp.example.json
```

你编辑 `pdk.py`、`custom_devices/`、`specs/`。`klink` 和插件是安装包——
从不编辑。

## Onboarding:你描述的领域就是默认

**没有硬编码的默认项目**。在全新项目里 agent 会:

1. **访谈你**在做什么,直到能说出领域名,
2. **选中匹配的 recipe**(见 [recipes.zh-CN](recipes.zh-CN.md))并告诉你它的
   几何等级——如果需要你的保密几何,会开口要,
3. **起脚手架**:`pdk.py` + 第一个 `custom_devices/` 脚本,把你的工艺
   **显式**传进 klink,
4. **跑起来并验证**:用结构化几何查询 / LVS。

## 验证靠查询,不靠截图

版图用 `selection.get`、`shape.query`、`layout.info`、层计数和 live LVS 检查
——不用截图。一条路由/一版布局只有在 live KLayout LVS 返回 `match=True`
时才算"完成";marker 数量和"看起来布通了"不算。

## 保密几何永不入库

你的项目里永远不能有 GDS/PDK 内容——私有 foundry PDK 和晶体管版图都不行。
模板 `.gitignore` 默认拦 `*.gds`/`*.oas`。recipe 代码在运行时指向这些文件。
(依赖开放 PDK 没问题,但同样不入库。)
