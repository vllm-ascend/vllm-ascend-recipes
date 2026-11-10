# Recipe CI 流程模板（以 DeepSeek-V4-Flash 为例）

本文用 `recipes/overlays/deepseek-ai/DeepSeek-V4-Flash.yaml` 走一遍完整流程，用于确认整体思路。
MAP 驱动的实现落盘前，先按本模板评审。

## 1. 目标与边界

目标：给定「模型 + HARDWARE + VARIANT + FEATURES」组合，从 recipes 站点取到该组合的渲染
结果，还原成可直接在 NPU 容器内执行的 `vllm serve` 脚本并验证服务可用。

**本期只做单机**：`nodes=1`、`deploy_type=single_node`、无 PD 分离。站点返回非单机结果时
该 combo 记 skip（判定规则见 4.2 第 3 条）。多机与 PD 分离不实现，但在 MAP / 断言 /
result / 执行四层预留扩展口（见第 11 节）。

本期不覆盖：多节点（`node_count > 1`）、`pd_cluster` 及 `multi_node_*` 策略、性能与精度基线。

## 2. 端到端流程

```text
MAP.json
   │  每个 entry = 一个模型 YAML；combos[] = HARDWARE / VARIANT / FEATURES
   ▼
[1] 解析组合 → 拼 URL
   ▼
[2] 取数：优先静态 JSON API，非默认 FEATURES 走页面渲染
   ▼
[3] 解析顶层 argv / env（renderer 已归一化）
   │   断言 argv 以 vllm serve 开头
   │   env → export；ASCEND_RT_VISIBLE_DEVICES 有则覆盖、无则补设
   │   解析 --port（缺省 8000）
   ▼
[4] 生成 run.sh（+ companions 脚本）→ 分配 NPU → 执行 → 探活
   ▼
[5] 输出 result.json + 日志 artifact → PR 汇总
```

## 3. MAP 文件格式

```json
{
  "schema_version": 1,
  "site": "https://recipes.vllm.ai",
  "entries": [
    {
      "name": "DeepSeek-V4-Flash",
      "yaml": "recipes/overlays/deepseek-ai/DeepSeek-V4-Flash.yaml",
      "hf_id": "deepseek-ai/DeepSeek-V4-Flash",
      "combos": [
        {
          "id": "a5-950dt-default-dspark",
          "hardware": "ascend_950dt_8x",
          "variant": "default",
          "features": {},
          "expect": { "spec_decoding": "dspark" },
          "nodes": 1
        },
        {
          "id": "a5-950dt-default-dflash",
          "hardware": "ascend_950dt_8x",
          "variant": "default",
          "features": { "spec_decoding": "dflash" },
          "nodes": 1
        },
        {
          "id": "a3-910c-w8a8-mtp",
          "hardware": "ascend_910c",
          "variant": "ascend_w8a8",
          "features": { "spec_decoding": "mtp" },
          "nodes": 1
        }
      ]
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `hf_id` | 是 | 站点路径，对应 `/<hf_org>/<hf_repo>` |
| `yaml` | 是 | 仅用于溯源与「站点/本地 overlay 是否漂移」检查 |
| `hardware` | 是 | taxonomy 的 profile id，如 `ascend_910c`、`ascend_950dt_8x` |
| `variant` | 是 | recipe 的 variant key，如 `default`、`ascend_w8a8` |
| `features` | 否 | `{feature_id: mode_id}`；`{}` 表示用该 variant 的默认特性 |
| `expect` | 否 | 期望断言值（如 `{"spec_decoding": "dspark"}`）；缺省时只记录实际渲染值不断言（见 4.2） |
| `nodes` | 否 | 预留多机扩展位，当前必须为 1；`> 1` 的组合记 `skip: multi_node not supported yet` |

注意：`ascend_w8a8` 这类 variant 的 MTP 组合必须走 variant 提升后的 endpoint
（见 4.1），MAP 生成 URL 时自动处理；`default` variant 在 A5 上请求 `mtp` 会被
站点静默回退成 `dspark`（见 4.2），**此类组合不应写进 MAP**。

`yaml` 与站点不是同一份数据：站点发布的是 renderer 合并后的结果，本地 overlay 可能尚未发布。
CI 以站点为准，`yaml` 只做漂移告警，不做命令来源。

全局 `site` 默认 `https://recipes.vllm.ai`；未合入的 recipe（如 PR21065 的 MiniMax-M3）
只有 PR 的 Vercel preview 站有渲染结果，entry 级可用 `site` 覆盖，合入后移除。
`source_url` 照常记录，保证可追溯。

