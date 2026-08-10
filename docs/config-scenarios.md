# Recipe 基线 + 可配置参数 + 场景标签路由

## 目标

1. **验证 tutorial 准确性**:每个基线场景(单机 A2 / 单机 A3 / 多机 PD 分离)的 serve 参数以
   tutorial 原始配置为**默认值**,CI 用默认值跑,保证"教程怎么写,CI 就怎么跑"。
2. **特殊参数可配置**:文档里的 Key Parameter Descriptions(`--max-model-len`、
   `--no-enable-prefix-caching`、`--max-num-seqs`、`--gpu-memory-utilization` 等)
   抽成 `config_params`,给出默认值(取自 tutorial),页面/CI 可覆盖。
3. **上游字段对齐**:顶层字段(`meta/model/variants/compatible_strategies/features/
   dependencies`)与 vllm-project/recipes schema 一致;`scenarios` 保留为我们的扩展字段
   (CI 基线),其 serve 命令参数用占位符渲染。
4. **流水线路由**:`scenarios[].tags` 打标(`a2-single` / `a3-single` / `pd-multinode`),
   workflow 按标签决定走哪条流水线。

## 字段模型

```yaml
meta:          # 上游对齐(含 related_recipes 等)
model:         # 上游对齐(含 base_args/base_env/install/nightly_required)
variants:      # 上游:精度/显存/描述(默认 = tutorial)
compatible_strategies:  # 上游:部署策略
features:      # 上游:模型特性(expert_parallel/tool_calling/reasoning/spec_decoding)
opt_in_features: []
dependencies:  # 上游:额外 pip 依赖

# —— 新增:可配置参数(默认值 = tutorial 原始配置)——
config_params:
  max_model_len:            {default: 1048576, type: number, description: "最大上下文长度(输入+输出),按实际场景调整"}
  max_num_seqs:             {default: 256,     type: number, description: "每个 DP 组最大并发序列数"}
  gpu_memory_utilization:   {default: 0.90,    type: number, description: "KV cache 可用显存比例"}
  prefix_caching:           {default: false,   type: bool,   description: "默认关闭;开启后去掉 --no-enable-prefix-caching"}

scenarios:     # 保留(CI 基线),每场景打流水线标签
- npu: Atlas 800I A2
  deployment: 单节点-多卡
  tags: [a2-single]         # ← 流水线路由
  steps:
    content: |
      vllm serve ... --max-model-len {{max_model_len}} \
        --max-num-seqs {{max_num_seqs}} \
        --gpu-memory-utilization {{gpu_memory_utilization}} \
        {{prefix_caching:--no-enable-prefix-caching}} ...
- npu: Atlas 800I A3
  deployment: 多节点-PD分离
  tags: [pd-multinode]
```

## 占位符约定

- `{{name}}` — 用 `config_params.name.default`(或用户覆盖值)替换。
- `{{name:text}}` — 布尔参数:name 为 falsy 时渲染 `text`,为 truthy 时渲染空。
  (示例:`{{prefix_caching:--no-enable-prefix-caching}}` → 默认 false 时输出
  `--no-enable-prefix-caching`,勾选开启后该参数消失。)

## 渲染与执行

- **页面**:CascadeSelector 增加"参数配置"面板,显示 config_params(默认值),输入后实时
  替换到步骤的 serve 命令;场景标题旁展示 tags 对应的流水线标签。
- **CI**:workflow prepare 读选中 recipe 的 scenario tags:
  - `a2-single` / `a3-single` → 单机流水线(verify-recipe.sh)
  - `pd-multinode` → 多机流水线(controller)
  用默认值替换占位符后执行,校验 tutorial 基线。

## 兼容性

- 上游字段与我们的教程字段共存;上游 build 忽略未知字段(已实测)。
- 现有 `%%CONFIG:key%%` 机制保留(用于步骤内启停文本),新占位符机制并行。
