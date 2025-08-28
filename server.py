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

# ----------
# PROJECTS
# ----------

@mcp.tool()
def search_repositories(query: str, membership: bool = False, starred: bool = False,
                        visibility: Optional[str] = None, simple: bool = True,
                        page: int = 1, per_page: int = 20) -> List[Dict[str, Any]]:
    """Buscar proyectos (repos) en GitLab."""
    projects = gl.projects.list(search=query, membership=membership, starred=starred,
                                visibility=visibility, simple=simple, page=page, per_page=per_page)
    return [
        {
            "id": p.id,
            "name": p.name,
            "name_with_namespace": getattr(p, "name_with_namespace", p.name),
            "path_with_namespace": getattr(p, "path_with_namespace", None),
            "web_url": getattr(p, "web_url", None),
            "default_branch": getattr(p, "default_branch", None),
            "last_activity_at": getattr(p, "last_activity_at", None),
        }
        for p in projects
    ]

@mcp.tool()
def create_repository(name: str, namespace_id: Optional[int] = None,
                      visibility: str = "private", description: Optional[str] = None) -> Dict[str, Any]:
    """Crear un nuevo proyecto (repo)."""
    _assert_can_write()
    data: Dict[str, Any] = {"name": name, "visibility": visibility}
    if namespace_id:
        data["namespace_id"] = namespace_id
    if description:
        data["description"] = description
    proj = gl.projects.create(data)
    return {"id": proj.id, "name": proj.name, "web_url": proj.web_url}

# ----------
# FILES & COMMITS
# ----------

@mcp.tool()
def get_file_contents(ref: str, path: str, project_id: Optional[Union[int, str]] = None,
                      with_tree: bool = False) -> Dict[str, Any]:
    """Obtener contenido de archivo o (opcional) listado del árbol."""
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)

    # Si piden árbol
    tree: Optional[List[Dict[str, Any]]] = None
    if with_tree:
        tree_objs = project.repository_tree(path=path, ref=ref, recursive=False)
        tree = [{"type": t["type"], "path": t["path"]} for t in tree_objs]

    # Intentar leer archivo
    try:
        f = project.files.get(file_path=path, ref=ref)
        # Contenido viene en base64
        content = f.decode()
        return {"path": path, "ref": ref, "content": content, "tree": tree}
    except Exception:
        # Puede ser carpeta solamente
        if tree is not None:
            return {"path": path, "ref": ref, "content": None, "tree": tree}
        raise

@mcp.tool()
def create_or_update_file(branch: str, path: str, content: str, commit_message: str,
                          project_id: Optional[Union[int, str]] = None) -> Dict[str, Any]:
    """Crear o actualizar un archivo único en una rama."""
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)

    try:
        f = project.files.get(file_path=path, ref=branch)
        f.content = content
        f.save(branch=branch, commit_message=commit_message)
        action = "updated"
    except gitlab.exceptions.GitlabGetError:
        project.files.create({
            "file_path": path,
            "branch": branch,
            "content": content,
            "commit_message": commit_message,
        })
        action = "created"

    return {"project_id": pid, "branch": branch, "path": path, "action": action}

@mcp.tool()
def push_files(branch: str, files: List[Dict[str, str]], commit_message: str,
               project_id: Optional[Union[int, str]] = None) -> Dict[str, Any]:
    """Push de múltiples archivos en un solo commit.

    `files`: lista de acciones estilo GitLab API, ejemplo:
      {"action": "create|update|delete|move", "file_path": "a.txt", "content": "..."}
    """
    
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)

    commit = project.commits.create({
        "branch": branch,
        "commit_message": commit_message,
        "actions": files,
    })
    return {"id": commit.id, "short_id": commit.short_id, "title": commit.title}

@mcp.tool()
def fork_repository(project_id: Optional[Union[int, str]] = None,
                    namespace: Optional[str] = None) -> Dict[str, Any]:
    """Fork del repositorio hacia tu espacio o el `namespace` indicado."""
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    fork = project.forks.create({"namespace_path": namespace} if namespace else {})
    return {"id": fork.id, "path_with_namespace": fork.path_with_namespace, "web_url": fork.web_url}

