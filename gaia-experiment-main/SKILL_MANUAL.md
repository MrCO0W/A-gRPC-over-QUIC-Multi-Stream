# 📘 GAIA MAM 자동화 스킬 사용 매뉴얼 (SKILL_MANUAL.md)

이 문서는 GAIA 네트워크 에뮬레이션 환경을 제어하고 자동화하기 위해 구축된 **Multi-Agent Mux (MAM) 특화 자동화 스킬 3종** 및 **다중 에이전트 자율 오케스트레이션 루프**의 상세 사용법을 안내합니다.

---

## 📑 목차
1. [스킬 개요 및 워크플로우](#1-스킬-개요-및-워크플로우)
2. [gaia-topo-gen (네트워크 토폴로지 생성)](#2-gaia-topo-gen-네트워크-토폴로지-생성)
   - [2.4 기존 토폴로지 기반 파라미터 변경 및 새 이름 저장 (--base-topo)](#24-기존-토폴로지-기반-파라미터-변경-및-새-이름-저장---base-topo)
   - [2.6 SKILL 및 CLI를 이용한 토폴로지 검증 (WebUI 생성 파일 포함)](#26-skill-및-cli를-이용한-토폴로지-검증-webui-생성-파일-포함)
3. [gaia-scenario-gen (실험 시나리오 생성)](#3-gaia-scenario-gen-실험-시나리오-생성)
   - [3.4 기존 시나리오 기반 파라미터 변경 및 새 이름 저장 (--base-xp)](#34-기존-시나리오-기반-파라미터-변경-및-새-이름-저장---base-xp)
4. [gaia-run-analyze (실험 실행 및 분석)](#4-gaia-run-analyze-실험-실행-및-분석)
5. [다중 에이전트 자율 루프 연동 가이드](#5-다중-에이전트-자율-루프-연동-가이드)
6. [실전 E2E 튜토리얼 (Step-by-Step)](#6-실전-e2e-튜토리얼-step-by-step)

---

## 1. 스킬 개요 및 워크플로우

GAIA 자동화 스킬은 자연어 요구사항으로부터 실제 네트워크 실험 수행 및 분석 리포트 도출까지의 전 과정을 모듈화하여 제공합니다.

```mermaid
flowchart LR
    A["자연어 요구사항 / 사양"] --> B["<b>gaia-topo-gen</b><br/>(config/topo/* 생성)"]
    B --> C["<b>gaia-scenario-gen</b><br/>(config/xp/* 생성)"]
    C --> D["<b>gaia-run-analyze</b><br/>(Preflight ➜ Run ➜ Analyze)"]
    D --> E["📊 결과 리포트<br/>(CSV / JSON / SVG Chart)"]
```

| 스킬명 | 경로 | 핵심 역할 |
|---|---|---|
| **`gaia-topo-gen`** | `.agents/skills/gaia-topo-gen/` | 자연어/`TopoSpec` 기반 MultiIf/Generic 토폴로지 설정 파일 자동 생성 및 정적 검증 |
| **`gaia-scenario-gen`** | `.agents/skills/gaia-scenario-gen/` | 토폴로지 메타데이터 기반 `iperf3_exp`/`mesh_ping` 실험 파라미터 및 동적 Netem 스케줄 생성 |
| **`gaia-run-analyze`** | `.agents/skills/gaia-run-analyze/` | 사전 도달성 검증(Mesh Ping) ➜ 트래픽 실행 ➜ pcap 수집 ➜ 메트릭 분석/시각화 원클릭 실행 |

> [!NOTE]
> 모든 스킬 스크립트는 **워크스페이스 루트 디렉터리 기준 상대경로**로 실행됩니다.

---

## 2. gaia-topo-gen (네트워크 토폴로지 생성)

자연어 요구사항 또는 구조화된 JSON 사양(`TopoSpec`), 또는 기존 토폴로지 파일(`--base-topo`)을 입력받아 무결성이 검증된 토폴로지 파일(`config/topo/<name>`)을 생성합니다.

### 📌 CLI 옵션 상세 설명 (Options Reference)

| 옵션 플래그 | 타입 / 필수 여부 | 기본값 | 상세 설명 |
|---|---|:---:|---|
| **`--spec <경로\|JSON>`** | 문자열 (조건부 필수) | - | **새 토폴로지 사양 입력**.<br>• `spec.json` 파일 경로 또는 인라인 JSON 문자열(`'{"name": "...", ...}'`) 전달 |
| **`--base-topo <경로>`** | 파일 경로 (조건부 필수) | - | **기존 토폴로지 기반 파라미터 변경 모드**.<br>• 기존 설정 파일(예: `config/topo/topo_sc1_baseline`)을 로드하여 링크 파라미터만 오버라이드 |
| **`--name <이름>`** | 문자열 | - | **새로 생성할 토폴로지 파일명**.<br>• `--base-topo` 사용 시 필수.<br>• 영문, 숫자, `.`, `_`, `-` 허용, 최대 64자 |
| **`--bw`, `--bandwidth <Mbps>`** | 실수 (float) | - | 링크 대역폭(Bandwidth) 변경 (단위: Mbps, 예: `100`, `250.5`) |
| **`--delay <ms>`** | 실수 (float) | - | 링크 지연 시간(Delay) 변경 (단위: ms, 예: `10`, `5.0`) |
| **`--queue`, `--queue-size <크기>`** | 정수 (int) | - | 링크 인터페이스 큐 버퍼 크기(Queue Size) 변경 (예: `1000`, `2000`) |
| **`--loss <%>`** | 실수 (float) | - | 링크 패킷 손실률(Packet Loss) 변경 (단위: %, 예: `0`, `1.5`) |
| **`--output-dir <경로>`** | 디렉터리 경로 | `config/topo` | 생성된 토폴로지 파일이 저장될 출력 디렉터리 |
| **`--force`** | 플래그 (action) | `false` | 동일한 이름의 토폴로지 파일이 이미 존재할 경우 강제로 덮어쓰기 |
| **`--json`** | 플래그 (action) | `false` | 실행 결과(유효성, 반환코드, 생성 경로, 정적 검증 리포트)를 **기계 가독형 JSON**으로 표준 출력 |

---

### 2.1 토폴로지 계열(Family) 자동 선택 규칙
- **`mptcp_multipath`** (`clients == 1` & `servers == 1`): `MultiIf` 계열 선택 (MPTCP 다중 인터페이스/서브넷 매핑)
- **`mptcp_multipath`** (`clients > 1` 또는 `servers > 1`): `MultiInterfaceMultiClients` 계열 선택
- **`multi_router`** (`routers_count >= 2`): `Generic` 계열 선택 (중계 라우터 및 IPAM 자동 할당)
- **`single_path`**: `Generic` 계열 선택

### 2.2 TopoSpec JSON 스키마 예시

```json
{
  "name": "topo_mptcp_demo",
  "intent": "mptcp_multipath",
  "clients": 1,
  "servers": 1,
  "paths": [
    {"delay_ms": 10, "bandwidth_mbps": 100, "queue_size": 1000, "loss_pct": 0, "backup": false},
    {"delay_ms": 30, "bandwidth_mbps": 50, "queue_size": 1000, "loss_pct": 0, "backup": false}
  ],
  "server_paths": [
    {"delay_ms": 10, "bandwidth_mbps": 100, "queue_size": 1000, "loss_pct": 0, "backup": false}
  ],
  "netem_schedule": [
    {"path_type": "c2r", "path_id": 1, "at_sec": 5.0, "cmd": "loss 100%"}
  ]
}
```

### 2.3 실행 명령어

```bash
# 1. TopoSpec JSON 파일로부터 토폴로지 생성
python3 .agents/skills/gaia-topo-gen/scripts/generate.py --spec spec_topo.json --json

# 2. 인라인 JSON 문자열로 즉시 생성
python3 .agents/skills/gaia-topo-gen/scripts/generate.py \
  --spec '{"name": "topo_quick_test", "intent": "mptcp_multipath", "paths": [{"delay_ms": 20, "bandwidth_mbps": 100}, {"delay_ms": 20, "bandwidth_mbps": 100}]}' \
  --json

# 3. 기존 파일 강제 덮어쓰기 (--force)
python3 .agents/skills/gaia-topo-gen/scripts/generate.py --spec spec_topo.json --force
```

### 2.4 기존 토폴로지 기반 파라미터 변경 및 새 이름 저장 (`--base-topo`)

이미 존재하는 토폴로지(WebUI 생성 파일 또는 프리셋 파일)의 구조를 그대로 유지하면서, **`bandwidth` (대역폭), `delay` (지연시간), `queue_size` (큐 크기), `loss` (손실률)**만 변경하여 **새로운 이름으로 저장**할 수 있습니다.

#### ① CLI 명령어 직접 지정
```bash
# topo_sc1_baseline을 기반으로 대역폭 300Mbps, 지연시간 5ms로 변경하여 topo_sc1_fast로 저장
python3 .agents/skills/gaia-topo-gen/scripts/generate.py \
  --base-topo config/topo/topo_sc1_baseline \
  --name topo_sc1_fast \
  --bw 300 \
  --delay 5 \
  --queue 2000 \
  --json
```

#### ② JSON Spec 파일 사용
```json
{
  "name": "topo_sc2_fast_mptcp",
  "base_topo": "config/topo/topo_sc2_mptcp_2path",
  "override": {
    "bandwidth_mbps": 500,
    "delay_ms": 10,
    "queue_size": 1500,
    "loss_pct": 0
  }
}
```
```bash
python3 .agents/skills/gaia-topo-gen/scripts/generate.py --spec override_spec.json --json
```

### 2.5 반환 코드 (Exit Codes)
- `0`: 성공 (토폴로지 파일 생성 및 `cli.py verify` 검증 완료)
- `1`: 런타임 오류
- `2`: 사양 위반 또는 정적 검증 실패 (IFNAMSIZ 초과, 서브넷 충돌 등)

### 2.6 SKILL 및 CLI를 이용한 토폴로지 검증 (WebUI 생성 파일 포함)

WebUI(`Generate & Save`)를 통해 생성된 토폴로지 파일 또는 기존 설정 파일(`config/topo/*`)은 **`cli.py verify`** 또는 **`gaia-run-analyze` Dry-run**을 통해 Mininet 구동 없이 즉시 무결성을 정적 검증할 수 있습니다.

#### ① 기본 검증 (Human-readable)
```bash
# WebUI에서 생성된 토폴로지 파일 단독 검증
python3 cli.py verify -t config/topo/topo_sc1_baseline
```
- **출력 예시**:
  ```text
  [✓] Configuration is VALID.
    - Topology Type: Generic
    - Routers: 2, Hosts: 2, Links: 3
    - MPTCP Capable: False
  ```

#### ② 상세 기계 가독형 검증 (`--json`)
에이전트 또는 자동화 파이프라인에서 검증 결과를 세부적으로 파싱할 때 사용합니다:
```bash
python3 cli.py verify -t config/topo/topo_sc1_baseline --json
```

#### ③ 실험 시나리오와의 MPTCP 실현 가능성(Feasibility) 교차 검증
토폴로지가 특정 실험 시나리오(예: MPTCP 다중 경로)를 물리적으로 지원할 수 있는지 교차 검증합니다 (단일 IP 토폴로지에 `mptcp:yes` 시 fail-closed 기각):
```bash
python3 cli.py verify -t config/topo/topo_sc2_mptcp_2path -x config/xp/xp_sc2_mptcp_2path
```

#### ④ `gaia-run-analyze` 스킬을 통한 E2E 사전 무결성 점검 (Dry-run)
```bash
python3 .agents/skills/gaia-run-analyze/scripts/run.py \
  -t config/topo/topo_sc1_baseline \
  -x config/xp/xp_sc1_baseline \
  --dry-run \
  --json
```

---

## 3. gaia-scenario-gen (실험 시나리오 생성)

생성된 토폴로지의 메타데이터(MPTCP 지원 여부, 링크 개수 등)를 파싱하거나 기존 시나리오 파일(`--base-xp`)을 바탕으로 최적의 실험 시나리오 파일(`config/xp/<name>`)을 생성합니다.

### 📌 CLI 옵션 상세 설명 (Options Reference)

| 옵션 플래그 | 타입 / 필수 여부 | 기본값 | 상세 설명 |
|---|---|:---:|---|
| **`--spec <경로\|JSON>`** | 문자열 (조건부 필수) | - | **새 시나리오 사양 입력**.<br>• `spec.json` 파일 경로 또는 인라인 JSON 문자열(`'{"name": "...", ...}'`) 전달 |
| **`--base-xp`, `--base-scenario <경로>`** | 파일 경로 (조건부 필수) | - | **기존 시나리오 기반 파라미터 변경 모드**.<br>• 기존 시나리오 파일(예: `config/xp/xp_sc1_baseline`)을 로드하여 실험 파라미터 오버라이드 |
| **`--name <이름>`** | 문자열 | - | **새로 생성할 시나리오 파일명**.<br>• `--base-xp` 사용 시 필수.<br>• 영문, 숫자, `.`, `_`, `-` 허용, 최대 64자 |
| **`-t`, `--topo`, `--topo-file <경로>`** | 파일 경로 | - | 연동할 대상 토폴로지 설정 파일 경로 |
| **`--type`, `--xp-type <유형>`** | 문자열 | `iperf3_exp` | 실험 유형 (`iperf3_exp`, `mesh_ping`, `performance-analysis`) |
| **`--duration <초>`** | 정수 (int) | `10` | iperf3 트래픽 전송 지속 시간 (초 단위) |
| **`--parallel <개수>`** | 정수 (int) | `1` | iperf3 병렬 스트림 수 |
| **`--congctrl <알고리즘>`** | 문자열 | `cubic` | TCP 혼잡 제어 알고리즘 (예: `cubic`, `reno`, `bbr`) |
| **`--mptcp <auto\|yes\|no>`** | 문자열 | `auto` | MPTCP 활성화 모드 (`auto` 지정 시 토폴로지 지원 여부에 따라 자동 결정) |
| **`--pcap <yes\|no>`** | 문자열 | `no` | 인터페이스 패킷 캡처 (PCAP) 활성화 여부 |
| **`--reverse`** | 플래그 (action) | `false` | 역방향 트래픽 전송 (서버 ➔ 클라이언트) |
| **`--pa-protocol <udp\|tcp\|quic>`** | 문자열 | `udp` | `performance-analysis` 실험 프로토콜 |
| **`--pa-size <바이트>`** | 정수 (int) | `100` | `performance-analysis` 패킷 페이로드 크기 (Byte) |
| **`--pa-count <개수>`** | 정수 (int) | `10` | `performance-analysis` 전송 패킷 개수 |
| **`--pa-exp <하위유형>`** | 문자열 | `simple` | `performance-analysis` 하위 실험 유형 |
| **`--ping-count <개수>`** | 정수 (int) | `3` | `mesh_ping` 노드 간 ICMP 핑 전송 횟수 |
| **`--ping-timeout <초>`** | 정수 (int) | `1` | `mesh_ping` 핑 타임아웃 (초) |
| **`--max-probes <개수>`** | 정수 (int) | `200` | `mesh_ping` 최대 프로브 전송 횟수 |
| **`--strict-mesh`** | 플래그 (action) | `false` | 100% 도달성 강제 (패킷 손실 발생 시 즉시 실패 판정) |
| **`--output-dir <경로>`** | 디렉터리 경로 | `config/xp` | 생성된 시나리오 파일이 저장될 출력 디렉터리 |
| **`--force`** | 플래그 (action) | `false` | 동일한 이름의 시나리오 파일이 이미 존재할 경우 강제로 덮어쓰기 |
| **`--json`** | 플래그 (action) | `false` | 실행 결과(유효성, 반환코드, 생성 경로, 정적 검증 리포트)를 **기계 가독형 JSON**으로 표준 출력 |

---

### 3.1 실험 유형(xp_type) 매핑 규칙
- **MPTCP 처리량/서브플로우 측정**: `iperf3_exp` (`mptcp:yes`)
- **단일 경로 TCP 베이스라인**: `iperf3_exp` (`mptcp:no`)
- **전체 노드 도달성 점검**: `mesh_ping`
- **QUIC 프로토콜 에뮬레이션**: `performance-analysis` (`protocol:quic`)
- **노드별 이기종 커스텀 워크로드**: `workload_exp` (각 노드별 `iperf3_client`, `custom_server`, `custom_client` 등 지정)

### 3.2 ScenarioSpec JSON 스키마 예시

#### iperf3 실험 시나리오:
```json
{
  "name": "xp_mptcp_demo",
  "topo_file": "config/topo/topo_sc2_mptcp_2path",
  "xp_type": "iperf3_exp",
  "mptcp": "auto",
  "duration": 10,
  "parallel": 1,
  "congctrl": "cubic",
  "pcap": "yes",
  "base_xp": "config/xp/xp_sc1_baseline",
  "override": {
    "duration": 20,
    "parallel": 2,
    "pcap": "yes"
  }
}
```

#### 커스텀 워크로드 시나리오 (`workload_exp`):
```json
{
  "name": "xp_custom_http",
  "topo_file": "config/topo/topo_sc4_custom",
  "xp_type": "workload_exp",
  "duration": 10,
  "workloads": [
    {
      "node": "s0",
      "kind": "custom_server",
      "command": "python3 -m http.server 8080"
    },
    {
      "node": "c0",
      "kind": "custom_client",
      "command": "curl -s {server_ip:s0}:8080 -o {output_dir}/result.txt"
    },
    {
      "node": "h0",
      "kind": "none"
    }
  ]
}
```

### 3.3 실행 명령어

```bash
# 1. ScenarioSpec JSON 파일로부터 시나리오 생성
python3 .agents/skills/gaia-scenario-gen/scripts/generate.py --spec spec_xp.json --json

# 2. 인라인 JSON 문자열로 시나리오 생성
python3 .agents/skills/gaia-scenario-gen/scripts/generate.py \
  --spec '{"name": "xp_quick_iperf", "topo_file": "config/topo/topo_sc1_baseline", "xp_type": "iperf3_exp", "duration": 5}' \
  --json

# 3. 기존 시나리오 덮어쓰기 (--force)
python3 .agents/skills/gaia-scenario-gen/scripts/generate.py --spec spec_xp.json --force
```

### 3.4 기존 시나리오 기반 파라미터 변경 및 새 이름 저장 (`--base-xp`)

이미 존재하는 시나리오 파일의 설정을 유지하면서, **전송 시간(`duration`), 병렬 스트림 수(`parallel`), 혼잡 제어(`congctrl`), 패킷 캡처(`pcap`), 역방향 전송(`reverse`)** 등의 파라미터만 변경하여 **새로운 이름으로 저장**할 수 있습니다.

#### ① CLI 명령어 직접 지정
```bash
# xp_sc1_baseline을 기반으로 30초, 병렬 4스트림, PCAP 캡처 활성화하여 xp_sc1_long으로 저장
python3 .agents/skills/gaia-scenario-gen/scripts/generate.py \
  --base-xp config/xp/xp_sc1_baseline \
  --name xp_sc1_long \
  -t config/topo/topo_sc1_baseline \
  --duration 30 \
  --parallel 4 \
  --pcap yes \
  --json
```

#### ② JSON Spec 파일 사용
```json
{
  "name": "xp_sc2_heavy_mptcp",
  "topo_file": "config/topo/topo_sc2_mptcp_2path",
  "base_xp": "config/xp/xp_sc2_mptcp_2path",
  "override": {
    "duration": 60,
    "parallel": 8,
    "congctrl": "bbr",
    "pcap": "yes"
  }
}
```
```bash
python3 .agents/skills/gaia-scenario-gen/scripts/generate.py --spec override_xp_spec.json --json
```

### 3.5 반환 코드 (Exit Codes)
- `0`: 성공 (시나리오 파일 생성 및 `(topo, xp)` 조합 정적 검증 통과)
- `1`: 런타임 오류
- `2`: 필수 파라미터 누락 또는 조합 검증 실패

---

## 4. gaia-run-analyze (실험 실행 및 분석)

사전 정적 검증(S2), 도달성 핑 게이트(S3), Mininet 에뮬레이션 트래픽 실행(S4), 시스템 파라미터 복원(S5), 데이터 분석 및 시각화(S6)의 전체 파이프라인을 실행합니다.

### 📌 CLI 옵션 상세 설명 (Options Reference)

| 옵션 플래그 | 타입 / 필수 여부 | 기본값 | 상세 설명 |
|---|---|:---:|---|
| **`-t`, `--topo <경로>`** | 파일 경로 (조건부 필수) | - | 대상 네트워크 토폴로지 설정 파일 경로 (`config/topo/*`) |
| **`-x`, `--xp <경로>`** | 파일 경로 (조건부 필수) | - | 대상 실험 시나리오 설정 파일 경로 (`config/xp/*`) |
| **`-o`, `--output-dir <경로>`** | 디렉터리 경로 | `output/<timestamp>` | 실험 로그, PCAP, SVG 차트, 요약 결과가 저장될 출력 디렉터리 |
| **`--dry-run`** | 플래그 (action) | `false` | **비Root 사전 점검 모드**.<br>• Root 권한 없이 실행될 Mininet 커맨드라인 및 예상 산출물 경로를 점검 |
| **`--analyze-only`** | 플래그 (action) | `false` | **비Root 기존 결과 재분석 모드**.<br>• Mininet 재실행 없이 기존 실행 산출물 디렉터리를 재평가 및 판정 |
| **`--run-dir <경로>`** | 디렉터리 경로 (조건부 필수) | - | 재분석할 기존 실험 결과 디렉터리 경로 (`--analyze-only` 사용 시) |
| **`--herdr-session <이름>`** | 문자열 (조건부 필수) | - | 대상 Herdr 세션 데몬 이름 (`--setup` 또는 위임 실행 시 **필수**) |
| **`--runner-name <이름>`** | 문자열 (선택) | `gaia-root-runner` | Herdr 러너 워크스페이스 라벨 (기본값: `gaia-root-runner`) |
| **`--herdr-workspace <이름>`** | 문자열 (호환용) | - | [Legacy 호환 별칭] Herdr 세션 데몬 이름 (`--herdr-session`과 동일) |
| **`--setup`** | 플래그 (action) | `false` | **Root 전용 Herdr 러너 세션 초기화**.<br>• 지정된 세션에 러너 워크스페이스를 생성하고 `sudo su` 가이드 제공 |
| **`--timeout <초>`** | 정수 (int) | `300` | Herdr 위임 실행 시 실험 완료 대기 타임아웃 (초 단위) |
| **`--skip-preflight`** | 플래그 (action) | `false` | S3 사전 도달성(Mesh Ping) 게이트 생략 (곧바로 S4 Mininet 트래픽 실행) |
| **`--allow-custom-cmd`** | 플래그 (action) | `false` | **커스텀 워크로드 셸 명령 실행 명시적 승인 (S-4 보안 게이트)**.<br>• `custom_client` 또는 `custom_server` 포함 시 필수 |
| **`--strict`** | 플래그 (action) | `false` | **엄격 유효성 검증 강제**.<br>• `Single-Path Fallback`, `No Data`, 미검증 MPTCP 발생 시 반환코드 `4` 반환 |
| **`--json`** | 플래그 (action) | `false` | 실행 및 분석 결과 리포트를 **기계 가독형 JSON**으로 표준 출력 |

---

### 4.1 주요 실행 모드

#### ① Herdr 패널을 통한 Root 위임 실행 (권장: 에이전트 무권한 연동)
사용자가 1회 `sudo su` 해둔 Herdr 세션으로 실험 명령을 원격 주입하고 결과를 자동 수거합니다.
```bash
# 1단계: Herdr 러너 세션 생성 (--setup)
python3 .agents/skills/gaia-run-analyze/scripts/run.py \
  --herdr-session gaia-experiment \
  --runner-name gaia-root-runner \
  --setup

# 2단계: 위임 실행 및 자동 수거
python3 .agents/skills/gaia-run-analyze/scripts/run.py \
  -t config/topo/topo_sc2_mptcp_2path \
  -x config/xp/xp_sc2_mptcp_2path \
  --herdr-session gaia-experiment \
  --runner-name gaia-root-runner \
  --json
```

#### ② 로컬 직접 실행 (Full Execution)
Root 권한(Mininet) 환경에서 직접 전체 실험을 수행하고 메트릭을 분석합니다.
```bash
sudo .venv/bin/python3 .agents/skills/gaia-run-analyze/scripts/run.py \
  -t config/topo/topo_sc2_mptcp_2path \
  -x config/xp/xp_sc2_mptcp_2path \
  --json
```

#### ③ 비Root 사전 점검 모드 (`--dry-run`)
Root 권한 없이 토폴로지/시나리오 무결성을 검증하고, 실행될 Mininet 커맨드 라인과 예상 산출물 경로를 점검합니다.
```bash
python3 .agents/skills/gaia-run-analyze/scripts/run.py \
  -t config/topo/topo_sc2_mptcp_2path \
  -x config/xp/xp_sc2_mptcp_2path \
  --dry-run \
  --json
```

#### ④ 기존 결과 재분석 모드 (`--analyze-only`)
이미 수행된 실험 결과 디렉터리를 대상으로 pcap 파싱 및 메트릭 재생성을 수행합니다.
```bash
python3 .agents/skills/gaia-run-analyze/scripts/run.py \
  --analyze-only \
  --run-dir output/20260818-120000 \
  --strict \
  --json
```

### 4.2 생성되는 주요 산출물
- `summary.json`: Throughput, Retransmit Rate, RTT, 유효성 판정(`Valid Multipath` 등) 요약
- `results.csv`: 시계열 인터벌별 대역폭 및 손실 데이터
- `goodput_chart.svg`: 브라우저에서 바로 확인 가능한 반응형 대역폭 추이 벡터 차트
- `reachability.json`: 사전 도달성 검증(Mesh Ping) 매트릭스 결과
- `subflows.json`: Linux 커널 MPTCP 서브플로우 동적 연결 추적 기록

### 4.3 반환 코드 (Exit Codes)
- `0`: 성공 및 메트릭 검증 합격
- `1`: 일반 런타임 예외
- `2`: S2 정적 검증 실패
- `3`: S3 사전 도달성 핑 게이트 실패
- `4`: 엄격 유효성 실패 (`--strict` 모드에서 Single-Path Fallback 등 발생 시)

---

## 5. 다중 에이전트 자율 루프 연동 가이드

`multi-agent-mux-loop`를 활용하면 Planner, Creator, Reviewer 에이전트가 위 3가지 스킬을 자동으로 연동하여 실험 기획, 설정 파일 생성, 검증, 실행, 판정까지 자율적으로 수행합니다.

### 5.1 협업 루프 역할 분담
- **Planner**: 사용자 요구사항 분석 ➜ `gaia-topo-gen` 및 `gaia-scenario-gen` 입력 사양 도출
- **Creator**: 스킬 실행을 통한 설정 파일 생성 및 `--dry-run`/실행 관제
- **Reviewer**: `cli.py verify` 및 `--analyze-only --strict`를 통한 결과 정합성 교차 검증 및 `[VERDICT: PASS]` 판정

### 5.2 자율 협업 루프 구동 명령어

```bash
# 전체 에이전트 협업 루프 기동 (기획 토론 + 만장일치 교차 리뷰)
bash .agents/skills/multi-agent-mux-loop/scripts/run_loop.sh \
  --plan \
  --plan-talk 1 \
  --all-reviewer \
  --max-loop 3 \
  --target-agent "creator-agy-01" \
  --task "100Mbps/20ms 및 50Mbps/40ms의 비대칭 2-Path MPTCP 환경을 생성하고, iperf3 10초 트래픽 시나리오를 구성하여 dry-run 검증 리포트를 도출하라."
```

---

## 6. 실전 E2E 튜토리얼 (Step-by-Step)

아래 4단계 명령어로 토폴로지 생성부터 분석까지 직접 수행해 볼 수 있습니다:

```bash
# [Step 1] 2경로 MPTCP 토폴로지 생성
python3 .agents/skills/gaia-topo-gen/scripts/generate.py \
  --spec '{"name": "topo_tutorial_2path", "intent": "mptcp_multipath", "paths": [{"delay_ms": 15, "bandwidth_mbps": 100}, {"delay_ms": 30, "bandwidth_mbps": 50}]}' \
  --json

# [Step 2] iperf3 MPTCP 10초 시나리오 생성
python3 .agents/skills/gaia-scenario-gen/scripts/generate.py \
  --spec '{"name": "xp_tutorial_2path", "topo_file": "config/topo/topo_tutorial_2path", "xp_type": "iperf3_exp", "duration": 10, "mptcp": "yes", "pcap": "yes"}' \
  --json

# [Step 3] 비Root Dry-run 무결성 검증
python3 .agents/skills/gaia-run-analyze/scripts/run.py \
  -t config/topo/topo_tutorial_2path \
  -x config/xp/xp_tutorial_2path \
  --dry-run \
  --json

# [Step 4] (선택) Mininet 환경에서 실제 실행 (Root 권한 필요)
# sudo python3 .agents/skills/gaia-run-analyze/scripts/run.py \
#   -t config/topo/topo_tutorial_2path \
#   -x config/xp/xp_tutorial_2path \
#   --json
```

---
*GAIA Multi-Agent Experiment Automation Manual — 2026*
