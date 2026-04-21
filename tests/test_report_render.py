from pathlib import Path

from agents.common.rendering.report_render import render_markdown_to_pdf


class _FakePage:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.goto_calls: list[dict[str, object]] = []
        self.wait_calls: list[dict[str, object]] = []
        self.evaluate_calls: list[tuple[object, object]] = []

    def goto(self, url: str, wait_until: str, timeout: int) -> None:
        self.goto_calls.append({"url": url, "wait_until": wait_until, "timeout": timeout})

    def wait_for_function(self, expression: str, timeout: int) -> None:
        self.wait_calls.append({"expression": expression, "timeout": timeout})

    def evaluate(self, script, arg=None):
        self.evaluate_calls.append((script, arg))
        return None

    def pdf(self, **kwargs) -> None:
        Path(kwargs["path"]).write_bytes(b"%PDF-1.4\n")


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.closed = False

    def new_page(self) -> _FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser

    def launch(self, headless: bool):
        return self.browser


class _FakePlaywright:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = _FakeChromium(browser)


class _FakePlaywrightContext:
    def __init__(self, playwright: _FakePlaywright) -> None:
        self.playwright = playwright

    def __enter__(self) -> _FakePlaywright:
        return self.playwright

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_render_markdown_to_pdf_bounds_mathjax_wait(monkeypatch, tmp_path: Path) -> None:
    output_path = tmp_path / "report.pdf"
    page = _FakePage(output_path)
    browser = _FakeBrowser(page)

    monkeypatch.setattr(
        "agents.common.rendering.report_render.sync_playwright",
        lambda: _FakePlaywrightContext(_FakePlaywright(browser)),
    )

    render_markdown_to_pdf(
        md_text="公式: $$x+y$$",
        output_path=output_path,
        enable_mathjax=True,
        wait_timeout_ms=4321,
    )

    assert output_path.exists()
    assert page.goto_calls[0]["wait_until"] == "domcontentloaded"
    assert page.goto_calls[0]["timeout"] == 4321
    assert page.wait_calls[0]["timeout"] == 4321
    script, arg = page.evaluate_calls[0]
    assert "Promise.race" in str(script)
    assert arg == 4321
