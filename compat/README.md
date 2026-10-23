# compat/ — Compatibility layer

The **`ulm`** package is the single implementation. This folder exists
so the previous `unshackle`-based ecosystem keeps running unchanged:

| What | Where | How |
|---|---|---|
| `unshackle.*` imports | `unshackle/` at repo root | a meta-path shim aliases every `ulm.X` module request to `unshackle.X`, so `from unshackle.core.service import Service` resolves to the `ulm` implementation |
| `unshackle` console script | `pyproject.toml` | `[project.scripts] unshackle = "ulm.core.__main__:main"` keeps CLI parity |
| `unshackle.yaml` | `ulm.core.config` | the loader still prefers `unshackle.yaml` filenames and `UNSHACKLE_*` env variables |
| Service discovery | `ulm.core.services` | still reads `services/<TAG>/__init__.py`, next to the repo and in the data dir |
| old `Service.get_session()` | `ulm.core.service.Service.get_session()` | returns the small `requests` session; TLS fingerprinting is now opt-in (`session.mount` your own adapter) |

## Rules

1. `unshackle/` (repo-root folder) contains **no code of its own** —
   check any change here stays under 60 lines of alias logic.
2. Real fixes belong in `ulm/`; that keeps one source of truth.
3. Service modules should migrate to `ulm` imports over time; there's
   no need to do it in one sweep, the shim keeps them green meanwhile.

## Why "compat"?

Everything the old core exposed (click context, `config.headers`,
`TrackRequest`, vault names, `.wvd` device handling) was re-implemented
in `ulm` with a smaller memory footprint. The shim is the bridge so
existing service code keeps working while a rewrite removes heavy deps
(`rich`, `rnet`, `aiohttp`, `langcodes`, `lxml`, `wasmtime`...).
