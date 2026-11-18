package wire

// Hand-written, byte-compatible implementation of proto/control.proto
// (Experiment C, downlink command delivery). Same conventions as the
// telemetry messages in wire.go: proto3 encoding, zero values skipped.

import (
	"errors"
	"math"
)

// Command mirrors test.control.v1.Command.
type Command struct {
	Seq          uint64  `json:"seq"`
	Device       string  `json:"device,omitempty"`
	Op           string  `json:"op,omitempty"`
	Value        float64 `json:"value,omitempty"`
	IssuedUnixNs int64   `json:"issued_unix_ns"`
	ValidUntilNs int64   `json:"valid_until_ns,omitempty"`
	Payload      []byte  `json:"payload,omitempty"`
}

// CommandAck mirrors test.control.v1.CommandAck.
type CommandAck struct {
	Seq        uint64 `json:"seq"`
	RecvUnixNs int64  `json:"recv_unix_ns,omitempty"`
	DoneUnixNs int64  `json:"done_unix_ns,omitempty"`
}

// Hello mirrors test.control.v1.Hello.
type Hello struct {
	AgentID  string  `json:"agent_id"`
	AfterSeq uint64  `json:"after_seq,omitempty"`
	CmdRate  float64 `json:"cmd_rate,omitempty"`
	CmdBytes uint32  `json:"cmd_bytes,omitempty"`
	Seed     int64   `json:"seed,omitempty"`
}

// EdgeMsg mirrors test.control.v1.EdgeMsg (a oneof). Exactly one of Hello or
// Ack is set.
type EdgeMsg struct {
	Hello *Hello
	Ack   *CommandAck
}

// ErrEmptyEdgeMsg is returned when an EdgeMsg carries neither branch.
var ErrEmptyEdgeMsg = errors.New("wire: EdgeMsg has no body")

// ---------------------------------------------------------------------------
// Command
// ---------------------------------------------------------------------------

// Marshal encodes the Command.
func (c *Command) Marshal() []byte {
	out := make([]byte, 0, 48+len(c.Payload)+len(c.Device)+len(c.Op))
	out = appendUint64(out, 1, c.Seq)
	out = appendString(out, 2, c.Device)
	out = appendString(out, 3, c.Op)
	out = appendDouble(out, 4, c.Value)
	out = appendInt64(out, 5, c.IssuedUnixNs)
	out = appendInt64(out, 6, c.ValidUntilNs)
	out = appendBytes(out, 7, c.Payload)
	return out
}

// Unmarshal decodes a Command in place.
func (c *Command) Unmarshal(data []byte) error {
	*c = Command{}
	r := &reader{buf: data}
	for !r.eof() {
		key, err := r.varint()
		if err != nil {
			return err
		}
		field, wt := int(key>>3), int(key&7)
		switch {
		case field == 1 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			c.Seq = u
		case field == 2 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			c.Device = string(s)
		case field == 3 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			c.Op = string(s)
		case field == 4 && wt == wtFixed64:
			u, err := r.fixed64()
			if err != nil {
				return err
			}
			c.Value = math.Float64frombits(u)
		case field == 5 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			c.IssuedUnixNs = int64(u)
		case field == 6 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			c.ValidUntilNs = int64(u)
		case field == 7 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			c.Payload = append([]byte(nil), s...)
		default:
			if err := r.skip(wt); err != nil {
				return err
			}
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// CommandAck
// ---------------------------------------------------------------------------

// Marshal encodes the CommandAck.
func (a *CommandAck) Marshal() []byte {
	out := make([]byte, 0, 32)
	out = appendUint64(out, 1, a.Seq)
	out = appendInt64(out, 2, a.RecvUnixNs)
	out = appendInt64(out, 3, a.DoneUnixNs)
	return out
}

// Unmarshal decodes a CommandAck in place.
func (a *CommandAck) Unmarshal(data []byte) error {
	*a = CommandAck{}
	r := &reader{buf: data}
	for !r.eof() {
		key, err := r.varint()
		if err != nil {
			return err
		}
		field, wt := int(key>>3), int(key&7)
		switch {
		case field == 1 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			a.Seq = u
		case field == 2 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			a.RecvUnixNs = int64(u)
		case field == 3 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			a.DoneUnixNs = int64(u)
		default:
			if err := r.skip(wt); err != nil {
				return err
			}
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// Hello
// ---------------------------------------------------------------------------

func (h *Hello) appendTo(b []byte) []byte {
	b = appendString(b, 1, h.AgentID)
	b = appendUint64(b, 2, h.AfterSeq)
	b = appendDouble(b, 3, h.CmdRate)
	b = appendUint32(b, 4, h.CmdBytes)
	b = appendInt64(b, 5, h.Seed)
	return b
}

func (h *Hello) unmarshal(data []byte) error {
	*h = Hello{}
	r := &reader{buf: data}
	for !r.eof() {
		key, err := r.varint()
		if err != nil {
			return err
		}
		field, wt := int(key>>3), int(key&7)
		switch {
		case field == 1 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			h.AgentID = string(s)
		case field == 2 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			h.AfterSeq = u
		case field == 3 && wt == wtFixed64:
			u, err := r.fixed64()
			if err != nil {
				return err
			}
			h.CmdRate = math.Float64frombits(u)
		case field == 4 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			h.CmdBytes = uint32(u)
		case field == 5 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			h.Seed = int64(u)
		default:
			if err := r.skip(wt); err != nil {
				return err
			}
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// EdgeMsg
// ---------------------------------------------------------------------------

// Marshal encodes the EdgeMsg.
func (m *EdgeMsg) Marshal() []byte {
	out := make([]byte, 0, 64)
	var scratch []byte
	switch {
	case m.Hello != nil:
		scratch = m.Hello.appendTo(nil)
		out = appendTag(out, 1, wtBytes)
	case m.Ack != nil:
		scratch = m.Ack.Marshal()
		out = appendTag(out, 2, wtBytes)
	default:
		return out
	}
	out = appendVarint(out, uint64(len(scratch)))
	return append(out, scratch...)
}

// Unmarshal decodes an EdgeMsg in place.
func (m *EdgeMsg) Unmarshal(data []byte) error {
	*m = EdgeMsg{}
	r := &reader{buf: data}
	for !r.eof() {
		key, err := r.varint()
		if err != nil {
			return err
		}
		field, wt := int(key>>3), int(key&7)
		switch {
		case field == 1 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			h := &Hello{}
			if err := h.unmarshal(s); err != nil {
				return err
			}
			m.Hello, m.Ack = h, nil
		case field == 2 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			a := &CommandAck{}
			if err := a.Unmarshal(s); err != nil {
				return err
			}
			m.Ack, m.Hello = a, nil
		default:
			if err := r.skip(wt); err != nil {
				return err
			}
		}
	}
	if m.Hello == nil && m.Ack == nil {
		return ErrEmptyEdgeMsg
	}
	return nil
}
