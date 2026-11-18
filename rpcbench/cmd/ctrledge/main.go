// Command ctrledge is the Edge Agent side of Experiment C (downlink command
// delivery) and the measuring party.
//
// It receives commands from ctrlgw with one delivery mechanism over one HTTP
// version, acknowledges each one, optionally runs uplink telemetry alongside,
// and records how long every command took from the moment upstream issued it
// to the moment the Edge Agent held it.
//
//	ctrledge -server 10.0.2.2 -app grpc -proto h3 -duration 30s \
//	         -ca ./certs/ca.pem -out /tmp/run/ctrlbench.json -param loss_pct=1.0
//
// Apps: poll (REST short polling, -poll-interval), lpoll (REST long polling),
// sse (REST + Server-Sent Events), grpc (gRPC bidirectional stream).
// Protos: h1 (HTTP/1.1), h2 (HTTP/2), h3 (HTTP/3 over QUIC). grpc needs h2/h3.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/control"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/stats"
)

type paramList map[string]any

func (p paramList) String() string { return fmt.Sprintf("%v", map[string]any(p)) }
func (p paramList) Set(v string) error {
	k, val, ok := strings.Cut(v, "=")
	if !ok {
		return fmt.Errorf("expected key=value, got %q", v)
	}
	if f, err := strconv.ParseFloat(strings.TrimSpace(val), 64); err == nil {
		p[strings.TrimSpace(k)] = f
		return nil
	}
	p[strings.TrimSpace(k)] = strings.TrimSpace(val)
	return nil
}

// Result is the document written per run (schema test.ctrlbench.result/1).
type Result struct {
	Schema    string         `json:"schema"`
	Mode      string         `json:"mode"` // "<app>-<proto>", e.g. "grpc-h3"
	App       string         `json:"app"`
	Proto     string         `json:"proto"`
	Label     string         `json:"label"`
	Params    map[string]any `json:"params"`
	StartedAt string         `json:"started_at"`

	Downlink Downlink          `json:"downlink"`
	Control  ControlPlane      `json:"control"`
	Link     Link              `json:"link"`
	Uplink   *Uplink           `json:"uplink,omitempty"`
	Notes    map[string]string `json:"notes"`
}

// Downlink is the headline section.
type Downlink struct {
	// From the upstream ledger: commands issued inside the window.
	Issued     int  `json:"issued"`
	LedgerOK   bool `json:"ledger_ok"`
	Delivered  int  `json:"delivered"`
	Missing    int  `json:"missing"`
	Duplicates int  `json:"duplicates"`
	// Issue -> Edge Agent holds it (one-way, shared clock).
	Latency stats.Dist `json:"latency"`
	// Issue -> upstream has the Edge Agent's acknowledgement (full loop).
	Confirm stats.Dist `json:"confirm"`
	// Raw one-way latencies in ms, in seq order, so repetitions can be pooled
	// before taking percentiles.
	LatenciesMs []float64 `json:"latencies_ms"`
}

// ControlPlane counts what the delivery mechanism cost in requests.
type ControlPlane struct {
	PollRequests       int64   `json:"poll_requests"`
	EmptyPolls         int64   `json:"empty_polls"`
	AckRequests        int64   `json:"ack_requests"`
	AckMessages        int64   `json:"ack_messages"`
	AckDropped         int64   `json:"ack_dropped"`
	Reconnects         int64   `json:"reconnects"`
	Errors             int64   `json:"errors"`
	FirstError         string  `json:"first_error,omitempty"`
	RequestsPerCommand float64 `json:"requests_per_command"`
}

// Link is the Edge Agent's interface counters over the window.
type Link struct {
	Interfaces      []string `json:"interfaces"`
	TxBytes         int64    `json:"tx_bytes"`
	RxBytes         int64    `json:"rx_bytes"`
	TxPackets       int64    `json:"tx_packets"`
	RxPackets       int64    `json:"rx_packets"`
	BytesPerCommand float64  `json:"bytes_per_command"`
}

// Uplink summarizes the telemetry that ran alongside.
type Uplink struct {
	Streams      int        `json:"streams"`
	Batch        int        `json:"batch"`
	MessagesPerS float64    `json:"messages_per_s"`
	RTT          stats.Dist `json:"rtt"`
	Errors       int        `json:"errors"`
	FirstError   string     `json:"first_error,omitempty"`
}

