"""media_fields: the photo's source follows where its file page lives."""

from types import SimpleNamespace

from brave.tasks.pipeline import media_fields


def _fields(source_url: str) -> dict[str, str]:
    row = SimpleNamespace(entity_type="destination", canonical_key="ibge:AL:2704302", role="gallery",
                          rank=1, wikidata_qid=None)
    img = SimpleNamespace(sha1="a" * 40, author="Marco Ankosqui", license="Public Domain Mark 1.0",
                          license_url=None, credit_line="c", source_url=source_url)
    return media_fields(row, img)


def test_commons_file_is_wikimedia_commons() -> None:
    assert _fields("https://commons.wikimedia.org/wiki/File:X.jpg")["source"] == "wikimedia_commons"


def test_flickr_photo_is_flickr() -> None:
    assert _fields("https://www.flickr.com/photos/mturdestinos/39955568485/")["source"] == "flickr"
