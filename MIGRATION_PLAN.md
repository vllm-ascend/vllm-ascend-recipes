# vLLM-Ascend Tutorial 迁移计划

## 概述

将 `/home/wxy/code/vllm-ascend/docs/source/tutorials/models/` 下的 **37 个模型教程** 迁移到 recipes 项目的 YAML 中。

### 迁移状态

| 状态 | 数量 |
|------|------|
| ✅ 已迁移 (en+zh) | 3 |
| 🔴 待迁移 | 34 |
| **总计** | **37** |

### 已迁移模型 (3)

| # | 模型 | 文件 |
|---|------|------|
| 1 | Qwen3-235B-A22B | `models/{en,zh}/Qwen/Qwen3-235B-A22B.yaml` |
| 2 | Qwen3-30B-A3B | `models/{en,zh}/Qwen/Qwen3-30B-A3B.yaml` |
| 3 | DeepSeek-V4-Flash | `models/{en,zh}/deepseek-ai/DeepSeek-V4-Flash.yaml` |

---

## YAML 字段映射

| 源文档 Section | YAML 字段 | 类型 | 说明 |
|----------------|-----------|------|------|
| 1 Introduction | `overview` | markdown string | 模型简介 |
| 2 Supported Features | — | 跳过 | 不迁移 |
| 3.1 Model Weight | `weight_download` | array | 权重版本+下载源 |
| 3.2 Multi-node Comm | `prerequisites` | array | 硬件要求+通信验证 |
| 3.x Quantization | `quantization` | object | 量化说明 (可选) |
| 4 Installation | `env_setup` | object | pip/container 安装 |
| 5 Online Service Deployment | `scenarios` | array | 部署场景 (npu/precision/deployment/case/steps) |
| 6 Functional Verification | `verification` | markdown string | 功能验证 (可选) |
| 7 Accuracy Evaluation | `performance.accuracy` | markdown (可选) | 精度评测 |
| 8 Performance Evaluation | `performance.benchmark` | markdown (可选) | 性能评测 |
| 9 Performance Tuning | `tuning` | markdown (可选) | 性能调优 |
| 10 FAQ | `faq` | markdown (可选) | 常见问题 |

### 元数据字段

| 字段 | 来源 |
|------|------|
| `hf_id` | 模型 HuggingFace ID，从 Section 1 的链接提取 |
| `title` | 从 `# <title>` 提取 |
| `provider` | 从 hf_id 第一部分推断 (Qwen/DeepSeek/GLM 等) |
| `description` | 从 Section 1 第一段提炼 |
| `architecture` | dense / moe (从 Section 1 判断) |
| `parameters` | 参数量字符串 (如 "235B") |
| `active_parameters` | MOE 激活参数量 (如 "22B")，dense 为 null |
| `context_length` | 上下文长度 (从 Section 1 或 5) |
| `modality` | text / multimodal / vision / embedding / reranker / asr / ocr |
| `updated` | 文档更新日期 (从源文档或当前日期) |
| `references` | 参考链接数组 |

### Case 字段取值

每个 scenario 需设置 `case` 字段：
- `High Throughput` — 高吞吐
- `Low Latency` — 低延迟
- `Long Context` — 长上下文
- `PD Separation` — Prefill-Decode 分离
- `Multi Card` — 多卡部署
- `Multi Node` — 多节点
- `MTP` — 多 token 预测
- `Standard` — 默认单节点部署
- `Single NPU` — 单卡
- `EAGLE3` — EAGLE3 投机解码

### Deployment 字段取值

- `Single Node` — 单节点
- `Multi Node` — 多节点
- `PD Separation` — PD 分离

### 迁移注意事项

