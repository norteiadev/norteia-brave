"""Project rule: no crawl/fetch call carries the company name (User-Agent defaults)."""

from brave.clients.editorial_http import _USER_AGENT
from brave.config.settings import ImagesConfig, NominatimConfig


def test_no_default_crawl_identity_names_the_company(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_NOMINATIM_USER_AGENT", raising=False)
    monkeypatch.delenv("BRAVE_IMAGES_USER_AGENT", raising=False)
    for ua in (_USER_AGENT, NominatimConfig().user_agent, ImagesConfig().user_agent):
        assert ua and "norteia" not in ua.lower(), ua