@mcp.tool()
def create_branch(branch: str, ref: str, project_id: Optional[Union[int, str]] = None) -> Dict[str, Any]:
    """Crear una nueva rama (desde `ref`)."""
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    b = project.branches.create({"branch": branch, "ref": ref})
    return {"name": b.name, "commit": getattr(b, "commit", None)}

# ----------
# ISSUES
# ----------

@mcp.tool()
def create_issue(title: str, description: Optional[str] = None, labels: Optional[str] = None,
                 assignee_ids: Optional[List[int]] = None, milestone_id: Optional[int] = None,
                 confidential: bool = False, due_date: Optional[str] = None,
                 project_id: Optional[Union[int, str]] = None) -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    data: Dict[str, Any] = {"title": title, "confidential": confidential}
    if description:
        data["description"] = description
    if labels:
        data["labels"] = labels
    if assignee_ids:
        data["assignee_ids"] = assignee_ids
    if milestone_id:
        data["milestone_id"] = milestone_id
    if due_date:
        data["due_date"] = due_date
    issue = project.issues.create(data)
    return {"iid": issue.iid, "web_url": issue.web_url}

@mcp.tool()
def list_issues(project_id: Optional[Union[int, str]] = None, scope: str = "created_by_me",
               state: Optional[str] = None, search: Optional[str] = None,
               labels: Optional[str] = None, page: int = 1, per_page: int = 20) -> List[Dict[str, Any]]:
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    issues = project.issues.list(scope=scope, state=state, search=search, labels=labels,
                                 page=page, per_page=per_page)
    return [{"iid": i.iid, "title": i.title, "state": i.state, "web_url": i.web_url} for i in issues]

# ----------
# MERGE REQUESTS & DIFFS
# ----------

@mcp.tool()
def create_merge_request(source_branch: str, target_branch: str, title: str,
                         description: Optional[str] = None, draft: bool = False,
                         remove_source_branch: bool = False, assignee_ids: Optional[List[int]] = None,
                         reviewer_ids: Optional[List[int]] = None,
                         project_id: Optional[Union[int, str]] = None) -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    data: Dict[str, Any] = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": ("Draft: " + title) if draft and not title.lower().startswith("draft:") else title,
        "remove_source_branch": remove_source_branch,
    }
    if description:
        data["description"] = description
    if assignee_ids:
        data["assignee_ids"] = assignee_ids
    if reviewer_ids:
        data["reviewer_ids"] = reviewer_ids

    mr = project.mergerequests.create(data)
    return {"iid": mr.iid, "web_url": mr.web_url, "state": mr.state}

def _resolve_mr(project, merge_request_iid: Optional[int], branch_name: Optional[str]):
    if merge_request_iid:
        return project.mergerequests.get(merge_request_iid)
    if branch_name:
        mrs = project.mergerequests.list(source_branch=branch_name, state="opened")
        if not mrs:
            raise ValueError(f"No se encontró MR abierto con source_branch={branch_name}")
        return mrs[0]
    raise ValueError("Provee merge_request_iid o branch_name")

@mcp.tool()
def get_merge_request(project_id: Optional[Union[int, str]] = None,
                      merge_request_iid: Optional[int] = None,
                      branch_name: Optional[str] = None) -> Dict[str, Any]:
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = _resolve_mr(project, merge_request_iid, branch_name)
    return {
        "iid": mr.iid,
        "title": mr.title,
        "state": mr.state,
        "source_branch": mr.source_branch,
        "target_branch": mr.target_branch,
        "web_url": mr.web_url,
    }

@mcp.tool()
def update_merge_request(project_id: Optional[Union[int, str]] = None,
                         merge_request_iid: Optional[int] = None,
                         branch_name: Optional[str] = None,
                         title: Optional[str] = None,
                         description: Optional[str] = None,
                         labels: Optional[str] = None,
                         state_event: Optional[str] = None) -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = _resolve_mr(project, merge_request_iid, branch_name)

    if title is not None:
        mr.title = title
    if description is not None:
        mr.description = description
    if labels is not None:
        mr.labels = labels
    if state_event is not None:
        mr.state_event = state_event  # "close" | "reopen"

    mr.save()
    return {"iid": mr.iid, "title": mr.title, "state": mr.state}

