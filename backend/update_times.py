import requests
import os
from dotenv import load_dotenv
from peewee import *
load_dotenv()
import time
import json
import random
from data import *
import numpy as np
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from collections import defaultdict
import traceback
api_key = os.getenv("TFL_API_KEY")
import influxdb_client, os, time
from influxdb_client import InfluxDBClient, WritePrecision
from influxdb_client import Point as InfluxPoint
from influxdb_client.client.write_api import SYNCHRONOUS
token = os.environ.get("INFLUXDB_TOKEN")
org = "local-org"
url = "http://influxdb:8086"
write_client = influxdb_client.InfluxDBClient(url=url, token=token, org=org)
vehicles = set()
arrivaltimes = {}
stop_names = {}
services = {}
status_codes = defaultdict(int)

start_of_day_epoch = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))

raildata_api_key = os.getenv("RAIL_MARKETPLACE_API_KEY_3")
min_lon, max_lon = -0.75, 0.55
min_lat, max_lat = 51.10, 51.85

services_lock = Lock()

status_lock = Lock()
def getStopName(stop_id):
    if stop_id in stop_names:
        return stop_names[stop_id]
    else:
        try:
            name = Point.get(Point.point_id == stop_id).name
            stop_names[stop_id] = name
            return name
        except Point.DoesNotExist:
            return stop_id
            print(f"No point for stop {stop_id}; skipping")
            return stop_id

def getInMins(time_unix):
    return int((time_unix-time.time())/60)

platforms = {}

