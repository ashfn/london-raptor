import json
from collections import defaultdict
from typing import Dict, List, Tuple, Set, Optional
import heapq
import time
from data import connect_db, Point
from update_times import getArrivalsAndPlatforms


class McRAPTOR:
    def __init__(self, arrivaltimes: dict, walking_distances_file: str, max_walking_distance: float = 600):
        self.timetable = arrivaltimes
        with open(walking_distances_file, 'r') as f:
            self.walking = json.load(f)
        
        self.max_walking_distance = max_walking_distance
        try:
            db = connect_db()
        except:
            from data import db
        self.stop_names = {}
        for point in Point.select():
            self.stop_names[point.point_id] = point.name
        self.routes_at_stop = defaultdict(set)  # stop_id -> set of (route_id, vehicle_id)
        
        for route_id, vehicles in self.timetable.items():
            for vehicle_id, stops in vehicles.items():
                for stop_id, arrival_time in stops:
                    self.routes_at_stop[stop_id].add((route_id, vehicle_id))
    def get_trip_stops(self, route_id: str, vehicle_id: str) -> List[Tuple[str, int]]:
        return self.timetable[route_id][vehicle_id]
    
    def get_walking_neighbors(self, stop_id: str) -> List[Tuple[str, float]]:
        if stop_id not in self.walking:
            return []
        
        neighbors = []
        for neighbor_id, walk_seconds in self.walking[stop_id].items():
            if walk_seconds <= self.max_walking_distance:
                neighbors.append((neighbor_id, walk_seconds))
        return neighbors
    
    def get_stop_name(self, stop_id: str) -> str:
        return self.stop_names.get(stop_id, stop_id)
    
    def is_pareto_dominated(self, arrival_time: int, legs: int, pareto_set: List[Tuple[int, int]]) -> bool:
        for existing_time, existing_legs in pareto_set:
            if existing_time <= arrival_time and existing_legs <= legs:
                if existing_time < arrival_time or existing_legs < legs:
                    return True
        return False
    
    def add_to_pareto_set(self, arrival_time: int, legs: int, pareto_set: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        new_set = []
        for existing_time, existing_legs in pareto_set:
            if not (arrival_time <= existing_time and legs <= existing_legs and 
                    (arrival_time < existing_time or legs < existing_legs)):
                new_set.append((existing_time, existing_legs))
        new_set.append((arrival_time, legs))
        return new_set
    
    def route(self, origin: str, destination: str, departure_time: int, max_rounds: int = 5):
        pareto_labels = defaultdict(list)
        pareto_labels[origin] = [(departure_time, 0)]
        
        paths = defaultdict(dict)
        paths[origin][0] = None
        
        marked_stops = {origin}
        for neighbor, walking_time_seconds in self.get_walking_neighbors(origin):
            walking_time = int(walking_time_seconds)
            estimated_distance = walking_time_seconds * 1.4
            new_time = departure_time + walking_time
            
            pareto_labels[neighbor] = [(new_time, 0)]
            paths[neighbor][0] = (origin, 0, "WALK", None, estimated_distance, walking_time)
            marked_stops.add(neighbor)
        
        for k in range(1, max_rounds + 1):
            marked_stops_next = set()
            routes_to_scan = set()
            for stop in marked_stops:
                for route_id, vehicle_id in self.routes_at_stop.get(stop, []):
                    routes_to_scan.add((route_id, vehicle_id))
            for route_id, vehicle_id in routes_to_scan:
                trip_stops = self.get_trip_stops(route_id, vehicle_id)
                earliest_board_idx = None
                earliest_board_stop = None
                earliest_board_time = float('inf')
                board_label_idx = -1
                for i, (stop_id, arrival_time) in enumerate(trip_stops):
                    if stop_id in pareto_labels:
                        for label_idx, (label_time, label_legs) in enumerate(pareto_labels[stop_id]):
                            if label_time <= arrival_time and label_legs == k - 1:
                                if earliest_board_idx is None or i < earliest_board_idx:
                                    earliest_board_time = arrival_time
                                    earliest_board_stop = stop_id
                                    earliest_board_idx = i
                                    board_label_idx = label_idx
                
                if earliest_board_idx is not None:
                    for i in range(earliest_board_idx + 1, len(trip_stops)):
                        stop_id, arrival_time = trip_stops[i]
                        if arrival_time < earliest_board_time:
                            continue

                        if not self.is_pareto_dominated(arrival_time, k, pareto_labels[stop_id]):
                            old_len = len(pareto_labels[stop_id])
                            pareto_labels[stop_id] = self.add_to_pareto_set(
                                arrival_time, k, pareto_labels[stop_id]
                            )
                            new_len = len(pareto_labels[stop_id])
                            label_idx = new_len - 1
                            board_time = earliest_board_time
                            alight_time = arrival_time
                            paths[stop_id][label_idx] = (
                                earliest_board_stop, board_label_idx, route_id, vehicle_id, board_time, alight_time
                            )
                            
                            marked_stops_next.add(stop_id)
            walking_marked = set()
            
            vehicle_stops = set(marked_stops_next)
            for stop in vehicle_stops:
                for neighbor, walking_time_seconds in self.get_walking_neighbors(stop):
                    walking_time = int(walking_time_seconds)
                    estimated_distance = walking_time_seconds * 1.4
                    best_label = None
                    best_time = float('inf')
                    best_label_idx = -1
                    
                    for label_idx, (label_time, label_legs) in enumerate(pareto_labels[stop]):
                        if label_legs == k and label_time < best_time:
                            best_time = label_time
                            best_label_idx = label_idx
                    
                    if best_label_idx >= 0:
                        new_time = best_time + walking_time
                        
                        if not self.is_pareto_dominated(new_time, k, pareto_labels[neighbor]):
                            old_len_neighbor = len(pareto_labels[neighbor])
                            pareto_labels[neighbor] = self.add_to_pareto_set(
                                new_time, k, pareto_labels[neighbor]
                            )
                            new_len_neighbor = len(pareto_labels[neighbor])
                            new_label_idx = new_len_neighbor - 1
                            paths[neighbor][new_label_idx] = (
                                stop, best_label_idx, "WALK", None, estimated_distance, walking_time
                            )
                            
                            walking_marked.add(neighbor)
            
            marked_stops_next.update(walking_marked)
            marked_stops = marked_stops_next
        
        if destination not in pareto_labels:
            print("\nNo path found!")
            return []
        
        results = []
        for label_idx, (arrival_time, num_legs) in enumerate(pareto_labels[destination]):
            path = self.reconstruct_path(destination, label_idx, paths)
            results.append({
                'arrival_time': arrival_time,
                'num_legs': num_legs,
                'journey_time': arrival_time - departure_time,
                'path': path
            })
        
        results.sort(key=lambda x: (x['num_legs'], x['arrival_time']))
        
        return results
    
    def reconstruct_path(self, stop: str, label_idx: int, paths: dict) -> List[dict]:
        path = []
        current_stop = stop
        current_label_idx = label_idx
        
        while current_stop in paths and current_label_idx in paths[current_stop]:
            path_info = paths[current_stop][current_label_idx]
            
            if path_info is None:
                break
            
            if len(path_info) == 6:
                prev_stop, prev_label_idx, route_id, vehicle_id, time1, time2 = path_info
            else:
                prev_stop, prev_label_idx, route_id, vehicle_id = path_info
                time1 = None
                time2 = None
            
            if route_id == "WALK":
                path.append({
                    'type': 'walk',
                    'from': prev_stop,
                    'from_name': self.get_stop_name(prev_stop),
                    'to': current_stop,
                    'to_name': self.get_stop_name(current_stop),
                    'distance': time1,
                    'walk_time': time2
                })
            else:
                segment = {
                    'type': 'trip',
                    'route': route_id,
                    'vehicle': vehicle_id,
                    'from': prev_stop,
                    'from_name': self.get_stop_name(prev_stop),
                    'to': current_stop,
                    'to_name': self.get_stop_name(current_stop)
                }
                if time1 is not None and time2 is not None:
                    segment['ride_time'] = int(time2 - time1)
                path.append(segment)
            
            current_stop = prev_stop
            current_label_idx = prev_label_idx
        
        path.reverse()
        
        merged_path = []
        i = 0
        while i < len(path):
            segment = path[i]
            
            if segment['type'] == 'walk':
                total_walk_time = segment['walk_time']
                total_distance = segment['distance']
                start_stop = segment['from']
                start_name = segment['from_name']
                end_stop = segment['to']
                end_name = segment['to_name']
                
                j = i + 1
                while j < len(path) and path[j]['type'] == 'walk':
                    total_walk_time += path[j]['walk_time']
                    total_distance += path[j]['distance']
                    end_stop = path[j]['to']
                    end_name = path[j]['to_name']
                    j += 1
                
                merged_path.append({
                    'type': 'walk',
                    'from': start_stop,
                    'from_name': start_name,
                    'to': end_stop,
                    'to_name': end_name,
                    'distance': total_distance,
                    'walk_time': total_walk_time
                })
                
                i = j
            else:
                merged_path.append(segment)
                i += 1
        
        return merged_path