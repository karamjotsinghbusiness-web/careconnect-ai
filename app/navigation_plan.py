"""Deterministic patient navigation plans for CareConnect.

The plan turns search results into concrete administrative actions. It never
diagnoses, recommends treatment, or claims that insurance coverage is verified.
"""

from __future__ import annotations

import math


PRIORITIES = {
    "fastest": "Fastest appointment",
    "closest": "Closest care",
    "quality": "Best public quality",
    "cost": "Lowest cost",
}

BARRIERS = {
    "transportation": "Transportation",
    "cost": "Cost",
    "referral": "Referral",
    "language": "Language",
    "mobility": "Mobility",
    "childcare": "Childcare",
}


def _clean_text(value, limit=300):
    return " ".join(str(value or "").strip().split())[:limit]


def _safe_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rows(value, limit=5):
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            return value.head(limit).to_dict(orient="records")
        except TypeError:
            return []
    if isinstance(value, list):
        return [row for row in value[:limit] if isinstance(row, dict)]
    return []


def _option_name(row):
    for key in (
        "provider_name",
        "clinic_name",
        "facility_name",
        "advocate_name",
        "name",
    ):
        value = _clean_text(row.get(key), 120)
        if value:
            return value
    return "Care option"


def _care_option(row, option_type):
    distance = _safe_float(row.get("distance_miles"))
    return {
        "name": _option_name(row),
        "type": option_type,
        "city": _clean_text(row.get("city") or row.get("city_town"), 100),
        "address": _clean_text(
            row.get("address")
            or row.get("practice_address")
            or row.get("address_line_1"),
            180,
        ),
        "phone": _clean_text(
            row.get("phone")
            or row.get("telephone_number")
            or row.get("phone_number"),
            40,
        ),
        "distance_miles": round(distance, 1) if distance is not None else None,
        "network_status": "Not verified",
        "availability_status": "Call to confirm",
        "source_url": _clean_text(row.get("source_url"), 500),
    }


def _deduplicate_options(options):
    result = []
    seen = set()
    for option in options:
        key = (option["name"].lower(), option["city"].lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(option)
    return result


def _normalize_priority(value):
    value = _clean_text(value, 30).lower()
    return value if value in PRIORITIES else "fastest"


def _normalize_barriers(values):
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple, set)):
        return []
    normalized = []
    for value in values:
        key = _clean_text(value, 30).lower()
        if key in BARRIERS and key not in normalized:
            normalized.append(key)
    return normalized[:6]


def _barrier_actions(barriers):
    actions = {
        "transportation": "Ask about telehealth, transportation support, and the closest realistic backup location.",
        "cost": "Ask for a written estimate, financial assistance, payment plans, and lower-cost site-of-care options.",
        "referral": "Ask the plan and clinic who must submit the referral, where it must be sent, and how to confirm receipt.",
        "language": "Request a qualified interpreter and ask whether the service is available at no charge.",
        "mobility": "Confirm accessible parking, entrances, exam equipment, and any mobility accommodation before the visit.",
        "childcare": "Ask about appointment timing, telehealth, and whether a support person or child may accompany you.",
    }
    return [
        {"barrier": BARRIERS[key], "action": actions[key]}
        for key in barriers
    ]


