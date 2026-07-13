// Package airports resolves a lat/lon to the nearest airport using an embedded
// snapshot of the public-domain OurAirports dataset (small/medium/large
// airports + seaplane bases; closed fields and heliports excluded). Regenerate
// data/airports.csv.gz from https://davidmegginson.github.io/ourairports-data/
// airports.csv — see the python snippet in the package README section of
// docs/flights.md.
package airports

import (
	"bytes"
	"compress/gzip"
	"embed"
	"encoding/csv"
	"io"
	"math"
	"strconv"
	"sync"
)

//go:embed data/airports.csv.gz
var dataFS embed.FS

// Airport is one entry from the embedded dataset.
type Airport struct {
	Ident string // OurAirports ident: ICAO where one exists (KTKI), else local/regional code
	Name  string
	Lat   float64
	Lon   float64
}

var (
	loadOnce sync.Once
	all      []Airport
	loadErr  error
)

func load() ([]Airport, error) {
	loadOnce.Do(func() {
		raw, err := dataFS.ReadFile("data/airports.csv.gz")
		if err != nil {
			loadErr = err
			return
		}
		zr, err := gzip.NewReader(bytes.NewReader(raw))
		if err != nil {
			loadErr = err
			return
		}
		defer zr.Close()
		r := csv.NewReader(zr)
		r.FieldsPerRecord = 4
		var out []Airport
		for {
			rec, err := r.Read()
			if err == io.EOF {
				break
			}
			if err != nil {
				loadErr = err
				return
			}
			lat, err1 := strconv.ParseFloat(rec[2], 64)
			lon, err2 := strconv.ParseFloat(rec[3], 64)
			if err1 != nil || err2 != nil {
				continue
			}
			out = append(out, Airport{Ident: rec[0], Name: rec[1], Lat: lat, Lon: lon})
		}
		all = out
	})
	return all, loadErr
}

const earthRadiusKm = 6371.0

// distKm is the haversine great-circle distance.
func distKm(lat1, lon1, lat2, lon2 float64) float64 {
	const d = math.Pi / 180
	dLat := (lat2 - lat1) * d
	dLon := (lon2 - lon1) * d
	a := math.Sin(dLat/2)*math.Sin(dLat/2) +
		math.Cos(lat1*d)*math.Cos(lat2*d)*math.Sin(dLon/2)*math.Sin(dLon/2)
	return 2 * earthRadiusKm * math.Asin(math.Sqrt(a))
}

// Nearest returns the closest airport to (lat, lon) within maxKm, or ok=false
// if none qualifies. A linear scan over ~49k entries takes ~1 ms — fine for
// the two lookups per flight the scanner needs.
func Nearest(lat, lon, maxKm float64) (ap Airport, dKm float64, ok bool) {
	list, err := load()
	if err != nil {
		return Airport{}, 0, false
	}
	// Cheap prefilter: 1° latitude ≈ 111 km; skip entries whose latitude alone
	// puts them out of range before paying for haversine.
	latBand := maxKm/111.0 + 0.01
	best := math.MaxFloat64
	for i := range list {
		if math.Abs(list[i].Lat-lat) > latBand {
			continue
		}
		if d := distKm(lat, lon, list[i].Lat, list[i].Lon); d < best {
			best = d
			ap = list[i]
		}
	}
	if best > maxKm {
		return Airport{}, 0, false
	}
	return ap, best, true
}