points = []
def addBusTimes():
    global arrivaltimes
    url = "https://api.tfl.gov.uk/Mode/bus/Arrivals?count=-1"
    response = requests.get(url, headers={"Authorization": f"Bearer {api_key}"})
    times = response.json()
    latestinfo = {}
    bustimetable = open("bus_timetable.json", "r").read()
    bustimetable = json.loads(bustimetable)

    print(f"Loaded {len(times)} times")

    vehicle_info = {}

    vehicle_directions = {}

    for i in times:
        line = i["lineId"]
        vehicleId = i["vehicleId"]
        direction = i["direction"]
        if(i["currentLocation"]!=""):
            print(f"{json.dumps(i, indent=4)}")
        if vehicleId not in vehicles:
            vehicles.add(vehicleId)
        if line not in arrivaltimes:
            arrivaltimes[line] = {}
        if vehicleId not in arrivaltimes[line]:
            arrivaltimes[line][vehicleId] = []
        if not vehicleId in vehicle_directions: 
            vehicle_directions[vehicleId] = direction
        timeUnix = int(time.mktime(time.strptime(i["expectedArrival"], "%Y-%m-%dT%H:%M:%SZ")))
        arrivaltimes[line][vehicleId].append((i["naptanId"], timeUnix))
        if line not in latestinfo:
            latestinfo[line] = timeUnix
        else:
            latestinfo[line] = max(latestinfo[line], timeUnix)
        
        # Track vehicle info for route extension
        if vehicleId not in vehicle_info:
            vehicle_info[vehicleId] = {
                'line': line,
                'direction': direction,
                'stops': []
            }
        vehicle_info[vehicleId]['stops'].append((i["naptanId"], timeUnix))

    predictions = 0

    for vehicle in list(vehicle_info.keys()):
        line = vehicle_info[vehicle]['line']

        sorted_by_time = sorted(arrivaltimes[line][vehicle], key=lambda x: x[1])

        earliest_interval_time = None
        if line not in bustimetable:
            continue

        routes = bustimetable[line][vehicle_directions[vehicle]]
        if(len(routes) == 1):
            try:
                timetable = routes[list(routes.keys())[0]]

                if "intervals" not in timetable:
                    continue

                stop_intervals = {}
                for stop in timetable['intervals']:
                    stop_intervals[stop[0]] = stop[1]

                earliest_stop = None
                earliest_interval_time = None
                for stop in sorted_by_time:
                    if stop[0] in stop_intervals:
                        earliest_stop = stop
                        earliest_interval_time = stop_intervals[stop[0]]
                        break

                differences = []
                already_included = []
                # lprint(f"Vehicle {vehicle} on line {line}")
                
                # Track cumulative delay
                cumulative_delay = 0
                last_actual = earliest_stop[1]
                last_interval = earliest_interval_time
                
                for stop in sorted_by_time:
                    if stop[0] in stop_intervals:
                        already_included.append(stop[0])
                        interval_diff = (stop_intervals[stop[0]] - last_interval) # In minutes
                        expected_time = last_actual + (interval_diff * 60) # Convert to seconds
                        delay = stop[1] - expected_time
                        differences.append(delay)
                        last_actual = stop[1]
                        last_interval = stop_intervals[stop[0]]
                        # lprint(f"A| {getStopName(stop[0])} {getInMins(stop[1])}mins")

                # Use numpy median for more robust delay estimation
                if differences:
                    delay_per_stop = int(np.median(differences))
                    if delay_per_stop < 0:
                        delay_per_stop = 0
                else:
                    delay_per_stop = 60 # Default 1 minute delay if no data

                # lprint(f"Using median delay of {delay_per_stop} seconds per stop")

                # Reset to earliest known stop for predictions
                last_actual = earliest_stop[1]
                last_interval = earliest_interval_time

                for stop, interval in stop_intervals.items():
                    if stop not in already_included and interval > earliest_interval_time:
                        interval_diff = (interval - last_interval) # In minutes
                        predicted_time = last_actual + (interval_diff * 60) + delay_per_stop
                        # lprint(f"P| {getStopName(stop)}: {getInMins(predicted_time)}mins")
                        arrivaltimes[line][vehicle].append((stop, predicted_time))
                        last_actual = predicted_time
                        last_interval = interval
                        predictions += 1
            except:
                print(f"Error predicting {line}/{vehicle}")
    
    future_added = 0
    for line in bustimetable:
        if not line in latestinfo:
            print(f"No latest info for line {line}; skipping")
            continue
        for direction in list(bustimetable[line].keys()):
            for routeCode,route in bustimetable[line][direction].items():
                start = routeCode.split(":")[0]
                end = routeCode.split(":")[1]
                if "start_times" not in bustimetable[line][direction][routeCode]:
                    print(f"No start times for line {line} direction {direction} route {routeCode}")
                    continue
                for start_time in bustimetable[line][direction][routeCode]["start_times"]:
                    unixstart = start_of_day_epoch + start_time
                    if unixstart > latestinfo[line]+300:
                        future_added+=1
                        arrivaltimes[line][f"T{unixstart}"] = [(start, unixstart)]

                        for interval in bustimetable[line][direction][routeCode]["intervals"]:
                            arrivaltimes[line][f"T{unixstart}"].append((interval[0], unixstart+(interval[1]*60)))
    
    points.append(InfluxPoint("bus_data").field("vehicles", len(vehicle_info)))
    points.append(InfluxPoint("bus_data").field("times", len(times)))
    points.append(InfluxPoint("bus_data").field("future", future_added))
    points.append(InfluxPoint("bus_data").field("predictions", predictions))

    
    print(f"Added {future_added} future times")
    print(f"Created {predictions} predictions")
    # print(f"{len(vehicles)} vehicles for all lines")
    # print(f"{len(arrivaltimes)} lines for all directions")

