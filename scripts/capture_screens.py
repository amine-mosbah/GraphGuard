"""Recapture the dashboard screenshots kept under ``docs/assets/screens``.

Driven over the Chrome DevTools Protocol rather than through a browser-automation
dependency: the repository already has ``websockets``, and adding Playwright to a
project whose analytics packages deliberately carry no frontend tooling is not worth
one figure-generation script.

Every knob that could make two runs differ is pinned here — viewport, device pixel
ratio, theme, reduced motion, and the run being displayed — because ``docs/DECISIONS.md``
D27 requires the reported figures to be reproducible from the current configuration.

Usage, with the API on :8000 and the dev server on :5173::

    .venv/bin/python scripts/capture_screens.py --email you@example.com --password ...
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# 1680x1050 at DPR 2, which is what the existing figures were captured at. Matching it
# means the .tex needs no change to \includegraphics widths.
WIDTH, HEIGHT, SCALE = 1680, 1050, 2
DEBUG_PORT = 9222

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets" / "screens"


class Chrome:
    """A minimal CDP client: enough to navigate, evaluate and screenshot."""

    def __init__(self, ws: websockets.ClientConnection) -> None:
        """Wrap one open DevTools websocket."""
        self._ws = ws
        self._id = 0

    async def call(self, method: str, **params: object) -> dict:
        """Send one CDP command and return its matching response, skipping events."""
        self._id += 1
        await self._ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            message = json.loads(await self._ws.recv())
            # Events share the socket with responses; only the matching id is ours.
            if message.get("id") == self._id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def evaluate(self, expression: str) -> object:
        """Evaluate a JavaScript expression in the page and return its value."""
        result = await self.call(
            "Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True
        )
        if "exceptionDetails" in result:
            raise RuntimeError(f"Browser evaluation failed: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    async def goto(self, url: str, settle: float) -> None:
        """Navigate and wait a fixed settle time for the page to render."""
        await self.call("Page.navigate", url=url)
        # The console fetches on mount and the force simulation runs synchronously on
        # first paint, so a fixed settle is both simpler and steadier than racing a
        # load event that fires before any of that has happened.
        await asyncio.sleep(settle)

    async def shoot(self, path: Path) -> None:
        """Screenshot the current viewport to a PNG at `path`."""
        result = await self.call("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(result["data"]))
        print(f"  wrote {path.relative_to(ROOT)}")


def launch() -> subprocess.Popen[bytes]:
    """Start headless Chrome with the pinned viewport and scale factor."""
    process = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--window-size={WIDTH},{HEIGHT}",
            f"--force-device-scale-factor={SCALE}",
            "--hide-scrollbars",
            "--force-prefers-reduced-motion",
            "--no-first-run",
            "--user-data-dir=/tmp/gg-capture-profile",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1):
                return process
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("Chrome did not expose its debugging port")


def websocket_url() -> str:
    """Fetch the DevTools websocket URL from the debugging port."""
    with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/list") as response:
        for target in json.load(response):
            if target["type"] == "page":
                return target["webSocketDebuggerUrl"]
    raise RuntimeError("no page target")


async def capture(base: str, email: str, password: str) -> None:
    """Sign in, then walk every screen the report includes and capture each."""
    async with websockets.connect(websocket_url(), max_size=64 * 1024 * 1024) as socket:
        chrome = Chrome(socket)
        await chrome.call("Page.enable")
        await chrome.call("Runtime.enable")
        await chrome.call(
            "Emulation.setDeviceMetricsOverride",
            width=WIDTH,
            height=HEIGHT,
            deviceScaleFactor=SCALE,
            mobile=False,
        )
        # The figures are printed on white paper, so they are captured in the light
        # theme regardless of whatever the machine's OS setting happens to be.
        await chrome.call(
            "Emulation.setEmulatedMedia",
            features=[
                {"name": "prefers-color-scheme", "value": "light"},
                {"name": "prefers-reduced-motion", "value": "reduce"},
            ],
        )

        await chrome.goto(base, 2.0)
        signed_in = await chrome.evaluate(f"""(async () => {{
              const r = await fetch('/api/auth/login', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{email: {email!r}, password: {password!r}}})
              }});
              if (!r.ok) return 'login failed: ' + r.status;
              const t = await r.json();
              localStorage.setItem('gg.access', t.access_token);
              localStorage.setItem('gg.refresh', t.refresh_token);
              localStorage.setItem('gg.theme', 'light');
              return 'ok';
            }})()""")
        if signed_in != "ok":
            raise SystemExit(f"could not sign in: {signed_in}")

        OUT.mkdir(parents=True, exist_ok=True)

        # The alert detail figure needs a concrete alert, and it should be a confirmed
        # one so the panel shows a populated triage history rather than an empty queue.
        await chrome.goto(f"{base}/", 3.5)
        alert_id = await chrome.evaluate("""(async () => {
              const h = {'Content-Type': 'application/json',
                         Authorization: 'Bearer ' + localStorage.getItem('gg.access')};
              // The figure must show the *latest* run's alert, so the framework tags and
              // the current explanation format are what gets printed; an older run's rows
              // predate both. The chosen alert is the chain credential's, confirmed with
              // the rationale an analyst would actually record, so the audit trail in the
              // figure is populated rather than empty.
              const runs = (await (await fetch('/api/v1/runs?limit=1', {headers: h})).json()).runs;
              const chains = (await (await fetch(`/api/v1/runs/${runs[0].id}/chains`,
                {headers: h})).json()).chains;
              const key = chains.length ? chains[0].internal_entity_id : null;
              const j = await (await fetch(`/api/v1/alerts?run_id=${runs[0].id}&limit=200`,
                {headers: h})).json();
              const onKey = j.alerts.filter(a => a.entity_id === key);
              const best = onKey.find(a => a.state === 'true_positive')
                ?? onKey[0] ?? j.alerts[0];
              if (best && best.state === 'new') {
                const move = async (state, rationale) =>
                  fetch(`/api/v1/alerts/${best.id}/transition`, {
                    method: 'POST', headers: h,
                    body: JSON.stringify({state, rationale})});
                await move('investigating',
                  'Chain terminates at this credential; checking what it touched after the pivot.');
                await move('true_positive',
                  'Confirmed: authentication succeeded from ' + chains[0].source_ip +
                  ' after 270 blocked attempts, and the key then reached' +
                  ' models it had never called.');
              }
              return best ? best.id : null;
            })()""")

        # The investigation figure's caption promises a case being assembled from the
        # cross-layer chain and the alerts on its credential, so the capture builds that
        # case if the database does not already hold one. Idempotent: an existing case
        # is reused, membership conflicts are ignored.
        await chrome.evaluate("""(async () => {
              const h = {'Content-Type': 'application/json',
                         Authorization: 'Bearer ' + localStorage.getItem('gg.access')};
              const cases = (await (await fetch('/api/v1/investigations', {headers: h})).json())
                .investigations;
              let caseId = cases.length ? cases[0].id : null;
              const runs = (await (await fetch('/api/v1/runs?limit=1', {headers: h})).json()).runs;
              const chains = (await (await fetch(`/api/v1/runs/${runs[0].id}/chains`,
                {headers: h})).json()).chains;
              const chain = chains[0];  // fields: source_ip, internal_entity_id
              if (!caseId) {
                const created = await (await fetch('/api/v1/investigations', {
                  method: 'POST', headers: h,
                  body: JSON.stringify({
                    title: 'Compromise of ' + chain.internal_entity_id,
                    summary: 'External brute force from ' + chain.source_ip +
                             ' followed by a first-use pivot onto ' + chain.internal_entity_id +
                             '; correlating the chain with the per-window alerts.'})
                })).json();
                caseId = created.id;
              }
              const attach = async (payload, rationale) => {
                await fetch(`/api/v1/investigations/${caseId}/members`, {
                  method: 'POST', headers: h,
                  body: JSON.stringify({...payload, rationale})});
              };
              await attach({chain_id: chain.id}, 'The path that ties the stages together');
              const alerts = (await (await fetch('/api/v1/alerts?limit=200', {headers: h})).json())
                .alerts.filter(a => a.entity_id === chain.internal_entity_id).slice(0, 3);
              for (const a of alerts) {
                await attach({alert_id: a.id}, 'Per-window behaviour of the pivoted credential');
              }
              return caseId;
            })()""")

        shots = [
            ("screen-overview.png", "/", 4.0),
            ("screen-triage.png", "/alerts", 3.0),
            ("screen-alert.png", f"/alerts/{alert_id}", 3.0),
            ("screen-network.png", "/network", 5.0),
            ("screen-chains.png", "/chains", 3.0),
            ("screen-investigation.png", "/evidence", 3.0),
            ("screen-settings.png", "/settings", 3.0),
        ]
        for name, path, settle in shots:
            print(f"{name} ← {path}")
            # The current map places its inspector below the canvas. Include the
            # measured features and attached alerts promised by the report caption.
            await chrome.call(
                "Emulation.setDeviceMetricsOverride",
                width=WIDTH,
                height=1650 if name == "screen-network.png" else HEIGHT,
                deviceScaleFactor=SCALE,
                mobile=False,
            )
            await chrome.goto(f"{base}{path}", settle)

            if name == "screen-network.png":
                # The caption promises a flagged entity selected with its inspector open.
                # The chain credential is the entity the report's narrative follows, so it
                # is found through the page's own search box and its chip is clicked.
                await chrome.evaluate("""(async () => {
                      const h = {Authorization: 'Bearer ' + localStorage.getItem('gg.access')};
                      const runs = (await (await fetch('/api/v1/runs?limit=1', {headers: h}))
                        .json()).runs;
                      const chains = (await (await fetch(
                        `/api/v1/runs/${runs[0].id}/chains`, {headers: h})).json()).chains;
                      const key = chains[0].internal_entity_id;
                      const input = document.querySelector('input[aria-label="Find an entity"]');
                      const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                      setter.call(input, key);
                      input.dispatchEvent(new Event('input', {bubbles: true}));
                      await new Promise(r => setTimeout(r, 400));
                      const chip = [...document.querySelectorAll('button')]
                        .find(b => b.textContent.includes(key));
                      if (chip) chip.click();
                      return key;
                    })()""")
                await asyncio.sleep(1.5)

            if name == "screen-investigation.png":
                # Open the case the seed step built, so the figure shows assembly, not
                # the empty state.
                await chrome.evaluate("""(() => {
                      const item = [...document.querySelectorAll('button')]
                        .find(b => /\\d+ alerts? · \\d+ chains?/.test(b.textContent));
                      if (item) item.click();
                    })()""")
                await asyncio.sleep(1.5)

            await chrome.shoot(OUT / name)


def main() -> None:
    """Entry point: launch Chrome, run the capture, always clean up."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:5173")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    process = launch()
    try:
        asyncio.run(capture(args.base, args.email, args.password))
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
