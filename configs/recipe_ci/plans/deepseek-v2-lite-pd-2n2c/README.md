# DeepSeek-V2-Lite P/D 双节点四卡验证

这个手工中间态用例用于打通第一阶段主链路：两台机器分别承担 Prefill 和 Decode，
每台机器由 vLLM Ascend 的 `launch_online_dp.py` 启动两个 TP1 实例，因此每节点使用
2 张 NPU，总计 4 卡。所有后端就绪后，Prefill 节点启动上游 P/D Proxy，再依次执行
completion 检查和可选的 AISBench 评测。

该用例不依赖 Recipe 文档转换、Kubernetes 或共享文件系统。

## 运行镜像契约

使用 vLLM Ascend 官方运行镜像。镜像除已安装的 vLLM 和 vLLM Ascend 外，还必须保留
完整源码目录：

```text
/vllm-workspace/vllm-ascend/
├── examples/external_online_dp/launch_online_dp.py
└── examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py
```

这些工具由镜像提供，本仓库不复制它们，也不会在执行时下载 vLLM Ascend 源码。

## 第一次本地验证：启动镜像后 clone

下面的动作需要在两台装有 NPU 的物理机上各执行一次。先把本次修改提交并推送到可
访问的分支；容器内 `git clone` 无法读取宿主机尚未推送的工作区修改。

在两台机器的宿主机上启动相同版本的镜像，并将模型挂载到相同的容器路径。镜像名、
宿主机模型路径按实际环境替换：

```bash
docker run --rm -it \
  --privileged \
  --network host \
  --ipc host \
  --shm-size 64g \
  -v /path/on/host/DeepSeek-V2-Lite-W8A8:/root/.cache/modelscope/hub/models/vllm-ascend/DeepSeek-V2-Lite-W8A8:ro \
  quay.io/ascend/vllm-ascend:v0.22.1rc1 \
  bash
```

进入容器后，先确认镜像契约，再 clone 当前分支。使用 HTTPS 可以避免容器内缺少宿主机
SSH 凭据：

```bash
test -f /vllm-workspace/vllm-ascend/examples/external_online_dp/launch_online_dp.py
test -f /vllm-workspace/vllm-ascend/examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py

cd /vllm-workspace
git clone --branch <your-branch> --depth 1 \
  https://github.com/MrZ20/vllm-ascend-recipes.git \
  vllm-ascend-recipes
cd /vllm-workspace/vllm-ascend-recipes
```

两台机器通过相同的 `RECIPE_CI_CLUSTER_IPS` 提供按 `node0,node1` 排列的集群地址。
`node0` 的角色是 Prefill，并作为默认控制面 leader；这里的默认值来自节点顺序，不需要
在 plan 中重复声明。

先检查中间态结构，不会检查 NPU 或启动服务：

```bash
RECIPE_CI_PLAN=configs/recipe_ci/plans/deepseek-v2-lite-pd-2n2c/plan.yaml \
RECIPE_CI_VALIDATE_ONLY=true scripts/recipe_ci/run.sh
```

Prefill 机器作为 `node0` 执行：

```bash
export RECIPE_CI_PLAN=configs/recipe_ci/plans/deepseek-v2-lite-pd-2n2c/plan.yaml
export RECIPE_CI_CLUSTER_IPS="<node0_ip>,<node1_ip>"
export RECIPE_CI_INTERFACE="<local_interface>"
export LWS_WORKER_INDEX=0
export ASCEND_RT_VISIBLE_DEVICES=4,5
scripts/recipe_ci/run.sh
```

Decode 机器作为 `node1` 执行：

```bash
export RECIPE_CI_PLAN=configs/recipe_ci/plans/deepseek-v2-lite-pd-2n2c/plan.yaml
export RECIPE_CI_CLUSTER_IPS="<node0_ip>,<node1_ip>"
export RECIPE_CI_INTERFACE="<local_interface>"
export LWS_WORKER_INDEX=1
export ASCEND_RT_VISIBLE_DEVICES=4,5
scripts/recipe_ci/run.sh
```

两边的启动先后没有要求。框架默认使用
`/vllm-workspace/vllm-ascend`；非标准镜像可通过 `VLLM_ASCEND_ROOT` 覆盖。
每节点只消费候选列表的前两张卡；卡号需按两台机器各自的 `npu-smi info` 结果选择。
框架不会清理或改写任何 `http_proxy`、`https_proxy`、`ftp_proxy` 环境变量，只为集群
IP 补充 `NO_PROXY`。

需要放通节点间通信，至少包括协调端口 `29599`、服务端口 `7100-7101`、DP RPC 端口
`12321`、Mooncake 端口 `30000/30200` 和 Proxy 端口 `38085`。

成功或失败后，各节点都会清理自己启动的进程。日志默认位于：

```text
/tmp/recipe-ci/deepseek-v2-lite-pd-2n2c/
├── node0/
│   ├── service.log
│   ├── gateway.log
│   └── checks/completion.log
└── node1/service.log
```

## AISBench 阶段

plan 中声明的 completion、accuracy 和 performance 会全部执行。运行前应按
`docs/MULTI_NODE_RECIPE_CI.md` 安装 AISBench。这个轻量 plan 自带 8 条离线 GSM8K
格式样本，evaluation 会把它链接到 AISBench 的标准数据目录，不再运行时下载数据集。

plan 内的 `aisbench/models/vllm_api_general_chat.py` 和 `vllm_api_stream_chat.py` 是
Recipe 转换产物，分别供精度和性能评测使用。evaluation 会根据当前 endpoint、模型路径和
served model 将占位符渲染到 artifact 目录，无需修改 AISBench 安装目录。模型配置名可分别通过
`RECIPE_AISBENCH_ACCURACY_MODEL_CONFIG` 和
`RECIPE_AISBENCH_PERFORMANCE_MODEL_CONFIG` 覆盖；样本数可通过
`RECIPE_AISBENCH_ACCURACY_NUM_PROMPTS` 和
`RECIPE_AISBENCH_PERFORMANCE_NUM_PROMPTS` 调整。评测命令输出和 AISBench 产物都会
写到该节点的 `accuracy/` 或 `performance/` artifact 目录。
