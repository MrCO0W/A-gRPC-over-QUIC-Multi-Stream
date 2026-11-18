// Command edgegw is the Edge Gateway: the client side of the Edge<->Cloud hop.
//
// It stands in for a gateway that has already received raw readings over
// MQTT/CoAP and normalized them. One logical stream is opened per sensor kind,
// and each stream repeatedly sends a batch and waits for its acknowledgement.
//
// Every transport sees the same generator, the same batch size, the same
// pacing and the same in-flight limit, so a difference in the result is a
// difference in the transport.
//
//	edgegw -server 10.0.2.2 -mode grpc -streams 8 -duration 30s \
//	       -ca ./certs/ca.pem -out /tmp/run/rpcbench.json -param loss_pct=1.0
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/sensor"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/stats"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/transport"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

type paramList map[string]any

func (p paramList) String() string { return fmt.Sprintf("%v", map[string]any(p)) }

func (p paramList) Set(v string) error {
	k, val, ok := strings.Cut(v, "=")
	if !ok {
		return fmt.Errorf("expected key=value, got %q", v)
	}
	k = strings.TrimSpace(k)
	val = strings.TrimSpace(val)
	if f, err := strconv.ParseFloat(val, 64); err == nil {
		p[k] = f
		return nil
	}
	p[k] = val
	return nil
}