def addTramTimes():
    global arrivaltimes
    vehicles.clear()
    tramtimetable = open("tram_timetable.json", "r").read()
    tramtimetable = json.loads(tramtimetable)

    print(f"Loaded {len(tramtimetable)} tram times")

    response = requests.get(f"https://api.tfl.gov.uk/Mode/tram/Arrivals?count=-1", headers={"Authorization": f"Bearer {api_key}"})
    times = response.json()

    # open("tram_arrivaltimes.json", "w").write(json.dumps(times, indent=4))

    latestinfo = {}

    for i in times:
        line = i["lineId"]
        vehicleId = i["vehicleId"]
        direction = i["direction"]
        if vehicleId not in vehicles:
            vehicles.add(vehicleId)
        if line not in arrivaltimes:
            arrivaltimes[line] = {}
        if vehicleId not in arrivaltimes[line]:
            arrivaltimes[line][vehicleId] = []
        # "2025-10-18T13:51:31.423Z"
        timeUnix = int(time.mktime(time.strptime(i["expectedArrival"], "%Y-%m-%dT%H:%M:%S.%fZ")))
        arrivaltimes[line][vehicleId].append((i["naptanId"], timeUnix))
        if line not in latestinfo:
            latestinfo[line] = timeUnix
        else:
            latestinfo[line] = max(latestinfo[line], timeUnix)

    for line in tramtimetable:
        if not line in latestinfo:
            print(f"No latest info for line {line}; skipping")
            continue
        for name in tramtimetable[line]:
            if "start_times" not in tramtimetable[line][name]:
                print(f"No start times for line {line} name {name}")
                continue
            for start_time in tramtimetable[line][name]["start_times"]:
                unixstart = start_of_day_epoch + start_time
                if unixstart > latestinfo[line]:
                    arrivaltimes[line][f"T{unixstart}"] = []
                    for interval in tramtimetable[line][name]["intervals"]:
                        arrivaltimes[line][f"T{unixstart}"].append((interval[0], unixstart+(interval[1]*60)))


    print(f"{len(vehicles)} vehicles for all lines")
    print(f"{len(arrivaltimes)} lines for all directions")

