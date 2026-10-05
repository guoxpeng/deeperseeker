"""HTTPS 支持：证书解析、配对校验与自签证书生成。

设计目标
--------
* **开箱即用** —— 不提供证书时自动生成一张自签证书，HTTPS 端口直接能起，
  不需要用户先去折腾 openssl 或买证书。
* **可替换** —— 通过 ``DEEPSEEKER_HTTPS_CERT`` / ``DEEPSEEKER_HTTPS_KEY``
  指向自己的证书（内网 CA 签发、正式 CA 签发都行），本模块只做校验，不做任何假设。
* **不猜** —— 证书与私钥不配对、文件损坏、已过期，都在启动阶段明确报错，
  而不是让 uvicorn 抛一句难懂的 SSL 异常。

自签证书生成后，把 cert 文件导入系统的「受信任的根证书颁发机构」，
浏览器就会显示正常的锁标记。
"""

import ipaddress
import json
import logging
import os
import socket
import stat
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

logger = logging.getLogger("deeperseeker.tls")

# 自签证书的默认有效期。取 10 年是为了避免「自签证书悄悄过期、
# 用户还得重新导入一次信任」这种烦人场景；到期前会打 WARNING。
DEFAULT_VALID_DAYS = 3650
# 剩余有效期低于这个天数时提醒续期。
EXPIRY_WARN_DAYS = 30

DEFAULT_COMMON_NAME = "DeeperSeeker"


class TLSError(RuntimeError):
    """证书配置错误。启动阶段抛出，附带可执行的修复提示。"""


# ---------------------------------------------------------------------------
# SAN 收集
# ---------------------------------------------------------------------------

def _default_route_ip():
    """取本机默认出口网卡的 IPv4 地址。

    UDP ``connect()`` 不会真的发包，只是让内核选一条路由，因此这里拿到的是
    「对外那个地址」——通常就是局域网里别人访问你用的地址。
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        if sock is not None:
            sock.close()


def _hostname_aliases():
    names = []
    try:
        hostname = socket.gethostname()
    except OSError:
        return names
    if hostname:
        names.append(hostname)
        names.append(f"{hostname}.local")
    return names


def collect_san_entries(extra=None):
    """汇总自签证书应该包含的 SAN 条目。

    ``extra`` 是用户通过 ``DEEPSEEKER_HTTPS_SAN`` 追加的逗号分隔列表，
    可以混写 DNS 名和 IP（自动按格式判断）。局域网里用固定主机名访问时，
    一定要把那个名字写进来，否则浏览器会报主机名不匹配。
    """
    dns_names = ["localhost"]
    ip_addresses = ["127.0.0.1", "::1"]

    for name in _hostname_aliases():
        if name not in dns_names:
            dns_names.append(name)

    route_ip = _default_route_ip()
    if route_ip and route_ip not in ip_addresses:
        ip_addresses.append(route_ip)

    for raw in (extra or []):
        item = (raw or "").strip()
        if not item:
            continue
        try:
            ipaddress.ip_address(item)
        except ValueError:
            if item not in dns_names:
                dns_names.append(item)
        else:
            if item not in ip_addresses:
                ip_addresses.append(item)

    return dns_names, ip_addresses


def parse_san_env(raw):
    """解析 ``DEEPSEEKER_HTTPS_SAN``：逗号分隔的 DNS 名 / IP 混写列表。"""
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _build_san_extension(dns_names, ip_addresses):
    entries = [x509.DNSName(name) for name in dns_names]
    entries += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ip_addresses]
    return x509.SubjectAlternativeName(entries)


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------

def generate_self_signed(cert_path, key_path, common_name=DEFAULT_COMMON_NAME,
                         dns_names=None, ip_addresses=None, days=DEFAULT_VALID_DAYS):
    """生成一张自签证书并落盘（证书 644、私钥 600）。

    私钥用 EC P-256：密钥小、握手快，现代客户端全部支持。证书带
    ``serverAuth`` 扩展用途，导入信任库后浏览器会正常显示锁标记。
    """
    dns_names = list(dns_names or ["localhost"])
    ip_addresses = list(ip_addresses or ["127.0.0.1", "::1"])

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DeeperSeeker Self-Signed"),
    ])

    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)  # 自签：签发者就是自己
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # 回拨一天，避开客户端与服务端时钟漂移导致的 "not yet valid"
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(_build_san_extension(dns_names, ip_addresses), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
    )
    certificate = builder.sign(key, hashes.SHA256())

    _write_atomic(
        cert_path,
        certificate.public_bytes(serialization.Encoding.PEM),
        mode=0o644,
    )
    _write_atomic(
        key_path,
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            # 私钥不加密：容器里无人值守启动，加密了就得多传一个口令。
            # 文件权限 600 是这里的实际保护手段。
            encryption_algorithm=serialization.NoEncryption(),
        ),
        mode=0o600,
    )
    return certificate


def _write_atomic(path, data, mode=0o600):
    """先写同目录临时文件再 os.replace，避免中断时留下半截证书。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass  # Windows 上 chmod 语义有限，失败不影响使用
        os.replace(tmp_path, path)
    finally:
        # 清理临时文件时吞掉一切异常（含 BaseException）：沙箱拦删除抛的是
        # SystemExit，用 except Exception 接不住，会直接把启动带崩。
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except BaseException:
            pass


