// Read the installed client's own skill provider without loading a profile or starting a model turn.
import {resolve, join} from 'node:path';
import {pathToFileURL} from 'node:url';

const options = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  const flag = process.argv[index];
  const value = process.argv[index + 1];
  if (!['--runtime', '--home', '--cwd'].includes(flag) || !value || options.has(flag)) {
    throw new Error('Expected one runtime, home and cwd argument');
  }
  options.set(flag, value);
}
if (options.size !== 3) throw new Error('Missing native catalog argument');

const runtime = resolve(options.get('--runtime'));
const modulePath = join(runtime, 'node_modules', '@deepseek-ai', 'dsh-skill-filesystem', 'lib', 'index.js');
const {FileSystemSkillProvider} = await import(pathToFileURL(modulePath).href);
if (typeof FileSystemSkillProvider !== 'function') throw new Error('Native skill provider export unavailable');

const controller = new AbortController();
let warnings = 0;
const context = {get: () => undefined, logger: {warn() { warnings += 1; }}};
const provider = new FileSystemSkillProvider(context, {
  signal: controller.signal,
  invalidate() {},
}, {dshHome: resolve(options.get('--home')), watch: false});
try {
  const observation = await provider.list({cwd: resolve(options.get('--cwd')), signal: controller.signal});
  const complete = Array.isArray(observation) || observation.complete !== false;
  const candidates = Array.isArray(observation) ? observation : observation.candidates;
  if (!Array.isArray(candidates)) throw new Error('Native skill provider did not return candidates');
  const skills = candidates.map(skill => ({
    name: skill.name,
    path: skill.locator?.path,
    source: skill.source,
    visible: skill.invocation?.modelInvocable !== false || skill.invocation?.userInvocable !== false,
  }));
  process.stdout.write(JSON.stringify({complete, native_parse_errors: warnings, skills}) + '\n');
} finally {
  await provider.dispose();
}
