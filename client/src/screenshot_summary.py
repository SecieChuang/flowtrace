"""screenshot_summary.py - 每日截屏时间线 AI 总结。

用法:
    python screenshot_summary.py
    python screenshot_summary.py --date 2026-03-11
    python screenshot_summary.py --dry-run
    python screenshot_summary.py --config path/to/config.json
"""
import argparse
import base64
import html as _html
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests

from webhook_utils import (
    safe_print,
    parse_json_response,
    mask_webhook_url,
    send_webhook,
)
from ai_provider import call_ai

_DIR = Path(__file__).resolve().parent
CLIENT_DIR = _DIR.parent
DEFAULT_CONFIG_PATH = CLIENT_DIR / "config" / "config.json"
SCREENSHOTS_DIR = CLIENT_DIR / "screenshots"
HTML_BLOCK_RE = re.compile(r"```(?:html)?\s*(<!DOCTYPE.*?</html>|<html.*?</html>)\s*```", re.DOTALL | re.IGNORECASE)


def load_config(path: Path) -> Dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def previous_day(now: datetime | None = None) -> str:
    return ((now or datetime.now()) - timedelta(days=1)).strftime("%Y-%m-%d")


def today(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def collect_screenshots(date: str, max_images: int) -> List[tuple[str, Path]]:
    """Return (HH:MM, path) list sorted by time, evenly sampled to at most max_images."""
    day_dir = SCREENSHOTS_DIR / date
    if not day_dir.exists():
        return []
    files: List[tuple[str, Path]] = []
    for f in sorted(day_dir.glob("*.jpg")):
        parts = f.stem.split("-")
        if len(parts) >= 2:
            files.append((f"{parts[0]}:{parts[1]}", f))
    if not files:
        return []
    if len(files) <= max_images:
        return files
    step = len(files) / max_images
    return [files[int(i * step)] for i in range(max_images)]


def image_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def build_batch_messages(batch: List[tuple[str, Path]]) -> List[Dict]:
    """单批截图 → 让 AI 用自然语言描述每张图的内容。"""
    content: List[Dict] = []
    for hhmm, path in batch:
        content.append({"image": f"data:image/jpeg;base64,{image_to_base64(path)}"})
        content.append({"text": f"[{hhmm}]"})
    content.append({"text": (
        "你将看到一组按时间顺序排列的截图。每张截图后面紧跟一个时间戳标记：[HH:MM]。\n"
        "你的任务是为每个时间戳输出且只输出一行日志，格式严格为：\n\n"
        "[HH:MM] 正在……\n\n"
        "硬性规则：\n"
        "1) 每个时间戳最多输出 1 行。禁止同一时间戳出现多行（不要重复[HH:MM]）。\n"
        "2) 只根据截图中可见内容描述在做什么，优先写出：正在使用的应用/网站/窗口标题/明显的任务动作。\n"
        "3) 允许少量推断，但必须保守：不确定时用『可能在…』『看起来在…』，不要编造具体科研结论。\n"
        "4) 不要写分析/评价/总结，不要输出多余空行。\n"
        "5) 不要把多张截图合并成一行；一张截图对应一个时间戳，你就输出对应那一行。\n\n"
        "身份与判定口径（仅用于选用措辞，本阶段不做最终定性）：\n"
        "- 用户是计算物理博士。科研相关通常包括：写/调试科研代码、读论文/科研文档、看结构模型(VESTA/ASE)、跑模拟(DFT/MD/MC)、分析数据/作图、写论文、整理实验记录等。\n"
        "- 明显摸鱼通常包括：刷短视频/社交媒体、娱乐/美女/游戏相关搜索、购物/新闻等无关网页、纯聊天、发呆/离开/无操作。\n"
        "- 非科研开发/工具折腾：配置环境、折腾工具链、搭建与科研不直接相关的AI工作流/个人项目。若无法判断是否直接为科研服务，就用中性描述（如『配置开发环境/浏览API文档/调试工作流』），不要强行写成科研成果。\n\n"
        "现在开始输出日志（仅输出日志行）。"
    )})
    return [{"role": "user", "content": content}]


def dedupe_timestamp_lines(note: str) -> str:
    """Remove duplicate [HH:MM] lines returned by vision model.

    Keeps the first occurrence order, but if a later duplicate line is longer
    (more informative), it replaces the earlier one.
    """
    out: List[str] = []
    idx_by_ts: Dict[str, int] = {}
    for raw in note.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\[(\d{1,2}:\d{2})\]", line)
        if m:
            ts = m.group(1)
            if ts in idx_by_ts:
                i = idx_by_ts[ts]
                if len(line) > len(out[i]):
                    out[i] = line
                continue
            idx_by_ts[ts] = len(out)
        # Collapse exact consecutive duplicates
        if out and line == out[-1]:
            continue
        out.append(line)
    return "\n".join(out)


def _normalize_list(items: Any, max_items: int = 3, max_item_chars: int = 80) -> List[str]:
    """Normalize and de-duplicate list-like model outputs."""
    if not items:
        return []
    if not isinstance(items, list):
        items = [items]
    out: List[str] = []
    seen: set[str] = set()
    for it in items:
        s = str(it).strip()
        if not s:
            continue
        # Strip common bullet prefixes
        s = re.sub(r"^[\s•\-\+]+", "", s).strip()
        if not s:
            continue
        if len(s) > max_item_chars:
            s = s[: max(1, max_item_chars - 1)].rstrip() + "…"
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def normalize_summary_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Post-process model JSON to remove duplicates and keep output stable."""
    if not isinstance(data, dict):
        return {}
    data = dict(data)
    data["highlights"] = _normalize_list(data.get("highlights"), max_items=3, max_item_chars=80)
    data["distractions"] = _normalize_list(data.get("distractions"), max_items=3, max_item_chars=80)
    # Timeline: keep as-is, but ensure it's a list
    tl = data.get("timeline")
    data["timeline"] = tl if isinstance(tl, list) else []
    return data


def build_aggregation_messages(date: str, batch_notes: List[str]) -> List[Dict]:
    """把所有批次的文字描述汇总 → 生成结构化时间线 JSON。"""
    combined = "\n\n".join(
        f"【{i + 1}】\n{note.strip()}" for i, note in enumerate(batch_notes)
    )
    prompt = (
        "你将收到一份按时间顺序的『截图逐条日志』，每行格式为：\n"
        "[HH:MM] 正在…\n\n"
        f"以下是 {date} 当天日志：\n\n{combined}\n\n"
        "你的任务：基于这些日志，生成『当天时间线摘要』JSON。必须只输出一个 JSON 对象，结构如下：\n"
        '{"summary":"一句话概括今天（约100字）","timeline":[{"from":"HH:MM","to":"HH:MM","activity":"做了什么","focus":"高/中/低"}],"highlights":["..."],"distractions":["..."]}\n\n'
        "统一判定口径（前后一致）：\n"
        "- 用户身份：计算物理博士。\n"
        "- 科研/有效工作：与科研目标直接相关（科研代码/模拟/数据分析/论文/实验记录/科研文献等）。\n"
        "- 非科研开发/工具折腾：配置环境、折腾工具链、搭建AI工作流、浏览通用API文档等；只有当日志能明确表明『为科研任务服务』时，才可算有效工作，否则算一般事务/偏低价值投入。\n"
        "- 摸鱼：刷短视频/社交媒体/娱乐、美女等无关搜索、购物新闻等无关网页、纯聊天、发呆/无操作等。\n\n"
        "输出要求：\n"
        "1) summary：约100字，覆盖上午/下午/晚间在做什么 + 是否摸鱼（不要粉饰也不要夸张）。\n"
        "2) timeline：\n"
        "   - 合并连续相似活动，不同活动分开；\n"
        "   - 每段时长不超过 1 小时，超过要拆分（目标 4-8 段）；\n"
        "   - activity 必须短但具体，尽量包含对象（做什么 + 载体/对象），例如：『写Python科研脚本』『读XX主题文献』『配置驱动/依赖』『摸鱼-刷短视频』『摸鱼-QQ聊天』『摸鱼-无关网页浏览』『摸鱼-发呆无操作』。\n"
        "   - 若判定为摸鱼：activity 字符串必须包含“摸鱼”并写清具体行为，不要只写“摸鱼”。\n"
        "3) focus 只填 高/中/低，严格按口径：\n"
        "   - 高：明确科研有效工作且持续投入；\n"
        "   - 中：一般事务/辅助性工作/不确定是否科研相关；\n"
        "   - 低：摸鱼或明显低效无关。\n"
        "4) highlights：必须 exactly 3 条，只能从『科研/有效工作』中挑选；如果当天有效科研产出有限，要如实写但仍输出 3 条，禁止编造。\n"
        "5) distractions：必须 exactly 3 条，来自摸鱼/分心；若摸鱼很少，就写主要 3 个分心点，但不要捏造不存在的行为。\n"
        "6) 一致性约束：distractions 的每一条都必须能在 timeline 的『低专注』段的 activity 中找到对应的具体行为（同义也可以）。如果找不到，就调整 timeline/activity，使其一致。\n"
        "7) 除 JSON 外不要输出任何额外文字。"
    )
    return [{"role": "user", "content": prompt}]

def format_summary_preview(data: Dict[str, Any], date: str) -> str:
    focus_icons = {"高": ">>", "中": "->", "低": ".."}
    lines = [f"# {date} Screenshot Timeline", "", f"  {data.get('summary', '')}"]

    timeline = data.get("timeline") or []
    if timeline:
        lines += ["", "Timeline:"]
        for seg in timeline:
            icon = focus_icons.get(seg.get("focus", ""), "  ")
            lines.append(f"  {seg.get('from', '')}--{seg.get('to', '')}  {icon}  {seg.get('activity', '')}")

    highlights = data.get("highlights") or []
    if highlights:
        lines += ["", "Highlights:"]
        lines.extend(f"  + {h}" for h in highlights)

    distractions = data.get("distractions") or []
    if distractions:
        lines += ["", "Distractions:"]
        lines.extend(f"  - {d}" for d in distractions)

    return "\n".join(lines)


def select_key_screenshots(
    screenshots: List[tuple[str, Path]], data: Dict[str, Any], max_key: int = 0
) -> List[tuple[str, Path]]:
    """Pick one screenshot near each timeline segment start, up to max_key total."""
    timeline = data.get("timeline") or []
    # 默认：每个 timeline 条目配一张图，至少1张
    if max_key <= 0:
        max_key = max(len(timeline), 1)

    def hhmm_to_min(hhmm: str) -> int:
        try:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return 0

    if timeline and screenshots:
        shot_mins = [hhmm_to_min(hhmm) for hhmm, _ in screenshots]
        seen: set = set()
        selected: List[tuple[str, Path]] = []
        for seg in timeline:
            target = hhmm_to_min(seg.get("from", "00:00"))
            idx = min(range(len(shot_mins)), key=lambda i: abs(shot_mins[i] - target))
            if idx not in seen:
                seen.add(idx)
                selected.append(screenshots[idx])
            if len(selected) >= max_key:
                break
        if selected:
            return selected

    # Fallback: even sampling
    if len(screenshots) <= max_key:
        return screenshots
    step = len(screenshots) / max_key
    return [screenshots[int(i * step)] for i in range(max_key)]


def render_newspaper_html(
    date: str,
    data: Dict[str, Any],
    batch_notes: List[str],
    key_shots: List[tuple[str, Path]],
    screenshot_count: int = 0,
) -> str:
    """Render a newspaper-style HTML report using the Gemini-designed template."""
    esc = _html.escape

    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        date_display = f"{dt.year}年{dt.month}月{dt.day}日 {weekdays[dt.weekday()]}"
    except Exception:
        date_display = date

    summary = data.get("summary", "")
    timeline = data.get("timeline") or []
    highlights = data.get("highlights") or []
    distractions = data.get("distractions") or []

    def hhmm_to_min(hhmm: str) -> int:
        try:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return 0

    focus_label = {"高": "高专注", "中": "中专注", "低": "低专注", "摸鱼": "摸鱼"}
    focus_cls   = {"高": "focus-good", "中": "focus-mid", "低": "focus-low"}
    shot_mins   = [hhmm_to_min(hhmm) for hhmm, _ in key_shots]
    shot_used: set = set()

    # ── 解析所有批次日志行，建立 (分钟, 原文) 列表 ─────────────────────────
    log_line_pool: List[tuple[int, str]] = []
    for note in batch_notes:
        for raw in note.strip().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            m = re.match(r'\[(\d{1,2}:\d{2})\]', raw)
            if m:
                log_line_pool.append((hhmm_to_min(m.group(1)), raw))

    # ── 时间线 HTML（左文右图）────────────────────────────────────────────
    timeline_items_html = ""
    for seg in timeline:
        from_t    = esc(seg.get("from", ""))
        to_t      = esc(seg.get("to", ""))
        activity  = esc(seg.get("activity", ""))
        focus     = seg.get("focus", "")
        flabel    = esc(focus_label.get(focus, focus))
        # 摸鱼 activity 使用 too-low 样式
        if "摸鱼" in activity:
            fcls = "focus-too-low"
        else:
            fcls = focus_cls.get(focus, "focus-mid")
        seg_start = hhmm_to_min(seg.get("from", "00:00"))
        seg_end   = hhmm_to_min(seg.get("to",   "00:00"))

        seg_lines = [line for mins, line in log_line_pool if seg_start <= mins <= seg_end + 15]
        detail_html = ""
        if seg_lines:
            def _tag(mm: re.Match) -> str:
                return f'<span class="time-tag">[{esc(mm.group(1))}]</span>'
            items = "".join(
                "<p>" + re.sub(r'\[(\d{1,2}:\d{2})\]', _tag, esc(line)) + "</p>"
                for line in seg_lines
            )
            detail_html = f'<div class="activity-detail">{items}</div>'

        img_html = ""
        candidates = [i for i in range(len(shot_mins)) if i not in shot_used]
        if candidates:
            best = min(candidates, key=lambda i: abs(shot_mins[i] - seg_start))
            if abs(shot_mins[best] - seg_start) <= 90:
                shot_used.add(best)
                hhmm, path = key_shots[best]
                b64 = image_to_base64(path)
                img_html = f'<img src="data:image/jpeg;base64,{b64}" alt="{esc(hhmm)}" class="timeline-img">'

        timeline_items_html += (
            f'<div class="timeline-item {fcls}">'
            f'<div class="img-col">{img_html}</div>'
            f'<div class="time-col">{from_t}<br>|<br>{to_t}</div>'
            f'<div class="content-col">'
            f'<div class="activity-header">'
            f'<h4 class="activity-title">{activity}</h4>'
            f'<span class="focus-badge">专注度：{flabel}</span>'
            f'</div>{detail_html}'
            f'</div></div>'
        )

    # ── 逐时活动记录已内联到时间线，此处留空 ─────────────────────────────
    hourly_log_html = ""

    hl_items = "".join(f"<li>{esc(h)}</li>" for h in highlights)
    ds_items = "".join(f"<li>{esc(d)}</li>" for d in distractions)
    total        = screenshot_count or len(key_shots)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Gemini 设计的报纸模板 ──────────────────────────────────────────────
    template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{newspaper_name}} - {{date}}</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600;700&family=Noto+Sans+SC:wght@300;400;500&display=swap');
        :root {
            --bg-color: #f4f1ea;
            --text-color: #1a1a1a;
            --border-color: #2c2c2c;
            --font-serif: 'Noto Serif SC', 'Georgia', 'Times New Roman', serif;
            --font-sans: 'Noto Sans SC', 'Helvetica Neue', 'Arial', sans-serif;
        }
        body {
            margin: 0; padding: 20px;
            background-color: #c8c2b8;
            color: var(--text-color);
            font-family: var(--font-serif);
            line-height: 1.6;
            display: flex; justify-content: center;
        }
        .newspaper-container {
            background-color: var(--bg-color);
            max-width: 1240px; width: 100%;
            padding: 40px;
            box-shadow: 0 0 20px rgba(0,0,0,0.3);
            box-sizing: border-box;
        }
        .masthead {
            text-align: center;
            border-bottom: 6px double var(--border-color);
            padding-bottom: 20px; margin-bottom: 30px;
        }
        .masthead h1 {
            font-size: 4.5rem; margin: 0;
            font-family: var(--font-serif);
            font-weight: 900; letter-spacing: 2px;
            line-height: 1.1; text-transform: uppercase;
        }
        .masthead-info {
            display: flex; justify-content: space-between; align-items: center;
            border-top: 1px solid var(--border-color);
            border-bottom: 1px solid var(--border-color);
            padding: 5px 10px; margin-top: 15px;
            font-family: var(--font-sans); font-size: 0.9rem;
            font-weight: bold; text-transform: uppercase;
        }
        .lead-story {
            margin-bottom: 30px; padding-bottom: 20px;
            border-bottom: 2px solid var(--border-color);
        }
        .lead-story h2 {
            text-align: center; font-size: 1.8rem;
            margin: 0 0 15px 0; text-transform: uppercase;
        }
        .lead-story p {
            font-size: 1.2rem; text-align: justify;
            margin: 0; font-weight: bold;
        }
        .lead-story p::first-letter {
            font-size: 3.5rem; float: left;
            margin-top: -0.1em; margin-right: 0.15em;
            line-height: 1; color: var(--text-color);
        }
        .top-boxes {
            display: flex; gap: 24px; margin-bottom: 30px;
        }
        .top-boxes .sidebar-box { flex: 1; margin-bottom: 0; }
        .top-boxes .sidebar-box ul { display: flex; flex-wrap: wrap; gap: 6px 24px; padding-left: 20px; }
        .top-boxes .sidebar-box li { margin-bottom: 0; }
        .main-content { margin-bottom: 20px; }
        .section-header {
            font-size: 1.4rem;
            border-top: 2px solid var(--border-color);
            border-bottom: 1px solid var(--border-color);
            padding: 5px 0; margin-top: 0; margin-bottom: 20px;
            text-align: center; text-transform: uppercase;
            background-color: rgba(0,0,0,0.03);
        }
        .timeline { margin-bottom: 40px; }
        .timeline-item {
            display: flex; align-items: flex-start;
            margin-bottom: 28px;
            border-bottom: 1px dashed #ccc; padding-bottom: 20px;
        }
        .timeline-item:last-child { border-bottom: none; }
        .img-col {
            flex: 0 0 300px;
        }
        .time-col {
            flex: 0 0 90px;
            font-family: var(--font-sans); font-weight: bold; font-size: 0.9rem;
            border-left: 2px solid var(--border-color);
            border-right: 2px solid var(--border-color);
            padding: 4px 14px; margin: 0 18px;
            text-align: center;
        }
        .content-col { flex: 1; min-width: 0; }
        .activity-header {
            display: flex; justify-content: space-between; align-items: baseline;
            margin-bottom: 8px; flex-wrap: wrap; gap: 4px;
        }
        .activity-title { font-size: 1.2rem; font-weight: bold; margin: 0; }
        .focus-badge {
            font-family: var(--font-sans); font-size: 0.75rem;
            padding: 2px 8px; border: 1px solid var(--border-color);
            border-radius: 2px; background: #fff; font-weight: bold;
            white-space: nowrap; flex-shrink: 0;
        }
        .focus-good  .focus-badge { color: #4CB963; border-color: #4CB963; }
        .focus-mid   .focus-badge { color: #c8860a; border-color: #c8860a; background: #FFF8DC; }
        .focus-low   .focus-badge { color: #FF6978; border-color: #FF6978; }
        .focus-good  .time-col { border-color: #4CB963; color: #4CB963; }
        .focus-mid   .time-col { border-color: #c8860a; color: #c8860a; }
        .focus-low   .time-col { border-color: #FF6978; color: #FF6978; }
        .activity-detail {
            margin-top: 10px; font-family: var(--font-sans);
            font-size: 1.05rem; line-height: 1.75; color: #333;
        }
        .activity-detail p { margin: 0 0 6px 0; }
        .timeline-img {
            width: 100%; height: 210px;
            object-fit: cover; display: block;
            border: 3px solid #fff;
            box-shadow: 0 0 0 1px var(--border-color);
            filter: grayscale(15%);
        }
        .hourly-log { display: none; }
        .dense-text {
            column-count: 2; column-gap: 20px;
            column-rule: 1px solid #ccc;
            text-align: justify; font-size: 0.95rem;
            font-family: var(--font-sans); line-height: 1.7;
        }
        .dense-text p { margin-top: 0; margin-bottom: 1em; text-indent: 2em; }
        .time-tag {
            font-weight: 700; font-family: var(--font-sans);
            color: #3f2a1c; background: #efe5d6;
            border: 1px solid #b0957a;
            padding: 1px 7px; margin-right: 6px; border-radius: 3px;
            font-size: 0.95em;
        }
        .sidebar-box {
            border: 2px solid var(--border-color); padding: 15px;
            margin-bottom: 30px; background-color: #fdfcf9;
            box-shadow: 3px 3px 0 var(--border-color);
        }
        .box-title {
            text-align: center; font-size: 1.2rem;
            margin-top: 0; margin-bottom: 15px;
            padding-bottom: 5px; border-bottom: 1px solid var(--border-color);
        }
        .sidebar-box ul { list-style-type: square; padding-left: 20px; margin: 0; }
        .sidebar-box li { margin-bottom: 12px; font-size: 1.05rem; }
        .distractions-box { background-color: #f9f0f0; }
        .gallery-section {
            border-top: 4px double var(--border-color);
            padding-top: 20px; margin-top: 20px; clear: both;
        }
        .gallery-grid {
            display: flex; flex-wrap: wrap; gap: 15px; justify-content: space-between;
        }
        .gallery-item {
            flex: 1 1 calc(25% - 15px); min-width: 150px;
            margin: 0; display: flex; flex-direction: column;
        }
        .gallery-item img {
            width: 100%; height: 120px; object-fit: cover;
            border: 1px solid var(--border-color); padding: 3px; background: #fff;
        }
        .gallery-item figcaption {
            text-align: center; font-family: var(--font-sans);
            font-size: 0.8rem; margin-top: 5px; font-style: italic;
        }
        .footer {
            border-top: 1px solid var(--border-color); margin-top: 40px;
            padding-top: 15px; display: flex; justify-content: space-between;
            font-family: var(--font-sans); font-size: 0.85rem; color: #555;
        }
        @media (max-width: 768px) {
            .main-content { grid-template-columns: 1fr; }
            .dense-text { column-count: 1; }
            .gallery-item { flex: 1 1 calc(50% - 15px); }
            .masthead h1 { font-size: 3rem; }
        }
        /* Lightbox */
        .zoomable { cursor: zoom-in; transition: opacity 0.15s; }
        .zoomable:hover { opacity: 0.88; }
        #lightbox {
            display: none; position: fixed; inset: 0;
            background: rgba(0,0,0,0.82); z-index: 9999;
            align-items: center; justify-content: center;
            cursor: zoom-out;
        }
        #lightbox.open { display: flex; }
        #lightbox img {
            max-width: 92vw; max-height: 92vh;
            object-fit: contain;
            box-shadow: 0 0 40px rgba(0,0,0,0.8);
            border: 3px solid #fff;
            animation: lb-in 0.18s ease;
        }
        #lightbox-caption {
            position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
            color: #ddd; font-family: monospace; font-size: 0.9rem;
            background: rgba(0,0,0,0.5); padding: 4px 12px; border-radius: 4px;
        }
        @keyframes lb-in { from { transform: scale(0.88); opacity: 0; } to { transform: scale(1); opacity: 1; } }
    </style>
</head>
<body>
<div id="lightbox"><img id="lightbox-img" src="" alt=""><div id="lightbox-caption"></div></div>
<div class="newspaper-container">

    <header class="masthead">
        <h1>{{newspaper_name}}</h1>
        <div class="masthead-info">
            <span class="date">{{date}}</span>
            <span class="edition">版次：{{edition}}</span>
        </div>
    </header>

    <section class="lead-story">
        <h2>今日导语</h2>
        <p>{{daily_summary}}</p>
    </section>

    <div class="top-boxes">
        <section class="sidebar-box highlights-box">
            <h3 class="box-title">🏆 今日亮点</h3>
            <ul>{{highlights_items}}</ul>
        </section>
        <section class="sidebar-box distractions-box">
            <h3 class="box-title">⚠ 干扰因素</h3>
            <ul>{{distractions_items}}</ul>
        </section>
    </div>

    <main class="main-content">
        <section class="timeline">
            <h3 class="section-header">核心工作追踪</h3>
            {{timeline_items_html}}
        </section>
    </main>

    <footer class="footer">
        <div>今日有效截图总数：<strong>{{total_screenshots}}</strong></div>
        <div>文档生成时间：{{generation_time}}</div>
    </footer>

</div>
<script>
(function() {
    var lb = document.getElementById('lightbox');
    var lbImg = document.getElementById('lightbox-img');
    var lbCap = document.getElementById('lightbox-caption');
    function open(src, alt) {
        lbImg.src = src;
        lbCap.textContent = alt || '';
        lb.classList.add('open');
    }
    function close() {
        lb.classList.remove('open');
        lbImg.src = '';
    }
    document.querySelectorAll('img').forEach(function(img) {
        img.classList.add('zoomable');
        img.addEventListener('click', function(e) {
            e.stopPropagation();
            open(this.src, this.alt);
        });
    });
    lb.addEventListener('click', close);
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') close();
    });
})();
</script>
</body>
</html>"""

    return (
        template
        .replace("{{newspaper_name}}", "Flowtrace Daily")
        .replace("{{date}}",           esc(date_display))
        .replace("{{edition}}",        "Daily Edition")
        .replace("{{daily_summary}}",  esc(summary))
        .replace("{{timeline_items_html}}",     timeline_items_html)
        .replace("{{hourly_log_paragraphs}}",   hourly_log_html)
        .replace("{{highlights_items}}",        hl_items)
        .replace("{{distractions_items}}",      ds_items)
        .replace("{{total_screenshots}}",       str(total))
        .replace("{{generation_time}}",         esc(generated_at))
    )


def build_feishu_summary_card(payload: Dict[str, Any]) -> Dict[str, Any]:
    focus_icons = {"高": "🟢", "中": "🟡", "低": "🔴", "摸鱼": "🔴"}
    timeline = payload.get("timeline") or []
    timeline_md = "\n".join(
        f"• {s.get('from','')}–{s.get('to','')} {focus_icons.get(s.get('focus',''), '')} {s.get('activity','')}"
        for s in timeline
    )
    highlights_md = "\n".join(f"• {h}" for h in (payload.get("highlights") or []))
    distractions_md = "\n".join(f"• {d}" for d in (payload.get("distractions") or []))

    elements: List[Dict] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"📋 **今日概括**\n{payload.get('summary', '')}"}},
        {"tag": "hr"},
    ]
    if timeline_md:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"⏱️ **时间线**\n{timeline_md}"}})
        elements.append({"tag": "hr"})
    if highlights_md:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"✅ **亮点**\n{highlights_md}"}})
    if distractions_md:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"⚠️ **分心**\n{distractions_md}"}})
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"📸 截屏 {payload.get('screenshot_count', 0)} 张 | {payload['date']}"}],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {
                "template": "indigo",
                "title": {"tag": "plain_text", "content": f"{payload['date']} 时间线"},
            },
            "elements": elements,
        },
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily screenshot timeline AI summary")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--date", default="", help="Date to summarize (YYYY-MM-DD), default: today")
    parser.add_argument("--dry-run", action="store_true", help="List screenshots without calling AI")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    cfg = load_config(Path(args.config))
    date = args.date or today()
    max_images = int(cfg.get("screenshot_summary_max_images", 20))
    if max_images <= 0:
        raise RuntimeError("screenshot_summary_max_images must be greater than 0")

    screenshots = collect_screenshots(date, max_images)
    safe_print(f"SCREENSHOT_SUMMARY_DATE|date={date}")
    safe_print(f"SCREENSHOT_SUMMARY_IMAGES|count={len(screenshots)}")

    if args.dry_run:
        for hhmm, path in screenshots:
            safe_print(f"  [{hhmm}] {path.name}")
        return 0

    if not screenshots:
        raise RuntimeError(f"No screenshots found for {date} in {SCREENSHOTS_DIR / date}")

    # Get config values
    timeout_seconds = int(cfg.get("ai_timeout_seconds", 120))
    batch_size = int(cfg.get("screenshot_summary_batch_size", 6))
    if batch_size <= 0:
        raise RuntimeError("screenshot_summary_batch_size must be greater than 0")

    # First stage: batch image description using vision model via call_ai (with fallback)
    batches = [screenshots[i:i + batch_size] for i in range(0, len(screenshots), batch_size)]
    safe_print(f"SCREENSHOT_SUMMARY_BATCHES|count={len(batches)}|batch_size={batch_size}")
    batch_notes: List[str] = []
    for i, batch in enumerate(batches):
        safe_print(f"SCREENSHOT_SUMMARY_BATCH|batch={i + 1}/{len(batches)}|images={len(batch)}")
        # Use call_ai with fallback support - model from provider config
        note = call_ai(cfg, build_batch_messages(batch), timeout_seconds, is_vision=True)
        note = dedupe_timestamp_lines(note)
        batch_notes.append(note)

    # Second stage: text aggregation using text model via call_ai (with fallback)
    safe_print(f"SCREENSHOT_SUMMARY_AGGREGATING")
    response_text = call_ai(cfg, build_aggregation_messages(date, batch_notes), timeout_seconds, is_vision=False)
    data = normalize_summary_data(parse_json_response(response_text))

    safe_print()
    safe_print(format_summary_preview(data, date))

    # 第三阶段：Python 模板渲染报纸 HTML
    safe_print("SCREENSHOT_SUMMARY_HTML|phase=3|renderer=python")
    key_shots = select_key_screenshots(screenshots, data)
    html_content = render_newspaper_html(date, data, batch_notes, key_shots, len(screenshots))
    summaries_dir = CLIENT_DIR / "runtime" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)

    # 保存 HTML
    summary_file = summaries_dir / f"screenshot-summary-{date}.html"
    summary_file.write_text(html_content, encoding="utf-8")
    safe_print(f"SCREENSHOT_SUMMARY_SAVED|path={summary_file}")

    # 保存 JSON（供周报读取）
    summary_json = {
        "date": date,
        "summary": data.get("summary", ""),
        "timeline": data.get("timeline") or [],
        "highlights": data.get("highlights") or [],
        "distractions": data.get("distractions") or [],
        "screenshot_count": len(screenshots),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    json_file = summaries_dir / f"screenshot-summary-{date}.json"
    json_file.write_text(json.dumps(summary_json, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_print(f"SCREENSHOT_SUMMARY_JSON_SAVED|path={json_file}")

    _ss_webhook_url = str(cfg.get("screenshot_summary_webhook_url", "")).strip()
    if _ss_webhook_url:
        webhook_enabled = bool(cfg.get("screenshot_summary_webhook_enabled", True))
        webhook_url = _ss_webhook_url
        webhook_secret = str(cfg.get("screenshot_summary_webhook_secret", cfg.get("webhook_secret", ""))).strip()
    else:
        webhook_enabled = bool(cfg.get("webhook_enabled", False))
        webhook_url = str(cfg.get("webhook_url", "")).strip()
        webhook_secret = str(cfg.get("webhook_secret", "")).strip()
    if webhook_enabled:
        if not webhook_url:
            raise RuntimeError("Missing config field: webhook_url")
        webhook_timeout = int(cfg.get("webhook_timeout_seconds", 15))
        webhook_retry = int(cfg.get("webhook_retry_count", 2))
        webhook_delay = float(cfg.get("webhook_retry_delay_seconds", 2.0))

        payload = {
            "type": "screenshot_summary",
            "date": date,
            "summary": data.get("summary", ""),
            "timeline": data.get("timeline") or [],
            "highlights": data.get("highlights") or [],
            "distractions": data.get("distractions") or [],
            "screenshot_count": len(screenshots),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        send_webhook(
            webhook_url, payload, webhook_timeout, webhook_secret,
            webhook_retry, webhook_delay,
            feishu_card_builder=build_feishu_summary_card,
        )
        safe_print(f"WEBHOOK_SENT|url={mask_webhook_url(webhook_url)}|status=success")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        safe_print(f"ERROR: {exc}")
        raise SystemExit(1)
