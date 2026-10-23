"""RedFix 최종 발표 자료 초안 생성.

주의: RedFix_최종발표.pptx는 이 스크립트로 만든 뒤 PowerPoint에서 직접 고치고 슬라이드를 뺐다.
다시 실행하면 수동 수정이 사라지므로, 지금 발표 자료는 pptx 파일을 직접 고친다.

python build_final_deck.py <output.pptx> [preview_dir]

python-pptx·Pillow가 필요하다 (프로젝트 의존성이 아니므로 별도 가상환경에서 실행).
preview_dir을 주면 같은 배치로 슬라이드 미리보기 PNG를 그리고, 글자가 상자를
넘치는 곳을 출력한다 (LibreOffice가 없는 환경의 확인용 근사치).
"""

import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

FONT = "Malgun Gothic"
W, H = 13.333, 7.5
LM = 0.6  # 좌우 여백
CW = W - 2 * LM  # 본문 폭

INK, MUTED, LINE = "1B1F27", "5B6270", "D9DCE1"
RED, RED_T = "D62839", "FBE7E9"
GREEN, GREEN_T = "1F8A70", "E3F3EE"
AMBER, AMBER_T = "9A6312", "FFF4E0"
GRAY_T, WHITE = "F3F4F6", "FFFFFF"
DARK, CARD, CARD2, ON_DARK = "161A21", "242A35", "323A49", "C9CED8"
RED_D, GREEN_D, AMBER_D = "FF5A66", "3DBE95", "F2B544"


# ---------------------------------------------------------------- 기록(미리보기용)

class Rec:
    """슬라이드에 넣은 도형·글상자를 미리보기용으로 기록한다."""

    def __init__(self) -> None:
        self.slides: list[dict] = []

    def new(self, bg: str) -> dict:
        s = {"bg": bg, "items": []}
        self.slides.append(s)
        return s


REC = Rec()


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


def _font(run, size, bold, color, italic=False):
    f = run.font
    f.size, f.bold, f.italic = Pt(size), bold, italic
    f.color.rgb = rgb(color)
    f.name = FONT
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rPr, qn(tag))
        el.set("typeface", FONT)


def R(text: str, **o) -> tuple[str, dict]:
    """글자 조각(run)."""
    return (text, o)


class Slide:
    def __init__(self, prs, bg: str, notes: str):
        self.s = prs.slides.add_slide(prs.slide_layouts[6])
        self.rec = REC.new(bg)
        fill = self.s.background.fill
        fill.solid()
        fill.fore_color.rgb = rgb(bg)
        self.s.notes_slide.notes_text_frame.text = notes.strip()

    # 도형
    def box(self, x, y, w, h, fill, shape="round", line=None, radius=0.12, name=None, rot=90):
        kind = {"round": MSO_SHAPE.ROUNDED_RECTANGLE, "rect": MSO_SHAPE.RECTANGLE,
                "oval": MSO_SHAPE.OVAL, "tri": MSO_SHAPE.ISOSCELES_TRIANGLE}[shape]
        sh = self.s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
        if shape == "round":
            sh.adjustments[0] = min(0.5, radius / min(w, h))
        if shape == "tri":
            sh.rotation = rot
        if fill is None:
            sh.fill.background()
        else:
            sh.fill.solid()
            sh.fill.fore_color.rgb = rgb(fill)
        if line:
            sh.line.color.rgb = rgb(line)
            sh.line.width = Pt(1.25)
        else:
            sh.line.fill.background()
        sh.shadow.inherit = False
        if name:
            sh.name = name
        self.rec["items"].append({"t": "box", "x": x, "y": y, "w": w, "h": h, "fill": fill,
                                  "shape": shape, "line": line, "radius": radius, "rot": rot})
        return sh

    def hline(self, x, y, w, color=LINE):
        self.box(x, y, w, 0.012, color, shape="rect")

    # 글상자: paras = [str | [run, ...] | {"runs": [...], "bullet": bool, "after": pt}]
    def text(self, x, y, w, h, paras, size=16, color=INK, bold=False, align="l",
             anchor="t", spacing=1.0, check=True, name=None):
        tb = self.s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        if name:
            tb.name = name
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE,
                              "b": MSO_ANCHOR.BOTTOM}[anchor]
        if isinstance(paras, str):
            paras = [paras]
        recp = []
        for i, p in enumerate(paras):
            if isinstance(p, str):
                p = {"runs": [R(p)]}
            elif isinstance(p, list):
                p = {"runs": p}
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER,
                              "r": PP_ALIGN.RIGHT}[align]
            para.line_spacing = spacing
            if p.get("after"):
                para.space_after = Pt(p["after"])
            if p.get("bullet"):
                pPr = para._p.get_or_add_pPr()
                pPr.set("marL", str(int(Inches(0.22))))
                pPr.set("indent", str(-int(Inches(0.22))))
                bu = etree.SubElement(pPr, qn("a:buChar"))
                bu.set("char", "•")
            rr = []
            for t, o in p["runs"]:
                run = para.add_run()
                run.text = t
                sz, b, c = o.get("size", size), o.get("bold", bold), o.get("color", color)
                _font(run, sz, b, c, o.get("italic", False))
                rr.append((t, sz, b, c))
            recp.append({"runs": rr, "bullet": p.get("bullet", False),
                         "after": p.get("after", 0)})
        self.rec["items"].append({"t": "text", "x": x, "y": y, "w": w, "h": h, "paras": recp,
                                  "align": align, "anchor": anchor, "spacing": spacing,
                                  "check": check})
        return tb

    def circle(self, x, y, d, label, fill=RED, color=WHITE, size=16):
        self.box(x, y, d, d, fill, shape="oval")
        self.text(x, y, d, d, label, size=size, color=color, bold=True, align="c", anchor="m")

    def arrow(self, x, y, size=0.16, color=LINE, rot=90):
        self.box(x, y, size, size, color, shape="tri", rot=rot)


def header(sl: Slide, eyebrow: str, title: str, dark=False):
    sl.text(LM, 0.45, CW, 0.35, eyebrow, size=14, bold=True, color=RED_D if dark else RED)
    sl.text(LM, 0.82, CW, 0.75, title, size=30, bold=True, color=WHITE if dark else INK)


def footer(sl: Slide, n: int, dark=False):
    c = ON_DARK if dark else MUTED
    sl.text(LM, 7.0, 3, 0.3, "RedFix", size=11, color=c, bold=True)
    sl.text(W - LM - 1, 7.0, 1, 0.3, str(n), size=11, color=c, align="r")


# ---------------------------------------------------------------- 슬라이드

def s_cover(prs, n):
    sl = Slide(prs, DARK, NOTES["cover"])
    sl.text(LM, 1.1, CW, 0.4, "SCSA27 · AI AGENT 프로젝트 최종 발표", size=16, color=ON_DARK)
    sl.text(LM, 1.6, CW, 1.6, [[R("Red", color=RED_D), R("Fix", color=WHITE)]],
            size=80, bold=True)
    sl.text(LM, 3.25, 9.5, 1.1,
            ["운영 개발자가 근거를 보고 믿고 승인할 수 있는",
             "변경 요청(SR) 처리 에이전트"], size=24, color=WHITE)
    steps = ["접수 검사", "분석", "수정", "공격", "판정", "승인"]
    x, y, w, h, gap = LM, 4.95, 1.62, 0.62, 0.42
    for i, st in enumerate(steps):
        last = i == len(steps) - 1
        sl.box(x, y, w, h, GREEN_D if last else CARD, radius=0.31)
        sl.text(x, y, w, h, st, size=16, bold=True, color=DARK if last else WHITE,
                align="c", anchor="m")
        if not last:
            sl.arrow(x + w + (gap - 0.14) / 2, y + (h - 0.14) / 2, 0.14, ON_DARK)
        x += w + gap
    sl.text(LM + 5 * (1.62 + 0.42), 5.68, 1.62, 0.35, "사람이 결정", size=13,
            color=GREEN_D, bold=True, align="c")
    sl.text(LM, 6.75, 6, 0.35, "이종협  ·  2026. 10", size=14, color=ON_DARK)


