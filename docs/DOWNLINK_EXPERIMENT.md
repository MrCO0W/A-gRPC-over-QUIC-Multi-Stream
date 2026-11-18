# 실험 C: 하향 명령 전달 지연 (REST 폴링·SSE 대 gRPC 스트림)

에이전트가 상위(Edge Master)에서 판단한 제어 명령이 Edge Agent에 도착하기까지 걸리는 시간을, 전달 방식과 HTTP 버전을 바꿔 가며 측정하는 실험입니다. 기존 실험(실험 A·B)이 상향 텔레메트리의 전송 계층 효과를 다뤘다면, 이 실험은 "gRPC를 쓰는 이유"인 하향 경로를 직접 측정합니다.

- 네트워크는 기존 실험과 같습니다. gen_ctrl_scenarios.py가 gen_scenarios.py의 링크 상수와 토폴로지 작성 함수를 그대로 가져다 씁니다(RTT 40 ms, 50 Mbit/s, WAN 링크 양방향 netem 손실).
- 기존 코드와 결과는 바꾸지 않았습니다. 새 파일을 추가했고, 기존 파일은 build.sh에서 새 바이너리 2개를 함께 빌드하도록 한 줄만 고쳤습니다.

---

## 1. 무엇을 비교하나

### 전달 방식 4종

| 이름 | 방식 | 명령이 내려가는 과정 |
|---|---|---|
| poll | REST 짧은 폴링 | Edge Agent가 주기마다 "새 명령 있나요?"를 묻고, 서버는 바로 답합니다(빈 응답 포함). 주기는 1 s와 0.1 s |
| lpoll | REST 롱 폴링 | 요청을 서버가 명령이 생길 때까지(최대 25 s) 붙잡고 있다가 답하고, Edge Agent는 즉시 다시 묻습니다 |
| sse | REST + Server-Sent Events | 오래 열어 두는 GET 응답 하나로, 서버가 명령이 생기는 즉시 이벤트로 써 보냅니다 |
| grpc | gRPC 양방향 스트리밍 | Edge Agent가 연결 직후 Control.Session 호출을 열어 두고, 상위가 그 스트림으로 명령을 밀어 보냅니다. 확인 응답도 같은 호출로 올라갑니다 |

### HTTP 버전 3종

| 이름 | 전송 | 연결 사용 |
|---|---|---|
| h1 | HTTP/1.1, TLS 1.3, TCP | 한 연결에 한 번에 한 요청만 가능하므로 구성 요소마다 연결을 따로 엽니다(명령 수신 1개, 확인 응답 최대 4개, 상향 텔레메트리 스트림마다 1개) |
| h2 | HTTP/2, TLS 1.3, TCP | 명령, 확인 응답, 상향 텔레메트리가 모두 TCP 연결 하나를 공유합니다 |
| h3 | HTTP/3, QUIC | 모두 QUIC 연결 하나를 공유합니다 |

gRPC는 HTTP/2 이상에서만 정의되므로 grpc-h1은 없습니다. 그 결과 기본 매트릭스는 14개 구성입니다. poll은 h1·h2·h3 × 주기 2종이고, lpoll·sse는 h1·h2·h3, grpc는 h2·h3입니다.

REST를 HTTP/2와 HTTP/3 위에서도 돌리는 이유는 공정성입니다. "같은 HTTP 버전 위의 REST 대 gRPC"를 비교해야 gRPC 고유의 효과를 분리할 수 있습니다. REST를 HTTP/1.1로만 두면 차이의 상당 부분이 HTTP 버전 차이가 됩니다.

### 부하 조건 2종

| 이름 | 내용 | 무엇을 보나 |
|---|---|---|
| idle | 명령 경로만 동작 | 전달 방식 자체의 지연, 명령당 요청 수와 링크 바이트 |
| busy | 상향 텔레메트리 4스트림(배치 20, 미완료 1개)을 함께 운용 | h2·h3에서는 텔레메트리와 명령이 한 연결을 공유하므로, 텔레메트리 패킷 손실이 명령을 붙잡는지(h2) 붙잡지 않는지(h3) |

