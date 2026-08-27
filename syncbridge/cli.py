import argparse

from .app import serve


def main():
    parser = argparse.ArgumentParser(prog="syncbridge")
    parser.add_argument("serve", nargs="?")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    serve(args.host, args.port)
