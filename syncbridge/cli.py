import argparse
import json
import os

from .app import serve
from .config import database_url, init_env, load_env
from .csv_ingest import import_csv, watch_directory
from .mapping import FieldMap
from .postgres_store import PostgresStore
from .store import Store


def configured_store():
    dsn = database_url()
    return PostgresStore(dsn) if dsn else Store(os.getenv("SYNCBRIDGE_DB", "data/syncbridge.db"))


def main():
    parser = argparse.ArgumentParser(prog="syncbridge")
    parser.add_argument("--env-file", default=None, help="required literal KEY=value file; default .env is optional; process environment takes precedence")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create local secrets without overwriting existing configuration")
    serve_cmd = sub.add_parser("serve")
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8080)
    import_cmd = sub.add_parser("import-csv")
    import_cmd.add_argument("path")
    import_cmd.add_argument("--source", default="csv")
    import_cmd.add_argument("--map")
    watch_cmd = sub.add_parser("watch-csv")
    watch_cmd.add_argument("directory")
    watch_cmd.add_argument("--interval", type=int, default=10)
    watch_cmd.add_argument("--map")
    args = parser.parse_args()
    try:
        if args.command == "init":
            created = init_env(args.env_file or ".env")
            print("Configuration created. Edit destination settings before serving." if created else "Existing configuration preserved; no changes made.")
            return
        load_env(args.env_file if args.env_file is not None else ".env", required=args.env_file is not None)
        database_url()
    except (OSError, ValueError):
        parser.exit(2, "Configuration could not be created or loaded; check file syntax and permissions.\n")
    if args.command == "serve":
        serve(args.host, args.port)
    elif args.command == "import-csv":
        print(json.dumps(import_csv(configured_store(), args.path, args.source, FieldMap.from_file(args.map))))
    else:
        watch_directory(configured_store(), args.directory, args.interval, FieldMap.from_file(args.map))


if __name__ == "__main__":
    main()
