package transport

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

// RESTPath is the ingest endpoint; the stream name is the final path element.
const RESTPath = "/v1/ingest/"

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

type restServer struct {
	srv  *http.Server
	ln   net.Listener
	addr string
}

// NewRESTServer binds a TLS HTTP/1.1 listener. HTTP/2 is explicitly disabled
// (empty TLSNextProto plus an "http/1.1"-only ALPN list) so that the REST arm
// really is one request per connection at a time, which is what distinguishes
// it from the gRPC arm.
func NewRESTServer(addr string, tlsConf *tls.Config, h Handler) (Server, error) {
	mux := http.NewServeMux()
	mux.HandleFunc(RESTPath, func(w http.ResponseWriter, r *http.Request) {
		recvAt := time.Now()
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		stream := strings.TrimPrefix(r.URL.Path, RESTPath)
		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		var b wire.Batch
		if err := json.Unmarshal(body, &b); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if b.Stream == "" {
			b.Stream = stream
		}
		ack := h(&b)
		ack.RecvUnixNs = recvAt.UnixNano()
		ack.SrvUnixNs = time.Now().UnixNano()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(ack)
	})

	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, fmt.Errorf("rest: listen %s: %w", addr, err)
	}
	conf := tlsConf.Clone()
	conf.NextProtos = []string{"http/1.1"}
	s := &restServer{
		srv: &http.Server{
			Handler:      mux,
			TLSConfig:    conf,
			ReadTimeout:  0,
			WriteTimeout: 0,
			// Disable the automatic HTTP/2 upgrade.
			TLSNextProto: map[string]func(*http.Server, *tls.Conn, http.Handler){},
		},
		ln:   ln,
		addr: ln.Addr().String(),
	}
	return s, nil
}

func (s *restServer) Mode() Mode   { return ModeREST }
func (s *restServer) Addr() string { return s.addr }
func (s *restServer) Serve() error {
	err := s.srv.ServeTLS(s.ln, "", "")
	if err == http.ErrServerClosed {
		return nil
	}
	return err
}
func (s *restServer) Close() error { return s.srv.Close() }

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

type restClient struct {
	base     string
	tlsConf  *tls.Config
	conns    int
	mu       sync.Mutex
	children []*restStream
}

// NewRESTClient returns a client that will create connsPerStream dedicated
// HTTP/1.1 connections for every logical stream. connsPerStream mirrors the
// --inflight setting: HTTP/1.1 has no multiplexing, so the only way to keep k
// batches in flight is to open k connections.
func NewRESTClient(host string, port int, tlsConf *tls.Config, connsPerStream int) (Client, error) {
	if connsPerStream < 1 {
		connsPerStream = 1
	}
	u := url.URL{Scheme: "https", Host: net.JoinHostPort(host, fmt.Sprint(port))}
	return &restClient{base: u.String(), tlsConf: tlsConf, conns: connsPerStream}, nil
}

func (c *restClient) Open(ctx context.Context, name string) (Stream, error) {
	st := &restStream{
		name:  name,
		url:   c.base + RESTPath + name,
		acks:  make(chan ackOrErr, c.conns*4),
		slots: make(chan *http.Client, c.conns),
	}
	for i := 0; i < c.conns; i++ {
		tr := &http.Transport{
			TLSClientConfig:     c.tlsConf.Clone(),
			ForceAttemptHTTP2:   false,
			MaxConnsPerHost:     1,
			MaxIdleConnsPerHost: 1,
			IdleConnTimeout:     90 * time.Second,
			DisableCompression:  true,
			// A short handshake budget keeps a dead run from hanging the sweep.
			TLSHandshakeTimeout: 20 * time.Second,
		}
		st.slots <- &http.Client{Transport: tr}
		st.transports = append(st.transports, tr)
	}
	c.mu.Lock()
	c.children = append(c.children, st)
	c.mu.Unlock()

	// Warm the connections so that the TCP and TLS handshakes are not charged
	// to the first measured batch.
	warm := &wire.Batch{Stream: name}
	if _, err := st.Send(warm); err != nil {
		return nil, err
	}
	if _, err := st.Recv(); err != nil {
		return nil, err
	}
	return st, nil
}

func (c *restClient) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, st := range c.children {
		_ = st.Close()
	}
	return nil
}

type ackOrErr struct {
	ack *wire.Ack
	err error
}

type restStream struct {
	name       string
	url        string
	slots      chan *http.Client
	transports []*http.Transport
	acks       chan ackOrErr
	closeOnce  sync.Once
}

func (s *restStream) Send(b *wire.Batch) (int64, error) {
	body, err := json.Marshal(b)
	if err != nil {
		return 0, err
	}
	cli := <-s.slots
	go func() {
		defer func() { s.slots <- cli }()
		req, err := http.NewRequest(http.MethodPost, s.url, bytes.NewReader(body))
		if err != nil {
			s.acks <- ackOrErr{err: err}
			return
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := cli.Do(req)
		if err != nil {
			s.acks <- ackOrErr{err: err}
			return
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			io.Copy(io.Discard, resp.Body)
			s.acks <- ackOrErr{err: fmt.Errorf("rest: status %d", resp.StatusCode)}
			return
		}
		var ack wire.Ack
		if err := json.NewDecoder(resp.Body).Decode(&ack); err != nil {
			s.acks <- ackOrErr{err: err}
			return
		}
		s.acks <- ackOrErr{ack: &ack}
	}()
	// Bytes actually put on the wire for the request body. TLS record and HTTP
	// header overhead is excluded here for every transport alike, so the
	// goodput figures stay comparable; header cost shows up indirectly in the
	// latency and in the pcap-derived link utilisation.
	return int64(len(body)), nil
}

func (s *restStream) Recv() (*wire.Ack, error) {
	r, ok := <-s.acks
	if !ok {
		return nil, io.EOF
	}
	return r.ack, r.err
}

func (s *restStream) CloseSend() error { return nil }

func (s *restStream) Close() error {
	s.closeOnce.Do(func() {
		for _, tr := range s.transports {
			tr.CloseIdleConnections()
		}
	})
	return nil
}
