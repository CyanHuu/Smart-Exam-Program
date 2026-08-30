"""CodeWave 调用的智能排考 HTTP API。"""

from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import hmac
import os
from pathlib import Path
import shutil
import tempfile
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ai_optimizer import (
    assignment_reasons,
    compare_metrics,
    normalise_policy,
    optimise_exam_sessions,
    schedule_metrics,
    validate_policy_references,
)
from classroom_2 import get_classroom_info
from core_logic import _teacher_records, assign_exam_sessions, build_workload_stats, preference_weights
from examiner import analyze_teacher_list
from outputTask import split_schedule_by_room_groups, write_session_assignments_to_excel
from schedule_loader import load_exam_sessions


DATA_TTL_SECONDS = int(os.getenv("DATA_TTL_SECONDS", "86400"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
DATASETS = {}
SCHEDULES = {}


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experience_weight: int = Field(60, ge=0)
    gender_weight: int = Field(25, ge=0)
    department_weight: int = Field(15, ge=0)
    fairness_weight: int = Field(100, ge=0)
    consecutive_penalty: int = Field(20, ge=0)
    stability_weight: int = Field(100, ge=0)
    backup_count: int = Field(2, ge=0, le=5)
    max_formal_count: int | None = Field(None, ge=0)
    consecutive_gap_minutes: int = Field(120, ge=0, le=1440)
    time_limit_seconds: int = Field(20, ge=1, le=120)
    random_seed: int = 7
    unavailable: dict[str, list[str]] = Field(default_factory=dict)
    avoid_rooms: dict[str, list[str]] = Field(default_factory=dict)
    allow_consecutive: dict[str, bool] = Field(default_factory=dict)


class DatasetRequest(BaseModel):
    dataset_id: str


class PolicyRequest(DatasetRequest):
    policy: PolicyModel = Field(default_factory=PolicyModel)


class ReplanRequest(BaseModel):
    schedule_id: str
    unavailable_teacher_ids: list[str] = Field(min_length=1)
    affected_session_ids: list[str] = Field(default_factory=list)
    policy: PolicyModel = Field(default_factory=PolicyModel)


class ExportRequest(BaseModel):
    schedule_id: str


class GroupExportRequest(ExportRequest):
    rules: str = Field(min_length=1)
    session_id: str = Field(default="__all__", min_length=1)


class LoginRequest(BaseModel):
    username: str
    password: str
    role: str | None = None


def require_api_key(x_api_key: str | None = Header(None)):
    expected = os.getenv("SCHEDULER_API_KEY", "dev-key")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="API key无效")


def _cleanup():
    cutoff = time.time() - DATA_TTL_SECONDS
    expired = [key for key, value in DATASETS.items() if value["created_at"] < cutoff]
    for dataset_id in expired:
        dataset = DATASETS.pop(dataset_id)
        shutil.rmtree(dataset["directory"], ignore_errors=True)
        for schedule_id in [key for key, value in SCHEDULES.items() if value["dataset_id"] == dataset_id]:
            SCHEDULES.pop(schedule_id, None)


async def _save_upload(upload: UploadFile, directory: Path, label: str):
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        raise HTTPException(status_code=400, detail=f"{label}必须是.xls或.xlsx文件")
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"{label}超过上传限制")
    path = directory / f"{label}{suffix}"
    path.write_bytes(content)
    return path


def _dataset(dataset_id):
    _cleanup()
    value = DATASETS.get(dataset_id)
    if not value:
        raise HTTPException(status_code=404, detail="数据集不存在或已过期")
    return value


def _schedule(schedule_id):
    _cleanup()
    value = SCHEDULES.get(schedule_id)
    if not value:
        raise HTTPException(status_code=404, detail="排考方案不存在或已过期")
    return value


def _teacher_json(teacher):
    return {
        "teacher_id": teacher[0], "name": teacher[1], "experienced": bool(teacher[2]),
        "gender": "男" if teacher[3] else "女", "department": teacher[4],
    }


