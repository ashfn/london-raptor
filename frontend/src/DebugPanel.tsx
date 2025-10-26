import { useState, useEffect } from 'react'
import L from 'leaflet'

interface DebugPanelProps {
  map: L.Map | null
}

interface Stop {
  id: string
  name: string
  lat: number
  lon: number
}

export function DebugPanel({ map }: DebugPanelProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [lines, setLines] = useState<string[]>([])
  const [stops, setStops] = useState<Stop[]>([])
  const [selectedLine, setSelectedLine] = useState('')
  const [selectedOrigin, setSelectedOrigin] = useState('')
  const [selectedDest, setSelectedDest] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingStops, setLoadingStops] = useState(false)
  const [debugLayer, setDebugLayer] = useState<L.Polyline | null>(null)
  const [error, setError] = useState('')

  // Fetch available lines
  useEffect(() => {
    fetch('http://localhost:4225/lines')
      .then(res => res.json())
      .then(data => {
        setLines(data.lines)
        console.log(`Loaded ${data.lines.length} bus lines`)
      })
      .catch(err => {
        console.error('Failed to fetch lines:', err)
        setError('Failed to fetch lines. Is API running?')
      })
  }, [])

  // Fetch stops for selected line
  useEffect(() => {
    if (!selectedLine) {
      setStops([])
      setSelectedOrigin('')
      setSelectedDest('')
      return
    }

    setLoadingStops(true)
    setError('')
    
    fetch(`http://localhost:4225/stops?line=${selectedLine}`)
      .then(res => res.json())
      .then(data => {
        setStops(data.stops)
        console.log(`Loaded ${data.stops.length} stops for line ${selectedLine}`)
        setLoadingStops(false)
      })
      .catch(err => {
        console.error('Failed to fetch stops:', err)
        setError('Failed to fetch stops')
        setLoadingStops(false)
      })
  }, [selectedLine])

  const handleDraw = async () => {
    if (!map || !selectedLine || !selectedOrigin || !selectedDest) {
      setError('Please select a line, origin, and destination')
      return
    }

    setLoading(true)
    setError('')
    
    try {
      const res = await fetch(
        `http://localhost:4225/linestring?line=${selectedLine}&start=${selectedOrigin}&end=${selectedDest}`
      )
      
      if (!res.ok) {
        const errorData = await res.json()
        setError(errorData.error || 'Failed to fetch linestring')
        setLoading(false)
        return
      }
      
      const data = await res.json()
      
      // Remove previous debug layer
      if (debugLayer) {
        map.removeLayer(debugLayer)
      }
      
      // Parse linestring
      const linestring = JSON.parse(data.linestring)
      const coords = linestring[0][0].map((coord: number[]) => [coord[1], coord[0]] as [number, number])
      
      console.log(`Drawing route with ${coords.length} points`)
      
      // Draw on map
      const line = L.polyline(coords, {
        color: '#ef4444',
        weight: 8,
        opacity: 0.8,
        lineCap: 'round',
        lineJoin: 'round',
        smoothFactor: 3.0,
      }).addTo(map)
      
      setDebugLayer(line)
      
      // Fit bounds
      map.fitBounds(line.getBounds(), { padding: [50, 50] })
      
      console.log('Route drawn successfully')
    } catch (err) {
      console.error('Failed to draw linestring:', err)
      setError('Failed to draw linestring. Check console for details.')
    } finally {
      setLoading(false)
    }
  }

  const handleClear = () => {
    if (debugLayer && map) {
      map.removeLayer(debugLayer)
      setDebugLayer(null)
    }
  }

  return (
    <div style={{
      position: 'absolute',
      top: '1rem',
      right: '1rem',
      zIndex: 1000,
      background: 'white',
      borderRadius: '0.5rem',
      boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
      minWidth: isOpen ? '320px' : 'auto',
      maxWidth: isOpen ? '320px' : 'auto',
    }}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        style={{
          width: '100%',
          padding: '0.75rem 1rem',
          background: '#3b82f6',
          color: 'white',
          border: 'none',
          borderRadius: '0.5rem',
          cursor: 'pointer',
          fontWeight: 'bold',
          fontSize: '0.875rem',
        }}
      >
        🐛 Debug Linestrings {isOpen ? '▼' : '▶'}
      </button>
      
      {isOpen && (
        <div style={{
          padding: '1rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.75rem',
        }}>
          {error && (
            <div style={{
              padding: '0.5rem',
              background: '#fee2e2',
              color: '#991b1b',
              borderRadius: '0.375rem',
              fontSize: '0.75rem',
            }}>
              {error}
            </div>
          )}
          
          <div>
            <label style={{
              display: 'block',
              fontSize: '0.75rem',
              fontWeight: 'bold',
              marginBottom: '0.25rem',
              color: '#374151'
            }}>
              Line
            </label>
            <select
              value={selectedLine}
              onChange={(e) => setSelectedLine(e.target.value)}
              style={{
                width: '100%',
                padding: '0.5rem',
                border: '1px solid #d1d5db',
                borderRadius: '0.375rem',
                fontSize: '0.875rem',
              }}
            >
              <option value="">Select line...</option>
              {lines.map(line => (
                <option key={line} value={line}>{line}</option>
              ))}
            </select>
          </div>
          
          {loadingStops && (
            <div style={{
              textAlign: 'center',
              fontSize: '0.75rem',
              color: '#6b7280',
            }}>
              Loading stops for line {selectedLine}...
            </div>
          )}
          
          {selectedLine && !loadingStops && (
            <>
              <div>
                <label style={{
                  display: 'block',
                  fontSize: '0.75rem',
                  fontWeight: 'bold',
                  marginBottom: '0.25rem',
                  color: '#374151'
                }}>
                  Origin Stop
                </label>
                <select
                  value={selectedOrigin}
                  onChange={(e) => setSelectedOrigin(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '0.5rem',
                    border: '1px solid #d1d5db',
                    borderRadius: '0.375rem',
                    fontSize: '0.875rem',
                  }}
                >
                  <option value="">Select origin...</option>
                  {stops.map(stop => (
                    <option key={stop.id} value={stop.id}>
                      {stop.name}
                    </option>
                  ))}
                </select>
              </div>
              
              <div>
                <label style={{
                  display: 'block',
                  fontSize: '0.75rem',
                  fontWeight: 'bold',
                  marginBottom: '0.25rem',
                  color: '#374151'
                }}>
                  Destination Stop
                </label>
                <select
                  value={selectedDest}
                  onChange={(e) => setSelectedDest(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '0.5rem',
                    border: '1px solid #d1d5db',
                    borderRadius: '0.375rem',
                    fontSize: '0.875rem',
                  }}
                >
                  <option value="">Select destination...</option>
                  {stops.map(stop => (
                    <option key={stop.id} value={stop.id}>
                      {stop.name}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}
          
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              onClick={handleDraw}
              disabled={loading || !selectedLine || !selectedOrigin || !selectedDest}
              style={{
                flex: 1,
                padding: '0.5rem',
                background: loading ? '#9ca3af' : '#10b981',
                color: 'white',
                border: 'none',
                borderRadius: '0.375rem',
                cursor: (loading || !selectedLine || !selectedOrigin || !selectedDest) ? 'not-allowed' : 'pointer',
                fontSize: '0.875rem',
                fontWeight: 'bold',
                opacity: (loading || !selectedLine || !selectedOrigin || !selectedDest) ? 0.5 : 1,
              }}
            >
              {loading ? 'Loading...' : 'Draw'}
            </button>
            
            <button
              onClick={handleClear}
              disabled={!debugLayer}
              style={{
                padding: '0.5rem 1rem',
                background: '#ef4444',
                color: 'white',
                border: 'none',
                borderRadius: '0.375rem',
                cursor: debugLayer ? 'pointer' : 'not-allowed',
                fontSize: '0.875rem',
                opacity: debugLayer ? 1 : 0.5,
              }}
            >
              Clear
            </button>
          </div>
          
          <div style={{
            fontSize: '0.75rem',
            color: '#6b7280',
            marginTop: '0.5rem',
            borderTop: '1px solid #e5e7eb',
            paddingTop: '0.5rem',
          }}>
            {lines.length} lines available
            {selectedLine && stops.length > 0 && (
              <div>{stops.length} stops on line {selectedLine}</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
