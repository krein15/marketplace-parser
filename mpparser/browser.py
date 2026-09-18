"""A real Chrome/Edge controlled through Patchright.

Both marketplaces protect their internal APIs with anti-bot tokens bound to the browser fingerprint,
so requests to protected endpoints are sent with ``fetch`` from inside an opened site page.
Unprotected endpoints (CDN, reviews) go through the context's request client, which shares cookies
but is not subject to CORS.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from patchright.async_api import BrowserContext, Page, Playwright, async_playwright

log = logging.getLogger(__name__)

CHANNELS = ("chrome", "msedge")

_FETCH_JS = """
async ([url, headers, timeoutMs]) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(url, {headers, credentials: 'include', signal: controller.signal});
        return {status: response.status, text: await response.text()};
    } catch (error) {
        return {status: 0, text: String(error)};
    } finally {
        clearTimeout(timer);
    }
}
"""


class BrowserError(RuntimeError):
    pass


class Browser:
    def __init__(self, profile_dir: Path, headless: bool = True) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> Browser:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise BrowserError("Браузер не запущен")
        return self._context

    async def start(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._launch(user_agent=None)
        if self.headless:
            # Headless Chrome announces itself as "HeadlessChrome", which WB refuses to serve.
            page = await self._context.new_page()
            user_agent = await page.evaluate("navigator.userAgent")
            await self._context.close()
            self._context = await self._launch(user_agent=user_agent.replace("HeadlessChrome", "Chrome"))

    async def _launch(self, user_agent: str | None) -> BrowserContext:
        assert self._playwright is not None
        errors = []
        for channel in CHANNELS:
            try:
                context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    channel=channel,
                    headless=self.headless,
                    no_viewport=True,
                    user_agent=user_agent,
                    locale="ru-RU",
                    args=["--window-size=1280,900"],
                )
            except Exception as exc:  # Playwright reports a missing browser as a plain Error
                errors.append(f"{channel}: {exc}")
                continue
            log.info("Browser started: %s, headless=%s", channel, self.headless)
            return context
        details = "\n".join(errors)
        if "ProcessSingleton" in details or "user data directory is already in use" in details:
            raise BrowserError("Профиль браузера занят: похоже, парсер уже запущен в другом окне.")
        raise BrowserError(
            "Не удалось запустить Google Chrome или Microsoft Edge. Установите один из браузеров.\n" + details
        )

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:  # closing a crashed browser must not mask the original error
            log.debug("Browser context close failed", exc_info=True)
        finally:
            self._context = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None

    async def new_page(self) -> Page:
        return await self.context.new_page()

    async def fetch(
        self, page: Page, url: str, headers: dict[str, str] | None = None, timeout: float = 30
    ) -> tuple[int, str]:
        """Run fetch() inside ``page`` so the request carries the site's cookies and fingerprint."""
        for attempt in range(3):
            try:
                result = await page.evaluate(_FETCH_JS, [url, headers or {}, int(timeout * 1000)])
                return result["status"], result["text"]
            except Exception as exc:
                # The site may reload the page (e.g. after an anti-bot check) and destroy the JS context.
                if "Execution context was destroyed" not in str(exc) or attempt == 2:
                    raise
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)
        raise AssertionError("unreachable")

    async def get_json(self, url: str, timeout: float = 30) -> tuple[int, Any]:
        """GET an unprotected endpoint outside of any page. Returns (status, parsed JSON or None)."""
        try:
            response = await self.context.request.get(url, timeout=timeout * 1000)
        except Exception as exc:
            log.warning("Request failed %s: %s", url, exc)
            return 0, None
        if not response.ok:
            return response.status, None
        try:
            return response.status, json.loads(await response.text())
        except ValueError:
            return response.status, None
