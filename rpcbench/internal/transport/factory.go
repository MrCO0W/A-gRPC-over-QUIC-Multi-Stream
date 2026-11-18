package transport

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/tlsutil"
)

// NewServer builds the listener for one mode. certsDir must contain the files
// produced by `gencerts`.
func NewServer(m Mode, bind string, port int, certsDir string, h Handler) (Server, error) {
	tlsConf, err := tlsutil.ServerConfig(certsDir, ALPNFor(m))
	if err != nil {
		return nil, err
	}
	addr := net.JoinHostPort(bind, fmt.Sprint(port))
	switch m {
	case ModeREST:
		return NewRESTServer(addr, tlsConf, h)
	case ModeGRPC, ModeGRPCMulti:
		return newGRPCServer(m, addr, tlsConf, h)
	case ModeH3, ModeH3Multi:
		return NewH3Server(addr, tlsConf, h)
	case ModeQTun:
		return NewQTunServer(addr, tlsConf, h)
	default:
		return nil, fmt.Errorf("transport: no server for mode %q", m)
	}
}

// ClientOptions configures a client dial.
type ClientOptions struct {
	Host string
	Port int
	// CAPath is the PEM file produced by gencerts. Empty means skip
	// verification.
	CAPath string
	// ServerName overrides SNI; defaults to Host.
	ServerName string
	Insecure   bool
	// ConnsPerStream only affects the REST arm, where HTTP/1.1 cannot
	// multiplex and keeping k batches in flight requires k connections.
	ConnsPerStream int
}

// NewClient dials the Cloud Gateway for one mode.
func NewClient(ctx context.Context, m Mode, o ClientOptions) (Client, error) {
	name := o.ServerName
	if name == "" {
		name = o.Host
	}
	tlsConf, err := tlsutil.ClientConfig(o.CAPath, name, o.Insecure, ALPNFor(m))
	if err != nil {
		return nil, err
	}
	return newClientWithTLS(ctx, m, o, tlsConf)
}

func newClientWithTLS(ctx context.Context, m Mode, o ClientOptions, tlsConf *tls.Config) (Client, error) {
	switch m {
	case ModeREST:
		return NewRESTClient(o.Host, o.Port, tlsConf, o.ConnsPerStream)
	case ModeGRPC:
		return NewGRPCClient(o.Host, o.Port, tlsConf)
	case ModeGRPCMulti:
		return NewGRPCMultiClient(o.Host, o.Port, tlsConf)
	case ModeH3:
		return NewH3Client(o.Host, o.Port, tlsConf)
	case ModeH3Multi:
		return NewH3MultiClient(o.Host, o.Port, tlsConf)
	case ModeQTun:
		return NewQTunClient(ctx, o.Host, o.Port, tlsConf)
	default:
		return nil, fmt.Errorf("transport: no client for mode %q", m)
	}
}
