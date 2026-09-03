# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Run a vertical example (API + web apps) with one command.

    python scripts/run_demo.py retail              # API :8000 + storefront :3000
    python scripts/run_demo.py travel              # API :8001 + storefront :3001
    python scripts/run_demo.py telecom             # API :8002 + storefront :3002
    python scripts/run_demo.py entertainment       # API :8003 + storefront :3003
    python scripts/run_demo.py retail --merchant   # API :8000 + merchant portal :3100
    python scripts/run_demo.py retail --all        # API :8000 + storefront :3000 + portal :3100

Boots uvicorn and the Next.js dev server(s), waits until they answer, prints the URLs, and
stops everything on Ctrl-C. Python packages and the examples/ npm workspace are installed on
first run. Ports are preferences: this vertical's API already running on its port is reused;
any other busy port moves the server to the next free one, and the web apps are pointed at
wherever the API is. Chat credentials come from the environment, the vertical's .env, the
repo-root .env, then the SDK's credential chain; browsing and /showcase need none.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# encoding: this script and the servers it relays print check marks, which a Windows
# console in a non-UTF-8 code page (cp950, cp1252) cannot encode -- without this the
# relay threads and the "demo is up" line die with UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
WINDOWS = os.name == "nt"
NEXT = EXAMPLES_DIR / "node_modules" / ".bin" / ("next.cmd" if WINDOWS else "next")
NPM = "npm.cmd" if WINDOWS else "npm"

VERTICALS: dict[str, dict[str, object]] = {
    "retail": {
        "api_port": 8000,
        "store": "ACME",
        # Retail persists memory across restarts; --fresh-memory deletes these files.
        "memory_files": ("data/.memory-store.json", "data/.memory-seeded.json"),
    },
    "travel": {"api_port": 8001, "store": "ACME Travel"},
    "telecom": {"api_port": 8002, "store": "ACME Mobile"},
    "entertainment": {"api_port": 8003, "store": "ACME Tickets"},
}

PYTHON_MODULES = (
    "commerce_common",
    "shopping_agent",
    "shopping_agent_runtime",
    "merchant_agent",
    "merchant_agent_runtime",
    "fastapi",
    "uvicorn",
    "dotenv",
)

GREEN, YELLOW, DIM, RESET = "\033[32m", "\033[33m", "\033[2m", "\033[0m"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(preferred: int, span: int = 50) -> int:
    for port in range(preferred, preferred + span):
        if not port_in_use(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def env_file_has_key(path: Path) -> bool:
    """True when the file sets a non-empty ANTHROPIC_API_KEY (a copied placeholder does not)."""
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.strip().partition("=")
        if key.strip() == "ANTHROPIC_API_KEY" and value.strip().strip("\"'"):
            return True
    return False


def healthy_demo_api(port: int, store: str) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/health", timeout=2) as response:
            return json.load(response).get("store") == store
    except Exception:  # anything else on the port is not ours
        return False


def plan_api_port(preferred: int, store: str, no_reuse: bool) -> tuple[int, bool]:
    """``(port, reuse)``: reuse this vertical's own API on the preferred port, else a free one."""
    if not port_in_use(preferred):
        return preferred, False
    if not no_reuse and healthy_demo_api(preferred, store):
        return preferred, True
    return find_free_port(preferred + 1), False


def wait_for(url: str, timeout_s: float, process: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:  # not up yet
            time.sleep(0.5)
    return False


def pipe_output(process: subprocess.Popen, label: str) -> None:
    def run() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(f"{DIM}[{label}]{RESET} {line.decode(errors='replace')}")

    threading.Thread(target=run, daemon=True).start()


def ensure_python_deps(install: bool) -> None:
    missing = [m for m in PYTHON_MODULES if importlib.util.find_spec(m) is None]
    if not missing:
        return
    requirements = REPO_ROOT / "requirements.txt"
    if not install:
        sys.exit(
            f"Missing Python packages ({', '.join(missing)}). Run: pip install -r {requirements}"
        )
    print(f"{YELLOW}Installing the repository's Python packages (first run)…{RESET}")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements)], check=True)