def addTubeTimes():
    global points
    response = requests.get(f"https://api.tfl.gov.uk/Mode/tube/Arrivals?count=-1", headers={"Authorization": f"Bearer {api_key}"})
    times = response.json()

    # open("tube_times.json", "w+").write(json.dumps(times, indent=4))

    tube_vehicles = {}
    tube_vehicle_count = 0

    tube_safe_ignore = {
        "940GZZLUWIG": ["metropolitan"],
        "940GZZLUPAC": ["hammersmith-city"],
        "940GZZLUBWT": ["hammersmith-city"],
        "940GZZLUNDN": ["metropolitan"]
    }

    tube_timetable = open("tube_timetable2.json", "r").read()
    tube_timetable = json.loads(tube_timetable)

    current_day = time.strftime("%A")
    for arrival in times:
        if "destinationNaptanId" not in arrival:
            continue
        vehicleId = f"{arrival['vehicleId']}/{arrival['lineId']}"
        if vehicleId not in tube_vehicles:
            tube_vehicle_count += 1
            tube_vehicles[vehicleId] = {
                "line": arrival["lineId"],
                "towards": arrival["towards"],
                "stops": []
            }
        arrival_time = 0
        if "." in arrival["expectedArrival"]:
            arrival_time = int(time.mktime(time.strptime(arrival["expectedArrival"], "%Y-%m-%dT%H:%M:%S.%fZ")))
        else:
            arrival_time = int(time.mktime(time.strptime(arrival["expectedArrival"], "%Y-%m-%dT%H:%M:%SZ")))
        tube_vehicles[vehicleId]["stops"].append((arrival["naptanId"], arrival_time))

    single_interval_count = 0
    vlogs = {}

    vehiclesWithOnePossible = 0

    # { line: [naptanId, naptanId, naptanId, ...]}
    possibleStops = {}

    for line in tube_timetable:
        if not line in possibleStops:
            possibleStops[line] = []
        linePossibleStops = set()
        for routeCode,route in tube_timetable[line].items():
            for interval in route["intervals"]:
                for stop in interval:
                    if not stop[0] in linePossibleStops:
                        linePossibleStops.add(stop[0])
        possibleStops[line] = list(linePossibleStops)

    # { routeCode: [naptanId, naptanId, naptanId, ...]}
    routePossibleStops = {}
    for line in tube_timetable:
        for routeCode,route in tube_timetable[line].items():
            if not routeCode in routePossibleStops:
                routePossibleStops[routeCode] = set()
            for interval in route["intervals"]:
                for stop in interval:
                    if not stop[0] in routePossibleStops[routeCode]:
                        routePossibleStops[routeCode].add(stop[0])


    knownVehicleRoutes = {}

    # vehicleId: set()
    potentialVehicleRoutes = {}

    for vehicleId, vehicle in tube_vehicles.items():
        if not vehicleId in vlogs:
            vlogs[vehicleId] = ""
        def vprint(message):
            vlogs[vehicleId] += f"{message}\n"
        if not vehicle["line"] in arrivaltimes:
            arrivaltimes[vehicle["line"]] = {}
        if not vehicleId in arrivaltimes[vehicle["line"]]:
            arrivaltimes[vehicle["line"]][vehicleId] = []
        # for stop in vehicle["stops"]:
        #     arrivaltimes[vehicle["line"]][vehicleId].append((stop[0], stop[1]))
        
        possibleCount = 0
        possibleRoutesFromTowards = 0
        possibleRoutesFromIntervals = 0
        possibleRouteCodes = set()
        if not vehicleId in potentialVehicleRoutes:
            potentialVehicleRoutes[vehicleId] = set()
        for routeCode,route in tube_timetable[vehicle["line"]].items():
            routeDestNaptan = routeCode.split(":")[1]
            routeStartNaptan = routeCode.split(":")[0]
            routePossible = False
            if(vehicle["towards"].split(" ")[0].strip().lower() in getStopName(routeDestNaptan).strip().lower()):
                routePossible = True
                possibleRoutesFromTowards+=1
                possibleRouteCodes.add(routeCode)
                potentialVehicleRoutes[vehicleId].add(routeCode)
                continue
            else:
                routePossible = True
            # observed stop sequence
            observed_ids = [s[0] for s in vehicle["stops"]]

            for interval in tube_timetable[vehicle["line"]][routeCode]["intervals"]:
                interval_ids = [s[0] for s in interval]

                # check if observed sequence is a subsequence of interval stops
                it = iter(interval_ids)
                if all(obs in it for obs in observed_ids):
                    # this interval is consistent with the observed sequence
                    routePossible = True
                    potentialVehicleRoutes[vehicleId].add(routeCode)
                    break
                else:
                    routePossible = False

                # # [naptanId, time]
                # earliestPossibleStop = None
                # latestPossibleStop = None



                # for stop in vehicle["stops"]:
                #     if stop[0] in possibleStops[vehicle["line"]]:
                #         if not stop[0] in routePossibleStops[routeCode]:
                #             routePossible = False
                #             break
                #         else:
                #             if earliestPossibleStop is None or stop[1] < earliestPossibleStop[1]:
                #                 earliestPossibleStop = stop
                #             if latestPossibleStop is None or stop[1] > latestPossibleStop[1]:
                #                 latestPossibleStop = stop
                
                # # Check if order coincides with the route
                # for interval in tube_timetable[vehicle["line"]][routeCode]["intervals"]:
                #     ordered_interval_stops = []
                #     for stop in interval:
                #         ordered_interval_stops.append(stop[0])
                #     if earliestPossibleStop is not None and latestPossibleStop is not None:
                #         if earliestPossibleStop[0] in ordered_interval_stops and latestPossibleStop[0] in ordered_interval_stops:
                #             if ordered_interval_stops.index(earliestPossibleStop[0]) > ordered_interval_stops.index(latestPossibleStop[0]):
                #                 routePossible = False
                #                 break
                
            if routePossible:
                possibleRouteCodes.add(routeCode)
                possibleRoutesFromIntervals+=1
        if possibleRoutesFromTowards>0:
            possibleCount = possibleRoutesFromTowards
        else:
            possibleCount = possibleRoutesFromIntervals
        if possibleCount == 1:
            knownVehicleRoutes[vehicleId] = list(possibleRouteCodes)[0]
            vehiclesWithOnePossible+=1
        # print(f"{vehicleId:<20} has {len(possibleRouteCodes)} possible routes: {possibleRouteCodes}")

    print(f"Found {vehiclesWithOnePossible}/{len(tube_vehicles)} vehicles with one possible route")

    singleIntervalVehicles = 0
    multiIntervalVehicles = 0

    predicted_tube_count = 0

    done_vehicles = set()

    for vehicleId,vehicle in tube_vehicles.items():
        if not vehicleId in knownVehicleRoutes:
            arrivaltimes[vehicle["line"]][vehicleId] = []
            for stop in vehicle["stops"]:
                if stop[0] in possibleStops[vehicle["line"]]:
                    arrivaltimes[vehicle["line"]][vehicleId].append((stop[0], stop[1]))
            # print(f"NP| {vehicleId:<20} Added {len(arrivaltimes[vehicle["line"]][vehicleId])}/{len(vehicle["stops"])} stops")
            continue
        
        # We need to find out what interval to use for the vehicle
        route = tube_timetable[vehicle["line"]][knownVehicleRoutes[vehicleId]]

        possibleIntervalIds = set()

        unix_now = time.time() - start_of_day_epoch
        unix_lower = unix_now - 7200  # Extended from 1 hour to 2 hours
        unix_upper = unix_now

        if current_day not in route["schedules"]:
            continue

        for intervalStart in route["schedules"][current_day]:
            intervalId = intervalStart[0]
            intervalUnix = intervalStart[1]
            if(intervalUnix>unix_lower and intervalUnix<unix_upper):
                possibleIntervalIds.add(intervalId)
        normal_time_added_count = 0
        if(len(possibleIntervalIds) == 1):

            interval = list(possibleIntervalIds)[0]

            ordered_stops = []
            for stop in vehicle["stops"]:
                if stop[0] in routePossibleStops[knownVehicleRoutes[vehicleId]]:
                    if stop[0] not in ordered_stops:    
                        ordered_stops.append(stop)

            if(len(ordered_stops) == 0):
                continue

            timetableIntervals = {}
            for intv in route["intervals"][interval]:
                timetableIntervals[intv[0]] = intv[1]

            first_interval_time = timetableIntervals[ordered_stops[0][0]]

            actualIntervals = {}
            actualTimes = {}
            for i in range(len(ordered_stops)):
                stop = ordered_stops[i]
                if i == 0:
                    actualIntervals[stop[0]] = 0 + first_interval_time
                else:
                    actualIntervals[stop[0]] = (stop[1] - ordered_stops[0][1])/60 + first_interval_time
                actualTimes[stop[0]] = stop[1]

            differences = []
            for stop in route["intervals"][interval]:
                if stop[0] in actualIntervals:
                    diff = actualIntervals[stop[0]] - stop[1]
                    differences.append(diff)

            median_diff_min = np.median(differences)
            if(len(differences) < 2): 
                median_diff_min = 0.5
            median_diff = int(median_diff_min*60)
            # print(f"P | {vehicleId:<20} MD {median_diff}s")

            added_stop_count = 0

            if not vehicleId in arrivaltimes[vehicle["line"]]:
                arrivaltimes[vehicle["line"]][vehicleId] = []


            vehicle_arrivaltimes = []
            for stop in route["intervals"][interval]:
                if stop[0] in actualIntervals:
                    actual_time = actualTimes[stop[0]]
                    vehicle_arrivaltimes.append((stop[0], actual_time))
                    added_stop_count+=1
                    # print(f"P | {vehicleId:<20} {actual_time} {getInMins(actual_time)}mins")
                else:
                    # Only add it if it is after the ones we have already added
                    if(timetableIntervals[stop[0]] <= timetableIntervals[ordered_stops[0][0]]):
                        continue
                    predicted_time = (timetableIntervals[stop[0]]-first_interval_time)*60 + median_diff + ordered_stops[0][1]
                    vehicle_arrivaltimes.append((stop[0], predicted_time))
                    added_stop_count+=1
                    # print(f"P | {vehicleId:<20} {first_interval_time} {timetableIntervals[stop[0]]} {getInMins(predicted_time)}mins")
            if(len(vehicle_arrivaltimes) > 0):
                arrivaltimes[vehicle["line"]][vehicleId].extend(vehicle_arrivaltimes)
                done_vehicles.add(vehicleId)
            predicted_tube_count+=1
            singleIntervalVehicles+=1
        
        elif(len(possibleIntervalIds) > 1 and len(possibleIntervalIds) <= 5):
            # Handle multiple possible intervals by finding common stops or averaging predictions
            multiIntervalVehicles += 1
            
            ordered_stops = []
            for stop in vehicle["stops"]:
                if stop[0] in routePossibleStops[knownVehicleRoutes[vehicleId]]:
                    if stop[0] not in ordered_stops:    
                        ordered_stops.append(stop)

            if(len(ordered_stops) == 0):
                continue

            # Collect predictions from all possible intervals
            all_predictions = {}  # stop_id -> list of (time, interval_id)
            
            for interval_id in possibleIntervalIds:
                timetableIntervals = {}
                for intv in route["intervals"][interval_id]:
                    timetableIntervals[intv[0]] = intv[1]
                
                if ordered_stops[0][0] not in timetableIntervals:
                    continue
                
                first_interval_time = timetableIntervals[ordered_stops[0][0]]

                actualIntervals = {}
                actualTimes = {}
                for i in range(len(ordered_stops)):
                    stop = ordered_stops[i]
                    if i == 0:
                        actualIntervals[stop[0]] = 0 + first_interval_time
                    else:
                        actualIntervals[stop[0]] = (stop[1] - ordered_stops[0][1])/60 + first_interval_time
                    actualTimes[stop[0]] = stop[1]

                differences = []
                for stop in route["intervals"][interval_id]:
                    if stop[0] in actualIntervals:
                        diff = actualIntervals[stop[0]] - stop[1]
                        differences.append(diff)

                median_diff_min = np.median(differences) if len(differences) >= 2 else 0.5
                median_diff = int(median_diff_min*60)

                # Predict stops for this interval
                for stop in route["intervals"][interval_id]:
                    if stop[0] not in all_predictions:
                        all_predictions[stop[0]] = []
                    
                    if stop[0] in actualIntervals:
                        actual_time = actualTimes[stop[0]]
                        all_predictions[stop[0]].append((actual_time, interval_id))
                    else:
                        if(timetableIntervals.get(stop[0], 0) <= timetableIntervals[ordered_stops[0][0]]):
                            continue
                        predicted_time = (timetableIntervals[stop[0]]-first_interval_time)*60 + median_diff + ordered_stops[0][1]
                        all_predictions[stop[0]].append((predicted_time, interval_id))
            
            # Now aggregate predictions: use median time for each stop
            vehicle_arrivaltimes = []
            for stop_id, predictions in all_predictions.items():
                if len(predictions) > 0:
                    # Use median time across all interval predictions
                    median_time = int(np.median([p[0] for p in predictions]))
                    vehicle_arrivaltimes.append((stop_id, median_time))
            
            if(len(vehicle_arrivaltimes) > 0):
                arrivaltimes[vehicle["line"]][vehicleId] = vehicle_arrivaltimes
                done_vehicles.add(vehicleId)
                predicted_tube_count += 1

    for vehicleId,vehicle in tube_vehicles.items():
        if vehicleId in done_vehicles:
            continue
        
        knownStops = set()
        for stop in vehicle["stops"]:
            knownStops.add(stop[0])

        latestKnownStop = None
        for stop in vehicle["stops"]:
            if stop[0] in possibleStops[vehicle["line"]]:
                if latestKnownStop is None or stop[1] > latestKnownStop[1]:
                    latestKnownStop = stop


        # if(len(potentialVehicleRoutes[vehicleId]) == 1 and latestKnownStop is not None):
        #     # Must have one possible route and many possible intervals, lets check which stops are common
        #     route = tube_timetable[vehicle["line"]][list(potentialVehicleRoutes[vehicleId])[0]]

        #     latestStopIndexInInterval = []


        #     for intervalIndex, interval in enumerate(route["intervals"]):
        #         latestStopIndexInInterval.append(-1)
        #         for stopIndex, intervalStop in enumerate(interval):
        #             if intervalStop[0] == latestKnownStop[0]:
        #                 latestStopIndexInInterval[intervalIndex] = stopIndex
        #                 break


        #     indexAdd = 1
        #     nextStops = []
        #     for index, interval in enumerate(route["intervals"]):
        #         if latestStopIndexInInterval[index] == -1:
        #             continue
        #         if indexAdd < len(interval):
        #             indexAddStop = latestStopIndexInInterval[index]+indexAdd
        #             if indexAddStop < len(interval):
        #                 nextStops.append(interval[indexAddStop])
        #         indexAdd += 1

        #     print(f"{vehicleId:<20} {nextStops}")

        # print(f"{vehicleId:<20} has {len(potentialVehicleRoutes[vehicleId])} potential routes: {potentialVehicleRoutes[vehicleId]}")
        

        # if not vehicleId in arrivaltimes[vehicle["line"]]:
        #     arrivaltimes[vehicle["line"]][vehicleId] = []
        # for stop in vehicle["stops"]:
        #     if stop[0] in possibleStops[vehicle["line"]]:
        #         arrivaltimes[vehicle["line"]][vehicleId].append((stop[0], stop[1]))

        
        normal_time_added_count+=1
    
    points.append(InfluxPoint("tube_data").field("vehicles", len(tube_vehicles)))
    points.append(InfluxPoint("tube_data").field("single_interval_vehicles", singleIntervalVehicles))
    points.append(InfluxPoint("tube_data").field("single_route_vehicles", vehiclesWithOnePossible))
    points.append(InfluxPoint("tube_data").field("predicted_tube_count", predicted_tube_count))

    print(f"Found {singleIntervalVehicles}/{len(tube_vehicles)} vehicles with one possible interval")
    print(f"Found {multiIntervalVehicles}/{len(tube_vehicles)} vehicles with multiple possible intervals (using median prediction)")
    print(f"Predicted {predicted_tube_count} tube times (up from {singleIntervalVehicles})")
    print(f"Added {normal_time_added_count} normal times")

