"""화면(F6) 로직: 진행 단계, KPI, 승인·반려(F5-6), 티켓 목록·등록, 재생 모드(PRD 9장).

Streamlit 없이 테스트할 수 있게 화면 그리기와 분리한다. 화면은 app/streamlit_app.py.

재생용 결과 저장: python -m sr_agent.ui save-replay SR-2041 [SR-2077 ...]  (PYTHONPATH=src)
"""

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from sr_agent import config
from sr_agent.tools.code import function_source, module_rel
from sr_agent.tools.workspace import remove_tree
from sr_agent.workflow.state import Decision, Ticket, TicketState

STAGES = ("분석", "수정", "공격", "판정", "승인")
TYPE_LABELS = {"defect": "결함 수정", "standardize": "코드 표준화", "security": "보안 진단 조치"}
STATUS_LABELS = {"new": "대기", "analyzing": "분석 중", "fixing": "수정 중", "approval_pending": "승인 대기",
                 "human_review": "사람 검토", "approved": "승인됨", "rejected": "반려됨"}
CHECK_LABELS = {
    "repro_original_fails": "재현 테스트가 원본에서 실패", "repro_patched_passes": "재현 테스트가 수정본에서 통과",
    "existing_tests_pass": "기존 테스트 통과", "change_scope": "변경 범위 (대상 함수만)",
    "reference_property": "정답 구현과 출력 동일 (Hypothesis)",
    "behavior_preserved": "원본과 출력 완전 동일 (Hypothesis)",
    "deprecated_zero": "지원 종료 API 0건", "style_zero": "팀 스타일 위반 0건", "security_zero": "보안 위반 0건",
    "attack_neutralized": "공격 입력 무력화", "normal_preserved": "정상 입력 원본과 동일 (Hypothesis)",
    "attack_findings_rechecked": "공격 입력 재실행: 정답(표준화는 원본)과 동일",
    "attack_regressions_preserved": "공격이 찾은 회귀 입력에서 원본과 동일",  # 4일차 재생 데이터용
    "hidden_tests_pass": "숨은 정답 테스트 통과 (E2E)",
}
_TICKET_ID = re.compile(r"^SR-\d{4}$")


# ---------------- 진행 단계 ----------------

def stage_status(state: TicketState) -> list[tuple[str, str]]:
    """단계별 상태: done | current | pending | skipped | failed."""
    t = state.ticket
    status = state.status
    last = state.attempts[-1] if state.attempts else None
    stages: dict[str, str] = dict.fromkeys(STAGES, "pending")
    if t.type != "defect":
        stages["분석"] = "skipped"
    elif state.repro_verified:
        stages["분석"] = "done"
    elif status == "analyzing":
        stages["분석"] = "current"
    elif status == "human_review":
        stages["분석"] = "failed"
    if last:
        stages["수정"] = "done" if last.fix or last.diff else "failed"
        stages["공격"] = "done" if last.attack else ("current" if status == "fixing" else "pending")
        if last.judge:
            stages["판정"] = "done" if last.judge.passed else "failed"
    elif status == "fixing":
        stages["수정"] = "current"
    if status == "approval_pending":
        stages["승인"] = "current"
    elif status == "approved":
        stages["승인"] = "done"
    elif status == "rejected":
        stages["승인"] = "failed"
    return list(stages.items())


# ---------------- KPI (F6-4) ----------------

def _check(state: TicketState, name: str):
    last = state.attempts[-1] if state.attempts else None
    if last and last.judge:
        return next((c for c in last.judge.checks if c.name == name), None)
    return None


def kpis(state: TicketState) -> dict:
    """재시도 횟수, 발견한 미달·회귀 수, 테스트 통과 수."""
    attack = {"under_fix": 0, "regression": 0}
    judged = {"under_fix": 0, "regression": 0}
    for att in state.attempts:
        if att.attack:
            for f in att.attack.verified():
                attack[f.kind] += 1
        if att.judge:
            for c in att.judge.checks:
                if c.passed is False:
                    judged["under_fix"] += c.data.get("under_fix", 0)
                    judged["regression"] += c.data.get("regression", 0)
    tests = _check(state, "existing_tests_pass")
    return {
        "retries": max(0, len(state.attempts) - 1),
        "attack_under_fix": attack["under_fix"], "attack_regression": attack["regression"],
        "judge_under_fix": judged["under_fix"], "judge_regression": judged["regression"],
        "tests_passed": tests.data.get("passed") if tests else None,
    }