손실률은 방향당 0, 1, 2, 5 %입니다.

---

## 2. 어떻게 측정하나

### 명령 생성 (ctrlgw, s0 = Edge Master 역할)

- Edge Agent가 처음 접속하는 순간부터 그 Edge Agent 전용 명령을 포아송 과정으로 만듭니다. 기본값은 초당 5개, 명령당 64 B 패딩입니다.
- 지수 분포 간격을 쓰는 이유: 고정 간격이면 명령 발생 시점이 폴링 주기와 위상이 맞아떨어져 폴링이 실제보다 좋거나 나쁘게 보일 수 있기 때문입니다.
- 명령에는 순번, 대상 장치, 연산, 값, 발행 시각, 유효 구간이 들어갑니다(proto/control.proto).

### 수신과 확인 응답 (ctrledge, c0 = Edge Agent 역할, 측정 주체)

- 명령을 받는 순간의 시각을 기록하고, 구동기 대신 즉시 반환하는 스텁을 호출한 뒤 확인 응답을 보냅니다.
- REST 방식은 확인 응답을 POST /v1/agents/{id}/acks로 보냅니다. 이전 응답을 기다리지 않고 바로 보내며, 최대 4개 요청을 동시에 띄웁니다. 순차 전송으로 REST를 불리하게 만들지 않기 위해서입니다.
- gRPC는 같은 Session 호출 위로 CommandAck 메시지를 하나씩 보냅니다.

### 지표

| 지표 | 정의 | 비고 |
|---|---|---|
| 명령 지연 (latency) | 상위가 명령을 발행한 시각부터 Edge Agent가 그 명령을 손에 쥔 시각까지, 편도 | Mininet 호스트는 같은 커널 시계를 쓰므로 편도 측정이 정확합니다. 주 지표 |
| 확인 지연 (confirm) | 발행부터 상위가 그 명령의 확인 응답을 받기까지 | 판단에서 실행 확인까지의 전체 루프 |
| 누락 (missing) | 측정 구간에 발행되었으나 끝까지(구간 종료 + 3 s) 도착하지 않은 명령 수 | 실행 후 서버 원장(ledger)과 대조 |
| 명령당 HTTP 요청 수 | (폴링 요청 + 확인 응답 POST 요청) ÷ 전달된 명령 | 전달 방식의 제어 부담. gRPC는 실행 내내 열린 호출 하나뿐이고 확인 응답은 그 위의 메시지(ack_messages)라 거의 0입니다 |
| 명령당 링크 바이트 | Edge Agent 호스트 인터페이스 송수신 바이트 ÷ 전달된 명령 | /proc/net/dev 기준이라 Ethernet, IP, TCP/UDP, TLS, QUIC, HTTP 헤더가 모두 포함됩니다. idle 조건에서만 의미가 있습니다 |
| 상향 텔레메트리 전송률 | busy 조건에서 함께 보낸 측정치/초와 99백분위 RTT | 명령 경로가 텔레메트리를 방해하는지 확인 |

- 실행당 측정 구간은 30 s, 명령은 약 150개입니다.
- 분석 스크립트는 반복 실행의 명령 지연을 모두 합친 뒤 백분위를 구합니다. 5회 반복이면 약 750개 표본이라 99백분위가 상위 7~8개 표본으로 정해집니다. 실행별 99백분위의 중앙값과 최소·최대도 함께 냅니다.

### 예상 (손실 0 %, 기준 RTT 40 ms)

| 방식 | 예상 명령 지연 | 이유 |
|---|---|---|
| poll, 1 s | 중앙값 약 520 ms, 최대 약 1,020 ms | 다음 폴링까지 평균 반 주기를 기다림 |
| poll, 0.1 s | 중앙값 약 70 ms | 같은 이유. 대신 명령당 요청 수가 크게 늘어남 |
| lpoll | 대부분 약 20 ms, 일부 약 60 ms | 응답을 받고 다음 요청이 서버에 닿기 전(편도 20 ms) 사이에 생긴 명령은 1왕복을 더 기다림 |
| sse, grpc | 약 20 ms | 열려 있는 스트림으로 즉시 밀어 보냄 |

