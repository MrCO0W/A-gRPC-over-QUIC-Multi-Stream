package control

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/quic-go/quic-go/http3"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/rpcframe"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/sensor"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/stats"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/tlsutil"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/transport"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

// App is the delivery mechanism under test.
type App string

// Proto is the HTTP version (and therefore the transport) under test.
type Proto string

const (
	AppPoll  App = "poll"
	AppLPoll App = "lpoll"
	AppSSE   App = "sse"
	AppGRPC  App = "grpc"

	ProtoH1 Proto = "h1" // HTTP/1.1 over TLS over TCP
	ProtoH2 Proto = "h2" // HTTP/2 over TLS over TCP
	ProtoH3 Proto = "h3" // HTTP/3 over QUIC
)

// DefaultPort is where ctrlgw listens (TCP for h1/h2, UDP for h3).
const DefaultPort = 8450

// ParseApp validates an application name.
func ParseApp(s string) (App, error) {
	switch a := App(strings.ToLower(strings.TrimSpace(s))); a {
	case AppPoll, AppLPoll, AppSSE, AppGRPC:
		return a, nil
	}
	return "", fmt.Errorf("control: unknown app %q (want poll, lpoll, sse or grpc)", s)
}

// ParseProto validates a protocol name.
func ParseProto(s string) (Proto, error) {
	switch p := Proto(strings.ToLower(strings.TrimSpace(s))); p {
	case ProtoH1, ProtoH2, ProtoH3:
		return p, nil
	}
	return "", fmt.Errorf("control: unknown proto %q (want h1, h2 or h3)", s)
}

// Describe is a one-line description of an app/proto pair.
func Describe(a App, p Proto) string {
	tr := map[Proto]string{
		ProtoH1: "HTTP/1.1 over TLS/TCP",
		ProtoH2: "HTTP/2 over TLS/TCP (one connection)",
		ProtoH3: "HTTP/3 over QUIC (one connection)",
	}[p]
	switch a {
	case AppPoll:
		return "REST short polling, " + tr
	case AppLPoll:
		return "REST long polling, " + tr
	case AppSSE:
		return "REST + Server-Sent Events, " + tr
	case AppGRPC:
		return "gRPC bidirectional stream (Control.Session), " + tr
	}
	return string(a) + "/" + string(p)
}

// EdgeConfig configures the Edge Agent side.
type EdgeConfig struct {
	Host, ServerName, CAPath string
	Port                     int
	Insecure                 bool

	App          App
	Proto        Proto
	PollInterval time.Duration // poll only

	AgentID  string
	CmdRate  float64
	CmdBytes int
	Seed     int64

	// Uplink telemetry running alongside the control plane. On h2 and h3 it
	// shares the control-plane connection; on h1 every stream has its own.
	UpStreams int
	UpBatch   int
}

// Edge is one Edge Agent under measurement.
type Edge struct {
	cfg  EdgeConfig
	base string
	tls  map[Proto]*tls.Config

	shared      *http.Client // h2 and h3: the one connection everything uses
	sharedClose func()
	closers     []func()
	closersMu   sync.Mutex

	mu      sync.Mutex
	got     map[uint64]Delivery
	dups    int
	lastSeq atomic.Uint64
	ackQ    chan wire.CommandAck
	sessMu  sync.Mutex
	sessW   *rpcframe.Writer // current gRPC session writer, nil if none

	// Control-plane request accounting.
	Polls, EmptyPolls, AckRequests, AckMessages, AckDropped atomic.Int64
	Reconnects, RecvErrors                                  atomic.Int64
	errMu                                                   sync.Mutex
	firstErr                                                string

	up *stats.Collector
}

// Delivery is one command as the Edge Agent saw it.
type Delivery struct {
	Seq        uint64
	IssuedNs   int64
	RecvNs     int64
	Duplicates int
}

