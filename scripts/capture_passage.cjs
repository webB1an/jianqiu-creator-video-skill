/** Capture a real article and locate an exact passage on that same browser raster. */
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function locate(quote, selector, scroll) {
  const roots = selector ? document.querySelectorAll(selector)
    : [document.querySelector('article') || document.querySelector('main') || document.body];
  if (roots.length !== 1) throw new Error('Choose a CSS selector matching one visible passage container.');
  const walker = document.createTreeWalker(roots[0], NodeFilter.SHOW_TEXT);
  const fragments = [];
  const chars = [];
  const offsets = [];
  let previousBlock;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    let el = node.parentElement;
    if (el.closest('script,style,noscript,template') || !el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) continue;
    while (el.parentElement && ['inline','contents'].includes(getComputedStyle(el).display)) el = el.parentElement;
    if (previousBlock && previousBlock !== el && chars.at(-1) !== ' ') {
      chars.push(' '); offsets.push(null);
    }
    previousBlock = el;
    const index = fragments.push(node) - 1;
    for (let i=0; i<node.length; i++) {
      const char = /\s/.test(node.data[i]) ? ' ' : node.data[i];
      if (char === ' ' && chars.at(-1) === ' ') continue;
      chars.push(char); offsets.push([index,i]);
    }
  }
  const haystack = chars.join('');
  const search = quote.trim().replace(/\s+/g, ' ');
  const start = haystack.indexOf(search);
  if (start < 0) throw new Error('The exact passage was not found in this container.');
  if (haystack.indexOf(search, start+1) >= 0) throw new Error('Passage is ambiguous. Supply a longer quote or a narrower CSS selector.');
  const [first, left] = offsets[start], [last, right] = offsets[start+search.length-1];
  const lines = [];
  const parents = [];
  for (let i=first; i<=last; i++) {
    const range = new Range();
    range.setStart(fragments[i], i === first ? left : 0);
    range.setEnd(fragments[i], i === last ? right+1 : fragments[i].length);
    const style = getComputedStyle(fragments[i].parentElement);
    if (style.writingMode !== 'horizontal-tb' || style.direction !== 'ltr') throw new Error('Use manual line bounds for this writing direction.');
    for (const r of range.getClientRects()) {
      if (r.width > 0 && r.height > 0) {
        lines.push({x:r.x, y:r.y, w:r.width, h:r.height});
        parents.push(fragments[i].parentElement);
      }
    }
  }
  if (!lines.length) throw new Error('Passage has no visible text rectangles.');
  const top = Math.min(...lines.map(r=>r.y));
  const bottom = Math.max(...lines.map(r=>r.y+r.h));
  if (scroll) {
    window.scrollTo({top:window.scrollY+(top+bottom-innerHeight)/2,behavior:'instant'});
    return;
  }
  for (const [index,r] of lines.entries()) {
    if (r.x < 0 || r.y < 0 || r.x+r.w > innerWidth || r.y+r.h > innerHeight)
      throw new Error('The whole passage must fit within the capture viewport.');
    const hit = document.elementFromPoint(r.x+r.w/2,r.y+r.h/2);
    if (!hit?.contains(parents[index])) throw new Error('A different page element covers the passage. Choose an unobstructed source.');
  }
  return {lines, title:document.title, url:location.href, scroll:[scrollX,scrollY], dpr:devicePixelRatio};
}

async function main() {
  const [url, quote, directory, selector] = process.argv.slice(2);
  if (!url || !quote?.trim() || !directory || process.argv.length > 6)
    throw new Error('Usage: node capture_passage.cjs URL "exact quote" NEW-DIRECTORY [CSS-SELECTOR]');
  if (!['http:','https:','file:'].includes(new URL(url).protocol)) throw new Error('Use an http, https or local file URL.');
  let puppeteer;
  try { puppeteer = require('puppeteer-core'); }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    puppeteer = require(path.join(os.homedir(), '.cache/creator-video-motion/hyperframes/node_modules/puppeteer-core'));
  }
  const executablePath = process.env.CREATOR_CHROME || ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'].find(fs.existsSync);
  if (!executablePath) throw new Error('Set CREATOR_CHROME to the installed Chromium executable.');
  const output = path.resolve(directory);
  if (fs.existsSync(output)) throw new Error('Choose a new directory for this capture.');
  const viewport = {width:1440, height:900, deviceScaleFactor:2};
  const browser = await puppeteer.launch({headless:true, executablePath});
  try {
    const page = await browser.newPage();
    await page.setViewport(viewport);
    const response = await page.goto(url,{waitUntil:'domcontentloaded'});
    if (response && !response.ok()) throw new Error('Source page returned HTTP '+response.status());
    const settle = () => page.evaluate(async() => {
      await document.fonts.ready;
      await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));
    });
    await settle();
    await page.evaluate(locate,quote,selector??null,true);
    await settle();
    const measured = await page.evaluate(locate,quote,selector??null,false);
    const png = await page.screenshot({type:'png'});
    const after = await page.evaluate(locate,quote,selector??null,false);
    if (JSON.stringify(measured) !== JSON.stringify(after)) throw new Error('The page layout moved during capture. Retry after it settles.');
    const {lines} = measured;
    const x = Math.min(...lines.map(r=>r.x)), y = Math.min(...lines.map(r=>r.y));
    const right = Math.max(...lines.map(r=>r.x+r.w)), bottom = Math.max(...lines.map(r=>r.y+r.h));
    if ((bottom-y)*16/9 > viewport.width) throw new Error('Passage is taller than the delivery frame. Split at a natural reading break.');
    const width = Math.min(viewport.width, Math.max((right-x)*1.18,(bottom-y)*16/9*1.22,240));
    const spec = {clean:'source.png',source:{url:measured.url,title:measured.title,quote,scroll:measured.scroll,selector:selector||null},
      clip:{x:0,y:0,width:viewport.width,height:viewport.height},dpr:measured.dpr,lines,
      marker:'#F5C768',view_from:[viewport.width/2,viewport.height/2,viewport.width],view_to:[(x+right)/2,(y+bottom)/2,width],
      move_frames:[10,30],reveal_frames:[32,48],frames:90,fps:30,size:[1920,1080]};
    fs.mkdirSync(path.dirname(output), {recursive:true});
    const staging = fs.mkdtempSync(path.join(path.dirname(output),'.passage-'));
    try {
      fs.writeFileSync(path.join(staging,'source.png'),png);
      fs.writeFileSync(path.join(staging,'shot.json'),JSON.stringify(spec,null,2)+'\n');
      fs.renameSync(staging,output);
    } finally {
      fs.rmSync(staging,{recursive:true,force:true});
    }
    console.log(path.join(output,'shot.json'));
  } finally { await browser.close(); }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