## 4. Step 1：由组合拼 URL

站点只有一种取数方式：**带 query 参数的 JSON API（服务器端渲染）**。

### 4.1 URL 格式

```text
{site}/{hf_id}.json?hardware={hw}&variant={variant}&strategy={strategy}&features={f1,f2}&fmode={f1:m1,f2:m2}&frontend={frontend}&nodes=1
```

参数格式与页面 `CommandBuilder.jsx` 完全一致：`features` 是逗号分隔的 feature id，
`fmode` 是 URL 编码后的 `feature:mode` 逗号分隔对（`:` 编码为 `%3A`）。
已实测：`.json` 端点在服务器端处理这些参数并返回该组合的完整渲染结果（含
`command` / `argv` / `docker_command` / `docker_argv` / `companions` / `selection` 回显），
**无需无头浏览器**。

DeepSeek-V4-Flash 的实际地址（均已实测）：

| 组合 | URL | 返回的投机解码 |
| --- | --- | --- |
| A5 DEP 默认 | `https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash.json?hardware=ascend_950dt_8x&variant=default` | dspark |
| A5 DFlash | `.../DeepSeek-V4-Flash.json?hardware=ascend_950dt_8x&variant=default&features=spec_decoding&fmode=spec_decoding%3Adflash` | dflash ✅ |
| A3 W8A8 + MTP | `https://recipes.vllm.ai/Eco-Tech/DeepSeek-V4-Flash-w8a8-mtp.json?hardware=ascend_910c&variant=default&features=spec_decoding&fmode=spec_decoding%3Amtp` | mtp（NPU override：1 token + enforce_eager）✅ |

形态 B（PR21065，未合入，preview 站实测）：
`https://vllm-recipes-git-preview-vllm-ascend-pr-21065-inferact-inc.vercel.app/MiniMaxAI/MiniMax-M3.json?hardware=ascend_910c&variant=ascend_w8a8`
→ 返回 `vllm serve Eco-Tech/MiniMax-M3-w8a8-0626 ...` 完整渲染，query 协议与正式站一致。

`variant` 注意事项：**主路径 = 父端点 + variant 原名**（实测 `deepseek-ai/DeepSeek-V4-Flash.json?hardware=ascend_910c&variant=ascend_w8a8` 现在直接返回正确渲染）。若父端点 404，回退到 bare JSON 中 `variants[<variant>].json` 指针指向的提升端点（variant 键保持原名，不再重写为 `default`）。早期「父路径 404、提升端点内 key 重写为 `default`」的行为已被站点更新取代，CI 不能依赖它。

### 4.2 静默回退与内容断言（关键）

服务器**不校验 fmode 与 variant 的兼容性**：当请求的 mode 不在该 variant 支持列表时，
静默回退到 variant 的默认 mode，且 `selection.modes` 回显的仍是请求值。实测：

- `deepseek-ai/...json?hardware=ascend_950dt_8x&variant=default&features=spec_decoding&fmode=spec_decoding%3Amtp`
  → 返回 **dspark**（`mtp` mode 的 `variants` 列表是
  `[fp8, nvfp4, redhat_nvfp4, ascend_w8a8]`，不含 `default`）

因此 CI 在拿到 JSON 后必须做**内容断言**，不能只看 HTTP 200：

1. 解析 `command` 中的 `--speculative-config`，断言 `method` 与 MAP 一致：显式请求
   fmode 时必须等于请求值；`features: {}` 时把实际 method 记入 `result.json` 的
   `features` 字段，MAP 带 `expect` 字段则升级为强断言。注意比较的是 `method` 值，
   与 mode id 可能不同名（mode `eagle3_gqa` 渲染 `"method": "eagle3"`），`expect` 里
   写 method 值