손실이 있으면 다음 차이가 드러날 것으로 봅니다.

- h2는 TCP 하나를 공유하므로, 텔레메트리나 확인 응답의 손실이 명령 전달을 함께 멈춥니다. 특히 busy 조건에서 그렇습니다.
- h3는 QUIC 스트림이 독립이므로 명령 스트림의 손실만 명령을 늦춥니다.
- h1은 연결이 분리되어 있어 다른 흐름의 손실 영향은 없습니다. 대신 연결 수가 많고, 폴링은 손실 재전송이 다음 폴링까지 밀립니다.

---

## 3. 추가된 파일

```text
rpcbench/
├── proto/control.proto            # 명령·확인 응답·Session 서비스 규범 정의
├── cmd/ctrlgw/main.go             # 상위(Edge Master) 쪽 서버
├── cmd/ctrledge/main.go           # Edge Agent 쪽, 측정 주체
├── internal/control/hub.go        # Edge Agent별 포아송 명령 생성기와 원장
├── internal/control/server.go     # 폴링·롱 폴링·SSE·gRPC Session·확인 응답·텔레메트리 수신 엔드포인트, h1/h2/h3 리스너
├── internal/control/edge.go       # 4가지 수신 방식, 확인 응답, 상향 텔레메트리, 원장 조회
├── internal/control/procnet.go    # /proc/net/dev 인터페이스 카운터
├── internal/wire/control.go       # control.proto 수기 인코더(기존 wire.go와 같은 방식)
├── internal/wire/control_test.go
├── internal/transport/export.go   # 기존 텔레메트리 핸들러·gRPC 스트림 클라이언트를 외부에서 쓰도록 노출(기존 동작 변경 없음)
└── internal/stats/export.go       # DistOf 노출
scripts/
├── gen_ctrl_scenarios.py          # GAIA 시나리오 + manifest_ctrl.json 생성
├── run_ctrl_sweep.sh              # 스윕 실행(run_sweep.sh와 같은 방식, 결과 파일 ctrlbench.json)
└── analyze_ctrl.py                # 집계 → summary_ctrl.csv, report_ctrl.md, SVG 그림
```

변경한 기존 파일은 rpcbench/build.sh 한 줄입니다. 빌드 대상에 ctrlgw, ctrledge를 추가했습니다.

---

## 4. 실행 방법

기존 실험을 돌리던 리눅스 호스트(Mininet, GAIA)에서 그대로 실행합니다. 서버 배치(/home/user/Desktop/test 아래에 gaia-experiment-main, rpcbench, scripts가 나란히 있는 구조)를 그대로 쓰므로, 경로 인자 없이 자동 탐지됩니다. 생성되는 시나리오는 서버의 GAIA workload_exp 파서(custom_server, custom_client, {server_ip:s0}, {output_dir})로 검증했습니다.

### 4.1 빌드

```bash
cd rpcbench && ./build.sh build    # 바이너리 5개(cloudgw, edgegw, gencerts, ctrlgw, ctrledge)
```

새 외부 의존성은 없고(quic-go v0.48.2 그대로) go.mod, go.sum도 바꾸지 않았습니다. 서버에는 이미 certs/와 모듈 캐시가 있으므로 build만 하면 되고, 인터넷 연결도 필요 없습니다. 인자 없이 ./build.sh를 실행하면 go mod tidy와 인증서 재생성까지 하므로 이번에는 필요하지 않습니다.

### 4.2 동작 확인 (Mininet 없이, 1분)

```bash
./bin/ctrlgw -certs certs -bind 127.0.0.1 &
for m in poll-h1 poll-h2 poll-h3 lpoll-h1 lpoll-h2 lpoll-h3 sse-h1 sse-h2 sse-h3 grpc-h2 grpc-h3; do
  ./bin/ctrledge -server 127.0.0.1 -app ${m%-*} -proto ${m#*-} -poll-interval 200ms \
                 -duration 4s -warmup 1s -grace 1s -up-streams 2 -ca certs/ca.pem -label smoke
done
kill %1
```