def s_problem(prs, n):
    sl = Slide(prs, WHITE, NOTES["problem"])
    header(sl, "01 · 문제", "LLM은 금방 고친다. 그런데 그대로 반영할 수 있나?")
    cards = [
        ("근거가 없다", "왜 거기를 고쳤는지 모르면 담당자가 처음부터 다시 봐야 한다", False),
        ("회귀 확인은 사람 몫", "\"테스트 통과했습니다\"라는 LLM의 말만으로는 반영할 수 없다", False),
        ("엉뚱한 요청도 고친다",
         "\"test / 집가고싶다\" 티켓이 코드에 있던 다른 결함을 고치고 검증까지 통과했다", True),
    ]
    w, gap, y, h = (CW - 0.8) / 3, 0.4, 1.95, 3.05
    for i, (head, body, hot) in enumerate(cards):
        x = LM + i * (w + gap)
        sl.box(x, y, w, h, RED_T if hot else GRAY_T, name=f"problem-{i + 1}")
        sl.circle(x + 0.35, y + 0.35, 0.6, str(i + 1), fill=RED if hot else INK)
        sl.text(x + 0.35, y + 1.2, w - 0.7, 0.5, head, size=20, bold=True,
                color=RED if hot else INK)
        sl.text(x + 0.35, y + 1.78, w - 0.7, 1.15, body, size=15, color=INK, spacing=1.1)
    y2 = 5.35
    sl.box(LM, y2, CW, 1.25, GRAY_T)
    sl.text(LM + 0.35, y2 + 0.22, 2.3, 0.5, "1~2시간*", size=22, bold=True)
    sl.text(LM + 2.7, y2 + 0.25, CW - 3.0, 0.45,
            "현업 SR 한 건에서 원인 위치 찾기 · 회귀 확인 · 사내 규칙과 장애 이력 대조에 드는 시간",
            size=15)
    sl.text(LM + 2.7, y2 + 0.75, CW - 3.0, 0.3, "* 기획 단계 가정치", size=11, color=MUTED)
    footer(sl, n)


def s_direction(prs, n):
    sl = Slide(prs, WHITE, NOTES["direction"])
    header(sl, "02 · 해결 방향", "고치는 AI가 아니라, 믿고 판단하게 넘겨주는 AI")
    cx, gap, cw = LM + 1.55, 0.2, 1.6
    y1, h = 1.95, 0.95
    sl.text(LM, y1 + 0.12, 1.4, 0.4, "지금", size=18, bold=True)
    sl.text(LM, y1 + 0.52, 1.4, 0.3, "사람이 전부", size=12, color=MUTED)
    now = [("티켓 읽기", None), ("원인 찾기", "1~2시간*"), ("수정", None), ("회귀 확인", None),
           ("규칙·이력 대조", None), ("배포 요청", None)]
    for i, (t, sub) in enumerate(now):
        x = cx + i * (cw + gap)
        sl.box(x, y1, cw, h, INK)
        paras = [t] if sub is None else [t, [R(sub, size=12, color=ON_DARK, bold=False)]]
        sl.text(x + 0.1, y1, cw - 0.2, h, paras, size=15, bold=True, color=WHITE,
                align="c", anchor="m")
    y2 = 3.25
    sl.text(LM, y2 + 0.12, 1.5, 0.4, "RedFix", size=18, bold=True, color=RED)
    sl.text(LM, y2 + 0.52, 1.5, 0.3, "담당자는 결정만", size=12, color=MUTED)
    auto_w = (cx + 6 * cw + 5 * gap) - cx - 3 * cw - 3 * gap
    x = cx
    sl.box(x, y2, cw, h, INK)
    sl.text(x + 0.1, y2, cw - 0.2, h, "티켓 등록", size=15, bold=True, color=WHITE,
            align="c", anchor="m")
    x += cw + gap
    sl.box(x, y2, auto_w, h, GRAY_T, line=LINE)
    sl.text(x + 0.2, y2, auto_w - 0.4, h,
            [[R("자동 처리 · 평균 5.1분", size=16, bold=True)],
             [R("접수 검사 → 분석 → 수정 → 공격 → 판정", size=13, color=MUTED)]],
            align="c", anchor="m")
    x += auto_w + gap
    for t in ("근거·diff·검증 확인", "승인 / 반려"):
        sl.box(x, y2, cw, h, INK)
        sl.text(x + 0.1, y2, cw - 0.2, h, t, size=15, bold=True, color=WHITE,
                align="c", anchor="m")
        x += cw + gap
    ly = 4.45
    sl.box(cx, ly + 0.06, 0.2, 0.2, INK, shape="rect")
    sl.text(cx + 0.3, ly, 1.0, 0.3, "담당자", size=12, color=MUTED)
    sl.box(cx + 1.3, ly + 0.06, 0.2, 0.2, GRAY_T, shape="rect", line=LINE)
    sl.text(cx + 1.6, ly, 1.0, 0.3, "자동", size=12, color=MUTED)
    sl.text(cx + 2.6, ly, 5, 0.3, "* 기획 단계 가정치", size=11, color=MUTED)
    y3 = 5.05
    sl.box(LM, y3, CW, 1.5, AMBER_T)
    sl.text(LM + 0.35, y3 + 0.22, CW - 0.7, 0.45,
            "막히면 억지로 고치지 않고, 사유를 붙여 사람에게 넘긴다", size=18, bold=True)
    pills = ["보완 요청 · 수정 요청으로 볼 수 없음", "원인 확신 불가", "재시도 3회 초과 → 사람 검토"]
    px = LM + 0.35
    for t in pills:
        pw = 0.5 + 0.2 * len(t)
        sl.box(px, y3 + 0.82, pw, 0.45, WHITE, line=AMBER, radius=0.22)
        sl.text(px, y3 + 0.82, pw, 0.45, t, size=14, bold=True, color=AMBER, align="c",
                anchor="m")
        px += pw + 0.25
    footer(sl, n)


def s_flow(prs, n):
    sl = Slide(prs, WHITE, NOTES["flow"])
    header(sl, "03 · 전체 흐름", "분석 → (수정 → 공격 → 판정) 반복 → 승인")
    y, h = 2.55, 1.15
    gx, gy, gw, gh = 4.65, 2.0, 6.0, 2.05
    sl.box(gx, gy, gw, gh, GRAY_T, line=LINE, name="verify-loop")
    sl.text(gx + 0.2, gy + 0.12, 3.5, 0.3, "검증 루프 · 최대 3회", size=13, bold=True, color=RED)
    nodes = [
        (0.6, 1.55, "LLM + 코드", "접수 검사", "수정 요청인지 확인", "n"),
        (2.5, 1.8, "AGENT", "분석 에이전트", "위치·근거·재현 테스트", "n"),
        (4.85, 1.7, "AGENT", "수정 에이전트", "규칙 반영 패치", "n"),
        (6.8, 1.7, "AGENT", "공격 에이전트", "덜 고친 곳 · 회귀 탐색", "n"),
        (8.75, 1.7, "CODE", "판정 노드", "통과 조건 검사", "judge"),
        (11.0, 1.6, "HUMAN", "사람 승인", "승인 / 반려", "human"),
    ]
    for i, (x, w, tag, name, sub, kind) in enumerate(nodes):
        fill = {"n": WHITE, "judge": RED_T, "human": INK}[kind]
        fg = WHITE if kind == "human" else INK
        tc = {"n": MUTED, "judge": RED, "human": GREEN_D}[kind]
        sl.box(x, y, w, h, fill, line=None if kind == "human" else LINE, name=f"node-{i + 1}")
        sl.text(x + 0.15, y + 0.14, w - 0.3, 0.25, tag, size=10, bold=True, color=tc, align="c")
        sl.text(x + 0.15, y + 0.4, w - 0.3, 0.35, name, size=16, bold=True, color=fg, align="c")
        sl.text(x + 0.1, y + 0.76, w - 0.2, 0.35, sub, size=11, color=ON_DARK if kind == "human" else MUTED,
                align="c")
        if i < len(nodes) - 1:
            nx = nodes[i + 1][0]
            sl.arrow((x + w + nx) / 2 - 0.08, y + h / 2 - 0.08, 0.16,
                     GREEN if i == 4 else MUTED)
    sl.text(10.4, y + 0.12, 0.65, 0.25, "통과", size=10, bold=True, color=GREEN, align="c")
    # 재시도 화살표: 판정 아래 → 수정 아래
    jx, fx, ly = 9.6, 5.7, 4.32
    sl.box(jx - 0.015, y + h, 0.03, ly - y - h, RED, shape="rect")
    sl.box(fx - 0.015, ly, jx - fx + 0.03, 0.03, RED, shape="rect")
    sl.box(fx - 0.015, y + h + 0.14, 0.03, ly - y - h - 0.14, RED, shape="rect")
    sl.arrow(fx - 0.09, y + h, 0.18, RED, rot=0)
    sl.text(fx + 0.2, ly + 0.08, jx - fx - 0.4, 0.3, "판정 실패 → 실패 내용을 붙여 수정부터 다시",
            size=12, bold=True, color=RED, align="c")
    # 멈추는 곳
    stops = [(0.6, 1.55, 1.375, y + h, "보완 요청", "수정 요청이 아님"),
             (2.5, 1.8, 3.4, y + h, "사람 검토", "원인 확신 불가"),
             (9.75, 1.8, 10.3, gy + gh, "사람 검토", "재시도 3회 초과")]
    py = 4.95
    for x, w, lx, top, t, d in stops:
        sl.box(lx - 0.012, top, 0.024, py - top, AMBER, shape="rect")
        sl.box(x, py, w, 0.62, AMBER_T, line=AMBER, radius=0.14)
        sl.text(x, py + 0.05, w, 0.3, t, size=13, bold=True, color=AMBER, align="c")
        sl.text(x, py + 0.33, w, 0.25, d, size=11, color=INK, align="c")
    sl.text(4.65, py + 0.17, 4.9, 0.3, "← 막히면 억지로 고치지 않고 멈춘다 →", size=12, color=AMBER,
            bold=True, align="c")
    sl.box(LM, 5.85, CW, 0.95, GRAY_T)
    sl.text(LM + 0.3, 5.85, CW - 0.6, 0.95,
            [{"runs": [R("판정 노드: ", bold=True, color=RED),
                       R("기존 테스트 1,145개 · 정답 구현과 입력 200개 비교 · 공격 입력 재실행, LLM 호출 없음")],
              "after": 3},
             [R("수정은 티켓별 작업본의 대상 함수·import만, 테스트 수정 불가 · "
                "에이전트별 도구 분석 8 · 수정 7 · 공격 6 · 노드 사이에는 JSON만", color=MUTED)]],
            size=13, anchor="m")
    footer(sl, n)


