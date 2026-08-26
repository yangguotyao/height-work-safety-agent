(() => {
  const host = document.querySelector('#modelCallMonitor');
  if (!host) return;

  const style = document.createElement('style');
  style.textContent = `
    #modelCallMonitor { margin-top:18px; }
    .monitor-head { display:flex; justify-content:space-between; gap:12px; align-items:center; }
    .monitor-head button,.call-row button { width:auto; min-height:36px; padding:7px 12px; }
    .monitor-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin-top:14px; }
    .monitor-metric { background:#f1f5f3; border-radius:10px; padding:10px; }
    .call-list { display:grid; gap:10px; margin-top:14px; }
    .call-row { border:1px solid #dce5df; border-radius:10px; padding:12px; }
    .call-row-head { display:flex; justify-content:space-between; gap:10px; align-items:center; }
    .call-meta { color:#64726b; font-size:13px; line-height:1.6; }
    .call-detail { max-height:520px; overflow:auto; white-space:pre-wrap; word-break:break-word;
      background:#101713; color:#dff2e7; border-radius:8px; padding:12px; font:12px/1.55 Consolas,monospace; }
  `;
  document.head.append(style);

  let runId = '';
  let loadedRunId = '';

  const statusLabels = {
    running:'请求中', http_completed:'HTTP已返回', parsed:'解析成功', parse_partial:'部分解析',
    validated:'校验成功', validation_partial:'部分校验', persisted:'结果已保存',
    failed:'请求失败', parse_failed:'解析失败', validation_failed:'校验失败',
    persistence_failed:'保存失败', completed:'已完成'
  };

  function metric(label, value) {
    const item = document.createElement('div'); item.className = 'monitor-metric';
    const title = document.createElement('div'); title.className = 'call-meta'; title.textContent = label;
    const number = document.createElement('strong'); number.textContent = value;
    item.append(title, number); return item;
  }

  async function showDetail(callId, container, button) {
    if (container.dataset.loaded === 'true') {
      container.hidden = !container.hidden;
      button.textContent = container.hidden ? '查看输入输出' : '收起输入输出';
      return;
    }
    button.disabled = true; button.textContent = '加载中…';
    try {
      const response = await fetch(`/model-calls/${callId}`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || '读取调用详情失败');
      container.textContent = `【输入】\n${JSON.stringify(body.request, null, 2)}\n\n【输出】\n${JSON.stringify(body.response, null, 2)}${body.error ? `\n\n【错误】\n${body.error}` : ''}`;
      container.dataset.loaded = 'true'; container.hidden = false;
      button.textContent = '收起输入输出';
    } catch (error) {
      container.textContent = error.message || String(error); container.hidden = false;
      button.textContent = '重试';
    } finally { button.disabled = false; }
  }

  async function loadCalls() {
    if (!runId) return;
    const button = host.querySelector('[data-load-calls]');
    button.disabled = true; button.textContent = '加载中…';
    try {
      const response = await fetch(`/audits/${runId}/model-calls`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || '读取模型调用记录失败');
      loadedRunId = runId;
      const summary = body.summary || {};
      const grid = host.querySelector('.monitor-grid'); grid.replaceChildren(
        metric('实际请求', summary.call_count || 0),
        metric('成功', summary.completed_count || 0),
        metric('失败', summary.failed_count || 0),
        metric('解析失败', summary.parse_failed_count || 0),
        metric('部分校验', summary.validation_partial_count || 0),
        metric('输入 Token', Number(summary.prompt_tokens || 0).toLocaleString()),
        metric('输出 Token', Number(summary.completion_tokens || 0).toLocaleString()),
        metric('总 Token', Number(summary.total_tokens || 0).toLocaleString()),
      );
      const list = host.querySelector('.call-list'); list.replaceChildren();
      for (const call of body.items || []) {
        const row = document.createElement('article'); row.className = 'call-row';
        const head = document.createElement('div'); head.className = 'call-row-head';
        const title = document.createElement('strong');
        title.textContent = `${call.purpose} · ${statusLabels[call.status] || call.status} · 第${call.attempt}次尝试`;
        const detailButton = document.createElement('button'); detailButton.type = 'button';
        detailButton.textContent = '查看输入输出';
        head.append(title, detailButton);
        const meta = document.createElement('div'); meta.className = 'call-meta';
        meta.textContent = `HTTP：${call.http_status}；解析：${call.parse_status}；校验：${call.validation_status}；保存：${call.persistence_status}；输入 ${call.prompt_tokens || 0} / 输出 ${call.completion_tokens || 0} Token；耗时 ${Math.round(call.latency_ms || 0)}ms；${JSON.stringify(call.metadata || {})}`;
        const detail = document.createElement('pre'); detail.className = 'call-detail'; detail.hidden = true;
        detailButton.addEventListener('click', () => showDetail(call.id, detail, detailButton));
        row.append(head, meta, detail); list.append(row);
      }
      button.textContent = '刷新调用记录';
    } catch (error) {
      button.textContent = '加载失败，点击重试';
      host.querySelector('.call-list').textContent = error.message || String(error);
    } finally { button.disabled = false; }
  }

  function renderShell() {
    host.hidden = false;
    host.className = 'panel';
    host.innerHTML = `<div class="monitor-head"><div><strong>模型调用监控</strong><div class="call-meta">独立调试模块，可查看每次外部模型请求的用途、耗时、Token、完整输入和输出。</div></div><button type="button" data-load-calls>加载调用记录</button></div><div class="monitor-grid"></div><div class="call-list"></div>`;
    host.querySelector('[data-load-calls]').addEventListener('click', loadCalls);
  }

  window.addEventListener('audit:updated', event => {
    const nextRunId = event.detail?.id || '';
    if (!nextRunId) return;
    if (runId !== nextRunId) {
      runId = nextRunId; loadedRunId = ''; renderShell();
    } else if (loadedRunId === runId) {
      host.querySelector('[data-load-calls]').textContent = '有新进度，点击刷新';
    }
  });
})();
