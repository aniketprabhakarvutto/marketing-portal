from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class WorkspaceFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend checks")
    def test_reload_preserves_valid_selections_and_updates_dashboard_figures(self):
        script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const elements = new Map();
const selectIds = new Set(['#savedDashboards','#savedExplorerViews','#jobDestination']);
const $ = (id) => {
  if (!elements.has(id)) {
    const node = {value:'', hidden:false, textContent:'', disabled:false};
    let html = '';
    Object.defineProperty(node, 'innerHTML', {
      get:() => html,
      set:(value) => {html = value; if (selectIds.has(id)) node.value = '';},
    });
    node.replaceChildren = () => {node.innerHTML = '';};
    elements.set(id, node);
  }
  return elements.get(id);
};
const state = {start:'2026-10-03',end:'2026-10-09'};
let workspace = {dashboards:[{id:1,name:'Leadership',layout:['spend','sales']}],
  views:[{id:2,name:'Jaipur'}], destinations:[{id:3,name:'Reports',kind:'folder',enabled:true}],
  jobs:[],job_runs:[],audit:[],governance:{rules:[],users:[],metrics:[]}};
let deferred = false;
let finish;
const context = vm.createContext({state,$,currentUser:{role:'admin'},
  api:async(path) => {
    if (path === '/api/workspace') {
      if (deferred) return new Promise(resolve => {finish = () => resolve(workspace);});
      return workspace;
    }
    if (path.startsWith('/api/planning')) return {attribution:{note:''}};
    if (path.startsWith('/api/overview')) return {current:{spend:state.start === '2026-10-03' ? 100 : 700,sales:5}};
    if (path === '/api/meta') return {scheduler_enabled:true};
    throw new Error('Unexpected request ' + path);
  },
  qs:() => `start=${state.start}&end=${state.end}`, esc:String, num:String,
  money:(value)=>`INR ${value}`, nf1:{format:String},
  workspaceItems:()=>{},runExplorer:async()=>{},table:()=>{},
});
const constantsStart = source.indexOf('const DASHBOARD_METRICS =');
const workspaceStart = source.indexOf('async function loadWorkspace()');
const workspaceEnd = source.indexOf('function confirmWorkspaceRemoval(',workspaceStart);
const previewStart = source.indexOf('function previewDashboard(');
const previewEnd = source.indexOf('async function saveWorkspaceObject(',previewStart);
vm.runInContext(source.slice(constantsStart,workspaceStart)+
  source.slice(workspaceStart,workspaceEnd)+source.slice(previewStart,previewEnd)+
  ';this.testApi={loadWorkspace,previewDashboard}',context);
(async()=>{
  await context.testApi.loadWorkspace();
  $('#savedDashboards').value = '1';
  $('#savedExplorerViews').value = '2';
  $('#jobDestination').value = '3';
  context.testApi.previewDashboard('1');
  assert($('#dashboardPreview').innerHTML.includes('INR 100'));
  state.start = '2026-09-10';
  deferred = true;
  const refresh = context.testApi.loadWorkspace();
  assert(!$('#dashboardPreview').innerHTML.includes('INR 100'), 'Old dashboard figures must clear during reload');
  assert($('#dashboardPreview').textContent.includes('Loading'), 'Loading dashboard state should be visible');
  finish(); await refresh;
  assert.strictEqual($('#savedDashboards').value,'1','Dashboard selection must survive date changes');
  assert.strictEqual($('#savedExplorerViews').value,'2','Saved view selection must survive reload');
  assert.strictEqual($('#removeExplorerView').disabled,false);
  assert.strictEqual($('#jobDestination').value,'3','Draft job destination must survive reload');
  assert.strictEqual($('#dashboardPreview').hidden,false);
  assert($('#dashboardPreview').innerHTML.includes('INR 700'), 'Selected dashboard must show new range figures');
  workspace = {...workspace,dashboards:[],views:[],destinations:[]};
  deferred = false;
  await context.testApi.loadWorkspace();
  assert.strictEqual($('#savedDashboards').value,'');
  assert.strictEqual($('#savedExplorerViews').value,'');
  assert.strictEqual($('#removeExplorerView').disabled,true);
  assert.strictEqual($('#jobDestination').value,'');
  assert.strictEqual($('#dashboardPreview').hidden,true,'Removed dashboard preview must clear');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        app = Path(__file__).resolve().parents[1] / "portal" / "web" / "app.js"
        result = subprocess.run(["node", "-e", script, str(app)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
