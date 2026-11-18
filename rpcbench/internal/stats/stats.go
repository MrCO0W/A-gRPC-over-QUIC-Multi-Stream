// Package stats collects per-stream timing samples and derives the metrics the
// experiment reports.
//
// Two families of metrics matter here:
//
//  1. Conventional throughput/latency: goodput, messages per second, and the
//     latency distribution of a batch round trip.
//
//  2. Head-of-line blocking evidence. Under loss, a transport that multiplexes
//     N logical streams onto one ordered byte stream (gRPC over HTTP/2 over TCP)
//     stalls *all* N streams whenever any segment is lost, because TCP will not
//     deliver later bytes until the gap is retransmitted. QUIC recovers each
//     stream independently, so only the affected stream should stall. The
//     stall-correlation metrics below make that difference measurable rather
//     than anecdotal.
//
// Mininet hosts share the host kernel's clock, so client and server timestamps
// are directly comparable. That lets us report true one-way delay in addition
// to the application-level round trip — something that is normally impossible
// without clock synchronisation.
package stats

import (
	"math"
	"sort"
	"sync"
	"time"
)

// Sample is one completed batch round trip on one stream.
type Sample struct {
	SentAt    time.Time // client, just before the first byte of the batch
	AckedAt   time.Time // client, just after the ack was decoded
	ServerAt  time.Time // server-side arrival timestamp carried in the Ack
	WireBytes int64     // framed request bytes put on the wire
	Items     int       // measurements in the batch
}

// StreamRecorder accumulates samples for a single logical stream.
type StreamRecorder struct {
	Name string

	mu       sync.Mutex
	samples  []Sample
	sendErr  int
	recvErr  int
	firstErr string
}

// NewStreamRecorder returns a recorder for the named stream.
func NewStreamRecorder(name string) *StreamRecorder {
	return &StreamRecorder{Name: name, samples: make([]Sample, 0, 4096)}
}

// Add records one completed round trip.
func (s *StreamRecorder) Add(sm Sample) {
	s.mu.Lock()
	s.samples = append(s.samples, sm)
	s.mu.Unlock()
}

// AddSendError records a failed send. The first error message is kept so that
// a run which produced no data explains itself in the result file instead of
// only in the scrollback.
func (s *StreamRecorder) AddSendError(err error) {
	s.mu.Lock()
	s.sendErr++
	s.noteErr("send", err)
	s.mu.Unlock()
}

// AddRecvError records a failed receive.
func (s *StreamRecorder) AddRecvError(err error) {
	s.mu.Lock()
	s.recvErr++
	s.noteErr("recv", err)
	s.mu.Unlock()
}

// noteErr must be called with the mutex held.
func (s *StreamRecorder) noteErr(kind string, err error) {
	if s.firstErr == "" && err != nil {
		s.firstErr = kind + ": " + err.Error()
	}
}

// ---------------------------------------------------------------------------
// Report structures (serialized to JSON)
// ---------------------------------------------------------------------------

// Dist is a latency distribution in milliseconds.
type Dist struct {
	Count int     `json:"count"`
	Mean  float64 `json:"mean_ms"`
	P50   float64 `json:"p50_ms"`
	P90   float64 `json:"p90_ms"`
	P99   float64 `json:"p99_ms"`
	Max   float64 `json:"max_ms"`
	Std   float64 `json:"std_ms"`
}

// StreamReport is the per-stream section of the result file.
type StreamReport struct {
	Name         string  `json:"name"`
	Batches      int     `json:"batches"`
	Messages     int     `json:"messages"`
	WireBytes    int64   `json:"wire_bytes"`
	RTT          Dist    `json:"rtt"`
	OneWay       Dist    `json:"one_way"`
	MaxStallMs   float64 `json:"max_stall_ms"`
	P99StallMs   float64 `json:"p99_stall_ms"`
	SendErrors   int     `json:"send_errors"`
	RecvErrors   int     `json:"recv_errors"`
	FirstError   string  `json:"first_error,omitempty"`
	GoodputMbps  float64 `json:"goodput_mbps"`
	MessagesPerS float64 `json:"messages_per_s"`
}

