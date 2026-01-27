# C:\Clinical-Note-Generator\server\services\note_gen_server.py
import asyncio
import aiohttp
import json
import os
import re
import subprocess
import shutil
from pathlib import Path
from typing import AsyncIterator, Dict, Optional, List
import logging
import socket
from contextlib import suppress
import unicodedata

logger = logging.getLogger(__name__)


def _sanitize_prompt_text(text: str) -> str:
    """
    Normalize prompt text so it is safe for JSON / UTF-8 and llama-server.

    - Convert to string if needed.
    - Normalize unicode (NFKD) to split accents from base characters.
    - Encode/decode to drop any invalid sequences.
    - Finally, strip to plain ASCII, removing fancy quotes and symbols.
    """
    try:
        if not isinstance(text, str):
            text = str(text)
        # Normalize unicode and drop invalid sequences
        normalized = unicodedata.normalize("NFKD", text)
        safe_utf8 = normalized.encode("utf-8", "ignore").decode("utf-8", "ignore")
        # Enforce plain ASCII for the actual payload sent to llama-server
        safe_ascii = safe_utf8.encode("ascii", "ignore").decode("ascii", "ignore")
        return safe_ascii
    except Exception:
        # In worst case, fall back to best-effort string conversion
        return str(text)


class LlamaServerManager:
    """Manages a single persistent llama-server instance"""

    def __init__(self):
        self.process = None
        self.config_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        self.config = self._load_config()

        # Port and URL respect config
        self.server_port = int(self.config.get("llama_server_port", 8081))
        self.server_url = f"http://localhost:{self.server_port}"
        # Do NOT auto-manage external llama-server unless explicitly enabled
        self.auto_manage = bool(self.config.get("llama_auto_manage", False))
        self.is_starting = False
        self.last_error: Optional[str] = None
        self._last_attempted_paths: List[str] = []
        # Tuning parameters
        try:
            self.threads = int(self.config.get("llama_server_threads", 8))
        except Exception:
            self.threads = 8
        try:
            # Safer default for large models; can be raised later via config
            self.batch_size = int(self.config.get("llama_server_batch_size", 256))
        except Exception:
            self.batch_size = 256
        try:
            self.cuda_mode = str(self.config.get("llama_cuda_mode", "config")).lower()
        except Exception:
            self.cuda_mode = "config"
        try:
            self.cuda_min_vram_mb = int(self.config.get("llama_cuda_min_vram_mb", 12000))
        except Exception:
            self.cuda_min_vram_mb = 12000
        # Optional logging control
        self.log_disable = bool(self.config.get("llama_server_log_disable", True))
        # Optional CUDA backend controls via env
        try:
            self.force_cublas = bool(self.config.get("llama_force_cublas", False))
        except Exception:
            self.force_cublas = False
        try:
            mmq_enable = self.config.get("llama_mmq_enable")
            if mmq_enable is None:
                self.mmq_enable = None
            else:
                self.mmq_enable = bool(mmq_enable)
        except Exception:
            self.mmq_enable = None
        # Arbitrary environment overrides
        self.extra_env = {}
        try:
            if isinstance(self.config.get("llama_env"), dict):
                # ensure stringified values
                self.extra_env = {str(k): str(v) for k, v in self.config["llama_env"].items()}
        except Exception:
            self.extra_env = {}
        # Serialize lifecycle operations to avoid race during rapid restarts
        self._op_lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Config & paths
    # ------------------------------------------------------------------
    def _load_config(self) -> Dict:
        """Load configuration from config.json"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "default_note_temperature": 0.2,
            "default_note_max_tokens": 2048,
            "default_top_k": 20,
            "default_top_p": 0.92,
            "default_min_p": 0.06,
            "llm_model": "C:\\Clinical-Note-Generator\\models\\llama\\MedPruned-GPTOSS20B-MXFP4.gguf",
        }

    def reload_config(self) -> None:
        """Reload configuration and update derived fields (port/url)."""
        self.config = self._load_config()
        self.server_port = int(self.config.get("llama_server_port", 8081))
        self.server_url = f"http://localhost:{self.server_port}"
        self.auto_manage = bool(self.config.get("llama_auto_manage", False))
        try:
            self.threads = int(self.config.get("llama_server_threads", self.threads))
        except Exception:
            pass
        try:
            self.batch_size = int(self.config.get("llama_server_batch_size", self.batch_size))
        except Exception:
            pass
        try:
            self.cuda_mode = str(self.config.get("llama_cuda_mode", self.cuda_mode)).lower()
        except Exception:
            pass
        try:
            self.cuda_min_vram_mb = int(self.config.get("llama_cuda_min_vram_mb", self.cuda_min_vram_mb))
        except Exception:
            pass
        try:
            self.force_cublas = bool(self.config.get("llama_force_cublas", self.force_cublas))
        except Exception:
            pass
        try:
            mmq_enable = self.config.get("llama_mmq_enable", self.mmq_enable)
            self.mmq_enable = None if mmq_enable is None else bool(mmq_enable)
        except Exception:
            pass
        try:
            if isinstance(self.config.get("llama_env"), dict):
                self.extra_env = {str(k): str(v) for k, v in self.config["llama_env"].items()}
            else:
                self.extra_env = {}
        except Exception:
            self.extra_env = {}

    @staticmethod
    def _normalize_path(p: str) -> str:
        # Strip quotes, expand env vars, and normalize slashes
        p = p.strip().strip('"')
        return os.path.normpath(os.path.expandvars(p))

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _default_models_root(self) -> Path:
        # Your stated location
        return Path(r"C:\Clinical-Note-Generator\models")

    def _gpu_meets_requirement(self, min_vram_mb: int) -> bool:
        """Return True when a GPU with at least min_vram_mb memory is present."""
        smi = shutil.which("nvidia-smi")
        if not smi:
            return False
        try:
            result = subprocess.run(
                [smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            for raw in (result.stdout or "").splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    vram = int(float(line))
                except ValueError:
                    continue
                if vram >= min_vram_mb:
                    return True
        except Exception:
            return False
        return False

    def _apply_cuda_mode(self, env: Dict[str, str]) -> None:
        """Adjust GGML_CUDA_DISABLE based on configured mode."""
        mode = (self.cuda_mode or "config").lower()
        if mode == "gpu":
            env["GGML_CUDA_DISABLE"] = "0"
        elif mode == "cpu":
            env["GGML_CUDA_DISABLE"] = "1"
        elif mode == "auto":
            env["GGML_CUDA_DISABLE"] = "0" if self._gpu_meets_requirement(self.cuda_min_vram_mb) else "1"
        else:
            # Leave env as-is when mode is "config" or unknown.
            pass

    def _get_model_search_paths(self) -> List[Path]:
        """Compute search paths for LLM .gguf files.
        Order (earlier wins):
          1) models_dir from config or default C:\\Clinical-Note-Generator\\models
          2) <models_dir>\llama
          3) model_search_paths entries (with tokens expanded)
          4) <repo_root>\models
          5) <repo_root>\models\llama
        """
        paths: List[Path] = []

        # 1) models_dir
        models_dir_cfg = self.config.get("models_dir")
        if models_dir_cfg:
            models_dir = Path(self._normalize_path(models_dir_cfg))
        else:
            models_dir = self._default_models_root()
        paths.append(models_dir)

        # 2) <models_dir>\llama
        paths.append(models_dir / "llama")

        # 3) model_search_paths
        search_cfg = self.config.get("model_search_paths", [])
        repo_root = self._repo_root()
        token_map = {
            "<repo_root>": str(repo_root),
            "<models_dir>": str(models_dir),
        }
        for raw in search_cfg:
            s = raw
            for token, value in token_map.items():
                s = s.replace(token, value)
            paths.append(Path(self._normalize_path(s)))

        # 4) repo_root\models and 5) repo_root\models\llama
        paths.append(repo_root / "models")
        paths.append(repo_root / "models" / "llama")

        # De-duplicate while preserving order
        dedup: List[Path] = []
        seen = set()
        for p in paths:
            ps = str(p)
            if ps not in seen:
                dedup.append(p)
                seen.add(ps)
        return dedup

    def _find_llama_server(self) -> str:
        """Find llama-server executable"""
        env_path = os.environ.get("LLAMA_SERVER")
        if env_path:
            env_path_n = self._normalize_path(env_path)
            if os.path.exists(env_path_n):
                return env_path_n

        possible_paths = [
            self._repo_root() / "executables" / "llama-server.exe",
            self._repo_root() / "llama-server.exe",
            Path.cwd() / "llama-server.exe",
            Path.cwd().parent / "llama-server.exe",
            Path("llama-server.exe"),
            Path("llama-server"),
        ]

        for path in possible_paths:
            path_str = str(path)
            if os.path.exists(path_str):
                return os.path.abspath(path_str)

        return "llama-server"

    def _resolve_relative_against(self, base: Path, maybe_rel: str) -> Optional[str]:
        candidate = base / maybe_rel
        candidate = Path(self._normalize_path(str(candidate)))
        if candidate.exists():
            return str(candidate)
        return None

    def _get_model_path(self) -> str:
        """Resolve the LLM model path based on config and search paths.
        Supports:
          - Absolute path in llm_model
          - Relative path (with slashes) resolved against repo root and models_dir
          - Bare filename searched across configured search paths
        """
        llm_model_raw = self.config.get("llm_model", "C:\\Clinical-Note-Generator\\models\\llama\\BioMistralMed-2x7b.Q8_0.gguf")
        llm_model = self._normalize_path(llm_model_raw)

        # 1) Absolute path
        if os.path.isabs(llm_model):
            logger.info(f"Using absolute llm_model path: {llm_model}")
            return llm_model

        # Prepare search
        search_paths = self._get_model_search_paths()
        attempted: List[str] = []
        
        # 2) If it looks like a relative path (contains / or \\), try resolving against repo_root and models_dir
        if ("/" in llm_model) or ("\\" in llm_model):
            for base in [self._repo_root(), search_paths[0]]:  # repo root then models_dir
                resolved = self._resolve_relative_against(base, llm_model)
                if resolved:
                    logger.info(f"Resolved relative llm_model '{llm_model}' against '{base}' -> {resolved}")
                    return resolved
                attempted.append(str(Path(base) / llm_model))

        # 3) Treat as bare filename: search across known paths
        filename = llm_model
        for base in search_paths:
            candidate = Path(base) / filename
            candidate = Path(self._normalize_path(str(candidate)))
            attempted.append(str(candidate))
            if candidate.exists():
                logger.info(f"Found llm_model in search path: {candidate}")
                return str(candidate)

        # 4) Try discovering any .gguf model in search paths as last resort
        for base in search_paths:
            try:
                if base.exists():
                    for p in base.glob("*.gguf"):
                        logger.warning(f"LLM model '{llm_model}' not found; using discovered model: {p}")
                        self._last_attempted_paths = attempted
                        return str(p)
            except Exception:
                continue

        # 5) Final fallback to <models_dir>\llama\<filename>
        fallback = Path(search_paths[1]) / filename if len(search_paths) > 1 else Path(filename)
        logger.warning(
            "LLM model not found in expected locations. Attempted:\n" + "\n".join(attempted) + f"\nFalling back to: {fallback}"
        )
        self._last_attempted_paths = attempted
        return str(fallback)

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------
    async def _wait_port_free(self, host: str, port: int, timeout: float = 10.0, interval: float = 0.2) -> bool:
        """Wait until TCP port is not accepting connections (free)."""
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            try:
                with socket.create_connection((host, port), timeout=0.2):
                    # still open
                    await asyncio.sleep(interval)
                    continue
            except Exception:
                return True
        return False
    async def _kill_all_llama_servers(self):
        """Kill llama-server processes on our port only (don't affect OCR server)"""
        logger.info(f"Killing llama-server processes on port {self.server_port}...")

        killed_processes = []

        try:
            import psutil

            # Find and kill llama-server processes using our port only
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if proc.info['name'] and 'llama-server' in proc.info['name'].lower():
                        # Check if this process is using our port
                        try:
                            connections = proc.connections(kind='inet')
                            for conn in connections:
                                if conn.laddr.port == self.server_port:
                                    pid = proc.info['pid']
                                    logger.info(f"Found llama-server process on our port {self.server_port} (PID: {pid}), terminating...")
                                    proc.terminate()
                                    killed_processes.append(pid)
                                    break
                        except (psutil.AccessDenied, psutil.NoSuchProcess):
                            # Can't check connections, skip this process
                            continue
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            # Wait a bit for graceful termination
            if killed_processes:
                await asyncio.sleep(2)

                # Force kill any that didn't terminate gracefully
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        if proc.info['name'] and 'llama-server' in proc.info['name'].lower():
                            connections = proc.connections(kind='inet')
                            for conn in connections:
                                if conn.laddr.port == self.server_port:
                                    pid = proc.info['pid']
                                    logger.warning(f"Force killing stubborn llama-server process on port {self.server_port} (PID: {pid})")
                                    proc.kill()
                                    break
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue

        except ImportError:
            # psutil not available, use Windows commands
            logger.info("psutil not available, using Windows taskkill command")

        # Windows fallback: Use netstat to find process using our port, then kill it
        try:
            import subprocess

            # Find process using our port
            result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True, shell=True)
            port_pattern = f"127.0.0.1:{self.server_port}"

            for line in result.stdout.split('\n'):
                if port_pattern in line and 'LISTENING' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        logger.info(f"Found process using port {self.server_port}, PID: {pid}")
                        try:
                            # Kill the specific PID
                            subprocess.run(['taskkill', '/F', '/PID', pid],
                                         capture_output=True, text=True, shell=True)
                            logger.info(f"Killed process PID {pid} using port {self.server_port}")
                        except Exception as e:
                            logger.warning(f"Could not kill PID {pid}: {e}")

        except Exception as e:
            logger.error(f"Could not use netstat/taskkill method: {e}")

        # Final catch-all exception handler
        if not killed_processes:
            try:
                # One more check for any remaining processes
                import subprocess
                verify_result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq llama-server.exe'],
                                      capture_output=True, text=True, shell=True)
                if 'llama-server.exe' not in verify_result.stdout:
                    logger.info("Final verification: no llama-server processes running")
            except Exception as e:
                logger.warning(f"Could not perform final verification: {e}")

        if killed_processes:
            logger.info(f"Killed {len(killed_processes)} llama-server processes: {killed_processes}")
        else:
            logger.info("No llama-server processes were running")

    async def is_server_running(self) -> bool:
        """Check if llama-server is responding"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(f"{self.server_url}/health") as response:
                    return response.status == 200
        except Exception:
            return False

    async def start_server(self) -> bool:
        """Start the llama-server - always kill any existing instances first to ensure single instance"""
        if not self.auto_manage:
            self.last_error = (
                "llama_auto_manage is disabled; not starting or killing llama-server."
            )
            logger.info(self.last_error)
            return False

        logger.info("Starting llama-server under manager control (llama_auto_manage=True)")
        async with self._op_lock:
            # Kill any existing llama-server processes on our port only
            await self._kill_all_llama_servers()
            # Ensure port frees before starting
            await self._wait_port_free("127.0.0.1", self.server_port, timeout=10.0, interval=0.2)

            if self.is_starting:
                # Wait for ongoing startup
                for _ in range(30):
                    await asyncio.sleep(1)
                    if await self.is_server_running():
                        return True
                return False

            self.is_starting = True
            try:
                llama_server = self._find_llama_server()
                model_path = self._get_model_path()

                if not os.path.exists(llama_server):
                    self.last_error = f"llama-server executable not found at: {llama_server}"
                    raise FileNotFoundError(self.last_error)

                if not os.path.exists(model_path):
                    self.last_error = (
                        "Model file not found. Resolved path: " + model_path +
                        "\nAttempted: " + " | ".join(self._last_attempted_paths or []) +
                        "\nHint: set 'llm_model' to an absolute path, or place the file in one of the search paths, or configure 'models_dir'/'model_search_paths' in config.json."
                    )
                    raise FileNotFoundError(self.last_error)

                # Start llama-server
                n_gpu_layers = str(self.config.get("llama_server_gpu_layers", 999))
                enable_jinja = bool(self.config.get("llama_server_enable_jinja", True))
                chat_template_val = str(self.config.get("llama_server_chat_template", "") or "").strip()

                args = [
                    llama_server,
                    "--model", model_path,
                    "--port", str(self.server_port),
                    "--host", "0.0.0.0",
                    "--ctx-size", str(self.config.get("context_length", 64000)),
                    "--n-gpu-layers", n_gpu_layers,
                    "--ubatch-size", str(self.config.get("llama_server_ubatch_size", 128)),
                    "--threads", str(self.threads),
                    "--batch-size", str(self.batch_size),
                    "--parallel", str(self.config.get("llama_server_parallel", 1)),
                ]
                # Enable parallel request processing
                presence_penalty = self.config.get("llama_presence_penalty")
                if presence_penalty is not None:
                    args.extend(["--presence-penalty", str(presence_penalty)])
                ctk_val = self.config.get("llama_ctk")
                if ctk_val:
                    args.extend(["-ctk", str(ctk_val)])
                ctv_val = self.config.get("llama_ctv")
                if ctv_val:
                    args.extend(["-ctv", str(ctv_val)])
                if self.log_disable:
                    args.append("--log-disable")
                if bool(self.config.get("llama_no_context_shift", False)):
                    args.append("--no-context-shift")
                if bool(self.config.get("llama_cont_batching", False)):
                    args.append("--cont-batching")
                fa_mode = self.config.get("llama_fa")
                if fa_mode is not None:
                    args.extend(["-fa", str(fa_mode)])
                if bool(self.config.get("llama_no_mmap", False)):
                    args.append("--no-mmap")
                if chat_template_val and enable_jinja:
                    args.extend(["--chat-template", chat_template_val])

                logger.info("Starting llama-server with parameters:")
                logger.info(f"  Model: {model_path}")
                logger.info(f"  Port: {self.server_port}")
                logger.info(f"  Context size: {self.config.get('context_length', 32000)}")
                logger.info(f"  GPU layers: {n_gpu_layers}")
                logger.info(f"  Threads: {self.threads}")
                logger.info(f"  Batch size: {self.batch_size}")
                logger.info(f"  CUDA mode: {self.cuda_mode} (min VRAM {self.cuda_min_vram_mb} MB)")
                logger.info(f"  Jinja templating: {enable_jinja}")
                chat_template_logged = chat_template_val if (chat_template_val and enable_jinja) else ""
                logger.info(f"  Chat template: {chat_template_logged or '<none>'}")
                logger.info("Command: %s", " ".join(args))

                # Prepare environment for the child process
                env = os.environ.copy()
                if self.force_cublas:
                    env["GGML_CUDA_FORCE_CUBLAS"] = "1"
                if self.mmq_enable is not None:
                    env["GGML_CUDA_MMQ_ENABLE"] = "1" if self.mmq_enable else "0"
                for k, v in (self.extra_env or {}).items():
                    env[str(k)] = str(v)
                # Optional explicit GPU visibility from config (overrides process env)
                try:
                    vis = self.config.get("llama_server_cuda_visible_devices")
                    if isinstance(vis, (int, float)):
                        env["CUDA_VISIBLE_DEVICES"] = str(int(vis))
                    elif isinstance(vis, str) and vis.strip():
                        env["CUDA_VISIBLE_DEVICES"] = vis.strip()
                except Exception:
                    pass
                self._apply_cuda_mode(env)

                # Optional main GPU selection among visible devices
                try:
                    mg = self.config.get("llama_server_main_gpu")
                    if isinstance(mg, (int, float)):
                        args.extend(["--main-gpu", str(int(mg))])
                    elif isinstance(mg, str) and mg.strip():
                        args.extend(["--main-gpu", mg.strip()])
                except Exception:
                    pass

                try:
                    logger.info("CUDA_VISIBLE_DEVICES=%s", env.get("CUDA_VISIBLE_DEVICES", "<unset>"))
                except Exception:
                    pass

                self.process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.PIPE,
                    env=env,
                )

                # Wait for server to become available - increased timeout for large models
                logger.info("Waiting for llama-server to start (this may take 2-3 minutes for large models)...")
                for i in range(180):  # Wait up to 3 minutes for startup (large models take time)
                    await asyncio.sleep(1)

                    # Log progress every 30 seconds
                    if i > 0 and i % 30 == 0:
                        logger.info(f"Still waiting for llama-server startup... ({i}/180 seconds)")

                    if await self.is_server_running():
                        logger.info("✅ llama-server started successfully on port %s", self.server_port)
                        return True

                    if self.process.returncode is not None:
                        # Process died during startup - get all available output
                        try:
                            stderr_output = await self.process.stderr.read()
                            stdout_output = await self.process.stdout.read()
                            error_msg = stderr_output.decode('utf-8', errors='ignore')
                            stdout_msg = stdout_output.decode('utf-8', errors='ignore')
                            combined_output = f"stderr: {error_msg}\nstdout: {stdout_msg}"
                            logger.error("llama-server process died during startup")
                            logger.error(f"Exit code: {self.process.returncode}")
                            logger.error(f"Output: {combined_output}")
                            self.last_error = f"Process died during startup (exit code: {self.process.returncode}). Output: {combined_output}"
                        except Exception as e:
                            logger.error(f"Could not read process output: {e}")
                            self.last_error = f"Process died during startup (exit code: {self.process.returncode}). Could not read output."
                        return False

                # Check if the process is still alive but not responding
                if self.process and self.process.returncode is None:
                    # Process is running but not responding on health endpoint
                    logger.error("llama-server startup timeout - process running but not responding")
                    self.last_error = f"Startup timeout: llama-server process (PID: {self.process.pid}) started but did not respond on /health endpoint after 3 minutes"
                else:
                    logger.error("llama-server startup timeout - process died")
                    self.last_error = "Startup timeout: llama-server process died during startup after 3 minutes"
                return False

            except Exception as e:
                logger.error(f"Error starting llama-server: {e}")
                if not self.last_error:
                    self.last_error = str(e)
                return False
            finally:
                self.is_starting = False

    async def stop_server(self):
        """Stop ALL llama-server instances"""
        logger.info("Stopping all llama-server instances...")

        async with self._op_lock:
            # Stop our managed process first if it exists
            if self.process and self.process.returncode is None:
                try:
                    self.process.terminate()
                    await asyncio.wait_for(self.process.wait(), timeout=8)
                    logger.info("Managed llama-server process stopped gracefully")
                except asyncio.TimeoutError:
                    with suppress(Exception):
                        self.process.kill()
                        await self.process.wait()
                        logger.info("Managed llama-server process force killed")

            # Kill all llama-server processes to ensure complete cleanup
            await self._kill_all_llama_servers()
            # Ensure port is free before returning
            await self._wait_port_free("127.0.0.1", self.server_port, timeout=10.0, interval=0.2)

            # Clear our process reference
            self.process = None
            logger.info("All llama-server instances stopped")

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    async def _reset_server_context(self) -> None:
        """Ask llama-server to drop cached KV data."""
        if not await self.is_server_running():
            return
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.server_url}/command",
                    json={"cmd": "reset"},
                    headers={"Content-Type": "application/json"},
                ):
                    pass
        except Exception:
            pass

    async def generate_completion(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        stop: Optional[List[str]] = None,
    ) -> AsyncIterator[str]:
        """Generate text using llama-server in non-streaming mode and yield chunks."""
        prompt = _sanitize_prompt_text(prompt)

        if not await self.is_server_running():
            if self.auto_manage:
                if not await self.start_server():
                    raise RuntimeError("Failed to start llama-server")
            else:
                self.last_error = (
                    f"llama-server not reachable at {self.server_url}. "
                    "Start it with start_llama_server.bat or enable 'llama_auto_manage' in config.json."
                )
                raise RuntimeError(self.last_error)

        await self._validate_active_model()

        def _cfg_float(name: str, default: float) -> float:
            try:
                return float(self.config.get(name, default))
            except Exception:
                return default

        def _cfg_int(name: str, default: int) -> int:
            try:
                return int(self.config.get(name, default))
            except Exception:
                return default

        repeat_penalty = _cfg_float("default_repeat_penalty", 1.18)
        repeat_last_n = max(64, _cfg_int("default_repeat_last_n", 1024))
        top_p = _cfg_float("default_top_p", 0.9)
        top_k = max(1, _cfg_int("default_top_k", 40))
        min_p = _cfg_float("default_min_p", 0.05)
        seed = _cfg_int("default_seed", -1)

        top_p = min(max(top_p, 0.01), 1.0)
        min_p = min(max(min_p, 0.0), top_p)
        if seed < -1:
            seed = -1

        payload = {
            "prompt": prompt,
            "temperature": temperature,
            "n_predict": max_tokens,
            "stream": False,
            "repeat_penalty": repeat_penalty,
            "repeat_last_n": repeat_last_n,
            "n_keep": 256,
            "cache_prompt": False,
            "seed": seed,
            "top_p": top_p,
            "top_k": top_k,
            "min_p": min_p,
        }

        if stop is not None:
            payload["stop"] = stop
            if stop:
                payload["trim_stop"] = True

        try:
            content = await self._request_full_completion(payload)
            if not content:
                logger.warning("llama-server returned empty content")
                return

            if self._detect_commentary_behavior(content):
                logger.warning("Commentary behavior detected in completion output")
            else:
                self._detect_section_repetition(content)
                self._detect_content_repetition(content)

            for chunk in self._chunk_text(content, 600):
                if chunk:
                    yield chunk
        finally:
            await self._reset_server_context()

    async def _validate_active_model(self) -> None:
        """Ensure the running llama-server matches the configured model."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as sess:
                async with sess.get(f"{self.server_url}/v1/models") as r:
                    if r.status != 200:
                        return
                    data = await r.json()
                    active_id = None
                    if isinstance(data, dict):
                        dl = data.get("data")
                        if isinstance(dl, list) and dl and isinstance(dl[0], dict):
                            active_id = str(dl[0].get("id") or dl[0].get("name") or dl[0].get("model") or "").strip() or None
                        if not active_id:
                            ml = data.get("models")
                            if isinstance(ml, list) and ml and isinstance(ml[0], dict):
                                active_id = str(ml[0].get("model") or ml[0].get("name") or ml[0].get("id") or "").strip() or None
                    if not active_id:
                        return
                    cfg_path = self._get_model_path()
                    cfg_norm = os.path.normcase(os.path.normpath(cfg_path))
                    act_norm = os.path.normcase(os.path.normpath(active_id)) if os.path.isabs(active_id) else None
                    if not ((act_norm and act_norm == cfg_norm) or (os.path.basename(active_id).lower() == os.path.basename(cfg_path).lower())):
                        self.last_error = f"Active model mismatch. Expected: {cfg_path}, Got: {active_id}"
                        raise RuntimeError(self.last_error)
        except Exception:
            # if the endpoint is unavailable we simply continue
            return

    async def _request_full_completion(self, payload: Dict[str, object]) -> Optional[str]:
        """Send a non-streaming completion request and return the extracted content."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.server_url}/completion",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(f"llama-server error {response.status}: {error_text[:200]}")

                raw_text = await response.text()
                try:
                    response_data = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    logger.error("Failed to parse llama-server response: %s", exc)
                    logger.debug("Response payload preview: %s", raw_text[:200])
                    raise RuntimeError(f"Invalid JSON response: {exc}")

                content = self._extract_completion_content(response_data)
                return content

    def _extract_completion_content(self, data: Dict[str, object]) -> Optional[str]:
        """Handle multiple llama.cpp / OpenAI style response formats."""
        if "content" in data:
            return data.get("content")  # type: ignore[arg-type]
        if "text" in data:
            return data.get("text")  # type: ignore[arg-type]
        if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
            choice = data["choices"][0]
            if isinstance(choice, dict):
                if "text" in choice:
                    return choice.get("text")  # type: ignore[arg-type]
                message = choice.get("message")
                if isinstance(message, dict) and "content" in message:
                    return message.get("content")  # type: ignore[arg-type]
        if "message" in data and isinstance(data["message"], dict):
            message = data["message"]
            if "content" in message:
                return message.get("content")  # type: ignore[arg-type]
        logger.warning("Unknown completion response format: keys=%s", list(data.keys()))
        return None

    def _chunk_text(self, text: str, max_chars: int = 600) -> List[str]:
        """Split text into manageable chunks for streaming to clients."""
        if not text:
            return []
        paragraphs = text.split("\n\n")
        chunks: List[str] = []
        buffer = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            tentative = f"{buffer}\n\n{para}".strip() if buffer else para
            if len(tentative) <= max_chars:
                buffer = tentative
            else:
                if buffer:
                    chunks.append(buffer)
                buffer = para
                while len(buffer) > max_chars:
                    split_point = max_chars
                    for idx in range(max_chars - 1, max(0, max_chars - 200), -1):
                        if buffer[idx] in ".!?\n":
                            split_point = idx + 1
                            break
                    chunks.append(buffer[:split_point].strip())
                    buffer = buffer[split_point:].lstrip()
        if buffer:
            chunks.append(buffer)
        return [chunk for chunk in chunks if chunk]

    # ------------------------------------------------------------------
    # Heuristics
    # ------------------------------------------------------------------
    def _detect_commentary_behavior(self, content: str) -> bool:
        """Detect if model is adding unwanted commentary instead of generating medical note"""
        start = content[:300].lower()
        patterns = [
            r"\bthe input is\b",
            r"\bthis is a\b",
            r"\bbased on the\b",
            r"\bthe transcription\b",
            r"\bthe provided\b",
            r"\banalysis:\b",
            r"\bsummary:\b",
            r"\bcommentary:\b",
            r"\bexplanation:\b",
            r"\baccording to\b",
            r"\bas can be seen\b",
            r"\bit appears that\b",
            r"\bfrom the transcription\b",
        ]
        hits = sum(1 for p in patterns if re.search(p, start))
        return hits >= 2

    def _detect_section_repetition(self, content: str) -> bool:
        """Detect if medical document sections are being repeated.
        Relaxed thresholds: ignore early content (<1200 chars) and require
        at least four different headers to appear more than 4 times before striking.
        This allows legitimate medical note structure with subsections.
        """
        if len(content) < 1200:  # Increased from 600 to give more buffer
            return False

        headers = [
            "ID",
            "History of Present Illness",
            "Past Medical History",
            "Medications",
            "Social History",
            "Allergies",
            "Physical Exam",
            "Investigations",
            "Impression",
            "Plan",
        ]
        repeated_headers = 0
        for h in headers:
            m = re.findall(rf"(?mi)^\s*{re.escape(h)}\s*:", content)
            if len(m) > 4:  # Increased from 2 to allow subsections and legitimate structure
                repeated_headers += 1
        return repeated_headers >= 4  # Increased from 2 to require more evidence of true repetition

    def _detect_content_repetition(self, generated_content: str) -> bool:
        """Detect if recent content is repetitive using two heuristics:
        1) Loop guard: last 120 chars occur >=4 times in last 1200 chars (relaxed from 3).
        2) Rolling n-gram repetition over the last ~3000 chars with longer n-grams.
        Relaxed thresholds to avoid flagging legitimate medical terminology repetition.
        """
        # 1) Loop guard for repeating paragraphs
        if len(generated_content) >= 1200:
            window = generated_content[-1200:]
            last120 = generated_content[-120:]
            try:
                if window.count(last120) >= 4:  # Increased from 3 to reduce false positives
                    return True
            except Exception:
                pass

        # 2) Rolling n-gram repetition
        tail = generated_content[-3000:]
        if len(tail) < 400:
            return False
        n = 40  # Increased from 24 to reduce false positives from medical terminology
        counts: Dict[str, int] = {}
        for i in range(0, len(tail) - n, 4):  # stride to reduce cost
            s = tail[i : i + n]
            counts[s] = counts.get(s, 0) + 1
            if counts[s] >= 5:  # Increased from 3 to allow more legitimate repetition
                return True
        return False


# Global instance
_llama_server_manager = None

def get_llama_server_manager() -> LlamaServerManager:
    """Get the global llama-server manager instance"""
    global _llama_server_manager
    if _llama_server_manager is None:
        _llama_server_manager = LlamaServerManager()
    return _llama_server_manager


# ------------------------------------------------------------------
# OCR Server Manager (llama-server with multimodal OCR model)
# ------------------------------------------------------------------
class OCRServerManager:
    """Manages a single persistent llama-server instance for OCR (vision)"""

    def __init__(self):
        self.process = None
        self.config_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        self.config = self._load_config()
        self.server_port = self._get_port()
        self.server_url = f"http://127.0.0.1:{self.server_port}"
        self.is_starting = False
        self.last_error: Optional[str] = None

    def _load_config(self) -> Dict:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def reload_config(self) -> None:
        self.config = self._load_config()
        self.server_port = self._get_port()
        self.server_url = f"http://127.0.0.1:{self.server_port}"

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _get_port(self) -> int:
        # Respect config 'ocr_server_url' if present; else default 8090
        url = str(self.config.get("ocr_server_url", "http://127.0.0.1:8090")).strip()
        try:
            if "://" in url:
                host_port = url.split("://", 1)[1].split("/", 1)[0]
            else:
                host_port = url.split("/", 1)[0]
            if ":" in host_port:
                return int(host_port.split(":", 1)[1])
        except Exception:
            pass
        return 8090

    def _find_llama_server(self) -> str:
        env_path = os.environ.get("LLAMA_SERVER")
        if env_path and os.path.exists(env_path):
            return env_path
        candidates = [
            self._repo_root() / "executables" / "llama-server.exe",
            self._repo_root() / "llama-server.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return str(c)
        return str(candidates[0])

    def _get_ocr_model_paths(self) -> Dict[str, str]:
        # Resolve OCR model paths from config first, then env, then defaults
        repo = self._repo_root()

        def _norm(p: str) -> str:
            try:
                s = str(p or "").strip().strip('"')
                s = s.replace("<repo_root>", str(repo))
                return os.path.normpath(os.path.expandvars(s))
            except Exception:
                return p

        cfg_model = self.config.get("ocr_model")
        cfg_mmproj = self.config.get("ocr_mmproj_model")

        model = cfg_model or os.environ.get("OCR_MODEL") or str(repo / "models" / "ocr" / "Nanonets-OCR-s-Q6_K.gguf")
        mmproj = cfg_mmproj or os.environ.get("MMPROJ_MODEL") or str(repo / "models" / "ocr" / "Nano_mmproj-BF16.gguf")

        return {"model": _norm(model), "mmproj": _norm(mmproj)}

    def _get_ocr_model_name(self) -> str:
        name = os.environ.get("OCR_MODEL_NAME")
        if not name:
            name = os.environ.get("OCR_CHAT_MODEL")
        if not name:
            name = str(self.config.get("ocr_model_name") or "").strip()
        if not name:
            name = "nanonets-ocr-s"
        return name

    async def is_server_running(self) -> bool:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(f"{self.server_url}/health") as response:
                    return response.status == 200
        except Exception:
            return False

    async def start_server(self) -> bool:
        if await self.is_server_running():
            return True
        if self.is_starting:
            for _ in range(20):
                await asyncio.sleep(1)
                if await self.is_server_running():
                    return True
            return False
        self.is_starting = True
        try:
            exe = self._find_llama_server()
            paths = self._get_ocr_model_paths()
            if not os.path.exists(exe):
                self.last_error = f"llama-server.exe not found at: {exe}"
                raise FileNotFoundError(self.last_error)
            if not os.path.exists(paths["model"]):
                self.last_error = f"OCR model not found at: {paths['model']}"
                raise FileNotFoundError(self.last_error)
            if not os.path.exists(paths["mmproj"]):
                self.last_error = f"MMProj model not found at: {paths['mmproj']}"
                raise FileNotFoundError(self.last_error)

            # OCR server args: add batch size and continuous batching to keep model resident
            args = [
                exe,
                "--model", paths["model"],
                "--mmproj", paths["mmproj"],
                "--port", str(self.server_port),
                "--host", "0.0.0.0",
                "--ctx-size", str(self.config.get("ocr_ctx_size", 16000)),
                "--n-gpu-layers", str(self.config.get("ocr_gpu_layers", 99)),
                "--threads", str(self.config.get("ocr_threads", 16)),
                "--batch-size", str(self.config.get("ocr_batch_size", 2048)),
            ]
            # Enable parallel request processing for OCR server
            args.extend(["--parallel", str(self.config.get("ocr_parallel", 2))])
            env = os.environ.copy()
            cuda_visible = self.config.get("ocr_cuda_visible_devices")
            if isinstance(cuda_visible, (int, float)):
                cuda_visible = str(int(cuda_visible))
            elif isinstance(cuda_visible, str):
                cuda_visible = cuda_visible.strip()
            else:
                cuda_visible = None
            if not cuda_visible:
                cuda_visible = os.environ.get("OCR_CUDA_VISIBLE_DEVICES") or os.environ.get("CUDA_VISIBLE_DEVICES")
                if cuda_visible:
                    cuda_visible = str(cuda_visible).strip()
            if cuda_visible:
                env["CUDA_VISIBLE_DEVICES"] = cuda_visible
                try:
                    print(f"[OCR] Forcing CUDA_VISIBLE_DEVICES={cuda_visible}")
                except Exception:
                    pass
            env["OCR_MODEL_NAME"] = self._get_ocr_model_name()
            self.process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                env=env,
            )
            for _ in range(40):
                await asyncio.sleep(0.5)
                if await self.is_server_running():
                    return True
                if self.process.returncode is not None:
                    stderr_output = await self.process.stderr.read()
                    self.last_error = stderr_output.decode(errors="ignore")
                    return False
            self.last_error = "OCR server startup timeout"
            return False
        except Exception as e:
            self.last_error = str(e)
            return False
        finally:
            self.is_starting = False

    async def stop_server(self) -> bool:
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=10)
                return True
            except asyncio.TimeoutError:
                try:
                    self.process.kill()
                    await self.process.wait()
                    return True
                except Exception:
                    return False
            except Exception:
                return False
        # Try to detect and kill any lingering llama-server on the OCR port
        return True


_ocr_server_manager: Optional[OCRServerManager] = None

def get_ocr_server_manager() -> OCRServerManager:
    global _ocr_server_manager
    if _ocr_server_manager is None:
        _ocr_server_manager = OCRServerManager()
    return _ocr_server_manager


class NoteGeneratorServer:
    """Note generator using persistent llama-server instead of multiple llama-cli processes"""

    def __init__(self):
        self.server_manager = get_llama_server_manager()
        self.config_path = Path(__file__).resolve().parents[2] / "config" / "config.json"

    def _load_config(self) -> Dict:
        """Load configuration from config.json"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "default_note_temperature": 0.2,
            "default_note_max_tokens": 2048,
            "default_top_k": 20,
            "default_top_p": 0.92,
            "default_min_p": 0.06,
        }

    async def generate_stream(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop: Optional[List[str]] = None,
    ) -> AsyncIterator[str]:
        """Generate streaming text using llama-server"""
        config = self._load_config()

        use_temperature = temperature if temperature is not None else config.get("default_note_temperature", 0.3)
        use_max_tokens = max_tokens if max_tokens is not None else config.get("default_note_max_tokens", 2048)
        stop_tokens = stop if stop is not None else []

        logger.info(
            "Generating with llama-server - temp: %s, max_tokens: %s", use_temperature, use_max_tokens
        )

        try:
            async for chunk in self.server_manager.generate_completion(
                prompt, use_temperature, use_max_tokens, stop=stop_tokens
            ):
                yield chunk
        except Exception as e:
            logger.error(f"Error in generate_stream: {e}")
            raise

    async def cleanup(self):
        """Clean up resources"""
        await self.server_manager.stop_server()

    # Allow admin routes to refresh configuration without restarting FastAPI
    def reload_config(self) -> None:
        try:
            self.server_manager.reload_config()
        except Exception:
            pass
