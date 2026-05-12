# KICS 권한 요청 처리 자동화 GUI

버튼을 눌러 내부 공문 페이지의 요청 정보를 읽고, 관리자 웹사이트에서 권한 처리 흐름을 실행하는 Python + Playwright + Tkinter 템플릿입니다.

## 설치

```bash
pip install playwright
python -m playwright install chromium
```

## 설정

1. `config.sample.json`을 `config.json`으로 복사합니다.
2. 내부망에서 공문 URL과 관리자 URL을 입력합니다.
3. F12 개발자도구로 실제 selector를 확인해 `document_selectors`와 `admin_selectors`를 교체합니다.
4. 권한 문구와 관리자 권한명을 `permission_map`에 맞춥니다.

## 실행

```bash
python kics_gui_automation.py
```

## 권장 순서

1. 프로그램 실행
2. 관리자 URL 입력
3. `로그인 저장` 클릭
4. 브라우저에서 관리자 로그인 완료
5. 확인을 눌러 `auth.json` 저장
6. Dry-run 체크 상태로 `작업 시작`
7. 로그와 브라우저 화면에서 추출 ID/성명/권한이 맞는지 확인
8. 검증 후에만 Dry-run 해제
9. 최종 승인 전 확인창은 초기 운영 중 유지 권장


## 실행 화면 상태 표시

이 버전은 `작업 시작`을 누른 뒤 전체 프로세스를 한눈에 볼 수 있도록 상태 표시를 추가했습니다.

- 상단 `실행 상태`: 현재 진행 중인 단계와 진행률을 표시합니다.
- `처리 단계`: 공문 접속, 데이터 추출, 관리자 이동, 사용자 검색, 권한 선택, 최종 승인/종료를 단계별로 표시합니다.
- `실행 로그`: 진행/성공/경고/실패 메시지를 색상과 기호로 구분합니다.
- 성공 또는 Dry-run 완료 시 안내창이 표시됩니다.
- 실패 시 현재 진행 중이던 단계가 `실패`로 바뀌고 경고창에 원인이 표시됩니다.

정상 흐름 예시는 다음과 같습니다.

```text
▶ 작업을 시작합니다.
▶ 공문 페이지로 이동합니다.
✔ 공문 로드 완료
▶ 공문 데이터 분석 중...
✔ 추출 결과 - 성명:  , ID: 1432, 권한: 통합사건검색 사건진행내사
▶ 관리자 페이지로 이동합니다.
✔ 관리자 페이지 로드 완료
▶ 사용자 검색: 1432
✔ 사용자 조회 완료
▶ 권한 선택 중 (1/1): 통합사건검색 사건진행내사
✔ 권한 선택 완료: 통합사건검색 사건진행내사
⚠ Dry-run 모드입니다. 최종 승인/저장은 클릭하지 않습니다.
✔ 작업이 종료되었습니다.
```

실패 흐름 예시는 다음과 같습니다.

```text
▶ 작업을 시작합니다.
▶ 공문 페이지로 이동합니다.
✔ 공문 로드 완료
▶ 공문 데이터 분석 중...
❌ 작업 실패: 사용자 ID를 추출하지 못했습니다.
```

실패가 발생하면 자동 승인/저장 단계로 넘어가지 않습니다.

## 중요한 안전 기준

- 비밀번호는 코드나 설정 파일에 저장하지 않습니다.
- 기본값은 Dry-run입니다.
- 실제 승인 전에는 사용자 ID와 권한명이 맞는지 반드시 확인하세요.
- selector가 정확하지 않으면 프로그램이 실패하도록 두는 것이 잘못 클릭하는 것보다 안전합니다.

## 유동 양식 대응

이 버전은 고정 selector 추출 뒤에 유동 양식 추출을 추가로 시도합니다.

추출 우선순위:

1. `document_selectors`에 지정한 selector
2. `flexible_extraction`의 라벨 기반 추출
3. `fallback_regex` 정규식 추출
4. 실패 시 작업 중단

예를 들어 아래 표현은 모두 같은 사용자 ID 후보로 처리할 수 있습니다.

```text
아이디: 1432
사용자 ID 1432
계정명: 1432
KICS ID : 1432
```

권한명은 `permission_map`의 키와 `permission_aliases`를 함께 봅니다.

```json
"permission_map": {
  "통합사건검색": ["통합사건검색 사건진행내사"]
},
"permission_aliases": {
  "통합사건검색": ["통합 사건 검색", "사건검색"]
}
```

운영 팁:

- 실제 운영 전에는 Dry-run 상태에서 로그의 `추출 결과`와 `권한 매핑 근거 키워드`를 확인하세요.
- 새 양식이 추가되면 코드 수정 전에 `config.json`의 라벨/별칭/정규식만 먼저 보강하세요.
- 사용자 ID 또는 권한을 못 찾으면 프로그램은 관리자 변경 전에 중단합니다.


## 감시 모드

이 버전은 기존 `작업 시작` 버튼의 1건 처리 방식에 더해, 공문 목록을 주기적으로 새로고침하면서 새 요청 공문을 찾는 `감시 모드`를 제공합니다.

기본 흐름은 다음과 같습니다.

