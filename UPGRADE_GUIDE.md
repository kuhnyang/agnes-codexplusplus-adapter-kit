# Codex++ × Agnes AI：长期升级指南

## 推荐目录

把本目录作为独立的 Agnes Adapter 保存，不要把它放进 Codex++ 上游仓库的业务源码里再手工维护。

- `manifest.json`：Agnes Provider / 模型的单一事实来源
- `adapter.rs.tmpl`：全部 Agnes 特有 Rust 适配逻辑
- `sync_agnes_integration.py`：可重复、幂等的同步器

## 首次集成

```bash
python3 sync_agnes_integration.py --codex-root /path/to/CodexPlusPlus-main
```

建议同步后先执行：

```bash
python3 sync_agnes_integration.py --codex-root /path/to/CodexPlusPlus-main --check
```

然后按照上游 Codex++ 的构建流程构建。

## Codex++ 发布新版本后的升级

不要把新版本直接覆盖当前 Agnes 定制版本。保留 Adapter Kit，然后从新的上游源码重新生成：

```bash
python3 sync_agnes_integration.py \
  --codex-root /path/to/new/CodexPlusPlus-main
```

同步器会重新安装 Agnes 独立模块，并刷新带有 `CODEX++ Agnes Adapter` 标记的最小接入点。

再次执行：

```bash
python3 sync_agnes_integration.py \
  --codex-root /path/to/new/CodexPlusPlus-main \
  --check
```

如果上游代码重构导致必要的接入点消失，同步器会直接失败并指出具体文件/anchor，而不会悄悄生成一个看起来能编译、实际缺少 Agnes 路由的版本。

## 三个模型

- `agnes-2.5-flash`：Chat Completions
- `agnes-image-2.5-flash`：Images Generations
- `agnes-video-2.5-flash`：Videos + `video_id` polling

默认 Base URL：`https://apihub.agnes-ai.com/v1`

## 设计原则

1. OpenAI/其他现有 Provider 继续使用 Codex++ 原逻辑。
2. Agnes 模型不要求把 Agnes 切成全局 active provider；模型选择后由 Adapter 按模型自动路由。
3. Agnes 的模型 ID、Provider 地址和模型清单集中在 `manifest.json`，避免在多个文件复制字符串。
4. 业务逻辑集中于 `agnes_adapter.rs`，Codex++ 核心只保留少量标记接入点。
