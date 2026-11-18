package transport

import (
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"sync"
	"time"
)

// GRPCFullMethod is the gRPC path for Ingest.StreamBatches. Using the real
// path (and the real content type, framing and trailers) keeps the arm
// wire-compatible with gRPC tooling: a capture opened in Wireshark decodes as
// gRPC, and grpcurl can talk to the server given the .proto.
const GRPCFullMethod = "/test.telemetry.v1.Ingest/StreamBatches"

const grpcContentType = "application/grpc+proto"

// ---------------------------------------------------------------------------
// Server (HTTP/2 and HTTP/3 share this handler)
// ---------------------------------------------------------------------------

// grpcHandler returns an http.Handler implementing the bidirectional
// StreamBatches RPC. It is mounted by both the HTTP/2 and the HTTP/3 server, so
// the two arms differ only in the transport underneath.
func grpcHandler(h Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if r.URL.Path != GRPCFullMethod {
			http.Error(w, "unimplemented", http.StatusNotFound)
			return
		}
		hdr := w.Header()
		hdr.Set("Content-Type", grpcContentType)
		hdr.Set("Grpc-Accept-Encoding", "identity")
		w.WriteHeader(http.StatusOK)
		if f, ok := w.(http.Flusher); ok {
			// Send response headers before the first message so the client's
			// RoundTrip returns immediately and the stream is genuinely
			// full-duplex rather than request-then-response.
			f.Flush()
		}

		streamName := r.Header.Get("X-Rpcbench-Stream")
		err := serveFramed(r.Body, w, streamName, h)

		// gRPC trailers. http.TrailerPrefix lets us set them after the body has
		// been written without pre-declaring them.
		status, message := "0", ""
		if err != nil && err != io.EOF {
			status, message = "2", err.Error() // 2 == UNKNOWN
		}
		hdr.Set(http.TrailerPrefix+"grpc-status", status)
		if message != "" {
			hdr.Set(http.TrailerPrefix+"grpc-message", message)
		}
	})
}

type h2Server struct {
	srv  *http.Server
	ln   net.Listener
	addr string
	mode Mode
}

// NewGRPCServer binds a TLS HTTP/2 listener speaking the gRPC wire protocol.
//
// All logical streams from one client land on a single TCP connection, which is
// the configuration that exposes TCP head-of-line blocking: the kernel will not
// deliver bytes of stream B that arrived after a lost segment of stream A until
// that segment has been retransmitted.
func NewGRPCServer(addr string, tlsConf *tls.Config, h Handler) (Server, error) {
	return newGRPCServer(ModeGRPC, addr, tlsConf, h)
}

// newGRPCServer backs both the grpc and grpcm arms: one listener, and the mode
// is carried only so logs and reports name the arm that is actually running.
func newGRPCServer(mode Mode, addr string, tlsConf *tls.Config, h Handler) (Server, error) {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, fmt.Errorf("grpc: listen %s: %w", addr, err)
	}
	conf := tlsConf.Clone()
	conf.NextProtos = []string{alpnGRPC}
	s := &h2Server{
		srv: &http.Server{
			Handler:   grpcHandler(h),
			TLSConfig: conf,
			// No read/write deadlines: these are long-lived streams.
			ReadTimeout:  0,
			WriteTimeout: 0,
			IdleTimeout:  0,
		},
		ln:   ln,
		addr: ln.Addr().String(),
		mode: mode,
	}
	return s, nil
}

func (s *h2Server) Mode() Mode   { return s.mode }
func (s *h2Server) Addr() string { return s.addr }
func (s *h2Server) Serve() error {
	err := s.srv.ServeTLS(s.ln, "", "")
	if err == http.ErrServerClosed {
		return nil
	}
	return err
}
func (s *h2Server) Close() error { return s.srv.Close() }

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

type httpGRPCClient struct {
	mode   Mode
	url    string
	client *http.Client
	closer func()
	// newClient, when set, builds a fresh transport for every logical stream,
	// which is how the grpcm arm gets one TCP connection per stream.
	newClient func() (*http.Client, func())

	mu       sync.Mutex
	streams  []*framedStream
	perConns []func()
}

// NewGRPCClient dials the Cloud Gateway over HTTP/2. One *http.Transport means
// one pooled TCP connection for every logical stream, which is both what a real
// gRPC deployment does and what the experiment needs.
func NewGRPCClient(host string, port int, tlsConf *tls.Config) (Client, error) {
	cli, closer := newH2Transport(tlsConf)
	u := url.URL{Scheme: "https", Host: net.JoinHostPort(host, fmt.Sprint(port)), Path: GRPCFullMethod}
	return &httpGRPCClient{
		mode:   ModeGRPC,
		url:    u.String(),
		client: cli,
		closer: closer,
	}, nil
}

