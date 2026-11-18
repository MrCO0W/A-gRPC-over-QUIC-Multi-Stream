// Command ctrlgw is the upstream side of Experiment C (downlink command
// delivery): it stands in for the Edge Master, where an agent decides on a
// command and has to get it to an Edge Agent.
//
// One process serves every delivery mechanism (REST short/long polling, REST +
// Server-Sent Events, gRPC bidirectional streaming) over HTTP/1.1 and HTTP/2
// on a TCP port and over HTTP/3 on the same UDP port. It also accepts uplink
// telemetry on the REST and gRPC ingest paths of rpcbench, so commands can
// share a connection with telemetry.
//
// Commands are generated per Edge Agent on a Poisson schedule that starts when
// that agent first makes contact; rate and size are chosen by the Edge Agent.
//
//	ctrlgw -certs ./certs -bind 0.0.0.0
package main

import (
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/control"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/tlsutil"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/transport"
)

func main() {
	certs := flag.String("certs", "./certs", "directory holding ca.pem, server.pem and server.key")
	bind := flag.String("bind", "0.0.0.0", "bind address")
	port := flag.Int("port", control.DefaultPort, "TCP port for HTTP/1.1 and HTTP/2, and UDP port for HTTP/3")
	noH3 := flag.Bool("no-h3", false, "do not start the HTTP/3 (UDP) listener")
	maxLife := flag.Duration("max-life", 15*time.Minute, "stop generating commands for an agent after this long")
	readyFile := flag.String("ready-file", "", "create this file once the listeners are bound")
	flag.Parse()

	tlsConf, err := tlsutil.ServerConfig(*certs, []string{"h2", "http/1.1"})
	if err != nil {
		log.Fatalf("ctrlgw: %v", err)
	}
	hub := control.NewHub(*maxLife)
	mux := control.NewMux(hub, transport.DefaultHandler)
	addr := net.JoinHostPort(*bind, strconv.Itoa(*port))
	srv, err := control.Listen(addr, tlsConf, mux, true, !*noH3)
	if err != nil {
		log.Fatalf("ctrlgw: %v", err)
	}
	srv.Serve()
	log.Printf("ctrlgw listening on %s (tcp: HTTP/1.1, HTTP/2; udp: HTTP/3=%v)", addr, !*noH3)

	if *readyFile != "" {
		if err := os.WriteFile(*readyFile, []byte(fmt.Sprintf("ready %d\n", os.Getpid())), 0o644); err == nil {
			defer os.Remove(*readyFile)
		}
	}
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	<-sig
	log.Print("ctrlgw shutting down")
	srv.Close()
}