func main() {
	server := flag.String("server", "127.0.0.1", "Cloud Gateway address")
	modeStr := flag.String("mode", "grpc", "transport: rest, grpc, h3 or qtun")
	port := flag.Int("port", 0, "override the default port for the chosen mode")
	streams := flag.Int("streams", 8, "number of concurrent logical streams (one per sensor kind)")
	batch := flag.Int("batch", 20, "measurements per batch")
	pad := flag.Int("pad", 0, "extra opaque bytes per measurement, to control payload size")
	duration := flag.Duration("duration", 30*time.Second, "measurement window")
	warmup := flag.Duration("warmup", 3*time.Second, "warm-up period excluded from the results")
	inflight := flag.Int("inflight", 1, "batches allowed in flight per stream (REST opens this many connections per stream)")
	rate := flag.Float64("rate", 0, "batches per second per stream; 0 means closed-loop (send as fast as acknowledgements allow)")
	bucket := flag.Duration("bucket", 100*time.Millisecond, "timeline bucket width for the head-of-line blocking metrics")
	out := flag.String("out", "", "write the JSON result here (stdout if empty)")
	caPath := flag.String("ca", "", "CA certificate to verify the server against")
	insecure := flag.Bool("insecure", false, "skip server certificate verification")
	serverName := flag.String("server-name", "", "SNI override; defaults to -server")
	label := flag.String("label", "", "free-form run label recorded in the result")
	connectTimeout := flag.Duration("connect-timeout", 30*time.Second, "budget for dialling and opening every stream")
	seed := flag.Int64("seed", 1, "generator seed, for reproducible runs")
	// Experiment B. Zero keeps the steady-state behaviour: every stream is
	// opened before the clock starts. A positive value brings streams online
	// one at a time, which is what a gateway actually does when a new sensor
	// type is provisioned, and is the only way to measure what that costs.
	rampInterval := flag.Duration("ramp-interval", 0,
		"bring streams online one at a time, this far apart (0 = open all before the run)")
	rampWindow := flag.Duration("ramp-window", 200*time.Millisecond,
		"window either side of an add in which the already-running streams' acks are counted")

	params := paramList{}
	flag.Var(params, "param", "extra key=value recorded in the result (repeatable), e.g. -param loss_pct=1.0")
	flag.Parse()

	mode, err := transport.ParseMode(*modeStr)
	if err != nil {
		log.Fatalf("edgegw: %v", err)
	}
	p := *port
	if p == 0 {
		p = transport.DefaultPorts[mode]
	}
	if *streams < 1 {
		log.Fatal("edgegw: -streams must be at least 1")
	}
	if *batch < 1 {
		log.Fatal("edgegw: -batch must be at least 1")
	}
	if *inflight < 1 {
		*inflight = 1
	}

	// -----------------------------------------------------------------------
	// Connect and open every stream before the clock starts.
	//
	// Two contexts on purpose. dialCtx bounds only the initial connection
	// handshake. runCtx is what the logical streams are opened with, and it
	// must outlive the whole measurement: on the HTTP arms the request context
	// is the stream's lifetime, so cancelling it would tear every stream down
	// the instant the handshake finished.
	// -----------------------------------------------------------------------
	runCtx, cancelRun := context.WithCancel(context.Background())
	defer cancelRun()
	dialCtx, cancelDial := context.WithTimeout(runCtx, *connectTimeout)
	defer cancelDial()

	client, err := transport.NewClient(dialCtx, mode, transport.ClientOptions{
		Host:           *server,
		Port:           p,
		CAPath:         *caPath,
		ServerName:     *serverName,
		Insecure:       *insecure,
		ConnsPerStream: *inflight,
	})
	if err != nil {
		log.Fatalf("edgegw: connect: %v", err)
	}
	defer client.Close()

	names := sensor.StreamNames(*streams)
	collector := stats.NewCollector(*bucket)

	newRunner := func(i int, name string, st transport.Stream) *runner {
		return &runner{
			firstAck: make(chan struct{}),
			stream:   st,
			gen:      sensor.NewGenerator(i, name, *pad, *seed),
			rec:      collector.Stream(name),
			inflight: *inflight,
			batch:    *batch,
			rate:     *rate,
		}
	}

	// A recorder must exist for every stream before Build() runs, even in ramp
	// mode where the later streams are opened well into the run; registering
	// them up front keeps the stream list stable and ordered.
	runners := make([]*runner, *streams)

	log.Printf("edgegw: %s -> %s:%d | %d streams x %d measurements/batch, pad %dB, inflight %d",
		mode, *server, p, *streams, *batch, *pad, *inflight)
	log.Printf("edgegw: %s", mode.Describe())
	if *rampInterval > 0 {
		log.Printf("edgegw: ramp mode - one stream every %s "+
			"(throughput totals are NOT comparable with a steady-state run)", *rampInterval)
	}

	// -----------------------------------------------------------------------
	// Run.
	// -----------------------------------------------------------------------
	total := *warmup + *duration
	var wg sync.WaitGroup
	var adds []stats.StreamAdd

	// start launches one stream's driver and returns once it is running.
	start := func(r *runner, measureStart, deadline time.Time) {
		r.measureStart = measureStart
		r.deadline = deadline
		wg.Add(1)
		go func() {
			defer wg.Done()
			r.run()
		}()
	}

	var runStart, measureStart, deadline, measureEnd time.Time

	if *rampInterval <= 0 {
		// ---- steady state: open everything, then start the clock ----------
		for i, name := range names {
			st, err := client.Open(runCtx, name)
			if err != nil {
				log.Fatalf("edgegw: open stream %q: %v", name, err)
			}
			runners[i] = newRunner(i, name, st)
		}
		cancelDial()

		runStart = time.Now()
		measureStart = runStart.Add(*warmup)
		deadline = runStart.Add(total)
		for _, r := range runners {
			start(r, measureStart, deadline)
		}
	} else {
		// ---- ramp: stream 0 before the clock, the rest on a schedule ------
		//
		// Stream 0 is opened up front so that the run has traffic from t=0 and
		// every later add is measured against a link that is already in use -
		// which is the situation a gateway is actually in when a new sensor
		// type appears. Stream 0 therefore has no add cost to report.
		st, err := client.Open(runCtx, names[0])
		if err != nil {
			log.Fatalf("edgegw: open stream %q: %v", names[0], err)
		}
		runners[0] = newRunner(0, names[0], st)
		cancelDial()

		runStart = time.Now()
		measureStart = runStart.Add(*warmup)
		deadline = runStart.Add(total)
		start(runners[0], measureStart, deadline)
		adds = append(adds, stats.StreamAdd{Name: names[0], Index: 0})

		for i := 1; i < *streams; i++ {
			at := runStart.Add(time.Duration(i) * *rampInterval)
			if !at.Before(deadline) {
				log.Printf("edgegw: ramp would open stream %d after the deadline; "+
					"stopping at %d streams", i, i)
				runners = runners[:i]
				break
			}
			// Sample the "before" window so that it ENDS at the scheduled
			// instant. Measuring it after waiting would push every subsequent
			// add later by one window and make requested_ms drift away from
			// the schedule.
			if d := time.Until(at.Add(-*rampWindow)); d > 0 {
				time.Sleep(d)
			}
			// Acks completed by the streams already in service, in the window
			// before this add. Compared with the same count afterwards, this
			// says whether bringing a stream up disturbed the ones running.
			before := peerAcks(runners[:i], *rampWindow)
			if d := time.Until(at); d > 0 {
				time.Sleep(d)
			}

			requested := time.Now()
			nst, err := client.Open(runCtx, names[i])
			opened := time.Now()
			if err != nil {
				log.Printf("edgegw: ramp open stream %q failed: %v", names[i], err)
				runners = runners[:i]
				break
			}
			r := newRunner(i, names[i], nst)
			runners[i] = r
			start(r, measureStart, deadline)

			after := peerAcks(runners[:i], *rampWindow)

			rec := stats.StreamAdd{
				Name:           names[i],
				Index:          i,
				RequestedMs:    ms(requested.Sub(runStart)),
				OpenedMs:       ms(opened.Sub(runStart)),
				OpenLatencyMs:  ms(opened.Sub(requested)),
				PeerAcksBefore: before,
				PeerAcksAfter:  after,
			}
			// firstAckAt is written by the runner's receiver goroutine; give it
			// a bounded moment to land rather than blocking the schedule.
			if t, ok := r.waitFirstAck(2 * time.Second); ok {
				rec.FirstAckMs = ms(t.Sub(runStart))
				rec.AddLatencyMs = ms(t.Sub(requested))
			}
			adds = append(adds, rec)
		}
	}

	wg.Wait()
	measureEnd = time.Now()
	if measureEnd.After(deadline) {
		measureEnd = deadline
	}

	// -----------------------------------------------------------------------
	// Report.
	// -----------------------------------------------------------------------
	reportParams := map[string]any{
		"streams":         *streams,
		"batch":           *batch,
		"pad_bytes":       *pad,
		"inflight":        *inflight,
		"rate_per_s":      *rate,
		"duration_s":      duration.Seconds(),
		"warmup_s":        warmup.Seconds(),
		"server":          *server,
		"port":            p,
		"seed":            *seed,
		"description":     mode.Describe(),
		"ramp_interval_s": rampInterval.Seconds(),
	}
	for k, v := range params {
		reportParams[k] = v
	}

	rep := collector.Build(string(mode), *label, reportParams, measureStart, measureEnd)
	if *rampInterval > 0 {
		rep.Ramp = stats.BuildRamp(float64(rampInterval.Milliseconds()),
			float64(rampWindow.Milliseconds()), adds)
		log.Printf("edgegw: ramp | add latency p50 %.0f ms p99 %.0f ms max %.0f ms (n=%d)",
			rep.Ramp.AddLatency.P50, rep.Ramp.AddLatency.P99,
			rep.Ramp.AddLatency.Max, rep.Ramp.AddLatency.Count)
	}
	rep.Notes = map[string]string{
		"framing": "gRPC length-prefixed messages on grpc/h3/qtun; JSON request body on rest",
		"bytes":   "wire_bytes counts framed application bytes sent by the client; TLS, HTTP header and QUIC packet overhead are excluded uniformly",
	}

	blob, err := json.MarshalIndent(rep, "", "  ")
	if err != nil {
		log.Fatalf("edgegw: encode result: %v", err)
	}
	if *out == "" {
		fmt.Println(string(blob))
	} else {
		if err := os.MkdirAll(filepath.Dir(*out), 0o755); err != nil {
			log.Fatalf("edgegw: %v", err)
		}
		if err := os.WriteFile(*out, blob, 0o644); err != nil {
			log.Fatalf("edgegw: write result: %v", err)
		}
		log.Printf("edgegw: wrote %s", *out)
	}

	log.Printf("edgegw: %s | goodput %.2f Mbps | %.0f msg/s | RTT p50 %.2f ms p99 %.2f ms | stall correlation %.2f (%d streams)",
		mode, rep.Totals.GoodputMbps, rep.Totals.MessagesPerS,
		rep.RTT.P50, rep.RTT.P99, rep.HoL.StallCorrelation, rep.HoL.Streams)

	if rep.Totals.Batches == 0 {
		log.Print("edgegw: WARNING no batches completed inside the measurement window")
		os.Exit(1)
	}
}

