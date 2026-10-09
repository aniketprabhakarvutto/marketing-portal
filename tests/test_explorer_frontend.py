from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class ExplorerFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend checks")
    def test_refresh_keeps_selected_controls_but_requeries_dates_and_account_currency(self):
        script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const elements = new Map();
const $ = (id) => {
  if (!elements.has(id)) elements.set(id, {id:id.slice(1), value:'', hidden:false, textContent:'',
    innerHTML:'', replaceChildren() { this.innerHTML = ''; }});
  return elements.get(id);
};
$('#explorerFilter').value = 'Jaipur';
$('#explorerDirection').value = 'asc';
const $$ = (selector) => selector.includes('dimension')
  ? [{value:'campaign'}, {value:'platform'}] : [{value:'spend'}, {value:'clicks'}];
const state = {start:'2026-09-10', end:'2026-10-09', currency:'INR', tableViews:{}};
const calls = [];
const rendered = [];
const pending = [];
const pendingWorkspace = [];
let deferExplorer = false;
let deferWorkspace = false;
const api = async (path, options) => {
  if (path === '/api/workspace') {
    const workspace = {dashboards:[], views:[], destinations:[], jobs:[], job_runs:[],
      audit:[], governance:{rules:[], users:[], metrics:[]}};
    if (deferWorkspace) return new Promise((resolve) => pendingWorkspace.push(() => resolve(workspace)));
    return workspace;
  }
  if (path.startsWith('/api/planning')) return {attribution:{note:''},
    daily_spend:state.start === '2026-09-10' ? 600 : 150};
  if (path.startsWith('/api/overview')) return {current:{}};
  if (path === '/api/meta') return {scheduler_enabled:true};
  if (path === '/api/explorer/query') {
    const body = JSON.parse(options.body); calls.push(body);
    const spend = body.start === '2026-09-10' ? 600 : 150;
    const result = {columns:['campaign','platform','currency','spend','clicks'], warnings:[],
      rows:[{campaign:'Jaipur', platform:'google_ads', currency:'USD', spend, clicks:10}]};
    if (deferExplorer) return new Promise((resolve) => pending.push(() => resolve(result)));
    return result;
  }
  throw new Error('Unexpected request: ' + path);
};
const context = vm.createContext({state, $, $$, api, currentUser:{role:'admin'},
  qs:() => `start=${state.start}&end=${state.end}`,
  money:(value) => `WRONG-INR-${value}`, num:String, esc:String,
  nf1:{format:String}, moneyInCurrency:(value,currency) => `${currency} ${value}`,
  storedObject:() => ({tblExplorer:['campaign-0','spend-1']}),
  table:(node, columns, rows) => {
    node._tableConfig = {cols:columns};
    node.lastRows = rows;
    if (rows.length) rendered.push(rows.map((row) => columns.map((column) => column.get(row))));
  },
  workspaceItems:() => {}, previewDashboard:() => {},
});
const explorerStart = source.indexOf('async function runExplorer()');
const explorerEnd = source.indexOf('function selectedDashboardMetrics()', explorerStart);
const workspaceStart = source.indexOf('async function loadWorkspace()');
const workspaceEnd = source.indexOf('function confirmWorkspaceRemoval(', workspaceStart);
assert(explorerStart >= 0 && explorerEnd > explorerStart);
assert(workspaceStart >= 0 && workspaceEnd > workspaceStart);
vm.runInContext(source.slice(explorerStart, explorerEnd) +
  source.slice(workspaceStart, workspaceEnd) +
  ';this.testApi={runExplorer,loadWorkspace}', context);
(async () => {
  await context.testApi.loadWorkspace();
  assert.strictEqual(rendered[0][0][3], 'USD 600');
  assert(state.tableViews.tblExplorer.columns.has('spend'), 'Stored Spend preference must survive inserting Currency');
  assert(state.tableViews.tblExplorer.columns.has('currency'), 'Financial reports must identify account currency');
  assert.strictEqual($('#tblExplorer')._tableConfig.cols[3].id, 'spend', 'Explorer column identity must not depend on position');
  state.start = '2026-10-03';
  await context.testApi.loadWorkspace();
  assert.strictEqual(calls.length, 2, 'Changing reporting dates must rerun the explorer');
  assert.strictEqual(calls[1].start, '2026-10-03');
  assert.strictEqual(calls[1].end, '2026-10-09');
  assert.deepStrictEqual(calls[1].dimensions, ['campaign','platform']);
  assert.deepStrictEqual(calls[1].filters, [{field:'campaign',operator:'contains',value:'Jaipur'}]);
  assert.strictEqual(calls[1].direction, 'asc');
  assert.strictEqual(rendered[1][0][3], 'USD 150');
  deferExplorer = true;
  state.start = '2026-09-10';
  const oldRequest = context.testApi.runExplorer();
  state.start = '2026-10-03';
  const newRequest = context.testApi.runExplorer();
  pending[1](); await newRequest;
  const rendersBeforeStaleResponse = rendered.length;
  pending[0](); await oldRequest;
  assert.strictEqual(rendered.length, rendersBeforeStaleResponse, 'An older response must not overwrite the latest range');
  assert.strictEqual(state.explorerDefinition.start, '2026-10-03');
  assert.strictEqual(rendered.at(-1)[0][3], 'USD 150');
  deferExplorer = false; deferWorkspace = true;
  state.start = '2026-09-10';
  const oldWorkspace = context.testApi.loadWorkspace();
  state.start = '2026-10-03';
  const newWorkspace = context.testApi.loadWorkspace();
  pendingWorkspace[1](); await newWorkspace;
  pendingWorkspace[0](); await oldWorkspace;
  assert($('#planningCards').innerHTML.includes('WRONG-INR-150'), 'Delayed Workspace responses must not restore older planning figures');
  assert(!$('#planningCards').innerHTML.includes('WRONG-INR-600'));
  deferExplorer = true;
  state.start = '2026-09-10';
  const olderExplorer = context.testApi.runExplorer();
  state.start = '2026-10-03';
  const pendingNewWorkspace = context.testApi.loadWorkspace();
  const renderedBeforeOldExplorer = rendered.length;
  pending.at(-1)(); await olderExplorer;
  assert.strictEqual(rendered.length, renderedBeforeOldExplorer, 'Old Explorer response must be ignored even while the new Workspace load waits');
  deferExplorer = false;
  pendingWorkspace.at(-1)(); await pendingNewWorkspace;
  state.start = '2026-09-10';
  const pendingDateChange = context.testApi.loadWorkspace();
  assert.strictEqual($('#tblExplorer').lastRows.length, 0, 'Old figures must be cleared while changed dates are loading');
  pendingWorkspace.at(-1)(); await pendingDateChange;
})().catch((error) => {console.error(error); process.exitCode = 1;});
"""
        app = Path(__file__).resolve().parents[1] / "portal" / "web" / "app.js"
        result = subprocess.run(["node", "-e", script, str(app)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
