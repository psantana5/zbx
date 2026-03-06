# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.4.x   | ✅ Current |
| 0.3.x   | ✅ Security fixes only |
| < 0.3   | ❌ No longer supported |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Send a private report to **pausantanapi2@gmail.com** with:

1. A description of the vulnerability
2. Steps to reproduce
3. The potential impact
4. (Optional) A suggested fix

You will receive an acknowledgement within **48 hours** and a full response within **7 days**.

## Disclosure policy

* We follow [responsible disclosure](https://en.wikipedia.org/wiki/Responsible_disclosure)
* We will credit reporters in the release notes unless you prefer to remain anonymous
* We aim to release a patch within 14 days of confirmation

## Security considerations for zbxctl users

* **Credentials**: Store your Zabbix API credentials in environment variables or a `.env` file — never commit them to Git
* **`.env` files**: The `.gitignore` in this repo excludes `.env` by default
* **API tokens**: Prefer Zabbix API tokens over username/password where possible
* **Network**: zbxctl communicates directly with the Zabbix API over HTTP/HTTPS — use HTTPS in production