// ms renders a duration as fractional milliseconds.
func ms(d time.Duration) float64 { return float64(d) / float64(time.Millisecond) }

// peerAcks counts the acknowledgements the given runners complete over one
// window. Called immediately before and after a stream is brought online, the
// two counts say whether the new stream cost the running ones any progress.
func peerAcks(rs []*runner, window time.Duration) int {
	if len(rs) == 0 || window <= 0 {
		return 0
	}
	var start int64
	for _, r := range rs {
		if r != nil {
			start += atomic.LoadInt64(&r.acks)
		}
	}
	time.Sleep(window)
	var end int64
	for _, r := range rs {
		if r != nil {
			end += atomic.LoadInt64(&r.acks)
		}
	}
	return int(end - start)
}

// ---------------------------------------------------------------------------
// Per-stream driver
// ---------------------------------------------------------------------------

type pending struct {
	sentAt time.Time
	bytes  int64
	items  int
}

type runner struct {
	stream   transport.Stream
	gen      *sensor.Generator
	rec      *stats.StreamRecorder
	inflight int
	batch    int
	rate     float64

	measureStart time.Time
	deadline     time.Time

	// acks is read by peerAcks() from the scheduling goroutine while the
	// receiver goroutine writes it, hence atomic.
	acks int64
	// firstAck closes as soon as this stream's first acknowledgement arrives,
	// which is what makes the stream "in service" from the application's view.
	firstAckOnce sync.Once
	firstAck     chan struct{}
	firstAckAt   time.Time

	mu   sync.Mutex
	open map[uint64]pending
}

