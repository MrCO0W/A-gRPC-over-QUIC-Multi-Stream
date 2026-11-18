# GAIA Network Emulator (Mininet-based Experiment Framework)

**GAIA Network Emulator**는 [Mininet](http://mininet.org/)을 기반으로 다중 인터페이스(Multi-Interface) 및 다중 경로(Multi-Path) 네트워크 환경을 구축하고, 다양한 프로토콜(MPTCP, QUIC, TCP, UDP 등)의 성능 분석 및 네트워크 에뮬레이션을 자동화하는 Python 프레임워크입니다.

---

## 📌 주요 기능 (Key Features)

- **유연한 네트워크 토폴로지 에뮬레이션**: 클라이언트, 라우터, 서버 간 bottleneck 링크의 대역폭(Bandwidth), 지연 시간(Delay), 패킷 손실률(Loss), 큐 크기(Queue Size) 지정
- **동적 네트워크 조건 변경**: 에뮬레이션 동작 중 `netem` 명령을 통해 지연 시간 및 패킷 손실률을 실시간으로 동적 변경 (`NetemAt`)
- **실험 워크플로우 자동화**: 커널 Sysctl 설정 자동 복원, `tcpdump` 기반 패킷 캡처, traffic generator(`iperf`, `pa` 등) 연동
- **CLI 툴킷 제공**: 토폴로지/실험 파라미터 파일 자동 생성, 실험 템플릿 코드 보완 (`mkexp`), pcap 병합 (`mergecap`), 결과 청소 (`clean`) 지원

---

## 📁 프로젝트 구조 (Directory Structure)

```text
gaia-experiment/
├── cli.py                    # 고수준 CLI 래퍼 (실험 실행, 템플릿 생성, pcap 병합, 정리)
├── runner.py                 # 실행 메인 엔트리포인트 (토폴로지 생성 -> 실험 실행 -> Mininet 정리)
├── mininet_builder.py        # Mininet API 래퍼 클래스 (Host, Switch, Link, CLI 제어)
├── core/                     # 프레임워크 핵심 코어 모듈
│   ├── parameter.py          # 설정 파일(Key-Value) 파서 Base Class
│   ├── topo.py               # Topo, TopoConfig, BottleneckLink, LinkCharacteristics 정의
│   └── experiment.py         # Experiment, ExperimentParameter, Sysctl/tcpdump 제어
├── topos/                    # 네트워크 토폴로지 정의 모듈
│   ├── multi_interface.py    # 단일 클라이언트-라우터-서버 기반 다중 인터페이스 토폴로지 (MultiIf)
│   └── multi_interface_multi_clients.py # 다중 클라이언트/서버 지원 토폴로지
├── experiments/              # 실험 로직 구현 모듈
│   ├── iperf_exp.py          # iperf 벤치마크 실험
│   ├── ping_exp.py           # ping 네트워크 도달성 실험
│   └── performance-analysis.py # pa 유틸리티 활용 UDP/TCP/QUIC 성능 분석 실험
├── apps/                     # 논문 실험용 테스트 서버/클라이언트 구현체 및 벤치마크 소스 (CoAP, QUIC 등)
├── config/                   # 토폴로지 및 실험 설정 파일 저장소
│   ├── topo/                 # 생성된 토폴로지 파라미터 파일
│   └── xp/                   # 생성된 실험 파라미터 파일
├── template/                 # 토폴로지, 실험 설정 및 코드 생성을 위한 템플릿
├── utils/                    # C/C++ 기반 성능 측정 유틸리티 (`pa` 등)
└── AGENTS.md                 # 프로젝트 개발 및 멀티에이전트 협업 가이드
```

---

## ⚙️ 환경 설정 및 설치 (Prerequisites & Installation)

### 1. Mininet 설치
```bash
sudo apt update
sudo apt install -y mininet
# 설치 확인
mn --version
```

### 2. Mininet 필수 유틸리티 설치
```bash
git clone https://github.com/agent-networking-lab/gaia-mininet.git mininet
bash mininet/util/install.sh -fw 
```

### 3. 필수 패키지 및 유틸리티 설치
```bash
sudo apt install -y python3-pip iperf3 wireshark-common
pip install mininet
```

### 4. IP Forwarding 활성화
```bash
sudo sysctl -w net.ipv4.ip_forward=1
```

---

## 🚀 사용 방법 (Usage & Workflow)

### 1. 기본 실행 (`runner.py`)
직접 생성한 토폴로지 파라미터 파일(`-t`)과 실험 파라미터 파일(`-x`)을 지정하여 에뮬레이션을 실행합니다.
```bash
sudo python runner.py -t config/topo/topo-15_1000_100_0 -x config/xp/iperf_exp-simple-no
```

### 2. 고수준 6단계 워크플로우 CLI (`cli.py`)

#### S2 정적 검증 (비Root 실행)
```bash
python cli.py verify -t config/topo/topo_sc1_baseline -x config/xp/xp_sc1_baseline --json
```

#### S3 사전 도달성 검증 (Mesh Ping Gate, rc=3 on failure)
```bash
python cli.py preflight -t config/topo/topo_sc1_baseline --count 3 --timeout 1
```

#### Web UI 토폴로지 & 시나리오 비주얼 에디터 (WebUI v2)
```bash
# 기본 127.0.0.1 바인딩 (S-1 보안 기본값)
python webui/app.py --port 8080
```

#### S4 표준 실험 파이프라인 자동 실행 (검증 -> 프리플라이트 -> 실행 -> 분석)
```bash
# 기본 실행
python cli.py exec -t config/topo/topo_sc2_mptcp_2path -x config/xp/xp_sc2_mptcp_2path

# 커스텀 워크로드 실행 시 보안 게이트 플래그(--allow-custom-cmd, S-4) 명시 필수
python cli.py exec -t config/topo/topo_custom -x config/xp/xp_custom --allow-custom-cmd
```

#### S6 데이터 분석 및 SVG 차트 생성 (CSV, JSON, SVG 출력)
```bash
python cli.py analyze --run-dir output/{exp-dir-name} --strict
```

#### pcap 캡처 파일 병합
```bash
python cli.py merge output.pcap
```

#### 생성된 로그 및 임시 파일 정리
```bash
python cli.py clean
```

---

## 🛠️ 업그레이드 및 리팩토링 현황 (Refactoring Status)

현재 프레임워크는 Phase 1 (WebUI), Phase 2 (백엔드 아키텍처), Phase 3 (표준 네트워크 실험 워크플로우 & 파이프라인), Phase 4 (MAM 특화 자동화 스킬 3종 구축) 및 **Phase 5 (WebUI 고도화, 노드별 워크로드 정의 & 보안 게이트 체계)**를 완료하였습니다.

1. **6단계 표준 실험 워크플로우 (S1~S6)**: 정적 검증(S2), Mesh Ping 사전 도달성 게이트(S3), 실행(S4), Teardown 보장(S5), 데이터 분석 & SVG 시각화(S6) 파이프라인 완성 ([`EXPERIMENT_WORKFLOW.md`](EXPERIMENT_WORKFLOW.md) 참조)
2. **MAM 특화 자동화 스킬 3종**: `.agents/skills/gaia-topo-gen` (토폴로지 생성), `.agents/skills/gaia-scenario-gen` (시나리오 생성), `.agents/skills/gaia-run-analyze` (실행, 관제 및 Herdr 세션 기반 Root 위임 에뮬레이션)
3. **노드별 워크로드 및 WebUI v2 에디터**: 3대 역할(`client`, `server`, `idle`) 모델 정합화, `workload:` 스키마 및 `workload_exp` 런타임 엔진, 안전한 플레이스홀더 치환(`bind_placeholders`), WebUI Topo/Scenario 2-Button 내보내기 & 자동 재수화(Rehydration)
4. **보안 통제 체계 (S-1 ~ S-4)**: WebUI 로컬 바인딩(`127.0.0.1`), CSRF 방어(Origin/Referer/JSON Content-Type), 커스텀 명령 실행 보안 게이트(`--allow-custom-cmd`)
5. **테스트 및 검증 체계**: `tests/run_all.py` 스위트 내 **210개 단위/통합/E2E 테스트 전건 통과** 달성 (Mininet 의존성 없는 Fake/Stub 격리 및 rootless E2E 검증)