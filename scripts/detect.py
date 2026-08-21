#!/usr/bin/env python3
import os
import json
import re
import subprocess
from datetime import datetime
from collections import defaultdict

# ================= 配置区域 =================
PROXY_FILES = ['http.txt', 'https.txt', 'socks4.txt', 'socks5.txt', 'connect.txt']
PROTOCOL_MAP = {
    'http.txt': 'http',
    'https.txt': 'https',
    'socks4.txt': 'socks4',
    'socks5.txt': 'socks5',
}
CURL_TIMEOUT_CONNECT = 5
CURL_TIMEOUT_MAX = 10
TARGET_HTTP = 'http://myip.ipip.net'
TARGET_HTTPS = 'https://myip.ipip.net'
IP_REGEX = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')

STATUS_FILE = 'status.json'
REPORT_DIR = 'daily_reports'
VALID_10DAYS_FILE = 'valid_10days.txt'

# ★★★ 硬编码园地配置 ★★★
YUANDI_CONFIG = [
    {'url': 'https://www.readfree.net', 'feature': '网上读书园地'},
    # 可添加更多园地
]

# ================= 工具函数 =================

def parse_proxy_line(line):
    line = line.strip()
    if not line:
        return None
    parts = line.split(':')
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None

def check_proxy(ip, port, protocol):
    addr = f"{ip}:{port}"
    if protocol == 'http':
        cmd = ['curl', '-sS', '--connect-timeout', str(CURL_TIMEOUT_CONNECT),
               '--max-time', str(CURL_TIMEOUT_MAX), '-x', addr, TARGET_HTTP]
    elif protocol == 'https':
        cmd = ['curl', '-sS', '-k', '--connect-timeout', str(CURL_TIMEOUT_CONNECT),
               '--max-time', str(CURL_TIMEOUT_MAX), '-x', f'https://{addr}', TARGET_HTTPS]
    elif protocol == 'socks5':
        cmd = ['curl', '-sS', '--connect-timeout', str(CURL_TIMEOUT_CONNECT),
               '--max-time', str(CURL_TIMEOUT_MAX), '--socks5', addr, TARGET_HTTP]
    elif protocol == 'socks4':
        cmd = ['curl', '-sS', '--connect-timeout', str(CURL_TIMEOUT_CONNECT),
               '--max-time', str(CURL_TIMEOUT_MAX), '--socks4', addr, TARGET_HTTP]
    else:
        return False, None
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=CURL_TIMEOUT_MAX+2)
        if result.returncode != 0:
            return False, None
        output = result.stdout.strip()
        ip_match = IP_REGEX.search(output)
        if ip_match:
            return True, ip_match.group()
        else:
            return False, None
    except Exception:
        return False, None

def check_all_protocols(ip, port):
    protocols = ['http', 'https', 'socks5', 'socks4']
    for proto in protocols:
        success, out_ip = check_proxy(ip, port, proto)
        if success:
            return True, out_ip, proto
    return False, None, None

def check_yuandi(ip, port, protocol, yuandi_config):
    addr = f"{ip}:{port}"
    for site in yuandi_config:
        url = site['url']
        feature = site.get('feature')
        if protocol == 'http':
            cmd = ['curl', '-sS', '-L', '--connect-timeout', str(CURL_TIMEOUT_CONNECT),
                   '--max-time', str(CURL_TIMEOUT_MAX), '-x', addr, url]
        elif protocol == 'https':
            cmd = ['curl', '-sS', '-k', '-L', '--connect-timeout', str(CURL_TIMEOUT_CONNECT),
                   '--max-time', str(CURL_TIMEOUT_MAX), '-x', f'https://{addr}', url]
        elif protocol == 'socks5':
            cmd = ['curl', '-sS', '-L', '--connect-timeout', str(CURL_TIMEOUT_CONNECT),
                   '--max-time', str(CURL_TIMEOUT_MAX), '--socks5', addr, url]
        elif protocol == 'socks4':
            cmd = ['curl', '-sS', '-L', '--connect-timeout', str(CURL_TIMEOUT_CONNECT),
                   '--max-time', str(CURL_TIMEOUT_MAX), '--socks4', addr, url]
        else:
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=CURL_TIMEOUT_MAX+2)
            if result.returncode != 0:
                continue
            if feature:
                if feature in result.stdout:
                    return True, url
            else:
                if result.stdout.strip():
                    return True, url
        except Exception:
            continue
    return False, None

# ================= 主流程 =================

