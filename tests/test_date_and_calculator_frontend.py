from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "portal" / "web" / "app.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend checks")
class DateAndCalculatorFrontendTests(unittest.TestCase):
    def run_node(self, script: str) -> None:
        result = subprocess.run(["node", "-e", script, str(APP)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dismissing_date_draft_restores_committed_ask_ai_dates(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const elements = new Map();
const $ = (selector) => {
  if (!elements.has(selector)) elements.set(selector, {
    value:'',hidden:false,textContent:'',innerHTML:'',disabled:false,
    setAttribute() {},
  });
  return elements.get(selector);
};
const state = {start:'2026-10-01',end:'2026-10-08'};
$('#start').value = state.start;
$('#end').value = state.end;
const context = vm.createContext({$,state,Date,Math,Number,String,
  calendarDate:(value)=>value,
  monthIso:(value)=>`${value.slice(0,7)}-01`,
  portalTodayIso:()=> '2026-10-09',
  shiftIso:(value,amount)=>{
    const d = new Date(`${value}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate()+amount);
    return d.toISOString().slice(0,10);
  },
  syncCalendarWheelSelection:()=>{},esc:String,requestAnimationFrame:()=>{},
});
const closeStart = source.indexOf('function closeDateRange()');
const closeEnd = source.indexOf('let calendarDraftStart',closeStart);
const renderStart = source.indexOf('function renderCalendar(focusDay = "")');
const renderEnd = source.indexOf('function openDateRange()',renderStart);
vm.runInContext(`let calendarMonth='2026-10-01';
 let calendarDraftStart='2026-10-04';let calendarDraftEnd='2026-10-07';
 ${source.slice(closeStart,closeEnd)}
 ${source.slice(renderStart,renderEnd)}
 this.testApi={renderCalendar,closeDateRange};`,context);
context.testApi.renderCalendar();
assert.strictEqual($('#start').value,state.start,'draft start must never replace committed input');
assert.strictEqual($('#end').value,state.end,'draft end must never replace committed input');
$('#dateRangeMenu').hidden = false;
context.testApi.closeDateRange();
assert.strictEqual($('#start').value,state.start,'Ask AI must use committed start');
assert.strictEqual($('#end').value,state.end,'Ask AI must use committed end');
""")

    def test_campaign_fetch_reads_every_page_before_returning_totals(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('async function fetchAllCampaigns(');
assert(start >= 0,'Campaign report needs a paginated fetch helper');
const end = source.indexOf('/* ------------------------------------------------------------------- ga4 */',start);
const calls = [];
const context = vm.createContext({
  api:async(path) => {
    calls.push(path);
    const offset = Number(new URL(`http://local${path}`).searchParams.get('offset'));
    return offset === 0 ? Array.from({length:1000},(_,i)=>({campaign:`c${i}`,spend:1}))
      : [{campaign:'last',spend:3}];
  },
  qs:()=> 'start=2026-10-08&end=2026-10-08',
});
vm.runInContext(`${source.slice(start,end).split('function renderPaid()')[0]};this.testApi={fetchAllCampaigns};`,context);
(async()=>{
  const rows = await context.testApi.fetchAllCampaigns('google_ads');
  assert.strictEqual(rows.length,1001);
  assert.strictEqual(rows.reduce((sum,row)=>sum+row.spend,0),1003);
  assert.strictEqual(calls.length,2);
  assert(calls[0].includes('limit=1000&offset=0'));
  assert(calls[1].includes('limit=1000&offset=1000'));
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_campaign_selection_reloads_values_when_reporting_date_changes(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const elements = new Map();
const $ = (selector) => {
  if (!elements.has(selector)) {
    const node = {value:'',innerHTML:'',textContent:'',disabled:false,dataset:{},
      classList:{add(){},remove(){}}};
    if (selector === '#metricCampaignSelect') Object.defineProperty(node,'innerHTML',{
      get(){return this._html || ''},set(html){this._html=html;this.value='';},
    });
    if (selector === '#metricValueA' || selector === '#metricValueB') Object.defineProperty(node,'value',{
      get(){return this._value || ''},set(value){this._value=String(value)},
    });
    elements.set(selector,node);
  }
  return elements.get(selector);
};
const state = {start:'2026-10-07',end:'2026-10-07',currency:'INR',campaignSrc:'google_ads'};
const row = (spend)=>({source:'google_ads',account_id:'a',campaign_id:'c1',
  campaign:'Google_Supply_Jaipur',currency:'INR',spend,clicks:10,
  crm_leads:5,crm_sales:1,crm_leads_source:'Superleap CRM'});
const context = vm.createContext({$,state,esc:String,nf:{format:String},
  metricFormatter:(format,value)=>`INR ${value}`,
  METRIC_FIELDS:[['spend','Spend'],['crm_leads','CRM leads'],['crm_sales','CRM sales']],
  PAID_PLATFORM_META:{google_ads:{label:'Google Ads',badge:'google'}},
  qs:()=>`start=${state.start}&end=${state.end}`,
  api:async()=>[row(state.start==='2026-10-07'?100:200)],
  fetchAllCampaigns:async()=>[row(state.start==='2026-10-07'?100:200)],
});
const start = source.indexOf('  const metricPresets = {');
const end = source.indexOf('  $("#metricPresetSelect").addEventListener',start);
vm.runInContext(`(function(){${source.slice(start,end)};
 this.testApi={loadMetricCampaigns,fillMetricFromCampaign};}).call(this)`,context);
(async()=>{
  await context.testApi.loadMetricCampaigns();
  $('#metricCampaignSelect').value='0';
  context.testApi.fillMetricFromCampaign();
  assert.strictEqual($('#metricValueA').value,'100');
  state.start='2026-10-08';state.end='2026-10-08';
  await context.testApi.loadMetricCampaigns();
  assert.strictEqual($('#metricCampaignSelect').value,'0','same campaign must remain selected');
  assert.strictEqual($('#metricValueA').value,'200','auto-filled spend must follow selected date');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_paid_table_distinguishes_unmatched_crm_from_matched_zero(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('const PAID_COLUMNS = [');
const end = source.indexOf('const METRIC_FIELDS = [',start);
const context = vm.createContext({
  PAID_PLATFORM_META:{},cssVar:()=>'',esc:String,campaignFunnel:()=>'',
  moneyInCurrency:()=>'',num:(value)=>String(value),dec:String,pct:String,
});
vm.runInContext(`${source.slice(start,end)};this.columns=PAID_COLUMNS`,context);
for (const id of ['crm_leads','crm_sales']) {
  const column = context.columns.find((entry)=>entry.id===id);
  assert.strictEqual(column.get({crm_leads:0,crm_sales:0,crm_leads_source:null}),
    '—',`${id} without attribution must be unavailable`);
  assert.strictEqual(column.get({crm_leads:0,crm_sales:0,crm_leads_source:'Superleap CRM'}),
    '0',`${id} with an actual CRM match may be zero`);
}
""")

    def test_calculator_does_not_use_unmatched_crm_zero_as_denominator(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  const metricPresets = {');
const end = source.indexOf('  const fillMetricFromCampaign =',start);
const context = vm.createContext({state:{start:'2026-10-08',end:'2026-10-08'}});
vm.runInContext(`(function(){${source.slice(start,end)};
this.testApi={values:portalValuesForPreset,presets:metricPresets};}).call(this)`,context);
const {values,presets} = context.testApi;
const missing = values(presets.cac,{spend:100,crm_sales:0,crm_leads_source:null});
assert.strictEqual(missing[1],null,'unmatched CRM sales are not verified zero');
const matched = values(presets.cac,{spend:100,crm_sales:0,crm_leads_source:'Superleap CRM'});
assert.strictEqual(matched[1],0,'matched CRM sales can be zero');
""")

    def test_paid_table_escapes_connector_controlled_labels(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('const PAID_COLUMNS = [');
const end = source.indexOf('const METRIC_FIELDS = [',start);
const escapeHtml = (value) => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;')
  .replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
const context = vm.createContext({
  PAID_PLATFORM_META:{},cssVar:()=>'',esc:escapeHtml,campaignFunnel:()=>'',
  moneyInCurrency:()=>'',num:String,dec:String,pct:String,
});
vm.runInContext(`${source.slice(start,end)};this.columns=PAID_COLUMNS`,context);
const malicious = '<img src=https://example.invalid/pixel>';
assert.strictEqual(context.columns.find((entry)=>entry.id==='platform').get({source:malicious}),
  '&lt;img src=https://example.invalid/pixel&gt;');
assert.strictEqual(context.columns.find((entry)=>entry.id==='type').get({channel:malicious}),
  '&lt;img src=https://example.invalid/pixel&gt;');
""")

    def test_date_preset_name_is_updated_instead_of_duplicated(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const store = new Map([['calendarDatePresets',JSON.stringify([
  {id:'saved-1',label:'Last 14 Days',days:14}])]]);
const nodes = new Map();
const $ = (selector) => {
  if (!nodes.has(selector)) nodes.set(selector,{
    value:'',innerHTML:'',focus(){},addEventListener(event,callback){this[event]=callback},
  });
  return nodes.get(selector);
};
const context = vm.createContext({$,Date,Number,JSON,
  DEFAULT_DATE_PRESETS:[],
  localStorage:{getItem:(key)=>store.get(key),setItem:(key,value)=>store.set(key,value)},
  saveUiPreferenceRaw:(key,value)=>store.set(key,value),
  modal:()=>{},closeModal:()=>{},toast:()=>{},esc:String,
});
const start = source.indexOf('function getCustomDatePresets()');
const end = source.indexOf('function monthIso(',start);
vm.runInContext(`${source.slice(start,end)};this.createPreset=openDatePresetCreator`,context);
context.createPreset();
$('#datePresetName').value=' last 14 DAYS ';
$('#datePresetDays').value='21';
$('#saveDatePreset').click();
const presets = JSON.parse(store.get('calendarDatePresets'));
assert.strictEqual(presets.length,1,'same label must not appear twice');
assert.strictEqual(presets[0].days,21,'existing preset should receive new duration');
assert.strictEqual(presets[0].id,'saved-1','upsert must preserve removal identity');
""")

    def test_budget_pacing_uses_observed_budgeted_campaign_days(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  const metricPresets = {');
const end = source.indexOf('  const fillMetricFromCampaign =',start);
const context = vm.createContext({state:{start:'2026-10-01',end:'2026-10-30'}});
vm.runInContext(`(function(){${source.slice(start,end)};
selectedMetricPreset='budget_pacing';
this.testApi={values:portalValuesForPreset,presets:metricPresets};}).call(this)`,context);
const row = {spend:500,daily_budget:100,period_budget:200,budgeted_spend:200,
  reported_days:5,budgeted_days:2,selected_days:30,budget_coverage_percent:40};
const value = context.testApi.values(context.testApi.presets.budget_pacing,row);
assert.strictEqual(value[0],200,'spend must be scoped to budgeted days');
assert.strictEqual(value[1],200,'planned budget must sum observed campaign-day budgets');
""")

    def test_older_campaign_request_cannot_replace_new_date_and_source(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const elements = new Map();
const $ = (selector) => {
  if (!elements.has(selector)) {
    const node = {value:'',textContent:'',disabled:false,dataset:{},innerHTML:'',
      classList:{add(){},remove(){}}};
    if (selector === '#metricCampaignSelect') Object.defineProperty(node,'innerHTML',{
      get(){return this._html || ''},set(html){this._html=html;this.value='';},
    });
    elements.set(selector,node);
  }
  return elements.get(selector);
};
const state = {start:'2026-10-07',end:'2026-10-07',campaignSrc:'google_ads',currency:'INR'};
const pending = [];
const context = vm.createContext({$,state,esc:String,nf:{format:String},
  METRIC_FIELDS:[],PAID_PLATFORM_META:{},metricFormatter:()=>'',
  fetchAllCampaigns:(source)=>new Promise((resolve)=>pending.push({source,resolve})),
});
const start = source.indexOf('  const metricPresets = {');
const end = source.indexOf('  $("#metricPresetSelect").addEventListener',start);
vm.runInContext(`(function(){${source.slice(start,end)};
this.loadMetricCampaigns=loadMetricCampaigns;}).call(this)`,context);
(async()=>{
  const oldRequest = context.loadMetricCampaigns();
  state.start='2026-10-08';state.end='2026-10-08';state.campaignSrc='meta_ads';
  const newRequest = context.loadMetricCampaigns();
  assert.strictEqual(pending.length,2);
  pending[1].resolve([{campaign:'New Meta campaign',source:'meta_ads'}]);
  await newRequest;
  pending[0].resolve([{campaign:'Old Google campaign',source:'google_ads'}]);
  await oldRequest;
  assert($('#metricCampaignSelect').innerHTML.includes('New Meta campaign'));
  assert(!$('#metricCampaignSelect').innerHTML.includes('Old Google campaign'));
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_portal_total_calculator_recomputes_on_date_change(self) -> None:
        self.run_node(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const elements = new Map();
const $ = (selector) => {
  if (!elements.has(selector)) {
    const node = {value:'',textContent:'',disabled:false,dataset:{},innerHTML:'',
      classList:{add(){},remove(){}}};
    if (selector === '#metricCampaignSelect') Object.defineProperty(node,'innerHTML',{
      get(){return this._html || ''},set(html){this._html=html;this.value='';},
    });
    elements.set(selector,node);
  }
  return elements.get(selector);
};
const state = {start:'2026-10-07',end:'2026-10-07',campaignSrc:'',currency:'INR'};
$('#metricInputMode').value='portal';
const context = vm.createContext({$,state,esc:String,nf:{format:String},
  METRIC_FIELDS:[['spend','Spend'],['clicks','Clicks']],PAID_PLATFORM_META:{},
  metricFormatter:(format,value)=>`INR ${value}`,
  fetchAllCampaigns:async()=>[{source:'google_ads',currency:'INR',campaign:'Google Supply',
    spend:state.start==='2026-10-07'?100:200,clicks:10}],
});
const start = source.indexOf('  const metricPresets = {');
const end = source.indexOf('  $("#metricPresetSelect").addEventListener',start);
vm.runInContext(`(function(){${source.slice(start,end)};
selectedMetricPreset='cpc';this.testApi={loadMetricCampaigns,updateMetricResult};}).call(this)`,context);
(async()=>{
  await context.testApi.updateMetricResult();
  assert($('#metricCalculatorResult').innerHTML.includes('INR 10'));
  state.start='2026-10-08';state.end='2026-10-08';
  await context.testApi.loadMetricCampaigns();
  assert($('#metricCalculatorResult').innerHTML.includes('INR 20'),
    'portal totals must recompute when date changes');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


if __name__ == "__main__":
    unittest.main()
