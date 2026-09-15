"""Flowtrace · 数据核查交互式 CLI

用法（本地开发，包模式）：
    python -m server.manage_cli                    # 默认最近 N 天
    python -m server.manage_cli --days 7
    python -m server.manage_cli --from 2026-04-01 --to 2026-04-30

用法（Docker 容器，平铺模式）：
    python manage_cli.py --days 7
"""
import argparse
import importlib
from datetime import datetime, timedelta


def _from_server(attr):
    """Resolve an attribute from server.py (flat module or package)."""
    try:
        mod = importlib.import_module("server.server")
    except ImportError:
        mod = importlib.import_module("server")
    return getattr(mod, attr)


def _from_detection(attr):
    """Resolve an attribute from detection.py (flat module or package)."""
    try:
        mod = importlib.import_module("server.detection")
    except ImportError:
        mod = importlib.import_module("detection")
    return getattr(mod, attr)


_ACTION_LABEL = {"clock_in": "上", "clock_out": "下"}


def _format_card(card, include_id=False):
    """Format a real checkin card; missing-checkin issues have no card."""
    if not card:
        return None
    date = card.get("date", "")
    time = card.get("time", "")
    action = card.get("action", "")
    action_label = _ACTION_LABEL.get(action, action or "?")
    suffix = f"#{card.get('id')}" if include_id and card.get("id") is not None else ""
    return f"{date} {time} {action_label}{suffix}".strip()


def _format_issue_anchor(issue, include_id=False):
    """Return a stable display anchor for both card and missing-card issues."""
    primary_text = _format_card(issue.get("primary"), include_id=include_id)
    if primary_text:
        return primary_text

    inferred_dt = issue.get("inferred_dt")
    if inferred_dt is not None:
        if hasattr(inferred_dt, "strftime"):
            inferred_text = inferred_dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            inferred_text = str(inferred_dt)
        return f"{inferred_text}（待补卡）"
    return "（无对应打卡）"


def _format_gap(sec):
    if sec < 60:
        return f"{sec} 秒"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分" if m else f"{h} 小时"
    return f"{m}分{s}秒" if s else f"{m} 分钟"


# ── 渲染 ─────────────────────────────────────────────

def render_issue(idx, total, issue, ctx, actions):
    bar = "═" * 70
    print(f"\n{bar}")
    print(f"[{idx}/{total}]  {issue['type_label']}  ·  {issue['anchor_date']}")

    p = issue.get("primary")
    if p and issue.get("secondary"):
        s = issue["secondary"]
        if p["date"] != s["date"]:
            p_str = f"{p['date']} {p['time']} {_ACTION_LABEL[p['action']]}"
            s_str = f"{s['date']} {s['time']} {_ACTION_LABEL[s['action']]}"
        else:
            p_str = f"{p['time']} {_ACTION_LABEL[p['action']]}"
            s_str = f"{s['time']} {_ACTION_LABEL[s['action']]}"
        print(f"       {p_str}, {s_str}  (间隔 {_format_gap(issue['gap_seconds'])})")
    elif p:
        p_str = f"{p['time']} {_ACTION_LABEL[p['action']]}"
        print(f"       {p_str}  (无配对)")
    else:
        print(f"       {_format_issue_anchor(issue)}  (缺失打卡)")

    print()
    print(f"       当日打卡 ({len(ctx['day_checkins'])} 条):")
    for c in ctx["day_checkins"]:
        marker = "  ←" if c["highlight"] else ""
        print(f"         {c['time']} {_ACTION_LABEL[c['action']]}{marker}")

    if ctx["day_activity"]:
        print()
        print(f"       参考活动段:")
        for a in ctx["day_activity"]:
            end = a["end_time"] or "—"
            print(f"         {a['start_time']}–{end}")

    print()
    print("怎么处理？")
    for i, a in enumerate(actions, 1):
        tag = "  ← 推荐" if a.get("recommended") else ""
        print(f"  [{i}] {a['label']}{tag}")
    print(f"  [s] 跳过")
    print(f"  [b] 返回上一条")
    print(f"  [q] 退出")


