# London-Raptor

This is a project that uses RAPTOR and several transport APIs to provide live route generation for public transport to get from A to B. Uses OSRM for walking times and vite/react for the frontend with leaflet for the map.

A detailed writeup of the project is [here](https://asherfalcon.com/blog/posts/5)

I have provided a docker-compose file if you want to run this yourself, however you may need to change the image platforms, and you will definitely need to download the england osm pbf files and initialise them, with the following commands inside the osrm directory

### Download and process files
```
curl -L -o england.osm.pbf https://download.geofabrik.de/europe/united-kingdom/england-latest.osm.pbf

docker run -t -v $(pwd):/data osrm/osrm-backend osrm-extract -p /opt/foot.lua /data/england.osm.pbf

docker run -t -v $(pwd):/data osrm/osrm-backend osrm-partition /data/england.osrm

docker run -t -v $(pwd):/data osrm/osrm-backend osrm-customize /data/england.osrm
```


### Start the system

```
docker compose up --build
```

You will also need to find the correct api keys for the rail data api and place them in .env
```
TFL_API_KEY=your tfl api key
RAIL_MARKETPLACE_API_KEY_3=your rail data api key for the arrivals endpoint
```