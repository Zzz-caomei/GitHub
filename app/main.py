from __future__ import annotations

import ast
import base64
import hashlib
import html
import json
import hmac
import os
import re
import secrets
import sqlite3
import time
import uuid
import zipfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw, ImageFont

BASE = Path(os.environ.get("SHOT_PLATFORM_HOME", "/opt/shot-analysis-platform"))
APP_DIR = Path(__file__).resolve().parent
DATA = BASE / "data"
UPLOADS = DATA / "uploads"
FRAMES = DATA / "frames"
SHEETS = DATA / "contact_sheets"
REPORTS = DATA / "reports"
EXPORTS = DATA / "exports"
KB = BASE / "knowledge_base" / "08_视频生成反推案例库"
LOGS = BASE / "logs"
DB_PATH = DATA / "database.sqlite"
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "300"))
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "false").lower() in {"1", "true", "yes", "on"}

for folder in (UPLOADS, FRAMES, SHEETS, REPORTS, EXPORTS, KB, LOGS):
    folder.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Shot Analysis Platform", version="0.7.0")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
security = HTTPBasic(auto_error=False)

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def media_type_for_name(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return "video"


def password_hash(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return salt, digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    _, actual_hash = password_hash(password, salt)
    return hmac.compare_digest(actual_hash, expected_hash)


def split_projects(value: str) -> list[str]:
    projects: list[str] = []
    for item in re.split(r"[,，\n]", value or ""):
        item = item.strip()
        if item and item not in projects:
            projects.append(item)
    return projects


def is_admin(user: dict[str, Any]) -> bool:
    return user.get("role") == "admin"


def can_access_project(user: dict[str, Any], project_name: str) -> bool:
    if is_admin(user):
        return True
    project_key = safe_stem(project_name or "未分组项目")
    for allowed in split_projects(user.get("allowed_projects", "")):
        if project_name == allowed or project_key == safe_stem(allowed):
            return True
    return False


def can_access_task(user: dict[str, Any], task: dict[str, Any] | None) -> bool:
    if not task:
        return False
    return can_access_project(user, task.get("project_name") or "未分组项目")


def get_app_user(username: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM app_users WHERE username=? AND is_active=1", (username,)).fetchone()
    return row_to_dict(row)


def auth_user(username: str, password: str) -> dict[str, Any] | None:
    if secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD):
        return {"username": ADMIN_USER, "role": "admin", "allowed_projects": "*", "is_env_admin": True}
    user = get_app_user(username)
    if user and verify_password(password, user.get("password_salt", ""), user.get("password_hash", "")):
        return user
    return None


def session_secret() -> str:
    return hashlib.sha256((ADMIN_PASSWORD + str(DB_PATH)).encode("utf-8")).hexdigest()


def sign_session(username: str, expires: int) -> str:
    message = f"{username}|{expires}"
    sig = hmac.new(session_secret().encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{message}|{sig}"


def read_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    parts = token.split("|")
    if len(parts) != 3:
        return None
    username, expires_text, sig = parts
    try:
        expires = int(expires_text)
    except ValueError:
        return None
    if expires < int(time.time()):
        return None
    expected = hmac.new(session_secret().encode("utf-8"), f"{username}|{expires}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if username == ADMIN_USER:
        return {"username": ADMIN_USER, "role": "admin", "allowed_projects": "*", "is_env_admin": True}
    return get_app_user(username)


def require_auth(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)) -> dict[str, Any]:
    if AUTH_DISABLED:
        return {"username": "department", "role": "admin", "allowed_projects": "*", "is_internal_tool": True}
    user = read_session(request.cookies.get("shot_session"))
    if user:
        return user
    if credentials:
        user = auth_user(credentials.username, credentials.password)
        if user:
            return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


def require_admin(user: dict[str, Any]) -> None:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="只有管理员可以执行此操作")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_stem(name: str) -> str:
    stem = Path(name).stem.strip() or "video"
    allowed = []
    for ch in stem:
        if ch.isalnum() or ch in "-_ .()[]中文短剧镜头参考案例客户项目标签":
            allowed.append(ch)
    cleaned = "".join(allowed).strip().replace(" ", "_")
    return cleaned[:80] or "video"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    additions = {
        "client_name": "TEXT NOT NULL DEFAULT ''",
        "project_name": "TEXT NOT NULL DEFAULT ''",
        "tags": "TEXT NOT NULL DEFAULT ''",
        "kb_path": "TEXT NOT NULL DEFAULT ''",
        "ai_status": "TEXT NOT NULL DEFAULT 'not_started'",
        "ai_analysis_path": "TEXT NOT NULL DEFAULT ''",
        "ai_error": "TEXT NOT NULL DEFAULT ''",
    }
    for column, definition in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                client_name TEXT NOT NULL DEFAULT '',
                project_name TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                upload_path TEXT NOT NULL,
                analysis_goal TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                duration REAL NOT NULL DEFAULT 0,
                fps REAL NOT NULL DEFAULT 0,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                frame_count INTEGER NOT NULL DEFAULT 0,
                segments_json TEXT NOT NULL DEFAULT '',
                sheet_path TEXT NOT NULL DEFAULT '',
                report_path TEXT NOT NULL DEFAULT '',
                kb_path TEXT NOT NULL DEFAULT '',
                ai_status TEXT NOT NULL DEFAULT 'not_started',
                ai_analysis_path TEXT NOT NULL DEFAULT '',
                ai_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                username TEXT PRIMARY KEY,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'customer',
                allowed_projects TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(conn)
        conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def create_task(task: dict[str, Any]) -> None:
    keys = list(task.keys())
    placeholders = ", ".join("?" for _ in keys)
    with connect() as conn:
        conn.execute(
            f"INSERT INTO tasks ({', '.join(keys)}) VALUES ({placeholders})",
            [task[k] for k in keys],
        )
        conn.commit()


def update_task(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_text()
    keys = list(fields.keys())
    sets = ", ".join(f"{k}=?" for k in keys)
    with connect() as conn:
        conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", [fields[k] for k in keys] + [task_id])
        conn.commit()


def get_task(task_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return row_to_dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def list_tasks(user: dict[str, Any], q: str = "", status_filter: str = "", project: str = "") -> list[dict[str, Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    if q:
        clauses.append("(title LIKE ? OR original_name LIKE ? OR analysis_goal LIKE ? OR tags LIKE ? OR client_name LIKE ?)")
        needle = f"%{q}%"
        values.extend([needle, needle, needle, needle, needle])
    if status_filter:
        clauses.append("status = ?")
        values.append(status_filter)
    if project:
        clauses.append("project_name = ?")
        values.append(project)
    if not is_admin(user):
        allowed = split_projects(user.get("allowed_projects", ""))
        if not allowed:
            return []
        clauses.append("project_name IN (" + ",".join("?" for _ in allowed) + ")")
        values.extend(allowed)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT 120", values).fetchall()
    return [dict(row) for row in rows]


def task_stats(user: dict[str, Any]) -> dict[str, Any]:
    allowed = split_projects(user.get("allowed_projects", ""))
    suffix = ""
    values: list[Any] = []
    if not is_admin(user):
        if not allowed:
            return {"total": 0, "done": 0, "processing": 0, "projects": 0}
        suffix = " WHERE project_name IN (" + ",".join("?" for _ in allowed) + ")"
        values = allowed
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM tasks" + suffix, values).fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM tasks" + suffix + (" AND" if suffix else " WHERE") + " status='done'", values).fetchone()[0]
        processing = conn.execute("SELECT COUNT(*) FROM tasks" + suffix + (" AND" if suffix else " WHERE") + " status IN ('queued','processing')", values).fetchone()[0]
        projects = conn.execute("SELECT COUNT(DISTINCT project_name) FROM tasks" + suffix + (" AND" if suffix else " WHERE") + " project_name != ''", values).fetchone()[0]
    return {"total": total, "done": done, "processing": processing, "projects": projects}


def list_projects(user: dict[str, Any]) -> list[str]:
    if not is_admin(user):
        return split_projects(user.get("allowed_projects", ""))
    with connect() as conn:
        rows = conn.execute("SELECT DISTINCT project_name FROM tasks WHERE project_name != '' ORDER BY project_name").fetchall()
    return [row[0] for row in rows]


def list_users() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT username, role, allowed_projects, is_active, created_at, updated_at FROM app_users ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def upsert_user(username: str, password: str, role: str, allowed_projects: str, is_active: bool = True) -> None:
    username = username.strip()
    if not username:
        raise ValueError("用户名不能为空")
    if role not in {"admin", "customer"}:
        role = "customer"
    now = now_text()
    with connect() as conn:
        exists = conn.execute("SELECT username FROM app_users WHERE username=?", (username,)).fetchone()
        if exists:
            if password:
                salt, digest = password_hash(password)
                conn.execute(
                    "UPDATE app_users SET password_salt=?, password_hash=?, role=?, allowed_projects=?, is_active=?, updated_at=? WHERE username=?",
                    (salt, digest, role, allowed_projects.strip(), 1 if is_active else 0, now, username),
                )
            else:
                conn.execute(
                    "UPDATE app_users SET role=?, allowed_projects=?, is_active=?, updated_at=? WHERE username=?",
                    (role, allowed_projects.strip(), 1 if is_active else 0, now, username),
                )
        else:
            if not password:
                raise ValueError("新用户必须设置密码")
            salt, digest = password_hash(password)
            conn.execute(
                "INSERT INTO app_users (username, password_salt, password_hash, role, allowed_projects, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (username, salt, digest, role, allowed_projects.strip(), 1 if is_active else 0, now, now),
            )
        conn.commit()


def read_video_info(path: Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("无法读取视频文件，请确认格式为 mp4/mov/webm 等常见格式。")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = frame_count / fps if fps else 0
    cap.release()
    return {"fps": fps, "frame_count": frame_count, "width": width, "height": height, "duration": duration}


def sample_frames(video_path: Path, task_id: str, max_frames: int = 32) -> list[Path]:
    out_dir = FRAMES / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()

    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        total = 1
    indices = np.linspace(0, max(total - 1, 0), num=min(max_frames, total), dtype=int)
    paths: list[Path] = []
    for idx, frame_no in enumerate(indices, start=1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_no))
        ok, frame = cap.read()
        if not ok:
            continue
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(frame)
        image.thumbnail((640, 360), Image.LANCZOS)
        out = out_dir / f"frame_{idx:02d}_{int(frame_no):06d}.jpg"
        image.save(out, quality=88)
        paths.append(out)
    cap.release()
    return paths


def make_contact_sheet(frame_paths: list[Path], task_id: str, title: str) -> Path:
    if not frame_paths:
        raise RuntimeError("抽帧失败：未生成任何关键帧。")
    thumbs: list[Image.Image] = []
    for path in frame_paths:
        img = Image.open(path).convert("RGB")
        img.thumbnail((300, 169), Image.LANCZOS)
        canvas = Image.new("RGB", (300, 169), (24, 24, 24))
        canvas.paste(img, ((300 - img.width) // 2, (169 - img.height) // 2))
        thumbs.append(canvas)

    cols = 4
    rows = int(np.ceil(len(thumbs) / cols))
    header = 76
    gap = 10
    card_h = 198
    width = cols * 300 + (cols + 1) * gap
    height = header + rows * card_h + (rows + 1) * gap
    sheet = Image.new("RGB", (width, height), (15, 18, 24))
    draw = ImageDraw.Draw(sheet)
    try:
        font_path = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
        if not Path(font_path).exists():
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font_title = ImageFont.truetype(font_path, 25)
        font_small = ImageFont.truetype(font_path, 14)
    except Exception:
        font_title = font_small = ImageFont.load_default()
    draw.text((18, 18), "素材关键帧预览", fill=(240, 226, 198), font=font_title)
    draw.text((18, 50), f"已抽取 {len(frame_paths)} 张关键帧，用于镜头与运镜判断", fill=(147, 166, 188), font=font_small)

    for i, thumb in enumerate(thumbs):
        row, col = divmod(i, cols)
        x = gap + col * (300 + gap)
        y = header + gap + row * card_h
        sheet.paste(thumb, (x, y))
        draw.text((x + 8, y + 176), f"#{i + 1:02d}  frame {frame_paths[i].stem.split('_')[-1]}", fill=(212, 218, 228), font=font_small)

    out = SHEETS / f"{task_id}.jpg"
    sheet.save(out, quality=90)
    return out


def infer_segments(video_path: Path, max_segments: int = 18) -> list[dict[str, Any]]:
    cap = cv2.VideoCapture(str(video_path))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total / fps if total else 0
    if duration <= 0:
        cap.release()
        return []

    step = max(int(fps * 0.5), 1)
    last_gray = None
    scores: list[tuple[float, float]] = []
    frame_no = 0
    while frame_no < total:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, frame = cap.read()
        if not ok:
            break
        small = cv2.resize(frame, (160, 90))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if last_gray is not None:
            diff = float(cv2.absdiff(gray, last_gray).mean())
            scores.append((frame_no / fps, diff))
        last_gray = gray
        frame_no += step
    cap.release()

    if not scores:
        return [{"start": 0.0, "end": round(duration, 2), "label": "完整镜头", "note": "视频较短或变化较少"}]

    values = np.array([s for _, s in scores])
    threshold = float(values.mean() + values.std() * 1.15)
    cuts = [t for t, score in scores if score >= threshold]
    filtered: list[float] = []
    for cut in cuts:
        if not filtered or cut - filtered[-1] >= 1.0:
            filtered.append(float(cut))
    filtered = filtered[: max_segments - 1]

    points = [0.0] + filtered + [duration]
    segments = []
    for i in range(len(points) - 1):
        start = points[i]
        end = points[i + 1]
        if end - start < 0.35:
            continue
        segments.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "label": f"镜头 {len(segments) + 1}",
                "note": "基于画面变化自动切分，需要人工复核镜头语言。",
            }
        )
    return segments or [{"start": 0.0, "end": round(duration, 2), "label": "完整镜头", "note": "未检测到明显转场"}]


def make_ai_prompt(task: dict[str, Any] | None) -> str:
    if not task:
        return ""
    media_label = "参考图片" if media_type_for_name(task.get("original_name", "")) == "image" else "参考视频"
    first_item = "1. 如果是视频，按时间线拆分镜头；如果是图片，拆解主体、构图、机位、景深和画面层次。"
    return "\n".join(
        [
            "你是短剧导演、摄影指导和 AI 视频提示词工程师。",
            f"请分析这个{media_label}：{task['title']}",
            f"归档/项目：{task.get('client_name') or '未填写'} / {task.get('project_name') or '未填写'}",
            f"目标：{task.get('analysis_goal') or '提取镜头语言并转成生成视频关键词'}",
            "请输出：",
            first_item,
            "2. 写明镜头效果：景别、机位高度、焦段感、构图、主体调度、景深、质感。",
            "3. 写明运镜方式：推、拉、摇、移、跟拍、环绕、升降、手持、变焦、转场。",
            "4. 写明光线、色彩、速度、动态模糊、情绪节奏和画面氛围。",
            "5. 输出 8 秒生成视频分镜脚本，每秒或每 2 秒一个镜头节点。",
            "6. 输出正向关键词、负面关键词和可直接复制的视频生成关键词。",
        ]
    )


def clean_analysis_goal(raw: str) -> str:
    if not raw:
        return "自动提取镜头效果、运镜、光线、节奏和可复用关键词。"
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("输出类型："):
            continue
        if stripped.startswith("补充要求："):
            stripped = stripped.replace("补充要求：", "", 1).strip()
        if stripped:
            lines.append(stripped)
    return "；".join(lines) or "自动提取镜头效果、运镜、光线、节奏和可复用关键词。"


def strip_display_heading(text: str) -> str:
    lines = text.strip().splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lines.pop(0)
    return "\n".join(lines).strip()


def read_ai_keyword_text(task: dict[str, Any]) -> str:
    path_text = task.get("ai_analysis_path") or ""
    if not path_text:
        return ""
    path = Path(path_text)
    if not path.exists():
        return ""
    return strip_display_heading(path.read_text(encoding="utf-8"))


def friendly_ai_error(error: str) -> str:
    if not error:
        return "未知错误"
    if "令牌已过期" in error or "token" in error.lower() and "expired" in error.lower():
        return "当前中转 API 令牌已过期，需要更新 API Key 后才能生成真实关键词。"
    if "HTTP 401" in error:
        return "当前 API 鉴权失败，需要检查 API Key 或中转 API 配置。"
    return error[:260]


def make_direct_keywords(task: dict[str, Any] | None) -> str:
    if not task:
        return ""
    ai_text = read_ai_keyword_text(task)
    if ai_text:
        return ai_text

    status_value = task.get("status") or "queued"
    ai_status = task.get("ai_status") or "not_started"
    if ai_status == "processing":
        return "AI 正在根据关键帧反推视频生成关键词...\n\n完成后这里会直接显示可复制的真实关键词。"
    if status_value in {"queued", "processing"}:
        return "正在抽取素材关键帧...\n\n完成后会继续调用 AI 反推视频生成关键词。"
    if status_value == "failed":
        return "关键词生成失败。请重新上传素材，或换成更小的 mp4 / mov / jpg / png 文件。"
    if ai_status == "failed":
        return f"AI 关键词生成失败：{friendly_ai_error(task.get('ai_error') or '')}\n\n请更新 API Key 后重新上传素材，系统才会根据关键帧生成真实关键词。"

    return "素材基础分析已完成，正在等待 AI 生成关键词。请稍后刷新页面。"



def task_status_label(task: dict[str, Any] | None) -> str:
    if not task:
        return "未开始"
    status_value = task.get("status") or "queued"
    ai_status = task.get("ai_status") or "not_started"
    if status_value == "failed":
        return "分析失败"
    if status_value == "done" and ai_status == "failed":
        return "待更新 API"
    if status_value == "done":
        return "分析完成"
    if status_value == "processing":
        return "处理中"
    return "等待中"


def task_display_title(task: dict[str, Any] | None) -> str:
    if not task:
        return "完整报告"
    status_value = task.get("status") or "queued"
    ai_status = task.get("ai_status") or "not_started"
    if status_value == "failed":
        return "素材分析失败"
    if status_value == "done" and ai_status == "failed":
        return "关键词待重新生成"
    if status_value == "done":
        return "素材分析完成"
    if status_value == "processing":
        progress = int(task.get("progress") or 0)
        if progress >= 90:
            return "正在生成关键词"
        return "正在拆解素材"
    return "等待开始分析"


def task_display_lead(task: dict[str, Any] | None) -> str:
    if not task:
        return "完整报告"
    media_label = "图片素材" if media_type_for_name(task.get("original_name", "")) == "image" else "视频素材"
    goal = clean_analysis_goal(task.get("analysis_goal") or "")
    parts = [media_label, "完整报告"]
    if goal:
        parts.append(goal)
    return " · ".join(parts[:3])


def clean_task_report_text(task: dict[str, Any], markdown_text: str) -> str:
    """Hide raw upload filenames from the browser-facing report preview."""
    cleaned = markdown_text
    replacements = [
        (task.get("original_name") or "", "已上传素材"),
        (task.get("title") or "", "当前素材"),
    ]
    for source, target in replacements:
        if source:
            cleaned = cleaned.replace(source, target)
    cleaned = cleaned.replace("# 当前素材 - 素材关键词拆解草稿", "# 素材关键词拆解报告")
    return cleaned



def task_record_title(task: dict[str, Any] | None) -> str:
    if not task:
        return "素材记录"
    media_label = "图片素材" if media_type_for_name(task.get("original_name", "")) == "image" else "视频素材"
    status_value = task.get("status") or "queued"
    ai_status = task.get("ai_status") or "not_started"
    if status_value == "failed":
        return f"{media_label} · 分析失败"
    if status_value == "done" and ai_status == "failed":
        return f"{media_label} · 待更新 API"
    if status_value == "done":
        return f"{media_label} · 分析完成"
    if status_value == "processing":
        return f"{media_label} · 正在分析"
    return f"{media_label} · 等待处理"


def task_record_meta(task: dict[str, Any] | None) -> str:
    if not task:
        return ""
    bits: list[str] = []
    duration = float(task.get("duration") or 0)
    width = int(task.get("width") or 0)
    height = int(task.get("height") or 0)
    if duration > 0:
        bits.append(f"{duration:.2f}s")
    if width and height:
        bits.append(f"{width}x{height}")
    created = (task.get("created_at") or "").strip()
    if created:
        bits.append(created[:16])
    project = (task.get("project_name") or "").strip()
    if project and project != "未分组项目":
        bits.append(project)
    return " · ".join(bits) or "已上传素材"


def with_task_display(task: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(task)
    enriched["record_title"] = task_record_title(task)
    enriched["record_meta"] = task_record_meta(task)
    enriched["status_label"] = task_status_label(task)
    return enriched


def generate_report(task: dict[str, Any], info: dict[str, Any], frame_paths: list[Path], sheet_path: Path, segments: list[dict[str, Any]]) -> tuple[Path, Path]:
    report = REPORTS / f"{task['id']}.md"
    project_part = safe_stem(task.get("project_name") or "未分组项目")
    kb_dir = KB / project_part
    kb_dir.mkdir(parents=True, exist_ok=True)
    kb_report = kb_dir / f"{task['created_at'][:10]}_{safe_stem(task['title'])}_{task['id'][:8]}.md"
    prompt = make_ai_prompt(task)
    lines: list[str] = []
    lines.append("# 素材关键词拆解报告")
    lines.append("")
    lines.append("## 基础信息")
    lines.append("")
    lines.append(f"- 归档：{task.get('client_name') or '未填写'}")
    lines.append(f"- 项目：{task.get('project_name') or '未分组项目'}")
    lines.append(f"- 标签：{task.get('tags') or '未填写'}")
    lines.append("- 原始素材：已上传素材")
    lines.append(f"- 分析目标：{task['analysis_goal'] or '未填写'}")
    lines.append(f"- 时长：{info['duration']:.2f}s")
    lines.append(f"- 分辨率：{info['width']}x{info['height']}")
    lines.append(f"- FPS：{info['fps']:.2f}")
    lines.append(f"- 关键帧数量：{len(frame_paths)}")
    lines.append(f"- 联系表：`{sheet_path}`")
    lines.append("")
    lines.append("## 自动镜头切分")
    lines.append("")
    lines.append("| 时间段 | 镜头 | 初步判断 |")
    lines.append("|---|---|---|")
    for seg in segments:
        lines.append(f"| {seg['start']:.2f}s - {seg['end']:.2f}s | {seg['label']} | {seg['note']} |")
    lines.append("")
    lines.append("## 给 AI 的深度分析提示词")
    lines.append("")
    lines.append("```text")
    lines.append(prompt)
    lines.append("```")
    lines.append("")
    lines.append("## 可复用关键词草稿")
    lines.append("")
    lines.append("- 镜头风格：电影感、短剧预告、强节奏剪辑、情绪化光影")
    lines.append("- 运镜关键词：高速跟拍、低机位推进、轻微手持、动态模糊、景深分离")
    lines.append("- 负面关键词：画面糊脸、肢体畸形、透视错误、过度锐化、廉价塑料质感、文字乱码")
    lines.append("")
    content = "\n".join(lines) + "\n"
    report.write_text(content, encoding="utf-8")
    kb_report.write_text(content, encoding="utf-8")
    return report, kb_report


def parse_segments(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        try:
            data = ast.literal_eval(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []


def markdown_to_html(text: str) -> str:
    result: list[str] = []
    in_code = False
    in_table = False
    for line in text.splitlines():
        raw = line.rstrip()
        if raw.startswith("```"):
            if in_code:
                result.append("</code></pre>")
                in_code = False
            else:
                result.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            result.append(html.escape(raw) + "\n")
            continue
        if raw.startswith("|") and raw.endswith("|"):
            cells = [html.escape(cell.strip()) for cell in raw.strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            if not in_table:
                result.append("<table>")
                in_table = True
            result.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
            continue
        if in_table:
            result.append("</table>")
            in_table = False
        if raw.startswith("# "):
            result.append(f"<h1>{html.escape(raw[2:])}</h1>")
        elif raw.startswith("## "):
            result.append(f"<h2>{html.escape(raw[3:])}</h2>")
        elif raw.startswith("- "):
            result.append(f"<p class='bullet'>• {html.escape(raw[2:])}</p>")
        elif re.match(r"^\d+\. ", raw):
            result.append(f"<p class='bullet'>{html.escape(raw)}</p>")
        elif raw:
            result.append(f"<p>{html.escape(raw)}</p>")
        else:
            result.append("<br>")
    if in_table:
        result.append("</table>")
    if in_code:
        result.append("</code></pre>")
    return "\n".join(result)


def list_knowledge_files(user: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(KB.glob("**/*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        rel = path.relative_to(KB)
        project_name = rel.parts[0] if len(rel.parts) > 1 else "未分组项目"
        if not can_access_project(user, project_name):
            continue
        files.append(
            {
                "name": path.stem,
                "project": project_name,
                "path": str(path),
                "rel": str(rel),
                "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return files[:120]


def ai_is_configured() -> bool:
    return bool(OPENAI_API_KEY)


def openai_responses_url() -> str:
    base = OPENAI_BASE_URL or "https://api.openai.com/v1"
    if base.endswith("/responses"):
        return base
    if base.endswith("/v1"):
        return f"{base}/responses"
    return f"{base}/v1/responses"


def image_data_url(path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def extract_response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    parts: list[str] = []
    for item in payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            text = content.get("text") or content.get("output_text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def call_openai_vision(task: dict[str, Any]) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 未配置，无法自动 AI 分析。")
    if not task.get("sheet_path") or not Path(task["sheet_path"]).exists():
        raise RuntimeError("关键帧联系表不存在，无法进行视觉分析。")
    user_goal = clean_analysis_goal(task.get("analysis_goal", ""))
    media_label = "参考图片" if media_type_for_name(task.get("original_name", "")) == "image" else "参考视频"
    text_prompt = "\n".join(
        [
            "你是专业短剧导演、摄影指导和 AI 视频生成提示词工程师。",
            f"请观察这张从{media_label}抽取的关键帧联系表，反推出可直接用于视频生成工具的中文关键词。",
            "不要输出分析过程，不要写‘请分析’，不要写教程说明，只输出可复制的生成关键词。",
            f"用户重点要求：{user_goal}",
            "输出格式必须严格包含以下栏目：",
            "【镜头效果】写景别、机位、焦段感、构图、主体调度、景深、质感。",
            "【运镜方式】写推、拉、摇、移、跟拍、环绕、升降、手持、变焦、转场、速度变化。",
            "【光线与画面】写光线方向、冷暖关系、色彩、动态模糊、画面氛围。",
            "【节奏】写适合 8 秒视频的 0-2 秒、2-5 秒、5-8 秒节奏。",
            "【正向关键词】写一整段可直接放入视频生成器的关键词。",
            "【负面关键词】写需要避免的问题。",
            "【最终关键词】写一段完整、可直接复制的视频生成关键词，适合把同样镜头风格套用到新人物或产品上。",
        ]
    )
    body = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": text_prompt},
                    {"type": "input_image", "image_url": image_data_url(Path(task["sheet_path"])), "detail": "high"},
                ],
            }
        ],
        "max_output_tokens": 3500,
    }
    request = urllib.request.Request(
        openai_responses_url(),
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"OpenAI API 请求失败：HTTP {exc.code} {detail}") from exc
    text = extract_response_text(data)
    if not text:
        raise RuntimeError("OpenAI API 返回为空，未生成分析文本。")
    return text


def save_ai_analysis(task: dict[str, Any], text: str) -> Path:
    path = REPORTS / f"{task['id']}_ai_analysis.md"
    content = f"# {task['title']} - AI 视频生成关键词\n\n" + text.strip() + "\n"
    path.write_text(content, encoding="utf-8")
    kb_project = safe_stem(task.get("project_name") or "未分组项目")
    kb_dir = KB / kb_project
    kb_dir.mkdir(parents=True, exist_ok=True)
    kb_path = kb_dir / f"{task.get('created_at', now_text())[:10]}_{safe_stem(task['title'])}_{task['id'][:8]}_AI分析.md"
    kb_path.write_text(content, encoding="utf-8")
    return path


def run_ai_analysis(task_id: str) -> None:
    task = get_task(task_id)
    if not task:
        return
    try:
        update_task(task_id, ai_status="processing", ai_error="")
        text = call_openai_vision(task)
        path = save_ai_analysis(task, text)
        update_task(task_id, ai_status="done", ai_analysis_path=str(path), ai_error="")
    except Exception as exc:
        update_task(task_id, ai_status="failed", ai_error=str(exc))
        with (LOGS / "errors.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{now_text()}] AI {task_id}: {exc}\n")


def build_export_zip(task: dict[str, Any]) -> Path:
    export_path = EXPORTS / f"{task['id']}_shot_package.zip"
    metadata = {
        "id": task["id"],
        "title": task["title"],
        "client_name": task.get("client_name", ""),
        "project_name": task.get("project_name", ""),
        "tags": task.get("tags", ""),
        "original_name": task.get("original_name", ""),
        "analysis_goal": task.get("analysis_goal", ""),
        "duration": task.get("duration", 0),
        "fps": task.get("fps", 0),
        "width": task.get("width", 0),
        "height": task.get("height", 0),
        "frame_count": task.get("frame_count", 0),
        "segments": parse_segments(task.get("segments_json", "")),
        "created_at": task.get("created_at", ""),
        "updated_at": task.get("updated_at", ""),
    }
    with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        zf.writestr("ai_analysis_prompt.txt", make_ai_prompt(task))
        if task.get("report_path") and Path(task["report_path"]).exists():
            zf.write(task["report_path"], arcname="shot_analysis_report.md")
        if task.get("ai_analysis_path") and Path(task["ai_analysis_path"]).exists():
            zf.write(task["ai_analysis_path"], arcname="ai_shot_language_analysis.md")
        if task.get("sheet_path") and Path(task["sheet_path"]).exists():
            zf.write(task["sheet_path"], arcname="contact_sheet.jpg")
        frame_dir = FRAMES / task["id"]
        if frame_dir.exists():
            for frame in sorted(frame_dir.glob("*.jpg")):
                zf.write(frame, arcname=f"sampled_frames/{frame.name}")
    return export_path



def process_image(task_id: str) -> None:
    task = get_task(task_id)
    if not task:
        return
    try:
        update_task(task_id, status="processing", progress=12)
        image_path = Path(task["upload_path"])
        out_dir = FRAMES / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.jpg"):
            old.unlink()
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            width, height = img.size
            preview = img.copy()
            preview.thumbnail((960, 540), Image.LANCZOS)
            frame_path = out_dir / "frame_01_image.jpg"
            preview.save(frame_path, quality=90)
        info = {"fps": 0, "frame_count": 1, "width": width, "height": height, "duration": 0}
        update_task(task_id, progress=45, **info)
        sheet_path = make_contact_sheet([frame_path], task_id, task["title"])
        update_task(task_id, progress=70, sheet_path=str(sheet_path))
        segments = [
            {
                "start": 0.0,
                "end": 0.0,
                "label": "单张参考图",
                "note": "围绕主体、构图、光线、色彩、景深和画面氛围提炼关键词。",
            }
        ]
        update_task(task_id, progress=86, segments_json=json.dumps(segments, ensure_ascii=False))
        task = get_task(task_id) or task
        report_path, kb_path = generate_report(task, info, [frame_path], sheet_path, segments)
        update_task(task_id, progress=92, report_path=str(report_path), kb_path=str(kb_path), error="")
        if ai_is_configured():
            run_ai_analysis(task_id)
        update_task(task_id, status="done", progress=100, error="")
    except Exception as exc:
        update_task(task_id, status="failed", error=str(exc), progress=100)
        with (LOGS / "errors.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{now_text()}] {task_id}: {exc}\n")


def process_media(task_id: str) -> None:
    task = get_task(task_id)
    if not task:
        return
    if media_type_for_name(task.get("original_name", "")) == "image":
        process_image(task_id)
    else:
        process_video(task_id)

def process_video(task_id: str) -> None:
    task = get_task(task_id)
    if not task:
        return
    try:
        update_task(task_id, status="processing", progress=8)
        video_path = Path(task["upload_path"])
        info = read_video_info(video_path)
        update_task(task_id, progress=25, **info)
        frame_paths = sample_frames(video_path, task_id)
        update_task(task_id, progress=52)
        sheet_path = make_contact_sheet(frame_paths, task_id, task["title"])
        update_task(task_id, progress=70, sheet_path=str(sheet_path))
        segments = infer_segments(video_path)
        update_task(task_id, progress=84, segments_json=json.dumps(segments, ensure_ascii=False))
        task = get_task(task_id) or task
        report_path, kb_path = generate_report(task, info, frame_paths, sheet_path, segments)
        update_task(task_id, progress=92, report_path=str(report_path), kb_path=str(kb_path), error="")
        if ai_is_configured():
            run_ai_analysis(task_id)
        update_task(task_id, status="done", progress=100, error="")
    except Exception as exc:
        update_task(task_id, status="failed", error=str(exc), progress=100)
        with (LOGS / "errors.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{now_text()}] {task_id}: {exc}\n")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if AUTH_DISABLED:
        return RedirectResponse(url="/", status_code=303)
    user = read_session(request.cookies.get("shot_session"))
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")):
    if AUTH_DISABLED:
        return RedirectResponse(url="/", status_code=303)
    init_db()
    user = auth_user(username.strip(), password)
    if not user:
        return templates.TemplateResponse(request, "login.html", {"error": "账号或密码不正确"}, status_code=401)
    expires = int(time.time()) + 60 * 60 * 24 * 7
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("shot_session", sign_session(user["username"], expires), max_age=60 * 60 * 24 * 7, httponly=True, samesite="lax")
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("shot_session")
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": "true", "service": "shot-analysis-platform", "version": "0.7.0", "mode": "internal" if AUTH_DISABLED else "portal", "auth_disabled": AUTH_DISABLED, "ai_configured": bool(OPENAI_API_KEY), "ai_model": OPENAI_MODEL, "ai_base_configured": bool(OPENAI_BASE_URL)}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, background_tasks: BackgroundTasks, user: dict[str, Any] = Depends(require_auth)) -> HTMLResponse:
    init_db()
    q = request.query_params.get("q", "").strip()
    status_filter = request.query_params.get("status", "").strip()
    project = request.query_params.get("project", "").strip()
    tasks = [with_task_display(task) for task in list_tasks(user, q, status_filter, project)]
    latest_task = tasks[0] if tasks else None
    if latest_task and latest_task.get("status") == "done" and latest_task.get("ai_status") == "not_started" and ai_is_configured() and latest_task.get("sheet_path"):
        update_task(latest_task["id"], ai_status="processing", ai_error="")
        latest_task = get_task(latest_task["id"]) or latest_task
        background_tasks.add_task(run_ai_analysis, latest_task["id"])
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "tasks": tasks,
            "latest_task": latest_task,
            "latest_prompt": make_direct_keywords(latest_task) if latest_task else "",
            "max_mb": MAX_UPLOAD_MB,
            "stats": task_stats(user),
            "projects": list_projects(user),
            "current_user": user,
            "is_admin": is_admin(user),
            "filters": {"q": q, "status": status_filter, "project": project},
        },
    )


@app.get("/library", response_class=HTMLResponse)
def library(request: Request, user: dict[str, Any] = Depends(require_auth)) -> HTMLResponse:
    init_db()
    return templates.TemplateResponse(request, "library.html", {"files": list_knowledge_files(user), "stats": task_stats(user), "current_user": user, "is_admin": is_admin(user)})


@app.post("/upload")
def upload(
    background_tasks: BackgroundTasks,
    title: str = Form(""),
    client_name: str = Form(""),
    project_name: str = Form(""),
    tags: str = Form(""),
    analysis_goal: str = Form(""),
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(require_auth),
):
    init_db()
    original = file.filename or "media.mp4"
    suffix = Path(original).suffix.lower() or ".mp4"
    if suffix not in VIDEO_SUFFIXES | IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="请上传常见视频或图片格式：mp4/mov/webm/avi/mkv/jpg/png/webp")

    allowed_projects = split_projects(user.get("allowed_projects", ""))
    if not is_admin(user):
        if not allowed_projects:
            raise HTTPException(status_code=403, detail="当前客户账号尚未绑定项目，无法上传")
        if not project_name.strip():
            project_name = allowed_projects[0]
        if project_name.strip() not in allowed_projects:
            raise HTTPException(status_code=403, detail="无权上传到该项目")
        if not client_name.strip():
            client_name = user.get("username", "")

    task_id = uuid.uuid4().hex
    digest = hashlib.sha1(f"{original}-{time.time()}".encode()).hexdigest()[:10]
    stored_name = f"{safe_stem(original)}_{digest}{suffix}"
    upload_path = UPLOADS / stored_name

    size = 0
    with upload_path.open("wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                upload_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_MB}MB 限制")
            out.write(chunk)

    created = now_text()
    create_task(
        {
            "id": task_id,
            "title": title.strip() or safe_stem(original),
            "client_name": client_name.strip(),
            "project_name": project_name.strip() or "未分组项目",
            "tags": tags.strip(),
            "original_name": original,
            "stored_name": stored_name,
            "upload_path": str(upload_path),
            "analysis_goal": analysis_goal.strip(),
            "status": "queued",
            "progress": 0,
            "error": "",
            "duration": 0,
            "fps": 0,
            "width": 0,
            "height": 0,
            "frame_count": 0,
            "segments_json": "",
            "sheet_path": "",
            "report_path": "",
            "kb_path": "",
            "ai_status": "not_started",
            "ai_analysis_path": "",
            "ai_error": "",
            "created_at": created,
            "updated_at": created,
        }
    )
    background_tasks.add_task(process_media, task_id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_page(task_id: str, request: Request, user: dict[str, Any] = Depends(require_auth)) -> HTMLResponse:
    task = get_task(task_id)
    if not task or not can_access_task(user, task):
        raise HTTPException(status_code=404, detail="任务不存在")
    report_html = ""
    ai_html = ""
    if task.get("report_path") and Path(task["report_path"]).exists():
        report_text = clean_task_report_text(task, Path(task["report_path"]).read_text(encoding="utf-8"))
        report_html = markdown_to_html(report_text)
    if task.get("ai_analysis_path") and Path(task["ai_analysis_path"]).exists():
        ai_text = clean_task_report_text(task, Path(task["ai_analysis_path"]).read_text(encoding="utf-8"))
        ai_html = markdown_to_html(ai_text)
    return templates.TemplateResponse(
        request,
        "task.html",
        {
            "task": task,
            "segments": parse_segments(task.get("segments_json", "")),
            "report_html": report_html,
            "ai_html": ai_html,
            "display_title": task_display_title(task),
            "display_lead": task_display_lead(task),
            "status_label": task_status_label(task),
            "keyword_text": make_direct_keywords(task),
            "ai_prompt": make_ai_prompt(task),
            "ai_configured": ai_is_configured(),
            "ai_model": OPENAI_MODEL,
            "current_user": user,
            "is_admin": is_admin(user),
        },
    )


@app.get("/tasks/{task_id}/status")
def task_status(task_id: str, user: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    task = get_task(task_id)
    if not task or not can_access_task(user, task):
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONResponse(task)


@app.get("/tasks/{task_id}/contact")
def contact_sheet(task_id: str, user: dict[str, Any] = Depends(require_auth)):
    task = get_task(task_id)
    if not task or not can_access_task(user, task) or not task.get("sheet_path"):
        raise HTTPException(status_code=404, detail="联系表未生成")
    path = Path(task["sheet_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="联系表文件不存在")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@app.get("/tasks/{task_id}/report")
def report(task_id: str, user: dict[str, Any] = Depends(require_auth)):
    task = get_task(task_id)
    if not task or not can_access_task(user, task) or not task.get("report_path"):
        raise HTTPException(status_code=404, detail="报告未生成")
    path = Path(task["report_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="报告文件不存在")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@app.get("/tasks/{task_id}/prompt")
def prompt(task_id: str, user: dict[str, Any] = Depends(require_auth)):
    task = get_task(task_id)
    if not task or not can_access_task(user, task):
        raise HTTPException(status_code=404, detail="任务不存在")
    return PlainTextResponse(make_direct_keywords(task), media_type="text/plain; charset=utf-8")


@app.get("/tasks/{task_id}/export")
def export_task(task_id: str, user: dict[str, Any] = Depends(require_auth)):
    task = get_task(task_id)
    if not task or not can_access_task(user, task):
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.get("status") != "done":
        raise HTTPException(status_code=400, detail="任务完成后才能导出素材包")
    export_path = build_export_zip(task)
    filename = f"{safe_stem(task['title'])}_{task['id'][:8]}_shot_package.zip"
    return FileResponse(export_path, media_type="application/zip", filename=filename)


@app.post("/tasks/{task_id}/ai-analysis")
def start_ai_analysis(task_id: str, background_tasks: BackgroundTasks, user: dict[str, Any] = Depends(require_auth)):
    require_admin(user)
    task = get_task(task_id)
    if not task or not can_access_task(user, task):
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.get("status") != "done":
        raise HTTPException(status_code=400, detail="任务完成后才能启动 AI 分析")
    if not ai_is_configured():
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY 未配置。请先在 /etc/shot-analysis-platform.env 配置后重启本服务。")
    if task.get("ai_status") == "processing":
        return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)
    background_tasks.add_task(run_ai_analysis, task_id)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)


@app.get("/tasks/{task_id}/ai-analysis")
def ai_analysis(task_id: str, user: dict[str, Any] = Depends(require_auth)):
    task = get_task(task_id)
    if not task or not can_access_task(user, task) or not task.get("ai_analysis_path"):
        raise HTTPException(status_code=404, detail="AI 分析尚未生成")
    path = Path(task["ai_analysis_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="AI 分析文件不存在")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, user: dict[str, Any] = Depends(require_auth)) -> HTMLResponse:
    if AUTH_DISABLED:
        raise HTTPException(status_code=404, detail="当前模式不启用账号管理")
    require_admin(user)
    init_db()
    return templates.TemplateResponse(
        request,
        "users.html",
        {"users": list_users(), "projects": list_projects(user), "current_user": user, "is_admin": True},
    )


@app.post("/users")
def save_user(
    username: str = Form(""),
    password: str = Form(""),
    role: str = Form("customer"),
    allowed_projects: str = Form(""),
    is_active: str = Form("1"),
    user: dict[str, Any] = Depends(require_auth),
):
    if AUTH_DISABLED:
        raise HTTPException(status_code=404, detail="当前模式不启用账号管理")
    require_admin(user)
    try:
        upsert_user(username, password, role, allowed_projects, is_active == "1")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/users", status_code=303)


@app.exception_handler(HTTPException)
def http_error(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        if AUTH_DISABLED:
            return RedirectResponse(url="/", status_code=303)
        accept = request.headers.get("accept", "")
        if "text/html" in accept or "*/*" in accept:
            return templates.TemplateResponse(request, "login.html", {"error": "请先登录"}, status_code=200)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    message = html.escape(str(exc.detail))
    return HTMLResponse(f"<h1>出错了</h1><p>{message}</p><p><a href='/'>返回首页</a></p>", status_code=exc.status_code)
