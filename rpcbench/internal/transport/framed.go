package transport

import (
	"io"
	"sync"
	"time"

	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/rpcframe"
	"github.com/MrCO0W/A-gRPC-over-QUIC-Multi-Stream/rpcbench/internal/wire"
)

// framedStream carries gRPC length-prefixed messages over any bidirectional
// byte stream. The HTTP/2, HTTP/3 and raw-QUIC transports all reduce to this
// once their stream has been established, which is exactly the property that
// makes the three arms comparable.
type framedStream struct {
	name string

	sendMu sync.Mutex
	w      *rpcframe.Writer

	recvMu sync.Mutex
	r      *rpcframe.Reader

	onCloseSend func() error
	onClose     func() error
	closeOnce   sync.Once
}

func newFramedStream(name string, w io.Writer, r io.Reader, onCloseSend, onClose func() error) *framedStream {
	return &framedStream{
		name:        name,
		w:           rpcframe.NewWriter(w),
		r:           rpcframe.NewReader(r),
		onCloseSend: onCloseSend,
		onClose:     onClose,
	}
}

func (s *framedStream) Send(b *wire.Batch) (int64, error) {
	msg := b.Marshal()
	s.sendMu.Lock()
	err := s.w.WriteMessage(msg)
	s.sendMu.Unlock()
	if err != nil {
		return 0, err
	}
	return int64(len(msg) + rpcframe.HeaderLen), nil
}

func (s *framedStream) Recv() (*wire.Ack, error) {
	s.recvMu.Lock()
	msg, err := s.r.ReadMessage()
	if err != nil {
		s.recvMu.Unlock()
		return nil, err
	}
	var ack wire.Ack
	err = ack.Unmarshal(msg)
	s.recvMu.Unlock()
	if err != nil {
		return nil, err
	}
	return &ack, nil
}

func (s *framedStream) CloseSend() error {
	if s.onCloseSend == nil {
		return nil
	}
	return s.onCloseSend()
}

func (s *framedStream) Close() error {
	var err error
	s.closeOnce.Do(func() {
		if s.onClose != nil {
			err = s.onClose()
		}
	})
	return err
}

// serveFramed is the server half: read batches, hand them to the handler, write
// acknowledgements back on the same stream. It returns nil on a clean
// half-close by the peer.
//
// defaultStream is used when a batch omits its stream key (the raw-QUIC tunnel
// sends the key once in a stream preamble instead of on every message).
func serveFramed(r io.Reader, w io.Writer, defaultStream string, h Handler) error {
	fr := rpcframe.NewReader(r)
	fw := rpcframe.NewWriter(w)
	for {
		msg, err := fr.ReadMessage()
		if err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}
		recvAt := time.Now()
		var b wire.Batch
		if err := b.Unmarshal(msg); err != nil {
			return err
		}
		if b.Stream == "" {
			b.Stream = defaultStream
		}
		ack := h(&b)
		ack.RecvUnixNs = recvAt.UnixNano()
		ack.SrvUnixNs = time.Now().UnixNano()
		if err := fw.WriteMessage(ack.Marshal()); err != nil {
			return err
		}
	}
}

// DefaultHandler acknowledges a batch without doing any application work. The
// Cloud Gateway in this experiment is intentionally a sink: adding server-side
// processing would add variance that has nothing to do with the transport.
func DefaultHandler(b *wire.Batch) *wire.Ack {
	return &wire.Ack{
		Stream:   b.Stream,
		BatchSeq: b.BatchSeq,
		Accepted: uint32(len(b.Items)),
	}
}
