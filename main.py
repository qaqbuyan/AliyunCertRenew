import os
import time
import json
import uuid
import hmac
import base64
import logging
import hashlib
import subprocess
import tempfile
import urllib.parse
import urllib.request

# ─── 常量 ────────────────────────────────────────────
RENEW_THRESHOLD = 7 * 86400          # 7天的秒数
FORCE_DOWNLOAD = False                # True 则即使证书未到期也强制下载（主要是功能测试用）
CERT_FORMAT = CertFormat.NGINX        # 证书输出格式，可选 NGINX / APACHE / TOMCAT / IIS / JKS

class CertFormat:
    """支持的证书输出格式"""
    NGINX = 'nginx'      # .pem + .key
    APACHE = 'apache'    # .crt + .key
    TOMCAT = 'tomcat'    # .pfx
    IIS = 'iis'          # .pfx
    JKS = 'jks'          # .jks
    VALUES = (NGINX, APACHE, TOMCAT, IIS, JKS)

# ─── 阿里云 OpenAPI 签名与调用 ──────────────────────
def _percent_encode(s: str) -> str:
    """阿里云签名要求的百分比编码"""
    return urllib.parse.quote(str(s), safe='')

def _call_api(action: str, **kwargs) -> dict:
    """直接调用阿里云 OpenAPI（无需 SDK）"""
    key_id = os.getenv('ALIYUN_ACCESS_KEY_ID_RENEW')
    key_secret = os.getenv('ALIYUN_ACCESS_KEY_SECRET_RENEW')
    if not key_id or not key_secret:
        raise ValueError("请设置 ALIYUN_ACCESS_KEY_ID_RENEW 和 ALIYUN_ACCESS_KEY_SECRET_RENEW 环境变量")

    # 公共参数
    params = {
        'Action': action,
        'AccessKeyId': key_id,
        'Format': 'JSON',
        'Version': '2020-04-07',
        'SignatureMethod': 'HMAC-SHA1',
        'SignatureVersion': '1.0',
        'SignatureNonce': str(uuid.uuid4()),
        'Timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    # 业务参数
    for k, v in kwargs.items():
        if v is not None:
            params[k] = str(v)

    # 构造签名
    sorted_keys = sorted(params.keys())
    canonicalized = '&'.join(
        f'{_percent_encode(k)}={_percent_encode(params[k])}'
        for k in sorted_keys
    )
    string_to_sign = f'POST&%2F&{_percent_encode(canonicalized)}'
    signing_key = (key_secret + '&').encode('utf-8')
    signature = hmac.new(signing_key, string_to_sign.encode('utf-8'),
                         hashlib.sha1).digest()
    params['Signature'] = base64.b64encode(signature).decode('utf-8')

    # 发送请求
    data = urllib.parse.urlencode(params).encode('utf-8')
    req = urllib.request.Request('https://cas.aliyuncs.com', data=data, method='POST')
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode('utf-8'))

    # 检查阿里云错误
    if 'Code' in result and result['Code'] != 'Success':
        raise RuntimeError(f"API {action} 错误: {result.get('Code', '')} - {result.get('Message', '')}")
    return result

