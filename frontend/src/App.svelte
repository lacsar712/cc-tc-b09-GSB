<script>
  let session = null;
  let logs = [];
  let peaks = [];
  let locks = [];
  let view = "main";
  let loginUser = "surveyor";
  let loginPass = "surv123456";
  let chainage = "";
  let deltaMm = "";
  let error = "";
  let loading = false;
  let timer;

  $: isWriter = session?.role === "writer";

  function headers() {
    return session ? { Authorization: "Bearer " + session.token } : {};
  }

  async function refresh() {
    if (!session) return;
    const opts = { headers: headers() };
    const [logsRes, peaksRes, locksRes] = await Promise.all([
      fetch("/api/logs", opts),
      fetch("/api/peaks", opts),
      fetch("/api/locks", opts),
    ]);
    if (logsRes.status === 401 || peaksRes.status === 401 || locksRes.status === 401) {
      logout();
      return;
    }
    if (logsRes.ok) logs = await logsRes.json();
    if (peaksRes.ok) peaks = await peaksRes.json();
    if (locksRes.ok) locks = await locksRes.json();
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
      await refresh();
      timer = setInterval(refresh, 2000);
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
    peaks = [];
    locks = [];
    view = "main";
    localStorage.removeItem("tunnel_session");
  }

  async function submit() {
    error = "";
    loading = true;
    try {
      const res = await fetch("/api/logs", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...headers() },
        body: JSON.stringify({ chainage, delta_mm: Number(deltaMm) }),
      });
      const data = await res.json();
      if (!res.ok) {
        error = data.detail || "提交失败";
        return;
      }
      chainage = "";
      deltaMm = "";
      await refresh();
    } catch {
      error = "提交时网络异常";
    } finally {
      loading = false;
    }
  }

  async function lockPeak(target) {
    error = "";
    loading = true;
    try {
      // 只发断面桩号，峰值与时刻由服务端按办结集合重算后写入锁区
      const res = await fetch("/api/peaks/lock", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...headers() },
        body: JSON.stringify({ chainage: target }),
      });
      const data = await res.json();
      if (!res.ok) {
        error = data.detail || "锁定失败";
        return;
      }
      await refresh();
    } catch {
      error = "锁定时网络异常";
    } finally {
      loading = false;
    }
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString();
  }

  const raw = localStorage.getItem("tunnel_session");
  if (raw) {
    try {
      session = JSON.parse(raw);
      refresh();
      timer = setInterval(refresh, 2000);
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
  main { max-width: 960px; margin: 0 auto; padding: 1.5rem; }
  h1 { color: #fbbf24; margin: 0 0 0.25rem; }
  h2 { color: #fcd34d; margin: 0; font-size: 1.05rem; }
  .sub { color: #a8a29e; margin-bottom: 1.25rem; }
  section {
    background: #292524; border: 1px solid #44403c; border-radius: 8px;
    padding: 1rem 1.25rem; margin-bottom: 1rem;
  }
  .wall {
    border-color: #b45309;
    background: #241f1a;
  }
  .wall-head {
    display: flex; justify-content: space-between; align-items: center;
    gap: 0.75rem; flex-wrap: wrap; margin-bottom: 0.75rem;
  }
  .wall-head .actions { display: flex; gap: 0.5rem; }
  label { display: block; font-size: 0.85rem; color: #d6d3d1; margin-bottom: 0.25rem; }
  input {
    width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px;
    border: 1px solid #57534e; background: #0c0a09; color: #fafaf9; margin-bottom: 0.75rem;
  }
  button {
    cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px;
    background: #d97706; color: #fff; font-weight: 600;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.secondary { background: #57534e; }
  .err { color: #fb7185; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #44403c; }
  .tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
  .ok { background: #14532d; color: #86efac; }
  .bad { background: #7f1d1d; color: #fca5a5; }
  .pending { background: #713f12; color: #fde68a; }
  .locked { background: #1e3a8a; color: #bfdbfe; }
</style>

<main>
  <h1>隧道收敛测缝台</h1>
  {#if !session}
    <p class="sub">测量员提交桩号与收敛毫米值，接口进程内线程认领后出结论。登录框已预填可写账号 surveyor / surv123456。</p>
    <section>
      <label>用户名</label>
      <input bind:value={loginUser} autocomplete="off" />
      <label>密码</label>
      <input type="password" bind:value={loginPass} autocomplete="off" />
      <button disabled={loading} on:click={login}>登录</button>
      {#if error}<p class="err">{error}</p>{/if}
    </section>
  {:else if view === "main"}
    <p class="sub">已登录：{session.username}（{isWriter ? "测量员，可提交可锁定" : "巡检岗，只许观看"}）</p>
    <section class="wall">
      <div class="wall-head">
        <h2>峰值墙 · 拱顶历史峰值</h2>
        <div class="actions">
          <button class="secondary" disabled={loading} on:click={refresh}>刷新</button>
          <button class="secondary" on:click={() => (view = "locks")}>锁区专页</button>
          <button class="secondary" on:click={logout}>退出</button>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>断面</th><th>峰值mm</th><th>峰值时刻</th><th>状态</th>{#if isWriter}<th>操作</th>{/if}
          </tr>
        </thead>
        <tbody>
          {#each peaks as p}
            <tr>
              <td>{p.chainage}</td>
              <td>{p.peak_delta_mm ?? "—"}</td>
              <td>{fmtTime(p.peak_at)}</td>
              <td>
                {#if p.locked}
                  <span class="tag locked">已锁</span>
                {:else if p.peak_delta_mm == null}
                  <span class="tag pending">待办结</span>
                {:else}
                  <span class="tag ok">未锁</span>
                {/if}
              </td>
              {#if isWriter}
                <td>
                  {#if !p.locked && p.peak_delta_mm != null}
                    <button disabled={loading} on:click={() => lockPeak(p.chainage)}>锁定</button>
                  {:else}—{/if}
                </td>
              {/if}
            </tr>
          {:else}
            <tr><td colspan={isWriter ? 5 : 4}>暂无断面</td></tr>
          {/each}
        </tbody>
      </table>
      {#if error}<p class="err">{error}</p>{/if}
    </section>
    {#if isWriter}
      <section>
        <label>里程桩号</label>
        <input placeholder="例如 K20+050" bind:value={chainage} />
        <label>收敛（毫米，可正可负）</label>
        <input type="number" step="0.1" bind:value={deltaMm} />
        <button disabled={loading} on:click={submit}>提交（进入待认领）</button>
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
    <p class="sub">已登录：{session.username}（{isWriter ? "测量员，可提交可锁定" : "巡检岗，只许观看"}）</p>
    <section class="wall">
      <div class="wall-head">
        <h2>锁区专页 · 只读副本</h2>
        <div class="actions">
          <button class="secondary" disabled={loading} on:click={refresh}>刷新</button>
          <button class="secondary" on:click={() => (view = "main")}>返回峰值墙</button>
        </div>
      </div>
      <p class="sub">锁定那一刻的峰值与时刻已压成只读副本，任何角色都只能观看，之后新办结不再改动这些列。</p>
      <table>
        <thead>
          <tr><th>断面</th><th>锁定峰值mm</th><th>峰值时刻</th><th>锁定人</th><th>锁定时刻</th></tr>
        </thead>
        <tbody>
          {#each locks as lock}
            <tr>
              <td>{lock.chainage}</td>
              <td>{lock.peak_delta_mm}</td>
              <td>{fmtTime(lock.peak_at)}</td>
              <td>{lock.locked_by}</td>
              <td>{fmtTime(lock.locked_at)}</td>
            </tr>
          {:else}
            <tr><td colspan="5">锁区为空</td></tr>
          {/each}
        </tbody>
      </table>
    </section>
  {/if}
</main>
