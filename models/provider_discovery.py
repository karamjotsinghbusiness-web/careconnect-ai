import json
import logging
import os
import re
from urllib.parse import urlparse

import pandas as pd
from openai import OpenAI


logger = logging.getLogger("careconnect")


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


def discover_supplemental_resources(city, specialty, condition, limit=3):
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
        search_model = os.getenv("OPENAI_SEARCH_MODEL", "gpt-5.4-mini").strip()
        # Older setup instructions used gpt-5-mini. Use the current fast model
        # that explicitly supports Responses API web search.
        if search_model == "gpt-5-mini":
            search_model = "gpt-5.4-mini"

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
            # Keep the whole synchronous Railway request below its edge timeout.
            timeout=_bounded_timeout("OPENAI_SEARCH_TIMEOUT_SECONDS", 16, 3, 18),
        )
        result = _parse_json(response.output_text)
        providers = pd.DataFrame(_clean_rows(result.get("providers"), "provider", limit))
        clinics = pd.DataFrame(_clean_rows(result.get("clinics"), "clinic", limit))
        advocates = pd.DataFrame(_clean_rows(result.get("advocates"), "advocate", limit))
        return providers, clinics, advocates
    except Exception:
        logger.exception("OpenAI supplemental provider search failed")
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
