package config

import "math"

// GyroTCO is the cabin ICM-45686 piecewise-linear gyro bias vs die temperature
// shape, expressed as Δb(T) = b(T) − b(T_ref) in °/s. At T_ref, Δb = 0.
//
// Six-face Accept programs OFFUSER to cancel bias at T_ref (not T_cal). The UI
// (and later Step-1 compensator) subtracts Δb(T) from the published gyro so
// the boldface line is correct away from T_ref. Flight DB rows stay raw.
type GyroTCO struct {
	TRefC    float64    `json:"t_ref_c"`
	KneesC   []float64  `json:"knees_c"`
	DeltaDPS GyroTCOXYZ `json:"delta_dps"`
}

// GyroTCOXYZ holds per-axis Δb samples aligned with KneesC (°/s).
type GyroTCOXYZ struct {
	X []float64 `json:"x"`
	Y []float64 `json:"y"`
	Z []float64 `json:"z"`
}

// DefaultGyroTCORefC is the standard die temperature for OFFUSER / config bias.
// Summer flights run hotter (~40 °C); 35 °C sits between desktop and flight and
// is easy to change later via config.
const DefaultGyroTCORefC = 35.0

// DefaultGyroTCO returns the soak-fitted Δb table (fridge + freezer + hotbox,
// 2026-07-14/15), zeroed at T_ref = 35 °C. Knees are die temperature (°C);
// delta_dps are °/s. Re-fit notes: docs/analysis/gyro_tco.md.
func DefaultGyroTCO() GyroTCO {
	return GyroTCO{
		TRefC:  DefaultGyroTCORefC,
		KneesC: []float64{-22, -10, 10, 30, 35, 55},
		DeltaDPS: GyroTCOXYZ{
			X: []float64{0.28945, 0.25506, 0.29260, 0.01147, 0, -0.04588},
			Y: []float64{-0.24436, -0.17546, 0.12470, 0.00406, 0, -0.01623},
			Z: []float64{0.10225, 0.07823, 0.01549, -0.01886, 0, 0.07544},
		},
	}
}

// Valid reports whether the table can be interpolated.
func (g GyroTCO) Valid() bool {
	n := len(g.KneesC)
	if n < 2 {
		return false
	}
	if len(g.DeltaDPS.X) != n || len(g.DeltaDPS.Y) != n || len(g.DeltaDPS.Z) != n {
		return false
	}
	for i := 1; i < n; i++ {
		if g.KneesC[i] <= g.KneesC[i-1] {
			return false
		}
	}
	return true
}

// MergeGyroTCODefaults fills an empty or invalid table from DefaultGyroTCO.
// A present but partial table is replaced entirely (shape is one unit).
func MergeGyroTCODefaults(g *GyroTCO) {
	if g == nil {
		return
	}
	if !g.Valid() {
		*g = DefaultGyroTCO()
		return
	}
	if g.TRefC == 0 {
		g.TRefC = DefaultGyroTCORefC
	}
}

// DeltaDPSAt returns Δb(T) in °/s via linear interpolation between knees.
// Outside the knee range, clamps to the end values.
func (g GyroTCO) DeltaDPSAt(tempC float64) [3]float64 {
	if !g.Valid() {
		return [3]float64{}
	}
	return [3]float64{
		interpSorted(g.KneesC, g.DeltaDPS.X, tempC),
		interpSorted(g.KneesC, g.DeltaDPS.Y, tempC),
		interpSorted(g.KneesC, g.DeltaDPS.Z, tempC),
	}
}

// DeltaRadAt is DeltaDPSAt converted to rad/s (hub / OFFUSER units).
func (g GyroTCO) DeltaRadAt(tempC float64) [3]float64 {
	d := g.DeltaDPSAt(tempC)
	s := math.Pi / 180
	return [3]float64{d[0] * s, d[1] * s, d[2] * s}
}

func interpSorted(xs, ys []float64, x float64) float64 {
	if x <= xs[0] {
		return ys[0]
	}
	n := len(xs)
	if x >= xs[n-1] {
		return ys[n-1]
	}
	// xs is strictly increasing (Valid).
	i := 1
	for i < n && xs[i] < x {
		i++
	}
	x0, x1 := xs[i-1], xs[i]
	y0, y1 := ys[i-1], ys[i]
	w := (x - x0) / (x1 - x0)
	return y0 + w*(y1-y0)
}
