"""发电机组接口：维护发电机组，覆盖启动发电、关闭机组、登记故障等动作。

另含按月排的试机视图：GET /api/genset/monthly-plan 与 POST /api/genset/{id}/trial。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.genset import GensetService

router = APIRouter(prefix="/api/genset", tags=["发电机组"])

service = GensetService()

LIST_FIELDS = ["机组编号", "机组型号", "额定功率", "所属站点", "上次试机", "油量储备", "启动状态", "机组状态"]
STATUSES = ["待命", "发电中", "故障", "维修中"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按机组编号检索"),
    status: str | None = Query(default=None, description="待命、发电中、故障、维修中"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按机组编号与状态过滤发电机组列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/monthly-plan", response_model=dict)
def monthly_plan(
    month: str | None = Query(default=None, description="月份 YYYY-MM，缺省取本月"),
) -> dict[str, Any]:
    """按月排的试机视图：按站点列出本月待试机机组、距上次试机天数与油量缺口。

    没有机组记录的站点返回「暂无机组」标记，不计入待试机；
    本月已经试过的机组归入「本月已试」，不再重复排。
    """
    try:
        return service.monthly_plan(month=month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出发电机组清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "genset", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条发电机组明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"发电机组 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条发电机组，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="发电机组已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条发电机组执行启动发电、关闭机组、登记故障；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/trial", response_model=ActionResult)
def schedule_trial(entry_id: int, payload: EntryPayload) -> ActionResult:
    """安排一次试机并回填试机日期。

    可传「试机日期」（YYYY-MM-DD，缺省今天）与「试机时长」（小时，缺省 1）；
    按试机时长折算油耗，扣完的余量写回油料台账，机组油量储备同步取台账余量。
    """
    entry, message, _fuel_book = service.schedule_trial(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
