"""Optional manual Google Maps smoke test using a public place name.

This helper is not part of the CareConnect test suite and must never contain a
credential or patient location.
"""

import os

__test__ = False


def main():
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise SystemExit("GOOGLE_MAPS_API_KEY is required for this manual test.")

    try:
        import googlemaps
    except ImportError as exc:
        raise SystemExit(
            "Install the optional googlemaps package before running this helper."
        ) from exc

    client = googlemaps.Client(key=api_key)
    results = client.geocode("Kansas City, Missouri")
    if not results:
        raise SystemExit("Google Maps returned no result for the public test place.")

    print(results[0]["geometry"]["location"])


if __name__ == "__main__":
    main()
