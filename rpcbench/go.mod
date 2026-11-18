module github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench

go 1.22

// quic-go is pinned deliberately. The HTTP/3 client API was renamed in later
// releases (http3.RoundTripper became http3.Transport), so `go get -u` will
// break the build. If you must upgrade, change internal/transport/grpch3.go to
// use http3.Transport and re-run the sweep end to end — never mix results
// produced by different quic-go versions in one figure.
require github.com/quic-go/quic-go v0.48.2

require (
	github.com/go-task/slim-sprig v0.0.0-20230315185526-52ccab3ef572 // indirect
	github.com/google/pprof v0.0.0-20210407192527-94a9f03dee38 // indirect
	github.com/onsi/ginkgo/v2 v2.9.5 // indirect
	github.com/quic-go/qpack v0.5.1 // indirect
	go.uber.org/mock v0.4.0 // indirect
	golang.org/x/crypto v0.26.0 // indirect
	golang.org/x/exp v0.0.0-20240506185415-9bf2ced13842 // indirect
	golang.org/x/mod v0.20.0 // indirect
	golang.org/x/net v0.28.0 // indirect
	golang.org/x/sync v0.8.0 // indirect
	golang.org/x/sys v0.24.0 // indirect
	golang.org/x/text v0.17.0 // indirect
	golang.org/x/tools v0.24.0 // indirect
)