1. **Sphinx 语法转换**：`{code-block} bash :substitutions:` → ` ```bash `, `:::{note}` → 保留（MarkdownContent.astro 已支持）
2. **替换变量**：`|vllm_ascend_version|` 保留（渲染器自动替换为 `v0.22.1rc1`）
3. **交叉引用**：`[text](../../path/to/doc.md)` → 改为绝对 URL (https://docs.vllm.ai/...)
(源文档的绝对路径对应：
./ 对应 https://docs.vllm.ai/projects/ascend/zh-cn/latest/tutorials/models/
同时区分中英文绝对路径，例如：
../../installation.md 对应：
多节点通信（中文）
https://docs.vllm.ai/projects/ascend/zh-cn/latest/installation.html
多节点通信（英文）
https://docs.vllm.ai/projects/ascend/en/latest/installation.html
)
4. **表格**：保留 markdown table 格式
5. **中英同步**：每个模型需要 en/zh 两份 YAML，内容结构一致
6. **section 编号不完整的文档**：部分文档没有统一的 10-section 编号，需要根据实际内容映射

---

## TODO LIST — 按模型逐个

### 🔴 批次 1: DeepSeek 系列 (4个)

---

#### [ ] 1. DeepSeek-R1

| 字段 | 值 |
|------|-----|
| hf_id | `deepseek-ai/DeepSeek-R1` |
| provider | DeepSeek |
| architecture | moe |
| parameters | 671B (需要确认) |
| active_parameters | 37B (需要确认) |
| context_length | 131072 |
| modality | text |
| NPU | Atlas 800I A3 (64G×16), Atlas 800I A2 (64G×8) |
| Precisions | W8A8 (量化版本) |
| Deployments | Single Node, Multi Node |
| 源文件 | `DeepSeek-R1.md` (10 sections) |
| 难度 | ⭐⭐⭐ (大文件) |

文件路径:
- `models/en/deepseek-ai/DeepSeek-R1.yaml`
- `models/zh/deepseek-ai/DeepSeek-R1.yaml`

迁移内容: overview, weight_download, prerequisites, env_setup, scenarios, verification, performance, tuning, faq, references

---

#### [ ] 2. DeepSeek-V3.1

| 字段 | 值 |
|------|-----|
| hf_id | `deepseek-ai/DeepSeek-V3.1` |
| provider | DeepSeek |
| architecture | moe |
| parameters | 685B (需要确认) |
| active_parameters | 37B (需要确认) |
| context_length | 131072 |
| modality | text |
| NPU | Atlas 800I A3, Atlas 800I A2 |
| Precisions | BF16, W8A8, W4A8 |
| Deployments | Single Node, Multi Node, PD Separation |
| 源文件 | `DeepSeek-V3.1.md` (10 sections) |
| 难度 | ⭐⭐⭐ (大文件，多版本) |

文件路径:
- `models/en/deepseek-ai/DeepSeek-V3.1.yaml`
- `models/zh/deepseek-ai/DeepSeek-V3.1.yaml`

迁移内容: overview, weight_download, prerequisites, env_setup, scenarios, verification, performance, tuning, faq, references

---

#### [ ] 3. DeepSeek-V3.2

| 字段 | 值 |
|------|-----|
| hf_id | `deepseek-ai/DeepSeek-V3.2` |
| provider | DeepSeek |
| architecture | moe |
| parameters | 685B (需要确认) |
| active_parameters | 37B (需要确认) |
| context_length | 131072 |
| modality | text |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `DeepSeek-V3.2.md` (8 sections, 非标准编号) |
| 难度 | ⭐⭐ (section 编号非标准) |

文件路径:
- `models/en/deepseek-ai/DeepSeek-V3.2.yaml`
- `models/zh/deepseek-ai/DeepSeek-V3.2.yaml`

迁移内容: overview, weight_download, env_setup, scenarios, verification, accuracy, performance

---

#### [ ] 4. DeepSeek-V4-Pro

| 字段 | 值 |
|------|-----|
| hf_id | `deepseek-ai/DeepSeek-V4-Pro` |
| provider | DeepSeek |
| architecture | moe |
| parameters | 需要从源文档确认 |
| active_parameters | 需要从源文档确认 |
| context_length | 131072 |
| modality | text |
| NPU | Atlas 800I A3 (128G×8) × 2, Atlas 800I A2 (64G×8) × 4 |
| Precisions | W4A8 (量化版本) |
| Deployments | Multi Node |
| 源文件 | `DeepSeek-V4-Pro.md` (10 sections) |
| 难度 | ⭐⭐⭐ (大文件，多节点) |

文件路径:
- `models/en/deepseek-ai/DeepSeek-V4-Pro.yaml`
- `models/zh/deepseek-ai/DeepSeek-V4-Pro.yaml`

迁移内容: overview, weight_download, prerequisites, env_setup, scenarios, verification, performance, tuning, faq, references

---

### 🔴 批次 2: Qwen 文本模型系列 (9个)

---

#### [ ] 5. Qwen3-Dense (0.6B/1.7B/4B/8B/14B/32B)

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-32B` (多个模型共享一个教程) |
| provider | Qwen |
| architecture | dense |
| parameters | 0.6B-32B |
| active_parameters | null |
| context_length | 32768 (需要确认) |
| modality | text |
| NPU | Atlas 800I A3, Atlas 800I A2 |
| Precisions | BF16, W8A8, W4A8, W4A4 |
| Deployments | Single Node, Multi Node |
| 源文件 | `Qwen3-Dense.md` (10 sections) |
| 难度 | ⭐⭐⭐ (多个子模型，表格复杂) |