def _result_json(results):
    payload = {}
    for session_id, result in results.items():
        session = result["session"]
        payload[session_id] = {
            "session": {
                "session_id": str(session["session_id"]),
                "title": session.get("title", ""),
                "period_text": session.get("period_text", ""),
                "start": session["start"].isoformat(),
                "end": session["end"].isoformat(),
                "rooms": [{"room": str(room), "needed": int(needed)} for room, needed in session["rooms"]],
            },
            "assignments": {room: [_teacher_json(t) for t in items] for room, items in result["assignments"].items()},
            "backups": {room: [_teacher_json(t) for t in items] for room, items in result.get("backups", {}).items()},
            "report": result.get("report", {}),
        }
    return payload


def _changed_assignments(before, after):
    changes = []
    for session_id, after_result in after.items():
        before_result = before.get(session_id, {})
        rooms = set(after_result.get("assignments", {})) | set(before_result.get("assignments", {}))
        for room in sorted(rooms):
            old = {teacher[0]: teacher[1] for teacher in before_result.get("assignments", {}).get(room, [])}
            new = {teacher[0]: teacher[1] for teacher in after_result.get("assignments", {}).get(room, [])}
            if old != new:
                changes.append({
                    "session_id": session_id,
                    "room": room,
                    "removed": [{"teacher_id": key, "name": old[key]} for key in sorted(old.keys() - new.keys())],
                    "added": [{"teacher_id": key, "name": new[key]} for key in sorted(new.keys() - old.keys())],
                })
    return changes


app = FastAPI(title="AI智能排考服务", version="1.0.0")
origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/api/v1/auth/login")
def login(request: LoginRequest):
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    if hmac.compare_digest(request.username, admin_username) and hmac.compare_digest(request.password, admin_password):
        return {"role": "admin", "display_name": "排考老师"}
    datasets = sorted(DATASETS.items(), key=lambda pair: pair[1]["created_at"], reverse=True)
    for dataset_id, dataset in datasets:
        teacher_df = dataset["teacher_df"]
        matched = teacher_df[teacher_df["id_col"].astype(str).str.strip() == request.username.strip()]
        if not matched.empty and hmac.compare_digest(request.password, "123456"):
            row = matched.iloc[0]
            schedule_id = next((key for key, value in sorted(SCHEDULES.items(), key=lambda pair: pair[1]["created_at"], reverse=True) if value["dataset_id"] == dataset_id), None)
            return {"role": "teacher", "display_name": "监考老师", "teacher_id": str(row["id_col"]).strip(), "teacher_name": str(row["name_col"]).strip(), "schedule_id": schedule_id}
    raise HTTPException(status_code=401, detail="工号不存在或密码不正确，请使用教师表中的工号和密码123456")


@app.post("/api/v1/datasets/import", dependencies=[Depends(require_api_key)])
async def import_dataset(
    classroom_file: UploadFile = File(...),
    teacher_file: UploadFile = File(...),
    schedule_file: UploadFile = File(...),
):
    directory = Path(tempfile.mkdtemp(prefix="smart-exam-"))
    try:
        classroom_path = await _save_upload(classroom_file, directory, "classroom")
        teacher_path = await _save_upload(teacher_file, directory, "teacher")
        schedule_path = await _save_upload(schedule_file, directory, "schedule")
        classroom_data = get_classroom_info(classroom_path)
        teacher_df = analyze_teacher_list(teacher_path)
        sessions = load_exam_sessions(schedule_path, classroom_data)
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dataset_id = uuid4().hex
    DATASETS[dataset_id] = {
        "created_at": time.time(), "directory": directory,
        "classroom_path": classroom_path, "teacher_path": teacher_path, "schedule_path": schedule_path,
        "classroom_data": classroom_data, "teacher_df": teacher_df, "sessions": sessions,
    }
    return {
        "dataset_id": dataset_id,
        "expires_in_seconds": DATA_TTL_SECONDS,
        "summary": {
            "teachers": len(teacher_df), "sessions": len(sessions),
            "rooms": sum(len(session["rooms"]) for session in sessions),
            "formal_slots": sum(needed for session in sessions for _, needed in session["rooms"]),
        },
        "teachers": [{"teacher_id": row["id_col"], "name": row["name_col"]} for _, row in teacher_df.iterrows()],
        "sessions": [{
            "session_id": str(session["session_id"]),
            "start": session["start"].isoformat(), "end": session["end"].isoformat(),
        } for session in sessions],
    }