2. 断言 `--data-parallel-size` / `--tensor-parallel-size` 与期望一致
3. **单机边界（多机/PD 扩展闸门，见第 11 节）**：断言 `deploy_type == single_node`、
   `node_count == 1`、`strategy` ∉ `multi_node_*` ∪ `pd_cluster`、`selection.pd` 为空；
   不满足记 `skip: multi_node/pd not supported yet`
4. **`selection.adjusted` 必须为空**：实测站点对 hardware/variant 的静默改写会记入该数组
   （如 `ascend_910c` 在不支持当前 variant 时被改写成 `h200` 并注明原因），且此时
   `selection.hardware` 回显的已是改写后的值。非空 → `fail: command fallback mismatch`。
   注意 **fmode 回退不记入 `adjusted`**（实测 A5 default 请求 `mtp` 返回 `dspark`、
   `adjusted` 仍为空），所以第 1 条的方法断言不可由本条替代。

注意「features 请求为空 ⇒ 无 feature 参数」**不成立**：variant 会注入默认 mode（实测
DeepSeek-V4-Flash 默认组合渲染 `dspark`；MiniMax-M3 经 `default_modes` 渲染
`eagle3_gqa`，且 `--speculative-config` 带 `model` 字段指向 draft 模型，权重检查见 7）。

断言失败 → `fail: command fallback mismatch`，提示该组合在站点上不可用。

## 5. Step 2：解析渲染结果

主路径取 JSON 顶层 `argv` + `env`：renderer 已归一化，两种命令形态（见 5.2）无差别，
不再解析 `docker run`、不再剥离 `--device` / `-v` / `--privileged` 等容器参数
（这些由 runner 环境保证）。

### 5.1 顶层字段与生成规则

| 字段 | 用途 |
| --- | --- |
| `argv` | 内层命令（已分词），恒以 `vllm serve` 开头 |
| `env` | 环境变量 dict，逐项生成 `export K=V` |
| `docker_image` | 与 runner 的 `VLLM_ASCEND_IMAGE` 做一致性断言 |
| `strategy` / `deploy_type` / `node_count` / `selection` | 单机边界与回退断言（4.2） |
| `companions` | companion 命令（6.2） |

生成规则：

1. 断言 `argv[:2] == ["vllm", "serve"]`，不满足直接失败——未来 recipe 可能把
   `docker_serve_command` 设为其他命令，不猜语义
2. `argv` 逐 token shell quoting 后重组命令行（token 含 JSON 串，如 `--additional-config`）
3. `ASCEND_RT_VISIBLE_DEVICES`：**有则覆盖、无则补设**为本次分配卡号（实测：存量
   recipe 带 `0,1,2,3`；MiniMax-M3 未带，靠 `--device` 全直通，不补设会在并发时抢卡）
4. `env` 值含 `$VAR`（如 `LD_PRELOAD`）在生成时展开，置于 `set -u` 之前
5. `--port` 从 `argv` 解析，缺省 8000（实测：存量带 `--port 8100`，MiniMax-M3 未带）

关键假设：**CI job 直接运行在 recipe 指定的镜像内**。执行前用 `VLLM_ASCEND_IMAGE` 与
`docker_image` 做一致性断言；不一致直接失败，避免验证到错误版本。镜像 tag 已多版本并存
（v0.23.0、v0.30.0、nightly、模型专属 tag、SWR 镜像），断言必须逐 combo 做。

### 5.2 两种命令形态（背景，主路径无需感知）

`docker_command` 存在两种形态，差异仅在镜像之后；顶层 `argv` 对两者等价（形态 A =
`vllm serve` + 镜像后 token；形态 B = 镜像后 token 原样）：

```bash
# 形态 A（存量，overlay 未声明 docker_serve_command，镜像 ENTRYPOINT 即 serve）
  quay.io/ascend/vllm-ascend:v0.23.0-a5 deepseek-ai/DeepSeek-V4-Flash-0731 \
  --trust-remote-code ...

# 形态 B（PR21065 起，overlay 显式声明 docker_serve_command: "vllm serve"）
  quay.io/ascend/vllm-ascend:v0.30.0-a3 \
  vllm serve Eco-Tech/MiniMax-M3-w8a8-0626 \
  --block-size 128 ...
```

