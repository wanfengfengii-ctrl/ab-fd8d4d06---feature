"use strict";

const $ = (id) => document.getElementById(id);

const fields = [
  "probeA", "probeB",
  "round2ProbeA", "round2ProbeB",
  "offsetMin", "offsetMax", "tolerance", "minPairs",
];

function markStale() {
  // 旧结论立即失效：隐藏上一次结果，避免被误读为当前输入的结论。
  $("staleNote").hidden = false;
  $("resultPanel").hidden = true;
  $("errorBox").hidden = true;
}

fields.forEach((id) => {
  $(id).addEventListener("input", markStale);
});

function sharedEnabled() {
  return $("sharedReview").checked;
}

$("sharedReview").addEventListener("change", () => {
  const on = sharedEnabled();
  $("round2Panel").hidden = !on;
  $("sharedHint").hidden = !on;
  $("submitBtn").textContent = on ? "提交共享偏移复核" : "提交联合校准";
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

function renderRound(rnd, { showPairs, diagnosticPairs = null }) {
  const diag = diagnosticPairs
    ? `<p class="diagnostic-note">第 ${rnd.round} 轮最大配对数对齐明细（诊断用，不构成校准结论）：</p>
       ${renderPairs(diagnosticPairs)}`
    : "";
  return `<div class="round-block">
      <h3>第 ${rnd.round} 轮（实际配对 ${rnd.pair_count} 对）</h3>
      ${showPairs ? renderPairs(rnd.pairs) : diag}
      <div class="grid2">
        <div>${renderUnpaired(`第 ${rnd.round} 轮未配对的 A 脉冲`, rnd.unpaired_a)}</div>
        <div>${renderUnpaired(`第 ${rnd.round} 轮未配对的 B 脉冲`, rnd.unpaired_b)}</div>
      </div>
    </div>`;
}

function renderSingle(r) {
  $("sharedRounds").innerHTML = "";
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

function renderShared(r) {
  // 共享复核结论区独占 sharedRounds；单轮遗留容器清空，避免混淆。
  $("pairsWrap").innerHTML = "";
  $("unpairedA").innerHTML = "";
  $("unpairedB").innerHTML = "";

  const [c1, c2] = r.round_pair_counts;
  if (r.sufficient) {
    $("verdict").innerHTML =
      `<div class="verdict ok">共享偏移复核通过：两轮数据可由同一个整数时钟偏移
        <span style="font-variant-numeric:tabular-nums">${r.offset}</span> ns
        同时解释。第 1 轮 ${c1} 对、第 2 轮 ${c2} 对（每轮门槛 ${r.min_pairs} 对）。</div>`;
    $("metrics").innerHTML =
      metric("共享整数偏移 (ns)", r.offset) +
      metric("两轮较小配对数", `${r.min_pair_count} / 门槛 ${r.min_pairs}`) +
      metric("两轮配对总数", r.total_pair_count) +
      metric("合并残差绝对值总和 (ns)", r.residual_abs_sum) +
      metric("合并最大残差绝对值 (ns)", r.max_abs_residual) +
      metric("两轮实际配对数", `第 1 轮 ${c1} ｜ 第 2 轮 ${c2}`);
  } else {
    // 任一轮未达门槛：页面同样不得给出校准偏移。
    $("verdict").innerHTML =
      `<div class="verdict bad">共享偏移复核未通过：两轮可同时达到的
        <strong>最大较小配对数仅为 ${r.min_pair_count}</strong> 对
        （门槛 ${r.min_pairs} 对）；第 1 轮实际 ${c1} 对、第 2 轮实际 ${c2} 对。
        <span class="reason">${esc(r.reason || "")}</span></div>`;
    $("metrics").innerHTML =
      metric("可同时达到的最大较小配对数", `${r.min_pair_count} / 门槛 ${r.min_pairs}`) +
      metric("第 1 轮实际配对数", c1) +
      metric("第 2 轮实际配对数", c2) +
      metric("两轮配对总数", r.total_pair_count) +
      metric("合并残差绝对值总和 (ns)", r.residual_abs_sum) +
      metric("合并最大残差绝对值 (ns)", r.max_abs_residual);
  }

  const diagRounds = (!r.sufficient && r.diagnostic && r.diagnostic.rounds) || [];
  $("sharedRounds").innerHTML = r.rounds
    .map((rnd) =>
      renderRound(rnd, {
        showPairs: r.sufficient,
        diagnosticPairs: r.sufficient
          ? null
          : (diagRounds[rnd.round - 1] || {}).pairs || [],
      })
    )
    .join("");
}

function renderResult(r) {
  const panel = $("resultPanel");
  panel.hidden = false;
  if (r.shared_offset_review) {
    renderShared(r);
  } else {
    renderSingle(r);
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

  if (sharedEnabled()) {
    payload.shared_offset_review = true;
    payload.round2_probe_a = parseTimes($("round2ProbeA").value);
    payload.round2_probe_b = parseTimes($("round2ProbeB").value);
  }

  const btn = $("submitBtn");
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = "计算中…";
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
    btn.textContent = oldText;
  }
}

$("submitBtn").addEventListener("click", submit);
