from __future__ import annotations

import argparse
import asyncio
import ctypes
from ctypes import wintypes
from dataclasses import replace
import os
from pathlib import Path
import subprocess
from time import monotonic

from .bridge import JsonLinesBridgeClient
from .bridge_runtime import BridgeRuntimeAdapter
from .models import Action, ContractError, Risk
from .registry import ActionRegistry


user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None


def find_visible_window(process_id: int) -> int | None:
    if user32 is None:
        return None
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(window, _):
        window_process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(window_process_id))
        if window_process_id.value == process_id and user32.IsWindowVisible(window):
            found.append(int(window))
            return False
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found[0] if found else None


def force_foreground(window: int) -> None:
    foreground = user32.GetForegroundWindow()
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    target_thread = user32.GetWindowThreadProcessId(window, None)
    attached_foreground = bool(
        foreground_thread
        and foreground_thread != current_thread
        and user32.AttachThreadInput(current_thread, foreground_thread, True)
    )
    attached_target = bool(
        target_thread
        and target_thread != current_thread
        and user32.AttachThreadInput(current_thread, target_thread, True)
    )
    try:
        user32.ShowWindow(window, 9)
        user32.BringWindowToTop(window)
        user32.SetForegroundWindow(window)
        user32.SetActiveWindow(window)
    finally:
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)


async def wait_for_owned_foreground(process: subprocess.Popen, timeout_seconds: float = 8) -> int:
    started = monotonic()
    while monotonic() - started < timeout_seconds:
        if process.poll() is not None:
            raise ContractError("synthetic target exited before its window was available")
        window = find_visible_window(process.pid)
        if window is not None:
            force_foreground(window)
            await asyncio.sleep(0.25)
            foreground = int(user32.GetForegroundWindow())
            foreground_process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_process_id))
            if foreground == window and foreground_process_id.value == process.pid:
                return window
        await asyncio.sleep(0.2)
    raise ContractError("could not foreground the exact synthetic target process")


async def smoke(host_path: Path, target_path: Path) -> None:
    if os.name != "nt":
        raise ContractError("Windows UI smoke test requires Windows")
    if not host_path.is_file() or not target_path.is_file():
        raise ContractError("compiled host or smoke target is missing; run windows-host/build-framework.ps1")

    target = subprocess.Popen([str(target_path)])
    bridge = JsonLinesBridgeClient([str(host_path)], timeout_seconds=5)
    try:
        await wait_for_owned_foreground(target)
        runtime = BridgeRuntimeAdapter(bridge)
        before = await runtime.observe()
        if before.facts.get("process_name", "").lower() != "automatejev.smoketarget.exe":
            raise ContractError("foreground observation does not belong to the owned synthetic target")
        action = Action(
            id="smoke-target.fill-required-text",
            domain="desktop",
            verb="fill",
            description="Fill the approved marker in the owned synthetic target.",
            target={"process": "AutomateJev.SmokeTarget.exe", "control_type": "Edit"},
            arguments={"text": "DEMO_APPROVED"},
            preconditions=({"type": "fact_equals", "key": "required_text_present", "value": False},),
            expected_effects=({
                "type": "fact_equals",
                "key": "required_text_present",
                "value": True,
                "risk_effect": "fill_demo_text",
            },),
            risk=Risk.SAFE,
        )
        registry = ActionRegistry("windows-ui-smoke")
        snapshot = registry.replace([action])
        await runtime.register(snapshot)
        envelope = registry.envelope(action.id, before, "windows-ui-smoke:step-1")
        await runtime.execute_if_current(envelope)
        await asyncio.sleep(0.3)
        after = await runtime.observe()
        if after.facts.get("required_text_present") is not True:
            raise ContractError("native host did not observe the expected marker after execution")
        try:
            await runtime.execute_if_current(envelope)
        except ContractError as error:
            if "DUPLICATE" not in str(error):
                raise
        else:
            raise ContractError("native host accepted a duplicate idempotency key")
        stale = replace(envelope, idempotency_key="windows-ui-smoke:step-2")
        try:
            await runtime.execute_if_current(stale)
        except ContractError as error:
            if "revision mismatch" not in str(error):
                raise
        else:
            raise ContractError("native host accepted a stale state revision")
        print({
            "host": "C_SHARP",
            "target_pid": target.pid,
            "action_id": action.id,
            "before_revision": before.state_revision,
            "after_revision": after.state_revision,
            "effect_observed": True,
            "duplicate_blocked": True,
            "stale_revision_blocked": True,
        })
    finally:
        await bridge.close()
        if target.poll() is None:
            target.kill()
            target.wait(timeout=3)


def main() -> None:
    bin_directory = Path(__file__).parents[2] / "windows-host" / "bin"
    parser = argparse.ArgumentParser(description="Run an isolated Windows UI Automation smoke test.")
    parser.add_argument("--host", type=Path, default=bin_directory / "AutomateJev.WindowsHost.exe")
    parser.add_argument("--target", type=Path, default=bin_directory / "AutomateJev.SmokeTarget.exe")
    arguments = parser.parse_args()
    asyncio.run(smoke(arguments.host.resolve(), arguments.target.resolve()))


if __name__ == "__main__":
    main()
