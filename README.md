# 冷链探头超温台

记录员上报**冷媒批次号**（必填）、探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

## 冷媒批次规则

- 冷媒批次号为报温必填项；缺批次时整笔挡回（`400`），网页提交与绕过网页的直连接口走同一校验、同一文案：`冷媒批次号为报温必填项，请填写冷媒批次后再提交`。
- 入队与写入锁定清单 `coolant_batch_locks` 在**同一数据库事务**内原子完成，不存在“先入队、锁失败”的半截读数。
- 批次一经写入即由数据库触发器锁死：无法 `UPDATE` 批次列；锁定清单不可改、不可删；读数被锁行外键约束保护不可删。事后直接改表也动不了旧单。
- 总览批次列、读数详情、批次落地页锁定清单三处共用同一落库字段 `probe_readings.coolant_batch`（清单经 JOIN 取值），三处恒为同值。
- 顶栏「批次落地页」：上方说明规则、中部按批次筛选输入、下方锁定清单一览（仅展示，不加业务列）。
- 值班员（watcher）可见三处批次但无提交表单，不能报温。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员，可提交读数 |
| watcher | watch123456 | 值班员，只读列表 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 种子数据

| 探头 | 冷媒批次 | 温度 | 结论 |
|------|----------|------|------|
| 探头A01 | 批-种子 | 4.2℃ | 合格 |
| 探头B02 | 批-种子 | 12.5℃ | 超温 |

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/readings` | 读数总览（含 `coolant_batch`） |
| POST | `/api/readings` | 报温，body 必含 `probe_id`/`temp_c`/`coolant_batch`，缺批次 400 |
| GET | `/api/readings/{id}` | 读数详情（含批次） |
| GET | `/api/batch-locks?coolant_batch=` | 批次锁定清单，批次 JOIN 自读数落库字段，可按批次筛选 |

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
