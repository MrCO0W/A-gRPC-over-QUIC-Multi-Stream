// Command gencerts writes the TLS material shared by the Edge and Cloud
// gateways: a self-signed CA and a server certificate.
//
// Run it once before the sweep. The Mininet hosts share a filesystem, so a
// single certs directory is visible to every node.
//
//	gencerts -dir ./certs -hosts 10.0.2.2,127.0.0.1,localhost
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/tlsutil"
)

func main() {
	dir := flag.String("dir", "./certs", "output directory for ca.pem, server.pem and server.key")
	hosts := flag.String("hosts", "127.0.0.1,localhost,10.0.2.2", "comma-separated SANs (IPs or DNS names) for the server certificate")
	days := flag.Int("days", 365, "certificate validity in days")
	force := flag.Bool("force", false, "overwrite an existing certificate directory")
	flag.Parse()

	caPath := filepath.Join(*dir, tlsutil.CAFile)
	if _, err := os.Stat(caPath); err == nil && !*force {
		fmt.Printf("certificates already present in %s (use -force to regenerate)\n", *dir)
		return
	}

	var sans []string
	for _, h := range strings.Split(*hosts, ",") {
		if h = strings.TrimSpace(h); h != "" {
			sans = append(sans, h)
		}
	}
	if len(sans) == 0 {
		log.Fatal("gencerts: -hosts must name at least one SAN")
	}

	if err := tlsutil.Generate(*dir, sans, time.Duration(*days)*24*time.Hour); err != nil {
		log.Fatalf("gencerts: %v", err)
	}
	fmt.Printf("wrote %s, %s and %s for SANs %s\n",
		tlsutil.CAFile, tlsutil.CertFile, tlsutil.KeyFile, strings.Join(sans, ", "))
}
