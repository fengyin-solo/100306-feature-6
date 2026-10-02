"""发电机组业务规则：状态流转、月度试机计划与试机耗油口径都收在这里。

试机视图的关键口径：
- 每个自然月每台机组至少试机一次；本月已有「已完成」试机记录的机组不再排入待试。
- 「距上次试机」按自然日计算；从未试机的机组不写数字，单独标记。
- 能不能撑一小时，用站点油料台账的当前存量对比「小时油耗」；没有油料台账
  或存量不足一小时的机组单独标出。机组自身不存独立余量，耗油后统一回写
  油料台账，再把台账余量同步到机组油量储备，保证两边是同一份数。
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

from app.services.fuel import FuelService, to_number
from app.store import store

MODULE = "genset"
SITE_MODULE = "site"
REQUIRED_FIELDS = ["机组编号", "机组型号", "额定功率"]
STATUS_ORDER = ["待命", "发电中", "故障", "维修中"]
ACTION_RULES = {"启动发电": "发电中", "关闭机组": "待命", "登记故障": "维修中"}
NEGATIVE_ACTIONS = []

DEFAULT_TEST_HOURS = 1.0
MAX_TEST_HOURS = 24.0


class GensetService:
    def __init__(self) -> None:
        self.fuel = FuelService()

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
        # 所属站点、小时油耗为选填；新机组默认从未试机、油箱余量未知。
        for field in ("所属站点", "小时油耗"):
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry["上次试机"] = values.get("上次试机") or ""
        entry["油量储备"] = to_number(values.get("油量储备"))
        entry["试机月份"] = []
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

    # ---- 月度试机计划 ----------------------------------------------------

    def monthly_plan(self, month: str | None = None) -> dict[str, Any]:
        """按站点汇总某月试机安排：待试机组、距上次试机天数、一小时用油缺口。"""
        target_month = month or date.today().strftime("%Y-%m")
        year, month_no = self._parse_month(target_month)
        last_day = date(year, month_no, monthrange(year, month_no)[1])
        today = date.today()
        # 回看历史月份时按当月最后一天算天数；看当前或未来月份时按今天算。
        anchor = min(today, last_day)

        groups: list[dict[str, Any]] = []
        site_names = [
            str(row.get("基站名称", ""))
            for row in store.rows(SITE_MODULE)
            if str(row.get("基站名称", "")).strip()
        ]
        # 台账里挂在站点表之外的机组也要露面，不能因为站点表漏记就消失。
        for row in store.rows(MODULE):
            name = str(row.get("所属站点", "")).strip()
            if name and name not in site_names:
                site_names.append(name)

        total_pending = total_short = total_done = 0
        for site_name in site_names:
            gensets = [
                row for row in store.rows(MODULE)
                if str(row.get("所属站点", "")).strip() == site_name
            ]
            group: dict[str, Any] = {
                "站点": site_name,
                "有无机组": bool(gensets),
                "待试机组": [],
                "本月已试": [],
            }
            if not gensets:
                # 没有机组记录的站点显式标空，不允许误算成待试机。
                groups.append(group)
                continue

            stock = self.fuel.site_stock(site_name)
            group["油料台账余量"] = stock
            group["有无油料台账"] = stock is not None
            for genset in gensets:
                view = self._build_genset_view(genset, target_month, anchor, stock)
                if view["本月已试"]:
                    group["本月已试"].append(view)
                    total_done += 1
                else:
                    group["待试机组"].append(view)
                    total_pending += 1
                    if view["油量不足"]:
                        total_short += 1
            # 同站多机组共用一个油罐：按站汇总待试机组的一小时耗油，更贴近实际。
            pending_hour_need = sum(
                item["小时油耗"] or 0.0 for item in group["待试机组"]
            )
            group["待试一小时合计耗油"] = round(pending_hour_need, 2)
            groups.append(group)

        groups.sort(key=lambda item: (not item["有无机组"], item["站点"]))
        return {
            "月份": target_month,
            "基准日期": anchor.isoformat(),
            "汇总": {
                "站点数": len(groups),
                "无机组站点数": sum(1 for item in groups if not item["有无机组"]),
                "待试机组数": total_pending,
                "油量不足机组数": total_short,
                "本月已试机组数": total_done,
            },
            "站点": groups,
        }

    def schedule_test(
        self,
        entry_id: int,
        *,
        month: str | None,
        test_date: str | None,
        hours: float | None,
        complete: bool,
        remark: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        """安排一次试机；complete=True 时直接回填完成，按试机时长扣减站点存油。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"发电机组 {entry_id} 不存在或已归档"

        target_month = month or date.today().strftime("%Y-%m")
        self._parse_month(target_month)

        test_day = self._parse_date(test_date) if test_date else date.today()
        if test_day is None:
            return None, "试机日期格式应为 YYYY-MM-DD"
        if test_day.strftime("%Y-%m") != target_month:
            return None, f"试机日期 {test_day.isoformat()} 不在 {target_month} 月内，无法排入该月计划"

        duration = DEFAULT_TEST_HOURS if hours is None else hours
        if duration <= 0 or duration > MAX_TEST_HOURS:
            return None, f"试机时长需在 0~{MAX_TEST_HOURS:g} 小时之间"

        done_months = [str(value) for value in entry.get("试机月份", [])]
        if target_month in done_months:
            return None, "该机组本月已完成试机，不再重复排入"

        site_name = str(entry.get("所属站点", "")).strip()
        if not site_name:
            return None, "机组未登记所属站点，无法核对油罐余量"
        hourly = to_number(entry.get("小时油耗"))
        if hourly is None or hourly <= 0:
            return None, "机组未登记小时油耗，无法估算试机耗油，请先补全机组资料"

        liters = round(hourly * duration, 2)

        if not complete:
            # 只安排计划：不扣油，但把油够不够先讲清楚。
            stock = self.fuel.site_stock(site_name)
            tip = "油量可支撑本次试机"
            if stock is None:
                tip = "站点暂无油料台账，试机前需先补录台账并确认油量"
            elif stock < liters:
                tip = f"油罐余量仅 {stock:g} 升，本次试机预计耗油 {liters:g} 升，建议先补油"
            return {
                "机组": entry,
                "计划日期": test_day.isoformat(),
                "试机时长": duration,
                "预计耗油": liters,
                "提示": tip,
            }, f"已安排 {test_day.isoformat()} 试机（{duration:g} 小时）"

        if test_day > date.today():
            return None, "试机完成日期不能晚于今天"

        # 回填完成：耗油写进同一份油料台账，余量不足时整笔拦下。
        remaining, error = self.fuel.consume_for_test(
            site_name, liters, test_day.isoformat()
        )
        if error:
            return None, error

        entry["上次试机"] = test_day.isoformat()
        done_months.append(target_month)
        entry["试机月份"] = done_months
        entry["油量储备"] = remaining
        # 同站其他机组看的是同一个油罐，余量也要同步成台账的同一份数。
        for other in store.rows(MODULE):
            if other is not entry and str(other.get("所属站点", "")).strip() == site_name:
                other["油量储备"] = remaining
        entry.setdefault("试机记录", []).append({
            "月份": target_month,
            "试机日期": test_day.isoformat(),
            "试机时长": duration,
            "耗油量": liters,
            "备注": remark or "",
        })
        if entry.get("status") == "维修中":
            entry["status"] = "待命"
        return {
            "机组": entry,
            "试机日期": test_day.isoformat(),
            "试机时长": duration,
            "耗油量": liters,
            "扣减后余量": remaining,
        }, (
            f"试机已回填：{entry.get('机组编号')} 于 {test_day.isoformat()} 试机 {duration:g} 小时，"
            f"耗油 {liters:g} 升，站点「{site_name}」台账余量 {remaining:g} 升"
        )

    # ---- 视图组装 --------------------------------------------------------

    def _build_genset_view(
        self,
        genset: dict[str, Any],
        target_month: str,
        anchor: date,
        site_stock: float | None,
    ) -> dict[str, Any]:
        last_test_text = str(genset.get("上次试机", "")).strip()
        last_test = self._parse_date(last_test_text)
        done_months = [str(value) for value in genset.get("试机月份", [])]
        tested_this_month = target_month in done_months
        hourly = to_number(genset.get("小时油耗"))

        days_since: int | None = None
        if last_test is not None:
            days_since = max((anchor - last_test).days, 0)

        short = False
        short_reason = ""
        if not tested_this_month:
            if hourly is None or hourly <= 0:
                short = False
                short_reason = "未登记小时油耗，无法估算"
            elif site_stock is None:
                short = True
                short_reason = "站点暂无油料台账"
            elif site_stock < hourly:
                short = True
                gap = round(hourly - site_stock, 2)
                short_reason = f"余量 {site_stock:g} 升 < 一小时耗油 {hourly:g} 升，缺口 {gap:g} 升"

        return {
            "id": genset.get("id"),
            "机组编号": genset.get("机组编号"),
            "机组型号": genset.get("机组型号"),
            "额定功率": genset.get("额定功率"),
            "上次试机": last_test_text or None,
            "距上次试机天数": days_since,
            "从未试机": last_test is None,
            "小时油耗": hourly,
            "站点油量储备": site_stock,
            "油量不足": short,
            "油量说明": short_reason,
            "本月已试": tested_this_month,
            "启动状态": genset.get("启动状态"),
            "机组状态": genset.get("机组状态"),
        }

    # ---- 工具 ------------------------------------------------------------

    @staticmethod
    def _parse_month(month: str) -> tuple[int, int]:
        try:
            year_text, month_text = str(month).split("-", 1)
            year, month_no = int(year_text), int(month_text)
            if not 1 <= month_no <= 12:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError(f"月份格式应为 YYYY-MM：{month}") from exc
        return year, month_no

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
