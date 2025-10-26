export type Station = {
  id: string
  name: string
  modes: Array<'tube' | 'bus' | 'rail' | 'tram' | 'dlr' | 'elizabeth'>
  lines: string[]
  lat: number
  lng: number
}

export const LINE_COLORS: Record<string, string> = {
  Bakerloo: '#B36305',
  Central: '#E32017',
  Circle: '#FFD300',
  District: '#00782A',
  HammersmithCity: '#F3A9BB',
  Jubilee: '#A0A5A9',
  Metropolitan: '#9B0056',
  Northern: '#000000',
  Piccadilly: '#0019A8',
  Victoria: '#0098D4',
  WaterlooCity: '#95CDBA',
  DLR: '#00A4A7',
  Elizabeth: '#9364CC',
}

export const LINE_ROUNDEL_TEXT: Record<string, string> = {
  Bakerloo: 'B',
  Central: 'C',
  Circle: 'Ci',
  District: 'D',
  HammersmithCity: 'HC',
  Jubilee: 'J',
  Metropolitan: 'M',
  Northern: 'N',
  Piccadilly: 'P',
  Victoria: 'V',
  WaterlooCity: 'W&C',
  DLR: 'DLR',
  Elizabeth: 'EL',
}

export const stations: Station[] = [
  {
    id: 'baker-street',
    name: 'Baker Street',
    modes: ['tube', 'bus'],
    lines: ['Bakerloo', 'Jubilee', 'Metropolitan', 'Circle', 'HammersmithCity'],
    lat: 51.523109,
    lng: -0.156889,
  },
  {
    id: 'oxford-circus',
    name: 'Oxford Circus',
    modes: ['tube', 'bus'],
    lines: ['Bakerloo', 'Central', 'Victoria'],
    lat: 51.515224,
    lng: -0.141903,
  },
  {
    id: 'piccadilly-circus',
    name: 'Piccadilly Circus',
    modes: ['tube', 'bus'],
    lines: ['Bakerloo', 'Piccadilly'],
    lat: 51.510067,
    lng: -0.134696,
  },
  {
    id: 'waterloo',
    name: 'Waterloo',
    modes: ['tube', 'rail', 'bus'],
    lines: ['Bakerloo', 'Jubilee', 'Northern', 'WaterlooCity'],
    lat: 51.503378,
    lng: -0.112732,
  },
  {
    id: 'kings-cross',
    name: 'King\'s Cross St Pancras',
    modes: ['tube', 'rail', 'bus'],
    lines: ['Circle', 'HammersmithCity', 'Metropolitan', 'Northern', 'Piccadilly', 'Victoria'],
    lat: 51.530833,
    lng: -0.123333,
  },
  {
    id: 'paddington',
    name: 'Paddington',
    modes: ['tube', 'rail', 'bus'],
    lines: ['Bakerloo', 'Circle', 'District', 'Elizabeth'],
    lat: 51.515392,
    lng: -0.175569,
  },
  {
    id: 'elephant-castle',
    name: 'Elephant & Castle',
    modes: ['tube', 'bus'],
    lines: ['Bakerloo', 'Northern'],
    lat: 51.4951,
    lng: -0.1007,
  },
  {
    id: 'hammersmith',
    name: 'Hammersmith',
    modes: ['tube', 'bus'],
    lines: ['Piccadilly', 'District', 'HammersmithCity', 'Circle'],
    lat: 51.4922,
    lng: -0.2232,
  },
  {
    id: 'victoria',
    name: 'Victoria',
    modes: ['tube', 'rail', 'bus'],
    lines: ['Victoria', 'Circle', 'District'],
    lat: 51.4965,
    lng: -0.1447,
  },
  {
    id: 'green-park',
    name: 'Green Park',
    modes: ['tube', 'bus'],
    lines: ['Piccadilly', 'Jubilee', 'Victoria'],
    lat: 51.5067,
    lng: -0.1428,
  },
]