def build_navigation_plan(
    patient,
    specialty,
    emergency,
    access_score,
    care_gap,
    insurance_assessment,
    providers=None,
    nearest_clinics=None,
    fallback_hospitals=None,
    recommended_hospitals=None,
    advocates=None,
):
    """Create an administrative, barrier-aware plan from returned care options."""

    patient = patient if isinstance(patient, dict) else {}
    priority = _normalize_priority(patient.get("priority"))
    barriers = _normalize_barriers(patient.get("barriers"))
    condition = _clean_text(patient.get("condition"), 300) or "the concern I entered"
    city = _clean_text(patient.get("city"), 100) or "my area"
    specialty = _clean_text(specialty, 120) or "an appropriate care option"

    option_sources = {
        "fastest": (
            (providers, "Matching provider"),
            (nearest_clinics, "Nearby clinic"),
            (recommended_hospitals, "Hospital option"),
            (fallback_hospitals, "Backup facility"),
        ),
        "closest": (
            (providers, "Matching provider"),
            (nearest_clinics, "Nearby clinic"),
            (recommended_hospitals, "Hospital option"),
            (fallback_hospitals, "Backup facility"),
        ),
        "quality": (
            (recommended_hospitals, "Hospital option with public quality data"),
            (providers, "Matching provider"),
            (nearest_clinics, "Nearby clinic"),
            (fallback_hospitals, "Backup facility"),
        ),
        "cost": (
            (nearest_clinics, "Nearby clinic; ask about sliding-fee eligibility"),
            (providers, "Matching provider"),
            (fallback_hospitals, "Backup facility"),
            (recommended_hospitals, "Hospital option"),
        ),
    }

    options = []
    for frame, option_type in option_sources[priority]:
        options.extend(_care_option(row, option_type) for row in _rows(frame, 5))
    options = _deduplicate_options(options)

    if priority == "closest":
        options.sort(
            key=lambda item: (
                item["distance_miles"] is None,
                item["distance_miles"] if item["distance_miles"] is not None else 99999,
            )
        )

    primary = options[0] if options else None
    backup = options[1] if len(options) > 1 else None
    plan_name = primary["name"] if primary else "the first available care option"

    access_level = _clean_text((access_score or {}).get("level"), 60) or "Unknown access"
    gap_detected = bool((care_gap or {}).get("detected"))
    emergency_warning = bool((emergency or {}).get("is_emergency"))

    if emergency_warning:
        do_first = {
            "title": "Act on the urgent warning first",
            "text": (
                "If symptoms are severe or you think this may be an emergency, call emergency services "
                "or seek immediate medical help. Do not wait for routine scheduling calls."
            ),
            "action": "Review urgent warning",
        }
    else:
        do_first = {
            "title": "Call two options, not just one" if gap_detected else "Call your first care option",
            "text": (
                f"Call {plan_name} and confirm the soonest appropriate appointment, referral requirements, "
                "and your exact plan network before scheduling."
            ),
            "action": "Open call kit",
        }

    insurance_name = _clean_text((insurance_assessment or {}).get("payer"), 100) or "my health plan"
    plan_type = _clean_text((insurance_assessment or {}).get("plan_type"), 60) or "my exact plan"

    provider_script = (
        f"Hi, I’m calling to ask about the soonest new-patient appointment for {condition}. "
        f"CareConnect suggested I ask about {specialty}. Before I schedule, are you accepting new patients, "
        "do I need a referral, and can you confirm my exact insurance plan is in network?"
    )
    insurance_script = (
        f"Hi, I’m calling to verify benefits for a possible {specialty} visit near {city}. "
        f"My plan is {insurance_name}, {plan_type}. Please confirm active coverage, whether the individual "
        "clinician and facility are in network, whether a referral or prior authorization is required, "
        "and my deductible, copay, or coinsurance for this visit."
    )

    tasks = [
        {
            "id": "confirm_coverage",
            "title": "Confirm coverage",
            "detail": "Call the plan and the provider; record names, reference numbers, and what each person says.",
            "status": "not_started",
        },
        {
            "id": "call_two_options",
            "title": "Call two care options",
            "detail": "Compare the soonest available visit and keep a backup if the first option is blocked.",
            "status": "not_started",
        },
        {
            "id": "prepare_visit",
            "title": "Prepare for the visit",
            "detail": "Bring your medication list, allergies, symptom timeline, insurance card, and questions.",
            "status": "not_started",
        },
        {
            "id": "follow_up",
            "title": "Follow up if blocked",
            "detail": "Use the backup route and ask an advocate for help with scheduling or coordination.",
            "status": "not_started",
        },
    ]

    backup_steps = []
    if backup:
        backup_steps.append(f"Call {backup['name']} as the second option instead of waiting on one callback.")
    else:
        backup_steps.append("Call the nearest clinic or primary care option if the specialty office cannot schedule you.")
    backup_steps.extend([
        "Ask to join a cancellation list and write down the expected callback date.",
        "Ask the plan for additional in-network options if the returned locations cannot help.",
        "Use an advocate or community support resource when transportation, language, cost, or scheduling is blocking care.",
    ])

    advocate_options = [
        _care_option(row, "Care support specialist")
        for row in _rows(advocates, 3)
    ]

    return {
        "version": "care-route-v1",
        "scope": "administrative_care_navigation_only",
        "headline": "Your route starts here",
        "priority": {"id": priority, "label": PRIORITIES[priority]},
        "barriers": [BARRIERS[key] for key in barriers],
        "navigation_signal": {
            "level": access_level,
            "care_gap_detected": gap_detected,
            "label": (
                "Limited access — use two options"
                if gap_detected or access_level in {"Limited Access", "Care Gap Risk"}
                else "Multiple navigation options found"
            ),
            "disclaimer": "Navigation signal, not a medical score.",
        },
        "do_first": do_first,
        "tasks": tasks,
        "care_options": options[:6],
        "call_kits": {
            "provider": {
                "title": "Provider phone script",
                "script": provider_script,
                "questions": [
                    "Are you accepting new patients?",
                    "What is the soonest appropriate appointment?",
                    "Do I need a referral or records before scheduling?",
                    "Can you confirm my exact plan is in network?",
                    "What should I bring, and who should I call if symptoms change?",
                ],
            },
            "insurance": {
                "title": "Insurance phone script",
                "script": insurance_script,
                "questions": list((insurance_assessment or {}).get("next_steps") or []),
                "record_fields": [
                    "Representative name",
                    "Date and time",
                    "Call reference number",
                    "Coverage answer",
                    "Network answer for clinician and facility",
                    "Referral or authorization answer",
                ],
            },
        },
        "prep_pack": {
            "one_sentence_summary": (
                f"I am seeking help for {condition}, and I would like to understand the next appropriate step."
            ),
            "bring": [
                "Insurance card and photo ID",
                "Current medication and supplement list",
                "Allergy list",
                "Symptom timeline and prior records you already have",
                "Referral or authorization details, if required",
            ],
            "questions": [
                "What do you think the next step should be, and why?",
                "What warning signs should make me seek help sooner?",
                "What follow-up is needed, and when?",
                "Who should I contact if I cannot complete the recommended next step?",
            ],
        },
        "barrier_plan": _barrier_actions(barriers),
        "backup_route": backup_steps,
        "advocate_options": advocate_options,
        "progress_storage": "device_only",
        "safety": {
            "coverage_verified": False,
            "availability_verified": False,
            "medical_advice": False,
            "message": (
                "CareConnect organizes public results and administrative next steps. "
                "Confirm medical decisions with a licensed clinician and coverage with the plan and provider."
            ),
        },
    }
