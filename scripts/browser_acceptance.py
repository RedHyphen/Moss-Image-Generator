#!/usr/bin/env python3
"""Exercise the authenticated browser generation flow against isolated services."""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "OpenMOSS-Team/MOSS-Image-SFT"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def image_base64() -> str:
    output = io.BytesIO()
    Image.new("RGB", (512, 512), (38, 64, 56)).save(output, format="PNG")
    import base64

    return base64.b64encode(output.getvalue()).decode("ascii")


class MockVLLMHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    image = image_base64()

    def log_message(self, _format: str, *_args) -> None:
        return

    def respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTPServer hook
        if self.path == "/health":
            self.respond(200, {})
        elif self.path == "/v1/models":
            self.respond(200, {"data": [{"id": MODEL_ID}]})
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTPServer hook
        if self.path != "/v1/images/generations":
            self.respond(404, {"error": "not found"})
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(content_length))
        self.respond(200, {"data": [{"b64_json": self.image, "seed": 7}]})


def wait_for_http(url: str, timeout: float = 30) -> None:
    import urllib.request

    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception as exc:  # startup is expected to fail until uvicorn binds
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def browser_driver():
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service

    options = Options()
    options.add_argument("-headless")
    configured_driver = os.getenv("MOSS_GECKODRIVER")
    driver_path = configured_driver or shutil.which("geckodriver") or "/snap/bin/geckodriver"
    service = Service(driver_path) if Path(driver_path).exists() else Service()
    return webdriver.Firefox(options=options, service=service)