def load_status():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_status(status):
    with open(STATUS_FILE, 'w') as f:
        json.dump(status, f, indent=2)

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def main():
    ensure_dir(REPORT_DIR)
    status = load_status()
    today = datetime.utcnow().strftime('%Y-%m-%d')
    report_lines = [f"Daily Report - {today}"]
    report_lines.append("="*60)

    to_keep = defaultdict(set)
    to_remove = defaultdict(set)

    for filename in PROXY_FILES:
        if not os.path.exists(filename):
            continue
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for line in lines:
            parsed = parse_proxy_line(line)
            if not parsed:
                continue
            ip, port = parsed

            # --- 基本有效性检测 ---
            if filename == 'connect.txt':
                base_success, out_ip, proto_used = check_all_protocols(ip, port)
                proto_info = f"({proto_used})" if base_success else ""
            else:
                protocol = PROTOCOL_MAP[filename]
                base_success, out_ip = check_proxy(ip, port, protocol)
                proto_used = protocol
                proto_info = f"({proto_used})" if base_success else ""

            if not base_success:
                yuandi_success = False
                yuandi_url = None
            else:
                yuandi_success, yuandi_url = check_yuandi(ip, port, proto_used, YUANDI_CONFIG)
                yuandi_info = f"园地访问成功: {yuandi_url}" if yuandi_success else "园地访问失败"

            overall_success = base_success and yuandi_success

            # --- 状态管理 ---
            key = f"{filename.split('.')[0]}:{ip}:{port}"
            if key not in status:
                status[key] = {
                    'success_days': 0,
                    'fail_days': 0,
                    'last_date': None,
                    'valid_10_flag': False
                }

            last_date = status[key].get('last_date')
            is_valid_10 = status[key].get('valid_10_flag', False)

            if last_date != today:
                if is_valid_10:
                    if overall_success:
                        status[key]['fail_days'] = 0
                        status[key]['success_days'] = 10
                    else:
                        status[key]['fail_days'] += 1
                        if status[key]['fail_days'] >= 3:
                            status[key]['success_days'] = 0
                            status[key]['valid_10_flag'] = False
                            status[key]['fail_days'] = 0
                        else:
                            status[key]['success_days'] = 10
                else:
                    if overall_success:
                        status[key]['success_days'] += 1
                        status[key]['fail_days'] = 0
                        if status[key]['success_days'] >= 10:
                            status[key]['valid_10_flag'] = True
                            status[key]['success_days'] = 10
                    else:
                        status[key]['fail_days'] += 1
                        status[key]['success_days'] = 0
                status[key]['last_date'] = today

            if status[key].get('fail_days', 0) >= 5:
                to_remove[filename].add((ip, port))
            else:
                to_keep[filename].add((ip, port))

            result_str = "成功" if overall_success else "失败"
            out_info = f"出口IP: {out_ip}" if base_success else ""
            yuandi_info_str = f" | {yuandi_info}" if base_success else ""
            report_lines.append(f"{filename.upper()} {ip}:{port} - {result_str} {proto_info} {out_info}{yuandi_info_str}")

    # --- 写入更新后的文件 ---
    for filename in PROXY_FILES:
        remove_set = to_remove.get(filename, set())
        if not os.path.exists(filename):
            continue
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            parsed = parse_proxy_line(line)
            if not parsed:
                continue
            ip, port = parsed
            if (ip, port) not in remove_set:
                new_lines.append(line)
        if len(new_lines) != len(lines):
            with open(filename, 'w', encoding='utf-8', newline='\r\n') as f:
                f.writelines(new_lines)

    # --- 生成长期有效列表 ---
    valid_10 = defaultdict(list)
    for key, val in status.items():
        if val.get('valid_10_flag', False) and val.get('success_days', 0) >= 10:
            parts = key.split(':', 2)
            if len(parts) == 3:
                proto, ip, port = parts
                file_map = {
                    'http': 'http.txt',
                    'https': 'https.txt',
                    'socks4': 'socks4.txt',
                    'socks5': 'socks5.txt',
                    'connect': 'connect.txt'
                }
                filename = file_map.get(proto, 'unknown.txt')
                valid_10[filename].append(f"{ip}:{port}:China (已通过园地验证)")

    with open(VALID_10DAYS_FILE, 'w', encoding='utf-8', newline='\r\n') as f:
        for filename, items in valid_10.items():
            f.write(f"# {filename} - 连续有效10天 + 园地可访问\n")
            for item in sorted(items):
                f.write(item + '\n')
            f.write('\n')

    save_status(status)

    report_path = os.path.join(REPORT_DIR, f"{today}.txt")
    with open(report_path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\r\n'.join(report_lines))

    total = sum(len(v) for v in to_keep.values()) + sum(len(v) for v in to_remove.values())
    removed = sum(len(v) for v in to_remove.values())
    print(f"总代理数: {total}, 本次移除 {removed} 个（连续无效≥5天）")
    print(f"当前长期有效代理数: {sum(len(v) for v in valid_10.values())}")

if __name__ == '__main__':
    main()