@mcp.tool()
def merge_merge_request(project_id: Optional[Union[int, str]] = None,
                        merge_request_iid: Optional[int] = None,
                        branch_name: Optional[str] = None,
                        merge_when_pipeline_succeeds: bool = False,
                        squash: bool = False,
                        sha: Optional[str] = None) -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = _resolve_mr(project, merge_request_iid, branch_name)

    mr.merge(when_pipeline_succeeds=merge_when_pipeline_succeeds, squash=squash, sha=sha)
    mr = project.mergerequests.get(mr.iid)
    return {"iid": mr.iid, "state": mr.state, "merged_at": getattr(mr, "merged_at", None)}

@mcp.tool()
def get_merge_request_diffs(project_id: Optional[Union[int, str]] = None,
                            merge_request_iid: Optional[int] = None,
                            branch_name: Optional[str] = None,
                            page: int = 1, per_page: int = 20) -> List[Dict[str, Any]]:
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = _resolve_mr(project, merge_request_iid, branch_name)
    diffs = mr.changes()['changes']  # lista de archivos con diffs
    # paginar manualmente
    start = (page - 1) * per_page
    end = start + per_page
    return diffs[start:end]

@mcp.tool()
def get_branch_diffs(project_id: Optional[Union[int, str]] = None,
                     from_ref: str = "main", to_ref: str = "HEAD") -> Dict[str, Any]:
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    comp = project.repository_compare(from_ref, to_ref)
    return comp

# ----------
# NOTES & DISCUSSIONS (Issues/MR)
# ----------

@mcp.tool()
def create_note(project_id: Optional[Union[int, str]] = None, iid: int = 0,
                on: str = "merge_request", body: str = "") -> Dict[str, Any]:
    """Crear comentario en issue o MR. `on`: 'merge_request'|'issue'"""
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    if on == "issue":
        tgt = project.issues.get(iid)
    else:
        tgt = project.mergerequests.get(iid)
    note = tgt.notes.create({"body": body})
    return {"id": note.id, "body": note.body}

@mcp.tool()
def mr_discussions(project_id: Optional[Union[int, str]] = None, merge_request_iid: int = 0) -> List[Dict[str, Any]]:
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    discs = mr.discussions.list(get_all=True)
    out: List[Dict[str, Any]] = []
    for d in discs:
        out.append({
            "id": d.id,
            "notes": [
                {"id": n.id, "author": getattr(n, "author", {}), "body": getattr(n, "body", ""), "system": getattr(n, "system", False)}
                for n in d.attributes.get("notes", [])
            ],
        })
    return out

@mcp.tool()
def create_merge_request_note(project_id: Optional[Union[int, str]] = None,
                              merge_request_iid: int = 0, body: str = "") -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    note = mr.notes.create({"body": body})
    return {"id": note.id, "body": note.body}

@mcp.tool()
def update_merge_request_note(project_id: Optional[Union[int, str]] = None,
                              merge_request_iid: int = 0, note_id: int = 0, body: str = "") -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    note = mr.notes.get(note_id)
    note.body = body
    note.save()
    return {"id": note.id, "body": note.body}

# Draft Notes

@mcp.tool()
def list_draft_notes(project_id: Optional[Union[int, str]] = None, merge_request_iid: int = 0) -> List[Dict[str, Any]]:
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    drafts = mr.draft_notes.list(get_all=True)
    return [{"id": d.id, "note": d.note, "resolved": getattr(d, "resolved", False)} for d in drafts]

@mcp.tool()
def get_draft_note(project_id: Optional[Union[int, str]] = None, merge_request_iid: int = 0, draft_id: int = 0) -> Dict[str, Any]:
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    d = mr.draft_notes.get(draft_id)
    return {"id": d.id, "note": d.note}

@mcp.tool()
def create_draft_note(project_id: Optional[Union[int, str]] = None, merge_request_iid: int = 0, note: str = "") -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    d = mr.draft_notes.create({"note": note})
    return {"id": d.id, "note": d.note}

