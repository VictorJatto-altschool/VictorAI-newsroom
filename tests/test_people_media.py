from victor.media.extract import find_media
from victor.media.fetch import person_in, prepare_media


def test_person_detection():
    assert person_in("Elon Musk receives the National Medal of Science") == "Elon Musk"
    assert person_in("Altman says GPT-6 ships next week") == "Sam Altman"
    assert person_in("NVIDIA ships a new GPU") is None
    assert person_in("Why it matters: the fan base is small") is None  # 'Ma' and 'Fan' must not match inside other words
    assert person_in("Jack Ma returns to Alibaba") == "Jack Ma"


def test_whitehouse_images_are_public_domain():
    html = '<img src="https://www.whitehouse.gov/wp-content/uploads/2026/10/medal.jpg">'
    assert find_media(html, "", "whitehouse.gov")[0]["rights"] == "public_domain"


def test_leader_story_gets_licensed_photo_with_attribution(tmp_path, monkeypatch):
    monkeypatch.setattr("victor.media.fetch.MEDIA_DIR", tmp_path)
    fake_hit = {"type": "image", "url": "https://upload.wikimedia.org/x.jpg", "rights": "cc", "license": "CC BY-SA 4.0",
                "artist": "Steve Jurvetson", "attribution": "Photo: Steve Jurvetson, CC BY-SA 4.0, via Wikimedia Commons"}
    monkeypatch.setattr("victor.media.fetch.download_licensed", lambda url, name, client=None: tmp_path / "p.jpg")
    (tmp_path / "p.jpg").write_bytes(b"jpg")
    m = prepare_media({"mode": "render"}, "Elon Musk receives the National Medal of Science", "Hook\n\nFact.", "leaders", "@x",
                      want_clip=False, lookup_person=lambda name: fake_hit if name == "Elon Musk" else None)
    assert m["mode"] == "upload" and m["rights"] == "cc" and m["attribution"].startswith("Photo: Steve Jurvetson")


def test_unlicensed_lookup_falls_back_to_rendered_card(tmp_path, monkeypatch):
    monkeypatch.setattr("victor.media.fetch.MEDIA_DIR", tmp_path)
    m = prepare_media({"mode": "render"}, "Elon Musk receives a medal", "Hook\n\nFact.", "leaders", "@x",
                      want_clip=False, lookup_person=lambda name: None)
    assert m["mode"] == "render" and m["rights"] == "original"
