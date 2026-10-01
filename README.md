# 双探头符合事件联合校准服务

核医学质控场景：对齐同一标准源在两台探头上的脉冲记录。系统**联合**选择一个
整数时钟偏移 `d`（取自闭区间 `[offset_min, offset_max]`）和一个保持输入顺序的
一对一配对，严格按以下字典序优化：

1. **最大化配对数**；
2. 在配对数相同的方案中，**最小化带符号残差的绝对值之和**
   `Σ |a_i − (b_j + d)|`；
3. 再**最小化最大残差绝对值** `max |a_i − (b_j + d)|`；
4. 仍相同则取**最小整数偏移**，再取**输入序号字典序最小**的配对。

不会“先估时钟偏移、再贪心配对”——偏移与配对在同一优化中联合决定，
因此真实符合事件不会被噪声脉冲占用。

## 为什么 20 亿纳秒区间不需要逐纳秒扫描

边 `(i, j)` 在偏移 `d` 下可配当且仅当 `|(a_i − b_j) − d| ≤ T`，即可行窗
`[c_ij − T, c_ij + T]`（`c_ij = a_i − b_j`）。求解器构造一个**有限且可证明完备
的候选偏移集合**，对每个候选运行一次 O(n·m) 的保序配对 DP：

- 每条边的 `c_ij − T`、`c_ij`、`c_ij + T`、`c_ij + T + 1`（可行窗首/末整数、
  首个不可行整数，以及残差折点）：配对数最优可取在窗口边界，残差绝对值和的
  最小值取在某个被匹配边的 `c_ij`（中位数）或被夹到的窗口边界 `c_ij ± T`；
- 任意两条**可共存于同一保序匹配**的边（序号同向）且中点处两边缘都在容差内
  （`|c_p − c_q| ≤ 2T`），加入 `(c_p + c_q)/2` 的向下/向上取整：固定匹配的最大
  残差 `max(c_max − d, d − c_min)` 的整数最小点正是两条残差极值边的中点
  （半点取较小整数，由最小偏移裁决）；
- 区间两个端点。

过滤后候选数仅数千个（24×24 最坏实测约 0.6 秒），所有访问的偏移都由配对关系的
临界值导出，绝不逐纳秒扫描。正确性由仓库内的全枚举对拍（小例逐偏移枚举所有
配对）与数千例逐纳秒 DP 密集扫描对拍保证。

## 目录

```
app/
  alignment.py    联合优化求解（临界值 + DP + 中位数/极差精化）
  validation.py   输入校验（6–24 个严格递增整数等）
  server.py       标准库 HTTP 服务：页面 / /healthz / POST /api/calibrate
  static/         录入页面
tests/            单元测试 + 全枚举/密集扫描对拍（1500+ 暴力例）
verify/
  verify.sh       一次性校验：测试 → 构建 → 宽区间 API 冒烟
  smoke_api.py    宽 ±1e9 区间 API 冒烟
Dockerfile
docker-compose.yml
```

## 运行

```bash
# 应用（宿主机端口可用 APP_PORT 配置，默认 8080）
docker compose up --build app
# 打开 http://localhost:8080

# 自定义宿主机端口
APP_PORT=9091 docker compose up --build app
```

应用容器内置 `/healthz` 健康检查。

## verify 一次性服务

`verify` 服务通过 `depends_on: condition: service_healthy` 保证在应用健康后
才启动，依次执行：

1. 全部代码测试（含与暴力枚举、逐纳秒 DP 扫描的对拍）；
2. 构建检查（`compileall`）；
3. 宽偏移区间（±1,000,000,000 ns）API 冒烟，含：
   - 大偏移精确恢复；
   - 配对数不足时 `offset` 必须为 `null` 且给出真实最大配对数与原因。

它以退出码报告结果（0 成功 / 非 0 失败）后退出：

```bash
docker compose up --build --abort-on-container-exit verify
# 或只运行一次性服务（会自动先构建镜像）：
docker compose run --build verify; echo "exit=$?"
```

本地（无 Docker）也可运行：

```bash
python3 -m unittest discover -s tests -v
APP_PORT=8080 python3 -m app.server &
APP_BASE_URL=http://127.0.0.1:8080 ./verify/verify.sh
```

## API

`POST /api/calibrate`

```json
{
  "probe_a": [100, 240, 388, 512, 665, 820],
  "probe_b": [18, 159, 306, 431, 585, 740],
  "offset_min": -1000000000,
  "offset_max": 1000000000,
  "tolerance": 25,
  "min_pairs": 4
}
```

成功（达到最低配对数）返回 `offset`、每对的 `corrected_b`（= b + 偏移）、
带符号 `residual`（= a − 校正后 B）以及两侧未配对脉冲。

**配对数不足时不伪造校准值**：`sufficient=false`、`offset=null`、`pairs=[]`，
仅返回真实 `pair_count` 与中文 `reason`；最大配对数对齐明细放在明确标注的
`diagnostic` 字段，仅供诊断。页面同步显示“实际最大配对数 + 无法形成足够符合
事件的原因”，不展示校准结论。

页面任一字段被修改后，旧结论立即隐藏并提示“输入已修改，旧结论已失效”，
重新提交才会再次调用真实 API。
