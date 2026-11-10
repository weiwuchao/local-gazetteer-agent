"""SearchScope：不可变与类型校验。"""

from __future__ import annotations

import dataclasses

import pytest

from app.rag.scope import SearchScope


def test_scope_keeps_tuple_and_is_frozen() -> None:
    scope = SearchScope(
        region_id="rg_1",
        visible_doc_ids=("doc_1", "doc_2"),
        index_version="idx__bge__1024__c800__v1",
    )
    assert scope.visible_doc_ids == ("doc_1", "doc_2")

    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.region_id = "rg_2"  # type: ignore[misc]


def test_scope_rejects_list() -> None:
    with pytest.raises(TypeError, match="tuple"):
        SearchScope(
            region_id="rg_1",
            visible_doc_ids=["doc_1"],  # type: ignore[arg-type]
            index_version="idx",
        )


@pytest.mark.parametrize(
    ("region_id", "index_version"),
    [("", "idx"), ("rg_1", ""), ("   ", "idx"), ("rg_1", "   ")],
)
def test_scope_rejects_blank_region_or_index(region_id: str, index_version: str) -> None:
    with pytest.raises(ValueError):
        SearchScope(
            region_id=region_id,
            visible_doc_ids=("doc_1",),
            index_version=index_version,
        )


def test_scope_rejects_non_string_doc_id() -> None:
    with pytest.raises(ValueError):
        SearchScope(
            region_id="rg_1",
            visible_doc_ids=("doc_1", 2),  # type: ignore[arg-type]
            index_version="idx",
        )