@app.post("/api/v1/policies/validate", dependencies=[Depends(require_api_key)])
def validate_policy(request: PolicyRequest):
    dataset = _dataset(request.dataset_id)
    policy = request.policy.model_dump()
    errors = validate_policy_references(dataset["sessions"], dataset["teacher_df"], policy)
    return {"valid": not errors, "errors": errors, "policy": normalise_policy(policy)}


@app.post("/api/v1/schedules/solve", dependencies=[Depends(require_api_key)])
def solve_schedule(request: PolicyRequest):
    dataset = _dataset(request.dataset_id)
    policy = request.policy.model_dump()
    try:
        with redirect_stdout(StringIO()):
            baseline, _ = assign_exam_sessions(
                dataset["sessions"], dataset["teacher_df"],
                weights=preference_weights("default"), backup_count=policy["backup_count"], random_seed=7,
            )
        teachers = _teacher_records(dataset["teacher_df"])
        baseline_metrics = schedule_metrics(baseline, teachers)
        optimised, metrics = optimise_exam_sessions(dataset["sessions"], dataset["teacher_df"], policy)
        workload = build_workload_stats(optimised, teachers)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    schedule_id = uuid4().hex
    SCHEDULES[schedule_id] = {
        "dataset_id": request.dataset_id, "created_at": time.time(), "results": optimised,
        "baseline": baseline, "policy": policy, "metrics": metrics,
    }
    return {
        "schedule_id": schedule_id,
        "baseline_metrics": baseline_metrics,
        "optimised_metrics": metrics,
        "comparison": compare_metrics(baseline_metrics, metrics),
        "explanation_facts": assignment_reasons(optimised, dataset["teacher_df"]),
        "workload": list(workload.values()),
        "baseline": _result_json(baseline),
        "optimised": _result_json(optimised),
    }


@app.post("/api/v1/schedules/replan", dependencies=[Depends(require_api_key)])
def replan_schedule(request: ReplanRequest):
    previous = _schedule(request.schedule_id)
    dataset = _dataset(previous["dataset_id"])
    absent = set(map(str, request.unavailable_teacher_ids))
    affected = set(map(str, request.affected_session_ids))
    for session_id, result in previous["results"].items():
        if any(teacher[0] in absent for source in (result["assignments"], result.get("backups", {})) for items in source.values() for teacher in items):
            affected.add(str(session_id))
    all_sessions = {str(session["session_id"]) for session in dataset["sessions"]}
    unknown = affected - all_sessions
    if unknown:
        raise HTTPException(status_code=422, detail=f"未知受影响场次: {', '.join(sorted(unknown))}")
    try:
        replanned, metrics = optimise_exam_sessions(
            dataset["sessions"], dataset["teacher_df"], request.policy.model_dump(),
            previous_results=previous["results"], unavailable_teacher_ids=absent,
            locked_session_ids=all_sessions - affected,
        )
        workload = build_workload_stats(replanned, _teacher_records(dataset["teacher_df"]))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    schedule_id = uuid4().hex
    changes = _changed_assignments(previous["results"], replanned)
    SCHEDULES[schedule_id] = {
        "dataset_id": previous["dataset_id"], "created_at": time.time(), "results": replanned,
        "baseline": previous["baseline"], "policy": request.policy.model_dump(), "metrics": metrics,
    }
    return {
        "schedule_id": schedule_id, "affected_session_ids": sorted(affected),
        "changed_rooms": len(changes), "changes": changes, "metrics": metrics,
        "workload": list(workload.values()),
        "explanation_facts": assignment_reasons(replanned, dataset["teacher_df"]),
        "optimised": _result_json(replanned),
    }


