from pathlib import Path

from sqlalchemy import create_engine, text

from wikipeople.replica import LengthCursor, ReplicaClient

# A slice of the real shape: two pages tie on length, and one redirect sits at a size
# that would otherwise put it near the top of the walk.
ROWS = [
    (1, 1001, 90000, 0),
    (2, 1002, 50000, 0),
    (3, 1003, 50000, 0),
    (4, 1004, 70000, 1),
    (5, 1005, 100, 0),
]


def build_client(tmp_path: Path, wiki: str = "frwiki") -> ReplicaClient:
    engine = create_engine(f"sqlite:///{tmp_path / 'replica.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE page (page_id INTEGER, page_latest INTEGER,"
                " page_len INTEGER, page_is_redirect INTEGER, page_namespace INTEGER)"
            )
        )
        for page_id, latest, length, redirect in ROWS:
            connection.execute(
                text("INSERT INTO page VALUES (:i, :l, :n, :r, 0)"),
                {"i": page_id, "l": latest, "n": length, "r": redirect},
            )
    client = ReplicaClient("user", "password")
    client._engines[wiki] = engine
    return client


def test_pages_come_back_heaviest_first_and_without_redirects(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    pages, cursor = client.pages_by_descending_length("frwiki", None, 10)

    assert [page.length for page in pages] == [90000, 50000, 50000, 100]
    assert 70000 not in [page.length for page in pages]
    assert pages[0].revision_id == 1001
    assert cursor is None
    client.close()


def test_a_full_batch_resumes_where_it_stopped_even_inside_a_tie(tmp_path: Path) -> None:
    """The tie is the reason the cursor carries a page id at all.

    Two pages share 50000 bytes and a batch boundary falls between them. A cursor of
    length alone would either re-serve the first or skip the second, and on frwiki
    tens of thousands of pages share a length, so the error would not be a rounding
    one.
    """
    client = build_client(tmp_path)

    first, cursor = client.pages_by_descending_length("frwiki", None, 2)
    assert [page.page_id for page in first] == [1, 3]
    assert cursor == LengthCursor(length=50000, page_id=3)

    second, _ = client.pages_by_descending_length("frwiki", cursor, 2)
    assert [page.page_id for page in second] == [2, 5]
    client.close()


def test_a_short_batch_reports_the_wiki_exhausted(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    _, cursor = client.pages_by_descending_length("frwiki", LengthCursor(50000, 2), 10)

    assert cursor is None
    client.close()


def test_an_unreadable_cursor_restarts_rather_than_crashing_the_job() -> None:
    """The stored cursor used to be a page title, and may still be one.

    The hourly job reads it before it can do anything useful, so a parse error there
    stops the backfill for good rather than for one run. Restarting from the heaviest
    page costs one wasted batch, because `enqueue_if_stale` drops what is already cached.
    """
    assert LengthCursor.decode("(361)_Bononia") is None
    assert LengthCursor.decode("") is None
    assert LengthCursor.decode(None) is None
    assert LengthCursor.decode("50000:3") == LengthCursor(50000, 3)
    assert LengthCursor(50000, 3).encode() == "50000:3"


def test_no_replica_credentials_means_no_replica() -> None:
    assert ReplicaClient("", "").available() is False
    assert ReplicaClient("user", "password").available() is True
