import googlemaps

API_KEY = "AIzaSyDms8HF9oyB6tRUAp7ZukLLkYhHWCjWYDY"

gmaps = googlemaps.Client(key=API_KEY)

results = gmaps.geocode(
    "Kansas City, Missouri"
)

print(results[0]["geometry"]["location"])