def run() -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait

    with tempfile.TemporaryDirectory(prefix="moss-browser-") as temporary:
        data_dir = Path(temporary) / "data"
        worker_server = ThreadingHTTPServer(("127.0.0.1", 0), MockVLLMHandler)
        worker_port = worker_server.server_address[1]
        worker_thread = __import__("threading").Thread(target=worker_server.serve_forever, daemon=True)
        worker_thread.start()

        app_port = free_port()
        environment = os.environ.copy()
        environment.update(
            {
                "MOSS_DATA_DIR": str(data_dir),
                "MOSS_STATIC_DIR": str(ROOT / "frontend" / "dist"),
                "MOSS_HOST": "127.0.0.1",
                "MOSS_PORT": str(app_port),
                "MOSS_VLLM_URL": f"http://127.0.0.1:{worker_port}",
                "MOSS_MODEL": MODEL_ID,
                "MOSS_MAX_QUEUE": "1",
                "MOSS_MAX_PIXELS": os.getenv("MOSS_BROWSER_MAX_PIXELS", "589824"),
                "MOSS_LOCAL_ONLY": "true",
                "MOSS_OFFLINE_MODE": "false",
                "MOSS_ROUTER_ASSISTANCE": "false",
                "VLLM_API_KEY": "",
            }
        )
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            "backend",
            "--host",
            "127.0.0.1",
            "--port",
            str(app_port),
        ]
        app_process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        driver = None
        try:
            wait_for_http(f"http://127.0.0.1:{app_port}/api/auth/status")
            driver = browser_driver()
            driver.set_window_size(390, 844)
            driver.get(f"http://127.0.0.1:{app_port}/")
            wait = WebDriverWait(driver, 30)
            username = wait.until(lambda item: item.find_element(By.CSS_SELECTOR, "input[autocomplete='username']"))
            password = wait.until(lambda item: item.find_element(By.CSS_SELECTOR, "input[type='password']"))
            username.send_keys("selenium-user")
            password.send_keys("local-browser-password")
            driver.find_element(By.XPATH, "//button[contains(., 'Create account')]").click()
            composer = wait.until(
                lambda item: item.find_element(By.CSS_SELECTOR, "textarea[placeholder*='Describe what you want to generate']")
            )
            wait.until(lambda item: item.find_elements(By.CSS_SELECTOR, ".conversation-row.active"))
            driver.find_element(By.XPATH, "//button[contains(., 'Show advanced controls')]").click()
            for viewport_width in (320, 360, 390, 600, 720):
                driver.set_window_size(viewport_width, 844)
                mobile_layout = driver.execute_script(
                    """
                    const root = document.documentElement;
                    const bounds = (selector) => {
                      const rect = document.querySelector(selector)?.getBoundingClientRect();
                      return rect ? {left: rect.left, right: rect.right, width: rect.width} : null;
                    };
                    const overflow = [];
                    for (const element of document.querySelectorAll('*')) {
                      const rect = element.getBoundingClientRect();
                      if (rect.right > root.clientWidth + 1 || rect.left < -1) {
                        overflow.push({tag: element.tagName, className: String(element.className), left: rect.left, right: rect.right, width: rect.width});
                      }
                    }
                    return {
                      clientWidth: root.clientWidth,
                      scrollWidth: root.scrollWidth,
                      advanced: bounds('.advanced-heading'),
                      toggle: bounds('.advanced-heading .ghost-toggle'),
                      panel: bounds('.control-panel'),
                      overflow: overflow.slice(0, 20),
                    };
                    """
                )
                assert mobile_layout["scrollWidth"] <= mobile_layout["clientWidth"] + 1, (
                    "Mobile advanced controls overflow the viewport: "
                    f"{mobile_layout}"
                )
                for name in ("advanced", "toggle", "panel"):
                    bounds = mobile_layout[name]
                    assert bounds and bounds["left"] >= -1 and bounds["right"] <= mobile_layout["clientWidth"] + 1, (
                        f"Mobile {name} exceeds the viewport at {viewport_width}px: {mobile_layout}"
                    )
                assert not mobile_layout["overflow"], (
                    f"A mobile element exceeds the viewport at {viewport_width}px: {mobile_layout}"
                )
            screenshot_path = os.getenv("MOSS_BROWSER_SCREENSHOT")
            if screenshot_path:
                driver.execute_script("document.querySelector('.control-panel')?.scrollIntoView({block: 'start'});")
                driver.save_screenshot(screenshot_path)
            if int(environment["MOSS_MAX_PIXELS"]) >= 1024 * 1024:
                resolution_select = wait.until(
                    lambda item: item.find_element(By.XPATH, "//label[contains(., 'Aspect ratio')]//select")
                )
                assert any(
                    "Square UHD" in option.text
                    for option in resolution_select.find_elements(By.TAG_NAME, "option")
                ), "Square UHD preset is missing from the resolution selector"
            parameter_inputs = driver.find_elements(By.CSS_SELECTOR, ".parameter-input-field input")
            for element, value in zip(parameter_inputs, ("768", "576", "37", "4", "2")):
                element.clear()
                element.send_keys(value)
            driver.find_element(By.CSS_SELECTOR, ".batch-toggle input").click()
            negative_prompt = wait.until(
                lambda item: item.execute_script(
                    "const label = Array.from(document.querySelectorAll('label')).find((candidate) => "
                    "candidate.textContent.includes('Negative prompt')); "
                    "return label && label.nextElementSibling && label.nextElementSibling.matches('input') "
                    "? label.nextElementSibling : null;"
                )
            )
            negative_prompt.clear()
            negative_prompt.send_keys("duplicate person, multiple subjects")
            composer.send_keys("A single green square on a plain background")
            composer.send_keys(Keys.ENTER)
            wait.until(lambda item: item.find_elements(By.CSS_SELECTOR, ".chat-message.user, .conversation-alert"))
            wait.until(lambda item: item.find_elements(By.CSS_SELECTOR, ".message-asset img"))
            load_prompt = wait.until(lambda item: item.find_element(By.XPATH, "//button[contains(., 'Load prompt') or contains(., 'Loaded')]"))
            load_prompt.click()
            wait.until(lambda item: "A single green square" in item.find_element(By.CSS_SELECTOR, "textarea.prompt-box").get_attribute("value"))
            wait.until(
                lambda item: [field.get_attribute("value") for field in item.find_elements(By.CSS_SELECTOR, ".parameter-input-field input")]
                == ["768", "576", "37", "4", "2"]
            )
            assert driver.find_element(By.CSS_SELECTOR, ".seed-row input[type='number']").get_attribute("value") == "7", "Loaded prompt did not restore the exact output seed"
            assert not driver.find_element(By.CSS_SELECTOR, ".batch-toggle input").is_selected(), "Loaded output should disable randomized batch mode for reproducibility"
            assert not driver.find_element(By.CSS_SELECTOR, ".seed-row .check input").is_selected(), "Loaded output should disable random seed mode for reproducibility"
            driver.find_element(By.CSS_SELECTOR, "button[title='Refresh']").click()
            gallery_load = wait.until(
                lambda item: item.find_element(
                    By.XPATH,
                    "//article[contains(@class, 'gallery-card')]//button[contains(., 'Load prompt') or contains(., 'Loaded')]",
                )
            )
            gallery_load.click()
            wait.until(lambda item: "A single green square" in item.find_element(By.CSS_SELECTOR, "textarea.prompt-box").get_attribute("value"))
            wait.until(
                lambda item: [field.get_attribute("value") for field in item.find_elements(By.CSS_SELECTOR, ".parameter-input-field input")]
                == ["768", "576", "37", "4", "2"]
            )
            print("Browser acceptance passed: login, conversation, prompt, queue, terminal result, rendered image, and prompt loading")
        finally:
            if driver is not None:
                driver.quit()
            app_process.terminate()
            try:
                output, _ = app_process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                app_process.kill()
                output, _ = app_process.communicate()
            worker_server.shutdown()
            if app_process.returncode not in (0, -15):
                raise RuntimeError(f"application exited with {app_process.returncode}:\n{output[-4000:]}")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"Browser acceptance failed: {exc}", file=sys.stderr)
        raise
