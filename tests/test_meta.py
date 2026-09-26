"""Reading a program's own URLs back out of its packaging metadata."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

from devicectl import meta


class FakeMetadata:
    """As much of an ``importlib.metadata`` message as this module reads."""

    def __init__(self, urls: list[str] | None) -> None:
        self.urls = urls

    def get_all(self, name: str) -> list[str] | None:
        assert name == "Project-URL"
        return self.urls


def test_the_urls_come_back_under_their_labels(monkeypatch) -> None:
    monkeypatch.setattr(
        meta,
        "metadata",
        lambda _: FakeMetadata(
            [
                "Homepage, https://example.invalid/widgetctl",
                "Releases, https://example.invalid/widgetctl/releases",
            ]
        ),
    )
    assert meta.project_links("widgetctl") == {
        "homepage": "https://example.invalid/widgetctl",
        "releases": "https://example.invalid/widgetctl/releases",
    }


def test_a_label_is_matched_however_it_was_spelled(monkeypatch) -> None:
    # Nothing enforces how these are written, and the ones PyPI recognises are
    # spelled several ways each.
    monkeypatch.setattr(
        meta,
        "metadata",
        lambda _: FakeMetadata(["  Bug Tracker , https://example.invalid/bugs "]),
    )
    assert meta.project_links("widgetctl") == {
        "bugtracker": "https://example.invalid/bugs"
    }


def test_the_first_of_a_repeated_label_wins(monkeypatch) -> None:
    monkeypatch.setattr(
        meta,
        "metadata",
        lambda _: FakeMetadata(
            ["Homepage, https://first.invalid", "homepage, https://second.invalid"]
        ),
    )
    assert meta.project_links("widgetctl") == {"homepage": "https://first.invalid"}


def test_a_label_with_no_url_is_dropped(monkeypatch) -> None:
    monkeypatch.setattr(
        meta, "metadata", lambda _: FakeMetadata(["Homepage,   ", "Issues, https://i"])
    )
    assert meta.project_links("widgetctl") == {"issues": "https://i"}


def test_a_distribution_that_names_none_gives_none(monkeypatch) -> None:
    monkeypatch.setattr(meta, "metadata", lambda _: FakeMetadata(None))
    assert meta.project_links("widgetctl") == {}


def test_an_uninstalled_program_is_not_an_error(monkeypatch) -> None:
    # A checkout nobody installed has no metadata at all, which is a wordmark
    # that does not click rather than a page that will not draw.
    def missing(_: str) -> FakeMetadata:
        raise PackageNotFoundError("widgetctl")

    monkeypatch.setattr(meta, "metadata", missing)
    assert meta.project_links("widgetctl") == {}


def test_this_package_names_its_own() -> None:
    # Not a fake: the real `[project.urls]`, which is what both programs read.
    links = meta.project_links("devicectl-core")
    assert links["homepage"].startswith("https://")
    assert links["license"].endswith("/LICENSE")
    assert links["releases"].endswith("/releases")