@app.post("/api/v1/schedules/export", dependencies=[Depends(require_api_key)])
def export_schedule(request: ExportRequest):
    schedule = _schedule(request.schedule_id)
    dataset = _dataset(schedule["dataset_id"])
    suffix = dataset["schedule_path"].suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        raise HTTPException(status_code=422, detail="排考模板必须是.xls或.xlsx文件")
    output_path = dataset["directory"] / f"智能排考_{request.schedule_id[:8]}{suffix}"
    try:
        write_session_assignments_to_excel(
            schedule["results"], str(dataset["schedule_path"]), header_row=2, output_path=str(output_path)
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = "ai_exam_schedule.xlsx" if suffix == ".xlsx" else "ai_exam_schedule.xls"
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if suffix == ".xlsx" else "application/vnd.ms-excel"
    return FileResponse(output_path, filename=filename, media_type=media_type)


@app.post("/api/v1/schedules/export-groups", dependencies=[Depends(require_api_key)])
def export_schedule_groups(request: GroupExportRequest):
    schedule = _schedule(request.schedule_id)
    dataset = _dataset(schedule["dataset_id"])
    suffix = dataset["schedule_path"].suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        raise HTTPException(status_code=422, detail="排考模板必须是.xls或.xlsx文件")
    if request.session_id != "__all__" and request.session_id not in {str(key) for key in schedule["results"]}:
        raise HTTPException(status_code=422, detail="所选考试场次不存在，请重新排考")
    full_path = dataset["directory"] / f"智能排考_{request.schedule_id[:8]}_完整{suffix}"
    output_path = dataset["directory"] / f"智能排考_{request.schedule_id[:8]}_分组{suffix}"
    try:
        write_session_assignments_to_excel(
            schedule["results"], str(dataset["schedule_path"]), header_row=2, output_path=str(full_path)
        )
        split_schedule_by_room_groups(str(full_path), str(output_path), header_row=2, rules=request.rules, session_ids=None if request.session_id == "__all__" else [request.session_id])
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = "ai_exam_schedule_grouped.xlsx" if suffix == ".xlsx" else "ai_exam_schedule_grouped.xls"
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if suffix == ".xlsx" else "application/vnd.ms-excel"
    return FileResponse(output_path, filename=filename, media_type=media_type)


@app.get("/api/v1/schedules/{schedule_id}/workload", dependencies=[Depends(require_api_key)])
def schedule_workload(schedule_id: str):
    schedule = _schedule(schedule_id)
    dataset = _dataset(schedule["dataset_id"])
    teachers = _teacher_records(dataset["teacher_df"])
    try:
        workload = build_workload_stats(schedule["results"], teachers)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"教师工作量计算失败：{exc}") from exc
    return {"items": list(workload.values())}


@app.get("/api/v1/schedules/{schedule_id}/teachers/{teacher_id}", dependencies=[Depends(require_api_key)])
def teacher_schedule(schedule_id: str, teacher_id: str):
    schedule = _schedule(schedule_id)
    dataset = _dataset(schedule["dataset_id"])
    teacher_ids = {str(value) for value in dataset["teacher_df"]["id_col"].tolist()}
    if str(teacher_id) not in teacher_ids:
        raise HTTPException(status_code=404, detail="教师工号不存在")
    filtered = {}
    for session_id, result in schedule["results"].items():
        assignments = {room: [teacher for teacher in teachers if str(teacher[0]) == str(teacher_id)] for room, teachers in result["assignments"].items()}
        backups = {room: [teacher for teacher in teachers if str(teacher[0]) == str(teacher_id)] for room, teachers in result.get("backups", {}).items()}
        if any(assignments.values()) or any(backups.values()):
            filtered[session_id] = {**result, "assignments": assignments, "backups": backups}
    return {"schedule_id": schedule_id, "teacher_id": str(teacher_id), "results": _result_json(filtered)}


# 同源提供网页，避免本地部署时再配置一个前端开发服务器。
app.mount("/", StaticFiles(directory=Path(__file__).parent / "design_export", html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