def s_arch(prs, n):
    sl = Slide(prs, WHITE, NOTES["arch"])
    header(sl, "04 · 단계별 장치", "단계마다 믿을 장치를 넣었다")
    cols = [
        ("접수 검사", "LLM 분류", "인용한 증상이 티켓에 그대로 있는지 코드가 대조", "n"),
        ("분석", "XGBoost·SHAP 후보 + RAG 규칙·이력 검색", "최종 위치와 이유는 에이전트가, 규칙 ID 인용", "n"),
        ("수정", "티켓별 작업본에만 패치", "대상 함수·import만 변경, 테스트 수정 불가", "n"),
        ("공격", "공격 에이전트 + Hypothesis", "덜 고친 곳과 회귀를 찾는 입력을 제안", "n"),
        ("판정", "코드 (LLM 없음)", "테스트 1,145개 · 정답 비교 입력 200개 · 공격 입력 재실행", "judge"),
        ("승인", "담당자", "근거·diff·검증 결과를 보고 승인 / 반려", "human"),
    ]
    gap = 0.3
    w = (CW - 5 * gap) / 6
    y, h = 1.95, 4.55
    for i, (name, what, why, kind) in enumerate(cols):
        x = LM + i * (w + gap)
        fill = {"n": GRAY_T, "judge": RED_T, "human": INK}[kind]
        fg = WHITE if kind == "human" else INK
        sub = ON_DARK if kind == "human" else MUTED
        sl.box(x, y, w, h, fill, name=f"stage-{i + 1}")
        sl.circle(x + 0.2, y + 0.25, 0.5, str(i + 1),
                  fill=GREEN_D if kind == "human" else (RED if kind == "judge" else INK),
                  color=DARK if kind == "human" else WHITE, size=14)
        sl.text(x + 0.2, y + 0.9, w - 0.35, 0.45, name, size=18, bold=True, color=fg)
        sl.text(x + 0.2, y + 1.5, w - 0.35, 0.3, "무엇으로", size=11, color=sub)
        sl.text(x + 0.2, y + 1.8, w - 0.35, 1.05, what, size=14,
                color=RED if kind == "judge" else fg, bold=kind != "n")
        sl.text(x + 0.2, y + 2.9, w - 0.35, 0.3, "왜 믿을 수 있나", size=11, color=sub)
        sl.text(x + 0.2, y + 3.2, w - 0.35, 1.3, why, size=14, color=fg)
        if i < 5:
            sl.arrow(x + w + (gap - 0.14) / 2, y + 0.43, 0.14, MUTED)
    sl.text(LM, 6.62, CW, 0.3,
            "판정 실패 → 수정부터 다시 (최대 3회) → 넘으면 사람 검토로 멈춤",
            size=13, color=MUTED)
    footer(sl, n)


def s_data(prs, n):
    sl = Slide(prs, WHITE, NOTES["data"])
    header(sl, "04 · 데이터", "공개 데이터가 없어, 결함을 직접 심고 티켓을 만들었다")
    steps = [
        ("대상 프로젝트", "268", "함수", "가상 주문·정산 레거시 모듈 20개 · 테스트 1,145개"),
        ("결함 주입", "2종", "방식", "AST 뮤테이션 11종(직접 구현) + LLM 업무 로직 결함"),
        ("테스트로 선별", "1,341", "결함", "테스트가 깨지는 것만 채택 · AST 853 + LLM 488"),
        ("티켓 작성", "2,678", "티켓", "결함마다 운영·QA 말투 2건 · 스택트레이스 15.6%"),
        ("결함 단위 분할", "20", "홀드아웃 결함", "학습 1,057 · 평가 262 · E2E 20"),
    ]
    gap = 0.3
    w = (CW - 4 * gap) / 5
    y, h = 1.95, 2.55
    for i, (t, num, unit, d) in enumerate(steps):
        x = LM + i * (w + gap)
        hot = i == 4
        sl.box(x, y, w, h, RED_T if hot else GRAY_T, name=f"data-{i + 1}")
        sl.text(x + 0.25, y + 0.22, w - 0.5, 0.35, f"{i + 1}  {t}", size=14, bold=True,
                color=RED if hot else MUTED)
        sl.text(x + 0.25, y + 0.55, w - 0.5, 0.65, num, size=34, bold=True,
                color=RED if hot else INK)
        sl.text(x + 0.25, y + 1.17, w - 0.5, 0.3, unit, size=12, color=MUTED)
        sl.text(x + 0.25, y + 1.55, w - 0.5, 0.95, d, size=13)
        if i < 4:
            sl.arrow(x + w + (gap - 0.14) / 2, y + h / 2 - 0.07, 0.14, MUTED)
    rules = [
        ("정답 라벨은 자동", "결함을 직접 심었으니 정답 함수를 사람이 라벨링하지 않아도 정확하다"),
        ("티켓에 원인을 쓰지 않게", "LLM에 결함 diff·코드를 주지 않음. 함수명이 들어가면 재시도, 그래도 남으면 제외"),
        ("평가 결함이 새지 않게", "같은 결함의 티켓은 같은 분할. 홀드아웃은 KB·프롬프트 예시·피처에서 제외"),
    ]
    w2 = (CW - 0.6) / 3
    y2 = 4.75
    for i, (t, d) in enumerate(rules):
        x = LM + i * (w2 + 0.3)
        sl.box(x, y2, w2, 1.55, WHITE, line=LINE)
        sl.text(x + 0.3, y2 + 0.2, w2 - 0.6, 0.35, t, size=16, bold=True)
        sl.text(x + 0.3, y2 + 0.62, w2 - 0.6, 0.85, d, size=13, color=MUTED)
    sl.text(LM, 6.5, CW, 0.3,
            "RAG 지식: 사내 규칙 40건 + 학습 결함으로 만든 장애·조치 이력 1,057건 · 데이터 생성 LLM(gpt-6-luna) 비용 약 $0.44",
            size=12, color=MUTED)
    footer(sl, n)