文件路径:
- `models/en/Qwen/Qwen3-Dense.yaml`
- `models/zh/Qwen/Qwen3-Dense.yaml`

**建议**: 为每个 dense 子模型创建单独的 YAML，或将它们作为一个合集处理。推荐用合集方式，在 description 中列出所有变体。

---

#### [ ] 6. Qwen3.5-27B / Qwen3.6-27B

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3.5-27B` + `Qwen/Qwen3.6-27B` |
| provider | Qwen |
| architecture | dense |
| parameters | 27B |
| active_parameters | null |
| context_length | 131072 |
| modality | text |
| NPU | Atlas 800I A3, Atlas 800I A2 |
| Precisions | BF16, W8A8 |
| Deployments | Single Node, Multi Node |
| 源文件 | `Qwen3.5-27B-Qwen3.6-27B.md` (10 sections) |
| 难度 | ⭐⭐ (两个模型共享一个教程) |

文件路径:
- `models/en/Qwen/Qwen3.5-27B-Qwen3.6-27B.yaml`
- `models/zh/Qwen/Qwen3.5-27B-Qwen3.6-27B.yaml`

---

#### [ ] 7. Qwen3.5-397B-A17B

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3.5-397B-A17B` |
| provider | Qwen |
| architecture | moe |
| parameters | 397B |
| active_parameters | 17B |
| context_length | 131072 |
| modality | text |
| NPU | Atlas 800I A3 × 2, Atlas 800I A2 × 4 (BF16); A3 × 1, A2 × 2 (W8A8) |
| Precisions | BF16, W8A8 |
| Deployments | Single Node, Multi Node, PD Separation |
| 源文件 | `Qwen3.5-397B-A17B.md` (10 sections) |
| 难度 | ⭐⭐⭐ (大模型，多节点+PD) |

文件路径:
- `models/en/Qwen/Qwen3.5-397B-A17B.yaml`
- `models/zh/Qwen/Qwen3.5-397B-A17B.yaml`

---

#### [ ] 8. Qwen3.6-35B-A3B

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3.6-35B-A3B` |
| provider | Qwen |
| architecture | moe |
| parameters | 35B |
| active_parameters | 3B |
| context_length | 131072 |
| modality | text |
| NPU | Atlas 800I A3, Atlas 800I A2 |
| Precisions | BF16, W8A8 |
| Deployments | Single Node |
| 源文件 | `Qwen3.6-35B-A3B.md` (10 sections) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/Qwen/Qwen3.6-35B-A3B.yaml`
- `models/zh/Qwen/Qwen3.6-35B-A3B.yaml`

---

#### [ ] 9. Qwen3-Coder-30B-A3B

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-Coder-30B-A3B-Instruct` |
| provider | Qwen |
| architecture | moe |
| parameters | 30.5B |
| active_parameters | 3.3B |
| context_length | 1048576 (1M) |
| modality | text |
| NPU | Atlas 800I A3 (1-2 cards), Atlas 800I A2 (2-4 cards) |
| Precisions | BF16, W8A8 |
| Deployments | Single Node |
| 源文件 | `Qwen3-Coder-30B-A3B.md` (10 sections) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/Qwen/Qwen3-Coder-30B-A3B.yaml`
- `models/zh/Qwen/Qwen3-Coder-30B-A3B.yaml`

