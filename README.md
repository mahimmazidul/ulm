# ulm

🪶 **Light on RAM. Fast to finish. Made to run anywhere.**

A DRM-aware archival tool for movies, TV and music — HLS / DASH / ISM
manifest parsing, Widevine licensing, segment streaming, and muxing —
rewritten from the ground up to be small, fast, and friendly to phones,
seedboxes and anything with 1 GB of RAM.

```
ulm dl <manifest-or-title-url>   # download + mux
ulm wvd add <device.wvd>         # import a Widevine device
ulm kv add SERVICE KID KEY      # store a content key
ulm env check                    # verify ffmpeg/mkvmerge/mp4decrypt
ulm cfg set dl.workers 2         # persist a config value
```

## What's inside (`ulm/` package)

| Area | Module | Notes |
|---|---|---|
| Config | `ulm.core.config` | one `unshackle.yaml`, mutable once, all paths resolvable |
| HTTP | `ulm.core.session` | one `requests` Session, default timeouts, Retry-After aware |
| Downloader | `ulm.core.downloader` | streaming w/ 128K chunks, bounded RAM, `.ush-manifest` resume sidecars, optional shared speed cap |
| Tracks | `ulm.core.tracks` | Track / Video / Audio / Subtitle / Chapters / Tracks + DRMInfo; MP4 box sniffing with zero deps |
| Titles | `ulm.core.titles` | Movie / Episode / Song + template-based filenames |
| Manifests | `ulm.core.manifests.{hls,dash,ism}` | pure-Python parsers (no `m3u8`, no `lxml`), per-segment AES-128 for identity-keyed HLS |
| DRM | `ulm.core.cdm` | pywidevine local `.wvd` + remote CDMs, vault-friendly |
| Vaults | `ulm.core.vaults` | stdlib `sqlite3` vault (single indexed table), fan-out across configured backends |
| Mux | `ulm.core.muxer` | mkvmerge (preferred) or ffmpeg fallback, plain-text chapter files |
| CLI | `ulm.commands` | `dl`, `wvd`, `kv`, `env`, `cfg` |

## Install (from source, no root)

```shell
git clone https://github.com/mahimmazidul/ulm.git ulm && cd ulm
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
ulm --help
```

Requirements: Python 3.9+ and `ffmpeg`/`mkvmerge`/`mp4decrypt` on PATH
(`ulm env check` verifies). Everything else is Python.

## Compatibility with the old core

Old service modules (or anything written against `unshackle.core.*`)
keep working untouched: the `unshackle/` package here is a **shim** that
aliases every `ulm.*` import. See [`compat/README.md`](compat/README.md).

Services live in `services/<TAG>/__init__.py` and subclass
`ulm.core.service.Service` (or the old `unshackle.core.service.Service`).

## License

GPL-3.0. Archival use only, respect the law and the services you use.
