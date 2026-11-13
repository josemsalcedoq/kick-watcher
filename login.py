"""Opens a real browser window on kick.com, waits for the owner to log in by hand,
then saves the session (cookies + localStorage) to state.json for the EC2 watcher.

Run on the PC:  python login.py
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
STATE = HERE / "state.json"
PROFILE = HERE / "login-profile"
WAIT_SECONDS = 30 * 60


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE),
            channel="msedge",
            headless=False,
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://kick.com/", wait_until="domcontentloaded")
        print("Esperando a que inicies sesión en Kick...", flush=True)

        deadline = time.time() + WAIT_SECONDS
        while time.time() < deadline:
            try:
                names = {c["name"] for c in ctx.cookies("https://kick.com")}
            except Exception:
                print("La ventana se cerró antes de iniciar sesión.", flush=True)
                return 2
            if "session_token" in names:
                time.sleep(6)  # let the site finish writing the rest of the session
                ctx.storage_state(path=str(STATE))
                data = json.loads(STATE.read_text(encoding="utf-8"))
                print(f"Sesión guardada: {len(data['cookies'])} cookies -> {STATE}", flush=True)
                ctx.close()
                return 0
            time.sleep(2)

        print("Se acabó el tiempo de espera sin iniciar sesión.", flush=True)
        ctx.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