---

#### [ ] 10. Qwen3-Next (80B-A3B)

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-Next-80B-A3B-Instruct` |
| provider | Qwen |
| architecture | moe |
| parameters | 80B |
| active_parameters | 3B |
| context_length | 1048576 (1M) |
| modality | text |
| NPU | Atlas 800I A3 (8 cards), Atlas 800I A2 (8 cards) |
| Precisions | BF16 |
| Deployments | Single Node |
| 源文件 | `Qwen3-Next.md` (10 sections) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/Qwen/Qwen3-Next.yaml`
- `models/zh/Qwen/Qwen3-Next.yaml`

---

#### [ ] 11. Qwen3-ASR-1.7B

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-ASR-1.7B` |
| provider | Qwen |
| architecture | dense |
| parameters | 1.7B |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | asr (语音识别) |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Qwen3-ASR-1.7B.md` (5 sections, 非标准) |
| 难度 | ⭐⭐ (section 非标准) |

文件路径:
- `models/en/Qwen/Qwen3-ASR-1.7B.yaml`
- `models/zh/Qwen/Qwen3-ASR-1.7B.yaml`

---

#### [ ] 12. Qwen3-Embedding

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-Embedding` |
| provider | Qwen |
| architecture | dense |
| parameters | 需要从源文档确认 |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | embedding |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Qwen3_embedding.md` (5 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/Qwen/Qwen3-Embedding.yaml`
- `models/zh/Qwen/Qwen3-Embedding.yaml`

---

#### [ ] 13. Qwen3-Reranker

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-Reranker` |
| provider | Qwen |
| architecture | dense |
| parameters | 需要从源文档确认 |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | reranker |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Qwen3_reranker.md` (5 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/Qwen/Qwen3-Reranker.yaml`
- `models/zh/Qwen/Qwen3-Reranker.yaml`

---

### 🔴 批次 3: Qwen 多模态模型系列 (7个)

---

#### [ ] 14. Qwen3-Omni-30B-A3B-Thinking

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-Omni-30B-A3B` |
| provider | Qwen |
| architecture | moe |
| parameters | 30B |
| active_parameters | 3B |
| context_length | 需要从源文档确认 |
| modality | multimodal (text+image+audio+video) |
| NPU | Atlas 800I A3 (1-2 cards), Atlas 800I A2 (2-4 cards) |
| Precisions | BF16, W8A8 |
| Deployments | Single Node |
| 源文件 | `Qwen3-Omni-30B-A3B-Thinking.md` (10 sections) |
| 难度 | ⭐⭐⭐ (omni-modal, 复杂) |

文件路径:
- `models/en/Qwen/Qwen3-Omni-30B-A3B-Thinking.yaml`
- `models/zh/Qwen/Qwen3-Omni-30B-A3B-Thinking.yaml`

---

#### [ ] 15. Qwen3-VL-235B-A22B-Instruct

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-VL-235B-A22B-Instruct` |
| provider | Qwen |
| architecture | moe |
| parameters | 235B |
| active_parameters | 22B |
| context_length | 需要从源文档确认 |
| modality | vision |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Qwen3-VL-235B-A22B-Instruct.md` (7 sections, 非标准) |
| 难度 | ⭐⭐ (section 非标准) |

文件路径:
- `models/en/Qwen/Qwen3-VL-235B-A22B-Instruct.yaml`
- `models/zh/Qwen/Qwen3-VL-235B-A22B-Instruct.yaml`

---

#### [ ] 16. Qwen3-VL-30B-A3B-Instruct

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-VL-30B-A3B-Instruct` |
| provider | Qwen |
| architecture | moe |
| parameters | 30B |
| active_parameters | 3B |
| context_length | 需要从源文档确认 |
| modality | vision |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Qwen3-VL-30B-A3B-Instruct.md` (4 sections, 非标准) |
| 难度 | ⭐ (内容少) |

文件路径:
- `models/en/Qwen/Qwen3-VL-30B-A3B-Instruct.yaml`
- `models/zh/Qwen/Qwen3-VL-30B-A3B-Instruct.yaml`

---

#### [ ] 17. Qwen3-VL-Embedding

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-VL-Embedding` |
| provider | Qwen |
| architecture | dense |
| parameters | 需要从源文档确认 |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | vision+embedding |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Qwen3-VL-Embedding.md` (5 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/Qwen/Qwen3-VL-Embedding.yaml`
- `models/zh/Qwen/Qwen3-VL-Embedding.yaml`

---

#### [ ] 18. Qwen3-VL-Reranker

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-VL-Reranker` |
| provider | Qwen |
| architecture | dense |
| parameters | 需要从源文档确认 |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | vision+reranker |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Qwen3-VL-Reranker.md` (5 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/Qwen/Qwen3-VL-Reranker.yaml`
- `models/zh/Qwen/Qwen3-VL-Reranker.yaml`

---

#### [ ] 19. Qwen-VL-Dense (2B/4B/8B/32B)

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen3-VL-8B-Instruct` (多个模型) |
| provider | Qwen |
| architecture | dense |
| parameters | 2B-32B |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | vision |
| NPU | Atlas 800I A3, Atlas 800I A2 |
| Precisions | BF16 |
| Deployments | Single NPU, Multi NPU |
| 源文件 | `Qwen-VL-Dense.md` (10 sections) |
| 难度 | ⭐⭐ (多个子模型) |

文件路径:
- `models/en/Qwen/Qwen-VL-Dense.yaml`
- `models/zh/Qwen/Qwen-VL-Dense.yaml`

---

#### [ ] 20. Qwen2.5-Math-RM-72B

| 字段 | 值 |
|------|-----|
| hf_id | `Qwen/Qwen2.5-Math-RM-72B` |
| provider | Qwen |
| architecture | dense |
| parameters | 72B |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | text (math reward model) |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Qwen2.5-Math-RM-72B.md` (6 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/Qwen/Qwen2.5-Math-RM-72B.yaml`
- `models/zh/Qwen/Qwen2.5-Math-RM-72B.yaml`

---

### 🔴 批次 4: GLM 系列 (3个)

---

#### [ ] 21. GLM-5 / GLM-5.1

| 字段 | 值 |
|------|-----|
| hf_id | `zai-org/GLM-5` / `zai-org/GLM-5.1` |
| provider | THUDM (ZhipuAI) |
| architecture | moe |
| parameters | 需要从源文档确认 |
| active_parameters | 需要从源文档确认 |
| context_length | 需要从源文档确认 |
| modality | text |
| NPU | Atlas 800I A3, Atlas 800I A2 |
| Precisions | BF16, W8A8, W4A8 |
| Deployments | Single Node, Multi Node, PD Separation |
| 源文件 | `GLM5.md` (10 sections) |
| 难度 | ⭐⭐⭐ (两个模型版本，多部署模式) |

文件路径:
- `models/en/THUDM/GLM-5.yaml`
- `models/zh/THUDM/GLM-5.yaml`

---

#### [ ] 22. GLM-5.2

| 字段 | 值 |
|------|-----|
| hf_id | `zai-org/GLM-5.2` |
| provider | THUDM (ZhipuAI) |
| architecture | moe |
| parameters | 需要从源文档确认 |
| active_parameters | 需要从源文档确认 |
| context_length | 需要从源文档确认 |
| modality | text |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `GLM5.2.md` (8 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/THUDM/GLM-5.2.yaml`
- `models/zh/THUDM/GLM-5.2.yaml`

---

#### [ ] 23. GLM-4.5/4.6/4.7

| 字段 | 值 |
|------|-----|
| hf_id | 需要从源文档确认 |
| provider | THUDM (ZhipuAI) |
| architecture | moe |
| parameters | 需要从源文档确认 |
| active_parameters | 需要从源文档确认 |
| context_length | 需要从源文档确认 |
| modality | text |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `GLM4.x.md` (9 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/THUDM/GLM-4.x.yaml`
- `models/zh/THUDM/GLM-4.x.yaml`

---

### 🔴 批次 5: Kimi/Moonshot 系列 (4个)

---

#### [ ] 24. Kimi-K2-Thinking

| 字段 | 值 |
|------|-----|
| hf_id | `moonshotai/Kimi-K2-Thinking` |
| provider | Moonshot AI |
| architecture | moe |
| parameters | 需要从源文档确认 |
| active_parameters | 需要从源文档确认 |
| context_length | 131072 |
| modality | text |
| NPU | Atlas 800I A3 (64G×16) |
| Precisions | BF16 |
| Deployments | Single Node |
| 源文件 | `Kimi-K2-Thinking.md` (10 sections) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/moonshotai/Kimi-K2-Thinking.yaml`
- `models/zh/moonshotai/Kimi-K2-Thinking.yaml`

---

#### [ ] 25. Kimi-K2.5

| 字段 | 值 |
|------|-----|
| hf_id | `moonshotai/Kimi-K2.5` |
| provider | Moonshot AI |
| architecture | moe |
| parameters | 需要从源文档确认 |
| active_parameters | 需要从源文档确认 |
| context_length | 131072 |
| modality | multimodal |
| NPU | Atlas 800I A3, Atlas 800I A2 |
| Precisions | W4A8 |
| Deployments | Single Node, Multi Node, PD Separation, EAGLE3 |
| 源文件 | `Kimi-K2.5.md` (10 sections) |
| 难度 | ⭐⭐⭐ (多部署场景，multimodal) |

文件路径:
- `models/en/moonshotai/Kimi-K2.5.yaml`
- `models/zh/moonshotai/Kimi-K2.5.yaml`

---

#### [ ] 26. Kimi-K2.6

| 字段 | 值 |
|------|-----|
| hf_id | `moonshotai/Kimi-K2.6` |
| provider | Moonshot AI |
| architecture | moe |
| parameters | 需要从源文档确认 |
| active_parameters | 需要从源文档确认 |
| context_length | 131072 |
| modality | multimodal |
| NPU | Atlas 800I A3, Atlas 800I A2 |
| Precisions | W4A8 |
| Deployments | Single Node, Multi Node, PD Separation, EAGLE3, DFlash |
| 源文件 | `Kimi-K2.6.md` (10 sections) |
| 难度 | ⭐⭐⭐ (多部署场景) |

文件路径:
- `models/en/moonshotai/Kimi-K2.6.yaml`
- `models/zh/moonshotai/Kimi-K2.6.yaml`

---

#### [ ] 27. LLaVA-OneVision-Qwen2-0.5B-OV

| 字段 | 值 |
|------|-----|
| hf_id | 需要从源文档确认 |
| provider | 需要从源文档确认 |
| architecture | dense |
| parameters | 0.5B |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | vision |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `LLaVA-OneVision-Qwen2-0.5B-OV.md` (6 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径: 需要确认 provider slug

---

### 🔴 批次 6: 其他厂商 (10个)

---

#### [ ] 28. DeepSeek-OCR-2

| 字段 | 值 |
|------|-----|
| hf_id | `deepseek-ai/DeepSeek-OCR-2` |
| provider | DeepSeek |
| architecture | 需要确认 |
| parameters | 需要从源文档确认 |
| active_parameters | null / 需要确认 |
| context_length | 需要从源文档确认 |
| modality | ocr |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `DeepSeekOCR2.md` (10 sections) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/deepseek-ai/DeepSeek-OCR-2.yaml`
- `models/zh/deepseek-ai/DeepSeek-OCR-2.yaml`

---

#### [ ] 29. MiniMax-M2 (M2.5/M2.7)

| 字段 | 值 |
|------|-----|
| hf_id | `MiniMax/MiniMax-M2.5` + `MiniMax/MiniMax-M2.7` |
| provider | MiniMax |
| architecture | moe |
| parameters | 需要从源文档确认 |
| active_parameters | 需要从源文档确认 |
| context_length | 需要从源文档确认 |
| modality | text |
| NPU | Atlas 800I A3, Atlas 800I A2 |
| Precisions | W8A8, W8A8C8 |
| Deployments | Single Node, PD Separation, EAGLE3 |
| 源文件 | `MiniMax-M2.md` (10 sections) |
| 难度 | ⭐⭐⭐ (两个模型，多部署) |

文件路径:
- `models/en/MiniMax/MiniMax-M2.yaml`
- `models/zh/MiniMax/MiniMax-M2.yaml`

---

#### [ ] 30. InternVL3.5 (38B/241B-A28B)

| 字段 | 值 |
|------|-----|
| hf_id | `OpenGVLab/InternVL3_5-241B-A28B` + `OpenGVLab/InternVL3_5-38B` |
| provider | OpenGVLab |
| architecture | moe (241B) / dense (38B) |
| parameters | 38B / 241B |
| active_parameters | null / 28B |
| context_length | 需要从源文档确认 |
| modality | vision |
| NPU | Atlas 800I A3 (64G×16) |
| Precisions | W8A8 |
| Deployments | Single Node |
| 源文件 | `InternVL3.5.md` (9 sections, 非标准) |
| 难度 | ⭐⭐ (两个模型变体) |

文件路径:
- `models/en/OpenGVLab/InternVL3.5.yaml`
- `models/zh/OpenGVLab/InternVL3.5.yaml`

---

#### [ ] 31. PaddleOCR-VL

| 字段 | 值 |
|------|-----|
| hf_id | `PaddlePaddle/PaddleOCR-VL` |
| provider | PaddlePaddle |
| architecture | dense |
| parameters | 0.9B |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | ocr+vision |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | Single Node |
| 源文件 | `PaddleOCR-VL.md` (10 sections) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/PaddlePaddle/PaddleOCR-VL.yaml`
- `models/zh/PaddlePaddle/PaddleOCR-VL.yaml`

---

#### [ ] 32. Hunyuan-A13B-Instruct

| 字段 | 值 |
|------|-----|
| hf_id | `tencent/Hunyuan-A13B-Instruct` |
| provider | Tencent |
| architecture | moe |
| parameters | 13B (需要确认) |
| active_parameters | 需要从源文档确认 |
| context_length | 需要从源文档确认 |
| modality | text |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Hunyuan-A13B-Instruct.md` (6 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径:
- `models/en/Tencent/Hunyuan-A13B-Instruct.yaml`
- `models/zh/Tencent/Hunyuan-A13B-Instruct.yaml`

---

#### [ ] 33. Hy3-preview

| 字段 | 值 |
|------|-----|
| hf_id | 需要从源文档确认 |
| provider | 需要从源文档确认 |
| architecture | 需要确认 |
| parameters | 需要从源文档确认 |
| active_parameters | 需要从源文档确认 |
| context_length | 需要从源文档确认 |
| modality | 需要从源文档确认 |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Hy3-preview.md` (8 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径: 需要确认 provider slug

---

#### [ ] 34. Mixtral-8x7B-Instruct-v0.1

| 字段 | 值 |
|------|-----|
| hf_id | `mistralai/Mixtral-8x7B-Instruct-v0.1` |
| provider | Mistral AI |
| architecture | moe |
| parameters | 46.7B |
| active_parameters | 12.9B |
| context_length | 32768 |
| modality | text |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Mixtral-8x7B-Instruct-v0.1.md` (6 sections, 非标准) |
| 难度 | ⭐ |

文件路径:
- `models/en/Mistral/Mixtral-8x7B-Instruct-v0.1.yaml`
- `models/zh/Mistral/Mixtral-8x7B-Instruct-v0.1.yaml`

---

#### [ ] 35. Minitron-8B-Base

| 字段 | 值 |
|------|-----|
| hf_id | `nvidia/Minitron-8B-Base` |
| provider | NVIDIA |
| architecture | dense |
| parameters | 8B |
| active_parameters | null |
| context_length | 需要从源文档确认 |
| modality | text |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `Minitron-8B-Base.md` (6 sections, 非标准) |
| 难度 | ⭐ |

文件路径:
- `models/en/NVIDIA/Minitron-8B-Base.yaml`
- `models/zh/NVIDIA/Minitron-8B-Base.yaml`

---

#### [ ] 36. gpt-oss-120b

| 字段 | 值 |
|------|-----|
| hf_id | 需要从源文档确认 |
| provider | 需要从源文档确认 |
| architecture | 需要确认 |
| parameters | 120B |
| active_parameters | 需要从源文档确认 |
| context_length | 需要从源文档确认 |
| modality | text |
| NPU | 需要从源文档确认 |
| Precisions | 需要从源文档确认 |
| Deployments | 需要从源文档确认 |
| 源文件 | `gpt-oss-120b.md` (7 sections, 非标准) |
| 难度 | ⭐⭐ |

文件路径: 需要确认 provider slug

---

### 🔴 批次 7: DeepSeek-V4-Flash 校验

---

#### [ ] 37. DeepSeek-V4-Flash (内容校验)

已迁移，但需校验内容完整性，确保 verification, tuning, faq 字段与源文档一致。

源文件: `DeepSeek-V4-Flash.md` (10 sections)

---

## 迁移工作流

每个模型的迁移步骤:

```
1. 读取源 .md → 提取 section 1-10 内容
2. 确定元数据 (hf_id, provider, architecture 等)
3. 创建 models/en/<provider>/<model>.yaml
4. 创建 models/zh/<provider>/<model>.yaml (中文翻译或先复制 en)
5. Sphinx → Markdown 转换:
   - {code-block} bash → ```bash
   - ::: 交叉引用 → 绝对 URL
   - :::{note} / :::{warning} / :::{tip} → 保留 (渲染器支持)
6. 提取 scenarios (npu, precision, deployment, case, steps)
7. 运行 npx astro build 验证
```

### 目录结构约定

```
models/
├── en/
│   ├── Qwen/           # 已有 + 新增 13 个
│   ├── deepseek-ai/    # 已有 + 新增 4 个
│   ├── THUDM/          # 新增 3 个 (GLM)
│   ├── moonshotai/     # 新增 4 个 (Kimi)
│   ├── MiniMax/        # 新增 1 个
│   ├── OpenGVLab/      # 新增 1 个
│   ├── PaddlePaddle/   # 新增 1 个
│   ├── Tencent/        # 新增 1 个
│   ├── Mistral/        # 新增 1 个
│   ├── NVIDIA/         # 新增 1 个
│   └── ...             # 待确定 (gpt-oss, Hy3, LLaVA)
└── zh/                 # 镜像结构
```

### Provider Slug 映射

| Provider 名称 | Slug |
|---------------|------|
| Qwen (Alibaba) | `Qwen` |
| DeepSeek | `deepseek-ai` |
| Zhipu AI / THUDM | `THUDM` |
| Moonshot AI | `moonshotai` |
| MiniMax | `MiniMax` |
| OpenGVLab | `OpenGVLab` |
| PaddlePaddle | `PaddlePaddle` |
| Tencent | `Tencent` |
| Mistral AI | `Mistral` |
| NVIDIA | `NVIDIA` |

---

## 统计

| 分类 | 数量 |
|------|------|
| 总源文档 | 40 |
| index.md (跳过) | 1 |
| 已迁移 | 3 |
| 待迁移 | 36 |
| - 完整 10-section | 18 |
| - 非标准 section (5-9) | 18 |
| - Qwen 系列 | 16 |
| - DeepSeek 系列 | 5 |
| - GLM 系列 | 3 |
| - Kimi 系列 | 4 |
| - 其他厂商 | 8 |
| - text modality | ~20 |
| - vision/multimodal | ~10 |
| - embedding/reranker/asr/ocr | ~7 |

---

## 优先级建议

### P0 (核心大模型)
1. DeepSeek-R1
2. DeepSeek-V3.1
3. GLM-5/5.1
4. Qwen3.5-397B-A17B

### P1 (重要模型)
5. Qwen3-Dense (多个子模型)
6. Qwen3-Coder-30B-A3B
7. Kimi-K2.5
8. Kimi-K2.6
9. MiniMax-M2
10. Qwen3-Omni-30B-A3B-Thinking

### P2 (补充模型)
11-20. Qwen-VL 系列, GLM-4.x, GLM-5.2 等

### P3 (小众模型)
21-36. embedding/reranker, OCR, 第三方模型等