// NewEdge validates the configuration and prepares HTTP clients.
func NewEdge(cfg EdgeConfig) (*Edge, error) {
	if cfg.App == AppGRPC && cfg.Proto == ProtoH1 {
		return nil, errors.New("control: gRPC needs HTTP/2 or HTTP/3 (grpc-h1 is not a valid mode)")
	}
	if cfg.App == AppPoll && cfg.PollInterval <= 0 {
		return nil, errors.New("control: poll needs -poll-interval > 0")
	}
	name := cfg.ServerName
	if name == "" {
		name = cfg.Host
	}
	e := &Edge{
		cfg:  cfg,
		base: "https://" + net.JoinHostPort(cfg.Host, strconv.Itoa(cfg.Port)),
		tls:  map[Proto]*tls.Config{},
		got:  map[uint64]Delivery{},
		ackQ: make(chan wire.CommandAck, 1<<14),
		up:   stats.NewCollector(100 * time.Millisecond),
	}
	for p, alpn := range map[Proto]string{ProtoH1: "http/1.1", ProtoH2: "h2", ProtoH3: "h3"} {
		c, err := tlsutil.ClientConfig(cfg.CAPath, name, cfg.Insecure, []string{alpn})
		if err != nil {
			return nil, err
		}
		e.tls[p] = c
	}
	switch cfg.Proto {
	case ProtoH2:
		tr := &http.Transport{
			TLSClientConfig:     e.tls[ProtoH2],
			ForceAttemptHTTP2:   true,
			DisableCompression:  true,
			TLSHandshakeTimeout: 20 * time.Second,
		}
		e.shared, e.sharedClose = &http.Client{Transport: tr}, tr.CloseIdleConnections
	case ProtoH3:
		rt := &http3.RoundTripper{TLSClientConfig: e.tls[ProtoH3], QUICConfig: transport.QUICConfig()}
		e.shared, e.sharedClose = &http.Client{Transport: rt}, func() { _ = rt.Close() }
	}
	return e, nil
}

// client returns the client a component should use. On h1 every component
// gets its own connection, because HTTP/1.1 cannot carry two exchanges at
// once on one connection; on h2/h3 everything shares one.
func (e *Edge) client() *http.Client { return e.clientPool(1) }

// clientPool is client() with up to n parallel connections on h1. On h2/h3 n
// is irrelevant: exchanges are multiplexed on the shared connection.
func (e *Edge) clientPool(n int) *http.Client {
	if e.cfg.Proto != ProtoH1 {
		return e.shared
	}
	tr := &http.Transport{
		TLSClientConfig:     e.tls[ProtoH1].Clone(),
		ForceAttemptHTTP2:   false,
		TLSNextProto:        map[string]func(string, *tls.Conn) http.RoundTripper{},
		MaxConnsPerHost:     n,
		MaxIdleConnsPerHost: n,
		DisableCompression:  true,
		TLSHandshakeTimeout: 20 * time.Second,
	}
	e.closersMu.Lock()
	e.closers = append(e.closers, tr.CloseIdleConnections)
	e.closersMu.Unlock()
	return &http.Client{Transport: tr}
}

func (e *Edge) noteErr(err error) {
	if err == nil {
		return
	}
	e.RecvErrors.Add(1)
	e.errMu.Lock()
	if e.firstErr == "" {
		e.firstErr = err.Error()
	}
	e.errMu.Unlock()
}

// FirstError returns the first control-plane error seen, if any.
func (e *Edge) FirstError() string {
	e.errMu.Lock()
	defer e.errMu.Unlock()
	return e.firstErr
}

func (e *Edge) genQuery() string {
	v := url.Values{}
	v.Set("rate", strconv.FormatFloat(e.cfg.CmdRate, 'f', -1, 64))
	v.Set("bytes", strconv.Itoa(e.cfg.CmdBytes))
	v.Set("seed", strconv.FormatInt(e.cfg.Seed, 10))
	return v.Encode()
}

func (e *Edge) agentURL(suffix string) string {
	return e.base + "/v1/agents/" + url.PathEscape(e.cfg.AgentID) + suffix
}

