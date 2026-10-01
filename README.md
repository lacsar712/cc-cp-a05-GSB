# 冷链探头超温台

记录员上报**冷媒批次号**、探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

## 冷媒批次号规则

- **报温必填**：缺少批次（无字段 / 空串 / 纯空白）的提交整笔挡回，网页提交与绕过网页的直连接口走同一校验、返回同一措辞 `冷媒批次号为报温必填项，缺少批次，整笔退回`。
- **一处落库、三处同值**：批次只存在 `probe_readings.batch_no` 一列；总览批次列、读数详情、`batch_lock_list` 锁定清单（数据库视图）全部读它。
- **写入即锁死**：`trg_lock_batch_no` 触发器禁止修改任何旧单的批次，事后直接改表也会被数据库拒绝。
- **原子写入**：入队行即为锁定清单行，单事务提交，不存在“先入队后加锁失败”的半截单据。
- 值班员可查看三处批次，但不能报温。


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
| 探头A01 | 批零一 | 4.2℃ | 合格 |
| 探头B02 | 批零二 | 12.5℃ | 超温 |

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/readings` | 读数总览（含 `batch_no` 批次列） |
| POST | `/api/readings` | 报温入队，字段 `probe_id` / `batch_no`（必填）/ `temp_c` |
| GET | `/api/readings/{id}` | 读数详情（含批次） |
| GET | `/api/batch-locks?batch_no=` | 批次锁定清单，取数据库视图，可按批次精确筛选 |


## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
