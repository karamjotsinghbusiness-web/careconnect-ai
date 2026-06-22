import os
from openai import OpenAI


client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


def explain_recommendation(patient, specialty, providers, advocates, hospitals, hospices=None):
    """
    Creates a simple explanation for the patient.
    This does not diagnose. It only explains the care navigation results.
    """

    patient_city = patient.get("city", "Unknown")
    condition = patient.get("condition", "Unknown")

    provider_names = []
    if providers is not None and not providers.empty:
        provider_names = providers.get("provider_name", providers.get("facility_name", [])).head(3).astype(str).tolist()

    hospital_names = []
    if hospitals is not None and not hospitals.empty:
        hospital_names = hospitals.get("facility_name", []).head(3).astype(str).tolist()

    hospice_names = []
    if hospices is not None and not hospices.empty:
        hospice_names = hospices.get("facility_name", []).head(3).astype(str).tolist()

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
Top hospitals: {hospital_names}
Top hospice providers: {hospice_names}

Write:
1. A short explanation of what type of care may help
2. Why these options appeared
3. A reminder to contact a licensed healthcare professional
"""

    try:
        response = client.responses.create(
            model="gpt-5.4",
            input=prompt
        )

        return response.output_text

    except Exception as error:
        return f"AI explanation is currently unavailable. Error: {str(error)}"