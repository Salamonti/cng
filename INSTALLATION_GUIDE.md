# CNG (Clinical Note Generator) - Installation Guide

**Version**: 2026-03-10  
**Status**: Production-ready  
**Repository**: Clean, self-contained structure

---

## Table of Contents
1. [System Requirements](#system-requirements)
2. [Quick Start (Windows)](#quick-start-windows)
3. [Detailed Installation](#detailed-installation)
4. [Configuration](#configuration)
5. [Service Management](#service-management)
6. [Health Checks](#health-checks)
7. [Troubleshooting](#troubleshooting)
8. [Backup & Recovery](#backup--recovery)

---

## Architecture Overview

CNG uses a multi-service architecture with a reverse proxy:

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────────┐
│    User Browser │────▶│   PCHost     │────▶│   FastAPI Backend│
│                 │     │ (Reverse     │     │   (Port 7860)    │
│                 │     │   Proxy)     │     │                  │
│                 │     │ Port 3000/3443│     └──────────────────┘
└─────────────────┘     └──────────────┘              │
                                                      ▼
                                       ┌──────────────────────────┐
                                       │   External Services      │
                                       │   • OCR (8082/8090)      │
                                       │   • ASR (8095/9000)      │
                                       │   • RAG (8007)           │
                                       │   • LLM (8081)           │
                                       └──────────────────────────┘
```

**Key Components:**
1. **PCHost**: Reverse proxy + static file server (your entry point)
2. **FastAPI Backend**: Main application logic (Clinical-Note-Generator)
3. **External Services**: OCR, ASR, RAG, LLM (can be local or remote)

**Note**: PCHost is NOT optional - it's the required reverse proxy that handles HTTPS, CORS, and routing.

---

## System Requirements

### Minimum Hardware
- **CPU**: x64 processor with AVX2 support
- **RAM**: 16 GB minimum, 32 GB recommended
- **Storage**: 50 GB free space
- **GPU**: NVIDIA GPU with 8+ GB VRAM (RTX 3060+ recommended)
- **OS**: Windows 10/11, Ubuntu 20.04+, or macOS 12+

### Software Dependencies
- **Python**: 3.11+ (3.14 recommended)
- **Node.js**: 18+ (for PCHost proxy)
- **Git**: For cloning repository
- **CUDA**: 12.1+ (for GPU acceleration on Windows/Linux)
- **llama.cpp**: Local LLM server (port 8081)

**Note on macOS**: This application is primarily developed and tested on Windows. macOS support is limited to basic functionality without GPU acceleration. For production use on macOS, consider using external OCR/ASR services.

### External Services (Optional)
- **OCR Service**: Multimodal LLM endpoint (e.g., `http://localhost:8082`)
- **ASR Service**: WhisperX endpoint (e.g., `http://localhost:8095` or `http://localhost:9000`)
- **RAG Service**: Vector database endpoint

---

## Quick Start (Windows)

### 1. Clone Repository
```powershell
git clone https://github.com/your-org/cng.git
cd cng
```

### 2. Install Python Dependencies
```powershell
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install backend dependencies
pip install -r Clinical-Note-Generator\requirements.txt

# Install RAG dependencies (if using local RAG)
pip install -r RAG\requirements.txt
```

### 3. Install Node.js Dependencies
```powershell
cd PCHost
npm install
cd ..
```

### 4. Configure Environment
```powershell
# Copy example config
copy Clinical-Note-Generator\config\config.json.example Clinical-Note-Generator\config\config.json

# Set JWT secret (generate a secure key)
$env:JWT_SECRET="your-secure-jwt-secret-here"
```

### 5. Start Services

**Method A: Using batch files**
```powershell
# Start FastAPI backend with external services (recommended)
cd Clinical-Note-Generator
start_fastapi_server_external.bat

# Start PCHost proxy (in new terminal)
cd PCHost
node server.js

# Start RAG service (if using local, in new terminal)
cd RAG
start_rag_service.bat
```

**Method B: Direct commands (manual control - your current workflow)**
```powershell
# Terminal 1: FastAPI backend (with external services)
cd Clinical-Note-Generator
.venv\Scripts\activate
set NOTEGEN_URL_PRIMARY=http://127.0.0.1:8081
set OCR_URL_PRIMARY=http://127.0.0.1:8090
set RAG_URL=http://127.0.0.1:8007
set ASR_URL=http://127.0.0.1:8095
set ASR_API_KEY=notegenadmin
python -m uvicorn server.app:app --host 0.0.0.0 --port 7860

# Terminal 2: PCHost proxy  
cd PCHost
node server.js

# Terminal 3: RAG service (if using local)
cd RAG
.venv\Scripts\activate
python -m uvicorn query_api:app --host 0.0.0.0 --port 8007
```

### 6. Access Application
- **Main UI**: http://localhost:3000
- **API Docs**: http://localhost:7860/docs
- **Health Check**: http://localhost:7860/api/health

---

## Detailed Installation

### Backend (Clinical-Note-Generator)

#### Python Environment Setup
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# or .venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Install development dependencies (optional)
pip install pytest pytest-asyncio httpx
```

#### Database Initialization
```bash
# The SQLite database will be created automatically on first run
# at Clinical-Note-Generator/data/user_data.sqlite

# Create admin user (optional)
python scripts/create_admin.py
```

#### Configuration
Edit `config/config.json`:
```json
{
  "chat_model_name": "ministral14",
  "chat_model_path": "C:/models/ministral-3-14b.Q5_K_M.gguf",
  "note_gen_url_primary": "http://127.0.0.1:8081",
  "ocr_url_primary": "http://127.0.0.1:8082",
  "asr_url_primary": "http://127.0.0.1:9000",
  "rag_url": "http://127.0.0.1:8000"
}
```

### Frontend Proxy (PCHost)

#### Node.js Setup (Reverse Proxy)
```bash
cd PCHost
npm install  # Install dependencies

# Configure reverse proxy
# Edit config/server_config.json for your environment
```

#### Reverse Proxy Configuration
PCHost serves as the reverse proxy with these key functions:
- **Static file serving**: Serves web interface from `web/` directory
- **API proxying**: Routes `/api/*` to FastAPI backend (port 7860)
- **HTTPS termination**: Handles SSL certificates (port 3443)
- **CORS management**: Handles cross-origin requests
- **Request logging**: Logs all incoming requests

Key settings in `server.js`:
- HTTP Port: 3000
- HTTPS Port: 3443  
- Backend proxy: http://127.0.0.1:7860 (FastAPI)
- OpenWebUI proxy: :8443 (optional, if using OpenWebUI)

### RAG Service (Optional)

#### Local RAG Setup
```bash
cd RAG
pip install -r requirements.txt

# Configure sources
# Edit sources_config.yaml for your document sources

# Start service (choose one method)
python query_api.py  # Simple start (defaults to port 8007)
# OR with uvicorn for production:
python -m uvicorn query_api:app --host 0.0.0.0 --port 8007
# OR use start_rag_service.bat (Windows - starts on port 8007)
```

#### External RAG
Update `config/config.json` to point to your RAG endpoint:
```json
{
  "rag_url": "https://your-rag-service.com"
}
```

---

## Configuration

### Environment Variables
Set these in your environment or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `FASTAPI_PORT` | 7860 | FastAPI server port |
| `DATABASE_URL` | `sqlite:///data/user_data.sqlite` | Database connection |
| `JWT_SECRET` | (required) | JWT signing secret |
| `ENV` | `development` | Environment label |
| `LOG_LEVEL` | `INFO` | Logging level |

### Port Configuration

| Service | Port | Protocol | Description |
|---------|------|----------|-------------|
| PCHost | 3000 | HTTP | Main web interface |
| PCHost | 3443 | HTTPS | Secure web interface |
| FastAPI | 7860 | HTTP | Backend API |
| llama.cpp | 8081 | HTTP | LLM inference |
| OCR Service | 8082 | HTTP | Document processing (primary) |
| OCR Service | 8090 | HTTP | Document processing (fallback/alternative) |
| ASR Service | 8095 | HTTP | Speech recognition (WhisperX) |
| ASR Service | 9000 | HTTP | Speech recognition (alternative) |
| RAG Service | 8007 | HTTP | Retrieval service |

### Security Configuration

#### JWT Setup
```bash
# Generate a secure JWT secret
openssl rand -hex 32
# or in PowerShell:
[System.Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

#### CORS Configuration
Edit `Clinical-Note-Generator/server/app.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Service Management

### Windows (NSSM Recommended)

#### Install as Services
```powershell
# Install NSSM (if not installed)
choco install nssm

# Create FastAPI service
nssm install CNG-FastAPI "C:\path\to\python.exe" "C:\path\to\cng\Clinical-Note-Generator\server\app.py"
nssm set CNG-FastAPI AppDirectory "C:\path\to\cng\Clinical-Note-Generator"
nssm set CNG-FastAPI AppEnvironmentExtra "JWT_SECRET=your-secret"

# Create PCHost service
nssm install CNG-PCHost "C:\Program Files\nodejs\node.exe" "C:\path\to\cng\PCHost\server.js"
nssm set CNG-PCHost AppDirectory "C:\path\to\cng\PCHost"

# Start services
nssm start CNG-FastAPI
nssm start CNG-PCHost
```

#### Batch Files (Optional Templates)
Pre-configured batch files are provided as templates:
- `Clinical-Note-Generator/start_fastapi_server.bat` - Starts FastAPI with basic settings
- `Clinical-Note-Generator/start_fastapi_server_external.bat` - With external service env vars
- `PCHost/New_Main_Server.bat` - Starts PCHost with port checking
- `RAG/start_rag_service.bat` - Starts RAG service

**Note**: These are templates. Experienced users typically run services directly:
```powershell
# FastAPI: python -m uvicorn server.app:app --host 0.0.0.0 --port 7860
# PCHost: node server.js
# RAG: python -m uvicorn query_api:app --host 0.0.0.0 --port 8000
```

### Linux/macOS (systemd)

#### Create Service Files
```bash
# /etc/systemd/system/cng-fastapi.service
[Unit]
Description=CNG FastAPI Backend
After=network.target

[Service]
Type=simple
User=cng-user
WorkingDirectory=/opt/cng/Clinical-Note-Generator
Environment="JWT_SECRET=your-secret"
ExecStart=/opt/cng/.venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 7860
Restart=always

[Install]
WantedBy=multi-user.target

# /etc/systemd/system/cng-pchost.service
[Unit]
Description=CNG PCHost Proxy
After=network.target cng-fastapi.service

[Service]
Type=simple
User=cng-user
WorkingDirectory=/opt/cng/PCHost
ExecStart=/usr/bin/node server.js
Restart=always

[Install]
WantedBy=multi-user.target
```

#### Enable Services
```bash
sudo systemctl daemon-reload
sudo systemctl enable cng-fastapi cng-pchost
sudo systemctl start cng-fastapi cng-pchost
```

---

## Health Checks

### API Endpoints

| Endpoint | Method | Expected Response |
|----------|--------|-------------------|
| `/api/health` | GET | `{"status":"healthy"}` |
| `/api/version` | GET | Version info + commit hash |
| `/api/asr_engine` | GET | ASR service status |
| `/api/generate_v8` | POST | Note generation test |

### Manual Testing
```bash
# Test backend
curl http://localhost:7860/api/health

# Test proxy
curl http://localhost:3000

# Test authentication
curl -X POST http://localhost:7860/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}'
```

### Monitoring Script
Create `check_services.sh`:
```bash
#!/bin/bash
services=(
  "http://localhost:7860/api/health"
  "http://localhost:3000"
  "http://localhost:8081/health"  # llama.cpp
)

for service in "${services[@]}"; do
  if curl -s --max-time 5 "$service" > /dev/null; then
    echo "✓ $service"
  else
    echo "✗ $service"
  fi
done
```

---

## Troubleshooting

### Common Issues

#### 1. Port Conflicts
```bash
# Check what's using port 7860
netstat -ano | findstr :7860  # Windows
lsof -i :7860  # Linux/macOS
```

#### 2. Python Import Errors
```bash
# Ensure virtual environment is activated
source .venv/bin/activate  # or .venv\Scripts\activate

# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

#### 3. Database Issues
```bash
# Reset database (WARNING: deletes all data)
rm Clinical-Note-Generator/data/user_data.sqlite
# Database recreates on next startup
```

#### 4. macOS-Specific Issues
```bash
# If you encounter permission errors on macOS:
xcode-select --install  # Install command line tools
brew install python@3.14  # Install Python via Homebrew

# For GPU acceleration issues on macOS (Metal):
# Note: This application uses CUDA for GPU acceleration which is not available on macOS.
# Use external OCR/ASR services or run on CPU-only mode.
```

#### 4. JWT Errors
```powershell
# Regenerate JWT secret
$env:JWT_SECRET=[System.Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

#### 5. GPU Memory Issues
Edit `config/config.json`:
```json
{
  "max_tokens": 4096,  # Reduce from 8192
  "gpu_layers": 20,    # Reduce GPU layers
  "batch_size": 512    # Reduce batch size
}
```

### Logs Location
- **FastAPI logs**: `Clinical-Note-Generator/server/logs/`
- **PCHost logs**: Check Node.js console output
- **System logs**: Windows Event Viewer or journalctl

### Debug Mode
```bash
# Start with debug logging
set LOG_LEVEL=DEBUG  # Windows
export LOG_LEVEL=DEBUG  # Linux/macOS

# Or edit config.json
{
  "log_level": "DEBUG"
}
```

---

## Backup & Recovery

### Critical Data
1. **Database**: `Clinical-Note-Generator/data/user_data.sqlite`
2. **Configuration**: `Clinical-Note-Generator/config/config.json`
3. **User uploads**: `Clinical-Note-Generator/data/queue_files/`
4. **Training data**: `Clinical-Note-Generator/data/datasets/`

### Backup Script
```bash
#!/bin/bash
BACKUP_DIR="/backups/cng-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Backup database
cp Clinical-Note-Generator/data/user_data.sqlite "$BACKUP_DIR/"

# Backup config
cp -r Clinical-Note-Generator/config "$BACKUP_DIR/"

# Backup datasets
cp -r Clinical-Note-Generator/data/datasets "$BACKUP_DIR/"

# Create archive
tar -czf "$BACKUP_DIR.tar.gz" "$BACKUP_DIR"
echo "Backup created: $BACKUP_DIR.tar.gz"
```

### Recovery
```bash
# Restore from backup
tar -xzf backup.tar.gz
cp backup/data/user_data.sqlite Clinical-Note-Generator/data/
cp -r backup/config/* Clinical-Note-Generator/config/
```

### Automated Backups (cron)
```bash
# Add to crontab (runs daily at 2 AM)
0 2 * * * /opt/cng/backup.sh
```

---

## Updating

### Update Process
```bash
# 1. Backup current installation
./backup.sh

# 2. Stop services
systemctl stop cng-fastapi cng-pchost  # or stop batch files

# 3. Update code
git pull origin main

# 4. Update dependencies
pip install -r Clinical-Note-Generator/requirements.txt --upgrade
cd PCHost && npm update

# 5. Restart services
systemctl start cng-fastapi cng-pchost
```

### Version Compatibility
Check `Clinical-Note-Generator/docs/CHANGELOG.md` for breaking changes.

---

## Support

### Documentation
- `Clinical-Note-Generator/docs/CNG_PROJECT_HANDOFF.md` - System architecture
- `Clinical-Note-Generator/docs/EXTERNAL_SERVERS_SETUP.md` - External services
- `Clinical-Note-Generator/docs/regression_checklist.md` - Testing

### Getting Help
1. Check logs in `Clinical-Note-Generator/server/logs/`
2. Test individual endpoints with curl/Postman
3. Review configuration files
4. Check service status with health endpoints

### Emergency Restart
```bash
# Complete restart sequence
pkill -f "uvicorn.*7860"  # Kill FastAPI
pkill -f "node.*server.js"  # Kill PCHost
# Then restart using service management above
```

---

## Appendix

### A. Development Setup
```bash
# Install development tools
pip install black flake8 mypy pytest

# Run tests
pytest Clinical-Note-Generator/server/tests/

# Code formatting
black Clinical-Note-Generator/server/
```

### B. Production Checklist
- [ ] JWT secret set and secure
- [ ] CORS restricted to trusted origins
- [ ] HTTPS enabled (PCHost port 3443)
- [ ] Regular backups configured
- [ ] Monitoring/alerting setup
- [ ] Log rotation configured
- [ ] Firewall rules set
- [ ] Services running as non-root user

### C. Performance Tuning
- Adjust `config.json` parameters for your hardware
- Monitor GPU memory usage with `nvidia-smi`
- Consider using `start_fastapi_server_external.bat` for external services
- Enable response compression in PCHost config

---

*Last updated: 2026-03-10*  
*For issues, check the GitHub repository or contact support.*