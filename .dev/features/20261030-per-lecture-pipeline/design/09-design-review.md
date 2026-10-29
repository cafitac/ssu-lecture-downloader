# 설계 검토

- 기능 / 범위: 강의별 독립 파이프라인 (받기 → 음성 추출 → 자막을 강의마다 순서대로, 강의끼리는 동시에) + 원격 자막 "작업 방식" 연결부 초안
- 근거 상태: 현재(CURRENT) / 제안(PROPOSED) / 미결정(OPEN)
- 기준 산출물: `02-domain-model.puml`, `04-target-schema.dbml`, `05-transaction-flows.puml`, `07-consistency-migration.md`, `08-acceptance-trace.md`, `remote-stt-job-protocol.md`
- 검토 언어: 한국어
- 표현 원칙: TARGET_OVERVIEW_FIRST

## 한눈에 보는 최종 구조

- 현재: 강의 여러 개를 고르면 영상을 **모두** 받은 뒤에야 후처리가 시작되고, 후처리(음성 추출·자막)는 강의 하나씩만 돌아요. 한 강의가 실패하면 나머지도 멈추고, 영상 하나라도 못 받으면 나머지 강의의 후처리 자체가 안 돌아요. 받기가 끝난 강의는 후처리 차례를 기다리는 동안 "받는 중 · 100%"로 보여요.
- 완성 후: 강의마다 자기 순서(받기 → 음성 추출 → 자막 → 정리)를 따로 밟아요. 1강 영상이 다 받아지면 2강이 아직 받는 중이어도 1강은 바로 음성 추출을 시작하고, 음성이 준비된 강의는 바로 자막 서버로 보내요. 이 컴퓨터의 CPU를 쓰는 일만 제한해요: 음성 추출 등 ffmpeg 작업 동시에 2개, 이 컴퓨터에서 도는 자막 엔진 1개. 원격 자막 서버로 보내는 요청은 앱이 줄 세우지 않아요 — 순서는 서버가 정해요.
- 안전 경계: 한 강의가 실패해도 그 강의만 "실패"로 끝나고 나머지는 끝까지 가요. "모두 취소"는 어느 단계에 있든 전부 멈춰요. 원격 서버에서 기다린 시간 때문에 자막이 시간 초과로 실패하지 않아요. 새 파일·새 저장소는 없고, 화면이 받는 응답 모양은 그대로(추가만)예요. Mac Studio와 공용 모델 서버(modelhost)는 이 기능에서 건드리지 않아요.

## 시각화

### 전체 목표 구조

근거: `02-domain-model.puml`, `05-transaction-flows.puml` Flow 1, `07-consistency-migration.md` § Concurrency model

```mermaid
flowchart TB
    subgraph UI["앱 화면 (React)"]
        DR["다운로드 진행 서랍 DownloadDrawer<br/>제목 '진행 중 d/t'(PROPOSED)<br/>강의마다 실제 단계·대기·순번 표시"]
    end
    subgraph PY["Python 백엔드 (사이드카)"]
        JOB["다운로드 작업 ArtifactPipeline<br/>계획(현재) → 강의별 파이프라인에 넘겨줌(PROPOSED)"]
        DL["영상 받기 LmsService<br/>동시 받기 max_parallel_downloads(현재)<br/>강의 하나 끝날 때마다 on_item_ready(PROPOSED)"]
        subgraph LP["강의별 파이프라인 LecturePipeline (강의 수만큼, 서로 독립)"]
            L1["1강: 받기 → 음성 추출 → 자막 → 정리"]
            L2["2강: 받기 → 음성 추출 → 자막 → 정리"]
            L3["n강: …"]
        end
        GATE["작업 제한 StageGates (프로세스 전체 공용)<br/>ffmpeg 작업 2개 · 로컬 자막 엔진 1개"]
        RS["원격 자막 연결 RemoteSTTService<br/>앱에서 줄 세우지 않음"]
    end
    subgraph EXT["외부 (이 기능에서 변경 없음)"]
        WS["지금: whisper-server :8795<br/>(서버가 한 번에 하나씩 처리)"]
        MH["나중: 공용 모델 서버(예: modelhost)<br/>작업 방식 API — 대기 순번·진행률"]
    end
    LMS["숭실 LMS (현재)"]
    DR -->|"작업 상태 조회(현재 API)"| JOB
    JOB --> DL
    DL --> LMS
    DL -->|"이 강의 받기 끝"| LP
    JOB -->|"영상이 필요 없는 강의는 바로"| LP
    L1 & L2 & L3 -->|"음성 추출·WAV 변환 때만"| GATE
    L1 & L2 & L3 -->|"음성 준비되면 바로"| RS
    RS -->|"지금: 요청 하나씩 그대로 보냄"| WS
    RS -.->|"나중(합의 후): 등록 → 순번·진행률 조회 → 결과"| MH
```