若走 `docker_argv` 解析（不作为主路径），形态 B 必须加「已以 `vllm serve` 开头则不
重复添加」守卫，否则生成 `vllm serve vllm serve ...` 的坏命令。`docker_argv` 现仅做
交叉校验：`docker_image` 的值必须出现在其中。

### 5.3 实测对照（存量 A5 vs MiniMax-M3 preview）

| | DeepSeek-V4-Flash A5 `default` | MiniMax-M3 A3 `ascend_w8a8` |
| --- | --- | --- |
| image 后 token | `deepseek-ai/...-0731 --trust-remote-code` | `vllm serve Eco-Tech/... --block-size 128` |
| 顶层 `argv[0:3]` | `vllm serve deepseek-ai/...` | `vllm serve Eco-Tech/...` |
| `env` | 7 项，含 `ASCEND_RT_VISIBLE_DEVICES=0,1,2,3` | 4 项，无 `ASCEND_RT_VISIBLE_DEVICES` |
| `--port` | `8100` | 无（默认 8000） |
| 镜像 | `v0.23.0-a5` | `v0.30.0-a3` |
| strategy / companion | `single_node_dep` / `vllm-router` | `single_node_tp` / 无 |

### 5.4 生成输出

```bash
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export OMP_PROC_BIND=false
export OMP_NUM_THREADS=10
export HCCL_BUFFSIZE=1024
export ASCEND_RT_VISIBLE_DEVICES=4,5,6,7   # 有则覆盖、无则补设，示例值
export VLLM_USE_RUST_FRONTEND=1
```

```bash
vllm serve deepseek-ai/DeepSeek-V4-Flash-0731 \
  --trust-remote-code \
  --enable-expert-parallel \
  --port 8100 \
  --data-parallel-size 4
```

### 5.5 A3 组合差异

`Eco-Tech/DeepSeek-V4-Flash-w8a8-mtp` + `ascend_910c` 的解析结果额外包含：

- `export VLLM_USE_MODELSCOPE=True`
- `export LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libjemalloc.so.2:$LD_PRELOAD`
- `--quantization ascend`

`LD_PRELOAD` 必须在生成脚本时展开，否则 `set -u` 下引用未定义变量会直接退出。

## 6. Step 3：资源与 companion

### 6.1 NPU 数量推导

```text
required_npu = tp × pp × dp
dp = --data-parallel-size（缺省 1）
tp = --tensor-parallel-size（缺省 1）
pp = --pipeline-parallel-size（缺省 1）
```

再与 `ASCEND_RT_VISIBLE_DEVICES` 的卡数取较大值（该变量缺失时按推导值判定）。实测结果：

| combo | 推导 | runner |
| --- | --- | --- |
| A5 `default` | dp=4 → 4 | `linux-aarch64-a5-4` |
| A3 `ascend_w8a8` | dp=4 → 4 | `linux-aarch64-a3-4` |
| MiniMax A3 `ascend_w8a8` | TP4×DP4 → 16 | `linux-aarch64-a3-800t-16` |
| MiniMax A3 `ascend_w8a8_1m` | TP4×PP4×DP1 → 16 | `linux-aarch64-a3-800t-16` |
| MiniMax A5 `ascend_mxfp8` | TP4×DP2 → 8 | 无 8 卡 A5 runner → `skip: insufficient NPU` |

注意：A3 variant 的 `description` 写的是 `DP2+TP8`（16 卡），但站点当前渲染结果是
`--data-parallel-size 4`（4 卡）。这类「描述与渲染不一致」需要单独告警给 recipe 维护者，
CI 以渲染结果为准。

### 6.2 companion 进程

`single_node_dep` 策略在 JSON 的 `companions` 里给出 `vllm-router`：

```bash
vllm-router \
  --host 0.0.0.0 \
  --port 8000 \
  --policy consistent_hash \
  --intra-node-data-parallel-size 4 \
  --worker-urls http://localhost:8100 \
  --worker-startup-timeout-secs 1800
```

处理方式：

- `after: true` → 引擎就绪后启动
- `{dp_size}`、`{port}` 用解析出的实际值替换
- 镜像内若无 `vllm-router`，按 `description` 中的 `uv pip install vllm-router` 安装；安装失败则该 combo 记 `skip`，不算失败

