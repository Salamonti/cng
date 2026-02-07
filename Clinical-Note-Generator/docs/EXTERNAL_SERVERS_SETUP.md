# External Servers Setup and Port Configuration

This document summarizes the current externalized service setup and where to change ports.

---

## Overview (What Runs Where)

1) FastAPI (Main App)
- Runs the UI + API server.
- Default port: 7860.
- Started via: `C:\project-root\Clinical-Note-Generator\start_fastapi_server_external.bat`

2) Llama note generation / Q&A (llama-server)
- External llama.cpp server.
- Default primary port: 8081.
- Optional fallback port: 8036.

3) OCR (llama-server multimodal)
- External llama.cpp multimodal server.
- Default primary port: 8090.
- Optional fallback port: 8091.

4) RAG (retriever)
- External RAG service (no LLM).
- Port: 8007 (single endpoint, no fallback).

5) ASR (WhisperX)
- External ASR service (FastAPI).
- Primary port: 8095.
- Optional fallback port: 8096 (CPU).
- Endpoints:
  - `/transcribe_diarized` (multipart, field `audio`, returns plain text)
  - `/v1/audio/transcriptions` (OpenAI-style multipart, field `file` or `audio`, returns JSON `{ "text": "..." }`)
  - `/asr_engine` (info)

---

## Where to Change Ports (Single Source of Truth)

All ports are set via environment variables. No config.json fallback is used.

### 1) FastAPI (Main App)
- Change in the startup command or set env var:
  - `FASTAPI_PORT` (if you run uvicorn manually)
  - In `start_fastapi_server_external.bat`, change the `--port` value in the uvicorn command.

### 2) Note Gen / Q&A
- Set in environment:
  - `NOTEGEN_URL_PRIMARY` (e.g., `http://127.0.0.1:8081`)
  - `NOTEGEN_URL_FALLBACK` (e.g., `http://127.0.0.1:8036`)

### 3) OCR
- Set in environment:
  - `OCR_URL_PRIMARY` (e.g., `http://127.0.0.1:8090`)
  - `OCR_URL_FALLBACK` (e.g., `http://127.0.0.1:8091`)

### 4) RAG
- Set in environment:
  - `RAG_URL` (e.g., `http://127.0.0.1:8007`)

### 5) ASR
- Set in environment:
  - `ASR_URL` (e.g., `http://127.0.0.1:8095`)
  - `ASR_URL_FALLBACK` (e.g., `http://127.0.0.1:8096`)
  - `ASR_API_KEY` (default: `notegenadmin`)
  - `ASR_ENABLE_DIARIZATION` (1 = on, 0 = off)

---

## Current Startup Commands

### A) Main FastAPI
Use the external batch:
- `C:\project-root\Clinical-Note-Generator\start_fastapi_server_external.bat`

This sets:
- NOTEGEN_URL_PRIMARY
- NOTEGEN_URL_FALLBACK
- OCR_URL_PRIMARY
- OCR_URL_FALLBACK
- RAG_URL
- ASR_URL
- ASR_URL_FALLBACK
- ASR_API_KEY
- ASR_ENABLE_DIARIZATION

### B) ASR (WhisperX)
Run in the venv where WhisperX is installed:
```
cd C:\project-root\Clinical-Note-Generator
.\.venv\Scripts\Activate.ps1
$env:ASR_API_KEY="notegenadmin"
$env:ASR_ENABLE_DIARIZATION="1"   # optional
uvicorn asr.asr_service:app --host 0.0.0.0 --port 8095
```
Fallback CPU instance (no diarization):
```
cd C:\project-root\Clinical-Note-Generator
.\.venv\Scripts\Activate.ps1
$env:ASR_API_KEY="notegenadmin"
uvicorn asr.asr_service_cpu:app --host 0.0.0.0 --port 8096
```

### C) Llama note gen / Q&A (llama.cpp)
Example (edit model path and params):
```
llama-server.exe --model <path_to.gguf> --host 0.0.0.0 --port 8081 --ctx-size 64000 --n-gpu-layers <N> --ubatch-size <N> --threads <N> --batch-size <N> --parallel <N> --jinja
```

### D) OCR (llama.cpp multimodal)
Example:
```
llama-server.exe --model <ocr_model.gguf> --mmproj <mmproj.gguf> --host 0.0.0.0 --port 8090 --ctx-size <N> --n-gpu-layers <N> --threads <N> --batch-size <N> --parallel <N>
```

### E) RAG (retriever)
Run your existing RAG service on port 8007.

---

## Open WebUI Connection (ASR)

