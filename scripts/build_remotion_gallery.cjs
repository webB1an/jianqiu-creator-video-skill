/* Rebuild the offline viewer from the same catalog and JSX that the renderer uses. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createRequire} = require('node:module');
const {execFileSync} = require('node:child_process');

const skill = path.resolve(__dirname, '..');
const library = path.join(skill, 'assets', 'remotion-library');
const runtime = process.env.CREATOR_REMOTION || path.join(os.homedir(), '.cache', 'creator-video-motion', 'remotion');
const runtimeRequire = createRequire(path.join(runtime, 'package.json'));
const esbuild = runtimeRequire('esbuild');
const entries = JSON.parse(execFileSync(process.env.PYTHON || 'python3', [
  path.join(__dirname, 'remotion_library.py'), 'list', '--json',
], {encoding: 'utf8'}));
const groups = [...new Set(entries.map(entry => entry.group))];
const imports = groups.map((group, i) => `import * as group${i} from './${group}/effects.jsx';`);
const components = entries.map(entry => `${JSON.stringify(entry.id)}: group${groups.indexOf(entry.group)}.${entry.component}`);
const registry = [...imports, 'export const entries = ' + JSON.stringify(entries) + ';',
  'export const views = {' + components.join(',') + '};'].join('\n');

async function build() {
  const result = await esbuild.build({
    entryPoints: [path.join(library, 'gallery.jsx')],
    outfile: path.join(library, 'gallery.min.js'),
    format: 'iife', platform: 'browser', target: 'es2020',
    bundle: true, minify: true, legalComments: 'linked', metafile: true, write: false,
    define: {'process.env.NODE_ENV': '"production"'},
    nodePaths: [path.join(runtime, 'node_modules')],
    plugins: [{
      name: 'local-remotion-catalog',
      setup(builder) {
        builder.onResolve({filter: /^\.\/gallery-registry\.js$/}, () => ({path: 'catalog', namespace: 'local-catalog'}));
        builder.onLoad({filter: /.*/, namespace: 'local-catalog'}, () => ({contents: registry, resolveDir: library}));
      },
    }],
  });
  const modules = path.join(runtime, 'node_modules') + path.sep;
  const importedPackages = new Set(Object.keys(result.metafile.inputs).flatMap(input => {
    const absolute = path.resolve(input);
    if (!absolute.startsWith(modules)) return [];
    const parts = absolute.slice(modules.length).split(path.sep);
    return [parts[0].startsWith('@') ? parts.slice(0, 2).join('/') : parts[0]];
  }));
  const notices = [];
  for (const name of [...importedPackages].sort()) {
    const folder = path.join(modules, name);
    const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'package.json'), 'utf8'));
    notices.push(name + ' ' + manifest.version + ' — ' + (manifest.license || ''));
    for (const file of fs.readdirSync(folder).sort().filter(file => /^(?:third_party_)?licen[sc]e(?:s)?(?:\.|$)/i.test(file))) {
      notices.push(fs.readFileSync(path.join(folder, file), 'utf8').trim());
    }
  }
  const noticePath = path.join(library, 'gallery.licenses.txt');
  fs.writeFileSync(noticePath, notices.join('\n\n') + '\n');
  for (const file of result.outputFiles) {
    const temporary = file.path + '.' + process.pid + '.tmp';
    fs.writeFileSync(temporary, file.contents);
    fs.renameSync(temporary, file.path);
  }
  console.log(`${entries.length} effects → ${path.join(library, 'index.html')}`);
}
build().catch(error => {console.error(error.message); process.exitCode = 1;});
