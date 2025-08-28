# server.py
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession

import gitlab

# -----------------------------
# Utilidades de configuración
# -----------------------------

def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}

GITLAB_API_URL = os.getenv("GITLAB_API_URL", "https://gitlab.com")
GITLAB_TOKEN = os.getenv("GITLAB_PERSONAL_ACCESS_TOKEN")
DEFAULT_PROJECT_ID = os.getenv("GITLAB_PROJECT_ID")
ALLOWED_IDS = {s.strip() for s in os.getenv("GITLAB_ALLOWED_PROJECT_IDS", "").split(",") if s.strip()}
READ_ONLY = _env_bool("GITLAB_READ_ONLY_MODE", "false")
USE_WIKI = _env_bool("USE_GITLAB_WIKI", "false")
USE_MILESTONE = _env_bool("USE_MILESTONE", "false")
USE_PIPELINE = _env_bool("USE_PIPELINE", "false")
AUTH_COOKIE_PATH = os.getenv("GITLAB_AUTH_COOKIE_PATH")

# Transporte (elige por env)
TRANSPORT = "stdio"
if _env_bool("STREAMABLE_HTTP"):
    TRANSPORT = "streamable-http"
elif _env_bool("SSE"):
    TRANSPORT = "sse"

# -----------------------------
# Cliente GitLab
# -----------------------------
if not GITLAB_TOKEN:
    raise RuntimeError("Falta GITLAB_PERSONAL_ACCESS_TOKEN en el entorno.")

gl = gitlab.Gitlab(GITLAB_API_URL, private_token=GITLAB_TOKEN, api_version=4)

# Cookie-based auth opcional
if AUTH_COOKIE_PATH and os.path.exists(AUTH_COOKIE_PATH):
    with open(AUTH_COOKIE_PATH, "r", encoding="utf-8") as fh:
        cookie_value = fh.read().strip()
        if cookie_value:
            gl.session.headers["Cookie"] = cookie_value

def _ensure_pid(project_id: Optional[Union[int, str]]) -> Union[int, str]:
    pid = project_id or DEFAULT_PROJECT_ID
    if not pid:
        raise ValueError("'project_id' es requerido (o define GITLAB_PROJECT_ID).")
    if ALLOWED_IDS and str(pid) not in ALLOWED_IDS:
        raise PermissionError(f"project_id {pid} no permitido por GITLAB_ALLOWED_PROJECT_IDS")
    return pid

def _assert_can_write():
    if READ_ONLY:
        raise PermissionError("Servidor en modo solo lectura (GITLAB_READ_ONLY_MODE=true)")

# -----------------------------
# Servidor MCP
# -----------------------------

mcp = FastMCP("GitLab MCP (Python)")

# Ejemplo mínimo de tool
@mcp.tool()
def search_repositories(query: str, membership: bool = False, starred: bool = False,
                        visibility: Optional[str] = None, simple: bool = True,
                        page: int = 1, per_page: int = 20) -> List[Dict[str, Any]]:
    "Buscar proyectos (repos) en GitLab."
    projects = gl.projects.list(search=query, membership=membership, starred=starred,
                                visibility=visibility, simple=simple, page=page, per_page=per_page)
    return [{"id": p.id, "name": p.name, "web_url": getattr(p, "web_url", None)} for p in projects]

# -----------------------------
# main
# -----------------------------
if __name__ == "__main__":
    mcp.run(transport=TRANSPORT)