func main() {
	server := flag.String("server", "127.0.0.1", "ctrlgw address")
	port := flag.Int("port", control.DefaultPort, "ctrlgw port")
	appStr := flag.String("app", "grpc", "delivery mechanism: poll, lpoll, sse or grpc")
	protoStr := flag.String("proto", "h2", "HTTP version: h1, h2 or h3")
	pollInterval := flag.Duration("poll-interval", time.Second, "short-polling period (app=poll only)")
	cmdRate := flag.Float64("cmd-rate", control.DefaultCmdRate, "commands per second upstream issues for this agent (Poisson)")
	cmdBytes := flag.Int("cmd-bytes", control.DefaultCmdBytes, "opaque padding per command")
	upStreams := flag.Int("up-streams", 0, "uplink telemetry streams running alongside (0 = control plane only)")
	upBatch := flag.Int("up-batch", 20, "measurements per uplink batch")
	duration := flag.Duration("duration", 30*time.Second, "measurement window")
	warmup := flag.Duration("warmup", 5*time.Second, "warm-up excluded from the results")
	grace := flag.Duration("grace", 3*time.Second, "time after the window for commands issued inside it to arrive")
	caPath := flag.String("ca", "", "CA certificate")
	insecure := flag.Bool("insecure", false, "skip certificate verification")
	serverName := flag.String("server-name", "", "SNI override; defaults to -server")
	agent := flag.String("agent", "", "agent id (default: derived from label, time and pid, unique per run)")
	label := flag.String("label", "", "free-form run label")
	seed := flag.Int64("seed", 1, "seed for the command schedule and the telemetry generator")
	out := flag.String("out", "", "write the JSON result here (stdout if empty)")
	params := paramList{}
	flag.Var(params, "param", "extra key=value recorded in the result (repeatable)")
	flag.Parse()

	app, err := control.ParseApp(*appStr)
	if err != nil {
		log.Fatalf("ctrledge: %v", err)
	}
	proto, err := control.ParseProto(*protoStr)
	if err != nil {
		log.Fatalf("ctrledge: %v", err)
	}
	if *agent == "" {
		*agent = fmt.Sprintf("edge-%s-%d-%d", strings.ReplaceAll(*label, "/", "_"), time.Now().UnixNano(), os.Getpid())
	}
	cfg := control.EdgeConfig{
		Host: *server, Port: *port, ServerName: *serverName, CAPath: *caPath, Insecure: *insecure,
		App: app, Proto: proto, PollInterval: *pollInterval,
		AgentID: *agent, CmdRate: *cmdRate, CmdBytes: *cmdBytes, Seed: *seed,
		UpStreams: *upStreams, UpBatch: *upBatch,
	}
	edge, err := control.NewEdge(cfg)
	if err != nil {
		log.Fatalf("ctrledge: %v", err)
	}
	mode := string(app) + "-" + string(proto)
	log.Printf("ctrledge: %s -> %s:%d | %s | %.1f cmd/s, %d B, uplink %d streams",
		mode, *server, *port, control.Describe(app, proto), *cmdRate, *cmdBytes, *upStreams)

	// ---- run ---------------------------------------------------------------
	ctx, cancel := context.WithCancel(context.Background())
	runStart := time.Now()
	measureStart := runStart.Add(*warmup)
	measureEnd := measureStart.Add(*duration)

	edge.Start(ctx)
	upWG, err := edge.StartUplink(ctx, measureStart, measureEnd)
	if err != nil {
		log.Fatalf("ctrledge: %v", err)
	}

	time.Sleep(time.Until(measureStart))
	before, _ := control.ReadNetDev()
	pollsBefore, emptyBefore := edge.Polls.Load(), edge.EmptyPolls.Load()
	ackReqBefore, ackMsgBefore := edge.AckRequests.Load(), edge.AckMessages.Load()
	time.Sleep(time.Until(measureEnd))
	after, _ := control.ReadNetDev()
	polls, empty := edge.Polls.Load()-pollsBefore, edge.EmptyPolls.Load()-emptyBefore
	ackReq, ackMsg := edge.AckRequests.Load()-ackReqBefore, edge.AckMessages.Load()-ackMsgBefore

	time.Sleep(*grace) // let commands issued just before the end arrive
	upWG.Wait()
	cancel()
	edge.Close()

	// ---- ledger from upstream ------------------------------------------------
	lctx, lcancel := context.WithTimeout(context.Background(), 60*time.Second)
	ledger, lerr := control.FetchLedger(lctx, cfg, measureStart, measureEnd)
	lcancel()

	// ---- assemble ------------------------------------------------------------
	got, dups := edge.Deliveries()
	inWindow := func(ns int64) bool { return ns >= measureStart.UnixNano() && ns < measureEnd.UnixNano() }

	var lat []float64
	var seqs []uint64
	for s, d := range got {
		if inWindow(d.IssuedNs) {
			seqs = append(seqs, s)
		}
	}
	sort.Slice(seqs, func(i, j int) bool { return seqs[i] < seqs[j] })
	for _, s := range seqs {
		d := got[s]
		lat = append(lat, float64(d.RecvNs-d.IssuedNs)/1e6)
	}

	res := Result{
		Schema:    "test.ctrlbench.result/1",
		Mode:      mode,
		App:       string(app),
		Proto:     string(proto),
		Label:     *label,
		StartedAt: measureStart.UTC().Format(time.RFC3339Nano),
		Params: map[string]any{
			"server": *server, "port": *port, "poll_interval_s": pollInterval.Seconds(),
			"cmd_rate": *cmdRate, "cmd_bytes": *cmdBytes, "up_streams": *upStreams, "up_batch": *upBatch,
			"duration_s": duration.Seconds(), "warmup_s": warmup.Seconds(), "grace_s": grace.Seconds(),
			"seed": *seed, "agent": *agent, "description": control.Describe(app, proto),
		},
		Notes: map[string]string{
			"latency":  "issued upstream -> held by the Edge Agent, one-way; Mininet hosts share one clock",
			"confirm":  "issued upstream -> acknowledgement received upstream (decision-to-confirmation loop)",
			"link":     "interface counters of the Edge Agent host over the window, all headers included; with up_streams > 0 they include telemetry",
			"requests": "requests_per_command = HTTP requests issued for the control plane (polls + ack POSTs) / delivered commands; a gRPC run uses one long-lived call, its acks are messages on it (ack_messages), so it reports ~0",
		},
	}
	for k, v := range params {
		res.Params[k] = v
	}
	if app != control.AppPoll {
		res.Params["poll_interval_s"] = 0
	}

	dl := &res.Downlink
	dl.Delivered = len(seqs)
	dl.Duplicates = dups
	dl.Latency = stats.DistOf(lat)
	dl.LatenciesMs = lat
	if ledger != nil {
		dl.LedgerOK = true
		dl.Issued = len(ledger.Issued)
		var conf []float64
		for _, r := range ledger.Issued {
			if _, ok := got[r.Seq]; !ok {
				dl.Missing++
			}
			if r.AckUnixNs > 0 {
				conf = append(conf, float64(r.AckUnixNs-r.IssuedUnixNs)/1e6)
			}
		}
		dl.Confirm = stats.DistOf(conf)
	} else {
		log.Printf("ctrledge: WARNING ledger unavailable (%v); missing counted from sequence gaps", lerr)
		if len(seqs) > 0 {
			span := int(seqs[len(seqs)-1]-seqs[0]) + 1
			dl.Issued = span
			dl.Missing = span - len(seqs)
		}
	}

	cp := &res.Control
	cp.PollRequests, cp.EmptyPolls = polls, empty
	cp.AckRequests, cp.AckMessages = ackReq, ackMsg
	cp.AckDropped = edge.AckDropped.Load()
	cp.Reconnects = edge.Reconnects.Load()
	cp.Errors = edge.RecvErrors.Load()
	cp.FirstError = edge.FirstError()
	if dl.Delivered > 0 {
		cp.RequestsPerCommand = float64(polls+ackReq) / float64(dl.Delivered)
	}

	if before != nil && after != nil {
		d, names := control.LinkDelta(before, after)
		res.Link = Link{Interfaces: names, TxBytes: d.TxBytes, RxBytes: d.RxBytes,
			TxPackets: d.TxPackets, RxPackets: d.RxPackets}
		if dl.Delivered > 0 {
			res.Link.BytesPerCommand = float64(d.TxBytes+d.RxBytes) / float64(dl.Delivered)
		}
	}

	if *upStreams > 0 {
		ur := edge.UplinkReport(measureStart, measureEnd)
		res.Uplink = &Uplink{Streams: *upStreams, Batch: *upBatch,
			MessagesPerS: ur.Totals.MessagesPerS, RTT: ur.RTT,
			Errors: ur.Totals.SendErrors + ur.Totals.RecvErrors, FirstError: ur.Totals.FirstError}
	}

	blob, err := json.MarshalIndent(res, "", "  ")
	if err != nil {
		log.Fatalf("ctrledge: %v", err)
	}
	if *out == "" {
		fmt.Println(string(blob))
	} else {
		if err := os.MkdirAll(filepath.Dir(*out), 0o755); err != nil {
			log.Fatalf("ctrledge: %v", err)
		}
		if err := os.WriteFile(*out, blob, 0o644); err != nil {
			log.Fatalf("ctrledge: %v", err)
		}
		log.Printf("ctrledge: wrote %s", *out)
	}
	log.Printf("ctrledge: %s | delivered %d/%d (missing %d) | latency p50 %.1f ms p99 %.1f ms max %.1f ms | confirm p99 %.1f ms | %.2f req/cmd | %.0f B/cmd",
		mode, dl.Delivered, dl.Issued, dl.Missing, dl.Latency.P50, dl.Latency.P99, dl.Latency.Max,
		dl.Confirm.P99, cp.RequestsPerCommand, res.Link.BytesPerCommand)
	if dl.Delivered == 0 {
		log.Print("ctrledge: WARNING no command delivered inside the window")
		os.Exit(1)
	}
}
