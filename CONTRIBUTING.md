# Contributing to klink

Contributions are welcome — bug reports, PRs, docs, and new domain workflows.

**Contact:** want to submit a PR, discuss a larger change, or co-develop a
feature? Email the maintainers at **klinkdev2026@163.com**, or open a
GitHub issue.

## Ground rules

The full rules live in the [README's Contributing section](README.md#contributing);
the short version:

- **Process purity.** `klink/` is pure mechanism and holds zero process data
  (no hardcoded layers, devices, DRC numbers, ports, or PDK instances).
  Process facts live in an example or user `pdk.py` and are passed explicitly
  into the APIs.
- **One intention = one call.** Agent-facing tools use one call per user
  intention, instructive errors (`next_action`), validate-before-mutate, and
  state persisted on disk.
- **Tests must pass.** `python -m pytest -q tests/public` before sending a
  change. Routing/LVS changes count as done only on a live KLayout LVS
  `match=True`.
- **Byte-frozen router.** `klink/routing/backends/flexdr/` and `rust/` are
  byte-parity baselines; do not alter them casually.

## Sending a change

1. Fork, branch, make the change.
2. Run the public suite; describe what changed and how you verified it in the
   PR body.
3. Contributions are accepted under the project's Apache-2.0 license.

---

中文：想提 PR、讨论较大的改动或一起开发，请联系 **klinkdev2026@163.com**，
或直接开 GitHub issue。项目规则见 [README.zh-CN.md](README.zh-CN.md) 的"贡献"一节。