def s_model(prs, n):
    sl = Slide(prs, WHITE, NOTES["model"])
    header(sl, "05 · 결함 위치 예측 모델", "티켓마다 함수 268개의 순위를 매기도록 학습했다")
    lw = 6.2
    # 문제 정의
    sl.box(LM, 1.95, lw, 1.85, GRAY_T)
    sl.text(LM + 0.3, 2.12, lw - 0.6, 0.35, "문제 정의 · 순위 학습", size=15, bold=True)
    sl.text(LM + 0.3, 2.52, lw - 0.6, 0.6,
            "티켓 × 후보 함수 한 쌍마다 피처를 계산하고, XGBoost Ranker가 점수를 매겨 정렬",
            size=13)
    chips = ["TF-IDF 유사도", "bge-m3 임베딩", "스택트레이스", "길이·복잡도"]
    cx = LM + 0.3
    for t in chips:
        cw = (lw - 0.6 - 3 * 0.12) / 4
        sl.box(cx, 3.22, cw, 0.4, WHITE, line=LINE, radius=0.2)
        sl.text(cx, 3.22, cw, 0.4, t, size=11, align="c", anchor="m")
        cx += cw + 0.12
    # 학습 절차
    sl.text(LM, 4.0, lw, 0.35, "학습 절차 · 평가 분할을 보고 고르지 않게", size=15, bold=True)
    proc = ["학습 분할 안에서 결함 단위 5-fold로 하이퍼파라미터 8조합 비교",
            "학습 분할 전체로 학습",
            "평가 분할 524건으로 한 번만 평가"]
    for i, t in enumerate(proc):
        yy = 4.45 + i * 0.5
        sl.circle(LM, yy, 0.36, str(i + 1), fill=INK, size=12)
        sl.text(LM + 0.5, yy, lw - 0.5, 0.36, t, size=14, anchor="m")
    sl.text(LM, 6.05, lw, 0.6,
            ["후보마다 SHAP 기여 피처 3개를 근거로 붙여 화면에 보여 줌"],
            size=12, color=MUTED)
    # 오른쪽: 결과 차트 + 결정
    rx = LM + lw + 0.4
    rw = CW - lw - 0.4
    cd = CategoryChartData()
    cats = ["TF-IDF", "임베딩 (bge-m3)", "혼합형 XGBoost"]
    cd.categories = cats
    cd.add_series("Top-3", (0.523, 0.790, 0.803))
    gf = sl.s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(rx), Inches(1.9),
                               Inches(rw), Inches(2.6), cd)
    sl.rec["items"].append({"t": "chart", "x": rx, "y": 1.9, "w": rw, "h": 2.6,
                            "vals": [0.523, 0.790, 0.803], "cats": cats})
    ch = gf.chart
    ch.has_legend = False
    ch.has_title = True
    ch.chart_title.text_frame.text = "평가 분할 524건 · Top-3 정확도"
    tr = ch.chart_title.text_frame.paragraphs[0].runs[0]
    tr.font.size, tr.font.bold, tr.font.name = Pt(13), True, FONT
    tr.font.color.rgb = rgb(INK)
    plot = ch.plots[0]
    plot.gap_width = 60
    plot.has_data_labels = True
    dl = plot.data_labels
    dl.number_format, dl.number_format_is_linked = "0.000", False
    dl.position = XL_LABEL_POSITION.OUTSIDE_END
    dl.font.size, dl.font.bold, dl.font.name = Pt(13), True, FONT
    dl.font.color.rgb = rgb(INK)
    ser = plot.series[0]
    for i, col in enumerate([LINE, "9AA1AD", RED]):
        pt = ser.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = rgb(col)
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale = 0, 1
    va.has_major_gridlines = False
    va.visible = False
    ca = ch.category_axis
    ca.tick_labels.font.size, ca.tick_labels.font.name = Pt(12), FONT
    ca.tick_labels.font.color.rgb = rgb(MUTED)
    ca.format.line.fill.background()
    ca.reverse_order = True
    facts = [
        ("1차 과적합 → 대상 2배 확장", "학습 0.941 / 평가 0.776 → 0.825 / 0.803"),
        ("'과거 결함 수' 피처 제외", "처음 보는 함수에서 Top-3 0.263으로 무너짐"),
        ("트레이스 유무 0.938 / 0.779", "그래서 순위는 참고, 최종 위치는 에이전트가"),
    ]
    y = 4.65
    for t, d in facts:
        sl.box(rx, y, rw, 0.62, GRAY_T)
        sl.text(rx + 0.25, y, 3.0, 0.62, t, size=13, bold=True, anchor="m")
        sl.text(rx + 3.25, y, rw - 3.45, 0.62, d, size=12, color=MUTED, anchor="m")
        y += 0.7
    footer(sl, n)


def s_demo(prs, n):
    sl = Slide(prs, DARK, NOTES["demo"])
    header(sl, "시연", "담당자의 세 가지 결정", dark=True)
    sl.text(LM, 1.6, CW, 0.35, "재생 모드(실제 실행 녹화)로 진행 · ②만 실시간 · 약 3분 30초",
            size=14, color=ON_DARK)
    scenes = [
        ("1분 30초", "SR-2041 · 결함 수정", "쿠폰 2장이면 정산 금액이 음수", "승인", GREEN_D,
         ["모델 1위를 안 고른 이유", "공격의 주장을 판정이 기각"]),
        ("40초 · 실시간", "새 티켓 · 접수 검사", "\"정산 관련 확인 부탁드립니다\"", "보완 요청", AMBER_D,
         ["분석 전에 스스로 멈춤", "인용한 증상을 코드가 대조"]),
        ("1분 20초", "케이스 C · 처음 보는 결함", "공통 조회 함수 db.query_one", "반려", RED_D,
         ["판정 통과, 호출부만 감쌈", "숨긴 테스트 19개 실패"]),
    ]
    w, gap, y, h = (CW - 0.8) / 3, 0.4, 2.25, 4.15
    for i, (tm, tag, sit, dec, dc, look) in enumerate(scenes):
        x = LM + i * (w + gap)
        sl.box(x, y, w, h, CARD, name=f"scene-{i + 1}")
        sl.circle(x + 0.35, y + 0.3, 0.55, str(i + 1), fill=RED, size=16)
        sl.text(x + 1.1, y + 0.4, w - 1.45, 0.35, tm, size=12, color=ON_DARK, align="r")
        sl.text(x + 0.35, y + 1.0, w - 0.7, 0.3, tag, size=13, color=ON_DARK)
        sl.text(x + 0.35, y + 1.32, w - 0.7, 0.7, sit, size=17, bold=True, color=WHITE)
        sl.text(x + 0.35, y + 2.12, w - 0.7, 0.3, "담당자의 결정", size=11, color=ON_DARK)
        sl.text(x + 0.35, y + 2.4, w - 0.7, 0.6, dec, size=30, bold=True, color=dc)
        sl.text(x + 0.35, y + 3.2, w - 0.7, 0.85,
                [{"runs": [R(t)], "bullet": True, "after": 4} for t in look],
                size=14, color=WHITE)
    sl.text(LM, 6.55, CW, 0.35,
            "성공 · 멈춤 · 실패를 하나씩: 자동 검증이 어디까지 하고, 어디서 사람이 결정하는지",
            size=14, color=ON_DARK)
    footer(sl, n, dark=True)


