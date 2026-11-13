"""Kick watcher: keeps one live Kick stream playing in a real Chrome (under Xvfb)
with the owner's session so the daily Sunset Reward Case unlocks.

Modes:
  probe [slug]   open the site, play a live stream ~40 s, dump screenshots +
                 interesting network calls to DATA_DIR/debug (used to learn the page)
  run            daily loop (service)
"""
import json
import os
import random
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent


def _data_dir():
    if os.environ.get("DATA_DIR"):
        return Path(os.environ["DATA_DIR"])
    server = Path("/var/lib/kick-watcher")
    if server.is_dir() and os.access(server, os.W_OK):
        return server
    return Path.home() / ".kick-watcher"


def _browser():
    """Real Chrome is needed for H.264; on Windows fall back to Edge if Chrome is missing."""
    if os.environ.get("KICK_BROWSER"):
        return os.environ["KICK_BROWSER"]
    if os.name == "nt":
        roots = [os.environ.get(v, "") for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        if not any(r and Path(r, "Google/Chrome/Application/chrome.exe").exists() for r in roots):
            return "msedge"
    return "chrome"


DATA = _data_dir()
DATA.mkdir(parents=True, exist_ok=True)
PROFILE = DATA / "profile"
STATE = DATA / "state.json"          # session exported by login.py
if not STATE.exists() and (HERE / "state.json").exists():
    STATE = HERE / "state.json"      # running straight from the repo folder
BROWSER = _browser()
STATUS = DATA / "status.json"
DEBUG = DATA / "debug"
LIMA = timezone(timedelta(hours=-5))
HINTS = ("drop", "reward", "case", "chest", "streak", "progress", "watch")
TARGET_MIN = int(os.environ.get("TARGET_MIN", "190"))   # 3 h + margin
LIVE_API = "https://kick.com/stream/livestreams/es?limit=24&sort=desc"
SKIP_CATEGORIES = {"Slots & Casino"}


def log(msg):
    print(msg, flush=True)


def now_lima():
    return datetime.now(LIMA)


def load_status():
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_status(**kw):
    st = load_status()
    st.update(kw)
    st["actualizado"] = now_lima().isoformat(timespec="seconds")
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATUS)
    if os.name != "nt":
        os.chmod(STATUS, 0o644)
    return st


def launch(p):
    PROFILE.mkdir(parents=True, exist_ok=True)
    ctx = p.chromium.launch_persistent_context(
        str(PROFILE),
        channel=BROWSER,
        headless=False,
        viewport={"width": 1280, "height": 800},
        locale="es-PE",
        timezone_id="America/Lima",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required",
            "--mute-audio",
            "--no-first-run",
            "--disable-dev-shm-usage",
            "--window-size=1280,800",
        ],
        ignore_default_args=["--enable-automation"],
    )
    if STATE.exists() and not logged_in(ctx):
        state = json.loads(STATE.read_text(encoding="utf-8"))
        ctx.add_cookies(state["cookies"])
        log(f"sesión importada ({len(state['cookies'])} cookies)")
    return ctx


def logged_in(ctx):
    return any(c["name"] == "session_token" for c in ctx.cookies("https://kick.com"))


def video_info(page):
    try:
        return page.evaluate(
            """() => { // the stream player, not small looping videos (reward-case animations)
                const v = [...document.querySelectorAll('video')]
                    .sort((a, b) => b.videoWidth * b.videoHeight - a.videoWidth * a.videoHeight)[0];
                if (!v) return null;
                return {t: v.currentTime, paused: v.paused, rs: v.readyState,
                        w: v.videoWidth, h: v.videoHeight}; }"""
        )
    except Exception:
        return None


def dismiss_banners(page):
    # cookie banner and "Novedades" announcement popups, which cover the page
    for name in ("Accept All Cookies", "Aceptar todas las cookies", "Entendido", "Got it"):
        try:
            b = page.get_by_role("button", name=name)
            if b.count() and b.first.is_visible():
                b.first.click(timeout=3000)
        except Exception:
            pass


def live_streams(page):
    """Live channels (most viewers first), fetched from inside the browser."""
    data = page.evaluate(
        """async (u) => { const r = await fetch(u, {headers: {Accept: 'application/json'}});
            return r.ok ? await r.json() : {data: []}; }""",
        LIVE_API,
    )
    out = []
    for it in data.get("data", []):
        cats = {c.get("name") for c in (it.get("categories") or [])}
        if it.get("is_mature") or cats & SKIP_CATEGORIES:
            continue
        slug = (it.get("channel") or {}).get("slug")
        if slug:
            out.append(slug)
    return out


