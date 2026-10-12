#!/usr/bin/env node
// Render countdown.html to video, one screenshot per frame.
//
//   node render.js                         -> renders/good-news-intermission-1080p2997.mp4
//   node render.js --stills 0.4,1.7,30,60.2  -> renders/still-<t>.png, for checking a look
//
// Time inside the page is timecode seconds (frame / 30), so on a 29.97 timeline
// the clock changes exactly on each timecode second and reads 0:00 at 00:01:00:00.

const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { chromium } = require('playwright');

const ROOT = __dirname;
const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };

const FPS_NUM = 30000, FPS_DEN = 1001;   // 29.97
const TC_FPS = 30;                       // frames per timecode second
const OUT_DIR = path.join(ROOT, 'renders');

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png',
                '.woff2': 'font/woff2', '.jpg': 'image/jpeg' };

function serve() {
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => {
      const p = path.join(ROOT, decodeURIComponent(new URL(req.url, 'http://x').pathname));
      if (!p.startsWith(ROOT) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(p)] || 'application/octet-stream' });
      fs.createReadStream(p).pipe(res);
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

(async () => {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const srv = await serve();
  const url = `http://127.0.0.1:${srv.address().port}/countdown.html?t=0`;
  const browser = await chromium.launch({ args: ['--force-color-profile=srgb', '--disable-lcd-text'] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on('console', m => console.log('[page]', m.text()));
  page.on('pageerror', e => { console.error('[page error]', e); process.exit(1); });
  await page.goto(url);
  await page.evaluate(() => window.ready);
  const cdp = await page.context().newCDPSession(page);
  const grab = async t => {
    await page.evaluate(t => window.renderFrame(t), t);
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true });
    return Buffer.from(data, 'base64');
  };

  const stills = opt('--stills');
  if (stills) {
    for (const s of stills.split(',')) {
      const f = path.join(OUT_DIR, `still-${s}.png`);
      fs.writeFileSync(f, await grab(parseFloat(s)));
      console.log(f);
    }
  } else {
    const frames = Math.round(await page.evaluate(() => window.CLIP_END) * TC_FPS);
    const out = opt('--out', path.join(OUT_DIR, 'good-news-intermission-1080p2997.mp4'));
    const ff = spawn('ffmpeg', [
      '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', `${FPS_NUM}/${FPS_DEN}`, '-i', '-',
      '-vf', 'scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p',
      '-c:v', 'libx264', '-preset', 'slow', '-crf', '12', '-tune', 'animation', '-profile:v', 'high',
      '-g', '30', '-bf', '2', '-x264-params', 'keyint=30:min-keyint=30',
      '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv',
      '-timecode', '00:00:00:00', '-movflags', '+faststart', out,
    ], { stdio: ['pipe', 'inherit', 'inherit'] });
    const t0 = Date.now();
    for (let i = 0; i < frames; i++) {
      const buf = await grab(i / TC_FPS);
      if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
      if (i % 150 === 0) console.log(`frame ${i}/${frames}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
    }
    ff.stdin.end();
    await new Promise(r => ff.on('close', r));
    console.log('wrote', out);
  }
  await browser.close();
  srv.close();
})();
