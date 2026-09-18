import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any, Dict

import yaml
from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["Scanner"])

is_scanning: bool = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "projects.yaml"

ENVIRONMENTS = ["dev", "qa", "perf", "prod"]


def load_projects_config() -> tuple[Path, list[dict]]:
    if not CONFIG_PATH.exists():
        return PROJECT_ROOT, []

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    repos_dir_str = data.get("repos_dir", "")
    repos_base_dir = (PROJECT_ROOT / repos_dir_str).resolve()
    projects_list = data.get("projects", [])

    return repos_base_dir, projects_list


def ensure_repo_cloned(repo_url: str, repo_dir: Path) -> tuple[bool, str]:
    # 1. If repo already exists, pull the latest changes from main
    if repo_dir.exists() and (repo_dir / ".git").exists():
        try:
            # Fetch latest remote changes
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            # Checkout main (or master) and pull
            pull_proc = subprocess.run(
                ["git", "checkout", "main"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if pull_proc.returncode != 0:
                # Fallback if the default branch is 'master' instead of 'main'
                subprocess.run(
                    ["git", "checkout", "master"],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            subprocess.run(
                ["git", "pull"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            return True, "Updated local repository to latest main."
        except Exception as exc:
            return False, f"Failed to pull latest git changes: {str(exc)}"

    # 2. If repo does NOT exist, clone it into the specified directory
    if not repo_url:
        return False, f"Directory '{repo_dir}' does not exist and no repo_url provided."

    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        process = subprocess.run(
            ["git", "clone", repo_url, str(repo_dir)],
            capture_output=True,
            text=True,
            check=False,
        )

        if process.returncode == 0:
            return True, "Cloned repository successfully."
        else:
            return False, f"Git clone failed:\n{process.stderr}"
    except Exception as exc:
        return False, f"Failed to execute git clone: {str(exc)}"


def build_initial_results() -> Dict[str, Any]:
    repos_base_dir, target_projects = load_projects_config()
    initial_projects = {}

    for proj in target_projects:
        proj_name = proj.get("project_name", "Unknown Project")
        roles_list = proj.get("roles", []) or []

        env_matrix = {}
        for env in ENVIRONMENTS:
            env_matrix[env] = {
                "status": "Not Scanned",
                "summary": f"No scan executed yet for {env.upper()}.",
                "role_results": {},
            }

        initial_projects[proj_name] = {
            "environments": env_matrix,
            "roles": roles_list,
        }

    return initial_projects


current_results: Dict[str, Any] = build_initial_results()


def run_single_plan(role_path: str, environment: str) -> dict:
    if not os.path.exists(role_path):
        return {
            "status": "error",
            "summary": f"Role path '{role_path}' does not exist on disk.",
        }

    target_cmd = f"plan-{environment}"

    try:
        process = subprocess.Popen(
            ["make", target_cmd],
            cwd=role_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        stdout_output, _ = process.communicate()
        output = stdout_output or "No output returned from process."

        if process.returncode == 0:
            status = "In Sync"
        elif process.returncode == 2:
            status = "Drift Detected"
        else:
            status = "Error"

        return {"status": status, "summary": output}

    except Exception as exc:
        return {"status": "Error", "summary": f"Execution Exception: {str(exc)}"}


async def background_drift_scan():
    global is_scanning, current_results
    is_scanning = True

    try:
        repos_base_dir, target_projects = load_projects_config()
        updated_projects = {}

        for proj in target_projects:
            proj_name = proj.get("project_name", "Unknown Project")
            repo_url = proj.get("repo_url", "")
            roles_list = proj.get("roles", []) or []

            repo_dir = repos_base_dir / proj_name
            cloned, clone_msg = ensure_repo_cloned(repo_url, repo_dir)

            env_matrix = {}

            for env in ENVIRONMENTS:
                role_results = {}
                overall_status = "In Sync"
                summaries = []

                if not cloned:
                    env_matrix[env] = {
                        "status": "Error",
                        "summary": f"Repository checkout failed: {clone_msg}",
                        "role_results": {},
                    }
                    continue

                for role_rel_path in roles_list:
                    role_path = str(repo_dir / role_rel_path)

                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, run_single_plan, role_path, env
                    )

                    role_results[role_rel_path] = result
                    summaries.append(f"--- {role_rel_path} ---\n{result['summary']}")

                    if result["status"] == "Error":
                        overall_status = "Error"
                    elif result["status"] == "Drift Detected" and overall_status != "Error":
                        overall_status = "Drift Detected"

                env_matrix[env] = {
                    "status": overall_status,
                    "summary": "\n\n".join(summaries),
                    "role_results": role_results,
                }

            updated_projects[proj_name] = {
                "environments": env_matrix,
                "roles": roles_list,
            }

        current_results = updated_projects

    finally:
        is_scanning = False


@router.get("/results")
async def get_results():
    return JSONResponse(
        content={
            "is_scanning": is_scanning,
            "projects": current_results,
        }
    )


@router.post("/scan/trigger")
async def trigger_scan(background_tasks: BackgroundTasks):
    global is_scanning

    if is_scanning:
        return JSONResponse(
            status_code=400,
            content={"message": "Scan is already in progress"},
        )

    background_tasks.add_task(background_drift_scan)
    return {"status": "started", "message": "Drift scan initiated successfully"}