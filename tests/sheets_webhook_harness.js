// Local Apps Script simulation. Run: node tests/sheets_webhook_harness.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const rows = { Finance: [['header']], Content: [['header']] };
const props = { SPREADSHEET_ID: 'test-sheet', SHEETS_WEBHOOK_SECRET: 'test-secret-at-least-24-characters' };
const context = {
  console,
  PropertiesService: { getScriptProperties: () => ({ getProperty: key => props[key] }) },
  LockService: { getScriptLock: () => {
    let held = false;
    return { waitLock: () => { held = true; }, hasLock: () => held, releaseLock: () => { held = false; } };
  } },
  SpreadsheetApp: {
    openById: () => ({ getSheetByName: name => rows[name] && ({
      getLastRow: () => rows[name].length,
      getRange: (start, column, height) => ({
        getValues: () => rows[name].slice(start - 1, start - 1 + height).map(row => [row[0]]),
        setValues: values => { rows[name][start - 1] = values[0]; },
      }),
    }) }),
    flush: () => {},
  },
  ContentService: {
    MimeType: { JSON: 'json' },
    createTextOutput: text => ({ text, setMimeType() { return this; } }),
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('scripts/google_apps_script/SheetsWebhook.gs', 'utf8'), context);
const call = body => JSON.parse(context.doPost({ postData: { contents: JSON.stringify(body) } }).text);
const secret = props.SHEETS_WEBHOOK_SECRET;
const finance = Array(26).fill('');
finance[0] = 'F-1'; finance[2] = '=unsafe'; finance[14] = '12.34';
assert.equal(call({ secret, tab: 'Finance', values: finance }).status, 'ok');
assert.equal(rows.Finance[1][2], "'=unsafe");
assert.equal(rows.Finance[1][14], 12.34);
assert.equal(call({ secret, tab: 'Finance', values: finance }).id, 'F-1');
assert.equal(rows.Finance.length, 2);
const content = Array(13).fill(''); content[0] = 'C-1';
assert.equal(call({ secret, tab: 'Content', values: content }).status, 'ok');
assert.equal(call({ secret: 'incorrect', tab: 'Content', values: content }).status, 'error');
assert.equal(call({ secret, tab: 'Content', values: finance }).status, 'error');
assert.equal(rows.Content.length, 2);
console.log('Apps Script simulation passed');
