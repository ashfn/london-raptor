import { useEffect, useRef, useState } from 'react'
import 'leaflet/dist/leaflet.css'
import './App.css'
import L from 'leaflet'

interface Stop {
  id: string
  name: string
  lat: number
  lng: number
  mode: string
}

function App() {
  const mapRef = useRef<HTMLDivElement | null>(null)
  const mapInstanceRef = useRef<L.Map | null>(null)
  const [fromValue, setFromValue] = useState('')
  const [toValue, setToValue] = useState('')
  const [activeField, setActiveField] = useState<'from' | 'to' | null>(null)
  const routeLayerRef = useRef<L.Polyline | null>(null)
  const [fromStop, setFromStop] = useState<Stop | null>(null)
  const [toStop, setToStop] = useState<Stop | null>(null)
  const [suggestions, setSuggestions] = useState<Stop[]>([])
  const [routeInfo, setRouteInfo] = useState<any>(null)
  const [expandedSegments, setExpandedSegments] = useState<Set<number>>(new Set())
  const [expandedStops, setExpandedStops] = useState<Set<number>>(new Set())
  const debounceTimerRef = useRef<number | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [lastRefreshed, setLastRefreshed] = useState<number | null>(null)

  // Calculate distance between two coordinates in meters using Haversine formula
  const calculateDistance = (coord1: [number, number], coord2: [number, number]): number => {
    const R = 6371e3 // Earth radius in meters
    const lat1 = coord1[0] * Math.PI / 180
    const lat2 = coord2[0] * Math.PI / 180
    const deltaLat = (coord2[0] - coord1[0]) * Math.PI / 180
    const deltaLon = (coord2[1] - coord1[1]) * Math.PI / 180

    const a = Math.sin(deltaLat / 2) * Math.sin(deltaLat / 2) +
              Math.cos(lat1) * Math.cos(lat2) *
              Math.sin(deltaLon / 2) * Math.sin(deltaLon / 2)
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))

    return R * c
  }

  const fetchRoute = async () => {
    const map = mapInstanceRef.current
    if (!map || !fromStop || !toStop) return

    setIsLoading(true)
    
    try {
      // Call routing API
      const res = await fetch('http://localhost:4225/api/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          origin: fromStop.id,
          destination: toStop.id
        })
      })
      
      if (!res.ok) {
        const error = await res.json()
        alert(error.error || 'Routing failed')
        setIsLoading(false)
        return
      }
      
      const data = await res.json()
      setLastRefreshed(Date.now())
      
      // Remove previous route and shine
      if (routeLayerRef.current) {
        map.removeLayer(routeLayerRef.current)
        routeLayerRef.current = null
      }

      // Draw each segment with different colors, connecting walking to previous/next
      const layers: L.Polyline[] = []
      
      for (let i = 0; i < data.segments.length; i++) {
        const segment = data.segments[i]
        if (segment.coordinates && segment.coordinates.length > 1) {
          let coords: [number, number][] = segment.coordinates.map((c: number[]) => [c[0], c[1]])
          
          const isWalking = segment.type === 'walk'
          
          // If this is a walking segment, extend it to connect to prev/next segments
          if (isWalking) {
            // Connect to previous segment's end point
            if (i > 0) {
              const prevSegment = data.segments[i - 1]
              if (prevSegment.coordinates && prevSegment.coordinates.length > 0) {
                const prevEnd = prevSegment.coordinates[prevSegment.coordinates.length - 1]
                coords.unshift([prevEnd[0], prevEnd[1]])
              }
            }
            // Connect to next segment's start point
            if (i < data.segments.length - 1) {
              const nextSegment = data.segments[i + 1]
              if (nextSegment.coordinates && nextSegment.coordinates.length > 0) {
                const nextStart = nextSegment.coordinates[0]
                coords.push([nextStart[0], nextStart[1]])
              }
            }
          }
          
          // Determine line color - use segment's line_color if available, otherwise default
          const lineColor = isWalking ? '#3b82f6' : (segment.line_color || '#ef4444')
          
          const line = L.polyline(coords, {
            color: lineColor,
            weight: 7,
            opacity: 1,
            lineCap: 'round',
            lineJoin: 'round',
            smoothFactor: 3.0,
          }).addTo(map)
          
          layers.push(line)
        }
      }
      
      // Store all layers
      if (layers.length > 0) {
        // Collect all markers
        const allMarkers: L.Layer[] = [...layers]
        
        // Track transfer points for smart labeling
        const transferPoints: any[] = []
        
        // First pass: identify transfer points
        // Find all pairs of transport segments (may have walk between)
        for (let i = 0; i < data.segments.length; i++) {
          const segment = data.segments[i]
          if (segment.type === 'walk' || !segment.coordinates) continue
          
          // Look for the next transport segment
          let walkTime = 0
          for (let j = i + 1; j < data.segments.length; j++) {
            const nextSegment = data.segments[j]
            if (nextSegment.type === 'walk') {
              // Accumulate walk time
              walkTime += nextSegment.duration || 0
              continue
            }
            if (nextSegment.type !== 'walk' && nextSegment.coordinates && nextSegment.coordinates.length > 0) {
              // Calculate midpoint and distance between end of first segment and start of next segment
              const fromEnd = segment.coordinates[segment.coordinates.length - 1]
              const toStart = nextSegment.coordinates[0]
              const midLat = (fromEnd[0] + toStart[0]) / 2
              const midLng = (fromEnd[1] + toStart[1]) / 2
              const dist = calculateDistance([fromEnd[0], fromEnd[1]], [toStart[0], toStart[1]])
              
              // Found a transfer from segment i to segment j
              transferPoints.push({
                fromIdx: i,
                toIdx: j,
                fromSegment: segment,
                toSegment: nextSegment,
                walkMinutes: walkTime > 0 ? Math.max(1, Math.ceil(walkTime / 60)) : 0,
                distance: dist,  // Distance between stops
                isCloseTransfer: dist <= 10,  // Within 10 meters
                coord: [midLat, midLng]  // Position in the middle
              })
              break  // Only look for the immediate next transport
            }
          }
        }
        
        // Add markers at start, end, and transfer points
        data.segments.forEach((segment: any, idx: number) => {
          if (segment.coordinates && segment.coordinates.length > 0) {
            const isFirst = idx === 0
            const isLast = idx === data.segments.length - 1
            
            // Start point - black dot with "Start" label
            if (isFirst) {
              const startCoord = segment.coordinates[0]
              const startMarker = L.circleMarker([startCoord[0], startCoord[1]], {
                radius: 4,
                color: '#000000',
                weight: 1,
                fillColor: '#000000',
                fillOpacity: 1,
              })
              startMarker.bindTooltip('Start', { permanent: true, direction: 'top', offset: [0, -8], className: 'transfer-label-black' })
              allMarkers.push(startMarker)
            }
            
            // End point - black dot with "End" label
            if (isLast) {
              const endCoord = segment.coordinates[segment.coordinates.length - 1]
              const endMarker = L.circleMarker([endCoord[0], endCoord[1]], {
                radius: 4,
                color: '#000000',
                weight: 1,
                fillColor: '#000000',
                fillOpacity: 1,
              })
              endMarker.bindTooltip('End', { permanent: true, direction: 'top', offset: [0, -8], className: 'transfer-label-black' })
              allMarkers.push(endMarker)
            }
            
            // Get off labels (when transferring to another bus/tube, or at significant distance)
            if (!isLast && segment.type !== 'walk') {
              // Check if there's another bus/tube segment after this one
              let hasNextTransportSegment = false
              
              // Look ahead to find the next bus/tube segment
              for (let j = idx + 1; j < data.segments.length; j++) {
                const futureSegment = data.segments[j]
                if (futureSegment.type !== 'walk' && futureSegment.coordinates && futureSegment.coordinates.length > 0) {
                  hasNextTransportSegment = true
                  break
                }
              }
              
              // Show "Get off" if there's another transport segment or the next segment is far
              const endCoord = segment.coordinates[segment.coordinates.length - 1]
              const nextSegment = data.segments[idx + 1]
              
              let shouldShowGetOff = hasNextTransportSegment
              
              // Also check distance to next segment (walk or transport)
              if (nextSegment && nextSegment.coordinates && nextSegment.coordinates.length > 0) {
                const nextStartCoord = nextSegment.coordinates[0]
                const distance = calculateDistance(
                  [endCoord[0], endCoord[1]], 
                  [nextStartCoord[0], nextStartCoord[1]]
                )
                
                // If > 5 meters, also show "Get off" label
                if (distance > 5) {
                  shouldShowGetOff = true
                }
              }
              
              if (shouldShowGetOff) {
                const getOffMarker = L.circleMarker([endCoord[0], endCoord[1]], {
                  radius: 0,
                  fillOpacity: 0,
                  stroke: false
                })
                
                const stopName = segment.to
                const lineColor = segment.line_color || '#ef4444'
                let labelHtml = ''
                
                const segMode = String(segment.mode || '').toLowerCase();
                const isRail = segMode === 'rail' || segment.rail_line;
                const isTube = segMode === 'tube' || segment.tube_line;

                if (isTube) {
                  const lineName = segment.tube_line || 'Underground'
                  labelHtml = `Get off <span class="transport-badge" style="background-color: ${lineColor};">${lineName}</span> at ${stopName}`
                } else if (isRail) {
                  const lineName = segment.rail_line || segment.route
                  labelHtml = `Get off <span class="transport-badge" style="background-color: ${lineColor};">${lineName}</span> at ${stopName}`
                } else {
                  const busNumber = segment.route.toUpperCase()
                  labelHtml = `Get off bus <span class="transport-badge" style="background-color: ${lineColor};">${busNumber}</span> at ${stopName}`
                }
                
                getOffMarker.bindTooltip(labelHtml, {
                  permanent: false,  // Controlled by zoom
                  direction: 'top',
                  offset: [0, -8],
                  className: 'transfer-label-white'
                })
                allMarkers.push(getOffMarker)
                
                // Store marker reference for zoom control
                // Check if this is part of a close transfer
                const transfer = transferPoints.find(tp => tp.fromIdx === idx)
                ;(getOffMarker as any)._isDetailLabel = true
                ;(getOffMarker as any)._isCloseTransfer = transfer?.isCloseTransfer || false
              }
            }
            
            // Walking labels (in the middle of the walking segment)
            if (segment.type === 'walk' && segment.coordinates.length >= 2) {
              // Get middle point of the walking segment
              const midIndex = Math.floor(segment.coordinates.length / 2)
              const midCoord = segment.coordinates[midIndex]
              
              const walkMarker = L.circleMarker([midCoord[0], midCoord[1]], {
                radius: 0,
                fillOpacity: 0,
                stroke: false
              })
              
              const walkMinutes = Math.max(1, Math.ceil(segment.duration / 60))
              const walkIcon = `<svg width="12" height="12" viewBox="0 0 24 24" fill="white"><path d="M13.5 5.5c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zM9.8 8.9L7 23h2.1l1.8-8 2.1 2v6h2v-7.5l-2.1-2 .6-3C14.8 12 16.8 13 19 13v-2c-1.9 0-3.5-1-4.3-2.4l-1-1.6c-.4-.6-1-1-1.7-1-.3 0-.5.1-.8.1L6 8.3V13h2V9.6l1.8-.7"/></svg>`
              const labelHtml = `${walkIcon}Walk ${walkMinutes} min${walkMinutes !== 1 ? 's' : ''}`
              
              walkMarker.bindTooltip(labelHtml, {
                permanent: false,  // Not permanent - controlled by zoom
                direction: 'center',
                offset: [0, 0],
                className: 'walk-label'
              })
              
              allMarkers.push(walkMarker)
              
              // Show/hide based on zoom level
              const updateWalkLabelVisibility = () => {
                const zoom = map.getZoom()
                if (zoom >= 16) {
                  walkMarker.openTooltip()
                } else {
                  walkMarker.closeTooltip()
                }
              }
              
              map.on('zoomend', updateWalkLabelVisibility)
              updateWalkLabelVisibility()
            }
            
            // Board labels (when switching from walk to bus/tube)
            if (!isFirst && segment.type !== 'walk') {
              const startCoord = segment.coordinates[0]
              const boardMarker = L.circleMarker([startCoord[0], startCoord[1]], {
                radius: 0, // Invisible marker, only showing label
                fillOpacity: 0,
                stroke: false
              })
              
              // Create HTML label - different for tube vs bus
              const stopName = segment.from
              const lineColor = segment.line_color || '#ef4444'
              const segMode = String(segment.mode || '').toLowerCase();
              const isRail = segMode === 'rail' || segment.rail_line;
              const isTube = segMode === 'tube' || segment.tube_line;
              let labelHtml = ''

              if (isTube) {
                const lineName = segment.tube_line || 'Underground'
                labelHtml = `Board <span class="transport-badge" style="background-color: ${lineColor};">${lineName}</span> at ${stopName}`
              } else if (isRail) {
                const lineName = segment.rail_line || segment.route
                labelHtml = `Board <span class="transport-badge" style="background-color: ${lineColor};">${lineName}</span> at ${stopName}`
              } else {
                const busNumber = segment.route.toUpperCase()
                labelHtml = `Board bus <span class="transport-badge" style="background-color: ${lineColor};">${busNumber}</span> at ${stopName}`
              }
              
              boardMarker.bindTooltip(labelHtml, { 
                permanent: false,  // Controlled by zoom
                direction: 'top', 
                offset: [0, -8], 
                className: 'transfer-label-white' 
              })
              allMarkers.push(boardMarker)
              
              // Store marker references for zoom control
              // Check if this is part of a close transfer
              const transfer = transferPoints.find(tp => tp.toIdx === idx)
              ;(boardMarker as any)._isDetailLabel = true
              ;(boardMarker as any)._isCloseTransfer = transfer?.isCloseTransfer || false
            }
          }
        })
        
        // Add combined transfer labels for when zoomed out
        transferPoints.forEach((tp: any) => {
          const transferMarker = L.circleMarker([tp.coord[0], tp.coord[1]], {
            radius: 0,
            fillOpacity: 0,
            stroke: false
          })
          
          const fromColor = tp.fromSegment.line_color || '#ef4444'
          const toColor = tp.toSegment.line_color || '#ef4444'
          
          let fromLabel = ''
          let toLabel = ''

          const fromMode = String(tp.fromSegment.mode || '').toLowerCase();
          const toMode = String(tp.toSegment.mode || '').toLowerCase();
          const fromIsRail = fromMode === 'rail' || tp.fromSegment.rail_line;
          const fromIsTube = fromMode === 'tube' || tp.fromSegment.tube_line;
          const toIsRail = toMode === 'rail' || tp.toSegment.rail_line;
          const toIsTube = toMode === 'tube' || tp.toSegment.tube_line;

          if (fromIsTube) {
            fromLabel = tp.fromSegment.tube_line || 'Underground'
          } else if (fromIsRail) {
            fromLabel = tp.fromSegment.rail_line || tp.fromSegment.route
          } else {
            fromLabel = tp.fromSegment.route.toUpperCase()
          }

          if (toIsTube) {
            toLabel = tp.toSegment.tube_line || 'Underground'
          } else if (toIsRail) {
            toLabel = tp.toSegment.rail_line || tp.toSegment.route
          } else {
            toLabel = tp.toSegment.route.toUpperCase()
          }
          
          let labelHtml = ''
          if (tp.isCloseTransfer) {
            // Close transfer: show station name above
            const stationName = tp.toSegment.from  // Station name where the transfer happens
            labelHtml = `<div class="transfer-close"><div class="transfer-station">${stationName}</div><div class="transfer-line">Transfer from <span class="transport-badge" style="background-color: ${fromColor};">${fromLabel}</span> to <span class="transport-badge" style="background-color: ${toColor};">${toLabel}</span></div></div>`
          } else if (tp.walkMinutes > 0) {
            labelHtml = `Transfer from <span class="transport-badge" style="background-color: ${fromColor};">${fromLabel}</span> to <span class="transport-badge" style="background-color: ${toColor};">${toLabel}</span> (${tp.walkMinutes} min walk)`
          } else {
            labelHtml = `Transfer from <span class="transport-badge" style="background-color: ${fromColor};">${fromLabel}</span> to <span class="transport-badge" style="background-color: ${toColor};">${toLabel}</span>`
          }
          
          transferMarker.bindTooltip(labelHtml, {
            permanent: false,
            direction: 'top',
            offset: [0, -8],
            className: 'transfer-label-white'
          })
          
          allMarkers.push(transferMarker)
          ;(transferMarker as any)._isTransferLabel = true
          ;(transferMarker as any)._isCloseTransfer = tp.isCloseTransfer
        })
        
        // Create a layer group with all layers and markers
        const layerGroup = L.layerGroup(allMarkers).addTo(map)
        routeLayerRef.current = layerGroup as any
        
        // Zoom-dependent label visibility
        const updateLabelVisibility = () => {
          const zoom = map.getZoom()
          allMarkers.forEach((marker: any) => {
            if (marker._isDetailLabel) {
              // Show detailed get off/board labels only when zoomed in far enough AND not a close transfer
              if (zoom >= 16 && !marker._isCloseTransfer) {
                marker.openTooltip?.()
              } else {
                marker.closeTooltip?.()
              }
            } else if (marker._isTransferLabel) {
              // Show transfer labels when zoomed out/medium zoom OR if it's a close transfer (always show)
              if (zoom < 16 || marker._isCloseTransfer) {
                marker.openTooltip?.()
              } else {
                marker.closeTooltip?.()
              }
            }
          })
        }
        
        map.on('zoomend', updateLabelVisibility)
        updateLabelVisibility()
        
        // Fit bounds to all layers
        const bounds = L.latLngBounds([])
        layers.forEach(layer => bounds.extend(layer.getBounds()))
        map.fitBounds(bounds, { padding: [50, 50] })
      }
      
      // Show detailed route info
      setRouteInfo(data)
      
      // Initialize expanded segments
      // If there's more than one transport leg, collapse all by default
      const transportLegs = data.segments.filter((seg: any) => seg.type !== 'walk')
      const initialExpanded = new Set<number>()
      
      if (transportLegs.length <= 1) {
        // Single transport leg - expand it
        data.segments.forEach((seg: any, idx: number) => {
          if (seg.type !== 'walk') {
            initialExpanded.add(idx)
          }
        })
      }
      // Otherwise all collapsed by default
      
      setExpandedSegments(initialExpanded)
      setIsLoading(false)
      
    } catch (err) {
      console.error('Routing failed', err)
      alert('Routing failed. Make sure the API is running.')
      setIsLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setActiveField(null)
    await fetchRoute()
  }

  const getTimeAgo = (timestamp: number): string => {
    const seconds = Math.floor((Date.now() - timestamp) / 1000)
    if (seconds < 60) return 'Just now'
    const minutes = Math.floor(seconds / 60)
    if (minutes === 1) return '1 min ago'
    if (minutes < 60) return `${minutes} mins ago`
    const hours = Math.floor(minutes / 60)
    if (hours === 1) return '1 hour ago'
    return `${hours} hours ago`
  }

  // Update time ago display every minute
  const [, setTimeNow] = useState(Date.now())
  useEffect(() => {
    const interval = setInterval(() => {
      setTimeNow(Date.now())
    }, 60000) // Update every minute
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (!mapRef.current || mapInstanceRef.current) return

    // Greater London bounding box (approximate)
    const londonBounds = L.latLngBounds(
      L.latLng(51.28676, -0.510375),
      L.latLng(51.691874, 0.334015)
    )
    // Slightly expand bounds beyond London for a more permissive edge
    const boundsForLimits = londonBounds.pad(0.1)

    const map = L.map(mapRef.current, {
      center: [51.5074, -0.1278],
      zoom: 11,
      zoomControl: true,
      // Prevent panning outside of (slightly expanded) London bounds
      maxBounds: boundsForLimits,
      maxBoundsViscosity: 1.0,
    })

    map.zoomControl.setPosition('bottomleft')

    L.tileLayer('https://tile.thunderforest.com/atlas/{z}/{x}/{y}.png?apikey=6a53e8b25d114a5e9216df5bf9b5e9c8', {
      attribution: '',
      maxZoom: 22,
    }).addTo(map)

    // Limit zooming out to at most show all of London on screen
    const computeAndApplyMinZoom = () => {
      const minZ = map.getBoundsZoom(boundsForLimits, true, L.point(16, 16))
      map.setMinZoom(minZ)
      if (map.getZoom() < minZ) map.setZoom(minZ)
    }
    computeAndApplyMinZoom()
    map.on('resize', computeAndApplyMinZoom)


    mapInstanceRef.current = map

    return () => {
      map.off('resize', computeAndApplyMinZoom)
      map.remove()
      mapInstanceRef.current = null
    }
  }, [])

  // debounced suggestions search
  useEffect(() => {
    const query = activeField === 'from' ? fromValue : activeField === 'to' ? toValue : ''
    if (debounceTimerRef.current) {
      window.clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }
    if (!activeField || !query.trim() || query.trim().length < 2) {
      setSuggestions([])
      return
    }
    debounceTimerRef.current = window.setTimeout(async () => {
      const q = query.trim()
      try {
        const res = await fetch(`http://localhost:4225/api/search?q=${encodeURIComponent(q)}`)
        if (res.ok) {
          const results = await res.json()
          setSuggestions(results.slice(0, 8))
        }
      } catch (err) {
        console.error('Search failed', err)
      }
    }, 250)
    return () => {
      if (debounceTimerRef.current) {
        window.clearTimeout(debounceTimerRef.current)
        debounceTimerRef.current = null
      }
    }
  }, [fromValue, toValue, activeField])

  const handleSelectStop = (which: 'from' | 'to', stop: Stop) => {
    if (which === 'from') {
      setFromValue(stop.name)
      setFromStop(stop)
    } else {
      setToValue(stop.name)
      setToStop(stop)
    }
    setActiveField(null)
    setSuggestions([])
  }

  return (
    <div className="app-root">
      <div className="route-planner">
        <form className="route-form" onSubmit={handleSubmit}>
          <input
            type="text"
            placeholder="From stop/station"
            value={fromValue}
            onChange={(e) => setFromValue(e.target.value)}
            onFocus={() => setActiveField('from')}
            aria-label="From"
          />
          <span className="arrow">→</span>
          <input
            type="text"
            placeholder="To stop/station"
            value={toValue}
            onChange={(e) => setToValue(e.target.value)}
            onFocus={() => setActiveField('to')}
            aria-label="To"
          />
          <button type="submit">
                                {isLoading ? (
                      <svg className="spinner" width="24" height="24" viewBox="0 0 24 24">
                        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" strokeDasharray="31.4 31.4" strokeLinecap="round">
                          <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="1s" repeatCount="indefinite"/>
                        </circle>
                      </svg>
                    ) : (
                      'GO'
                    )}
          </button>
        </form>
        {activeField && suggestions.length > 0 && (
          <div className="suggestions" role="listbox" aria-label="Stop suggestions">
            {suggestions.map((s: any) => (
              <button
                key={s.id}
                type="button"
                className="suggestion-item"
                onClick={() => handleSelectStop(activeField, s)}
              >
                <div className="suggestion-main">
                  <span className="suggestion-name">{s.name}</span>
                  {s.lines && s.lines.length > 0 && (
                    <div className="suggestion-lines">
                      {s.lines.map((line: any, idx: number) => (
                        <span 
                          key={idx} 
                          className="line-badge"
                          style={{ backgroundColor: line.color }}
                        >
                          {line.name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
        {routeInfo && (
          <div className="route-info">
            <div className="journey-summary-top">
              <div className="summary-header">
                <div className="summary-title">Journey Details</div>
                <div className="summary-refresh">
                  {lastRefreshed && !isLoading && (
                    <span className="last-updated">{getTimeAgo(lastRefreshed)}</span>
                  )}
                  <button 
                    className="refresh-button" 
                    onClick={() => fetchRoute()}
                    disabled={isLoading}
                    title="Refresh route"
                  >
                    {isLoading ? (
                      <svg className="spinner" width="16" height="16" viewBox="0 0 24 24">
                        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" strokeDasharray="31.4 31.4" strokeLinecap="round">
                          <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="1s" repeatCount="indefinite"/>
                        </circle>
                      </svg>
                    ) : (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0118.8-4.3M22 12.5a10 10 0 01-18.8 4.2"/>
                      </svg>
                    )}
                  </button>
                </div>
              </div>
              <div className="summary-row">
                <span className="summary-label">Total journey time:</span>
                <span className="summary-value">{Math.max(1, routeInfo.journey_minutes)} min{routeInfo.journey_minutes !== 1 ? 's' : ''}</span>
              </div>
              <div className="summary-row">
                <span className="summary-label">Arrival:</span>
                <span className="summary-value">{new Date(routeInfo.arrival_time * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</span>
              </div>
            </div>
            
            <div className="route-segments">
              {routeInfo.segments.map((seg: any, idx: number) => {
                const isExpanded = expandedSegments.has(idx)
                const isStopsExpanded = expandedStops.has(idx)
                const duration = Math.max(1, Math.floor(seg.duration / 60)) // Never show 0
                
                const toggleExpanded = () => {
                  const newExpanded = new Set(expandedSegments)
                  if (isExpanded) {
                    newExpanded.delete(idx)
                  } else {
                    newExpanded.add(idx)
                  }
                  setExpandedSegments(newExpanded)
                }
                
                const toggleStops = () => {
                  const newExpanded = new Set(expandedStops)
                  if (isStopsExpanded) {
                    newExpanded.delete(idx)
                  } else {
                    newExpanded.add(idx)
                  }
                  setExpandedStops(newExpanded)
                }
                
                const formatTime = (timestamp: number) => {
                  const date = new Date(timestamp * 1000)
                  return date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
                }
                
                return (
                  <div key={idx} className="route-segment">
                    {seg.type === 'walk' ? (
                      <div className="segment-container">
                        <button 
                          className="segment-header segment-walk-header"
                          onClick={toggleExpanded}
                        >
                          <div className="segment-main">
                            <div className="segment-title">
                              <svg className="segment-icon" width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M13.5 5.5c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zM9.8 8.9L7 23h2.1l1.8-8 2.1 2v6h2v-7.5l-2.1-2 .6-3C14.8 12 16.8 13 19 13v-2c-1.9 0-3.5-1-4.3-2.4l-1-1.6c-.4-.6-1-1-1.7-1-.3 0-.5.1-.8.1L6 8.3V13h2V9.6l1.8-.7"/>
                              </svg>
                              Walk
                            </div>
                            <div className="segment-duration">{duration} <span className="duration-unit">min{duration !== 1 ? 's' : ''}</span></div>
                          </div>
                          <div className="expand-icon">{isExpanded ? '▼' : '▶'}</div>
                        </button>
                        {isExpanded && (
                          <div className="segment-details">
                            <div className="segment-route">
                              <div className="stop-list">
                                <div className="timeline-stops">
                                    <div className="timeline-line" style={{ backgroundColor: '#111827' }}></div>
                                    <div className="timeline-item">
                                      <div className="timeline-dot" style={{ backgroundColor: '#111827' }}></div>
                                      <div className="timeline-content">
                                        <span className="stop-time">{formatTime(seg.start_time)}</span>
                                        <span className="stop-name">{seg.from}</span>
                                      </div>
                                    </div>
                                    <div className="timeline-item">
                                      <div className="timeline-dot" style={{ backgroundColor: '#111827' }}></div>
                                      <div className="timeline-content">
                                          <span className="stop-time">{formatTime(seg.end_time)}</span>
                                          <span className="stop-name">{seg.to}</span>
                                        </div>
                                    </div>
                                </div>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="segment-container">
                        <button 
                          className="segment-header segment-bus-header"
                          style={{ backgroundColor: seg.line_color || '#ef4444' }}
                          onClick={toggleExpanded}
                        >
                          <div className="segment-main">
                            <div className="segment-title">
                              {(() => {
                                const mode = String(seg.mode || '').toLowerCase();
                                const isRail = mode === 'rail' || seg.rail_line;
                                const isTube = mode === 'tube' || seg.tube_line;
                                console.log('Segment mode:', seg.mode, 'isRail:', isRail, 'isTube:', isTube, 'rail_line:', seg.rail_line, 'tube_line:', seg.tube_line);

                                if (isTube) {
                                  return (
                                    <>
                                      <img src="/underground.png" alt="Underground" width="24" height="24" />
                                      Take {seg.tube_line || 'tube'}
                                    </>
                                  );
                                } else if (isRail) {
                                  const rail_line_parts = seg.rail_line.split("/")
                                  return (
                                    <>
                                      <img src="/nationalrail.png" alt="National Rail" width="32" height="32" />
                                      <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                        <div style={{ fontSize: '16px', fontWeight: '700' }}>{rail_line_parts[1]}</div>
                                        <div style={{ fontSize: '12px', fontWeight: '500', opacity: '0.85' }}>{rail_line_parts[0]}</div>
                                      </div>
                                    </>
                                  );
                                } else {
                                  return (
                                    <>
                                      <svg className="segment-icon" width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                                        <path d="M4 16c0 .88.39 1.67 1 2.22V20c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h8v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4s-8 .5-8 4v10zm3.5 1c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm9 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm1.5-6H6V6h12v5z"/>
                                      </svg>
                                      Take bus {seg.route.toUpperCase()}
                                    </>
                                  );
                                }
                              })()}
                            </div>
                            <div className="segment-duration">{duration} <span className="duration-unit">min{duration !== 1 ? 's' : ''}</span></div>
                          </div>
                          <div className="expand-icon">{isExpanded ? '▼' : '▶'}</div>
                        </button>
                        {isExpanded && (
                          <div className="segment-details">
                            <div className="segment-route">
                              {seg.platform &&seg.platform!='?' && (
                                <div className="segment-platform">Board at platform {seg.platform}</div>
                              )}
                              <div className="stop-list">
                                {seg.stops && seg.stops.length > 0 ? (
                                  <div className="timeline-stops">
                                    <div className="timeline-line" style={{ backgroundColor: seg.line_color || '#ef4444' }}></div>
                                    <div className="timeline-item">
                                      <div className="timeline-dot" style={{ backgroundColor: seg.line_color || '#ef4444' }}></div>
                                      <div className="timeline-content">
                                        <span className="stop-time">{formatTime(seg.stops[0].time)}</span>
                                        <span className="stop-name">{seg.stops[0].name}</span>
                                      </div>
                                    </div>
                                    
                                    {seg.stops.length > 2 && (
                                      <>
                                        <button 
                                          className="intermediate-stops-toggle"
                                          onClick={(e) => { e.stopPropagation(); toggleStops(); }}
                                        >
                                          <span className="stops-count">
                                            {isStopsExpanded ? '▼' : '▶'} {seg.stops.length - 2} stop{seg.stops.length - 2 !== 1 ? 's' : ''}
                                          </span>
                                        </button>
                                        
                                        {isStopsExpanded && (
                                          <>
                                            {seg.stops.slice(1, -1).map((stop: any, stopIdx: number) => (
                                              <div key={stopIdx} className="timeline-item">
                                                <div className="timeline-dot" style={{ backgroundColor: seg.line_color || '#ef4444' }}></div>
                                                <div className="timeline-content">
                                                  <span className="stop-time">{formatTime(stop.time)}</span>
                                                  <span className="stop-name">{stop.name}</span>
                                                </div>
                                              </div>
                                            ))}
                                          </>
                                        )}
                                      </>
                                    )}
                                    
                                    {(() => {
                                      // Check if this is a close transfer to next segment
                                      let isCloseTransfer = false
                                      if (idx < routeInfo.segments.length - 1 && seg.coordinates && seg.coordinates.length > 0) {
                                        // Find next transport segment
                                        for (let nextIdx = idx + 1; nextIdx < routeInfo.segments.length; nextIdx++) {
                                          const nextSeg = routeInfo.segments[nextIdx]
                                          if (nextSeg.type !== 'walk' && nextSeg.coordinates && nextSeg.coordinates.length > 0) {
                                            const endCoord = seg.coordinates[seg.coordinates.length - 1]
                                            const nextStartCoord = nextSeg.coordinates[0]
                                            const dist = calculateDistance([endCoord[0], endCoord[1]], [nextStartCoord[0], nextStartCoord[1]])
                                            isCloseTransfer = dist <= 10
                                            break
                                          }
                                        }
                                      }
                                      
                                      return (
                                        <div className="timeline-item">
                                          <div className={`timeline-dot ${isCloseTransfer ? 'timeline-dot-transfer' : ''}`} style={{ backgroundColor: seg.line_color || '#ef4444' }}></div>
                                          <div className="timeline-content">
                                            <span className="stop-time">{formatTime(seg.stops[seg.stops.length - 1].time)}</span>
                                            <span className="stop-name">{seg.stops[seg.stops.length - 1].name}</span>
                                          </div>
                                        </div>
                                      )
                                    })()}
                                  </div>
                                ) : (
                                  <>
                                    <div className="stop-item start">{seg.from}</div>
                                    <div className="route-line" style={{ backgroundColor: seg.line_color || '#ef4444' }}></div>
                                    <div className="stop-item end">{seg.to}</div>
                                  </>
                                )}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
      <div id="map" ref={mapRef} className="map-container"></div>
    </div>
  )
}

export default App
