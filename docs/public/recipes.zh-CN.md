# Recipes

> English: [recipes.md](recipes.md)

一个 recipe 是一个领域的参考实现。agent 按你描述的领域,从匹配的 recipe 起
你的项目脚手架。没有默认 recipe。

## 几何等级

- **自包含(Self-contained)** —— 完全由 `pdk.py` + 代码生成。不需要你提供
  任何几何。
- **开放或自备(Open or your own)** —— 在开放 PDK 上就能跑;也可以换成你
  的私有 PDK。
- **运行时自备(Bring your own)** —— 需要你在运行时提供保密几何。

**GDS/PDK 内容永不入库**——开放的也一样。代码在运行时指向文件。

## 目录

| 领域 | 等级 | 可复用的 klink 核心 | 现在能跑吗? |
|---|---|---|---|
| **EBL 纳米器件** | 自包含 | `klink.domains.nanodevice.devices.wraparound.build_wraparound_demo` | ✅ 离线 |
| **神经电极 harness** | 自包含 | tapered-hybrid 路由器 + `port.mark` / `anchor.mark` | ✅ 配 KLayout |
| **硅光** | 开放或自备 | `klink.routing.backends.gdsfactory.gdsfactory_ports.route_gdsfactory_ports` + photonics blackbox harvester | ✅ 开放 `gf.gpdk` feature 示例 `pip install gdsfactory` 即可跑;换你自己的 PDK 同样布 |
| **数字 P&R → LVS** | 自包含或自备 | `map_logic_to_devices(...)` → 放置 → FlexDR → live LVS | ✅ fit-device demo 用合成 exemplar 就能跑;换你自己的器件几何即可拟合并布你的。Verilog→gates 需要外部 yosys(`pip install yowasp-yosys`、PATH 上有原生 `yosys`,或 `KLINK_YOSYS=<path>`);缺了它流程会返回这个确切修法 |

## 加一个领域

新领域 = 一份按它的形状写的 `pdk.py` + 一个 `custom_devices/` 脚本:导入你
的工艺,显式调用相关 klink API。抄最接近的目录条目改。加领域从不需要改
klink。
