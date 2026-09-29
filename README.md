# Codex++ × Agnes AI Adapter

This kit keeps the Agnes integration outside the upstream Codex++ source tree.
It installs only small, marked adapter hooks plus a provider adapter module.

## Apply to a Codex++ checkout

```bash
python3 sync_agnes_integration.py --codex-root /path/to/CodexPlusPlus-main
```

The command is idempotent. It reads `manifest.json`, renders the Agnes adapter,
then adds or refreshes only the Agnes-marked blocks. Existing OpenAI/other-provider
code is left in place.

## Upgrade Codex++ later

Keep this adapter kit in a separate directory/repository. When Codex++ updates,
use a fresh upstream checkout and run the same sync command again:

```bash
python3 sync_agnes_integration.py --codex-root /path/to/new/CodexPlusPlus-main
```

If an upstream refactor removes a required hook, the script stops with a precise
error instead of silently producing a broken build.

## Files owned by this kit

- `manifest.json` — Agnes provider/model source of truth
- `adapter.rs.tmpl` — all Agnes-specific Rust logic
- `sync_agnes_integration.py` — repeatable/idempotent integration installer

## Current official Agnes models

- `agnes-2.5-flash` — Chat Completions
- `agnes-image-2.5-flash` — Images Generations
- `agnes-video-2.5-flash` — Videos + `video_id` polling

The API base URL defaults to `https://apihub.agnes-ai.com/v1`.

## 升级指南

详见 `UPGRADE_GUIDE.md`。长期使用时只需要保留本 Adapter Kit；Codex++ 出新版本后，从新上游源码运行同一个 `sync_agnes_integration.py` 即可重新生成 Agnes 集成。
