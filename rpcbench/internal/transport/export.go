package transport

// Exported entry points used by Experiment C (cmd/ctrlgw, cmd/ctrledge).
//
// Experiment C needs the telemetry ingest path to share an HTTP listener and,
// on HTTP/2 and HTTP/3, a single client connection with the control-plane
// endpoints. The existing servers and clients in this package each own their
// listener and connection, so these helpers expose the same handlers and the
// same gRPC stream client over a caller-supplied mux or *http.Client. Nothing
// here changes the behaviour of the rest/grpc/grpcm/h3/h3m/qtun arms.

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

// GRPCIngestHandler returns the Ingest.StreamBatches handler used by the grpc
// and h3 arms, for mounting at GRPCFullMethod on any mux.
func GRPCIngestHandler(h Handler) http.Handler { return grpcHandler(h) }

// RESTIngestHandler returns the JSON ingest handler used by the rest arm, for
// mounting at RESTPath on any mux. It is the same logic as NewRESTServer's
// handler, but it does not force HTTP/1.1: the protocol is whatever the
// listener negotiated.
func RESTIngestHandler(h Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
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
}

// NewGRPCStreamClient returns a Client whose logical streams are gRPC
// StreamBatches calls carried by cli. Passing the same *http.Client that the
// control plane uses puts telemetry and commands on one HTTP/2 or HTTP/3
// connection. mode must be ModeGRPC (HTTP/2) or ModeH3 (HTTP/3); it is used for
// protocol checks and log labels only. Close does not close cli.
func NewGRPCStreamClient(mode Mode, host string, port int, cli *http.Client) Client {
	u := url.URL{Scheme: "https", Host: net.JoinHostPort(host, fmt.Sprint(port)), Path: GRPCFullMethod}
	return &httpGRPCClient{mode: mode, url: u.String(), client: cli}
}