@mcp.tool()
def update_draft_note(project_id: Optional[Union[int, str]] = None, merge_request_iid: int = 0, draft_id: int = 0, note: str = "") -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    d = mr.draft_notes.get(draft_id)
    d.note = note
    d.save()
    return {"id": d.id, "note": d.note}

@mcp.tool()
def delete_draft_note(project_id: Optional[Union[int, str]] = None, merge_request_iid: int = 0, draft_id: int = 0) -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    d = mr.draft_notes.get(draft_id)
    d.delete()
    return {"deleted": True}

@mcp.tool()
def publish_draft_note(project_id: Optional[Union[int, str]] = None, merge_request_iid: int = 0, draft_id: int = 0) -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    d = mr.draft_notes.get(draft_id)
    d.publish()
    return {"published": True}

@mcp.tool()
def bulk_publish_draft_notes(project_id: Optional[Union[int, str]] = None, merge_request_iid: int = 0) -> Dict[str, Any]:
    _assert_can_write()
    pid = _ensure_pid(project_id)
    project = gl.projects.get(pid)
    mr = project.mergerequests.get(merge_request_iid)
    mr.draft_notes.publish_all()
    return {"published_all": True}

# ----------
# PIPELINES (opcionales)
# ----------

if USE_PIPELINE:

    @mcp.tool()
    def list_pipelines(project_id: Optional[Union[int, str]] = None, ref: Optional[str] = None,
                       status: Optional[str] = None, page: int = 1, per_page: int = 20) -> List[Dict[str, Any]]:
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        pls = project.pipelines.list(ref=ref, status=status, page=page, per_page=per_page)
        return [{"id": p.id, "status": p.status, "sha": p.sha, "ref": p.ref, "web_url": p.web_url} for p in pls]

    @mcp.tool()
    def get_pipeline(project_id: Optional[Union[int, str]] = None, pipeline_id: int = 0) -> Dict[str, Any]:
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        p = project.pipelines.get(pipeline_id)
        return p.attributes

    @mcp.tool()
    def list_pipeline_jobs(project_id: Optional[Union[int, str]] = None, pipeline_id: int = 0) -> List[Dict[str, Any]]:
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        p = project.pipelines.get(pipeline_id)
        jobs = p.jobs.list(get_all=True)
        return [{"id": j.id, "name": j.name, "status": j.status, "stage": j.stage} for j in jobs]

    @mcp.tool()
    def get_pipeline_job(project_id: Optional[Union[int, str]] = None, job_id: int = 0) -> Dict[str, Any]:
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        job = project.jobs.get(job_id)
        return job.attributes

    @mcp.tool()
    def get_pipeline_job_output(project_id: Optional[Union[int, str]] = None, job_id: int = 0) -> Dict[str, Any]:
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        job = project.jobs.get(job_id)
        # Log de job (trace)
        trace = job.trace()
        return {"id": job.id, "trace": trace}

    @mcp.tool()
    def create_pipeline(project_id: Optional[Union[int, str]] = None, ref: str = "main",
                        variables: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        _assert_can_write()
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        payload: Dict[str, Any] = {"ref": ref}
        if variables:
            payload["variables"] = [{"key": k, "value": v} for k, v in variables.items()]
        p = project.pipelines.create(payload)
        return {"id": p.id, "status": p.status, "web_url": p.web_url}

    @mcp.tool()
    def retry_pipeline(project_id: Optional[Union[int, str]] = None, pipeline_id: int = 0) -> Dict[str, Any]:
        _assert_can_write()
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        p = project.pipelines.get(pipeline_id)
        p.retry()
        return {"id": p.id, "status": p.status}

    @mcp.tool()
    def cancel_pipeline(project_id: Optional[Union[int, str]] = None, pipeline_id: int = 0) -> Dict[str, Any]:
        _assert_can_write()
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        p = project.pipelines.get(pipeline_id)
        p.cancel()
        return {"id": p.id, "status": p.status}

# ----------
# WIKI (opcional)
# ----------

if USE_WIKI:

    @mcp.tool()
    def list_wiki_pages(project_id: Optional[Union[int, str]] = None, with_content: bool = False) -> List[Dict[str, Any]]:
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        pages = project.wikis.list(get_all=True)
        out = []
        for p in pages:
            d = {"slug": p.slug, "title": p.title}
            if with_content:
                page = project.wikis.get(p.slug)
                d["content"] = page.content
            out.append(d)
        return out

    @mcp.tool()
    def get_wiki_page(project_id: Optional[Union[int, str]] = None, slug: str = "") -> Dict[str, Any]:
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        page = project.wikis.get(slug)
        return {"slug": page.slug, "title": page.title, "content": page.content}

    @mcp.tool()
    def create_wiki_page(project_id: Optional[Union[int, str]] = None, title: str = "",
                         content: str = "", format: str = "markdown") -> Dict[str, Any]:
        _assert_can_write()
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        page = project.wikis.create({"title": title, "content": content, "format": format})
        return {"slug": page.slug, "title": page.title}

    @mcp.tool()
    def update_wiki_page(project_id: Optional[Union[int, str]] = None, slug: str = "",
                         content: Optional[str] = None, title: Optional[str] = None,
                         format: Optional[str] = None) -> Dict[str, Any]:
        _assert_can_write()
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        page = project.wikis.get(slug)
        if content is not None:
            page.content = content
        if title is not None:
            page.title = title
        if format is not None:
            page.format = format
        page.save()
        return {"slug": page.slug, "title": page.title}

    @mcp.tool()
    def delete_wiki_page(project_id: Optional[Union[int, str]] = None, slug: str = "") -> Dict[str, Any]:
        _assert_can_write()
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        page = project.wikis.get(slug)
        page.delete()
        return {"deleted": True}

# ----------
# MILESTONES (opcional)
# ----------

if USE_MILESTONE:

    @mcp.tool()
    def list_milestones(project_id: Optional[Union[int, str]] = None, state: Optional[str] = None) -> List[Dict[str, Any]]:
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        mss = project.milestones.list(state=state, get_all=True)
        return [{"id": m.id, "title": m.title, "state": m.state, "iid": getattr(m, "iid", None)} for m in mss]

    @mcp.tool()
    def get_milestone(project_id: Optional[Union[int, str]] = None, milestone_id: int = 0) -> Dict[str, Any]:
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        m = project.milestones.get(milestone_id)
        return m.attributes

    @mcp.tool()
    def create_milestone(project_id: Optional[Union[int, str]] = None, title: str = "",
                         description: Optional[str] = None, due_date: Optional[str] = None,
                         start_date: Optional[str] = None) -> Dict[str, Any]:
        _assert_can_write()
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        payload: Dict[str, Any] = {"title": title}
        if description:
            payload["description"] = description
        if due_date:
            payload["due_date"] = due_date
        if start_date:
            payload["start_date"] = start_date
        m = project.milestones.create(payload)
        return {"id": m.id, "title": m.title}

    @mcp.tool()
    def edit_milestone(project_id: Optional[Union[int, str]] = None, milestone_id: int = 0,
                       title: Optional[str] = None, description: Optional[str] = None,
                       due_date: Optional[str] = None, start_date: Optional[str] = None,
                       state_event: Optional[str] = None) -> Dict[str, Any]:
        _assert_can_write()
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        m = project.milestones.get(milestone_id)
        if title is not None:
            m.title = title
        if description is not None:
            m.description = description
        if due_date is not None:
            m.due_date = due_date
        if start_date is not None:
            m.start_date = start_date
        if state_event is not None:
            m.state_event = state_event  # "close" | "activate"
        m.save()
        return {"id": m.id, "title": m.title, "state": m.state}

    @mcp.tool()
    def delete_milestone(project_id: Optional[Union[int, str]] = None, milestone_id: int = 0) -> Dict[str, Any]:
        _assert_can_write()
        pid = _ensure_pid(project_id)
        project = gl.projects.get(pid)
        m = project.milestones.get(milestone_id)
        m.delete()
        return {"deleted": True}

# -----------------------------
# main
# -----------------------------
if __name__ == "__main__":
    mcp.run(transport=TRANSPORT)
