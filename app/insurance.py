"""Insurance normalization and verification-readiness helpers.

CareConnect does not claim eligibility or network participation without a
clearinghouse or payer response.  These helpers make the insurance portion of
the navigation workflow useful today while keeping that boundary explicit.
"""

import re


PAYER_ALIASES = {
    "aetna": "Aetna",
    "anthem": "Anthem / Blue Cross Blue Shield",
    "bcbs": "Anthem / Blue Cross Blue Shield",
    "blue cross": "Anthem / Blue Cross Blue Shield",
    "blue cross blue shield": "Anthem / Blue Cross Blue Shield",
    "cigna": "Cigna",
    "medicaid": "Medicaid",
    "mo healthnet": "Medicaid",
    "medicare": "Medicare",
    "original medicare": "Medicare",
    "self pay": "Self-pay",
    "self-pay": "Self-pay",
    "tricare": "TRICARE",
    "uhc": "UnitedHealthcare",
    "united healthcare": "UnitedHealthcare",
    "unitedhealthcare": "UnitedHealthcare",
    "uninsured": "Self-pay",
    "va": "Veterans Affairs",
    "veterans affairs": "Veterans Affairs",
}

PLAN_TYPE_ALIASES = {
    "chip": "CHIP",
    "epo": "EPO",
    "hmo": "HMO",
    "medicaid": "Medicaid",
    "medicare advantage": "Medicare Advantage",
    "original medicare": "Original Medicare",
    "pos": "POS",
    "ppo": "PPO",
    "self pay": "Self-pay",
    "self-pay": "Self-pay",
    "tricare": "TRICARE",
    "unknown": "Unknown",
}


def _normalized_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_payer(value):
    raw = _normalized_text(value)
    lowered = raw.lower()
    if not raw:
        return "Not provided"
    for alias in sorted(PAYER_ALIASES, key=len, reverse=True):
        if alias in lowered:
            return PAYER_ALIASES[alias]
    return raw[:100]


def normalize_plan_type(value, payer=""):
    raw = _normalized_text(value)
    lowered = raw.lower()
    if lowered in PLAN_TYPE_ALIASES:
        return PLAN_TYPE_ALIASES[lowered]

    payer_name = normalize_payer(payer)
    if payer_name == "Medicaid":
        return "Medicaid"
    if payer_name == "Medicare":
        return "Unknown Medicare plan"
    if payer_name == "Self-pay":
        return "Self-pay"
    return raw[:60] if raw else "Unknown"


def assess_insurance(payload):
    """Build a truthful insurance-readiness assessment.

    ``member_id_present`` and ``date_of_birth_present`` are booleans so the
    patient-facing recommendation does not need to echo sensitive identifiers.
    A future clearinghouse adapter can replace the not-verified status only
    after it receives a signed payer response.
    """

    payload = payload if isinstance(payload, dict) else {}
    payer_input = payload.get("payer", payload.get("insurance", ""))
    payer = normalize_payer(payer_input)
    plan_type = normalize_plan_type(
        payload.get("plan_type", payload.get("insurance_plan_type", "")),
        payer=payer,
    )
    self_pay = payer == "Self-pay" or plan_type == "Self-pay"

    missing = []
    if payer == "Not provided":
        missing.append("Insurance company or self-pay selection")
    if plan_type == "Unknown":
        missing.append("Plan type, if shown on the insurance card")
    if not self_pay and not payload.get("member_id_present", False):
        missing.append("Subscriber or member ID")
    if not self_pay and not payload.get("date_of_birth_present", False):
        missing.append("Patient date of birth")

    if self_pay:
        readiness = "self_pay"
        summary = (
            "Self-pay selected. Ask the facility for its good-faith estimate, "
            "financial-assistance policy, and payment options before scheduling."
        )
    elif payer == "Not provided":
        readiness = "missing_payer"
        summary = "Add an insurance company or select self-pay before checking coverage."
    elif missing:
        readiness = "needs_member_details"
        summary = (
            "The plan is identified, but member details are still needed for a "
            "real eligibility transaction."
        )
    else:
        readiness = "ready_for_gateway"
        summary = (
            "The required details appear available for an eligibility transaction. "
            "Coverage is still unverified until the payer or clearinghouse responds."
        )

    next_steps = []
    if self_pay:
        next_steps.extend([
            "Request a written estimate for the planned visit or service.",
            "Ask whether financial assistance or a payment plan is available.",
        ])
    else:
        next_steps.extend([
            "Verify active coverage for the planned date of service.",
            "Confirm the facility and individual clinician are both in-network.",
            "Ask whether a referral or prior authorization is required.",
            "Confirm deductible, copay, coinsurance, and service-specific limits with the payer.",
        ])

    return {
        "payer_entered": _normalized_text(payer_input)[:100],
        "payer": payer,
        "plan_type": plan_type,
        "coverage_status": "not_verified",
        "coverage_status_label": "Coverage not verified",
        "network_status": "not_verified",
        "network_status_label": "Provider network status not verified",
        "readiness": readiness,
        "missing_for_verification": missing,
        "summary": summary,
        "next_steps": next_steps,
        "destination": {
            "resource": "Coverage",
            "status": "draft_mapping_only",
        },
        "disclaimer": (
            "CareConnect has not received a payer eligibility or network response. "
            "Benefits and network participation must be confirmed with the plan and provider."
        ),
    }


def add_network_verification_status(frame, assessment):
    """Annotate result rows without inventing an in-network determination."""

    if frame is None or not hasattr(frame, "copy"):
        return frame
    annotated = frame.copy()
    if annotated.empty:
        return annotated

    payer = (assessment or {}).get("payer", "the selected plan")
    annotated["insurance_network_status"] = "Not verified"
    annotated["insurance_follow_up"] = (
        f"Confirm this location and clinician with {payer} before scheduling."
    )
    return annotated
