"""问卷 docx 导入：把客户回填的 Word 问卷解析成 survey_96 的答案 JSON。

设计原则（与确定性推导三铁律一致）：
1. **只提取，不推断**——文档里没写的值一律不填，不拿默认值凑；
2. **可溯源**——每条答案带 `src`（第几段 / 第几张表），前端能点回去核对；
3. **对不上就报**——题号或题干与模板不符时记入 `mismatch`，不硬塞。

解析对象是「已填写的问卷」（勾选框 ☑/□ + 下划线/冒号填空 + 原生表格），
不是空白模板生成。
"""
from __future__ import annotations

import io
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

_KN = Path(__file__).resolve().parent.parent / "knowledge"

# 勾选符号：☑ 是标准，其余是同义写法（客户可能用 Word 符号或手动输入）
CHECKED = "☑✅✓√■☒"
UNCHECKED = "□☐○◯"
BOXES = CHECKED + UNCHECKED
_NUM = "一二三四五六七八九十"

QNO = re.compile(r"^\s*(\d{1,3})\s*[.．、]\s*(.*)$")
TIME = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{1,2})")
NUM = re.compile(r"(\d+(?:\.\d+)?)")
CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}

# 题干/选项里要为匹配去掉的噪声：必答星号、括号提示、空白
_NOISE = re.compile(r"[\s\u3000*＊　]+")


def _norm(s: str) -> str:
    return _NOISE.sub("", s or "")


_PUNCT = re.compile(r"[？?。\.：:，,、；;！!（）()\[\]【】\-—…·~～\"]+")


def _cmp(s: str) -> str:
    """比对用：去空白 + 去标点。标点差异不算「题干不一致」。"""
    return _PUNCT.sub("", _norm(s))


def _stem(text: str) -> str:
    """题干归一：去掉「（追问）/（补充）」前缀和括号注释再比对。

    模板里写「项目规模（按项目逐行填写，同一公司/部门下有多个项目的分行列出）」，
    原卷写「项目规模　*　按项目逐行填写…」——括号不算差异，不该报 warning。
    """
    s = re.sub(r"^[（(]\s*(追问|补充)\s*[）)]", "", text or "")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    return _norm(s)


def _clean(s: str) -> str:
    return re.sub(r"[\s\u3000]+", " ", (s or "").strip()).strip()