# ── 输入 ─────────────────────────────────────────────

def _prompt_choice_field(field):
    opts = field["options"]
    while True:
        prompt_str = "  选择 " + field["name"] + " ["
        prompt_str += ", ".join(f"{i + 1}={o['label']}" for i, o in enumerate(opts))
        prompt_str += "]: "
        raw = input(prompt_str).strip()
        try:
            i = int(raw) - 1
            if 0 <= i < len(opts):
                return opts[i]["value"]
        except ValueError:
            pass
        print(f"  ⚠ 输入无效，请重新输入")


def _prompt_time_field(field):
    _validate_time = _from_server("_validate_time")
    for _ in range(3):
        raw = input(f"  新时间 (HH:MM:SS): ").strip()
        v = _validate_time(raw)
        if v:
            return v
        print(f"  ⚠ 时间格式无效")
    return None


def _prompt_date_field(field):
    _validate_date = _from_server("_validate_date")
    default = field.get("default", "")
    for _ in range(3):
        suffix = f" [{default}]" if default else ""
        raw = input(f"  日期 (YYYY-MM-DD){suffix}: ").strip()
        if not raw and default:
            raw = default
        v = _validate_date(raw)
        if v:
            return v
        print(f"  ⚠ 日期格式无效")
    return None


def _collect_field_values(fields):
    values = {}
    for f in fields:
        t = f["type"]
        if t == "choice":
            v = _prompt_choice_field(f)
        elif t == "time":
            v = _prompt_time_field(f)
        elif t == "date":
            v = _prompt_date_field(f)
        else:
            v = input(f"  {f['name']}: ").strip()
        if v is None:
            return None
        values[f["name"]] = v
    return values


def prompt_action(actions):
    """Return one of:
       ('execute', op, params, label)
       ('skip',)
       ('back',)
       ('quit',)
    """
    while True:
        raw = input("> ").strip().lower()
        if raw == "q":
            return ("quit",)
        if raw == "s":
            return ("skip",)
        if raw == "b":
            return ("back",)
        try:
            i = int(raw) - 1
            if not (0 <= i < len(actions)):
                raise ValueError()
        except ValueError:
            print(f"  ⚠ 请输入 1-{len(actions)} / s / b / q")
            continue
        action = actions[i]
        if "params" in action:
            return ("execute", action["op"], action["params"], action["label"])
        prompted = _collect_field_values(action["interactive_fields"])
        if prompted is None:
            print("  ✗ 取消")
            continue
        if "target_id" in prompted:
            prompted["id"] = prompted.pop("target_id")
        params = {**action.get("fixed_params", {}), **prompted}
        return ("execute", action["op"], params, action["label"])


# ── 汇总 ─────────────────────────────────────────────

def render_summary(pending_actions):
    """Show summary of all chosen actions before batch execution."""
    bar = "═" * 70
    print(f"\n{bar}")
    print("待执行操作汇总：\n")
    for i, entry in enumerate(pending_actions, 1):
        if entry[0] == "execute":
            _, _, _, label = entry
            print(f"  [{i}] {label}")
        else:
            print(f"  [{i}] 跳过")


# ── 数据库写入助手（测试可直接调用）─────────────────

def render_issue_list(prepared):
    """Print detected issues without entering the repair flow."""
    if not prepared:
        print("No manage issues detected.")
        return

    print(f"Detected {len(prepared)} manage issue(s):")
    for i, item in enumerate(prepared, 1):
        issue = item["issue"]
        secondary = issue.get("secondary")
        primary_text = _format_issue_anchor(issue, include_id=True)
        secondary_text = ""
        if secondary:
            secondary_text = f" -> {_format_card(secondary, include_id=True)}"
        recommended = next((a for a in item["actions"] if a.get("recommended")), None)
        recommended_text = recommended["label"] if recommended else "-"
        print(
            f"  [{i}] {issue['id']} | {issue['type_label']} | {issue['anchor_date']} | "
            f"{primary_text}{secondary_text} | recommended: {recommended_text}"
        )


