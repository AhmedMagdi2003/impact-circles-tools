// Configure SPREADSHEET_ID and SHEETS_WEBHOOK_SECRET in Script Properties.
// Deploy as a web app that executes as the organization-controlled account.
function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    const props = PropertiesService.getScriptProperties();
    const body = JSON.parse(e.postData.contents);
    const secret = props.getProperty('SHEETS_WEBHOOK_SECRET');
    if (!secret || secret.length < 24 || body.secret !== secret) throw new Error('Unauthorized');
    const widths = { Finance: 26, Content: 13 };
    if (!Object.prototype.hasOwnProperty.call(widths, body.tab) ||
        !Array.isArray(body.values) || body.values.length !== widths[body.tab]) throw new Error('Invalid row');
    const id = body.values[0];
    if (typeof id !== 'string' || !(body.tab === 'Finance' ? /^F-\d+$/ : /^C-\d+$/).test(id)) {
      throw new Error('Invalid ID');
    }
    lock.waitLock(20000);
    const sheet = SpreadsheetApp.openById(props.getProperty('SPREADSHEET_ID')).getSheetByName(body.tab);
    if (!sheet) throw new Error('Missing tab');
    const count = sheet.getLastRow();
    const ids = count > 1 ? sheet.getRange(2, 1, count - 1, 1).getValues() : [];
    if (!ids.some(row => String(row[0]) === id)) {
      const values = body.values.map((value, index) => {
        const text = value == null ? '' : String(value);
        if (body.tab === 'Finance' && index >= 10 && index <= 14 && /^\d+(\.\d+)?$/.test(text)) {
          return Number(text);
        }
        // Treat generated and extracted values as text, never sheet formulas.
        return /^\s*[=+@-]/.test(text) ? "'" + text : text;
      });
      sheet.getRange(Math.max(2, count + 1), 1, 1, values.length).setValues([values]);
      SpreadsheetApp.flush();
    }
    return reply({status: 'ok', id: id});
  } catch (error) {
    console.error('Sheets sync failed: ' + String(error.message || error));
    // Do not disclose request data or credentials in the response.
    return reply({status: 'error'});
  } finally {
    if (lock.hasLock()) lock.releaseLock();
  }
}

function reply(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
