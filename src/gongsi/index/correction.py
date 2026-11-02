r"""정정공시 체인 (D단계). 출력: chains.jsonl ― rcept_no 별 chain_id/version/is_latest.

설계 근거: 01_팀문서\정정공시_체인_분석.md (전수 실측)
  - periodic:      manifest 키 (corp_code, doc_subtype, base_year, base_month) → 98.5% 매칭
  - major/holding: 원문 헤더 "2. 정정대상 공시서류의 최초제출일 : YYYY년 MM월 DD일" → 원본 rcept_dt 대조
  - exchange:      ※관련공시(related.jsonl) 중 "같은 서식명" 링크가 체인. 정정관련 제출일은
                   최초가 아니라 '직전'이라 단독으로는 체인이 안 된다 (분석 문서 실측)

원칙:
  - 미매칭 0 을 목표로 하지 않는다. 사유별 분류가 목표다
    (out_of_corpus = 원본이 2023-01-01 이전 / original_missing / parse_failed)
  - 이전 버전을 버리지 않는다. "정정 전 값" 질의를 위해 체인 전체를 남긴다

실행: scripts\run_correction.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "io"))
import loader  # noqa: E402

CORPUS_START = "20230101"

# 날짜 표기 실측 4종: "2024년 11월 18일" · "2023-01-27" · "2023.12.29." ·
# 그리고 원문 오타 "2025년 08년 28일" ("월"이 "년"). 구분자를 관대하게 받는다.
_RE_FIRST_DT = re.compile(
    r"2\.\s*정정대상\s*공시서류의\s*최초제출일\s*:?\s*(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})")
# exchange 정정 헤더. 이 날짜는 최초가 아니라 **직전** 제출일이다 (분석 문서 실측)
_RE_PREV_DT = re.compile(
    r"정정관련\s*공시서류\s*제출일\s*:?\s*(\d{4})[-.](\d{1,2})[-.](\d{1,2})")
_RE_TAGS = re.compile(r"<[^>]+>")
_RE_BRACKET = re.compile(r"^\[[^\]]*\]")          # [기재정정] [첨부정정] …
_RE_PAREN = re.compile(r"\([^)]*\)")              # (자율공시) (일반) …


# ── 계약 단위 게이트 ────────────────────────────────────────────────────
# ★ 2026-08-15 실측. exchange 체인은 **서식명**만 보고 union 한다. 그런데 한 회사의
#   공급계약 공시는 서식명이 전부 같다 — 「단일판매ㆍ공급계약체결」. 그래서 간선 하나가
#   잘못 걸리면 union-find 의 **전이성**이 두 체인을 통째로 합친다.
#
#   결과 (수정 전):
#     2버전 이상 체인 305개 중 **43개(14.1%)** 가 계약명이 뒤섞임
#     최악 삼성E&A 20230517800369 — 48버전에 계약명 13종
#     (평택 V2L / P3 154kV / P4 Ph1 / 사우디 AGIC / P5 Project …가 한 체인)
#
#   「이 계약의 정정 전 금액은?」에 **전혀 다른 계약의 금액**을 내주게 된다.
#
#   그래서 union 전에 **계약명(건명)이 같은지**를 본다. 판정 규칙은 «완전일치»다.
#   느슨한 규칙을 시험해 봤으나 전부 더 나빴다 (실측):
#     · 접두일치      : 3건만 구제, 의미 없음
#     · 최장공통접두≥10: 「PLN EPC (Tobelo)」↔「(Sumbawa)」, 「Lot3:전동차」↔「Lot2:전기기관차」
#                        처럼 **끝에서 갈리는 다른 계약**을 살려 버린다 — 더 위험하다
#   변별 토큰이 문자열 **뒤쪽**에 있으므로 접두 계열은 원리적으로 안 맞는다.
#
#   트레이드오프를 명시한다: 완전일치는 «같은 계약인데 건명 표기가 바뀐» 체인도 끊는다.
#   그래도 이쪽이 옳다 — **합치면 확신오답이 나오고, 끊으면 「정정 전 값을 못 찾음」이 된다.**
#   후자는 한계이고 전자는 거짓이다.
_RE_KEY = re.compile(r"[^0-9A-Za-z가-힣]")


def contract_key(s: str | None) -> str:
    return _RE_KEY.sub("", str(s or "")).lower()


def same_contract(a: str | None, b: str | None,
                  sa: str | None = None, sb: str | None = None) -> bool:
    """두 공시가 같은 계약인가. 한쪽이라도 단서가 없으면 판단 보류(True).

    ★ 2026-08-20 **심문(R32) 지적 (M-140)** — 건명만으로는 **이름이 같은 다른 계약**을
      못 가른다. 실측:

          효성중공업 「420kV 변압기 등 공급계약」·노르웨이 송전청 — 한 체인 5버전
            v0/v1/v3  기간 **2023-11-08**~…   1,018억 → 1,135억   ┐ 계약 A
            v2/v4     기간 **2024-07-05**~…   3,597억 → 3,723억   ┘ 계약 B (is_latest=1)
          삼성바이오로직스 「의약품 위탁생산계약」 — 한 체인 **17버전 · 시작일 12종**

      → **계약기간 시작일이 다르면 다른 계약이다.** 둘 다 있고 다를 때만 끊는다
        (없으면 종전대로 판단 보류 — 「합치면 확신오답, 끊으면 한계」 원칙은 그대로다).

      전 코퍼스: 2버전 이상 체인 **325개 중 10개(3.1%)** 가 시작일 2종 이상이고,
      그 체인들에서 `is_latest=0` 이 된 공시가 **41건 · 10.05조원**이다.
      「최신 금액은?」에 **다른 계약의 금액**을 «현재 유효본»이라며 답하게 된다.
    """
    ka, kb = contract_key(a), contract_key(b)
    if ka and kb and ka != kb:
        return False
    sa, sb = str(sa or "").strip(), str(sb or "").strip()
    if sa and sb and sa != sb:
        return False                      # 계약기간 시작일이 다르면 다른 계약이다
    return True


def _grp_by_day(members, manifest) -> dict:
    """R50 — 멤버를 접수일별로 묶는다 (ext 버킷 «같은 날 형제» 계기용)."""
    out: dict = defaultdict(list)
    for m in members:
        if m in manifest:
            out[manifest[m]["rcept_dt"]].append(m)
    return out


def _ext_node(corp_code: str, dt: str, contract: str | None,
              start: str | None = None, root_dt: str | None = None) -> str:
    """코퍼스 밖 원공시를 가리키는 가상 노드.

    ★ 전에는 `ext:{회사}:{날짜}` 였다. 그러면 **같은 회사가 코퍼스 밖 원공시를 가진
      서로 다른 계약**이 전부 한 버킷으로 합쳐진다 — 삼성E&A 의 P3 154kV · P3 Ph3 ·
      P4 Ph1 · P3 Ph2 네 계약이 그렇게 17버전 한 체인이 됐다 (실측).
      계약 키를 노드에 넣어 계약 단위로 가른다. 건명이 없으면 종전대로 회사·날짜 버킷.

    ★★ 두 번째 실측(2026-08-15). 처음엔 키를 `[:24]` 로 **잘라서** 넣었다.
       그랬더니 위 «접두 계열은 원리적으로 안 맞는다»는 판정을 **내 손으로 되살렸다** —
       끝에서 갈리는 계약이 절단 후 같은 키가 되어 다시 합쳐졌다:

           사우디 Marjan Increment Program PKG #6  ↔  PKG #12
           필리핀 South Commuter Railway (CP S-04) ↔ (S-05) ↔ (S-06)
           카타르 Lusail Plaza Towers(Plot4)       ↔ (Plot3)

       잔여 오염 9개 중 3개가 이것이었다. **자르지 말고 전체 키를 해시**한다.
    """
    ck = contract_key(contract)
    if not ck:
        return f"ext:{corp_code}:{dt}"
    h = hashlib.blake2s(ck.encode("utf-8"), digest_size=8).hexdigest()
    # ★ 2026-08-20 (M-140 ②) — 계약기간 시작일도 키에 넣는다. 건명이 같은 **다른 계약**이
    #   같은 가상 노드로 합쳐지는 것을 막는다(삼성바이오로직스 「의약품 위탁생산계약」은
    #   건명이 전부 같고 시작일만 다르다 — 한 체인에 16버전·시작일 11종이 됐다).
    _st = str(start or "").strip()
    # ★ 2026-09-03 (R48 · R13-014 = H9-030 오병합 클래스) — 투자판단류 «서술형» 공시는
    #   건명이 서식 제목이고 계약기간(start)도 없어 위 두 키가 전부 무력하다. 그런데
    #   **서로 다른 두 계약이 코퍼스 밖 같은 날(2022-12-28)에 무더기 정정**되면 ※관련공시
    #   날짜 버킷이 하나로 뭉쳐 두 체인이 병합된다(삼성바이오 의향서 — 20버전 한 체인 ·
    #   질문의 계약 전부 is_latest=0 → 제품이 «전제부정» + 딴 계약 값 확신 단언 · 0.00).
    #   판별자는 정정헤더 「정정관련 공시서류제출일」 — 이 계열은 **최초(뿌리) 날짜라 체인 내
    #   상수**다(실측: A=2021-12-23 ×10 · B=2022-04-25 ×10, 예외 0).
    #   ★ 함정 20 — 공급계약 계열의 헤더 날짜는 «직전»이라 체인 안에서 변한다. 그래서
    #   **뿌리 날짜가 코퍼스 밖일 때만** 키에 넣는다: 공급계약 체인은 «직전이 코퍼스 밖»인
    #   멤버가 체인당 최대 1건(첫 in-corpus 정정)이라 키가 갈려도 chain 이 안 쪼개지고,
    #   나머지 멤버는 linked_prev_date 로 직접 이어진다.
    _rd = str(root_dt or "").replace("-", "").strip()
    if not (_rd and _rd < CORPUS_START):
        _rd = ""
    return (f"ext:{corp_code}:{dt}:{h}" + (f":{_st}" if _st else "")
            + (f":r{_rd}" if _rd else ""))


def norm_name(name: str) -> str:
    """보고서명 비교용 정규화. '[기재정정]단일판매ㆍ공급계약 체결(자율공시)' → '단일판매공급계약체결'"""
    s = _RE_BRACKET.sub("", name or "")
    s = _RE_PAREN.sub("", s)
    return re.sub(r"[\sㆍ·※]", "", s)


def norm_name_major(name: str) -> str:
    """major/holding 전용 — **괄호를 남긴다.**

    ★ 2026-08-17 M-44. 홀드아웃 실패를 파고들다 찾았다. `norm_name` 은 exchange 의
      「…(자율공시)」 접미를 떼려고 괄호를 지우는데, **major 는 괄호 안이 곧 유형**이다.

          주요사항보고서(교환사채권발행결정)  →  '주요사항보고서'
          주요사항보고서(자기주식처분결정)    →  '주요사항보고서'      ← 같아진다

      그래서 같은 날 같은 회사가 낸 **서로 다른 공시**가 한 정정 체인으로 묶였다.
      결과로 원공시가 `is_latest=0` 이 되고, 제품이 그 공시를 「코퍼스에 없다」고 답한다.
      실측 — 유형이 섞인 체인 11개 · 그 안에서 잘못 밀린 행 25건 ·
      `is_latest=0` 인데 정정본이 아닌 행 225건.
      제3자 홀드아웃 T3-032(하나금융지주 자기주식신탁 4,000억)가 정확히 이 경로로 틀렸다.

      **유형이 다르면 정정 관계가 성립할 수 없다.** 함정 16-1 의 계약 단위 게이트와 같은 계열이다.
    """
    return re.sub(r"[\sㆍ·※]", "", _RE_BRACKET.sub("", name or ""))


def _header_date(doc: dict, pattern: re.Pattern) -> str | None:
    """정정 원문 헤더에서 날짜(YYYYMMDD). 태그 제거·공백 압축 후 정규식."""
    for _name, text in loader.read_doc(doc["file_path"]):
        flat = _RE_TAGS.sub(" ", text[:20000])     # 헤더는 문서 첫머리에 있다
        flat = " ".join(flat.split())              # 연속 공백 압축 ― "2024 년 7 월" 대응
        m = pattern.search(flat)
        if m:
            y, mo, d = m.groups()
            return f"{y}{int(mo):02d}{int(d):02d}"
    return None


def header_first_date(doc: dict) -> str | None:
    return _header_date(doc, _RE_FIRST_DT)


def header_prev_date(doc: dict) -> str | None:
    return _header_date(doc, _RE_PREV_DT)


class UnionFind:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)      # 작은 키(이른 접수)가 루트


def build() -> int:
    c = loader.load_corpus()
    manifest = {r["rcept_no"]: r for r in c.manifest}
    out_rows: list[dict] = []
    stats = defaultdict(int)

    # ---------- 1) periodic: manifest 키 ----------
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in c.manifest:
        if r["doc_group"] == "periodic":
            key = (r["corp_code"], r["doc_subtype"], r.get("base_year"), r.get("base_month"))
            groups[key].append(r)
    for key, docs in groups.items():
        docs.sort(key=lambda d: d["rcept_no"])
        chain_id = docs[0]["rcept_no"]
        has_orig = any(not d.get("is_correction") for d in docs)
        for i, d in enumerate(docs):
            status = "linked" if has_orig or len(docs) > 1 else "single"
            if not has_orig and d.get("is_correction"):
                status = "original_missing"        # 한화솔루션 케이스: 원본 없이 정정본만
            out_rows.append({"rcept_no": d["rcept_no"], "doc_group": "periodic",
                             "chain_id": chain_id, "version": i,
                             "is_latest": 1 if i == len(docs) - 1 else 0,
                             "status": status, "method": "manifest_key"})
            if d.get("is_correction"):
                stats[f"periodic:{status}"] += 1

    # ---------- 2) major / holding: 원문 헤더 ----------
    by_corp_dt: dict[tuple, list[dict]] = defaultdict(list)
    for r in c.manifest:
        if r["doc_group"] in ("major", "holding"):
            by_corp_dt[(r["corp_code"], r["doc_group"], r["rcept_dt"])].append(r)

    mh_chain: dict[str, str] = {}                  # rcept_no → chain_id(원본 rcept_no 또는 ext:)
    mh_status: dict[str, str] = {}
    for r in c.manifest:
        if r["doc_group"] not in ("major", "holding") or not r.get("is_correction"):
            continue
        try:
            first_dt = header_first_date(r)
        except Exception:                          # noqa: BLE001
            first_dt = None
        if not first_dt:
            mh_status[r["rcept_no"]] = "parse_failed"
            stats[f"{r['doc_group']}:parse_failed"] += 1
            continue
        if first_dt < CORPUS_START:
            mh_chain[r["rcept_no"]] = f"ext:{r['corp_code']}:{first_dt}"
            mh_status[r["rcept_no"]] = "out_of_corpus"
            stats[f"{r['doc_group']}:out_of_corpus"] += 1
            continue
        cands = [d for d in by_corp_dt.get((r["corp_code"], r["doc_group"], first_dt), [])
                 if d["rcept_no"] != r["rcept_no"]]
        base = norm_name_major(r["report_nm"])         # ★ 괄호(=유형)를 남긴다 (M-44)
        named = [d for d in cands if norm_name_major(d["report_nm"]) == base]
        # ★ M-44 — 전에는 `pick = (named or cands)` 라 서식명이 안 맞으면 **같은 날 아무
        #   공시에나** 이었다. 실측상 그 폴백이 실제로 쓰인 적은 0건이지만, 괄호를 남기는
        #   순간 «불일치»가 생기므로 이제부터는 폴백이 살아난다. 그러면 안 된다 —
        #   **못 잇는 것은 한계이고, 잘못 잇는 것은 거짓이다** (3-b 절의 원칙 그대로).
        pick = named
        if pick:
            orig = min(pick, key=lambda d: d["rcept_no"])
            mh_chain[r["rcept_no"]] = orig["rcept_no"]
            mh_status[r["rcept_no"]] = "linked"
            stats[f"{r['doc_group']}:linked"] += 1
        else:
            mh_status[r["rcept_no"]] = "original_missing"
            stats[f"{r['doc_group']}:original_missing"] += 1

    chains_mh: dict[str, list[str]] = defaultdict(list)
    for no, cid in mh_chain.items():
        chains_mh[cid].append(no)
    for cid, members in chains_mh.items():
        full = ([cid] if cid in manifest else []) + sorted(members)
        for i, no in enumerate(full):
            out_rows.append({"rcept_no": no, "doc_group": manifest.get(no, {}).get("doc_group", ""),
                             "chain_id": cid, "version": i,
                             "is_latest": 1 if i == len(full) - 1 else 0,
                             "status": mh_status.get(no, "linked"), "method": "header_date"})
    for no, st in mh_status.items():
        if st in ("parse_failed", "original_missing"):
            out_rows.append({"rcept_no": no, "doc_group": manifest[no]["doc_group"],
                             "chain_id": no, "version": 0, "is_latest": 1,
                             "status": st, "method": "header_date"})

    # ---------- 3) exchange: ※관련공시 중 같은 서식명 링크 ----------
    rel = defaultdict(list)
    rel_path = Path(loader.DERIVED_DIR) / "db" / "related.jsonl"
    for line in open(rel_path, encoding="utf-8"):
        row = json.loads(line)
        rel[row["rcept_no"]].append(row)

    # 계약명(건명)은 manifest 에 없고 events.jsonl 에 있다. parse_event 가 먼저 돈다(README 순서).
    ev_name: dict[str, str] = {}
    ev_party: dict[str, str] = {}
    ev_start: dict[str, str] = {}          # 계약기간 시작일 — 계약 단위 게이트 보강 (M-140)
    ev_amt: dict[str, int] = {}            # R50 — ext 버킷 «같은 날 형제» 판별자 (3-a)
    ev_path = Path(loader.DERIVED_DIR) / "db" / "events.jsonl"
    if ev_path.exists():
        for line in ev_path.open(encoding="utf-8"):
            row = json.loads(line)
            if row.get("계약명"):
                ev_name[row["rcept_no"]] = row["계약명"]
            if row.get("counterparty"):
                ev_party[row["rcept_no"]] = row["counterparty"]
            if row.get("period_start"):
                ev_start[row["rcept_no"]] = row["period_start"]
            if row.get("amount") is not None:
                ev_amt[row["rcept_no"]] = row["amount"]
    else:
        print("  ⚠ events.jsonl 이 없어 계약 단위 게이트를 적용하지 못했다 "
              "(run_parse_event.py 를 먼저 실행할 것) — 체인이 오병합될 수 있다")

    uf = UnionFind()
    ex_status: dict[str, str] = {}
    cut_edges = 0
    # ★ R50 — ext 버킷은 «즉시 union» 하지 않고 멤버를 모아 둔다. 3-a 에서 «같은 날
    #   형제»를 금액으로 가른 뒤에 union 한다 (아래 3-a 주석 참조).
    ext_members: dict[str, list[str]] = defaultdict(list)
    for r in c.manifest:
        if r["doc_group"] != "exchange" or not r.get("is_correction"):
            continue
        no, base = r["rcept_no"], norm_name(r["report_nm"])
        same_name = [x for x in rel.get(no, []) if norm_name(x["related_nm"]) == base]
        if not same_name:
            # 폴백: 헤더의 '정정관련 공시서류제출일'(직전 제출일)로 한 칸 거슬러 올라간다.
            # 직전이 또 정정본이면 그쪽 간선이 다시 이어지므로 union-find 가 체인을 완성한다.
            try:
                prev_dt = header_prev_date(r)
            except Exception:                      # noqa: BLE001
                prev_dt = None
            # 같은 날 같은 서식으로 **다른 계약**을 정정한 경우가 있다. 계약명까지 봐야
            # min(rcept_no) 이 엉뚱한 건을 집지 않는다.
            cands = [d for d in c.manifest
                     if d["doc_group"] == "exchange" and d["corp_code"] == r["corp_code"]
                     and d["rcept_dt"] == prev_dt and d["rcept_no"] != no
                     and norm_name(d["report_nm"]) == base
                     and same_contract(ev_name.get(no), ev_name.get(d["rcept_no"]),
                                       ev_start.get(no), ev_start.get(d["rcept_no"]))] if prev_dt else []
            if cands:
                uf.union(no, min(cands, key=lambda d: d["rcept_no"])["rcept_no"])
                ex_status[no] = "linked_prev_date"
                stats["exchange:linked_prev_date"] += 1
            elif prev_dt and prev_dt < CORPUS_START:
                # ★ R50 — 두 자리 통일: 아래 관련공시 분기는 start 를 넘기는데 여기만
                #   안 넘겼다(함정 23 「한쪽에만 있는 처방」). 다만 실측상 이 통일 자체는
                #   ext 노드 수를 40 → 40 으로 **한 개도 안 바꾼다** — 대칭을 위한 것이지
                #   수리가 아니다. 수리는 3-a 다.
                ext_members[_ext_node(r["corp_code"], prev_dt, ev_name.get(no),
                                      ev_start.get(no))].append(no)
                ex_status[no] = "out_of_corpus"
                stats["exchange:out_of_corpus"] += 1
            else:
                ex_status[no] = "no_related_link"
                stats["exchange:no_related_link"] += 1
            continue
        linked_any = out_any = False
        # R48 — 코퍼스 밖 날짜 버킷의 «뿌리 신원» 판별자 (함수 주석 참조).
        try:
            _hdr_dt = header_prev_date(r)
        except Exception:                          # noqa: BLE001
            _hdr_dt = None
        for x in same_name:
            if x.get("related_rcept"):
                # ★ 계약 단위 게이트. 서식명이 같아도 **다른 계약이면 잇지 않는다.**
                if not same_contract(ev_name.get(no), ev_name.get(x["related_rcept"]),
                                     ev_start.get(no), ev_start.get(x["related_rcept"])):
                    cut_edges += 1
                    continue
                uf.union(no, x["related_rcept"])
                linked_any = True
            else:
                dt = (x.get("related_dt") or "").replace("-", "")
                if dt and dt < CORPUS_START:
                    ext_members[_ext_node(r["corp_code"], dt, ev_name.get(no),
                                          ev_start.get(no), root_dt=_hdr_dt)].append(no)
                    out_any = True
        # ★ R48-2 — «직전 제출일 걷기» 간선을 관련공시가 있어도 겸용한다 (유일 후보 한정).
        #   시뮬 실측: ext 접미사만 넣자 삼성E&A 등 **정당 체인 5개가 끊겼다** — 그들의
        #   직접 링크(related_rcept)는 8/22 정직성 게이트로 ambiguous 가 됐고, 공유
        #   out-of-corpus 버킷이 몰래 체인을 지탱하고 있었다. 접미사가 그 다리를 끊으니
        #   지탱의 실체가 드러난 것. 직전 걷기(헤더제출일 = in-corpus 문서 접수일 + 서식 +
        #   계약 게이트 + **유일**)로 그 체인을 정면으로 잇는다. min() 집기는 안 한다 —
        #   후보 둘이면 잇지 않는다(못 잇는 것은 한계, 잘못 잇는 것은 거짓).
        #   ex_status 부기는 종전 그대로 둔다(간선만 추가 — 기준선 통계 불변).
        if _hdr_dt and _hdr_dt >= CORPUS_START:
            _pcands = [d for d in c.manifest
                       if d["doc_group"] == "exchange"
                       and d["corp_code"] == r["corp_code"]
                       and d["rcept_dt"] == _hdr_dt and d["rcept_no"] != no
                       and norm_name(d["report_nm"]) == base
                       and same_contract(ev_name.get(no), ev_name.get(d["rcept_no"]),
                                         ev_start.get(no), ev_start.get(d["rcept_no"]))]
            if len(_pcands) == 1:
                uf.union(no, _pcands[0]["rcept_no"])
        ex_status[no] = "linked" if linked_any else ("out_of_corpus" if out_any else "unresolved")
        stats[f"exchange:{ex_status[no]}"] += 1

    # ---------- 3-a) ext 버킷 «같은 날 형제» 분할 (R50) ----------
    # ★ 증상. 서로 다른 두 계약이 한 «코퍼스 밖 원공시» 버킷에 뭉쳐 한 체인이 된다.
    #     20250117800487 현대자동차(주) 1,652,800,000,000   ┐ 현대글로비스 — 한 체인
    #     20250117800494 기아(주)       1,345,900,000,000   ┘   (원문 대조로 별개 확인)
    #   그러면 코드가 **있지도 않은 정정을 단언한다** —
    #     「[정정 이력] 상대방: 최초(v0) 현대자동차(주) → 정정 1차(v1) 현재 유효본 기아(주)」
    #   삼성바이오 체인 20231128800002 는 더 나쁘다. 두 계약이 «지그재그»로 섞여
    #   122.4B → 48.9B → 122.4B → 48.9B 라는 «왕복 4단 사다리»가 만들어지고,
    #   그 양 끝을 빼서 **가짜 「-73,456,446,959원 감소」**를 `[코드 계산]` 권위로 못 박았다
    #   (함정 62 — 「코드가 «차액»을 단언하는 것은 «승자»를 단언하는 것만큼 위험하다」).
    #
    # ★ 판별식. ext 노드는 «코퍼스 밖 원공시 **하나**»를 가리키는 가상 버킷이다.
    #   그러므로 그 안에 **같은 날 같은 서식으로 낸 문서가 둘** 있으면 그것은 한 원공시의
    #   두 판일 수 없다 — 서로 다른 계약이다. 다만 **금액이 서로 다를 때만** 가른다.
    #   실측(2건 이상 뭉치는 ext 버킷 115개 전수): 같은 날 2건 이상을 담은 버킷 6개 ·
    #   실질 4쌍. 원문 전수 판정 → **오병합 3 · 정당 1**. 정당한 1쌍
    #   (20240321801006 / 801360)은 정정사유가 「정정일자 정정」뿐이라 **금액이 같다** —
    #   금액 조건이 그것을 그대로 보존한다. **3/3 정타 · 1/1 정당 보존.**
    #
    # ★ ⑷ 가 안전장치다. 어느 갈래에도 배정 못 하는 멤버가 하나라도 있으면 **가르지 않는다.**
    #   그래서 Roche 버킷(멤버 셋 중 20230605800006 의 금액이 두 대표 중 어느 것도 아님)은
    #   **보류**된다 — 알려진 오병합 하나가 남지만, **잘못 가르지는 않는다.**
    #   「못 잇는 것은 한계이고, 잘못 잇는 것은 거짓이다」의 반대편 적용이다.
    #
    # ★ 하지 마라 — 「금액 짝 잇기」를 ext 밖의 일반 체인에 쓰는 것. 검출력을 쟀고 기각했다:
    #   같은 날 2판 이상 체인 46개 중 금액 짝이 깔끔히 성립하는 것은 **3개(6.5%)** 뿐이다.
    #   계약상대를 `_ext_node` 키에 넣는 대안도 기각 — 법인격어 제거 키는 삼성바이오(익명
    #   상대)를 못 고치고, 원시 키는 「한국수력원자력(주) ↔ 한국수력원자력주식회사」를
    #   **오분할**한다.
    ext_split = 0
    for _node, _mem in ext_members.items():
        _mem = sorted(set(_mem))
        _byday: dict[str, list[str]] = defaultdict(list)
        for _m in _mem:
            _byday[manifest[_m]["rcept_dt"]].append(_m)
        _sib = max(_byday.values(), key=len)              # 같은 날 형제 = 갈래 후보
        _amts = [ev_amt.get(_m) for _m in _sib]
        _ok = (len(_sib) >= 2                              # ⑴ 같은 날 둘 이상
               and all(a is not None for a in _amts)       # ⑵ 금액이 다 있고
               and len(set(_amts)) == len(_amts)           # ⑶ 서로 «다르고»
               and all(ev_amt.get(_m) in set(_amts) for _m in _mem))   # ⑷ 나머지도 배정 가능
        for _m in _mem:
            uf.union(_m, f"{_node}:a{ev_amt[_m]}" if _ok else _node)
        if _ok:
            ext_split += 1

    # ---------- 3-b) unresolved 구제: 계약 3키가 **유일할 때만** ----------
    # ★ 2026-08-15 조사. 계약 게이트를 넣은 뒤 남은 `unresolved` 12건을 전수로 열었더니
    #   **`※ 관련공시` 가 「이 공시의 원본」이 아니라 「관련 있는 다른 공시」를 가리키고 있었다.**
    #
    #     한전기술 「PLN EPC (Tobelo)」 정정  → 관련공시가 (Sumbawa) 체결 공시
    #     LIG    「TMMR 2차 양산」   정정  → 관련공시가 「'23년 무전기세트 외 3항목」
    #     두산퓨얼셀 「LTSA」        정정  → 관련공시가 「연료전지 시스템 공급 계약」
    #
    #   CLAUDE.md 함정 15 의 「※관련공시 매칭률 100%」는 **해지 20건**에서 잰 값이다.
    #   정정 체인에는 그대로 적용되지 않는다.
    #
    #   그래서 (회사·서식·계약명·상대방) 3키로 이어 봤더니 12건 중 8건이 상대를 찾았다.
    #   **그러나 3키를 그냥 쓰면 안 된다** — 삼성바이오로직스는 상대방이 익명이라
    #   「의약품위탁생산계약 / 아시아 소재 제약사」 한 그룹에 **15건**이 뭉친다.
    #   그건 오늘 내내 고친 그 오병합의 재발이다.
    #
    #   → 프로젝트 원칙 그대로: **완전일치가 «유일성»을 가질 때만 단정한다.**
    #     상대가 정확히 하나(그룹 크기 2)인 경우에만 잇고, 나머지는 unresolved 로 둔다.
    #     못 잇는 것은 한계이고, 잘못 잇는 것은 거짓이다.
    key3: dict[tuple, list[str]] = defaultdict(list)
    for r in c.manifest:
        if r["doc_group"] != "exchange":
            continue
        nm, cp = contract_key(ev_name.get(r["rcept_no"])), contract_key(ev_party.get(r["rcept_no"]))
        if nm and cp:
            key3[(r["corp_code"], norm_name(r["report_nm"]), nm, cp)].append(r["rcept_no"])
    rescued = 0
    for no, st in list(ex_status.items()):
        if st not in ("unresolved", "no_related_link"):
            continue
        nm, cp = contract_key(ev_name.get(no)), contract_key(ev_party.get(no))
        grp = key3.get((manifest[no]["corp_code"], norm_name(manifest[no]["report_nm"]), nm, cp), [])
        if len(grp) == 2:                      # 상대가 정확히 하나 = 유일성 확보
            other = next(x for x in grp if x != no)
            uf.union(no, other)
            ex_status[no] = "linked_contract_key"
            stats["exchange:linked_contract_key"] += 1
            stats[f"exchange:{st}"] -= 1
            rescued += 1

    # ---------- 3-c) 2차 열쇠: 정정헤더 «정정관련 공시서류제출일» + 계약명 완전일치 (R52) ----------
    # ★ 티켓 «체인 미연결 12» — 정정본이 자기 헤더에 원공시 제출일을 명시하고, 그 날짜에
    #   같은 회사·같은 서식의 문서가 코퍼스에 실재하는데도 체인이 안 묶인 것이 12건이었다.
    #   1차 열쇠(※ 관련공시)가 related_rcept 를 못 주는 자리다(무링크·날짜만·엉뚱한 공시 지목).
    #   함정 20 — 이 헤더 날짜는 «최초»가 아니라 «직전» 제출일이다. 그래서 「그 날짜에 접수된
    #   문서」와 직접 대조하는 이 방식이 옳다(직전이 또 정정본이면 그쪽 간선이 다시 이어져
    #   union-find 가 체인을 완성한다). 기존 «직전 걷기»(무링크 폴백·R48-2)와 다른 점:
    #     · same_contract(비면 보류=True · 시작일 불일치=끊음)가 아니라 **계약명 완전일치 +
    #       양쪽 비어있지 않음**을 요구한다 — 1차 열쇠 없이 잇는 자리라 더 좁게 걸되(함정 16-1),
    #       시작일은 게이트로 쓰지 않는다. 정정이 «계약기간 자체»를 고치는 경우가 실재한다
    #       (한전기술 20250304800611 — 시작일 12-19 → 12-05 정정. 그것 때문에 기존 폴백이 못 이었다).
    #     · 후보가 여럿일 때 min() 을 집지 않고 아래 ①②로 «유일화»한다.
    #   ① 후보 전원이 이미 한 체인이면 아무나 이어도 같다 → min 과 잇는다
    #     (삼성E&A P5 — 원공시와 무더기 정정본이 같은 날에 있다).
    #   ② 정정사항 표의 «정정전 계약금액»(corr_items)과 정확히 일치하는 후보가 하나면 그와
    #     잇는다 — 정정본이 스스로 적은 원공시의 금액이라 헤더 날짜보다 더 강한 판별자다
    #     (삼성바이오 — 같은 날·같은 계약명·같은 시작일 원공시가 2건이라 이것으로만 갈린다).
    #   ③ 그 밖에는 잇지 않는다 — 못 잇는 것은 한계이고, 잘못 잇는 것은 거짓이다.
    #   ★ out_of_corpus 멤버에도 간선은 놓되 부기는 그대로 둔다(R48-2 전례 — 통계 기준선 보존).
    corr_before_amt: dict[str, list[int]] = {}
    ci_path = Path(loader.DERIVED_DIR) / "db" / "corr_items.jsonl"
    if ci_path.exists():
        for line in ci_path.open(encoding="utf-8"):
            row = json.loads(line)
            vals = []
            for it in row.get("items", []):
                lab = str(it.get("label") or "")
                if "계약금액" in lab and "대비" not in lab:
                    digits = re.sub(r"[^0-9]", "", str(it.get("before") or ""))
                    if digits:
                        vals.append(int(digits))
            if vals:
                corr_before_amt[row["rcept_no"]] = vals
    hdr_linked = 0
    for no, st in list(ex_status.items()):
        if st not in ("unresolved", "no_related_link", "out_of_corpus"):
            continue
        ck = contract_key(ev_name.get(no))
        if not ck:
            continue                       # 계약명이 비면(『-』) 잇지 않는다 (티켓 명시)
        try:
            hdr_dt = header_prev_date(manifest[no])
        except Exception:                  # noqa: BLE001
            hdr_dt = None
        if not hdr_dt or hdr_dt < CORPUS_START:
            continue
        base = norm_name(manifest[no]["report_nm"])
        cands = [d for d in c.manifest
                 if d["doc_group"] == "exchange" and d["corp_code"] == manifest[no]["corp_code"]
                 and d["rcept_dt"] == hdr_dt and d["rcept_no"] != no
                 and norm_name(d["report_nm"]) == base
                 and contract_key(ev_name.get(d["rcept_no"])) == ck]
        pick = None
        if len(cands) == 1:
            pick = cands[0]
        elif len(cands) >= 2:
            # ★ uf.find 는 없는 노드를 «만들어 넣는다» — 후보를 함부로 find 하면
            #   비연결 원공시가 단독 체인 행으로 새어 나간다. 있는 노드만 find 한다.
            roots = {uf.find(d["rcept_no"]) if d["rcept_no"] in uf.p else d["rcept_no"]
                     for d in cands}
            if len(roots) == 1:
                pick = min(cands, key=lambda d: d["rcept_no"])     # ① 전원이 한 체인
            else:
                bef = corr_before_amt.get(no, [])
                hit = [d for d in cands if ev_amt.get(d["rcept_no"]) in bef]
                if len(hit) == 1:
                    pick = hit[0]                                  # ② 정정전 금액 유일 일치
        if pick is None:
            continue
        uf.union(no, pick["rcept_no"])
        hdr_linked += 1
        if st != "out_of_corpus":
            ex_status[no] = "linked_hdr_date"
            stats["exchange:linked_hdr_date"] += 1
            stats[f"exchange:{st}"] -= 1

    # ---------- 3-d) 3차 구제: «정정전 계약금액» 정확·유일 일치 (R52 심문 3차 ①) ----------
    # ★ 3-c 의 계약명 완전일치 게이트가 «의도적으로» 거부한 6건을 심문이 원문으로 전수 판정:
    #   **6/6 이 같은 계약**이었다 — 정정이 «체결계약명 자체»를 고치는 서식이 실재한다
    #   (VLAC→원유운반선 · 2척→1척 · 신고리→새울 개명 · 원공시 오타 「운반석」 · 파싱 접두 중복).
    #   이때 판별자는 이름이 아니라 **정정사항 표의 «정정전 계약금액»** 이다 — 정정본이
    #   스스로 적은 원공시의 금액이라, 후보의 금액과 **자리수까지 정확히 일치**하면 같은 계약이다.
    # 게이트(함정 16-1 방어): ①헤더 날짜(직전 제출일)의 같은 회사·같은 서식 후보에 한함
    #   ②정정전 금액이 있고 ③그 금액과 정확 일치하는 후보가 **유일**할 때만
    #   (같은 날 같은 금액 둘이면 잇지 않는다) ④금액이 비어 있으면 잇지 않는다.
    for no, st in list(ex_status.items()):
        if st not in ("unresolved", "no_related_link", "out_of_corpus"):
            continue
        bef = corr_before_amt.get(no, [])
        if not bef:
            continue
        try:
            hdr_dt = header_prev_date(manifest[no])
        except Exception:                  # noqa: BLE001
            hdr_dt = None
        if not hdr_dt or hdr_dt < CORPUS_START:
            continue
        base = norm_name(manifest[no]["report_nm"])
        cands = [d for d in c.manifest
                 if d["doc_group"] == "exchange" and d["corp_code"] == manifest[no]["corp_code"]
                 and d["rcept_dt"] == hdr_dt and d["rcept_no"] != no
                 and norm_name(d["report_nm"]) == base
                 and ev_amt.get(d["rcept_no"]) in bef]
        if len(cands) != 1:
            continue                       # ③ 유일할 때만 — 못 잇는 것은 한계, 잘못 잇는 것은 거짓
        uf.union(no, cands[0]["rcept_no"])
        hdr_linked += 1
        if st != "out_of_corpus":
            ex_status[no] = "linked_amt_match"
            stats["exchange:linked_amt_match"] += 1
            stats[f"exchange:{st}"] -= 1

    chains_ex: dict[str, list[str]] = defaultdict(list)
    for node in list(uf.p):
        chains_ex[uf.find(node)].append(node)
    for cid, members in chains_ex.items():
        real = sorted(m for m in members if m in manifest)
        for i, no in enumerate(real):
            out_rows.append({"rcept_no": no, "doc_group": "exchange",
                             "chain_id": cid, "version": i,
                             "is_latest": 1 if i == len(real) - 1 else 0,
                             "status": ex_status.get(no, "linked"), "method": "related_block"})
    for no, st in ex_status.items():
        if st in ("no_related_link", "unresolved"):
            out_rows.append({"rcept_no": no, "doc_group": "exchange",
                             "chain_id": no, "version": 0, "is_latest": 1,
                             "status": st, "method": "related_block"})

    # ---------- 저장 + 리포트 ----------
    out_path = Path(loader.DERIVED_DIR) / "db" / "chains.jsonl"
    seen: set[str] = set()
    with out_path.open("w", encoding="utf-8") as fh:
        for row in out_rows:
            if row["rcept_no"] in seen:            # 한 문서는 한 체인에만
                continue
            seen.add(row["rcept_no"])
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---------- ★ R50 — «진짜 검사» 세 줄 ----------
    # `exchange:unresolved` 는 이 모듈의 검사가 **아니다.** 코드로 확인했다:
    #   ex_status 는 :359~373 의 `for x in same_name:` 안에서만 정해지고, «직전 걷기»
    #   간선도 `_ext_node` 키 변경도 3-a 분할도 그 값에 **원리적으로 닿지 않는다.**
    #   실제로 시뮬 두 판에서 chains.jsonl 은 5행이 달라졌는데 매칭 사유별 통계 13종은
    #   **한 줄도 안 변했다.** 「검사가 눈이 멀었다」는 증거다.
    # 아래 셋은 **결과물(chains)에서** 센다 — 코드가 만들어 놓은 항등식이 될 수 없는 자리.
    # ★★ 그러나 이것들은 **«회귀 트립와이어»이지 «미탐지 탐지기»가 아니다** (R50 심문 ①-3).
    #   셋 다 「같은 날 형제」를 AND 조건으로 요구하므로, **같은 날 형제가 없는데 금액·상대가
    #   2종인 ext 버킷 54개**를 들여다보지도 않는다(전수 실측). 그 대부분은 「같은 계약의 판이
    #   올라가며 금액이 오른」 정상 사례로 보이지만 **판정하지 않았다.**
    #   → 「chain_multi_amount 가 5 다」를 **「잔여 오병합이 5 다」로 읽지 마라.**
    #     이 수가 «오르면» 오병합이 늘어난 것이고, «그대로»라고 없다는 뜻이 아니다.
    _chain_of: dict[str, str] = {}
    for row in out_rows:
        _chain_of.setdefault(row["rcept_no"], row["chain_id"])
    _members: dict[str, list[str]] = defaultdict(list)
    for _no, _cid in _chain_of.items():
        _members[_cid].append(_no)
    _multi_amt = _multi_party = 0
    for _cid, _ms in _members.items():
        if len(_ms) < 2:
            continue
        _byday2: dict[str, list[str]] = defaultdict(list)
        for _m in _ms:
            if _m in manifest:
                _byday2[manifest[_m]["rcept_dt"]].append(_m)
        if any(len(v) >= 2 for v in _byday2.values()):
            if len({ev_amt[_m] for _m in _ms if _m in ev_amt}) >= 2:
                _multi_amt += 1
        if len({contract_key(ev_party.get(_m)) for _m in _ms
                if contract_key(ev_party.get(_m))}) >= 2:
            _multi_party += 1
    _ext_sameday = sum(1 for _mem in ext_members.values()
                       if any(len(v) >= 2 for v in
                              _grp_by_day(sorted(set(_mem)), manifest).values()))

    n_corr = sum(1 for r in c.manifest if r.get("is_correction"))
    print("===== correction 리포트 =====")
    print(f"  정정 문서: {n_corr:,}건 · 체인 행: {len(seen):,}")
    print(f"  계약 단위 게이트: 서식명은 같지만 계약명이 달라 **끊은 간선 {cut_edges}건** "
          f"(union-find 전이성으로 체인이 통째로 합쳐지는 것을 막는다)")
    print(f"  ext 버킷 «같은 날 형제» 분할: **{ext_split}개 버킷** (R50 기준선 3)")
    print(f"  2차 열쇠(정정헤더 제출일 + 계약명 완전일치): 새 간선 {hdr_linked}건 (R52)")
    print("  [정정 매칭 사유별]")
    for k in sorted(stats):
        print(f"    {k:<28} {stats[k]:>5}")
    print("  [오병합 계기 — ★ unresolved 는 이 모듈의 검사가 아니다 (항등식)]")
    print(f"    chain_multi_amount   {_multi_amt:>5}   R50 기준선 5  (R49 이전 7 · major 3 + exchange 2)")
    print(f"    chain_multi_party    {_multi_party:>5}   R50 기준선 12 (R49 이전 13 · 계약상대 2종 이상)")
    print(f"    ext_bucket_sameday   {_ext_sameday:>5}   R50 기준선 6  («후보» 모집단 — 분할해도 안 움직인다)")
    print(f"    ext_split(위 분할)   {ext_split:>5}   R50 기준선 3  (실제로 «갈린» 버킷 = 움직이는 계기)")
    print(f"  출력: {out_path}")
    return 0


def main() -> int:
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
