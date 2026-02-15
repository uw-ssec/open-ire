from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from scrapy.http import Request

from open_ire.author import ParsedAuthor
from open_ire.items import AuthorItem
from open_ire.spiders.search import AuthorSearchSpider


class DummyAuthorSearchSpider(AuthorSearchSpider):
    name = "dummy-author-search"

    def build_search_request(self, record: ParsedAuthor) -> Request:
        term = self.author_name_for_query(record)
        return Request(
            f"https://example.test/search?{urlencode({'search': term})}",
            meta={"searched_author": self.canonical_author_name(record)},
        )

    def author_name_for_query(self, record: ParsedAuthor) -> str:
        return record.full_name


async def _collect_outputs(spider: DummyAuthorSearchSpider) -> list[Request | AuthorItem]:
    return [output async for output in spider.start()]


def _search_value(req: Request) -> str:
    return parse_qs(urlparse(req.url).query)["search"][0]


def _make_author_csv(path: Path, authors: list[ParsedAuthor]) -> Path:
    lines = ["Full Name,FirstName,MiddleNames,LastName,Email"]
    for author in authors:
        lines.append(
            f"{author.full_name},{author.first_name},{author.middle_names},{author.last_name},{author.email or ''}"
        )
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def sample_authors() -> list[ParsedAuthor]:
    return [
        ParsedAuthor("Luis Manuel Garcia-Mispireta"),
        ParsedAuthor("E.V.S.S.K. Babu"),
        ParsedAuthor("Ramón H. Rivera-Servera"),
        ParsedAuthor("M. Elena Alvarez-Alvarez"),
        ParsedAuthor("Kemi Adeyemi", email="kadeyemi@uw.edu"),
    ]


class TestAuthorSearchSpider:
    """Tests for AuthorSearchSpider class."""

    def test_no_arguments_raise_error(self) -> None:
        with pytest.raises(ValueError, match="requires either"):
            DummyAuthorSearchSpider()

    @pytest.mark.asyncio
    async def test_start_yields_author_items_then_requests(
        self, tmp_path: Path, sample_authors: list[ParsedAuthor]
    ) -> None:
        csv_author = sample_authors[4]
        name_author = sample_authors[1]
        csv_path = _make_author_csv(tmp_path / "authors.csv", [csv_author])
        spider = DummyAuthorSearchSpider(
            author_csv=str(csv_path), author_name=name_author.full_name
        )

        outputs = await _collect_outputs(spider)
        author_items = [output for output in outputs if isinstance(output, AuthorItem)]
        requests = [output for output in outputs if isinstance(output, Request)]

        assert len(outputs) == 4
        assert isinstance(outputs[0], AuthorItem)
        assert isinstance(outputs[1], AuthorItem)
        assert isinstance(outputs[2], Request)
        assert isinstance(outputs[3], Request)
        assert [item.author for item in author_items] == [csv_author, name_author]
        assert [item.identifiers for item in author_items] == [
            [{"authority": "email", "identifier": csv_author.email}],
            [],
        ]
        assert [_search_value(req) for req in requests] == [
            csv_author.full_name,
            name_author.full_name,
        ]

    @pytest.mark.asyncio
    async def test_start_with_name_only_yields_empty_identifiers(
        self, sample_authors: list[ParsedAuthor]
    ) -> None:
        author = sample_authors[0]
        spider = DummyAuthorSearchSpider(author_name=author.full_name)

        outputs = await _collect_outputs(spider)
        author_items = [output for output in outputs if isinstance(output, AuthorItem)]
        requests = [output for output in outputs if isinstance(output, Request)]

        assert len(author_items) == 1
        assert author_items[0].author == author
        assert author_items[0].identifiers == []
        assert len(requests) == 1

    @pytest.mark.asyncio
    async def test_old_csv_format_still_supported(
        self, tmp_path: Path, sample_authors: list[ParsedAuthor]
    ) -> None:
        """CSVs with only FirstName and LastName columns are still supported."""
        csv_author = sample_authors[0]
        csv_path = tmp_path / "authors.csv"
        csv_path.write_text(
            "Full Name,FirstName,LastName,Email\n"
            f"{csv_author.full_name},{csv_author.first_name},{csv_author.last_name},{csv_author.email}\n"
        )

        spider = DummyAuthorSearchSpider(author_csv=str(csv_path))
        requests: list[Request] = []
        async for output in spider.start():
            if isinstance(output, Request):
                requests.append(output)

        assert _search_value(requests[0]) == f"{csv_author.first_name} {csv_author.last_name}"
