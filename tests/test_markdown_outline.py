from src.services.markdown_outline import MarkdownOutlineParser, OutlineBlock, PageDocument


def test_parse_nested_blocks_and_ids():
    text = """id:: page-1
title:: Test Page
tags:: alpha, beta

- Parent
  id:: block-1
  - Child id:: block-2
"""
    page = MarkdownOutlineParser().parse(text)

    assert page.id == "page-1"
    assert page.title == "Test Page"
    assert page.tags == ["alpha", "beta"]
    assert page.blocks[0].id == "block-1"
    assert page.blocks[0].children[0].id == "block-2"


def test_ensure_ids_and_round_trip():
    parser = MarkdownOutlineParser()
    page = PageDocument(title="Round Trip", blocks=[
        OutlineBlock(content="A", children=[OutlineBlock(content="B")])
    ])

    assert parser.ensure_ids(page) is True
    rendered = parser.serialize(page)
    reparsed = parser.parse(rendered)

    assert reparsed.id == page.id
    assert reparsed.blocks[0].id == page.blocks[0].id
    assert reparsed.blocks[0].children[0].content == "B"