def ensure_web_deps(install: bool) -> None:
    """The eight web apps share one npm workspace at examples/, installed once."""
    if NEXT.exists():
        return
    if not install:
        sys.exit(f"{EXAMPLES_DIR}/node_modules missing — run `npm ci` in {EXAMPLES_DIR} first.")
    print(f"{YELLOW}Installing the web workspace (first run)…{RESET}")
    result = subprocess.run([NPM, "ci", "--no-audit", "--no-fund"], cwd=EXAMPLES_DIR)
    if result.returncode:
        sys.exit(f"npm ci failed (see above); fix the registry, or run it in {EXAMPLES_DIR}.")


def spawn(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # each child owns a process group, so shutdown takes its tree
    )


def stop_process(process: subprocess.Popen, force: bool = False) -> None:
    if WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    os.killpg(os.getpgid(process.pid), signal.SIGKILL if force else signal.SIGTERM)


def start_api(vertical: str, port: int, federated: bool) -> subprocess.Popen:
    env = os.environ.copy()
    if federated:
        env["COMMERCE_DEMO_AUTH"] = "sdk"
    module = f"{vertical}.api.main:app"
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        module,
        "--app-dir",
        str(EXAMPLES_DIR),
        "--port",
        str(port),
    ]
    return spawn(command, REPO_ROOT, env)


def start_web(app_dir: Path, port: int, api_port: int, prod: bool) -> subprocess.Popen:
    env = {**os.environ, "NEXT_PUBLIC_API_URL": f"http://localhost:{api_port}"}
    if prod:
        subprocess.run([str(NEXT), "build"], cwd=app_dir, check=True, env=env)
    return spawn([str(NEXT), "start" if prod else "dev", "--port", str(port)], app_dir, env)


