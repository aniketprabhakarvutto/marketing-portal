from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class UiPreferenceFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend checks")
    def test_hydration_migration_account_isolation_and_updates(self) -> None:
        script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('const UI_PREFERENCE_KEYS =');
const end = source.indexOf('function applyRoleUI(', start);
assert(start >= 0 && end > start);
const storage = new Map([
  ['savedPortalViews', JSON.stringify([{id:'legacy', name:'Old view'}])],
  ['campaignColumns', JSON.stringify(['browser-column'])],
]);
const localStorage = {
  getItem: (key) => storage.has(key) ? storage.get(key) : null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
let server = {campaignColumns: ['server-column']};
const posts = [];
let nextTimer = 0;
const timers = new Map();
const api = async (path, opts) => {
  assert.strictEqual(path, '/api/ui-preferences');
  if (!opts) return {preferences: {...server}};
  const payload = JSON.parse(opts.body);
  posts.push(payload);
  if (payload.value === null) delete server[payload.key];
  else server[payload.key] = payload.value;
  return {preferences: {...server}};
};
const context = vm.createContext({api, localStorage, toast: () => {},
  setTimeout: (fn) => { const id = ++nextTimer; timers.set(id, fn); return id; },
  clearTimeout: (id) => timers.delete(id),
});
vm.runInContext(source.slice(start, end) + ';this.prefs={hydrateUiPreferences,saveUiPreferenceRaw,removeUiPreference,uiPreferenceWrites}', context);
(async () => {
  await context.prefs.hydrateUiPreferences({id: 7});
  assert.strictEqual(storage.get('campaignColumns'), JSON.stringify(['server-column']));
  assert.deepStrictEqual(server.savedPortalViews, [{id:'legacy', name:'Old view'}]);
  assert(!posts.some((item) => item.key === 'campaignColumns'));
  assert.strictEqual(storage.get('portalUiPreferencesMigratedFor'), '7');

  context.prefs.saveUiPreferenceRaw('customMetrics', JSON.stringify([{name:'CPL'}]));
  await context.prefs.uiPreferenceWrites.get('customMetrics');
  assert.deepStrictEqual(server.customMetrics, [{name:'CPL'}]);
  context.prefs.removeUiPreference('customMetrics');
  await context.prefs.uiPreferenceWrites.get('customMetrics');
  assert(!Object.hasOwn(server, 'customMetrics'));

  context.prefs.saveUiPreferenceRaw('campaignWidths', JSON.stringify({spend: 100}));
  context.prefs.saveUiPreferenceRaw('campaignWidths', JSON.stringify({spend: 180}));
  assert.strictEqual(timers.size, 1);
  const pending = [...timers.values()][0]; timers.clear(); pending();
  await context.prefs.uiPreferenceWrites.get('campaignWidths');
  assert.deepStrictEqual(server.campaignWidths, {spend: 180});
  assert.strictEqual(posts.filter((item) => item.key === 'campaignWidths').length, 1);

  const postCount = posts.length;
  server = {};
  await context.prefs.hydrateUiPreferences({id: 8});
  assert.strictEqual(storage.get('portalUiPreferencesMigratedFor'), '8');
  assert(!storage.has('savedPortalViews'));
  assert(!storage.has('campaignColumns'));
  assert.strictEqual(posts.length, postCount);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        app = Path(__file__).resolve().parents[1] / "portal" / "web" / "app.js"
        result = subprocess.run(
            ["node", "-e", script, str(app)], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
