#!/usr/bin/env python3
import os
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

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
MAX_WORKERS = 5                 # 并发线程数
KEEP_REPORT_DAYS = 30           # 保留最近30天的报告
KEEP_STATUS_DAYS = 60           # ★ 状态记录保留天数（失效IP超过60天未重新出现则删除）

STATUS_FILE = 'status.json'
REPORT_DIR = 'daily_reports'
VALID_10DAYS_FILE = 'valid_10days.txt'

# ★★★ 硬编码园地配置 ★★★
YUANDI_CONFIG = [
    {'url': 'https://www.readfree.net', 'feature': '网上读书园地'},
]

# ================= 工具函数（无状态） =================

def parse_proxy_line(line):
    line = line.strip()
    if not line:
        return None
    parts = line.split(':')
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None

def check_proxy(ip, port, protocol):
    """检测单个代理在指定协议下是否有效（能获取出口 IP）"""
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
    """对 connect.txt 的代理尝试全部 4 种协议，返回 (success, out_ip, supported_protocol)"""
    protocols = ['http', 'https', 'socks5', 'socks4']
    for proto in protocols:
        success, out_ip = check_proxy(ip, port, proto)
        if success:
            return True, out_ip, proto
    return False, None, None

def check_yuandi(ip, port, protocol, yuandi_config):
    """检测代理能否访问园地并返回特征码"""
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

# ================= 单个代理检测任务（用于并发） =================

def detect_one(ip, port, filename):
    """
    检测单个代理，返回包含所有结果的字典
    """
    result = {
        'ip': ip,
        'port': port,
        'filename': filename,
        'base_success': False,
        'out_ip': None,
        'proto_used': None,
        'yuandi_success': False,
        'yuandi_url': None,
        'overall_success': False,
    }

    # 基本检测
    if filename == 'connect.txt':
        base_success, out_ip, proto_used = check_all_protocols(ip, port)
    else:
        protocol = PROTOCOL_MAP[filename]
        base_success, out_ip = check_proxy(ip, port, protocol)
        proto_used = protocol

    result['base_success'] = base_success
    result['out_ip'] = out_ip
    result['proto_used'] = proto_used

    # 如果基本检测失败，直接返回
    if not base_success:
        return result

    # 园地检测
    yuandi_success, yuandi_url = check_yuandi(ip, port, proto_used, YUANDI_CONFIG)
    result['yuandi_success'] = yuandi_success
    result['yuandi_url'] = yuandi_url
    result['overall_success'] = base_success and yuandi_success

    return result

# ================= 清理旧报告 =================

def clean_old_reports(report_dir, keep_days):
    """删除 keep_days 天以前的报告文件"""
    if not os.path.exists(report_dir):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    for filename in os.listdir(report_dir):
        if not filename.endswith('.txt'):
            continue
        try:
            date_str = filename.split('.')[0]
            file_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            if file_date < cutoff:
                file_path = os.path.join(report_dir, filename)
                os.remove(file_path)
                print(f"已删除旧报告: {filename}")
        except Exception as e:
            print(f"处理报告文件 {filename} 时出错: {e}")

# ================= ★ 清理陈旧状态记录 =================

def clean_stale_status(status, keep_days):
    """
    删除 status 中最后更新日期距今超过 keep_days 天的记录
    （即该 IP 已从代理文件中消失超过 keep_days 天）
    """
    if not status:
        return 0
    today = datetime.now(timezone.utc)
    to_delete = []
    for key, val in status.items():
        last_date_str = val.get('last_date')
        if not last_date_str:
            continue
        try:
            last_date = datetime.strptime(last_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            if (today - last_date).days > keep_days:
                to_delete.append(key)
        except Exception:
            continue
    for key in to_delete:
        del status[key]
    return len(to_delete)

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
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    report_lines = [f"Daily Report - {today}"]
    report_lines.append("="*60)

    # --- 收集所有代理任务 ---
    tasks = []
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
            tasks.append((ip, port, filename))

    # --- 并发检测 ---
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_task = {
            executor.submit(detect_one, ip, port, filename): (ip, port, filename)
            for ip, port, filename in tasks
        }
        for future in as_completed(future_to_task):
            try:
                res = future.result()
                results.append(res)
            except Exception as e:
                ip, port, filename = future_to_task[future]
                print(f"检测 {ip}:{port} 时出错: {e}")
                res = {
                    'ip': ip,
                    'port': port,
                    'filename': filename,
                    'base_success': False,
                    'out_ip': None,
                    'proto_used': None,
                    'yuandi_success': False,
                    'yuandi_url': None,
                    'overall_success': False,
                }
                results.append(res)

    # --- 处理检测结果，更新状态 ---
    to_keep = defaultdict(set)
    to_remove = defaultdict(set)

    for res in results:
        ip = res['ip']
        port = res['port']
        filename = res['filename']
        overall_success = res['overall_success']
        base_success = res['base_success']
        out_ip = res['out_ip']
        proto_used = res['proto_used']
        yuandi_success = res['yuandi_success']
        yuandi_url = res['yuandi_url']

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

        proto_info = f"({proto_used})" if base_success else ""
        out_info = f"出口IP: {out_ip}" if base_success else ""
        yuandi_info = f"园地访问成功: {yuandi_url}" if base_success and yuandi_success else "园地访问失败" if base_success else ""
        result_str = "成功" if overall_success else "失败"
        report_lines.append(f"{filename.upper()} {ip}:{port} - {result_str} {proto_info} {out_info} | {yuandi_info}")

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

    # --- 写入每日报告 ---
    report_path = os.path.join(REPORT_DIR, f"{today}.txt")
    with open(report_path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\r\n'.join(report_lines))

    # ★ 清理 30 天以前的旧报告
    clean_old_reports(REPORT_DIR, KEEP_REPORT_DAYS)

    # ★ 清理 60 天以前且已不在代理文件中的陈旧状态记录
    stale_count = clean_stale_status(status, KEEP_STATUS_DAYS)
    if stale_count > 0:
        save_status(status)  # 有删除则重新保存
        print(f"已删除 {stale_count} 条超过 {KEEP_STATUS_DAYS} 天未更新的陈旧状态记录")
    else:
        print("没有需要清理的陈旧状态记录")

    total = sum(len(v) for v in to_keep.values()) + sum(len(v) for v in to_remove.values())
    removed = sum(len(v) for v in to_remove.values())
    print(f"总代理数: {total}, 本次移除 {removed} 个（连续无效≥5天）")
    print(f"当前长期有效代理数: {sum(len(v) for v in valid_10.values())}")

if __name__ == '__main__':
    main()
