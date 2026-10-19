# vllm-ascend-recipes CI/CD 建设踩坑记录

> 目标：为 `vllm-ascend-recipes` 仓库搭建 Recipe 端到端验证流水线，覆盖 YAML 契约校验 → 页面可渲染 → 模型可部署 → aisbench 性能测试 → OBS 留证。

## 流水线架构

```
PR 合入前:
  Actions 审批放行（如需要）→ 变更的 recipe → 格式/构建检查 → 按单机场景选择 A2/A3/A5
    → CANN容器 → vllm serve → 任务API验证 → 聊天场景aisbench → 停服务 → 上传artifact

合入后:
  定时/推送/手动 → nightly-recipe-verify → single_node 名单 → 单机验证 → OBS留证
  定时/手动 → verify_multi_node → multi_node 名单 → 原有多机Runtime → OBS留证
```

## 验证范围配置

PR 不设模型白名单，也不要求 GitHub label。新增或修改 `models/en/`、`models/zh/` 的 YAML 后，流水线自动找到对应英文 recipe，根据 `scenarios` 中的 `npu`、`deployment: Single-Node` 和可执行的 `vllm serve` 选择硬件。`experimental` 不阻止首次验证；明确标记 `unsupported` 的硬件不调度。仅包含多机场景的 recipe 不启动单机任务。

外部 PR 如需审批，在 Actions 中点 **Approve and run workflows** 即可继续，不需要先另提 PR 修改名单。此按钮不是代码 Review 的 Approve。是否等待审批由仓库/组织的 Actions 设置控制；本次不修改审批策略、不启用 `pull_request_target`，也不给 fork PR 传递 OBS 等 secrets。维护者应先检查 PR 中的脚本和工作流，再批准它在自托管 runner 上执行。

nightly 的资源名单统一在 [`.github/nightly-recipes.yaml`](../.github/nightly-recipes.yaml) 配置。`single_node` 按 `a2/a3/a5` 分组，`multi_node` 填已有验证目标的 ID。例如：

```yaml
single_node:
  a2: []
  a3: []
  a5:
    - models/en/Qwen/Qwen3-VL-30B-A3B-Instruct.yaml
multi_node: []
```

单机每天北京时间 02:00 执行名单；合入 main 时只回归名单内有改动的 recipe。修改名单本身会验证整份单机名单。多机每天 02:30 执行自己的名单，沿用原有 Runtime，不在 PR 中申请实机；PR 仍保留不占 NPU 的 Converter/计划静态检查。列表设为 `[]` 即关闭对应范围，所有列表为空时不会申请自托管 runner。

恢复多机回归时，从 `.github/verification-targets.yaml` 中选择 `mode: multi-node` 的 ID 放入 `multi_node`，例如 `deepseek-v2-lite-a2-w8a8-1p1d` 或 `qwen3-30b-a3b-a2-bf16-2node`。新多机用例仍需按原 Converter DSL 编写场景及 `test_id`、登记验证目标后再加入名单。关闭的目标不会被补写为失败。

当前名单共六个单机模型/硬件组合：A2 为 Qwen3-30B-A3B、Qwen3.5/3.6-27B；A3 为 Qwen3-30B-A3B；A5 为 DeepSeek-V4-Flash、GLM-5、Qwen3.5/3.6-27B。多机默认关闭，待有资源再添加。A2/A5 使用现有 8 卡 runner；A3 使用现有 `linux-aarch64-a3-800i-16-cn12-001`，可容纳 TP16 单机场景。

手动运行 `Nightly Recipe Verify` 时，默认使用上述单机名单。临时验证其他模型可填写 `recipe_allowlist`（英文路径、逗号分隔），不需要登记名单；`hardware_filter` 可限制硬件。`full_run` 则扫描全部英文 recipe，显式填写的 `recipe_allowlist` 优先。手动运行 `Multi-node framework` 默认使用多机名单，也可用 `target_ids` 临时指定已有目标。这些输入均不影响 PR，PR 审批时无需填写。

单机镜像优先读取 recipe 的 `model.docker_image`，已知的 0.23 同标签公共镜像映射到现有 SWR 镜像；新标签和 digest 不降级。nightly 单项也可以写成 `{recipe: 路径, image: 镜像}` 覆盖已知 CI 环境，手动镜像输入优先级最高。同硬件不同镜像拆为独立任务，避免覆盖产物或互相取消。

权重不自动下载。容器内标准目录为 `/root/.cache/modelscope/hub/models/<org>/<name>`，与 `model.model_id` 一致且存在 `config.json` 时，新 recipe 不需要登记别名。自定义、量化或按硬件区分的目录继续由 `models/_cache_paths.yaml` 映射，已有别名优先。审批前确认权重已预置；缺失时明确跳过，不算通过。A5 沿用宿主机 `/mnt/weight` 到 `/root/.cache` 的挂载；实际挂载仍需集群环境验证。

