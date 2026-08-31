"""CodeWave 调用的智能排考 HTTP API。"""

from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import hmac
import json
import os
import re
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
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
    max_total_count: int | None = Field(None, ge=0)
    consecutive_gap_minutes: int = Field(120, ge=0, le=1440)
    time_limit_seconds: int = Field(20, ge=1, le=120)
    random_seed: int = 7
    unavailable: dict[str, list[str]] = Field(default_factory=dict)
    avoid_rooms: dict[str, list[str]] = Field(default_factory=dict)
    allow_consecutive: dict[str, bool] = Field(default_factory=dict)


class DatasetRequest(BaseModel):
    dataset_id: str


class AiPolicyRequest(BaseModel):
    dataset_id: str
    instruction: str = Field(min_length=1, max_length=2000)
    current_policy: dict = Field(default_factory=dict)
    schedule_id: str | None = None
    conversation: list[dict] = Field(default_factory=list, max_length=20)


class PolicyRequest(DatasetRequest):
    policy: PolicyModel = Field(default_factory=PolicyModel)


class ReplanRequest(BaseModel):
    schedule_id: str
    unavailable_teacher_ids: list[str] = Field(default_factory=list)
    unavailable_by_session: dict[str, list[str]] = Field(default_factory=dict)
    affected_session_ids: list[str] = Field(default_factory=list)
    policy: PolicyModel = Field(default_factory=PolicyModel)
    preview: bool = False


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


def _natural_session_ids(instruction, sessions):
    """从自然语言时间筛选真实场次，避免模型把年月数字误当成场次ID。"""
    month_words = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
    month_match = re.search(r"(?:(\d{4})\s*年)?\s*(\d{1,2}|十一|十二|十|[一二三四五六七八九])\s*月", instruction)
    day_match = re.search(r"(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*(?:日|号)", instruction)
    weekday_match = re.search(r"(?:周|星期)([一二三四五六日天7])", instruction)
    month = int(month_match.group(2)) if month_match and month_match.group(2).isdigit() else month_words.get(month_match.group(2)) if month_match else None
    year = int(month_match.group(1)) if month_match and month_match.group(1) else None
    weekday = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "7": 6}.get(weekday_match.group(1)) if weekday_match else None
    morning = "上午" in instruction or "早上" in instruction
    afternoon = "下午" in instruction or "晚上" in instruction
    selected = []
    for session in sessions:
        start = session["start"]
        if year and start.year != year:
            continue
        if month and start.month != month:
            continue
        if day_match:
            day_month = int(day_match.group(1)) if day_match.group(1) else None
            day = int(day_match.group(2))
            if day_month and start.month != day_month:
                continue
            if start.day != day:
                continue
        if weekday is not None and start.weekday() != weekday:
            continue
        if morning and start.hour >= 12:
            continue
        if afternoon and start.hour < 12:
            continue
        if month or day_match or weekday is not None:
            selected.append(str(session["session_id"]))
    return selected


def _natural_teacher_ids(instruction, teacher_df):
    known = {str(row["id_col"]).strip(): str(row["name_col"]).strip() for _, row in teacher_df.iterrows()}
    ids = {teacher_id for teacher_id in known if teacher_id in instruction}
    ids.update(teacher_id for teacher_id, name in known.items() if name and name in instruction)
    batch_words = ("所有", "全部", "全体", "批量", "每位", "每个")
    has_batch_scope = any(word in instruction for word in batch_words)
    all_teacher_scope = has_batch_scope and bool(re.search(r"(?:所有|全部|全体)\s*(?:的)?\s*(?:老师|教师|人员)", instruction))
    male_scope = bool(re.search(r"男(?:性|生|老师|教师|的)?", instruction))
    female_scope = bool(re.search(r"女(?:性|生|老师|教师|的)?", instruction))
    experienced_scope = bool(re.search(r"有经验|经验丰富|资深|熟悉排考", instruction))
    inexperienced_scope = bool(re.search(r"无经验|没有经验|经验不足", instruction))
    for _, row in teacher_df.iterrows():
        teacher_id = str(row["id_col"]).strip()
        department = str(row.get("dept_col", "")).strip()
        department_stem = re.split(r"学院|专业|教研室|系|部", department)[0].strip()
        department_match = bool(department and (department in instruction or len(department_stem) >= 2 and department_stem in instruction))
        gender = str(row.get("gender_col", "")).strip().lower()
        is_male = gender in {"1", "男", "male", "m"}
        is_experienced = str(row.get("experience_col", "")).strip().lower() in {"1", "是", "有", "有经验", "yes", "true"}
        attribute_match = (
            (male_scope and is_male) or (female_scope and not is_male)
            or (experienced_scope and is_experienced) or (inexperienced_scope and not is_experienced)
        )
        if all_teacher_scope or (has_batch_scope and (department_match or attribute_match)) or (attribute_match and "老师" in instruction):
            ids.add(teacher_id)
    return sorted(ids)


