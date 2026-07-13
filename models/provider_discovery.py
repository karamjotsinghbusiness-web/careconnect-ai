import json
import logging
import os
import queue
import re
import threading
from urllib.parse import urlparse

import pandas as pd
from openai import OpenAI


logger = logging.getLogger("careconnect")
_SEARCH_SLOTS = threading.BoundedSemaphore(2)
_NORMALIZATION_SLOTS = threading.BoundedSemaphore(2)


def _bounded_timeout(env_name, default, minimum, maximum):
    try:
        value = float(os.getenv(env_name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)

    # Web-search answers can wrap the requested JSON in a short sentence or
    # citation block. Extract the outer JSON object instead of throwing away
    # otherwise valid provider results.
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]

    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        logger.warning("OpenAI provider discovery returned invalid JSON")
        return {}


def _clean_rows(rows, kind, limit):
    cleaned = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        url = str(row.get("source_url", "")).strip()
        parsed_url = urlparse(url)
        if not name or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            continue
        name_column = {
            "provider": "provider_name",
            "clinic": "clinic_name",
            "advocate": "advocate_name",
        }[kind]
        role_column = "role" if kind == "advocate" else "specialty"
        cleaned.append({
            name_column: name[:200],
            role_column: str(row.get("role_or_specialty", "Unknown"))[:150],
            "city": str(row.get("city", ""))[:100],
            "state": str(row.get("state", "MO"))[:30],
            "phone": str(row.get("phone", "Not listed"))[:50],
            "website": url[:500],
            "source_url": url[:500],
            "source": "OpenAI web search (supplemental)",
            "verification_status": "Unverified — confirm directly before use",
        })
        if len(cleaned) >= limit:
            break
    return cleaned