def format_time(time_str):
    if(not ":" in time_str):
        raise Exception("Invalid time string")
    else:
        hours = int(time_str.split(":")[0])
        minutes = int(time_str.split(":")[1])
        return start_of_day_epoch + (hours * 3600) + (minutes * 60)

def process_stop(stop):
    stopId = stop.point_id
    stopName = stop.name
    stopLatitude = stop.latitude
    stopLongitude = stop.longitude
    
    if stopLatitude < min_lat or stopLatitude > max_lat or stopLongitude < min_lon or stopLongitude > max_lon:
        return None, stopName, "outside_bounds", None, None
    
    try:
        response = requests.get(
            f"https://api1.raildata.org.uk/1010-live-arrival-and-departure-boards-arr-and-dep1_1/LDBWS/api/20220120/GetArrDepBoardWithDetails/{stopId}",
            headers={
                "User-Agent": "",
                "x-apikey": raildata_api_key
            },
            params={
                "timeWindow": 120
            },
            timeout=30
        )
        
        status_code = response.status_code
        
        with status_lock:
            status_codes[status_code] += 1
        
        response.raise_for_status()
        times_data = response.json()
        
        local_services = {}
        train_services = times_data.get("trainServices", [])
        local_platforms = {}
        for train in train_services:
            if train.get("isCancelled"):
                continue
            
            serviceId = train["serviceID"][:7]

            time_unix = -1
            platform = train["platform"] if "platform" in train else "?"

            platformServiceId = f"{serviceId}/{stopId}"
            if platformServiceId not in local_platforms:
                local_platforms[platformServiceId] = platform
            if "sta" in train:
                time_unix = format_time(train["sta"])
            if "eta" in train and ":" in train["eta"]:
                time_unix = format_time(train["eta"])
            else:
                if "ata" in train and ":" in train["ata"]:
                    time_unix = format_time(train["ata"])
            service = {
                "unix_sta": time_unix,
                "station": stopId,
                "destination": train["destination"][0]["crs"],
                "operator": train["operator"],
                "platform": platform,
                "subsequent_stops": train["subsequentCallingPoints"] if "subsequentCallingPoints" in train else [],
                "previous_stops": train["previousCallingPoints"] if "previousCallingPoints" in train else []
            }
            
            local_services[serviceId] = service
        
        return local_services, stopName, len(train_services), status_code, local_platforms
    
    except Exception as e:
        traceback.print_exc()
        return None, stopName, "error", None, None

