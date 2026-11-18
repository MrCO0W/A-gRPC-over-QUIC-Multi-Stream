# test — REST / gRPC / gRPC over QUIC 성능 비교 실험 (업링크 telemetry)

**rpcbench**는 IoT 텔레메트리 파이프라인의 **Edge ↔ Cloud 구간**에서
REST, gRPC, gRPC over QUIC의 성능을 비교하기 위한 실험 환경입니다.
[GAIA Network Emulator](../gaia-experiment-main/README.md)의 Mininet 토폴로지
위에서 동작하며, 손실 환경에서의 **멀티스트림 Head-of-Line 블로킹**을
정량적으로 측정합니다.

---

## 📌 주요 기능 (Key Features)

- **4종 전송 계층 동시 지원**: REST(HTTP/1.1), gRPC(HTTP/2), gRPC over QUIC(HTTP/3), raw QUIC 터널 — **프레이밍·코덱·페이싱을 완전히 공유**하여 전송 계층 차이만 격리
- **Head-of-Line 블로킹 정량화**: 100 ms 버킷 기반 `stall_correlation` 지표로 "한 스트림의 손실이 전체 스트림을 멈추는가"를 0~1 수치로 측정
- **실제 편도 지연 측정**: Mininet 노드가 호스트 커널 시계를 공유하는 성질을 이용해 RTT뿐 아니라 **true one-way delay** 기록
- **실험 자동화 파이프라인**: 토폴로지·시나리오 자동 생성 → GAIA 워크플로우 스윕 실행 → CSV/SVG 리포트 생성까지 3개 스크립트로 완결
- **의존성 최소화**: Go는 `quic-go` 하나, Python은 표준 라이브러리만 사용 (pip 설치 불필요)

---

## 📁 프로젝트 구조 (Directory Structure)

```text
A-gRPC-over-QUIC-Multi-Stream/
├── README.md                     # 저장소 개요 (영문)
├── docs/
│   ├── README_ko.md              # 이 문서 (개요 및 빠른 시작)
│   ├── SETUP_AND_USAGE.md        # 설치·실행·파라미터 수정 상세 가이드
│   └── EXPERIMENT_DESIGN.md      # 실험 설계, 지표 정의, 타당성 위협 분석
├── results/                      # 논문 수치의 근거 데이터 (집계 CSV + run별 원시 결과)
├── gaia-experiment-main/         # Mininet 기반 실험 프레임워크
│
├── rpcbench/                     # Go 벤치마크 본체
│   ├── build.sh                  # 빌드 + 테스트 + 인증서 생성 스크립트
│   ├── go.mod                    # 의존성 (quic-go v0.48.2 고정)
│   ├── proto/telemetry.proto     # 와이어 스키마 정의 (규범 문서)
│   ├── cmd/
│   │   ├── cloudgw/              # Cloud Gateway (서버) — 4종 전송 동시 리슨
│   │   ├── edgegw/               # Edge Gateway (클라이언트) — 측정 주체
│   │   └── gencerts/             # TLS 인증서 생성기
│   └── internal/
│       ├── wire/                 # protobuf 수동 코덱 (codegen 불필요)
│       ├── rpcframe/             # gRPC 길이-프리픽스 프레이밍
│       ├── sensor/               # 센서 종류별 합성 데이터 생성기
│       ├── stats/                # 지연·처리량·HoL 지표 집계
│       ├── tlsutil/              # TLS 1.3 설정 및 인증서 생성
│       └── transport/            # 4종 전송 구현 (rest/grpc/h3/qtun)
│
└── scripts/
    ├── gen_scenarios.py          # GAIA 토폴로지·시나리오 파일 생성 (+ --explain)
    ├── run_sweep.sh              # 전체 매트릭스 스윕 실행
    └── analyze.py                # 결과 집계 → CSV + SVG 그림 + report.md
```

---

## 🔬 무엇을 측정하는가

### 비교 대상 4종