def s_changes(prs, n):
    sl = Slide(prs, WHITE, NOTES["changes"])
    header(sl, "06 · 만들면서 바꾼 것", "기준은 하나, \"담당자가 이 결과를 믿을 수 있나\"")
    c = [(LM, 3.9), (LM + 4.1, 4.95), (LM + 9.25, CW - 9.25)]
    for (x, w), t in zip(c, ["부딪힌 것", "결정", "효과"]):
        sl.text(x + 0.3, 1.9, w - 0.3, 0.35, t, size=13, bold=True, color=MUTED)
    rows = [
        ("공격이 찾은 회귀가 판정을 그냥 통과",
         [[R("판정이 공격 입력을 전부 다시 실행")],
          [R("S2: 판정 1 실패 → 재시도 → 판정 2 통과", size=12, color=MUTED)]],
         "누출 2건 → 0건"),
        ("시연 티켓에서 정답 함수가 모델 1위가 아님",
         ["예측 모델은 참고 자료, 최종 위치와 이유는 에이전트가 제시"], "순위 대신 \"왜 여기인가\""),
        ("숫자 카드와 150줄 로그로는 판단이 안 됨",
         ["질문별 탭, 한 줄 요약, 확인 못 한 조건 표시"], "요약 탭에서 바로 승인"),
        ("공격 라운드마다 같은 코드를 다시 읽음 (41회 중 23회)",
         ["코드를 미리 넣고, 재시도 공격은 의심 관점 3개만"],
         [[R("토큰 -39%")], [R("해결 18 → 19건", size=13)]]),
    ]
    y, h, g = 2.3, 0.95, 0.15
    for i, (a, b, e) in enumerate(rows):
        ry = y + i * (h + g)
        sl.box(LM, ry, CW, h, GRAY_T)
        sl.text(c[0][0] + 0.3, ry, c[0][1] - 0.4, h, a, size=15, anchor="m")
        sl.text(c[1][0] + 0.3, ry, c[1][1] - 0.4, h, b, size=15, anchor="m")
        sl.text(c[2][0] + 0.3, ry, c[2][1] - 0.5, h, e, size=15, bold=True, color=GREEN,
                anchor="m")
    footer(sl, n)


def s_kpi(prs, n):
    sl = Slide(prs, WHITE, NOTES["kpi"])
    header(sl, "07 · 기획 목표 대비 결과", "기획서 목표 8개를 모두 넘었다. 두 개는 여유가 작다")
    cols = [("지표", 2.5, "l"), ("목표", 1.7, "l"), ("결과", 1.75, "l"), ("어떻게 쟀나", 4.58, "l"),
            ("평가", 1.6, "c")]
    xs, x = [], LM
    for _, w, _ in cols:
        xs.append(x)
        x += w
    sl.box(LM, 1.9, CW, 0.48, INK, radius=0.08)
    for (t, w, al), x in zip(cols, xs):
        sl.text(x + 0.2, 1.9, w - 0.3, 0.48, t, size=13, bold=True, color=WHITE,
                align=al, anchor="m")
    rows = [
        ("E2E 해결률", "60% 이상", "19 / 20", "처음 보는 결함 20건, 사람 개입 없이 판정 통과", "ok"),
        ("회귀 누출", "1건 이하", "0건", "판정 통과 뒤 숨긴 테스트로 다시 확인", "ok"),
        ("SR당 처리 시간", "2시간 → 15분", "평균 5.1분", "접수부터 승인 요청까지, E2E 20건 평균", "ok"),
        ("공격 탐지율", "80% 이상", "10 / 10", "일부러 덜 고친 패치 10건 (미달 6 · 회귀 4)", "ok"),
        ("근거 인용률", "80% 이상", "100%", "패치 설명에 규칙·이력 ID가 들어간 비율", "ok"),
        ("자동 검증 통과", "3유형", "3 / 3", "S1 결함 수정 · S2 표준화 · S3 보안", "ok"),
        ("결함 위치 Top-3", "0.80 이상", "0.803", "평가 분할 524건 · 신뢰구간 약 ±3.4%p", "edge"),
        ("예측 도구 기여", "해결률 향상", "+5%p", "예측 끔 90% → 켬 95% · 20건 중 1건 차이", "edge"),
    ]
    rh = 0.5
    for i, (k, t, r, how, ev) in enumerate(rows):
        ry = 2.42 + i * rh
        vals = [k, t, r, how]
        for j, ((_, w, al), x, v) in enumerate(zip(cols[:4], xs[:4], vals)):
            sl.text(x + 0.2, ry, w - 0.3, rh, v, size=15 if j == 2 else (12 if j == 3 else 14),
                    bold=j in (0, 2), color=RED if j == 2 else (MUTED if j in (1, 3) else INK),
                    anchor="m")
        ok = ev == "ok"
        px = xs[4] + 0.2
        sl.box(px, ry + 0.09, cols[4][1] - 0.4, rh - 0.18, GREEN_T if ok else AMBER_T, radius=0.16)
        sl.text(px, ry + 0.09, cols[4][1] - 0.4, rh - 0.18, "달성" if ok else "여유 작음",
                size=12, bold=True, color=GREEN if ok else AMBER, align="c", anchor="m")
        if i < len(rows) - 1:
            sl.hline(LM, ry + rh, CW)
    sl.text(LM, 6.55, CW, 0.3,
            "E2E: 학습·이력 KB에 없는 홀드아웃 결함 20건 · 티켓당 평균 약 $0.4 · 못 푼 1건은 시연의 db.query_one",
            size=12, color=MUTED)
    footer(sl, n)


def s_ablation(prs, n):
    sl = Slide(prs, WHITE, NOTES["ablation"])
    header(sl, "08 · 기능별 기여", "기능을 하나씩 끄고 같은 20건을 다시 돌렸다")
    heads = ["지표", "전체 기능", "예측 도구 끔", "공격 단계 끔"]
    rows = [("해결률", "95%", "90%", "95%"), ("회귀 누출", "0건", "0건", "0건"),
            ("평균 수정 시도", "0.95회", "0.90회", "1.15회"),
            ("토큰 평균", "28.7만", "29.8만", "15.7만"), ("비용 합계 (20건)", "$7.76", "$7.50", "$4.09"),
            ("평균 소요", "306초", "296초", "220초")]
    cx = [LM, LM + 2.4, LM + 3.95, LM + 5.5]
    cw = [2.4, 1.55, 1.55, 1.55]
    tw = sum(cw)
    sl.box(LM, 1.95, tw, 0.5, INK, radius=0.08)
    for x, w, t in zip(cx, cw, heads):
        sl.text(x + 0.2, 1.95, w - 0.3, 0.5, t, size=13, bold=True, color=WHITE,
                align="l" if t == "지표" else "r", anchor="m")
    for i, r in enumerate(rows):
        ry = 2.5 + i * 0.5
        for j, (x, w, t) in enumerate(zip(cx, cw, r)):
            sl.text(x + 0.2, ry, w - 0.3, 0.5, t, size=14, bold=j == 1,
                    align="l" if j == 0 else "r", anchor="m")
        sl.hline(LM, ry + 0.5, tw)
    sl.text(LM, 5.65, tw, 0.95,
            [{"runs": [R(t)], "bullet": True, "after": 3} for t in [
                "예측 도구: 20건 중 1건 차이로 작다. 토큰은 티켓당 약 1.1만 절약",
                "공격을 꺼도 누출 0건: 이번 홀드아웃은 단일 함수 결함이라 기존 테스트로 충분했음",
            ]], size=12, color=MUTED)
    x = LM + tw + 0.5
    w = CW - tw - 0.5
    sl.box(x, 1.95, w, 4.6, RED_T)
    sl.text(x + 0.35, 2.2, w - 0.7, 0.35, "공격 탐지 실험", size=15, bold=True, color=RED)
    sl.text(x + 0.35, 2.6, w - 0.7, 1.0, "10 / 10", size=54, bold=True, color=RED)
    sl.text(x + 0.35, 3.65, w - 0.7, 0.4, "미달 6 / 6 · 회귀 4 / 4", size=16, bold=True)
    sl.text(x + 0.35, 4.2, w - 0.7, 2.2,
            [{"runs": [R(t)], "bullet": True, "after": 4} for t in [
                "사람이 일부러 불완전하게 쓴 패치, 기존 테스트로는 안 잡히는 것만",
                "탐지 = 판정이 공격 입력을 다시 실행해 정답과 다른 결과를 확인",
                "공격은 테스트가 못 잡는 덜 고침·회귀를 맡는 단계"]],
            size=13)
    footer(sl, n)


