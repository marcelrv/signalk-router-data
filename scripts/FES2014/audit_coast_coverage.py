"""
Audit coastal coverage of UTCEF tide regions against global coastlines.
Identifies populated coastal areas (GHSL built-up) not covered by any region.
"""
import json, math, sys, os

def point_in_polygon(lat, lon, polygon):
    """polygon is list of [lon, lat] pairs (GeoJSON convention)"""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

COASTAL_SAMPLES = [
    # Northwest Europe / Atlantic
    ("NW Europe/Norway coast", 63.0, 8.0),
    ("NW Europe/Shetland/Faroe", 61.0, -3.0),
    ("NW Europe/Denmark coast", 56.0, 10.0),
    ("NW Europe/Poland/Baltic", 54.5, 18.0),
    ("NW Europe/Estonia/Baltic", 59.0, 24.0),
    ("NW Europe/Botnian Gulf", 63.0, 20.0),
    ("NW Europe/Finland Gulf", 60.0, 26.0),

    # Southwest Europe / Med
    ("SW Europe/Spain Med coast", 40.0, 1.0),
    ("SW Europe/France Med coast", 43.0, 5.0),
    ("SW Europe/Italy west", 42.0, 11.0),
    ("SW Europe/Italy east/Adriatic", 43.0, 14.0),
    ("SW Europe/Greece west/Ionian", 38.0, 21.0),
    ("SW Europe/Greece Aegean", 37.0, 25.0),
    ("SW Europe/Turkey Aegean", 38.0, 27.0),
    ("SW Europe/Crete", 35.0, 25.0),
    ("SW Europe/Tunisia", 36.0, 10.0),
    ("SW Europe/Egypt/Nile Delta", 31.5, 32.0),
    ("SW Europe/Israel coast", 32.0, 35.0),
    ("SW Europe/Syria coast", 35.0, 36.0),
    ("SW Europe/Black Sea/W Romania", 44.0, 29.0),
    ("SW Europe/Crimea", 45.0, 34.0),
    ("SW Europe/Georgia/Batumi", 42.0, 41.0),
    ("SW Europe/Turkey Black Sea", 42.0, 36.0),

    # Africa West
    ("Africa West/Morocco Atlantic", 32.0, -9.0),
    ("Africa West/Western Sahara", 26.0, -14.0),
    ("Africa West/Mauritania", 20.0, -16.0),
    ("Africa West/Senegal/Dakar", 14.7, -17.5),
    ("Africa West/Gambia", 13.5, -16.5),
    ("Africa West/Guinea-Bissau", 11.5, -16.0),
    ("Africa West/Guinea/Conakry", 9.5, -14.0),
    ("Africa West/Sierra Leone", 8.5, -13.0),
    ("Africa West/Liberia", 6.5, -11.0),
    ("Africa West/Ivory Coast", 5.0, -5.0),
    ("Africa West/Ghana/Accra", 5.5, 0.0),
    ("Africa West/Togo/Benin", 6.0, 2.0),
    ("Africa West/Nigeria/Lagos", 6.5, 3.5),

    # Africa Gulf of Guinea / Central
    ("Africa/Equatorial Guinea", 3.5, 9.0),
    ("Africa/Gabon/Libreville", 0.5, 9.5),
    ("Africa/Congo/Brazzaville", -4.5, 12.0),
    ("Africa/Angola/Luanda", -8.5, 13.5),
    ("Africa/Namibia/Walvis Bay", -23.0, 14.5),
    ("Africa/South Africa/Cape Town", -34.0, 18.5),
    ("Africa/South Africa/Durban", -30.0, 31.0),

    # Africa East
    ("Africa East/Mozambique/Maputo", -26.0, 32.5),
    ("Africa East/Mozambique/Beira", -20.0, 34.5),
    ("Africa East/Tanzania/Dar Es Salaam", -6.5, 39.0),
    ("Africa East/Kenya/Mombasa", -4.0, 39.5),
    ("Africa East/Somalia/Mogadishu", 2.0, 45.5),
    ("Africa East/Somalia/Gulf of Aden", 11.0, 44.0),
    ("Africa East/Djibouti", 11.5, 43.0),
    ("Africa East/Sudan/Port Sudan", 19.5, 37.0),
    ("Africa East/Egypt/Red Sea", 27.0, 34.0),

    # Middle East / Indian Ocean
    ("Middle East/Yemen/Aden", 13.0, 45.0),
    ("Middle East/Oman/Muscat", 23.5, 58.5),
    ("Middle East/UAEs/Dubai", 25.0, 55.0),
    ("Middle East/Qatar/Doha", 25.5, 51.5),
    ("Middle East/Bahrain", 26.0, 50.5),
    ("Middle East/Saudi/Dammam", 26.5, 50.0),
    ("Middle East/Kuwait", 29.5, 48.0),
    ("Middle East/Iraq/Basra", 30.0, 48.0),
    ("Middle East/Iran/Bandar Abbas", 27.0, 56.0),
    ("Middle East/Pakistan/Karachi", 25.0, 67.0),
    ("Middle East/India/Mumbai", 19.0, 72.5),
    ("Middle East/India/Chennai", 13.0, 80.5),
    ("Middle East/India/Kolkata", 21.5, 88.0),
    ("Middle East/Bangladesh/Chittagong", 22.5, 91.5),
    ("Middle East/Myanmar/Yangon", 16.5, 96.0),

    # SE Asia
    ("SE Asia/Thailand/Bangkok", 13.5, 100.5),
    ("SE Asia/Cambodia/Sihanoukville", 10.5, 103.5),
    ("SE Asia/Vietnam/Ho Chi Minh", 10.5, 107.0),
    ("SE Asia/Vietnam/Da Nang", 16.0, 108.0),
    ("SE Asia/Vietnam/Hanoi/Haiphong", 20.5, 107.0),
    ("SE Asia/Malaysia/Kuala Lumpur", 3.0, 101.5),
    ("SE Asia/Singapore", 1.5, 104.0),
    ("SE Asia/Indonesia/Sumatra/Medan", 3.5, 99.0),
    ("SE Asia/Indonesia/Sumatra/Palembang", -2.5, 105.0),
    ("SE Asia/Indonesia/Java/Jakarta", -6.0, 107.0),
    ("SE Asia/Indonesia/Java/Surabaya", -7.0, 113.0),
    ("SE Asia/Indonesia/Bali", -8.5, 115.5),
    ("SE Asia/Indonesia/Lombok", -8.5, 116.5),
    ("SE Asia/Indonesia/Sulawesi/Makassar", -5.0, 119.5),
    ("SE Asia/Indonesia/Kalimantan/Banjarmasin", -3.5, 114.5),
    ("SE Asia/Indonesia/Maluku/Ambon", -3.5, 128.0),
    ("SE Asia/Indonesia/Papua/Jayapura", -2.5, 140.5),
    ("SE Asia/East Timor/Dili", -8.5, 125.5),
    ("SE Asia/Philippines/Manila", 14.5, 121.0),
    ("SE Asia/Philippines/Cebu", 10.5, 124.0),
    ("SE Asia/Philippines/Palawan", 9.5, 118.5),
    ("SE Asia/Philippines/Mindanao/Davao", 7.0, 125.5),
    ("SE Asia/Taiwan/Kaohsiung", 22.5, 120.5),
    ("SE Asia/Taiwan/Taipei", 25.0, 121.5),

    # East Asia
    ("East Asia/China/Shanghai", 31.0, 122.0),
    ("East Asia/China/Hong Kong", 22.5, 114.5),
    ("East Asia/China/Xiamen", 24.5, 118.0),
    ("East Asia/China/Qingdao", 36.0, 120.5),
    ("East Asia/China/Tianjin/Beijing", 39.0, 118.0),
    ("East Asia/Korea/Busan", 35.0, 129.0),
    ("East Asia/Korea/Incheon", 37.5, 126.5),
    ("East Asia/Japan/Tokyo", 35.5, 140.0),
    ("East Asia/Japan/Osaka", 34.5, 135.5),
    ("East Asia/Japan/Nagoya", 35.0, 137.0),
    ("East Asia/Japan/Fukuoka", 33.5, 130.5),
    ("East Asia/Japan/Okayama/Hiroshima", 34.5, 133.5),
    ("East Asia/Russia/Vladivostok", 43.0, 132.0),
    ("East Asia/Russia/Sakhalin", 47.0, 143.0),
    ("East Asia/Russia/Kamchatka", 53.0, 158.5),

    # Pacific North
    ("Pacific North/Alaska/Anchorage", 61.0, -150.0),
    ("Pacific North/Alaska/Juneau", 58.5, -134.5),
    ("Pacific North/Canada/Vancouver", 49.5, -123.5),
    ("Pacific North/USA/Seattle", 47.5, -122.5),
    ("Pacific North/USA/San Francisco", 37.5, -122.5),
    ("Pacific North/USA/Los Angeles", 34.0, -118.5),
    ("Pacific North/USA/San Diego", 32.5, -117.5),
    ("Pacific North/Mexico/Ensenada", 32.0, -117.0),
    ("Pacific North/Mexico/Cabo San Lucas", 23.0, -110.0),
    ("Pacific North/Mexico/Mazatlan", 23.5, -106.5),
    ("Pacific North/Mexico/Puerto Vallarta", 20.5, -105.5),
    ("Pacific North/Mexico/Acapulco", 17.0, -100.0),
    ("Pacific North/Mexico/Salina Cruz", 16.0, -95.0),
    ("Pacific North/Hawaii", 21.5, -158.0),

    # Central America Pacific
    ("CentAm/Guatemala", 14.0, -92.0),
    ("CentAm/El Salvador", 13.5, -90.0),
    ("CentAm/Nicaragua/Corinto", 12.5, -87.0),
    ("CentAm/Costa Rica/Puntarenas", 10.0, -85.0),
    ("CentAm/Panama City", 8.5, -79.5),

    # South America Pacific
    ("SAm Pacific/Colombia/Buenaventura", 4.0, -77.5),
    ("SAm Pacific/Ecuador/Guayaquil", -2.5, -80.0),
    ("SAm Pacific/Peru/Lima", -12.0, -77.5),
    ("SAm Pacific/Chile/Valparaiso", -33.0, -71.5),
    ("SAm Pacific/Chile/Concepcion", -36.5, -73.0),
    ("SAm Pacific/Chile/Puerto Montt", -41.5, -73.0),
    ("SAm Pacific/Chile/Punta Arenas", -53.0, -71.0),

    # South America Atlantic
    ("SAm Atlantic/Venezuela/Maracaibo", 10.5, -71.5),
    ("SAm Atlantic/Venezuela/Caracas", 10.5, -67.0),
    ("SAm Atlantic/Colombia/Barranquilla", 11.0, -75.0),
    ("SAm Atlantic/Guyana/Georgetown", 6.5, -58.0),
    ("SAm Atlantic/Suriname", 6.0, -55.0),
    ("SAm Atlantic/French Guiana", 5.0, -52.0),
    ("SAm Atlantic/Brazil/Belem", -1.0, -48.5),
    ("SAm Atlantic/Brazil/Fortaleza", -3.5, -38.5),
    ("SAm Atlantic/Brazil/Recife", -8.0, -35.0),
    ("SAm Atlantic/Brazil/Salvador", -13.0, -38.5),
    ("SAm Atlantic/Brazil/Rio de Janeiro", -23.0, -43.5),
    ("SAm Atlantic/Brazil/Santos", -24.0, -46.5),
    ("SAm Atlantic/Brazil/Porto Alegre", -30.0, -50.5),
    ("SAm Atlantic/Uruguay/Montevideo", -35.0, -56.0),
    ("SAm Atlantic/Argentina/Buenos Aires", -34.5, -58.0),
    ("SAm Atlantic/Argentina/Mar del Plata", -38.0, -57.5),
    ("SAm Atlantic/Argentina/Comodoro Rivadavia", -45.5, -67.5),
    ("SAm Atlantic/Argentina/Rio Gallegos", -51.5, -69.0),
    ("SAm Atlantic/Falklands", -51.5, -58.0),

    # North America Atlantic
    ("NAtlantic/USA/Miami", 25.5, -80.0),
    ("NAtlantic/USA/Jacksonville", 30.5, -81.5),
    ("NAtlantic/USA/Norfolk", 37.0, -76.0),
    ("NAtlantic/USA/New York", 40.5, -74.0),
    ("NAtlantic/USA/Boston", 42.5, -71.0),
    ("NAtlantic/Canada/Halifax", 44.5, -63.5),
    ("NAtlantic/Canada/St Johns", 47.5, -52.5),
    ("NAtlantic/Canada/Labrador/Goose Bay", 53.5, -60.0),
    ("NAtlantic/Greenland/Nuuk", 64.0, -51.5),
    ("NAtlantic/Canada/Hudson Bay/Churchill", 59.0, -94.0),
    ("NAtlantic/Canada/Hudson Bay/Iqaluit", 63.5, -68.5),
    ("NAtlantic/Canada/Strait of Belle Isle", 51.5, -56.0),

    # Caribbean
    ("Caribbean/Cuba/Havana", 23.0, -82.5),
    ("Caribbean/Cuba/Santiago", 20.0, -76.0),
    ("Caribbean/Jamaica/Kingston", 18.0, -77.0),
    ("Caribbean/Haiti/Port Au Prince", 18.5, -72.5),
    ("Caribbean/Dominican Republic", 18.5, -69.5),
    ("Caribbean/Puerto Rico", 18.5, -66.0),
    ("Caribbean/Trinidad", 10.5, -61.5),
    ("Caribbean/Barbados", 13.0, -59.5),
    ("Caribbean/Bahamas/Nassau", 25.0, -77.5),

    # Pacific Oceania
    ("Oceania/Australia/Darwin", -12.5, 130.5),
    ("Oceania/Australia/Perth", -32.0, 115.5),
    ("Oceania/Australia/Adelaide", -34.5, 138.5),
    ("Oceania/Australia/Melbourne", -38.0, 145.0),
    ("Oceania/Australia/Sydney", -34.0, 151.0),
    ("Oceania/Australia/Brisbane", -27.5, 153.0),
    ("Oceania/Australia/Cairns", -16.5, 145.5),
    ("Oceania/Papua NG/Port Moresby", -9.5, 147.0),
    ("Oceania/Papua NG/Rabaul", -4.5, 152.0),
    ("Oceania/New Zealand/Auckland", -36.5, 174.5),
    ("Oceania/New Zealand/Wellington", -41.5, 175.0),
    ("Oceania/New Zealand/Christchurch", -43.5, 172.5),
    ("Oceania/Fiji/Suva", -18.0, 178.5),
    ("Oceania/Vanuatu/Port Vila", -17.5, 168.5),
    ("Oceania/Solomon Islands/Honiara", -9.5, 160.0),
    ("Oceania/New Caledonia/Noumea", -22.5, 166.5),
    ("Oceania/Tahiti/Papeete", -17.5, -149.5),
    ("Oceania/Samoa/Apia", -13.5, -172.0),
    ("Oceania/Tonga/Nuku Alofa", -21.0, -175.0),
    ("Oceania/Marshall Is/Majuro", 7.0, 171.0),
    ("Oceania/Micronesia/Chuuk", 7.5, 151.5),
    ("Oceania/Palau", 7.5, 134.5),
    ("Oceania/Guam", 13.5, 144.5),
    ("Oceania/Northern Marianas/Saipan", 15.0, 145.5),
    ("Oceania/Kiribati/Tarawa", 1.5, 173.0),
    ("Oceania/Easter Island", -27.0, -109.0),
    ("Oceania/Galapagos", -0.5, -90.5),

    # Arctic
    ("Arctic/Iceland/Reykjavik", 64.0, -22.0),
    ("Arctic/Norway/Tromso", 70.0, 19.0),
    ("Arctic/Russia/Murmansk", 69.0, 33.0),
    ("Arctic/Russia/Arkhangelsk", 64.5, 40.5),
    ("Arctic/Russia/Khatanga", 72.0, 103.0),
    ("Arctic/Canada/Alert", 82.5, -62.5),
    ("Arctic/Greenland NE", 76.0, -18.0),
    ("Arctic/Alaska/Prudhoe Bay", 70.5, -149.0),
]