// deliver records a command the moment the Edge Agent holds it, then hands it
// to the actuator stub and queues the acknowledgement.
func (e *Edge) deliver(c wire.Command) {
	now := time.Now()
	e.mu.Lock()
	if d, ok := e.got[c.Seq]; ok {
		d.Duplicates++
		e.got[c.Seq] = d
		e.dups++
		e.mu.Unlock()
		return
	}
	e.got[c.Seq] = Delivery{Seq: c.Seq, IssuedNs: c.IssuedUnixNs, RecvNs: now.UnixNano()}
	e.mu.Unlock()
	for {
		old := e.lastSeq.Load()
		if c.Seq <= old || e.lastSeq.CompareAndSwap(old, c.Seq) {
			break
		}
	}
	// Actuator stub: the experiment measures delivery, not the device, so the
	// call returns at once. Its timestamp is still recorded in the ack.
	ack := wire.CommandAck{Seq: c.Seq, RecvUnixNs: now.UnixNano(), DoneUnixNs: time.Now().UnixNano()}
	select {
	case e.ackQ <- ack:
	default:
		e.AckDropped.Add(1)
	}
}

// Deliveries returns a copy of everything received.
func (e *Edge) Deliveries() (map[uint64]Delivery, int) {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make(map[uint64]Delivery, len(e.got))
	for k, v := range e.got {
		out[k] = v
	}
	return out, e.dups
}

// Start launches the receiver and the ack sender. They run until ctx ends.
func (e *Edge) Start(ctx context.Context) {
	switch e.cfg.App {
	case AppPoll:
		go e.runPoll(ctx, e.client(), e.cfg.PollInterval, 0)
	case AppLPoll:
		go e.runPoll(ctx, e.client(), 0, LongPollMax)
	case AppSSE:
		go e.runSSE(ctx, e.client())
	case AppGRPC:
		go e.runSession(ctx, e.shared)
	}
	if e.cfg.App == AppGRPC {
		go e.ackOverSession(ctx)
	} else {
		go e.ackOverREST(ctx, e.clientPool(AckParallel))
	}
}

// ---------------------------------------------------------------------------
// Receivers
// ---------------------------------------------------------------------------

func sleepCtx(ctx context.Context, d time.Duration) bool {
	if d <= 0 {
		return ctx.Err() == nil
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return true
	case <-ctx.Done():
		return false
	}
}

// runPoll implements both short polling (interval > 0, wait = 0) and long
// polling (interval = 0, wait > 0). Short polling keeps a fixed cadence: the
// next request starts one interval after the previous one started, or at once
// if the previous one took longer than that. Requests never overlap.
func (e *Edge) runPoll(ctx context.Context, cli *http.Client, interval, wait time.Duration) {
	q := e.genQuery()
	for ctx.Err() == nil {
		start := time.Now()
		u := fmt.Sprintf("%s?after=%d&wait=%d&%s", e.agentURL("/commands"),
			e.lastSeq.Load(), wait.Milliseconds(), q)
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
		resp, err := cli.Do(req)
		e.Polls.Add(1)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			e.noteErr(fmt.Errorf("poll: %w", err))
			sleepCtx(ctx, 100*time.Millisecond)
			continue
		}
		var pr PollResponse
		err = json.NewDecoder(resp.Body).Decode(&pr)
		_, _ = io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		if err != nil || resp.StatusCode != http.StatusOK {
			if ctx.Err() != nil {
				return
			}
			e.noteErr(fmt.Errorf("poll: status %d: %v", resp.StatusCode, err))
			sleepCtx(ctx, 100*time.Millisecond)
			continue
		}
		if len(pr.Commands) == 0 {
			e.EmptyPolls.Add(1)
		}
		for _, c := range pr.Commands {
			e.deliver(c)
		}
		if interval > 0 && !sleepCtx(ctx, time.Until(start.Add(interval))) {
			return
		}
	}
}

// runSSE keeps one event stream open and reconnects from the last id.
func (e *Edge) runSSE(ctx context.Context, cli *http.Client) {
	q := e.genQuery()
	first := true
	for ctx.Err() == nil {
		if !first {
			e.Reconnects.Add(1)
		}
		first = false
		last := e.lastSeq.Load()
		u := fmt.Sprintf("%s?after=%d&%s", e.agentURL("/events"), last, q)
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
		req.Header.Set("Accept", "text/event-stream")
		if last > 0 {
			req.Header.Set("Last-Event-ID", strconv.FormatUint(last, 10))
		}
		resp, err := cli.Do(req)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			e.noteErr(fmt.Errorf("sse: %w", err))
			sleepCtx(ctx, 200*time.Millisecond)
			continue
		}
		if resp.StatusCode != http.StatusOK {
			resp.Body.Close()
			e.noteErr(fmt.Errorf("sse: status %d", resp.StatusCode))
			sleepCtx(ctx, 200*time.Millisecond)
			continue
		}
		err = e.readSSE(resp.Body)
		resp.Body.Close()
		if ctx.Err() != nil {
			return
		}
		e.noteErr(fmt.Errorf("sse stream ended: %v", err))
	}
}

