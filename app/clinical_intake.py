"""Clinician-entered intake structuring with mandatory human review.

The assistant organizes facts that a clinician entered.  It does not diagnose,
select codes, place orders, or write to an EHR.  Explicit form values always win
over narrative extraction, and every result remains a draft.
"""

import json
import logging
import os
import re


logger = logging.getLogger("careconnect")

MAX_NOTE_LENGTH = 6000


def _text(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _multiline(value, limit=MAX_NOTE_LENGTH):
    return str(value or "").strip()[:limit]


def _split_items(value, limit=20):
    if isinstance(value, list):
        values = value
    else:
        values = re.split(r"[\n;]+", str(value or ""))

    cleaned = []
    for item in values:
        text = _text(item, 250)
        if text and text.lower() not in {entry.lower() for entry in cleaned}:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _parse_json_object(value):
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _extract_labeled_sections(note):
    aliases = {
        "chief complaint": "chief_complaint",
        "cc": "chief_complaint",
        "reason for visit": "chief_complaint",
        "history": "history_present_illness",
        "hpi": "history_present_illness",
        "history of present illness": "history_present_illness",
        "review of systems": "review_of_systems",
        "ros": "review_of_systems",
        "allergies": "allergies",
        "medications": "medications",
        "meds": "medications",
        "vitals": "vitals",
        "assessment": "assessment",
        "impression": "assessment",
        "plan": "plan",
    }
    extracted = {}
    unmatched = []
    current = None

    for raw_line in _multiline(note).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z ]{2,32})\s*:\s*(.*)$", line)
        key = aliases.get(match.group(1).strip().lower()) if match else None
        if key:
            current = key
            value = match.group(2).strip()
            if value:
                extracted[key] = (extracted.get(key, "") + " " + value).strip()
        elif current:
            extracted[current] = (extracted.get(current, "") + " " + line).strip()
        else:
            unmatched.append(line)

    if unmatched and not extracted.get("history_present_illness"):
        extracted["history_present_illness"] = " ".join(unmatched)

    return extracted


def _extract_vitals(note):
    patterns = {
        "blood_pressure": r"\b(?:BP|blood pressure)\s*[:=-]?\s*(\d{2,3}\s*/\s*\d{2,3})\b",
        "heart_rate": r"\b(?:HR|heart rate|pulse)\s*[:=-]?\s*(\d{2,3})\b",
        "respiratory_rate": r"\b(?:RR|respiratory rate|respirations)\s*[:=-]?\s*(\d{1,2})\b",
        "oxygen_saturation": r"\b(?:SpO2|oxygen saturation|O2 sat)\s*[:=-]?\s*(\d{2,3})\s*%?",
        "temperature_f": r"\b(?:temp(?:erature)?)\s*[:=-]?\s*(\d{2,3}(?:\.\d+)?)\s*°?\s*[Ff]?\b",
        "weight_lb": r"\bweight\s*[:=-]?\s*(\d{2,3}(?:\.\d+)?)\s*(?:lb|lbs|pounds)?\b",
        "height_in": r"\bheight\s*[:=-]?\s*(\d{2,3}(?:\.\d+)?)\s*(?:in|inches)?\b",
        "pain_score": r"\bpain\s*[:=-]?\s*(\d{1,2})\s*(?:/\s*10)?\b",
    }
    found = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, note or "", flags=re.IGNORECASE)
        if match:
            found[key] = re.sub(r"\s+", "", match.group(1))
    return found


def _local_extract(note):
    sections = _extract_labeled_sections(note)
    return {
        "chief_complaint": _text(sections.get("chief_complaint"), 500),
        "history_present_illness": _text(sections.get("history_present_illness"), 2000),
        "review_of_systems": _text(sections.get("review_of_systems"), 1500),
        "allergies": _split_items(sections.get("allergies")),
        "medications": _split_items(sections.get("medications")),
        "assessment": _text(sections.get("assessment"), 1500),
        "plan": _text(sections.get("plan"), 1500),
        "vitals": _extract_vitals(note),
    }