// waitFirstAck blocks until this stream's first acknowledgement lands, or the
// budget expires.
func (r *runner) waitFirstAck(budget time.Duration) (time.Time, bool) {
	select {
	case <-r.firstAck:
		return r.firstAckAt, true
	case <-time.After(budget):
		return time.Time{}, false
	}
}

func (r *runner) run() {
	r.open = make(map[uint64]pending, r.inflight*2)
	tokens := make(chan struct{}, r.inflight)
	for i := 0; i < r.inflight; i++ {
		tokens <- struct{}{}
	}
	done := make(chan struct{})

	// Receiver: drains acknowledgements and returns in-flight tokens.
	go func() {
		defer close(done)
		for {
			ack, err := r.stream.Recv()
			if err != nil {
				if time.Now().Before(r.deadline) {
					r.rec.AddRecvError(err)
				}
				return
			}
			now := time.Now()
			atomic.AddInt64(&r.acks, 1)
			r.firstAckOnce.Do(func() {
				r.firstAckAt = now
				close(r.firstAck)
			})
			r.mu.Lock()
			pd, ok := r.open[ack.BatchSeq]
			if ok {
				delete(r.open, ack.BatchSeq)
			}
			r.mu.Unlock()
			select {
			case tokens <- struct{}{}:
			default:
			}
			if !ok || pd.sentAt.Before(r.measureStart) {
				continue // warm-up, or an ack we have no record of
			}
			var srvAt time.Time
			if ack.RecvUnixNs != 0 {
				srvAt = time.Unix(0, ack.RecvUnixNs)
			}
			r.rec.Add(stats.Sample{
				SentAt:    pd.sentAt,
				AckedAt:   now,
				ServerAt:  srvAt,
				WireBytes: pd.bytes,
				Items:     pd.items,
			})
		}
	}()

	var ticker *time.Ticker
	if r.rate > 0 {
		ticker = time.NewTicker(time.Duration(float64(time.Second) / r.rate))
		defer ticker.Stop()
	}

	var seq uint64
	items := make([]wire.Measurement, r.batch)
	for time.Now().Before(r.deadline) {
		select {
		case <-tokens:
		case <-time.After(time.Until(r.deadline)):
			goto drain
		}
		if ticker != nil {
			select {
			case <-ticker.C:
			case <-time.After(time.Until(r.deadline)):
				goto drain
			}
		}
		if !time.Now().Before(r.deadline) {
			break
		}

		seq++
		items = r.gen.Next(items, r.batch)
		b := &wire.Batch{
			Stream:     r.gen.Name(),
			BatchSeq:   seq,
			SentUnixNs: time.Now().UnixNano(),
			Items:      items,
		}
		sentAt := time.Now()
		r.mu.Lock()
		r.open[seq] = pending{sentAt: sentAt, items: len(items)}
		r.mu.Unlock()

		n, err := r.stream.Send(b)
		if err != nil {
			r.mu.Lock()
			delete(r.open, seq)
			r.mu.Unlock()
			r.rec.AddSendError(err)
			break
		}
		r.mu.Lock()
		if pd, ok := r.open[seq]; ok {
			pd.bytes = n
			r.open[seq] = pd
		}
		r.mu.Unlock()
	}

drain:
	// Let the acknowledgements for the last in-flight batches arrive, then
	// tear the stream down and wait for the receiver goroutine to finish.
	_ = r.stream.CloseSend()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
	}
	_ = r.stream.Close()
	select {
	case <-done:
	case <-time.After(time.Second):
	}
}
