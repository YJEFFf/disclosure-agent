"""
공시 코퍼스 접근 단일 통로.

원문/메타에 손대는 코드는 반드시 이 모듈만 거친다. 여기서 한 번에 처리하는 것:
  1) manifest 의 file_path 는 'raw/...' 로 시작 → 재구성된 01_공시원문 으로 치환
  2) 폴더명 NFC/NFD 자모분리 → 양쪽 모두 시도
  3) XML 이라고 적혀 있지만 내용은 HTML, meta 는 euc-kr 선언 → utf-8 강제
  4) 통용명(현대차·KT·엔씨소프트) → DART 법인명(corp_name) 통일

자체 점검:  python loader.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

# src/gongsi/io/loader.py → parents: [io, gongsi, src, gongsi-agent]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_DEFAULT = _REPO_ROOT / "configs" / "paths.yaml"

# ★ 2026-08-20 (D-1) — **경로를 환경변수로 덮을 수 있게 한다.**
#   여태 `data_root` 가 절대경로 하드코딩이라 «이 맥의 이 폴더»에서만 돌았다.
#   배포 컨테이너는 코퍼스를 읽기전용 볼륨(/data), 스토어를 다른 볼륨(/stores),
#   쓰기 가능한 캐시를 또 다른 볼륨에 두므로 **키마다 따로** 덮을 수 있어야 한다.
#
#   규칙:  paths.yaml 의 키 `raw_dir` → 환경변수 `GONGSI_RAW_DIR`
#          (`GONGSI_` + 키를 대문자로. 값은 ${} 전개 «전»에 들어가므로
#           `GONGSI_DATA_ROOT` 하나만 줘도 나머지가 전부 따라 움직인다.)
#   설정 파일 자체도 `GONGSI_PATHS_YAML` 로 지목할 수 있다.
_ENV_PREFIX = "GONGSI_"
_CFG = Path(os.environ.get(_ENV_PREFIX + "PATHS_YAML") or _CFG_DEFAULT)
_VAR = re.compile(r"\$\{(\w+)\}")

# manifest 의 file_path 가 달고 있는 접두사. 폴더를 재구성했으므로 떼어내고 RAW_DIR 에 붙인다.
_MANIFEST_PREFIX = "raw/"


def _load_cfg(cfg_path: Path = _CFG) -> dict:
    """paths.yaml → 환경변수 덮어쓰기 → ${} 전개.

    ★ 덮어쓰기가 **전개 «앞»**에 있어야 한다. `GONGSI_DATA_ROOT` 하나만 주면
      `raw_dir`·`meta_dir`·`derived_dir`·`event_db`… 가 전부 따라 움직이게 하려면
      `${data_root}` 를 풀기 «전»에 값을 갈아 끼워야 하기 때문이다.
    """
    if not cfg_path.exists():
        # ★ 조용히 넘어가지 않는다 (함정 23 — `except: pass` 가 사전을 통째로 삼켰던 그 병).
        raise FileNotFoundError(
            f"경로 설정 파일이 없습니다: {cfg_path}\n"
            f"  {_ENV_PREFIX}PATHS_YAML 로 다른 파일을 지목할 수 있습니다.")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    # ① 환경변수로 덮는다 (전개 전)
    overridden: list[str] = []
    for key in list(cfg):
        env = os.environ.get(_ENV_PREFIX + key.upper())
        if env:
            cfg[key] = env
            overridden.append(key)

    # ② ${} 전개
    #
    # ★ 2026-09-04 (R53) — **저장소 뿌리를 전개 변수 `${repo_root}` 로 심는다.**
    #   `data_root` 가 절대경로로 박혀 있던 탓에, 작업 폴더를 `gongsi-agent_20260819`
    #   → `_20260821` 로 이름만 바꾸자 설정이 **옛 경로에 남았다**. 그 셸에서
    #   `GONGSI_DATA_ROOT` 를 빼먹고 돌린 것들은 존재하지도 않는 폴더를 `mkdir` 해
    #   **빈 스토어를 파고 조용히 완주했다** (2026-09-04 19:59, 0바이트 section.sqlite).
    #   DERIVED_DIR 은 아래 경고 루프가 «빌드 전엔 없는 게 정상» 이라 보지 않으므로
    #   경고 한 줄 없이 지나간다 — 이 저장소가 가장 두려워하는 「조용히 틀린다」다.
    #   같은 사고가 `_20260815` → `_20260819` 때도 있었다(작업일지 §6312).
    #
    #   이제 `paths.yaml` 은 `${repo_root}` 를 쓴다. 폴더 이름이 무엇이든,
    #   저장소를 통째로 옮기든 따라온다. 씨앗을 `out` 에 미리 넣어 두는 이유는
    #   아래 전개가 «이미 처리된 키»(`out`)만 참조하기 때문이다.
    #   `GONGSI_REPO_ROOT` 로 덮을 수 있다(yaml 키가 아니라 여기서 따로 받는다).
    out: dict[str, str] = {
        "repo_root": os.environ.get(_ENV_PREFIX + "REPO_ROOT") or str(_REPO_ROOT),
    }
    for key, val in cfg.items():
        if isinstance(val, str):
            for _ in range(5):  # ${a} 중첩 전개 (무한루프 방지용 상한)
                if not _VAR.search(val):
                    break
                val = _VAR.sub(lambda m: str(out.get(m.group(1), m.group(0))), val)
        out[key] = val

    if overridden:
        print(f"[loader] 환경변수로 덮어쓴 경로: {', '.join(sorted(overridden))}",
              file=sys.stderr)
    return out


CFG = _load_cfg()
RAW_DIR = Path(CFG["raw_dir"])
META_DIR = Path(CFG["meta_dir"])
DERIVED_DIR = Path(CFG["derived_dir"])

# ★ 2026-09-04 (R50) — **런타임이 여는 스토어의 정본 목록.**
#   전에는 이 목록이 `scripts/stage_deploy.py:STORE_FILES` 와 `ops/sync.sh:STORE_ALL`
#   **둘**에 따로 적혀 있었고, `/health`·`/selfcheck` 는 그중 **4종만** 열어 봤다.
#   그러면 나머지 6벌이 배송 트리에서 빠져도 **워치독은 초록을 유지하고 답변만 조용히
#   나빠진다** — 이 프로젝트가 가장 두려워하는 실패 모양이다(「조용히 틀린다」).
#   목록을 여기 하나로 모으고 server.py 가 import 한다.
STORE_FILES: tuple[str, ...] = (
    "db/event.sqlite",
    "db/section.sqlite",
    "db/catalog.jsonl",
    "db/chains.jsonl",
    "db/correction_headers.jsonl",
    "db/cover_fields.jsonl",
    "db/major_notes.jsonl",
    "db/holding_tables.jsonl",
    "db/corr_items.jsonl",
    "db/ordinals.jsonl",
)

# ★ 원문·메타가 «없으면 없다고 말한다».  파생물(DERIVED_DIR)은 빌드 전에는 없는 것이
#   정상이므로 여기서 보지 않는다.  죽이지는 않는다 — 배송 트리는 raw 의 일부만 싣는다.
for _name, _d in (("raw_dir", RAW_DIR), ("meta_dir", META_DIR)):
    if not _d.exists():
        print(f"[loader] ★ 경고: {_name} 가 없습니다 → {_d}\n"
              f"         configs/paths.yaml 또는 {_ENV_PREFIX}{_name.upper()} 를 확인하십시오.",
              file=sys.stderr)


def nfc(text: str) -> str:
    """비교·조인용 표준형. 문자열 키는 전부 이걸 통과시킨다."""
    return unicodedata.normalize("NFC", text)


def resolve(file_path: str) -> Path:
    """manifest 의 file_path → 실제 디스크 경로.

    NFC 로 먼저 찾고, 없으면 NFD 로 재시도한다.
    (코퍼스를 다시 풀면 NFD 로 돌아오므로 정규화 스크립트를 돌렸더라도 이 폴백은 남겨둔다.)
    """
    rel = file_path.replace("\\", "/")
    if rel.startswith(_MANIFEST_PREFIX):
        rel = rel[len(_MANIFEST_PREFIX):]

    for form in ("NFC", "NFD"):
        cand = RAW_DIR / unicodedata.normalize(form, rel)
        if cand.exists():
            return cand
    raise FileNotFoundError(f"원문 없음: {file_path} (RAW_DIR={RAW_DIR})")


def read_doc(file_path: str) -> list[tuple[str, str]]:
    """문서 폴더 안의 파일을 (파일명, 본문) 목록으로. 인코딩은 utf-8 강제."""
    folder = resolve(file_path)
    docs: list[tuple[str, str]] = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in {".xml", ".html", ".htm"}:
            docs.append((f.name, f.read_text(encoding="utf-8", errors="replace")))
    return docs


def parse_doc(file_path: str):
    """문서 폴더 안의 XML 을 (파일명, lxml root) 목록으로.

    주의: DART 원문은 <?xml ...?> 선언을 달고 있지만 well-formed XML 이 아니다.
    본문에 'Sales & Trading' 같은 미이스케이프 & 가 섞여 있어
    xml.etree.ElementTree / 브라우저는 그 지점에서 파싱을 중단한다.
    lxml 의 recover=True 로만 끝까지 읽힌다. huge_tree 는 20MB 급 사업보고서용.
    """
    from lxml import etree  # 무거우니 필요할 때만

    parser = etree.XMLParser(recover=True, huge_tree=True)
    roots = []
    for name, text in read_doc(file_path):
        # ★ 2026-09-06 (R55 §B) — **이중따옴표 속성이 문서 후반부를 통째로 죽인다.**
        #   `ENG=""HD Hyundai … ""` 처럼 값이 따옴표 두 겹으로 감싸인 속성이 인용 상태를
        #   뒤집어, recover 로도 그 지점 «이후 절반»이 트리에서 사라진다(실측
        #   HD현대일렉트릭 20260316000940: TITLE 154 → 64 · «6. 배당에 관한 사항» 포함
        #   후반 전부 소실 → R17-038/048/051 배당지표 3문항 0.00 의 뿌리).
        #   모집단 실측: 전 코퍼스 4,622파일 중 **periodic 20파일**(exchange·major·holding 0
        #   — events 재빌드 불필요). 치환은 «이미 망가진 형태»만 건드린다: `=""X""` → `="X"`.
        #   치환 후 그 파일 TITLE 64 → 156(원문 154 + 첨부) · 배당 섹션 복원 확인.
        # ★ 심문 R55 성립 4 — 캡처에 `=` 를 배제한다. 종전 판은 `<TBODY ID="" CLASS="">`
        #   같은 «정상 이웃 빈 속성» 7파일도 뭉갰다(텍스트 산출은 동일했지만 속성 1개 소실).
        text = re.sub(r'=\s*""([^"<>=]*?)""', r'="\1"', text)
        roots.append((name, etree.fromstring(text.encode("utf-8"), parser)))
    return roots


def sections(file_path: str) -> list[str]:
    """정기공시의 TITLE 태그 = 목차. 청킹 경계로 그대로 쓴다."""
    out: list[str] = []
    for _name, root in parse_doc(file_path):
        for el in root.iter("TITLE"):
            title = " ".join("".join(el.itertext()).split())
            if title:
                out.append(title)
    return out


@dataclass(frozen=True)
class Corpus:
    manifest: list[dict]
    universe: list[dict]
    alias: dict[str, str]  # 통용명·영문명 → corp_name

    def canon(self, name: str) -> str:
        """'현대차' → '현대자동차' 처럼 어떤 표기가 와도 DART 법인명으로."""
        return self.alias.get(nfc(name).strip(), nfc(name).strip())

    def docs_of(self, name: str, doc_group: str | None = None) -> list[dict]:
        corp = self.canon(name)
        return [
            r for r in self.manifest
            if r["corp_name"] == corp and (doc_group is None or r["doc_group"] == doc_group)
        ]


def load_corpus() -> Corpus:
    import csv

    manifest = [
        {**r, "corp_name": nfc(r["corp_name"])}
        for r in (json.loads(line) for line in
                  Path(CFG["manifest"]).read_text(encoding="utf-8").splitlines() if line.strip())
    ]
    # universe.csv 는 UTF-8 BOM. utf-8-sig 로 읽어야 첫 컬럼명이 깨지지 않는다.
    with open(CFG["universe"], encoding="utf-8-sig", newline="") as fh:
        universe = [{k: v for k, v in row.items()} for row in csv.DictReader(fh)]

    alias: dict[str, str] = {}
    for row in universe:
        corp = nfc(row["corp_name"])
        for key in ("corp_name", "listed_name", "corp_eng_name", "stock_code", "corp_code"):
            val = (row.get(key) or "").strip()
            if val:
                alias[nfc(val)] = corp
    # ★ 2026-08-18 (M-55) — **영문 사명 회사들의 한글 통용 표기가 어디에도 없다.**
    #   universe.csv 의 corp_name·listed_name·corp_eng_name 이 전부 영문이라
    #   「**네이버**가 2025년 9월 18일 공시한 …」에 기업 특정이 **실패**하고
    #   회수가 0 이 된다 (실측: 제3자 홀드아웃 H3-027).
    #
    #   추측으로 넓히지 않는다 — **모집단이 닫혀 있다.** corp_name 에 한글이 한 자도
    #   없는 회사는 코퍼스 70개사 중 **NAVER · NC · HMM · JYP Ent 넷뿐**이다.
    #   각 표기는 **그 회사 자신의 정기공시 본문에서 「주식회사」 앞뒤에 실제로 쓰인 것**을
    #   채굴해 확인했다 (네이버 44회 · 에이치엠엠 31회 · 제이와이피 15회).
    #   엔씨소프트는 이미 `listed_name` 으로 들어와 있어 넣지 않는다.
    #   ★ 자회사 표기(스노우·네이버클라우드 등)는 **넣지 않는다** — 채굴 상위에 섞여
    #     나오지만 그것들은 다른 법인이다. 넣으면 M-48 반대 1(부분수열 규칙)과 같은
    #     «코퍼스 밖 회사를 단정하는» 오류가 된다.
    # ★ 2026-08-19 (M-113) — **정식 명칭·옛 사명으로 물으면 «없는 회사»라고 단언했다.**
    #   실측 T3-004: 「**한국전력기술**이 용인사옥을 양도하기로 한 공시…」 →
    #   「수집 대상 70개사에 포함되지 않습니다」. 코퍼스 사명은 「한전기술」이다.
    #   회수 실패보다 나쁘다 — **있는 회사를 없다고 단언**하기 때문이다.
    #
    #   채굴 자리: 그 회사 사업보고서 「1. 회사의 개요」의 **자기 선언**
    #       ○ 국문명칭 : 한국전력기술주식회사
    #   ★ 본문 전체 채굴은 **기각**했다 — 117건이 나오는데 대부분 **자회사**다
    #     (우리금융지주←우리은행 196 · LG에너지솔루션←LG화학 104 · 두산에너빌리티←두산 43).
    #     «회사의 개요»로 좁히면 **7건/6개사**만 남고 전부 같은 법인이다(옛 사명 포함).
    for _kr, _corp in (("삼성이앤에이", "삼성E&A"), ("삼성엔지니어링", "삼성E&A"),
                       ("삼성생명보험", "삼성생명"), ("신한금융지주회사", "신한지주"),
                       ("한국항공우주산업", "한국항공우주"), ("한국전력기술", "한전기술"),
                       ("대우조선해양", "한화오션")):
        if nfc(_kr) not in alias and any(nfc(r["corp_name"]) == _corp for r in universe):
            alias[nfc(_kr)] = _corp
    for _kr, _corp in (("네이버", "NAVER"), ("에이치엠엠", "HMM"),
                       ("제이와이피", "JYP Ent"), ("제이와이피엔터테인먼트", "JYP Ent")):
        if nfc(_kr) not in alias and any(nfc(r["corp_name"]) == _corp for r in universe):
            alias[nfc(_kr)] = _corp
    # ★ 2026-09-01 — 「하이닉스」·「기아자동차」로 물으면 «없는 회사»라고 단언했다(M-113 류).
    #   근거 채굴(섹션 DB is_latest=1): 기아 연혁 자기 선언 「기아자동차주식회사에서
    #   기아주식회사로 변경」 38회 · SK하이닉스 연혁 「주식회사 하이닉스반도체로 상호 변경」.
    #   「하이닉스」 단독 표기는 자사 본문 35회, 유니버스 70개사 중 유일 부분명 + 동일 법인
    #   연속(옛 사명 어간)이라 유일 해석이다 — 역질문하면 오히려 감점(평가지표 7 반대면).
    #   ★ 코퍼스 용례 0 인 순수 통용어(기아차·삼전)는 넣지 않는다 — 심문관 검토 대상.
    for _kr, _corp in (("기아자동차", "기아"), ("하이닉스반도체", "SK하이닉스"),
                       ("하이닉스", "SK하이닉스")):
        if nfc(_kr) not in alias and any(nfc(r["corp_name"]) == _corp for r in universe):
            alias[nfc(_kr)] = _corp
    # ★ 2026-09-06 (프리즈 후 · R4 부검) — 「SKT」로 물으면 **«코퍼스에 없다»고 단언**했다.
    #   제3자 출제자가 R4 60문항 중 4문항을 「SKT」로 썼고 셋이 **0.00**(H3R4-014/015/016).
    #   M-113·하이닉스와 **정확히 같은 병**이고, 회수 실패보다 나쁘다(있는 회사를 없다고 단언).
    #
    #   채택 근거 — 위 「하이닉스」에 쓴 자와 같은 자로 쟀다 (섹션 DB is_latest=1 실측):
    #     · 「SKT」 자사(SK텔레콤) 본문 **135섹션** ← 하이닉스 채택 근거(자사 35회)보다 4배 강하다
    #     · 유니버스 70개사 중 「SKT」로 해석될 다른 법인이 **없다** → 유일 해석(역질문하면 오히려 감점)
    #     · 「에스케이텔레콤」은 한글 음차이므로 위 에이치엠엠→HMM · 제이와이피→JYP Ent 와 같은 꼴
    #
    #   ★ 같은 자로 재서 **기각한 것들**(넣지 않는다):
    #     · 「포스코」 516섹션 — 포스코홀딩스·포스코인터내셔널·포스코퓨처엠이 **다른 법인**이다.
    #       유일 해석이 아니므로 역질문이 정답이다(위 M-48 반대 1과 같은 이유).
    #     · 「한전」 221섹션 — 통상 «한국전력공사»를 뜻하고 그것은 **코퍼스 밖**이다.
    #       한전기술로 단정하면 «다른 회사를 단정»하는 더 나쁜 오류가 된다.
    #     · 「기아차」 48섹션 · 「삼바」 0 · 「LG엔솔」 10 — **자사 본문 용례 0**(기아차의 상위는
    #       현대글로비스·POSCO홀딩스다). 종전 결정(순수 통용어 제외)을 그대로 둔다.
    #
    #   파급 실측: 문항 풀 전수 13,993개 중 「SKT|에스케이텔레콤」 포함은 **4개뿐**이고
    #   전부 소진된 R4 다 → 기존 채점 수치에 닿지 않는다(회귀 위험 0).
    for _kr, _corp in (("SKT", "SK텔레콤"), ("에스케이텔레콤", "SK텔레콤")):
        if nfc(_kr) not in alias and any(nfc(r["corp_name"]) == _corp for r in universe):
            alias[nfc(_kr)] = _corp
    # ★ 2026-09-06 (R54) — SKT 는 인스턴스 수정이었다. **클래스로 고친다** (함정 28 계열).
    #   후보를 손으로 떠올리지 않고 코퍼스에서 «전수 채굴»했다 (SKT·하이닉스와 같은 자):
    #     ⑴ 연혁 자기선언 「X에서 Y로 상호 변경」 70개사 전수 (M-113 의 자)
    #     ⑵ 사명의 한글 어간 (HD현대중공업→현대중공업) — 단독 표기 자사 용례로 검증
    #     ⑶ 영문 사명 이니셜 (KAI·HMC) — 자사 본문 «단어 경계» 용례로 검증
    #   채택 기준은 종전과 동일: ①자사 본문(is_latest=1) 단독 용례 > 0 ②70개사 중 유일
    #   해석(부분 포함 충돌 0) ③현재 «확신 거절»되는 이름만(기존 특정 경로 불변).
    #   아래 숫자는 전부 «단독 표기» 섹션 수다 — 「LG유플러스」 안의 「유플러스」는 안 셌다.
    #   ★ 같은 채굴에서 **기각**: 텔레콤(일반명사·자사 16 vs 타사 14) · 에너지솔루션(타사
    #     352 > 자사 103) · 생활건강(자사 1) · 이노텍(자사 2) · 씨엔에스(자사 0) ·
    #     빅히트 단독(「빅히트를 기록」 일반명사 위험 — 옛 사명 전체 표기만 채택) ·
    #     2글자 영문 이니셜 전부(ST·SS·HO… — 부분일치 허수, 통용 표기 아님) ·
    #     동양제철화학·포항종합제철·기아특수강 등 2000년 이전 사명(질문 개연성 0).
    for _kr, _corp in (
            ("빅히트엔터테인먼트", "하이브"),       # 연혁 자기선언 30회 · 자사 단독 11
            ("현대상선", "HMM"),                    # 자기선언 17회 · 자사 단독 21
            ("한화테크윈", "한화에어로스페이스"),   # 자기선언 11회 · 자사 단독 146
            ("한화케미칼", "한화솔루션"),           # 자사 단독 31 (2020 통합 전 사명)
            ("두산중공업", "두산에너빌리티"),       # 자사 단독 12 · 타사 95 (전부 동일 법인 지칭)
            ("LS산전", "엘에스일렉트릭"),           # 자사 단독 8 (옛 사명)
            ("엘에스산전", "엘에스일렉트릭"),       # 연혁 자기선언 23회 (한글 표기)
            ("현대중공업", "HD현대중공업"),         # 자사 단독 96 · 유니버스 내 유일
            ("현대일렉트릭", "HD현대일렉트릭"),     # 자사 단독 31
            ("유플러스", "LG유플러스"),             # 자사 단독 173
            ("제일제당", "CJ제일제당"),             # 자사 단독 14 (옛 사명이기도 하다)
            ("KAI", "한국항공우주"),                # 자사 단독 20 · 경계 있는 표기만
            ("HMC", "현대자동차")):                 # 이하-자기선언 6회 · 자사 단독 26
        if nfc(_kr) not in alias and any(nfc(r["corp_name"]) == _corp for r in universe):
            alias[nfc(_kr)] = _corp
    return Corpus(manifest=manifest, universe=universe, alias=alias)


if __name__ == "__main__":
    c = load_corpus()
    ok = miss = 0
    missing: list[str] = []
    for row in c.manifest:
        try:
            resolve(row["file_path"])
            ok += 1
        except FileNotFoundError:
            miss += 1
            if len(missing) < 5:
                missing.append(row["file_path"])

    corps = {r["corp_name"] for r in c.manifest}
    print("===== loader 자체 점검 =====")
    print(f"  manifest 문서      : {len(c.manifest):,}건 (기대 4,204)")
    print(f"  universe 기업      : {len(c.universe):,}개 (기대 70)")
    print(f"  경로 해석 성공     : {ok:,} / 실패 {miss:,}")
    print(f"  corp_name 조인     : {'OK' if corps <= set(a for a in c.alias.values()) else '불일치'}")
    print(f"  통용명 변환 확인   : 현대차→{c.canon('현대차')} · KT→{c.canon('KT')} · 005930→{c.canon('005930')}")
    if missing:
        print("  실패 예시:", *missing, sep="\n    ")