def s_next(prs, n):
    sl = Slide(prs, DARK, NOTES["next"])
    header(sl, "09 · 향후 개선", "지금 확인한 것과 다음 단계", dark=True)
    lx, lw = LM, 5.35
    rx = LM + lw + 0.55
    rw = CW - lw - 0.55
    sl.text(lx + 0.3, 1.75, lw, 0.3, "지금 확인한 것", size=12, bold=True, color=ON_DARK)
    sl.text(rx + 0.3, 1.75, rw, 0.3, "다음 단계", size=12, bold=True, color=ON_DARK)
    rows = [
        ("공통 함수 수정이 약함 (시연 케이스 C)",
         "공통 함수의 호출 관계를 따라 여러 파일 수정 지원"),
        ("정답 없는 실제 티켓은 일부 조건이 미평가",
         "입력 규칙을 자동으로 추론해 원본과 비교하는 범위를 넓힘"),
        ("위치 예측 Top-3 0.803, 예측 도구 기여 +5%p",
         "데이터를 늘리고 호출 관계 피처를 더해 예측 신호 강화"),
        ("평가 표본이 작음 (E2E 20건, 공격 10건)", "평가 세트를 늘려 수치의 흔들림 확인"),
        ("가상 데이터로만 검증", "사내 코드·규칙·이력으로 재학습하고 실제 티켓으로 재검증"),
    ]
    y, h, g = 2.1, 0.62, 0.12
    for i, (a, b) in enumerate(rows):
        ry = y + i * (h + g)
        sl.box(lx, ry, lw, h, CARD)
        sl.text(lx + 0.3, ry, lw - 0.5, h, a, size=14, color=ON_DARK, anchor="m")
        sl.arrow(lx + lw + 0.2, ry + (h - 0.16) / 2, 0.16, RED_D)
        sl.box(rx, ry, rw, h, CARD2)
        sl.text(rx + 0.3, ry, rw - 0.5, h, b, size=14, bold=True, color=WHITE, anchor="m")
    sl.text(LM, 5.95, 9.5, 0.5, "RedFix는 담당자를 대신하지 않는다.", size=24, bold=True,
            color=WHITE)
    sl.text(LM, 6.45, 9.5, 0.4, "근거와 검증 결과로, 담당자가 빠르고 안전하게 결정하게 돕는다.",
            size=16, color=ON_DARK)
    sl.text(W - LM - 2.5, 6.1, 2.5, 0.55, "감사합니다", size=22, bold=True, color=RED_D,
            align="r")
    footer(sl, n, dark=True)


SLIDES = [s_cover, s_problem, s_direction, s_flow, s_data, s_model, s_demo, s_changes,
          s_kpi, s_ablation, s_next]


# ---------------------------------------------------------------- 발표자 노트

FLOW_NOTE = """
[1분] 핵심: 흐름은 고정, 통과 여부는 코드가, 막히는 곳마다 멈춘다, 마지막은 사람

말할 것 (왼쪽 → 오른쪽으로 손으로 따라가며)
- 기획 단계부터 이 순서를 고정해 두고, 프레임워크 없이 Python으로 직접 짠 워크플로 그래프로 돌립니다.
- 접수 검사: 수정 요청인지 LLM이 먼저 봅니다. LLM이 인용한 증상이 티켓에 글자 그대로 있는지는 코드가 확인합니다.
- 분석 에이전트: 결함 위치 예측 모델로 후보를 좁히고, RAG로 사내 규칙 40건과 과거 장애 이력을 찾아 근거를 붙이고, 재현 테스트를 씁니다.
- 가운데 검증 루프: 수정 에이전트가 고치고, 공격 에이전트가 덜 고친 곳과 깨진 곳을 찾는 입력을 만들고, 판정 노드가 통과 여부를 정합니다.
- 판정이 실패하면 실패 내용을 붙여 수정부터 다시, 최대 3번입니다. 통과하면 사람 승인으로 넘어갑니다.

강조 1 (아래 회색 상자 첫 줄): "판정에는 LLM이 없습니다. 기존 테스트 1,145개, 정답 구현과 입력 200개 비교, 공격 에이전트가 낸 입력까지 코드가 직접 다시 실행합니다. LLM이 LLM을 검사하는 구조가 아닙니다."
강조 2 (노란 상자 세 개): "막히는 곳이 세 군데입니다. 수정 요청이 아니면 보완 요청, 원인을 확신 못 하면 사람 검토, 3번 넘게 실패하면 사람 검토. 억지로 고치지 않습니다."

(시간 있으면) 수정은 티켓별 작업본에서 대상 함수와 import만 바꿀 수 있고, 테스트는 고칠 수 없습니다.
넘어가는 말: "분석 단계의 예측 모델은 데이터부터 직접 만들어 학습했습니다."
"""


