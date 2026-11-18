package wire

import (
	"bytes"
	"testing"
)

func TestBatchRoundTrip(t *testing.T) {
	in := Batch{
		Stream:     "temperature",
		BatchSeq:   4242,
		SentUnixNs: 1_700_000_000_123_456_789,
		Items: []Measurement{
			{SensorID: "temp-0001", Kind: "temperature", Value: 21.75, Unit: "degC",
				TsUnixNs: 1_700_000_000_000_000_000, Seq: 1},
			{SensorID: "temp-0002", Kind: "temperature", Value: -3.5, Unit: "degC",
				TsUnixNs: 1_700_000_000_000_000_001, Seq: 2, Payload: bytes.Repeat([]byte{0xAB}, 200)},
			{}, // all-default message must survive a round trip as an empty item
		},
	}

	var out Batch
	if err := out.Unmarshal(in.Marshal()); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if out.Stream != in.Stream || out.BatchSeq != in.BatchSeq || out.SentUnixNs != in.SentUnixNs {
		t.Fatalf("header mismatch: %+v", out)
	}
	if len(out.Items) != len(in.Items) {
		t.Fatalf("item count: got %d want %d", len(out.Items), len(in.Items))
	}
	for i := range in.Items {
		a, b := in.Items[i], out.Items[i]
		if a.SensorID != b.SensorID || a.Kind != b.Kind || a.Value != b.Value ||
			a.Unit != b.Unit || a.TsUnixNs != b.TsUnixNs || a.Seq != b.Seq ||
			!bytes.Equal(a.Payload, b.Payload) {
			t.Fatalf("item %d mismatch:\n got %+v\nwant %+v", i, b, a)
		}
	}
}

func TestAckRoundTrip(t *testing.T) {
	in := Ack{Stream: "pm25", BatchSeq: 9, RecvUnixNs: 111, SrvUnixNs: 222, Accepted: 20}
	var out Ack
	if err := out.Unmarshal(in.Marshal()); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if in != out {
		t.Fatalf("got %+v want %+v", out, in)
	}
}

func TestNegativeInt64UsesTenByteVarint(t *testing.T) {
	// proto3 int64 encodes negatives as the 64-bit two's complement pattern,
	// which is always a 10-byte varint. Guard against an accidental zigzag.
	a := Ack{RecvUnixNs: -1}
	enc := a.Marshal()
	if len(enc) != 1+10 {
		t.Fatalf("expected tag+10-byte varint, got %d bytes: %x", len(enc), enc)
	}
	var out Ack
	if err := out.Unmarshal(enc); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if out.RecvUnixNs != -1 {
		t.Fatalf("got %d want -1", out.RecvUnixNs)
	}
}

func TestSkipsUnknownFields(t *testing.T) {
	a := Ack{Stream: "x", Accepted: 3}
	enc := a.Marshal()
	// Append an unknown field 15, wire type 2 (bytes).
	enc = append(enc, byte(15<<3|2), 2, 0xDE, 0xAD)
	var out Ack
	if err := out.Unmarshal(enc); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if out.Stream != "x" || out.Accepted != 3 {
		t.Fatalf("got %+v", out)
	}
}

func TestTruncatedIsAnError(t *testing.T) {
	b := Batch{Stream: "temperature", Items: []Measurement{{SensorID: "a"}}}
	enc := b.Marshal()
	var out Batch
	if err := out.Unmarshal(enc[:len(enc)-1]); err == nil {
		t.Fatal("expected an error on truncated input")
	}
}