Use the OpenAI audio endpoint:
- Base URL: `http://host.docker.internal:8095`
- API key: `notegenadmin`
- Open WebUI will call `/v1/audio/transcriptions` (multipart).

---

## What Was Removed / Externalized

- Internal llama-server management is disabled (no auto-start from Python).
- OCR auto-start in Python removed.
- Conv normalizer removed from usage.

---

## Quick Health Checks

- FastAPI: `http://<host>:7860/api/health`
- ASR: `http://<host>:8095/asr_engine`
- Llama: `http://<host>:8081/health`
- OCR: `http://<host>:8090/health`
- RAG: `http://<host>:8007/health` (if implemented)

---

## Notes
- All ports and URLs are controlled by environment variables.
- Config.json now only contains reference comments and non-endpoint settings.

The Whisper model selection is hardcoded in the ASR engine:
                                                                                                                                                     
  - C:\Clinical-Note-Generator\server\services\asr_whisperx.py:148 sets WHISPERX_MODEL_PATH = r"C:\Clinical-Note-Generator\models\whisper\medium.en"


  the new whisper (without diarization) only in cpu int8 with base.en
    cd C:\project-root\Clinical-Note-Generator                                               
  .\.venv\Scripts\Activate.ps1                                                             
  $env:ASR_API_KEY="notegenadmin"                                                          
  uvicorn asr.asr_service_cpu:app --host 0.0.0.0 --port 8096 


  references:
  main LLM:
  set CUDA_VISIBLE_DEVICES=0 && llama-server -m "C:\Clinical-Note-Generator\models\llama\Qwen3-30B-A3B-instruct_IQ4XS.gguf" -c 51200 --jinja --host 0.0.0.0 --port 8081 -ctk q8_0 -ctv q8_0

  OCR:
  set CUDA_VISIBLE_DEVICES=0 && llama-server -m "C:\Clinical-Note-Generator\models\ocr\Nanonets-OCR2-3B.Q6_K.gguf" --mmproj "C:\Clinical-Note-Generator\models\ocr\Nanonets-OCR2-3B.mmproj-f16.gguf" --jinja --host 0.0.0.0 --port 8090 -c 4096 -ctk q8_0 -ctv q8_0
  backup on 8091

  ASR:
  set ASR_API_KEY=notegenadmin && uvicorn asr.asr_service:app --host 0.0.0.0 --port 8095

  Second LLM:
  set CUDA_VISIBLE_DEVICES=1 && llama-server -m "D:\Models\VIsionModels\Qwen3-8B\Qwen3-VL-8B-Instruct-IQ4_XS.gguf" --mmproj "D:\Models\VIsionModels\Qwen3-8B\mmproj-Qwen3VL-8B-Instruct-F16.gguf" --host 0.0.0.0 --port 8036 -c 25600 -ctk q8_0 -ctv q8_0 -b 1024 -ub 256 --temp 0.2 -fa on -n 2048 --no-context-shift --keep -1 --repeat-penalty 1.2 --frequency-penalty 0.5 --repeat-last-n 128

FastAPI server
set ASR_API_KEY=notegenadmin && C:\project-root\Clinical-Note-Generator\start_fastapi_server_external.bat

text only mostly on CPU but runs OK:
set CUDA_VISIBLE_DEVICES=0 LLAMA_CHAT_TEMPLATE_KWARGS={"reasoning_effort":"low"} && llama-server -m "C:\Clinical-Note-Generator\models\llama\ggml-gpt-oss-20b-mxfp4.gguf" -c 12800 --jinja --host 0.0.0.0 --port 8038 -ngl 999 --cpu-moe -t 12

image creator in docker/wsl:
islameissa@WORKSTATION:~/projects/stable-diffusion-webui-docker$ docker run -it --rm   --name comfyui   --gpus all   -e CUDA_VISIBLE_DEVICES=1 -p 8188:8188   -v "$(pwd)"/storage:/root   -v "$(pwd)"/storage-models/models:/root/ComfyUI/models   -v "$(pwd)"/storage-user/output:/root/ComfyUI/output   -e CLI_ARGS="--listen"   yanwk/comfyui-boot:cu128-slim

Big-Consideration to change to Mistral
set CUDA_VISIBLE_DEVICES=0 && llama-server -m "D:\Models\VIsionModels\Ministral-3-14B-Instruct-2512-Q5_K_M.gguf" --mmproj "D:\Models\VIsionModels\Ministral-3-14B-Instruct-2512-BF16-mmproj.gguf" --jinja -c 51200 --host 0.0.0.0 --port 8081 -ctk q8_0 -ctv q8_0 -b 1024 -ub 256 -fa on
