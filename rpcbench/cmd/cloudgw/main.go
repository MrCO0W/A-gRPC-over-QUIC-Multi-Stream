// Command cloudgw is the Cloud Gateway: the server side of the Edge<->Cloud
// hop under test.
//
// It serves every transport at once on separate ports, so one long-lived server
// process covers a whole sweep and the Mininet scenario only needs a single
// server workload line. Idle listeners cost nothing, and because only one arm
// is driven at a time there is no cross-talk between them.
//
//	cloudgw -certs ./certs -bind 0.0.0.0
//	cloudgw -certs ./certs -modes grpc,h3 -ready-file /tmp/cloudgw.ready
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/transport"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

func main() {
	certs := flag.String("certs", "./certs", "directory holding ca.pem, server.pem and server.key")
	bind := flag.String("bind", "0.0.0.0", "bind address")
	modes := flag.String("modes", "rest,grpc,grpcm,h3,h3m,qtun", "comma-separated transports to serve")
	restPort := flag.Int("rest-port", transport.DefaultPorts[transport.ModeREST], "REST (HTTP/1.1, TCP) port")
	grpcPort := flag.Int("grpc-port", transport.DefaultPorts[transport.ModeGRPC], "gRPC (HTTP/2, TCP) port")
	h3Port := flag.Int("h3-port", transport.DefaultPorts[transport.ModeH3], "gRPC over HTTP/3 (QUIC, UDP) port")
	qtunPort := flag.Int("qtun-port", transport.DefaultPorts[transport.ModeQTun], "raw QUIC tunnel (UDP) port")
	readyFile := flag.String("ready-file", "", "create this file once every listener is bound (useful for scripted runs)")
	verbose := flag.Bool("v", false, "log a line per accepted batch (debugging only; distorts timing)")
	flag.Parse()

	// Flag overrides, per mode. grpcm deliberately shares the gRPC port: it is
	// the same protocol on the same listener and only the client's connection
	// count differs.
	portOverride := map[transport.Mode]int{
		transport.ModeREST:      *restPort,
		transport.ModeGRPC:      *grpcPort,
		transport.ModeGRPCMulti: *grpcPort,
		transport.ModeH3:        *h3Port,
		transport.ModeH3Multi:   *h3Port,
		transport.ModeQTun:      *qtunPort,
	}

	// Fall back to the registered default for any mode missing above, and
	// refuse to start if that leaves us with no port. A missing entry used to
	// resolve to 0, which the kernel reads as "bind any free port" — the server
	// came up happily on an ephemeral port and every client got connection
	// refused, which is a confusing way to learn that a mode was added in one
	// place and not the other.
	portFor := func(m transport.Mode) int {
		if p, ok := portOverride[m]; ok && p > 0 {
			return p
		}
		if p, ok := transport.DefaultPorts[m]; ok && p > 0 {
			return p
		}
		log.Fatalf("cloudgw: no port configured for mode %q — add it to portOverride or transport.DefaultPorts", m)
		return 0
	}

	var batches, messages atomic.Int64
	handler := func(b *wire.Batch) *wire.Ack {
		batches.Add(1)
		messages.Add(int64(len(b.Items)))
		if *verbose {
			log.Printf("batch stream=%s seq=%d items=%d", b.Stream, b.BatchSeq, len(b.Items))
		}
		return transport.DefaultHandler(b)
	}

	var servers []transport.Server
	for _, name := range strings.Split(*modes, ",") {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		m, err := transport.ParseMode(name)
		if err != nil {
			log.Fatalf("cloudgw: %v", err)
		}
		srv, err := transport.NewServer(m, *bind, portFor(m), *certs, handler)
		if err != nil {
			log.Fatalf("cloudgw: %v", err)
		}
		servers = append(servers, srv)
		log.Printf("listening %-5s on %s  (%s)", m, srv.Addr(), m.Describe())
	}
	if len(servers) == 0 {
		log.Fatal("cloudgw: -modes selected no transports")
	}

	var wg sync.WaitGroup
	for _, srv := range servers {
		wg.Add(1)
		go func(s transport.Server) {
			defer wg.Done()
			if err := s.Serve(); err != nil {
				log.Printf("cloudgw: %s server stopped: %v", s.Mode(), err)
			}
		}(srv)
	}

	if *readyFile != "" {
		if err := os.WriteFile(*readyFile, []byte(fmt.Sprintf("ready %d\n", os.Getpid())), 0o644); err != nil {
			log.Printf("cloudgw: cannot write ready file: %v", err)
		} else {
			defer os.Remove(*readyFile)
		}
	}
	log.Printf("cloudgw ready (pid %d)", os.Getpid())

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	<-sig

	log.Printf("cloudgw shutting down after %d batches / %d measurements",
		batches.Load(), messages.Load())
	for _, s := range servers {
		_ = s.Close()
	}
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
	}
}
