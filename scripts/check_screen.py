"""Drive every control of the decision-support screen and compare it with its data.

    python scripts/check_screen.py [--data web/data/screen_data.json] [--url DEPLOYED_SITE]

The script serves web/ on a free local port, opens it in headless Chromium (Playwright)
and, for every synthetic patient:
    * opens the patient and checks the default decision hour;
    * moves the decision-time slider through every hour and reads the numbers shown
      (risk now, risk after one hour, next-hour chance, intervals, wording, risk drivers)
      against screen_data.json;
    * checks which treatment rows are shown (norepinephrine hidden while a vasopressor
      runs, the 65 trial only for patients aged 65 or older) and that inputs never come
      from after the decision hour;
    * presses the one-hour step buttons at both ends;
    * selects every treatment option and checks the 2x2 header and status chips;
    * hovers over the course chart and checks the tooltip appears and disappears.
It also checks that each patient keeps its own decision hour, that no console error
occurs, and that the page never scrolls sideways at phone width.

Needs `pip install playwright` and `python -m playwright install chromium`.
Exit code 1 lists the failed checks.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import math
import re
import socketserver
import threading
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class Checks:
    def __init__(self) -> None:
        self.count = 0
        self.failures: list[str] = []

    def expect(self, condition: bool, message: str) -> None:
        self.count += 1
        if not condition:
            self.failures.append(message)


# The screen's own formatting rules, written again here so the check is independent.
def _round(value: float) -> int:
    return math.floor(value + 0.5)  # JavaScript Math.round


def percent(probability: float) -> str:
    return ("<1" if probability < 0.005 else str(_round(probability * 100))) + "%"


def interval(pair: list[float]) -> str:
    def number(value: float) -> str:
        return "<1" if value < 0.005 else str(_round(value * 100))

    return f"{number(pair[0])}–{number(pair[1])}%"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *arguments):  # noqa: A002 - keep the output to the results
        pass


def serve(directory: Path) -> tuple[socketserver.TCPServer, str]:
    handler = functools.partial(QuietHandler, directory=str(directory))
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/index.html"


def set_hour(page, hour: float) -> None:
    page.evaluate(
        "(h) => { const s = document.querySelector('#hour'); s.value = String(h);"
        " s.dispatchEvent(new Event('input', {bubbles: true})); }",
        int(hour),
    )


def check_hour(page, checks: Checks, patient: dict, hour: float) -> None:
    score = next(entry for entry in patient["scores"] if entry["hour"] == hour)
    where = f"{patient['label']} h{hour:g}"
    checks.expect(page.inner_text("#figure-now") == percent(score["risk"]), f"{where}: risk now")
    checks.expect(
        page.inner_text("#figure-wait") == percent(score["after_hour"]), f"{where}: risk after 1 h"
    )
    next_hour = "less than 1%" if score["next_hour"] < 0.005 else percent(score["next_hour"])
    checks.expect(page.inner_text("#figure-next-hour") == next_hour, f"{where}: next-hour chance")
    readout = page.inner_text("#readout")
    checks.expect(interval(score["risk_interval"]) in readout, f"{where}: risk interval")
    checks.expect(interval(score["after_hour_interval"]) in readout, f"{where}: later interval")
    title = page.inner_text("#answer-title")
    if score["population"] == "pre_shock":
        checks.expect(title.startswith("Chance of shock or death"), f"{where}: title '{title}'")
        checks.expect("next stage, shock or death" in readout, f"{where}: pre-shock wording")
    else:
        checks.expect(title.startswith("Chance of death"), f"{where}: title '{title}'")
        checks.expect("of dying within" in readout, f"{where}: shock-stage wording")
    options = page.eval_on_selector_all(
        "#evidence [data-option]", "elements => elements.map(e => e.dataset.option)"
    )
    checks.expect(
        ("Norepinephrine" in options) == (not score["on_vasopressor"]),
        f"{where}: norepinephrine row while on_vasopressor={score['on_vasopressor']}",
    )
    shows_65_trial = "Patients aged 65 or older" in page.inner_text("#evidence")
    checks.expect(
        shows_65_trial == (patient["age"] >= 65 and not score["on_vasopressor"]),
        f"{where}: 65 trial shown={shows_65_trial} for age {patient['age']}",
    )
    drivers = page.eval_on_selector_all(
        ".driver__name", "elements => elements.map(e => e.textContent)"
    )
    checks.expect(
        drivers == [driver["description"] for driver in score["drivers"]], f"{where}: drivers"
    )
    checks.expect(
        page.inner_text("#hour-readout").startswith(f"h {hour:g}"), f"{where}: clock readout"
    )
    for cells in page.eval_on_selector_all(
        "#inputs tbody tr", "rows => rows.map(r => [...r.children].map(c => c.textContent))"
    ):
        taken = cells[2]
        if taken != "not measured":
            hours_ago = float(re.sub(r"[^0-9.\-]", "", taken.split("h")[0].replace("−", "-")))
            checks.expect(hours_ago >= 0, f"{where}: {cells[0]} recorded after the decision hour")


def check_patient(page, checks: Checks, index: int, patient: dict) -> None:
    page.click(f'[data-bed="{index}"]')
    checks.expect(
        page.get_attribute(f'[data-bed="{index}"]', "aria-pressed") == "true",
        f"{patient['label']}: tab not marked as selected",
    )
    hours = [entry["hour"] for entry in patient["scores"]]
    checks.expect(
        page.input_value("#hour") == f"{patient['default_now_hour']:g}",
        f"{patient['label']}: default decision hour",
    )
    for hour in hours:
        set_hour(page, hour)
        check_hour(page, checks, patient, hour)

    set_hour(page, hours[0])
    checks.expect(page.is_disabled("#hour-back"), f"{patient['label']}: -1 h enabled at start")
    page.click("#hour-forward")
    checks.expect(page.input_value("#hour") == f"{hours[0] + 1:g}", f"{patient['label']}: +1 h")
    page.click("#hour-back")
    checks.expect(page.input_value("#hour") == f"{hours[0]:g}", f"{patient['label']}: -1 h")
    set_hour(page, hours[-1])
    checks.expect(page.is_disabled("#hour-forward"), f"{patient['label']}: +1 h enabled at end")

    set_hour(page, patient["default_now_hour"])
    for option in page.eval_on_selector_all(
        "#evidence input[name=option]", "inputs => inputs.map(i => i.value)"
    ):
        page.check(f'#evidence input[value="{option}"]')
        header = page.inner_text(".answer__colhead--treatment h3")
        expected = option if option.startswith("IV") else option[0].lower() + option[1:]
        checks.expect(header == f"Start {expected}", f"{patient['label']}: header for {option}")
        chips = page.eval_on_selector_all(
            ".answer__cell--none .status", "chips => chips.map(c => c.textContent.trim())"
        )
        if option == "Antibiotics":
            wanted = ["Already started"] * 2
        elif "+" in option:
            wanted = ["Not estimable"] * 2
        else:
            wanted = ["External evidence only"] * 2
        checks.expect(chips == wanted, f"{patient['label']}: status for {option}: {chips}")
    page.click('#evidence [data-option="IV fluids"] .ev-list >> nth=0')
    checks.expect(
        "IV fluids" in page.inner_text(".answer__colhead--treatment h3"),
        f"{patient['label']}: clicking a row does not select it",
    )

    page.locator("#course-chart").scroll_into_view_if_needed()
    box = page.locator("#hover-area").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + 40)
    checks.expect(
        page.is_visible("#course-tooltip") and page.inner_text("#course-tooltip").startswith("h "),
        f"{patient['label']}: chart tooltip",
    )
    page.mouse.move(2, 2)
    checks.expect(not page.is_visible("#course-tooltip"), f"{patient['label']}: tooltip stays")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, default=REPOSITORY_ROOT / "web/data/screen_data.json")
    parser.add_argument(
        "--url",
        help="check a deployed copy (for example the GitHub Pages site) instead of web/ served locally",
    )
    arguments = parser.parse_args()
    data = json.loads(arguments.data.read_text(encoding="utf-8"))
    checks = Checks()
    console_errors: list[str] = []

    if arguments.url:
        server = None
        url = arguments.url if arguments.url.endswith("/") else arguments.url + "/"
        # The deployed data must be the same file as the one the checks read.
        with urllib.request.urlopen(url + "data/screen_data.json", timeout=30) as response:
            deployed = response.read()
        checks.expect(
            hashlib.sha256(deployed).hexdigest()
            == hashlib.sha256(arguments.data.read_bytes()).hexdigest(),
            "the deployed screen_data.json differs from the local one",
        )
    else:
        server, url = serve(REPOSITORY_ROOT / "web")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on(
                "console",
                lambda message: (
                    console_errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on("pageerror", lambda error: console_errors.append(str(error)))
            page.goto(url)
            page.wait_for_selector("#figure-now")
            for index, patient in enumerate(data["patients"]):
                check_patient(page, checks, index, patient)
            page.click('[data-bed="0"]')
            set_hour(page, 5)
            page.click('[data-bed="1"]')
            page.click('[data-bed="0"]')
            checks.expect(page.input_value("#hour") == "5", "a patient lost its decision hour")

            phone = browser.new_page(viewport={"width": 400, "height": 860})
            phone.goto(url)
            phone.wait_for_selector("#figure-now")
            widths = phone.evaluate(
                "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
            )
            checks.expect(widths[0] <= widths[1], f"page scrolls sideways at 400 px: {widths}")
            browser.close()
    finally:
        if server is not None:
            server.shutdown()

    checks.expect(not console_errors, f"console errors: {console_errors[:3]}")
    print(f"{checks.count} checks, {len(checks.failures)} failed")
    for failure in checks.failures[:50]:
        print(f"FAILED {failure}")
    return 1 if checks.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
