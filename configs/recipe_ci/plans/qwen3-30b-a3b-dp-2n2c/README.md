# Qwen3-30B-A3B 普通 DP 双节点四卡验证

这个用例验证 vLLM 内置的普通 data parallel，不包含 Prefill/Decode 拆分：

```text
node0 (api): API server + DP rank 0、1，TP1，共 2 卡
node1 (headless): headless DP rank 2、3，TP1，共 2 卡
                         ↓
全局 DP4，DP coordinator 为 node0:12321
                         ↓
node0:7100 作为唯一服务入口
                         ↓
completion curl 和 plan 中声明的 AISBench
```

它直接消费手工 `plan.yaml` 中间态，不依赖 Recipe 转换、Kubernetes 或共享文件系统。
两个节点分别直接执行自己的 `vllm serve`。`node0` 的 `api` 角色使用 vLLM 内置负载均衡
暴露 API，`node1` 的 `headless` 角色只运行远端 DP rank。Runner 不展开 rank，也不依赖社区已经删除的
普通 DP 示例 Proxy。

## 准备镜像和源码

两台 NPU 机器使用相同的 vLLM Ascend 镜像、代码分支和模型。当前分支需要先提交并
推送，容器内 `git clone` 才能获取修改。

宿主机启动容器的示例：

```bash
docker run --rm -it \
  --privileged \
  --network host \
  --ipc host \
  --shm-size 64g \
  -v /path/on/host/Qwen3-30B-A3B:/root/.cache/modelscope/hub/models/Qwen/Qwen3-30B-A3B:ro \
  quay.io/ascend/vllm-ascend:v0.22.1rc1 \
  bash
```

容器内确认 vLLM 命令存在，然后 clone recipes 仓库：

```bash
command -v vllm

cd /vllm-workspace
git clone --branch <your-branch> --depth 1 \
  https://github.com/MrZ20/vllm-ascend-recipes.git \
  vllm-ascend-recipes
cd /vllm-workspace/vllm-ascend-recipes
```

两台机器通过相同的 `RECIPE_CI_CLUSTER_IPS` 提供按 `node0,node1` 排列的集群地址。

## 启动

先做静态校验：

```bash
RECIPE_CI_PLAN=configs/recipe_ci/plans/qwen3-30b-a3b-dp-2n2c/plan.yaml \
RECIPE_CI_VALIDATE_ONLY=true scripts/recipe_ci/run.sh
```

API 机器作为 `node0` 执行：

```bash
export RECIPE_CI_PLAN=configs/recipe_ci/plans/qwen3-30b-a3b-dp-2n2c/plan.yaml
export RECIPE_CI_CLUSTER_IPS="<node0_ip>,<node1_ip>"
export RECIPE_CI_INTERFACE="<local_interface>"
export LWS_WORKER_INDEX=0
export ASCEND_RT_VISIBLE_DEVICES=4,5
scripts/recipe_ci/run.sh
```

Headless 机器作为 `node1` 执行：

```bash
export RECIPE_CI_PLAN=configs/recipe_ci/plans/qwen3-30b-a3b-dp-2n2c/plan.yaml
export RECIPE_CI_CLUSTER_IPS="<node0_ip>,<node1_ip>"
export RECIPE_CI_INTERFACE="<local_interface>"
export LWS_WORKER_INDEX=1
export ASCEND_RT_VISIBLE_DEVICES=4,5
scripts/recipe_ci/run.sh
```

启动顺序没有要求。`node0` 同时是默认控制 leader，并承担 `api` 和 DP coordinator
角色；这些职责概念上仍然独立。`node1` 的 `headless` 角色没有 HTTP readiness；
`node0` 的 `/health` 只有在全局 DP4 就绪后才会通过，因此它同时验证远端
rank 已连接。每节点直接消费 `ASCEND_RT_VISIBLE_DEVICES` 中的两张卡；卡号需按两台机器
各自的 `npu-smi info` 结果选择。

至少需要放通 Runner 协调端口 `29599`、API 端口 `7100` 和 DP RPC 端口 `12321`。
默认日志位于：

```text
/tmp/recipe-ci/qwen3-30b-a3b-dp-2n2c/
├── node0/
│   ├── service.log
│   └── checks/completion.log
└── node1/service.log
```

plan 中声明的 check、accuracy 和 performance 都会执行，因此应先按
`docs/MULTI_NODE_RECIPE_CI.md` 安装 AISBench 和 GSM8K 数据集。plan 内的
`vllm_api_general_chat.py` 和 `vllm_api_stream_chat.py` 分别供精度和性能评测使用，
evaluation 会用当前 endpoint 和 served model 渲染后再执行；样本数可通过
`RECIPE_AISBENCH_ACCURACY_NUM_PROMPTS` 和
`RECIPE_AISBENCH_PERFORMANCE_NUM_PROMPTS` 调整。Runner 不会清理任何代理环境变量，
只会把节点 IP 加入 `NO_PROXY`。
