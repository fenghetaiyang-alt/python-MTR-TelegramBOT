# 更新需求：一个链路同时探测多个目标地址，并且任意一个地址出问题就触发报警
# 1 将 LINKS 配置中的 target 改为列表（List）格式，支持填入一个或多个 IP。
# 2 脚本在循环到该链路时，会遍历列表中的所有目标 IP，为每个 IP 独立分配线程跑 mtr 探测。
# 3 报警通知和日志中会明确带上是哪个具体的目标 IP 出了故障，方便你精准定位。
#!/root/mtr_env/bin/python3
# -*- coding: utf-8 -*-

import os
import re
import time
import subprocess
import threading
import requests
from concurrent.futures import ThreadPoolExecutor

# ==================== 🛠️ 核心配置区域 ====================
TG_TOKEN = "----------------------------"
CHAT_ID = "-5454849963"

# 📊 业务触发配置
LOSS_THRESHOLD = 30.0                    # 目的地触发丢包阈值
INTERVAL_SEC = 35                        # 探测频率（35秒安全节奏，防线程堆积）
REMIND_INTERVAL_SEC = 10 * 60            # 持续故障时，每 10 分钟重新提醒一次
CONSECUTIVE_UNKNOWN_LIMIT = 3            # 连续 3 跳 ??? 则判定为骨干网断路故障
# ========================================================

LOG_DIR = "/var/log/mtr_flash"
os.makedirs(LOG_DIR, exist_ok=True)

LINK_STATES = {}
state_lock = threading.Lock()

# 🌐 链路核心配置：target 支持配置多个 IP，用逗号隔开包裹在 [ ] 中
LINKS = {
    "BRO-HK":    {"src_ip": "10.49.251.4", "target": ["8.8.8.8"]},
    "MKN-HK":    {"src_ip": "10.49.251.5", "target": ["8.8.8.8"]},
    "Backup-HK": {"src_ip": "10.49.251.6", "target": ["8.8.8.8"]},
    "MKN-JPZ":   {"src_ip": "10.49.251.7", "target": ["8.8.8.8", "154.89.6.1"]}  # 💡 示范：同时拨测两个目标
}

# 动态初始化所有链路状态（状态粒度细化到：链路名 + 目标IP）
for name, info in LINKS.items():
    for tgt in info["target"]:
        state_key = f"{name}_{tgt}"
        LINK_STATES[state_key] = {
            "status": "OK",
            "fail_count": 0,
            "ok_count": 0,
            "last_alert_time": 0
        }

def send_tg_msg(html_text):
    clean_token = TG_TOKEN.strip()
    if not clean_token.startswith("bot"):
        clean_token = f"bot{clean_token}"
        
    payload = {"chat_id": CHAT_ID, "parse_mode": "HTML", "text": html_text}
    
    urls = [
        f"https://api.telegram.org/{clean_token}/sendMessage",
        f"https://tgproxy.cc/{clean_token}/sendMessage",
        f"https://telegram-proxy.org/{clean_token}/sendMessage"
    ]
    
    for url in urls:
        try:
            response = requests.post(url, json=payload, timeout=6)
            if response.status_code == 200 and response.json().get("ok"):
                return  
        except:
            continue  

