# models/geocode_providers.py

import os
import sys
import time

import pandas as pd
import googlemaps

# SECURITY: never hardcode API keys in source. Set this in your environment
# (e.g. a .env file loaded by python-dotenv, or your host's secret manager),
# and make sure that .env is in .gitignore so it never reaches version control.
API_KEY = os.env.get("GOOGLE_MAPS_API_KEY")

if not API_KEY:
    sys.exit(
        "GOOGLE_MAPS_API_KEY is not set. Set it as an environment variable "
        "before running this script (do not hardcode it in the file)."
    )

# NOTE: the key that used to be hardcoded here
# (AIzaSyDms8HF9oyB6tRUAp7ZukLLkYhHWCjWYDY) has been exposed publicly.
# Revoke/regenerate it in the Google Cloud Console immediately, and also
# restrict the new key's usage to the Geocoding API + your server's IP.

gmaps = googlemaps.Client(key=API_KEY)

providers = pd.read_csv("data/providers.csv")

providers["latitude"] = None
providers["longitude"] = None

for i, row in providers.iterrows():
    try:
        address = f"{row['address']}, {row['city']}, MO"

        result = gmaps.geocode(address)

        if result:
            providers.at[i, "latitude"] = result[0]["geometry"]["location"]["lat"]
            providers.at[i, "longitude"] = result[0]["geometry"]["location"]["lng"]

            print(f"✓ {row['provider_name']}")

        time.sleep(0.05)

    except Exception as e:
        # Don't print full exception details if they might ever contain the
        # key or request URL; keep it to the provider name + error type.
        print(f"✗ {row.get('provider_name', 'Unknown')} : {type(e).__name__}")

providers.to_csv(
    "data/providers_geocoded.csv",
    index=False
)

print("Geocoding complete")