验证执行 recipe 的 curl/Python 请求并检查任务响应，支持聊天、文本补全、Embedding、Score/Rerank 和 ASR。只有 `/v1/models`、没有请求或未支持的请求方式不会当作通过；错误 JSON、无任务结果或请求到了其他服务均不能通过。非聊天场景不运行聊天 benchmark。这是单机 API 冒烟验证，不代表精度认证，也不覆盖 recipe 所有可选特性。

`.github/verification-targets.yaml` 继续定义详情页的验证目标，不作为 PR 单机准入条件；其中多机目标由 nightly 的 `multi_node` 显式选择后才运行。

各硬件独立上传原始产物，结束后在 GitHub 托管 runner 上汇总成发布器原先使用的产物名称。同名 recipe 的参数按硬件保留在 `hardware_runs` 中，避免互相覆盖；任一硬件失败或计划中的硬件缺失产物时，模型级汇总不标为通过。跳过的硬件不加入已执行场景列表。

本地生成执行计划不占用 NPU：

```bash
python .github/_scripts/verification_plan.py --trigger pr --base-ref origin/main
python .github/_scripts/verification_plan.py --trigger nightly
python .github/_scripts/nightly_multi_node_plan.py --trigger nightly
python .github/_scripts/verification_plan.py --trigger manual \
  --recipe-allowlist models/en/Qwen/Qwen3-VL-30B-A3B-Instruct.yaml --hardware-filter a5
```

## 踩坑记录

### 一、镜像与依赖

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 1 | `quay.io/ascend/vllm-ascend` 拉不到 | CI 集群网络不通 quay.io | 改用 `swr.cn-southwest-2.myhuaweicloud.com/base_image/ascend-ci/vllm-ascend:v0.23.0rc1` |
| 2 | SWR `:latest` 标签实际指向 vllm 0.11.0 | latest 标签未更新，不支持 `--tokenizer-mode deepseek_v4` | 显式指定 `v0.23.0rc1` 标签 |
| 3 | CANN 镜像 `pip install vllm vllm-ascend` 后 `vllm` CLI 找不到 | vllm 模块可 `import` 但二进制不在 `PATH` | `hash -r` + `export PATH="/usr/local/bin:/root/.local/bin:$PATH"`；或创建 `/usr/local/bin/vllm` wrapper 脚本 |
| 4 | `pip install ais_bench_benchmark` SSL 证书错误 | 未配 pip 内部镜像，直连 PyPI 失败 | 先执行 `pip config set global.index-url http://cache-service.nginx-pypi-cache.svc.cluster.local/pypi/simple` |

### 二、容器与环境

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 5 | `apt-get update` 卡死 | vllm-ascend 镜像已预装所有包，apt 走缓存服务器不通 | 删除 `apt-get install` 步骤，依赖镜像自带 |
| 6 | `source /usr/local/Ascend/ascend-toolkit/set_env.sh` 不生效 | 每个 `step` 是独立 shell，环境变量不跨 step 传递 | 在 verify step 内部 source（同一 shell 上下文） |

### 三、Runner 与网络

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 7 | Runner offline | 上轮 job 跑完 runner 进程断连 | 到 runner 机器重启 GitHub Actions runner service |
| 8 | `gh-proxy.test.osinfra.cn` 502 Bad Gateway | Git 代理服务挂了 | 运维修复代理 |
| 9 | Fork PR 没有进入单机验证 | 旧工作流限定同仓 PR，或仓库 Actions 审批尚未放行 | 不再限定同仓；维护者检查代码后在 Actions 审批。还需组织允许该仓库使用目标 runner |

