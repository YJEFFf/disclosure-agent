# 실행 안내

## 1. 환경 설치와 오프라인 확인

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

테스트는 임시 디렉터리의 합성 데이터와 모의 답변을 사용합니다. 네트워크 호출을 차단하므로 API 키나 대회 데이터 없이 실행할 수 있습니다. 검증 대상은 금액 환산, 답변의 링크 검증, API 응답 형식·캐시·오류 처리, 데이터 누락 감지입니다.

## 2. 실제 질의응답에 필요한 데이터

이 프로젝트는 대회 공시 코퍼스의 형식을 전제로 개발했습니다. 같은 구조로 준비한 데이터가 필요하며, 원문·메타데이터는 이 저장소에 배포하지 않습니다.

```text
02_데이터/
├── 01_공시원문/      # exchange / periodic / major / holding
├── 02_구조화/
│   ├── manifest.jsonl
│   └── universe.csv
└── 03_가공/          # 전처리로 생성되는 DB·검색 자료·캐시
```

`manifest.jsonl`은 공시별 파일 경로와 기업·공시 정보를, `universe.csv`는 기업 이름과 식별자를 담습니다. 정확한 읽기 방식은 [loader.py](../src/gongsi/io/loader.py)에 있습니다. 임의의 PDF 폴더를 이 경로에 넣는 것만으로는 동작하지 않습니다.

다른 위치의 데이터를 사용한다면 다음처럼 지정합니다. 세부 키는 [paths.yaml](../configs/paths.yaml)을 참고하세요.

```bash
export GONGSI_DATA_ROOT=/absolute/path/to/02_데이터
cp .env.example .env
# .env의 CLOVASTUDIO_API_KEY에 본인의 키 입력
```

## 3. 전처리

데이터 준비 후 저장소 루트에서 순서대로 실행합니다. 기본 검색은 SQL·FTS5이며 임베딩 생성은 필요하지 않습니다.

```bash
python scripts/run_parse_event.py
python scripts/run_parse_periodic.py
python scripts/run_correction.py
python scripts/run_build_db.py --check
python scripts/run_build_sectiondb.py --check
python scripts/run_build_catalog.py
python scripts/run_build_cover.py
python scripts/run_build_majornotes.py
python scripts/run_build_holdingtables.py
python scripts/run_build_corritems.py
python scripts/run_build_ordinals.py
python scripts/run_build_finsummary.py
```

정정 헤더 스토어는 원본 배포 준비 함수로 생성합니다. 이 명령은 배포 디렉터리를 재구성하지 않고 해당 스토어만 만듭니다.

```bash
python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
from stage_deploy import build_correction_store, loader
build_correction_store(Path(loader.DERIVED_DIR) / "db/correction_headers.jsonl")
PY
```

`stage_deploy.py --verify`는 기존 `var/deploy`를 다시 만들고 대회 코퍼스 규모에 맞춘 하한값으로 검사합니다. 별도 데이터셋의 일반 검증 명령으로 쓰지 마세요.

## 4. API 실행

```bash
python scripts/run_server.py --host 127.0.0.1 --port 8000
```

- `GET /answer?question_id=demo-001&question=질문`: 질의응답. 실제 LLM 호출에는 API 사용료가 발생할 수 있습니다.
- `GET /health`: 필수 데이터 저장소 상태 확인.
- `GET /selfcheck`: LLM을 호출하지 않고 검색 경로 점검. 원본 대회 기업과 질문을 사용하므로 다른 데이터셋에서는 조정이 필요합니다.

`/answer`는 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer`의 문자열 필드 5개를 반환합니다. 같은 `question_id`는 캐시를 재사용하므로 새로운 질문에는 새 ID를 사용합니다. 대회 규격에 따라 오류에도 HTTP 200을 반환하므로 응답 본문을 함께 확인해야 합니다.

`Dockerfile`과 `scripts/supervise.py`는 원본 운영 구성을 보존한 파일입니다. 컨테이너는 `/data`에 원문 그룹·메타데이터를, `/stores`에 전처리 결과를, `/srv/var`에 쓰기 가능한 캐시 볼륨을 필요로 합니다. 공개본에서는 컨테이너 실행을 별도로 검증하지 않았습니다.