# ---------------- 승인·반려 (F5-6) ----------------

def can_approve(state: TicketState) -> bool:
    """판정을 통과해 승인 대기인 티켓만 승인할 수 있다."""
    return state.status == "approval_pending" and state.passed


def decide(state: TicketState, state_path: Path, approve: bool, reason: str = "") -> TicketState:
    """승인하면 마지막 diff를 결과물(approved.diff)로 확정하고, 반려하면 사유를 기록한다."""
    reason = reason.strip()
    if approve and not can_approve(state):
        raise ValueError("판정을 통과한 승인 대기 티켓만 승인할 수 있음")
    if not approve and not reason:
        raise ValueError("반려 사유를 입력해야 함")
    if state.status in ("approved", "rejected"):
        raise ValueError(f"이미 결정된 티켓: {STATUS_LABELS[state.status]}")
    state_path = Path(state_path)
    result_path = None
    if approve:
        result = state_path.parent / "approved.diff"
        result.write_text(state.attempts[-1].diff, encoding="utf-8", newline="\n")
        result_path = str(result)
    state.decision = Decision(status="approved" if approve else "rejected", reason=reason,
                              decided_at=datetime.now().isoformat(timespec="seconds"), result_path=result_path)
    state.status = state.decision.status
    state.save(state_path)
    return state


# ---------------- 티켓 목록·등록 (F6-1) ----------------

def registry_path() -> Path:
    return config.RUNS_DIR / "registered_tickets.json"


def scenario_tickets() -> list[Ticket]:
    items = json.loads((config.DATA_DIR / "scenarios.json").read_text(encoding="utf-8"))
    return [Ticket(**i) for i in items]


def registered_tickets() -> list[Ticket]:
    path = registry_path()
    return [Ticket(**i) for i in json.loads(path.read_text(encoding="utf-8"))] if path.is_file() else []


def all_tickets() -> list[Ticket]:
    return scenario_tickets() + registered_tickets()


def register_ticket(ticket_type: str, title: str, body: str, method: str | None = None) -> Ticket:
    """새 티켓 등록 (입력 가드: 길이 제한, 코드 표준화·보안은 대상 함수 필수)."""
    title, body, method = title.strip(), body.strip(), (method or "").strip() or None
    if ticket_type not in TYPE_LABELS:
        raise ValueError(f"알 수 없는 유형: {ticket_type}")
    if not title or not body:
        raise ValueError("제목과 본문을 입력해야 함")
    if len(title) + len(body) > config.MAX_TICKET_CHARS:
        raise ValueError(f"제목+본문은 최대 {config.MAX_TICKET_CHARS}자")
    if ticket_type != "defect" and not (method and re.match(r"^\w+\.\w+$", method)):
        raise ValueError("코드 표준화·보안 유형은 대상 함수(모듈.함수)가 필요함")
    existing = all_tickets()
    numbers = [int(t.ticket_id[3:]) for t in existing if _TICKET_ID.match(t.ticket_id)]
    ticket = Ticket(ticket_id=f"SR-{max([9000, *numbers]) + 1:04d}", type=ticket_type, title=title, body=body,
                    method=method if ticket_type != "defect" else None)
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [t.model_dump(exclude_none=True) for t in registered_tickets()] + [ticket.model_dump(exclude_none=True)]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return ticket


def register_demo(ticket: Ticket) -> Ticket:
    """시연 케이스 티켓을 등록 목록에 넣는다 (같은 ID가 있으면 바꾼다)."""
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [t.model_dump(exclude_none=True) for t in registered_tickets() if t.ticket_id != ticket.ticket_id]
    rows.append(ticket.model_dump(exclude_none=True))
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return ticket