### 四、Recipe 解析

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 10 | `Found 0 scenario(s)` | recipe 用 ` ```shell ` 而非 ` ```bash ` | 正则匹配两者：`r'```(?:bash\|shell)...'` |
| 11 | `%%CONFIG:prefix-caching%%` 未被去除 | `\w+` 不匹配连字符 `-`，导致 `prefix-caching` 无法匹配 | 改为 `[^%]+` 匹配除 `%` 外的所有字符 |
| 12 | 多行命令被截断（JSON 参数丢失） | 逐行解析只拿首行，`\` 续行后的 JSON 内容丢失 | **提取整个 bash code block 内容**而非逐行拼接 |
| 13 | `your_model_path` / `your_eagle3_model_path` 占位符未替换 | recipe 使用占位符作为示例 | Python 端 `replace` 为实际权重路径；eagle3 行直接删除 |
| 14 | A3 场景在 A2 runner 上执行报错 | 未按硬件过滤场景 | 加入 `if hw_key == 'atlas_800_a2' and 'A3' in s.get('npu', '')` 跳过 |
| 15 | 多节点 PD 分离场景报 `--port $2` | 位置参数未赋值（`$2` `$3` 等） | 检测 `$[0-9]` 跳过所有多节点场景 |

### 五、命令执行

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 16 | `eval "$cmd"` 特殊字符报错 | 多行命令含引号、`$` 符号、转义字符等 | `cat` 拼接临时脚本文件，`bash script.sh &` 执行 |
| 17 | `set -u` 导致 `LD_PRELOAD: unbound variable` | recipe 中 `export LD_PRELOAD=...:$LD_PRELOAD` 引用未定义变量 | temp 脚本用 `set -eo pipefail` 代替 `set -euo pipefail` |
| 18 | curl 多行命令拆成单行执行失败 | `while read` 逐行处理，每一行单独 eval | 写入临时脚本整体执行（同 #16） |
| 19 | 全局 `verification` 字段的 curl 未被执行 | parser 只扫描 `scenarios[].steps[]` | 额外提取顶层 `verification` 字段，追加到每个 scenario 的 verify_cmds |

### 六、退出与流程控制

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 20 | `exit $STATUS` 不生效，流水线一直 running | `echo \| while read` 管道在子 shell 中执行，变量修改不传给父 shell | Python 写元数据到文件，shell 用 `done < /tmp/scenario_list.txt` 文件重定向读取 |
| 21 | vllm serve 进程杀不掉 | `kill $PID` 只杀 bash 父进程，Python 子进程残留 | `kill -TERM -- -$pid` 杀整个进程组，`kill -KILL` 兜底 |
| 22 | curl 验证后服务被杀了但 benchmark 没跑 | 代码顺序：kill 在 benchmark 之前 | 调整为：**serve → curl → benchmark → kill** |

### 七、Workflow 文件

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 23 | GitHub 报 "This run likely failed because of a workflow file issue" | YAML 缩进不一致（OBS upload step 7空格 vs with 8空格） | 统一为 8 空格缩进 |
| 24 | `devops-actions/actionlint@v0` 不存在 | action 版本号 `v0` 不是有效 tag | 改为 `reviewdog/action-actionlint@v1` |
| 25 | Fork PR 中 `git diff origin/main...HEAD` 报 `fatal: bad revision` | merge commit checkout 没有 `origin/main` 分支引用 | 完整拉取历史，使用事件中的 base SHA 与 checkout HEAD 比较 |
| 26 | OBS 上传只在 Nightly 触发 | condition 写死 `trigger_type == 'nightly'` | 改为 `if: ${{ always() }}`（PR + Nightly 都上传） |
| 27 | PR 未同步最新 fork commit | fork PR sync 延迟 | push 空 commit 或修改任意 workflow YAML 强制触发 |

### 八、模型与权重

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 28 | DeepSeek-V4-Flash W8A8 OOM | 284B 参数 > 8×32G=256GB HBM | 切换到 Qwen3-30B-A3B（30B 总参 / 3B 激活，W8A8≈30GB） |
| 29 | `ModelSlim Quantization Config Not Found` | w8a8 权重非 ModelSlim 格式，缺 `quant_model_description.json` | 保留 `--quantization ascend` 参数；确保权重路径正确指向 w8a8 目录 |
| 30 | 权重路径与 recipe 不匹配 | recipe 用 `vllm-ascend/` 目录，实际在 `Eco-Tech/` | Python 脚本中 `str.replace` 替换权重路径 |
| 31 | `--speculative-config` eagle3 报 model path 无效 | `your_eagle3_model_path` 未被替换 | 整行 `--speculative-config` 通过正则删除 |

## 核心文件

```
.github/workflows/
├── _recipe_verify.yml       # 可复用 recipe 验证 workflow
├── pr-recipe-verify.yml     # PR 触发（路径检测 → validate → deploy verify）
├── nightly-recipe-verify.yml # Nightly 看护（定时/推送/手动）
├── lint.yml                 # 路径过滤 lint（已有，增强）
├── deploy.yml               # GitHub Pages 部署（已有）
├── preview-build.yml        # PR Preview 构建（已有）
└── preview-deploy.yml       # PR Preview Netlify 部署（已有）

scripts/
└── verify-recipe.sh         # 核心脚本：解析 recipe → 安装依赖 → vllm serve → curl 验证 → aisbench → 结果输出
```

## 关键设计决策

1. **镜像选择**：优先用预装 vllm-ascend 的镜像（跳过 pip install），版本必须 ≥ v0.23.0（支持 `--tokenizer-mode deepseek_v4`）
2. **命令传递**：Python 写临时文件 → shell `cat` 拼接 → `bash script.sh &` 执行，避免 eval 特殊字符问题
3. **子 shell**：永远不用 `echo | while read`，改用文件重定向 `done < file`
4. **进程清理**：`kill -TERM -- -$pid` 杀进程组，`kill -KILL` 兜底
5. **路径替换**：recipe 占位符在 Python 端用 `str.replace` + `re.sub` 处理，不修改源文件
6. **场景过滤**：按硬件（A2/A3）和部署模式（单节点/多节点）自动跳过不适用的场景

## 阶段目标

- [x] 2026-07-30 前：自动资料转化 Recipe + 基础 lint + Astro Build + PR Preview + 代表性部署验证（Qwen3-30B-A3B 跑通）
- [ ] 2026-08-30 前：模型 F0/Maintainer/CI 工程责任链 + 中英文检视 + 主仓冒烟用例 + 失败归因 + 修复闭环
- [ ] 2026-09-30 前：A5 Runner + A5 基础模型验证 + A2/A3 稳定回归基线