# ---------------------------------------------------------------------------
# 读取与校验
# ---------------------------------------------------------------------------

def load_certificate(cert_path):
    try:
        with open(cert_path, "rb") as handle:
            return x509.load_pem_x509_certificate(handle.read())
    except FileNotFoundError:
        raise TLSError(f"证书文件不存在：{cert_path}")
    except (ValueError, TypeError) as exc:
        raise TLSError(f"证书文件无法解析（需要 PEM 格式）：{cert_path} —— {exc}")


def load_private_key(key_path):
    try:
        with open(key_path, "rb") as handle:
            return serialization.load_pem_private_key(handle.read(), password=None)
    except FileNotFoundError:
        raise TLSError(f"私钥文件不存在：{key_path}")
    except TypeError:
        raise TLSError(
            f"私钥带有口令，无法无人值守加载：{key_path}。"
            "请改用无口令私钥，或在启动前手动解密。"
        )
    except (ValueError, TypeError) as exc:
        raise TLSError(f"私钥文件无法解析（需要 PEM 格式）：{key_path} —— {exc}")


def _public_bytes(key):
    return key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def validate_pair(cert_path, key_path):
    """校验证书与私钥是否配对、是否在有效期内。

    返回一个描述字典（供日志与控制台展示）。任何一项不通过都抛 ``TLSError``，
    并给出明确的修复方向。
    """
    certificate = load_certificate(cert_path)
    private_key = load_private_key(key_path)

    if _public_bytes(certificate.public_key()) != _public_bytes(private_key.public_key()):
        raise TLSError(
            f"证书与私钥不配对：{cert_path} / {key_path}。"
            "两者必须来自同一次签发，请检查是否拿错了文件。"
        )

    now = datetime.now(timezone.utc)
    not_before = _aware(certificate.not_valid_before_utc if hasattr(certificate, "not_valid_before_utc")
                        else certificate.not_valid_before)
    not_after = _aware(certificate.not_valid_after_utc if hasattr(certificate, "not_valid_after_utc")
                       else certificate.not_valid_after)

    if now < not_before:
        raise TLSError(
            f"证书尚未生效（生效时间 {not_before.isoformat()}）：{cert_path}。"
            "请检查服务器时间是否正确。"
        )
    if now > not_after:
        raise TLSError(
            f"证书已过期（到期时间 {not_after.isoformat()}）：{cert_path}。请续签或重新签发。"
        )

    remaining_days = (not_after - now).days
    if remaining_days <= EXPIRY_WARN_DAYS:
        logger.warning(
            "TLS 证书将在 %d 天后过期（%s）：%s",
            remaining_days, not_after.date().isoformat(), cert_path,
        )

    return {
        "subject": _name_to_text(certificate.subject),
        "issuer": _name_to_text(certificate.issuer),
        "self_signed": certificate.subject == certificate.issuer,
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
        "days_remaining": remaining_days,
        "san": _san_to_text(certificate),
        "key_type": private_key.__class__.__name__,
    }


