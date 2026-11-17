import copy
import gc
import io
import json
import os
import queue
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lc import images as module
from lc.image_input import ImageLineEdit, ReferenceImages
from lc.images import ImageStore, request_size, safe_image_log, validate_images
from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QKeyEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def picture(color="red", width=120, height=80):
    value = QImage(width, height, QImage.Format.Format_RGB32)
    value.fill(QColor(color))
    return value


def pump(app, condition):
    deadline = time.monotonic() + 5
    while not condition():
        app.processEvents()
        assert time.monotonic() < deadline
        time.sleep(0.01)
    app.processEvents()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_owned_file_survives_original_removal_and_normalizes(tmp_path, fmt):
    source = tmp_path / ("原始图片." + fmt.lower())
    original = picture(width=2600, height=1300)
    original.setText("Secret", "must not persist")
    assert original.save(str(source), fmt)
    store = ImageStore()
    image = store.prepare(source)
    assert (image.width, image.height) == (2048, 1024)
    assert image.size <= module.MAX_IMAGE_BYTES
    assert not QImage(str(image.path)).textKeys()
    source.unlink()
    assert image.data_url().startswith("data:image/png;base64,")
    owned = image.path
    del image
    gc.collect()
    assert not owned.exists()


@pytest.mark.parametrize("case", ["corrupt", "gif", "oversized", "pixels", "apng"])
def test_invalid_inputs_are_rejected(tmp_path, case):
    path = tmp_path / "input.png"
    if case == "corrupt":
        path.write_bytes(b"not a picture")
    elif case == "gif":
        path.write_bytes(b"GIF89a")
    elif case == "oversized":
        with path.open("wb") as stream:
            stream.truncate(module.MAX_SOURCE_BYTES + 1)
    elif case == "pixels":
        import struct
        import zlib

        data = struct.pack(">IIBBBBB", 10000, 10000, 8, 2, 0, 0, 0)
        chunk = b"IHDR" + data
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(data)) + chunk
                         + struct.pack(">I", zlib.crc32(chunk)))
    else:
        import struct
        import zlib

        assert picture().save(str(path), "PNG")
        raw = path.read_bytes()
        data = struct.pack(">II", 2, 0)
        chunk = b"acTL" + data
        path.write_bytes(raw[:33] + struct.pack(">I", 8) + chunk
                         + struct.pack(">I", zlib.crc32(chunk)) + raw[33:])
    with pytest.raises(ValueError):
        ImageStore().prepare(path)


def test_cache_keeps_live_sessions_and_removes_abandoned_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "cache_root", lambda: tmp_path)
    dead = tmp_path / ("a" * 32)
    dead.mkdir()
    (dead / "leftover.png").write_bytes(b"stale")
    first = ImageStore()
    image = first.prepare(picture())
    second = ImageStore()
    assert not dead.exists()
    assert image.path.exists() and first.directory.exists()
    folder = first.directory
    del first
    gc.collect()
    assert folder.exists()
    del image
    gc.collect()
    assert not folder.exists()
    assert second.directory.exists()


def test_request_limits_and_log_scrubbing():
    data = "data:image/png;base64," + "a" * (5 * 1024 * 1024)
    body = {"input": [{"role": "user", "content": [
        {"type": "input_text", "text": "test"},
        {"type": "input_image", "image_url": data}]}]}
    assert request_size(body) > 4 * 1024 * 1024
    with pytest.raises(ValueError, match="4 MiB"):
        request_size({"input": "x" * module.MAX_TEXT_BYTES})
    body["input"][0]["content"][1]["image_url"] = data * 3
    with pytest.raises(ValueError, match="12 MiB"):
        request_size(body)
    scrubbed = json.dumps(safe_image_log({
        "echo": data, "path": r"C:\private\我的图片.png", "bytes": b"abc",
        "local": {"type": "localImage", "path": "/private/path/image.png"}}))
    assert "base64" not in scrubbed and "private" not in scrubbed and "abc" not in scrubbed