def live_state_path(ticket_id: str) -> Path:
    return config.RUNS_DIR / ticket_id / config.STATE_FILENAME


def load_state(path: Path) -> TicketState | None:
    return TicketState.load(path)


def history_details(history_ids: list[str]) -> list[dict]:
    """인용된 이력의 티켓·원인·조치 (Chroma에서 ID로 조회, 임베딩 모델 불필요). 색인이 없으면 빈 목록."""
    if not history_ids:
        return []
    try:
        from sr_agent.rag.index import open_client

        found = open_client().get_collection(config.HISTORY_COLLECTION).get(ids=list(dict.fromkeys(history_ids)))
    except Exception:  # noqa: BLE001 - 화면 보조 정보라 없어도 진행
        return []
    return [m for m in found["metadatas"] if m]


def cited_rule_ids(state: TicketState) -> list[str]:
    """분석 근거와 수정 시도에서 인용한 규칙 ID (순서 유지, 중복 제거)."""
    ids = [e.ref for e in state.analysis.evidence if e.kind == "rule"] if state.analysis else []
    ids += [r for a in state.attempts if a.fix for r in a.fix.rule_ids]
    return list(dict.fromkeys(ids))


def cited_history_ids(state: TicketState) -> list[str]:
    ids = list(state.analysis.history_ids) if state.analysis else []
    ids += [h for a in state.attempts if a.fix for h in a.fix.history_ids]
    return [h for h in dict.fromkeys(ids) if h.startswith("HIS-")]


# ---------------- 수정 전/후 코드 ----------------

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def apply_diff(text: str, diff: str, rel: str) -> str | None:
    """unified diff에서 rel 파일 부분을 text에 적용한다. 문맥이 맞지 않으면 None (다른 원본에서 만든 diff)."""
    lines = text.splitlines()
    section, inside = [], False
    for line in diff.splitlines():
        if line.startswith("--- "):
            inside = line[4:].strip() == f"a/{rel}"
            continue
        if inside and not line.startswith("+++ "):
            section.append(line)
    if not section:
        return text
    out, pos, i = [], 0, 0
    while i < len(section):
        m = _HUNK.match(section[i])
        i += 1
        if not m:
            continue
        start, length = int(m.group(1)), int(m.group(2) or 1)
        start = start if length == 0 else start - 1
        if start < pos:
            return None
        out += lines[pos:start]
        pos = start
        while i < len(section) and not section[i].startswith("@@"):
            tag, body = section[i][:1], section[i][1:]
            i += 1
            if tag in (" ", "-"):
                if pos >= len(lines) or lines[pos] != body:
                    return None
                if tag == " ":
                    out.append(body)
                pos += 1
            elif tag == "+":
                out.append(body)
    out += lines[pos:]
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _target_method(state: TicketState, attempt=None) -> str | None:
    if attempt is not None and attempt.fix:
        return attempt.fix.method
    return state.analysis.final_method if state.analysis else state.ticket.method


def _original_text(ticket_id: str, rel: str) -> str:
    """티켓 작업본의 원본 복사본이 있으면 그것, 없으면 target/ 원본 (시연 재생 데이터는 target/에서 실행했다)."""
    for root in (config.RUNS_DIR / ticket_id / config.ORIGINAL_DIRNAME, config.TARGET_ROOT):
        if (root / rel).is_file():
            return (root / rel).read_bytes().decode("utf-8")
    return ""


def current_code(state: TicketState) -> str | None:
    """대상(최종 결함) 함수의 수정 전 코드."""
    first = state.attempts[0] if state.attempts else None
    if first is not None and first.before_code:
        return first.before_code
    method = _target_method(state)
    rel = module_rel(method) if method else None
    return function_source(_original_text(state.ticket.ticket_id, rel), method) if rel else None