def monitor_link(isp_name, src_ip, target_ip):
    global LINK_STATES
    
    # 🩹 采用标准兼容参数
    cmd = ["mtr", "-n", "-c", "10", "-i", "1", "-m", "24", "-G", "1", "-r", "-a", src_ip, target_ip]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=35)
        if result.returncode != 0: return
        raw_output = result.stdout
    except: 
        return

    lines = raw_output.strip().split("\n")
    hops_data = []
    
    for line in lines:
        if "Start:" in line or "HOST" in line or not line.strip(): continue
        
        hop_match = re.search(r"^\s*(\d+)", line)
        if not hop_match: continue
        hop = int(hop_match.group(1))
        
        if "???" in line:
            hops_data.append({
                "hop": hop, "ip": "???", "loss": 100.0,
                "last": "--", "avg": "--", "max": "--"
            })
            continue

        ip_match = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", line)
        if not ip_match: continue
        ip = ip_match.group(1)
        
        loss_match = re.search(r"(\d+\.\d+)%", line)
        loss = float(loss_match.group(1)) if loss_match else 0.0
            
        parts = line.split()
        last_lat, avg_lat, max_lat = "0.0", "0.0", "0.0"
        try:
            if len(parts) >= 5:
                last_lat = parts[-5]
                avg_lat = parts[-4]
                max_lat = parts[-2]  
        except: 
            pass

        hops_data.append({
            "hop": hop, "ip": ip, "loss": loss,
            "last": last_lat, "avg": avg_lat, "max": max_lat
        })

    if not hops_data: return

    # 1. 计算连续 ??? 的数量
    max_consecutive_unknown = 0
    current_consecutive = 0
    for node in hops_data:
        if node["ip"] == "???":
            current_consecutive += 1
            max_consecutive_unknown = max(max_consecutive_unknown, current_consecutive)
        else:
            current_consecutive = 0  

    # 2. 精准目的地送达判定
    dest_node = hops_data[-1]
    is_target_reached = (dest_node["ip"] == target_ip) 

    if is_target_reached:
        dest_loss = dest_node["loss"]
    else:
        dest_loss = 100.0

    # 🚨 触发报警的总开关
    is_fault = (dest_loss >= (LOSS_THRESHOLD - 0.01)) or (max_consecutive_unknown >= CONSECUTIVE_UNKNOWN_LIMIT)

    state_wall = ""
    for node in hops_data:
        pad_hop = f"{node['hop']:02d}"
        if node["ip"] == "???":
            state_wall += f"{pad_hop} | [隐身节点] | 100%  |    -- /    -- /    --\n"
        else:
            try: loss_int = int(node["loss"])
            except: loss_int = 0
            state_wall += f"{pad_hop} | {str(node['ip']):<15} | {loss_int:<4}% | {str(node['last']):>5} / {str(node['avg']):>5} / {str(node['max']):>5}\n"

    now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    now_ts = time.time()

    # 使用独特的 state_key 确保多目标在同一个链路下的状态不互相覆盖
    state_key = f"{isp_name}_{target_ip}"

    with state_lock:
        state = LINK_STATES[state_key]
        
        if is_fault:
            state["ok_count"] = 0        
            state["fail_count"] += 1     
            
            should_send_alert = False
            if state["fail_count"] <= 2:
                should_send_alert = True
            elif now_ts - state["last_alert_time"] >= REMIND_INTERVAL_SEC:
                should_send_alert = True
                
            if should_send_alert:
                state["status"] = "FAIL"
                state["last_alert_time"] = now_ts
                
                if not is_target_reached:
                    reason = f"路由中途彻底断开 (MTR进程被迫终止在第 {dest_node['hop']} 跳)"
                elif max_consecutive_unknown >= CONSECUTIVE_UNKNOWN_LIMIT:
                    reason = f"检测到骨干网连续 {max_consecutive_unknown} 跳断路故障"
                else:
                    reason = f"目的地拨测丢包过高 ({dest_loss}%)"
                
                html_msg = (
                    f"🚨 <b>[链路故障报警]</b>\n"
                    f"🕒 <b>探测时间</b>: <code>{now_str}</code>\n"
                    f"🌐 <b>受影响链路</b>: <b>{isp_name}</b> ({src_ip})\n"
                    f"🎯 <b>故障检测目的地</b>: <code>{target_ip}</code>\n"
                    f"⚠️ <b>可能故障原因</b>: <b>{reason}</b> (故障计数: #{state['fail_count']})\n\n"
                    f"📋 <b>全链路状态:</b>\n"
                    f"<pre><code>"
                    f"Hop| Node_IP         | Loss  |  Last /  Avg  /  Max  (ms)\n"
                    f"---|-----------------|-------|---------------------------\n"
                    f"{state_wall}"
                    f"</code></pre>"
                )
                send_tg_msg(html_msg)
                save_to_log(isp_name, html_msg)
                
        else:
            state["ok_count"] += 1
            if state["status"] == "FAIL" and state["ok_count"] >= 3:
                state["status"] = "OK"
                state["fail_count"] = 0  
                state["last_alert_time"] = 0
                
                msg_lines = [
                    f"🟢 <b>[链路恢复通知] 状态已恢复正常</b>",
                    f"🕒 <b>恢复时间</b>: <code>{now_str}</code>",
                    f"🌐 <b>正常链路</b>: <b>{isp_name}</b> ({src_ip})",
                    f"🎯 <b>恢复检测目的地</b>: <code>{target_ip}</code>",
                    f"",
                    f"📋 <b>全链路状态:</b>",
                    f"<pre><code>",
                    f"Hop| Node_IP         | Loss  |  Last /  Avg  /  Max  (ms)",
                    f"---|-----------------|-------|---------------------------",
                    f"{state_wall.strip()}",
                    f"</code></pre>"
                ]
                html_msg = "\n".join(msg_lines)

                send_tg_msg(html_msg)
                save_to_log(isp_name, html_msg)

def save_to_log(isp_name, html_msg):
    clean_name = isp_name.replace("-", "_").lower() + ".log"
    log_file = os.path.join(LOG_DIR, clean_name)
    try:
        clean_log_text = re.sub(r"<[^>]+>", "", html_msg)
        with open(log_file, "a", encoding="utf-8") as f: 
            f.write(f"{'='*45}\n{clean_log_text}\n\n")
    except: pass

def main():
    # 动态计算总探测任务数来决定线程池大小，防止任务阻塞
    total_tasks = sum(len(info["target"]) for info in LINKS.values())
    executor = ThreadPoolExecutor(max_workers=total_tasks)
    print(f" MTR 监控服务已启动。当前总监测任务数: {total_tasks}，探测间隔: {INTERVAL_SEC}秒。")
    
    while True:
        start_time = time.time()
        for isp_name, info in LINKS.items():
            # 💡 遍历当前链路下的所有目标 IP，分别为它们向线程池提交探测任务
            for target_ip in info["target"]:
                executor.submit(monitor_link, isp_name, info["src_ip"], target_ip)
            
        elapsed = time.time() - start_time
        sleep_time = max(0.1, INTERVAL_SEC - elapsed)
        time.sleep(sleep_time)

if __name__ == "__main__": 
    main()