def _normalize_condition_with_openai(condition):
    response = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0).responses.create(
        model=os.getenv("OPENAI_NORMALIZATION_MODEL", "gpt-4.1-mini").strip(),
        instructions=(
            "Correct spelling and spacing in the patient-entered symptom or condition so a "
            "healthcare navigation matcher can understand it. Preserve the user's meaning, do "
            "not diagnose, do not add symptoms, and do not provide medical advice. If it is "
            "already clear, return it unchanged. Examples: 'hedace' becomes 'headache'; "
            "'stomch pane' becomes 'stomach pain'; 'hart atack' becomes 'heart attack'."
        ),
        input=str(condition)[:300],
        text={
            "format": {
                "type": "json_schema",
                "name": "condition_normalization",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "normalized_condition": {"type": "string"},
                        "changed": {"type": "boolean"},
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": ["normalized_condition", "changed", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        max_output_tokens=120,
        store=False,
        timeout=_bounded_timeout("OPENAI_NORMALIZATION_TIMEOUT_SECONDS", 2.5, 1, 3),
    )
    result = _parse_json(response.output_text)
    normalized = str(result.get("normalized_condition", "")).strip()[:300]
    if not normalized:
        raise ValueError("OpenAI returned an empty normalized condition")
    return {
        "entered_condition": str(condition)[:300],
        "normalized_condition": normalized,
        "changed": normalized.casefold() != str(condition).strip().casefold(),
        "confidence": result.get("confidence", "low"),
        "used_openai": True,
    }


def normalize_condition(condition):
    """Correct spelling with OpenAI without blocking the request indefinitely."""
    entered = str(condition or "").strip()[:300]
    fallback = {
        "entered_condition": entered,
        "normalized_condition": entered,
        "changed": False,
        "confidence": "local_fallback",
        "used_openai": False,
    }
    if not entered or not os.getenv("OPENAI_API_KEY"):
        return fallback
    if not _NORMALIZATION_SLOTS.acquire(blocking=False):
        return fallback

    results = queue.Queue(maxsize=1)

    def run_normalization():
        try:
            results.put(_normalize_condition_with_openai(entered))
        except Exception as exc:
            logger.warning("OpenAI condition normalization failed: %s", type(exc).__name__)
        finally:
            _NORMALIZATION_SLOTS.release()

    worker = threading.Thread(target=run_normalization, daemon=True)
    worker.start()
    worker.join(_bounded_timeout("OPENAI_NORMALIZATION_HARD_LIMIT_SECONDS", 3.5, 2, 5))
    if worker.is_alive():
        logger.warning("OpenAI condition normalization exceeded its request budget")
        return fallback
    try:
        return results.get_nowait()
    except queue.Empty:
        return fallback


def _discover_supplemental_resources(city, specialty, condition, limit=3):
    """Find public listings to supplement, never replace, the local dataset."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or os.getenv("ENABLE_OPENAI_PROVIDER_SEARCH", "true").lower() != "true":
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    prompt = f"""
Search the public web for currently listed healthcare resources in or near {city}, Missouri.
Find up to {limit} providers relevant to specialty: {specialty} (patient-entered concern: {condition})
up to {limit} clinics or community health centers, and up to {limit} patient advocates,
care navigators, social workers, or nonprofit navigation services.

Use only listings supported by a public source page. Do not invent names, phone numbers, or URLs.
This is navigation information, not diagnosis. Return ONLY valid JSON with this exact shape:
{{"providers":[{{"name":"","role_or_specialty":"","city":"","state":"MO","phone":"","source_url":""}}],
"clinics":[{{"name":"","role_or_specialty":"","city":"","state":"MO","phone":"","source_url":""}}],
"advocates":[{{"name":"","role_or_specialty":"","city":"","state":"MO","phone":"","source_url":""}}]}}
"""

    try:
        search_model = os.getenv("OPENAI_SEARCH_MODEL", "gpt-4.1-mini").strip()
        # Reasoning models can exceed Railway's synchronous edge budget for a
        # simple local listing lookup. GPT-4.1 mini supports Responses web
        # search and is the low-latency path for this request.
        if search_model in {"gpt-5-mini", "gpt-5.4-mini"}:
            search_model = "gpt-4.1-mini"

        response = OpenAI(api_key=api_key, max_retries=0).responses.create(
            model=search_model,
            tools=[{
                "type": "web_search",
                "search_context_size": "low",
                "user_location": {
                    "type": "approximate",
                    "country": "US",
                    "city": str(city)[:100],
                    "region": "Missouri",
                    "timezone": "America/Chicago",
                },
            }],
            # Search is required here: accepting an uncited model-memory answer
            # would undermine the public-source verification label.
            tool_choice="required",
            input=prompt,
            max_output_tokens=1400,
            store=False,
            # Keep the whole synchronous Railway request below its edge timeout.
            timeout=_bounded_timeout("OPENAI_SEARCH_TIMEOUT_SECONDS", 10, 3, 12),
        )
        result = _parse_json(response.output_text)
        providers = pd.DataFrame(_clean_rows(result.get("providers"), "provider", limit))
        clinics = pd.DataFrame(_clean_rows(result.get("clinics"), "clinic", limit))
        advocates = pd.DataFrame(_clean_rows(result.get("advocates"), "advocate", limit))
        return providers, clinics, advocates
    except Exception:
        logger.exception("OpenAI supplemental provider search failed")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


def discover_supplemental_resources(city, specialty, condition, limit=3):
    """Run public-resource search with a hard wall-clock request budget."""
    if not os.getenv("OPENAI_API_KEY") or os.getenv(
        "ENABLE_OPENAI_PROVIDER_SEARCH", "true"
    ).lower() != "true":
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    if not _SEARCH_SLOTS.acquire(blocking=False):
        logger.warning("OpenAI supplemental search skipped because search capacity is busy")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    results = queue.Queue(maxsize=1)

    def run_search():
        try:
            results.put(_discover_supplemental_resources(city, specialty, condition, limit))
        finally:
            _SEARCH_SLOTS.release()

    worker = threading.Thread(target=run_search, daemon=True)
    worker.start()
    worker.join(_bounded_timeout("OPENAI_SEARCH_HARD_LIMIT_SECONDS", 14, 5, 18))

    if worker.is_alive():
        logger.warning("OpenAI supplemental provider search exceeded its request budget")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    try:
        return results.get_nowait()
    except queue.Empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


def merge_supplemental(
    dataset_rows,
    supplemental_rows,
    name_columns,
    dataset_limit=5,
    supplemental_limit=3,
):
    dataset_rows = dataset_rows.copy()
    supplemental_rows = supplemental_rows.copy()
    if supplemental_rows.empty:
        return dataset_rows.head(dataset_limit)

    known = set()
    for column in name_columns:
        if column in dataset_rows.columns:
            known.update(dataset_rows[column].fillna("").astype(str).str.lower().str.strip())

    supplemental_name = next((c for c in name_columns if c in supplemental_rows.columns), None)
    if supplemental_name:
        supplemental_rows = supplemental_rows[
            ~supplemental_rows[supplemental_name].fillna("").astype(str).str.lower().str.strip().isin(known)
        ]
    # Keep the strongest dataset matches and then add genuinely new web results.
    # Do not apply one shared head() after concatenation: that previously hid all
    # supplemental rows whenever the dataset already contained five matches.
    return pd.concat(
        [dataset_rows.head(dataset_limit), supplemental_rows.head(supplemental_limit)],
        ignore_index=True,
        sort=False,
    )