| 모드 | 전송 계층 | 멀티플렉싱 | 페이로드 |
|---|---|---|---|
| `rest` | HTTP/1.1 + TLS 1.3 (TCP) | 없음 — **스트림당 TCP 연결 1개** | JSON |
| `grpc` | gRPC over HTTP/2 + TLS 1.3 (TCP) | 8 스트림 → **TCP 연결 1개** | protobuf |
| `h3` | gRPC 프레이밍 over HTTP/3 (QUIC) | 8 스트림 → QUIC 연결 1개 | protobuf |
| `qtun` | gRPC 프레이밍 on **raw QUIC 스트림** (HTTP 계층 없음) | 8 스트림 → QUIC 연결 1개 | protobuf |

**`h3`와 `qtun`의 차이가 곧 HTTP/3 매핑 비용입니다.** QUIC 자체의 이득인지
HTTP/3 구현의 이득인지를 분리하기 위해 두 가지를 모두 구현했습니다.

### 실험 축

```
손실률 5단계 (0 / 0.5 / 1 / 2 / 5 %)
  × 전송 4종
  × 부하 레짐 2종 (지연 중심 / 처리량 중심)
  × 반복 3회
  ─────────────────────────────────
  = 120 run,  약 2~2.5시간
```

에뮬레이션 경로: **50 Mbps · RTT 40 ms WAN**, 8개 센서 종류를 각각 독립
스트림으로 전송.

```
c0 (Edge GW) ──1ms/1Gb──▶ r0 ──18ms/50Mb──▶ r1 ──1ms/1Gb──▶ s0 (Cloud GW)
                                  ▲
                           netem 손실 주입 지점 (양방향)
```

### 핵심 지표 — `stall_correlation`

100 ms 버킷 중 "**한 스트림이라도** 멈춘 구간" 대비 "**모든** 스트림이 멈춘 구간"의 비율.

```
stall_correlation = |모든 N개 스트림이 멈춘 버킷| / |하나라도 멈춘 버킷|
```

- TCP처럼 **순서 보장 바이트스트림 하나를 공유**하면 → **1.0**에 수렴
- QUIC처럼 **스트림별 독립 복구**가 되면 → **1/N**(8스트림이면 0.125) 근처

이 한 숫자가 "QUIC이 왜 더 나은가"라는 메커니즘을 그대로 진술합니다.

---

## ⚙️ 사전 준비 (Prerequisites)

| 항목 | 최소 버전 | 비고 |
|---|---|---|
| Linux + root | — | Mininet 실행에 필요 |
| Mininet | 2.3+ | GAIA를 돌리던 호스트면 충족 |
| Go | 1.22+ | 벤치마크 빌드 |
| Python | 3.8+ | **표준 라이브러리만 사용** |
| 인터넷 | 최초 1회 | `quic-go` 다운로드 |

