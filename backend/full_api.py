from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import math
import time
import requests
from collections import deque
from mcraptor import McRAPTOR
from data import Point, connect_db
from update_times import getArrivalsAndPlatforms
import threading

app = Flask(__name__)
CORS(app)
TUBE_COLORS = {
    'bakerloo': '#B36305',
    'central': '#E32017',
    'circle': '#FFD300',
    'district': '#00782A',
    'hammersmith-city': '#F3A9BB',
    'jubilee': '#A0A5A9',
    'metropolitan': '#9B0056',
    'northern': '#000000',
    'piccadilly': '#003688',
    'victoria': '#0098D4',
    'waterloo-city': '#95CDBA',
}

RAIL_COLORS = {
    'Southeastern': '#1E1E50',
    'Southern': '#003F2E',
    'Thameslink': '#E9418B',
    'London Overground': '#EE7C0E',
    'Elizabeth Line': '#6E4C9F',
}

connect_db()

try:
    with open("linestrings.json", "r") as f:
        LINESTRINGS = json.load(f)
except FileNotFoundError:
    LINESTRINGS = {}

def reloadLiveData():
    global arrivaltimes_data, PLATFORMS, raptor
    data = getArrivalsAndPlatforms()
    arrivaltimes_data = data["arrivaltimes"]
    PLATFORMS = data["platforms"]
    raptor = McRAPTOR(
        arrivaltimes=arrivaltimes_data,
        walking_distances_file='walking_distances.json',
        max_walking_distance=1800
    )

reloadLiveData()

RAIL_ROUTES = set()
for route in arrivaltimes_data.keys():
    if isinstance(arrivaltimes_data[route], dict):
        RAIL_ROUTES.add(route)
try:
    with open("platforms.json", "r") as f:
        PLATFORMS = json.load(f)
except FileNotFoundError:
    PLATFORMS = {}

stop_names = {}
def get_stop_name(stop_id):
    if stop_id in stop_names:
        return stop_names[stop_id]
    else:
        try:
            name = Point.get(Point.point_id == stop_id).name
            stop_names[stop_id] = name
            return name
        except Point.DoesNotExist:
            return stop_id
def get_tube_line_info(route_id):
    route_lower = route_id.lower()
    if route_lower in TUBE_COLORS:
        line_name = route_lower.replace('-', ' ').title()
        return line_name, TUBE_COLORS[route_lower]

    return None, None

def get_rail_line_info(route_id):
    if(len(route_id.split("/")) > 1):
        if route_id.split("/")[0] in RAIL_COLORS:
            return route_id.split("/")[0] + "/" + get_stop_name(route_id.split("/")[1]), RAIL_COLORS[route_id.split("/")[0]]
        else:
            return route_id.split("/")[0] + "/" + get_stop_name(route_id.split("/")[1]), '#3b82f6'
    else:
        return route_id, '#3b82f6'

def get_stop_coords(naptan_id):
    try:
        stop = Point.get(Point.point_id == naptan_id)
        return (stop.longitude, stop.latitude)
    except:
        return None

def distance(coord1, coord2):
    lon1, lat1 = coord1
    lon2, lat2 = coord2
    
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
    
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    return c * 6371000

def point_to_segment_distance(point, seg_start, seg_end):
    px, py = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    
    seg_len_sq = (x2 - x1)**2 + (y2 - y1)**2
    
    if seg_len_sq == 0:
        return distance(point, seg_start), seg_start
    
    t = max(0, min(1, ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / seg_len_sq))
    
    closest_x = x1 + t * (x2 - x1)
    closest_y = y1 + t * (y2 - y1)
    
    return distance(point, (closest_x, closest_y)), (closest_x, closest_y)

def find_closest_point_on_route(coords, target_coord):
    min_dist = float('inf')
    best_segment_idx = 0
    best_projection = None
    
    for i in range(len(coords) - 1):
        dist, projection = point_to_segment_distance(target_coord, coords[i], coords[i + 1])
        if dist < min_dist:
            min_dist = dist
            best_segment_idx = i
            best_projection = projection
    
    return best_segment_idx, best_projection

