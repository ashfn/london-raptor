from data import *
import json
import os
import requests
from requests.adapters import HTTPAdapter
from collections import defaultdict

db = connect_db()

# Reuse HTTP connections for faster local OSRM calls
session = requests.Session()
session.mount("http://", HTTPAdapter(pool_connections=16, pool_maxsize=64))
session.mount("https://", HTTPAdapter(pool_connections=16, pool_maxsize=64))

OSRM_BASE_URL = "http://localhost:5001"
OSRM_PROFILE = "walking"
TABLE_CHUNK_SIZE = 1000
SAVE_INTERVAL = 100  # Save more frequently to avoid data loss

# Load existing distances
try:
    with open("walking_distances.json", "r") as f:
        walking_distances = json.load(f)
except FileNotFoundError:
    walking_distances = {}

# Convert to defaultdict for cleaner code
walking_distances = defaultdict(dict, walking_distances)

# Preload all points into memory with spatial index
points_list = list(Point.select())
print(f"Loaded {len(points_list)} points")

# Create spatial lookup dict for faster nearby point queries
LAT_BUCKET_SIZE = 0.001 
LON_BUCKET_SIZE = 0.001  # ~70m at UK latitude
spatial_index = defaultdict(list)

for p in points_list:
    lat_bucket = int(p.latitude / LAT_BUCKET_SIZE)
    lon_bucket = int(p.longitude / LON_BUCKET_SIZE)
    spatial_index[(lat_bucket, lon_bucket)].append(p)

print(f"Built spatial index with {len(spatial_index)} buckets")

def get_nearby_points(point, radius=0.009):
    """Fast spatial lookup for nearby points"""
    lat_range = int(radius / LAT_BUCKET_SIZE) + 1
    lon_range = int(radius / LON_BUCKET_SIZE) + 1
    center_lat = int(point.latitude / LAT_BUCKET_SIZE)
    center_lon = int(point.longitude / LON_BUCKET_SIZE)
    
    nearby = []
    for lat_offset in range(-lat_range, lat_range + 1):
        for lon_offset in range(-lon_range, lon_range + 1):
            bucket = (center_lat + lat_offset, center_lon + lon_offset)
            nearby.extend(spatial_index[bucket])
    
    # Filter to exact radius
    top_left_lat = point.latitude - radius
    bottom_right_lat = point.latitude + radius
    top_left_lon = point.longitude - radius
    bottom_right_lon = point.longitude + radius
    
    return [p for p in nearby if 
            top_left_lat < p.latitude < bottom_right_lat and
            top_left_lon < p.longitude < bottom_right_lon and
            p.point_id != point.point_id]

done = 0
changes_since_save = 0

for point in points_list:
    print(f"{done:<5}|={point.point_id}")
    
    nearby_points = get_nearby_points(point)
    
    # Collect candidates not already computed
    candidates = []
    for nearby_point in nearby_points:
        # Check if we have distance in either direction
        if nearby_point.point_id in walking_distances[point.point_id]:
            continue
        if point.point_id in walking_distances[nearby_point.point_id]:
            # Use existing reverse distance
            dist = walking_distances[nearby_point.point_id][point.point_id]
            walking_distances[point.point_id][nearby_point.point_id] = dist
            print(f"{done:<5}|    {point.point_id}->{nearby_point.point_id}: {dist}s")
            changes_since_save += 1
            continue
        candidates.append(nearby_point)
    
    if not candidates:
        done += 1
        continue
    
    # Batch process with OSRM Table API
    idx = 0
    while idx < len(candidates):
        chunk = candidates[idx:idx+TABLE_CHUNK_SIZE]
        idx += TABLE_CHUNK_SIZE
        
        coords = [f"{point.longitude},{point.latitude}"] + \
                 [f"{p.longitude},{p.latitude}" for p in chunk]
        url = f"{OSRM_BASE_URL}/table/v1/{OSRM_PROFILE}/" + ";".join(coords)
        
        try:
            resp = session.get(url, params={"sources": "0", "annotations": "duration"}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("code") != "Ok" or not data.get("durations"):
                raise ValueError("Invalid OSRM response")
            
            durations = data["durations"][0]
            
            # Process all destinations in batch
            for i, dest in enumerate(chunk):
                try:
                    t = durations[i + 1]  # Skip source-to-source at index 0
                    if t is None or t >= 1e10:  # OSRM returns very large values for unreachable
                        raise ValueError("No route")
                    
                    print(f"{done:<5}|    {point.point_id}->{dest.point_id}: {t}s")
                    walking_distances[point.point_id][dest.point_id] = t
                    walking_distances[dest.point_id][point.point_id] = t
                    changes_since_save += 2
                except (IndexError, ValueError) as e:
                    print(f"{done:<5}|    {point.point_id}->{dest.point_id} ERROR: {e}")
                    
        except Exception as e:
            print(f"Batch failed, falling back to individual routes: {e}")
            # Fallback to individual route API calls
            for dest in chunk:
                try:
                    route_url = f"{OSRM_BASE_URL}/route/v1/{OSRM_PROFILE}/" \
                                f"{point.longitude},{point.latitude};" \
                                f"{dest.longitude},{dest.latitude}"
                    r = session.get(route_url, params={"overview": "false", "steps": "false"}, timeout=10)
                    r.raise_for_status()
                    rj = r.json()
                    
                    if rj.get("code") != "Ok" or not rj.get("routes"):
                        raise ValueError("No route found")
                    
                    t = rj["routes"][0]["duration"]
                    print(f"{done:<5}|    {point.point_id}->{dest.point_id}: {t}s")
                    walking_distances[point.point_id][dest.point_id] = t
                    walking_distances[dest.point_id][point.point_id] = t
                    changes_since_save += 2
                except Exception as e:
                    print(f"{done:<5}|    {point.point_id}->{dest.point_id} ERROR: {e}")
    
    done += 1
    
    # Save more frequently but only if there are changes
    if changes_since_save > 0 and done % SAVE_INTERVAL == 0:
        print(f"================SAVING ({changes_since_save} changes)=================")
        # Convert defaultdict back to regular dict for JSON
        save_data = {k: dict(v) for k, v in walking_distances.items()}
        tmp_path = "walking_distances.tmp"
        with open(tmp_path, "w") as f:
            json.dump(save_data, f, indent=4)
        os.replace(tmp_path, "walking_distances.json")
        changes_since_save = 0

# Final save
if changes_since_save > 0:
    print(f"================FINAL SAVE ({changes_since_save} changes)=================")
    save_data = {k: dict(v) for k, v in walking_distances.items()}
    tmp_path = "walking_distances.tmp"
    with open(tmp_path, "w") as f:
        json.dump(save_data, f, indent=4)
    os.replace(tmp_path, "walking_distances.json")

print(f"Completed processing {done} points")