// HoL holds the head-of-line blocking evidence.
type HoL struct {
	BucketMs int `json:"bucket_ms"`
	// Buckets in which at least one stream made no progress.
	StallBuckets int `json:"stall_buckets"`
	// Of those, the buckets in which EVERY stream made no progress. A transport
	// with a shared ordered byte stream drives this ratio towards 1.0; a
	// transport with independent streams keeps it near 1/N.
	AllStalledBuckets int     `json:"all_stalled_buckets"`
	StallCorrelation  float64 `json:"stall_correlation"`
	// Mean number of simultaneously stalled streams, counted only over buckets
	// where at least one stream stalled. Reported alongside the stream count so
	// the reader can see how far it is from 1 (perfect isolation).
	MeanStalledStreams float64 `json:"mean_stalled_streams"`
	Streams            int     `json:"streams"`
	// Spread between the slowest and the fastest stream's p99 latency. Shared
	// blocking compresses this towards zero (everyone suffers equally); stream
	// isolation widens it (only the unlucky stream suffers).
	P99SpreadMs float64 `json:"p99_spread_ms"`
	// Per-stream completions per bucket, for the progress-over-time figure.
	Timeline map[string][]int `json:"timeline"`
}

// Totals is the run-level aggregate.
type Totals struct {
	Batches      int     `json:"batches"`
	Messages     int     `json:"messages"`
	WireBytes    int64   `json:"wire_bytes"`
	DurationS    float64 `json:"duration_s"`
	GoodputMbps  float64 `json:"goodput_mbps"`
	MessagesPerS float64 `json:"messages_per_s"`
	SendErrors   int     `json:"send_errors"`
	RecvErrors   int     `json:"recv_errors"`
	FirstError   string  `json:"first_error,omitempty"`
}

// StreamAdd records what one logical stream cost to bring online, measured
// from the moment the application asked for it.
//
// This is the metric the "grow streams on one connection" design argument turns
// on, and it is the one thing a steady-state run cannot show. On a single
// connection (HTTP/2 or HTTP/3) opening a stream is a frame with a new stream
// ID - no round trip - so AddLatencyMs is one RTT: the first batch and its ack.
// On a per-stream-connection arm the transport must complete a fresh handshake
// first: TCP's three-way plus TLS 1.3 (about 3 RTT), or QUIC's 1-RTT handshake
// (about 2 RTT). Under loss the tail matters more than the mean, because the
// INITIAL retransmission timeout is one second, not the 200 ms RTO_MIN that
// applies once an RTT sample exists (RFC 6298). A lost SYN therefore costs a
// full second, and a design that never opens a second connection cannot be hit
// by it at all.
type StreamAdd struct {
	Name  string `json:"name"`
	Index int    `json:"index"`
	// Milliseconds from the start of the run.
	RequestedMs float64 `json:"requested_ms"`
	OpenedMs    float64 `json:"opened_ms"`
	FirstAckMs  float64 `json:"first_ack_ms"`
	// OpenedMs - RequestedMs: what the transport's Open() call itself took,
	// i.e. the handshake when there is one.
	OpenLatencyMs float64 `json:"open_latency_ms"`
	// FirstAckMs - RequestedMs: the number the design argument turns on - how
	// long until this new data type is actually flowing end to end.
	AddLatencyMs float64 `json:"add_latency_ms"`
	// Acknowledgements completed by the streams that were ALREADY running, in
	// the window before and after this one came online. A large drop means
	// bringing a stream up disturbed the streams already in service.
	PeerAcksBefore int `json:"peer_acks_before"`
	PeerAcksAfter  int `json:"peer_acks_after"`
}

// RampReport is present only for runs that brought streams online one at a
// time (-ramp-interval). Throughput totals from a ramp run are NOT comparable
// with a steady-state run, because the later streams were live for less of the
// window; use a ramp run for AddLatency and the disturbance counters, and a
// steady-state run for goodput.
type RampReport struct {
	IntervalMs float64     `json:"interval_ms"`
	WindowMs   float64     `json:"disturbance_window_ms"`
	Streams    []StreamAdd `json:"streams"`
	// Over every stream except the first, which is opened before the clock
	// starts and therefore has no add cost to measure.
	AddLatency  Dist `json:"add_latency"`
	OpenLatency Dist `json:"open_latency"`
}