def _agnes_policy(dataset, instruction, current_policy=None, schedule_id=None, conversation=None):
    """把自然语言排考要求转换成 policy；最终排考仍由本地 OR-Tools 完成。"""
    api_key = os.getenv("AGNES_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="未配置 AGNES_API_KEY")

    teachers = [
        {
            "teacher_id": str(row["id_col"]).strip(),
            "name": str(row["name_col"]).strip(),
            "department": str(row.get("dept_col", "")).strip(),
            "gender": "男" if str(row.get("gender_col", "")).strip().lower() in {"1", "男", "male", "m"} else "女",
            "experienced": str(row.get("experience_col", 0)).strip().lower() in {"1", "是", "有", "有经验", "yes", "true"},
        }
        for _, row in dataset["teacher_df"].iterrows()
    ]
    sessions = [
        {
            "session_id": str(session["session_id"]),
            "period": session.get("period_text", ""),
            "start": session["start"].isoformat(),
            "end": session["end"].isoformat(),
            "rooms": [str(room) for room, _ in session.get("rooms", [])],
        }
        for session in dataset["sessions"]
    ]
    policy_fields = set(normalise_policy())
    current_raw = normalise_policy(current_policy or {})
    current = {key: current_raw[key] for key in policy_fields}
    scheduled = _schedule(schedule_id) if schedule_id else None
    history = [
        str(item.get("content", "")).strip()[:2000]
        for item in (conversation or [])
        if isinstance(item, dict) and item.get("role") == "user" and str(item.get("content", "")).strip()
    ][-20:]
    conversation_text = "\n".join((history + [instruction])[-20:])
    prompt = f"""
请先判断用户意图，再只输出一个 JSON 对象，不要 Markdown。
你是排考助手，不是通用聊天机器人，也不能直接生成教师安排。
intent 只能是 policy、question、replan 或 unsupported：
- policy：用户要新增或修改排考规则，返回 policy 修改补丁；
- question：用户在询问排考规则或排考流程，返回简短 answer，不修改 policy；
- replan：用户说明某位教师在某个日期、时间或场次有事，返回 teacher_ids、affected_session_ids、reason 和 answer；不能直接修改排考结果；
- question：用户在询问排考规则或排考流程，必须依据“当前已有 policy”回答，返回简短 answer，不修改 policy；
- 用户询问“当前优先级”时，只展示经验匹配、男女搭配、部门均衡、公平分配这四个主优先级及其相对顺序；稳定性是默认硬性条件，不参与优先级排序；连续监考惩罚、备选人数等属于约束或辅助参数。
- 用户询问“备选监考/备用人员规则”时，说明默认备选人数不超过该考场正式监考人数（1名正式对应1名备选，2名正式对应2名备选），并先覆盖所有考场再追加名额；候选教师必须满足时间和总任务量限制，不得把备选规则回答成正式监考规则。
- unsupported：与排考无关，answer 必须是“我只能协助处理排考规则、排考安排和教师调度问题。”。
只能使用上下文中存在的教师工号、考试场次ID和考场编号。
这是一次连续对话。已有排考规则必须保留；只在用户本次明确修改时返回对应字段，没有修改的字段不要改变。
当用户调整“优先级”或说“更重视/降低某项”时，通过 experience_weight、gender_weight、department_weight 和 fairness_weight 重新分配权重；权重越大表示越优先。
当用户说“总任务量/总次数不超过N次”时，只修改 max_total_count；总任务量包含正式监考和备选待命。只有用户明确说“正式监考次数”时才修改 max_formal_count。
若同名教师无法唯一确定，返回 clarification_required=true，并在 message 说明需要确认。
如果用户表达“某老师周三上午有事”“工号01395第二场不能监考”等自然语言，请结合教师和场次上下文识别对应工号与场次；无法唯一对应时必须请求确认。
如果用户表达“所有/全部/批量 + 某部门/专业/学院/性别/经验条件 + 教师”，必须识别符合条件的全部教师工号，返回批量 teacher_ids；不要只返回一个教师，也不要要求用户逐个输入工号。当前教师字段包括工号、姓名、性别、是否有经验、部门。

JSON 顶层格式：
{{"intent": "policy", "clarification_required": false, "message": "", "answer": "", "teacher_ids": [], "affected_session_ids": [], "reason": "", "policy": {{...}}}}

policy 必须包含这些字段：
experience_weight, gender_weight, department_weight, fairness_weight,
consecutive_penalty, stability_weight, backup_count, max_formal_count, max_total_count,
consecutive_gap_minutes, time_limit_seconds, random_seed,
unavailable, avoid_rooms, allow_consecutive。

当前已有 policy：{json.dumps(current, ensure_ascii=False)}
教师上下文：{json.dumps(teachers, ensure_ascii=False)}
考试场次上下文：{json.dumps(sessions, ensure_ascii=False)}
已有排考方案：{json.dumps(bool(scheduled), ensure_ascii=False)}
本次对话中用户之前的要求（仅用于补全省略的教师、时间和场次，不要重复执行已完成的规则）：{conversation_text}
用户要求：{instruction}
"""
    base_url = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
    payload = json.dumps({
        "model": os.getenv("AGNES_MODEL", "agnes-2.5-flash"),
        "messages": [
            {"role": "system", "content": "严格遵守用户输入中的 JSON 输出要求。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 2000,
    }).encode("utf-8")
    request = Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        result = None
        timeout = int(os.getenv("AGNES_TIMEOUT_SECONDS", "180"))
        for attempt in range(2):
            try:
                with urlopen(request, timeout=timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                break
            except (ssl.SSLError, ConnectionResetError) as exc:
                if attempt == 1:
                    raise exc
    except (HTTPError, URLError, TimeoutError, socket.timeout) as exc:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            detail = "Agnes AI响应超时，请稍后重试；也可以设置 AGNES_TIMEOUT_SECONDS 延长等待时间"
        else:
            detail = f"Agnes API调用失败：{exc}"
        raise HTTPException(status_code=502, detail=detail) from exc
    except (ssl.SSLError, ConnectionResetError) as exc:
        raise HTTPException(status_code=502, detail="Agnes HTTPS连接被中断，请检查代理/VPN后重试") from exc

    try:
        content = result["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`").removeprefix("json").strip()
        parsed = json.loads(content)
        natural_teacher_ids = _natural_teacher_ids(conversation_text, dataset["teacher_df"])
        natural_session_ids = _natural_session_ids(conversation_text, dataset["sessions"])
        unavailable_language = re.search(r"有事|旅游|请假|休假|出差|不能监考|无法监考|不能工作|无法工作|不参加|不可用|不工作|没空|没时间|无法参加|不方便", conversation_text)
        # Agnes 可能因批量部门表达式不熟悉而先要求澄清；只要本地已识别出
        # 批量教师和调度意图，就继续走确定性的批量预览流程。
        if parsed.get("clarification_required") and not (natural_teacher_ids and unavailable_language):
            return parsed
        if parsed.get("intent") in {"question", "unsupported"} and not (natural_teacher_ids and unavailable_language):
            return {
                "intent": parsed["intent"],
                "clarification_required": False,
                "message": "",
                "answer": parsed.get("answer") or "我只能协助处理排考规则、排考安排和教师调度问题。",
                "policy": current,
            }
        if parsed.get("intent") == "replan" or (natural_teacher_ids and unavailable_language):
            teacher_ids = [str(value) for value in parsed.get("teacher_ids", [])]
            session_ids = [str(value) for value in parsed.get("affected_session_ids", [])]
            teacher_ids = sorted(set(teacher_ids) | set(natural_teacher_ids))
            if natural_session_ids:
                session_ids = natural_session_ids
            known_teachers = {str(row["id_col"]).strip() for _, row in dataset["teacher_df"].iterrows()}
            known_sessions = {str(session["session_id"]) for session in dataset["sessions"]}
            unknown_teachers = sorted(set(teacher_ids) - known_teachers)
            unknown_sessions = sorted(set(session_ids) - known_sessions)
            if not scheduled:
                raise HTTPException(status_code=422, detail="请先完成一次排考，再处理教师调度需求")
            if unknown_teachers or unknown_sessions or not teacher_ids or not session_ids:
                if not teacher_ids:
                    message = "我还不能唯一确定您说的是哪位教师，请补充教师姓名、工号或部门。"
                elif not session_ids:
                    message = f"我已识别到 {len(teacher_ids)} 名教师，请再告诉我具体日期、月份、星期或考试场次。"
                else:
                    message = "教师或场次没有对应到当前排考数据，请补充更准确的教师工号、日期或场次。"
                return {"intent": "replan", "clarification_required": True, "message": message, "answer": message, "teacher_ids": teacher_ids, "affected_session_ids": session_ids, "reason": "", "policy": current}
            return {
                "intent": "replan",
                "clarification_required": False,
                "message": "",
                "answer": parsed.get("answer") or "已识别到教师调度需求，请确认受影响场次后重新排考。",
                "teacher_ids": sorted(set(teacher_ids)),
                "affected_session_ids": sorted(set(session_ids)),
                "reason": parsed.get("reason", "教师临时不可用"),
                "policy": current,
            }
        raw_policy = parsed.get("policy", {})
        if not isinstance(raw_policy, dict):
            raise ValueError("policy 必须是对象")
        merged = dict(current)
        allowed = policy_fields
        merged.update({key: raw_policy[key] for key in allowed if key in raw_policy})
        policy = normalise_policy(merged)
        errors = validate_policy_references(dataset["sessions"], dataset["teacher_df"], policy)
        if errors:
            raise HTTPException(status_code=422, detail="；".join(errors))
        return {"intent": "policy", "clarification_required": False, "message": "", "answer": "", "policy": policy}
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Agnes返回的规则格式无效：{exc}") from exc


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
        schedule_id = next(
            (key for key, _ in sorted(SCHEDULES.items(), key=lambda pair: pair[1]["created_at"], reverse=True)),
            None,
        )
        return {"role": "admin", "display_name": "排考老师", "schedule_id": schedule_id}
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


@app.post("/api/v1/ai/parse-policy", dependencies=[Depends(require_api_key)])
def parse_ai_policy(request: AiPolicyRequest):
    return _agnes_policy(_dataset(request.dataset_id), request.instruction, request.current_policy, request.schedule_id, request.conversation)


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
    unavailable_by_session = {
        str(session_id): [str(teacher_id) for teacher_id in teacher_ids]
        for session_id, teacher_ids in request.unavailable_by_session.items()
    }
    affected = set(map(str, request.affected_session_ids))
    affected.update(unavailable_by_session)
    for session_id, result in previous["results"].items():
        if any(teacher[0] in absent for source in (result["assignments"], result.get("backups", {})) for items in source.values() for teacher in items):
            affected.add(str(session_id))
    all_sessions = {str(session["session_id"]) for session in dataset["sessions"]}
    unknown = affected - all_sessions
    if unknown:
        raise HTTPException(status_code=422, detail=f"未知受影响场次: {', '.join(sorted(unknown))}")
    all_teachers = {str(value) for value in dataset["teacher_df"]["id_col"].tolist()}
    unknown_scoped = {
        teacher_id for teacher_ids in unavailable_by_session.values() for teacher_id in teacher_ids
    } - all_teachers
    if unknown_scoped:
        raise HTTPException(status_code=422, detail=f"未知调度教师: {', '.join(sorted(unknown_scoped))}")
    policy_data = request.policy.model_dump()
    scoped_unavailable = dict(policy_data.get("unavailable", {}))
    for session_id, teacher_ids in unavailable_by_session.items():
        for teacher_id in teacher_ids:
            scoped_unavailable.setdefault(teacher_id, []).append(session_id)
    policy_data["unavailable"] = scoped_unavailable
    try:
        replanned, metrics = optimise_exam_sessions(
            dataset["sessions"], dataset["teacher_df"], policy_data,
            previous_results=previous["results"], unavailable_teacher_ids=absent,
            locked_session_ids=all_sessions - affected,
        )
        workload = build_workload_stats(replanned, _teacher_records(dataset["teacher_df"]))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    schedule_id = uuid4().hex
    changes = _changed_assignments(previous["results"], replanned)
    if not request.preview:
        SCHEDULES[schedule_id] = {
            "dataset_id": previous["dataset_id"], "created_at": time.time(), "results": replanned,
            "baseline": previous["baseline"], "policy": policy_data, "metrics": metrics,
        }
    return {
        "schedule_id": request.schedule_id if request.preview else schedule_id, "affected_session_ids": sorted(affected),
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
