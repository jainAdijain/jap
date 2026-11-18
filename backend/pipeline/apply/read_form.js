// Runs in the page (pipeline/apply/form.py): every fillable field, with its label, type,
// required flag and the value the page holds now. Radios and checkboxes sharing a name are
// one field with options. form.py runs it again just before Submit, to stop when a required
// field is still empty or a CAPTCHA has appeared (JAP-11).
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const text = (n) => clean(n ? (n.innerText || n.textContent) : '');
  const shown = (el) => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && (r.width > 0 || r.height > 0);
  };
  // Ashby wraps each question in a field entry whose title carries the required mark as a
  // class, with the * drawn by CSS, and puts no required attribute on its radios,
  // checkboxes or typeahead (JAP-11).
  const entryOf = (el) => el.closest('.ashby-application-form-field-entry, [class*="fieldEntry" i]');
  const titleOf = (entry) => entry && entry.querySelector(
    '.ashby-application-form-question-title, :scope > label, :scope > legend, :scope > fieldset > legend');
  const markedRequired = (el) => {
    const t = titleOf(entryOf(el));
    return !!t && /required/i.test(t.className || '');
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
    const t = titleOf(entryOf(el));
    if (t && !t.contains(el) && !skip.has(t) && text(t)) return text(t);
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
      const picked = group.map((o, i) => (o.checked ? options[i] : null)).filter((x) => x !== null);
      out.push({
        key, type: type === 'checkbox' && group.length > 1 ? 'checkboxes' : type, label,
        required: group.some((o) => o.required || o.getAttribute('aria-required') === 'true')
          || markedRequired(el),
        options, max_chars: null,
        value: type === 'checkbox' && group.length === 1 ? (el.checked ? 'checked' : '') : picked.join(';'),
      });
      continue;
    }
    const label = ownLabel(el) || groupLabel(el, new Set()) || el.placeholder || el.name || '';
    const required = el.required || el.getAttribute('aria-required') === 'true' || markedRequired(el);
    // A dropdown built from a text input (react-select on Greenhouse job-boards pages, the
    // location typeahead on Ashby): a select whose options form.py lists by opening it. A
    // typeahead lists nothing until something is typed, so it is read with no options.
    if (tag === 'input' && (el.getAttribute('role') === 'combobox'
        || el.getAttribute('aria-autocomplete') === 'list')) {
      out.push({
        key, type: 'select', widget: 'combobox', label, required,
        options: [], max_chars: null, value: null,  // what it shows is read by form.py
      });
      continue;
    }
    let value = el.value || '';
    if (type === 'file') value = [...(el.files || [])].map((f) => f.name).join(';');
    else if (tag === 'select') value = el.selectedIndex >= 0 && el.value !== '' ? clean(el.options[el.selectedIndex].text) : '';
    out.push({
      key, type, label, required,
      options: tag === 'select' ? [...el.options].filter((o) => o.value !== '').map((o) => clean(o.text)) : [],
      max_chars: el.maxLength > 0 ? el.maxLength : null,
      value,
    });
  }
  // A choice made of buttons (Ashby's Yes/No questions): two or more short buttons side by
  // side, under a question. It has no input of its own to name it, so its key is its
  // question; form.py clicks the button whose text is the answer. Ashby's buttons may carry
  // no type attribute, and sit beside a hidden checkbox that holds the value (JAP-9).
  const plain = (c) => c.tagName === 'BUTTON' && (c.getAttribute('type') || 'button') === 'button';
  const groups = new Set();
  for (const b of form.querySelectorAll('button')) {
    const box = b.parentElement;
    if (!plain(b) || !box || groups.has(box) || !shown(b)) continue;
    const buttons = [...box.children].filter(plain);
    const rest = [...box.children].filter((c) => !plain(c));
    if (buttons.length < 2 || rest.some((c) => c.tagName !== 'INPUT' || shown(c))) continue;
    const options = buttons.map((x) => text(x));
    if (options.some((o) => !o || o.length > 30)) continue;
    groups.add(box);
    const label = groupLabel(box, new Set());
    if (!label || seen.has('buttons:' + label)) continue;
    seen.add('buttons:' + label);
    // Which button is pressed, when the page shows it (aria-pressed, or a class such as
    // _active_ or selected); null when it gives no sign either way.
    const on = (x) => x.getAttribute('aria-pressed') === 'true' || x.getAttribute('aria-checked') === 'true'
      || /(^|[\s_-])(active|selected|checked|pressed)/i.test(x.className || '');
    const signals = buttons.some((x) => x.hasAttribute('aria-pressed') || x.hasAttribute('aria-checked') || on(x));
    out.push({
      key: 'buttons:' + label, type: 'radio', widget: 'buttons', label,
      // Ashby marks a required question by a class on its title, with the * drawn by CSS
      required: !!box.closest('[aria-required="true"]')
        || !!box.parentElement?.querySelector(':scope > label[class*="required" i]')
        || markedRequired(box),
      options, max_chars: null,
      value: signals ? buttons.filter(on).map((x) => text(x)).join(';') : null,
    });
  }
  // A CAPTCHA a person must solve: a checkbox widget, or a picture puzzle (a reCAPTCHA or
  // hCaptcha challenge, Arkose/FunCaptcha, GeeTest). JAP never tries to solve one. An
  // invisible reCAPTCHA, and the challenge frame it parks off screen, do not count until
  // the puzzle is actually shown.
  const onScreen = (n) => {
    if (!shown(n)) return false;
    const r = n.getBoundingClientRect();
    return r.bottom + window.scrollY > 0 && r.right + window.scrollX > 0 && r.width > 1 && r.height > 1;
  };
  const captcha = [...document.querySelectorAll(
    'iframe[src*="recaptcha"][src*="anchor"], iframe[src*="recaptcha"][src*="bframe"], iframe[title*="recaptcha challenge" i],'
    + ' iframe[src*="hcaptcha"], .h-captcha, .cf-turnstile, iframe[src*="turnstile"],'
    + ' iframe[src*="arkoselabs"], iframe[src*="funcaptcha"], #FunCaptcha, iframe[src*="geetest"], .geetest_panel,'
    + ' iframe[title*="captcha" i], iframe[title*="verification challenge" i], [data-captcha]'
  )].some((n) => !(n.src || '').includes('size=invisible') && n.getAttribute('data-size') !== 'invisible'
    && (n.matches('[data-captcha]') || onScreen(n)));
  return { fields: out, captcha, title: document.title };
}
