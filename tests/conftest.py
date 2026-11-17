import pytest
from lc.client import ROOT


@pytest.fixture(scope="session", autouse=True)
def report_directory() -> None:
    (ROOT / "tests/reports").mkdir(parents=True, exist_ok=True)
