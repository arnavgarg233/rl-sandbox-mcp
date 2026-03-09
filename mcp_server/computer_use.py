"""
Computer Use (GUI) tools for Mode 1 interaction.

Provides screenshot capture, mouse clicks, keyboard input, and mouse
movement by driving Xvfb + xdotool + scrot inside the container.
"""
from __future__ import annotations

import base64
import os
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, Field


SCREENSHOT_DIR = Path("/tmp/screenshots")


def _check_tool(name: str) -> None:
    """Raise RuntimeError if a required system tool is not installed."""
    try:
        subprocess.run(
            ["which", name], capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError(
            f"Required tool '{name}' is not installed. "
            f"Ensure the container image includes it (apt-get install {name})."
        )


def _display_env() -> dict[str, str]:
    """Return a copy of the current environment with DISPLAY guaranteed set."""
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":99")
    return env


class ScreenshotResponse(BaseModel):
    image_base64: str
    width: int
    height: int
    timestamp: float


class ClickRequest(BaseModel):
    x: int = Field(description="X coordinate to click")
    y: int = Field(description="Y coordinate to click")
    button: int = Field(default=1, description="Mouse button: 1=left, 2=middle, 3=right")


class ClickResponse(BaseModel):
    success: bool
    x: int
    y: int


class TypeTextRequest(BaseModel):
    text: str = Field(description="Text to type")


class TypeTextResponse(BaseModel):
    success: bool
    text_typed: str


class KeyPressRequest(BaseModel):
    key: str = Field(description="Key to press, e.g. 'Return', 'Tab', 'ctrl+s', 'alt+F4'")


class KeyPressResponse(BaseModel):
    success: bool
    key: str


class MouseMoveRequest(BaseModel):
    x: int
    y: int


class MouseMoveResponse(BaseModel):
    success: bool
    x: int
    y: int


class DoubleClickRequest(BaseModel):
    x: int
    y: int


class DoubleClickResponse(BaseModel):
    success: bool
    x: int
    y: int


class DragRequest(BaseModel):
    start_x: int
    start_y: int
    end_x: int
    end_y: int


class DragResponse(BaseModel):
    success: bool


class GetCursorPositionResponse(BaseModel):
    x: int
    y: int


def _run_xdotool(args: list[str], timeout: int = 5) -> subprocess.CompletedProcess:
    _check_tool("xdotool")
    return subprocess.run(
        ["xdotool"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_display_env(),
    )


def take_screenshot() -> ScreenshotResponse:
    """Capture the current screen state as a base64-encoded PNG."""
    _check_tool("scrot")
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = SCREENSHOT_DIR / f"screen_{int(time.time() * 1000)}.png"

    subprocess.run(
        ["scrot", "-o", str(filepath)],
        capture_output=True,
        timeout=5,
        env=_display_env(),
    )

    if not filepath.exists():
        raise RuntimeError("Screenshot capture failed")

    image_data = filepath.read_bytes()
    encoded = base64.b64encode(image_data).decode("utf-8")

    result = subprocess.run(
        ["identify", "-format", "%w %h", str(filepath)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    parts = result.stdout.strip().split()
    width = int(parts[0]) if len(parts) >= 2 else 1280
    height = int(parts[1]) if len(parts) >= 2 else 720

    filepath.unlink(missing_ok=True)

    return ScreenshotResponse(
        image_base64=encoded,
        width=width,
        height=height,
        timestamp=time.time(),
    )


def click(req: ClickRequest) -> ClickResponse:
    """Click at the specified screen coordinates."""
    _run_xdotool(["mousemove", str(req.x), str(req.y)])
    _run_xdotool(["click", str(req.button)])
    return ClickResponse(success=True, x=req.x, y=req.y)


def double_click(req: DoubleClickRequest) -> DoubleClickResponse:
    """Double-click at the specified screen coordinates."""
    _run_xdotool(["mousemove", str(req.x), str(req.y)])
    _run_xdotool(["click", "--repeat", "2", "--delay", "100", "1"])
    return DoubleClickResponse(success=True, x=req.x, y=req.y)


def type_text(req: TypeTextRequest) -> TypeTextResponse:
    """Type text using keyboard input."""
    _run_xdotool(["type", "--clearmodifiers", "--delay", "50", req.text])
    return TypeTextResponse(success=True, text_typed=req.text)


def key_press(req: KeyPressRequest) -> KeyPressResponse:
    """Press a key or key combination (e.g. 'Return', 'ctrl+s')."""
    _run_xdotool(["key", "--clearmodifiers", req.key])
    return KeyPressResponse(success=True, key=req.key)


def mouse_move(req: MouseMoveRequest) -> MouseMoveResponse:
    """Move the mouse cursor to the specified coordinates."""
    _run_xdotool(["mousemove", str(req.x), str(req.y)])
    return MouseMoveResponse(success=True, x=req.x, y=req.y)


def drag(req: DragRequest) -> DragResponse:
    """Click and drag from one position to another."""
    _run_xdotool(["mousemove", str(req.start_x), str(req.start_y)])
    _run_xdotool(["mousedown", "1"])
    _run_xdotool(["mousemove", "--delay", "50", str(req.end_x), str(req.end_y)])
    _run_xdotool(["mouseup", "1"])
    return DragResponse(success=True)


def get_cursor_position() -> GetCursorPositionResponse:
    """Get the current mouse cursor position."""
    result = _run_xdotool(["getmouselocation"])
    parts = result.stdout.strip().split()
    x = int(parts[0].split(":")[1])
    y = int(parts[1].split(":")[1])
    return GetCursorPositionResponse(x=x, y=y)
