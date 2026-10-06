<script>
  let session = null;
  let logs = [];
  let wall = [];
  let locks = [];
  let view = "desk"; // desk | locks
  let loginUser = "surveyor";
  let loginPass = "surv123456";
  let chainage = "";
  let deltaMm = "";
  let error = "";
  let wallMsg = "";
  let loading = false;
  let timer;

  // 锁区专页手改草稿，按锁 id 挂着；轮询刷新不覆盖正在编辑的草稿
  let drafts = {};

  $: isWriter = session?.role === "writer";

  function headers() {
    return session ? { Authorization: "Bearer " + session.token } : {};
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...headers(), ...(options.headers || {}) },
    });
    if (res.status === 401) {
      logout();
      return { ok: false, status: 401, data: {} };
    }
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }

  function fmt(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("zh-CN", { hour12: false });
  }

  function toLocalInput(iso) {
    const d = new Date(iso);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function seedDraft(row) {
    if (!drafts[row.id]) {
      drafts[row.id] = { peak_mm: row.peak_mm, peak_at: toLocalInput(row.peak_at), saving: false, err: "" };
    }
  }

  async function refreshLogs() {
    const r = await api("/api/logs");
    if (r.ok) logs = r.data;
  }

  async function refreshPeaks() {
    const r = await api("/api/peaks");
    if (r.ok) wall = r.data;
  }

  async function refreshLocks() {
    const r = await api("/api/locks");
    if (r.ok) {
      locks = r.data;
      // 为新出现的锁预置手改草稿；已有草稿（可能正在编辑）不覆盖
      locks.forEach(seedDraft);
    }
  }

  async function refreshAll() {
    if (!session) return;
    await Promise.all([refreshLogs(), refreshPeaks(), view === "locks" ? refreshLocks() : Promise.resolve()]);
  }

  async function login() {
    error = "";
    loading = true;
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: loginUser, password: loginPass }),
      });
      const data = await res.json();
      if (!res.ok) {
        error = data.detail || "登录失败";
        return;
      }
      session = { token: data.access_token, username: data.username, role: data.role };
      localStorage.setItem("tunnel_session", JSON.stringify(session));
      await refreshAll();
      timer = setInterval(refreshAll, 2000);
    } catch {
      error = "无法连接接口";
    } finally {
      loading = false;
    }
  }

  function logout() {
    if (timer) clearInterval(timer);
    session = null;
    logs = [];
    wall = [];
    locks = [];
    drafts = {};
    localStorage.removeItem("tunnel_session");
  }

  async function submit() {
    error = "";
    loading = true;
    try {
      const r = await api("/api/logs", {
        method: "POST",
        body: JSON.stringify({ chainage, delta_mm: Number(deltaMm) }),
      });
      if (!r.ok) {
        error = r.data.detail || "提交失败";
        return;
      }
      chainage = "";
      deltaMm = "";
      await Promise.all([refreshLogs(), refreshPeaks()]);
    } catch {
      error = "提交时网络异常";
    } finally {
      loading = false;
    }
  }

  async function lockRow(chainage) {
    wallMsg = "";
    const r = await api("/api/peaks/lock", { method: "POST", body: JSON.stringify({ chainage }) });
    if (!r.ok) {
      wallMsg = r.data.detail || "锁定失败";
      return;
    }
    wallMsg = `断面 ${chainage} 已压入锁区`;
    await Promise.all([refreshPeaks(), refreshLocks()]);
  }

  async function lockAll() {
    wallMsg = "";
    const r = await api("/api/peaks/lock", { method: "POST", body: JSON.stringify({}) });
    if (!r.ok) {
      wallMsg = r.data.detail || "锁定失败";
      return;
    }
    const n = r.data.locked?.length ?? 0;
    const skip = r.data.skipped?.length ?? 0;
    wallMsg = n > 0 ? `已锁定 ${n} 个断面的峰值${skip ? `；${skip} 个断面未办结、未虚填` : ""}` : "没有可锁定的新断面";
    await Promise.all([refreshPeaks(), refreshLocks()]);
  }

  async function saveLock(row) {
    const d = drafts[row.id];
    if (!d) return;
    d.err = "";
    const value = Number(d.peak_mm);
    if (!Number.isFinite(value)) {
      d.err = "峰值必须是数字";
      return;
    }
    const when = new Date(d.peak_at);
    if (Number.isNaN(when.getTime())) {
      d.err = "峰值时刻格式无法识别";
      return;
    }
    d.saving = true;
    try {
      const r = await api(`/api/locks/${row.id}`, {
        method: "PATCH",
        body: JSON.stringify({ peak_mm: value, peak_at: when.toISOString() }),
      });
      if (!r.ok) {
        d.err = r.data.detail || "保存失败";
        return;
      }
      delete drafts[row.id]; // 保存后用接口回来的冻结值重新打底
      await Promise.all([refreshLocks(), refreshPeaks()]);
    } finally {
      d.saving = false;
    }
  }

  const raw = localStorage.getItem("tunnel_session");
  if (raw) {
    try {
      session = JSON.parse(raw);
      refreshAll();
      timer = setInterval(refreshAll, 2000);
    } catch {
      localStorage.removeItem("tunnel_session");
    }
  }