def code_versions(state: TicketState, attempt) -> dict | None:
    """수정 시도의 대상 함수 수정 전/후 전체 코드와 바뀐 import. 만들 수 없으면 None (diff만 보여 준다)."""
    method = _target_method(state, attempt)
    rel = module_rel(method) if method else None
    if rel is None:
        return None
    changed = [ln for ln in _file_diff_lines(attempt.diff, rel) if ln[1:].lstrip().startswith(("import ", "from "))]
    imports = {"added": [ln[1:] for ln in changed if ln[0] == "+"],
               "removed": [ln[1:] for ln in changed if ln[0] == "-"]}
    if attempt.before_code and attempt.after_code:
        return {"method": method, "before": attempt.before_code, "after": attempt.after_code, "imports": imports}
    text = _original_text(state.ticket.ticket_id, rel)
    patched = apply_diff(text, attempt.diff, rel) if text else None
    before = function_source(text, method) if text else None
    after = function_source(patched, method) if patched else None
    if before is None or after is None:
        return None
    return {"method": method, "before": before, "after": after, "imports": imports}


def _file_diff_lines(diff: str, rel: str) -> list[str]:
    """diff에서 rel 파일의 추가·삭제 줄 (+/- 기호 포함)."""
    rows, inside = [], False
    for line in diff.splitlines():
        if line.startswith("--- "):
            inside = line[4:].strip() == f"a/{rel}"
        elif inside and line[:1] in "+-" and not line.startswith("+++ "):
            rows.append(line)
    return rows


def changed_line_numbers(before: str, after: str) -> list[int]:
    """수정 후 코드에서 바뀌거나 새로 생긴 줄 번호 (1부터)."""
    import difflib

    a, b = before.splitlines(), after.splitlines()
    changed = []
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            changed += range(j1 + 1, j2 + 1)
    return changed


# ---------------- 재생 모드 (PRD 9장) ----------------

def replay_source(ticket_id: str) -> Path:
    return config.REPLAY_DIR / ticket_id


def replay_working_path(ticket_id: str) -> Path:
    """재생 중 승인·반려를 기록하는 사본 (저장된 원본 재생 데이터는 건드리지 않는다)."""
    return config.RUNS_DIR / "replay" / ticket_id / config.STATE_FILENAME


def replay_tickets() -> list[str]:
    root = config.REPLAY_DIR
    return sorted(p.name for p in root.iterdir() if (p / config.STATE_FILENAME).is_file()) if root.is_dir() else []


def replay_ticket_list() -> list[Ticket]:
    """재생할 수 있는 티켓 (재생 데이터 안의 티켓 정보로. 시연 S1~S3, 그다음 시연 케이스 순)."""
    tickets = [s.ticket for tid in replay_tickets()
               if (s := TicketState.load(replay_source(tid) / config.STATE_FILENAME)) is not None]
    return sorted(tickets, key=lambda t: (t.demo_case is not None, t.demo_case or "", t.ticket_id))


