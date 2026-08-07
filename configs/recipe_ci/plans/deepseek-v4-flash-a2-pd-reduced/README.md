# DeepSeek-V4-Flash A2 双节点 P/D CI plan

该目录是 DeepSeek V4 A2 多节点 P/D 配置的双节点 CI 缩减中间态。它保留
A2 单节点 `DP8 x TP1` 启动参数，但只使用一个 Prefill 和一个 Decode 节点，
用于验证 LWS、服务、gateway 和 check 主链路，不代表 Recipe 的完整 4P4D
性能拓扑。Runner 不计算拓扑，DP/TP、端口、KV Connector 和 gateway backend
均由本目录显式保存。

## 拓扑

```text
node0 role=prefill: DP8 x TP1，8 张 A2 NPU，端口 7100-7107
node1 role=decode:  DP8 x TP1，8 张 A2 NPU，端口 7100-7107
node0 gateway:     端口 38085
```

每个节点必须提供 8 个可见设备，例如：

```bash
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
```

镜像必须保留：

```text
/vllm-workspace/vllm-ascend/examples/external_online_dp/launch_online_dp.py
/vllm-workspace/vllm-ascend/examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py
```

node0 运行：

```bash
export RECIPE_CI_PLAN=configs/recipe_ci/plans/deepseek-v4-flash-a2-pd-reduced/plan.yaml
export VLLM_ASCEND_ROOT=/vllm-workspace/vllm-ascend
export RECIPE_CI_CLUSTER_IPS="<node0_ip>,<node1_ip>"
export RECIPE_CI_INTERFACE="<local_interface>"
export LWS_WORKER_INDEX=0
scripts/recipe_ci/run.sh
```

node1 使用相同环境并将 `LWS_WORKER_INDEX` 改为 `1`。两个节点应使用相同仓库 commit、
Mooncake-enabled A2 镜像和 `RECIPE_CI_CLUSTER_IPS`。模型路径由固定缓存根目录
`/root/.cache/modelscope/hub/models` 与 `plan.model.cache_path` 拼接。

plan 中声明的 check、accuracy 和 performance 会全部执行。需要本地运行 AISBench 时，
先执行 `scripts/recipe_ci/install_aisbench.sh`，或在 node0/node1 的共同环境中设置
`RECIPE_CI_INSTALL_AISBENCH=true`；只有 node0 会执行安装和评测。
