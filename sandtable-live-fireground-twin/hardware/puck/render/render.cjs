// Headless product renders of the puck and the brick (three.js in the cached Playwright Chromium, SwiftShader GL).
// Run from the project root:
//   NP=$(ls -d ~/.npm/_npx/*/node_modules/playwright | head -1); NODE_PATH="${NP%/playwright}" node hardware/puck/render/render.cjs
const { chromium } = require('playwright');
const fs = require('fs'); const path = require('path');
const ROOT = path.resolve(__dirname, '..', '..', '..');
const R = path.join(ROOT, 'hardware', 'puck', 'render');
const res = JSON.parse(fs.readFileSync(path.join(ROOT, 'hardware', 'puck', 'results', 'puck_build.json')));
const D = res.derived, P = res.params;

const ORANGE = '#e8661f', CHAR = '#3a3e44', GREY = '#c9cdd1', PCB = '#1b3a6b', STEEL = '#a7adb3';
const shell = (file, name) => ({ file, name, color: ORANGE, rough: 0.55, clearcoat: 0.08 });
const puckParts = (o = {}) => [
  shell('shell_bottom.stl', 'bottom'), shell('shell_top.stl', 'top'),
  { file: 'boot_tpu.stl', name: 'boot', color: CHAR, rough: 0.78, sheen: 0.4 },
  { file: 'port_plug_tpu.stl', name: 'plug', color: CHAR, rough: 0.78 },
  { file: 'board_sled.stl', name: 'sled', color: GREY, rough: 0.5 },
  { file: 'board_pcb.stl', name: 'pcb', color: PCB, rough: 0.45 },
  { file: 'board_dwm3001c_module.stl', name: 'module', color: '#d9dce0', rough: 0.35, metal: 0.25 },
  { file: 'board_jlink.stl', name: 'jlink', color: '#16181b', rough: 0.5 },
  { file: 'board_usb.stl', name: 'usb', color: STEEL, rough: 0.35, metal: 0.25 },
  { file: 'board_rpi_header.stl', name: 'header', color: '#121315', rough: 0.6 },
  { file: 'board_back_parts.stl', name: 'back', color: '#25282c', rough: 0.6 },
  { file: 'cell.stl', name: 'cell', color: '#2f7fc0', rough: 0.35, clearcoat: 0.6 },
  { file: 'ballast.stl', name: 'ballast', color: '#b4b9be', rough: 0.35, metal: 0.3 },
  { file: 'charger.stl', name: 'charger', color: '#2c6b40', rough: 0.5 },
  { file: 'switch.stl', name: 'switch', color: '#16181b', rough: 0.5 },
  { file: 'data_socket.stl', name: 'socket', color: STEEL, rough: 0.35, metal: 0.25 },
].map(p => ({ ...p, offset: o[p.name] || [0, 0, 0] }));

const sph = (az, el, dist, tz) => { const a = az * Math.PI / 180, e = el * Math.PI / 180;
  return [Math.cos(a) * Math.cos(e) * dist, Math.sin(a) * Math.cos(e) * dist, tz + Math.sin(e) * dist]; };

const jobs = [];
// 1. hero render
jobs.push({ out: 'puck_render.png', cfg: { width: 1600, height: 1100, exposure: 1.0,
  camera: { pos: sph(204, 27, 470, 40), target: [0, 0, 42], fov: 20 },
  parts: puckParts(), labels: [],
  title: 'Sandtable Beacon puck', subtitle: `Ø ${(2 * P.R).toFixed(0)} mm, ${D.H.toFixed(0)} mm tall, about ${Math.round(res.mass_total_g / 10) * 10} g. Self-righting: one resting pose, antenna up.`,
  foot: 'Render of the CAD model (build_puck.py). PETG shells, TPU boot. Board outline is a placeholder until measured.' } });

// 2. exploded
const ex = { ballast: [0, 0, 52], charger: [0, 0, 58], cell: [0, 0, 74], sled: [0, 0, 112], pcb: [0, 0, 112], module: [0, 0, 112], jlink: [0, 0, 112],
  usb: [0, 0, 112], header: [0, 0, 112], back: [0, 0, 112], top: [0, 0, 205], boot: [0, 0, 300] };
