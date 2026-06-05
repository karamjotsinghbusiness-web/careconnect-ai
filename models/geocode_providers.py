# models/geocode_providers.py

import pandas as pd
import googlemaps
import time

API_KEY = "AIzaSyDms8HF9oyB6tRUAp7ZukLLkYhHWCjWYDY"

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
        print(f"✗ {row.get('provider_name','Unknown')} : {e}")

providers.to_csv(
    "data/providers_geocoded.csv",
    index=False
)

print("Geocoding complete")