#!/usr/bin/env python3
"""Harness Dashboard — FastAPI backend for the Harness-Engineering platform."""

import os
import sys
import json
import subprocess
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import asyncio

ROOT = Path(__file__).resolve().parent.parent.parent
WORKTREE_DIR = ROOT / ".worktrees"
HARNESS_DIR = ROOT / "harness"
REPO_DIR = ROOT / "repo"

app = FastAPI(title="Harness Dashboard", version="1.0")

# ── Models ──

class AgentInfo(BaseModel):
    task: str
    project: str
    branch: str
    agent_type: str
    docker: bool
    status: str
    phase: str
    created_at: str
    worktree_path: str
    running: bool
    has_readme: bool

class AgentDetail(AgentInfo):
    context: str = ""
    task_yaml_raw: dict = {}

class DispatchRequest(BaseModel):
    project: str
    task_name: str
    agent: str = "claude"
    base_branch: str = "dev"
    docker: bool = False
    launch: bool = False

class DispatchResult(BaseModel):
    success: bool
    message: str
    task_name: str = ""
    worktree_path: str = ""
    branch: str = ""

class ProjectInfo(BaseModel):
    name: str
    path: str
    has_readme: bool

class PlatformStatus(BaseModel):
    total_agents: int
    running_agents: int
    worktree_dir: str
    base_branch: str
    projects_count: int


# ── Helpers ──

def get_worktrees() -> list[Path]:
    """List all worktree directories."""
    if not WORKTREE_DIR.exists():
        return []
    paths = sorted(WORKTREE_DIR.iterdir(), key=lambda p: p.name)
    return [p for p in paths if p.is_dir() and (p / ".harness" / "task.yaml").exists()]


def parse_task_yaml(worktree: Path) -> dict:
    """Parse .harness/task.yaml into a dict."""
    yaml_path = worktree / ".harness" / "task.yaml"
    if not yaml_path.exists():
        return {}
    data = {}
    try:
        with open(yaml_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip().strip('"')
                    if val.lower() == "true":
                        val = True
                    elif val.lower() == "false":
                        val = False
                    data[key] = val
    except Exception:
        pass
    return data


def parse_status_phase(worktree: Path) -> str:
    """Extract current phase from workflow STATUS.md."""
    status_md = worktree / "workflow" / "STATUS.md"
    if not status_md.exists():
        return "unknown"
    try:
        content = status_md.read_text()
        for line in content.split("\n"):
            line = line.strip()
            if "当前阶段:" in line:
                phase = line.split(":", 1)[1].strip().replace("**", "")
                return phase if phase else "N/A"
    except Exception:
        pass
    return "N/A"


def check_running(task_name: str, agent_type: str) -> bool:
    """Check if agent is running via tmux or docker."""
    import shutil
    # tmux check
    if shutil.which("tmux"):
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", f"harness-{task_name}"],
                capture_output=True, timeout=2
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
    # docker check
    if shutil.which("docker"):
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=2
            )
            if f"sw-{task_name}" in result.stdout:
                return True
        except Exception:
            pass
    return False


# ── Routes ──

@app.get("/")
async def serve_index():
    return FileResponse(HARNESS_DIR / "dashboard" / "static" / "index.html")


@app.get("/api/agents")
async def list_agents() -> list[AgentInfo]:
    agents = []
    for wt in get_worktrees():
        meta = parse_task_yaml(wt)
        task_name = meta.get("name", wt.name)
        agent_type = str(meta.get("agent", "unknown"))
        phase = parse_status_phase(wt)
        running = check_running(wt.name, agent_type)

        agents.append(AgentInfo(
            task=task_name,
            project=str(meta.get("project", "unknown")),
            branch=str(meta.get("branch", "")),
            agent_type=agent_type,
            docker=bool(meta.get("docker", False)),
            status=str(meta.get("status", "unknown")),
            phase=phase,
            created_at=str(meta.get("created_at", "")),
            worktree_path=str(wt),
            running=running,
            has_readme=(wt / "workflow" / "current-context.md").exists(),
        ))
    return agents


