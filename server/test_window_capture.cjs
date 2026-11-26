// Windows fixture for test_browser.cjs --native-window. The picker selects only
// a uniquely titled test window. The second process supplies an unrelated tone.
const { spawn } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const path = require("node:path");

const player = `
from host.capture.test_isolation import write_tone, player
import sys, threading, tkinter as tk, winsound
file, frequency, title = sys.argv[1:]
write_tone(file, int(frequency), int(frequency))
if not title:
    player(file)
else:
    ui = tk.Tk()
    ui.title(title)
    tk.Label(ui, text="Audimate audio test", padx=20, pady=18).pack()
    winsound.PlaySound(file, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
    def control():
        for command in sys.stdin:
            action = command.strip()
            if action == "pause":
                winsound.PlaySound(None, 0)
            elif action in ("minimize", "restore"):
                def change_window(action=action):
                    if action == "minimize":
                        ui.iconify()
                    else:
                        ui.deiconify()
                    ui.update_idletasks()
                    print(ui.state(), flush=True)
                ui.after(0, change_window)
        ui.after(0, ui.destroy)
    threading.Thread(target=control, daemon=True).start()
    ui.after(250, lambda: print("ready", flush=True))
    ui.mainloop()
`;

async function startWindowPlayers(directory) {
  const title = "Audimate-test-" + randomUUID();
  const children = [];
  async function start(frequency, windowTitle) {
    const child = spawn(process.env.AUDIMATE_PYTHON || "python",
      ["-c", player, path.join(directory, frequency + ".wav"), String(frequency), windowTitle],
      {windowsHide: true, stdio: ["pipe", "pipe", "inherit"]});
    children.push(child);
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(Error("测试播放器启动超时")), 10000);
      child.once("error", error => { clearTimeout(timeout); reject(error); });
      child.once("exit", () => { clearTimeout(timeout); reject(Error("测试播放器已退出")); });
      child.stdout.once("data", data => {
        clearTimeout(timeout);
        if (data.toString().trim() === "ready") resolve();
        else reject(Error("测试播放器未就绪"));
      });
    });
    return child;
  }
  async function close() {
    await Promise.all(children.map(child => new Promise(resolve => {
      if (!child.pid || child.exitCode !== null || child.signalCode !== null) return resolve();
      const timeout = setTimeout(() => child.kill(), 3000);
      child.once("close", () => { clearTimeout(timeout); resolve(); });
      child.stdin.end();
    })));
  }
  try {
    const target = await start(700, title);
    await start(1800, "");
    return {
      args: ["--auto-select-window-capture-source-by-title=" + title],
      pause: () => target.stdin.write("pause\n"),
      setMinimized(minimized) {
        return new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(Error("播放器窗口状态切换超时")), 5000);
          target.stdout.once("data", data => {
            clearTimeout(timeout);
            if (data.toString().trim() === (minimized ? "iconic" : "normal")) resolve();
            else reject(Error("播放器窗口状态不符：" + data));
          });
          target.stdin.write(minimized ? "minimize\n" : "restore\n");
        });
      },
      close,
    };
  } catch (error) {
    await close();
    throw error;
  }
}

async function verifyWindowIsolation(listener, players) {
  await listener.evaluate(() => {
    window.windowLevels = [];
    const start = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args) {
      const samples = this.buffer.getChannelData(0), rate = this.buffer.sampleRate;
      function amplitude(hz) {
        let real = 0, imaginary = 0;
        for (let i = 0; i < samples.length; i++) {
          const phase = 2 * Math.PI * hz * i / rate;
          real += samples[i] * Math.cos(phase);
          imaginary += samples[i] * Math.sin(phase);
        }
        return 2 * Math.hypot(real, imaginary) / samples.length;
      }
      windowLevels.push([amplitude(700), amplitude(1800)]);
      if (windowLevels.length > 3) windowLevels.shift();
      return start.apply(this, args);
    };
  });
  await listener.waitForFunction(() => windowLevels.length === 3 &&
    windowLevels.every(([target, other]) => target > .002 && other < target * .02));
  players.pause();
  await listener.waitForFunction(() => windowLevels.every(([target, other]) => target < .0002 && other < .0002));
  console.log("PASS: native window audio reached listener, second program excluded, paused target silent");
}

module.exports = { startWindowPlayers, verifyWindowIsolation };
