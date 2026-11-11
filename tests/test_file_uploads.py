"""
Unit and integration tests for chat file uploads, secure streaming delivery,
MIME preview detection, and rolling TTL cleanup.
"""
import io
import json
import os
import shutil
import sys
import time
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
SERVER_DIR = os.path.join(ROOT_DIR, "server")


class MockHandler:
    def __init__(self, headers=None, body=b""):
        self.code = None
        self.content_type = None
        self.data = b""
        self.headers = headers or {}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass

    def send_raw(self, code, content_type, data, extra_headers=None):
        self.code = code
        self.content_type = content_type
        self.data = data
        if extra_headers:
            for k, v in extra_headers:
                self.headers[k] = v

    def send_json(self, code, payload):
        self.code = code
        self.content_type = "application/json; charset=utf-8"
        self.data = json.dumps(payload).encode("utf-8")

    def send_error(self, code, message):
        self.code = code
        self.data = str(message).encode("utf-8")


class TestFileUploads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for mod in list(sys.modules.keys()):
            if any(mod == p or mod.startswith(p + '.') for p in ['config', 'models', 'services', 'controllers', 'router', 'app']):
                sys.modules.pop(mod, None)
        sys.path = [p for p in sys.path if not p.endswith("music-bot")]
        if SERVER_DIR not in sys.path:
            sys.path.insert(0, SERVER_DIR)

        cls.test_upload_dir = os.path.join(ROOT_DIR, "test_uploads_sandbox")
        os.environ["VOLT_UPLOAD_DIR"] = cls.test_upload_dir

        import config
        config.UPLOAD_DIR = cls.test_upload_dir
        import services.file_service as fs
        import controllers.file_controller as fc

        cls.config = config
        cls.file_service = fs
        cls.file_controller = fc

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.test_upload_dir):
            shutil.rmtree(cls.test_upload_dir, ignore_errors=True)

    def setUp(self):
        os.makedirs(self.test_upload_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_upload_dir):
            shutil.rmtree(self.test_upload_dir, ignore_errors=True)

    def test_save_uploaded_file_valid_png(self):
        payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048
        stream = io.BytesIO(payload)
        meta = self.file_service.save_uploaded_file(stream, len(payload), "test_screenshot.png")

        self.assertTrue(meta["id"].startswith("f_"))
        self.assertEqual(meta["name"], "test_screenshot.png")
        self.assertEqual(meta["size"], len(payload))
        self.assertEqual(meta["mime"], "image/png")
        self.assertTrue(meta["is_image"])
        self.assertFalse(meta["is_video"])
        self.assertIn("/uploads/", meta["url"])

        # Check file exists on disk
        info = self.file_service.get_file_info(meta["id"], meta["name"])
        self.assertIsNotNone(info)
        self.assertEqual(info["size"], len(payload))
        self.assertTrue(info["is_previewable"])

    def test_save_uploaded_file_size_limit_and_empty(self):
        # Empty file
        with self.assertRaises(ValueError):
            self.file_service.save_uploaded_file(io.BytesIO(b""), 0, "empty.txt")

        # Exceed limit
        huge_len = self.config.MAX_FILE_SIZE_BYTES + 1024
        dummy_stream = io.BytesIO(b"x")
        with self.assertRaises(ValueError):
            self.file_service.save_uploaded_file(dummy_stream, huge_len, "huge.zip")

    def test_anti_path_traversal(self):
        # Malicious IDs and filenames
        self.assertIsNone(self.file_service.get_file_info("../../etc", "passwd"))
        self.assertIsNone(self.file_service.get_file_info("f_12345", "../../boot.ini"))

    def test_previewable_mime_types(self):
        self.assertTrue(self.file_service.is_safe_preview_mime("image/png"))
        self.assertTrue(self.file_service.is_safe_preview_mime("image/jpeg"))
        self.assertTrue(self.file_service.is_safe_preview_mime("video/mp4"))
        self.assertTrue(self.file_service.is_safe_preview_mime("audio/mp3"))

        # Non-previewables (must force attachment)
        self.assertFalse(self.file_service.is_safe_preview_mime("application/zip"))
        self.assertFalse(self.file_service.is_safe_preview_mime("text/html"))
        self.assertFalse(self.file_service.is_safe_preview_mime("application/x-msdownload"))

    def test_cleanup_expired_files(self):
        # Create an expired folder (>48h old)
        old_folder = os.path.join(self.test_upload_dir, "f_old12345678")
        os.makedirs(old_folder, exist_ok=True)
        old_file = os.path.join(old_folder, "old.txt")
        with open(old_file, "w") as f:
            f.write("old")
        # Backdate mtime by 50 hours
        past = time.time() - (50 * 3600)
        os.utime(old_folder, (past, past))
        os.utime(old_file, (past, past))

        # Create a fresh folder
        fresh_folder = os.path.join(self.test_upload_dir, "f_fresh123456")
        os.makedirs(fresh_folder, exist_ok=True)
        with open(os.path.join(fresh_folder, "fresh.txt"), "w") as f:
            f.write("fresh")

        purged = self.file_service.cleanup_expired_files()
        self.assertGreaterEqual(purged, 1)
        self.assertFalse(os.path.exists(old_folder))
        self.assertTrue(os.path.exists(fresh_folder))

    def test_file_controller_upload_and_serve(self):
        content = b"Config file content: [server] port=8000"
        h_post = MockHandler(
            headers={"Content-Length": str(len(content)), "X-Filename": "config.ini"},
            body=content,
        )
        self.file_controller.handle_chat_upload_post(h_post, {}, content)
        self.assertEqual(h_post.code, 200)

        res = json.loads(h_post.data.decode("utf-8"))
        self.assertTrue(res["ok"])
        file_obj = res["file"]
        self.assertEqual(file_obj["name"], "config.ini")
        self.assertEqual(file_obj["size"], len(content))

        # Test serving the file
        h_get = MockHandler()
        serve_path = f"/uploads/{file_obj['id']}/config.ini"
        self.file_controller.handle_serve_upload_get(h_get, serve_path, {})
        self.assertEqual(h_get.code, 200)
        # Verify strict security headers
        self.assertEqual(h_get.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("sandbox", h_get.headers.get("Content-Security-Policy", ""))
        self.assertIn("attachment", h_get.headers.get("Content-Disposition", ""))


if __name__ == "__main__":
    unittest.main()