NOTES = {
    "cover": """
[15초] 핵심: 무엇을 만들었는지 한 문장 + "마지막은 사람"

말할 것
- RedFix를 소개하겠습니다. 운영 시스템의 변경 요청, SR 티켓을 받아 고치는 에이전트입니다.
- 제가 가장 신경 쓴 건 '잘 고치는 것'보다 '담당자가 이 수정을 믿고 승인할 수 있게 만드는 것'이었습니다.
- 그래서 아래 흐름의 마지막 단계는 항상 사람의 승인입니다.

주의: 결과 숫자는 9장(기획 목표 대비 결과)에서 처음 공개합니다.
""",
    "problem": """
[45초] 핵심: LLM이 고친 코드를 현업이 그대로 반영할 수 없는 세 가지 이유

말할 것
- 요즘 LLM에게 고치라고 하면 코드는 금방 나옵니다. 그런데 현업에서는 그걸 그대로 반영할 수 없습니다.
- 첫째, 근거가 없습니다. 왜 거기를 고쳤는지 모르면 담당자가 처음부터 다시 봐야 합니다.
- 둘째, 다른 동작이 깨졌는지는 여전히 사람 몫입니다. "테스트 통과했습니다"라는 말만으로는 반영할 수 없습니다.
- 셋째, 엉뚱한 요청도 고칩니다. 실제로 'test / 집가고싶다'라는 장난 티켓을 넣었더니, 코드에 있던 다른 결함을 고치고 검증까지 통과했습니다.
  → (복선) "이 티켓은 시연에서 다시 보여 드리겠습니다."

주의: 1~2시간은 기획 단계 가정치. 강조하지 않습니다.
넘어가는 말: "그래서 목표를 이렇게 잡았습니다."
""",
    "direction": """
[45초] 핵심: 고치는 AI가 아니라, 믿고 판단하게 넘겨주는 AI

말할 것
- 지금은 티켓 읽기부터 배포 요청까지 사람이 다 합니다 (위 줄, 전부 진한 색).
- RedFix에서는 담당자가 티켓을 등록하면 중간 과정은 평균 5분 안에 자동으로 처리되고, 담당자는 근거와 바뀐 코드, 검증 결과를 보고 승인하거나 반려합니다.
- 막혔을 때가 중요합니다. 요청이 모호하거나, 원인을 확신하지 못하거나, 세 번 고쳐도 검증을 못 넘으면 억지로 고치지 않고 이유를 붙여 사람에게 넘깁니다.

넘어가는 말: "전체 흐름부터 보여 드리겠습니다."
""",
    "flow": FLOW_NOTE,
    "arch": """
[45초] 핵심: 판정은 코드가, 승인은 사람이 한다 (시연 ①·③에서 다시 확인)

여는 말: "방금 흐름의 단계마다, 결과를 믿을 수 있게 하는 장치를 넣었습니다."
말할 것 (왼쪽부터 한 문장씩, 판정·승인에 시간을 씀)
- 접수 검사: 수정 요청인지 LLM이 먼저 봅니다. LLM이 인용한 증상이 티켓에 글자 그대로 있는지는 코드가 확인합니다.
- 분석: 결함 위치 예측 모델과 SHAP으로 후보를 좁히고, RAG로 사내 규칙 40건과 과거 장애 이력을 찾습니다. 최종 위치와 이유는 에이전트가 제시하고 규칙 ID를 인용합니다.
- 수정: 원본은 건드리지 않고 티켓별 작업본에서만, 대상 함수와 import만 바꿀 수 있습니다. 테스트는 고칠 수 없습니다.
- 공격: 공격 에이전트가 덜 고친 곳이나 다른 동작이 깨진 곳을 찾는 입력을 만듭니다.
- 판정: 통과 여부는 LLM이 아니라 코드가 정합니다. 기존 테스트 1,145개, 정답 구현과 입력 200개 비교, 공격 에이전트가 낸 입력도 판정이 직접 다시 실행합니다.
- 승인: 마지막은 담당자입니다.

강조: "LLM이 LLM을 검사하는 구조가 아닙니다. LLM은 제안하고, 맞고 틀림은 코드가 정합니다."
넘어가는 말: "분석 단계의 예측 모델은 데이터부터 직접 만들어 학습했습니다."
""",
    "data": """
[1분] 핵심: 공개 데이터가 없어 직접 만들었고, 정답 라벨은 자동, 평가 결함은 새지 않게

말할 것 (위 흐름 → 아래 원칙)
- 결함 위치 예측 모델을 만들려면 '티켓 문장 → 정답 함수' 쌍이 많이 필요한데, 공개된 한국어 데이터가 없습니다. 그래서 직접 만들었습니다.
- ① 주문·정산 업무의 가상 레거시 프로젝트를 만들었습니다. 모듈 20개, 함수 268개, 테스트 1,145개입니다.
- ② 결함은 두 방식으로 심었습니다. 연산자 하나를 바꾸는 AST 뮤테이션 11종은 mutmut이 Windows에서 돌지 않아 직접 구현했고, 업무 로직을 잘못 이해한 결함은 LLM에게 제안받아 코드로 검증했습니다.
- ③ 결함을 심은 복사본에서 테스트를 돌려, 테스트가 깨지는 것만 1,341개 채택했습니다.
- ④ 결함마다 LLM이 운영 담당자 말투와 QA 말투로 티켓을 2건씩, 총 2,678건 썼습니다.
- ⑤ 결함 단위로 학습·평가·홀드아웃으로 나눴습니다.

아래 원칙 세 가지 (강조)
- 결함을 직접 심었으니 정답 함수를 사람이 라벨링하지 않아도 정확합니다.
- 티켓에 원인이 새지 않게, LLM에게는 결함 코드를 주지 않고 증상만 줬습니다. 함수명이 들어가면 다시 쓰게 했습니다.
- 같은 결함의 티켓은 같은 분할에 두고, 홀드아웃 20개는 지식 베이스·프롬프트 예시·피처 어디에도 쓰지 않았습니다.

(시간 있으면) 처음엔 함수 108개로 만들었는데 결함 220개밖에 안 나와 과적합이 생겨서, 함수를 2배 넘게 늘렸습니다.
""",
    "model": """
[1분] 핵심: 순위 학습 + 누수 없는 학습 절차 + 순위는 참고로만

말할 것
- 티켓 하나가 들어오면 함수 268개에 점수를 매겨 순위를 냅니다. 분류가 아니라 (티켓, 함수) 쌍마다 피처를 계산하는 순위 학습이라, 함수 이름이 아니라 판단 패턴을 배웁니다.
- 피처는 티켓과 함수 소스의 TF-IDF 유사도, bge-m3 임베딩 유사도, 스택트레이스에 그 함수가 나오는지, 함수 길이와 복잡도입니다.
- 학습 절차: 평가 데이터를 보고 모델을 고르면 누수라서, 하이퍼파라미터는 학습 분할 안에서 결함 단위 5-fold로만 골랐고, 평가 분할은 마지막에 한 번만 봤습니다.
- 결과: TF-IDF 0.523, 임베딩 0.790, 혼합형 XGBoost 0.803으로 목표 0.80을 넘었습니다.

오른쪽 결정 세 가지 (하나씩 한 문장)
- 1차에는 학습 0.941, 평가 0.776으로 과적합이라, 앞에서 말씀드린 대로 대상 프로젝트를 늘려 차이를 0.02로 줄였습니다.
- '과거 결함 수' 피처는 넣으면 점수가 오르지만, 처음 보는 함수에서는 0.263으로 무너져서 뺐습니다. 실제 운영에서는 이력 없는 함수가 대부분이기 때문입니다.
- 스택트레이스가 없으면 0.779로 약합니다. 그래서 모델 순위는 참고 자료로 쓰고, 최종 위치는 에이전트가 코드를 읽고 정합니다. 후보마다 SHAP 근거 3개를 붙여 보여 줍니다.

넘어가는 말: "이제 담당자 화면으로 보여 드리겠습니다."
""",
    "demo": """
[3분 30초] 핵심: 담당자의 세 가지 결정 = 승인 / 보완 요청 / 반려
(이 슬라이드를 10초 띄워 세 장면을 예고하고 화면 전환)

여는 말: "세 장면을 보여 드립니다. 믿을 만해서 승인하는 경우, 시스템이 스스로 멈추는 경우, 자동 검증은 통과했는데 담당자가 잡아내는 경우입니다. 실시간 실행은 3~6분 걸려서 실제 실행을 녹화한 재생 모드로 보여 드리고, 접수 검사만 실시간으로 돌리겠습니다."

① SR-2041 · 승인 (1분 30초) — 탭 A(재생)
 1. SR-2041 → 요약 탭: "담당자는 여기서 시작합니다."
 2. ★ 결함 위치 탭 → '모델 1위를 고르지 않은 이유' 펼치기: "앞에서 본 그 모델이 1위로 고른 함수가 아니라, 에이전트가 이유를 붙여 다른 함수를 골랐습니다. ML은 후보를 좁히고, 결정은 근거로 합니다."
 3. 근거 탭 BIZ-CPN-01·02 → 패치 diff '바뀐 줄 표시': "규칙 두 개를 근거로 4줄만 고쳤습니다." (지나가며)
 4. ★ 공격 로그 탭 판정 결과 열 '문제 아님': "공격 에이전트가 덜 고쳤다고 주장해도, 판정이 그 입력을 다시 돌려 정답과 같으면 기각합니다."
 ⚠ S1은 학습 분할에 있는 결함. "흐름을 보여 드리는 티켓"이라고 소개.
 마무리: "담당자의 결정, 승인입니다."

② 접수 검사 · 보완 요청 (40초) — 탭 B(실시간)
 1. 새 티켓 등록: 결함 수정 / 제목 '정산 문의' / 본문 '정산 관련해서 확인 부탁드립니다.' → 등록 → 실행
 2. "이건 지금 바로 돌려 보겠습니다." → 수 초 안에 보완 요청
 3. ★ "아까 말씀드린 '집가고싶다' 같은 티켓이 이제는 여기서 멈춥니다. LLM이 증상을 지어내도, 티켓에 없는 문장이면 코드가 막습니다."
 4. 삭제. 마무리: "담당자의 결정, 보완 요청입니다."

③ 케이스 C · 반려 (1분 20초) — 탭 A(재생) DEMO-C
 1. ★ "학습에도, 과거 이력에도 없는 처음 보는 결함입니다. 판정은 통과했습니다."
 2. ★ 결함 위치 탭: 에이전트가 "db.query_one은 결과가 없으면 빈 dict를 반환"이라고 원인을 적고도, 패치는 호출부 coupon.find_coupon만 감쌌습니다.
 3. 판정 조건 탭 '미평가': "시스템이 스스로 확인 못 했다고 알려 줍니다." (시간 없으면 생략)
 4. ★ 반려 → 사유 입력 → 정답 확인: 숨긴 테스트 19개 실패, "담당자의 반려가 맞았습니다."
 마무리: "자동 검증은 여기까지 해 주고, 나머지는 담당자가 근거를 보고 결정합니다."

줄이는 순서: ①-3 → ③-3. ②와 ③-4는 끝까지.
""",
    "changes": """
[1분] 핵심: 기준은 하나, "담당자가 이 결과를 믿을 수 있나"

말할 것 (행마다 '부딪힌 것 → 결정' 한 문장)
- 판정: 공격이 회귀를 찾아도 판정이 그 입력을 다시 보지 않으면 그냥 통과되는 빈틈이 있었고, 실제로 누출이 2건 났습니다. 공격 입력을 판정이 전부 다시 실행하게 했고 누출은 0건이 됐습니다. S2가 그 사례로, 시간대 회귀로 판정 1이 실패하고 재시도 끝에 통과했습니다.
- 예측 모델: 시연 티켓에서 정답이 1위가 아니었습니다. 담당자에게 필요한 건 순위가 아니라 '왜 여기인가'라서 모델은 참고 자료로 내렸습니다.
- 화면: 숫자 카드와 긴 로그로는 판단이 안 돼서, 질문마다 탭 하나로 바꾸고 자동으로 확인 못 한 조건은 숨기지 않고 보여 줍니다.
- 비용: 같은 코드를 반복해서 읽고 있어서 미리 넣어 주고 재시도 공격을 줄였더니, 토큰이 39% 줄었고 해결률은 오히려 올랐습니다.

강조: 효과 열(초록), 특히 "누출 2건 → 0건".
""",
    "kpi": """
[1분] 핵심: 기획서에서 정한 목표 8개를 모두 넘었다. 다만 두 개는 여유가 작다고 먼저 말한다

말할 것
- 기획 단계에서 정한 목표 8개와 결과입니다.
- 가장 중요한 건 위 세 줄입니다. 학습에도 이력에도 없는 처음 보는 결함 20건 중 19건을 사람 개입 없이 해결했고, 판정을 통과한 패치를 숨긴 테스트로 다시 돌렸을 때 회귀 누출은 0건, SR 한 건 평균 5.1분입니다.
- 공격 탐지 10건 모두, 근거 인용 100%, 시연 3유형 모두 자동 검증을 통과했습니다.
- 아래 두 줄(노란색)은 정직하게 말씀드립니다. 위치 예측 0.803은 목표선 바로 위라 다시 재면 0.80 아래로 나올 수도 있고, 예측 도구 기여 +5%p는 20건 중 1건 차이입니다.
- 비용은 티켓당 평균 40센트 정도이고, 못 푼 1건이 시연에서 보신 db.query_one 결함입니다.

넘어가는 말: "기능별로 얼마나 기여했는지는 하나씩 꺼 보며 확인했습니다."
""",
    "ablation": """
[45초] 핵심: 기능을 끄고 같은 20건을 돌려 기여를 확인. 공격은 테스트가 못 잡는 경우를 맡는다

말할 것
- 같은 홀드아웃 20건을 예측 도구를 끄고, 공격 단계를 끄고 다시 돌렸습니다.
- 예측 도구를 끄면 해결률이 95%에서 90%로 떨어집니다. 1건 차이라 크게 주장하지는 않습니다.
- 공격을 꺼도 이번 20건에서는 누출이 0건이었습니다. 단일 함수 결함이라 기존 테스트로 충분했던 것으로 봅니다. 대신 수정 시도가 늘었습니다.
- 공격 단계가 필요한 경우는 오른쪽입니다. 기존 테스트로는 안 잡히게 일부러 덜 고친 패치 10건을 만들어 공격만 돌렸더니 10건 모두 찾았습니다.

강조: "테스트가 못 잡는 덜 고침과 회귀를 공격이 맡습니다."
""",
    "next": """
[30초] 핵심: 약점을 다음 단계로 + 마무리 한 문장

말할 것
- 지금 확인한 한계와 다음 단계입니다.
- 보신 것처럼 공통 함수나 여러 파일에 걸친 수정은 아직 약해서, 호출 관계를 따라 수정 범위를 넓히는 쪽으로 개선하려 합니다.
- 실제 티켓은 정답이 없어 일부 조건이 미평가로 남습니다. 입력 규칙을 자동으로 추론해 비교 범위를 넓히는 것이 다음 과제입니다.
- 예측 모델과 평가 표본, 실제 사내 데이터 검증도 다음 단계입니다. (나머지 세 줄은 빠르게)

마무리 (천천히)
- "RedFix는 담당자를 대신하지 않습니다. 근거와 검증 결과로, 담당자가 빠르고 안전하게 결정하게 돕습니다. 감사합니다."
""",
}