// BuildRamp turns per-stream add records into the ramp section.
func BuildRamp(intervalMs, windowMs float64, adds []StreamAdd) *RampReport {
	addMs := make([]float64, 0, len(adds))
	openMs := make([]float64, 0, len(adds))
	for _, a := range adds {
		if a.Index == 0 {
			continue // opened before the clock started
		}
		if a.AddLatencyMs > 0 {
			addMs = append(addMs, a.AddLatencyMs)
		}
		if a.OpenLatencyMs >= 0 {
			openMs = append(openMs, a.OpenLatencyMs)
		}
	}
	return &RampReport{
		IntervalMs:  intervalMs,
		WindowMs:    windowMs,
		Streams:     adds,
		AddLatency:  distOf(addMs),
		OpenLatency: distOf(openMs),
	}
}

// Report is the complete result document written by the Edge Gateway.
type Report struct {
	Schema    string            `json:"schema"`
	Mode      string            `json:"mode"`
	Label     string            `json:"label"`
	Params    map[string]any    `json:"params"`
	StartedAt string            `json:"started_at"`
	Totals    Totals            `json:"totals"`
	RTT       Dist              `json:"rtt"`
	OneWay    Dist              `json:"one_way"`
	HoL       HoL               `json:"hol"`
	Streams   []StreamReport    `json:"streams"`
	Ramp      *RampReport       `json:"ramp,omitempty"`
	Notes     map[string]string `json:"notes,omitempty"`
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

// Collector aggregates every stream recorder for one run.
type Collector struct {
	recorders []*StreamRecorder
	bucket    time.Duration
}

// NewCollector returns a Collector with the given timeline bucket width.
func NewCollector(bucket time.Duration) *Collector {
	if bucket <= 0 {
		bucket = 100 * time.Millisecond
	}
	return &Collector{bucket: bucket}
}

// Stream registers and returns a recorder for the named stream.
func (c *Collector) Stream(name string) *StreamRecorder {
	r := NewStreamRecorder(name)
	c.recorders = append(c.recorders, r)
	return r
}

func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	if len(sorted) == 1 {
		return sorted[0]
	}
	// Linear interpolation between closest ranks.
	pos := p * float64(len(sorted)-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	if lo == hi {
		return sorted[lo]
	}
	frac := pos - float64(lo)
	return sorted[lo]*(1-frac) + sorted[hi]*frac
}

func distOf(msValues []float64) Dist {
	d := Dist{Count: len(msValues)}
	if len(msValues) == 0 {
		return d
	}
	sorted := append([]float64(nil), msValues...)
	sort.Float64s(sorted)
	var sum float64
	for _, v := range sorted {
		sum += v
	}
	d.Mean = sum / float64(len(sorted))
	var sq float64
	for _, v := range sorted {
		sq += (v - d.Mean) * (v - d.Mean)
	}
	d.Std = math.Sqrt(sq / float64(len(sorted)))
	d.P50 = percentile(sorted, 0.50)
	d.P90 = percentile(sorted, 0.90)
	d.P99 = percentile(sorted, 0.99)
	d.Max = sorted[len(sorted)-1]
	return d
}

// Build produces the final report. start and end delimit the measurement
// window; samples outside it (warm-up) must already have been excluded by the
// caller.
func (c *Collector) Build(mode, label string, params map[string]any, start, end time.Time) Report {
	dur := end.Sub(start).Seconds()
	if dur <= 0 {
		dur = math.SmallestNonzeroFloat64
	}
	nBuckets := int(math.Ceil(end.Sub(start).Seconds() * 1000 / float64(c.bucket.Milliseconds())))
	if nBuckets < 1 {
		nBuckets = 1
	}

	rep := Report{
		Schema:    "test.rpcbench.result/1",
		Mode:      mode,
		Label:     label,
		Params:    params,
		StartedAt: start.UTC().Format(time.RFC3339Nano),
		HoL: HoL{
			BucketMs: int(c.bucket.Milliseconds()),
			Streams:  len(c.recorders),
			Timeline: map[string][]int{},
		},
	}

	var allRTT, allOneWay []float64
	timeline := make([][]int, len(c.recorders))
	var p99s []float64

	for i, r := range c.recorders {
		r.mu.Lock()
		samples := append([]Sample(nil), r.samples...)
		sendErr, recvErr, firstErr := r.sendErr, r.recvErr, r.firstErr
		r.mu.Unlock()

		sr := StreamReport{Name: r.Name, SendErrors: sendErr, RecvErrors: recvErr, FirstError: firstErr}
		rtt := make([]float64, 0, len(samples))
		oneway := make([]float64, 0, len(samples))
		buckets := make([]int, nBuckets)

		// Completion instants, used for the stall (inter-completion gap) metric.
		var lastAck time.Time
		gaps := make([]float64, 0, len(samples))

		for _, s := range samples {
			sr.Batches++
			sr.Messages += s.Items
			sr.WireBytes += s.WireBytes
			rtt = append(rtt, float64(s.AckedAt.Sub(s.SentAt).Nanoseconds())/1e6)
			if !s.ServerAt.IsZero() {
				oneway = append(oneway, float64(s.ServerAt.Sub(s.SentAt).Nanoseconds())/1e6)
			}
			if idx := int(s.AckedAt.Sub(start).Milliseconds() / c.bucket.Milliseconds()); idx >= 0 && idx < nBuckets {
				buckets[idx]++
			}
			ref := lastAck
			if ref.IsZero() {
				ref = start
			}
			gaps = append(gaps, float64(s.AckedAt.Sub(ref).Nanoseconds())/1e6)
			lastAck = s.AckedAt
		}

		sr.RTT = distOf(rtt)
		sr.OneWay = distOf(oneway)
		sortedGaps := append([]float64(nil), gaps...)
		sort.Float64s(sortedGaps)
		sr.P99StallMs = percentile(sortedGaps, 0.99)
		if len(sortedGaps) > 0 {
			sr.MaxStallMs = sortedGaps[len(sortedGaps)-1]
		}
		sr.GoodputMbps = float64(sr.WireBytes) * 8 / dur / 1e6
		sr.MessagesPerS = float64(sr.Messages) / dur

		allRTT = append(allRTT, rtt...)
		allOneWay = append(allOneWay, oneway...)
		timeline[i] = buckets
		rep.HoL.Timeline[r.Name] = buckets
		p99s = append(p99s, sr.RTT.P99)

		rep.Totals.Batches += sr.Batches
		rep.Totals.Messages += sr.Messages
		rep.Totals.WireBytes += sr.WireBytes
		rep.Totals.SendErrors += sendErr
		rep.Totals.RecvErrors += recvErr
		if rep.Totals.FirstError == "" && firstErr != "" {
			rep.Totals.FirstError = r.Name + " " + firstErr
		}
		rep.Streams = append(rep.Streams, sr)
	}

	rep.Totals.DurationS = dur
	rep.Totals.GoodputMbps = float64(rep.Totals.WireBytes) * 8 / dur / 1e6
	rep.Totals.MessagesPerS = float64(rep.Totals.Messages) / dur
	rep.RTT = distOf(allRTT)
	rep.OneWay = distOf(allOneWay)

	// Stall correlation across streams.
	if n := len(timeline); n > 0 {
		var stallBuckets, allStalled, stalledSum int
		for b := 0; b < nBuckets; b++ {
			stalled := 0
			for i := 0; i < n; i++ {
				if timeline[i][b] == 0 {
					stalled++
				}
			}
			if stalled > 0 {
				stallBuckets++
				stalledSum += stalled
				if stalled == n {
					allStalled++
				}
			}
		}
		rep.HoL.StallBuckets = stallBuckets
		rep.HoL.AllStalledBuckets = allStalled
		if stallBuckets > 0 {
			rep.HoL.StallCorrelation = float64(allStalled) / float64(stallBuckets)
			rep.HoL.MeanStalledStreams = float64(stalledSum) / float64(stallBuckets)
		}
	}
	if len(p99s) > 0 {
		sort.Float64s(p99s)
		rep.HoL.P99SpreadMs = p99s[len(p99s)-1] - p99s[0]
	}

	return rep
}
