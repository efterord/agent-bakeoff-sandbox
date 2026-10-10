"""Omnigent permission/telemetry default probe (efterord/ai-platform#606 §22).

No model call, no credential, no network beyond the pip install. Each check
calls one pure function of the installed release and records the result.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

results: dict[str, object] = {}


def check(name, fn):
    try:
        results[name] = fn()
    except Exception as exc:  # noqa: BLE001 - record, never crash
        results[name] = f"ERROR {type(exc).__name__}: {exc}"


def codex_bare():
    from omnigent.harnesses.codex_native.app_server import normalize_codex_permission_launch_args
    return normalize_codex_permission_launch_args([])


def codex_explicit():
    from omnigent.harnesses.codex_native.app_server import normalize_codex_permission_launch_args
    return normalize_codex_permission_launch_args(["-a", "on-request"])


def agy(headless):
    from omnigent.harnesses.antigravity_native.launch import should_skip_permissions
    return should_skip_permissions(permission_mode=None, headless=headless)


def hook(result):
    from omnigent.native.native_policy_hook import evaluation_response_to_hook_output
    return evaluation_response_to_hook_output("PreToolUse", {"result": result})


def sdk_default():
    from omnigent.inner import claude_sdk_harness
    return claude_sdk_harness._DEFAULT_PERMISSION_MODE


def launch_modes():
    from omnigent.server.routes._session_create_validation import CLAUDE_NATIVE_LAUNCH_PERMISSION_MODES
    return sorted(CLAUDE_NATIVE_LAUNCH_PERMISSION_MODES)


def telemetry_default_outside_ci():
    # Fresh interpreter, CI/opt-out variables removed, empty HOME: is telemetry on?
    drop = {"CI", "GITHUB_ACTIONS", "DO_NOT_TRACK", "OMNIGENT_ANALYTICS", "DISABLE_TELEMETRY",
            "OMNIGENT_DISABLE_TELEMETRY", "PYTEST_CURRENT_TEST"}
    env = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("RUNNER_")
           and not k.startswith("GITHUB_")}
    env["HOME"] = os.environ["POC_EMPTY_HOME"]
    code = "from omnigent.telemetry import client; print(client.is_disabled())"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    return {"is_disabled": out.stdout.strip(), "stderr_tail": out.stderr[-300:]}


def version():
    from importlib.metadata import version as v
    return {"omnigent": v("omnigent"), "claude-agent-sdk": v("claude-agent-sdk")}


check("version", version)
check("codex_bare_launch_args", codex_bare)
check("codex_explicit_launch_args", codex_explicit)
check("antigravity_skip_permissions_headless", lambda: agy(True))
check("antigravity_skip_permissions_attended", lambda: agy(False))
check("policy_hook_allow", lambda: hook("POLICY_ACTION_ALLOW"))
check("policy_hook_ask", lambda: hook("POLICY_ACTION_ASK"))
check("policy_hook_deny", lambda: hook("POLICY_ACTION_DENY"))
check("claude_sdk_default_permission_mode", sdk_default)
check("claude_native_launch_permission_modes", launch_modes)
check("telemetry_default_outside_ci", telemetry_default_outside_ci)

print(json.dumps(results, indent=2, default=str))
