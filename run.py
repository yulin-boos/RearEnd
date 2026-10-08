import argparse

import uvicorn

from Common.config import PROJECT_ROOT, Settings

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the plant disease recognition API")
    parser.add_argument("--reload", action="store_true", help="Watch for code changes during development")
    parser.add_argument("--host", help="Override the bind address; use 0.0.0.0 for a phone on the same LAN")
    parser.add_argument("--port", type=int, help="Override the API port")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    uvicorn.run("API.main:app", host=args.host or settings.host,
                port=args.port if args.port is not None else settings.port, reload=args.reload,
                reload_dirs=[str(PROJECT_ROOT / directory) for directory in
                             ("API", "Chat", "Common", "Jev", "Knowledge", "Shop", "Users", "Visual/recognition")]
                if args.reload else None)
