import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# 批次在写入时必填。锁定清单 coolant_batch_locks 与读数共用同一批次来源：
# 总览列、详情列、锁定清单三处的批次值最终都取自 probe_readings.coolant_batch。
# 逐条执行：函数体内含分号，不能合并成一条多语句串走扩展协议。
# 分两组：先建表/函数（PRE），回填并收紧 NOT NULL 后，再建触发器（TRIGGERS），
# 否则回填 UPDATE 会被“批次不可改”触发器误拦。
SCHEMA_PRE_STATEMENTS = [
    """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    coolant_batch text,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
)
    """,
    "CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id)",
    """
CREATE TABLE IF NOT EXISTS coolant_batch_locks (
    reading_id integer PRIMARY KEY
                    REFERENCES probe_readings(id) ON DELETE RESTRICT,
    coolant_batch text NOT NULL,
    locked_by text NOT NULL,
    locked_at timestamptz NOT NULL DEFAULT now()
)
    """,
    # 事后改表动不了旧单：禁止 UPDATE 批次列；其余字段（status/verdict/...）仍可由工人更新。
    """
CREATE OR REPLACE FUNCTION forbid_batch_change() RETURNS trigger AS $$
BEGIN
    IF NEW.coolant_batch IS DISTINCT FROM OLD.coolant_batch THEN
        RAISE EXCEPTION '冷媒批次已锁定，禁止修改（reading_id=%，旧批次=%）',
            OLD.id, OLD.coolant_batch
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
    """,
    # 锁定清单写死：既不能改，也不能删。
    """
CREATE OR REPLACE FUNCTION forbid_lock_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '冷媒批次锁定清单不可 %', TG_OP
            USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql
    """,
]

# 缺批次再送应挡回——没有读数新增，自然没有新锁，无需补偿。
SCHEMA_TRIGGER_STATEMENTS = [
    "DROP TRIGGER IF EXISTS trg_forbid_batch_change ON probe_readings",
    """
CREATE TRIGGER trg_forbid_batch_change
    BEFORE UPDATE ON probe_readings
    FOR EACH ROW EXECUTE FUNCTION forbid_batch_change()
    """,
    "DROP TRIGGER IF EXISTS trg_forbid_lock_update ON coolant_batch_locks",
    """
CREATE TRIGGER trg_forbid_lock_update
    BEFORE UPDATE ON coolant_batch_locks
    FOR EACH ROW EXECUTE FUNCTION forbid_lock_mutation()
    """,
    "DROP TRIGGER IF EXISTS trg_forbid_lock_delete ON coolant_batch_locks",
    """
CREATE TRIGGER trg_forbid_lock_delete
    BEFORE DELETE ON coolant_batch_locks
    FOR EACH ROW EXECUTE FUNCTION forbid_lock_mutation()
    """,
]

# 存量/新库统一回填值，随后收紧 NOT NULL。
BACKFILL_BATCH = "批-种子"


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def _migrate_sync(conn) -> None:
    conn.execute("ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS coolant_batch text;")
    conn.execute(
        "UPDATE probe_readings SET coolant_batch = %s WHERE coolant_batch IS NULL;",
        (BACKFILL_BATCH,),
    )
    conn.execute("ALTER TABLE probe_readings ALTER COLUMN coolant_batch SET NOT NULL;")


def ensure_schema_sync(conn) -> None:
    for stmt in SCHEMA_PRE_STATEMENTS:
        conn.execute(stmt)
    _migrate_sync(conn)
    for stmt in SCHEMA_TRIGGER_STATEMENTS:
        conn.execute(stmt)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        for stmt in SCHEMA_PRE_STATEMENTS:
            await conn.execute(stmt)
        await conn.execute(
            "ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS coolant_batch text"
        )
        await conn.execute(
            "UPDATE probe_readings SET coolant_batch = $1 WHERE coolant_batch IS NULL",
            BACKFILL_BATCH,
        )
        await conn.execute(
            "ALTER TABLE probe_readings ALTER COLUMN coolant_batch SET NOT NULL"
        )
        for stmt in SCHEMA_TRIGGER_STATEMENTS:
            await conn.execute(stmt)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        # 种子单同样遵守：批次 NOT NULL + 同事务写锁，保证三处同值。
        samples = [
            ("探头A01", 4.2, BACKFILL_BATCH),
            ("探头B02", 12.5, BACKFILL_BATCH),
        ]
        for probe_id, temp_c, batch in samples:
            verdict, reason = judge_temp(temp_c)
            async with conn.transaction():
                rid = await conn.fetchval(
                    """
                    INSERT INTO probe_readings
                        (probe_id, temp_c, coolant_batch, verdict, reason,
                         status, created_by, processed_at)
                    VALUES ($1, $2, $3, $4, $5, 'done', 'logger', now())
                    RETURNING id
                    """,
                    probe_id,
                    temp_c,
                    batch,
                    verdict,
                    reason,
                )
                await conn.execute(
                    """
                    INSERT INTO coolant_batch_locks
                        (reading_id, coolant_batch, locked_by)
                    VALUES ($1, $2, 'logger')
                    """,
                    rid,
                    batch,
                )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", 4.2, BACKFILL_BATCH),
        ("探头B02", 12.5, BACKFILL_BATCH),
    ]
    for probe_id, temp_c, batch in samples:
        verdict, reason = judge_temp(temp_c)
        with conn.transaction():
            rid = conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, coolant_batch, verdict, reason,
                     status, created_by, processed_at)
                VALUES (%s, %s, %s, %s, %s, 'done', 'logger', now())
                RETURNING id
                """,
                (probe_id, temp_c, batch, verdict, reason),
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO coolant_batch_locks
                    (reading_id, coolant_batch, locked_by)
                VALUES (%s, %s, 'logger')
                """,
                (rid, batch),
            )
    conn.commit()