## 7. Step 4：执行与判定

`run.sh` 骨架：

```bash
#!/usr/bin/env bash
set -euo pipefail

export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export ASCEND_RT_VISIBLE_DEVICES="${CI_NPU_LIST:?}"

npu-smi info > /dev/null

vllm serve deepseek-ai/DeepSeek-V4-Flash-0731 \
  --trust-remote-code \
  --enable-expert-parallel \
  --port 8100 \
  --data-parallel-size 4
```

判定顺序：

1. 启动前：`npu-smi info` 可见卡数 ≥ `required_npu`，否则 `skip: insufficient NPU`
2. 权重：`model_id` 未命中缓存 → `skip: weights not cached`（沿用旧仓 `_cache_paths.yaml`
   思路）。**draft 模型一并检查**：`--speculative-config` 含 `model` 字段时（如
   `Inferact/MiniMax-M3-EAGLE3-GQA`）该模型也要命中缓存，否则失败发生在就绪阶段、
   白烧整段卡时；缓存路径按 `VLLM_USE_MODELSCOPE` 区分 ModelScope / HF
3. 就绪：轮询 `http://127.0.0.1:{port}/v1/models`（port 为 5.1 解析值，缺省 8000），
   超时 1800s；有 companion 时再轮询 companion 端口
4. 冒烟：`POST /v1/chat/completions`，断言 `choices[0].message.content` 非空
5. 收尾：杀掉进程组，收集 `serve.log`、`npu-smi info`、耗时

超时建议：A5 200GB 级权重首载，就绪等待 1800s；总 job 上限 90 分钟。例外：combo 的
`env` 自带 `VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS`（MiniMax 1M 组合为 7200s）时，就绪
超时与 job 上限按该值放宽，否则 1M 组合会被误杀；不放宽的这类组合记 skip 也可。

## 8. Step 5：结果输出

每个 combo 产出 `result.json`：

```json
{
  "combo_id": "a5-950dt-default-dspark",
  "hf_id": "deepseek-ai/DeepSeek-V4-Flash",
  "hardware": "ascend_950dt_8x",
  "variant": "default",
  "features": { "spec_decoding": "dspark" },
  "image": "quay.io/ascend/vllm-ascend:v0.23.0-a5",
  "resolved_strategy": "single_node_dep",
  "deploy_type": "single_node",
  "required_npu": 4,
  "port": 8100,
  "status": "pass",
  "readiness_seconds": 412,
  "source_url": "https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash.json?hardware=ascend_950dt_8x&variant=default&nodes=1"
}
```

`status` 取值：`pass` / `fail` / `skip`（附 `skip_reason`）。PR 汇总按 `hf_id` 聚合，
失败的 combo 附最后 100 行 `serve.log`。

`source_url` 保留完整 query 串，保证结果可追溯、可复现取数。

## 9. 与现有 CI 的衔接