def delete_checkin(checkin_id: int) -> None:
    app = _from_server("app"); get_db = _from_server("get_db"); _invalidate_day_cache = _from_server("_invalidate_day_cache")
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT date FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
        if not row:
            raise LookupError(f"checkin id {checkin_id} not found")
        db.execute("DELETE FROM checkins WHERE id = ?", (checkin_id,))
        db.commit()
        _invalidate_day_cache(row["date"])


def patch_checkin(checkin_id: int, new_time: str) -> None:
    app = _from_server("app"); get_db = _from_server("get_db")
    _invalidate_day_cache = _from_server("_invalidate_day_cache"); _validate_time = _from_server("_validate_time")
    v = _validate_time(new_time)
    if not v:
        raise ValueError(f"invalid time: {new_time}")
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT date FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
        if not row:
            raise LookupError(f"checkin id {checkin_id} not found")
        db.execute("UPDATE checkins SET time = ? WHERE id = ?", (v, checkin_id))
        db.commit()
        _invalidate_day_cache(row["date"])


def flip_checkin_action(checkin_id: int, new_action: str) -> None:
    app = _from_server("app"); get_db = _from_server("get_db")
    _invalidate_day_cache = _from_server("_invalidate_day_cache")
    if new_action not in ("clock_in", "clock_out"):
        raise ValueError(f"invalid action: {new_action}")
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT date FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
        if not row:
            raise LookupError(f"checkin id {checkin_id} not found")
        db.execute("UPDATE checkins SET action = ? WHERE id = ?", (new_action, checkin_id))
        db.commit()
        _invalidate_day_cache(row["date"])


def add_checkin(date: str, time: str, action: str) -> int:
    app = _from_server("app"); get_db = _from_server("get_db")
    _invalidate_day_cache = _from_server("_invalidate_day_cache")
    _validate_date = _from_server("_validate_date"); _validate_time = _from_server("_validate_time")
    d = _validate_date(date)
    t = _validate_time(time)
    if not d or not t:
        raise ValueError(f"invalid date/time: {date} {time}")
    if action not in ("clock_in", "clock_out"):
        raise ValueError(f"invalid action: {action}")
    with app.app_context():
        db = get_db()
        cur = db.execute("INSERT INTO checkins (action, date, time) VALUES (?, ?, ?)", (action, d, t))
        db.commit()
        _invalidate_day_cache(d)
        return cur.lastrowid


def execute_op(op, params):
    if op == "delete":
        delete_checkin(params["id"])
    elif op == "edit_time":
        patch_checkin(params["id"], params["time"])
    elif op == "add":
        add_checkin(params["date"], params["time"], params["action"])
    elif op == "flip_action":
        flip_checkin_action(params["id"], params["action"])
    elif op == "noop":
        pass
    else:
        raise ValueError(f"unknown op: {op}")


# ── 主循环 ───────────────────────────────────────────

