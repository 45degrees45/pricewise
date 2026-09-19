// PriceWise — Community Spots API
// Deploy as: Execute as Me · Anyone can access
// Handles GET /spots and POST /report from the price guide page.

var SHEET_ID   = '13oXhZHtL4b2X1WWy7rlm0MBx9sKtk9aQ-9AHZwQ0yOw';
var SHEET_NAME = 'Spots';
var COLUMNS    = ['Date', 'Item', 'Shop', 'Location', 'Price', 'Unit', 'Quality', 'Reporter', 'Notes'];

// ── Helpers ────────────────────────────────────────────────────────

function _cors(output) {
  return output
    .setMimeType(ContentService.MimeType.JSON);
  // Apps Script web apps deployed as "Anyone" automatically send CORS headers.
}

function _getSheet() {
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(SHEET_NAME);
    sh.appendRow(COLUMNS);
  }
  return sh;
}

function _sheetToRecords(sh) {
  var values = sh.getDataRange().getValues();
  if (values.length < 2) return [];
  var headers = values[0];
  return values.slice(1).map(function(row) {
    var obj = {};
    headers.forEach(function(h, i) { obj[h] = row[i]; });
    return obj;
  });
}

// ── GET /spots ─────────────────────────────────────────────────────

function doGet(e) {
  try {
    var records = _sheetToRecords(_getSheet());
    // Sort by Price ascending
    records.sort(function(a, b) {
      return parseFloat(a.Price || 0) - parseFloat(b.Price || 0);
    });
    return _cors(ContentService.createTextOutput(JSON.stringify(records)));
  } catch (err) {
    return _cors(ContentService.createTextOutput(JSON.stringify({ error: err.message })));
  }
}

// ── POST /report ───────────────────────────────────────────────────

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);

    // Validate required fields
    if (!data.item || !data.shop || data.price === undefined || data.price === '') {
      return _cors(ContentService.createTextOutput(
        JSON.stringify({ ok: false, error: 'Missing required fields: item, shop, price' })
      ));
    }

    var sh   = _getSheet();
    var date = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');

    sh.appendRow([
      date,
      String(data.item  || '').trim(),
      String(data.shop  || '').trim(),
      String(data.location || '').trim(),
      parseFloat(data.price),
      String(data.unit || 'per kg'),
      data.quality ? parseInt(data.quality) : '',
      String(data.reporter || 'Anonymous').trim() || 'Anonymous',
      String(data.notes || '').trim(),
    ]);

    return _cors(ContentService.createTextOutput(JSON.stringify({ ok: true })));
  } catch (err) {
    return _cors(ContentService.createTextOutput(JSON.stringify({ ok: false, error: err.message })));
  }
}