def extract_partial_linestring(linestring, origin_coord, dest_coord):
    if isinstance(linestring, str):
        coords = json.loads(linestring)[0]
    else:
        coords = linestring[0]
    
    print(f"Total route points: {len(coords)}")
    print(f"Origin coord: {origin_coord}")
    print(f"Dest coord: {dest_coord}")
    
    origin_seg_idx, origin_projection = find_closest_point_on_route(coords, origin_coord)
    dest_seg_idx, dest_projection = find_closest_point_on_route(coords, dest_coord)
    
    print(f"Origin segment index: {origin_seg_idx}, projection: {origin_projection}")
    print(f"Dest segment index: {dest_seg_idx}, projection: {dest_projection}")
    
    origin_idx = None
    dest_idx = None

    for i, coord in enumerate(coords):
        if distance(coord, origin_projection) < 0.001:
            origin_idx = i
        if distance(coord, dest_projection) < 0.001:
            dest_idx = i

    if origin_idx is None or dest_idx is None:
        origin_idx = origin_seg_idx + 1 if origin_seg_idx + 1 < len(coords) else origin_seg_idx
        dest_idx = dest_seg_idx + 1 if dest_seg_idx + 1 < len(coords) else dest_seg_idx

    print(f"Using coordinate indices: origin={origin_idx}, dest={dest_idx}")

    queue = deque([(origin_idx, [origin_idx])])
    visited = {origin_idx}

    found_path = None

    while queue:
        current_idx, path = queue.popleft()

        if current_idx == dest_idx:
            found_path = path
            break

        neighbors = []
        if current_idx > 0:
            neighbors.append(current_idx - 1)
        if current_idx < len(coords) - 1:
            neighbors.append(current_idx + 1)

        for neighbor in neighbors:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    if found_path:
        partial_coords = [coords[i] for i in found_path]
        partial_coords[0] = origin_projection
        partial_coords[-1] = dest_projection
        partial = partial_coords
    else:
        print("BFS failed, using simple approach")
        partial = [origin_projection, dest_projection]
    
    print(f"Partial route has {len(partial)} points")
    
    result = [[coord[1], coord[0]] for coord in partial]
    print(f"Returning {len(result)} points")
    return result

def create_straight_line(origin_coord, dest_coord):
    lon1, lat1 = origin_coord
    lon2, lat2 = dest_coord
    return [[lat1, lon1], [lat2, lon2]]

def get_walking_route_from_osrm(origin_coord, dest_coord):
    lon1, lat1 = origin_coord
    lon2, lat2 = dest_coord
    
    try:
        url = f"http://osrm:5000/route/v1/walking/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"
        response = requests.get(url, timeout=2)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == 'Ok' and data.get('routes'):
                route = data['routes'][0]
                duration = route.get('duration', 0)
                distance = route.get('distance', 0)
                
                coords = route['geometry']['coordinates']
                leaflet_coords = [[coord[1], coord[0]] for coord in coords]
                
                return {
                    'coordinates': leaflet_coords,
                    'duration': int(duration),
                    'distance': distance
                }
    except Exception as e:
        print(f"OSRM walking route failed: {e}")
    
    return {
        'coordinates': create_straight_line(origin_coord, dest_coord),
        'duration': int(distance(origin_coord, dest_coord) / 1.4),
        'distance': distance(origin_coord, dest_coord)
    }

