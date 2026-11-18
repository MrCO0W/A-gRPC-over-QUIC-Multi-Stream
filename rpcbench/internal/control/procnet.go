package control

import (
	"bufio"
	"os"
	"strconv"
	"strings"
)

// IfCounters are the kernel's byte and packet counters for one interface.
type IfCounters struct {
	RxBytes, RxPackets, TxBytes, TxPackets int64
}

// ReadNetDev parses /proc/net/dev. The file is per network namespace, so
// inside a Mininet host it shows that host's interfaces only; the counters
// include every header (Ethernet, IP, TCP/UDP, TLS, QUIC, HTTP), which is what
// "bytes on the link" should mean when comparing protocols.
func ReadNetDev() (map[string]IfCounters, error) {
	f, err := os.Open("/proc/net/dev")
	if err != nil {
		return nil, err
	}
	defer f.Close()
	out := map[string]IfCounters{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		name, rest, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		fs := strings.Fields(rest)
		if len(fs) < 16 {
			continue
		}
		n := func(i int) int64 { v, _ := strconv.ParseInt(fs[i], 10, 64); return v }
		out[strings.TrimSpace(name)] = IfCounters{RxBytes: n(0), RxPackets: n(1), TxBytes: n(8), TxPackets: n(9)}
	}
	return out, sc.Err()
}

// LinkDelta sums the counter change over the interfaces that carry traffic:
// every interface except lo, or lo alone when it is the only one (a loopback
// smoke test).
func LinkDelta(before, after map[string]IfCounters) (IfCounters, []string) {
	var names []string
	for k := range after {
		if k != "lo" {
			names = append(names, k)
		}
	}
	if len(names) == 0 {
		names = []string{"lo"}
	}
	var d IfCounters
	for _, k := range names {
		a, b := after[k], before[k]
		d.RxBytes += a.RxBytes - b.RxBytes
		d.RxPackets += a.RxPackets - b.RxPackets
		d.TxBytes += a.TxBytes - b.TxBytes
		d.TxPackets += a.TxPackets - b.TxPackets
	}
	return d, names
}
