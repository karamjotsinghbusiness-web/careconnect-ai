import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from app.security_events import record_security_event


def _target_url():
    return os.environ.get(
        "MONITOR_TARGET_URL",
        "https://careconnect-ai-production-c747.up.railway.app/",
    ).strip()


def check_once():
    target = _target_url()
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        record_security_event(
            "monitor_insecure_target", "critical", "external-monitor",
            details={"configuration": "MONITOR_TARGET_URL must use HTTPS"},
        )
        return False

    try:
        request = urllib.request.Request(
            target,
            headers={"User-Agent": "CareConnect-External-Monitor/1.0"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
            body = response.read(16 * 1024)
        if status != 200:
            raise RuntimeError(f"unexpected HTTP status {status}")
        data = json.loads(body)
        if not isinstance(data, dict) or "message" not in data:
            raise ValueError("health response format was invalid")

        if data.get("real_phi_enabled") and not os.environ.get("MONITOR_EXPECT_REAL_PHI", "false").lower() == "true":
            record_security_event(
                "unexpected_real_phi_mode", "critical", "external-monitor",
                details={"configuration": "Production reports real PHI mode enabled unexpectedly"},
            )
            return False
        return True
    except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        record_security_event(
            "service_health_check_failed", "critical", "external-monitor",
            details={"reason": type(exc).__name__},
        )
        return False


def main():
    parser = argparse.ArgumentParser(description="CareConnect external security and uptime monitor")
    parser.add_argument("--once", action="store_true", help="run one health check and exit")
    args = parser.parse_args()
    interval = max(60, int(os.environ.get("MONITOR_INTERVAL_SECONDS", "300")))

    while True:
        healthy = check_once()
        print(f"CareConnect monitor check: {'healthy' if healthy else 'alert recorded'}", flush=True)
        if args.once:
            raise SystemExit(0 if healthy else 1)
        time.sleep(interval)


if __name__ == "__main__":
    main()
