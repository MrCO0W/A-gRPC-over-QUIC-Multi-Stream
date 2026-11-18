// Package tlsutil generates and loads the TLS material used by every
// transport.
//
// All four transports run over TLS 1.3 with the same certificate and the same
// cipher suites. That is not incidental: QUIC mandates TLS 1.3, so running the
// TCP-based transports in plaintext would hand them an unearned advantage and
// make the comparison meaningless.
package tlsutil

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"time"
)

// File names produced by Generate and expected by Load.
const (
	CAFile   = "ca.pem"
	CertFile = "server.pem"
	KeyFile  = "server.key"
)

// Generate writes a self-signed CA plus a server leaf certificate into dir.
// hosts are the SANs to embed; IP literals are recognised automatically.
func Generate(dir string, hosts []string, validFor time.Duration) error {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}

	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	caTmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "test rpcbench CA", Organization: []string{"test"}},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(validFor),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTmpl, caTmpl, &caKey.PublicKey, caKey)
	if err != nil {
		return err
	}
	caCert, err := x509.ParseCertificate(caDER)
	if err != nil {
		return err
	}

	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	leafTmpl := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: "rpcbench-cloudgw", Organization: []string{"test"}},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(validFor),
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
	}
	for _, h := range hosts {
		if ip := net.ParseIP(h); ip != nil {
			leafTmpl.IPAddresses = append(leafTmpl.IPAddresses, ip)
		} else {
			leafTmpl.DNSNames = append(leafTmpl.DNSNames, h)
		}
	}
	leafDER, err := x509.CreateCertificate(rand.Reader, leafTmpl, caCert, &leafKey.PublicKey, caKey)
	if err != nil {
		return err
	}
	leafKeyDER, err := x509.MarshalECPrivateKey(leafKey)
	if err != nil {
		return err
	}

	if err := writePEM(filepath.Join(dir, CAFile), "CERTIFICATE", caDER, 0o644); err != nil {
		return err
	}
	if err := writePEM(filepath.Join(dir, CertFile), "CERTIFICATE", leafDER, 0o644); err != nil {
		return err
	}
	return writePEM(filepath.Join(dir, KeyFile), "EC PRIVATE KEY", leafKeyDER, 0o600)
}

func writePEM(path, blockType string, der []byte, mode os.FileMode) error {
	buf := pem.EncodeToMemory(&pem.Block{Type: blockType, Bytes: der})
	if buf == nil {
		return fmt.Errorf("tlsutil: cannot encode %s", blockType)
	}
	return os.WriteFile(path, buf, mode)
}

// ServerConfig loads the leaf certificate from dir and returns a TLS 1.3 server
// config advertising the supplied ALPN protocols.
func ServerConfig(dir string, nextProtos []string) (*tls.Config, error) {
	cert, err := tls.LoadX509KeyPair(filepath.Join(dir, CertFile), filepath.Join(dir, KeyFile))
	if err != nil {
		return nil, fmt.Errorf("tlsutil: load server key pair: %w", err)
	}
	return &tls.Config{
		Certificates: []tls.Certificate{cert},
		MinVersion:   tls.VersionTLS13,
		MaxVersion:   tls.VersionTLS13,
		NextProtos:   nextProtos,
	}, nil
}

// ClientConfig builds a TLS 1.3 client config. When caPath is empty or
// insecure is true, server verification is disabled — acceptable inside a
// Mininet namespace and documented as such in the experiment write-up.
func ClientConfig(caPath, serverName string, insecure bool, nextProtos []string) (*tls.Config, error) {
	cfg := &tls.Config{
		MinVersion: tls.VersionTLS13,
		MaxVersion: tls.VersionTLS13,
		NextProtos: nextProtos,
		ServerName: serverName,
	}
	if insecure || caPath == "" {
		cfg.InsecureSkipVerify = true
		return cfg, nil
	}
	pemBytes, err := os.ReadFile(caPath)
	if err != nil {
		return nil, fmt.Errorf("tlsutil: read CA: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pemBytes) {
		return nil, fmt.Errorf("tlsutil: no certificate found in %s", caPath)
	}
	cfg.RootCAs = pool
	return cfg, nil
}
