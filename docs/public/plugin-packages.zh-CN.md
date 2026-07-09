# 编写 klink 扩展包(`klink.plugins`)

klink 只发机制;你的工艺数据和自家工具可以住在**你自己的 pip 包**里,不改
klink 一行就接入。实验室或 PDK 供应商发布一个包;任何人把它装在
`klayout-klink` 旁边,就能在 `klink.find_tools` 里看到供应商的工具,并按
名字解析供应商的 profile / 器件库 / recipe / 叠层。

> English: [plugin-packages.md](plugin-packages.md)

## 只是要用别人的扩展包?三步

这一节之外的内容你都不需要:

1. `pip install acme-pdk-klink`——装进跑 klink 的同一个 Python
   (包名由供应商告诉你)。
2. 重启你的 agent(MCP server 在 agent 启动时加载)。
3. 完事。你的 agent 的 `klink.find_tools` 里会出现供应商的域(如
   `acme_pdk`)和它的工具——像内置工具一样直接调。自己写脚本时,供应商
   的工艺数据按名字解析:

   ```python
   from klink import ext
   profile = ext.get_resource("profile", "acme_2m")   # 供应商的 ProcessProfile
   ```

预期的工具没出现时,查 `klink.status` → `extensions`:它列出已装扩展包,
坏掉的会被点名并附错误。卸载该包,一切随之干净消失。

下面的内容全部是写给**编写**这种包的人的。

## 最小可用包

两个文件。`pyproject.toml`:

```toml
[project]
name = "acme-pdk-klink"
version = "0.1.0"
dependencies = ["klayout-klink"]

[project.entry-points."klink.plugins"]
acme = "acme_pdk.klink_ext:register"
```

`acme_pdk/klink_ext.py`:

```python
from acme_pdk.process import ACME_2M_PROFILE     # 你的数据,你的包

def hello(ctx, arguments):
    # ctx 是 MCP bridge:ctx._client 可达 live KLayout 会话
    return {"greeting": f"hello {arguments.get('who', 'world')}"}

def register(hook):
    hook.add_domain(
        "acme_pdk", title="ACME PDK",
        summary="ACME 的工艺数据与辅助工具",
        usage="用 klink.ext.get_resource('profile', 'acme_2m') 解析 "
              "profile;acme_pdk.hello 是冒烟检查。")
    hook.add_tool(
        "acme_pdk.hello", hello,
        description="ACME 扩展的冒烟检查",
        input_schema={"type": "object",
                      "properties": {"who": {"type": "string"}}},
        domain="acme_pdk")
    hook.add_profile("acme_2m", ACME_2M_PROFILE)
```

把 `acme-pdk-klink` 装在 klink 旁边,重启 MCP server,然后:

- `klink.find_tools`(无参)会列出 `acme_pdk` 域和它的工具数;
  `domain="acme_pdk"` 返回你的 usage 文本——你的扩展享有与内置域相同的
  渐进式披露。
- `acme_pdk.hello` 像任何内置工具一样可调用。
- 任何示例或脚本可用 `klink.ext.get_resource("profile", "acme_2m")` 拿到
  你的 `ProcessProfile`。

## 你能贡献什么

| hook 调用 | 贡献 | 说明 |
|---|---|---|
| `add_tool(名, handler, description=, input_schema=, domain=)` | 一个 MCP 工具 | 名字必须命名空间化 `<token>.<名>`;内置前缀(`view.`、`shape.`、`routing.`……)保留,占用会被拒 |
| `add_domain(token, title=, summary=, usage=)` | 一个 `find_tools` 域 | `usage` 字符串就是你的 skill 式配方,按需披露 |
| `add_profile(名, 对象)` / `add_devices` / `add_recipe` / `add_stack` | 命名资源 | 用 `klink.ext.get_resource(kind, 名)` 解析;示例和流程借此拿到你的工艺数据 |

Handler 遵循本地工具契约:`handler(ctx, arguments)` 返回可 JSON 序列化的
dict;`ctx` 是 MCP bridge(其 `_client` 是已连接的 `KLinkClient`)。贡献
的工具应遵守与内置相同的设计规则——一次调用一个用户意图、错误即指令、
先校验后改动。

## 故障隔离(包坏了会怎样)

发现是惰性(首次使用)且按包隔离的:

- 导入失败或 `register()` 抛异常的扩展会被**完整回滚**(不存在注册一半
  的工具),并记录为点名该包和错误的失败条目;
- 其它扩展和所有内置工具不受影响——MCP server 绝不因坏扩展而崩;
- `klink.status` 报告 `extensions` 块:已装包及其贡献、失败清单。预期的
  工具不见时先查它。
- **零**扩展安装时,klink 的工具清单与素装逐字节一致——机制不用就零
  开销。

## 验证你的包

1. 在 `klayout-klink` 旁 `pip install -e .` 你的扩展。
2. `klink.status` → 你的包出现在 `extensions.installed`,`failures` 为空。
3. `klink.find_tools` → 你的域带正确计数出现;调一次你的工具。
4. `python -c "from klink import ext; print(ext.get_resource('profile',
   '<名>'))"`。
5. 卸载再查:一切干净消失。

工艺纯净律同样适用于你:*数字*(层号、尺寸、z 表)放你包里的数据模块,
显式传进 klink API——你的扩展正是"自带工艺的 example"模式的发行版形态。