def open_stream(page, skip=()):
    """Navigate to a live stream and make sure the video is actually advancing."""
    if "kick.com" not in page.url:
        page.goto("https://kick.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
    for slug in [s for s in live_streams(page) if s not in skip][:6]:
        try:
            page.goto(f"https://kick.com/{slug}", wait_until="domcontentloaded")
        except Exception as e:
            log(f"no cargó {slug}: {e}")
            continue
        page.wait_for_timeout(12000)
        dismiss_banners(page)
        a = video_info(page)
        if a and a["paused"]:
            try:
                page.evaluate("""() => { const v = [...document.querySelectorAll('video')]
                    .sort((a, b) => b.videoWidth * b.videoHeight - a.videoWidth * a.videoHeight)[0];
                    v.muted = true; return v.play(); }""")
            except Exception:
                pass
        page.wait_for_timeout(8000)
        b = video_info(page)
        if a and b and b["t"] > a["t"]:
            log(f"viendo {slug} ({b['w']}x{b['h']})")
            return slug
        log(f"{slug}: el video no avanza ({a} -> {b})")
    return None


def watch(page, minutes, on_tick=None):
    """Keep a live stream playing until `minutes` of real playback accumulate."""
    watched = 0.0
    slug = open_stream(page)
    bad = []
    last = video_info(page) if slug else None
    stalled = 0
    while watched < minutes * 60:
        if not slug:
            log("sin stream reproducible; reintento en 2 min")
            time.sleep(120)
            bad = bad[-3:]
            slug = open_stream(page, skip=bad)
            last = video_info(page) if slug else None
            continue
        t0 = time.time()
        page.wait_for_timeout(60000)
        cur = video_info(page)
        if cur and last and cur["t"] > last["t"] + 20:
            watched += time.time() - t0
            stalled = 0
        else:
            stalled += 1
            log(f"{slug}: sin avance ({stalled}) {cur}")
            if stalled >= 3:
                bad.append(slug)
                slug = open_stream(page, skip=bad)
                stalled = 0
                cur = video_info(page) if slug else None
        last = cur
        if on_tick:
            on_tick(slug, watched / 60)
    return slug


CHALLENGES_API = "https://web.kick.com/api/v1/gamification/challenges"


def challenges(page, ctx):
    """Reward cases with watch-time progress, straight from Kick's own API."""
    token = next((c["value"] for c in ctx.cookies("https://kick.com") if c["name"] == "session_token"), "")
    res = page.evaluate(
        """async ([u, t]) => {
            const h = {Accept: 'application/json'};
            if (t) h.Authorization = 'Bearer ' + decodeURIComponent(t);
            try { const r = await fetch(u, {headers: h, credentials: 'include'});
                  return {status: r.status, body: r.ok ? await r.json() : null}; }
            catch (e) { return {status: 0, body: null, err: String(e)}; } }""",
        [CHALLENGES_API, token],
    )
    if res["status"] != 200 or not res["body"]:
        log(f"challenges: HTTP {res['status']} {res.get('err', '')}")
        return None
    return [c for c in res["body"].get("data", [])
            if (c.get("condition") or {}).get("type") == "watch_time_minutes"]


def summary(chs):
    return [{"nombre": c["name"], "min": c["condition"]["progress"],
             "meta": c["condition"]["threshold"], "estado": c["status"]} for c in chs]


JS_CLICK = """(labels) => {
    const bs = [...document.querySelectorAll('button')].filter(b => !b.disabled
        && b.offsetParent !== null && labels.includes((b.innerText || '').trim()));
    if (!bs.length) return null;
    const b = bs[bs.length - 1];
    b.click();
    return (b.innerText || '').trim(); }"""


def js_click(page, *labels):
    """Click a visible, enabled button by its text from inside the page.
    (Playwright's own click often stalls on Kick's heavy pages under Xvfb.)"""
    try:
        return page.evaluate(JS_CLICK, list(labels))
    except Exception:
        return None


def shot(page, name):
    try:
        page.screenshot(path=str(DEBUG / name), timeout=20000)
    except Exception as e:
        log(f"captura {name} falló: {str(e)[:120]}")


def prize(seen):
    """Whatever the claim endpoint says we won, minus the roulette filler."""
    for r in seen:
        if "/claim" in r["url"] and r.get("status") == 200:
            try:
                data = json.loads(r["body"]).get("data", {})
                data.pop("roulette", None)
                return json.dumps(data, ensure_ascii=False)[:700]
            except Exception:
                return None
    return None


def claim(page, tag):
    """Open the rewards dialog and press every enabled 'Reclamar'. Returns what happened."""
    DEBUG.mkdir(parents=True, exist_ok=True)
    seen = []

    def on_resp(r):
        if "web.kick.com/api" in r.url and r.request.method != "GET" and "/realtime/" not in r.url:
            try:
                seen.append({"url": r.url, "status": r.status, "body": r.text()[:60000]})
            except Exception:
                seen.append({"url": r.url, "status": r.status})

    page.on("response", on_resp)
    clicked, text = 0, ""
    try:
        js_click(page, "Entendido", "Got it")      # "Novedades" popup covers everything
        page.wait_for_timeout(1000)
        opened = page.evaluate(
            """() => { const b = document.querySelector('button[aria-label="Reclama tu recompensa"], button[aria-label="Claim your reward"]');
                if (!b) return false; b.click(); return true; }""")
        if not opened:
            raise RuntimeError("no encontré el botón de recompensas")
        page.wait_for_timeout(5000)
        if js_click(page, "Entendido", "Got it"):   # it can also open on top of the dialog
            page.wait_for_timeout(2000)
        for step in range(8):
            shot(page, f"claim-{tag}-{step}.png")
            if js_click(page, "Reclamar", "Claim"):
                clicked += 1
                page.wait_for_timeout(15000)
                continue
            if clicked:
                break
            nxt = page.get_by_role("dialog").locator('button:has(svg[data-ds-icon*="Right"]), button[aria-label*="iguiente"]')
            try:
                if step < 5 and nxt.count() and nxt.first.is_visible():
                    nxt.first.click(timeout=3000, no_wait_after=True)
                    page.wait_for_timeout(2500)
                    continue
            except Exception:
                pass
            break
        shot(page, f"claim-{tag}-fin.png")
        try:
            text = page.evaluate(
                """() => { const d = [...document.querySelectorAll('[role=dialog]')].filter(e => e.getClientRects().length);
                    return d.length ? d[d.length - 1].innerText.slice(0, 1500) : ''; }""")
        except Exception:
            pass
        try:
            (DEBUG / f"claim-{tag}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        page.keyboard.press("Escape")
    except Exception as e:
        text = f"error: {str(e)[:300]}"
        log(f"claim falló: {str(e)[:300]}")
    finally:
        page.remove_listener("response", on_resp)
    (DEBUG / f"claim-{tag}.json").write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"claim: {clicked} clics, {len(seen)} respuestas, premio={prize(seen)}")
    return {"clics": clicked, "texto": text, "respuestas": seen, "premio": prize(seen)}


def parse_z(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def session(p):
    """One browser session: watch until today's cases are unlocked and claimed.
    Returns the number of seconds to sleep before the next session."""
    ctx = launch(p)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://kick.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        dismiss_banners(page)
        if not logged_in(ctx):
            save_status(fase="sesion_perdida")
            log("sin sesión: hay que volver a correr login.py en la PC y copiar state.json")
            return 3600
        chs = challenges(page, ctx)
        if chs is None:
            save_status(fase="sesion_perdida")
            return 1800

        st = load_status()
        tried = st.get("reclamados", {})        # "<id>|<window start>" -> result
        slug, bad, last, stalled, tick = None, [], None, 0, 0
        while True:
            key = lambda c: f"{c['id']}|{c['window']['starts_at']}"
            ready = [c for c in chs if c["status"] not in ("in_progress", "claimed")
                     and c["condition"]["progress"] >= c["condition"]["threshold"]
                     and (key(c) not in tried or
                          (tried[key(c)].get("estado_despues") == c["status"]
                           and tried[key(c)].get("intentos", 1) < 3))]
            for c in ready:
                log(f"listo para reclamar: {c['name']} ({c['status']})")
                res = claim(page, c["name"].split()[0].lower() + "-" + now_lima().strftime("%m%d"))
                after = challenges(page, ctx) or chs
                now_c = next((x for x in after if x["id"] == c["id"]), c)
                tried[key(c)] = {"nombre": c["name"], "cuando": now_lima().isoformat(timespec="seconds"),
                                 "intentos": tried.get(key(c), {}).get("intentos", 0) + 1,
                                 "estado_antes": c["status"], "estado_despues": now_c["status"],
                                 "clics": res["clics"], "premio": res["premio"],
                                 "texto": res["texto"][:600]}
                tried = dict(list(tried.items())[-40:])
                save_status(reclamados=tried, ultimo_reclamo=tried[key(c)], cofres=summary(after))
                chs = after
            pending = [c for c in chs if c["status"] == "in_progress"]
            if not pending:
                end = min(parse_z(c["window"]["ends_at"]) for c in chs) if chs else None
                wait = (end - datetime.now(timezone.utc)).total_seconds() if end else 6 * 3600
                wait = max(600, wait + random.randint(300, 1500))
                save_status(fase="esperando", cofres=summary(chs), viendo=None,
                            proxima=(now_lima() + timedelta(seconds=wait)).isoformat(timespec="seconds"))
                log(f"todo hecho por hoy; duermo {wait / 3600:.1f} h")
                return wait

            if not slug:
                slug = open_stream(page, skip=bad[-4:])
                last, stalled = (video_info(page) if slug else None), 0
                if not slug:
                    save_status(fase="sin_stream", cofres=summary(chs))
                    time.sleep(120)
                    continue
            save_status(fase="viendo", viendo=slug, cofres=summary(chs), proxima=None)
            page.wait_for_timeout(60000)
            cur = video_info(page)
            if cur and last and cur["t"] > last["t"] + 20:
                stalled = 0
            else:
                stalled += 1
                log(f"{slug}: sin avance ({stalled}) {cur}")
                if stalled >= 3:
                    bad.append(slug)
                    slug = None
            last = cur
            tick += 1
            if tick % 5 == 0:
                new = challenges(page, ctx)
                if new is None:
                    if not logged_in(ctx):
                        save_status(fase="sesion_perdida")
                        return 3600
                else:
                    chs = new
                    log("progreso: " + ", ".join(f"{c['name']} {c['condition']['progress']}/{c['condition']['threshold']} {c['status']}" for c in chs))
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def run():
    while True:
        try:
            with sync_playwright() as p:
                wait = session(p)
        except Exception as e:
            log(f"sesión caída: {e!r}")
            wait = 120
        time.sleep(wait)


def probe(slug=None):
    DEBUG.mkdir(parents=True, exist_ok=True)
    hits = []
    with sync_playwright() as p:
        ctx = launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_resp(r):
            u = r.url
            if "kick.com" in u and "analytics" not in u and any(k in u.lower() for k in HINTS):
                body = ""
                try:
                    if "json" in (r.headers.get("content-type") or ""):
                        body = r.text()[:1500]
                except Exception:
                    pass
                hits.append(f"{r.status} {r.request.method} {u}\n    {body}")

        page.on("response", on_resp)
        page.goto("https://kick.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        dismiss_banners(page)
        log(f"home title={page.title()!r} logged_in={logged_in(ctx)}")
        page.screenshot(path=str(DEBUG / "home.png"))
        if slug:
            page.goto(f"https://kick.com/{slug}", wait_until="domcontentloaded")
            page.wait_for_timeout(15000)
        else:
            slug = open_stream(page)
        a = video_info(page)
        page.wait_for_timeout(30000)
        b = video_info(page)
        log(f"stream={slug} video before={a} after={b}")
        page.screenshot(path=str(DEBUG / "stream.png"))
        (DEBUG / "stream.html").write_text(page.content(), encoding="utf-8")
        (DEBUG / "network.txt").write_text("\n".join(hits), encoding="utf-8")
        log(f"{len(hits)} llamadas interesantes -> {DEBUG}/network.txt")
        ctx.close()


def inspect():
    """Learn the reward-case UI: dump header buttons, click the case, log API calls."""
    from urllib.parse import urlparse
    DEBUG.mkdir(parents=True, exist_ok=True)
    hits = []
    with sync_playwright() as p:
        ctx = launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_resp(r):
            host = urlparse(r.url).hostname or ""
            if not host.endswith("kick.com") or r.request.resource_type not in ("xhr", "fetch"):
                return
            body = ""
            try:
                body = r.text()[:2500]
            except Exception:
                pass
            hits.append(f"{r.status} {r.request.method} {r.url}\n    {body}")

        page.on("response", on_resp)
        page.goto("https://kick.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        slug = open_stream(page)
        btns = page.evaluate(
            """() => [...document.querySelectorAll('button, a')].map(b => {
                const r = b.getBoundingClientRect();
                return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
                        html: b.outerHTML.slice(0, 700)}; })
              .filter(b => b.y < 60 && b.x > 950 && b.w > 0)"""
        )
        (DEBUG / "header.json").write_text(json.dumps(btns, indent=1), encoding="utf-8")
        hits.append("===== CLICK CASE =====")
        page.mouse.click(1034, 30)
        page.wait_for_timeout(6000)
        page.screenshot(path=str(DEBUG / "case.png"))
        (DEBUG / "case.html").write_text(page.content(), encoding="utf-8")
        (DEBUG / "api.txt").write_text("\n".join(hits), encoding="utf-8")
        log(f"stream={slug} {len(hits)} llamadas, {len(btns)} botones")
        ctx.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "claim":      # one manual claim attempt (stop the service first)
        with sync_playwright() as p:
            ctx = launch(p)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://kick.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            dismiss_banners(page)
            res = claim(page, "manual")
            print(json.dumps({"clics": res["clics"], "texto": res["texto"][:800]}, ensure_ascii=False, indent=1))
            print(json.dumps(summary(challenges(page, ctx) or []), ensure_ascii=False))
            ctx.close()
    elif mode == "inspect":
        inspect()
    elif mode == "probe":
        probe(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        run()
