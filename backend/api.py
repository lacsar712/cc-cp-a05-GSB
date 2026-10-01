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

# 缺批次的统一挡回文案：网页提交与绕过网页的直连接口共用同一常量，措辞必须一致。
BATCH_REQUIRED_DETAIL = "冷媒批次号为报温必填项，请填写冷媒批次后再提交"

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


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


def _batch_required() -> web.HTTPBadRequest:
    # 唯一出口：两条路径（网页/直连）都从这里返回，保证同状态码同文案。
    return web.HTTPBadRequest(
        text=json.dumps({"detail": BATCH_REQUIRED_DETAIL}, ensure_ascii=False),
        content_type="application/json",
    )


def reading_dict(r) -> dict:
    return {
        "id": r["id"],
        "probe_id": r["probe_id"],
        "temp_c": r["temp_c"],
        "coolant_batch": r["coolant_batch"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
    }


_READING_COLS = (
    "id, probe_id, temp_c, coolant_batch, verdict, reason, status, "
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
        SELECT {_READING_COLS}
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    return web.json_response([reading_dict(r) for r in rows])


async def get_reading(request: web.Request) -> web.Response:
    require_user(request)
    try:
        reading_id = int(request.match_info["id"])
    except ValueError as exc:
        raise web.HTTPNotFound(text=json.dumps({"detail": "读数不存在"}, ensure_ascii=False),
                               content_type="application/json") from exc
    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        f"SELECT {_READING_COLS} FROM probe_readings WHERE id = $1",
        reading_id,
    )
    if not row:
        raise web.HTTPNotFound(
            text=json.dumps({"detail": "读数不存在"}, ensure_ascii=False),
            content_type="application/json",
        )
    return web.json_response(reading_dict(row))


async def list_batch_locks(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    # 锁定清单展示的批次直接 JOIN 取自 probe_readings.coolant_batch 这一落库字段，
    # 与总览列/详情列共用同一套值；可选批次筛选（落地页中部输入）。
    batch_filter = str(request.query.get("coolant_batch", "")).strip()
    sql = f"""
        SELECT l.reading_id, l.locked_by, l.locked_at,
               r.probe_id, r.temp_c, r.status, r.coolant_batch
        FROM coolant_batch_locks l
        JOIN probe_readings r ON r.id = l.reading_id
        {"WHERE r.coolant_batch = $1" if batch_filter else ""}
        ORDER BY l.reading_id DESC
    """
    rows = (
        await pool.fetch(sql, batch_filter) if batch_filter else await pool.fetch(sql)
    )
    out = [
        {
            "reading_id": r["reading_id"],
            "probe_id": r["probe_id"],
            "temp_c": r["temp_c"],
            "coolant_batch": r["coolant_batch"],
            "status": r["status"],
            "locked_by": r["locked_by"],
            "locked_at": r["locked_at"].isoformat() if r["locked_at"] else None,
        }
        for r in rows
    ]
    return web.json_response(out)


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc

    # —— 缺批次整笔挡回：在任何写库之前，网页与直连都走到同一个校验、同一个文案。 ——
    raw_batch = body.get("coolant_batch")
    coolant_batch = str(raw_batch).strip() if raw_batch is not None else ""
    if not coolant_batch:
        raise _batch_required()

    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
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
    # —— 入队与锁定清单原子写入：同一事务，先入队后锁失败会整体回滚，
    #    绝不留下“可改批次的半截读数”。 ——
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                INSERT INTO probe_readings
                    (probe_id, temp_c, coolant_batch, status, created_by, created_at)
                VALUES ($1, $2, $3, 'pending', $4, now())
                RETURNING {_READING_COLS}
                """,
                probe_id,
                temp_c,
                coolant_batch,
                user["username"],
            )
            await conn.execute(
                """
                INSERT INTO coolant_batch_locks
                    (reading_id, coolant_batch, locked_by)
                VALUES ($1, $2, $3)
                """,
                row["id"],
                coolant_batch,
                user["username"],
            )

    payload = reading_dict(row)
    payload["processed_at"] = None
    payload["message"] = "已入队并锁定冷媒批次，后台工人将认领并判定"
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
