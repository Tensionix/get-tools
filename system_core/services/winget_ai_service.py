from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import re

from system_core.config_resolver import (
    CONFIG_DIR,
    api_key_entries,
    get_path,
    load_settings,
    resolve_api_key,
    resolve_model,
)
from system_core.core.jobs import JobContext
from system_core.services import winget_service


MODEL_CACHE_PATH = CONFIG_DIR / "gui_model_cache.json"
KEY_CACHE_PATH = CONFIG_DIR / "gui_key_cache.json"
PROMPT_CACHE_PATH = CONFIG_DIR / "gui_package_prompt_cache.json"
PLAN_CACHE_PATH = CONFIG_DIR / "gui_package_plan_cache.json"
MODEL_CHECK_STALE_DAYS = 14
MODEL_CHECK_PROMPT = "Reply with OK."

DEFAULT_PLANNER_PROMPT = (
    "Подбери Windows/WinGet пакеты под задачу. Сначала предложи варианты, "
    "не устанавливай ничего. Отдавай практичный короткий план: что поставить, "
    "зачем, какие есть риски или неоднозначности."
)

PACKAGE_PLANNER_SYSTEM = """
You are Audion Get AI Package Planner.
Return only a JSON object.

Your job:
- Convert the user's Windows software request into a reviewable WinGet package plan.
- Prefer exact WinGet package IDs when you know them, but mark uncertain IDs as review_required.
- Do not invent commands that execute anything outside WinGet.
- Prefer stable package choices for Windows desktop/dev/media workflows.
- Keep the list focused; avoid huge broad package dumps unless explicitly requested.
- Treat "Known Audion package groups" as curated options only, not installed state.
- Say a package is installed only when it appears in "Already installed WinGet IDs"; otherwise avoid claiming installed status.

JSON schema:
{
  "summary": "short human-readable summary",
  "packages": [
    {
      "name": "display name",
      "query": "WinGet search query if ID is uncertain",
      "winget_id": "exact package id if known",
      "group": "system|dev|ai|pkms|office|media_images|media_audio|media_video|network|hardware|msvc|custom",
      "action": "install|update|pin|research",
      "reason": "why this package fits",
      "risk": "risk or review note"
    }
  ],
  "notes": ["optional notes"]
}
""".strip()