# ---------------------------------------------------------------- 미리보기

def preview(out_dir: Path) -> list[str]:
    from PIL import Image, ImageDraw, ImageFont

    S = 100  # px/inch
    fonts: dict = {}

    def font(size, bold):
        k = (round(size * S / 72), bold)
        if k not in fonts:
            fonts[k] = ImageFont.truetype(
                "C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf", k[0])
        return fonts[k]

    def hx(h):
        return "#" + h

    def wrap(runs, width, bullet):
        """run 목록을 어절 단위로 줄바꿈. [(line_tokens, line_height)]"""
        toks = []
        for t, sz, b, c in runs:
            for part in t.replace("\n", " \n ").split(" "):
                toks.append((part, sz, b, c))
        lines, cur, cw = [], [], 0.0
        indent = 0.22 * S if bullet else 0
        for part, sz, b, c in toks:
            if part == "\n":
                lines.append(cur)
                cur, cw = [], 0.0
                continue
            f = font(sz, b)
            pw = f.getlength(part + " ")
            if cur and cw + f.getlength(part) > width - indent:
                lines.append(cur)
                cur, cw = [], 0.0
            cur.append((part, sz, b, c, pw))
            cw += pw
        lines.append(cur)
        return lines

    problems = []
    for idx, sd in enumerate(REC.slides, 1):
        img = Image.new("RGB", (int(W * S), int(H * S)), hx(sd["bg"]))
        d = ImageDraw.Draw(img)
        for it in sd["items"]:
            x, y, w, h = (it[k] * S for k in ("x", "y", "w", "h"))
            if it["t"] == "box":
                fill = hx(it["fill"]) if it["fill"] else None
                out = hx(it["line"]) if it["line"] else None
                if it["shape"] == "oval":
                    d.ellipse([x, y, x + w, y + h], fill=fill, outline=out)
                elif it["shape"] == "tri":
                    pts = {90: [(x, y), (x + w, y + h / 2), (x, y + h)],
                           0: [(x, y + h), (x + w / 2, y), (x + w, y + h)]}[it.get("rot", 90)]
                    d.polygon(pts, fill=fill)
                elif it["shape"] == "round":
                    r = min(it["radius"] * S, w / 2, h / 2)
                    d.rounded_rectangle([x, y, x + w, y + h], r, fill=fill, outline=out,
                                        width=2 if out else 1)
                else:
                    d.rectangle([x, y, x + w, y + h], fill=fill)
            elif it["t"] == "chart":
                n = len(it["vals"])
                bh = (h - 60) / n
                for i, (v, c) in enumerate(zip(it["vals"], it["cats"])):
                    by = y + 40 + i * bh + bh * 0.2
                    d.rectangle([x + 170, by, x + 170 + v * (w - 260), by + bh * 0.6], fill="#9AA1AD")
                    d.text((x, by + 5), c, font=font(13, False), fill="#5B6270")
                    d.text((x + 175 + v * (w - 260), by + 5), f"{v:.3f}", font=font(14, True),
                           fill="#1B1F27")
            else:
                blocks, total = [], 0.0
                for p in it["paras"]:
                    lines = wrap(p["runs"], w, p["bullet"])
                    for li, ln in enumerate(lines):
                        mx = max((t[1] for t in ln), default=p["runs"][0][1] if p["runs"] else 12)
                        lh = mx * S / 72 * 1.33 * it["spacing"]
                        blocks.append((ln, lh, p["bullet"] and li == 0, p["bullet"]))
                        total += lh
                    total += p["after"] * S / 72
                if it["check"] and total > h + 2:
                    first = "".join(r[0] for r in it["paras"][0]["runs"])[:30]
                    problems.append(f"slide {idx}: 높이 넘침 {total / S:.2f}in > {h / S:.2f}in  '{first}'")
                for ln, lh, bul, _ in blocks:
                    for part, sz, b, c, pw in ln:
                        if pw - font(sz, b).getlength(" ") > w + 1:
                            problems.append(f"slide {idx}: 폭 넘침 '{part}'")
                cy = y + {"t": 0, "m": (h - total) / 2, "b": h - total}[it["anchor"]]
                for ln, lh, bul, inbul in blocks:
                    lw = sum(t[4] for t in ln)
                    cx = x + {"l": 0, "c": (w - lw) / 2, "r": w - lw}[it["align"]]
                    if bul:
                        d.text((cx, cy), "•", font=font(ln[0][1] if ln else 14, False),
                               fill=hx(ln[0][3]) if ln else "#000")
                    if inbul:
                        cx += 0.22 * S
                    for part, sz, b, c, pw in ln:
                        d.text((cx, cy + lh * 0.1), part, font=font(sz, b), fill=hx(c))
                        cx += pw
                    cy += lh
        img.save(out_dir / f"slide-{idx:02d}.png")
    return problems


def main() -> None:
    out = Path(sys.argv[1])
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)
    for i, fn in enumerate(SLIDES, 1):
        fn(prs, i)
    prs.core_properties.title = "RedFix 최종 발표"
    prs.core_properties.author = "이종협"
    prs.save(out)
    print("saved", out)
    if len(sys.argv) > 2:
        d = Path(sys.argv[2])
        d.mkdir(parents=True, exist_ok=True)
        probs = preview(d)
        print("\n".join(probs) if probs else "넘침 없음")


if __name__ == "__main__":
    main()
