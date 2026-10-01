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

function statusText(s) {
  if (s === "pending") return "待处理";
  if (s === "processing") return "处理中";
  if (s === "done") return "已判定";
  return s;
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
  const [submitForm, setSubmitForm] = useState({ probe_id: "", batch_no: "", temp_c: "" });
  const [rows, setRows] = useState([]);
  const [locks, setLocks] = useState([]);
  const [batchFilter, setBatchFilter] = useState("");
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

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

  const loadLocks = useCallback(async () => {
    if (!token) return;
    const q = batchFilter.trim()
      ? `?batch_no=${encodeURIComponent(batchFilter.trim())}`
      : "";
    const res = await fetch(`/api/batch-locks${q}`, { headers: authHeaders() });
    if (!res.ok) return;
    setLocks(await res.json());
  }, [token, authHeaders, batchFilter]);

  useEffect(() => {
    if (!token) return undefined;
    if (view === "desk") loadReadings();
    if (view === "batch") loadLocks();
    const t = setInterval(view === "desk" ? loadReadings : loadLocks, 3000);
    return () => clearInterval(t);
  }, [loadReadings, loadLocks, token, view]);

  async function openDetail(id) {
    const res = await fetch(`/api/readings/${id}`, { headers: authHeaders() });
    if (!res.ok) {
      setError("打开详情失败");
      return;
    }
    setDetail(await res.json());
  }

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
    setLocks([]);
    setDetail(null);
    setView("desk");
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
          batch_no: submitForm.batch_no,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // 直接展示服务端原文：缺批次时网页与直连接口拿到的是同一句
        setError(data.detail || "提交失败");
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", batch_no: "", temp_c: "" });
      await loadReadings();
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交探头编号、冷媒批次号与摄氏温度，后台工人认领后判定合格或超温。</p>
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
          <p class="sub" style={{ marginBottom: 0 }}>
            温度不超过 8℃ 为合格，否则为超温。冷媒批次号报温必填、提交即锁死。
          </p>
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
              批次锁定清单
            </button>
          </nav>
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {view === "desk" && (
        <div>
          {isWriter && (
            <div class="card">
              <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
              <form onSubmit={onSubmit}>
                <div class="row">
                  <label>
                    冷媒批次号（必填，提交后锁定）
                    <input
                      required
                      value={submitForm.batch_no}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, batch_no: e.target.value })
                      }
                      placeholder="例如 批零三"
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
            <p class="sub" style={{ fontSize: "0.85rem" }}>
              值班视图只读：批次列、单详情与批次锁定清单中的批次来自同一服务端落库字段，不可报温。
            </p>
          )}

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数总览（点击行查看详情）</h2>
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
                  <tr key={r.id} class="openable" onClick={() => openDetail(r.id)}>
                    <td>{r.id}</td>
                    <td><span class="batch">{r.batch_no}</span></td>
                    <td>{r.probe_id}</td>
                    <td>{r.temp_c}</td>
                    <td>
                      <span class={verdictClass(r.verdict, r.status)}>
                        {displayVerdict(r)}
                      </span>
                    </td>
                    <td>{r.reason || "—"}</td>
                    <td>{statusText(r.status)}</td>
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
        </div>
      )}

      {view === "batch" && (
        <div>
          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>冷媒批次号说明</h2>
            <ul class="rules">
              <li>冷媒批次号是<b>报温必填项</b>：缺批次的提交整笔挡回，网页与直连接口同一结论、同一措辞。</li>
              <li>批次<b>随报温一次性写入即锁死</b>：总览批次列、读数详情、锁定清单三处显示同一落库值，事后直接改表也无法修改旧单批次。</li>
              <li>入队与进锁定清单<b>原子写入</b>：不会出现先入队、加锁失败、批次还可改的半截单据。</li>
              <li>值班员可查看三处批次，但不可报温。</li>
            </ul>
          </div>

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>按批次筛选</h2>
            <div class="row">
              <label style={{ flex: 1 }}>
                输入完整冷媒批次号精确筛选
                <input
                  value={batchFilter}
                  onInput={(e) => setBatchFilter(e.target.value)}
                  placeholder="例如 批零三，留空显示全部"
                />
              </label>
              <button type="button" class="secondary" onClick={() => setBatchFilter("")}>
                清除
              </button>
            </div>
          </div>

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>批次锁定清单</h2>
            <table>
              <thead>
                <tr>
                  <th>读数编号</th>
                  <th>冷媒批次（已锁定）</th>
                  <th>探头</th>
                  <th>处理状态</th>
                  <th>提交人</th>
                  <th>提交时间</th>
                </tr>
              </thead>
              <tbody>
                {locks.map((r) => (
                  <tr key={r.reading_id} class="openable" onClick={() => { setView("desk"); openDetail(r.reading_id); }}>
                    <td>{r.reading_id}</td>
                    <td><span class="batch">{r.batch_no}</span></td>
                    <td>{r.probe_id}</td>
                    <td>{statusText(r.status)}</td>
                    <td>{r.created_by}</td>
                    <td>{r.created_at ? r.created_at.replace("T", " ").slice(0, 19) : "—"}</td>
                  </tr>
                ))}
                {locks.length === 0 && (
                  <tr>
                    <td colspan="6">{batchFilter ? "该批次下没有已锁定单据" : "暂无数据"}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {detail && (
        <div class="modal-mask" onClick={() => setDetail(null)}>
          <div class="modal" onClick={(e) => e.stopPropagation()}>
            <h2 style={{ marginTop: 0, fontSize: "1.05rem" }}>读数详情 #{detail.id}</h2>
            <table>
              <tbody>
                <tr><th>冷媒批次号（已锁定）</th><td><span class="batch">{detail.batch_no}</span></td></tr>
                <tr><th>探头编号</th><td>{detail.probe_id}</td></tr>
                <tr><th>温度</th><td>{detail.temp_c} ℃</td></tr>
                <tr><th>结论</th><td>{displayVerdict(detail)}</td></tr>
                <tr><th>说明</th><td>{detail.reason || "—"}</td></tr>
                <tr><th>状态</th><td>{statusText(detail.status)}</td></tr>
                <tr><th>提交人</th><td>{detail.created_by}</td></tr>
                <tr><th>提交时间</th><td>{detail.created_at ? detail.created_at.replace("T", " ").slice(0, 19) : "—"}</td></tr>
                <tr><th>判定时间</th><td>{detail.processed_at ? detail.processed_at.replace("T", " ").slice(0, 19) : "—"}</td></tr>
              </tbody>
            </table>
            <div class="row" style={{ justifyContent: "flex-end", marginTop: "0.75rem" }}>
              <button type="button" class="secondary" onClick={() => setDetail(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
