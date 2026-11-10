"""
Integration and unit tests for the modular VOLT server.
Tests routing, token generation, room presence, static assets, and state store.
"""
import unittest
import os
import sys
import json
import time

SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
sys.path.insert(0, SERVER_DIR)

from config import API_KEY, API_SECRET, DEFAULT_ROOM_ID
from models.state import StateStore
from services.token_service import generate_client_token, generate_admin_token
from services.static_service import inject_web_hash, get_file_meta
from router import Router

class MockHandler:
    def __init__(self):
        self.code = None
        self.content_type = None
        self.data = None
        self.headers = {'Host': 'localhost'}

    def send_raw(self, code, content_type, data, extra_headers=None):
        self.code = code
        self.content_type = content_type
        self.data = data

    def send_json(self, code, payload):
        self.code = code
        self.content_type = 'application/json; charset=utf-8'
        self.data = json.dumps(payload).encode('utf-8')

    def send_error(self, code, message):
        self.code = code
        self.data = message.encode('utf-8')


class TestModularVoltServer(unittest.TestCase):
    def test_state_store_thread_safe_presence(self):
        store = StateStore()
        store.touch_user("lounge", "User1")
        store.touch_user("lounge", "User2")
        users = store.get_fallback_active_users("lounge")
        self.assertIn("User1", users)
        self.assertIn("User2", users)

        store.remove_user("lounge", "User1")
        users2 = store.get_fallback_active_users("lounge")
        self.assertNotIn("User1", users2)
        self.assertIn("User2", users2)

        store.record_hardware_session("hw_123456", "User2", "Desktop", "lounge")
        sess = store.get_hardware_session("hw_123456")
        self.assertIsNotNone(sess)
        self.assertEqual(sess["name"], "User2")

    def test_livekit_token_minting_and_structure(self):
        import base64
        import hmac
        import hashlib

        token = generate_client_token("Alice", "lounge", hwid="hw_abcdef123456")
        parts = token.split('.')
        self.assertEqual(len(parts), 3)

        payload_b64 = parts[1] + '=' * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        self.assertEqual(payload["iss"], API_KEY)
        self.assertEqual(payload["video"]["room"], "lounge")
        self.assertTrue(payload["video"]["canPublish"])
        self.assertTrue(payload["video"]["canSubscribe"])

        # Signature check
        unsigned = f"{parts[0]}.{parts[1]}"
        expected_sig = base64.urlsafe_b64encode(
            hmac.new(API_SECRET.encode(), unsigned.encode(), hashlib.sha256).digest()
        ).rstrip(b'=').decode()
        self.assertEqual(parts[2], expected_sig)

    def test_admin_token_expiration(self):
        import base64
        tok = generate_admin_token("lounge")
        parts = tok.split('.')
        payload_b64 = parts[1] + '=' * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        self.assertTrue(payload["video"]["roomAdmin"])
        self.assertEqual(payload["sub"], "volt_server_admin")
        self.assertGreater(payload["exp"], payload["iat"])

    def test_static_hash_injection(self):
        sample_html = b"<!DOCTYPE html><html><head><title>Test</title></head><body>OK</body></html>"
        injected = inject_web_hash(sample_html, "hash123")
        self.assertIn(b'window.__VOLT_INITIAL_WEB_HASH__="hash123";', injected)
        self.assertIn(b'</head>', injected)

    def test_router_dispatch_get_and_post(self):
        r = Router()

        @r.get("/api/test")
        def handle_test(handler, query):
            handler.send_json(200, {"success": True, "q": query.get("k", [""])[0]})

        @r.post("/api/echo")
        def handle_echo(handler, query, body):
            handler.send_raw(200, "text/plain", body)

        h1 = MockHandler()
        r.dispatch_get(h1, "/api/test", {"k": ["val1"]})
        self.assertEqual(h1.code, 200)
        self.assertIn(b'"success": true', h1.data)
        self.assertIn(b'"q": "val1"', h1.data)

        h2 = MockHandler()
        r.dispatch_post(h2, "/api/echo", {}, b"Hello World")
        self.assertEqual(h2.code, 200)
        self.assertEqual(h2.data, b"Hello World")

        h3 = MockHandler()
        r.dispatch_get(h3, "/unknown", {})
        self.assertEqual(h3.code, 404)

    def test_controllers_invocation(self):
        from controllers import room_controller, token_controller

        h = MockHandler()
        room_controller.handle_get_rooms(h, {})
        self.assertEqual(h.code, 200)
        res = json.loads(h.data.decode())
        self.assertTrue(res["ok"])
        self.assertEqual(res["rooms"][0]["id"], "lounge")

        h_tok = MockHandler()
        token_controller.handle_token(h_tok, {"name": ["Bob"], "hwid": ["hw_999"]})
        self.assertEqual(h_tok.code, 200)
        tok_data = json.loads(h_tok.data.decode())
        self.assertTrue(bool(tok_data["token"]))
        self.assertEqual(tok_data["hwid"], "hw_999")


if __name__ == '__main__':
    unittest.main()