</script>

<style>
  :global(body) {
    margin: 0;
    font-family: "Segoe UI", system-ui, sans-serif;
    background: #1c1917;
    color: #f5f5f4;
  }
  main { max-width: 1020px; margin: 0 auto; padding: 1.5rem; }
  header {
    display: flex; align-items: flex-start; justify-content: space-between;
    flex-wrap: wrap; gap: 0.5rem; border-bottom: 2px solid #d97706;
    padding-bottom: 0.75rem; margin-bottom: 1rem;
  }
  h1 { color: #fbbf24; margin: 0; font-size: 1.5rem; }
  .who { color: #a8a29e; font-size: 0.85rem; margin-top: 0.2rem; }
  nav { display: flex; gap: 0.5rem; }
  .sub { color: #a8a29e; margin-bottom: 1.25rem; }
  section {
    background: #292524; border: 1px solid #44403c; border-radius: 8px;
    padding: 1rem 1.25rem; margin-bottom: 1rem;
  }
  section h2 { margin: 0 0 0.75rem; font-size: 1.05rem; color: #fde68a; }
  label { display: block; font-size: 0.85rem; color: #d6d3d1; margin-bottom: 0.25rem; }
  input {
    width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px;
    border: 1px solid #57534e; background: #0c0a09; color: #fafaf9; margin-bottom: 0.75rem;
  }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #44403c; vertical-align: middle; }
  .wall td.peak { font-variant-numeric: tabular-nums; font-weight: 600; }
  .tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; white-space: nowrap; }
  .ok { background: #14532d; color: #86efac; }
  .bad { background: #7f1d1d; color: #fca5a5; }
  .pending { background: #713f12; color: #fde68a; }
  .locked { background: #1e3a8a; color: #bfdbfe; }
  .muted { color: #78716c; }
  .frozen { color: #bfdbfe; }
  button {
    cursor: pointer; padding: 0.45rem 0.9rem; border: none; border-radius: 6px;
    background: #d97706; color: #fff; font-weight: 600;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.secondary { background: #57534e; }
  button.small { padding: 0.25rem 0.6rem; font-size: 0.8rem; }
  button.tab { background: #44403c; }
  button.tab.active { background: #d97706; }
  .toolbar { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
  .err { color: #fb7185; }
  .msg { color: #86efac; font-size: 0.85rem; }
  .edit-num { width: 7rem; margin: 0; }
  .edit-time { width: 13.5rem; margin: 0; }
  .ro-tip { color: #a8a29e; font-size: 0.85rem; margin: 0 0 0.75rem; }
</style>

<main>
  {#if !session}
    <h1>隧道收敛测缝台</h1>
    <p class="sub">测量员提交桩号与收敛毫米值，接口进程内线程认领后出结论。登录框已预填可写账号 surveyor / surv123456。</p>
    <section>
      <label>用户名</label>
      <input bind:value={loginUser} autocomplete="off" />
      <label>密码</label>
      <input type="password" bind:value={loginPass} autocomplete="off" />
      <button disabled={loading} on:click={login}>登录</button>
      {#if error}<p class="err">{error}</p>{/if}
    </section>
  {:else}
    <header>
      <div>
        <h1>隧道收敛测缝台 · 拱顶峰值墙</h1>
        <div class="who">已登录：{session.username}（{isWriter ? "测量员·可写可锁" : "巡检岗·只读"}）</div>
      </div>
      <nav>
        <button class="tab {view === 'desk' ? 'active' : ''}" on:click={() => (view = "desk")}>办结台</button>
        <button class="tab {view === 'locks' ? 'active' : ''}" on:click={() => { view = "locks"; refreshLocks(); }}>锁区专页</button>
        <button class="secondary" on:click={logout}>退出</button>
      </nav>
    </header>

    <!-- 页眉峰值墙：左列断面，右列峰值与时刻 -->
    <section>
      <h2>拱顶历史峰值墙</h2>
      <div class="toolbar" style="margin-bottom: 0.75rem;">
        <button class="secondary small" disabled={loading} on:click={refreshAll}>刷新</button>
        {#if isWriter}
          <button class="small" on:click={lockAll}>锁定当前峰值</button>
        {/if}
        <span class="muted" style="font-size:0.8rem;">每 2 秒自动刷新 · 峰值只取自办结集合，未办结断面不虚填</span>
        {#if wallMsg}<span class="msg">{wallMsg}</span>{/if}
      </div>
      <table class="wall">
        <thead>
          <tr><th>断面（桩号）</th><th>历史峰值 mm</th><th>峰值时刻</th><th>状态</th>{#if isWriter}<th>操作</th>{/if}</tr>
        </thead>
        <tbody>
          {#each wall as row}
            <tr>
              <td>{row.chainage}</td>
              {#if row.locked}
                <!-- 已锁列：照锁区副本显示，新办结不再改动 -->
                <td class="peak frozen">{row.lock.peak_mm}</td>
                <td class="frozen">{fmt(row.lock.peak_at)}</td>
                <td><span class="tag locked">🔒 已锁（只读副本）</span></td>
                {#if isWriter}<td class="muted">锁定后不再变，可去锁区专页手改</td>{/if}
              {:else if row.has_done}
                <td class="peak">{row.live_peak_mm}</td>
                <td>{fmt(row.live_peak_at)}</td>
                <td><span class="tag ok">实时（未锁）</span></td>
                {#if isWriter}
                  <td><button class="small" on:click={() => lockRow(row.chainage)}>锁</button></td>
                {/if}
              {:else}
                <!-- 只有 pending 的断面：不许虚填峰值 -->
                <td class="muted">待办结</td>
                <td class="muted">—</td>
                <td><span class="tag pending">有待判读数</span></td>
                {#if isWriter}<td class="muted">办结后方可锁</td>{/if}
              {/if}
            </tr>
          {/each}
          {#if wall.length === 0}
            <tr><td colspan={isWriter ? 5 : 4} class="muted">还没有任何断面读数</td></tr>
          {/if}
        </tbody>
      </table>
    </section>

    {#if view === "desk"}
      {#if isWriter}
        <section>
          <h2>提交收敛读数</h2>
          <label>里程桩号</label>
          <input placeholder="例如 K20+050" bind:value={chainage} />
          <label>收敛（毫米，可正可负）</label>
          <input type="number" step="0.1" bind:value={deltaMm} />
          <button disabled={loading} on:click={submit}>提交（进入待认领）</button>
          {#if error}<p class="err">{error}</p>{/if}
        </section>
      {/if}
      <section>
        <h2>办结集合</h2>
        <table>
          <thead>
            <tr><th>编号</th><th>桩号</th><th>收敛mm</th><th>状态</th><th>结论</th><th>说明</th></tr>
          </thead>
          <tbody>
            {#each logs as row}
              <tr>
                <td>{row.id}</td>
                <td>{row.chainage}</td>
                <td>{row.delta_mm}</td>
                <td><span class="tag {row.status === 'pending' ? 'pending' : 'ok'}">{row.status === 'pending' ? '待处理' : '已完成'}</span></td>
                <td>
                  {#if row.verdict}
                    <span class="tag {row.verdict === '合格' ? 'ok' : 'bad'}">{row.verdict}</span>
                  {:else}—{/if}
                </td>
                <td>{row.reason ?? "—"}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </section>
    {:else}
      <section>
        <h2>锁区专页（冻结只读副本）</h2>
        {#if isWriter}
          <p class="ro-tip">手改数字直接写入锁区，不会回到办结集合重算，也不会被新办结覆盖。</p>
        {:else}
          <p class="ro-tip">巡检岗只许观看，锁定与手改仅测量员可用。</p>
        {/if}
        <table>
          <thead>
            <tr><th>断面</th><th>锁定峰值 mm</th><th>峰值时刻</th><th>锁定时刻</th><th>锁定人</th>{#if isWriter}<th>手改</th>{/if}</tr>
          </thead>
          <tbody>
            {#each locks as lk}
              {#if drafts[lk.id]}
              <tr>
                <td>{lk.chainage}</td>
                {#if isWriter}
                  <td><input class="edit-num" type="number" step="0.1" bind:value={drafts[lk.id].peak_mm} /></td>
                  <td><input class="edit-time" type="datetime-local" bind:value={drafts[lk.id].peak_at} /></td>
                {:else}
                  <td class="peak frozen">{lk.peak_mm}</td>
                  <td>{fmt(lk.peak_at)}</td>
                {/if}
                <td>{fmt(lk.locked_at)}</td>
                <td>{lk.locked_by}</td>
                {#if isWriter}
                  <td>
                    <button class="small" disabled={drafts[lk.id].saving} on:click={() => saveLock(lk)}>保存</button>
                    {#if drafts[lk.id].err}<div class="err" style="font-size:0.75rem;">{drafts[lk.id].err}</div>{/if}
                  </td>
                {/if}
              </tr>
              {/if}
            {/each}
            {#if locks.length === 0}
              <tr><td colspan={isWriter ? 6 : 5} class="muted">尚无锁定断面，请回到峰值墙对已办结断面点“锁”。</td></tr>
            {/if}
          </tbody>
        </table>
      </section>
    {/if}
  {/if}
</main>