def reset_persisted_memory(vertical: str) -> str:
    files = VERTICALS[vertical].get("memory_files")
    if not files:
        return (
            f"{DIM}--fresh-memory: {vertical} keeps memory in-process; each boot is fresh.{RESET}"
        )
    removed = []
    for relative in files:  # type: ignore[union-attr]
        path = EXAMPLES_DIR / vertical / relative
        if path.exists():
            path.unlink()
            removed.append(relative)
    names = ", ".join(removed) or "nothing"
    return f"{DIM}--fresh-memory: removed {names}; booting from the seed.{RESET}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").partition("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(__doc__ or "").partition("\n")[2],
    )
    parser.add_argument("vertical", choices=sorted(VERTICALS))
    side = parser.add_mutually_exclusive_group()
    side.add_argument("--api-only", action="store_true", help="start only the API")
    side.add_argument("--web-only", action="store_true", help="start only the web app(s)")
    surface = parser.add_mutually_exclusive_group()
    surface.add_argument(
        "--merchant", action="store_true", help="the merchant portal instead of the storefront"
    )
    surface.add_argument(
        "--all", action="store_true", help="the storefront and the merchant portal"
    )
    parser.add_argument(
        "--api-port",
        type=int,
        metavar="PORT",
        help="preferred API port (with --web-only: its port)",
    )
    parser.add_argument("--no-reuse", action="store_true", help="always boot a fresh API")
    parser.add_argument(
        "--fresh-memory",
        action="store_true",
        help="delete the vertical's persisted memory files first (implies --no-reuse)",
    )
    parser.add_argument("--no-install", action="store_true", help="fail instead of installing")
    parser.add_argument("--prod", action="store_true", help="next build + start instead of dev")
    parser.add_argument(
        "--federated",
        action="store_true",
        help="authenticate through the SDK's credential chain, ignoring key files and ANTHROPIC_API_KEY",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    def terminate(signum: int, frame: object) -> None:
        del signum, frame
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)

    config = VERTICALS[args.vertical]
    store = str(config["store"])
    default_api_port = int(config["api_port"])  # type: ignore[arg-type]
    api_port = args.api_port if args.api_port is not None else default_api_port
    web_port = default_api_port - 5000  # 8000 -> 3000; the portal adds 100
    run_api, run_web = not args.web_only, not args.api_only

    if args.fresh_memory:
        args.no_reuse = True
        if run_api:
            print(reset_persisted_memory(args.vertical))

    webs: list[tuple[str, Path, int]] = []
    if run_web and not args.merchant:
        webs.append(("storefront", EXAMPLES_DIR / args.vertical / "storefront-web", web_port))
    if run_web and (args.merchant or args.all):
        webs.append(
            ("merchant portal", EXAMPLES_DIR / args.vertical / "merchant-web", web_port + 100)
        )

    reuse_api = False
    if run_api:
        preferred = api_port
        api_port, reuse_api = plan_api_port(preferred, store, args.no_reuse)
        if reuse_api:
            print(f"{DIM}{store} API already running on :{api_port} — reusing it.{RESET}")
        elif api_port != preferred:
            print(f"{YELLOW}Port {preferred} is busy — starting the API on :{api_port}.{RESET}")
    for index, (label, app_dir, port) in enumerate(webs):
        if port_in_use(port):
            moved = find_free_port(port + 1)
            print(f"{YELLOW}Port {port} is busy — starting the {label} on :{moved}.{RESET}")
            webs[index] = (label, app_dir, moved)

    if run_api and not reuse_api and not args.federated and not os.environ.get("ANTHROPIC_API_KEY"):
        env_file = EXAMPLES_DIR / args.vertical / ".env"
        if not env_file_has_key(env_file) and not env_file_has_key(REPO_ROOT / ".env"):
            print(
                f"{YELLOW}No ANTHROPIC_API_KEY found; chat uses the SDK's credential chain if "
                f"one is configured and otherwise returns an error event. To use a key:{RESET}\n"
                "  cp .env.example .env   # at the repo root; fill in the key and restart"
            )

    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        if run_api and not reuse_api:
            ensure_python_deps(install=not args.no_install)
            api = start_api(args.vertical, api_port, args.federated)
            processes.append(("api", api))
            pipe_output(api, f"{args.vertical}-api")
            if not wait_for(f"http://localhost:{api_port}/api/health", 90, api):
                raise RuntimeError(f"The API didn't come up on :{api_port} — see output above.")
        if webs:
            ensure_web_deps(install=not args.no_install)
        for label, app_dir, port in webs:
            process = start_web(app_dir, port, api_port, args.prod)
            processes.append((label, process))
            pipe_output(process, f"{args.vertical}-{app_dir.name}")
            if not wait_for(f"http://localhost:{port}", 180, process):
                raise RuntimeError(f"The {label} didn't come up on :{port} — see output above.")

        print(f"\n{GREEN}✓ {args.vertical} demo is up{RESET}")
        for label, app_dir, port in webs:
            print(f"  {label:<16} http://localhost:{port}")
            if app_dir.name == "storefront-web":
                print(f"  {'showcase':<16} http://localhost:{port}/showcase")
        print(f"  {'api':<16} http://localhost:{api_port}/api/health")
        if not processes:
            print(f"{DIM}Nothing new was started; everything was already running.{RESET}")
            return 0
        print(f"{DIM}Ctrl-C stops everything.{RESET}\n")
        while all(process.poll() is None for _, process in processes):
            time.sleep(1)
        for name, process in processes:
            if process.poll() is not None:
                print(f"{YELLOW}{name} exited with code {process.returncode}.{RESET}")
                return process.returncode or 1
        return 0
    except RuntimeError as error:
        print(f"{YELLOW}{error}{RESET}")
        return 1
    except KeyboardInterrupt:
        print("\nShutting down…")
        return 0
    finally:
        for _, process in processes:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    stop_process(process)
        deadline = time.monotonic() + 10
        for _, process in processes:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    stop_process(process, force=True)


if __name__ == "__main__":
    raise SystemExit(main())
