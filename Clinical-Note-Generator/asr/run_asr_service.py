import argparse
import os


def _set_env(key: str, value: str | None) -> None:
    if value is None:
        return
    os.environ[key] = value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run WhisperX ASR service with configurable devices and model settings."
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--workers", type=int, default=1)

    parser.add_argument("--asr-device", choices=["cpu", "cuda", "cuda:0", "cuda:1"], default=None)
    parser.add_argument("--align-device", choices=["cpu", "cuda", "cuda:0", "cuda:1"], default=None)
    parser.add_argument("--diar-device", choices=["cpu", "cuda", "cuda:0", "cuda:1"], default=None)

    parser.add_argument("--diarization", choices=["on", "off"], default=None)
    parser.add_argument("--alignment", choices=["on", "off"], default=None)

    parser.add_argument("--model-path", default=None)
    parser.add_argument("--compute-type", default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--initial-prompt", default=None)

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    _set_env("ASR_DEVICE", args.asr_device)
    _set_env("ASR_ALIGN_DEVICE", args.align_device)
    _set_env("ASR_DIAR_DEVICE", args.diar_device)

    if args.diarization is not None:
        _set_env("ASR_ENABLE_DIARIZATION", "1" if args.diarization == "on" else "0")
    if args.alignment is not None:
        _set_env("ASR_ENABLE_ALIGNMENT", "1" if args.alignment == "on" else "0")

    _set_env("ASR_MODEL_PATH", args.model_path)
    _set_env("ASR_COMPUTE_TYPE", args.compute_type)
    if args.batch_size is not None:
        _set_env("ASR_TRANSCRIBE_BATCH_SIZE", str(args.batch_size))
    _set_env("ASR_INITIAL_PROMPT", args.initial_prompt)

    import uvicorn

    uvicorn.run(
        "asr.asr_service:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