11개 구성 모두 마지막 줄이 "delivered N/N (missing 0)"이어야 합니다. 루프백에서는 지연이 1 ms 미만으로 나오고, 링크 바이트는 의미가 없습니다.

### 4.3 시나리오 생성

```bash
cd ../scripts
python3 gen_ctrl_scenarios.py --explain     # 조건, 예상 지연, 총 실행 수와 소요 시간 확인
python3 gen_ctrl_scenarios.py               # 전체 매트릭스 (14구성 × 손실 4 × 조건 2 = 112셀, 5회 반복)
```

전체 매트릭스는 560회입니다. 서버의 기존 스윕 기록(sweep-run.out)에서 35 s짜리 실행이 약 40 s 걸렸으므로, 실험 C는 실행당 약 45 s, 전체 약 7시간이 걸립니다. 먼저 핵심만 돌리려면 다음처럼 줄입니다.

```bash
# 핵심 6구성, 3회 반복: 6 × 4 × 2 × 3 = 144회, 약 2시간
python3 gen_ctrl_scenarios.py --modes poll_h1_i1000,lpoll_h2,sse_h2,sse_h3,grpc_h2,grpc_h3 --repeats 3
```

### 4.4 스윕

```bash
sudo ./run_ctrl_sweep.sh
sudo REPEATS=1 ./run_ctrl_sweep.sh                 # 빠른 1회 패스
sudo ONLY='_busy_(sse|grpc)_' ./run_ctrl_sweep.sh   # 일부 셀만
```

결과는 <gaia>/output/test_ctrl/<시나리오>/rep<N>/ctrlbench.json에 쌓입니다. 중단 후 같은 명령을 다시 실행하면 끝난 셀은 건너뜁니다.

### 4.5 분석

```bash
python3 analyze_ctrl.py --results <gaia>/output/test_ctrl --out report_ctrl
```

| 산출물 | 내용 |
|---|---|
| summary_ctrl.csv | 셀마다 한 줄: 지연 p50/p90/p99/최대, 실행별 p99 중앙값과 범위, 확인 지연, 누락, 명령당 요청 수와 바이트, 빈 폴링 비율, 재연결, 상향 텔레메트리 |
| report_ctrl.md | 조건별 표(지연 p50, p99, 확인 지연, 요청 수, 바이트 또는 텔레메트리, 누락) |
| fig_c1_latency_idle.svg, fig_c1_latency_busy.svg | 손실률에 따른 명령 지연(실선 p99, 점선 p50, 로그 축) |
| fig_c2_cost_idle.svg | 손실 0 %에서 명령당 요청 수와 링크 바이트 |

---

## 5. 파라미터 수정

gen_ctrl_scenarios.py 상단에서 조정합니다. 네트워크 값은 gen_scenarios.py를 고치면 두 실험에 함께 적용됩니다.

```python
CTRL_LOSSES = [0.0, 1.0, 2.0, 5.0]
CTRL_MODES  = [("poll","h1",1.0), ..., ("grpc","h3",None)]
CTRL_REGIMES = {"idle": dict(up_streams=0, up_batch=20), "busy": dict(up_streams=4, up_batch=20)}
CMD_RATE = 5.0      # 명령/초 (포아송)
CMD_BYTES = 64      # 명령당 패딩
DURATION_S = 30; WARMUP_S = 5; GRACE_S = 3
```

- 명령 빈도를 낮추면(예: 초당 1개) 실제 제어 빈도에 가깝지만 실행당 표본이 줄어듭니다. 그때는 DURATION_S나 반복 수를 늘리십시오.
- 롱 폴링의 최대 대기는 internal/control/server.go의 LongPollMax(25 s)입니다.
- REST 확인 응답 동시 요청 수는 internal/control/edge.go의 AckParallel(4)입니다.

