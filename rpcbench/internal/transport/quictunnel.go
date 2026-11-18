package transport

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"

	"github.com/quic-go/quic-go"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/rpcframe"
)

// The raw-QUIC tunnel removes the HTTP/3 mapping and puts gRPC's
// length-prefixed framing straight onto QUIC bidirectional streams.
//
// Why both this and the HTTP/3 arm: HTTP/3 adds a frame layer, QPACK header
// compression and a control stream on top of QUIC. Measuring only HTTP/3 leaves
// it ambiguous whether an observed gain comes from QUIC's independent stream
// recovery or merely from a different HTTP implementation. Running the same
// framing directly on QUIC streams isolates the transport effect; the gap
// between the two arms is the cost of the HTTP/3 mapping.
//
// Stream preamble: the first framed message on a new stream carries the logical
// stream name as raw UTF-8. After that the stream carries Batch and Ack
// messages exactly as the HTTP arms do.

// maxStreamNameLen bounds the preamble so a malformed peer cannot force a large
// allocation.
const maxStreamNameLen = 128

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

type qtunServer struct {
	ln     *quic.Listener
	addr   string
	h      Handler
	cancel context.CancelFunc
	ctx    context.Context
	closed chan struct{}
	once   sync.Once
}

// NewQTunServer binds a raw QUIC listener.
func NewQTunServer(addr string, tlsConf *tls.Config, h Handler) (Server, error) {
	conf := tlsConf.Clone()
	conf.NextProtos = []string{alpnQTun}
	ln, err := quic.ListenAddr(addr, conf, QUICConfig())
	if err != nil {
		return nil, fmt.Errorf("qtun: listen %s: %w", addr, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	return &qtunServer{
		ln:     ln,
		addr:   ln.Addr().String(),
		h:      h,
		ctx:    ctx,
		cancel: cancel,
		closed: make(chan struct{}),
	}, nil
}

func (s *qtunServer) Mode() Mode   { return ModeQTun }
func (s *qtunServer) Addr() string { return s.addr }

func (s *qtunServer) Serve() error {
	for {
		conn, err := s.ln.Accept(s.ctx)
		if err != nil {
			select {
			case <-s.ctx.Done():
				return nil
			default:
			}
			if errors.Is(err, quic.ErrServerClosed) || errors.Is(err, net.ErrClosed) {
				return nil
			}
			return err
		}
		go s.serveConn(conn)
	}
}

func (s *qtunServer) serveConn(conn quic.Connection) {
	for {
		stream, err := conn.AcceptStream(s.ctx)
		if err != nil {
			return // peer went away or the server is shutting down
		}
		go func(st quic.Stream) {
			defer st.Close()
			name, err := readPreamble(st)
			if err != nil {
				st.CancelRead(0)
				return
			}
			_ = serveFramed(st, st, name, s.h)
		}(stream)
	}
}

func (s *qtunServer) Close() error {
	var err error
	s.once.Do(func() {
		s.cancel()
		err = s.ln.Close()
		close(s.closed)
	})
	return err
}

func readPreamble(r io.Reader) (string, error) {
	fr := rpcframe.NewReader(r)
	fr.SetMaxMessageSize(maxStreamNameLen)
	msg, err := fr.ReadMessage()
	if err != nil {
		return "", err
	}
	return string(msg), nil
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

type qtunClient struct {
	conn quic.Connection

	mu      sync.Mutex
	streams []*framedStream
}

// NewQTunClient dials one QUIC connection; every logical stream becomes a
// bidirectional stream on it.
func NewQTunClient(ctx context.Context, host string, port int, tlsConf *tls.Config) (Client, error) {
	conf := tlsConf.Clone()
	conf.NextProtos = []string{alpnQTun}
	addr := net.JoinHostPort(host, fmt.Sprint(port))
	conn, err := quic.DialAddr(ctx, addr, conf, QUICConfig())
	if err != nil {
		return nil, fmt.Errorf("qtun: dial %s: %w", addr, err)
	}
	return &qtunClient{conn: conn}, nil
}

func (c *qtunClient) Open(ctx context.Context, name string) (Stream, error) {
	if len(name) > maxStreamNameLen {
		return nil, fmt.Errorf("qtun: stream name %q is too long", name)
	}
	st, err := c.conn.OpenStreamSync(ctx)
	if err != nil {
		return nil, fmt.Errorf("qtun: open stream %q: %w", name, err)
	}
	// Preamble: the stream name, in one framed message.
	if err := rpcframe.NewWriter(st).WriteMessage([]byte(name)); err != nil {
		st.CancelRead(0)
		_ = st.Close()
		return nil, fmt.Errorf("qtun: write preamble for %q: %w", name, err)
	}

	fs := newFramedStream(name, st, st,
		func() error { return st.Close() },
		func() error {
			st.CancelRead(0)
			return st.Close()
		})
	c.mu.Lock()
	c.streams = append(c.streams, fs)
	c.mu.Unlock()
	return fs, nil
}

func (c *qtunClient) Close() error {
	c.mu.Lock()
	streams := append([]*framedStream(nil), c.streams...)
	c.mu.Unlock()
	for _, s := range streams {
		_ = s.Close()
	}
	return c.conn.CloseWithError(0, "done")
}