const zc = D.zc;
jobs.push({ out: 'puck_exploded.png', cfg: { width: 1500, height: 1700, exposure: 1.0, shadowOpacity: 0.1, key: [-40, 60, 900],
  camera: { pos: sph(128, 16, 1200, 170), target: [0, 0, 170], fov: 22 },
  parts: puckParts(ex),
  labels: [
    { text: 'TPU boot (bumper)', sub: 'rolls on last; covers seam and screws', part: 'boot', anchor: [-38, 38, zc - 6], dx: -270, dy: -10 },
    { text: 'Top shell, PETG', sub: 'dome, id pad, light pipe', part: 'top', anchor: [-30, 30, 82], dx: -250, dy: -40 },
    { text: 'DWM3001CDK on its sled', sub: 'module + antennas up; outline to be measured', part: 'module', anchor: [-6, 14, 66], dx: 290, dy: -40 },
    { text: 'Board sled, PETG', sub: 'reprint for another outline', part: 'sled', anchor: [-18, 10, 30], dx: -260, dy: 10 },
    { text: '18650 cell', sub: 'saddles + two cable ties', part: 'cell', anchor: [-25, -7.5, 25], dx: -280, dy: -10 },
    { text: 'Charger / 5 V boost', sub: 'USB-C behind the TPU plug', part: 'charger', anchor: [0, 27, 11.2], dx: -330, dy: -20 },
    { text: 'Ballast: 8 x M8 washers', sub: 'DIN 9021, on printed pegs', part: 'ballast', anchor: [-13, -7.5, 10.3], dx: 300, dy: 10 },
    { text: 'Bottom shell, PETG', sub: 'flat base, wells, saddles, lip', part: 'bottom', anchor: [-30, 40, 25], dx: 290, dy: 50 },
    { text: 'Power switch', sub: 'pressed through a TPU membrane', part: 'switch', anchor: [-35, -35, 24], dx: -280, dy: 40 },
  ],
  title: 'Exploded view', subtitle: 'Beacon puck: 5 printed parts, 1 board, 1 cell, 8 washers, 3 screws',
  foot: 'Offsets are along the assembly axis. Stand-ins for the bought parts are simplified boxes and cylinders.' } });

// 3. brick
const bp = (file, name, color, o = {}) => ({ file, name, color, ...o });
const lidUp = [0, 0, 62];
jobs.push({ out: 'puck_brick.png', cfg: { width: 1600, height: 1100, exposure: 1.0,
  camera: { pos: sph(232, 30, 900, 30), target: [0, 0, 52], fov: 22 }, key: [-160, -320, 460],
  parts: [
    bp('brick_tray.stl', 'tray', '#3a3f45', { rough: 0.55 }),
    bp('brick_lid.stl', 'lid', ORANGE, { rough: 0.42, clearcoat: 0.25, offset: lidUp }),
    bp('brick_mast.stl', 'mast', ORANGE, { rough: 0.42, clearcoat: 0.25, offset: lidUp }),
    bp('brick_dwm3001cdk.stl', 'dwm', PCB, { rough: 0.45, offset: lidUp }),
    bp('brick_dwm_module.stl', 'dwmmod', '#d7dade', { rough: 0.3, metal: 0.7, offset: [lidUp[0], lidUp[1], lidUp[2] + 0.05] }),
    bp('brick_bank.stl', 'bank', '#e9e9e6', { rough: 0.6 }),
    bp('brick_unoq.stl', 'unoq', '#127a83', { rough: 0.45 }),
    bp('brick_dongle.stl', 'dongle', '#5a5f66', { rough: 0.4, metal: 0.3 }),
  ],
  labels: [
    { text: 'DWM3001CDK on the mast', sub: 'antenna about 12 cm above the table', part: 'dwm', anchor: [58, 40, 110], dx: 240, dy: -60 },
    { text: 'Lid, PETG', sub: 'vents over the UNO Q; lifted to show inside', part: 'lid', anchor: [-60, -50, 24.8], dx: -200, dy: -150 },
    { text: 'Arduino UNO Q', sub: 'UNO hole pattern, M3 on standoffs', part: 'unoq', anchor: [40, -15, 12], dx: 300, dy: 90 },
    { text: 'Battery bank', sub: 'owner\'s; bay to be measured', part: 'bank', anchor: [-50, -10, 17], dx: -240, dy: 120 },
    { text: 'USB-C dongle (PD in)', sub: 'size to be measured', part: 'dongle', anchor: [50, 30, 14], dx: 300, dy: -10 },
  ],
  title: 'Variant B: the command brick', subtitle: `UNO Q host + battery bank + one DWM3001CDK. ${res.brick.outer_mm[0]} x ${res.brick.outer_mm[1]} x ${res.brick.outer_mm[2]} mm, placed, not thrown.`,
  foot: 'Render of the CAD model (build_puck.py). Bank, dongle and DWM3001CDK sizes are placeholders until measured.' } });

(async () => {
  const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const only = process.argv[2];
  for (const j of jobs) {
    if (only && j.out !== only) continue;
    const page = await browser.newPage({ viewport: { width: j.cfg.width, height: j.cfg.height } });
    page.on('console', m => { if (m.type() === 'error') console.log('console:', m.text()); });
    page.on('pageerror', e => console.log('pageerror:', e.message));
    await page.route('http://puck.local/**', async (route) => {
      const u = new URL(route.request().url()); let p = u.pathname.slice(1); let f;
      if (p === 'three.module.js') f = path.join(ROOT, 'web', 'vendor', 'three.module.js');
      else if (p.startsWith('fonts/')) f = path.join(ROOT, 'web', 'vendor', p);
      else if (p.startsWith('parts/')) f = path.join(R, p);
      else f = path.join(R, p);
      const ct = f.endsWith('.js') ? 'application/javascript' : f.endsWith('.html') ? 'text/html' : f.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream';
      await route.fulfill({ body: fs.readFileSync(f), contentType: ct });
    });
    await page.addInitScript((c) => { window.RENDER_CFG = c; }, j.cfg);
    await page.goto('http://puck.local/render.html');
    await page.waitForFunction(() => window.RENDER_DONE === true, null, { timeout: 180000 });
    const out = path.join(ROOT, 'figures', j.out);
    await page.screenshot({ path: out });
    console.log('wrote', out);
    await page.close();
  }
  await browser.close();
})();
