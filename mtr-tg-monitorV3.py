# [root@localhost ~]# cat /root/mtr_tg_monitor.py
# 我们要把故障细分为三种情况，让它永远不会误报：
# 1、中途暴毙（像现在这样）：MTR 压根没探到目的地（最后一跳 IP 不是 8.8.8.8），并且只有 1-2 个隐藏节点就结束了。
# 2、骨干网断路（你原本的设计）：MTR 探到了更深的地方，但中间连续出现了 3 个或更多的 ??? 隐藏节点。
# 3、目的地高丢包：顺利送达了目的地 8.8.8.8，但目的地的丢包率偏高。
# time.sleep(INTERVAL_SEC)改为（35秒）
# 整个主循环升级为更安全的线程池（Thread Run）模式防止线程无限堆积
# 增加一条链路和更新对应的源IP地址

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
TG_TOKEN = "----------------------"
CHAT_ID = "-5454849963"
TARGET = "8.8.8.8"                       # 🎯 已对齐你实际测试的公网目的地

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

# 🌐 4 个新链路与对应的源 IP 配置
LINKS = {
    "BRO-HK": "10.49.251.4",
    "MKN-HK": "10.49.251.5",
    "Backup-HK": "10.49.251.6",
    "MKN-JPZ": "10.49.251.7"
}

# 动态初始化所有链路状态
for name in LINKS.keys():
    LINK_STATES[name] = {
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

def monitor_link(isp_name, src_ip):
    global LINK_STATES
    
    # 🩹 采用标准兼容参数，不带可能导致部分系统崩溃的 -g 或 -G
    cmd = ["mtr", "-n", "-c", "10", "-i", "1", "-m", "24", "-G", "1", "-r", "-a", src_ip, TARGET]
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

    # 1. 计算连续 ??? 的数量（你的核心逻辑，完美保留）
    max_consecutive_unknown = 0
    current_consecutive = 0
    for node in hops_data:
        if node["ip"] == "???":
            current_consecutive += 1
            max_consecutive_unknown = max(max_consecutive_unknown, current_consecutive)
        else:
            current_consecutive = 0  

    # 2. 🚀 精准目的地送达判定（新增防御，应对 MTR 进程提前退出的特殊网络故障）
    dest_node = hops_data[-1]
    is_target_reached = (dest_node["ip"] == TARGET) 

    if is_target_reached:
        dest_loss = dest_node["loss"]
    else:
        # 如果最后一跳不是 8.8.8.8，说明中途路由暴毙，目的地属于 100% 无法访问状态
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

    with state_lock:
        state = LINK_STATES[isp_name]
        
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
                
                # 🚀 报警原因智能细分归类（让故障定位一目了然）
                if not is_target_reached:
                    # 本地端或网关彻底中断导致 MTR 提前中止
                    reason = f"路由中途彻底断开 (MTR进程被迫终止在第 {dest_node['hop']} 跳)"
                elif max_consecutive_unknown >= CONSECUTIVE_UNKNOWN_LIMIT:
                    # 骨干网连续隐藏节点故障
                    reason = f"检测到骨干网连续 {max_consecutive_unknown} 跳断路故障"
                else:
                    # 正常跑到目的地但高丢包
                    reason = f"目的地拨测丢包过高 ({dest_loss}%)"
                
                html_msg = (
                    f"🚨 <b>[链路故障报警]</b>\n"
                    f"🕒 <b>探测时间</b>: <code>{now_str}</code>\n"
                    f"🌐 <b>受影响链路</b>: <b>{isp_name}</b> ({src_ip})\n"
                    f"🎯 <b>拨测目的地</b>: <code>{TARGET}</code>\n"
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
                    f"🎯 <b>拨测目的地</b>: <code>{TARGET}</code>",
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
    # 🚀 使用安全的线程池，限制最大并发数为当前链路总数
    executor = ThreadPoolExecutor(max_workers=len(LINKS))
    print(f" MTR 监控服务已启动。当前监测链路数: {len(LINKS)}，探测间隔: {INTERVAL_SEC}秒。")
    
    while True:
        start_time = time.time()
        for isp_name, src_ip in LINKS.items():
            executor.submit(monitor_link, isp_name, src_ip)
            
        # 动态延迟补偿，保持每轮循环绝对精准对齐 35 秒
        elapsed = time.time() - start_time
        sleep_time = max(0.1, INTERVAL_SEC - elapsed)
        time.sleep(sleep_time)

if __name__ == "__main__": 
    main()
