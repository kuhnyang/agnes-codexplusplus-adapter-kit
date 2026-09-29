#!/usr/bin/env python3
"""Repeatably install/refresh the Agnes AI adapter in a Codex++ source tree."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

MARKER = "CODEX++ Agnes Adapter"
BEGIN = f"// BEGIN {MARKER}"
END = f"// END {MARKER}"


def die(message: str) -> None:
    raise SystemExit(f"Agnes sync failed: {message}")


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        die(f"missing file: {path}")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        die(f"required hook not found in {label}: {needle!r}")


def load_manifest(kit: Path) -> dict:
    try:
        manifest = json.loads(read(kit / "manifest.json"))
    except json.JSONDecodeError as exc:
        die(f"invalid manifest.json: {exc}")
    models = manifest.get("models")
    if not isinstance(models, list) or len(models) != 3:
        die("manifest.json must define exactly three Agnes models")
    if any(not isinstance(model.get("id"), str) or not model["id"].strip() for model in models):
        die("manifest.json contains an invalid model")
    return manifest


def replace_marked(text: str, block: str) -> str | None:
    pattern = re.compile(
        rf"^{re.escape(BEGIN)}.*?^{re.escape(END)}[ \t]*(?:\r?\n)?",
        re.MULTILINE | re.DOTALL,
    )
    if pattern.search(text):
        return pattern.sub(block.rstrip() + "\n", text, count=1)
    return None


def insert_after_line(text: str, anchor: str, block: str, label: str) -> str:
    replaced = replace_marked(text, block)
    if replaced is not None:
        return replaced
    pos = text.find(anchor)
    if pos < 0:
        die(f"required hook not found for {label}: {anchor!r}")
    pos += len(anchor)
    return text[:pos] + "\n" + block.rstrip() + "\n" + text[pos:]


def insert_before(text: str, anchor: str, block: str, label: str) -> str:
    replaced = replace_marked(text, block)
    if replaced is not None:
        return replaced
    pos = text.find(anchor)
    if pos < 0:
        die(f"required hook not found for {label}: {anchor!r}")
    return text[:pos] + block.rstrip() + "\n" + text[pos:]


def render_adapter(kit: Path, manifest: dict) -> str:
    text = read(kit / "adapter.rs.tmpl")
    replacements = {
        "__AGNES_PROVIDER_NAME__": manifest["providerName"],
        "__AGNES_TEXT_MODEL__": manifest["models"][0]["id"],
        "__AGNES_IMAGE_MODEL__": manifest["models"][1]["id"],
        "__AGNES_VIDEO_MODEL__": manifest["models"][2]["id"],
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def render_ts(manifest: dict) -> str:
    ids = [model["id"] for model in manifest["models"]]
    values = json.dumps(ids, ensure_ascii=False)
    hosts = json.dumps(["apihub.agnes-ai.com", "apihub.agnes-ai.cn", "api.agnes-ai.cn"])
    return f'''// {MARKER}: generated from integrations/agnes/manifest.json
export const AGNES_PROVIDER_ID = {json.dumps(manifest["providerId"], ensure_ascii=False)};
export const AGNES_PROVIDER_NAME = {json.dumps(manifest["providerName"], ensure_ascii=False)};
export const AGNES_BASE_URL = {json.dumps(manifest["baseUrl"], ensure_ascii=False)};
export const AGNES_API_KEY_URL = {json.dumps(manifest["apiKeyUrl"], ensure_ascii=False)};
export const AGNES_WEBSITE_URL = {json.dumps(manifest["websiteUrl"], ensure_ascii=False)};
export const AGNES_DEFAULT_MODEL = {json.dumps(ids[0], ensure_ascii=False)};
export const AGNES_MODELS = {values} as const;

const AGNES_HOSTS = new Set({hosts});

export function isAgnesBaseUrl(baseUrl: string): boolean {{
  try {{
    return AGNES_HOSTS.has(new URL(baseUrl.trim()).hostname.toLowerCase());
  }} catch {{
    return false;
  }}
}}

export function isAgnesModel(model: string): boolean {{
  const normalized = model.trim().toLowerCase();
  return AGNES_MODELS.some((candidate) => candidate.toLowerCase() === normalized);
}}
'''


def apply_lib(codex: Path) -> None:
    path = codex / "crates/codex-plus-core/src/lib.rs"
    text = read(path)
    block = f"{BEGIN} / rust module\npub mod agnes_adapter;\n{END}"
    text = insert_after_line(text, "pub mod ads;", block, "lib.rs")
    write(path, text)


def apply_protocol_proxy(codex: Path) -> None:
    path = codex / "crates/codex-plus-core/src/protocol_proxy.rs"
    text = read(path)
    # The old integrated v3 implementation was monolithic. Refuse to stack v4 on top of it.
    if "const AGNES_TEXT_MODEL" in text or "pub fn is_agnes_image_model" in text:
        die("found the older inline Agnes implementation; use the clean upstream tree or migrate once to v4")
    # Media responses return the final ProxyHttpResponse type. Do not place this hook in
    # open_chat_completions_proxy_request(), which returns the lower-level UpstreamProxyResponse.
    old = re.compile(
        rf"^{re.escape(BEGIN)} / media model routing\n.*?^{re.escape(END)}[ \t]*(?:\r?\n)?",
        re.MULTILINE | re.DOTALL,
    )
    text = old.sub("", text, count=1)
    hook = f'''{BEGIN} / responses media model routing
    if let Some(response) = crate::agnes_adapter::handle_media_model_response(body, None).await? {{
        return Ok(response);
    }}
{END}'''
    anchor = "    let is_compaction = request_has_compaction_trigger(&request_json);"
    text = insert_before(text, anchor, hook, "protocol_proxy.rs")
    text = text.replace("is_agnes_text_model(model)", "crate::agnes_adapter::is_text_model(model)")
    text = text.replace("is_agnes_upstream(&source)", "crate::agnes_adapter::is_upstream(&source)")
    text = text.replace("is_agnes_upstream(profile)", "crate::agnes_adapter::is_upstream(profile)")
    write(path, text)


def apply_launcher(codex: Path) -> None:
    path = codex / "crates/codex-plus-core/src/launcher.rs"
    text = read(path)
    hook = f'''{BEGIN} / media model routing
    if let Some(media_response) = match crate::agnes_adapter::handle_media_model_response(
        request_body,
        request_user_agent,
    )
    .await {{
        Ok(response) => response,
        Err(error) => {{
            let body = serde_json::to_vec(&serde_json::json!({{
                "status": "failed",
                "message": error.to_string()
            }}))?;
            write_http_response(
                stream,
                "502 Bad Gateway",
                "application/json; charset=utf-8",
                &body,
            )
            .await?;
            log_helper_response(
                "helper.agnes_media_model_failed",
                method,
                path,
                "502 Bad Gateway",
                remote_addr_text,
            );
            stream.shutdown().await?;
            return Ok(());
        }}
    }} {{
        write_http_response(
            stream,
            &media_response.status,
            &media_response.content_type,
            &media_response.body,
        )
        .await?;
        log_helper_response(
            if media_response.status.starts_with("200") {{
                "helper.agnes_media_model_ok"
            }} else {{
                "helper.agnes_media_model_error"
            }},
            method,
            path,
            &media_response.status,
            remote_addr_text,
        );
        stream.shutdown().await?;
        return Ok(());
    }}
{END}'''
    anchor = "    let request_json = serde_json::from_str::<serde_json::Value>(request_body).ok();"
    text = insert_after_line(text, anchor, hook, "launcher.rs")
    write(path, text)


def apply_model_catalog(codex: Path) -> None:
    path = codex / "crates/codex-plus-core/src/model_catalog.rs"
    text = read(path)
    if "augment_agnes_models" in text:
        write(path, text)
        return
    original = "            let catalog = relay_profile_model_catalog_value(&home, &profile);"
    local = f'''{BEGIN} / local catalog augmentation
            let catalog = augment_agnes_models(
                relay_profile_model_catalog_value(&home, &profile),
                &settings,
            );
{END}'''
    require(text, original, "model_catalog.rs")
    text = text.replace(original, local, 1)
    original_return = "    read_codex_model_catalog_from_home(&home, &env, client).await"
    fetched = f'''{BEGIN} / fetched catalog augmentation
    let catalog = read_codex_model_catalog_from_home(&home, &env, client).await;
    let catalog = if let Ok(settings) = SettingsStore::new(settings_path.clone()).load() {{
        augment_agnes_models(catalog, &settings)
    }} else {{
        catalog
    }};
    catalog
{END}'''
    require(text, original_return, "model_catalog.rs")
    text = text.replace(original_return, fetched, 1)
    helper = f'''{BEGIN} / catalog helper
fn augment_agnes_models(mut catalog: Value, settings: &crate::settings::BackendSettings) -> Value {{
    if !crate::agnes_adapter::is_configured(settings) {{
        return catalog;
    }}
    let Some(models) = catalog.get_mut("models").and_then(Value::as_array_mut) else {{
        return catalog;
    }};
    for model in crate::agnes_adapter::MODEL_IDS {{
        if !models.iter().any(|value| value.as_str() == Some(*model)) {{
            models.push(Value::String((*model).to_string()));
        }}
    }}
    catalog["provider_name"] = catalog
        .get("provider_name")
        .cloned()
        .unwrap_or_else(|| json!(crate::agnes_adapter::PROVIDER_NAME));
    catalog
}}
{END}
'''
    anchor = "fn relay_profile_model_catalog_value(home: &Path, profile: &RelayProfile) -> Value {"
    text = insert_before(text, anchor, helper, "model_catalog.rs helper")
    # The first SettingsStore call occurs before settings_path is reused later.
    text = text.replace("SettingsStore::new(settings_path).load()", "SettingsStore::new(settings_path.clone()).load()", 1)
    write(path, text)


def apply_manager(codex: Path, manifest: dict) -> None:
    src = codex / "apps/codex-plus-manager/src"
    write(src / "agnes.ts", render_ts(manifest))

    presets = src / "presets.ts"
    text = read(presets)
    import_line = 'import { AGNES_API_KEY_URL, AGNES_BASE_URL, AGNES_DEFAULT_MODEL, AGNES_MODELS, AGNES_PROVIDER_ID, AGNES_PROVIDER_NAME, AGNES_WEBSITE_URL } from "./agnes.ts";'
    if import_line not in text:
        text = import_line + "\n" + text
    block = f'''{BEGIN} / provider preset
  {{
    id: AGNES_PROVIDER_ID,
    name: AGNES_PROVIDER_NAME,
    websiteUrl: AGNES_WEBSITE_URL,
    apiKeyUrl: AGNES_API_KEY_URL,
    category: "third_party",
    baseUrl: AGNES_BASE_URL,
    protocol: "chatCompletions",
    model: AGNES_DEFAULT_MODEL,
    modelList: [...AGNES_MODELS],
  }},
{END}'''
    text = replace_marked(text, block) or text
    if BEGIN not in text:
        text = insert_before(text, "  // ── 聚合/中转 ──", block, "presets.ts")
    write(presets, text)

    routes = src / "model-routes.ts"
    text = read(routes)
    route_import = 'import { isAgnesBaseUrl } from "./agnes.ts";'
    if route_import not in text:
        text = route_import + "\n" + text
    # Remove old in-file Agnes URL helper when migrating a previous draft.
    text = re.sub(r"\nfunction isAgnesBaseUrl\(baseUrl: string\): boolean \{.*?\n\}\n", "\n", text, count=1, flags=re.DOTALL)
    write(routes, text)

    groups = src / "model-groups.ts"
    text = read(groups)
    if '["agnes", "Agnes AI"],' not in text:
        text = text.replace('  ["deepseek", "DeepSeek"],\n', '  ["deepseek", "DeepSeek"],\n  ["agnes", "Agnes AI"],\n', 1)
    write(groups, text)

    test = src / "agnes.test.ts"
    ids = json.dumps([m["id"] for m in manifest["models"]], ensure_ascii=False)
    default = json.dumps(manifest["models"][0]["id"], ensure_ascii=False)
    image = json.dumps(manifest["models"][1]["id"], ensure_ascii=False)
    write(test, f'''import assert from "node:assert/strict";
import test from "node:test";

import {{ AGNES_BASE_URL, AGNES_DEFAULT_MODEL, AGNES_MODELS, isAgnesBaseUrl, isAgnesModel }} from "./agnes.ts";

test("Agnes manifest exposes the supported models", () => {{
  assert.deepEqual([...AGNES_MODELS], {ids});
  assert.equal(AGNES_DEFAULT_MODEL, {default});
  assert.equal(isAgnesModel({image}), true);
  assert.equal(isAgnesModel("gpt-5.5"), false);
}});

test("Agnes base URL detection is host-based", () => {{
  assert.equal(isAgnesBaseUrl(AGNES_BASE_URL), true);
  assert.equal(isAgnesBaseUrl("https://APIHUB.AGNES-AI.COM/v1"), true);
  assert.equal(isAgnesBaseUrl("https://example.com/v1"), false);
}});
''')


def apply_renderer(codex: Path, manifest: dict) -> None:
    path = codex / "assets/inject/renderer-inject.js"
    text = read(path)
    ids = json.dumps([m["id"] for m in manifest["models"]], ensure_ascii=False)
    block = f'''{BEGIN} / model injection gate
  function codexPlusAgnesModelsPresent() {{
    return codexPlusModelNames().some((model) => {ids}.some((candidate) => String(model || "").toLowerCase() === candidate.toLowerCase()));
  }}

  function codexPlusModelInjectionEnabled() {{
    return codexPlusModelUnlockEnabled() || codexPlusAgnesModelsPresent();
  }}
{END}'''
    anchor = "  function codexPlusModelNames() {"
    text = insert_before(text, anchor, block, "renderer-inject.js model gate")
    text = text.replace("if (!codexPlusModelUnlockEnabled()) return payload;", "if (!codexPlusModelInjectionEnabled()) return payload;")
    text = text.replace("if (!codexPlusModelUnlockEnabled()) return result;", "if (!codexPlusModelInjectionEnabled()) return result;")
    text = text.replace("if (codexPlusModelUnlockEnabled()\n", "if (codexPlusModelInjectionEnabled()\n")
    text = text.replace("if (!codexPlusModelUnlockEnabled()) return;", "if (!codexPlusModelInjectionEnabled()) return;")
    write(path, text)


def copy_integration_docs(codex: Path, kit: Path, manifest: dict) -> None:
    root = codex / "integrations/agnes"
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(kit / "manifest.json", root / "manifest.json")
    shutil.copy2(kit / "adapter.rs.tmpl", root / "adapter.rs.tmpl")
    shutil.copy2(kit / "README.md", root / "README.md")
    upgrade = kit / "UPGRADE_GUIDE.md"
    if upgrade.exists():
        shutil.copy2(upgrade, root / "UPGRADE_GUIDE.md")
    write(codex / "crates/codex-plus-core/src/agnes_adapter.rs", render_adapter(kit, manifest))


def check_root(codex: Path) -> None:
    required = [
        "Cargo.toml",
        "apps/codex-plus-manager/package.json",
        "crates/codex-plus-core/src/lib.rs",
        "crates/codex-plus-core/src/protocol_proxy.rs",
        "crates/codex-plus-core/src/launcher.rs",
        "crates/codex-plus-core/src/model_catalog.rs",
        "apps/codex-plus-manager/src/presets.ts",
        "apps/codex-plus-manager/src/model-routes.ts",
        "apps/codex-plus-manager/src/model-groups.ts",
        "assets/inject/renderer-inject.js",
    ]
    missing = [str(codex / item) for item in required if not (codex / item).exists()]
    if missing:
        die("not a Codex++ source tree; missing:\n  " + "\n  ".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-root", type=Path, required=True)
    parser.add_argument("--kit-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    codex = args.codex_root.resolve()
    kit = args.kit_root.resolve()
    check_root(codex)
    manifest = load_manifest(kit)

    if args.check:
        print(f"Agnes sync check OK: {codex}")
        return 0

    copy_integration_docs(codex, kit, manifest)
    apply_lib(codex)
    apply_protocol_proxy(codex)
    apply_launcher(codex)
    apply_model_catalog(codex)
    apply_manager(codex, manifest)
    apply_renderer(codex, manifest)
    print(f"Agnes adapter synchronized into {codex}")
    print("The adapter kit remains independent; use the same kit after future Codex++ upgrades.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
