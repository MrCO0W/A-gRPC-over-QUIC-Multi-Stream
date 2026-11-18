# 설치 · 실행 · 설정 가이드

> 이 문서 하나로 **사전 준비 → 빌드 → 실행 → 결과 분석 → 파라미터 수정**까지 끝낼 수 있습니다.
> 실험이 왜 이렇게 설계되었는지는 [`EXPERIMENT_DESIGN.md`](EXPERIMENT_DESIGN.md)를 보세요.

---

## 📋 목차

1. [사전 준비](#1-사전-준비)
2. [빌드](#2-빌드)
3. [동작 확인 (Mininet 없이)](#3-동작-확인-mininet-없이)
4. [시나리오 생성](#4-시나리오-생성)
5. [스윕 실행](#5-스윕-실행)
6. [결과 분석](#6-결과-분석)
7. [환경 변수 · 파라미터 수정 방법](#7-환경-변수--파라미터-수정-방법)
8. [상황별 수정 레시피](#8-상황별-수정-레시피)
9. [문제 해결](#9-문제-해결)

---

## 1. 사전 준비

### 1.1 필수 요소 체크리스트

| 항목 | 최소 버전 | 확인 명령 | 비고 |
|---|---|---|---|
| Linux + root 권한 | — | `sudo -v` | Mininet은 네임스페이스 조작에 root가 필요 |
| Mininet | 2.3+ | `mn --version` | 기존 GAIA를 돌리던 그 호스트면 이미 충족 |
| GAIA 프레임워크 | — | `ls gaia-experiment-main/cli.py` | 이 실험은 GAIA 위에서 동작 |
| Go | 1.22+ | `go version` | 벤치마크 빌드용 |
| Python | 3.8+ | `python3 --version` | **pip 설치 불필요** (표준 라이브러리만 사용) |
| 인터넷 | 최초 1회 | `go env GOPROXY` | `quic-go` 의존성 다운로드용 |
| `tc` / `netem` | — | `tc -V` | `iproute2` 패키지. Mininet 설치 시 같이 들어옴 |

### 1.2 아직 없다면

```bash
# Mininet
sudo apt update
sudo apt install -y mininet iproute2
mn --version

# Go (apt 버전이 1.22 미만이면 공식 tarball 사용)
sudo apt install -y golang-go
go version
# 버전이 낮을 때:
#   wget https://go.dev/dl/go1.22.12.linux-amd64.tar.gz
#   sudo rm -rf /usr/local/go && sudo tar -C /usr/local -xzf go1.22.12.linux-amd64.tar.gz
#   export PATH=$PATH:/usr/local/go/bin   # ~/.bashrc 에도 추가

# IP 포워딩 (GAIA 라우터 노드가 사용)
sudo sysctl -w net.ipv4.ip_forward=1
```

### 1.3 디스크 · 시간 예산

| 항목 | 예상치 |
|---|---|
| Go 모듈 캐시 | 약 150 MB (최초 1회) |
| 바이너리 3개 | 약 30 MB |
| 전체 스윕 결과 (120 run) | 약 40 MB (`pcap:no` 기준) |
| `pcap:yes`로 돌릴 경우 | run당 수백 MB — **기본값 `no` 유지 권장** |
| 전체 스윕 소요 시간 | **2~2.5시간** (`REPEATS=1`이면 약 45분) |

### 1.4 파일 배치

`gaia-experiment-main`과 `test`가 **같은 Linux 호스트**에 있어야 합니다.
Mininet 노드들은 호스트의 파일시스템을 공유하므로, 바이너리와 인증서를
한 곳에 두면 모든 노드(c0, s0)에서 그대로 실행됩니다.

아래 두 배치 모두 스크립트가 **경로를 자동으로 찾습니다.**

```
# 배치 A — 한 폴더 안에 모두
A-gRPC-over-QUIC-Multi-Stream/
├── gaia-experiment-main/
├── rpcbench/
└── scripts/

# 배치 B — 실험을 별도 폴더로
workspace/
├── gaia-experiment-main/
└── A-gRPC-over-QUIC-Multi-Stream/
    ├── rpcbench/
    └── scripts/
```

그 밖의 배치라면 `--gaia`, `--rpcbench`로 직접 지정하면 됩니다.

> Windows에서 복사해 왔다면 실행 권한을 복원하세요.
> ```bash
> cd "A-gRPC-over-QUIC-Multi-Stream"
> chmod +x rpcbench/build.sh scripts/run_sweep.sh
> ```

---

## 2. 빌드

```bash
cd "A-gRPC-over-QUIC-Multi-Stream/rpcbench"
./build.sh
```

`./build.sh`는 아래를 순서대로 실행합니다.

| 단계 | 내용 | 개별 실행 |
|---|---|---|
| `deps` | `go mod tidy` — 의존성 다운로드 (**인터넷 필요, 최초 1회**) | `./build.sh deps` |
| `build` | `bin/cloudgw`, `bin/edgegw`, `bin/gencerts` 생성 | `./build.sh build` |
| `test` | `go vet` + 단위 테스트 (protobuf 코덱) | `./build.sh test` |
| `certs` | `certs/ca.pem`, `certs/server.pem`, `certs/server.key` 생성 | `./build.sh certs` |

전부 지우고 다시 시작하려면 `./build.sh clean`.

### ⚠️ `go get -u` 금지

`quic-go`는 **v0.48.2에 의도적으로 고정**되어 있습니다. 이후 릴리스에서
`http3.RoundTripper`가 `http3.Transport`로 이름이 바뀌어 빌드가 깨집니다.

굳이 올리려면 `internal/transport/grpch3.go`를 함께 고치고 **전체 스윕을 다시**
돌리세요. 서로 다른 quic-go 버전의 결과를 한 그래프에 섞으면 안 됩니다.

### 인증서 SAN 주의

인증서에는 서버 IP가 SAN으로 박혀 있어야 TLS 검증이 통과합니다. 기본값은
생성되는 토폴로지에 맞춰져 있습니다.

```bash
# 기본값
HOSTS=127.0.0.1,localhost,10.0.2.2,10.0.1.2

# 토폴로지의 서버 IP를 바꿨다면 반드시 재생성
HOSTS=127.0.0.1,localhost,10.0.9.2 ./build.sh certs
```

---

## 3. 동작 확인 (Mininet 없이)

**2시간짜리 스윕을 시작하기 전에 4개 모드를 전부 확인하세요.** loopback에서
도는 스모크 테스트라 1분이면 끝납니다.

```bash
cd "A-gRPC-over-QUIC-Multi-Stream/rpcbench"

# 서버를 백그라운드로 (4개 전송 모두 리슨)
./bin/cloudgw -certs certs -bind 127.0.0.1 &

for m in rest grpc h3 qtun; do
  echo "===== $m ====="
  ./bin/edgegw -server 127.0.0.1 -mode $m -streams 4 -batch 10 \
               -duration 5s -warmup 1s -ca certs/ca.pem
done

kill %1
```

**정상 출력**: 각 모드마다 goodput이 0이 아니고, 마지막 줄에 에러 없이
`stall correlation` 값이 찍힙니다. 무손실 loopback이므로 stall correlation은
0에 가깝게 나옵니다.

```
edgegw: h3 | goodput 42.31 Mbps | 85210 msg/s | RTT p50 0.42 ms p99 1.10 ms | stall correlation 0.00 (4 streams)
```

---

## 4. 시나리오 생성

### 4.1 먼저 지금 설정이 어떤 네트워크인지 확인

```bash
cd "A-gRPC-over-QUIC-Multi-Stream/scripts"
python3 gen_scenarios.py --explain
```

파일을 쓰지 않고, 현재 상수들이 **실제로 어떤 네트워크를 만드는지** 계산해서
보여줍니다. GAIA의 `core/topo.py` 공식을 그대로 재현한 것이라 실제 `tc` 설정과
일치합니다.

```
r0 <-> r1 (WAN)     one-way  18.0 ms     50.0 Mbit/s  queue_size 100
                      -> adds  36.0 ms to RTT, BDP 150 pkt, queueing 22 ms, buffer 362500 B (242 pkt)
------------------------------------------------------------------------------
base RTT (no queueing)        : 40.0 ms
worst-case RTT when buffer full: 62.0 ms

configured  1.0%  ->  1.99% of round trips lose a packet
```

**파라미터를 바꿀 때마다 이 명령을 먼저 돌리세요.** 아래 [7장](#7-환경-변수--파라미터-수정-방법)에서 자세히 다룹니다.

### 4.2 생성

`gaia-experiment-main`과 `rpcbench`가 이 프로젝트와 형제 디렉터리에 있으면
**경로를 적을 필요가 없습니다.**

```bash
python3 gen_scenarios.py
```

```
A-gRPC-over-QUIC-Multi-Stream/
├── gaia-experiment-main/   ← 자동 탐지
├── rpcbench/               ← 자동 탐지
└── scripts/                ← 여기서 실행
```

다른 곳에 있다면 명시하세요.

```bash
python3 gen_scenarios.py --gaia /path/to/gaia-experiment-main \
                         --rpcbench /path/to/rpcbench
```

> ⚠️ **`~`를 쓰지 마세요.** `sudo`나 root 셸에서는 `~`가 로그인 사용자의 홈이
> 아니라 `/root`로 풀립니다. 상대 경로(`../rpcbench`)나 절대 경로를 쓰거나,
> 그냥 인자를 생략하세요. 잘못된 경로를 주면 스크립트가 올바른 위치를
> 찾아서 알려줍니다.

출력:

```
topologies : 5
scenarios  : 40
repeats    : 3  -> 120 runs total
manifest   : .../scripts/manifest.json
```

생성되는 것:

| 위치 | 파일 | 개수 |
|---|---|---|
| `<gaia>/config/topo/` | `topo_test_l0`, `topo_test_l0p5`, `topo_test_l1`, `topo_test_l2`, `topo_test_l5` | 5 |
| `<gaia>/config/xp/` | `xp_test_<regime>_<mode>_l<loss>` | 40 |
| `scripts/` | `manifest.json` (스윕·분석 스크립트가 읽음) | 1 |

### 4.3 GAIA 검증기로 한 개 확인

```bash
cd ~/졸업논문/gaia-experiment-main
python3 cli.py verify -t config/topo/topo_test_l1 \
                      -x config/xp/xp_test_hol_h3_l1 --json
```

`"valid": true`가 나와야 합니다.

---

## 5. 스윕 실행

```bash
cd "A-gRPC-over-QUIC-Multi-Stream/scripts"
sudo ./run_sweep.sh
```

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `REPEATS` | manifest의 값 (3) | 셀당 반복 횟수 |
| `MANIFEST` | `scripts/manifest.json` | 다른 manifest를 쓸 때 |
| `OUT_ROOT` | `<gaia>/output/test` | 결과 저장 위치 |
| `PY` | `python3` | Python 인터프리터 |

```bash
sudo REPEATS=1 ./run_sweep.sh                      # 빠른 1회 패스 (~45분)
sudo OUT_ROOT=/data/test-run2 ./run_sweep.sh      # 결과 위치 변경
```

### 실행 중 화면

```
[12/40 rep1] xp_test_hol_grpc_l1
  ok  goodput=  12.44 Mbps  msg/s=    25402  rtt_p50=  41.20 ms  rtt_p99=  268.40 ms  stall_corr=0.71  (47s)
```

### 중단 후 이어하기

이미 `rpcbench.json`이 있는 셀은 **건너뜁니다.** 중단됐으면 그냥 같은 명령을
다시 돌리면 됩니다. 특정 셀만 다시 돌리고 싶으면 그 디렉터리를 지우세요.

```bash
rm -rf <gaia>/output/test/xp_test_hol_grpc_l1/rep2
sudo ./run_sweep.sh
```

### 결과 위치

```
<gaia>/output/test/
├── sweep.log                              ← 전체 요약 로그
└── xp_test_hol_grpc_l1/
    └── rep1/
        ├── rpcbench.json                  ← 측정 결과 (분석기 입력)
        ├── exec.log                        ← GAIA 실행 로그
        ├── workload-0.log                  ← cloudgw(서버) 출력
        ├── workload-1.log                  ← edgegw(클라이언트) 출력
        └── workloads.json                  ← 실제 실행된 명령
```

---

## 6. 결과 분석

```bash
cd "A-gRPC-over-QUIC-Multi-Stream/scripts"
python3 analyze.py
```

`--manifest`는 기본값이 `scripts/manifest.json`이고, 결과 디렉터리(`--results`)와
출력 위치(`--out`, 기본 `../report`)도 거기서 유도하므로 보통 인자가 필요 없습니다.
다른 스윕 결과를 분석하려면 명시하세요.

```bash
python3 analyze.py --results /data/test-run2 --out ../report-run2
```

| 산출물 | 내용 |
|---|---|
| `summary.csv` | (레짐, 전송, 손실)별 1행 — 반복의 **중앙값** |
| `runs.csv` | 개별 run별 1행 |
| `streams.csv` | run × 스트림별 1행 |
| `fig1_goodput_vs_loss.svg` | 손실 대비 goodput (처리량 레짐) |
| `fig2_p99_vs_loss.svg` | 손실 대비 p99 지연 (HoL 레짐) |
| `fig3_stall_correlation.svg` | 스톨이 전체 스트림을 덮친 비율 |
| `fig4_progress_l1p0.svg` | 스트림별 시간축 진행 — **메커니즘을 보여주는 그림** |
| `report.md` | 표와 열화율을 논문에 쓸 수 있는 형태로 정리 |

분석기 옵션:

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--results` | manifest의 `<gaia>/output/test` | 스윕 결과 디렉터리 |
| `--manifest` | `scripts/manifest.json` | 생성기가 만든 manifest |
| `--out` | `../report` | CSV·그림 출력 위치 |
| `--progress-loss` | `1.0` | fig4를 어느 손실률로 그릴지 |
| `--progress-window` | `100` | fig4에 표시할 100 ms 버킷 개수 (100 = 10초) |
| `--progress-offset` | `50` | fig4 발췌 시작 버킷 (50 = 5초 지점부터) |

```bash
# 5% 손실 구간을, 10초 지점부터 15초간 크게 보기
python3 analyze.py --results ... --progress-loss 5 \
                   --progress-offset 100 --progress-window 150 --out ../report-l5
```

> 📌 **그림의 축·범례는 영문입니다.** 논문 본문이 영문이라
> 그림을 그대로 붙여 넣을 수 있게 의도적으로 영문을 유지했습니다.
> 한글로 바꾸려면 `analyze.py`의 `SERIES` 리스트(라벨)와 각 `line_figure(...)`
> 호출의 `title`·`subtitle`·`ylabel` 인자만 고치면 됩니다.

---

## 7. 환경 변수 · 파라미터 수정 방법

### 7.1 전체 지도 — 무엇을 어디서 고치나

| 바꾸고 싶은 것 | 파일 | 위치 |
|---|---|---|
| 지연(delay), 대역폭, 손실률, 큐 | `scripts/gen_scenarios.py` | 상단 상수 블록 |
| 스트림 수, 배치 크기, in-flight, 측정 시간 | `scripts/gen_scenarios.py` | `REGIMES` 딕셔너리 |
| 노드 구성 · IP 주소 | `scripts/gen_scenarios.py` | `TOPO_TEMPLATE` |
| 실험 매트릭스(어떤 조합만 돌릴지) | 명령줄 | `gen_scenarios.py --modes/--losses/--regimes` |
| 반복 횟수, 결과 경로 | 명령줄 / 환경 변수 | `run_sweep.sh`의 `REPEATS`, `OUT_ROOT` |
| 클라이언트 세부 동작 | `scripts/gen_scenarios.py` | `write_xp()`의 `client_cmd` |
| 포트 번호 | `rpcbench/internal/transport/transport.go` | `DefaultPorts` |
| QUIC 윈도우 등 튜닝 | `rpcbench/internal/transport/grpch3.go` | `QUICConfig()` |
| 센서 종류 · 값 범위 | `rpcbench/internal/sensor/sensor.go` | `Kinds` 리스트 |
| 인증서 SAN | 환경 변수 | `HOSTS=... ./build.sh certs` |

### 7.2 네트워크 파라미터 (delay 등)

`scripts/gen_scenarios.py` 상단:

```python
ACCESS_DELAY_MS = 1.0      # c0↔r0, s0↔r1 링크의 편도 지연 (ms)
WAN_DELAY_MS    = 18.0     # r0↔r1 WAN 링크의 편도 지연 (ms)
ACCESS_BW_MBPS  = 1000.0   # 액세스 링크 대역폭 (Mbps, 방향당)
WAN_BW_MBPS     = 50.0     # WAN 병목 대역폭 (Mbps, 방향당)
ACCESS_QUEUE    = 1000     # 액세스 링크 큐 파라미터
WAN_QUEUE       = 100      # WAN 링크 큐 파라미터
LOSSES          = [0.0, 0.5, 1.0, 2.0, 5.0]   # 손실률 스윕 (%)
```

#### ⚠️ 반드시 알아야 할 3가지 함정

**① `delay`는 편도(one-way)이고, netem이 양방향에 걸립니다.**

GAIA는 링크 하나를 `bs0-bs1-bs2-bs3` 4-스위치 체인으로 만들고 **양쪽 방향
각각에** netem을 겁니다. 따라서 링크 하나가 RTT에 기여하는 값은 `2 × delay`입니다.

```
RTT = 2 × (ACCESS_DELAY + WAN_DELAY + ACCESS_DELAY)
    = 2 × (1 + 18 + 1) = 40 ms
```

RTT를 100 ms로 만들고 싶다면 `WAN_DELAY_MS = 48.0` (→ `2×(1+48+1) = 100`).

**② `queue_size`는 패킷 개수가 아닙니다.**

GAIA는 이 값을 **큐잉 지연(ms)** 으로 환산한 뒤, 실제 `tbf` 버퍼를
`1×링크 BDP + 그 큐잉 지연`으로 잡습니다 (`core/topo.py`).

```
큐잉지연(ms) = queue_size × 1500 × 8 × 1000 / (대역폭Mbps × 1024 × 1024)
버퍼(byte)   = 1500 × ceil(BDP패킷) + 대역폭Mbps × 1000 × 큐잉지연 / 8
```

18 ms / 50 Mbps에서 `queue_size=100` → 큐잉 22 ms, 버퍼 362 kB(약 242패킷).

**같은 `queue_size`라도 대역폭이 바뀌면 결과가 완전히 달라집니다.** 10 Mbps에서
`queue_size=100`은 큐잉 114 ms(심한 버퍼블로트)가 됩니다. 대역폭을 바꿨다면
반드시 `--explain`으로 다시 확인하세요.

> **왜 이 값이 중요한가**: 버퍼가 너무 얕으면 큐 오버플로 드롭이 발생해서,
> 우리가 일부러 주입한 netem 손실과 섞여 버립니다. 그러면 "손실률 1%에서의
> 거동"이라는 실험 자체가 무의미해집니다. 반대로 너무 깊으면 버퍼블로트로
> 지연이 부풀어 오릅니다. 현재 값은 `1×BDP + 22 ms`로 그 사이를 잡은 것입니다.

**③ 손실률도 양방향에 걸립니다.**

`1.0`으로 설정하면 **각 방향에 1%** 입니다. 왕복 기준 패킷 손실 확률은
`1 - (1-p)² ≈ 1.99%`입니다. `--explain`이 이 환산표를 출력합니다.

#### 수정 후 절차

```bash
# 1. 상수 수정
vim scripts/gen_scenarios.py

# 2. 실제로 어떤 네트워크가 되는지 확인  ← 이 단계를 건너뛰지 마세요
python3 scripts/gen_scenarios.py --explain

# 3. 서버 IP를 바꿨다면 인증서 재생성
cd rpcbench && HOSTS=127.0.0.1,localhost,<새 서버 IP> ./build.sh certs && cd ..

# 4. 시나리오 재생성
python3 scripts/gen_scenarios.py --gaia <gaia> --rpcbench <rpcbench>

# 5. 결과 디렉터리를 비우고 (이전 설정 결과와 섞이면 안 됨)
rm -rf <gaia>/output/test

# 6. 재실행
sudo scripts/run_sweep.sh
```

### 7.3 부하 파라미터 (레짐)

같은 파일의 `REGIMES`:

```python
REGIMES = {
    "hol":  dict(streams=8, batch=20,  inflight=1, pad=0, duration=30, warmup=5),
    "tput": dict(streams=8, batch=100, inflight=8, pad=0, duration=30, warmup=5),
}
```

| 키 | 의미 | 조정 시 효과 |
|---|---|---|
| `streams` | 동시 논리 스트림 수 (= 센서 종류 수) | 늘리면 HoL 효과가 커짐. `stall_correlation`의 기준선은 `1/streams` |
| `batch` | 배치당 측정값 개수 | 크게 하면 메시지가 커져 손실 확률↑, 처리량↑ |
| `inflight` | 스트림당 미확인 배치 허용 수 | `1`=클로즈드 루프(지연 실험), `>1`=파이프라인(처리량 실험). REST는 이 수만큼 TCP 연결을 엶 |
| `pad` | 측정값당 추가 더미 바이트 | 페이로드 크기를 강제로 키울 때 |
| `duration` | 측정 구간 (초) | 통계 안정성 ↔ 총 소요 시간 트레이드오프 |
| `warmup` | 결과에서 제외할 준비 구간 (초) | 핸드셰이크·슬로스타트 배제용. 5초 미만 비권장 |

`streams`를 8 초과로 올리면 `internal/sensor/sensor.go`의 8개 센서 종류가
`temperature_1`처럼 접미사를 붙여 순환합니다.

레짐 자체를 추가할 수도 있습니다:

```python
REGIMES = {
    "hol":  dict(streams=8,  batch=20,  inflight=1, pad=0, duration=30, warmup=5),
    "tput": dict(streams=8,  batch=100, inflight=8, pad=0, duration=30, warmup=5),
    "many": dict(streams=32, batch=20,  inflight=1, pad=0, duration=30, warmup=5),  # 추가
}
```

### 7.4 실험 매트릭스 좁히기 (파일 수정 없이)

```bash
# QUIC 2종만, 손실 0/1/5%만, HoL 레짐만, 1회씩
python3 gen_scenarios.py --gaia <gaia> --rpcbench <rpcbench> \
    --modes h3,qtun --losses 0,1,5 --regimes hol --repeats 1

# pcap 캡처 켜기 (용량 주의)
python3 gen_scenarios.py --gaia <gaia> --rpcbench <rpcbench> --pcap yes
```

| 옵션 | 기본값 |
|---|---|
| `--modes` | `rest,grpc,h3,qtun` |
| `--regimes` | `hol,tput` |
| `--losses` | `0.0,0.5,1.0,2.0,5.0` |
| `--repeats` | `3` |
| `--pcap` | `no` |
| `--out` | 스크립트와 같은 디렉터리 |

### 7.5 토폴로지 구조 자체 바꾸기

`gen_scenarios.py`의 `TOPO_TEMPLATE`:

```
topoType:Generic
router:r0,10.0.1.0/24,10.0.1.1
router:r1,10.0.2.0/24,10.0.2.1
client:c0,r0,10.0.1.2
server:s0,r1,10.0.2.2
link:c0,r0,{access_delay},{access_queue},{access_bw},0.0,0
link:r0,r1,{wan_delay},{wan_queue},{wan_bw},{loss},0
link:s0,r1,{access_delay},{access_queue},{access_bw},0.0,0
transitSubnet:172.31.0.0/16
changeNetem:false
```

**`link:` 줄의 필드 순서** (GAIA `core/netmodel.py` 기준):

```
link:<노드A>,<노드B>,<편도지연ms>,<queue_size>,<대역폭Mbps>,<손실%>,<backup>
```

라우터를 하나 더 넣어 3홉으로 만들려면:

```
router:r2,10.0.3.0/24,10.0.3.1
link:r1,r2,10.0,100,50.0,0.0,0
```
서버를 `r2`에 붙이고(`server:s0,r2,10.0.3.2`) **인증서 SAN을 새 IP로 재생성**해야 합니다.

> 서버 IP를 바꾸면 `write_xp()`의 `-server-name 10.0.2.2`도 함께 고쳐야 합니다.
> 이 값은 TLS SNI/인증서 검증에 쓰입니다.

### 7.6 실행 중 네트워크 조건 변경 (netemAt)

GAIA는 실험 도중 `tc netem`을 바꾸는 기능을 지원합니다. 링크 단절/복구 같은
시나리오를 만들 때 씁니다. 토폴로지 파일에 추가하세요.

```
changeNetem:true
netemAt_l_1:10.0,loss 20
netemAt_l_1:20.0,loss 0
```

`netemAt_<링크타입>_<링크인덱스>` 형식이고, 링크 인덱스는 `link:` 줄의
등장 순서(0부터)입니다. 위 예는 **1번 링크(WAN)** 에 10초 시점에 손실 20%를
걸고 20초에 해제합니다.

> ⚠️ 시각은 **오름차순**으로 적어야 합니다. 순서가 뒤집히면 GAIA가 무시하고
> 에러 로그만 남깁니다.

### 7.7 클라이언트 세부 플래그

`gen_scenarios.py`의 `write_xp()` 안 `client_cmd`가 최종 명령줄을 만듭니다.
`edgegw`가 받는 전체 플래그:

| 플래그 | 기본값 | 설명 |
|---|---|---|
| `-server` | `127.0.0.1` | Cloud Gateway 주소 |
| `-mode` | `grpc` | `rest` / `grpc` / `h3` / `qtun` |
| `-port` | 모드별 기본값 | 포트 강제 지정 |
| `-streams` | `8` | 동시 스트림 수 |
| `-batch` | `20` | 배치당 측정값 |
| `-pad` | `0` | 측정값당 더미 바이트 |
| `-duration` | `30s` | 측정 구간 |
| `-warmup` | `3s` | 제외할 준비 구간 |
| `-inflight` | `1` | 스트림당 미확인 배치 수 |
| `-rate` | `0` | 스트림당 초당 배치 수 (`0`=클로즈드 루프) |
| `-bucket` | `100ms` | HoL 지표의 타임라인 버킷 폭 |
| `-out` | (stdout) | 결과 JSON 경로 |
| `-ca` | — | 검증용 CA 인증서 |
| `-insecure` | `false` | 인증서 검증 생략 |
| `-server-name` | `-server` 값 | SNI 오버라이드 |
| `-label` | — | 결과에 기록할 라벨 |
| `-connect-timeout` | `30s` | 연결·스트림 개설 제한 시간 |
| `-seed` | `1` | 생성기 시드(재현성) |
| `-param k=v` | — | 결과 JSON에 임의 메타데이터 기록 (반복 가능) |

`-param`은 분석기가 그대로 읽어 축으로 씁니다. 새 변인을 추가했다면
`-param` 으로 찍어두면 CSV에 자동으로 따라옵니다.

### 7.8 서버 플래그

| 플래그 | 기본값 | 설명 |
|---|---|---|
| `-certs` | `./certs` | 인증서 디렉터리 |
| `-bind` | `0.0.0.0` | 바인드 주소 |
| `-modes` | `rest,grpc,h3,qtun` | 리슨할 전송 목록 |
| `-rest-port` | `8443` | REST (TCP) |
| `-grpc-port` | `8444` | gRPC/HTTP2 (TCP) |
| `-h3-port` | `8445` | gRPC/HTTP3 (UDP) |
| `-qtun-port` | `8446` | raw QUIC (UDP) |
| `-ready-file` | — | 모든 리스너가 뜨면 이 파일 생성 |
| `-v` | `false` | 배치마다 로그 (**타이밍을 왜곡하므로 디버깅 전용**) |

포트 기본값은 `rpcbench/internal/transport/transport.go`의 `DefaultPorts`에
있습니다.

### 7.9 QUIC 튜닝

`rpcbench/internal/transport/grpch3.go`의 `QUICConfig()`:

```go
MaxIdleTimeout:                 60 * time.Second,
KeepAlivePeriod:                15 * time.Second,
MaxIncomingStreams:             1024,
InitialStreamReceiveWindow:     4 << 20,   // 4 MB
MaxStreamReceiveWindow:         16 << 20,  // 16 MB
InitialConnectionReceiveWindow: 8 << 20,   // 8 MB
MaxConnectionReceiveWindow:     64 << 20,  // 64 MB
```

수신 윈도가 경로 BDP(250 kB)보다 훨씬 크게 잡혀 있습니다. **의도적입니다.**
윈도가 병목이 되면 QUIC의 손실 복구가 아니라 quic-go의 기본 윈도 크기를
측정하게 됩니다. 대역폭이나 RTT를 크게 올렸다면(예: 1 Gbps × 200 ms) 이 값들도
같이 올려야 합니다.

---

## 8. 상황별 수정 레시피

### RTT를 100 ms로 바꾸기

```python
WAN_DELAY_MS = 48.0      # 2 × (1 + 48 + 1) = 100 ms
WAN_QUEUE    = 250       # 큐잉 ≈ 57 ms 확보
```
```bash
python3 gen_scenarios.py --explain   # base RTT 100.0 ms 확인
```

### 대역폭을 10 Mbps로 낮추기 (저속 IoT 백홀)

```python
WAN_BW_MBPS = 10.0
WAN_QUEUE   = 20         # 10 Mbps에서 queue_size=100은 큐잉 114 ms → 과도
```
반드시 `--explain`으로 큐잉 지연을 확인하세요. 대역폭이 내려가면 같은
`queue_size`가 훨씬 큰 지연을 만듭니다.

### 스트림 수를 32개로 (HoL 효과 증폭)

```python
REGIMES["hol"]["streams"] = 32
```
`stall_correlation`의 이론적 기준선이 `1/8 = 0.125`에서 `1/32 = 0.031`로
내려가므로, TCP 공유 스트림과의 대비가 더 선명해집니다.

### 버스트 손실 (Gilbert-Elliott)

현재 netem은 **독립 균등 손실**입니다. 실제 무선은 버스트성이라 QUIC의
이점이 더 크게 나올 가능성이 높습니다. `gen_scenarios.py`의 `TOPO_TEMPLATE`
대신 `netemAt`으로 `loss gemodel p r 1-h 1-k` 문법을 주입해야 합니다.
(GAIA의 `build_netem_cmd`가 `cmd` 문자열을 그대로 넘기므로 가능합니다.)

### 초고속 스모크 (5분)

```bash
python3 gen_scenarios.py --gaia <g> --rpcbench <r> \
    --modes grpc,h3 --losses 0,2 --regimes hol --repeats 1
sudo REPEATS=1 ./run_sweep.sh
```
추가로 `REGIMES["hol"]["duration"] = 10`, `warmup = 2`로 줄이면 더 빨라집니다.

### 결과를 다 버리고 처음부터

```bash
rm -rf <gaia>/output/test
sudo mn -c                              # Mininet 잔여 상태 정리
cd rpcbench && ./build.sh clean && ./build.sh
```

---

## 9. 문제 해결

### `rpcbench.json`이 안 생김

```bash
cat <gaia>/output/test/<셀>/rep1/exec.log        # GAIA 단계 로그
cat <gaia>/output/test/<셀>/rep1/workload-1.log  # edgegw 출력
cat <gaia>/output/test/<셀>/rep1/workload-0.log  # cloudgw 출력
```

가장 흔한 원인 2가지:
1. **인증서 SAN 불일치** — `x509: cannot validate certificate for 10.0.2.2`
   → `HOSTS=...,10.0.2.2 ./build.sh certs`
2. **바이너리 경로** — `manifest.json`의 `rpcbench` 경로가 절대 경로이고
   해당 위치에 `bin/`, `certs/`가 실제로 있어야 합니다.

### `no batches completed inside the measurement window`

결과 JSON의 `totals.first_error`에 첫 실패 원인이 기록됩니다.

```bash
python3 -c "import json;print(json.load(open('rpcbench.json'))['totals'])"
```

`connection refused`면 서버가 안 뜬 것 — `workload-0.log`를 보세요.

### gRPC 결과가 QUIC과 똑같이 나옴

**8개 스트림이 정말로 TCP 연결 하나를 공유했는지** 확인해야 합니다. 실험 중
c0 네임스페이스에서:

```bash
sudo mnexec -a <c0의 PID> ss -tin 'dport = :8444'
```

established 연결이 **정확히 1개**여야 합니다. `edgegw`는 HTTP/2 협상 실패 시
즉시 중단하지만, 스트림 한계로 두 번째 연결이 열리면 조용히 효과가 사라집니다.

### 프리플라이트가 5% 손실에서 실패

정상입니다. `run_sweep.sh`가 경고만 남기고 계속 진행하며, 개별 run에는
`--skip-preflight`를 넘깁니다.

### Mininet 잔여 상태

```bash
sudo mn -c
```
스윕 전후로 한 번씩 돌리면 깔끔합니다.

### `does not look like a gaia-experiment checkout` (경로 문제)

거의 항상 **root 셸에서 `~`를 쓴 경우**입니다. `~`는 셸이 먼저 풀어버리는데,
root로는 `/root`가 되어 실제 프로젝트 위치와 무관해집니다.

```bash
# ✗ root 셸에서 /root/A-gRPC-over-QUIC-Multi-Stream/... 을 가리킴
python3 gen_scenarios.py --gaia ~/A-gRPC-over-QUIC-Multi-Stream/gaia-experiment-main

# ✓ 생략 (자동 탐지)
python3 gen_scenarios.py

# ✓ 상대 경로
python3 gen_scenarios.py --gaia ../gaia-experiment-main --rpcbench ../rpcbench
```

스크립트가 올바른 후보를 찾으면 `did you mean:` 줄로 알려줍니다.

### `go: module ... 403 Forbidden` / 다운로드 실패

사내망 프록시 환경일 수 있습니다.

```bash
go env -w GOPROXY=https://proxy.golang.org,direct
# 프록시 뒤라면
export HTTPS_PROXY=http://<프록시>:<포트>
```

### 포트 충돌

```bash
sudo ss -lntup | grep -E '844[3-6]'
```
다른 프로세스가 쓰고 있으면 `cloudgw`의 `-*-port` 플래그와
`transport.go`의 `DefaultPorts`를 함께 바꾸세요.