- 复用 [runner_label.json](file:///d:/project/vLLM/vllm-ascend/.github/workflows/scripts/runner_label.json) 做 runner 库存与卡数映射
- 复用 [recipes.yml](file:///d:/project/vLLM/vllm-ascend/.github/workflows/recipes.yml) 的触发路径与 `Recipes` 校验，新增 job 而不替换
- 渲染/解析阶段在 `ubuntu-latest` 完成，NPU 阶段只接收生成好的 `run.sh`，避免在 runner 上跑 Node 依赖

## 10. 风险与待确认

1. **HTTP 取数与网络依赖**：NPU runner 通常无外网。方案是 `ubuntu-latest` 取数并生成脚本，作为 artifact 传给 NPU job。
2. **站点静默回退**（见 4.2）：fmode 与 variant 不兼容时服务器不报错，回退到默认 mode。CI 必须做内容断言；不兼容组合（如 A5 default + MTP）不进 MAP。
3. **A5 runner 只有 4 卡**：`ascend_950pr_8x/950dt_8x` 是 8 卡 profile，DeepSeek 当前渲染
   只用 4 卡，`linux-aarch64-a5-4` 可满足；MiniMax-M3 的 A5 组合（TP4×DP2 / TP8 /
   TP4×PP2）需要 8 卡，新 runner 就位前这类 combo 只能 skip。
4. **A3 `ascend_910c` 未发布到父 recipe**：必须走 variant 提升后的 endpoint；若上游取消 variant 提升，URL 规则要同步调整。
5. **`ASCEND_RT_VISIBLE_DEVICES` 覆盖**：站点渲染的是 recipe 作者环境（如 `0,1,2,3`），CI 必须按实际分配改写，否则多 job 并发会抢卡。
6. **`LD_PRELOAD` 展开**：`$LD_PRELOAD` 需要在生成脚本时展开，且要在 `set -u` 之前处理。
7. **companion 依赖**：`vllm-router` 是否在镜像内，需要实测；不在则每次 pip 安装会引入网络依赖。
8. **query 端点依赖站点部署形态**：`.json` + query 是服务器端动态渲染（响应含 `selection` 回显与 `page_url`）。若上游未来改回纯静态导出，此能力可能消失——届时回退方案是本地跑 renderer 的 `resolveCommand`。建议 CI 加一个「无 query 与有 query 结果应不同」的探针用例，检测到回退立即告警。
9. **镜像版本分叉**：recipe 已出现 v0.23.0、v0.30.0、nightly、模型专属 tag
   （qwen3.8-a5、kimi-k3-a3）及华为 SWR 镜像并存；runner 镜像池覆盖是前置条件，
   `VLLM_ASCEND_IMAGE` 与 `docker_image` 不匹配时记 skip，不算失败。
10. **draft 模型权重**：eagle3 类 spec config 带 `model` 字段，权重检查必须覆盖
    （见 7），否则失败发生在就绪阶段，浪费整段卡时。
11. **1M 组合超时**：`VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=7200` 超过默认 90 分钟
    job 上限，需 per-combo 放宽或先 skip（见 7）。
12. **未合入 recipe 的取数**：preview 站（Vercel）可用但随 PR 更新重建、URL 与正式站
    不同；entry 级 `site` 覆盖（见 3），合入后切回正式站，`source_url` 保证可追溯。

## 11. 多机与 PD 分离扩展口

本期只做单机；多机（`multi_node_*` 策略）与 PD 分离（`pd_cluster`）不实现，但四层口子
已预留——扩展时翻转闸门、填实现即可，不动单机主路径：

| 层 | 本期行为 | 扩展时改什么 |
| --- | --- | --- |
| MAP 层 | `nodes` 必须为 1，`> 1` 记 `skip: multi_node not supported yet`（第 3 节） | 放开 `> 1`，补 `hosts`、每节点卡数 |
| 断言层（闸门） | 4.2 第 3 条：`deploy_type` / `node_count` / `strategy` / `selection.pd` 四项单机断言，不过则 skip | 放开对应断言即开启该形态，单机组合零影响 |
| scenario 层 | `result.json` 记 `resolved_strategy` + `deploy_type` | 多机补 `node_count`、`hosts`、Ray 启动记录；PD 补 `role`（P/D）与 connector 类型 |
| 执行层 | `run.sh` 生成器按 `deploy_type` 分派，`multi_node` / `pd` 分支留空实现 | 多机分支填 `ray start` + 多节点 `vllm serve` 编排；PD 分支填 1P1D / 1P2D 启动编排（MiniMax-M3 教程 `launch_online_dp.py` 形态） |

说明：PD 的站点渲染当前对 NPU 大多不可用（MiniMax-M3 显式 opt-out；DeepSeek-V4-Flash
A3 的 `pd_cluster` 标 supported，但本期被单机闸门 skip），站点能力就绪前该分支不会触发。

## 12. 待补的 MAP 条目

本模板只覆盖 DeepSeek-V4-Flash。确认思路后，MAP 需补齐 `recipes/overlays/` 下其余 8 个模型。
建议先按「每个模型 1 个 A5 默认组合 + 1 个 A3 组合」做最小集。

MiniMax-M3（PR21065，形态 B 首个用例）合入后即纳入 MAP；未合入期间可用 entry 级
`site` 指向 preview 站，先行验证 `argv`/`env` 解析主路径与 16 卡调度。
