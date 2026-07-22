from __future__ import annotations

import argparse
import uvicorn

from memoria.api import create_app
from memoria.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Memoria Agent")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    settings = Settings.load(args.config)
    uvicorn.run(create_app(args.config), host=args.host or settings.host, port=args.port or settings.port)


if __name__ == "__main__":
    main()
