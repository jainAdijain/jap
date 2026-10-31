// Runs in the page (pipeline/apply/form.py): every fillable field, with its label, type and
// required flag. Radios and checkboxes sharing a name are one field with options.
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const text = (n) => clean(n ? (n.innerText || n.textContent) : '');
  const shown = (el) => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && (r.width > 0 || r.height > 0);
  };
  const keyOf = (el) => el.name ? el.name : (el.id ? 'id:' + el.id : '');
  const ownLabel = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && text(l)) return text(l);
    }
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = clean(by.split(/\s+/).map((i) => text(document.getElementById(i))).join(' '));
      if (t) return t;
    }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    const wrap = el.closest('label');
    if (wrap && text(wrap)) return text(wrap);
    return '';
  };
  const groupLabel = (el, skip) => {
    const fs = el.closest('fieldset');
    if (fs && fs.querySelector('legend')) return text(fs.querySelector('legend'));
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      for (const l of p.querySelectorAll('label, legend, .application-label, [class*="label" i]')) {
        if (!l.contains(el) && !skip.has(l) && !l.querySelector('input, select, textarea') && text(l))
          return text(l);
      }
    }
    return '';
  };
  const out = [];
  const seen = new Set();
  const form = document.querySelector('form#application-form, form#application_form, form[action*="appl" i]')
    || [...document.querySelectorAll('form')].sort(
      (a, b) => b.querySelectorAll('input, textarea, select').length - a.querySelectorAll('input, textarea, select').length)[0]
    || document.body;
  for (const el of form.querySelectorAll('input, textarea, select')) {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.type || 'text').toLowerCase() : tag;
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(type)) continue;
    if (type !== 'file' && !shown(el)) continue;
    const key = keyOf(el);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    if (type === 'radio' || type === 'checkbox') {
      const group = [...form.querySelectorAll(`input[type="${type}"]`)].filter((o) => keyOf(o) === key);
      const optionLabels = new Set();
      const options = group.map((o) => {
        let l = null;
        if (o.id) l = document.querySelector(`label[for="${CSS.escape(o.id)}"]`);
        if (!l) l = o.closest('label');
        if (l) optionLabels.add(l);
        return clean((l && text(l)) || o.value);
      });
      let label = groupLabel(el, optionLabels);
      if (!label && group.length === 1) label = options[0];
      out.push({
        key, type: type === 'checkbox' && group.length > 1 ? 'checkboxes' : type, label,
        required: group.some((o) => o.required || o.getAttribute('aria-required') === 'true'),
        options, max_chars: null,
      });
      continue;
    }
    const label = ownLabel(el) || groupLabel(el, new Set()) || el.placeholder || el.name || '';
    // A dropdown built from a text input (react-select on Greenhouse job-boards pages):
    // a select whose options form.py lists by opening it.
    if (tag === 'input' && el.getAttribute('role') === 'combobox') {
      out.push({
        key, type: 'select', widget: 'combobox', label,
        required: el.required || el.getAttribute('aria-required') === 'true',
        options: [], max_chars: null,
      });
      continue;
    }
    out.push({
      key, type, label,
      required: el.required || el.getAttribute('aria-required') === 'true',
      options: tag === 'select' ? [...el.options].filter((o) => o.value !== '').map((o) => clean(o.text)) : [],
      max_chars: el.maxLength > 0 ? el.maxLength : null,
    });
  }
  // A choice made of buttons (Ashby's Yes/No questions): two or more short type="button"
  // buttons side by side, under a question. It has no input of its own to name it, so its
  // key is its question; form.py clicks the button whose text is the answer.
  const groups = new Set();
  for (const b of form.querySelectorAll('button[type="button"]')) {
    const box = b.parentElement;
    if (!box || groups.has(box) || !shown(b)) continue;
    const buttons = [...box.children].filter((c) => c.tagName === 'BUTTON' && c.type === 'button');
    if (buttons.length < 2 || buttons.length !== box.children.length) continue;
    const options = buttons.map((x) => text(x));
    if (options.some((o) => !o || o.length > 30)) continue;
    groups.add(box);
    const label = groupLabel(box, new Set());
    if (!label || seen.has('buttons:' + label)) continue;
    seen.add('buttons:' + label);
    out.push({
      key: 'buttons:' + label, type: 'radio', widget: 'buttons', label,
      required: !!box.closest('[aria-required="true"]'), options, max_chars: null,
    });
  }
  const captcha = [...document.querySelectorAll(
    'iframe[src*="recaptcha"][src*="anchor"], iframe[src*="hcaptcha"], .h-captcha, .cf-turnstile, iframe[src*="turnstile"], [data-captcha]'
  )].some((n) => !(n.src || '').includes('size=invisible') && n.getAttribute('data-size') !== 'invisible');
  return { fields: out, captcha, title: document.title };
}