def load_events(state: TicketState) -> list[dict]:
    """티켓 상태에 기록된 실행(run_ids)의 로그를 이어 붙인다 (없는 로그는 건너뜀)."""
    events = []
    for run_id in state.run_ids:
        log = config.RUNS_DIR / f"{run_id}.jsonl"
        if log.is_file():
            events += [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return events


def save_replay(ticket_id: str) -> Path:
    """runs/<ticket_id>의 최종 상태와 그 실행 로그를 data/replay/<ticket_id>/로 저장한다."""
    state = TicketState.load(live_state_path(ticket_id))
    if state is None:
        raise FileNotFoundError(f"실행 결과 없음: {live_state_path(ticket_id)}")
    if state.decision is not None or state.status in ("approved", "rejected"):  # 재생은 결정 전 상태에서 시작
        state.decision, state.status = None, "approval_pending" if state.passed else "human_review"
    if state.ticket.answer_tests:  # 시연 케이스: 재생에는 작업본이 없으니 정답 확인 결과를 미리 남긴다
        from sr_agent.demo import check_answer

        state.answer_check = check_answer(state)
    dest = replay_source(ticket_id)
    if dest.exists():
        remove_tree(dest)
    dest.mkdir(parents=True)
    state.save(dest / config.STATE_FILENAME)
    events = load_events(state)
    (dest / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                                       encoding="utf-8", newline="\n")
    return dest


def load_replay(ticket_id: str, reset: bool = False) -> tuple[TicketState, Path, list[dict]]:
    """재생용 (상태, 결정을 기록할 사본 경로, 실행 이벤트). 사본이 없거나 reset이면 저장본에서 새로 복사."""
    src = replay_source(ticket_id)
    work = replay_working_path(ticket_id)
    if reset or not work.is_file():
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / config.STATE_FILENAME, work)
    events_path = src / "events.jsonl"
    events = ([json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
              if events_path.is_file() else [])
    return TicketState.load(work), work, events


STEP_LABELS = {"analysis": "분석", "fix": "수정", "attack": "공격", "judge": "판정", "workflow": "완료"}
TOOL_LABELS = {
    "predict_fault_location": "결함 위치 예측", "search_history": "유사 이력 검색", "search_rules": "사내 규칙 검색",
    "get_callers": "호출부 조회", "get_coverage": "커버리지 조회", "read_method": "코드 읽기",
    "write_repro_test": "재현 테스트 작성", "run_tests": "테스트 실행", "apply_patch": "패치 적용",
    "run_diff": "차등 실행", "property_attack": "Hypothesis 공격", "attack_memory": "공격 이력 검색",
    "check_deprecated": "지원 종료 API 검사", "check_style": "팀 스타일 검사", "check_security": "보안 검사",
}


def _ts(event: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(event["ts"])
    except (KeyError, TypeError, ValueError):
        return None


def format_duration(seconds: float | None) -> str:
    """초 → '5분 44초' (없으면 '-')."""
    if seconds is None:
        return "-"
    m, s = divmod(round(seconds), 60)
    return f"{m}분 {s}초" if m else f"{s}초"


def format_tokens(tokens: int) -> str:
    """토큰 수 → '52.9만' 같은 짧은 표기."""
    return f"{tokens / 10000:.1f}만" if tokens >= 10000 else f"{tokens:,}"


def progress_label(event: dict) -> str | None:
    """재생 중 상태 줄에 보일 한 줄 (지금 무엇을 하는지만). 보일 필요 없으면 None."""
    stage = STEP_LABELS.get(event.get("step"))
    if stage is None or event.get("kind") not in ("tool", "llm", "judge", "round", "verify"):
        return None
    if event.get("kind") == "tool":
        return f"{stage} · {TOOL_LABELS.get(event.get('tool'), event.get('tool'))}"
    if event.get("kind") == "round":
        label = next((lb for n, lb, _ in config.ATTACK_ANGLES if n == event.get("angle")), event.get("angle"))
        return f"공격 · {label} 라운드 끝"
    return f"{stage} 진행 중"


def run_summary(events: list[dict]) -> dict:
    """실행 로그 → 단계별 요약 (화면의 '처리 과정'). 같은 단계가 이어진 구간을 한 줄로 묶는다.

    반환: {"segments": [{stage, attempt, turns, tools, seconds, result}], "seconds", "tokens"}
    """
    segments: list[dict] = []
    fix_count = 0
    for event in events:
        step = event.get("step")
        if step not in ("analysis", "fix", "attack", "judge") or \
                event.get("kind") not in ("llm", "tool", "verify", "round", "judge"):
            continue  # 판정 뒤 공격 이력 저장(memory) 같은 부수 기록은 구간을 만들지 않는다
        if not segments or segments[-1]["step"] != step:
            if step == "fix":
                fix_count += 1
            attempt = None if step == "analysis" else max(fix_count, 1)
            segments.append({"step": step, "stage": STEP_LABELS[step], "attempt": attempt, "turns": 0, "tools": 0,
                             "start": _ts(event), "end": _ts(event), "result": ""})
        seg = segments[-1]
        seg["end"] = _ts(event) or seg["end"]
        kind = event.get("kind")
        if kind == "llm":
            seg["turns"] += 1
        elif kind == "tool":
            seg["tools"] += 1
            if step == "fix" and event.get("tool") == "apply_patch" and not event.get("error"):
                seg["result"] = "패치 적용"
        elif kind == "verify" and step == "analysis":
            seg["result"] = "재현 테스트가 원본에서 실패함을 확인" if event.get("ok") else \
                f"재현 확인 실패: {event.get('detail', '')}"
        elif kind == "verify" and step == "attack":
            rounds = f"라운드 {len(event['rounds'])}/{event.get('planned_rounds')}회 · " if "rounds" in event else ""
            found = event.get("findings", 0)
            seg["result"] = rounds + (f"발견 {found}건 중 코드로 확인 {event.get('verified', 0)}건" if found
                                      else "발견 없음")
        elif kind == "judge":
            failed = ", ".join(CHECK_LABELS.get(n, n) for n in event.get("failed", []))
            seg["result"] = "통과" if event.get("passed") else f"실패 — {failed}"
    # 구간 시간: 다음 구간 시작까지 (마지막은 자기 끝까지)
    for i, seg in enumerate(segments):
        end = (segments[i + 1]["start"] if i + 1 < len(segments) else None) or seg["end"]
        seg["seconds"] = (end - seg["start"]).total_seconds() if seg["start"] and end else None
    stamps = [t for t in map(_ts, events) if t]
    end_event = next((e for e in reversed(events) if e.get("kind") == "end"), {})
    return {
        "segments": [{k: v for k, v in s.items() if k not in ("start", "end")} for s in segments],
        "seconds": (stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else None,
        "tokens": end_event.get("tokens") if isinstance(end_event.get("tokens"), int) else None,
    }


def event_message(event: dict) -> str | None:
    """실행 로그 한 줄 → 화면에 보일 진행 메시지 (보일 필요 없는 줄은 None)."""
    step = {"analysis": "분석", "fix": "수정", "attack": "공격", "judge": "판정", "workflow": "완료"}.get(
        event.get("step"), event.get("step"))
    kind = event.get("kind")
    if event.get("angle") and kind in ("llm", "tool"):  # 동시에 도는 공격 라운드 구분
        step += "·" + next((lb for n, lb, _ in config.ATTACK_ANGLES if n == event["angle"]), event["angle"])
    if kind == "llm":
        what = event.get("output_summary") or ""
        return f"[{step}] {event.get('turn')}턴 — " + ("결과 정리" if what == "final" else f"도구 호출: {what}")
    if kind == "tool":
        mark = " (오류)" if event.get("error") else ""
        return f"[{step}]   └ {event.get('tool')}{mark}: {str(event.get('input', ''))[:80]}"
    if kind == "verify" and step == "분석":
        return f"[재현 확인] {'성공' if event.get('ok') else '실패'}: {event.get('detail', '')}"
    if kind == "round":
        label = next((lb for n, lb, _ in config.ATTACK_ANGLES if n == event.get("angle")), event.get("angle"))
        tail = f" (오류: {event['error']})" if event.get("error") else ""
        return (f"[공격 {event.get('round')}/{event.get('rounds')} {label}] 발견 {event.get('findings', 0)}건, "
                f"코드로 확인 {event.get('verified', 0)}건{tail}")
    if kind == "verify" and step == "공격":
        rounds = f"라운드 {len(event['rounds'])}/{event.get('planned_rounds')}회, " if "rounds" in event else ""
        return f"[공격] {rounds}발견 {event.get('findings', 0)}건, 코드로 확인 {event.get('verified', 0)}건"
    if kind == "judge":
        failed = ", ".join(CHECK_LABELS.get(n, n) for n in event.get("failed", []))
        return f"[판정] 시도 {event.get('attempt')}: " + ("통과" if event.get("passed") else f"실패 ({failed})")
    if kind == "fallback":
        return f"[{step}] 모델 장애 → {event.get('fallback_model')}로 전환"
    if kind == "end":
        return f"[완료] 상태: {STATUS_LABELS.get(event.get('status'), event.get('status'))}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="재생용 실행 결과 저장 (PRD 9장)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    save = sub.add_parser("save-replay")
    save.add_argument("ticket_ids", nargs="+")
    args = parser.parse_args()
    for ticket_id in args.ticket_ids:
        print(f"저장: {save_replay(ticket_id)}")


if __name__ == "__main__":
    main()
