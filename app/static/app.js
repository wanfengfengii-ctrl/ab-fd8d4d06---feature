"use strict";

const $ = (id) => document.getElementById(id);

const round1Fields = ["probeA", "probeB", "offsetMin", "offsetMax", "tolerance", "minPairs"];
const round2Fields = ["probeA2", "probeB2"];

let sharedMode = false;

function markStale() {
  // 旧结论立即失效：隐藏上一次结果，避免被误读为当前输入的结论。
  $("staleNote").hidden = false;
  $("resultPanel").hidden = true;
  $("errorBox").hidden = true;
}

round1Fields.forEach((id) => {
  $(id).addEventListener("input", markStale);
});
round2Fields.forEach((id) => {
  $(id).addEventListener("input", markStale);
});

$("sharedToggle").addEventListener("change", () => {
  sharedMode = $("sharedToggle").checked;
  $("round2Section").hidden = !sharedMode;
  // 任一轮/模式修改立即使旧结论失效。
  markStale();
});

function parseTimes(text) {
  return text
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function showError(message) {
  const box = $("errorBox");
  box.textContent = message;
  box.hidden = false;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function metric(key, value, diagnostic = false) {
  return `<div class="metric${diagnostic ? " diagnostic" : ""}">
    <span class="k">${key}</span><span class="v">${value}</span></div>`;
}

function renderPairs(pairs) {
  if (!pairs.length) {
    return `<h3>配对明细</h3><p class="empty-note">没有任何满足容差的配对。</p>`;
  }
  const rows = pairs.map((p) => {
    const cls = p.residual > 0 ? "res-pos" : p.residual < 0 ? "res-neg" : "res-zero";
    const sign = p.residual > 0 ? "+" : "";
    return `<tr>
      <td>#${p.index_a}</td><td>#${p.index_b}</td>
      <td>${p.a_time}</td><td>${p.corrected_b}</td>
      <td class="${cls}">${sign}${p.residual}</td>
    </tr>`;
  }).join("");
  return `<h3>配对明细（校正时间 = B 时间 + 偏移；带符号残差 = A − 校正后 B）</h3>
    <table>
      <thead><tr><th>A 序号</th><th>B 序号</th><th>A 时间 (ns)</th>
      <th>B 校正时间 (ns)</th><th>残差 (ns)</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderUnpaired(title, list) {
  if (!list.length) {
    return `<h3>${title}</h3><p class="empty-note">无未配对脉冲。</p>`;
  }
  const chips = list
    .map((u) => `<span class="chip"><b>#${u.index}</b>${u.time}</span>`)
    .join("");
  return `<h3>${title}</h3><div class="chips">${chips}</div>`;
}

function renderResult(r) {
  if (r.mode === "shared") {
    renderSharedResult(r);
  } else {
    renderSingleResult(r);
  }
}

function renderSingleResult(r) {
  const panel = $("resultPanel");
  panel.hidden = false;

  if (r.sufficient) {
    $("verdict").innerHTML =
      `<div class="verdict ok">校准成立：最佳整数时钟偏移为
        <span style="font-variant-numeric:tabular-nums">${r.offset}</span> ns，
        形成 ${r.pair_count} 对符合事件（门槛 ${r.min_pairs} 对）。</div>`;
    $("metrics").innerHTML =
      metric("最佳整数偏移 (ns)", r.offset) +
      metric("配对数", `${r.pair_count} / 门槛 ${r.min_pairs}`) +
      metric("残差绝对值总和 (ns)", r.residual_abs_sum) +
      metric("最大残差绝对值 (ns)", r.max_abs_residual);
  } else {
    // 不伪造校准值：不把任何偏移作为校准结论展示。
    $("verdict").innerHTML =
      `<div class="verdict bad">无法形成足够的符合事件：实际最大配对数为
        <span style="font-variant-numeric:tabular-nums">${r.pair_count}</span>
        对，低于最低配对数 ${r.min_pairs} 对。
        <span class="reason">${esc(r.reason || "")}</span></div>`;
    $("metrics").innerHTML =
      metric("实际最大配对数", `${r.pair_count} / 门槛 ${r.min_pairs}`) +
      metric("残差绝对值总和 (ns)", r.residual_abs_sum) +
      metric("最大残差绝对值 (ns)", r.max_abs_residual);
  }

  $("pairsWrap").innerHTML = r.sufficient
    ? renderPairs(r.pairs)
    : `<p class="diagnostic-note">以下为最大配对数（${r.pair_count} 对）的对齐明细，
        仅用于诊断，不构成校准结论，页面不给出校准偏移。</p>` +
      renderPairs((r.diagnostic && r.diagnostic.pairs) || []);
  $("unpairedA").innerHTML = renderUnpaired("未配对的 A 脉冲（序号 / 时间）", r.unpaired_a);
  $("unpairedB").innerHTML = renderUnpaired("未配对的 B 脉冲（序号 / 时间）", r.unpaired_b);
}

function renderRoundBlock(round, idx) {
  return `<div class="round-block">
      <h3>第 ${idx} 轮：实际配对 ${round.pair_count} 对
        （残差绝对值和 ${round.residual_abs_sum}，
        最大残差绝对值 ${round.max_abs_residual}）</h3>
      ${renderPairs(round.pairs)}
      <div class="grid2">
        <div>${renderUnpaired(`第 ${idx} 轮未配对的 A 脉冲`, round.unpaired_a)}</div>
        <div>${renderUnpaired(`第 ${idx} 轮未配对的 B 脉冲`, round.unpaired_b)}</div>
      </div>
    </div>`;
}

function renderSharedResult(r) {
  const panel = $("resultPanel");
  panel.hidden = false;
  // 共享模式的两轮未配对明细都在各轮分块内，清空单轮遗留容器。
  $("unpairedA").innerHTML = "";
  $("unpairedB").innerHTML = "";

  if (r.sufficient) {
    $("verdict").innerHTML =
      `<div class="verdict ok">共享偏移复核通过：两轮记录可由同一个整数时钟偏移
        <span style="font-variant-numeric:tabular-nums">${r.offset}</span> ns
        共同解释（门槛为每轮至少 ${r.min_pairs} 对）。</div>`;
    $("metrics").innerHTML =
      metric("共同整数偏移 (ns)", r.offset) +
      metric("两轮较小配对数", `${r.min_pair_count} / 门槛 ${r.min_pairs}`) +
      metric("第一轮 / 第二轮配对数", `${r.pair_count_1} / ${r.pair_count_2}`) +
      metric("两轮配对总数", r.total_pair_count) +
      metric("合并残差绝对值总和 (ns)", r.residual_abs_sum) +
      metric("两轮最大残差绝对值 (ns)", r.max_abs_residual);
    $("pairsWrap").innerHTML =
      renderRoundBlock(r.rounds[0], 1) + renderRoundBlock(r.rounds[1], 2);
  } else {
    // 任一轮未达门槛：不给出校准偏移，只展示可同时达到的最大较小配对数、
    // 两轮实际配对数与批间证据不足的原因。
    $("verdict").innerHTML =
      `<div class="verdict bad">共享偏移复核不通过：两轮无法在同一整数时钟偏移下
        同时达到门槛 ${r.min_pairs} 对；可同时达到的最大较小配对数为
        <span style="font-variant-numeric:tabular-nums">${r.min_pair_count}</span>
        对（第一轮实际 ${r.pair_count_1} 对，第二轮实际 ${r.pair_count_2} 对）。
        <span class="reason">${esc(r.reason || "")}</span></div>`;
    $("metrics").innerHTML =
      metric("可同时达到的最大较小配对数", `${r.min_pair_count} / 门槛 ${r.min_pairs}`) +
      metric("第一轮实际配对数", r.pair_count_1) +
      metric("第二轮实际配对数", r.pair_count_2) +
      metric("两轮配对总数", r.total_pair_count);
    const d = r.diagnostic;
    $("pairsWrap").innerHTML =
      `<p class="diagnostic-note">页面不给出校准偏移。以下为联合最大配对诊断
        （两轮共用同一候选偏移），仅用于说明批间证据不足，不构成校准结论。</p>` +
      renderRoundBlock(d.rounds[0], 1) +
      renderRoundBlock(d.rounds[1], 2);
  }
}

async function submit() {
  const payload = {
    probe_a: parseTimes($("probeA").value),
    probe_b: parseTimes($("probeB").value),
    offset_min: $("offsetMin").value.trim(),
    offset_max: $("offsetMax").value.trim(),
    tolerance: $("tolerance").value.trim(),
    min_pairs: $("minPairs").value.trim(),
  };
  if (sharedMode) {
    payload.shared_review = true;
    payload.probe_a_2 = parseTimes($("probeA2").value);
    payload.probe_b_2 = parseTimes($("probeB2").value);
  }

  const btn = $("submitBtn");
  btn.disabled = true;
  const oldHtml = btn.innerHTML;
  btn.innerHTML = `<span class="spinner"></span>计算中…`;
  try {
    const resp = await fetch("/api/calibrate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    let data;
    try {
      data = await resp.json();
    } catch (e) {
      throw new Error("服务返回了无法解析的响应");
    }
    if (!resp.ok) {
      showError(data.error || `请求失败（HTTP ${resp.status}）`);
      return;
    }
    renderResult(data);
    $("staleNote").hidden = true;
  } catch (err) {
    showError(`提交失败：${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = oldHtml;
  }
}

$("submitBtn").addEventListener("click", submit);
