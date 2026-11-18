// Package transport defines the common client/server contract that every
// protocol under test implements, and holds the four implementations.
//
// The contract is deliberately narrow: open N logical streams, and on each
// stream send a Batch and read back exactly one Ack. Every transport sees the
// same call pattern, the same generated data and the same "one batch in flight
// per stream unless --inflight says otherwise" pacing, so the only thing that
// varies between runs is how those logical streams are carried:
//
//	rest : HTTP/1.1 + TLS 1.3, JSON bodies, ONE TCP connection per stream.
//	       HTTP/1.1 cannot multiplex, so N streams means N connections. This is
//	       how a REST telemetry gateway is actually deployed.
//	grpc : gRPC over HTTP/2 + TLS 1.3. N streams multiplexed onto ONE TCP
//	       connection. This is where TCP head-of-line blocking appears: a lost
//	       segment stalls every stream, not just the one that lost data.
//	grpcm: the same gRPC over HTTP/2, but with one TCP connection PER stream.
//	       A control arm: it holds protocol, framing and codec fixed and varies
//	       only the connection count, which is otherwise confounded with the
//	       REST arm (HTTP/1.1 cannot multiplex, so it must open N connections).
//	       grpc vs grpcm isolates multiplexing; grpcm vs rest isolates JSON.
//	h3   : the same gRPC framing over HTTP/3, i.e. gRPC over QUIC by way of the
//	       standard HTTP/3 mapping. N streams on ONE QUIC connection, recovered
//	       independently.
//	h3m  : the same HTTP/3, but one QUIC connection PER stream. Together with
//	       grpc/grpcm this completes a 2x2 over {TCP, QUIC} x {1 conn, N conns},
//	       so the transport effect and the connection-count effect can be read
//	       off separately instead of being confounded.
//	qtun : the same gRPC framing placed directly on raw QUIC bidirectional
//	       streams, with no HTTP/3 or QPACK layer. Isolates how much of the
//	       HTTP/3 result is QUIC and how much is the HTTP mapping.
package transport

import (
	"context"
	"fmt"
	"strings"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

// Mode names a transport implementation.
type Mode string

// Supported modes.
const (
	ModeREST      Mode = "rest"
	ModeGRPC      Mode = "grpc"
	ModeGRPCMulti Mode = "grpcm"
	ModeH3        Mode = "h3"
	ModeH3Multi   Mode = "h3m"
	ModeQTun      Mode = "qtun"
)

// AllModes is the canonical ordering used by the sweep scripts and the figures.
var AllModes = []Mode{ModeREST, ModeGRPC, ModeGRPCMulti, ModeH3, ModeH3Multi, ModeQTun}

// DefaultPorts maps each mode to its default listening port. REST and gRPC are
// TCP; h3 and qtun are UDP.
// grpcm shares the gRPC port: it is the same protocol on the same listener,
// only the client's connection count differs. The sweep runs one mode per
// scenario, so the two never bind at the same time.
var DefaultPorts = map[Mode]int{
	ModeREST:      8443,
	ModeGRPC:      8444,
	ModeGRPCMulti: 8444,
	ModeH3:        8445,
	ModeH3Multi:   8445,
	ModeQTun:      8446,
}

// ParseMode validates a mode string.
func ParseMode(s string) (Mode, error) {
	m := Mode(strings.TrimSpace(strings.ToLower(s)))
	for _, k := range AllModes {
		if k == m {
			return m, nil
		}
	}
	return "", fmt.Errorf("transport: unknown mode %q (want one of rest, grpc, grpcm, h3, h3m, qtun)", s)
}

// Describe returns a one-line human description, used in logs and reports.
func (m Mode) Describe() string {
	switch m {
	case ModeREST:
		return "REST over HTTP/1.1 + TLS 1.3 (JSON, one TCP connection per stream)"
	case ModeGRPC:
		return "gRPC over HTTP/2 + TLS 1.3 (protobuf, all streams on one TCP connection)"
	case ModeGRPCMulti:
		return "gRPC over HTTP/2 + TLS 1.3 (protobuf, one TCP connection per stream)"
	case ModeH3:
		return "gRPC over QUIC via HTTP/3 (protobuf, all streams on one QUIC connection)"
	case ModeH3Multi:
		return "gRPC over QUIC via HTTP/3 (protobuf, one QUIC connection per stream)"
	case ModeQTun:
		return "gRPC framing on raw QUIC streams, no HTTP layer (protobuf)"
	default:
		return string(m)
	}
}

// Stream is one logical sensor-kind stream.
//
// Send and Recv are called from a single goroutine per stream, in that order,
// unless the caller runs in pipelined mode, in which case one goroutine sends
// and another receives. Implementations must tolerate that split.
type Stream interface {
	// Send transmits one batch and reports how many bytes it put on the wire,
	// framing included.
	Send(b *wire.Batch) (wireBytes int64, err error)
	// Recv blocks until the next acknowledgement arrives.
	Recv() (*wire.Ack, error)
	// CloseSend signals that no further batches will be sent.
	CloseSend() error
	// Close releases the stream.
	Close() error
}

// Client opens logical streams towards a Cloud Gateway.
type Client interface {
	// Open starts a logical stream identified by name.
	Open(ctx context.Context, name string) (Stream, error)
	// Close tears down the underlying connection(s).
	Close() error
}

// Handler processes one batch and produces the acknowledgement for it. It is
// invoked concurrently from many streams, so it must be safe for concurrent
// use.
type Handler func(b *wire.Batch) *wire.Ack

// Server is a running Cloud Gateway listener for one mode.
type Server interface {
	// Mode reports which transport this server speaks.
	Mode() Mode
	// Addr is the address actually bound.
	Addr() string
	// Serve blocks until the server is closed.
	Serve() error
	// Close stops the listener.
	Close() error
}

// alpnGRPC is the ALPN token used by the HTTP/2 gRPC transport.
const alpnGRPC = "h2"

// alpnH3 is the ALPN token required by the HTTP/3 mapping.
const alpnH3 = "h3"

// alpnQTun is a private ALPN token for the raw-QUIC tunnel. Using a distinct
// token keeps the tunnel from being mistaken for HTTP/3 by middleboxes or by a
// capture-analysis tool.
const alpnQTun = "test-rpcbench-qtun/1"

// ALPNFor returns the ALPN protocol list for a mode.
func ALPNFor(m Mode) []string {
	switch m {
	case ModeREST:
		return []string{"http/1.1"}
	case ModeGRPC, ModeGRPCMulti:
		return []string{alpnGRPC}
	case ModeH3, ModeH3Multi:
		return []string{alpnH3}
	case ModeQTun:
		return []string{alpnQTun}
	default:
		return nil
	}
}