def load_regions(config_dir):
    regions = []
    config_files = [
        "fes2024_atlantic.json",
        "fes2024_europe_mediterranean.json",
        "fes2024_indian_ocean.json",
        "fes2024_pacific_asia.json",
        "fes2024_pacific_south.json",
    ]
    for cf in config_files:
        path = os.path.join(config_dir, cf)
        with open(path) as f:
            data = json.load(f)
        for r in data.get("regions", []):
            geom = r.get("boundary_geometry", {})
            typ = geom.get("type", "Polygon")
            all_rings = geom.get("coordinates", [[]])
            polygons = []
            if typ == "MultiPolygon":
                for poly_rings in all_rings:
                    if poly_rings:
                        polygons.append(poly_rings[0])
            else:
                if all_rings:
                    polygons.append(all_rings[0])
            regions.append({
                "id": r["region_id"],
                "name": r.get("name", ""),
                "polygons": polygons,  # list of polygon rings, each is list of [lon, lat] pairs
                "traffic": r.get("traffic_level", "unknown"),
                "resolution": r.get("resolution", ""),
            })
    return regions

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    regions = load_regions(script_dir)
    print(f"Loaded {len(regions)} regions\n")

    gaps = []
    covered = []
    for name, lat, lon in COASTAL_SAMPLES:
        matched = []
        for r in regions:
            for poly in r["polygons"]:
                if point_in_polygon(lat, lon, poly):
                    matched.append(r["id"])
                    break
        if matched:
            covered.append((name, lat, lon, matched))
        else:
            gaps.append((name, lat, lon))

    print(f"Sample coastal points covered: {len(covered)}/{len(COASTAL_SAMPLES)}")
    print(f"Coastal gaps (uncovered): {len(gaps)}\n")

    if gaps:
        print("=" * 70)
        print("UNCOVERED COASTAL AREAS (no UTCEF region polygon contains these points)")
        print("=" * 70)
        for name, lat, lon in gaps:
            print(f"  {name:50s}  @ ({lat:6.1f}, {lon:6.1f})")

    print(f"\nRegions used ({len(regions)}):")
    for r in sorted(regions, key=lambda x: x["id"]):
        print(f"  {r['id']}")

if __name__ == "__main__":
    main()
