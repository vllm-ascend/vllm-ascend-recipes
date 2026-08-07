# Multi-node PD Recipe Verify（简化版多机流水线）

在 K8s 集群上把 recipe 的「多节点-PD分离」场景真正跑起来：拉起 prefill / decode 两组
Pod，验证 PD 服务可用。比 `/recipes-research/multinode/`（9 脚本 + 4 workflow + PVC/NFS）
大幅简化 —— 这里只有 **1 个 workflow + 2 个 python 脚本，无 PVC / 无 NFS 依赖**。

## 文件

| 文件 | 作用 |
|---|---|
| `.github/workflows/multinode-recipe-verify.yml` | 唯一入口：PR 触发 + workflow_dispatch → controller → artifact |
| `scripts/multinode/controller.py` | 控制器（a2b4-0 调度节点，kubectl 驱动）：extract → render → apply → wait → verify → cleanup |
| `scripts/multinode/lws.yaml.jinja2` | LWS 模板（对齐上游 `tests/e2e/nightly/multi_node`），pod spec 由 controller 注入 |
| `scripts/multinode/node_entry.py` | 每个 LWS Pod 的入口：选角色 → 写脚本 → 填运行时变量 → 启动 |
| `scripts/multinode/launch_online_dp.py` | vendored 上游 example（recipe 不内嵌时用） |
| `scripts/multinode/mooncake/` | Mooncake 运行时一次性镜像构建 |

## 运行链路

```
workflow_dispatch(recipe, hw_filter, deployment_filter, prefill_nodes, decode_nodes, ...)
  → controller.py (runner: linux-aarch64-a2b4-0)
       ├─ 读 recipe → 选「多节点-PD分离」scenario → plan.json
       ├─ 渲染 LWS + ConfigMap(node_entry.py + plan.json) → kubectl apply
       ├─ kubectl 等全部 pod Ready
       ├─ 轮询 <prefill_node0>:<port>/v1/models 就绪
       ├─ [可选] 在 prefill node0 上跑 proxy_command，改 curl proxy
       ├─ 跑 recipe 的 Service Verification curl
       ├─ 写 summary.json → upload artifact；失败 dump 各 pod 日志
       └─ 清理：kubectl delete lws/configmap（always）
  → node_entry.py (每个 Pod, LWS_WORKER_INDEX 区分角色)
       ├─ idx < prefill_nodes ? prefill : decode
       ├─ 通过 LWS headless service DNS 解析全部 peer IP（hostNetwork，Pod IP == 节点 IP）
       ├─ 写 run_dp_template.sh + launch_online_dp.py + launch.sh
       ├─ 填 nic_name / local_ip / --dp-address / --dp-rank-start / kv_port / engine_id
       └─ source set_env.sh → python launch_online_dp.py（前台）
```

## 前置条件

1. **集群**：runner `linux-aarch64-a2b4-0` 有 `kubectl` + kubeconfig（能建 LWS/Pod）。**a2b4-0 是 K8s 调度节点**，多机流水线在它上面纯 kubectl 驱动；单机流水线在计算节点 `a2b4-8` 直接跑容器，两者完全分开。
2. **Namespace 隔离**：多机 LWS pod 都进**专用 namespace `ci-recipe-multinode`**（controller 首次自动创建），与单机流水线零交叉、不会混 pod。
3. **LWS CRD**：`leaderworkerset.x-k8s.io/v1` 已安装
   （`kubectl apply -f https://github.com/kubernetes-sigs/lws/releases/.../standard.yaml`）。
4. **Device plugin**：节点可申请 `huawei.com/Ascend910B`；节点带 label
   `node.kubernetes.io/npu.chip.name=910B4`（nodeAffinity 按此调度，可在 workflow 里改 `--chip`）。
5. **权重**：每个节点已预下载权重到 `/root/.cache/modelscope/hub/models/...`
   （Pod 以 hostPath 挂载该目录；DeepSeek-V4-Flash 的 recipe 硬编码了完整路径）。
6. **镜像**：`vllm-ascend:v0.23.0rc1`（与单机 runner 同一镜像）；**PD 场景需 Mooncake 运行时**，用 `vllm-ascend:v0.23.0rc1-mooncake`（见下节）。

## Mooncake（PD 场景的硬依赖）

recipe 的 `--kv-transfer-config '{"kv_connector": "MooncakeHybridConnector", ...}'`
在 vllm serve 启动时 `from mooncake.engine import TransferEngine`，**镜像里必须预装
Mooncake**。这不是流水线每跑一次能装的（`cmake -DUSE_ASCEND_DIRECT=ON` + `make` 太重），
所以**单独抽象成一次性镜像构建**，和权重预装同理：

```bash
scripts/multinode/mooncake/build.sh   # 产出 vllm-ascend:v0.23.0rc1-mooncake
# 推到集群能拉到的 registry，workflow 的 image 输入填这个 tag
```

`mooncake/Dockerfile` 复用上游 `tools/mooncake_installer.sh -y`（装系统依赖/yalantinglibs/
Go/submodules），再加 Mooncake 本身的 `cmake .. -DUSE_ASCEND_DIRECT=ON; make; make install`，
并 bake `LD_LIBRARY_PATH=/usr/local/lib64/python3.12/site-packages/mooncake`。**首次在真实
Ascend 节点上构建时要验证一遍**（本仓库无法跑 docker build）。

配套行为：
- `node_entry.py` 启动时检测角色模板含 `Mooncake` 就检查 `mooncake.engine` 可导入；
  缺了 → 快速失败（exit 2），提示先 `build.sh` 再换 image，而不是让 vllm 半小时后才崩。
