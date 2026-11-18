// Package sensor models the Terminal tier of the architecture.
//
// In the full picture the Edge Gateway receives raw readings over MQTT/CoAP and
// normalizes them into canonical units before forwarding them to the Cloud
// Gateway. The Edge<->Cloud hop is what this benchmark measures, so the
// Terminal tier is replaced by a deterministic in-process generator: it removes
// broker and CoAP-server variance from the measurement without changing the
// shape, size or cardinality of the data that crosses the link under test.
//
// Each sensor Kind becomes one logical stream. Splitting by kind is the point
// of the experiment: kinds are independent workloads, so a loss event affecting
// one of them must not, in principle, stall the others.
package sensor

import (
	"fmt"
	"math"
	"math/rand"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

// Kind describes one sensor type and the shape of the signal it emits.
type Kind struct {
	Name   string
	Unit   string
	Base   float64 // mean value after normalization
	Amp    float64 // diurnal amplitude
	Noise  float64 // per-sample gaussian sigma
	Period time.Duration
}

// Kinds is the default catalogue of eight heterogeneous sensor types. The list
// is ordered; --streams N takes the first N entries, cycling with a numeric
// suffix if N exceeds len(Kinds).
var Kinds = []Kind{
	{Name: "temperature", Unit: "degC", Base: 21.0, Amp: 6.0, Noise: 0.20, Period: 24 * time.Hour},
	{Name: "humidity", Unit: "percent", Base: 55.0, Amp: 15.0, Noise: 0.80, Period: 24 * time.Hour},
	{Name: "pressure", Unit: "hPa", Base: 1013.0, Amp: 4.0, Noise: 0.15, Period: 36 * time.Hour},
	{Name: "wind_speed", Unit: "m_s", Base: 3.5, Amp: 2.5, Noise: 0.60, Period: 6 * time.Hour},
	{Name: "rainfall", Unit: "mm_h", Base: 0.4, Amp: 0.4, Noise: 0.25, Period: 12 * time.Hour},
	{Name: "co2", Unit: "ppm", Base: 620.0, Amp: 180.0, Noise: 8.00, Period: 24 * time.Hour},
	{Name: "pm25", Unit: "ug_m3", Base: 24.0, Amp: 12.0, Noise: 2.00, Period: 24 * time.Hour},
	{Name: "illuminance", Unit: "lux", Base: 9000.0, Amp: 8500.0, Noise: 120.0, Period: 24 * time.Hour},
}

// StreamNames returns the names of the first n streams, cycling the catalogue
// with a numeric suffix when n exceeds the catalogue size.
func StreamNames(n int) []string {
	out := make([]string, 0, n)
	for i := 0; i < n; i++ {
		k := Kinds[i%len(Kinds)]
		if i < len(Kinds) {
			out = append(out, k.Name)
		} else {
			out = append(out, fmt.Sprintf("%s_%d", k.Name, i/len(Kinds)))
		}
	}
	return out
}

// Generator produces normalized measurements for exactly one stream.
type Generator struct {
	kind    Kind
	name    string
	rng     *rand.Rand
	seq     uint64
	padding []byte
	origin  time.Time
}

// NewGenerator builds a generator for stream index i with a per-stream padding
// size. The seed is derived from the stream index so a run is reproducible.
func NewGenerator(i int, name string, padBytes int, seed int64) *Generator {
	k := Kinds[i%len(Kinds)]
	g := &Generator{
		kind:   k,
		name:   name,
		rng:    rand.New(rand.NewSource(seed + int64(i)*7919)),
		origin: time.Now(),
	}
	if padBytes > 0 {
		g.padding = make([]byte, padBytes)
		for j := range g.padding {
			// Non-constant bytes so that any accidental compression on the path
			// cannot shrink the payload and distort the byte accounting.
			g.padding[j] = byte(g.rng.Intn(256))
		}
	}
	return g
}

// Name is the logical stream key.
func (g *Generator) Name() string { return g.name }

// Next fills dst with n freshly generated measurements and returns the slice.
// Reusing dst keeps the allocation profile of the client flat, so the latency
// distribution reflects the network rather than the garbage collector.
func (g *Generator) Next(dst []wire.Measurement, n int) []wire.Measurement {
	if cap(dst) < n {
		dst = make([]wire.Measurement, n)
	}
	dst = dst[:n]
	now := time.Now()
	phase := now.Sub(g.origin).Seconds() / g.kind.Period.Seconds() * 2 * math.Pi
	for i := 0; i < n; i++ {
		g.seq++
		v := g.kind.Base + g.kind.Amp*math.Sin(phase) + g.rng.NormFloat64()*g.kind.Noise
		if g.kind.Name == "rainfall" && v < 0 {
			v = 0 // physically bounded below
		}
		dst[i] = wire.Measurement{
			SensorID: fmt.Sprintf("%s-%04d", g.kind.Name, (g.seq%64)+1),
			Kind:     g.name,
			Value:    v,
			Unit:     g.kind.Unit,
			TsUnixNs: now.UnixNano(),
			Seq:      g.seq,
			Payload:  g.padding,
		}
	}
	return dst
}
