"""공격 도구 property_attack, run_diff(LLM 노출용), attack_memory (PRD 7.3, F5-3, F4-3).

- property_attack: Hypothesis로 입력을 만들어 원본과 수정본을 비교한다 (시드 config.SEED). 불일치는 최소 사례로 줄인다.
- compare_versions: 같은 실행기로 원본·수정본·참조 구현을 함께 비교한다 (판정 노드에서 사용).
- attack_memory: 검증된 공격을 Chroma에 저장하고, 같은 유형 티켓에서 먼저 꺼내 쓴다.
LLM의 strict 함수 도구는 형태가 자유로운 JSON을 표현하지 못해, 입력·전략은 JSON 문자열로도 받는다.
"""

import hashlib
import json
import sys
import threading
from pathlib import Path

from sr_agent import config
from sr_agent.tools.code import _split_method
from sr_agent.tools.execution import _run, _trim, run_diff
from sr_agent.tools.workspace import Workspace

ATTACK_KINDS = ("under_fix", "regression")  # 수정 미달 / 회귀


def input_key(method: str, value) -> str:
    """공격 입력을 비교할 때 쓰는 키 (함수 + 키 정렬한 JSON). 같은 입력을 여러 라운드·판정에서 같은 것으로 본다."""
    return method + " " + json.dumps(value, sort_keys=True, ensure_ascii=False)


