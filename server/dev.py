"""Loopback-only development and integration-test server."""
import argparse
from socketserver import ThreadingMixIn
from wsgiref.simple_server import make_server, WSGIServer

from .app import App


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", type=int, default=17881)
    parser.add_argument("--release", action="store_true", help="在回环地址验证正式服务入口的行为")
    args = parser.parse_args()
    with make_server("127.0.0.1", args.port, App(args.db, development=not args.release), server_class=ThreadedServer) as server:
        print("http://127.0.0.1:{}".format(server.server_port), flush=True)
        server.serve_forever()