def _openai_extract(note):
    if not note or not os.environ.get("OPENAI_API_KEY"):
        return {}
    if os.environ.get("ENABLE_OPENAI_CLINICAL_INTAKE", "true").lower() != "true":
        return {}

    from openai import OpenAI

    instructions = """
You organize clinician-entered intake text into a structured draft.
Extract only facts explicitly present in the note. Never diagnose, infer a
condition, add a medical code, recommend treatment, or fill a missing value.
Keep the clinician's meaning and uncertainty. Return only valid JSON:
{
  "chief_complaint":"",
  "history_present_illness":"",
  "review_of_systems":"",
  "allergies":[],
  "medications":[],
  "assessment":"",
  "plan":"",
  "vitals":{
    "blood_pressure":"", "heart_rate":"", "respiratory_rate":"",
    "oxygen_saturation":"", "temperature_f":"", "weight_lb":"",
    "height_in":"", "pain_score":""
  }
}
"""

    response = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), max_retries=0).responses.create(
        model=os.environ.get("OPENAI_CLINICAL_MODEL", "gpt-4.1-mini"),
        instructions=instructions,
        input=note,
        max_output_tokens=1800,
        timeout=float(os.environ.get("OPENAI_CLINICAL_TIMEOUT_SECONDS", "15")),
    )
    return _parse_json_object(response.output_text)


def _merge_extraction(local, ai):
    if not ai:
        return local
    merged = dict(local)
    for key in (
        "chief_complaint", "history_present_illness", "review_of_systems",
        "assessment", "plan",
    ):
        value = _text(ai.get(key), 2000)
        if value:
            merged[key] = value
    for key in ("allergies", "medications"):
        values = _split_items(ai.get(key))
        if values:
            merged[key] = values
    merged["vitals"] = dict(local.get("vitals", {}))
    for key, value in (ai.get("vitals") or {}).items():
        if key in {
            "blood_pressure", "heart_rate", "respiratory_rate",
            "oxygen_saturation", "temperature_f", "weight_lb", "height_in",
            "pain_score",
        } and _text(value, 30):
            merged["vitals"][key] = _text(value, 30)
    return merged


def _explicit_vitals(payload):
    values = payload.get("vitals", {})
    if not isinstance(values, dict):
        return {}
    allowed = {
        "blood_pressure", "heart_rate", "respiratory_rate",
        "oxygen_saturation", "temperature_f", "weight_lb", "height_in",
        "pain_score",
    }
    return {key: _text(value, 30) for key, value in values.items() if key in allowed and _text(value, 30)}


def _mapping(section, field, resource, path, value, status="ready"):
    return {
        "section": section,
        "field": field,
        "resource": resource,
        "path": path,
        "status": status,
        "has_value": bool(value),
    }


