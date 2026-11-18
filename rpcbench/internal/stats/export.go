package stats

// DistOf summarizes a set of millisecond values (count, mean, p50/p90/p99,
// max, std) with the same percentile rule as every other distribution in the
// result files. Exported for Experiment C (cmd/ctrledge).
func DistOf(msValues []float64) Dist { return distOf(msValues) }
