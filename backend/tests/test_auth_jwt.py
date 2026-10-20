"""JWT 登录专项：签发 / 校验 / 过期 / 篡改 / 网关拦截。

这组用例显式把 SDLC_AUTH 打开（其余测试默认关闭），
因为它验的就是「没登录进不来」这件事本身。
"""
import time

import pytest
from fastapi.testclient import TestClient

from app import security
from app.db import DEMO_LOGIN, DEMO_PASSWORD
from app.main import app


@pytest.fixture()
def guard_on(monkeypatch):
    monkeypatch.setenv("SDLC_AUTH", "on")
    yield


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, username=DEMO_LOGIN, password=DEMO_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ---------------------------------------------------------------- 登录

def test_demo账号能登录且拿到标准JWT(client):
    r = _login(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] > 0
    assert body["user"]["login_name"] == DEMO_LOGIN
    assert body["user"]["role"] == "admin"
    assert "password_hash" not in body["user"]          # 响应里绝不回传哈希

    # 标准 JWT：三段 base64url
    token = body["token"]
    assert token.count(".") == 2
    payload = security.verify(token)
    assert payload["sub"] == body["user"]["id"]
    assert payload["role"] == "admin"
    assert payload["exp"] - payload["iat"] == body["expires_in"]


def test_用邮箱也能登录(client):
    r = client.post("/api/auth/login",
                    json={"username": "zhoumin@demo.cn", "password": "demo1234"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "pm"


def test_密码错误返回401且不泄露账号是否存在(client):
    bad_pw = _login(client, password="wrong-password")
    no_user = _login(client, username="nobody@nowhere.cn", password="demo1234")
    assert bad_pw.status_code == 401 and no_user.status_code == 401
    # 两种失败的提示必须一模一样，否则等于给爆破者送账号枚举
    assert bad_pw.json()["detail"] == no_user.json()["detail"]


def test_不带密码直接拒绝(client):
    r = client.post("/api/auth/login", json={"username": DEMO_LOGIN})
    assert r.status_code == 422
    assert "密码" in r.json()["detail"]


def test_演示账号提示接口(client):
    r = client.get("/api/auth/demo")
    assert r.status_code == 200
    d = r.json()
    assert d["username"] == DEMO_LOGIN and d["password"] == DEMO_PASSWORD


# ---------------------------------------------------------------- me / 网关

def test_me需要token(client, guard_on):
    assert client.get("/api/auth/me").status_code == 401
    token = _login(client).json()["token"]
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["login_name"] == DEMO_LOGIN


def test_网关拦住未登录的业务接口(client, guard_on):
    r = client.get("/api/bootstrap")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"

    token = _login(client).json()["token"]
    ok = client.get("/api/bootstrap", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200


def test_登录接口本身不需要token(client, guard_on):
    assert _login(client).status_code == 200
    assert client.get("/api/auth/demo").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200


# ---------------------------------------------------------------- 防篡改 / 过期

def test_篡改载荷会被签名拦下(client, guard_on):
    token = _login(client).json()["token"]
    head, part, sig = token.split(".")
    # 把角色改成 admin 的伪造方式（demo 本来就是 admin，这里改 sub 更能说明问题）
    forged = f"{head}.{part}.{sig[:-4]}AAAA"
    assert client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {forged}"}).status_code == 401

    # 换 payload 但沿用旧签名
    import json
    body = json.loads(security._b64d(part))
    body["sub"] = "U-999"
    tampered = ".".join([head, security._b64e(
        json.dumps(body, separators=(",", ":")).encode()), sig])
    assert client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {tampered}"}).status_code == 401


def test_过期token被拒(client, guard_on):
    expired = security.sign({"sub": "U-DEMO", "role": "admin",
                             "iat": int(time.time()) - 100, "exp": int(time.time()) - 10})
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert "过期" in r.json()["detail"]


def test_乱码token被拒(client, guard_on):
    for bad in ("", "abc", "a.b.c", "Bearer"):
        h = {"Authorization": f"Bearer {bad}"} if bad else {}
        assert client.get("/api/auth/me", headers=h).status_code == 401


def test_token重签后其他密钥无效(client):
    """换掉密钥，旧 token 立刻失效（模拟密钥泄露后的紧急轮换）。"""
    token = _login(client).json()["token"]
    old = security._secret()
    import os
    os.environ["SDLC_JWT_SECRET"] = "rotated-secret-for-test"
    try:
        with pytest.raises(security.JWTError):
            security.verify(token)
    finally:
        os.environ.pop("SDLC_JWT_SECRET", None)
        assert security._secret() == old


# ---------------------------------------------------------------- 密码存储

def test_密码不落明文且盐随机():
    a = security.hash_password("demo1234")
    b = security.hash_password("demo1234")
    assert a != b                                   # 同密码不同盐 → 不同哈希
    assert "demo1234" not in a
    assert a.startswith("pbkdf2_sha256$")
    assert security.verify_password("demo1234", a)
    assert security.verify_password("demo1234", b)
    assert not security.verify_password("Demo1234", a)
    assert not security.verify_password("demo1234", None)
    assert not security.verify_password("demo1234", "garbage")
