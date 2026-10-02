"""油料管理业务规则：状态流转、字段校验与筛选口径都收在这里。

发电机组试机耗油也走本服务：机组不自己记一份余量，统一读写油料台账的
「当前存量」，保证试机视图与油料台账看到的是同一份数。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "fuel"
REQUIRED_FIELDS = ["记录编号", "所属站点", "油料类型"]
STATUS_ORDER = ["储备充足", "油量偏低", "需补油", "已补充"]
ACTION_RULES = {"记录消耗": "油量偏低", "申请补油": "需补油", "完成补油": "已补充"}
NEGATIVE_ACTIONS = []

# 站点存量低于该线（升）时，台账状态回写为「油量偏低」；为 0 则「需补油」。
LOW_STOCK_LINE = 50.0


def to_number(value: Any) -> float | None:
    """把台账里可能写成字符串的数值解析成数字；空值或非数字返回 None。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class FuelService:
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
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def site_ledger(self, site_name: str) -> dict[str, Any] | None:
        """取某站点最新一条油料台账；没有台账返回 None（按撑不起一小时处理）。"""
        rows = [row for row in store.rows(MODULE) if str(row.get("所属站点", "")) == site_name]
        if not rows:
            return None
        return max(rows, key=lambda row: (str(row.get("油料日期", "")), int(row.get("id", 0))))

    def site_stock(self, site_name: str) -> float | None:
        """站点当前存油（升）；无台账返回 None。"""
        ledger = self.site_ledger(site_name)
        if ledger is None:
            return None
        return to_number(ledger.get("当前存量"))

    def consume_for_test(self, site_name: str, liters: float, test_date: str) -> tuple[float | None, str]:
        """试机耗油落到站点油料台账：当前存量扣减、发电消耗累加，回写台账状态。

        返回扣减后的站点余量；没有台账或余量不足时不动数据并给出原因，
        由调用方决定要不要拦下这次试机。
        """
        ledger = self.site_ledger(site_name)
        if ledger is None:
            return None, f"站点「{site_name}」没有油料台账，无法登记耗油，请先补录台账或申请补油"
        stock = to_number(ledger.get("当前存量"))
        if stock is None:
            return None, "油料台账的当前存量不是有效数字，请先核对台账"
        if liters > stock:
            return stock, (
                f"站点「{site_name}」油罐余量 {stock:g} 升，本次试机需耗油 {liters:g} 升，"
                "余量不足，请补油后再回填试机"
            )
        consumed = to_number(ledger.get("发电消耗")) or 0.0
        ledger["当前存量"] = round(stock - liters, 2)
        ledger["发电消耗"] = round(consumed + liters, 2)
        ledger["油料日期"] = test_date
        if ledger["当前存量"] <= 0:
            ledger["status"] = "需补油"
            ledger["油料状态"] = "需补油"
        elif ledger["当前存量"] < LOW_STOCK_LINE:
            ledger["status"] = "油量偏低"
            ledger["油料状态"] = "油量偏低"
        else:
            ledger["status"] = "储备充足"
            ledger["油料状态"] = "储备充足"
        return float(ledger["当前存量"]), ""

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
            return None, f"油料记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于油料管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"油料记录已{action}"
