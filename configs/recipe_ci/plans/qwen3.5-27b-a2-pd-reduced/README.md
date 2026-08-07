# Qwen3.5-27B A2 双节点 P/D CI plan

该中间态参考 vLLM Ascend 官方文档的
[Qwen3.5-27B/Qwen3.6-27B 多节点 P/D 部署](https://docs.vllm.ai/projects/ascend/en/latest/tutorials/models/Qwen3.5-27B-Qwen3.6-27B.html#52-multi-node-pd-separation-deployment)。
官方标准拓扑使用两台 Atlas 800 A3，每节点 `DP8 x TP2 = 16 NPU`。当前 Recipe CI 的
LWS 运行在 A2、每个 Pod 申请 8 张 NPU，因此本 plan 保留官方推荐的 TP2，把本地 DP
缩减为 4：

```text
node0 prefill: DP4 x TP2，8 A2 NPU，端口 7100-7103
node1 decode:  DP4 x TP2，8 A2 NPU，端口 7100-7103
node0 gateway: 38085
```

这用于验证双节点 P/D、Mooncake、external DP、gateway、completion 和 AISBench 主链路，
不代表官方 A3 配置的吞吐性能。

模型缓存路径由 Runner 拼接：

```text
/root/.cache/modelscope/hub/models + Eco-Tech/Qwen3.5-27B-w8a8-mtp
```

本地运行 node0：

```bash
export RECIPE_CI_PLAN=configs/recipe_ci/plans/qwen3.5-27b-a2-pd-reduced/plan.yaml
export VLLM_ASCEND_ROOT=/vllm-workspace/vllm-ascend
export RECIPE_CI_CLUSTER_IPS="<node0_ip>,<node1_ip>"
export RECIPE_CI_INTERFACE="<local_interface>"
export LWS_WORKER_INDEX=0
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
scripts/recipe_ci/run.sh
```

node1 使用相同环境并将 `LWS_WORKER_INDEX` 改为 `1`。plan 声明的 completion、accuracy
和 performance 会全部执行；本地运行前需准备 AISBench，CI 由公共入口安装。

当前 plan 暂不加入 `.github/workflows/recipe_verify_multi_node.yaml` 的测试矩阵。加入前需要
先确认共享 PVC 中存在上述模型目录，并完成一次两节点人工验证。
