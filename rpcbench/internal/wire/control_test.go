package wire

import (
	"bytes"
	"testing"
)

func TestCommandRoundTrip(t *testing.T) {
	in := Command{Seq: 42, Device: "fan-03", Op: "set_speed", Value: 0.75,
		IssuedUnixNs: 1_700_000_000_123_456_789, ValidUntilNs: 1_700_000_005_000_000_000,
		Payload: bytes.Repeat([]byte{0xAB}, 64)}
	var out Command
	if err := out.Unmarshal(in.Marshal()); err != nil {
		t.Fatal(err)
	}
	if out.Seq != in.Seq || out.Device != in.Device || out.Op != in.Op ||
		out.Value != in.Value || out.IssuedUnixNs != in.IssuedUnixNs ||
		out.ValidUntilNs != in.ValidUntilNs || !bytes.Equal(out.Payload, in.Payload) {
		t.Fatalf("mismatch: %+v vs %+v", out, in)
	}
}

func TestEdgeMsgRoundTrip(t *testing.T) {
	h := EdgeMsg{Hello: &Hello{AgentID: "edge-1", AfterSeq: 7, CmdRate: 5, CmdBytes: 64, Seed: 3}}
	var got EdgeMsg
	if err := got.Unmarshal(h.Marshal()); err != nil {
		t.Fatal(err)
	}
	if got.Hello == nil || *got.Hello != *h.Hello || got.Ack != nil {
		t.Fatalf("hello mismatch: %+v", got)
	}
	a := EdgeMsg{Ack: &CommandAck{Seq: 9, RecvUnixNs: 11, DoneUnixNs: 12}}
	if err := got.Unmarshal(a.Marshal()); err != nil {
		t.Fatal(err)
	}
	if got.Ack == nil || *got.Ack != *a.Ack || got.Hello != nil {
		t.Fatalf("ack mismatch: %+v", got)
	}
	if err := got.Unmarshal(nil); err != ErrEmptyEdgeMsg {
		t.Fatalf("want ErrEmptyEdgeMsg, got %v", err)
	}
}