func (e *Edge) readSSE(body io.Reader) error {
	br := bufio.NewReaderSize(body, 64<<10)
	var event string
	var data bytes.Buffer
	for {
		line, err := br.ReadString('\n')
		if err != nil {
			return err
		}
		line = strings.TrimRight(line, "\r\n")
		switch {
		case line == "":
			if event == "command" && data.Len() > 0 {
				var c wire.Command
				if json.Unmarshal(data.Bytes(), &c) == nil {
					e.deliver(c)
				}
			}
			event = ""
			data.Reset()
		case strings.HasPrefix(line, ":"):
			// comment / heartbeat
		case strings.HasPrefix(line, "event:"):
			event = strings.TrimSpace(line[len("event:"):])
		case strings.HasPrefix(line, "data:"):
			if data.Len() > 0 {
				data.WriteByte('\n')
			}
			data.WriteString(strings.TrimPrefix(line[len("data:"):], " "))
		}
	}
}

// runSession keeps one gRPC Control.Session call open and reconnects from the
// last sequence number.
func (e *Edge) runSession(ctx context.Context, cli *http.Client) {
	first := true
	for ctx.Err() == nil {
		if !first {
			e.Reconnects.Add(1)
		}
		first = false
		err := e.session(ctx, cli)
		if ctx.Err() != nil {
			return
		}
		e.noteErr(fmt.Errorf("grpc session: %w", err))
		sleepCtx(ctx, 200*time.Millisecond)
	}
}

func (e *Edge) session(ctx context.Context, cli *http.Client) error {
	pr, pw := io.Pipe()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, e.base+SessionMethod, pr)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/grpc+proto")
	req.Header.Set("TE", "trailers")
	req.Header.Set("Grpc-Accept-Encoding", "identity")
	req.ContentLength = -1

	// The request body must start flowing before the response can arrive on
	// some stacks, so Hello is written from a goroutine.
	fw := rpcframe.NewWriter(pw)
	hello := wire.EdgeMsg{Hello: &wire.Hello{
		AgentID: e.cfg.AgentID, AfterSeq: e.lastSeq.Load(),
		CmdRate: e.cfg.CmdRate, CmdBytes: uint32(e.cfg.CmdBytes), Seed: e.cfg.Seed,
	}}
	go func() {
		e.sessMu.Lock()
		err := fw.WriteMessage(hello.Marshal())
		if err == nil {
			e.sessW = fw
		}
		e.sessMu.Unlock()
	}()
	defer func() {
		// Close the pipe first: a writer blocked on it (Hello or an ack) holds
		// sessMu, and closing is what releases it.
		pw.Close()
		e.sessMu.Lock()
		if e.sessW == fw {
			e.sessW = nil
		}
		e.sessMu.Unlock()
	}()

	resp, err := cli.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("status %d", resp.StatusCode)
	}
	if e.cfg.Proto == ProtoH2 && resp.Proto != "HTTP/2.0" {
		return fmt.Errorf("negotiated %s, expected HTTP/2.0", resp.Proto)
	}
	fr := rpcframe.NewReader(resp.Body)
	for {
		msg, err := fr.ReadMessage()
		if err != nil {
			if err == io.EOF {
				return fmt.Errorf("server closed the session (grpc-status %s)", resp.Trailer.Get("Grpc-Status"))
			}
			return err
		}
		var c wire.Command
		if err := c.Unmarshal(msg); err != nil {
			return err
		}
		e.deliver(c)
	}
}

// ---------------------------------------------------------------------------
// Acknowledgements
// ---------------------------------------------------------------------------

// AckParallel is how many acknowledgement requests a REST Edge Agent keeps in
// flight at once (and, on h1, how many connections it may open for them).
// Sending each acknowledgement at once, without waiting for the previous
// response, is what a well-written REST client does; a strictly sequential
// poster would add up to one round trip of queueing to REST's confirmation
// time that has nothing to do with REST itself.
const AckParallel = 4