### 목표 데이터 구조 (모두 메모리, 새 파일 없음)

근거: `04-target-schema.dbml`

```mermaid
erDiagram
    ARTIFACT_JOB ||--o{ LECTURE_PIPELINE : "강의마다 하나"
    LECTURE_PIPELINE ||--o{ PROGRESS_EVENT : "자기 단계 이벤트만 기록"
    LECTURE_PIPELINE ||--o| REMOTE_REQUEST : "자막 단계에서 원격 요청"
    STAGE_GATES ||--o{ LECTURE_PIPELINE : "ffmpeg 2개·로컬 자막 1개까지 허용"
    ARTIFACT_JOB ||--|| JOB_RESULT : "모든 강의가 끝나면 요약"
    ARTIFACT_JOB {
        string job_id PK "작업 번호 (현재)"
        string outputs "mp4 mp3 txt (현재)"
    }
    LECTURE_PIPELINE {
        string module_item_id PK "강의 번호"
        string state "대기·받는 중·음성 대기·추출 중·자막 대기·자막 중·완료·실패·취소"
        string failed_stage "실패한 단계"
        string error_code "실패 코드"
    }
    STAGE_GATES {
        string name PK "media 또는 local_stt"
        int limit "2 또는 1"
    }
    REMOTE_REQUEST {
        string kind "whisper_cpp_server 또는 stt_job"
        int ahead_requests "지금 방식: 먼저 보낸 요청 수"
        string job_id "작업 방식: 서버 작업 번호"
        string idempotency_key "작업 방식: 다시 보내도 한 번만"
    }
    PROGRESS_EVENT {
        string stage "단계 이름 (추가만)"
        int queue_position "서버가 알려 줄 때만"
        int progress_percent "진행률"
        float at "시각"
    }
    JOB_RESULT {
        json summary "전체·성공·실패·취소 수 (추가)"
        json items_status "강의별 최종 상태 (추가)"
    }
```

### 핵심 흐름: 강의별로 따로 진행, 한 강의 실패는 그 강의만

근거: `05-transaction-flows.puml` Flow 1, Flow 2, Flow 3

```mermaid
sequenceDiagram
    actor 학생
    participant 서랍 as 진행 서랍
    participant 작업 as 다운로드 작업
    participant 받기 as 영상 받기
    participant 강1 as 1강 파이프라인
    participant 강2 as 2강 파이프라인
    participant 제한 as 작업 제한(ffmpeg 2)
    participant 서버 as 원격 자막 서버
    학생->>서랍: 2개 강의 MP3+TXT 받기
    작업->>받기: 계획 후 동시에 받기 시작
    받기-->>작업: 1강 받기 끝 (2강은 아직 받는 중)
    작업->>강1: 1강 넘겨줌
    강1->>제한: ffmpeg 자리 요청
    제한-->>강1: 자리 있음
    강1->>강1: 음성 추출
    서랍-->>학생: 1강 "음성 추출 중 40%", 2강 "받는 중 70%"
    강1->>서버: 자막 요청 (앱은 줄 세우지 않음)
    받기-->>작업: 2강 받기 끝
    작업->>강2: 2강 넘겨줌
    강2->>강2: 음성 추출
    강2->>서버: 자막 요청 (1강 다음 차례)
    서랍-->>학생: 2강 "자막 서버 응답 기다리는 중 · 먼저 보낸 요청 1개"
    alt 2강 자막 실패
        서버-->>강2: 오류
        강2-->>작업: 2강만 실패
        서랍-->>학생: 2강 "실패 + 이유", 1강은 계속
    else 정상
        서버-->>강1: 1강 자막
        서버-->>강2: 2강 자막 (기다린 시간은 시간 초과에 안 셈)
    end
    작업-->>서랍: 모두 끝나면 요약 (성공 n · 실패 m)
    학생->>서랍: (언제든) 모두 취소
    서랍->>작업: 취소 → 모든 강의가 다음 단계로 안 넘어가고 멈춤
```

### 강의 한 개의 상태 (메모리에서만, 저장 안 함)

근거: `02-domain-model.puml` LecturePipeline, `04-target-schema.dbml` lecture_state

