# C:\Clinical-Note-Generator\server\control_agent.py
# server/control_agent.py
import json
import os
import socket
import subprocess
from typing import Dict, Tuple

from aiohttp import web


def load_config() -> Dict:
    here = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.normpath(os.path.join(here, '..', 'config', 'config.json'))
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_admin_token() -> str:
    # Prefer env var, then config
    cfg = load_config()
    return os.environ.get('ADMIN_API_KEY') or cfg.get('admin_api_key', 'notegenadmin')


def auth_ok(request: web.Request) -> bool:
    # Require admin bearer token only
    admin_expected = get_admin_token()
    if not admin_expected:
        return False
    auth = request.headers.get('authorization') or request.headers.get('Authorization')
    admin_token = None
    if auth and auth.lower().startswith('bearer '):
        admin_token = auth.split(' ', 1)[1].strip()
    return admin_token == admin_expected


def run_cmd(args: list[str], timeout: int = 10) -> Tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, shell=False)
        out = (p.stdout or '') + (p.stderr or '')
        return p.returncode, out.strip()
    except Exception as e:
        return 1, str(e)


def service_names(cfg: Dict) -> Dict[str, str]:
    services = cfg.get('services') or {}
    if not isinstance(services, dict):
        services = {}
    # Defaults to your provided names
    mapping = {
        'fastapi': services.get('fastapi', 'ClinicalFastAPI'),
        'fastapi_alt': services.get('fastapi_alt', 'FastAPIServer'),
        'llama': services.get('llama', 'LlamaServer'),
        'ocr': services.get('ocr', 'OCRServer'),
        'mainserver': services.get('mainserver', 'mainserver'),
    }
    return mapping


def service_action(name: str, action: str) -> Tuple[bool, str]:
    if action == 'start':
        code, out = run_cmd(['nssm', 'start', name])
        if code != 0:
            code, out = run_cmd(['sc', 'start', name])
        return code == 0, out
    if action == 'stop':
        code, out = run_cmd(['nssm', 'stop', name])
        if code != 0:
            code, out = run_cmd(['sc', 'stop', name])
        return code == 0, out
    if action == 'restart':
        ok1, out1 = service_action(name, 'stop')
        if not ok1:
            return False, out1
        ok2, out2 = service_action(name, 'start')
        return ok2, out2
    return False, f'unsupported action: {action}'


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


async def handle_health(request: web.Request):
    return web.json_response({'status': 'ok'})


async def handle_services_status(request: web.Request):
    if not auth_ok(request):
        return web.json_response({'error': 'unauthorized'}, status=401)
    cfg = load_config()
    names = service_names(cfg)
    # Derive ports from env (single source of truth)
    fastapi_port = int(os.environ.get("FASTAPI_PORT") or 7860)
    llama_port = 8081
    try:
        llama_url = os.environ.get("NOTEGEN_URL_PRIMARY", "http://127.0.0.1:8081")
        host_port = llama_url.split("://", 1)[1].split("/", 1)[0] if "://" in llama_url else llama_url.split("/", 1)[0]
        if ":" in host_port:
            llama_port = int(host_port.split(":", 1)[1])
    except Exception:
        llama_port = 8081
    ocr_port = 8090
    try:
        ocr_url = os.environ.get("OCR_URL_PRIMARY", "http://127.0.0.1:8090")
        host_port = ocr_url.split("://", 1)[1].split("/", 1)[0] if "://" in ocr_url else ocr_url.split("/", 1)[0]
        if ":" in host_port:
            ocr_port = int(host_port.split(":", 1)[1])
    except Exception:
        ocr_port = 8090
    ports = {
        'fastapi': fastapi_port,
        'fastapi_alt': fastapi_port,
        'llama': llama_port,
        'ocr': ocr_port,
        'mainserver': 3443,
    }
    out: Dict[str, Dict] = {}
    for sid, name in names.items():
        # Status via sc query (simple)
        code, txt = run_cmd(['sc', 'query', name])
        lower = txt.lower()
        status = 'running' if 'running' in lower else ('stopped' if 'stopped' in lower else 'unknown')
        out[sid] = {
            'id': sid,
            'name': name,
            'display': {'fastapi': 'FastAPI Server', 'llama': 'LLaMA Server', 'ocr': 'OCR Server'}.get(sid, name),
            'status': status,
            'port': ports.get(sid),
            'reachable': port_open('127.0.0.1', ports.get(sid, 0)) if ports.get(sid) else None,
        }
    return web.json_response({'services': out})


async def handle_service_action(request: web.Request):
    if not auth_ok(request):
        return web.json_response({'error': 'unauthorized'}, status=401)
    service_id = request.match_info.get('service_id')
    action = request.match_info.get('action')
    cfg = load_config()
    name = service_names(cfg).get(service_id or '')
    if not name:
        return web.json_response({'ok': False, 'message': 'unknown service id'}, status=404)
    ok, msg = service_action(name, action or '')
    return web.json_response({'ok': ok, 'message': msg if not ok else f'{action} {service_id}'})


def make_app() -> web.Application:
    app = web.Application()
    async def handle_services_all(request: web.Request):
        if not auth_ok(request):
            return web.json_response({'error': 'unauthorized'}, status=401)
        action = request.match_info.get('action')
        if action not in ('start', 'stop', 'restart'):
            return web.json_response({'ok': False, 'message': 'unsupported action'}, status=400)
        cfg = load_config()
        names = service_names(cfg)
        results = {}
        for sid, name in names.items():
            ok, msg = service_action(name, action)
            results[sid] = {'ok': ok, 'message': msg if not ok else f'{action} {sid}'}
        return web.json_response({'results': results})

    app.add_routes([
        web.get('/health', handle_health),
        web.get('/services/status', handle_services_status),
        web.post('/services/{action}/{service_id}', handle_service_action),
        web.post('/services/{action}-all', handle_services_all),
    ])
    return app


if __name__ == '__main__':
    web.run_app(make_app(), host='0.0.0.0', port=7870)
