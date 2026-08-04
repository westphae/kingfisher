package ups

// Time-to-empty modelling.
//
// The obvious estimator — extrapolate the average %/s since power loss — is
// badly optimistic, because this pack's reported SOC is not linear in time.
// Measured on the 2026-08-04 reference discharge (6700 mAh, 2x 18650, normal
// recorder load): the first half averaged 3.14 min per 1% and the second half
// 1.35 min per 1%, so the last 50% of indicated charge went 2.3x faster than
// the first. A linear fit therefore promised 140 min at the 50% mark when only
// 59 remained (2.37x over) — worst exactly where a pilot would act on it.
//
// Two simpler fixes were measured against that discharge and both fail:
//
//	                 at 80%   at 50%   at 20%
//	anchored average  1.95x    2.37x    2.28x   (what this replaced)
//	10-min SOC slope  2.84x    1.55x    1.11x   (worse than anchored up high)
//	10-min V slope    2.66x    1.02x    1.33x   (good late, useless early)
//
// A windowed slope only measures the rate *now*; it cannot know the rate is
// about to increase, and near the top of the voltage plateau it is worse than
// the anchored average. Voltage extrapolation is genuinely good below ~60%,
// once past the plateau, but hopeless above it.
//
// What is stable is the *shape* of the curve. So: keep a normalised profile of
// "fraction of total runtime still remaining vs SOC", and calibrate its scale
// at runtime from the elapsed time actually observed this discharge. The shape
// handles the non-linearity; the self-calibration absorbs a heavier or lighter
// load, so only the shape is assumed fixed, not the duration.
//
// LIMITS, because this matters for an instrument: the profile is a ONE-POINT
// calibration from a single discharge at room temperature under one workload.
// It should hold for this pack under a similar load and degrade gracefully
// otherwise, but it is not a battery model. Re-derive it (replay an
// `ups`-device discharge and recompute the knots) if the pack is replaced, if
// cell chemistry or count changes, or if the steady-state load moves a lot.
// Against its own reference discharge it lands within 1% at every decade,
// which is in-sample and should be read as "the arithmetic is right", not as
// a validated accuracy figure.

// socRuntimeProfile maps SOC percent to the fraction of a full discharge's
// runtime still remaining, descending by SOC. The zero point is the SOC at
// which the driver+UPower actually powered the machine off (5.83% indicated),
// so the estimate is time-until-poweroff, not time-until-flat.
var socRuntimeProfile = []struct{ socPct, frac float64 }{
	{94.00, 0.9981},
	{80.00, 0.7327},
	{70.00, 0.5514},
	{60.00, 0.4161},
	{50.00, 0.2976},
	{40.00, 0.2121},
	{30.00, 0.1417},
	{20.00, 0.0803},
	{10.00, 0.0243},
	{5.83, 0.0000},
}

// runtimeFrac interpolates the profile. Above the top knot it clamps to that
// knot's value rather than extrapolating: we have no data for a fuller pack,
// and a linear run-out there would invent runtime that may not exist.
func runtimeFrac(socPct float64) float64 {
	if socPct >= socRuntimeProfile[0].socPct {
		return socRuntimeProfile[0].frac
	}
	last := len(socRuntimeProfile) - 1
	if socPct <= socRuntimeProfile[last].socPct {
		return 0
	}
	for i := 0; i < last; i++ {
		hi, lo := socRuntimeProfile[i], socRuntimeProfile[i+1]
		if socPct <= hi.socPct && socPct >= lo.socPct {
			span := hi.socPct - lo.socPct
			if span <= 0 {
				return lo.frac
			}
			return lo.frac + (hi.frac-lo.frac)*(socPct-lo.socPct)/span
		}
	}
	return 0
}
