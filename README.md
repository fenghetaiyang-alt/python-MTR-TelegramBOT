# python-MTR-TelegramBOT
##### 1 安装了 Python3 的 HTTP 请求库 requests（用于发 TG 消息）：bash# 安装 Python3 和 requests 库
dnf install python3 -y  
pip3 install requests

##### 2 安装 Python 虚拟环境的核心组件（RockyLinux 默认可能未包含）
dnf install python3-pip python3-virtualenv -y  
##### 3 在 /root 下创建一个专门存放监控虚拟环境的目录（比如叫 mtr_env）
python3 -m venv /root/mtr_env  
##### 4 在当前终端激活这个虚拟环境
source /root/mtr_env/bin/activate
💡 激活成功后，你的终端提示符最前面会出现 (mtr_env) 字样
##### 5 在隔离环境内安装 requests 依赖包
pip install --upgrade pip  
pip install requests  

#### 第二步：修正 Python 脚本
为了确保脚本能 100% 自动找到我们刚才建好的虚拟环境，我们需要将脚本第一行的 #!/usr/bin/env python3（寻找系统默认 Python）强制修改为寻找我们虚拟环境内部的专用 Python 解释器。
打开或创建 Python 脚本：
vi /root/mtr_tg_monitor.py


#### 第三步：退出虚拟环境
永久后台挂载，因为脚本的第一行我们已经强行绑死了刚才建立的虚拟环境绝对路径 #!/root/mtr_env/bin/python3，所以不管你当前终端处于什么状态、甚至机器重启，系统都能自动用隔离的虚拟环境去跑它！退出终端当前的虚拟环境（让终端恢复原样）：  
deactivate  
清除可能在跑的旧进程：  
pkill -f mtr_tg_monitor.py  
在全局环境下，直接强行将脚本压入系统后台永久运行：  
chmod +x /root/mtr_tg_monitor.py  
nohup /root/mtr_tg_monitor.py >/dev/null 2>&1 &  
🏁 最终复核命令你可以运行下面这行命令，如果看到进程正常亮起，且它调用的确实是 /root/mtr_env/bin/python3，说明虚拟环境沙箱级隔离已经彻底大功告成！  
ps -ef | grep mtr_tg_monitor.py | grep -v grep  
现在你的监控服务已经缩进了一个绝对安全的“防火墙沙箱”里，外部其他项目的脚本怎么折腾，都绝对伤不到它一根汗毛。  

#### 5 终极 Systemd 部署步骤
请在你的 RockyLinux 终端直接执行以下三步，一气呵成完成工业级收网：  
  1. 创建 Systemd 服务配置文件
  vi /etc/systemd/system/mtr-monitor.service  
  2. 粘贴以下完美对齐、带安全资源限制的完全体配置：
[Unit]
Description=High-Precision Low-Overhead MTR Telegram Monitor Service
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
# 🟢 精准指向虚拟环境中的 Python 解释器，无需执行 source 激活
ExecStart=/root/mtr_env/bin/python3 /root/mtr_tg_monitor.py
WorkingDirectory=/root

# 🔄 自动重启自愈机制
Restart=always
RestartSec=5s
TimeoutStopSec=5s

# 📊 资源限制（防止内存泄漏撑爆宿主机）
MemoryAccounting=true
MemoryMax=64M
CPUAccounting=true
CPUSchedulingPolicy=other

# 🛡️ 工业级系统安全沙箱加固
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true
CapabilityBoundingSet=CAP_NET_RAW

# 📝 日志管理（交由 Systemd 统一接管，防 nohup.out 撑爆磁盘）
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target



保存并退出 (:wq)。3. 清理旧 nohup 进程，并激活开机自启bash# 1. 强行终止之前通过 nohup 挂在后台的老进程
pkill -f mtr_tg_monitor.py

# 2. 重新加载 Systemd 配置引擎
systemctl daemon-reload

# 3. 开启开机自动启动服务
systemctl enable mtr-monitor.service

# 4. 立刻原地全速启动服务
systemctl start mtr-monitor.service
请谨慎使用此类代码。🏁 运维常用指令小贴士现在你的监控服务已经完全被 RockyLinux 操作系统当成一个标准系统服务来养着了，以后排查只需要用这两行标准的命令：查看服务运行状态、实时内存和 CPU 占用：bashsystemctl status mtr-monitor.service
请谨慎使用此类代码。(💡 你会看到清晰的 Memory: X.XM (limit: 64.0M)，看着它在极低的能耗下稳定奔跑。)查看这个 Python 脚本产生的系统历史日志：bashjournalctl -u mtr-monitor.service -n 50 --no-pager
