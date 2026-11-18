package transport

import (
	"crypto/tls"
	"fmt"
	"net"
	"net/http"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/quic-go/quic-go/http3"
)

// QUICConfig returns the QUIC parameters shared by the h3 and qtun arms.
//
// The receive windows are raised well above the bandwidth-delay product of the
// emulated WAN link (50 Mbit/s x 40 ms RTT is about 250 KiB) so that flow
// control is never the binding constraint. If it were, the experiment would be
// measuring quic-go's default window rather than loss recovery.
func QUICConfig() *quic.Config {
	return &quic.Config{
		MaxIdleTimeout:                 60 * time.Second,
		KeepAlivePeriod:                15 * time.Second,
		MaxIncomingStreams:             1024,
		MaxIncomingUniStreams:          1024,
		InitialStreamReceiveWindow:     4 << 20,
		MaxStreamReceiveWindow:         16 << 20,
		InitialConnectionReceiveWindow: 8 << 20,
		MaxConnectionReceiveWindow:     64 << 20,
	}
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

type h3Server struct {
	srv  *http3.Server
	conn net.PacketConn
	addr string
}

// NewH3Server binds an HTTP/3 listener that serves the same gRPC handler as the
// HTTP/2 arm. This is "gRPC over QUIC" by way of the standard HTTP/3 mapping:
// gRPC framing and headers are unchanged, the carrier underneath becomes QUIC.
func NewH3Server(addr string, tlsConf *tls.Config, h Handler) (Server, error) {
	udpAddr, err := net.ResolveUDPAddr("udp", addr)
	if err != nil {
		return nil, fmt.Errorf("h3: resolve %s: %w", addr, err)
	}
	pc, err := net.ListenUDP("udp", udpAddr)
	if err != nil {
		return nil, fmt.Errorf("h3: listen %s: %w", addr, err)
	}
	conf := tlsConf.Clone()
	conf.NextProtos = []string{alpnH3}
	return &h3Server{
		srv: &http3.Server{
			Handler:    grpcHandler(h),
			TLSConfig:  conf,
			QUICConfig: QUICConfig(),
		},
		conn: pc,
		addr: pc.LocalAddr().String(),
	}, nil
}

func (s *h3Server) Mode() Mode   { return ModeH3 }
func (s *h3Server) Addr() string { return s.addr }
func (s *h3Server) Serve() error {
	err := s.srv.Serve(s.conn)
	if err == http.ErrServerClosed || err == quic.ErrServerClosed {
		return nil
	}
	return err
}
func (s *h3Server) Close() error {
	err := s.srv.Close()
	_ = s.conn.Close()
	return err
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

// NewH3Client dials the Cloud Gateway over HTTP/3. One RoundTripper means one
// QUIC connection carrying every logical stream — the direct counterpart of the
// single TCP connection used by the HTTP/2 arm, except that QUIC recovers each
// stream independently.
func NewH3Client(host string, port int, tlsConf *tls.Config) (Client, error) {
	rt, closer := newH3Transport(tlsConf)
	return newHTTPGRPCClient(ModeH3, host, port, rt, closer), nil
}

func newH3Transport(tlsConf *tls.Config) (*http3.RoundTripper, func()) {
	conf := tlsConf.Clone()
	conf.NextProtos = []string{alpnH3}
	rt := &http3.RoundTripper{
		TLSClientConfig: conf,
		QUICConfig:      QUICConfig(),
	}
	return rt, func() { _ = rt.Close() }
}

// NewH3MultiClient opens one QUIC connection per logical stream.
//
// This is the fourth cell of the design: grpc and h3 carry every stream on one
// connection, grpcm and h3m give each stream its own. Without it the sweep can
// only compare one-connection QUIC against eight-connection TCP, which mixes
// the transport effect with the connection-count effect — and in the
// link-saturating regime the connection count turns out to be the larger of the
// two, so the mix is not a detail.
func NewH3MultiClient(host string, port int, tlsConf *tls.Config) (Client, error) {
	c := newHTTPGRPCClient(ModeH3Multi, host, port, nil, nil)
	c.client = nil
	c.newClient = func() (*http.Client, func()) {
		rt, closer := newH3Transport(tlsConf)
		return &http.Client{Transport: rt}, closer
	}
	return c, nil
}
