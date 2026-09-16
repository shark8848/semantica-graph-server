"""规则实体候选抽取：markdown 结构性关键术语 + 「引号词」（默认路径不依赖 LLM、不联网）。

分层（同 `relations.py`、`core_writeback`）：*抽什么*在引擎。本模块只产出**候选术语名**
（按原文出现顺序、按归一化名去重），类型由 `graphSchema.entityTypes` 在 service 侧统一赋值；
正文定位与关系抽取见 `adapters.relations`。

口径（最小实现，全部可解释）：

1. 标记来源（最终按原文位置排序，与标记类型无关）：
   - ``「…」`` / ``“…”``：显式术语标记（原口径，向后兼容）；
   - markdown 标题 ``#…``：小节主题；
   - 粗体 ``**…**``：作者强调的术语；
   - 行内代码 ``` `…` ```：标识符/字段名；
2. 过滤：去首尾空白；长度 2~40 字符；归一化名为空者丢弃；粗体/行内代码**含空白**者视为句子或
   代码片段（不是术语），一并丢弃；
3. 去重：按 `domain.ids.normalize_name` 归一（与实体稳定 ID 同一口径，大小写/全半角差异算同一术语）；
4. 上限 `MAX_CANDIDATES`：按原文位置截断，保证同一文档的抽取结果**确定**、建图规模可控。

关系为什么需要它：`relations._located_entities` 只让**能在正文定位**的实体参与同句共现，
原先只有标题 + 引号词时，普通 markdown 文档只能得到 1 个实体（标题）→ 0 条边（图谱域演示不完整）。
"""

from __future__ import annotations

import re

from ..domain.ids import normalize_name

#: 候选术语长度上限（超出视为正文句子，不作为术语）
MAX_TERM_LEN = 40
#: 候选数量上限（按原文位置截断）
MAX_CANDIDATES = 50
#: 含空白即丢弃的标记：粗体/行内代码里的空白通常是整句强调或代码片段
_NO_SPACE_MARKERS = ("bold", "code")

#: 标记 → 正则（捕获组 1 为术语正文）。正文**不在正则里限长**：限长会让超长标记的定界符
#: 与下一段标记配对（跨对桥接），反而抽出无关术语；超长过滤统一交给 `MAX_TERM_LEN`。
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("quote", re.compile(r"[「“]([^」”\n]+)[」”]")),
    ("heading", re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)),
    ("bold", re.compile(r"\*\*([^*\n]+)\*\*")),
    ("code", re.compile(r"`([^`\n]+)`")),
)


def _acceptable(name: str, marker: str) -> bool:
    if not name or len(name) > MAX_TERM_LEN:
        return False
    if marker in _NO_SPACE_MARKERS and any(ch.isspace() for ch in name):
        return False
    return normalize_name(name) != ""


def candidate_terms(text: str) -> list[dict[str, str]]:
    """文本 → 候选术语 ``[{name, marker}]``（按原文位置排序、按归一化名去重、上限截断）。"""
    found: list[tuple[int, str, str]] = []
    for marker, pattern in _MARKERS:
        for match in pattern.finditer(text or ""):
            name = match.group(1).strip()
            if _acceptable(name, marker):
                found.append((match.start(1), name, marker))
    found.sort(key=lambda item: (item[0], item[1]))

    terms: list[dict[str, str]] = []
    seen: set[str] = set()
    for _, name, marker in found:
        key = normalize_name(name)
        if key in seen:
            continue
        seen.add(key)
        terms.append({"name": name, "marker": marker})
        if len(terms) >= MAX_CANDIDATES:
            break
    return terms


__all__ = ["MAX_CANDIDATES", "MAX_TERM_LEN", "candidate_terms"]
