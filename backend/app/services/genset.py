"""发电机组业务规则：状态流转、字段校验与筛选口径都收在这里。

按月排的试机视图也在本模块：
- ``monthly_plan`` 按站点汇总本月待试机机组、距上次试机天数、油量是否够跑一小时；
- ``schedule_trial`` 安排并回填一次试机，按试机时长折算油耗，
  扣完的余量直接写回油料台账（fuel 表），机组油量储备与台账取同一份；
- 本月已经试过的机组不会再排进待试机列表，也不能重复登记。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "genset"
FUEL_MODULE = "fuel"
SITE_MODULE = "site"
REQUIRED_FIELDS = ["机组编号", "机组型号", "额定功率"]
STATUS_ORDER = ["待命", "发电中", "故障", "维修中"]
ACTION_RULES = {"启动发电": "发电中", "关闭机组": "待命", "登记故障": "维修中"}
NEGATIVE_ACTIONS = []

# 故障/维修中的机组本月不安排试机，但仍在视图里列出并说明原因
TRIAL_BLOCKED_STATUS = {"故障", "维修中"}


def _to_float(value: Any) -> float | None:
    """把台账里可能写成字符串的数字读成浮点数；读不出来返回 None。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    """解析 YYYY-MM-DD；空值或不合法的日期返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_month(month: str | None) -> tuple[date, date]:
    """把 ``YYYY-MM`` 解析成月初、下月初一；缺省取本月。"""
    if not month:
        today = date.today()
        first = today.replace(day=1)
    else:
        text = month.strip()
        try:
            first = datetime.strptime(text, "%Y-%m").date().replace(day=1)
        except ValueError:
            raise ValueError("月份格式应为 YYYY-MM，例如 2026-10")
    if first.month == 12:
        next_first = first.replace(year=first.year + 1, month=1)
    else:
        next_first = first.replace(month=first.month + 1)
    return first, next_first


class GensetService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("机组编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"发电机组 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于发电机组可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"发电机组已{action}"

    # ------------------------------------------------------------------
    # 月度试机视图
    # ------------------------------------------------------------------
    def _fuel_book(self, site_name: str) -> dict[str, Any] | None:
        """取站点在用的油料台账：同站多条时取日期最新的一条。"""
        books = [
            row for row in store.rows(FUEL_MODULE)
            if str(row.get("所属站点", "")).strip() == site_name
        ]
        if not books:
            return None
        return max(books, key=lambda row: (str(row.get("油料日期", "")), int(row.get("id", 0))))

    def monthly_plan(self, month: str | None = None, today: date | None = None) -> dict[str, Any]:
        """按月排试机：按站点列出本月待试机机组、距上次试机天数与油量缺口。

        站点清单以基站台账为准：没有机组记录的站点只显示"暂无机组"，
        不会被算成待试机；台账外、机组上挂着的站点也补在后面。
        """
        first, next_first = _parse_month(month)
        today = today or date.today()

        gensets = store.rows(MODULE)
        site_order = [str(row.get("基站名称", "")).strip() for row in store.rows(SITE_MODULE)]
        site_order = [name for name in site_order if name]
        for row in gensets:
            name = str(row.get("所属站点", "")).strip()
            if name and name not in site_order:
                site_order.append(name)

        by_site: dict[str, list[dict[str, Any]]] = {name: [] for name in site_order}
        for row in gensets:
            name = str(row.get("所属站点", "")).strip()
            if not name:
                continue
            by_site.setdefault(name, []).append(row)

        sites: list[dict[str, Any]] = []
        total_due = total_tested = total_short = total_blocked = 0
        for site_name in site_order:
            rows = by_site.get(site_name, [])
            fuel_book = self._fuel_book(site_name)
            fuel_stock = _to_float(fuel_book.get("当前存量")) if fuel_book else None

            units: list[dict[str, Any]] = []
            due_count = tested_count = short_count = blocked_count = 0
            for row in rows:
                last_date = _parse_date(row.get("上次试机"))
                days_since = (today - last_date).days if last_date else None
                hourly_burn = _to_float(row.get("小时油耗"))
                # 同站共用油罐，油量储备一律以油料台账的当前存量为准
                reserve = fuel_stock if fuel_stock is not None else _to_float(row.get("油量储备"))
                need_one_hour = hourly_burn if hourly_burn is not None else None
                tested_this_month = bool(last_date and first <= last_date < next_first)
                blocked = str(row.get("status", "")) in TRIAL_BLOCKED_STATUS
                blocked_reason = f"机组{row.get('status')}，暂不安排试机" if blocked else ""
                fuel_short = bool(
                    not tested_this_month
                    and reserve is not None
                    and need_one_hour is not None
                    and reserve < need_one_hour
                )
                no_fuel_book = fuel_book is None
                if tested_this_month:
                    due = False
                    tested_count += 1
                elif blocked:
                    due = False
                    blocked_count += 1
                else:
                    due = True
                    due_count += 1
                    if fuel_short:
                        short_count += 1
                units.append({
                    "id": row.get("id"),
                    "机组编号": row.get("机组编号"),
                    "机组型号": row.get("机组型号"),
                    "额定功率": row.get("额定功率"),
                    "机组状态": row.get("机组状态") or row.get("status"),
                    "上次试机": row.get("上次试机") or "",
                    "距上次试机天数": days_since,
                    "小时油耗": hourly_burn,
                    "油量储备": reserve,
                    "够跑一小时": (not fuel_short) if need_one_hour is not None and reserve is not None else None,
                    "油量不足": fuel_short,
                    "无油料台账": no_fuel_book,
                    "本月已试": tested_this_month,
                    "待试机": due,
                    "不可排试": blocked,
                    "不可排原因": blocked_reason,
                })

            sites.append({
                "站点": site_name,
                "暂无机组": len(rows) == 0,
                "无油料台账": fuel_book is None and len(rows) > 0,
                "油量储备": fuel_stock,
                "待试机数": due_count,
                "本月已试数": tested_count,
                "油量不足数": short_count,
                "不可排数": blocked_count,
                "机组": units,
            })
            total_due += due_count
            total_tested += tested_count
            total_short += short_count
            total_blocked += blocked_count

        return {
            "月份": first.strftime("%Y-%m"),
            "月初": first.strftime("%Y-%m-%d"),
            "月末": (next_first - first).days,
            "站点数": len(site_order),
            "机组总数": len(gensets),
            "待试机数": total_due,
            "本月已试数": total_tested,
            "油量不足数": total_short,
            "暂不可排数": total_blocked,
            "站点": sites,
        }

    def schedule_trial(
        self,
        entry_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """安排并回填一次试机。

        试机日期缺省为今天；试机时长（小时）缺省为 1 小时。
        油耗 = 小时油耗 × 试机时长，从站点油料台账的当前存量里扣，
        机组油量储备同步成台账余量；本月已试、机组故障、台账油量不够都拦下。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"发电机组 {entry_id} 不存在或已归档", None

        trial_date = _parse_date(values.get("试机日期"))
        if values.get("试机日期") in (None, ""):
            trial_date = date.today()
        if trial_date is None:
            return None, "试机日期格式应为 YYYY-MM-DD", None

        duration = _to_float(values.get("试机时长"))
        if duration is None:
            duration = 1.0
        if duration <= 0:
            return None, "试机时长必须大于 0（小时）", None
        if duration > 24:
            return None, "单次试机时长不能超过 24 小时", None

        status = str(entry.get("status", ""))
        if status in TRIAL_BLOCKED_STATUS:
            return None, f"机组当前为「{status}」，不能安排试机", None

        first = trial_date.replace(day=1)
        last_date = _parse_date(entry.get("上次试机"))
        if last_date and last_date >= first:
            return None, f"该机组本月已于 {last_date:%Y-%m-%d} 试过机，本月不再重复安排", None

        hourly_burn = _to_float(entry.get("小时油耗"))
        if hourly_burn is None or hourly_burn <= 0:
            return None, "机组未登记小时油耗，无法折算试机油耗", None

        site_name = str(entry.get("所属站点", "")).strip()
        fuel_book = self._fuel_book(site_name)
        if fuel_book is None:
            return None, f"站点「{site_name}」没有油料台账，无法按试机时长扣减油量", None

        stock = _to_float(fuel_book.get("当前存量"))
        if stock is None:
            return None, f"站点「{site_name}」油料台账的当前存量不是有效数字", None

        consumed = round(hourly_burn * duration, 2)
        if stock < consumed:
            return (
                None,
                f"油量储备仅 {stock:g} 升，本次试机需 {consumed:g} 升，"
                f"撑不起 {duration:g} 小时试机，请先补油",
                None,
            )

        remaining = round(stock - consumed, 2)
        previous_burn = _to_float(fuel_book.get("发电消耗")) or 0.0
        # 写回油料台账：余量、累计消耗、日期都落在同一份记录上
        fuel_book["当前存量"] = remaining
        fuel_book["发电消耗"] = round(previous_burn + consumed, 2)
        fuel_book["油料日期"] = trial_date.strftime("%Y-%m-%d")
        if remaining < hourly_burn:
            fuel_book["status"] = "需补油"
            fuel_book["油料状态"] = "需补油"
            fuel_book["abnormal"] = True
        elif remaining < hourly_burn * 2:
            fuel_book["status"] = "油量偏低"
            fuel_book["油料状态"] = "油量偏低"
            fuel_book["abnormal"] = True
        else:
            fuel_book["status"] = "储备充足"
            fuel_book["油料状态"] = "储备充足"
            fuel_book["abnormal"] = False

        # 回填机组：试机日期与油量储备都取台账同一份数据
        entry["上次试机"] = trial_date.strftime("%Y-%m-%d")
        entry["油量储备"] = remaining
        if status == "发电中":
            entry["status"] = "待命"
            entry["机组状态"] = "待命"
            entry["启动状态"] = f"试机 {duration:g} 小时正常"
        else:
            entry["机组状态"] = entry.get("机组状态") or "待命"
            entry["启动状态"] = f"试机 {duration:g} 小时正常"

        result = dict(entry)
        result["本次油耗"] = consumed
        result["试机时长"] = duration
        result["试机日期"] = trial_date.strftime("%Y-%m-%d")
        result["台账余量"] = remaining
        return result, f"试机完成：耗油 {consumed:g} 升，台账余量 {remaining:g} 升", fuel_book
