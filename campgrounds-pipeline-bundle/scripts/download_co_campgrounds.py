import requests, json

base = "https://ndismaps.nrel.colostate.edu/arcgis/rest/services/FishingAtlas/FishingAtlas_Base_Map/MapServer/44/query"
all_features = []
offset = 0
page_size = 1000

while True:
    params = {
        "where": "1=1",
        "outFields": "*",
        "f": "geojson",
        "resultRecordCount": page_size,
        "resultOffset": offset
    }
    r = requests.get(base, params=params)
    data = r.json()
    features = data.get("features", [])
    all_features.extend(features)
    if len(features) < page_size:
        break
    offset += page_size

print(f"Total features: {len(all_features)}")

geojson = {"type": "FeatureCollection", "features": all_features}
with open("co_campgrounds.geojson", "w") as f:
    json.dump(geojson, f)
print("Saved to co_campgrounds.geojson")
