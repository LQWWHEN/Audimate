// Chrome peer for client.test_recognition_live, using a caller-supplied music fixture.
const {chromium} = require("playwright");
const {readFile, writeFile, access} = require("node:fs/promises");
const path = require("node:path");
const {setTimeout: delay} = require("node:timers/promises");
const [url, wav, scratch] = process.argv.slice(2);

async function run() {
  const browser = await chromium.launch({executablePath:process.env.CHROME_BIN, args:["--mute-audio"]});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(75000);
    await page.goto(url);
    await page.click("#registerTab");
    await page.fill("#name", "web_peer");
    await page.fill("#password", "isolated-room-password");
    await page.click("#submit");
    await page.waitForSelector("#room:visible");
    await page.selectOption("#receive","pcm");
    await page.click("#listen");
    await page.waitForFunction(() => $("source").textContent.includes("I Will Survive") &&
      $("songAlbum").textContent.includes("Love Tracks") && !$("songCover").hidden && playback);
    await writeFile(path.join(scratch,"browser-received"),"ok");
    await page.waitForFunction(() => room && !room.host);
    await page.evaluate(base64 => {
      navigator.mediaDevices.getDisplayMedia = async () => {
        const ctx = new AudioContext(), dest = ctx.createMediaStreamDestination();
        const pcm = Uint8Array.from(atob(base64), c=>c.charCodeAt(0));
        const source = ctx.createBufferSource();
        source.buffer = await ctx.decodeAudioData(pcm.buffer);
        source.loop = true;
        source.connect(dest);
        await ctx.resume();
        source.start();
        const video = document.createElement("canvas").captureStream().getVideoTracks()[0];
        video.getSettings = () => ({displaySurface:"browser"});
        return new MediaStream([video, ...dest.stream.getAudioTracks()]);
      };
    }, (await readFile(wav)).toString("base64"));
    await page.selectOption("#codec","flac");
    await page.check("#recognize");
    await page.click("#stage");
    await page.waitForFunction(() => room?.hosting && $("source").textContent === "尝试匹配中");
    await page.waitForFunction(() => $("songHint").textContent.includes("即将到来"));
    await page.waitForFunction(() => $("source").textContent.includes("I Will Survive") &&
      $("songAlbum").textContent.includes("Love Tracks") && !$("songCover").hidden);
    const deadline = Date.now()+75000;
    while (true) {
      try { await access(path.join(scratch,"desktop-received")); break; }
      catch (e) { if (e.code !== "ENOENT" || Date.now()>deadline) throw e; }
      await delay(100);
    }
    await page.click("#stage");
    await page.waitForFunction(() => room && !room.host);
    console.log("PASS Chrome received desktop metadata and artwork, then published FLAC and confirmed the candidate");
  } finally {
    await browser.close();
  }
}

run().catch(error => {console.error(error); process.exitCode=1;});