// ackOverREST posts acknowledgements as soon as they exist, batching only what
// queued up while every slot was busy.
func (e *Edge) ackOverREST(ctx context.Context, cli *http.Client) {
	u := e.agentURL("/acks")
	slots := make(chan struct{}, AckParallel)
	for {
		var first wire.CommandAck
		select {
		case first = <-e.ackQ:
		case <-ctx.Done():
			return
		}
		select {
		case slots <- struct{}{}:
		case <-ctx.Done():
			return
		}
		batch := []wire.CommandAck{first}
	drain:
		for len(batch) < 256 {
			select {
			case a := <-e.ackQ:
				batch = append(batch, a)
			default:
				break drain
			}
		}
		e.AckRequests.Add(1)
		e.AckMessages.Add(int64(len(batch)))
		go func(batch []wire.CommandAck) {
			defer func() { <-slots }()
			body, _ := json.Marshal(AckRequest{Acks: batch})
			req, _ := http.NewRequestWithContext(ctx, http.MethodPost, u, bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			resp, err := cli.Do(req)
			if err != nil {
				if ctx.Err() == nil {
					e.noteErr(fmt.Errorf("ack: %w", err))
				}
				return
			}
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
		}(batch)
	}
}

// ackOverSession writes one CommandAck message per command on the open
// session, the natural shape for a bidirectional stream.
func (e *Edge) ackOverSession(ctx context.Context) {
	for {
		var a wire.CommandAck
		select {
		case a = <-e.ackQ:
		case <-ctx.Done():
			return
		}
		msg := (&wire.EdgeMsg{Ack: &a}).Marshal()
		sent := false
		for !sent && ctx.Err() == nil {
			e.sessMu.Lock()
			w := e.sessW
			if w != nil {
				sent = w.WriteMessage(msg) == nil
			}
			e.sessMu.Unlock()
			if !sent {
				// No session at the moment (reconnecting); try again shortly.
				sleepCtx(ctx, 20*time.Millisecond)
			}
		}
		if sent {
			// A message on the open call, not an HTTP request: AckRequests
			// stays at zero for gRPC, which is the point of the comparison.
			e.AckMessages.Add(1)
		}
	}
}

// ---------------------------------------------------------------------------
// Uplink telemetry
// ---------------------------------------------------------------------------

type upChannel interface {
	roundTrip(b *wire.Batch) (*wire.Ack, int64, error)
	close()
}

type restUp struct {
	cli *http.Client
	url string
}

func (r *restUp) roundTrip(b *wire.Batch) (*wire.Ack, int64, error) {
	body, err := json.Marshal(b)
	if err != nil {
		return nil, 0, err
	}
	req, _ := http.NewRequest(http.MethodPost, r.url, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := r.cli.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		io.Copy(io.Discard, resp.Body)
		return nil, 0, fmt.Errorf("status %d", resp.StatusCode)
	}
	var ack wire.Ack
	if err := json.NewDecoder(resp.Body).Decode(&ack); err != nil {
		return nil, 0, err
	}
	return &ack, int64(len(body)), nil
}
func (r *restUp) close() {}

type grpcUp struct{ st transport.Stream }

func (g *grpcUp) roundTrip(b *wire.Batch) (*wire.Ack, int64, error) {
	n, err := g.st.Send(b)
	if err != nil {
		return nil, 0, err
	}
	a, err := g.st.Recv()
	return a, n, err
}
func (g *grpcUp) close() { _ = g.st.CloseSend(); _ = g.st.Close() }

// StartUplink opens UpStreams telemetry streams in the same encoding as the
// control plane (JSON for the REST apps, gRPC for the gRPC app) and drives each
// closed-loop, one batch in flight, until deadline. Only batches sent at or
// after measureStart are recorded.
func (e *Edge) StartUplink(ctx context.Context, measureStart, deadline time.Time) (*sync.WaitGroup, error) {
	wg := &sync.WaitGroup{}
	if e.cfg.UpStreams <= 0 {
		return wg, nil
	}
	names := sensor.StreamNames(e.cfg.UpStreams)
	var grpcCli transport.Client
	if e.cfg.App == AppGRPC {
		mode := transport.ModeGRPC
		if e.cfg.Proto == ProtoH3 {
			mode = transport.ModeH3
		}
		grpcCli = transport.NewGRPCStreamClient(mode, e.cfg.Host, e.cfg.Port, e.shared)
	}
	for i, name := range names {
		var ch upChannel
		if grpcCli != nil {
			st, err := grpcCli.Open(ctx, name)
			if err != nil {
				return wg, fmt.Errorf("uplink open %s: %w", name, err)
			}
			ch = &grpcUp{st: st}
		} else {
			ch = &restUp{cli: e.client(), url: e.base + transport.RESTPath + name}
		}
		rec := e.up.Stream(name)
		gen := sensor.NewGenerator(i, name, 0, e.cfg.Seed)
		wg.Add(1)
		go func(ch upChannel, rec *stats.StreamRecorder, gen *sensor.Generator) {
			defer wg.Done()
			defer ch.close()
			items := make([]wire.Measurement, e.cfg.UpBatch)
			var seq uint64
			for ctx.Err() == nil && time.Now().Before(deadline) {
				seq++
				items = gen.Next(items, e.cfg.UpBatch)
				b := &wire.Batch{Stream: gen.Name(), BatchSeq: seq, SentUnixNs: time.Now().UnixNano(), Items: items}
				sent := time.Now()
				ack, n, err := ch.roundTrip(b)
				if err != nil {
					if ctx.Err() == nil && time.Now().Before(deadline) {
						rec.AddRecvError(err)
					}
					return
				}
				if sent.Before(measureStart) {
					continue
				}
				var srv time.Time
				if ack != nil && ack.RecvUnixNs != 0 {
					srv = time.Unix(0, ack.RecvUnixNs)
				}
				rec.Add(stats.Sample{SentAt: sent, AckedAt: time.Now(), ServerAt: srv, WireBytes: n, Items: len(b.Items)})
			}
		}(ch, rec, gen)
	}
	return wg, nil
}

// UplinkReport builds the telemetry section for the measurement window.
func (e *Edge) UplinkReport(start, end time.Time) stats.Report {
	return e.up.Build("uplink", "", nil, start, end)
}

// Close tears every connection down.
func (e *Edge) Close() {
	if e.sharedClose != nil {
		e.sharedClose()
	}
	e.closersMu.Lock()
	for _, c := range e.closers {
		c()
	}
	e.closersMu.Unlock()
}

// FetchLedger asks upstream which commands it issued in [from, to) and when
// each acknowledgement arrived. It uses a fresh HTTP/1.1 connection after the
// measurement, so it cannot disturb the run, and retries because the link is
// still lossy.
func FetchLedger(ctx context.Context, cfg EdgeConfig, from, to time.Time) (*LedgerResponse, error) {
	name := cfg.ServerName
	if name == "" {
		name = cfg.Host
	}
	tc, err := tlsutil.ClientConfig(cfg.CAPath, name, cfg.Insecure, []string{"http/1.1"})
	if err != nil {
		return nil, err
	}
	tr := &http.Transport{TLSClientConfig: tc, TLSNextProto: map[string]func(string, *tls.Conn) http.RoundTripper{}}
	defer tr.CloseIdleConnections()
	cli := &http.Client{Transport: tr, Timeout: 15 * time.Second}
	u := fmt.Sprintf("https://%s/v1/agents/%s/ledger?from=%d&to=%d",
		net.JoinHostPort(cfg.Host, strconv.Itoa(cfg.Port)), url.PathEscape(cfg.AgentID),
		from.UnixNano(), to.UnixNano())
	var last error
	for i := 0; i < 6 && ctx.Err() == nil; i++ {
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
		resp, err := cli.Do(req)
		if err != nil {
			last = err
			sleepCtx(ctx, time.Second)
			continue
		}
		var lr LedgerResponse
		err = json.NewDecoder(resp.Body).Decode(&lr)
		resp.Body.Close()
		if err == nil && resp.StatusCode == http.StatusOK {
			return &lr, nil
		}
		last = fmt.Errorf("status %d: %v", resp.StatusCode, err)
		sleepCtx(ctx, time.Second)
	}
	return nil, last
}
