import { Fragment } from "preact";
import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function fmtTime(s) {
  if (!s) return "—";
  return s.replace("T", " ").slice(0, 19);
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [view, setView] = useState("desk"); // desk | batch
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "", coolant_batch: "" });
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState(null);
  const [locks, setLocks] = useState([]);
  const [batchFilter, setBatchFilter] = useState("");

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadReadings = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载列表失败，请重新登录");
      return;
    }
    setRows(await res.json());
  }, [token, authHeaders]);

  const loadLocks = useCallback(async (batch = batchFilter) => {
    if (!token) return;
    const q = batch.trim() ? `?coolant_batch=${encodeURIComponent(batch.trim())}` : "";
    const res = await fetch(`/api/batch-locks${q}`, { headers: authHeaders() });
    if (!res.ok) return;
    setLocks(await res.json());
  }, [token, authHeaders, batchFilter]);

  useEffect(() => {
    loadReadings();
    if (!token) return undefined;
    const t = setInterval(loadReadings, 3000);
    return () => clearInterval(t);
  }, [loadReadings, token]);

  useEffect(() => {
    if (view === "batch") loadLocks();
  }, [view]); // eslint-disable-line

  async function onLogin(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setRows([]);
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
          // 批次必填，但不在浏览器端拦截——缺批次时由服务端整笔挡回，
          // 网页与直连接口展示完全一致的服务端文案。
          coolant_batch: submitForm.coolant_batch,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败");
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", temp_c: "", coolant_batch: "" });
      await loadReadings();
      if (view === "batch") await loadLocks();
    } finally {
      setLoading(false);
    }
  }

  async function openDetail(id) {
    setDetail({ loading: true });
    const res = await fetch(`/api/readings/${id}`, { headers: authHeaders() });
    if (!res.ok) {
      setDetail({ error: "详情加载失败" });
      return;
    }
    setDetail({ row: await res.json() });
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交冷媒批次、探头编号与摄氏温度，后台工人认领后判定合格或超温。</p>
        <div class="card">
          <form onSubmit={onLogin}>
            <div class="row">
              <label>
                用户名
                <input
                  value={loginForm.username}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, username: e.target.value })
                  }
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginForm.password}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, password: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                登录
              </button>
            </div>
            {error && <p class="err">{error}</p>}
          </form>
          <p class="sub" style={{ marginBottom: 0 }}>
            记录员 logger / log123456 · 值班员 watcher / watch123456
          </p>
        </div>
      </div>
    );
  }

  const isWriter = user?.role === "writer";

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">温度不超过 8℃ 为合格，否则为超温；冷媒批次为报温必填项，写入即锁定。</p>
        </div>
        <div class="user">
          <nav class="nav">
            <button
              type="button"
              class={view === "desk" ? "navbtn on" : "navbtn"}
              onClick={() => setView("desk")}
            >
              报温总览
            </button>
            <button
              type="button"
              class={view === "batch" ? "navbtn on" : "navbtn"}
              onClick={() => setView("batch")}
            >
              批次落地页
            </button>
          </nav>
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {view === "batch" && (
        <div class="card">
          {/* 上：说明 */}
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>冷媒批次锁定清单</h2>
          <p class="sub" style={{ marginBottom: "0.75rem" }}>
            冷媒批次号是报温必填项。读数入队时与锁定清单在同一事务原子写入，批次一经写入即锁死：
            总览批次列、读数详情、锁定清单三处显示同一落库值，事后改表无法修改旧单。
            {isWriter ? "" : "值班员仅可查看三处批次，不能报温。"}
          </p>
          {/* 中：筛选输入 */}
          <div class="row" style={{ marginBottom: "0.75rem" }}>
            <label>
              按冷媒批次筛选
              <input
                value={batchFilter}
                onInput={(e) => setBatchFilter(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") loadLocks(); }}
                placeholder="例如 批零三"
              />
            </label>
            <button type="button" onClick={() => loadLocks()}>
              查询
            </button>
            <button type="button" class="secondary" onClick={() => { setBatchFilter(""); loadLocks(""); }}>
              全部
            </button>
          </div>
          {/* 下：锁定清单一览 */}
          <table>
            <thead>
              <tr>
                <th>读数编号</th>
                <th>探头</th>
                <th>温度℃</th>
                <th>冷媒批次</th>
                <th>状态</th>
                <th>锁定人</th>
                <th>锁定时间</th>
              </tr>
            </thead>
            <tbody>
              {locks.map((l) => (
                <tr key={l.reading_id}>
                  <td>{l.reading_id}</td>
                  <td>{l.probe_id}</td>
                  <td>{l.temp_c}</td>
                  <td><strong>{l.coolant_batch}</strong></td>
                  <td>{l.status}</td>
                  <td>{l.locked_by}</td>
                  <td>{fmtTime(l.locked_at)}</td>
                </tr>
              ))}
              {locks.length === 0 && (
                <tr><td colspan="7">暂无锁定记录</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {view === "desk" && (
        <Fragment>
          {isWriter && (
            <div class="card">
              <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
              <form onSubmit={onSubmit}>
                <div class="row">
                  <label>
                    冷媒批次号
                    <input
                      value={submitForm.coolant_batch}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, coolant_batch: e.target.value })
                      }
                      placeholder="必填，例如 批零三"
                    />
                  </label>
                  <label>
                    探头编号
                    <input
                      required
                      value={submitForm.probe_id}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, probe_id: e.target.value })
                      }
                      placeholder="例如 探头C03"
                    />
                  </label>
                  <label>
                    温度（℃）
                    <input
                      required
                      type="number"
                      step="0.1"
                      value={submitForm.temp_c}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, temp_c: e.target.value })
                      }
                    />
                  </label>
                  <button type="submit" disabled={loading}>
                    提交
                  </button>
                </div>
                {error && <p class="err">{error}</p>}
                {msg && <p class="ok">{msg}</p>}
              </form>
            </div>
          )}

          {!isWriter && (
            <p class="sub">值班员视图：可查看总览批次列、读数详情与批次锁定清单，不能报温。</p>
          )}

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数总览</h2>
            <table>
              <thead>
                <tr>
                  <th>编号</th>
                  <th>冷媒批次</th>
                  <th>探头</th>
                  <th>温度℃</th>
                  <th>结论</th>
                  <th>说明</th>
                  <th>状态</th>
                  <th>提交人</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} class="clickable" onClick={() => openDetail(r.id)}>
                    <td>{r.id}</td>
                    <td><strong>{r.coolant_batch}</strong></td>
                    <td>{r.probe_id}</td>
                    <td>{r.temp_c}</td>
                    <td>
                      <span class={verdictClass(r.verdict, r.status)}>
                        {displayVerdict(r)}
                      </span>
                    </td>
                    <td>{r.reason || "—"}</td>
                    <td>{r.status}</td>
                    <td>{r.created_by}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colspan="8">暂无数据</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Fragment>
      )}

      {detail && (
        <div class="modal-mask" onClick={() => setDetail(null)}>
          <div class="modal" onClick={(e) => e.stopPropagation()}>
            {detail.loading && <p>加载中…</p>}
            {detail.error && <p class="err">{detail.error}</p>}
            {detail.row && (
              <Fragment>
                <h2 style={{ marginTop: 0, fontSize: "1.05rem" }}>
                  读数详情 #{detail.row.id}
                </h2>
                <table>
                  <tbody>
                    <tr><th>冷媒批次</th><td><strong>{detail.row.coolant_batch}</strong></td></tr>
                    <tr><th>探头编号</th><td>{detail.row.probe_id}</td></tr>
                    <tr><th>温度（℃）</th><td>{detail.row.temp_c}</td></tr>
                    <tr><th>结论</th><td>{displayVerdict(detail.row)}</td></tr>
                    <tr><th>说明</th><td>{detail.row.reason || "—"}</td></tr>
                    <tr><th>状态</th><td>{detail.row.status}</td></tr>
                    <tr><th>提交人</th><td>{detail.row.created_by}</td></tr>
                    <tr><th>提交时间</th><td>{fmtTime(detail.row.created_at)}</td></tr>
                  </tbody>
                </table>
                <div class="row" style={{ marginTop: "0.75rem", justifyContent: "flex-end" }}>
                  <button type="button" onClick={() => setDetail(null)}>关闭</button>
                </div>
              </Fragment>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