def test_api_socket_receives_decodable_images_and_tool_followup():
    import base64
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from test_api_agent import agent_fixture, function

    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            if len(received) == 1:
                result = {"output": [function("read-count", "objects * --count")]}
            else:
                result = {"output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "Recognized"}]}]}
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    agent, _ = agent_fixture()
    agent.provider.base_url = f"http://127.0.0.1:{server.server_port}"
    agent.invoke_tool = lambda *args: {"count": 0}
    reference = ImageStore().prepare(picture("#123456"))
    try:
        result = agent._turn("Describe and count", (reference,))
        assert result["status"] == "completed" and len(received) == 2
        for body in received:
            images = [part for item in body["input"] if isinstance(item.get("content"), list)
                      for part in item["content"] if part.get("type") == "input_image"]
            assert len(images) == 1 and images[0]["detail"] == "auto"
            decoded = QImage.fromData(base64.b64decode(images[0]["image_url"].split(",", 1)[1]))
            assert decoded.pixelColor(10, 10).name() == "#123456"
    finally:
        server.shutdown()
        server.server_close()


def test_exif_rotation_and_noise_budget(tmp_path):
    import struct

    path = tmp_path / "rotated.jpg"
    assert picture(width=60, height=30).save(str(path), "JPEG")
    raw = path.read_bytes()
    exif = b"Exif\0\0II" + struct.pack("<HIH", 42, 8, 1)
    exif += struct.pack("<HHIHHI", 0x112, 3, 1, 6, 0, 0)
    path.write_bytes(raw[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + raw[2:])
    store = ImageStore()
    image = store.prepare(path)
    assert (image.width, image.height) == (30, 60)
    noise = os.urandom(2048 * 2048 * 3)
    decoded = QImage(noise, 2048, 2048, 2048 * 3, QImage.Format.Format_RGB888).copy()
    compressed = store.prepare(decoded)
    assert compressed.mime == "image/jpeg" and compressed.size <= module.MAX_IMAGE_BYTES


def test_api_image_history_replacement_tools_and_analysis_only():
    from test_api_agent import agent_fixture, function

    store = ImageStore()
    first, second = store.prepare(picture()), store.prepare(picture("blue"))
    agent, _ = agent_fixture()
    requests = []
    calls = iter([{"output": [function("count", "objects * --count")]},
                  {"output": [{"type": "message", "content": [
                      {"type": "output_text", "text": "red"}]}]}])

    def stream(body):
        requests.append(copy.deepcopy(body))
        return next(calls)

    agent.stream = stream
    agent.invoke_tool = lambda *args: {"count": 0}
    assert agent._turn("Describe and read count", (first,))["status"] == "completed"
    for body in requests:
        assert str(body).count("data:image/") == 1
    agent.stream = lambda body: requests.append(copy.deepcopy(body)) or {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}
    agent._turn("Continue", (first,))
    assert str(requests[-1]).count("data:image/") == 1
    agent._turn("", (second,))
    assert requests[-1]["tools"] == []
    assert first.data_url() not in str(requests[-1])
    assert second.data_url() in str(requests[-1])
    agent._turn("Continue without references")
    assert "data:image/" not in str(requests[-1])
    assert any(item.get("call_id") == "count" for item in requests[-1]["input"])


def test_capability_and_api_failure_never_silently_drop_images():
    from test_api_agent import agent_fixture

    image = ImageStore().prepare(picture())
    with pytest.raises(ValueError, match="不支持图像"):
        validate_images((image,), [{"model": "text", "inputModalities": ["text"]}], "text")
    validate_images((image,), [{"model": "unknown"}], "unknown")
    agent, _ = agent_fixture()
    count = []

    def fail(body):
        count.append(body)
        raise ValueError("HTTP 400 unsupported image " + image.data_url())

    agent.stream = fail
    result = agent._turn("Build", (image,))
    assert result["status"] == "failed" and len(count) == 1
    assert "base64" not in result["final"]
    assert agent.images == (image,)
    assert "data:image/" in str(agent.history)


def resident_fixture():
    from lc.resident import ResidentAgent

    agent = ResidentAgent.__new__(ResidentAgent)
    agent.thread_id, agent.model, agent.effort = "original", "chosen", "low"
    agent.provider = None
    agent._next_id = 0
    agent._id_lock = threading.Lock()
    agent._events = io.StringIO()
    agent.event_path = "test.jsonl"
    agent.on_event = lambda event: None
    agent.images, agent.text_history = (), []
    sent, requests = [], []
    incoming = queue.Queue()

    def send(message):
        sent.append(message)
        incoming.put({"id": message["id"], "result": {"turn": {"id": "turn"}}})
        incoming.put({"method": "turn/completed", "params": {
            "threadId": "stale-thread", "turn": {"status": "failed"}}})
        incoming.put({"method": "item/completed", "params": {"threadId": agent.thread_id,
            "item": {"type": "agentMessage", "text": "Recorded answer"}}})
        incoming.put({"method": "turn/completed", "params": {
            "threadId": agent.thread_id, "turn": {"status": "completed"}}})

    def request(method, params):
        requests.append((method, params))
        return {"thread": {"id": "replacement-" + str(len(requests))}}

    agent._send, agent._request, agent._receive = send, request, incoming.get
    return agent, sent, requests


def test_resident_single_attachment_replacement_clear_and_text_replay():
    store = ImageStore()
    first, second = store.prepare(picture()), store.prepare(picture("blue"))
    agent, sent, requests = resident_fixture()
    assert agent._turn("Describe", (first,))["status"] == "completed"
    assert sent[-1]["params"]["input"][1]["type"] == "localImage"
    agent._turn("Continue", (first,))
    assert len(sent[-1]["params"]["input"]) == 1
    agent._turn("Compare new reference", (second,))
    replay = next(params["items"] for method, params in requests if method == "thread/inject_items")
    assert replay[0]["role"] == "user" and replay[1]["role"] == "assistant"
    assert "Recorded answer" in str(replay) and "localImage" not in str(replay)
    assert first.sha256 not in str(sent[-1])
    agent._turn("No references")
    assert len(sent[-1]["params"]["input"]) == 1
    assert len([m for m, _ in requests if m == "thread/start"]) == 2


def test_ui_prepare_queue_snapshot_paste_and_replacement(app, tmp_path, monkeypatch):
    from lc import desktop
    from lc.conversation import QueuedPrompt

    monkeypatch.setenv("LC_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LC_PROJECT_SETTINGS", str(tmp_path / "projects.json"))

    class Backend:
        def __init__(self, *args):
            self.queue = queue.Queue()
            self.speech = None
            self.voice_lease = "voice"
            self.workspace = tmp_path
            self.data_dir = tmp_path / "data"
            self.port = 18870

        def send(self, text, images=()):
            self.queue.put(QueuedPrompt(text, time.perf_counter(), images))

        def close(self):
            pass

        def control(self, command):
            pass

    monkeypatch.setattr(desktop, "ConversationController", Backend)
    window = desktop.Companion(autostart=False)
    window.backend = Backend()
    window.ready = window.online = True
    try:
        QApplication.clipboard().setImage(picture())
        window.input.keyPressEvent(QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier))
        assert window.references.processing and not window.send.isEnabled()
        pump(app, lambda: not window.references.processing)
        assert len(window.references.images) == 1 and window.send.isEnabled()
        window.input.setText("Build a red hall")
        window.send_input()
        submitted = window.backend.queue.get_nowait()
        assert submitted.images and window.references.submitted
        window.references.add_sources([picture("blue"), picture("green")])
        pump(app, lambda: not window.references.processing)
        assert len(window.references.images) == 2
        assert submitted.images[0].path.exists()
        window.references.clear_images()
        assert submitted.images[0].path.exists()
        window.ready = False
        window.input.setText("Keep my text")
        window.send_input()
        assert window.input.text() == "Keep my text"
        window.ready = True
        window.references.add_sources([picture()])
        pump(app, lambda: not window.references.processing)
        window.input.clear()
        window.send_input()
        image_only = window.backend.queue.get_nowait()
        assert image_only.text == "" and image_only.images
        window.handle({"type": "transcript", "result": {"text": "continue", "finalization_s": 0}})
        assert window.backend.queue.get_nowait().images == image_only.images
    finally:
        window.quitting = True
        window.close()
        app.processEvents()


def test_ui_cancel_worker_failure_and_normal_text_paste(app, tmp_path, monkeypatch):
    panel = ReferenceImages()
    started, release = threading.Event(), threading.Event()
    original = panel.store.prepare

    def slow(source):
        started.set()
        release.wait(3)
        return original(source)

    monkeypatch.setattr(panel.store, "prepare", slow)
    try:
        panel.add_sources([picture()])
        assert started.wait(1)
        panel.cancel_import()
        release.set()
        panel.worker.submit(lambda: None).result(timeout=3)
        app.processEvents()
        assert not panel.images and not panel.blocked
        panel.add_sources([tmp_path / "missing.png"])
        pump(app, lambda: not panel.processing)
        assert panel.failed and panel.sources
        panel.cancel_import()
        assert not panel.blocked
        entry = ImageLineEdit()
        QApplication.clipboard().setText("plain text")
        entry.keyPressEvent(QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier))
        assert entry.text() == "plain text"
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "reference.png"))])
        from lc.image_input import mime_sources

        assert mime_sources(mime) == [tmp_path / "reference.png"]
    finally:
        release.set()
        panel.shutdown()
