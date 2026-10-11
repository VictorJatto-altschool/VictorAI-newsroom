from victor.ai.base import clean_post_text


def test_paragraph_split_keeps_abbreviations_together():
    one_paragraph = ("JUST IN 🚨 NASA will host a media teleconference at 4 p.m. EDT Thursday to preview the MMX mission. "
                     "U.S. labs and Dr. Rivera are taking part, e.g. on the instrument side. The launch is set for Oct 19.")
    out = clean_post_text(one_paragraph)
    assert "4 p.m. EDT Thursday" in out and "U.S. labs" in out and "Dr. Rivera" in out and "e.g. on" in out
    assert out.count("\n\n") == 2  # three real sentences, three paragraphs
