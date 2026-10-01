import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# batch_no 是冷媒批次号的唯一落库字段：
#   总览批次列 / 读数详情批次 / 锁定清单（视图 batch_lock_list）三处全部读这一列，
#   任何一环都不另存、不转换，保证三处同值。
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    batch_no text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);

-- 幂等迁移：老库补列，回填历史行，再收紧 NOT NULL
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS batch_no text;
UPDATE probe_readings SET batch_no = '历史批次-未登记' WHERE batch_no IS NULL;
ALTER TABLE probe_readings ALTER COLUMN batch_no SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

-- 批次锁死：有批次写入后，旧单批次物理上不可改（含直接连库改表）
CREATE OR REPLACE FUNCTION fn_lock_batch_no() RETURNS trigger AS $$
BEGIN
    IF NEW.batch_no IS DISTINCT FROM OLD.batch_no THEN
        RAISE EXCEPTION '冷媒批次号一经提交即锁定，禁止修改旧单批次（读数 %：% → %）',
            OLD.id, OLD.batch_no, NEW.batch_no
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_lock_batch_no ON probe_readings;
CREATE TRIGGER trg_lock_batch_no
    BEFORE UPDATE ON probe_readings
    FOR EACH ROW EXECUTE FUNCTION fn_lock_batch_no();

-- 锁定清单直接映射落库列，与总览/详情共用同一套值，不存在第二份可漂移的数据
CREATE OR REPLACE VIEW batch_lock_list AS
SELECT
    id          AS reading_id,
    probe_id,
    batch_no,
    status,
    created_by,
    created_at
FROM probe_readings;
"""


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        samples = [
            ("探头A01", "批零一", 4.2),
            ("探头B02", "批零二", 12.5),
        ]
        for probe_id, batch_no, temp_c in samples:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, batch_no, temp_c, verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, $5, 'done', 'logger', now())
                """,
                probe_id,
                batch_no,
                temp_c,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", "批零一", 4.2),
        ("探头B02", "批零二", 12.5),
    ]
    for probe_id, batch_no, temp_c in samples:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, batch_no, temp_c, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, %s, 'done', 'logger', now())
            """,
            (probe_id, batch_no, temp_c, verdict, reason),
        )
    conn.commit()
