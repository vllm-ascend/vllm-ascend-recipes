# Recipe YAML Schema 说明

> 本文档说明本仓库 recipe(YAML)的字段结构、与上游 [vllm-project/recipes](https://github.com/vllm-project/recipes) schema 的对齐关系,以及 `pnpm validate`(src/lib/schema.ts)的校验规则。
> 上游 schema 定义见 [.claude/skills/add-recipe/SKILL.md](https://github.com/vllm-project/recipes/blob/main/.claude/skills/add-recipe/SKILL.md)。

---

## 一、总体结构

一个 recipe 由两类字段组成:

1. **上游对齐字段**(vllm-project/recipes 兼容):meta / model / features / opt_in_features / variants / compatible_strategies / hardware_overrides / strategy_overrides / dependencies / guide。上游 builder 能识别并用于生成命令、徽标、硬件/策略选择器。
2. **本仓库扩展字段**(上游忽略,页面与 CI 使用):config_params / scenarios / extra_config / overview / prerequisites / env_setup / verification / evaluation / performance / tuning / faq / references。

```yaml
meta:            # 上游
model:           # 上游
features:        # 上游(布尔开关唯一声明源)
opt_in_features: # 上游(默认关闭列表)
variants:        # 上游
compatible_strategies:   # 上游(与 scenarios.strategy 互锁)
hardware_overrides:      # 上游
strategy_overrides:      # 上游
dependencies:    # 上游
guide:           # 上游(本仓库留空占位,教程正文在扩展字段)
# —— 以下为扩展 ——
config_params:   # 可编辑值参数(页面/CI)
overview / weight_download / prerequisites / env_setup / verification / ...
extra_config:    # 附加配置芯片(页面)
scenarios:       # CI 执行基线(tags/strategy 路由)
```

## 二、上游字段说明

| 字段 | 位置 | 说明 | 校验 |
|---|---|---|---|
| `meta` | 顶层 | title/slug/provider/description/date_added/date_updated/difficulty/tasks/performance_headline/related_recipes/hardware | difficulty 枚举 beginner/intermediate/advanced |
| `model.model_id` | model | 需与模型仓库 `org/repo` 一致(严格对齐文件路径属 P3) | 必填 |
| `model.base_args` / `base_env` | model | 所有场景都带的 serve 参数 / 环境变量 | 数组 / 字符串字典 |
| `model.docker_image` | model | 字符串或按硬件对象(如 atlas_800_a2/a3) | 二选一 |
| `model.nightly_required` | model | min_vllm_version 未发布稳定版时为 true | 布尔 |
| `model.install` | model | pip/docker 标签;`false` 隐藏该标签,对象可覆盖 command/note | 至少一个标签 |
| `features` | 顶层 | 特性声明,`label`/`description`/`args`/`env`;布尔开关唯一声明源 | args 或 env 至少一个 |
| `opt_in_features` | 顶层 | 默认关闭的特性 key 列表(引用 features) | 必须引用已声明 feature |
| `variants` | 顶层 | `default` 必填;变体可带 model_id/supported_hardware/extra_args/extra_env | 必须含 default 变体 |
| `compatible_strategies` | 顶层 | 支持的部署策略(本仓库:single_node_A2/A3/pd_cluster) | 与场景 strategy 互锁 |
| `hardware_overrides` / `strategy_overrides` | 顶层 | 按硬件/策略的 extra_args/extra_env/tp 覆盖 | 结构化 |
| `dependencies` | 顶层 | 额外安装:note/command/optional/brand | 结构化 |
| `guide` | 顶层 | 上游教程正文;本仓库留空(`""`),正文在扩展字段 | 可选字符串 |

> 说明:`variants.precision` 上游枚举为 `bf16/fp8/nvfp4/fp4/int4/int8/awq/gptq/mxfp4`;本仓库按约定保留 `W8A8`(Ascend 扩展),校验只要求非空字符串。

## 三、扩展字段说明

| 字段 | 说明 |
|---|---|
| `config_params` | 可编辑值参数(如 max_model_len),`default`/`type`/`description`;步骤里用 `{{name}}` 引用 |
| `scenarios` | CI 执行基线:每个场景含 npu/precision/deployment/case、`tags`(a2-single/a3-single/pd-multinode)、`strategy`(与 compatible_strategies 互锁)、`steps`(title + content 代码块) |
| `extra_config` | 页面附加配置芯片(key/label),步骤里用 `%%CONFIG:key%%...%%/CONFIG:key%%` 标记控制 |
| `overview` 等教程字段 | 页面展示用;上游忽略 |

## 四、占位符与标记约定

| 语法 | 含义 | 替换来源 |
|---|---|---|
| `{{name}}` | 值参数或特性开关 | config_params 的 `default`(值)/ features 的 `args`+`env`(开关,关闭时用 `flag_when_false`) |
| `%%CONFIG:key%%...%%/CONFIG:key%%` | 附加配置片段开关 | extra_config 的 key 或 feature key;选中时保留片段,否则删除 |
| `%%HL:key%%...%%/HL:key%%` | 命令颜色高亮 | 由替换逻辑自动注入,复制按钮会剥离 |

## 五、校验规则(`pnpm validate`)

除字段级类型校验外,`src/lib/schema.ts` 的 superRefine 做跨字段互锁:

1. `opt_in_features` 引用的 key 必须存在于 `features`;
2. `variants` 存在时必须包含 `default` 变体;
3. 场景 `strategy` 必须列在 `compatible_strategies`;
4. 步骤里的 `{{name}}` 必须能解析到 `config_params` 或 `features`;
5. 步骤里的 `%%CONFIG:key%%` 必须有对应的 `extra_config` key 或 feature。

## 六、中英文 1:1

`models/en/**` 与 `models/zh/**` 的同一模型字段结构必须完全一致(meta/model/features/variants/strategies/hardware_overrides/dependencies/guide 等),仅描述语言不同;`pnpm validate` 分别校验两个目录。