- `kv_port` 避开 Mooncake AscendDirectTransport 保留区间 `[20000, 20000+npu×1000)`；
  recipe 已用 30000/30400（8 卡节点保留到 27999），合规，勿改小。
- proxy：`load_balance_proxy_server_example.py` 在 `-mooncake` 镜像的
  `/opt/vllm-ascend/examples/disaggregated_prefill_v1/`，`proxy_command` 可直接引用。

## 怎么跑

- **GitHub Actions**：仓库 → Actions → `Multi-node Recipe Verify` → Run workflow。
  默认 `models/en/DeepSeek/DeepSeek-V4-Flash.yaml` A2 PD、1P4D。
- **本地 dry-run（不碰集群）**：
  ```bash
  python3 scripts/multinode/controller.py \
    --recipe models/en/DeepSeek/DeepSeek-V4-Flash.yaml --dry-run
  # 产物：/tmp/multinode-results/{plan.json, lws.yaml, configmap.yaml}
  ```
- **本地真实跑（runner 上）**：
  ```bash
  python3 scripts/multinode/controller.py \
    --recipe models/en/DeepSeek/DeepSeek-V4-Flash.yaml \
    --run-id demo1 --results-dir /tmp/multinode-results
  ```

## 拓扑怎么定

**默认自动推导**：controller 从 recipe 的 launch 命令算 `nodes = dp-size ÷ dp-size-local`、
`npu-per-node = dp-size-local × tp-size`，不需要手动配。`prefill_nodes` / `decode_nodes` /
`npu_per_node` 输入设 0 即自动（默认），非 0 才覆盖。

| Recipe 场景 | 命令里的 DP 配置 | 自动推导出的拓扑 |
|---|---|---|
| DeepSeek-V4-Flash A2 PD | prefill `--dp-size 8 --dp-size-local 8`；decode `--dp-size 32 --dp-size-local 8` | 1P4D（decode 需 4×8=32 rank） |
| DeepSeek-V4-Flash A3 PD（1P1D） | prefill `--dp-size 4 --tp 4`；decode `--dp-size 16 --tp 1` | 1P1D（A3 16 卡/节点） |
| **Qwen3.5/3.6-27B A2 PD（推荐验证）** | prefill/decode `--dp-size 8 --tp-size 1 --dp-size-local 8` | **1P1D（A2 8 卡/节点，2 节点共 16 卡）** |
| Qwen3.5/3.6-27B A3 PD | prefill/decode `--dp-size 8 --tp-size 2 --dp-size-local 8` | 1P1D（A3 16 卡/节点） |

## PR 触发

`pull_request` 触发（paths: `models/**`、`scripts/multinode/**`、本 workflow）：`prepare` job
检测改动里**第一个含 PD 场景的 en recipe**，交给 controller 自动推导拓扑验证。多机验证
`continue-on-error: true`——结果可见但不阻塞合入（依赖集群前置）。手动跑仍用
`workflow_dispatch`。

## 运行时填值白名单（plan.md §九）

`node_entry.py` 只替换以下运行时值，其余（TP / max-model-len / quantization /
compilation-config / additional-config 等）原样保留，替换前后做 diff，改了即 fail：

- `nic_name` → 默认路由网卡
- `local_ip=xx.xx.xx.x` → 本节点 IP（`$(hostname -I)` 写法原样保留）
- `--dp-address` → prefill: 本节点 IP；decode: decode 组首节点 IP
- `--dp-rank-start` → decode 节点 i = i × `--dp-size-local`
- `--kv-transfer-config` 的 `kv_port` / `engine_id` → 组内偏移递增（×100 / +1）
- `<your_model_path>` / `<MODEL_PATH>` → `models/_cache_paths.yaml` 解析的权重路径

## Proxy（可选）

recipe 的 Service Verification 建议走 Mooncake proxy，但 proxy 启动命令不在 recipe 里
（在 Mooncake 文档中）。默认**直接 curl prefill node0 的 vllm 端口**验证；要验证完整
PD 路由，在 workflow 的 `proxy_command` 输入里给一条启动命令（在 prefill node0 上
`kubectl exec` 运行），支持占位符：

```
python launch_proxy.py --port 8088 \
  --prefill-endpoints <prefill_endpoints> \
  --decode-endpoints <decode_endpoints>
```

`<prefill_endpoints>` / `<decode_endpoints>` 会被替换成各节点的 `http://ip:port` 列表。

## 失败怎么排查

controller 失败时：`kubectl logs` 每个 pod 的最近 200 行 → `results/logs/node-<i>.log`，
summary.json 的 `stages` / `fail_stage` 标明卡在哪个阶段。常见失败：

| 症状 | 原因 |
|---|---|
| `pods_ready` 卡住 | LWS CRD 未装 / device plugin 未就绪 / 节点无 910B4 label / NPU 被占 |
| `service_ready` 超时 | 权重没预装到节点 / 拓扑和 DP 配置不匹配（rank 收不齐）/ 镜像内 vllm 版本不对 |
| pod CrashLoop / ImagePull | `--image` 不存在或未授权拉取 |

## 边界 / 不做什么

- 不做 badge / publish-status（要徽章参考旧研究版的 `publish-multinode-status.yml`）。
- 不做完整 AISBench（`--run-eval` 只是轻量 latency 检查；完整 accuracy/perf 是后续）。
- 不碰现有单机三件套（`nightly/pr-recipe-verify.yml`、`_recipe_verify.yml`、`publish-status.yml`）。