def load_survey() -> dict:
    return json.loads((_KN / "survey_96.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 文档遍历

def _blocks(doc) -> list[dict]:
    """按正文顺序返回段落与表格，表格保持独立块（表格题只能靠位置对齐）。"""
    out: list[dict] = []
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, doc)
            out.append({"k": "p", "t": p.text or ""})
        elif child.tag == qn("w:tbl"):
            tb = Table(child, doc)
            rows = []
            for r in tb.rows:
                cells = []
                for c in r.cells:
                    cells.append(_clean(c.text))
                rows.append(cells)
            out.append({"k": "t", "rows": rows,
                        "head": " / ".join([c for c in (rows[0] if rows else []) if c])})
    return out


def _split_by_no(blocks: list[dict], numbers: set[int]) -> dict[int, list[dict]]:
    """按「N.」题号切块。题号只认模板里存在的号，避免把正文里的「1. xxx」误当题。

    遇到「第 X 部分」「确认签署」「备注」这类结构性标题要截断当前题——
    否则最后一道题的段落池会一路吞到下一部分，把标题当答案。
    """
    STOP = re.compile(r"^\s*(第\s*[一二三四五六七八九十]+\s*部分|确认签署|备注|附表|附录)")
    seg: dict[int, list[dict]] = {}
    cur: int | None = None
    for b in blocks:
        if b["k"] == "p":
            if STOP.match(b["t"] or ""):
                cur = None
                continue
            m = QNO.match(b["t"])
            if m and int(m.group(1)) in numbers:
                cur = int(m.group(1))
                seg.setdefault(cur, []).append({"k": "p", "t": m.group(2)})
                continue
        if cur is not None:
            seg[cur].append(b)
    return seg


def _fill_missing_no(blocks, seg, questions) -> list[dict]:
    """题号缺失的题（docx 里「最多允许挂起几次？1次」没带 52.）按题干兜底定位。"""
    notes = []
    for q in questions:
        no = q["no"]
        if no in seg or not q.get("text"):
            continue
        key = _norm(q["text"])[:12]
        if len(key) < 4:
            continue
        for i, b in enumerate(blocks):
            if b["k"] != "p" or key not in _norm(b["t"]):
                continue
            bucket = [{"k": "p", "t": _norm_line_of(b["t"], key)}]
            j = i + 1
            while j < len(blocks):
                bj = blocks[j]
                if bj["k"] == "p" and QNO.match(bj["t"]) and int(QNO.match(bj["t"]).group(1)) in {
                        x["no"] for x in questions}:
                    break
                bucket.append(bj)
                j += 1
            seg[no] = bucket
            notes.append({"no": no, "why": "原卷该题没有题号，按题干文字定位"})
            break
    return notes


def _norm_line_of(line: str, key: str) -> str:
    """题干兜底时，把「最多允许挂起几次？1次」中的答案部分保留，题干部分保留。"""
    return line


# ---------------------------------------------------------------- 选项解析

def _boxes(text: str) -> list[tuple[bool, str]]:
    """把一行里的「☑ 甲　□ 乙」拆成 [(是否勾选, 标签)]。

    以勾选符号为分隔，标签取到下一个符号或行尾，并去掉尾部的下划线填空。
    """
    out: list[tuple[bool, str]] = []
    idx = [m.start() for m in re.finditer(f"[{BOXES}]", text)]
    for n, i in enumerate(idx):
        end = idx[n + 1] if n + 1 < len(idx) else len(text)
        label = text[i + 1:end]
        label = re.sub(r"[_＿]{2,}.*$", "", label)      # 去掉「：______」
        label = label.strip(" 　:：、,，")
        if label:
            out.append((text[i] in CHECKED, _norm(label)))
    return out


def _hit(boxes: list[tuple[bool, str]], label: str) -> bool | None:
    """标签在候选里是否被勾选；先精确匹配，再前缀匹配，取第一个命中。"""
    want = _norm(label)
    if not want:
        return None
    for checked, got in boxes:
        if got == want:
            return checked
    for checked, got in boxes:
        if got.startswith(want) or want.startswith(got):
            return checked
    return None


def _parse_choice(q: dict, texts: list[str]) -> tuple[object, str]:
    boxes: list[tuple[bool, str]] = []
    for t in texts:
        boxes.extend(_boxes(t))
    if not boxes:
        return None, "本段没有勾选框"

    opts = q.get("options") or []
    picked = [(o["v"], _hit(boxes, o["l"])) for o in opts]
    yes = [v for v, h in picked if h is True]
    seen = any(h is not None for _, h in picked)

    if q["qtype"] == "multi":
        return (yes if yes else None), ("" if seen else "选项文字与模板对不上")
    if not seen:
        return None, "选项文字与模板对不上"
    return (yes[0] if yes else None), ("" if yes else "本行没有勾选项")


# 中文单位词 → survey_96 的 unit 枚举值
UNIT_WORDS = {"分钟": "min", "小时": "hour", "天": "day", "个月": "month", "月": "month",
              "次": "", "人次": "", "人": "", "个": ""}
_UNIT_PAT = "|".join(UNIT_WORDS)
_HINT_PREFIX = re.compile(r"^\s*(提示|参考|例如|示例|请说明|若选|说明|备注)")


def _is_hint(t: str, strict: bool = False) -> bool:
    """strict=True 只认「整段都是提示」——「请说明…，例如：<回答>」不算提示。"""
    s = t or ""
    if strict:
        return bool(re.match(r"^\s*(提示|参考|例如|示例|备注)", s))
    return bool(_HINT_PREFIX.match(s)) or "例如：" in s


def _drop_unchecked(text: str) -> str:
    """删掉未勾选片段「□ 2小时」。不删会把选项里的数字当成客户填的数值。"""
    return re.sub(f"[{UNCHECKED}][^{BOXES}]*", " ", text or "")


def _parse_number(q: dict, texts: list[str]) -> tuple[object, str]:
    units = q.get("unit") or []
    boxes: list[tuple[bool, str]] = []
    for t in texts:
        boxes.extend(_boxes(t))

    unit = ""
    for u in units:                                   # 单位优先看勾选
        if _hit(boxes, u["l"]) is True:
            unit = u["v"]
            break

    kept = [_drop_unchecked(t) for t in texts]        # 未勾选的选项不再参与取数
    body = " ".join(kept)

    val = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(" + _UNIT_PAT + ")", body)
    if m:                                             # 「30 分钟」「1 天」「0.5 天」
        val = float(m.group(1))
        unit = unit or UNIT_WORDS.get(m.group(2), "")
    if val is None:
        m = re.search(r"([一二两三四五六七八九十半])\s*(" + _UNIT_PAT + ")", body)
        if m:                                         # 「一个月」「三天」
            val = float(CN_DIGIT.get(m.group(1), 0))
            unit = unit or UNIT_WORDS.get(m.group(2), "")
    if val is None:
        for t in kept:                                # 纯数字（题干自带单位，如「几天」）
            if re.search(f"[{BOXES}]", t) and not re.search(r"\d", t):
                continue
            m = NUM.search(t)
            if m:
                val = float(m.group(1))
                break

    if val is None:
        # 勾了单位却没写数值：如实返回 None，交给 A3 冲突，不替客户决定是几小时
        if unit:
            return {"v": None, "unit": unit}, "只有单位没有数值"
        return None, "没有可识别的数值"
    v = int(val) if float(val).is_integer() else val
    if not units:
        return v, ""
    return {"v": v, "unit": unit}, ("" if unit else "有数值但没勾单位")


_SAMPLE_ONLY = re.compile(r"^\s*(例如|参考|示例)")


def _text_from(t: str) -> str | None:
    """从一段里取客户填的文本。

    - 整段以「例如 / 参考 / 示例」开头 → 这是问卷给的样例，不是答案，丢掉；
    - 「请说明……，例如：<回答>」这种题干和回答黏在一段的 → 取「例如：」后面的部分；
    - 其余按「：」切成题干 + 回答。
    """
    s = _clean(t)
    if not s or _SAMPLE_ONLY.match(s):
        return None
    m = re.search(r"例如\s*[：:]\s*(.+)$", s)
    if m:
        tail = _clean(re.sub(r"[_＿]{2,}", "", m.group(1)))
        return tail or None
    m = re.search(r"[：:]\s*(.+)$", s)
    if m:
        val = _clean(re.sub(r"[_＿]{2,}", "", m.group(1)))
        if val and not re.fullmatch(r"[*＊\s　]*", val):
            return val
    return None


def _parse_text(q: dict, texts: list[str]) -> tuple[object, str]:
    """文本题：优先题干「：」后的内容，其次题目下方的非提示段落。"""
    for t in texts:
        val = _text_from(t)
        if val:
            return val, ""
    for t in texts[1:]:
        if _is_hint(t, strict=True):
            continue
        s = _clean(t)
        if not s or re.search(f"[{BOXES}]", s):
            continue
        return s, ""
    return None, "没有可识别的文本"


def _parse_window(q: dict, texts: list[str]) -> tuple[object, str]:
    hits = []
    for t in texts:
        for m in TIME.finditer(t):
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                hits.append(f"{h:02d}:{mi:02d}")
    if len(hits) >= 2:
        return {"start": hits[0], "end": hits[1]}, ""
    if hits:
        return None, "只识别到一个时间点"
    return None, "没有时间点"


def _parse_table(q: dict, rows: list[list[str]]) -> tuple[object, str]:
    cols = (q.get("table") or {}).get("cols") or []
    if not rows:
        return None, "题目下没有表格"
    out = []
    for r in rows[1:]:                                  # 第一行是表头
        item = {}
        for i, c in enumerate(cols):
            v = _clean(r[i]) if i < len(r) else ""
            v = v.replace("\u3000", " ").strip(" :：")
            if c == "序号" and re.fullmatch(r"\d+", v or ""):
                continue                                  # 序号是行号，不占答案
            if v:
                item[c] = v
        if any(item.values()):
            out.append(item)
    if not out:
        return None, "表格是空的"
    return out, ""


# ---------------------------------------------------------------- 主流程

HANDLERS = {
    "single": _parse_choice,
    "multi": _parse_choice,
    "number": _parse_number,
    "text": _parse_text,
    "time_range": _parse_window,
}


def parse(data: bytes | str | Path) -> dict:
    """解析回填问卷，返回 {answers, report}。"""
    if isinstance(data, (bytes, bytearray)):
        doc = Document(io.BytesIO(data))
    else:
        doc = Document(str(data))

    survey = load_survey()
    questions = survey["questions"]
    blocks = _blocks(doc)
    numbers = {q["no"] for q in questions}

    seg = _split_by_no(blocks, numbers)
    notes = _fill_missing_no(blocks, seg, questions)

    answers: dict[str, object] = {}
    filled: list[dict] = []
    skipped: list[dict] = []
    mismatch: list[dict] = []
    inferred: list[dict] = []

    for q in questions:
        code, no = q["code"], q["no"]
        bucket = seg.get(no) or []
        texts = [b["t"] for b in bucket if b["k"] == "p" and (b["t"] or "").strip()]
        tables = [b["rows"] for b in bucket if b["k"] == "t"]

        # 题干校验：题号对上了但段落里找不到题干 → 只记 warning，不阻止解析
        # （客户常把「补充说明：」「请填写对应时长：」这类题干改短，值本身仍然可用）
        first = next((t for t in texts if not re.match(r"^\s*(提示|参考|例如)", t)), "")
        joined = _cmp(" ".join(texts))
        stem = _stem(q.get("text") or "")
        if stem and texts and _cmp(stem)[:12] not in joined:
            mismatch.append({"code": code, "no": no,
                             "sim": round(SequenceMatcher(None, _norm(first)[:40], stem).ratio(), 2),
                             "doc": _clean(first)[:60], "tpl": q.get("text", "")[:60]})

        if q["qtype"] == "table":
            val, why = _parse_table(q, tables[0] if tables else [])
        else:
            handler = HANDLERS.get(q["qtype"])
            if handler is None:
                val, why = None, f"暂不支持题型 {q['qtype']}"
            else:
                val, why = handler(q, texts)

        if val is None or (isinstance(val, list) and not val):
            if texts or tables:
                skipped.append({"code": code, "no": no, "why": why or "未填写"})
            continue

        answers[code] = val
        filled.append({"code": code, "no": no, "qtype": q["qtype"],
                       "src": "表格" if q["qtype"] == "table" else "第 %d 题段落" % no})

        # 有数值但单位不在模板选项里（如第 76 题填「0.5 天」而模板只给了分钟/小时）
        if isinstance(val, dict) and val.get("unit"):
            allowed = {u["v"] for u in (q.get("unit") or [])}
            if allowed and val["unit"] not in allowed:
                inferred.append({"code": code, "no": no, "unit": val["unit"]})

    return {
        "ok": True,
        "doc_title": _doc_title(doc, blocks),
        "answers": answers,
        "report": {
            "total": len(questions),
            "filled": len(filled),
            "skipped": skipped,
            "mismatch": mismatch,
            "inferred_unit": inferred,
            "notes": notes,
            "items": filled,
        },
    }


def _doc_title(doc, blocks) -> str:
    for b in blocks[:6]:
        if b["k"] == "p" and 4 <= len(_clean(b["t"])) <= 40:
            return _clean(b["t"])
    return ""


__all__ = ["parse", "load_survey"]