@app.get("/api/agents/{task_name}")
async def get_agent(task_name: str) -> AgentDetail:
    wt = WORKTREE_DIR / task_name
    if not wt.exists():
        raise HTTPException(status_code=404, detail="Agent not found")

    meta = parse_task_yaml(wt)
    agent_type = str(meta.get("agent", "unknown"))
    phase = parse_status_phase(wt)
    running = check_running(task_name, agent_type)

    context = ""
    ctx_file = wt / "workflow" / "current-context.md"
    if ctx_file.exists():
        try:
            context = ctx_file.read_text()
        except Exception:
            pass

    return AgentDetail(
        task=str(meta.get("name", task_name)),
        project=str(meta.get("project", "unknown")),
        branch=str(meta.get("branch", "")),
        agent_type=agent_type,
        docker=bool(meta.get("docker", False)),
        status=str(meta.get("status", "unknown")),
        phase=phase,
        created_at=str(meta.get("created_at", "")),
        worktree_path=str(wt),
        running=running,
        has_readme=bool(context),
        context=context,
        task_yaml_raw=meta,
    )


@app.get("/api/projects")
async def list_projects() -> list[ProjectInfo]:
    projects = [ProjectInfo(name="harness-flow", path=str(ROOT), has_readme=True)]
    if REPO_DIR.exists():
        for d in sorted(REPO_DIR.iterdir()):
            if d.is_dir():
                projects.append(ProjectInfo(
                    name=d.name,
                    path=str(d),
                    has_readme=(d / "README.md").exists(),
                ))
    return projects


@app.get("/api/status")
async def platform_status() -> PlatformStatus:
    agents = get_worktrees()
    running_count = sum(1 for wt in agents if check_running(wt.name,
        str(parse_task_yaml(wt).get("agent", ""))))
    projects_count = 0
    if REPO_DIR.exists():
        projects_count = sum(1 for d in REPO_DIR.iterdir() if d.is_dir())
    return PlatformStatus(
        total_agents=len(agents),
        running_agents=running_count,
        worktree_dir=str(WORKTREE_DIR),
        base_branch=os.environ.get("HARNESS_BASE_BRANCH", "dev"),
        projects_count=projects_count + 1,  # +1 for harness-flow itself
    )


@app.post("/api/dispatch")
async def dispatch_task(req: DispatchRequest) -> DispatchResult:
    if req.project == "harness-flow":
        pass
    elif not (REPO_DIR / req.project).exists():
        raise HTTPException(status_code=400, detail=f"项目 '{req.project}' 不存在")

    cmd = [
        str(HARNESS_DIR / "dispatch.sh"),
        req.project,
        req.task_name,
        "--agent", req.agent,
        "--base", req.base_branch,
    ]
    if req.docker:
        cmd.append("--docker")
    if req.launch:
        cmd.append("--launch")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            env={**os.environ, "CI": "true", "GIT_TERMINAL_PROMPT": "0"}
        )
        if result.returncode != 0:
            return DispatchResult(
                success=False,
                message=result.stderr.strip() or result.stdout.strip()[-200:]
            )

        sanitized = req.task_name.lower().replace(" ", "-")
        wt_path = str(WORKTREE_DIR / sanitized)
        branch = f"harness/{req.project}/{sanitized}"

        return DispatchResult(
            success=True,
            message="任务已派发",
            task_name=req.task_name,
            worktree_path=wt_path if (WORKTREE_DIR / sanitized).exists() else "",
            branch=branch,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="派发超时")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Log endpoints ──

@app.get("/api/agents/{task_name}/log")
async def get_agent_log(task_name: str, tail: int = Query(200, ge=1, le=5000)):
    wt = WORKTREE_DIR / task_name
    log_file = wt / ".harness" / "agent.log"
    if not log_file.exists():
        return {"lines": [], "total": 0, "message": "日志文件尚未生成"}
    lines = log_file.read_text().rstrip().split("\n")
    recent = lines[-tail:] if len(lines) > tail else lines
    return {"lines": recent, "total": len(lines), "tail": tail}


