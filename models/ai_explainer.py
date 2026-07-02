import os
import logging

from openai import OpenAI


logger = logging.getLogger("careconnect")


def _get_client():
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return None

    return OpenAI(api_key=api_key)


def _safe_names(df, primary_col, fallback_col=None, limit=3):
    if df is None or getattr(df, "empty", True):
        return []

    if primary_col in df.columns:
        return df[primary_col].head(limit).astype(str).tolist()

    if fallback_col and fallback_col in df.columns:
        return df[fallback_col].head(limit).astype(str).tolist()

    return []


def explain_recommendation(patient, specialty, providers, advocates, hospitals, hospices=None):
    """
    Creates a simple explanation for the patient.
    This is healthcare navigation only. It does not diagnose or tell users what treatment to take.
    """

    client = _get_client()

    if client is None:
        return "CareConnect AI explanation is currently unavailable because the OpenAI API key is not configured on the backend."

    patient_city = patient.get("city", "Unknown")
    condition = patient.get("condition", "Unknown")

    provider_names = _safe_names(providers, "provider_name", "facility_name")
    advocate_names = _safe_names(advocates, "advocate_name", "provider_name")
    hospital_names = _safe_names(hospitals, "facility_name")
    hospice_names = _safe_names(hospices, "facility_name")

    prompt = f"""
You are CareConnect AI, a healthcare navigation assistant.

Explain these results in simple English for a patient or family.
Do not diagnose the patient.
Do not say they definitely have a disease.
Do not tell them what medicine to take.
Do not replace a doctor.
Only explain why these care options may be helpful.

Patient city: {patient_city}
Patient condition/symptom: {condition}
Predicted specialty: {specialty}

Top providers: {provider_names}
Top advocates: {advocate_names}
Top hospitals: {hospital_names}
Top hospice providers: {hospice_names}

Write the answer in this format:
**What this means:** one short paragraph
**Why these options appeared:** one short paragraph
**Important reminder:** tell the user to contact a licensed healthcare professional for medical decisions
"""

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=prompt,
            max_output_tokens=350,
            timeout=20
        )

        return response.output_text

    except Exception:
        logger.exception("OpenAI explanation failed")
        return "CareConnect AI explanation is currently unavailable, but the provider recommendations were still created."

