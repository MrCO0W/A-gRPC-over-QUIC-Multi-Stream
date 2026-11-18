// Package control implements Experiment C: how fast a command decided upstream
// (Edge Master / agent) reaches the Edge Agent, under each way of carrying it.
//
// The upstream side (ctrlgw) generates commands on a Poisson schedule, one
// independent schedule per Edge Agent, and offers them through four delivery
// mechanisms:
//
//	poll  : REST short polling. The Edge Agent asks "anything new?" every
//	        -poll-interval and gets an immediate (possibly empty) answer.
//	lpoll : REST long polling. The request is held until a command exists or
//	        a timeout passes, then the Edge Agent asks again at once.
//	sse   : REST + Server-Sent Events. One long-lived GET whose response body
//	        is an event stream; upstream writes a command as soon as it exists.
//	grpc  : gRPC bidirectional streaming (Control.Session). The Edge Agent opens
//	        the call once and upstream pushes commands on it; acknowledgements
//	        travel back on the same call.
//
// Each mechanism runs over HTTP/1.1, HTTP/2 or HTTP/3 (gRPC over HTTP/2 or
// HTTP/3 only), so application pattern and transport can be varied
// independently. The measured quantity is the one-way delay from the moment a
// command is issued upstream to the moment the Edge Agent holds it. Mininet
// hosts share the host kernel clock, so the two timestamps are comparable.
package control

import (
	"fmt"
	"math/rand"
	"sync"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

// Generation defaults, used when the Edge Agent does not ask for others.
const (
	DefaultCmdRate  = 5.0 // commands per second, Poisson
	DefaultCmdBytes = 64  // opaque padding per command
	// ValidFor is the validity window stamped on every command. It has no
	// effect on delivery; it is there so the message has the shape the thesis
	// architecture describes (target, operation, validity window).
	ValidFor = 5 * time.Second
)

// Hub owns one Agent per Edge Agent id.
type Hub struct {
	mu      sync.Mutex
	agents  map[string]*Agent
	maxLife time.Duration
}

// NewHub returns a Hub. Each agent's generator stops after maxLife, which only
// bounds memory if a client disappears without the server being restarted.
func NewHub(maxLife time.Duration) *Hub {
	if maxLife <= 0 {
		maxLife = 15 * time.Minute
	}
	return &Hub{agents: map[string]*Agent{}, maxLife: maxLife}
}

// Agent returns the agent for id, creating it - and starting its command
// generator - on first contact. Later calls ignore rate, size and seed.
func (h *Hub) Agent(id string, rate float64, size int, seed int64) *Agent {
	h.mu.Lock()
	defer h.mu.Unlock()
	if a, ok := h.agents[id]; ok {
		return a
	}
	if rate <= 0 {
		rate = DefaultCmdRate
	}
	if size < 0 {
		size = DefaultCmdBytes
	}
	a := &Agent{
		ID:     id,
		rate:   rate,
		size:   size,
		notify: make(chan struct{}),
		acks:   map[uint64]int64{},
		start:  time.Now(),
	}
	h.agents[id] = a
	go a.generate(seed, h.maxLife)
	return a
}

// Lookup returns an existing agent without creating one.
func (h *Hub) Lookup(id string) (*Agent, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	a, ok := h.agents[id]
	return a, ok
}

// Agent is the command source for one Edge Agent.
type Agent struct {
	ID    string
	rate  float64
	size  int
	start time.Time

	mu     sync.Mutex
	cmds   []wire.Command // cmds[i].Seq == i+1
	notify chan struct{}  // closed and replaced whenever a command is issued
	acks   map[uint64]int64
	ackMsg int // ack messages / requests received
}

func (a *Agent) generate(seed int64, maxLife time.Duration) {
	rng := rand.New(rand.NewSource(seed))
	deadline := a.start.Add(maxLife)
	next := time.Now()
	for {
		// Exponential inter-arrival times: decisions do not line up with any
		// polling period, which would otherwise make short polling look better
		// or worse than it is depending on the phase.
		next = next.Add(time.Duration(rng.ExpFloat64() / a.rate * float64(time.Second)))
		if next.After(deadline) {
			return
		}
		time.Sleep(time.Until(next))
		a.issue(rng)
	}
}

func (a *Agent) issue(rng *rand.Rand) {
	now := time.Now()
	a.mu.Lock()
	seq := uint64(len(a.cmds) + 1)
	c := wire.Command{
		Seq:          seq,
		Device:       fmt.Sprintf("fan-%02d", rng.Intn(16)),
		Op:           "set_speed",
		Value:        float64(rng.Intn(101)) / 100,
		IssuedUnixNs: now.UnixNano(),
		ValidUntilNs: now.Add(ValidFor).UnixNano(),
	}
	if a.size > 0 {
		p := make([]byte, a.size)
		for i := range p {
			p[i] = byte(seq + uint64(i))
		}
		c.Payload = p
	}
	a.cmds = append(a.cmds, c)
	ch := a.notify
	a.notify = make(chan struct{})
	a.mu.Unlock()
	close(ch)
}

// Wait returns a channel that is closed when the next command is issued.
// Take it BEFORE calling Since, so a command issued in between is not missed.
func (a *Agent) Wait() <-chan struct{} {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.notify
}

// Since returns the commands with seq > after, oldest first.
func (a *Agent) Since(after uint64) []wire.Command {
	a.mu.Lock()
	defer a.mu.Unlock()
	if after >= uint64(len(a.cmds)) {
		return nil
	}
	out := make([]wire.Command, len(a.cmds)-int(after))
	copy(out, a.cmds[after:])
	return out
}

// Ack records acknowledgements. The first arrival per seq wins.
func (a *Agent) Ack(acks []wire.CommandAck) {
	now := time.Now().UnixNano()
	a.mu.Lock()
	a.ackMsg++
	for _, k := range acks {
		if _, ok := a.acks[k.Seq]; !ok {
			a.acks[k.Seq] = now
		}
	}
	a.mu.Unlock()
}

// IssuedRecord is one row of the issued-command ledger.
type IssuedRecord struct {
	Seq          uint64 `json:"seq"`
	IssuedUnixNs int64  `json:"issued_unix_ns"`
	// AckUnixNs is when upstream received the Edge Agent's acknowledgement,
	// zero if none arrived. AckUnixNs - IssuedUnixNs is the full
	// decision-to-confirmation loop.
	AckUnixNs int64 `json:"ack_unix_ns,omitempty"`
}

// Ledger returns every command issued in [fromNs, toNs).
func (a *Agent) Ledger(fromNs, toNs int64) (rows []IssuedRecord, ackMessages int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for _, c := range a.cmds {
		if c.IssuedUnixNs < fromNs || (toNs > 0 && c.IssuedUnixNs >= toNs) {
			continue
		}
		rows = append(rows, IssuedRecord{Seq: c.Seq, IssuedUnixNs: c.IssuedUnixNs, AckUnixNs: a.acks[c.Seq]})
	}
	return rows, a.ackMsg
}