> 상세 설치 절차와 확인 명령은 [`SETUP_AND_USAGE.md` 1장](SETUP_AND_USAGE.md#1-사전-준비)을 보세요.

---

## 🚀 빠른 시작 (Quick Start)

### 1. 빌드

```bash
cd "A-gRPC-over-QUIC-Multi-Stream"
chmod +x rpcbench/build.sh scripts/run_sweep.sh   # Windows에서 복사한 경우
cd rpcbench && ./build.sh
```

`go mod tidy` → 바이너리 3개 빌드 → 테스트 → TLS 인증서 생성까지 한 번에 수행합니다.

### 2. 동작 확인 (Mininet 없이, 1분)

```bash
./bin/cloudgw -certs certs -bind 127.0.0.1 &
for m in rest grpc h3 qtun; do
  ./bin/edgegw -server 127.0.0.1 -mode $m -streams 4 -batch 10 \
               -duration 5s -warmup 1s -ca certs/ca.pem
done
kill %1
```

**4개 모드 모두 goodput이 0이 아니고 에러가 없어야 합니다.** 여기서 막히면
2시간짜리 스윕을 시작하지 마세요.

### 3. 시나리오 생성

```bash
cd ../scripts
python3 gen_scenarios.py --explain      # 현재 설정이 만들 네트워크 확인
python3 gen_scenarios.py                # 경로는 자동 탐지됩니다
```

GAIA의 `config/topo/`에 토폴로지 5개, `config/xp/`에 시나리오 40개,
그리고 `manifest.json`이 생성됩니다.

> `gaia-experiment-main`과 `rpcbench`가 이 프로젝트와 **형제 디렉터리**에 있으면
> 경로를 적을 필요가 없습니다. 다른 곳에 있다면 `--gaia`, `--rpcbench`로 지정하세요.
> **`~` 사용은 피하세요** — `sudo`나 root 셸에서는 `/root`로 풀려서 엉뚱한 곳을 가리킵니다.

### 4. 스윕 실행

```bash
sudo ./run_sweep.sh                 # 전체 매트릭스 (~2.5시간)
sudo REPEATS=1 ./run_sweep.sh       # 빠른 1회 패스 (~45분)
```

중단되면 같은 명령을 다시 실행하세요 — 이미 끝난 셀은 건너뜁니다.

### 5. 분석

```bash
python3 analyze.py                      # 결과 위치도 manifest에서 읽습니다
```

`report/`에 CSV 3종, SVG 그림 4종, `report.md`가 생성됩니다.

---

## 📊 산출물 (Outputs)

| 파일 | 내용 |
|---|---|
| `summary.csv` | (레짐, 전송, 손실)별 1행 — 반복의 중앙값 |
| `runs.csv` / `streams.csv` | run별 / 스트림별 원자료 |
| `fig1_goodput_vs_loss.svg` | 손실 대비 goodput (처리량 레짐) |
| `fig2_p99_vs_loss.svg` | 손실 대비 p99 지연 (HoL 레짐) |
| `fig3_stall_correlation.svg` | 스톨이 전체 스트림을 덮친 비율 |
| `fig4_progress_l1p0.svg` | 스트림별 시간축 진행 — **메커니즘을 시각화** |
| `report.md` | 표와 열화율을 논문에 옮겨 쓸 수 있는 형태로 정리 |

SVG는 외부 의존성 없이 단독 렌더링되며, 라이트/다크 모드에 모두 대응하고
시리즈마다 다른 마커 모양을 써서 **흑백 인쇄에서도 구분**됩니다.

> 📌 그림의 축·범례는 **영문**입니다. 논문 본문이 영문이라,
> 그림을 그대로 삽입할 수 있게 의도한 것입니다. 한글화 방법은
> [`SETUP_AND_USAGE.md` 6장](SETUP_AND_USAGE.md#6-결과-분석)에 있습니다.

---

## 🔧 파라미터 수정

지연(delay), 대역폭, 손실률, 스트림 수 등 거의 모든 변인은
`scripts/gen_scenarios.py` 상단의 상수 블록에서 조정합니다.

```python
ACCESS_DELAY_MS = 1.0      # 액세스 링크 편도 지연 (ms)
WAN_DELAY_MS    = 18.0     # WAN 링크 편도 지연 (ms)
WAN_BW_MBPS     = 50.0     # WAN 병목 대역폭 (Mbps)
WAN_QUEUE       = 100      # WAN 큐 파라미터 (패킷 수가 아님!)
LOSSES          = [0.0, 0.5, 1.0, 2.0, 5.0]
```

### ⚠️ 수정 전 반드시 알아야 할 3가지

1. **`delay`는 편도값이고 netem이 양방향에 걸립니다.** 링크 하나가 RTT에
   기여하는 값은 `2 × delay`입니다.
2. **`queue_size`는 패킷 개수가 아닙니다.** GAIA가 큐잉 지연(ms)으로 환산한 뒤
   `1×BDP + 해당 지연`을 실제 버퍼로 잡습니다. 대역폭을 바꾸면 같은 값이라도
   전혀 다른 버퍼가 됩니다.
3. **손실률도 양방향입니다.** `1.0` 설정 시 왕복 손실 확률은 약 1.99%입니다.

```bash
# 값을 고친 뒤에는 반드시 이 명령으로 실제 결과를 확인하세요
python3 scripts/gen_scenarios.py --explain
```

> 전체 파라미터 목록, 수정 절차, 상황별 레시피(RTT 100ms로 바꾸기, 대역폭
> 10Mbps로 낮추기, 스트림 32개로 늘리기 등)는
> [`SETUP_AND_USAGE.md` 7~8장](SETUP_AND_USAGE.md#7-환경-변수--파라미터-수정-방법)에 정리되어 있습니다.

---

## 📖 문서 안내

| 문서 | 언제 읽나 |
|---|---|
| **README.md** (이 문서) | 전체 개요, 빠른 시작 |
| [**SETUP_AND_USAGE.md**](SETUP_AND_USAGE.md) | 설치·실행·파라미터 수정·문제 해결 |
| [**EXPERIMENT_DESIGN.md**](EXPERIMENT_DESIGN.md) | 가설, 통제 변인, 지표 정의, 타당성 위협 — **논문 방법론 절의 원본** |
| `rpcbench/proto/telemetry.proto` | 와이어 포맷 규범 정의 |
| [**DOWNLINK_EXPERIMENT.md**](DOWNLINK_EXPERIMENT.md) | 실험 C: 하향 명령 전달 지연(REST 폴링·롱 폴링·SSE 대 gRPC 스트림, HTTP/1.1·2·3) |

---

## 🆕 실험 C: 하향 명령 전달 지연

상위(Edge Master)에서 내린 제어 명령이 Edge Agent에 도착하기까지의 지연을
REST 짧은 폴링·롱 폴링·SSE와 gRPC 양방향 스트림으로, 각각 HTTP/1.1·HTTP/2·HTTP/3
위에서 비교합니다. 네트워크는 위 실험과 같고, 바이너리는 `ctrlgw`·`ctrledge`,
스크립트는 `gen_ctrl_scenarios.py`·`run_ctrl_sweep.sh`·`analyze_ctrl.py`입니다.

```bash
cd rpcbench && ./build.sh build
cd ../scripts && python3 gen_ctrl_scenarios.py --explain && python3 gen_ctrl_scenarios.py
sudo ./run_ctrl_sweep.sh && python3 analyze_ctrl.py --results <gaia>/output/test_ctrl --out report_ctrl
```

자세한 설계·지표·해석은 [`DOWNLINK_EXPERIMENT.md`](DOWNLINK_EXPERIMENT.md)를 보세요.

---

## ⚠️ 알려진 제약 (Known Limitations)

1. **gRPC 계열은 `grpc-go`가 아니라 와이어 호환 자체 구현입니다.**
   Go 표준 `net/http` 위에 실제 gRPC 경로·content-type·프레이밍·트레일러를
   구현했습니다(Wireshark에서 gRPC로 디코딩됨). HTTP/2·HTTP/3·raw QUIC 세 arm의
   프레이밍과 코덱을 동일하게 고정하려면 이 방법뿐이었습니다. 근거와 리뷰어
   대응 방법은 [`EXPERIMENT_DESIGN.md` 3장](EXPERIMENT_DESIGN.md#3-비교-대상-4종)에 있습니다.

2. **netem 손실은 독립 균등 분포입니다.** 실제 무선 손실은 버스트성이므로
   현재 결과는 QUIC에게 **보수적**입니다.

3. **quic-go는 유저스페이스, TCP는 커널입니다.** CPU 병목 상황에서는 TCP가
   유리합니다. 즉 QUIC의 승리는 보수적 추정이고, 0% 손실에서 QUIC이 지면
   프로토콜이 아니라 이 효과일 수 있습니다. 실행 중 호스트 CPU를 기록하세요.

4. **단일 클라이언트, 단일 경로**입니다. 확장성·이동성·연결 마이그레이션·0-RTT에
   대해서는 어떤 결론도 낼 수 없습니다.

5. **`quic-go`는 v0.48.2에 고정**되어 있습니다. `go get -u` 금지 —
   `http3.RoundTripper`가 이후 버전에서 `http3.Transport`로 개명되어 빌드가 깨집니다.
