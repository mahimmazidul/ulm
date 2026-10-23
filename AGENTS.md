# AGENTS.md — working rules for coding agents on this repo

This file is the contract for AI/agent contributors (including OpenCode,
Cursor and friends). Read it before touching code.

## Identity of this project

- **Name:** `ulm` — 🪶 Light on RAM. Fast to finish. Made to run anywhere.
- Repo: **https://github.com/mahimmazidul/ulm**
- Python package: `ulm` (the `unshackle/` folder is only a compatibility
  shim — never put real code there; see `compat/README.md`).
- Design pillars: **lightweight, low RAM, fast, mobile-friendly.**
  Reject anything that raises install weight or baseline memory for a
  feature. Rich terminals libraries, async event loops, ORM layers and
  fingerprint-impersonation stacks are deliberately NOT here.

## Ground rules

1. **One core, one implementation.** Compatibility layers live in
   `unshackle/` (shim) and `compat/` (docs and adapters). Fix or extend
   real behaviour only inside `ulm/`.
2. **Imports:** inside `ulm/` always use absolute `ulm.xxx` imports.
   Inside `unshackle/` (shim) never import `ulm` internals beyond the
   alias machinery.
3. **No heavy deps.** Before adding a PyPI dependency, try:
   a. stdlib, b. a lazy import inside the feature that needs it,
   c. then justify the addition here in a PR note.
   Current allowed/set deps: click, requests, PyYAML, pysubs2,
   pywidevine, pyplayready.
4. **Downloads must stream.** Any new network code reads chunked
   (≤1 MiB buffers), never whole-body into memory.
5. **Never mutate module-level state** (`config`, the vault list,
   registries). Pass what you need as arguments. In particular never
   write `config.<anything> = ...` at runtime.
6. **No console-as-control-flow.** Logs go through `ulm.core.console.log`
   / standard `logging`; progress goes through the callback the caller
   passed. Do not `print()` from library code.
7. **Tests:** stdlib `unittest`-style cases go in `tests/`; they must
   not need network. See `tests/test_mp4_init.py` for the pattern
   (box-structure verification without third-party deps).

## Geometry

```
ulm/            # the package (real code — this is the product)
  core/         # config, session, downloader, tracks, titles, manifests/, cdm, vaults, muxer, service, version
  core/utils/   # language tags, cookies, speedparse, mp4_init box writer, listing, serialization
  commands/     # CLI: dl | wvd | kv | env | cfg
unshackle/      # compatibility shim only (aliases ulm.*)
compat/         # compat documentation + adapters
tests/          # stdlib-unittest suite (no network needed)
pyproject.toml  # `ulm` + `unshackle = ulm.core.__main__:main` compat script
```

## Commit / push expectations

- Commit messages: present-tense, one-line subject under 72 chars,
  e.g. `core: fix segment resume on servers without Range support`.
- Never commit: `pat_token`, data dirs, device files, cookies,
  `.venv/`, `__pycache__/`, `*.ush` temp files. See `.gitignore`.

## Adding a service

1. Create `services/<TAG>/__init__.py`.
2. Subclass `ulm.core.service.Service` and implement `get_titles`,
   `get_tracks` (+ optionally `get_chapters`, license hooks).
3. Optional config keys for the service go in `services:
   <TAG>: ...` under `unshackle.yaml`.
4. Reload a dev install by restarting nothing: services import lazily.

## Release checklist

- `python -m pytest tests/` green
- `ulm -v`, `ulm --help`, `ulm env check` behave
- `pip install -e .` into a fresh venv from a clean checkout works
