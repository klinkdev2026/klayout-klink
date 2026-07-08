# Security

## Trust model

klink's RPC server binds to `127.0.0.1` only — it is never reachable from
another machine unless you explicitly forward the port yourself (don't).
It has **no authentication layer**: any local process (any user account,
any application) that can open a TCP socket to the listening port can call
every RPC the server exposes, including layout mutation.

`exec.python` executes arbitrary Python code inside the running KLayout
process **by design** — it is the documented escape hatch for anything not
covered by a typed RPC (see `CLAUDE.md` / the agent-operating-rules docs).
This is not a bug to report; it is the intended shape of a local
control-plane tool.

**Bottom line:** treat any local account that can reach `127.0.0.1:8765`
(RPC) or `127.0.0.1:8082` (klive-compat) as fully trusted by klink, in the
same way you would trust a process that can write to your KLayout macro
folder. klink assumes a single-user, single-machine trust boundary, the same
one KLayout's own macro/pya execution already has.

## What this means for you

- Do **not** port-forward, reverse-proxy, or otherwise expose the klink RPC
  port (`8765`) or the klive-compat port (`8082`) beyond localhost.
- Do not run klink on a shared or multi-tenant machine where other local
  accounts should not have layout-mutation or arbitrary-code-execution
  access to your KLayout session.
- MCP clients (Claude Code, other agent harnesses) connect to klink over
  the same localhost RPC path — no additional trust boundary is introduced
  by adding an agent to the loop; the agent has exactly the access the
  RPC surface already grants any local process.
- Session/context files under `.klink/sessions/` are plain local files with
  no secrets in them by default; don't add secrets to them.

## Reporting a vulnerability

If you find a security issue that is **not** the documented trust model
above (for example: the RPC server binding beyond localhost, an
authentication bypass in a future auth layer, or a path/command injection
that escapes the intended sandboxing of a typed RPC), please report it via:

- a GitHub issue on this repository, or
- email: **klinkdev2026@163.com**

Please include a minimal repro and the klink/KLayout versions involved
(`python -m klink.doctor` output is a good starting point). We don't yet
run a formal disclosure program or offer a bounty — this is a small,
community-maintained project — but we will respond and fix confirmed
issues promptly.
