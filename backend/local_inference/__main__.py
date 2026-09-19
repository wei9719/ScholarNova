"""Run with a separately provisioned Transformers interpreter; no auto-installation."""

import socket

import uvicorn

from .server import Settings, create_app


def main():
    settings = Settings.from_env()
    # Reserve the port before loading weights, without touching another service.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", settings.port))
        listener.listen(16)
        config = uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=settings.port,
            access_log=False, timeout_keep_alive=5,
        )
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
