"""property_attack·판정이 띄우는 자식 프로세스. 여러 버전의 대상 코드를 한 프로세스에 올려 Hypothesis로 비교한다.

사용: python -m sr_agent.tools._property_runner   (stdin JSON → stdout JSON)
stdin: {"package", "method", "versions": {이름: src 경로}, "baseline": 버전, "compare": 버전,
        "strategy": spec, "max_examples", "seed"}
- versions의 각 src에서 패키지를 따로 import한다 (sys.modules를 바꿔 가며). 대상 코드에 함수 안 import가 없어야 한다.
- baseline과 compare의 결과가 다른 입력을 찾고, Hypothesis가 최소 사례로 줄인다.
- 모든 버전의 결과를 입력마다 기록해 돌려준다 (판정에서 원본·수정본·참조를 함께 보기 위해).
stdout: {"examples", "mismatches", "minimal": 케이스 | null, "cases": [케이스...]} 또는 {"error"}
  케이스 = {"input": {"args", "kwargs"}, "results": {버전: {"ok", "value"|"exception"}}}
"""

import contextlib
import importlib
import io
import json
import sys

from sr_agent.tools._diff_runner import _call, _encode

MAX_CASES = 20


def load_versions(package: str, method: str, versions: dict[str, str]) -> dict:
    """버전마다 패키지를 새로 import해 함수를 얻는다. 각 함수는 자기 버전의 모듈 전역을 쥐고 있다."""
    module_name, func_name = method.rsplit(".", 1)
    funcs = {}
    for name, src in versions.items():
        for key in [k for k in sys.modules if k == package or k.startswith(package + ".")]:
            del sys.modules[key]
        sys.path.insert(0, src)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                funcs[name] = getattr(importlib.import_module(f"{package}.{module_name}"), func_name)
        finally:
            sys.path.remove(src)
    return funcs


def build_strategy(spec):
    """strategy_spec(JSON) → Hypothesis 전략. 지원 타입은 SPEC_TYPES."""
    from hypothesis import strategies as st

    if not isinstance(spec, dict) or "type" not in spec:
        raise ValueError(f"전략 형식 오류 (type 필요): {spec!r}")
    t = spec["type"]
    if t == "integer":
        return st.integers(spec.get("min"), spec.get("max"))
    if t == "boolean":
        return st.booleans()
    if t == "none":
        return st.none()
    if t == "just":
        return st.just(spec["value"])
    if t == "sampled":
        if not spec.get("values"):
            raise ValueError("sampled에는 values가 필요")
        return st.sampled_from(spec["values"])
    if t == "text":
        return st.text(alphabet=spec.get("alphabet") or st.characters(codec="utf-8"),
                       min_size=spec.get("min_size", 0), max_size=spec.get("max_size", 20))
    if t == "list":
        return st.lists(build_strategy(spec["items"]), min_size=spec.get("min_size", 0),
                        max_size=spec.get("max_size", 5))
    if t == "dict":
        return st.fixed_dictionaries({k: build_strategy(v) for k, v in spec["fields"].items()})
    if t == "one_of":
        return st.one_of(*[build_strategy(s) for s in spec["options"]])
    if t == "date":
        import datetime as dt

        lo = dt.date.fromisoformat(spec.get("min", "2020-01-01"))
        hi = dt.date.fromisoformat(spec.get("max", "2030-12-31"))
        return st.dates(lo, hi).map(lambda d: {"$date": d.isoformat()})
    if t == "datetime":
        import datetime as dt

        lo = dt.datetime.fromisoformat(spec.get("min", "2020-01-01T00:00:00"))
        hi = dt.datetime.fromisoformat(spec.get("max", "2030-12-31T23:59:59"))
        return st.datetimes(lo, hi).map(lambda d: {"$datetime": d.isoformat()})
    raise ValueError(f"지원하지 않는 전략 타입: {t} (지원: {', '.join(SPEC_TYPES)})")


SPEC_TYPES = ("integer", "boolean", "none", "just", "sampled", "text", "list", "dict", "one_of", "date", "datetime")


def input_strategy(spec: dict):
    """{"args": [spec...], "kwargs": {이름: spec}} → {"args": [...], "kwargs": {...}} 전략."""
    from hypothesis import strategies as st

    if not isinstance(spec, dict) or not set(spec) <= {"args", "kwargs"}:
        raise ValueError('strategy_spec은 {"args": [...], "kwargs": {...}} 형식이어야 함')
    args = st.tuples(*[build_strategy(s) for s in spec.get("args", [])]).map(list)
    kwargs = st.fixed_dictionaries({k: build_strategy(v) for k, v in spec.get("kwargs", {}).items()})
    return st.builds(lambda a, k: {"args": a, "kwargs": k}, args, kwargs)


def run(req: dict) -> dict:
    from hypothesis import HealthCheck, Phase, given, seed, settings

    package = req["package"]
    funcs = load_versions(package, req["method"], req["versions"])
    baseline, compare = req["baseline"], req["compare"]
    strategy = input_strategy(req["strategy"])
    cases: list[dict] = []
    stats = {"examples": 0, "mismatches": 0, "under_fix": 0, "regression": 0}
    # baseline이 참조 구현이면 불일치를 분류한다: 원본이 참조와 같았던 입력 = 회귀, 원본도 달랐던 입력 = 미달
    classify = baseline != "original" and "original" in funcs

    def evaluate(raw: dict) -> dict:
        encoded = json.loads(json.dumps(raw))  # 전략 값은 이미 JSON 표기 ($date 등)
        case = {"input": encoded, "results": {n: _call(f, encoded, package) for n, f in funcs.items()}}
        if classify and case["results"][baseline] != case["results"][compare]:
            case["kind"] = "regression" if case["results"]["original"] == case["results"][baseline] else "under_fix"
        return case

    common = {"database": None, "deadline": None, "max_examples": req["max_examples"],
              "suppress_health_check": list(HealthCheck), "derandomize": False}

    @seed(req["seed"])
    @settings(phases=[Phase.explicit, Phase.generate], **common)
    @given(strategy)
    def explore(raw):
        case = evaluate(raw)
        stats["examples"] += 1
        if case["results"][baseline] != case["results"][compare]:
            stats["mismatches"] += 1
            if classify:
                stats[case["kind"]] += 1
            if len(cases) < MAX_CASES:
                cases.append(case)

    explore()
    minimal = None
    if stats["mismatches"]:
        last: dict = {}

        @seed(req["seed"])
        @settings(phases=[Phase.generate, Phase.shrink], **common)
        @given(strategy)
        def shrink(raw):
            case = evaluate(raw)
            if case["results"][baseline] != case["results"][compare]:
                last["case"] = case
                raise AssertionError("mismatch")

        try:
            shrink()
        except AssertionError:
            minimal = last.get("case")
    return {**stats, "minimal": minimal, "cases": cases}


def main() -> None:
    req = json.loads(sys.stdin.read())
    try:
        out = run(req)
    except Exception as e:  # noqa: BLE001 - 잘못된 전략·import 실패 등을 오류 값으로
        out = {"error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(out, ensure_ascii=False, default=lambda v: _encode(v)))


if __name__ == "__main__":
    main()
