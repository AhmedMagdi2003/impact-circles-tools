// Show progress and avoid accidental duplicate submissions while the model responds.
document.querySelectorAll('form[data-ai-wait]').forEach((form) => {
  const button = form.querySelector('button[type="submit"], button:not([type])');
  const status = form.querySelector('[data-ai-status]');
  const originalText = button ? button.textContent : '';
  const initiallyDisabled = button ? button.disabled : false;
  form.addEventListener('submit', (event) => {
    if (form.dataset.submitting === 'true') {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = 'true';
    if (button) { button.disabled = true; button.textContent = 'Please wait…'; }
    if (status) { status.textContent = form.dataset.aiWait; status.classList.remove('hidden'); }
  });
  // Browsers may restore the page from history with the button still disabled.
  window.addEventListener('pageshow', () => {
    form.dataset.submitting = 'false';
    if (button) { button.disabled = initiallyDisabled; button.textContent = originalText; }
    if (status) { status.classList.add('hidden'); status.textContent = ''; }
  });
});

// The browser preview mirrors the server rule. The server always recalculates on confirmation.
const claimForm = document.querySelector('#claim-form');
if (claimForm && document.querySelector('#confirm-button')) {
  const box = document.querySelector('.claim-box');
  const rates = box && box.dataset.rates ? JSON.parse(box.dataset.rates) : {};
  const fx = box && box.dataset.fx ? JSON.parse(box.dataset.fx) : null;
  const claimedDates = new Set(box && box.dataset.claimedDates ? JSON.parse(box.dataset.claimedDates) : []);
  const includeBooking = box && box.dataset.include === 'true';
  const field = (name) => {
    const el = claimForm.elements.namedItem(name);
    return el ? el.value : '';
  };
  const display = (id, value) => {
    const el = document.querySelector(id);
    if (el) el.textContent = value;
  };
  const preview = () => {
    const start = field('start_date');
    const end = field('end_date');
    const amount = Number(field('amount'));
    const currency = field('currency').trim().toUpperCase();
    const travel = ['flight', 'hotel', 'train'].includes(field('document_type'));
    const rate = travel ? Number(rates.EUR || 0) : 0;
    const days = travel && start && end ? Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1 : 0;
    let alreadyClaimed = 0;
    if (travel && days > 0 && days <= 365) {
      const firstDay = Date.parse(start);
      for (let offset = 0; offset < days; offset++) {
        const day = new Date(firstDay + offset * 86400000).toISOString().slice(0, 10);
        if (claimedDates.has(day)) alreadyClaimed++;
      }
    }
    const payableDays = days - alreadyClaimed;
    let error = '';
    if (travel && (!start || !end || days < 1 || days > 365)) error = 'Enter valid first and last travel dates (up to 365 days).';
    else if (!Number.isFinite(amount) || amount < 0) error = 'Enter a valid booking amount.';
    else if (currency.length !== 3) error = 'Enter a three-letter currency code.';
    const needsNewRate = currency !== 'EUR' && (includeBooking || !travel) && (!fx || fx.source_currency !== currency);
    const errorBox = document.querySelector('#claim-error');
    if (errorBox) {
      errorBox.textContent = error;
      errorBox.classList.toggle('hidden', !error);
    }
    const confirmButton = document.querySelector('#confirm-button');
    if (confirmButton) confirmButton.disabled = Boolean(error);
    if (error) return;
    if (needsNewRate) {
      display('#booking-value', 'Check the new currency to get its daily exchange rate.');
      display('#total-value', 'Check the new currency to get its daily exchange rate.');
      if (confirmButton) confirmButton.textContent = 'Check updated total';
      return;
    }
    const booking = includeBooking || !travel ? Math.round((amount * (currency === 'EUR' ? 1 : Number(fx.rate)) + Number.EPSILON) * 100) / 100 : 0;
    display('#booking-value', `${booking.toFixed(2)} EUR${currency !== 'EUR' ? ` (${amount.toFixed(2)} ${currency} × ${fx.rate} EUR; rate dated ${fx.date})` : ''}`);
    display('#allowance-value', `${payableDays} payable ${payableDays === 1 ? 'day' : 'days'} × ${rate.toFixed(2)} EUR = ${(payableDays * rate).toFixed(2)} EUR${alreadyClaimed ? ` (${alreadyClaimed} of ${days} travel days already claimed)` : ''}`);
    display('#total-value', `${(booking + payableDays * rate).toFixed(2)} EUR`);
  };
  claimForm.addEventListener('input', preview);
  preview();
}

// One-click copy for content draft cards
function copyDraft(textareaId, buttonEl) {
  const textarea = document.getElementById(textareaId);
  if (!textarea) return;
  const text = textarea.value || textarea.textContent;
  if (!text) return;

  const originalText = buttonEl ? buttonEl.textContent : 'Copy';
  const onSuccess = () => {
    if (buttonEl) {
      buttonEl.textContent = 'Copied!';
      buttonEl.classList.add('copied');
      setTimeout(() => {
        buttonEl.textContent = originalText;
        buttonEl.classList.remove('copied');
      }, 2000);
    }
  };

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
      textarea.select();
      document.execCommand('copy');
      onSuccess();
    });
  } else {
    textarea.select();
    try {
      document.execCommand('copy');
      onSuccess();
    } catch (e) {
      alert('Please select and copy the text manually.');
    }
  }
}
window.copyDraft = copyDraft;