def get_linestring_for_segment(segment, segstops):
    origin_id = segment['from']
    dest_id = segment['to']
    origin_coord = get_stop_coords(origin_id)
    dest_coord = get_stop_coords(dest_id)

    if not origin_coord or not dest_coord:
        return {'coordinates': [], 'duration': 0, 'distance': 0}
    
    if segment['type'] == 'walk':
        return get_walking_route_from_osrm(origin_coord, dest_coord)
    
    route_id = segment['route']
    ride_time = segment.get('ride_time', 0)
    
    if route_id.lower() in TUBE_COLORS:
        return {
            'coordinates': create_straight_line(origin_coord, dest_coord),
            'duration': ride_time,
            'distance': distance(origin_coord, dest_coord)
        }

    if route_id in RAIL_ROUTES:
        origin_mode = None
        dest_mode = None
        try:
            origin_stop = Point.get(Point.point_id == origin_id)
            origin_mode = origin_stop.mode
        except:
            pass
        try:
            dest_stop = Point.get(Point.point_id == dest_id)
            dest_mode = dest_stop.mode
        except:
            pass

        if origin_mode == 'rail' or dest_mode == 'rail':
            stops_crs = list(map(lambda x: x["id"], segstops))
            origin_crs = origin_id.split("/")[0]
            dest_crs = dest_id.split("/")[0]
            return {
                'coordinates': create_straight_line(origin_coord, dest_coord),
                'duration': ride_time,
                'distance': distance(origin_coord, dest_coord)
            }
    
    if route_id in LINESTRINGS and LINESTRINGS[route_id]:
        try:
            coords = extract_partial_linestring(
                LINESTRINGS[route_id],
                origin_coord,
                dest_coord
            )
            return {
                'coordinates': coords,
                'duration': ride_time,
                'distance': distance(origin_coord, dest_coord)
            }
        except Exception as e:
            print(f"Error extracting linestring for {route_id}: {e}")
    
    return {
        'coordinates': create_straight_line(origin_coord, dest_coord),
        'duration': ride_time,
        'distance': distance(origin_coord, dest_coord)
    }

@app.route('/api/search', methods=['GET'])
def search_stops():
    from data import Connection
    query = request.args.get('q', '').strip().lower()
    
    if len(query) < 2:
        return jsonify([])
    
    all_results = []
    for stop in Point.select():
        if query in stop.name.lower():
            lines_info = []
            connections = Connection.select().where(
                (Connection.origin_point_id == stop.point_id)
            ).limit(50)
            
            line_ids = set()
            for conn in connections:
                line_ids.add(conn.line_id)
            
            for line_id in sorted(line_ids):
                connection_modes = set()
                try:
                    for conn in Connection.select().where(
                        (Connection.origin_point_id == stop.point_id) & (Connection.line_id == line_id)
                    ).limit(10):
                        try:
                            dest_stop = Point.get(Point.point_id == conn.destination_point_id)
                            connection_modes.add(dest_stop.mode)
                        except:
                            continue
                except:
                    pass

                if connection_modes:
                    if 'bus' in connection_modes:
                        lines_info.append({
                            'id': line_id.upper(),
                            'name': line_id.upper(),
                            'color': '#ef4444',
                            'type': 'bus'
                        })
                    elif 'rail' in connection_modes:
                        rail_name, rail_color = get_rail_line_info(line_id)
                        lines_info.append({
                            'id': line_id,
                            'name': rail_name or line_id,
                            'color': rail_color,
                            'type': 'rail'
                        })
                    elif 'tube' in connection_modes or 'underground' in connection_modes:
                        line_name, line_color = get_tube_line_info(line_id)
                        if line_name:
                            lines_info.append({
                                'id': line_id,
                                'name': line_name,
                                'color': line_color,
                                'type': 'tube'
                            })
                        else:
                            lines_info.append({
                                'id': line_id.upper(),
                                'name': line_id.upper(),
                                'color': '#ef4444',
                                'type': 'bus'
                            })
                    else:
                        lines_info.append({
                            'id': line_id.upper(),
                            'name': line_id.upper(),
                            'color': '#ef4444',
                            'type': 'bus'
                        })
                else:
                    line_name, line_color = get_tube_line_info(line_id)
                    if line_name:
                        lines_info.append({
                            'id': line_id,
                            'name': line_name,
                            'color': line_color,
                            'type': 'tube'
                        })
                    else:
                        rail_name, rail_color = get_rail_line_info(line_id)
                        if rail_name:
                            lines_info.append({
                                'id': line_id,
                                'name': rail_name,
                                'color': rail_color,
                                'type': 'rail'
                            })
                        else:
                            if stop.mode == "bus":
                                lines_info.append({
                                    'id': line_id.upper(),
                                    'name': line_id.upper(),
                                    'color': '#ef4444',
                                    'type': 'bus'
                                })
                            elif stop.mode == "rail":
                                lines_info.append({
                                    'id': line_id.upper(),
                                    'name': line_id.upper(),
                                    'color': '#3b82f6',
                                    'type': 'rail'
                                })
            
            all_results.append({
                'id': stop.point_id,
                'name': stop.name,
                'lat': stop.latitude,
                'lng': stop.longitude,
                'mode': stop.mode,
                'lines': lines_info[:10],
                'line_count': len(line_ids)
            })
    
    name_to_best = {}
    for result in all_results:
        name = result['name']
        is_rail = result['mode'] == 'rail'

        if name not in name_to_best:
            name_to_best[name] = result
        else:
            existing = name_to_best[name]
            existing_is_rail = existing['mode'] == 'rail'

            if is_rail and not existing_is_rail:
                name_to_best[name] = result
            elif not is_rail and existing_is_rail:
                continue
            elif result['line_count'] > existing['line_count']:
                name_to_best[name] = result

    results = list(name_to_best.values())

    results.sort(key=lambda x: (
        0 if 'underground' in x['mode'].lower() or 'tube' in x['mode'].lower() or x['mode'] == 'rail' else 1,
        -x['line_count']
    ))

    for r in results:
        r.pop('line_count', None)
    
    return jsonify(results[:20])

