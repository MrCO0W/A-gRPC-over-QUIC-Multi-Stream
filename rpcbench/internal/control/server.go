package control

import (
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/quic-go/quic-go/http3"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/rpcframe"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/transport"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

// HTTP surface of the upstream side. One mux serves every protocol.
const (
	// SessionMethod is the gRPC path of Control.Session.
	SessionMethod = "/test.control.v1.Control/Session"
	// LongPollMax caps how long a long-poll request is held.
	LongPollMax = 25 * time.Second
	// sseHeartbeat keeps intermediaries from timing an idle event stream out.
	sseHeartbeat = 15 * time.Second
)

// PollResponse is the body of GET /v1/agents/{id}/commands.
type PollResponse struct {
	Commands     []wire.Command `json:"commands"`
	ServerUnixNs int64          `json:"server_unix_ns"`
}

// AckRequest is the body of POST /v1/agents/{id}/acks.
type AckRequest struct {
	Acks []wire.CommandAck `json:"acks"`
}

// LedgerResponse is the body of GET /v1/agents/{id}/ledger.
type LedgerResponse struct {
	Issued      []IssuedRecord `json:"issued"`
	AckMessages int            `json:"ack_messages"`
	Rate        float64        `json:"cmd_rate"`
	Bytes       int            `json:"cmd_bytes"`
}

// NewMux returns the handler for every endpoint. telemetry handles uplink
// batches on both the REST and the gRPC ingest paths.
func NewMux(hub *Hub, telemetry transport.Handler) *http.ServeMux {
	mux := http.NewServeMux()

	agentFor := func(r *http.Request) *Agent {
		q := r.URL.Query()
		rate, _ := strconv.ParseFloat(q.Get("rate"), 64)
		size := -1
		if s := q.Get("bytes"); s != "" {
			size, _ = strconv.Atoi(s)
		}
		seed, _ := strconv.ParseInt(q.Get("seed"), 10, 64)
		return hub.Agent(r.PathValue("id"), rate, size, seed)
	}

	// Short and long polling share one endpoint; wait=0 answers at once.
	mux.HandleFunc("GET /v1/agents/{id}/commands", func(w http.ResponseWriter, r *http.Request) {
		a := agentFor(r)
		after, _ := strconv.ParseUint(r.URL.Query().Get("after"), 10, 64)
		waitMs, _ := strconv.Atoi(r.URL.Query().Get("wait"))
		wait := time.Duration(waitMs) * time.Millisecond
		if wait > LongPollMax {
			wait = LongPollMax
		}
		ch := a.Wait()
		cmds := a.Since(after)
		if len(cmds) == 0 && wait > 0 {
			t := time.NewTimer(wait)
			select {
			case <-ch:
				cmds = a.Since(after)
			case <-t.C:
			case <-r.Context().Done():
				t.Stop()
				return
			}
			t.Stop()
		}
		if cmds == nil {
			cmds = []wire.Command{}
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		_ = json.NewEncoder(w).Encode(PollResponse{Commands: cmds, ServerUnixNs: time.Now().UnixNano()})
	})

	// Server-Sent Events.
	mux.HandleFunc("GET /v1/agents/{id}/events", func(w http.ResponseWriter, r *http.Request) {
		a := agentFor(r)
		var after uint64
		if v := r.Header.Get("Last-Event-ID"); v != "" {
			after, _ = strconv.ParseUint(v, 10, 64)
		}
		if v := r.URL.Query().Get("after"); v != "" {
			if n, err := strconv.ParseUint(v, 10, 64); err == nil && n > after {
				after = n
			}
		}
		fl, ok := w.(http.Flusher)
		if !ok {
			http.Error(w, "streaming unsupported", http.StatusInternalServerError)
			return
		}
		h := w.Header()
		h.Set("Content-Type", "text/event-stream")
		h.Set("Cache-Control", "no-store")
		w.WriteHeader(http.StatusOK)
		fl.Flush()
		hb := time.NewTicker(sseHeartbeat)
		defer hb.Stop()
		for {
			ch := a.Wait()
			for _, c := range a.Since(after) {
				js, _ := json.Marshal(c)
				if _, err := fmt.Fprintf(w, "id: %d\nevent: command\ndata: %s\n\n", c.Seq, js); err != nil {
					return
				}
				after = c.Seq
			}
			fl.Flush()
			select {
			case <-ch:
			case <-hb.C:
				if _, err := io.WriteString(w, ": hb\n\n"); err != nil {
					return
				}
				fl.Flush()
			case <-r.Context().Done():
				return
			}
		}
	})

	mux.HandleFunc("POST /v1/agents/{id}/acks", func(w http.ResponseWriter, r *http.Request) {
		a, ok := hub.Lookup(r.PathValue("id"))
		var req AckRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if ok {
			a.Ack(req.Acks)
		}
		w.WriteHeader(http.StatusNoContent)
	})

	// Ledger: what was issued in a window, and when each was acknowledged.
	// Read by ctrledge after the measurement, over a fresh connection.
	mux.HandleFunc("GET /v1/agents/{id}/ledger", func(w http.ResponseWriter, r *http.Request) {
		a, ok := hub.Lookup(r.PathValue("id"))
		if !ok {
			http.Error(w, "unknown agent", http.StatusNotFound)
			return
		}
		from, _ := strconv.ParseInt(r.URL.Query().Get("from"), 10, 64)
		to, _ := strconv.ParseInt(r.URL.Query().Get("to"), 10, 64)
		rows, acks := a.Ledger(from, to)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(LedgerResponse{Issued: rows, AckMessages: acks, Rate: a.rate, Bytes: a.size})
	})

	// gRPC Control.Session, bidirectional streaming.
	mux.Handle("POST "+SessionMethod, sessionHandler(hub))

	// Uplink telemetry, so that commands can share a connection with it.
	mux.Handle("POST "+transport.RESTPath+"{stream}", transport.RESTIngestHandler(telemetry))
	mux.Handle("POST "+transport.GRPCFullMethod, transport.GRPCIngestHandler(telemetry))
	return mux
}

func sessionHandler(hub *Hub) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.ProtoMajor < 2 {
			http.Error(w, "gRPC needs HTTP/2 or HTTP/3", http.StatusHTTPVersionNotSupported)
			return
		}
		hdr := w.Header()
		hdr.Set("Content-Type", "application/grpc+proto")
		hdr.Set("Grpc-Accept-Encoding", "identity")
		w.WriteHeader(http.StatusOK)
		fl, _ := w.(http.Flusher)
		if fl != nil {
			fl.Flush()
		}

		status, message := "0", ""
		defer func() {
			hdr.Set(http.TrailerPrefix+"grpc-status", status)
			if message != "" {
				hdr.Set(http.TrailerPrefix+"grpc-message", message)
			}
		}()

		fr := rpcframe.NewReader(r.Body)
		msg, err := fr.ReadMessage()
		if err != nil {
			status, message = "2", "no hello: "+err.Error()
			return
		}
		var first wire.EdgeMsg
		if err := first.Unmarshal(msg); err != nil || first.Hello == nil {
			status, message = "3", "first message must be Hello" // INVALID_ARGUMENT
			return
		}
		h := first.Hello
		a := hub.Agent(h.AgentID, h.CmdRate, int(h.CmdBytes), h.Seed)

		// Acks arrive on the request side while commands go out on the response
		// side, concurrently.
		done := make(chan struct{})
		go func() {
			defer close(done)
			for {
				m, err := fr.ReadMessage()
				if err != nil {
					return
				}
				var em wire.EdgeMsg
				if em.Unmarshal(m) == nil && em.Ack != nil {
					a.Ack([]wire.CommandAck{*em.Ack})
				}
			}
		}()

		fw := rpcframe.NewWriter(w)
		after := h.AfterSeq
		for {
			ch := a.Wait()
			for _, c := range a.Since(after) {
				if err := fw.WriteMessage(c.Marshal()); err != nil {
					return
				}
				after = c.Seq
			}
			select {
			case <-ch:
			case <-done:
				return
			case <-r.Context().Done():
				return
			}
		}
	})
}