def addRailTimes():

    # Get all train stops
    trainstops = Point.select().where(Point.mode == "rail")
    trainstops_list = list(trainstops)

    print(f"Processing {len(trainstops_list)} train stops...")

    max_workers = 8

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_stop = {executor.submit(process_stop, stop): stop for stop in trainstops_list}
        
        for future in as_completed(future_to_stop):
            stop = future_to_stop[future]
            try:
                local_services, stopName, result, status_code, local_platforms = future.result()
                
                if result == "outside_bounds":
                    continue
                elif result == "error":
                    continue
                elif local_services:
                    new_count = 0
                    with services_lock:
                        for serviceId, service in local_services.items():
                            if serviceId not in services:
                                new_count += 1
                            services[serviceId] = service
                        for platformServiceId, platform in local_platforms.items():
                            platforms[platformServiceId] = platform
                    
            
            except Exception as e:
                traceback.print_exc()

    print(f"\nCompleted! Total unique services: {len(services)}")
    print(f"\nHTTP Status Codes:")
    for code, count in sorted(status_codes.items()):
        print(f"  {code}: {count}")
        points.append(InfluxPoint("rail_data").field("http_status_codes", count).tag("status_code", code))


    uniqueTrainCount = 0

    for serviceId, service in services.items():
        # if not service["operator"] in arrivaltimes:
        #     arrivaltimes[service["operator"]] = {}
        stops = []
        route = f"{service['operator']}/{service['destination']}"
        if not route in arrivaltimes:
            arrivaltimes[route] = {}
        stops.append((service["station"],service["unix_sta"]))
        if(len(service["previous_stops"])>0):
            for prevstop in service["previous_stops"][0]["callingPoint"]:
                if "at" in prevstop:
                    if(not ":" in prevstop["at"]):
                        unix_time=format_time(prevstop["st"])
                    else:
                        unix_time=format_time(prevstop["at"])
                    stops.append((prevstop["crs"], unix_time))
                elif "et" in prevstop:
                    unix_time=-1
                    if(not ":" in prevstop["et"]):
                        unix_time=format_time(prevstop["st"])
                    else:
                        unix_time=format_time(prevstop["et"])
                    stops.append((prevstop["crs"], unix_time))
        if(len(service["subsequent_stops"])>0):
            for subsequent_stop in service["subsequent_stops"][0]["callingPoint"]:
                if "at" in subsequent_stop:
                    if(not ":" in subsequent_stop["at"]):
                        unix_time=format_time(subsequent_stop["st"])
                    else:
                        unix_time=format_time(subsequent_stop["at"])
                    stops.append((subsequent_stop["crs"], unix_time))
                elif "et" in subsequent_stop:
                    unix_time=-1
                    if(not ":" in subsequent_stop["et"]):
                        unix_time=format_time(subsequent_stop["st"])
                    else:
                        unix_time=format_time(subsequent_stop["et"])
                    stops.append((subsequent_stop["crs"], unix_time))
        filtered_stops = []
        for stop in stops:
            if stop[1] > time.time():
                filtered_stops.append(stop)

        arrivaltimes[route][serviceId] = filtered_stops
        uniqueTrainCount += 1
    points.append(InfluxPoint("rail_data").field("train_count", uniqueTrainCount))
    # print(f"{list(arrivaltimes.keys())}")

def getArrivalsAndPlatforms():
    global arrivaltimes, platforms, points
    global vehicles, arrivaltimes, stop_names, services, status_codes

    vehicles = set()
    arrivaltimes = {}
    stop_names = {}
    services = {}
    status_codes = defaultdict(int)
    points = []

    write_api = write_client.write_api(write_options=SYNCHRONOUS)

    time_start = time.time()
    print(f"RELOADING TUBE TIME GRAPH")
    addTubeTimes()
    time_end = time.time()
    points.append(InfluxPoint("tube_reload").field("duration", time_end - time_start))

    time_start = time.time()
    print(f"RELOADING BUS TIME GRAPH")
    addBusTimes()
    time_end = time.time()
    points.append(InfluxPoint("bus_reload").field("duration", time_end - time_start))

    # print(f"RELOADING TRAM TIME GRAPH")
    # addTramTimes()


    time_start = time.time()
    print(f"RELOADING RAIL TIME GRAPH")
    addRailTimes()
    time_end = time.time()
    points.append(InfluxPoint("rail_reload").field("duration", time_end - time_start))

    for point in points:
        write_api.write(bucket="metrics", org="local-org", record=point)

    return {"arrivaltimes": arrivaltimes, "platforms": platforms}