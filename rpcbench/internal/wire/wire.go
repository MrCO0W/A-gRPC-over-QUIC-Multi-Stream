// Package wire is a hand-written, byte-compatible implementation of the
// protobuf messages defined in proto/telemetry.proto.
//
// Why hand-written: the benchmark must build on a bare Mininet host with
// nothing but the Go toolchain. Depending on protoc + protoc-gen-go would add
// a code-generation step that is easy to get wrong on an experiment machine and
// that contributes nothing to what we are measuring. The encoding produced here
// is standard proto3 (same tags, same wire types), so a generated stub can be
// dropped in later without changing a single byte on the network.
package wire

import (
	"encoding/binary"
	"errors"
	"fmt"
	"math"
)

// Wire types (proto3).
const (
	wtVarint  = 0
	wtFixed64 = 1
	wtBytes   = 2
	wtFixed32 = 5
)

var (
	ErrTruncated = errors.New("wire: truncated message")
	ErrWireType  = errors.New("wire: unexpected wire type")
)

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

// Measurement mirrors test.telemetry.v1.Measurement.
type Measurement struct {
	SensorID string  `json:"sensor_id,omitempty"`
	Kind     string  `json:"kind,omitempty"`
	Value    float64 `json:"value,omitempty"`
	Unit     string  `json:"unit,omitempty"`
	TsUnixNs int64   `json:"ts_unix_ns,omitempty"`
	Seq      uint64  `json:"seq,omitempty"`
	Payload  []byte  `json:"payload,omitempty"`
}

// Batch mirrors test.telemetry.v1.Batch.
type Batch struct {
	Stream     string        `json:"stream,omitempty"`
	BatchSeq   uint64        `json:"batch_seq,omitempty"`
	SentUnixNs int64         `json:"sent_unix_ns,omitempty"`
	Items      []Measurement `json:"items,omitempty"`
}

// Ack mirrors test.telemetry.v1.Ack.
type Ack struct {
	Stream     string `json:"stream,omitempty"`
	BatchSeq   uint64 `json:"batch_seq,omitempty"`
	RecvUnixNs int64  `json:"recv_unix_ns,omitempty"`
	SrvUnixNs  int64  `json:"srv_unix_ns,omitempty"`
	Accepted   uint32 `json:"accepted,omitempty"`
}

// ---------------------------------------------------------------------------
// Primitive encoders
// ---------------------------------------------------------------------------

func appendVarint(b []byte, v uint64) []byte {
	for v >= 0x80 {
		b = append(b, byte(v)|0x80)
		v >>= 7
	}
	return append(b, byte(v))
}

func appendTag(b []byte, field int, wt int) []byte {
	return appendVarint(b, uint64(field)<<3|uint64(wt))
}

func appendString(b []byte, field int, s string) []byte {
	if s == "" {
		return b // proto3: skip zero value
	}
	b = appendTag(b, field, wtBytes)
	b = appendVarint(b, uint64(len(s)))
	return append(b, s...)
}

func appendBytes(b []byte, field int, v []byte) []byte {
	if len(v) == 0 {
		return b
	}
	b = appendTag(b, field, wtBytes)
	b = appendVarint(b, uint64(len(v)))
	return append(b, v...)
}

func appendUint64(b []byte, field int, v uint64) []byte {
	if v == 0 {
		return b
	}
	b = appendTag(b, field, wtVarint)
	return appendVarint(b, v)
}

func appendUint32(b []byte, field int, v uint32) []byte {
	if v == 0 {
		return b
	}
	b = appendTag(b, field, wtVarint)
	return appendVarint(b, uint64(v))
}

// proto3 int64 uses plain varint over the two's-complement bit pattern.
func appendInt64(b []byte, field int, v int64) []byte {
	if v == 0 {
		return b
	}
	b = appendTag(b, field, wtVarint)
	return appendVarint(b, uint64(v))
}

func appendDouble(b []byte, field int, v float64) []byte {
	if v == 0 {
		return b
	}
	b = appendTag(b, field, wtFixed64)
	var tmp [8]byte
	binary.LittleEndian.PutUint64(tmp[:], math.Float64bits(v))
	return append(b, tmp[:]...)
}

// ---------------------------------------------------------------------------
// Primitive decoders
// ---------------------------------------------------------------------------

type reader struct {
	buf []byte
	pos int
}

func (r *reader) eof() bool { return r.pos >= len(r.buf) }

func (r *reader) varint() (uint64, error) {
	var v uint64
	var shift uint
	for {
		if r.pos >= len(r.buf) {
			return 0, ErrTruncated
		}
		c := r.buf[r.pos]
		r.pos++
		v |= uint64(c&0x7f) << shift
		if c < 0x80 {
			return v, nil
		}
		shift += 7
		if shift > 63 {
			return 0, errors.New("wire: varint overflow")
		}
	}
}