// ---------------------------------------------------------------------------
// Listeners
// ---------------------------------------------------------------------------

// Servers runs the TCP listener (HTTP/1.1 and HTTP/2 by ALPN) and, optionally,
// the UDP listener (HTTP/3) on the same port number.
type Servers struct {
	tcp  *http.Server
	ln   net.Listener
	h3   *http3.Server
	udp  net.PacketConn
	wg   sync.WaitGroup
	Addr string
}

// Listen binds the listeners. tlsConf must carry the server certificate.
func Listen(addr string, tlsConf *tls.Config, handler http.Handler, tcp, udp bool) (*Servers, error) {
	s := &Servers{Addr: addr}
	if tcp {
		ln, err := net.Listen("tcp", addr)
		if err != nil {
			return nil, fmt.Errorf("ctrlgw: tcp listen %s: %w", addr, err)
		}
		conf := tlsConf.Clone()
		conf.NextProtos = []string{"h2", "http/1.1"}
		s.ln = ln
		s.tcp = &http.Server{Handler: handler, TLSConfig: conf}
	}
	if udp {
		ua, err := net.ResolveUDPAddr("udp", addr)
		if err != nil {
			return nil, err
		}
		pc, err := net.ListenUDP("udp", ua)
		if err != nil {
			return nil, fmt.Errorf("ctrlgw: udp listen %s: %w", addr, err)
		}
		conf := tlsConf.Clone()
		conf.NextProtos = []string{"h3"}
		s.udp = pc
		s.h3 = &http3.Server{
			Handler: handler, TLSConfig: conf, QUICConfig: transport.QUICConfig(),
			// quic-go v0.48.2 dereferences Logger without a nil check when it
			// cannot write trailers (a client that cancelled a stream), which
			// panics the whole server. A discard logger avoids that.
			Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
		}
	}
	return s, nil
}

// Serve starts every listener and returns at once.
func (s *Servers) Serve() {
	if s.tcp != nil {
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			if err := s.tcp.ServeTLS(s.ln, "", ""); err != nil && err != http.ErrServerClosed {
				log.Printf("ctrlgw: tcp server: %v", err)
			}
		}()
	}
	if s.h3 != nil {
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			if err := s.h3.Serve(s.udp); err != nil && err != http.ErrServerClosed && err != quic.ErrServerClosed {
				log.Printf("ctrlgw: h3 server: %v", err)
			}
		}()
	}
}

// Close stops every listener.
func (s *Servers) Close() {
	if s.tcp != nil {
		_ = s.tcp.Close()
	}
	if s.h3 != nil {
		_ = s.h3.Close()
		_ = s.udp.Close()
	}
	done := make(chan struct{})
	go func() { s.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
	}
}
