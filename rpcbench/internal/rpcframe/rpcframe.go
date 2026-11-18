// Package rpcframe implements the gRPC length-prefixed message framing
// (gRPC HTTP/2 spec, "Length-Prefixed-Message"):
//
//	Compressed-Flag (1 byte, 0 = identity) | Message-Length (4 bytes, big endian) | Message
//
// The same framing is reused verbatim on every transport under test:
//
//	grpc : inside an HTTP/2 DATA stream          (standard gRPC)
//	h3   : inside an HTTP/3 DATA stream          (gRPC over QUIC via HTTP/3)
//	qtun : directly on a raw QUIC bidi stream    (gRPC over QUIC without HTTP)
//
// Keeping the framing and the payload codec identical across transports is a
// deliberate methodological choice: any difference we measure is attributable
// to the transport, not to a library's serialization or flow-control quirks.
package rpcframe

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// HeaderLen is the size of the gRPC length-prefix header.
const HeaderLen = 5

// DefaultMaxMessageSize mirrors gRPC's 4 MiB default receive limit.
const DefaultMaxMessageSize = 4 << 20

// ErrCompressed is returned when a peer sets the compressed flag; the benchmark
// always runs with identity compression so that payload bytes on the wire are
// exactly what the codec produced.
var ErrCompressed = errors.New("rpcframe: compressed messages are not supported")

// Writer serializes framed messages onto w, flushing after each message so that
// a batch leaves the host immediately instead of waiting for a full buffer.
// Flushing per message is what makes the latency measurement meaningful.
type Writer struct {
	w   io.Writer
	f   Flusher
	hdr [HeaderLen]byte
}

// Flusher is satisfied by http.Flusher and by bufio-style writers.
type Flusher interface{ Flush() }

// NewWriter returns a Writer. If w also implements Flusher, Flush is called
// after every message.
func NewWriter(w io.Writer) *Writer {
	f, _ := w.(Flusher)
	return &Writer{w: w, f: f}
}

// WriteMessage writes one length-prefixed message.
func (x *Writer) WriteMessage(msg []byte) error {
	x.hdr[0] = 0
	binary.BigEndian.PutUint32(x.hdr[1:], uint32(len(msg)))
	if _, err := x.w.Write(x.hdr[:]); err != nil {
		return err
	}
	if _, err := x.w.Write(msg); err != nil {
		return err
	}
	if x.f != nil {
		x.f.Flush()
	}
	return nil
}

// Reader deserializes framed messages from r.
type Reader struct {
	r      io.Reader
	max    int
	hdr    [HeaderLen]byte
	buf    []byte
	nBytes int64
}

// NewReader returns a Reader with the default 4 MiB message limit.
func NewReader(r io.Reader) *Reader { return &Reader{r: r, max: DefaultMaxMessageSize} }

// SetMaxMessageSize overrides the receive limit.
func (x *Reader) SetMaxMessageSize(n int) { x.max = n }

// BytesRead reports the total number of framed bytes consumed, header included.
func (x *Reader) BytesRead() int64 { return x.nBytes }

// ReadMessage reads one length-prefixed message. The returned slice is only
// valid until the next call to ReadMessage; copy it if you need to retain it.
// io.EOF is returned cleanly when the peer half-closes between messages.
func (x *Reader) ReadMessage() ([]byte, error) {
	if _, err := io.ReadFull(x.r, x.hdr[:]); err != nil {
		if err == io.ErrUnexpectedEOF {
			return nil, io.ErrUnexpectedEOF
		}
		return nil, err // io.EOF here means a clean end of stream
	}
	if x.hdr[0] != 0 {
		return nil, ErrCompressed
	}
	n := int(binary.BigEndian.Uint32(x.hdr[1:]))
	if n > x.max {
		return nil, fmt.Errorf("rpcframe: message of %d bytes exceeds the %d byte limit", n, x.max)
	}
	if cap(x.buf) < n {
		x.buf = make([]byte, n)
	}
	msg := x.buf[:n]
	if _, err := io.ReadFull(x.r, msg); err != nil {
		if err == io.EOF {
			return nil, io.ErrUnexpectedEOF
		}
		return nil, err
	}
	x.nBytes += int64(HeaderLen + n)
	return msg, nil
}
