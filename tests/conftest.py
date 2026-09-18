from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from sundial_llm.models import LifecycleRecord

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def load_fixture() -> Callable[[str], str]:
    def _load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
def index_records() -> Callable[[Iterable[LifecycleRecord]], dict[tuple[str, str], LifecycleRecord]]:
    def _index(records: Iterable[LifecycleRecord]) -> dict[tuple[str, str], LifecycleRecord]:
        return {(r.model_id, r.version or ""): r for r in records}

    return _index