func newH2Transport(tlsConf *tls.Config) (*http.Client, func()) {
	conf := tlsConf.Clone()
	conf.NextProtos = []string{alpnGRPC}
	tr := &http.Transport{
		TLSClientConfig:     conf,
		ForceAttemptHTTP2:   true,
		DisableCompression:  true,
		TLSHandshakeTimeout: 20 * time.Second,
		IdleConnTimeout:     0,
	}
	return &http.Client{Transport: tr}, tr.CloseIdleConnections
}

// NewGRPCMultiClient dials the Cloud Gateway over HTTP/2 with a separate
// transport — and therefore a separate TCP connection — for every logical
// stream.
//
// This exists to disentangle two things the REST arm confounds. HTTP/1.1 has no
// multiplexing, so a REST gateway must open one connection per stream, and that
// hands it N independent congestion controllers. Comparing it against a
// single-connection gRPC arm measures connection count as much as it measures
// protocol. With grpcm in the matrix, grpc vs grpcm isolates the cost of
// multiplexing and grpcm vs rest isolates the cost of JSON over HTTP/1.1.
func NewGRPCMultiClient(host string, port int, tlsConf *tls.Config) (Client, error) {
	u := url.URL{Scheme: "https", Host: net.JoinHostPort(host, fmt.Sprint(port)), Path: GRPCFullMethod}
	return &httpGRPCClient{
		mode:      ModeGRPCMulti,
		url:       u.String(),
		newClient: func() (*http.Client, func()) { return newH2Transport(tlsConf) },
	}, nil
}

// newHTTPGRPCClient is shared with the HTTP/3 arm, which supplies its own
// RoundTripper and teardown function.
func newHTTPGRPCClient(mode Mode, host string, port int, rt http.RoundTripper, closer func()) *httpGRPCClient {
	u := url.URL{Scheme: "https", Host: net.JoinHostPort(host, fmt.Sprint(port)), Path: GRPCFullMethod}
	return &httpGRPCClient{
		mode:   mode,
		url:    u.String(),
		client: &http.Client{Transport: rt},
		closer: closer,
	}
}

func (c *httpGRPCClient) Open(ctx context.Context, name string) (Stream, error) {
	cli := c.client
	if c.newClient != nil {
		var closer func()
		cli, closer = c.newClient()
		c.mu.Lock()
		c.perConns = append(c.perConns, closer)
		c.mu.Unlock()
	}

	pr, pw := io.Pipe()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.url, pr)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", grpcContentType)
	req.Header.Set("TE", "trailers")
	req.Header.Set("Grpc-Accept-Encoding", "identity")
	req.Header.Set("X-Rpcbench-Stream", name)
	// A streaming body must not advertise a length, or the transport will
	// buffer the whole request before sending it.
	req.ContentLength = -1

	resp, err := cli.Do(req)
	if err != nil {
		pw.Close()
		return nil, fmt.Errorf("%s: open stream %q: %w", c.mode, name, err)
	}
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		pw.Close()
		return nil, fmt.Errorf("%s: open stream %q: status %d", c.mode, name, resp.StatusCode)
	}
	if got := resp.Proto; (c.mode == ModeGRPC || c.mode == ModeGRPCMulti) && got != "HTTP/2.0" {
		resp.Body.Close()
		pw.Close()
		return nil, fmt.Errorf("grpc: negotiated %s, expected HTTP/2.0 — the gRPC arm must not fall back to HTTP/1.1", got)
	}

	st := newFramedStream(name, pw, resp.Body,
		func() error { return pw.Close() },
		func() error {
			pw.Close()
			return resp.Body.Close()
		})
	c.mu.Lock()
	c.streams = append(c.streams, st)
	c.mu.Unlock()
	return st, nil
}

func (c *httpGRPCClient) Close() error {
	c.mu.Lock()
	streams := append([]*framedStream(nil), c.streams...)
	c.mu.Unlock()
	for _, s := range streams {
		_ = s.Close()
	}
	c.mu.Lock()
	perConns := append([]func(){}, c.perConns...)
	c.mu.Unlock()
	for _, closeConn := range perConns {
		closeConn()
	}
	if c.closer != nil {
		c.closer()
	}
	return nil
}
