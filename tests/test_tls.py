"""HTTPS 支持（tls_helper）的回归测试。

覆盖三件事：
1. 自签证书能生成、能被校验、SAN 覆盖本机常用地址；
2. 生成策略是「幂等但不僵化」——参数没变就复用，SAN 变了就重新生成；
3. 用户自带的证书只被校验、不被覆盖，配置错误一律给出明确报错。

Run with the repo venv:  deeperseeker_env/Scripts/python.exe -m pytest tests/test_tls.py
(also plain-python runnable)
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

import tls_helper  # noqa: E402


class _TmpDir:
    """轻量临时目录上下文。

    不用 pytest 的 tmp_path 是为了让本文件在裸 python 下也能直接跑。
    """

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="ds_tls_test_")
        return self.path

    def __exit__(self, *_exc):
        # 清理失败不能影响测试结论：沙箱拦删除时抛的是 SystemExit（BaseException），
        # 用 except Exception 接不住。
        try:
            shutil.rmtree(self.path, ignore_errors=True)
        except BaseException:
            pass
        return False


def _paths(root):
    return os.path.join(root, "tls", "self-signed.crt"), os.path.join(root, "tls", "self-signed.key")


def test_generates_self_signed_certificate():
    with _TmpDir() as root:
        cert_path, key_path = _paths(root)
        _, _, info = tls_helper.ensure_certificate(
            cert_path, key_path, extra_san=["nas.local", "192.168.5.9"]
        )

        assert info["self_signed"] is True
        assert info["days_remaining"] > 3000
        assert "DeeperSeeker" in info["subject"]

        san = info["san"]
        assert "localhost" in san
        assert "nas.local" in san, "DEEPSEEKER_HTTPS_SAN 里的 DNS 名必须进 SAN"
        assert "192.168.5.9" in san, "DEEPSEEKER_HTTPS_SAN 里的 IP 必须进 SAN"
        assert "127.0.0.1" in san, "必须覆盖回环地址，否则本机访问就报错"


def test_certificate_is_reused_when_parameters_are_unchanged():
    with _TmpDir() as root:
        cert_path, key_path = _paths(root)
        tls_helper.ensure_certificate(cert_path, key_path, extra_san=["a.local"])
        first = open(cert_path, "rb").read()

        tls_helper.ensure_certificate(cert_path, key_path, extra_san=["a.local"])
        second = open(cert_path, "rb").read()

        assert first == second, "参数没变时不该重新生成证书（会让已导入信任库的用户掉信任）"


def test_certificate_is_regenerated_when_san_changes():
    with _TmpDir() as root:
        cert_path, key_path = _paths(root)
        tls_helper.ensure_certificate(cert_path, key_path, extra_san=["a.local"])
        first = open(cert_path, "rb").read()

        _, _, info = tls_helper.ensure_certificate(cert_path, key_path, extra_san=["b.local"])
        second = open(cert_path, "rb").read()

        assert first != second, "SAN 变了必须重新生成，否则新域名访问会报主机名不匹配"
        assert "b.local" in info["san"]


def test_only_one_of_cert_and_key_present_is_an_error():
    with _TmpDir() as root:
        cert_path, key_path = _paths(root)
        tls_helper.ensure_certificate(cert_path, key_path)
        os.remove(key_path)  # 只留证书，模拟路径配错

        try:
            tls_helper.ensure_certificate(cert_path, key_path)
        except tls_helper.TLSError as exc:
            assert "成对" in str(exc)
        else:
            raise AssertionError("只有一半文件时必须报错，不能默默重新生成")


def test_mismatched_pair_is_rejected():
    with _TmpDir() as root:
        cert_a, key_a = _paths(os.path.join(root, "a"))
        cert_b, key_b = _paths(os.path.join(root, "b"))
        tls_helper.ensure_certificate(cert_a, key_a)
        tls_helper.ensure_certificate(cert_b, key_b)

        try:
            tls_helper.validate_pair(cert_a, key_b)
        except tls_helper.TLSError as exc:
            assert "不配对" in str(exc)
        else:
            raise AssertionError("证书与私钥不配对时必须报错")


def test_expired_certificate_is_rejected():
    with _TmpDir() as root:
        cert_path, key_path = _paths(root)
        _write_expired_pair(cert_path, key_path)
        # 自带证书（没有 .meta.json）走的是「尊重用户」分支，但仍然要校验有效期
        try:
            tls_helper.validate_pair(cert_path, key_path)
        except tls_helper.TLSError as exc:
            assert "已过期" in str(exc)
        else:
            raise AssertionError("过期证书必须报错，而不是让 uvicorn 抛 SSL 异常")


def test_user_supplied_certificate_is_never_overwritten():
    with _TmpDir() as root:
        cert_path, key_path = _paths(root)
        _write_expired_pair(cert_path, key_path, days=900)  # 自带证书，未过期
        before = open(cert_path, "rb").read()

        # extra_san 与证书实际内容无关：自带证书不该因为参数不同就被重建
        tls_helper.ensure_certificate(cert_path, key_path, extra_san=["whatever.local"])

        assert open(cert_path, "rb").read() == before, "用户提供的证书不允许被覆盖"


def test_san_env_parsing():
    assert tls_helper.parse_san_env("a.local, 192.168.1.10 ,,b.local") == [
        "a.local", "192.168.1.10", "b.local",
    ]
    assert tls_helper.parse_san_env("") == []
    assert tls_helper.parse_san_env(None) == []


def test_collect_san_entries_always_covers_loopback():
    dns_names, ip_addresses = tls_helper.collect_san_entries()
    assert "localhost" in dns_names
    assert "127.0.0.1" in ip_addresses


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_expired_pair(cert_path, key_path, days=-1):
    """直接造一张指定有效期的证书，用来测过期与「自带证书」分支。"""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "external-test")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=abs(days) + 10))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("external-test")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    with open(cert_path, "wb") as handle:
        handle.write(certificate.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as handle:
        handle.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'全部通过' if not failures else str(failures) + ' 项失败'}")
    sys.exit(1 if failures else 0)