def structure_clinical_intake(payload, insurance_assessment, allow_openai=False):
    payload = payload if isinstance(payload, dict) else {}
    note = _multiline(payload.get("clinical_note"))
    local = _local_extract(note)
    ai = {}
    extraction_mode = "local_rules"
    if allow_openai and note:
        try:
            ai = _openai_extract(note)
            if ai:
                extraction_mode = "openai_review_draft"
        except Exception as exc:
            # Do not log note contents or API response bodies.
            logger.warning("Clinical extraction fallback: %s", type(exc).__name__)
    extracted = _merge_extraction(local, ai)

    explicit_vitals = _explicit_vitals(payload)
    vitals = dict(extracted.get("vitals", {}))
    vitals.update(explicit_vitals)

    patient_reference = _text(payload.get("patient_reference"), 100)
    visit_type = _text(payload.get("visit_type"), 80)
    encounter_datetime = _text(payload.get("encounter_datetime"), 60)
    reason_for_visit = _text(payload.get("reason_for_visit"), 500)
    chief_complaint = reason_for_visit or extracted.get("chief_complaint", "")
    allergies = _split_items(payload.get("allergies")) or extracted.get("allergies", [])
    medications = _split_items(payload.get("medications")) or extracted.get("medications", [])
    assessment = _text(payload.get("assessment"), 1500) or extracted.get("assessment", "")
    plan = _text(payload.get("plan"), 1500) or extracted.get("plan", "")

    sections = {
        "patient_and_encounter": {
            "patient_reference": patient_reference,
            "encounter_datetime": encounter_datetime,
            "visit_type": visit_type,
        },
        "subjective": {
            "chief_complaint": chief_complaint,
            "history_present_illness": extracted.get("history_present_illness", ""),
            "review_of_systems": extracted.get("review_of_systems", ""),
        },
        "objective": {
            "vitals": vitals,
        },
        "medication_reconciliation": {
            "allergies": allergies,
            "medications": medications,
        },
        "clinical_review": {
            "assessment": assessment,
            "plan": plan,
        },
        "insurance": insurance_assessment,
    }

    mappings = [
        _mapping("Patient & encounter", "Patient reference", "Patient", "Patient.identifier", patient_reference),
        _mapping("Patient & encounter", "Encounter date/time", "Encounter", "Encounter.period.start", encounter_datetime),
        _mapping("Patient & encounter", "Visit type", "Encounter", "Encounter.type.text", visit_type),
        _mapping("Subjective", "Reason for visit", "Observation", "Observation.valueString", chief_complaint),
        _mapping("Subjective", "History of present illness", "Observation", "Observation.valueString", extracted.get("history_present_illness")),
        _mapping("Objective", "Vitals", "Observation", "Observation.value[x]", vitals),
        _mapping("Medication reconciliation", "Allergies", "AllergyIntolerance", "AllergyIntolerance.code.text", allergies),
        _mapping("Medication reconciliation", "Medications", "MedicationStatement", "MedicationStatement.medicationCodeableConcept.text", medications),
        _mapping("Clinical review", "Assessment", "Condition", "Condition.code.text", assessment, "clinician_review_required"),
        _mapping("Clinical review", "Plan", "CarePlan", "CarePlan.activity.detail.description", plan, "clinician_review_required"),
        _mapping("Insurance", "Insurance coverage", "Coverage", "Coverage.type.text / payor.display", insurance_assessment.get("payer"), "verification_required"),
    ]

    review_items = []
    if not patient_reference:
        review_items.append("Add a patient reference before export.")
    if not encounter_datetime:
        review_items.append("Add the encounter date and time.")
    if not chief_complaint:
        review_items.append("Document the reason for visit or chief complaint.")
    if not allergies:
        review_items.append("Confirm allergy status, including an explicit no-known-allergies entry when appropriate.")
    if not medications:
        review_items.append("Confirm medication reconciliation, including an explicit none entry when appropriate.")
    if assessment:
        review_items.append("A clinician must confirm the assessment before it can map to a Condition.")
    if plan:
        review_items.append("A clinician must confirm the plan before it can map to a CarePlan.")
    if insurance_assessment.get("coverage_status") != "verified":
        review_items.append("Insurance coverage and network status are not verified.")

    required_values = [patient_reference, encounter_datetime, visit_type, chief_complaint, note]
    completed = sum(bool(value) for value in required_values)

    return {
        "success": True,
        "draft_status": "clinician_review_required",
        "extraction_mode": extraction_mode,
        "saved": False,
        "ehr_write_attempted": False,
        "sections": sections,
        "destination_mappings": mappings,
        "review_items": review_items,
        "completion": {
            "completed_required_fields": completed,
            "total_required_fields": len(required_values),
            "percent": round(completed / len(required_values) * 100),
        },
        "notice": "Draft only. Clinician review is required before any EHR write or clinical use.",
    }