def _as_int(value: Any, default: int) -> int:
    try:
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        text = str(value).strip()
        if not text:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if text in {"0", "false", "no", "n", "off", "нет"}:
        return False
    return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json_cache(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        return payload if isinstance(payload, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def _write_json_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _option(value: str, label: str, label_ru: str | None = None, **extra: str) -> dict[str, str]:
    return {"value": value, "label": label, "label_ru": label_ru or label, **extra}


def _dedupe_options(options: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for option in options:
        value = str(option.get("value", "")).strip()
        key = value or str(option.get("label", "")).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(option)
    return out


def _read_model_cache() -> dict[str, Any]:
    return _read_json_cache(MODEL_CACHE_PATH, {"providers": {}})


def _write_model_cache(payload: dict[str, Any]) -> None:
    _write_json_cache(MODEL_CACHE_PATH, payload)


def _provider_cache(payload: dict[str, Any], provider: str) -> dict[str, Any]:
    providers = payload.setdefault("providers", {})
    if not isinstance(providers, dict):
        payload["providers"] = providers = {}
    provider_payload = providers.setdefault(provider, {})
    if not isinstance(provider_payload, dict):
        providers[provider] = provider_payload = {}
    provider_payload.setdefault("models", [])
    provider_payload.setdefault("pinned", [])
    provider_payload.setdefault("checks", {})
    return provider_payload


def _parse_checked_at(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _checked_date(value: str) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _model_check_entry(provider: str, model_id: str) -> dict[str, Any]:
    cache = _provider_cache(_read_model_cache(), provider)
    checks = cache.get("checks", {})
    if not isinstance(checks, dict):
        return {}
    entry = checks.get(str(model_id or "").strip(), {})
    return entry if isinstance(entry, dict) else {}


def _model_check_display_status(entry: dict[str, Any]) -> str:
    status = str(entry.get("status") or "").strip().lower()
    if status not in {"ok", "error", "no_access"}:
        return ""
    checked_at = _parse_checked_at(str(entry.get("checked_at") or ""))
    if checked_at is None:
        return status
    age = datetime.now(timezone.utc) - checked_at
    if age.days >= MODEL_CHECK_STALE_DAYS:
        return "stale"
    return status


def _model_status_prefix(provider: str, model_id: str) -> str:
    entry = _model_check_entry(provider, model_id)
    status = _model_check_display_status(entry)
    if not status:
        return ""
    checked_at = _checked_date(str(entry.get("checked_at") or ""))
    token = {
        "ok": "OK",
        "error": "ERR",
        "no_access": "NO ACCESS",
        "stale": "STALE",
    }.get(status, status.upper())
    return f"[{token} {checked_at}]" if checked_at else f"[{token}]"


def _model_label_with_status(provider: str, model_id: str, label: str | None = None) -> str:
    base = str(label or model_id).strip()
    prefix = _model_status_prefix(provider, model_id)
    return f"{prefix} {base}" if prefix else base


def _remember_model_check(provider: str, model_id: str, status: str, message: str, *, key_ref: str = "") -> None:
    model_id = str(model_id or "").strip()
    if not model_id or model_id.startswith("__"):
        return
    payload = _read_model_cache()
    cache = _provider_cache(payload, provider)
    checks = cache.setdefault("checks", {})
    if not isinstance(checks, dict):
        checks = {}
        cache["checks"] = checks
    checks[model_id] = {
        "status": status,
        "checked_at": _utc_now_iso(),
        "message": str(message or "").strip()[:500],
        "key_ref": str(key_ref or "").strip(),
    }
    models = [str(model).strip() for model in cache.get("models", []) if str(model).strip()]
    if model_id not in models:
        models.append(model_id)
    cache["models"] = sorted(set(models))
    _write_model_cache(payload)


def _cached_models(provider: str) -> list[str]:
    cache = _provider_cache(_read_model_cache(), provider)
    models = cache.get("models", [])
    return [str(model).strip() for model in models if str(model).strip()] if isinstance(models, list) else []


def _pinned_models(provider: str) -> list[str]:
    cache = _provider_cache(_read_model_cache(), provider)
    pinned = cache.get("pinned", [])
    return [str(model).strip() for model in pinned if str(model).strip()] if isinstance(pinned, list) else []


def _remember_models(provider: str, model_ids: list[str]) -> None:
    payload = _read_model_cache()
    cache = _provider_cache(payload, provider)
    cache["models"] = sorted({model for model in model_ids if model})
    cache["pinned"] = [model for model in _pinned_models(provider) if model]
    _write_model_cache(payload)


def _pin_model(provider: str, model_id: str) -> None:
    model_id = str(model_id or "").strip()
    if not model_id or model_id.startswith("__"):
        return
    payload = _read_model_cache()
    cache = _provider_cache(payload, provider)
    pinned = [str(model).strip() for model in cache.get("pinned", []) if str(model).strip()]
    if model_id not in pinned:
        pinned.insert(0, model_id)
    cache["pinned"] = pinned[:20]
    models = [str(model).strip() for model in cache.get("models", []) if str(model).strip()]
    if model_id not in models:
        models.append(model_id)
    cache["models"] = sorted(set(models))
    _write_model_cache(payload)


def _unpin_model(provider: str, model_id: str) -> None:
    model_id = str(model_id or "").strip()
    if not model_id or model_id.startswith("__"):
        return
    payload = _read_model_cache()
    cache = _provider_cache(payload, provider)
    cache["pinned"] = [str(model).strip() for model in cache.get("pinned", []) if str(model).strip() and str(model).strip() != model_id]
    _write_model_cache(payload)


def _delete_model_cache_entry(provider: str, model_id: str) -> None:
    model_id = str(model_id or "").strip()
    if not model_id or model_id.startswith("__"):
        return
    payload = _read_model_cache()
    cache = _provider_cache(payload, provider)
    cache["pinned"] = [str(model).strip() for model in cache.get("pinned", []) if str(model).strip() and str(model).strip() != model_id]
    cache["models"] = [str(model).strip() for model in cache.get("models", []) if str(model).strip() and str(model).strip() != model_id]
    checks = cache.get("checks", {})
    if isinstance(checks, dict):
        checks.pop(model_id, None)
    _write_model_cache(payload)


def _model_options_from_cache(provider: str) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for model_id in _pinned_models(provider):
        label = _model_label_with_status(provider, model_id)
        options.append(_option(model_id, label, label, pinned="true"))
    for model_id in _cached_models(provider):
        label = _model_label_with_status(provider, model_id)
        options.append(_option(model_id, label, label))
    return options


def _read_key_cache() -> dict[str, Any]:
    return _read_json_cache(KEY_CACHE_PATH, {"providers": {}})


def _write_key_cache(payload: dict[str, Any]) -> None:
    _write_json_cache(KEY_CACHE_PATH, payload)


def _key_provider_cache(payload: dict[str, Any], provider: str) -> dict[str, Any]:
    providers = payload.setdefault("providers", {})
    if not isinstance(providers, dict):
        payload["providers"] = providers = {}
    provider_payload = providers.setdefault(provider, {})
    if not isinstance(provider_payload, dict):
        providers[provider] = provider_payload = {}
    provider_payload.setdefault("pinned", [])
    return provider_payload


def _pinned_key_refs(provider: str) -> list[str]:
    cache = _key_provider_cache(_read_key_cache(), provider)
    pinned = cache.get("pinned", [])
    return [str(item).strip() for item in pinned if str(item).strip()] if isinstance(pinned, list) else []


def _pin_api_key(provider: str, key_ref: str) -> None:
    key_ref = str(key_ref or "").strip()
    if not key_ref or key_ref.startswith("__"):
        return
    known_refs = {entry.get("ref", "") for entry in api_key_entries(provider)}
    if key_ref not in known_refs:
        return
    payload = _read_key_cache()
    cache = _key_provider_cache(payload, provider)
    pinned = [str(item).strip() for item in cache.get("pinned", []) if str(item).strip()]
    if key_ref not in pinned:
        pinned.insert(0, key_ref)
    cache["pinned"] = pinned[:20]
    _write_key_cache(payload)


def _unpin_api_key(provider: str, key_ref: str) -> None:
    key_ref = str(key_ref or "").strip()
    if not key_ref or key_ref.startswith("__"):
        return
    payload = _read_key_cache()
    cache = _key_provider_cache(payload, provider)
    cache["pinned"] = [str(item).strip() for item in cache.get("pinned", []) if str(item).strip() and str(item).strip() != key_ref]
    _write_key_cache(payload)


def _api_key_option_label(entry: dict[str, str]) -> str:
    label = str(entry.get("label") or entry.get("ref") or "API key").strip()
    note = str(entry.get("note") or "").strip()
    return f"{label} - {note}" if note else label


def _api_key_options(provider: str) -> list[dict[str, str]]:
    options = [_option("", "Config/env default", "По умолчанию из env/config")]
    entries = api_key_entries(provider)
    by_ref = {entry.get("ref", ""): entry for entry in entries}
    for key_ref in _pinned_key_refs(provider):
        entry = by_ref.get(key_ref)
        if entry:
            label = _api_key_option_label(entry)
            options.append(_option(key_ref, label, label, pinned="true"))
    for entry in entries:
        key_ref = entry.get("ref", "")
        if not key_ref:
            continue
        label = _api_key_option_label(entry)
        options.append(_option(key_ref, label, label))
    if len(options) == 1:
        options.append(_option("__missing_key_file__", f"No {provider} key entries found", f"Нет ключей {provider}"))
    return _dedupe_options(options)


def openai_api_key_options(root: Path | str | None = None) -> list[dict[str, str]]:
    del root
    return _api_key_options("openai")


def gemini_api_key_options(root: Path | str | None = None) -> list[dict[str, str]]:
    del root
    return _api_key_options("gemini")


def _configured_model_options(provider: str, default_tier: str = "audit") -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    try:
        settings = load_settings()
        configured = str(resolve_model(provider, default_tier, settings) or "").strip()
        if configured:
            label = _model_label_with_status(provider, configured)
            options.append(_option("", f"Auto default ({default_tier}): {label}", f"Авто ({default_tier}): {label}"))
        else:
            options.append(_option("", "Select model from live/cache list", "Выберите модель из live/cache списка"))
    except Exception as exc:
        message = f"Model fallback failed: {exc.__class__.__name__}: {exc}"
        options.append(_option("__config_failed__", message, message))
    return options


def _key_ref_from_values(provider: str, values: dict[str, Any] | None) -> str:
    if not isinstance(values, dict):
        return ""
    return str(values.get(f"{provider}_api_key_ref") or "").strip()


def openai_model_options(root: Path | str | None = None, values: dict[str, Any] | None = None) -> list[dict[str, str]]:
    del root
    base_options = _configured_model_options("openai", "audit")
    cache_options = _model_options_from_cache("openai")
    options = [*base_options]
    try:
        from openai import OpenAI

        settings = load_settings()
        key_ref = _key_ref_from_values("openai", values)
        api_key = resolve_api_key("openai", settings, key_ref=key_ref)
        if not api_key:
            return [*base_options, *cache_options, _option("__missing_key__", "OpenAI API key not found", "OpenAI API key не найден")]
        client = OpenAI(api_key=api_key, timeout=20.0, max_retries=1)
        models = client.models.list()
        model_ids = sorted(
            str(model.id)
            for model in getattr(models, "data", []) or []
            if str(getattr(model, "id", "")).strip()
        )
        for model_id in model_ids:
            label = _model_label_with_status("openai", model_id)
            options.append(_option(model_id, label, label))
        _remember_models("openai", model_ids)
    except Exception as exc:
        message = f"OpenAI model request failed: {exc.__class__.__name__}: {exc}"
        options.extend(cache_options)
        options.append(_option("__request_failed__", message, message))
    return _dedupe_options(options)


def gemini_model_options(root: Path | str | None = None, values: dict[str, Any] | None = None) -> list[dict[str, str]]:
    del root
    base_options = _configured_model_options("gemini", "audit_fast")
    cache_options = _model_options_from_cache("gemini")
    options = [*base_options]
    try:
        from google import genai

        settings = load_settings()
        key_ref = _key_ref_from_values("gemini", values)
        api_key = resolve_api_key("gemini", settings, key_ref=key_ref)
        if not api_key:
            return [*base_options, *cache_options, _option("__missing_key__", "Gemini API key not found", "Gemini API key не найден")]
        client = genai.Client(api_key=api_key)
        model_ids: list[str] = []
        for model in client.models.list():
            raw_name = str(getattr(model, "name", "") or "").strip()
            if not raw_name:
                continue
            actions = getattr(model, "supported_actions", None)
            if actions and "generateContent" not in set(str(action) for action in actions):
                continue
            model_id = raw_name.split("/")[-1]
            label = model_id
            display_name = str(getattr(model, "display_name", "") or "").strip()
            if display_name and display_name != model_id:
                label = f"{model_id} - {display_name}"
            label = _model_label_with_status("gemini", model_id, label)
            options.append(_option(model_id, label, label))
            model_ids.append(model_id)
        _remember_models("gemini", model_ids)
    except Exception as exc:
        message = f"Gemini model request failed: {exc.__class__.__name__}: {exc}"
        options.extend(cache_options)
        options.append(_option("__request_failed__", message, message))
    return _dedupe_options(options)


def _selected_api_key_ref(params: dict[str, Any], provider: str) -> str:
    selected = str(params.get(f"{provider}_api_key_ref") or "").strip()
    return "" if selected.startswith("__") else selected


def _selected_model(params: dict[str, Any], provider: str, tier: str) -> str:
    override = str(params.get(f"{provider}_model_override") or "").strip()
    if override:
        return override
    selected = str(params.get(f"{provider}_model") or "").strip()
    if selected and not selected.startswith("__"):
        return selected
    try:
        return str(resolve_model(provider, tier, load_settings()) or "").strip()
    except Exception:
        return ""


def _api_key_label(provider: str, key_ref: str) -> str:
    if not key_ref:
        return "env/config default"
    for entry in api_key_entries(provider):
        if entry.get("ref") == key_ref:
            return _api_key_option_label(entry)
    return key_ref


def _classify_model_check_error(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    text = str(exc).lower()
    no_access_markers = {
        "not found",
        "not available",
        "does not exist",
        "permission",
        "forbidden",
        "unauthorized",
        "invalid api key",
        "api key not valid",
        "access",
        "404",
        "403",
        "401",
    }
    if status_code in {401, 403, 404} or any(marker in text for marker in no_access_markers):
        return "no_access"
    return "error"


def _check_openai_model(model: str, api_key: str) -> tuple[str, str]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=20.0, max_retries=0)
    client.responses.create(model=model, input=MODEL_CHECK_PROMPT, max_output_tokens=16)
    return "ok", "Responses API accepted the model."


def _check_gemini_model(model: str, api_key: str) -> tuple[str, str]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(temperature=0.0, max_output_tokens=8)
    client.models.generate_content(model=model, contents=MODEL_CHECK_PROMPT, config=config)
    return "ok", "generateContent accepted the model."


def _check_selected_model(context: JobContext, params: dict[str, Any]) -> None:
    provider = str(params.get("provider") or "openai").strip().lower()
    if provider not in {"openai", "gemini"}:
        raise RuntimeError(f"Unsupported model provider: {provider}")
    tier = "audit_fast" if provider == "gemini" else "audit"
    model = _selected_model(params, provider, tier)
    if not model:
        raise RuntimeError("No model selected or configured to check.")
    settings = load_settings()
    key_ref = _selected_api_key_ref(params, provider)
    api_key = resolve_api_key(provider, settings, key_ref=key_ref)
    if not api_key:
        message = "API key not found."
        _remember_model_check(provider, model, "no_access", message, key_ref=key_ref)
        context.log(f"[MODEL CHECK] {provider}: {model} -> no_access ({message})")
        context.progress(1.0)
        raise RuntimeError(f"Model check failed: {message}")
    try:
        if provider == "openai":
            status, message = _check_openai_model(model, api_key)
        else:
            status, message = _check_gemini_model(model, api_key)
    except Exception as exc:
        status = _classify_model_check_error(exc)
        message = f"{exc.__class__.__name__}: {str(exc)}"
    _remember_model_check(provider, model, status, message, key_ref=key_ref)
    context.log(f"[MODEL CHECK] {provider}: {model} -> {status} ({message[:240]})")
    context.progress(1.0)
    if status != "ok":
        raise RuntimeError(f"Model check {status}: {message[:300]}")


def _prompt_ref(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
    return digest[:16]


def _read_prompt_cache() -> dict[str, Any]:
    payload = _read_json_cache(PROMPT_CACHE_PATH, {"prompts": [], "pinned": []})
    payload.setdefault("prompts", [])
    payload.setdefault("pinned", [])
    return payload


def _prompt_entries() -> list[dict[str, Any]]:
    prompts = _read_prompt_cache().get("prompts", [])
    return [entry for entry in prompts if isinstance(entry, dict)]


def prompt_cache_entry(prompt_ref: str) -> dict[str, Any]:
    prompt_ref = str(prompt_ref or "").strip()
    if not prompt_ref:
        return {}
    for entry in _prompt_entries():
        if str(entry.get("ref") or "") == prompt_ref:
            return dict(entry)
    return {}


def _prompt_option_label(entry: dict[str, Any]) -> str:
    label = str(entry.get("label") or entry.get("ref") or "Prompt").strip()
    note = str(entry.get("note") or "").strip()
    used = int(entry.get("usage_count") or 0)
    details = f"used {used}" if used else ""
    text = f"{label} - {note}" if note else label
    return f"{text} ({details})" if details else text


def ai_package_prompt_options(root: Path | str | None = None) -> list[dict[str, str]]:
    del root
    options = [_option("", "Textarea / default package planner prompt", "Текстовое поле / prompt по умолчанию")]
    payload = _read_prompt_cache()
    entries = _prompt_entries()
    by_ref = {str(entry.get("ref") or ""): entry for entry in entries}
    for ref in [str(item).strip() for item in payload.get("pinned", []) if str(item).strip()]:
        entry = by_ref.get(ref)
        if entry:
            label = _prompt_option_label(entry)
            options.append(_option(ref, label, label, pinned="true"))
    frequent = sorted(entries, key=lambda item: int(item.get("usage_count") or 0), reverse=True)
    for entry in frequent:
        ref = str(entry.get("ref") or "").strip()
        if ref:
            label = _prompt_option_label(entry)
            options.append(_option(ref, label, label))
    return _dedupe_options(options)


def _save_prompt(content: str, *, label: str = "", note: str = "", pin: bool = False) -> dict[str, Any]:
    content = str(content or "").strip()
    if not content:
        raise RuntimeError("Prompt text is empty.")
    payload = _read_prompt_cache()
    prompts = [entry for entry in payload.get("prompts", []) if isinstance(entry, dict)]
    ref = _prompt_ref(content)
    found: dict[str, Any] | None = None
    for entry in prompts:
        if str(entry.get("ref") or "") == ref:
            found = entry
            break
    now = _utc_now_iso()
    if found is None:
        found = {
            "ref": ref,
            "label": label or f"Package prompt {now[:10]}",
            "note": note,
            "content": content,
            "created_at": now,
            "updated_at": now,
            "usage_count": 0,
        }
        prompts.insert(0, found)
    else:
        if label:
            found["label"] = label
        if note:
            found["note"] = note
        found["content"] = content
        found["updated_at"] = now
    payload["prompts"] = prompts[:100]
    if pin:
        pinned = [str(item).strip() for item in payload.get("pinned", []) if str(item).strip()]
        if ref not in pinned:
            pinned.insert(0, ref)
        payload["pinned"] = pinned[:20]
    _write_json_cache(PROMPT_CACHE_PATH, payload)
    return found


def _pin_prompt_ref(prompt_ref: str) -> dict[str, Any]:
    prompt_ref = str(prompt_ref or "").strip()
    if not prompt_ref or prompt_ref.startswith("__"):
        raise RuntimeError("Select a cached prompt to pin.")
    payload = _read_prompt_cache()
    entries = [entry for entry in payload.get("prompts", []) if isinstance(entry, dict)]
    entry = next((item for item in entries if str(item.get("ref") or "") == prompt_ref), None)
    if entry is None:
        raise RuntimeError("Selected prompt was not found in cache.")
    pinned = [str(item).strip() for item in payload.get("pinned", []) if str(item).strip()]
    if prompt_ref not in pinned:
        pinned.insert(0, prompt_ref)
    payload["pinned"] = pinned[:20]
    _write_json_cache(PROMPT_CACHE_PATH, payload)
    return entry


def _unpin_prompt_ref(prompt_ref: str) -> dict[str, Any]:
    prompt_ref = str(prompt_ref or "").strip()
    if not prompt_ref or prompt_ref.startswith("__"):
        raise RuntimeError("Select a cached prompt to unpin.")
    payload = _read_prompt_cache()
    entries = [entry for entry in payload.get("prompts", []) if isinstance(entry, dict)]
    entry = next((item for item in entries if str(item.get("ref") or "") == prompt_ref), None)
    if entry is None:
        raise RuntimeError("Selected prompt was not found in cache.")
    payload["pinned"] = [str(item).strip() for item in payload.get("pinned", []) if str(item).strip() and str(item).strip() != prompt_ref]
    _write_json_cache(PROMPT_CACHE_PATH, payload)
    return entry


def _delete_prompt_ref(prompt_ref: str) -> dict[str, Any]:
    prompt_ref = str(prompt_ref or "").strip()
    if not prompt_ref or prompt_ref.startswith("__"):
        raise RuntimeError("Select a cached prompt to delete.")
    payload = _read_prompt_cache()
    prompts = [entry for entry in payload.get("prompts", []) if isinstance(entry, dict)]
    deleted = next((item for item in prompts if str(item.get("ref") or "") == prompt_ref), None)
    if deleted is None:
        raise RuntimeError("Selected prompt was not found in cache.")
    payload["prompts"] = [entry for entry in prompts if str(entry.get("ref") or "") != prompt_ref]
    payload["pinned"] = [str(item).strip() for item in payload.get("pinned", []) if str(item).strip() and str(item).strip() != prompt_ref]
    _write_json_cache(PROMPT_CACHE_PATH, payload)
    return deleted


def _resolve_prompt(params: dict[str, Any]) -> tuple[str, str]:
    text = str(params.get("ai_prompt") or "").strip()
    selected_ref = str(params.get("ai_prompt_ref") or "").strip()
    if selected_ref and not selected_ref.startswith("__"):
        for entry in _prompt_entries():
            if str(entry.get("ref") or "") == selected_ref:
                content = str(entry.get("content") or "").strip()
                if text and content and text != content:
                    return text, ""
                if content:
                    return content, selected_ref
                break
        if not text:
            raise RuntimeError("Selected AI package prompt was not found in cache.")
    if text:
        return text, ""
    return DEFAULT_PLANNER_PROMPT, ""


def _remember_prompt_use(prompt_ref: str) -> None:
    prompt_ref = str(prompt_ref or "").strip()
    if not prompt_ref:
        return
    payload = _read_prompt_cache()
    changed = False
    for entry in payload.get("prompts", []):
        if isinstance(entry, dict) and str(entry.get("ref") or "") == prompt_ref:
            entry["usage_count"] = int(entry.get("usage_count") or 0) + 1
            entry["last_used_at"] = _utc_now_iso()
            changed = True
            break
    if changed:
        _write_json_cache(PROMPT_CACHE_PATH, payload)


def _package_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if winget_service.PACKAGE_ID_PATTERN.fullmatch(text) else ""


def _markdown_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _winget_search_rows(query: str, *, exact_id: bool = False, limit: int = 10) -> tuple[int, list[dict[str, str]], list[str]]:
    query = str(query or "").strip()
    if not query:
        return 1, [], ["Search query is empty."]
    args = ["search", "--source", "winget", "--accept-source-agreements", "--disable-interactivity"]
    if exact_id:
        args.extend(["--id", query, "-e"])
    else:
        args.append(query)
    result = winget_service._run_winget_capture(args, timeout=45.0)
    rows = winget_service._parse_winget_package_lines(result.lines)
    return result.exit_code, rows[: max(1, limit)], list(result.lines)


def _installed_id_set(root: Path) -> set[str]:
    try:
        return {
            str(option.get("value") or "").strip().lower()
            for option in winget_service.installed_package_options(root)
            if str(option.get("value") or "").strip()
        }
    except Exception:
        return set()


def _known_manifest_summary(root: Path, max_items: int = 160) -> str:
    try:
        fields = winget_service._manifest_package_fields(root)
    except Exception:
        return ""
    lines: list[str] = []
    count = 0
    for field_id, options in fields.items():
        group = field_id.replace("packages_", "")
        values = []
        for option in options:
            if count >= max_items:
                break
            values.append(f"{option.get('label') or option.get('value')}={option.get('value')}")
            count += 1
        if values:
            lines.append(f"{group}: " + "; ".join(values))
        if count >= max_items:
            break
    return "\n".join(lines)


def _call_llm_plan(context: JobContext, params: dict[str, Any], user_prompt: str) -> dict[str, Any]:
    provider = str(params.get("provider") or "openai").strip().lower()
    if provider not in {"openai", "gemini"}:
        raise RuntimeError(f"Unsupported LLM provider: {provider}")
    tier = "audit_fast" if provider == "gemini" else "audit"
    model = _selected_model(params, provider, tier)
    if not model:
        raise RuntimeError(f"No {provider} model selected or configured.")

    settings = load_settings()
    key_ref = _selected_api_key_ref(params, provider)
    api_key = resolve_api_key(provider, settings, key_ref=key_ref)
    if not api_key:
        raise RuntimeError(f"{provider} API key was not found.")
    context.log(f"[AI] provider={provider} model={model}")
    context.log(f"[AI KEY] {provider}: {_api_key_label(provider, key_ref)}")

    max_retries = _as_int(params.get("llm_max_retries"), int(get_path(settings, "generation.max_retries", 2) or 2))
    max_output_tokens = _as_int(params.get("llm_max_output_tokens"), 5000)
    if provider == "openai":
        from openai import OpenAI
        from system_core.providers.openai_provider import call_json_object

        client = OpenAI(api_key=api_key, timeout=_as_float(params.get("llm_timeout_sec"), 240.0), max_retries=0)
        obj, usage, tier_name = call_json_object(
            client,
            model=model,
            instructions=PACKAGE_PLANNER_SYSTEM,
            user_prompt=user_prompt,
            reasoning_effort=str(params.get("openai_reasoning") or "low").strip(),
            max_output_tokens=max_output_tokens,
            timeout_sec=_as_float(params.get("llm_timeout_sec"), 240.0),
            max_retries=max_retries,
            service_tier=str(get_path(settings, "providers.openai.service_tier", "default") or "default"),
            use_idempotency=False,
            doc_hash="winget-package-plan",
            chunk_index=0,
            verbosity="low",
        )
    else:
        from google import genai
        from system_core.providers.gemini_provider import call_structured

        client = genai.Client(api_key=api_key)
        obj, usage, tier_name = call_structured(
            client,
            model=model,
            system_instruction=PACKAGE_PLANNER_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.0,
            max_retries=max_retries,
        )
    context.log(f"[AI USAGE] {json.dumps(usage, ensure_ascii=False)} tier={tier_name}")
    return obj if isinstance(obj, dict) else {}


def _normalize_llm_packages(obj: dict[str, Any]) -> list[dict[str, str]]:
    raw_packages = obj.get("packages", [])
    if not isinstance(raw_packages, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw_packages:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        query = str(item.get("query") or name).strip()
        winget_id = str(item.get("winget_id") or item.get("id") or "").strip()
        group = str(item.get("group") or "custom").strip().lower()
        action = str(item.get("action") or "install").strip().lower()
        reason = str(item.get("reason") or "").strip()
        risk = str(item.get("risk") or "").strip()
        if not any([name, query, winget_id]):
            continue
        result.append(
            {
                "name": name,
                "query": query or winget_id,
                "winget_id": winget_id,
                "group": group,
                "action": action,
                "reason": reason,
                "risk": risk,
            }
        )
    return result


def _validate_plan_packages(
    context: JobContext,
    packages: list[dict[str, str]],
    *,
    search_limit: int,
    installed_ids: set[str],
) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    total = max(1, len(packages))
    for index, package in enumerate(packages, start=1):
        candidate_id = _package_id(package.get("winget_id", ""))
        query = str(package.get("query") or package.get("name") or candidate_id).strip()
        status = "review_required"
        rows: list[dict[str, str]] = []
        exact_row: dict[str, str] | None = None

        if candidate_id:
            exit_code, rows, _lines = _winget_search_rows(candidate_id, exact_id=True, limit=search_limit)
            if exit_code == 0 and rows:
                exact_row = rows[0]
                status = "validated_exact"
            else:
                status = "id_not_found"

        if exact_row is None and query:
            _exit_code, rows, _lines = _winget_search_rows(query, exact_id=False, limit=search_limit)
            if rows:
                status = "search_candidates" if not candidate_id else status
                if not candidate_id and len(rows) == 1:
                    exact_row = rows[0]
                    candidate_id = str(exact_row.get("value") or "").strip()
                    status = "single_search_candidate"

        row = exact_row or (rows[0] if rows else {})
        selected_id = str(row.get("value") or candidate_id or "").strip()
        installed = selected_id.lower() in installed_ids if selected_id else False
        command = ""
        if selected_id and status in {"validated_exact", "single_search_candidate"}:
            command = (
                f"winget install --id {selected_id} -e --source winget "
                "--accept-package-agreements --accept-source-agreements --no-upgrade"
            )
        item = {
            **package,
            "winget_id": selected_id or candidate_id,
            "name": str(row.get("name") or package.get("name") or ""),
            "version": str(row.get("version") or ""),
            "source": str(row.get("source") or ""),
            "validation_status": status,
            "installed": installed,
            "install_command": command,
            "candidates": rows,
        }
        validated.append(item)
        context.log(
            "[AI PLAN] "
            f"{item.get('name') or item.get('query')} | {item.get('winget_id') or '?'} | "
            f"{status} | installed={installed}"
        )
        context.progress(min(0.95, 0.35 + (index / total) * 0.55))
    return validated


def _write_plan_cache(plan: dict[str, Any]) -> None:
    _write_json_cache(PLAN_CACHE_PATH, plan)


def ai_plan_package_options(root: Path | str | None = None) -> list[dict[str, str]]:
    del root
    payload = _read_json_cache(PLAN_CACHE_PATH, {"packages": []})
    packages = payload.get("packages", [])
    options: list[dict[str, str]] = []
    if not isinstance(packages, list):
        return [_option("", "No AI package plan cache", "Нет кэша AI-плана")]
    for item in packages:
        if not isinstance(item, dict):
            continue
        package_id = _package_id(item.get("winget_id"))
        if not package_id:
            continue
        status = str(item.get("validation_status") or "").strip()
        if status not in {"validated_exact", "single_search_candidate"}:
            continue
        name = str(item.get("name") or item.get("query") or package_id).strip()
        installed = "installed" if bool(item.get("installed")) else "missing"
        reason = str(item.get("reason") or "").strip()
        label = f"{name} | {package_id} | {installed}"
        if reason:
            label = f"{label} | {reason[:80]}"
        options.append(_option(package_id, label, label))
    if not options:
        return [_option("", "No exact validated IDs in last AI plan", "В последнем AI-плане нет точных проверенных ID")]
    return _dedupe_options(options)


def _write_plan_report(context: JobContext, plan: dict[str, Any]) -> dict[str, str]:
    json_path = context.report_dir / "ai_package_plan.json"
    md_path = context.report_dir / "ai_package_plan.md"
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# AI Package Plan",
        "",
        str(plan.get("summary") or ""),
        "",
        f"- Provider: `{plan.get('provider', '')}`",
        f"- Model: `{plan.get('model', '')}`",
        f"- Updated: `{plan.get('updated_at', '')}`",
        "",
        "| Name | WinGet ID | Status | Installed | Version | Source | Risk | Command |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    packages = plan.get("packages", [])
    if isinstance(packages, list):
        for item in packages:
            if not isinstance(item, dict):
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_escape(item.get("name") or item.get("query")),
                        f"`{_markdown_escape(item.get('winget_id'))}`",
                        _markdown_escape(item.get("validation_status")),
                        "yes" if item.get("installed") else "no",
                        _markdown_escape(item.get("version")),
                        _markdown_escape(item.get("source")),
                        _markdown_escape(item.get("risk")),
                        f"`{_markdown_escape(item.get('install_command'))}`" if item.get("install_command") else "",
                    ]
                )
                + " |"
            )
    notes = plan.get("notes", [])
    if isinstance(notes, list) and notes:
        lines.extend(["", "## Notes", ""])
        for note in notes:
            lines.append(f"- {_markdown_escape(note)}")
    md_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"json": str(json_path), "markdown": str(md_path)}


def run_ai_package_planner(context: JobContext) -> dict[str, Any]:
    params = dict(context.operation.parameters)
    prompt, prompt_ref = _resolve_prompt(params)
    _remember_prompt_use(prompt_ref)
    request = str(params.get("package_request") or "").strip()
    if not request:
        raise RuntimeError("Package request is empty.")

    installed_ids = _installed_id_set(context.paths.root) if _as_bool(params.get("include_installed_scan"), True) else set()
    known = _known_manifest_summary(context.paths.root) if _as_bool(params.get("include_known_packages"), True) else ""
    user_prompt = "\n\n".join(
        part
        for part in [
            f"User package request:\n{request}",
            f"Planner instructions:\n{prompt}",
            "Output contract:\nReturn a JSON object only, following the package plan schema from the system instructions.",
            f"Known Audion package groups:\n{known}" if known else "",
            (
                "Already installed WinGet IDs:\n"
                + ", ".join(sorted(installed_ids)[:500])
                if installed_ids
                else ""
            ),
        ]
        if part
    )

    provider = str(params.get("provider") or "openai").strip().lower()
    tier = "audit_fast" if provider == "gemini" else "audit"
    model = _selected_model(params, provider, tier)
    obj = _call_llm_plan(context, params, user_prompt)
    context.progress(0.35)
    packages = _normalize_llm_packages(obj)
    if not packages:
        raise RuntimeError("The model returned no package suggestions.")

    validated = _validate_plan_packages(
        context,
        packages,
        search_limit=_as_int(params.get("search_limit"), 5),
        installed_ids=installed_ids,
    )
    plan = {
        "updated_at": _utc_now_iso(),
        "provider": provider,
        "model": model,
        "prompt_ref": prompt_ref,
        "request": request,
        "summary": str(obj.get("summary") or "").strip(),
        "notes": obj.get("notes", []) if isinstance(obj.get("notes", []), list) else [],
        "packages": validated,
    }
    _write_plan_cache(plan)
    report = _write_plan_report(context, plan)
    context.log(f"[AI PLAN] report: {report['markdown']}")
    context.progress(1.0)
    return {"plan": str(PLAN_CACHE_PATH), "report": report, "packages": len(validated)}


def search_winget_request(context: JobContext) -> dict[str, Any]:
    params = dict(context.operation.parameters)
    query = str(params.get("search_query") or params.get("package_request") or "").strip()
    if not query:
        raise RuntimeError("Search query is empty.")
    limit = _as_int(params.get("search_limit"), 20)
    exact = _as_bool(params.get("exact_id_search"), False)
    exit_code, rows, lines = _winget_search_rows(query, exact_id=exact, limit=limit)
    context.log(f"[WINGET SEARCH] query={query} exact={exact} exit={exit_code} rows={len(rows)}")
    for row in rows:
        context.log(
            f"[FOUND] {row.get('name') or row.get('label') or ''} | "
            f"{row.get('value') or ''} | {row.get('version') or ''} | {row.get('source') or ''}"
        )

    payload = {
        "query": query,
        "exact": exact,
        "exit_code": exit_code,
        "rows": rows,
        "raw_lines": lines,
        "updated_at": _utc_now_iso(),
    }
    json_path = context.report_dir / "winget_search.json"
    md_path = context.report_dir / "winget_search.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = [
        "# WinGet Search",
        "",
        f"Query: `{_markdown_escape(query)}`",
        "",
        "| Name | ID | Version | Source |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        markdown.append(
            f"| {_markdown_escape(row.get('name') or row.get('label'))} | "
            f"`{_markdown_escape(row.get('value'))}` | "
            f"{_markdown_escape(row.get('version'))} | {_markdown_escape(row.get('source'))} |"
        )
    if not rows:
        markdown.append("| No results |  |  |  |")
    md_path.write_text("\n".join(markdown), encoding="utf-8", newline="\n")
    context.progress(1.0)
    if exit_code != 0 and not rows:
        raise RuntimeError(f"WinGet search failed with exit code {exit_code}.")
    return {"query": query, "rows": len(rows), "report": {"json": str(json_path), "markdown": str(md_path)}}


def run_selected_ai_plan_packages(context: JobContext) -> dict[str, Any]:
    params = dict(context.operation.parameters)
    raw = params.get("packages_ai_plan", [])
    if isinstance(raw, (list, tuple)):
        packages = [_package_id(item) for item in raw]
    else:
        packages = [_package_id(raw)]
    packages = [package_id for package_id in packages if package_id]
    if not packages:
        raise RuntimeError("Select at least one exact package ID from the last AI plan.")
    action = str(params.get("package_action") or "install").strip().lower()
    if action not in {"install", "update", "pin", "check", "uninstall"}:
        raise RuntimeError(f"Unsupported package action: {action}")
    return winget_service._run_package_batch(context, action, packages)


def install_exact_package(context: JobContext) -> dict[str, Any]:
    package_id = _package_id(context.operation.parameters.get("package_id"))
    if not package_id:
        raise RuntimeError("Package ID does not look like an exact WinGet ID.")
    return winget_service.install_package_by_id(context)


def update_exact_package(context: JobContext) -> dict[str, Any]:
    package_id = _package_id(context.operation.parameters.get("package_id"))
    if not package_id:
        raise RuntimeError("Package ID does not look like an exact WinGet ID.")
    return winget_service._run_package_batch(context, "update", [package_id])


def uninstall_exact_package(context: JobContext) -> dict[str, Any]:
    package_id = _package_id(context.operation.parameters.get("package_id"))
    if not package_id:
        raise RuntimeError("Package ID does not look like an exact WinGet ID.")
    return winget_service.uninstall_package_by_id(context)


def add_exact_package_to_list(context: JobContext) -> dict[str, Any]:
    package_id = _package_id(context.operation.parameters.get("package_id"))
    if not package_id:
        raise RuntimeError("Package ID does not look like an exact WinGet ID.")
    return winget_service.add_package_to_list(context)


def run_ai_package_control(context: JobContext) -> dict[str, Any]:
    params = dict(context.operation.parameters)
    mode = str(params.get("mode") or "").strip().lower()
    provider = str(params.get("provider") or "openai").strip().lower()
    if mode == "pin_api_key":
        key_ref = _selected_api_key_ref(params, provider)
        if not key_ref:
            raise RuntimeError("No API key selected for favorites.")
        _pin_api_key(provider, key_ref)
        context.log(f"[API KEY FAV] {provider}: {_api_key_label(provider, key_ref)}")
    elif mode == "unpin_api_key":
        key_ref = _selected_api_key_ref(params, provider)
        if not key_ref:
            raise RuntimeError("No API key selected to unpin.")
        _unpin_api_key(provider, key_ref)
        context.log(f"[API KEY UNPIN] {provider}: {_api_key_label(provider, key_ref)}")
    elif mode == "pin_model":
        tier = "audit_fast" if provider == "gemini" else "audit"
        model = _selected_model(params, provider, tier)
        if not model:
            raise RuntimeError("No model selected for favorites.")
        _pin_model(provider, model)
        context.log(f"[MODEL FAV] {provider}: {model}")
    elif mode == "unpin_model":
        tier = "audit_fast" if provider == "gemini" else "audit"
        model = _selected_model(params, provider, tier)
        if not model:
            raise RuntimeError("No model selected to unpin.")
        _unpin_model(provider, model)
        context.log(f"[MODEL UNPIN] {provider}: {model}")
    elif mode == "delete_model":
        tier = "audit_fast" if provider == "gemini" else "audit"
        model = _selected_model(params, provider, tier)
        if not model:
            raise RuntimeError("No model selected to delete from cache.")
        _delete_model_cache_entry(provider, model)
        context.log(f"[MODEL CACHE DELETE] {provider}: {model}")
    elif mode == "check_model":
        _check_selected_model(context, params)
        return {"mode": mode}
    elif mode == "save_prompt":
        prompt = str(params.get("ai_prompt") or "").strip()
        entry = _save_prompt(
            prompt,
            label=str(params.get("ai_prompt_label") or "").strip(),
            note=str(params.get("ai_prompt_note") or "").strip(),
            pin=False,
        )
        context.log(f"[AI PROMPT SAVE] {_prompt_option_label(entry)}")
    elif mode == "pin_prompt":
        prompt_ref = str(params.get("ai_prompt_ref") or "").strip()
        if prompt_ref:
            entry = _pin_prompt_ref(prompt_ref)
        else:
            prompt = str(params.get("ai_prompt") or "").strip()
            entry = _save_prompt(
                prompt,
                label=str(params.get("ai_prompt_label") or "").strip(),
                note=str(params.get("ai_prompt_note") or "").strip(),
                pin=True,
            )
        context.log(f"[AI PROMPT PIN] {_prompt_option_label(entry)}")
    elif mode == "unpin_prompt":
        prompt_ref = str(params.get("ai_prompt_ref") or "").strip()
        entry = _unpin_prompt_ref(prompt_ref)
        context.log(f"[AI PROMPT UNPIN] {_prompt_option_label(entry)}")
    elif mode == "delete_prompt":
        prompt_ref = str(params.get("ai_prompt_ref") or "").strip()
        entry = _delete_prompt_ref(prompt_ref)
        context.log(f"[AI PROMPT DELETE] {_prompt_option_label(entry)}")
    else:
        raise RuntimeError(f"Unknown AI package control mode: {mode}")
    context.progress(1.0)
    return {"mode": mode}
