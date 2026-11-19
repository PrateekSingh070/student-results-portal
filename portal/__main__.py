"""Run the portal with Waitress."""

import argparse

from waitress import serve

from . import create_app


def main():
    parser = argparse.ArgumentParser(description="Start the Student Results Portal.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()

    serve(create_app(), host=args.host, port=args.port, max_request_body_size=8 * 1024 * 1024)


if __name__ == "__main__":
    main()