---

## 6. 결과를 해석할 때 주의할 점

1. 이 실험은 전달 방식(응용 계층 패턴)과 HTTP 버전을 함께 바꿉니다. 같은 HTTP 버전끼리(sse-h2 대 grpc-h2, sse-h3 대 grpc-h3) 비교해야 gRPC 고유의 차이가 됩니다. poll-h1 대 grpc-h3는 "관행적 구성 대 제안 구성"의 비교입니다.
2. SSE와 gRPC는 손실이 없으면 지연이 거의 같게 나올 것입니다. 둘 다 열린 스트림으로 밀어 보내기 때문입니다. gRPC의 차이는 다음에서 찾아야 합니다.
   - 확인 응답이 같은 호출로 올라가는지(gRPC) 별도 요청인지(REST)
   - 명령당 요청 수와 바이트
   - 하나의 호출과 하나의 .proto 계약으로 양방향을 처리하는지
   이 점을 미리 알고 결과를 읽으시면 됩니다.
3. 링크 바이트는 idle 조건에서만 명령 경로의 비용입니다. busy 조건에서는 텔레메트리 바이트가 섞입니다.
4. 구동기는 즉시 반환하는 스텁입니다. 장치 동작 시간은 측정 대상이 아닙니다.
5. 단일 Edge Agent, 단일 경로입니다. 독립 균등 손실이고 RTT는 40 ms 고정입니다. 기존 실험과 같은 제약입니다.
6. quic-go v0.48.2 버그: HTTP/3 서버는 클라이언트가 취소한 스트림에 트레일러를 쓰지 못하면, Logger가 비어 있을 때 nil 포인터로 서버 전체가 종료됩니다. ctrlgw는 버리는 로거를 넣어 막았습니다. 기존 cloudgw의 h3 서버에도 같은 잠재 문제가 있습니다. 다만 실행마다 서버를 새로 띄우고, 이 문제는 측정이 끝나 클라이언트가 스트림을 닫을 때 생기므로 기존 결과에는 영향이 없습니다.

---

## 7. 결과 파일 형식 (ctrlbench.json, schema test.ctrlbench.result/1)

```text
mode, app, proto, label, params{loss_pct, regime, poll_interval_s, cmd_rate, cmd_bytes, up_streams, ...}
downlink { issued, ledger_ok, delivered, missing, duplicates,
           latency{count, mean_ms, p50_ms, p90_ms, p99_ms, max_ms, std_ms},
           confirm{...}, latencies_ms[...] }
control  { poll_requests, empty_polls, ack_requests, ack_messages, ack_dropped,
           reconnects, errors, first_error, requests_per_command }
link     { interfaces, tx_bytes, rx_bytes, tx_packets, rx_packets, bytes_per_command }
uplink   { streams, batch, messages_per_s, rtt{...}, errors }        (busy 조건만)
```

---

## 8. 작업 공간 시범 실행 결과 (참고용, 논문 인용 금지)

코드가 의도대로 동작하는지 확인하려고 Claude 작업 공간(리눅스 컨테이너)에서 한 번 돌린 결과입니다. 이 환경에는 netem이 없어서, 같은 조건(편도 20 ms, 방향당 50 Mbit/s, 약 362 kB 큐, 방향당 독립 균등 손실)을 흉내 내는 사용자 공간 브리지를 따로 만들어 썼습니다. 이 브리지는 납품물에 넣지 않았습니다.

- 셀마다 1회, 측정 구간 20 s(명령 약 100개)입니다. 99백분위가 사실상 최댓값 1~2개로 정해지므로, 손실 조건의 꼬리 값은 흔들림이 큽니다. 경향을 확인하는 용도로만 보십시오.
- 같은 에뮬레이터에서 기존 edgegw를 돌려 대조했습니다. 손실 0 %에서 gRPC/HTTP/2와 HTTP/3 모두 약 3,800 측정치/초, 손실 1 %에서 HTTP/3 3,673(논문 3,702), 99백분위 RTT 89 ms(논문 87.9 ms)로 논문 값과 가까웠습니다. TCP 쪽은 3,245로 논문(2,611)보다 높게 나와, 커널 TCP 동작은 연구실 호스트와 다를 수 있습니다.
- 88회 모두 누락 0건이었습니다.

