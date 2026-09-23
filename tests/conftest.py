import pytest_asyncio

from harmony.memory.db import connect
from harmony.memory.store import Store


@pytest_asyncio.fixture
async def store(tmp_path):
    db = await connect(str(tmp_path / "test.db"))
    yield Store(db)
    await db.close()