```text
[감시 시작 클릭]
      ↓
공문 목록 URL 접속
      ↓
설정된 주기마다 목록 스캔
      ↓
제목/행 텍스트가 request_title_keywords와 맞는 공문 탐지
      ↓
이미 처리한 공문인지 processed_documents.json 확인
      ↓
신규 공문이면 상세 URL로 이동
      ↓
기존 기본 로직 실행
      ↓
처리 결과 기록
      ↓
다시 목록 감시
```

### 화면에서 추가된 항목

- `공문 목록 URL (감시)`: 새 요청이 표시되는 목록 페이지 주소입니다.
- `스캔 주기(초)`: 목록 페이지를 다시 확인하는 주기입니다. 최소 5초입니다.
- `자동 재시도`: 실패 시 같은 공문을 다시 시도할지 선택합니다.
- `최대 시도 횟수`: 자동 재시도 포함 최대 처리 시도 횟수입니다.
- `감시 시작`: 목록 감시를 시작합니다.
- `감시 중지`: 현재 감시 루프를 중지 요청합니다. 현재 작업의 안전 지점에서 멈춥니다.

### 감시 모드 설정값

`config.json`의 `watch` 항목을 실제 내부망 페이지 구조에 맞춰 조정합니다.

```json
"watch": {
  "list_url": "http://internal-doc-site/document-list-url",
  "scan_interval_seconds": 30,
  "document_item_selector": "tr",
  "document_link_selector": "a",
  "document_link_attribute": "href",
  "document_title_selector": "",
  "document_key_selector": "",
  "document_key_attribute": "",
  "request_title_keywords": [
    "KICS 사용권한 부여 요청",
    "사용권한 부여 요청"
  ],
  "processed_state_path": "processed_documents.json",
  "max_items_per_scan": 100,
  "process_one_per_scan": true,
  "auto_retry_default": true,
  "max_retry_count": 3,
  "retry_delay_seconds": 5,
  "popup_on_failure": true,
  "skip_failed_after_retries": true
}
```

가장 먼저 맞춰야 하는 값은 아래 네 가지입니다.

```text
document_item_selector      공문 목록에서 한 행/카드/항목을 가리키는 selector
document_link_selector      각 항목 안에서 상세 공문으로 들어가는 링크 selector
request_title_keywords      KICS 권한 요청 공문임을 판별할 제목/본문 키워드
document_key_selector       공문번호처럼 중복 방지에 쓸 고유값 selector (없으면 상세 URL 사용)
```

예를 들어 공문 목록이 테이블이고 각 행에 제목 링크가 있다면 보통 아래처럼 시작합니다.

```json
"document_item_selector": "tr",
"document_link_selector": "a",
"request_title_keywords": ["KICS 사용권한 부여 요청"]
```

공문번호가 별도 칸에 있다면 중복 방지 안정성을 위해 `document_key_selector`를 지정하는 것이 좋습니다.

```json
"document_key_selector": "td.doc-no",
"document_key_attribute": ""
```

### 중복 처리 방지

감시 모드는 같은 공문을 반복 승인하지 않도록 `processed_documents.json` 파일에 처리 결과를 저장합니다.

- `approved`: 실제 승인 완료 기록입니다. 이후 Dry-run/실승인 모두에서 건너뜁니다.
- `dry_run`: Dry-run 검증 기록입니다. Dry-run 중에는 반복 처리하지 않지만, Dry-run을 끄면 실제 승인 대상으로 다시 처리할 수 있습니다.
- `cancelled`: 사용자가 최종 승인을 취소한 기록입니다.
- `failed`: 실패 기록입니다. 기본 설정에서는 같은 실패 공문을 반복 처리하지 않습니다.

테스트를 다시 해야 할 때는 프로그램을 종료한 뒤 `processed_documents.json`을 백업 또는 삭제하면 됩니다. 실운영에서는 이 파일을 임의로 지우면 중복 처리 위험이 있으므로 주의하세요.

### 자동 재시도

자동 재시도가 켜져 있으면 한 공문 처리 실패 시 `max_retry_count`까지 다시 시도합니다. 예를 들어 최대 시도 횟수가 3이면 다음처럼 동작합니다.

```text
1차 처리 실패
  ↓
retry_delay_seconds 대기
  ↓
2차 처리 실패
  ↓
retry_delay_seconds 대기
  ↓
3차 처리 실패
  ↓
실패 기록 저장 + 경고 팝업
```

사용자 ID 추출 실패, 권한 매핑 실패, selector 오류, 관리자 페이지 timeout 등이 발생하면 자동 승인으로 넘어가지 않습니다.

### 권장 운영 순서

1. 기존처럼 `로그인 저장`을 먼저 수행합니다.
2. `Dry-run` 체크를 유지합니다.
3. `공문 목록 URL (감시)`와 `watch` selector를 설정합니다.
4. `감시 시작`을 눌러 신규 공문 탐지 로그를 확인합니다.
5. 로그에서 상세 URL, 사용자 ID, 성명, 권한 매핑 근거가 맞는지 확인합니다.
6. 잘못 탐지되면 `request_title_keywords` 또는 selector를 먼저 수정합니다.
7. Dry-run에서 충분히 검증한 뒤에만 실제 승인 모드를 검토합니다.