func (r *reader) fixed64() (uint64, error) {
	if r.pos+8 > len(r.buf) {
		return 0, ErrTruncated
	}
	v := binary.LittleEndian.Uint64(r.buf[r.pos:])
	r.pos += 8
	return v, nil
}

func (r *reader) slice() ([]byte, error) {
	n, err := r.varint()
	if err != nil {
		return nil, err
	}
	if uint64(r.pos)+n > uint64(len(r.buf)) {
		return nil, ErrTruncated
	}
	out := r.buf[r.pos : r.pos+int(n)]
	r.pos += int(n)
	return out, nil
}

// skip advances past a field whose tag has already been consumed.
func (r *reader) skip(wt int) error {
	switch wt {
	case wtVarint:
		_, err := r.varint()
		return err
	case wtFixed64:
		_, err := r.fixed64()
		return err
	case wtBytes:
		_, err := r.slice()
		return err
	case wtFixed32:
		if r.pos+4 > len(r.buf) {
			return ErrTruncated
		}
		r.pos += 4
		return nil
	default:
		return fmt.Errorf("%w: %d", ErrWireType, wt)
	}
}

// ---------------------------------------------------------------------------
// Measurement
// ---------------------------------------------------------------------------

// AppendTo appends the encoded Measurement to b (as an embedded message body).
func (m *Measurement) AppendTo(b []byte) []byte {
	b = appendString(b, 1, m.SensorID)
	b = appendString(b, 2, m.Kind)
	b = appendDouble(b, 3, m.Value)
	b = appendString(b, 4, m.Unit)
	b = appendInt64(b, 5, m.TsUnixNs)
	b = appendUint64(b, 6, m.Seq)
	b = appendBytes(b, 7, m.Payload)
	return b
}

func (m *Measurement) unmarshal(data []byte) error {
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
			m.SensorID = string(s)
		case field == 2 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			m.Kind = string(s)
		case field == 3 && wt == wtFixed64:
			u, err := r.fixed64()
			if err != nil {
				return err
			}
			m.Value = math.Float64frombits(u)
		case field == 4 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			m.Unit = string(s)
		case field == 5 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			m.TsUnixNs = int64(u)
		case field == 6 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			m.Seq = u
		case field == 7 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			m.Payload = append([]byte(nil), s...)
		default:
			if err := r.skip(wt); err != nil {
				return err
			}
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// Batch
// ---------------------------------------------------------------------------

// Marshal encodes the Batch.
func (b *Batch) Marshal() []byte {
	// Rough pre-size: header + ~96B per item + padding.
	est := 32 + len(b.Items)*96
	for i := range b.Items {
		est += len(b.Items[i].Payload)
	}
	out := make([]byte, 0, est)
	out = appendString(out, 1, b.Stream)
	out = appendUint64(out, 2, b.BatchSeq)
	out = appendInt64(out, 3, b.SentUnixNs)

	// Each repeated message field is encoded canonically: tag, minimal varint
	// length, body. The item body is built in a scratch buffer that is reused
	// across items so the per-batch allocation count stays flat.
	scratch := make([]byte, 0, 256)
	for i := range b.Items {
		scratch = b.Items[i].AppendTo(scratch[:0])
		out = appendTag(out, 4, wtBytes)
		out = appendVarint(out, uint64(len(scratch)))
		out = append(out, scratch...)
	}
	return out
}

// Unmarshal decodes a Batch in place.
func (b *Batch) Unmarshal(data []byte) error {
	*b = Batch{}
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
			b.Stream = string(s)
		case field == 2 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			b.BatchSeq = u
		case field == 3 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			b.SentUnixNs = int64(u)
		case field == 4 && wt == wtBytes:
			s, err := r.slice()
			if err != nil {
				return err
			}
			var m Measurement
			if err := m.unmarshal(s); err != nil {
				return err
			}
			b.Items = append(b.Items, m)
		default:
			if err := r.skip(wt); err != nil {
				return err
			}
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// Ack
// ---------------------------------------------------------------------------

// Marshal encodes the Ack.
func (a *Ack) Marshal() []byte {
	out := make([]byte, 0, 64)
	out = appendString(out, 1, a.Stream)
	out = appendUint64(out, 2, a.BatchSeq)
	out = appendInt64(out, 3, a.RecvUnixNs)
	out = appendInt64(out, 4, a.SrvUnixNs)
	out = appendUint32(out, 5, a.Accepted)
	return out
}

// Unmarshal decodes an Ack in place.
func (a *Ack) Unmarshal(data []byte) error {
	*a = Ack{}
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
			a.Stream = string(s)
		case field == 2 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			a.BatchSeq = u
		case field == 3 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			a.RecvUnixNs = int64(u)
		case field == 4 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			a.SrvUnixNs = int64(u)
		case field == 5 && wt == wtVarint:
			u, err := r.varint()
			if err != nil {
				return err
			}
			a.Accepted = uint32(u)
		default:
			if err := r.skip(wt); err != nil {
				return err
			}
		}
	}
	return nil
}
