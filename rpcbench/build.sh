#!/usr/bin/env bash
#
# Build rpcbench and generate its TLS material.
#
# Run on the Mininet host (Linux). The first build needs network access to fetch
# quic-go; afterwards it works offline.
#
#   ./build.sh              # deps + build + test + certs
#   ./build.sh build        # binaries only
#   ./build.sh certs        # certificates only
#   ./build.sh test         # unit tests
#   ./build.sh clean
#
# Override the certificate SANs when the topology uses different addresses:
#   HOSTS=127.0.0.1,localhost,10.0.2.2 ./build.sh certs
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

BIN="${BIN:-bin}"
CERTS="${CERTS:-certs}"
LDFLAGS="${LDFLAGS:--s -w}"
# 10.0.2.2 is s0 (Cloud Gateway) and 10.0.1.2 is c0 (Edge Gateway) in the
# generated Edge<->Cloud topology. Regenerate if you change the addressing.
HOSTS="${HOSTS:-127.0.0.1,localhost,10.0.2.2,10.0.1.2}"

deps() {
  echo "== go mod tidy"
  go mod tidy
}

build() {
  echo "== building into $BIN/"
  mkdir -p "$BIN"
  for c in cloudgw edgegw gencerts ctrlgw ctrledge; do
    CGO_ENABLED=0 go build -ldflags "$LDFLAGS" -o "$BIN/$c" "./cmd/$c"
    echo "   $BIN/$c"
  done
}

test_() {
  echo "== go vet"
  go vet ./...
  echo "== go test"
  go test ./...
}

certs() {
  [[ -x "$BIN/gencerts" ]] || build
  echo "== certificates for SANs: $HOSTS"
  "$BIN/gencerts" -dir "$CERTS" -hosts "$HOSTS"
}

clean() {
  rm -rf "$BIN" "$CERTS"
  echo "removed $BIN/ and $CERTS/"
}

case "${1:-all}" in
  all)   deps; build; test_; certs ;;
  deps)  deps ;;
  build) build ;;
  test)  test_ ;;
  certs) certs ;;
  clean) clean ;;
  *) echo "usage: $0 [all|deps|build|test|certs|clean]" >&2; exit 2 ;;
esac