```mermaid
stateDiagram-v2
    state "대기 (자기 영상 받기 전)" as queued
    state "받는 중" as downloading
    state "음성 추출 대기 중" as waiting_audio
    state "음성 추출 중" as extracting
    state "자막 대기 중 (로컬 엔진·서버 순번)" as waiting_transcript
    state "자막 만드는 중" as transcribing
    state "완료" as done
    state "실패 (이 강의만)" as failed
    state "취소됨" as cancelled
    [*] --> queued
    queued --> downloading: 받기 시작
    queued --> waiting_audio: 영상이 이미 있음
    downloading --> waiting_audio: 받기 끝, ffmpeg 자리 없음
    downloading --> extracting: 받기 끝, 자리 있음
    waiting_audio --> extracting: 자리 생김
    extracting --> waiting_transcript: 로컬 엔진 사용 중 또는 서버 대기
    extracting --> transcribing: 바로 시작
    waiting_transcript --> transcribing: 차례 옴
    transcribing --> done: 자막 저장
    extracting --> done: MP3만 요청
    downloading --> failed: 받기 실패
    extracting --> failed: 추출 실패
    transcribing --> failed: 자막 실패
    downloading --> cancelled: 모두 취소
    waiting_audio --> cancelled: 모두 취소
    waiting_transcript --> cancelled: 모두 취소
    transcribing --> cancelled: 모두 취소
```

## 화면 문구 (진행 서랍의 한 줄)

| 상황 | 지금 보이는 것 | 바뀐 뒤 |
|---|---|---|
| 받기 끝, 음성 추출 차례 기다림 | 받는 중 · 100% | 음성 추출 대기 중 |
| 음성 추출 중 | 음성 추출 중 · n% | 같음 |
| 음성 끝, 자막 차례 기다림 (로컬 엔진 사용 중) | 받는 중 · 100% 또는 자막 만드는 중 0% | 자막 대기 중 |
| 지금 방식 원격: 먼저 보낸 요청이 서버에 있음 | 자막 만드는 중 (원격) · 5% (멈춘 것처럼 보임) | 자막 서버 응답 기다리는 중 · 먼저 보낸 요청 n개 |
| 작업 방식 원격: 서버 대기열 | (없음) | 자막 대기 중 (n번째) |
| 작업 방식 원격: 서버가 잠시 미룸(429) | (없음) | 자막 대기 중 (서버가 잠시 미룸) |
| 원격 처리 중, 서버가 진행률을 알려 줌 | — | 자막 만드는 중 (원격) · n% (추정치면 "약 n%") |
| 서랍 제목 | 받는 중 d/t | 진행 중 d/t |

- 전체 진행 막대: 강의마다 받기 40 · 음성 20 · 자막 40 비중으로 계산 (요청한 결과물만 반영).
- 새 실패 코드 문구: `stt_remote_job_lost` "자막 서버에서 작업을 찾지 못했어요. 다시 시도해 주세요.", `stt_remote_upload_too_large` "음성 파일이 서버 허용 크기보다 커요.", `stt_remote_bad_request` "자막 서버가 이 음성 파일을 받지 않았어요."

## 진행 이벤트 계약 (승인 시 고정, 화면 작업은 이 계약으로 병렬 진행)

모든 이벤트에 현재처럼 `index`, `total`, `module_item_id`, `title`이 붙고, 새로 `at`(초 단위 시각)이 붙어요. 아래는 **추가**되는 단계예요. 기존 단계 이름과 의미는 그대로예요.

| stage | 언제 | 추가 필드 |
|---|---|---|
| `artifact_item_queued` | 계획 직후 강의마다 한 번 | — |
| `audio_waiting` | ffmpeg 자리를 바로 못 얻었을 때 한 번 | — |
| `transcript_waiting` | 자막 차례를 기다릴 때 | `reason` (`local_engine` / `server_busy` / `server_deferred`), `ahead_requests`, `retry_after_seconds` |
| `transcript_queued` | 작업 방식 서버가 대기 중이라고 알릴 때 (갱신) | `queue_position`, `job_id` |
| `transcript_progress` (현재 단계) | 처리 중 | 선택 `progress_estimated` |
| `artifact_item_done` | 강의 하나가 모두 끝남 | — |
| `artifact_item_failed` | 강의 하나가 실패 | `error_code`, `failed_stage` |
| `artifact_item_cancelled` | 취소로 끝남 | — |

