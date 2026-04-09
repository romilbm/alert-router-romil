import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.state import app_state


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_state():
    """Reset all in-memory state before and after every test."""
    app_state.reset()
    yield
    app_state.reset()
