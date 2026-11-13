# Qwen3-Reranker-0.6B: qualified MTEB measurement

[Original aggregate MTEB TaskResult](selected-full-task-result.json.txt)

This existing measurement was prepared for review on 2026-11-05. Preparing this package did not rerun evaluation. The JSON is the first complete native serial result, copied byte-for-byte, rather than a merge of selected language results. The serial run completed on 2026-11-04. It and all thirteen independent language runs exited 0; their largest per-language main-score difference was 0.00075. The serial result is reported consistently below.

## Coverage and result

MultiLongDocReranking, test split: **13 language subsets, 3,800 queries**. Subsets are ar, de, en, es, fr, hi, it, ja, ko, pt, ru, th and zh. English and Chinese each contain 800 queries; the other eleven subsets contain 200 each.

| Result | nDCG@10 |
|---|---:|
| Unweighted language macro | 0.5218869230769231 |
| Chinese (zh) | 0.51087 |
| English (en) | 0.50828 |

The macro matches MTEB TaskResult.get_score(). It is a language macro average, not a query-weighted average; all per-language aggregate scores remain in the linked JSON.

The dataset is [mteb/MultiLongDocReranking at revision ad09ce14c17bce6edae151b7f6ef12e15d91dbf3](https://huggingface.co/datasets/mteb/MultiLongDocReranking/tree/ad09ce14c17bce6edae151b7f6ef12e15d91dbf3). All **52 source Parquet files** were checked against official LFS SHA-256 metadata at that revision. A verified local snapshot was used after network/metadata-service failures. Data integrity does not establish benchmark quality.

Model snapshot: [Qwen/Qwen3-Reranker-0.6B at revision e61197ed45024b0ed8a2d74b80b4d909f1255473](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/tree/e61197ed45024b0ed8a2d74b80b4d909f1255473).

## Measured configuration

Generic VllmCrossEncoderWrapper: **FP16, eager execution, max_model_len=10240, tensor_parallel_size=1**. Query and document prefixes were both empty. The original instruction-aware Qwen3 chat template and its query/document formatting were **not injected**. HF overrides selected Qwen3ForSequenceClassification, classifier_from_token=["no","yes"], and is_original_qwen3_reranker=true.

These scores therefore measure this exact unformatted wrapper configuration, separately from the formatted /v1/rerank smoke client. The source tutorial provides no numerical quality baseline or tolerance. No quality threshold, model-card reproduction or quality improvement is claimed.

Execution used A3 / Ascend910_9382 and this immutable nightly-main image fingerprint:

    quay.io/ascend/vllm-ascend@sha256:33233f7615046f20c29ca50640d1ae3a4a2c2786628615ead95de31a0b3a9fe5

Source commits present in that image:

- [vLLM 5df63a7203cdb9328d824a27b682e77c7b9928be](https://github.com/vllm-project/vllm/commit/5df63a7203cdb9328d824a27b682e77c7b9928be)
- [vLLM Ascend 1bf75989c152c353ae0254b3e2aefa717d74f982](https://github.com/vllm-project/vllm-ascend/commit/1bf75989c152c353ae0254b3e2aefa717d74f982)

This snapshot execution does not establish supported-release status or reinstate historical model support.

| Package | Recorded version |
|---|---|
| torch | 2.10.0+cpu |
| torch-npu | 2.10.0.post7 |
| vllm | 0.32.0+empty |
| vllm-ascend | 0.19.1rc2.dev3309+g1bf75989c |
| transformers | 5.19.0 |
| mteb | 2.26.33 |
| datasets | 4.4.2 |
| pyarrow | 21.0.0 |
| numpy | 1.26.4 |
| scipy | 1.14.1 |

The five torch/torch-npu/vllm/vllm-ascend/transformers packages remained unchanged while text-evaluation dependencies were aligned inside the task container.

## Dependency and earlier-attempt limits

pip check remained nonzero (exit 1). Ancillary packages declared missing plotly, matplotlib, msguard, colorlog or expecttest. triton-ascend declared scipy==1.13.1 while this run used 1.14.1; OpenCV headless declared numpy>=2 while the text run used 1.26.4. Successful evaluation does not make all image dependency declarations consistent.

Earlier failed attempts included official-endpoint connectivity, mirror metadata API rate limiting, an initial multiprocessing entry-point guard and a physical-versus-container device-index probe. Those failures and environment adjustments remain in the private runtime evidence. They are not superseded by an assertion that every attempt passed. The complete raw-log archive is not part of this public aggregate package.

## Integrity and publication scope

The native output contains 246 NaN values in auxiliary nauc_* metrics. They are preserved exactly as emitted by MTEB. All thirteen ndcg_at_10 values used above are finite. The .json.txt suffix identifies the unmodified native text rather than claiming RFC 8259 JSON compliance. Python's native JSON reader can load this output; strict JSON readers require an explicitly normalized copy. The local .gitattributes disables newline conversion for the raw result so its byte hash survives checkout.

The linked JSON is the original **80,884-byte** aggregate TaskResult. SHA-256:

    47501a096de3328e0d007b48ed85c90be2659e01877ca599229e7d8771e67811

All 120 string values and 2,115 object keys were recursively inspected before copying. No address, credential, login name, private path, input text or per-query prediction was found. The file contains aggregate metrics, language/task metadata and timing fields. No JSON field was removed or rewritten.

This note and the unchanged aggregate JSON are the entire public review package. Host connection details, private paths and raw runtime logs are excluded.
