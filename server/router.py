"""
Lightweight, declarative HTTP Router for dispatching requests to controllers.
"""
from typing import Callable, Any

class Router:
    def __init__(self):
        self.exact_get: dict[str, Callable] = {}
        self.exact_post: dict[str, Callable] = {}
        self.prefix_get: list[tuple[str, Callable]] = []
        self.prefix_post: list[tuple[str, Callable]] = []
        self.default_get: Callable | None = None

    def get(self, path: str):
        def decorator(func: Callable):
            self.exact_get[path] = func
            return func
        return decorator

    def post(self, path: str):
        def decorator(func: Callable):
            self.exact_post[path] = func
            return func
        return decorator

    def prefix_get_route(self, prefix: str):
        def decorator(func: Callable):
            self.prefix_get.append((prefix, func))
            return func
        return decorator

    def prefix_post_route(self, prefix: str):
        def decorator(func: Callable):
            self.prefix_post.append((prefix, func))
            return func
        return decorator

    def set_default_get(self, func: Callable):
        self.default_get = func

    def dispatch_get(self, handler: Any, path: str, query: dict) -> None:
        if path in self.exact_get:
            self.exact_get[path](handler, query)
            return
        for prefix, func in self.prefix_get:
            if path.startswith(prefix):
                func(handler, path, query)
                return
        if self.default_get:
            self.default_get(handler, path, query)
        else:
            handler.send_error(404, "Not Found")

    def dispatch_post(self, handler: Any, path: str, query: dict, body: bytes) -> None:
        if path in self.exact_post:
            self.exact_post[path](handler, query, body)
            return
        for prefix, func in self.prefix_post:
            if path.startswith(prefix):
                func(handler, path, query, body)
                return
        handler.send_error(404, "Not Found")
