from ais_bench.benchmark.models import VLLMCustomAPIChat
from ais_bench.benchmark.utils.postprocess.model_postprocessors import (
    extract_non_reasoning_content,
)


# Performance request configuration generated from the Recipe. The evaluation
# renders values supplied by the Runner before AISBench loads this file.
models = [
    dict(
        attr="service",
        type=VLLMCustomAPIChat,
        abbr="vllm-api-stream-chat",
        path=__RECIPE_MODEL_PATH__,
        model=__RECIPE_SERVED_MODEL_NAME__,
        stream=True,
        request_rate=0,
        use_timestamp=False,
        retry=2,
        api_key="",
        host_ip=__RECIPE_ENDPOINT_HOST__,
        host_port=__RECIPE_ENDPOINT_PORT__,
        url="",
        max_out_len=__RECIPE_AISBENCH_MAX_OUT_LEN__,
        batch_size=1,
        trust_remote_code=False,
        generation_kwargs=dict(temperature=0.01, ignore_eos=False),
        pred_postprocessor=dict(type=extract_non_reasoning_content),
    )
]
