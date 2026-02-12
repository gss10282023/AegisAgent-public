# agentbeats/emulator_host/

宿主机侧（本地电脑 / GitHub Actions runner）用于 **Step 2** 的脚本：启动 Android emulator，并把 **adb server** 以可被容器访问的方式运行（默认 `adb -a` 监听非 localhost 的 5037）。

> 安全提醒：`adb -a` 会把 5037 暴露到网卡；请确保只在本机/CI 内网可访问，勿暴露到公网。

## 前置条件（宿主机）

- 已安装 Android SDK **platform-tools**（提供 `adb`）
- 已安装 Android SDK **emulator**（提供 `emulator`）
- 已创建一个 AVD（用于 `AVD_NAME`）

## 使用

```bash
export AVD_NAME="<your_avd_name>"

bash agentbeats/emulator_host/start_adb_server.sh
bash agentbeats/emulator_host/start_emulator.sh
bash agentbeats/emulator_host/wait_until_ready.sh
```

然后跑容器侧 Step 2 验收：

```bash
python3 agentbeats/tools/validate_step2_remote_adb.py
```

## 常见问题：CI 上 Chrome 会弹“首次运行/登录/主题”引导

GitHub Actions 每次都会创建一个全新的 AVD，Chrome 数据也是全新的，所以会弹出首次运行引导界面，影响 `initial_state.url` 的打开。

本目录的 `wait_until_ready.sh` 在系统启动完成后会（默认）写入 Chrome 启动参数以跳过引导：

- `CHROME_DISABLE_FRE=1`（默认）启用；设为 `0` 可关闭
- `CHROME_COMMAND_LINE_FLAGS="--disable-fre --disable-signin-promo"`（可覆盖）
- `CHROME_COMMAND_LINE_FILE=/data/local/tmp/chrome-command-line`（一般无需改）
- `CHROME_PACKAGE=com.android.chrome`（一般无需改）
