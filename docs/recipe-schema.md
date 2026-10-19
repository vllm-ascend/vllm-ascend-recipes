# Recipe YAML Schema 说明

> 本文档说明本仓库 recipe(YAML)的字段结构、与上游 [vllm-project/recipes](https://github.com/vllm-project/recipes) schema 的对齐关系,以及 `pnpm validate`(src/lib/schema.ts)的校验规则。
> 上游 schema 定义见 [.claude/skills/add-recipe/SKILL.md](https://github.com/vllm-project/recipes/blob/main/.claude/skills/add-recipe/SKILL.md)。

---

## 一、总体结构

一个 recipe 由两类字段组成:

1. **上游对齐字段**(vllm-project/recipes 兼容):meta / model / features / opt_in_features / variants / compatible_strategies / hardware_overrides / strategy_overrides / dependencies / guide。上游 builder 能识别并用于生成命令、徽标、硬件/策略选择器。
2. **本仓库扩展字段**(上游忽略,供页面展示与本地校验使用):performance_model_names / config_params / scenarios / extra_config / overview / prerequisites / env_setup / verification / evaluation / performance / tuning / faq / references。

```yaml
meta:            # 上游
model:           # 上游
features:        # 上游(布尔开关唯一声明源)
opt_in_features: # 上游(默认关闭列表)
variants:        # 上游
compatible_strategies:   # 上游支持的部署策略
hardware_overrides:      # 上游
strategy_overrides:      # 上游
dependencies:    # 上游
guide:           # 上游(本仓库留空占位,教程正文在扩展字段)
# —— 以下为扩展 ——
performance_model_names: # 额外的性能表模型名
config_params:   # 可编辑值参数（页面）
overview / weight_download / prerequisites / env_setup / verification / ...
extra_config:    # 附加配置芯片(页面)
scenarios:       # 页面展示与 Recipe 场景基线
```

## 二、上游字段说明

| 字段                                        | 位置  | 说明                                                                                                                   | 校验                                           |
| ------------------------------------------- | ----- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `meta`                                      | 顶层  | title/slug/provider/description/date_added/date_updated/difficulty/tasks/performance_headline/related_recipes/hardware | difficulty 枚举 beginner/intermediate/advanced |
| `model.model_id`                            | model | 需与模型仓库 `org/repo` 一致(严格对齐文件路径属 P3)                                                                    | 必填                                           |
| `model.base_args` / `base_env`              | model | 所有场景都带的 serve 参数 / 环境变量                                                                                   | 数组 / 字符串字典                              |
| `model.docker_image`                        | model | 字符串或按硬件对象(如 atlas_800_a2/a3)                                                                                 | 二选一                                         |
| `model.nightly_required`                    | model | min_vllm_version 未发布稳定版时为 true                                                                                 | 布尔                                           |
| `model.install`                             | model | pip/docker 标签;`false` 隐藏该标签,对象可覆盖 command/note                                                             | 至少一个标签                                   |
| `features`                                  | 顶层  | 特性声明,`label`/`description`/`args`/`env`;布尔开关唯一声明源                                                         | args 或 env 至少一个                           |
| `opt_in_features`                           | 顶层  | 默认关闭的特性 key 列表(引用 features)                                                                                 | 必须引用已声明 feature                         |
| `variants`                                  | 顶层  | `default` 必填;变体可带 model_id/supported_hardware/extra_args/extra_env                                               | 必须含 default 变体                            |
| `compatible_strategies`                     | 顶层  | 支持的部署策略(本仓库:single_node_A2/A3/pd_cluster)                                                                    | 字符串数组                                     |
| `hardware_overrides` / `strategy_overrides` | 顶层  | 按硬件/策略的 extra_args/extra_env/tp 覆盖                                                                             | 结构化                                         |
| `dependencies`                              | 顶层  | 额外安装:note/command/optional/brand                                                                                   | 结构化                                         |
| `guide`                                     | 顶层  | 上游教程正文;本仓库留空(`""`),正文在扩展字段                                                                           | 可选字符串                                     |

> 说明:`variants.precision` 上游枚举为 `bf16/fp8/nvfp4/fp4/int4/int8/awq/gptq/mxfp4`;本仓库按约定保留 `W8A8`(Ascend 扩展),校验只要求非空字符串。

## 三、扩展字段说明

| 字段                  | 说明                                                                                   |
| --------------------- | -------------------------------------------------------------------------------------- |
| `config_params`       | 页面可编辑值参数；其 `default` 同时作为 Converter 值占位符的默认来源                    |
| `scenarios`           | 页面场景；带 `scripts` 的场景同时作为多节点 Converter 的输入                           |
| `test_id`             | script-backed 场景在当前 Recipe 内的唯一稳定标识，小写 kebab-case                      |
| `npu_per_node`        | script-backed 场景的每节点 NPU 资源数，正整数                                           |
| `aisbench`            | 公共 AISBench 测试选择，可包含 `accuracy`、`performance`；省略或空数组表示不运行       |
| `extra_config`        | 页面附加配置芯片(key/label),步骤里用 `%%CONFIG:key%%...%%/CONFIG:key%%` 标记控制       |
| `overview` 等教程字段 | 页面展示用;上游忽略                                                                    |

## 四、占位符与标记约定

| 语法                               | 含义                                     | 替换来源                                                                                  |
| ---------------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------------------- |
| `{{script:name}}`                  | 将脚本渲染为带语言标记的 Markdown 代码块 | 当前 scenario 的 `scripts.name.language` 与 `scripts.name.content`                        |
| `{{name}}`                         | 值参数或特性开关                         | 页面和 Converter 使用顶层/场景 config_params；`--set` 可临时覆盖                         |
| `%%CONFIG:key%%...%%/CONFIG:key%%` | 附加配置片段开关                         | extra_config 的 key 或 feature key;选中时保留片段,否则删除                                |
| `%%HL:key%%...%%/HL:key%%`         | 命令颜色高亮                             | 由替换逻辑自动注入,复制按钮会剥离                                                         |

脚本字段只承载 Converter 确实需要提取的完整脚本；仅用于说明或人工执行的命令仍直接写在
`steps[].content` 的普通 Markdown 代码块中。页面先展开 `{{script:name}}`，再替换脚本内部的
`config_params`/feature 占位符。脚本键名不预设业务命名规则，只需能在 `{{script:...}}` 中
被完整引用；脚本不能嵌套引用其他脚本。

需要按节点提取的脚本应在键名中使用零基角色编号，例如 `prefill-0-template`、
`prefill-1-template`、`decode-0-launch`，避免多节点场景出现同名脚本。
面向整个拓扑的端到端验证不属于某个节点，使用不带节点编号的 `service-check`。

采用 `scripts` 的场景同时使用可机器读取的拓扑字段：`deployment` 必须精确为 `pd` 或
`non-pd`；PD case 使用 `<P数量>p<D数量>d`（如 `1p1d`），非 PD case 使用
`<节点数>-node`（如 `2-node`）。页面会在 PD case 选项原有的悬停内容中直接解释 P/D。
`test_id`、`npu_per_node` 和 `service-check` 必填；同一 Recipe 中 `test_id` 不得重复。
Converter v1 读取场景的 `test_id`、`npu`、`deployment`、`case`、`npu_per_node`、`aisbench`、
`scripts`，以及顶层/场景 `config_params` 的有效默认值。`precision`、顶层 `model`、`variants`、
`steps` 等字段仍可供页面和上游格式使用，但不参与转换。启动脚本的 `vllm serve` 第一个参数直接写
最终模型相对路径，不增加额外路径变量。
端口、DP/TP/rank 等启动参数不另设重复字段，YAML 脚本中写什么，后续便提取什么。

`{{name}}` 值占位符由顶层 `config_params` 提供默认值，当前 scenario 可覆盖同名定义；Converter
与前端采用相同合并顺序。命令行 `--set` 可临时覆盖最终默认值；所有值合并后仍未解析的占位符会
导致转换失败。
Shell 形式的 `$...`/`${...}` 是 Runtime 环境变量，不属于这一层参数解析。

`aisbench` 仅选择公共测试类型，不携带模型私有数据集配置。例如
`aisbench: [accuracy, performance]` 会展开公共 accuracy 和 performance 默认配置；省略或空数组
不会生成 AISBench stage。端到端服务检查由必需的 `service-check` 脚本提供，与 AISBench 开关
相互独立。

## 五、校验规则(`pnpm validate`)

除字段级类型校验外,`src/lib/schema.ts` 的 superRefine 做跨字段互锁:

1. `opt_in_features` 引用的 key 必须存在于 `features`;
2. `variants` 存在时必须包含 `default` 变体;
3. 步骤里的 `{{name}}` 必须能解析到 `config_params` 或 `features`;
4. 步骤里的 `%%CONFIG:key%%` 必须有对应的 `extra_config` key 或 feature；
5. 使用 `scripts` 的场景必须满足固定的 deployment/case 结构，所有 `{{script:name}}` 必须引用
   同一场景已声明且不嵌套的脚本；
6. 使用 `scripts` 的场景必须声明当前 Recipe 内唯一的 `test_id`、正整数 `npu_per_node` 和
   `service-check`；
7. `aisbench` 只允许不重复的 `accuracy`、`performance`。

## 六、中英文 1:1

`models/en/**` 与 `models/zh/**` 的同一模型字段结构按约定保持一致，仅描述语言不同；
`pnpm validate` 分别校验两个目录的 schema。对于多节点模板，UT 还会精确比较 `test_id`、NPU、
precision、deployment、case、`npu_per_node`、AISBench 选择、完整 scripts 和有效
`config_params.default`，防止页面展示与 Converter 实际输入漂移。

---

## 七、作者流程(参考上游 skill)

上游把"新增 recipe"沉淀为 [.claude/skills/add-recipe/SKILL.md](https://github.com/vllm-project/recipes/blob/main/.claude/skills/add-recipe/SKILL.md) 的完整流程,本仓沿用并适配:

1. **确认 HF id**:`<org>/<repo>` 必须精确(从 URL 去掉 `https://huggingface.co/` 前缀)。
2. **拉模型元数据**:`bash scripts/hf-info.sh <org>/<repo>` 取 `config.json` / `params.json`,得到 architecture(moe/dense)、parameter_count、active_parameters、context_length。
3. **通读模型 README**(不能跳过):提取 min_vllm_version / nightly_required、dependencies、parser flags(features)、MTP/量化变体(variants.model_id)、推荐 serve 参数、硬件与采样默认值。
4. **交叉核对 vLLM 支持**:`gh search issues/prs --repo vllm-project/vllm`,并 curl 对应 tag 的 `registry.py` + `supported_models.md`;必选参数写进 `model.base_args`;以"实际可运行的最低稳定 tag"定 `min_vllm_version`,不要照抄 README 的 nightly 说法。
5. **写 YAML**:本仓结构 = 上游字段 + 扩展字段(见上文),场景步骤要能过 `pnpm validate` 的互锁校验。
6. **注册 provider**(新组织时):本仓在 `src/lib/providers.js`(或对应 provider 表)添加显示名与 logo。
7. **校验**:本仓跑 `pnpm validate`(zod);上游跑 `node scripts/build-recipes-api.mjs`(要求输出 `✓ JSON API: N models, ...`)。
8. **提交**:只提交 recipe YAML 与 provider 改动;`public/` 与设计文档不提交。

上游 Skill 里还有两条硬规则值得注意:

- **特性 key 用 `spec_decoding` 不用 `mtp`**(上游已全局改名)——我们已对齐;
- **`vram_minimum_gb` 公式**:`ceil(params × bytes_per_param × 1.2)`(8-bit=1 字节/参,4-bit=0.5);混合精度量化(如 NVFP4)要用真实权重体积 `ceil(real_GB × 1.2)`,不要用公式硬套。

## 八、校验与 CI 流水线(上游 vs 本仓)

| 环节      | 上游 vllm-project/recipes                                      | 本仓 vllm-ascend-recipes                                                                                                         |
| --------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 本地校验  | `node scripts/build-recipes-api.mjs`(构建 JSON API,失败即报错) | `pnpm validate`(zod schema,scripts/validate-yaml.ts 扫 models/en、models/zh)                                                     |
| 类型/格式 | 无独立 workflow;靠 build 脚本 + 人工 review                    | `pnpm typecheck` / `pnpm lint` / `prettier --check`                                                                              |
| 合入前 CI | **无 GitHub Actions workflow**(`.github/` 不存在)              | PR 触发：`lint`（frontend/yaml/workflow/Converter，CPU 检查）+ `pr-recipe-verify`（自动发现变更 recipe 的单机场景，无模型白名单）；不运行多机实机任务 |
| 合入后 CI | 无                                                             | nightly-recipes.yaml 统一配置 single_node（按硬件的 recipe 名单）和 multi_node（验证目标 ID 名单）；空列表关闭对应回归，另支持手动指定 |
| 页面预览  | recipes.vllm.ai 构建生成                                       | PR Preview Build + Netlify 部署,评论贴链接                                                                                       |

> 本仓的 `pnpm validate` 是 CI `validate` 步骤的同一实现——本地过,CI 才可能过。

## 九、上游指导文档

| 文档                               | 位置                       | 要点                                                                        |
| ---------------------------------- | -------------------------- | --------------------------------------------------------------------------- |
| CONTRIBUTING.md                    | 仓库根                     | recipe 提交权威指南:quick start、HF 元数据获取、YAML schema、预览与 PR 流程 |
| .claude/skills/add-recipe/SKILL.md | .claude/skills/add-recipe/ | Claude Code 用的作者流程:完整 schema、命名约定、校验清单、提交规范          |
| CLAUDE.md / AGENTS.md              | 仓库根                     | 仓库级 agent 指导(AGENTS.md 引用 CLAUDE.md)                                 |
| README.md                          | 仓库根                     | 站点(recipes.vllm.ai)与仓库定位                                             |
| scripts/build-recipes-api.mjs      | scripts/                   | 事实上的"合入校验"脚本                                                      |

## 十、本仓指导文档

| 文档                     | 位置   | 要点                                                                   |
| ------------------------ | ------ | ---------------------------------------------------------------------- |
| README.md                | 仓库根 | 站点功能、Quick start、脚本表(pnpm dev/build/validate/typecheck/lint…) |
| CONTRIBUTING.md          | 仓库根 | 贡献方式、加/改 recipe 流程、i18n、前端、commit/DCO、PR 流程           |
| AGENTS.md / CLAUDE.md    | 仓库根 | agent 协作约定(dev server 后台运行等)                                  |
| docs/config-scenarios.md | docs/  | 基线场景与 config_params 设计                                          |
| docs/MULTI_NODE_FRAMEWORK.md | docs/ | 新旧多节点链路边界、Converter、中间态与 Runtime 架构                 |
| docs/CI_BUILD_NOTES.md   | docs/  | CI/CD 建设踩坑记录(镜像、容器、runner、上传、LWS 等)                   |
| docs/recipe-schema.md    | docs/  | 本文档:字段结构、上游对齐、校验规则                                    |
| MIGRATION_PLAN.md        | 仓库根 | 迁移/演进计划                                                          |

## 十一、合入检查清单(给维护者)

1. `pnpm validate` / `pnpm typecheck` / `pnpm lint` / `prettier --check` 全绿;
2. en/zh 字段结构一致;
3. 步骤占位符（`{{name}}`、`{{script:name}}`）与 `%%CONFIG%%` 标记都能解析;
4. PR Preview Build + Netlify 预览正常；两个独立多节点模板均能生成临时 plan 并通过 Runtime
   validate-only；生成目录不提交。
