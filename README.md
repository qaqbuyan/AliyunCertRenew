# AliyunCertRenew

**基于[AliyunCertRenew-Python](https://github.com/wsgfz/AliyunCertRenew-Python) 的 Python 魔改版本，方便自行修改**

本程序用于自动续期阿里云免费 SSL 证书，**并将证书文件下载到本地**，支持 Nginx / Apache / Tomcat / IIS / JKS 多种输出格式。

> 本 fork 移除了安装依赖的环境，不需要安装依赖，直接运行即可。
> 移除了部署到云资源的功能，改为本地下载证书文件。
> 以便于自动化部署，如在监控机上一次性下载多个域名的证书，后续可自行添加功能通过其他方法推送至每一台服务器。


## 背景

[之前](https://help.aliyun.com/zh/ssl-certificate/product-overview/notice-on-adjustment-of-service-policies-for-free-certificates)阿里云将个人免费证书的时长调整为了三个月.  
云上资源也无法使用 ACME 自动申请部署新证书, 每三个月就需要手动登陆控制台续期, 且只会在证书过期前一个月有短信提醒.  
操作繁琐之外, 如果忘记续期还会导致意外的服务中断.

## 准备工作

请先确认以下内容:
1. 你的域名 DNS 解析已由阿里云托管 (用以完成域名 DNS 认证)
2. 目前你已在阿里云上申请并部署 SSL 证书 (程序会自动按照当前的配置续期)
3. 创建一个 [RAM 子用户](https://ram.console.aliyun.com/users), 授予 `AliyunYundunCertFullAccess` 权限, 创建并保存一对 AccessKey ID 和 AccessKey Secret

## 部署

程序从环境变量读取所需参数, 必填参数如下:

1. `DOMAIN`: 需要证书续期的域名, 多个域名用英文逗号分隔
2. `ALIYUN_ACCESS_KEY_ID_RENEW`: 上面提到的子用户 AccessKey ID
3. `ALIYUN_ACCESS_KEY_SECRET_RENEW`: 上面提到的子用户 AccessKey Secret

可选参数有：
- `CERT_DOWNLOAD_DIR`: 证书下载目录, 默认为脚本所在目录下的 `certs` 文件夹
- `LOG_OUTPUT`: 控制日志输出方式
  - `console` (默认): 只在控制台显示日志
  - `file`: 只保存到时间戳命名的日志文件
  - `both`: 同时在控制台显示和保存到文件
- `JKS_STORE_PASS`: JKS 密钥库密码, 默认为 `changeit`（仅 JKS 格式时有效）

证书输出格式通过代码中的 `CERT_FORMAT` 常量控制（见下方说明）。

程序会检查对应 `DOMAIN` 中所有的域名, 如果存在域名的证书过期时间在 7 天内, 则会申请新的免费证书, 并将证书文件下载到本地 `certs` 目录（或 `CERT_DOWNLOAD_DIR` 指定的目录）。输出格式由 `main.py` 中的 `CERT_FORMAT` 常量决定。

> 代码中 `main.py` 的 `FORCE_DOWNLOAD = True` 常量用于测试：
> 首次运行时可设为 `True`，测试程序能正常申请并下载证书；确认工作正常后，请记得改回 `False`。
>
> `CERT_FORMAT = CertFormat.NGINX` 控制证书输出格式，可选值：
> - `CertFormat.NGINX` — `.pem` + `.key`（Nginx）
> - `CertFormat.APACHE` — `.crt` + `.key`（Apache）
> - `CertFormat.TOMCAT` — `.pfx`（Tomcat，需系统中安装 OpenSSL）
> - `CertFormat.IIS` — `.pfx`（IIS，需系统中安装 OpenSSL）
> - `CertFormat.JKS` — `.jks`（JKS，需系统中安装 OpenSSL 与 Java keytool）

## 本地运行

#### 安装依赖

本脚本使用 Python 标准库实现，**无需安装任何第三方依赖包**，确保你安装了 Python 3.7+ 即可直接运行。

#### 运行程序

设置环境变量并运行：

```bash
# Windows PowerShell
$env:ALIYUN_ACCESS_KEY_ID_RENEW="你的AccessKey ID"
$env:ALIYUN_ACCESS_KEY_SECRET_RENEW="你的AccessKey Secret"
$env:DOMAIN="example.com,www.example.com"  # 多个域名用逗号分隔
$env:CERT_DOWNLOAD_DIR="D:\certs"  # 可选：自定义下载目录

python main.py
```

```bash
# Linux / macOS
export ALIYUN_ACCESS_KEY_ID_RENEW="你的AccessKey ID"
export ALIYUN_ACCESS_KEY_SECRET_RENEW="你的AccessKey Secret"
export DOMAIN="example.com,www.example.com"  # 多个域名用逗号分隔
export CERT_DOWNLOAD_DIR="/path/to/certs"  # 可选：自定义下载目录

python main.py
```

```bash
# 编辑crontab
crontab -e

# 添加以下行（每3天运行一次）
0 2 */3 * * cd /path/to/AliyunCertRenew && /usr/bin/python3 main.py
```

## 效果

一次正常的运行日志如下, 续期成功后证书文件会下载到本地指定目录：

```
2025-02-11 14:30:25,123 - INFO - 阿里云证书续期工具启动...
2025-02-11 14:30:25,124 - INFO - 需要检查的域名: ['example.com']
2025-02-11 14:30:25,124 - INFO - >>> 检查域名 example.com
2025-02-11 14:30:25,456 - INFO - 域名 example.com 需要证书续期
2025-02-11 14:30:25,789 - INFO - 已为域名 example.com 创建新的证书申请
2025-02-11 14:31:25,123 - INFO - 订单当前状态: CHECKING
2025-02-11 14:32:25,234 - INFO - 订单当前状态: CHECKING
2025-02-11 14:33:25,345 - INFO - 订单当前状态: CHECKING
2025-02-11 14:34:25,456 - INFO - 订单当前状态: ISSUED
2025-02-11 14:34:25,567 - INFO - 已为域名 example.com 创建新证书: 14764653
2025-02-11 14:34:30,678 - INFO - 证书文件已保存: /path/to/certs/example.com.pem
2025-02-11 14:34:30,678 - INFO - 私钥文件已保存: /path/to/certs/example.com.key
```

下载后的文件结构示例（Nginx 格式）：

```
certs/
├── example.com.pem    # PEM 格式的证书文件
└── example.com.key    # 对应的私钥文件
```

其他格式输出示例：

| 格式 | 输出文件 |
|------|----------|
| **Nginx** | `example.com.pem` + `example.com.key` |
| **Apache** | `example.com.crt` + `example.com.key` |
| **Tomcat** | `example.com.pfx` |
| **IIS** | `example.com.pfx` |
| **JKS** | `example.com.jks` |

## 项目结构

```
AliyunCertRenew/
├── main.py              # 主程序文件
├── README.md            # 项目说明
└── log/                 # 日志文件目录（自动创建）
```