@app.get("/api/agents/{task_name}/log/stream")
async def stream_agent_log(task_name: str):
    wt = WORKTREE_DIR / task_name
    log_file = wt / ".harness" / "agent.log"

    async def tail_log():
        if not log_file.exists():
            yield "data: {\"event\": \"waiting\", \"message\": \"日志文件尚未生成\"}\n\n"
            for _ in range(30):
                await asyncio.sleep(1)
                if log_file.exists():
                    break
            else:
                yield "data: {\"event\": \"timeout\", \"message\": \"等待超时\"}\n\n"
                return

        with open(log_file) as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                else:
                    await asyncio.sleep(0.5)

    return StreamingResponse(tail_log(), media_type="text/event-stream")


# ── Credential status ──

@app.get("/api/credentials")
async def credential_status():
    cred_file = HARNESS_DIR / "credentials.yaml"
    agents_status = {}
    if cred_file.exists():
        import yaml
        try:
            cfg = yaml.safe_load(cred_file.read_text())
            for name, agent_cfg in cfg.get("agents", {}).items():
                method = agent_cfg.get("method", "unknown")
                configured = False
                if method == "env":
                    env_key = agent_cfg.get("env_key", "")
                    configured = bool(os.environ.get(env_key, ""))
                elif method == "mount":
                    host_path = os.path.expanduser(agent_cfg.get("host_path", ""))
                    configured = os.path.isdir(host_path)
                agents_status[name] = {"method": method, "configured": configured}
        except Exception:
            pass
    return {"agents": agents_status}


# ── Log Endpoints ──

@app.get("/api/agents/{task_name}/log")
async def get_agent_log(task_name: str, tail: int = 200):
    wt = WORKTREE_DIR / task_name
    log_file = wt / ".harness" / "agent.log"
    if not log_file.exists():
        return {"lines": [], "message": "No log file yet"}
    lines = log_file.read_text().splitlines()[-tail:]
    return {"lines": lines, "total": len(lines), "path": str(log_file)}


@app.get("/api/agents/{task_name}/log/stream")
async def stream_agent_log(task_name: str, request: Request):
    wt = WORKTREE_DIR / task_name
    log_file = wt / ".harness" / "agent.log"

    async def tail_log():
        await asyncio.sleep(0.1)
        yield "data: {\"event\": \"init\"}\n\n"
        last_size = log_file.stat().st_size if log_file.exists() else 0
        while True:
            if await request.is_disconnected():
                break
            try:
                if log_file.exists():
                    current_size = log_file.stat().st_size
                    if current_size > last_size:
                        with open(log_file) as f:
                            f.seek(last_size)
                            new_data = f.read()
                        for line in new_data.splitlines():
                            if line.strip():
                                yield f"data: {json.dumps({'line': line})}\n\n"
                        last_size = current_size
            except Exception:
                pass
            await asyncio.sleep(1)

    return StreamingResponse(tail_log(), media_type="text/event-stream")


# ── Credential Status ──

@app.get("/api/credentials")
async def credential_status():
    cred_file = HARNESS_DIR / "credentials.yaml"
    if not cred_file.exists():
        return {"configured": False}
    try:
        import yaml
        with open(cred_file) as f:
            cfg = yaml.safe_load(f)
        agents_cfg = cfg.get("agents", {})
        result = {}
        for agent_name, agent_cfg in agents_cfg.items():
            method = agent_cfg.get("method", "unknown")
            if method == "env":
                env_val = os.environ.get(agent_cfg.get("env_key", ""))
                result[agent_name] = {"configured": bool(env_val), "method": "env"}
            elif method == "mount":
                host_path = os.path.expanduser(agent_cfg.get("host_path", ""))
                result[agent_name] = {"configured": os.path.isdir(host_path) if host_path else False, "method": "mount"}
            else:
                result[agent_name] = {"configured": False, "method": method}
        return {"configured": True, "agents": result}
    except Exception:
        return {"configured": False}


# ── Static files ──
static_dir = HARNESS_DIR / "dashboard" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8090))
    print(f"Harness Dashboard → http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