# ─── 业务逻辑 ────────────────────────────────────────
def setup_logging():
    """配置日志，默认同时输出到控制台和 ./log/ 目录下的日期文件"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, 'log')
    os.makedirs(log_dir, exist_ok=True)
    log_filename = os.path.join(log_dir, time.strftime('%Y-%m-%d.log'))

    log_output = os.getenv('LOG_OUTPUT', 'both').lower()
    handlers = []

    if log_output == 'file':
        handlers.append(logging.FileHandler(log_filename, encoding='utf-8'))
    elif log_output == 'console':
        handlers.append(logging.StreamHandler())
    else:  # 'both'（默认）
        handlers.append(logging.StreamHandler())
        handlers.append(logging.FileHandler(log_filename, encoding='utf-8'))
        print(f"日志将同时输出到控制台和文件: {log_filename}")

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

def get_basic_info(domain: str):
    """查询域名现有证书的到期状态"""
    result = _call_api('ListUserCertificateOrder',
                       Keyword=domain, OrderType='CERT')

    results = []
    need_renew = False

    for entry in result.get('CertificateOrderList', []):
        entry_domains = set()
        if entry.get('CommonName'):
            entry_domains.add(entry['CommonName'])
        if entry.get('Sans'):
            for san in entry['Sans'].split(','):
                entry_domains.add(san.strip())

        if domain not in entry_domains:
            continue

        cert_start = entry.get('CertStartTime')
        cert_end = entry.get('CertEndTime')
        if cert_start and cert_end:
            end_sec = int(cert_end) // 1000
            start_sec = int(cert_start) // 1000
            expire_sec = end_sec - int(time.time())
            expire_date = time.strftime('%Y-%m-%d', time.localtime(end_sec))
            start_date = time.strftime('%Y-%m-%d', time.localtime(start_sec))
            logging.info(f"证书 {entry.get('CertificateId')} (域名 {domain}) "
                          f"有效期：{start_date} ~ {expire_date}（剩余 {expire_sec} 秒）")
            if expire_sec < RENEW_THRESHOLD:
                need_renew = True
            results.append(entry)

    if not results:
        logging.info(f"域名 {domain} 未找到现有证书，将直接申请新证书")
        need_renew = True

    return need_renew, results

def apply_new_cert(domain: str) -> int:
    """申请新证书并等待签发"""
    result = _call_api('CreateCertificateForPackageRequest',
                       Domain=domain,
                       ProductCode='digicert-free-1-free',
                       ValidateType='DNS')
    order_id = int(result['OrderId'])
    logging.info(f"已为 {domain} 创建新的证书申请，订单 ID: {order_id}")

    for _ in range(30):
        time.sleep(60)
        order_result = _call_api('ListUserCertificateOrder',
                                Keyword=domain, OrderType='CPACK')

        for entry in order_result.get('CertificateOrderList', []):
            if int(entry['OrderId']) == order_id:
                logging.info(f"订单当前状态: {entry.get('Status')}")
                if entry.get('Status') == 'ISSUED':
                    cert_result = _call_api('ListUserCertificateOrder',
                                           Keyword=domain, OrderType='CERT')
                    for cert_entry in cert_result.get('CertificateOrderList', []):
                        if cert_entry.get('InstanceId') == entry.get('InstanceId'):
                            return int(cert_entry['CertificateId'])
                    raise ValueError("未找到已签发的证书记录")

    raise TimeoutError("等待证书签发超时")

def download_cert(cert_id: int, domain: str, output_dir: str = None, cert_format: str = None):
    """下载证书并根据指定格式保存到本地"""
    if output_dir is None:
        output_dir = os.environ.get('CERT_DOWNLOAD_DIR',
                                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certs'))
    if cert_format is None:
        cert_format = CERT_FORMAT
    os.makedirs(output_dir, exist_ok=True)

    result = _call_api('GetUserCertificateDetail',
                       CertId=cert_id, CertFilter=False)

    cert_body = result.get('Cert', '')
    cert_key = result.get('Key', '')

    if not cert_body:
        raise ValueError(f"返回的证书内容为空，可能为国密证书（Algorithm: {result.get('Algorithm', 'N/A')}）")

    # ─── PEM 系格式：直接保存 ────────────────────────
    if cert_format in (CertFormat.NGINX, CertFormat.APACHE):
        ext_pem = 'crt' if cert_format == CertFormat.APACHE else 'pem'
        pem_path = os.path.join(output_dir, f"{domain}.{ext_pem}")
        with open(pem_path, 'w', encoding='utf-8') as f:
            f.write(cert_body.strip() + '\n')
        logging.info(f"证书文件已保存: {pem_path}")

        if cert_key:
            key_path = os.path.join(output_dir, f"{domain}.key")
            with open(key_path, 'w', encoding='utf-8') as f:
                f.write(cert_key.strip() + '\n')
            logging.info(f"私钥文件已保存: {key_path}")
        else:
            logging.warning("未获取到私钥内容，请检查子账号权限")

    # ─── PFX 格式（Tomcat / IIS）：OpenSSL 转换 ─────
    elif cert_format in (CertFormat.TOMCAT, CertFormat.IIS):
        if not cert_key:
            raise ValueError("PFX 格式需要私钥，但未获取到私钥内容")
        with tempfile.TemporaryDirectory() as tmp_dir:
            pem_path = os.path.join(tmp_dir, f"{domain}.pem")
            key_path = os.path.join(tmp_dir, f"{domain}.key")
            with open(pem_path, 'w', encoding='utf-8') as f:
                f.write(cert_body.strip() + '\n')
            with open(key_path, 'w', encoding='utf-8') as f:
                f.write(cert_key.strip() + '\n')

            output_path = os.path.join(output_dir, f"{domain}.pfx")
            subprocess.run([
                'openssl', 'pkcs12', '-export',
                '-out', output_path,
                '-inkey', key_path,
                '-in', pem_path,
                '-passout', 'pass:'
            ], check=True, capture_output=True, text=True)
            logging.info(f"PFX 证书文件已保存: {output_path}")

    # ─── JKS 格式：PEM → PFX → JKS ─────────────────
    elif cert_format == CertFormat.JKS:
        if not cert_key:
            raise ValueError("JKS 格式需要私钥，但未获取到私钥内容")
        with tempfile.TemporaryDirectory() as tmp_dir:
            pem_path = os.path.join(tmp_dir, f"{domain}.pem")
            key_path = os.path.join(tmp_dir, f"{domain}.key")
            pfx_path = os.path.join(tmp_dir, f"{domain}.pfx")
            with open(pem_path, 'w', encoding='utf-8') as f:
                f.write(cert_body.strip() + '\n')
            with open(key_path, 'w', encoding='utf-8') as f:
                f.write(cert_key.strip() + '\n')

            # PEM → PFX
            subprocess.run([
                'openssl', 'pkcs12', '-export',
                '-out', pfx_path,
                '-inkey', key_path,
                '-in', pem_path,
                '-passout', 'pass:'
            ], check=True, capture_output=True, text=True)

            # PFX → JKS
            output_path = os.path.join(output_dir, f"{domain}.jks")
            storepass = os.getenv('JKS_STORE_PASS', 'changeit')
            subprocess.run([
                'keytool', '-importkeystore',
                '-srckeystore', pfx_path,
                '-srcstoretype', 'PKCS12',
                '-srcstorepass', '',
                '-destkeystore', output_path,
                '-deststoretype', 'JKS',
                '-deststorepass', storepass
            ], check=True, capture_output=True, text=True)
            logging.info(f"JKS 证书文件已保存: {output_path} （密钥库密码: {storepass}）")

    else:
        raise ValueError(f"不支持的证书格式: {cert_format}，支持的格式: {', '.join(CertFormat.VALUES)}")

    logging.info(f"域名 {domain} 的证书已下载到目录: {output_dir}")

def main():
    setup_logging()
    logging.info("阿里云证书续期工具启动...")

    domain_env = os.getenv('DOMAIN', '')
    if not domain_env:
        logging.fatal("未指定域名，退出...")
        return

    domains = domain_env.split(',')
    logging.info(f"需要检查的域名: {domains}")
    logging.info(f"证书输出格式: {CERT_FORMAT}")

    if FORCE_DOWNLOAD:
        logging.info("FORCE_DOWNLOAD 已开启，即使证书未到期也会下载到本地")

    for domain in domains:
        logging.info(f">>> 检查域名 {domain}")
        try:
            need_renew, cert_entries = get_basic_info(domain)

            if need_renew:
                logging.info(f"域名 {domain} 需要申请新证书")
                new_cert_id = apply_new_cert(domain)
                logging.info(f"已为域名 {domain} 创建新证书: {new_cert_id}")
                download_cert(new_cert_id, domain)
            elif FORCE_DOWNLOAD and cert_entries:
                entry = cert_entries[0]
                start_sec = int(entry['CertStartTime']) // 1000
                end_sec = int(entry['CertEndTime']) // 1000
                start_date = time.strftime('%Y-%m-%d', time.localtime(start_sec))
                end_date = time.strftime('%Y-%m-%d', time.localtime(end_sec))
                existing_cert_id = int(entry['CertificateId'])
                logging.info(f"域名 {domain} 证书仍有效（{start_date} ~ {end_date}），"
                             f"FORCE_DOWNLOAD 开启，直接下载现有证书 (ID: {existing_cert_id})")
                download_cert(existing_cert_id, domain)
            else:
                logging.info(f"域名 {domain} 的证书有效期充足，无需续期")

        except Exception as e:
            logging.error(f"处理域名 {domain} 时发生错误: {str(e)}")
            continue

if __name__ == "__main__":
    main()