def _resolve_range(args, default_days):
    if args.from_date and args.to_date:
        return args.from_date, args.to_date
    days = args.days or default_days
    today = datetime.now()
    return (today - timedelta(days=days - 1)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def prepare_issues(date_from, date_to):
    app = _from_server("app"); get_db = _from_server("get_db")
    CHECKIN_DEDUPE_SECONDS = _from_server("CHECKIN_DEDUPE_SECONDS")
    detect_issues = _from_detection("detect_issues"); build_context = _from_detection("build_context")
    build_actions_for_slot_mismatch = _from_detection("build_actions_for_slot_mismatch")
    build_actions_for_missing_checkin = _from_detection("build_actions_for_missing_checkin")
    build_actions_for_isolated = _from_detection("build_actions_for_isolated")

    prepared = []
    with app.app_context():
        db = get_db()
        # 查询区间 ±1 天缓冲
        date_from_obj = datetime.strptime(date_from, "%Y-%m-%d")
        date_to_obj = datetime.strptime(date_to, "%Y-%m-%d")
        buffer_from = (date_from_obj - timedelta(days=1)).strftime("%Y-%m-%d")
        buffer_to = (date_to_obj + timedelta(days=1)).strftime("%Y-%m-%d")

        rows = db.execute(
            "SELECT id, action, date, time FROM checkins WHERE date BETWEEN ? AND ? ORDER BY date, time",
            (buffer_from, buffer_to),
        ).fetchall()
        issues = detect_issues(
            [dict(r) for r in rows],
            dedupe_seconds=CHECKIN_DEDUPE_SECONDS,
            db=db,
        )

        for issue in issues:
            # 检查 primary 是否仍存在（可能已被修复）
            if issue.get("primary") and issue["primary"].get("id"):
                primary_alive = db.execute(
                    "SELECT 1 FROM checkins WHERE id = ?", (issue["primary"]["id"],)
                ).fetchone()
                if not primary_alive:
                    continue

            ctx = build_context(issue, db)

            if issue["category"] == "slot_mismatch":
                actions = build_actions_for_slot_mismatch(issue, db)
            elif issue["category"] == "missing_checkin":
                actions = build_actions_for_missing_checkin(issue, db)
            elif issue["category"] == "isolated":
                actions = build_actions_for_isolated(issue, db)
            else:
                actions = []

            prepared.append({"issue": issue, "ctx": ctx, "actions": actions})
    return prepared


def run_interactive(date_from, date_to):
    # Phase 1: Detect + build context (single DB session)
    prepared = prepare_issues(date_from, date_to)

    if not prepared:
        print("✓ 未检出混乱打卡")
        return

    # Phase 2: Interactive selection (no DB writes)
    total = len(prepared)
    pending_actions = []  # list of ("execute", op, params, label) or ("skip",)
    idx = 0

    while idx < total:
        item = prepared[idx]
        render_issue(idx + 1, total, item["issue"], item["ctx"], item["actions"])
        try:
            choice = prompt_action(item["actions"])
        except (EOFError, KeyboardInterrupt):
            print(f"\n中断。未做任何修改。")
            return
        if choice[0] == "quit":
            print(f"\n退出。未做任何修改。")
            return
        if choice[0] == "skip":
            pending_actions.append(("skip",))
            idx += 1
            continue
        if choice[0] == "back":
            if idx > 0:
                pending_actions.pop()
                idx -= 1
            else:
                print("  已是第一条")
            continue
        # choice is ("execute", op, params, label)
        pending_actions.append(choice)
        idx += 1

    # Phase 3: Summary + confirm
    render_summary(pending_actions)
    try:
        confirm = input("\n确认执行？(回车确认，其他键取消): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。未做任何修改。")
        return
    if confirm != "":
        print("已取消。未做任何修改。")
        return

    # Phase 4: Batch execute
    executed = 0
    failed = 0
    for entry in pending_actions:
        if entry[0] != "execute":
            continue
        _, op, params, label = entry
        try:
            execute_op(op, params)
            print(f"  ✓ {label}")
            executed += 1
        except Exception as e:
            print(f"  ✗ {label} — 失败：{e}")
            failed += 1

    skipped = sum(1 for e in pending_actions if e[0] == "skip")
    print(f"\n✓ 完成。已执行 {executed} 条，跳过 {skipped} 条" +
          (f"，失败 {failed} 条" if failed else "") + "。")


def _range_for_days(days):
    today = datetime.now()
    return (today - timedelta(days=days - 1)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _date_offset(date_str, days):
    dt = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def _prompt_days(default_days):
    raw = input(f"Days [{default_days}]: ").strip()
    if not raw:
        return default_days
    try:
        days = int(raw)
    except ValueError:
        print("Invalid number, using default.")
        return default_days
    return max(1, days)


def _prompt_date(default_date=None):
    _validate_date = _from_server("_validate_date")
    while True:
        suffix = f" [{default_date}]" if default_date else ""
        raw = input(f"Date YYYY-MM-DD{suffix}: ").strip()
        if not raw and default_date:
            raw = default_date
        value = _validate_date(raw)
        if value:
            return value
        print("Invalid date.")


def run_inspect_date(date_str):
    app = _from_server("app"); get_db = _from_server("get_db")
    date_from = _date_offset(date_str, -1)
    date_to = _date_offset(date_str, 1)
    prepared = [
        item for item in prepare_issues(date_from, date_to)
        if (
            item["issue"].get("anchor_date") == date_str
            or (item["issue"].get("primary") or {}).get("date") == date_str
            or (item["issue"].get("secondary") or {}).get("date") == date_str
        )
    ]

    with app.app_context():
        db = get_db()
        checkins = db.execute(
            "SELECT id, action, date, time, created_at FROM checkins WHERE date = ? ORDER BY time",
            (date_str,),
        ).fetchall()
        activity = db.execute(
            "SELECT start_time, end_time, duration_minutes, tags FROM activity_slices WHERE date = ? ORDER BY start_time",
            (date_str,),
        ).fetchall()

    print(f"\nInspect {date_str}")
    print("\nCheckins:")
    if checkins:
        for row in checkins:
            print(f"  #{row['id']} {row['time']} {row['action']} created={row['created_at']}")
    else:
        print("  none")

    print("\nActivity slices:")
    if activity:
        for row in activity:
            end = row["end_time"] or "-"
            duration = row["duration_minutes"]
            duration_text = "-" if duration is None else f"{duration:g}m"
            print(f"  {row['start_time']} - {end}  {duration_text}  {row['tags']}")
    else:
        print("  none")

    print("\nDetected issues touching this date:")
    render_issue_list(prepared)


def run_menu(default_days):
    while True:
        print("\nFlowtrace 数据核查")
        print("  1. 查看默认范围的问题")
        print("  2. 修复默认范围的问题")
        print("  3. 查看某一天的诊断")
        print("  4. 自选天数后查看问题")
        print("  5. 自选天数后进入修复")
        print("  q. 退出")
        raw = input("> ").strip().lower()

        if raw == "q":
            return
        if raw == "1":
            date_from, date_to = _range_for_days(default_days)
            print(f"Scan: {date_from} ~ {date_to}")
            render_issue_list(prepare_issues(date_from, date_to))
            continue
        if raw == "2":
            date_from, date_to = _range_for_days(default_days)
            print(f"Scan: {date_from} ~ {date_to}")
            run_interactive(date_from, date_to)
            continue
        if raw == "3":
            run_inspect_date(_prompt_date(datetime.now().strftime("%Y-%m-%d")))
            continue
        if raw == "4":
            days = _prompt_days(default_days)
            date_from, date_to = _range_for_days(days)
            print(f"Scan: {date_from} ~ {date_to}")
            render_issue_list(prepare_issues(date_from, date_to))
            continue
        if raw == "5":
            days = _prompt_days(default_days)
            date_from, date_to = _range_for_days(days)
            print(f"Scan: {date_from} ~ {date_to}")
            run_interactive(date_from, date_to)
            continue

        print("请输入 1-5 或 q。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Flowtrace 混乱打卡核查 CLI")
    parser.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--days", type=int, help="向前 N 天（与 --from/--to 互斥）")
    parser.add_argument("--list", action="store_true", help="list detected issues and exit without changes")
    parser.add_argument("--date", help="date for inspect mode, YYYY-MM-DD")
    parser.add_argument("--inspect", action="store_true", help="inspect one date and exit without changes")
    raw_argv = argv
    if raw_argv is None:
        import sys
        raw_argv = sys.argv[1:]
    args = parser.parse_args(argv)

    CFG = _from_server("CFG")
    default_days = int(CFG.get("manage_default_days", 30))
    if not raw_argv:
        run_menu(default_days)
        return
    if args.inspect:
        if args.date:
            _validate_date = _from_server("_validate_date")
            date_value = _validate_date(args.date)
            if not date_value:
                raise SystemExit(f"invalid date: {args.date}")
        else:
            date_value = _prompt_date(datetime.now().strftime("%Y-%m-%d"))
        run_inspect_date(date_value)
        return
    date_from, date_to = _resolve_range(args, default_days)
    print(f"扫描日期范围: {date_from} ~ {date_to}")
    if args.list:
        render_issue_list(prepare_issues(date_from, date_to))
        return
    run_interactive(date_from, date_to)


if __name__ == "__main__":
    main()
