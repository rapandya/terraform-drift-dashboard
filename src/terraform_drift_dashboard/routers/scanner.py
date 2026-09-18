import os
import sys
import yaml
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, BackgroundTasks, HTTPException

# Determine project paths - search parent directories dynamically for projects.yaml
BASE_DIR = Path(__file__).resolve().parent

def find_config_path() -> Path:
    """Finds projects.yaml by checking current and parent directories."""
    for parent in [BASE_DIR] + list(BASE_DIR.parents):
        candidate = parent / "projects.yaml"
        if candidate.exists():
            return candidate
    return BASE_DIR.parent.parent.parent / "projects.yaml"

CONFIG_PATH = find_config_path()

# Restrict maximum parallel Makefile/Terraform processes to prevent CPU/RAM crashes
MAX_CONCURRENT_WORKERS = 2

# Set up central Terraform plugin cache directory to save disk space and network
PLUGIN_CACHE_DIR = Path("/tmp/terraform_plugin_cache")
PLUGIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ["TF_IN_AUTOMATION"] = "true"
os.environ["TF_PLUGIN_CACHE_DIR"] = str(PLUGIN_CACHE_DIR)

ENVIRONMENTS = ["dev", "qa", "perf", "prod"]

# Global state in memory
is_scanning = False
current_scan_results = {
    "is_scanning": False,
    "projects": {}
}

# FastAPI Router instance
router = APIRouter(prefix="/api")


def load_projects_config() -> dict:
    """Loads projects and roles configuration from projects.yaml."""
    if not CONFIG_PATH.exists():
        print(f"Error: Config file not found at {CONFIG_PATH}")
        return {"repos_dir": "/tmp/terraform_repos", "projects": []}

    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f) or {}


def ensure_repo_cloned(repo_url: str, repo_dir: Path) -> tuple[bool, str]:
    """Clones repository once or pulls the latest main branch with shallow fetch."""
    if repo_dir.exists() and (repo_dir / ".git").exists():
        try:
            subprocess.run(
                ["git", "fetch", "--depth", "1", "origin", "main"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            pull_proc = subprocess.run(
                ["git", "checkout", "main"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if pull_proc.returncode != 0:
                subprocess.run(
                    ["git", "checkout", "master"],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            return True, "Updated local repository."
        except Exception as exc:
            return False, f"Failed to pull latest git changes: {str(exc)}"

    if not repo_url:
        return False, f"Directory '{repo_dir}' does not exist and no repo_url provided."

    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        process = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(repo_dir)],
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


def run_make_plan(role_dir: Path, env: str) -> dict:
    """Executes 'make plan-<env>' inside the specific role path directory."""
    if not role_dir.exists():
        return {
            "status": "Error",
            "summary": f"Directory path does not exist: {role_dir}"
        }

    target = f"plan-{env}"
    try:
        process = subprocess.run(
            ["make", target],
            cwd=role_dir,
            capture_output=True,
            text=True,
            check=False,
        )

        stdout = process.stdout or ""
        stderr = process.stderr or ""
        full_output = (stdout + "\n" + stderr).strip()

        if process.returncode == 0:
            status = "In Sync"
        elif process.returncode == 2 or "Terraform will perform the following actions" in full_output:
            status = "Drift Detected"
        else:
            status = "Error"

        return {
            "status": status,
            "summary": full_output if full_output else "No output produced."
        }

    except Exception as exc:
        return {
            "status": "Error",
            "summary": f"Failed to execute make command: {str(exc)}"
        }


def scan_single_target(project_name: str, repo_dir: Path, role_path: str, env: str) -> tuple[str, str, str, dict]:
    """Helper worker for thread pool scanning."""
    role_dir = repo_dir / role_path
    result = run_make_plan(role_dir, env)
    return project_name, role_path, env, result


def run_full_scan():
    """Main background scan worker executing throttled scans across all roles and envs."""
    global is_scanning, current_scan_results
    is_scanning = True
    current_scan_results["is_scanning"] = True

    config = load_projects_config()
    repos_base = Path(config.get("repos_dir", "/tmp/terraform_repos"))
    projects_list = config.get("projects", [])

    scan_tree = {}

    for proj in projects_list:
        project_name = proj.get("project_name")
        repo_url = proj.get("repo_url")
        roles = proj.get("roles", [])
        repo_dir = repos_base / project_name

        repo_ok, repo_msg = ensure_repo_cloned(repo_url, repo_dir)

        scan_tree[project_name] = {
            "roles": roles,
            "environments": {
                env: {"status": "Not Scanned", "summary": "", "role_results": {}}
                for env in ENVIRONMENTS
            }
        }

        for env in ENVIRONMENTS:
            for role_path in roles:
                if not repo_ok:
                    scan_tree[project_name]["environments"][env]["role_results"][role_path] = {
                        "status": "Error",
                        "summary": repo_msg
                    }

    tasks = []
    # Strict max_workers limit prevents system thrashing/crashing
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
        for proj in projects_list:
            project_name = proj.get("project_name")
            roles = proj.get("roles", [])
            repo_dir = repos_base / project_name

            if not (repo_dir / ".git").exists():
                continue

            for role_path in roles:
                for env in ENVIRONMENTS:
                    tasks.append(
                        executor.submit(
                            scan_single_target,
                            project_name,
                            repo_dir,
                            role_path,
                            env
                        )
                    )

        for future in as_completed(tasks):
            project_name, role_path, env, res = future.result()

            env_dict = scan_tree[project_name]["environments"][env]
            env_dict["role_results"][role_path] = res

            current_status = env_dict["status"]
            new_status = res["status"]

            if new_status == "Error":
                env_dict["status"] = "Error"
            elif new_status == "Drift Detected" and current_status != "Error":
                env_dict["status"] = "Drift Detected"
            elif new_status == "In Sync" and current_status not in ["Error", "Drift Detected"]:
                env_dict["status"] = "In Sync"

    current_scan_results = {
        "is_scanning": False,
        "projects": scan_tree
    }
    is_scanning = False


def get_results() -> dict:
    """Returns current scan state or initializes project structure from projects.yaml."""
    global current_scan_results

    if not current_scan_results.get("projects"):
        config = load_projects_config()
        projects_list = config.get("projects", [])
        scan_tree = {}

        for proj in projects_list:
            project_name = proj.get("project_name")
            roles = proj.get("roles", [])

            scan_tree[project_name] = {
                "roles": roles,
                "environments": {
                    env: {"status": "Not Scanned", "summary": "", "role_results": {}}
                    for env in ENVIRONMENTS
                }
            }

        current_scan_results["projects"] = scan_tree

    current_scan_results["is_scanning"] = is_scanning
    return current_scan_results


@router.get("/results")
def read_results():
    """Returns current scan state to API caller."""
    return get_results()


@router.post("/scan/trigger")
def trigger_scan(background_tasks: BackgroundTasks):
    """Triggers background drift scan job."""
    global is_scanning
    if is_scanning:
        raise HTTPException(status_code=400, detail="Scan is already running in background.")

    background_tasks.add_task(run_full_scan)
    return {"message": "Scan triggered successfully."}