@app.route('/api/route', methods=['POST'])
def route():
    data = request.json
    origin = data.get('origin')
    destination = data.get('destination')
    
    if not origin or not destination:
        return jsonify({'error': 'Missing origin or destination'}), 400
    
    departure_time = int(time.time())
    
    try:
        results = raptor.route(origin, destination, departure_time, max_rounds=5)
        
        if not results:
            return jsonify({'error': 'No route found'}), 404
        
        best = results[0]
        current_time = departure_time
        segments = []
        
        for segment in best['path']:
            seg_data = {
                'type': segment['type'],
                'from': segment['from_name'],
                'to': segment['to_name'],
                'from_id': segment['from'],
                'to_id': segment['to'],
                'start_time': current_time
            }
            
            if segment['type'] != 'walk':
                seg_data['route'] = segment['route']
                seg_data['vehicle'] = segment.get('vehicle', '')
                
                route_id = segment['route']
                origin_stop_id = segment['from']
                dest_stop_id = segment['to']

                origin_mode = None
                dest_mode = None
                try:
                    origin_stop = Point.get(Point.point_id == origin_stop_id)
                    origin_mode = origin_stop.mode
                except:
                    pass
                try:
                    dest_stop = Point.get(Point.point_id == dest_stop_id)
                    dest_mode = dest_stop.mode
                except:
                    pass

                if origin_mode == 'bus' or dest_mode == 'bus':
                    seg_data['mode'] = 'bus'
                    seg_data['line_color'] = '#ef4444'
                    print(f"Route: {route_id}, Set mode to: {seg_data['mode']} (bus stops)")
                elif origin_mode in ['tube', 'underground'] or dest_mode in ['tube', 'underground']:
                    tube_name, tube_color = get_tube_line_info(route_id)
                    if tube_name:
                        seg_data['mode'] = 'tube'
                        seg_data['tube_line'] = tube_name
                        seg_data['line_color'] = tube_color
                        print(f"Route: {route_id}, Set mode to: {seg_data['mode']} (tube stops)")
                    else:
                        seg_data['mode'] = 'bus'
                        seg_data['line_color'] = '#ef4444'
                        print(f"Route: {route_id}, Set mode to: {seg_data['mode']} (bus fallback)")
                elif (origin_mode == 'rail' or dest_mode == 'rail') and route_id in RAIL_ROUTES:
                    rail_name, rail_color = get_rail_line_info(route_id)
                    seg_data['mode'] = 'rail'
                    seg_data['rail_line'] = rail_name or route_id
                    seg_data['line_color'] = rail_color
                    vehicleId = segment.get('vehicle', '')
                    platformId = f"{vehicleId}/{origin_stop_id}"
                    print(f"Platform ID: {platformId}")
                    if platformId in PLATFORMS:
                        seg_data['platform'] = PLATFORMS[platformId]
                    else:
                        seg_data['platform'] = '?'
                    print(f"Route: {route_id}, Set mode to: {seg_data['mode']} (rail stops)")
                else:
                    tube_name, tube_color = get_tube_line_info(route_id)
                    if tube_name:
                        seg_data['mode'] = 'tube'
                        seg_data['tube_line'] = tube_name
                        seg_data['line_color'] = tube_color
                    else:
                        rail_name, rail_color = get_rail_line_info(route_id)
                        if rail_name:
                            seg_data['mode'] = 'rail'
                            seg_data['rail_line'] = rail_name
                            seg_data['line_color'] = rail_color
                        else:
                            seg_data['mode'] = 'bus'
                            seg_data['line_color'] = '#ef4444'

                print(f"Route: {route_id}, Final mode: {seg_data['mode']}, Color: {seg_data['line_color']}")
                
                try:
                    trip_stops = raptor.get_trip_stops(segment['route'], segment['vehicle'])
                    
                    board_idx = None
                    alight_idx = None
                    for idx, (stop_id, arrival_time) in enumerate(trip_stops):
                        if stop_id == segment['from']:
                            board_idx = idx
                        if stop_id == segment['to']:
                            alight_idx = idx
                    
                    if board_idx is not None and alight_idx is not None:
                        if board_idx < alight_idx:
                            stops_segment = trip_stops[board_idx:alight_idx + 1]
                        else:
                            stops_segment = trip_stops[alight_idx:board_idx + 1]
                            stops_segment.reverse()
                        
                        stops_list = []
                        for stop_id, arrival_time in stops_segment:
                            stops_list.append({
                                'id': stop_id,
                                'name': raptor.get_stop_name(stop_id),
                                'time': arrival_time
                            })
                        
                        seg_data['stops'] = stops_list
                except Exception as e:
                    print(f"Error getting intermediate stops: {e}")
            
            seg_data['end_time'] = current_time
            if "stops" in seg_data:
                linestring_data = get_linestring_for_segment(segment, seg_data['stops'])
            else:
                print(f"STOPS NOT IN DATA")
                linestring_data = get_linestring_for_segment(segment, [])
            current_time += linestring_data['duration']
            seg_data['coordinates'] = linestring_data['coordinates']
            seg_data['duration'] = linestring_data['duration']
            seg_data['distance'] = linestring_data['distance']
            segments.append(seg_data)

        return jsonify({
            'journey_time': best['journey_time'],
            'journey_minutes': best['journey_time'] // 60,
            'num_legs': best['num_legs'],
            'arrival_time': best['arrival_time'],
            'departure_time': departure_time,
            'segments': segments
        })
        
    except Exception as e:
        print(f"Routing error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
def run_periodic():
    while True:
        print(f"Reloading live data")
        reloadLiveData()
        time.sleep(30)

def start_background_thread():
    def run():
        thread = threading.Thread(target=run_periodic, daemon=True)
        thread.start()
        print("Background thread started.")
    threading.Thread(target=run, daemon=True).start()

if __name__ == '__main__':
    print("Full Routing API")
    print("Listening on http://localhost:4225")

    app.run(debug=False, port=4225, host='0.0.0.0')