def _loads(value, name: str):
    """JSON 문자열이면 파싱한다. 실패하면 ValueError."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"{name} JSON 파싱 실패: {e}") from None
    return value


def compare_versions(
    method: str,
    strategy_spec: dict,
    versions: dict[str, Path],
    baseline: str,
    compare: str,
    max_examples: int,
    cwd: Path,
    timeout: float | None = None,
) -> dict:
    """여러 버전을 Hypothesis 입력으로 비교한다. 반환은 _property_runner 결과 그대로 또는 {"error"}."""
    return _run_runner(method, versions, baseline, compare, cwd, timeout,
                       {"strategy": strategy_spec, "max_examples": max_examples, "seed": config.SEED})


def compare_inputs(
    method: str,
    inputs: list,
    versions: dict[str, Path],
    baseline: str,
    compare: str,
    cwd: Path,
    timeout: float | None = None,
) -> dict:
    """정해진 입력 목록으로 여러 버전을 비교한다 (Hypothesis 없음). 반환 cases에 입력마다 모든 버전의 결과."""
    return _run_runner(method, versions, baseline, compare, cwd, timeout, {"inputs": inputs})


def _run_runner(method: str, versions: dict[str, Path], baseline: str, compare: str, cwd: Path,
                timeout: float | None, extra: dict) -> dict:
    prefix = config.TARGET_PACKAGE + "."
    method = method[len(prefix):] if method.startswith(prefix) else method
    if _split_method(method) is None:
        return {"error": f"method 형식 오류 (모듈.함수): {method!r}"}
    payload = json.dumps({
        "package": config.TARGET_PACKAGE, "method": method,
        "versions": {k: str(v) for k, v in versions.items()}, "baseline": baseline, "compare": compare, **extra,
    })
    timeout = timeout or config.PROPERTY_TIMEOUT_S
    proc = _run([sys.executable, "-m", "sr_agent.tools._property_runner"], cwd, timeout, stdin=payload)
    if proc is None:
        return {"error": f"timeout: {timeout}초 안에 끝나지 않음 (max_examples를 줄이거나 입력 범위를 좁힐 것)"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "실행 실패: " + _trim(proc.stderr or proc.stdout)}


def _row(case: dict) -> dict:
    r = case["results"]
    return {"input": case["input"], "original": r["original"], "patched": r["patched"],
            "same": r["original"] == r["patched"]}


def property_attack(ws: Workspace, method: str, strategy_spec, max_examples: int = 200) -> dict:
    """원본 vs 수정본을 Hypothesis 입력으로 비교한다.

    strategy_spec: {"args": [spec...], "kwargs": {이름: spec}} (dict 또는 JSON 문자열)
      spec 타입: integer(min,max), boolean, none, just(value), sampled(values), text, list(items,min_size,max_size),
      dict(fields), one_of(options), date(min,max), datetime(min,max)
    반환: {"examples", "mismatches", "minimal": {input, original, patched, same} | null, "cases": [...]}
    """
    try:
        spec = _loads(strategy_spec, "strategy_spec")
    except ValueError as e:
        return {"error": str(e)}
    if not isinstance(max_examples, int) or not 1 <= max_examples <= config.PROPERTY_MAX_EXAMPLES:
        return {"error": f"max_examples는 1~{config.PROPERTY_MAX_EXAMPLES}"}
    src = config.TARGET_SRC_SUBDIR
    raw = compare_versions(method, spec, {"original": ws.original / src, "patched": ws.work / src},
                           "original", "patched", max_examples, ws.work)
    if "error" in raw:
        return raw
    return {"examples": raw["examples"], "mismatches": raw["mismatches"],
            "minimal": _row(raw["minimal"]) if raw["minimal"] else None,
            "cases": [_row(c) for c in raw["cases"][: config.PROPERTY_CASES_REPORTED]]}


def run_diff_json(ws: Workspace, method: str, inputs_json) -> list[dict] | dict:
    """run_diff의 LLM 노출용: inputs를 JSON 문자열로 받는다."""
    try:
        inputs = _loads(inputs_json, "inputs_json")
    except ValueError as e:
        return {"error": str(e)}
    return run_diff(ws, method, inputs)


# ---------------- attack_memory ----------------

_MEMORY_LOCK = threading.Lock()  # 동시에 도는 공격 라운드가 Chroma를 함께 열지 않게

def _collection(chroma_dir: Path | None = None):
    from sr_agent.rag.index import open_client

    client = open_client(chroma_dir)
    return client.get_or_create_collection(config.ATTACK_COLLECTION, embedding_function=None)


def attack_memory(op: str, attack_type: str, record=None, chroma_dir: Path | None = None) -> list[dict] | dict:
    """성공한 공격 저장·조회 (F4-3).

    attack_type: 티켓 유형 (defect | standardize | security). 같은 유형 티켓에서 먼저 재사용한다.
    save의 record: {"ticket_id", "method", "kind"(under_fix|regression), "input", "expected", "actual", "note"}
    search의 record: {"method"} (선택, 같은 함수의 공격을 앞에 둔다)
    반환: [{"method", "kind", "input", "expected", "actual", "note", "ticket_id"}]
    """
    with _MEMORY_LOCK:
        return _attack_memory(op, attack_type, record, chroma_dir)


def _attack_memory(op: str, attack_type: str, record, chroma_dir: Path | None) -> list[dict] | dict:
    if op not in ("search", "save"):
        return {"error": "op는 search 또는 save"}
    if attack_type not in config.SEARCH_RULES_TOP_K:
        return {"error": f"attack_type은 {sorted(config.SEARCH_RULES_TOP_K)} 중 하나"}
    try:
        record = _loads(record, "record") or {}
    except ValueError as e:
        return {"error": str(e)}
    col = _collection(chroma_dir)
    if op == "save":
        missing = {"ticket_id", "method", "kind", "input", "expected", "actual"} - set(record)
        if missing:
            return {"error": f"record에 필요한 필드 없음: {sorted(missing)}"}
        if record["kind"] not in ATTACK_KINDS:
            return {"error": f"kind는 {ATTACK_KINDS} 중 하나"}
        body = json.dumps({k: record.get(k) for k in ("input", "expected", "actual")}, ensure_ascii=False,
                          sort_keys=True, default=str)
        key = f"{attack_type}|{record['method']}|{record['kind']}|{body}"
        attack_id = "ATK-" + hashlib.sha1(key.encode()).hexdigest()[:10]
        meta = {"attack_type": attack_type, "method": record["method"], "kind": record["kind"],
                "ticket_id": record["ticket_id"], "note": str(record.get("note", "")), "body": body}
        col.upsert(ids=[attack_id], embeddings=[[1.0]], documents=[record.get("note") or record["method"]],
                   metadatas=[meta])
        return [{"saved": attack_id}]
    found = col.get(where={"attack_type": attack_type})
    method = record.get("method")
    rows = []
    for meta in found["metadatas"]:
        body = json.loads(meta["body"])
        rows.append({"method": meta["method"], "kind": meta["kind"], **body, "note": meta["note"],
                     "ticket_id": meta["ticket_id"]})
    rows.sort(key=lambda r: (r["method"] != method, r["ticket_id"], r["method"]))
    return rows[: config.ATTACK_MEMORY_TOP_K]
