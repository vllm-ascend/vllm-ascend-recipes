# Recipe 场景、页面参数与多节点模板

## 1. 两类场景

仓库中的 `scenarios` 用于模型文档和页面渲染；多节点 CI 只读取独立模板中的 script-backed 场景。
新 Converter 不读取或兼容既有模型文档场景。前端 schema 同时接受两种形状，是为了页面正常渲染，
不表示普通模型场景会被多节点 CI 自动消费。

当前 Converter 只支持：

| 类型 | 英文模板 | `test_id` |
| --- | --- | --- |
| PD external DP | `models/en/DeepSeek/template_pd.yaml` | `pd-2n2c` |
| 非 PD internal DP | `models/en/Qwen/template2_non_pd.yaml` | `dp-2n2c` |

对应的中文模板只用于中文页面渲染和 schema 镜像。

## 2. 可配置参数

页面和 Converter 共用模板内的 `config_params`，避免前端命令与 CI 命令出现两套默认值：

```yaml
config_params:
  max_model_len:
    default: 4096
    type: number
    description: Maximum context length
  max_num_seqs:
    default: 8
    type: number
    description: Maximum concurrent sequences

scenarios:
  - test_id: pd-2n2c
    config_params:
      max_num_seqs:
        default: 4
        type: number
        description: Scenario-specific concurrency
```

解析优先级固定为：

```text
顶层 config_params.default
  -> scenario 同名 config_params.default 覆盖
  -> Converter 命令行 --set name=value 临时覆盖
```

前端执行前两步，并允许用户在页面临时修改显示值。Converter 取合并后的 `default`，再应用
`--set`。`--set` 只用于一次本地或 CI 调试，不写回模板。

值占位符写作 `{{name}}`。缺少默认值、值类型不合法、渲染后仍有未解析占位符，或者显式传入未
使用的 `--set`，Converter 都会失败。

`{{name:text}}` 是页面现有的布尔参数语法：当 `name` 为 falsy 时渲染 `text`，为 truthy 时
渲染空。不要把它与 `{{script:name}}` 混淆。

## 3. 新模板场景字段

新场景示意：

```yaml
scenarios:
  - test_id: pd-2n2c
    npu: Atlas 800I A2
    precision: W8A8
    deployment: pd
    case: 1p1d
    npu_per_node: 2
    aisbench: [accuracy, performance]
    scripts:
      prefill-0-template:
        language: bash
        content: |-
          vllm serve model/path \
            --max-model-len {{max_model_len}}
      prefill-0-launch:
        language: bash
        content: |-
          python launch_online_dp.py ...
      service-check:
        language: bash
        content: |-
          curl ...
    steps:
      - title: Start Prefill node 0
        content: |-
          {{script:prefill-0-template}}

          {{script:prefill-0-launch}}
```

字段约束：

- `test_id`：模板内唯一、稳定的小写 kebab-case 标识；
- `deployment`：只允许精确值 `pd` 或 `non-pd`，不做模糊匹配；
- `case`：PD 使用 `<P>p<D>d`，非 PD 使用 `<N>-node`；
- `npu_per_node`：每节点正整数 NPU 数；
- `aisbench`：可选的 `accuracy`、`performance`，省略或空数组表示不评测；
- `scripts`：Converter 读取的完整脚本，必须包含 `service-check`；
- `precision`、`steps`：仍用于页面，不作为 Converter 推导模型或拓扑的替代字段。

实际模型取自渲染后 `vllm serve` 的第一个参数。served name、DP/TP/rank、服务/RPC/KV/Gateway
端口也直接从受支持脚本提取，不在 YAML 中增加一套重复的专用字段。

## 4. 脚本嵌入

需要同时被前端展示和 Converter 提取的完整脚本放入 `scripts`，正文使用精确的
`{{script:name}}` 引用。前端根据 `language` 渲染代码块；Converter 读取脚本对象本身，不从渲染
后的 Markdown 反向提取。

只用于说明、环境清理或人工操作的内容直接保留在 `steps[].content`。用得到的业务脚本才抽取，
不为普通文本额外创建字段。

节点脚本使用零基编号：

- PD：`prefill-0-template`、`prefill-0-launch`、`decode-0-template`、`decode-0-launch`；
- 非 PD：`api-0`、`headless-0`；
- 多节点扩展继续使用 `prefill-1`、`decode-1`、`headless-1`；
- 整体服务检查使用不带节点编号的 `service-check`。

脚本中的 `$API_NODE_0_IP`、`$PREFILL_NODE_0_IP`、`${ASCEND_RT_VISIBLE_DEVICES}` 等是 Runtime
环境变量，Converter 原样保留，不把真实节点 IP 或设备号写入模板和中间态。

## 5. 前端渲染

CascadeSelector 对新模板做以下处理：

1. 与普通 Recipe 一样渲染 NPU、precision、deployment 和 case 选项；
2. 在 PD case 原有悬停区域解释 P 表示 Prefill、D 表示 Decode；
3. 先展开 `{{script:name}}`，再应用 `config_params` 和页面配置；
4. 继续支持既有场景的 `tags`、`strategy`、`%%CONFIG:key%%` 和旧 PD 页面交互。

新模板的机器字段采用规范值，但前端可以把 `pd`、`non-pd` 显示为更友好的中英文标签。显示文案
不能参与 Converter 判断。

## 6. 生成与 CI

Converter 命令只需要模板路径和 `test_id`：

```bash
.venv/bin/python test/recipe/multi_node/convert.py \
  --recipe models/en/DeepSeek/template_pd.yaml \
  --test-id pd-2n2c
```

默认输出为：

```text
test/recipe/multi_node/.generated/<recipe-stem-kebab>/<test-id>/
```

`.generated/` 已被 Git 忽略。中间态只用于当前本地或 CI 运行，不提交。`--output` 可用于本地调试，
但必须仍指向 `.generated/` 内部；正常流程无需传入。

新 workflow matrix 每个 case 只维护：

```yaml
- name: deepseek-v2-lite-pd-2n2c
  recipe: models/en/DeepSeek/template_pd.yaml
  test_id: pd-2n2c
```

workflow checkout 后先调用 Converter，然后把包含生成目录的 workspace 复制到 PVC，最后沿用
`run_lws.sh -> run.sh -> runner.py` 执行 plan。workflow 不另外传 output、plan 路径、参数文件、
模型路径或拓扑字段。

matrix 中所有 case 共用一个 concurrency group，一次只运行一个多节点 case。LWS Pod 使用 host
network 和固定服务端口，共用 concurrency 可以避免不同 case 的端口冲突；同一 LWS 内仍通过
基于 `multi-node-run` 的节点级 anti-affinity 保证各 Pod 分布在不同物理节点。artifact bundle
构建后，本次运行目录始终从共享 PVC 清理，不依赖上传是否成功。

## 7. 适配新模板

后续适配者应：

1. 选择 PD 或非 PD 模板作为页面与字段参考；
2. 保持脚本编号、固定 deployment/case 格式和 `test_id` 唯一性；
3. 把页面与 CI 共用的值放在 `config_params.default`；
4. 在 `vllm serve` 脚本中直接给出模型、并行和端口；
5. 为新拓扑扩展独立 Converter analyzer/planner 和负例测试；
6. Converter 明确接受新模板后，再把 `name`、`recipe`、`test_id` 加入 workflow matrix。

不要通过给现有模型文档增加兼容分支来扩展新 Converter。
