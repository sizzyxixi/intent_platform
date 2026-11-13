import json
import os
from pathlib import Path

os.environ.setdefault("IP_DATABASE_URL", "postgresql+psycopg://intent:intent@127.0.0.1:5432/intent_test")
os.environ["IP_ENV"] = "test"
os.environ["IP_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["IP_SECRET_KEY"] = "test-secret-key-0123456789abcdef-xyz"
os.environ["IP_ADMIN_PASSWORD"] = "admin123456"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, engine, reset_engine  # noqa: E402
from app import seed as seed_mod  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
SEED = json.loads((BACKEND / "seed" / "demo_state.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session", autouse=True)
def _schema():
    get_settings.cache_clear()
    reset_engine()
    with engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture()
def tokens():
    with SessionLocal() as db:
        seed_mod.reset(db)
        toks = seed_mod.seed(db, SEED)
        seed_mod.ensure_admin(db)
    return toks


@pytest.fixture()
def client(tokens):
    from app.main import app
    with TestClient(app) as c:
        yield c


class Api:
    """开放接口调用助手：自动带 Key、空间与幂等键。"""

    def __init__(self, client, token):
        self.c, self.token, self.n = client, token, 0

    def h(self, space=None, idem=True, **extra):
        h = {"Authorization": f"Bearer {self.token}"}
        if space:
            h["X-Space-Id"] = space
        if idem:
            self.n += 1
            h["Idempotency-Key"] = f"t-{id(self)}-{self.n}"
        h.update(extra)
        return h

    def get(self, url, space=None, **kw):
        return self.c.get(url, headers=self.h(space, idem=False), **kw)

    def post(self, url, json=None, space=None, idem=True, **kw):
        return self.c.post(url, json=json, headers=self.h(space, idem), **kw)

    def put(self, url, json=None, space=None, idem=True, **kw):
        return self.c.put(url, json=json, headers=self.h(space, idem), **kw)

    def patch(self, url, json=None, space=None, idem=True, **kw):
        return self.c.patch(url, json=json, headers=self.h(space, idem), **kw)

    def delete(self, url, space=None, idem=True, **kw):
        return self.c.delete(url, headers=self.h(space, idem), **kw)


@pytest.fixture()
def linxia(client, tokens):  # 林夏的 AI 助手：cs，意图读写 + 知识读写
    return Api(client, tokens["ak_7f3a"])


@pytest.fixture()
def bot(client, tokens):  # 客服 Bot：cs，意图读 + 知识读 + 工具读
    return Api(client, tokens["ak_91c2"])


@pytest.fixture()
def mcbot(client, tokens):
    return Api(client, tokens["ak_mc01"])


@pytest.fixture()
def full_key(client):
    """管理员新建一个绑定两个空间、拥有全部权限的 Key。"""
    from app.services import keys
    from app.services.common import SYSTEM
    with SessionLocal() as db:
        _, tok = keys.create(db, {"name": "测试全权限", "spaces": ["cs", "mc"],
                                  "scopes": ["意图:读", "意图:写", "知识:读", "知识:写", "工具:读", "工具:写"]}, SYSTEM)
        db.commit()
    return Api(client, tok)


def login(client, username="admin", password="admin123456"):
    r = client.post("/api/admin/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r


@pytest.fixture()
def admin(client):
    login(client)
    return client