### 손실 0 %, idle (명령 경로만)

| 방식 | p50 (ms) | p99 (ms) | 확인 지연 p99 (ms) | 명령당 HTTP 요청 | 명령당 링크 바이트 |
|---|---|---|---|---|---|
| REST 폴링 1 s, HTTP/1.1 | 556.7 | 969.4 | 991.0 | 0.39 | 537 |
| REST 폴링 0.1 s, HTTP/1.1 | 67.4 | 119.8 | 140.9 | 2.71 | 1,904 |
| REST 롱 폴링, HTTP/2 | 21.1 | 60.2 | 81.0 | 1.86 | 1,221 |
| REST + SSE, HTTP/2 | 20.8 | 21.6 | 43.7 | 0.99 | 915 |
| REST + SSE, HTTP/3 | 20.9 | 21.7 | 43.3 | 0.98 | 892 |
| gRPC 스트림, HTTP/2 | 20.8 | 21.5 | 42.8 | 0 | 508 |
| gRPC 스트림, HTTP/3 | 20.8 | 21.6 | 42.5 | 0 | 445 |

- 폴링은 주기의 절반만큼 늦습니다. 주기를 줄이면 지연은 줄지만 요청과 바이트가 크게 늘어납니다.
- 롱 폴링은 중앙값은 푸시 방식과 같지만, 응답과 다음 요청 사이에 생긴 명령이 1왕복을 더 기다려 p99가 약 60 ms입니다.
- SSE와 gRPC는 지연이 같습니다(편도 약 21 ms). 같은 HTTP 버전에서 차이는 비용에 있습니다.
  - REST는 명령마다 확인 응답용 HTTP 요청이 따로 필요합니다(약 1개/명령).
  - gRPC는 열린 호출 하나로 끝납니다.
  - 링크 바이트는 gRPC가 SSE의 약 절반입니다(HTTP/3에서 445 대 892 B).

### 손실 5 %, busy (텔레메트리 4스트림 동반)

| 방식 | 명령 p99 (ms) | 확인 지연 p99 (ms) | 함께 보낸 텔레메트리 (측정치/초) |
|---|---|---|---|
| REST 폴링 1 s, HTTP/1.1 | 1,079.5 | 1,449.0 | 767 |
| REST 롱 폴링, HTTP/2 | 321.6 | 411.8 | 903 |
| REST + SSE, HTTP/2 | 90.4 | 410.3 | 891 |
| REST + SSE, HTTP/3 | 89.7 | 123.3 | 1,192 |
| gRPC 스트림, HTTP/2 | 176.9 | 239.9 | 1,144 |
| gRPC 스트림, HTTP/3 | 83.8 | 105.0 | 1,589 |

- 같은 HTTP/3 위에서 gRPC는 SSE와 명령 지연이 비슷했습니다. 그런데 같은 연결로 함께 보낸 텔레메트리는 1,589 대 1,192로 더 많았고, 확인 지연도 더 짧았습니다(105.0 대 123.3 ms).
- 텔레메트리 차이의 원인은 다음과 같이 추정합니다. REST는 배치마다 새 요청과 JSON 본문(약 2.7 kB, 패킷 2개)을 보내고, gRPC는 열린 스트림에 Protobuf 메시지(약 1.1 kB, 패킷 1개)를 씁니다. 따라서 손실에 노출되는 패킷 수가 다릅니다.
- HTTP/2(TCP 하나 공유)는 HTTP/3보다 명령 꼬리와 확인 지연이 깁니다. 실험 A·B와 같은 방향입니다.
- 반복 1회라 이 값들의 순위는 바뀔 수 있습니다. 본 실험에서 5회 반복으로 확인해야 합니다.
