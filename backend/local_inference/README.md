# Optional local text inference

An isolated, single-model Transformers service for the local
`Qwen2.5-1.5B-Instruct` weights. It does not import LLM-Twin code, load its LoRA,
modify model files, install packages, or download models.

The desktop launcher supplies a separately provisioned Python runtime and:

- `SCHOLARNOVA_LOCAL_MODEL_DIR`: existing absolute model directory, containing
  `model.safetensors`, configuration and tokenizer files.
- `SCHOLARNOVA_LOCAL_MODEL_TOKEN`: a dedicated random token (at least 32 characters).
- `SCHOLARNOVA_LOCAL_MODEL_PORT`: loopback port, default `8766`.
- Optional `SCHOLARNOVA_LOCAL_DEVICE`: `auto` (default), `cuda` or `cpu`.
- Optional `SCHOLARNOVA_LOCAL_TIMEOUT`: generation deadline in seconds, default `90`, maximum `120`.

Run `python -B -m local_inference` from the backend directory. This requires the
provisioned runtime to contain FastAPI, Uvicorn, Torch, Transformers, Accelerate,
Safetensors and psutil. Importing the package does not import Torch or load weights.
The entry point reserves `127.0.0.1:port` before loading a model, so an occupied
port fails without allocating model memory. There is no public network binding.

Both `GET /health` and `POST /v1/chat/completions` require the dedicated Bearer
token. No browser-origin requests, streaming, images, tools or arbitrary request
fields are supported. The canonical model ID is `Qwen2.5-1.5B-Instruct`; request
matching is case-insensitive. Health reports the actual device and capacity, but
never the token or weight directory.

Capacity is deliberately small: input **including chat-template tokens** plus
requested output must fit in 2048 tokens. Output defaults to 256 tokens and is
limited to 320. Oversized input is rejected, never truncated silently. Usage is
counted from the actual tokenized prompt and generated tokens, including EOS.

There is one decoding worker and no request queue. Busy requests return 429.
Timeouts and disconnects request cooperative cancellation at the next decoding
step. The slot stays occupied until that worker actually stops; cancellation
cannot interrupt an in-progress GPU kernel or prefill instantly. Shutdown waits
for the worker before releasing model memory. The launcher may terminate its own
child process if graceful shutdown exceeds its deadline.

Loading checks free RAM and GPU memory before allocation. `auto` selects CUDA if
available; insufficient GPU memory fails explicitly instead of silently putting
the model in system RAM. These checks are conservative estimates, not a guarantee
against other programs allocating memory concurrently. Model/cache access is
offline; process-local temporary caches are created under the launcher's TEMP
directory and removed on graceful shutdown.

Offline tests: `python -m pytest tests/test_local_inference.py -q` in a configured
ScholarNova development environment. Tests mock inference; they do not load weights,
open a listening port, send cloud requests or measure model quality.
