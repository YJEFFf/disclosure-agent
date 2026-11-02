# 공시 Agent

**미래에셋증권 × 네이버클라우드 AI FESTIVAL 2026 · 팀 북적북적**

기업 공시를 검색하고, 계산 가능한 값은 코드로 검증해 근거와 함께 답하는 HyperCLOVA X 기반 질의응답 에이전트입니다.

**대회 제출용 원본 저장소는 별도로 비공개(Private) 관리됩니다. 이 저장소는 팀 프로젝트의 실제 구현 코드를 정리한 개인 포트폴리오용 공개본입니다.**

`Python` · `HyperCLOVA X` · `SQLite / FTS5` · `FastAPI` · `Docker`

## 핵심 구현

- **근거 검색:** 기업·기간·공시 유형을 구분하고 SQL·FTS5로 관련 문서를 조회합니다.
- **정정 공시 처리:** 원본과 정정본을 연결해 유효한 공시를 선택합니다.
- **답변 검증:** 합계·차액·건수를 코드로 계산하고 근거에 없는 수치와 링크를 검사합니다.
- **API 운영:** 응답 캐시, 호출 시간 제한, 데이터 상태 점검과 서버 감시를 구현했습니다.

```text
질문 → 기업·조건 추출 → 공시 검색 → 코드 계산 → LLM 답변 → 근거 검증
```

## 코드 둘러보기

| 위치 | 내용 |
| --- | --- |
| [agent/answerer.py](src/gongsi/agent/answerer.py) | 검색·계산·답변 생성 및 검증 |
| [parse/](src/gongsi/parse) · [index/](src/gongsi/index) | 공시 파싱, 정정 관계, SQLite·FTS5 검색 |
| [llm/clovax.py](src/gongsi/llm/clovax.py) | LLM 호출·재시도·캐시·시간 예산 |
| [api/server.py](src/gongsi/api/server.py) | 질의응답 API와 상태 점검 |
| [scripts/](scripts) · [tests/](tests) | 전처리·서버 실행과 오프라인 테스트 |

## 시작하기

Python 3.12 환경에서:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

테스트는 합성 데이터와 모의 응답을 사용하며 외부 API를 호출하지 않습니다. 실제 질의응답에는 별도로 준비한 공시 데이터와 CLOVA Studio API 키가 필요합니다. 대회 제공 데이터·평가셋·API 키·운영 기록은 포함하지 않았습니다.

→ [데이터 준비·전처리·서버 실행](docs/RUNNING.md) · [공개본 구성과 원본 비교](docs/PROVENANCE.md)