- 작업 응답: `status`, `error_code` 의미 그대로 (`partial_failure`는 이미 화면 문구가 있음). `result`에 `summary`, `items_status`가 추가돼요.
- 옛 화면 + 새 백엔드: 모르는 단계는 무시, `artifact_item_failed`는 이름에 failed가 있어 실패로 표시돼요. 새 화면 + 옛 백엔드: 대기 상태를 `download_done`/`audio_done`에서 스스로 계산해요.

## 원격 자막: 지금 방식과 나중 방식

| | 지금 방식 (`whisper_cpp_server`, 그대로 유지) | 나중 방식 (`stt_job`, 공용 모델 서버와 합의 후) |
|---|---|---|
| 요청 | 강의마다 음성 전체를 한 번에 보내고 응답을 기다림 | 등록 → 작업 번호 → 상태 조회 → 결과 받기 |
| 줄 세우기 | 앱은 안 함, 서버가 한 번에 하나씩 처리 | 서버 대기열 (우선순위·공정 분배는 서버 몫) |
| 기다린 시간 | 응답 대기 한도를 "20분 × (1 + 먼저 보낸 요청 수)", 최대 4시간으로 늘림 | 대기 중에는 한도 없음, 처리 시간에만 한도 |
| 순번·진행률 | 순번 모름 → "먼저 보낸 요청 n개"만 표시 | 서버가 알려 주는 순번·진행률 표시 |
| 긴 강의 나누기 | 앱이 하지 않음 (지금과 같음) | 서버가 나눔, 앱은 나누지 않음 |
| 단점 | 다른 사용처가 같은 서버를 쓰면 한도를 넘을 수 있음, 서버가 멈추면 늦게 알아챔 | 서버 쪽 구현이 먼저 필요 |

- 나중 방식의 요청·응답 모양 초안은 `remote-stt-job-protocol.md`에 따로 있어요 (공용 모델 서버 쪽 검토용).

## 전달 계획 참고 (보조 정보)

목표 설계는 위 그림이 기준이에요. 아래는 구현 순서예요.

| 순서 | PR 단위 | 담당 설계 범위 | 검증 / 관찰 | 롤백 경계 |
|---|---|---|---|---|
| 1 | PL-PY-PIPELINE | 강의별 파이프라인, 작업 제한, 실패 격리, 취소, 진행 이벤트, 지금 방식 원격 대기 한도 | AC1–AC6, AC8 (pytest, CI) | PR 되돌리기 |
| 1' | PL-UI-STATES | 대기 상태·순번 문구, 서랍 제목, 전체 진행률 계산 (1과 병렬로 만들고 1 병합 뒤 병합) | AC7, AC8 화면 부분, 스크린샷 | PR 되돌리기 |
| 확인 | 사용자 확인 | 설치본에서 강의 3개 TXT, 겹쳐 진행·실패 격리·모두 취소 | AC10 | 이전 설치본 |
| 2 | STT-JOB-PROVIDER | 작업 방식 원격 연결부 (`stt_job`), 설정 선택지 | AC9 — 프로토콜 합의(AC11) 뒤에만 시작 | 설정에서 지금 방식으로 되돌리기 / PR 되돌리기 |
| 외부 | EXT-MODELHOST-STT | 공용 모델 서버의 STT 구현 (이 기능 밖, 소유자 + interview-coach 결정) | 질문 목록: `07-consistency-migration.md` 마지막 절 | — |

## 확인이 필요한 결정

1. 지금 방식(`whisper_cpp_server`)에서도 앱이 줄 세우지 않고 강의별 요청을 바로 보내고, 응답 대기 한도만 "20분 × (1 + 먼저 보낸 요청 수), 최대 4시간"으로 늘릴까요? (추천: 예 — 서버가 순서대로 처리하니 전체 시간은 같고, 대기 때문에 실패하지 않아요. 대신 서버가 멈추면 늦게 알아채요. 아니오를 고르면 이 방식에서만 원격 요청을 하나씩 보내요.)
2. 진행 서랍 제목을 "받는 중 d/t" 대신 "진행 중 d/t"로 바꿀까요? (추천: 예 — 음성 추출·자막까지 포함하니까요.)
3. 원격 자막 작업 방식 초안(`remote-stt-job-protocol.md`)을 공용 모델 서버(modelhost) 쪽 검토에 보낼까요? 반복 구간 다시 인식은 서버가 맡는 쪽을 추천해요 (서버가 이미 긴 음성을 나누니까요).
4. 위 구조·이벤트 계약·PR 순서로 이 설계(산출물 다이제스트는 `../feature.md`의 진행 기록)를 승인할까요?