def _aware(value):
    """老版本 cryptography 返回 naive datetime，统一补成 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _name_to_text(name):
    return ", ".join(f"{attr.oid._name}={attr.value}" for attr in name)


def _san_to_text(certificate):
    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return []
    return [str(entry.value) for entry in extension.value]


def describe(cert_path, key_path):
    """只做展示用的轻量描述（校验失败时返回 None，不抛异常）。"""
    try:
        return validate_pair(cert_path, key_path)
    except TLSError:
        return None


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def _meta_path(cert_path):
    return f"{cert_path}.meta.json"


def _read_meta(cert_path):
    try:
        with open(_meta_path(cert_path), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def ensure_certificate(cert_path, key_path, common_name=DEFAULT_COMMON_NAME,
                       extra_san=None, days=DEFAULT_VALID_DAYS):
    """拿到一对可用的证书/私钥，必要时生成自签证书。

    * 两个文件都在 → 校验配对与有效期后直接使用（不覆盖用户的东西）。
    * 只存在一个 → 明确报错，不猜、不覆盖（半套文件通常意味着配错了路径）。
    * 都不存在 → 生成自签证书；如果证书自带的 SAN/CN 与本次请求不一致，
      会重新生成，这样用户改了 ``DEEPSEEKER_HTTPS_SAN`` 之后能立即生效。
    """
    cert_exists = os.path.exists(cert_path)
    key_exists = os.path.exists(key_path)

    if cert_exists != key_exists:
        missing = key_path if cert_exists else cert_path
        raise TLSError(
            f"证书与私钥必须成对提供，缺少：{missing}。"
            "请同时设置 DEEPSEEKER_HTTPS_CERT 与 DEEPSEEKER_HTTPS_KEY，"
            "或把两者都留空以自动生成自签证书。"
        )

    dns_names, ip_addresses = collect_san_entries(extra_san)
    wanted = {"common_name": common_name, "dns": dns_names, "ip": ip_addresses, "days": days}

    if cert_exists:
        meta = _read_meta(cert_path)
        if meta is None:
            # 没有元数据 = 用户自己放的证书，尊重它，不重新生成。
            info = validate_pair(cert_path, key_path)
            logger.info(
                "使用外部提供的 TLS 证书：%s（主体：%s，到期：%s）",
                cert_path, info["subject"], info["not_after"][:10],
            )
            return cert_path, key_path, info
        if meta != wanted:
            logger.info(
                "自签证书的参数已变化（SAN 或有效期），重新生成：%s", cert_path
            )
        else:
            info = validate_pair(cert_path, key_path)
            logger.info(
                "复用已有自签证书：%s（到期：%s）", cert_path, info["not_after"][:10]
            )
            return cert_path, key_path, info

    certificate = generate_self_signed(
        cert_path, key_path,
        common_name=common_name,
        dns_names=dns_names,
        ip_addresses=ip_addresses,
        days=days,
    )
    _write_atomic(
        _meta_path(cert_path),
        json.dumps(wanted, ensure_ascii=False, indent=2).encode("utf-8"),
        mode=0o644,
    )
    logger.warning(
        "已生成自签 TLS 证书（有效期 %d 天）：%s\n"
        "  主体：%s\n"
        "  覆盖地址：%s\n"
        "  浏览器会提示证书不受信任 —— 把上面这个 crt 文件导入系统的"
        "「受信任的根证书颁发机构」即可消除警告，"
        "或改用 DEEPSEEKER_HTTPS_CERT / DEEPSEEKER_HTTPS_KEY 指向正式签发的证书。",
        days,
        cert_path,
        _name_to_text(certificate.subject),
        ", ".join(dns_names + ip_addresses),
    )
    return cert_path, key_path, describe(cert_path, key_path)


def default_cert_dir(data_dir):
    return os.path.join(data_dir, "tls")


def is_readable_secret(path):
    """私钥是否「只有属主可读」（POSIX 语义）。用于启动时给个提醒，不阻断启动。

    Windows 的 NTFS 没有 POSIX 的 group/other 位，``os.stat()`` 一律报 0o666，
    按位判断会稳定误报，所以那里直接跳过这项检查（改用 ACL 才有意义）。
    """
    if os.name == "nt":
        return True
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return False
    return not (mode & (stat.S_IRWXG | stat.S_IRWXO))
