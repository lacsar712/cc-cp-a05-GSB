import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import judge_temp

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}

# 缺冷媒批次号的唯一挡回措辞：网页提交与绕过网页直连接口走同一个 handler，
# 都返回这一句，两条路径措辞一致，且整笔挡回（不落任何半行数据）。
BATCH_REQUIRED_DETAIL = "冷媒批次号为报温必填项，缺少批次，整笔退回"


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可提交读数"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def reading_dict(r) -> dict:
    # 总览、详情、写入响应共用同一序列化器，batch_no 一律取自服务端落库字段
    return {
        "id": r["id"],
        "probe_id": r["probe_id"],
        "batch_no": r["batch_no"],
        "temp_c": r["temp_c"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
    }


READING_COLUMNS = (
    "id, probe_id, batch_no, temp_c, verdict, reason, status, "
    "created_by, created_at, processed_at"
)


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        f"""
        SELECT {READING_COLUMNS}
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    return web.json_response([reading_dict(r) for r in rows])


async def get_reading(request: web.Request) -> web.Response:
    require_user(request)
    reading_id = int(request.match_info["id"])
    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        f"SELECT {READING_COLUMNS} FROM probe_readings WHERE id = $1",
        reading_id,
    )
    if not row:
        raise web.HTTPNotFound(
            text=json.dumps({"detail": "读数不存在"}, ensure_ascii=False),
            content_type="application/json",
        )
    return web.json_response(reading_dict(row))


async def list_batch_locks(request: web.Request) -> web.Response:
    # 锁定清单：服务端从视图 batch_lock_list 取数，batch_no 即 probe_readings 落库列
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    batch_no = request.query.get("batch_no", "").strip()
    params: list = []
    where = ""
    if batch_no:
        params.append(batch_no)
        where = "WHERE batch_no = $1"
    rows = await pool.fetch(
        f"""
        SELECT reading_id, probe_id, batch_no, status, created_by, created_at
        FROM batch_lock_list
        {where}
        ORDER BY reading_id DESC
        """,
        *params,
    )
    return web.json_response(
        [
            {
                "reading_id": r["reading_id"],
                "probe_id": r["probe_id"],
                "batch_no": r["batch_no"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    )


async def read_payload(request: web.Request) -> dict:
    # 网页发 JSON；绕过网页直连接口可能发 form-urlencoded。
    # 两种载体都解析成同一个 dict，再走同一套业务校验，保证挡回措辞一致。
    try:
        body = await request.json()
    except json.JSONDecodeError:
        try:
            data = await request.post()
        except Exception:
            return {}
        return {k: v for k, v in data.items()}
    return body if isinstance(body, dict) else {}


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    body = await read_payload(request)
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    # 缺批次（缺字段、null、空串、纯空白）一律整笔挡回。
    # 网页和绕过网页的直连接口共用此校验，措辞完全一致。
    batch_no = str(body.get("batch_no", "")).strip()
    if not batch_no:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": BATCH_REQUIRED_DETAIL}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]
    # 入队（probe_readings）与进入锁定清单（同一行，视图即时可见）原子写入：
    # 单条 INSERT 单事务，要么整笔成功三处同时可见，要么整笔失败不留半截，
    # 不存在“先入队后加锁失败、批次还可改”的中间态。
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                INSERT INTO probe_readings
                    (probe_id, batch_no, temp_c, status, created_by, created_at)
                VALUES ($1, $2, $3, 'pending', $4, now())
                RETURNING {READING_COLUMNS}
                """,
                probe_id,
                batch_no,
                temp_c,
                user["username"],
            )
    payload = reading_dict(row)
    payload["message"] = "已入队并锁定批次，后台工人将认领并判定"
    return web.json_response(payload, status=201)


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/readings/{id}", get_reading)
    app.router.add_get("/api/batch-locks", list_batch_locks)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
