"""One read-only, offline Transformers model, owned only by this process."""

import gc
import os
import tempfile


class ContextLimitError(ValueError):
    pass


class GenerationCancelled(Exception):
    pass


class TransformersEngine:
    def __init__(self, settings):
        self.settings = settings
        self.model = None
        self.tokenizer = None
        self.cache = None
        self.torch = None
        self.device = "unloaded"

    def load(self):
        # Never import the donor project's code or put caches beside its weights.
        self.cache = tempfile.TemporaryDirectory(prefix="scholarnova-local-")
        for name in ("HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME"):
            os.environ[name] = self.cache.name
        os.environ.update(
            HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
            HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
            TOKENIZERS_PARALLELISM="false",
        )
        import psutil
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        torch.set_num_threads(2)
        self.device = self.settings.device
        if self.device == "auto":
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; choose CPU explicitly if sufficient memory is available")

        weights = self.settings.model_dir / "model.safetensors"
        if not weights.is_file():
            raise RuntimeError("A complete local model.safetensors file is required")
        weight_bytes = weights.stat().st_size
        reserve = 768 * 1024 * 1024
        cpu_required = weight_bytes * (2 if self.device == "cpu" else 1) + reserve
        if psutil.virtual_memory().available < cpu_required:
            raise RuntimeError("Insufficient free system memory for this local model")
        if self.device == "cuda" and torch.cuda.mem_get_info()[0] < weight_bytes + reserve:
            raise RuntimeError("Insufficient free GPU memory; close other GPU workloads first")

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.settings.model_dir, local_files_only=True, trust_remote_code=False,
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            self.settings.model_dir, local_files_only=True, trust_remote_code=False,
            use_safetensors=True, torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
            device_map={"": self.device}, low_cpu_mem_usage=True, attn_implementation="sdpa",
        )
        self.model.eval()

    def generate(self, messages, max_tokens, temperature, cancelled):
        from transformers import StoppingCriteria, StoppingCriteriaList

        if cancelled.is_set():
            raise GenerationCancelled()
        inputs = self.tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            return_tensors="pt", return_dict=True,
        )
        prompt_tokens = inputs["input_ids"].shape[1]
        model_context = getattr(self.model.config, "max_position_embeddings", self.settings.context)
        if prompt_tokens + max_tokens > min(self.settings.context, model_context):
            raise ContextLimitError("Input plus requested output exceeds the local context limit")

        class StopWhenCancelled(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return cancelled.is_set()

        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self.torch.inference_mode():
            outputs = self.model.generate(
                **inputs, max_new_tokens=max_tokens,
                do_sample=temperature > 0,
                temperature=temperature if temperature > 0 else None,
                top_p=1.0 if temperature > 0 else None, top_k=None,
                pad_token_id=self.tokenizer.eos_token_id,
                stopping_criteria=StoppingCriteriaList([StopWhenCancelled()]),
            )
        if cancelled.is_set():
            raise GenerationCancelled()
        generated = outputs[0, prompt_tokens:].tolist()
        eos = self.model.generation_config.eos_token_id
        eos_ids = eos if isinstance(eos, list) else [eos]
        return {
            "content": self.tokenizer.decode(generated, skip_special_tokens=True),
            "finish_reason": "length" if len(generated) >= max_tokens and generated[-1] not in eos_ids else "stop",
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": len(generated),
                "total_tokens": prompt_tokens + len(generated),
            },
        }

    def close(self):
        self.model = None
        self.tokenizer = None
        gc.collect()
        if self.torch is not None and self.device == "cuda":
            self.torch.cuda.empty_cache()
        if self.cache is not None:
            self.cache.cleanup()
            self.cache = None
