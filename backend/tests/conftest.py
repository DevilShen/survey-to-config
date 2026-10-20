"""测试夹具：用独立的临时数据库，避免污染演示数据。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DB = ROOT / "data" / "test.db"

# 必须在导入 app.db 之前设置，DB_PATH 在模块导入时就确定了
os.environ["SDLC_DB"] = str(TEST_DB)
os.environ["SDLC_RESET"] = "1"
# 单元测试聚焦业务逻辑，网关默认关闭；JWT 本身由 test_auth_jwt.py 显式打开验证
os.environ["SDLC_AUTH"] = "off"

sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.db import connect, init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    if TEST_DB.exists():
        TEST_DB.unlink()
    init_db(reset=True)
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture()
def conn():
    c = connect()
    try:
        yield c
    finally:
        c.close()
