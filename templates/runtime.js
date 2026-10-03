// Generic Paper-to-Playground runtime. Builds the whole page from the embedded spec JSON.
// Contains nothing concept-specific; see docs/spec_contract.md.
(function () {
  'use strict';
  var SVGNS = 'http://www.w3.org/2000/svg';
  var W = 600; // chart width in SVG units; set per chart to its container width (300–600) so text stays full size

  // ---------- small DOM helpers ----------
  function el(tag, attrs, parent, text) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'hidden') e.hidden = !!attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function sv(tag, attrs, parent) {
    var e = document.createElementNS(SVGNS, tag);
    if (attrs) for (var k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function svText(x, y, str, attrs, parent) {
    var a = Object.assign({ x: x, y: y }, attrs || {});
    var t = sv('text', a, parent);
    t.textContent = svgLabel(str);
    return t;
  }

  // ---------- rich text (tag allowlist, no attributes) ----------
  // Parsed in an inert DOMParser document (no scripts run, nothing loads), then rebuilt:
  // allowed tags are recreated without attributes, other tags are unwrapped to their text,
  // and DROP tags are removed with their content. A "<" that does not look like a tag
  // (e.g. "a<b and x > y": no "=" in the would-be attributes) is kept as a literal "<".
  var ALLOWED = { B: 1, I: 1, EM: 1, STRONG: 1, SUB: 1, SUP: 1, CODE: 1, BR: 1 };
  var DROP = { SCRIPT: 1, STYLE: 1, TEMPLATE: 1, IFRAME: 1, OBJECT: 1, EMBED: 1, NOSCRIPT: 1, TEXTAREA: 1, TITLE: 1, SVG: 1, MATH: 1 };
  var TAG_LIKE = /<(\/?[a-zA-Z][a-zA-Z0-9]*(?:\s+[^<>]*=[^<>]*)?\s*\/?>)?/g;
  function rich(target, str) {
    if (str == null) return target;
    var src = String(str).replace(TAG_LIKE, function (m, tag) { return tag ? m : '&lt;'; });
    var doc = new DOMParser().parseFromString('<!doctype html><body>' + src + '</body>', 'text/html');
    (function walk(from, to) {
      for (var i = 0; i < from.childNodes.length; i++) {
        var n = from.childNodes[i];
        if (n.nodeType === 3) to.appendChild(document.createTextNode(n.nodeValue));
        else if (n.nodeType === 1) {
          var tag = n.tagName.toUpperCase();
          if (ALLOWED[tag]) walk(n, to.appendChild(document.createElement(tag)));
          else if (!DROP[tag]) walk(n, to);
        }
      }
    })(doc.body, target);
    return target;
  }
  function plain(str) { var d = document.createElement('div'); rich(d, str); return d.textContent; }
  // For SVG <text>: turn <sub>/<sup> into Unicode sub/superscripts where possible.
  var SUB = { '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉', '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎', a: 'ₐ', e: 'ₑ', h: 'ₕ', i: 'ᵢ', j: 'ⱼ', k: 'ₖ', l: 'ₗ', m: 'ₘ', n: 'ₙ', o: 'ₒ', p: 'ₚ', r: 'ᵣ', s: 'ₛ', t: 'ₜ', u: 'ᵤ', v: 'ᵥ', x: 'ₓ' };
  var SUP = { '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '+': '⁺', '-': '⁻', '−': '⁻', '=': '⁼', '(': '⁽', ')': '⁾', i: 'ⁱ', n: 'ⁿ', T: 'ᵀ' };
  function svgLabel(str) {
    if (str == null) return '';
    var s = String(str).replace(/<(sub|sup)>(.*?)<\/\1>/gi, function (_, tag, inner) {
      var map = tag.toLowerCase() === 'sub' ? SUB : SUP, t = plain(inner), out = '';
      for (var i = 0; i < t.length; i++) { if (!map[t[i]]) return (tag.toLowerCase() === 'sub' ? '_' : '^') + t; out += map[t[i]]; }
      return out;
    });
    return plain(s);
  }

  // ---------- numbers ----------
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function fmt(v, digits) {
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'string') return v;
    if (typeof v !== 'number') return v == null ? '—' : String(v);
    if (isNaN(v)) return 'NaN';
    if (!isFinite(v)) return v > 0 ? '∞' : '−∞';
    var d = digits == null ? 3 : digits;
    var a = Math.abs(v);
    if (a < 1e-12) { v = 0; a = 0; } // floating-point noise
    if (a !== 0 &&(a >= 1e5 || a < Math.pow(10, -d))) return v.toExponential(2).replace('-', '−');
    var s = v.toFixed(d);
    if (/^-0\.?0*$/.test(s)) s = s.slice(1);
    return s.replace('-', '−');
  }
  function clamp(v, lo, hi) { if (isNum(lo) && v < lo) v = lo; if (isNum(hi) && v > hi) v = hi; return v; }
  function deepCopy(x) { return JSON.parse(JSON.stringify(x)); }
  function niceTicks(lo, hi, count) {
    if (!(hi > lo)) return [lo];
    var raw = (hi - lo) / (count || 5);
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var r = raw / mag;
    var step = (r < 1.5 ? 1 : r < 3 ? 2 : r < 7 ? 5 : 10) * mag;
    var out = [], start = Math.ceil(lo / step - 1e-9) * step;
    for (var t = start; t <= hi + step * 1e-9; t += step) out.push(Math.abs(t) < step * 1e-9 ? 0 : t);
    return out;
  }
  function tickFmt(t, ticks) {
    var step = ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : 1;
    var d = step >= 1 ? 0 : Math.min(6, Math.ceil(-Math.log10(step) - 1e-9));
    return fmt(t, d);
  }
  function linScale(d0, d1, r0, r1) {
    var k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
    return function (v) { return r0 + (v - d0) * k; };
  }
  function seriesColor(i) { return 'var(--s' + ((i % 5) + 1) + ')'; }

  // ---------- spec normalization: coerce model output to the contract's shapes ----------
  function isObj(x) { return x !== null && typeof x === 'object' && !Array.isArray(x); }
  function asArr(x) { return Array.isArray(x) ? x : (x == null ? [] : [x]); }
  function objs(x) { return asArr(x).filter(isObj); }
  function str(x) { return typeof x === 'string' ? x : (typeof x === 'number' ? String(x) : ''); }
  function strs(x) { return asArr(x).map(str).filter(Boolean); }
  function normalize(raw) {
    var s = isObj(raw) ? raw : {};
    s.title = str(s.title); s.subtitle = str(s.subtitle); s.audience = str(s.audience);
    var it = isObj(s.intro) ? s.intro : { idea: str(s.intro) };
    it.idea = str(it.idea); it.why = str(it.why); it.steps = strs(it.steps); it.symbols = objs(it.symbols);
    s.intro = it;
    var seen = {};
    s.controls = objs(s.controls).filter(function (c) {
      if (typeof c.id !== 'string' || !c.id || seen[c.id]) return false;
      return (seen[c.id] = true);
    });
    s.presets = objs(s.presets).map(function (p) { p.state = isObj(p.state) ? p.state : {}; return p; });
    s.compute = typeof s.compute === 'string' ? s.compute : null;
    s.visuals = objs(s.visuals);
    s.explorations = objs(s.explorations).map(function (x) {
      x.state = isObj(x.state) ? x.state : null; x.expect = objs(x.expect); return x;
    });
    s.limitations = strs(s.limitations);
    s.tests = objs(s.tests).map(function (t) { t.state = isObj(t.state) ? t.state : {}; t.expect = objs(t.expect); return t; });
    var src = isObj(s.source) ? s.source : { paper: str(s.source) };
    src.supported = asArr(src.supported).filter(function (x) { return typeof x === 'string' || isObj(x); });
    src.simplifications = strs(src.simplifications);
    s.source = src;
    return s;
  }

  // ---------- spec + state ----------
  var spec;
  try { spec = normalize(JSON.parse(document.getElementById('spec').textContent)); }
  catch (e) { bootError('The page specification could not be read: ' + e.message); return; }
  var controls = spec.controls;
  var byId = {};
  controls.forEach(function (c) { if (c && c.id) byId[c.id] = c; });

  function resolve(path, out, state) {
    if (typeof path !== 'string') return path;
    var parts = path.split('.'), v = out;
    if (parts[0] === 'state') { v = state; parts.shift(); }
    for (var i = 0; i < parts.length; i++) { if (v == null) return undefined; v = v[parts[i]]; }
    return v;
  }
  function labelsFor(spec_, n, out, state, prefix) {
    if (Array.isArray(spec_)) return range(n).map(function (i) { return spec_[i] != null ? String(spec_[i]) : String(i + 1); });
    if (typeof spec_ === 'string') {
      var r = resolve(spec_, out || {}, state || {});
      if (Array.isArray(r)) return labelsFor(r, n);
      if (spec_.indexOf('{i}') >= 0) return range(n).map(function (i) { return spec_.replace(/\{i\}/g, i + 1); });
    }
    return range(n).map(function (i) { return (prefix || '') + (i + 1); });
  }
  function range(n) { var a = []; for (var i = 0; i < n; i++) a.push(i); return a; }
  function dim(c, key, st, fallback) {
    var from = c[key + '_from'];
    if (from && st[from] != null) return Math.max(0, Math.round(Number(st[from]) || 0));
    if (isNum(c[key])) return Math.max(0, Math.round(c[key]));
    return fallback;
  }
  function toNum(v, d) { var x = Number(v); return isFinite(x) ? x : d; }

  function scalarDefault(c) {
    if (c.type === 'toggle') return !!c.default;
    if (c.type === 'select') {
      var opts = c.options || [];
      if (c.default != null) return c.default;
      return opts.length ? (opts[0].value != null ? opts[0].value : opts[0]) : null;
    }
    var v = toNum(c.default, isNum(c.min) ? c.min : 0);
    return clamp(v, c.min, c.max);
  }
  // Resize vectors/matrices to match their *_from controls; keep existing values.
  function conform(st) {
    controls.forEach(function (c) {
      if (c.type === 'vector') {
        var def = Array.isArray(c.default) ? c.default : [];
        var n = dim(c, 'length', st, def.length);
        var cur = Array.isArray(st[c.id]) ? st[c.id] : [];
        st[c.id] = range(n).map(function (i) {
          return toNum(cur[i] != null ? cur[i] : (def[i] != null ? def[i] : c.fill), 0);
        });
      } else if (c.type === 'matrix') {
        var dm = Array.isArray(c.default) ? c.default : [];
        var rows = dim(c, 'rows', st, dm.length);
        var cols = dim(c, 'cols', st, dm[0] ? dm[0].length : 0);
        var cm = Array.isArray(st[c.id]) ? st[c.id] : [];
        st[c.id] = range(rows).map(function (i) {
          return range(cols).map(function (j) {
            var v = cm[i] && cm[i][j] != null ? cm[i][j] : (dm[i] && dm[i][j] != null ? dm[i][j] : c.fill);
            return toNum(v, 0);
          });
        });
      }
    });
    return st;
  }
  function defaultState() {
    var st = {};
    controls.forEach(function (c) {
      if (c.type === 'vector' || c.type === 'matrix') st[c.id] = deepCopy(c.default || []);
      else st[c.id] = scalarDefault(c);
    });
    return conform(st);
  }
  // Merge a partial state: scalars first (they drive sizes), then arrays, then resize.
  function mergeState(base, partial) {
    var st = deepCopy(base);
    if (!partial) return st;
    Object.keys(partial).forEach(function (k) {
      var c = byId[k];
      if (!c || c.type === 'vector' || c.type === 'matrix') return;
      if (c.type === 'toggle') st[k] = !!partial[k];
      else if (c.type === 'select') st[k] = partial[k];
      else st[k] = clamp(toNum(partial[k], st[k]), c.min, c.max);
    });
    Object.keys(partial).forEach(function (k) {
      var c = byId[k];
      if (c && (c.type === 'vector' || c.type === 'matrix') && Array.isArray(partial[k])) st[k] = deepCopy(partial[k]);
      else if (!c) st[k] = deepCopy(partial[k]);
    });
    return conform(st);
  }

  function compileFn(src, name) {
    if (typeof src !== 'string' || !src.trim()) throw new Error(name + ' is missing');
    var f;
    try { f = new Function('"use strict"; return (' + src + '\n);')(); }
    catch (e) { f = new Function('state', 'out', 'h', src); }
    if (typeof f !== 'function') throw new Error(name + ' is not a function');
    return f;
  }

  var computeFn = null, computeErr = null;
  try { computeFn = compileFn(spec.compute, 'compute()'); } catch (e) { computeErr = e; }
  function runCompute(st) {
    if (!computeFn) throw computeErr || new Error('compute() unavailable');
    var out = computeFn(deepCopy(st));
    if (!out || typeof out !== 'object') throw new Error('compute() returned no result object');
    return out;
  }

  var state = defaultState();
  var lastOut = null;

  // ---------- sections ----------
  function section(id) { return document.querySelector('#' + id + ' .body') || document.getElementById(id); }
  function guard(name, fn) {
    try { fn(); }
    catch (e) {
      var host = document.getElementById(name);
      var target = (host && (host.querySelector('.body') || host)) || document.body;
      el('div', { class: 'banner error' }, target, 'This part of the page failed to build: ' + e.message);
      if (window.console) console.error(e);
    }
  }

  function buildHero() {
    var h = document.getElementById('hero');
    rich(el('h1', null, h), spec.title || 'Interactive explanation');
    if (spec.subtitle) rich(el('p', { class: 'subtitle' }, h), spec.subtitle);
    var chips = el('div', { class: 'chips' }, h);
    var s = spec.source || {};
    if (s.paper) el('span', { class: 'chip paper' }, chips, plain(s.paper) + (s.year ? ' (' + s.year + ')' : '') + (s.section ? ' · ' + plain(s.section) : ''));
    if (spec.audience) el('span', { class: 'chip' }, chips, 'For: ' + spec.audience);
  }

  function buildIntro() {
    var b = section('intro'), it = spec.intro || {};
    if (it.idea) { el('h3', null, b, 'What it is'); rich(el('p', { class: 'lead' }, b), it.idea); }
    if (it.why) { el('h3', null, b, 'Why it matters'); rich(el('p', null, b), it.why); }
    if (Array.isArray(it.steps) && it.steps.length) {
      el('h3', null, b, 'How it works');
      var ol = el('ol', { class: 'steps' }, b);
      it.steps.forEach(function (s) { rich(el('li', null, ol), s); });
    }
    if (Array.isArray(it.symbols) && it.symbols.length) {
      el('h3', null, b, 'Symbols');
      var wrap = el('div', { class: 'table-wrap' }, b);
      var t = el('table', { class: 'symtab' }, wrap);
      var hr = el('tr', null, el('thead', null, t));
      ['Symbol', 'Meaning', 'Unit / range'].forEach(function (x) { el('th', null, hr, x); });
      var tb = el('tbody', null, t);
      it.symbols.forEach(function (s) {
        var r = el('tr', null, tb);
        rich(el('td', null, r), s.symbol); rich(el('td', null, r), s.meaning); rich(el('td', null, r), s.unit || '');
      });
    }
  }

  // ---------- controls ----------
  var ctrlHosts = {};
  function buildControls() {
    var host = document.getElementById('controls');
    controls.forEach(function (c) {
      ctrlHosts[c.id] = el('div', { class: 'ctrl', 'data-control': c.id }, host);
      guardCtrl(c);
    });
    var ph = document.getElementById('presets');
    (spec.presets || []).forEach(function (p) {
      var b = el('button', { type: 'button' }, ph, plain(p.label || 'Preset'));
      b.addEventListener('click', function () { setState(mergeState(state, p.state)); });
    });
    var reset = el('button', { type: 'button' }, ph, 'Reset');
    reset.addEventListener('click', function () { setState(defaultState()); });
  }
  function guardCtrl(c) {
    var host = ctrlHosts[c.id];
    host.textContent = '';
    try { buildControl(c, host); }
    catch (e) { el('div', { class: 'verr' }, host, 'Control "' + c.id + '" failed: ' + e.message); }
  }
  function dependents(id) {
    return controls.filter(function (c) { return c.length_from === id || c.rows_from === id || c.cols_from === id; });
  }
  function changed(id) {
    var deps = dependents(id);
    if (deps.length) { conform(state); deps.forEach(guardCtrl); }
    update();
  }
  function setState(st) {
    state = st;
    controls.forEach(guardCtrl);
    update();
  }
  function labelRow(c, host, valueText) {
    var l = el('label', null, host);
    var name = el('span', null, l); rich(name, c.label || c.id);
    var v = null;
    if (valueText != null) v = el('span', { class: 'val' }, l, valueText);
    return v;
  }
  function numInput(attrs, value, onVal) {
    var i = el('input', Object.assign({ type: 'number' }, attrs));
    i.value = String(value);
    i.addEventListener('input', function () {
      if (i.value === '' || i.value === '-') return;
      var x = Number(i.value);
      if (isFinite(x)) onVal(x);
    });
    i.addEventListener('change', function () {
      var x = Number(i.value);
      if (!isFinite(x)) x = 0;
      x = clamp(x, attrs.min, attrs.max);
      i.value = String(x);
      onVal(x);
    });
    return i;
  }
  function rangeAttrs(c) {
    var a = {};
    if (isNum(c.min)) a.min = c.min;
    if (isNum(c.max)) a.max = c.max;
    a.step = isNum(c.step) ? c.step : 'any';
    return a;
  }
  function buildControl(c, host) {
    var t = c.type;
    if (t === 'slider') {
      var val = labelRow(c, host, fmt(state[c.id], decimals(c.step)));
      var r = el('input', { type: 'range', min: isNum(c.min) ? c.min : 0, max: isNum(c.max) ? c.max : 1, step: isNum(c.step) ? c.step : 'any', 'aria-label': plain(c.label || c.id) }, host);
      r.value = String(state[c.id]);
      r.addEventListener('input', function () {
        state[c.id] = Number(r.value);
        val.textContent = fmt(state[c.id], decimals(c.step));
        changed(c.id);
      });
    } else if (t === 'number') {
      labelRow(c, host);
      host.appendChild(numInput(Object.assign(rangeAttrs(c), { 'aria-label': plain(c.label || c.id) }), state[c.id], function (x) { state[c.id] = x; changed(c.id); }));
    } else if (t === 'toggle') {
      var lab = el('label', { class: 'toggle' }, host);
      var cb = el('input', { type: 'checkbox' }, lab);
      cb.checked = !!state[c.id];
      rich(el('span', null, lab), c.label || c.id);
      cb.addEventListener('change', function () { state[c.id] = cb.checked; changed(c.id); });
    } else if (t === 'select') {
      labelRow(c, host);
      var s = el('select', { 'aria-label': plain(c.label || c.id) }, host);
      var opts = (c.options || []).map(function (o) { return typeof o === 'object' && o !== null ? o : { value: o, label: String(o) }; });
      opts.forEach(function (o, i) {
        var op = el('option', { value: String(i) }, s, plain(o.label != null ? o.label : o.value));
        if (o.value === state[c.id] || String(o.value) === String(state[c.id])) s.value = String(i);
      });
      s.addEventListener('change', function () { state[c.id] = opts[Number(s.value)].value; changed(c.id); });
    } else if (t === 'vector') {
      var vec = state[c.id];
      var sumSpan = labelRow(c, host, '');
      var grid = el('div', { class: 'vec' }, host);
      var labels = labelsFor(c.item_labels, vec.length, lastOut, state);
      var showSum = function () { if (c.normalize) sumSpan.textContent = 'Σ = ' + fmt(state[c.id].reduce(function (a, b) { return a + b; }, 0), 3); };
      showSum();
      var inputs = vec.map(function (v, i) {
        var cell = el('label', { class: 'cell' }, grid);
        el('span', null, cell, labels[i]);
        var inp = numInput(Object.assign(rangeAttrs(c), { 'aria-label': plain(c.label || c.id) + ' ' + labels[i] }), v, function (x) { state[c.id][i] = x; showSum(); update(); });
        cell.appendChild(inp);
        return inp;
      });
      if (c.normalize) {
        var btns = el('div', { class: 'mini-btns' }, host);
        var nb = el('button', { type: 'button' }, btns, 'Normalize (Σ = 1)');
        nb.addEventListener('click', function () {
          var s0 = state[c.id].reduce(function (a, b) { return a + Math.max(0, b); }, 0);
          var n = state[c.id].length;
          state[c.id] = state[c.id].map(function (x) { return s0 > 0 ? Math.max(0, x) / s0 : 1 / n; });
          inputs.forEach(function (inp, i) { inp.value = String(+state[c.id][i].toFixed(6)); });
          showSum(); update();
        });
      }
    } else if (t === 'matrix') {
      var m = state[c.id];
      labelRow(c, host, m.length + ' × ' + (m[0] ? m[0].length : 0));
      var wrap = el('div', { class: 'table-wrap' }, host);
      var tbl = el('table', { class: 'mat' }, wrap);
      var cols = m[0] ? m[0].length : 0;
      var rl = labelsFor(c.row_labels, m.length, lastOut, state);
      var cl = labelsFor(c.col_labels, cols, lastOut, state);
      var hr = el('tr', null, tbl); el('th', null, hr, '');
      cl.forEach(function (x) { el('th', null, hr, x); });
      m.forEach(function (row, i) {
        var tr = el('tr', null, tbl);
        el('th', null, tr, rl[i]);
        row.forEach(function (v, j) {
          var td = el('td', null, tr);
          td.appendChild(numInput(Object.assign(rangeAttrs(c), { 'aria-label': plain(c.label || c.id) + ' row ' + rl[i] + ' col ' + cl[j] }), v, function (x) { state[c.id][i][j] = x; update(); }));
        });
      });
    } else {
      el('div', { class: 'verr' }, host, 'Unknown control type: ' + t);
    }
    if (c.help) rich(el('div', { class: 'help' }, host), c.help);
  }
  function decimals(step) {
    if (!isNum(step) || step >= 1) return 0;
    return Math.min(6, Math.ceil(-Math.log10(step) - 1e-9));
  }

  // ---------- visuals ----------
  var renderers = [];
  var clipSeq = 0;
  function buildVisuals() {
    var host = document.getElementById('visuals');
    (spec.visuals || []).forEach(function (v) {
      var card = el('div', { class: 'vcard' }, host);
      if (v.title) rich(el('h3', null, card), v.title);
      if (v.caption) rich(el('p', { class: 'caption' }, card), v.caption);
      var body = el('div', null, card);
      var err = el('div', { class: 'verr', hidden: true }, card);
      var draw = VISUALS[v.type];
      if (!draw) { err.hidden = false; err.textContent = 'Unknown visual type: ' + v.type; return; }
      var prepared = null;
      renderers.push(function (out) {
        try {
          if (v.type === 'custom' && !prepared) prepared = compileFn(v.draw, 'draw()');
          W = Math.max(300, Math.min(600, Math.round(body.clientWidth || 600)));
          draw(v, body, out, state, prepared);
          err.hidden = true;
        } catch (e) {
          body.textContent = '';
          err.hidden = false;
          err.textContent = 'This visual could not be drawn: ' + e.message;
        }
      });
    });
  }

  function newSvg(body, h) {
    body.textContent = '';
    return sv('svg', { viewBox: '0 0 ' + W + ' ' + h, role: 'img', preserveAspectRatio: 'xMidYMid meet' }, body);
  }
  function numArr(a, name) {
    if (!Array.isArray(a)) throw new Error('"' + name + '" is not an array in the compute output');
    return a.map(function (x) { return typeof x === 'number' ? x : Number(x); });
  }
  function yDomain(vals, ymin, ymax) {
    var f = vals.filter(isNum);
    var lo = Math.min.apply(null, [0].concat(f)), hi = Math.max.apply(null, [0].concat(f));
    if (isNum(ymin)) lo = Math.min(ymin, lo);
    if (isNum(ymax)) hi = Math.max(ymax, hi);
    if (hi === lo) hi = lo + 1;
    if (!isNum(ymax) || hi > ymax) hi += (hi - lo) * 0.08; // headroom for value labels
    if (lo < 0 && (!isNum(ymin) || lo < ymin)) lo -= (hi - lo) * 0.06;
    return [lo, hi];
  }
  function axesY(svg, m, h, ylo, yhi, yTitle, log) {
    var y = linScale(ylo, yhi, h - m.b, m.t);
    var ticks = niceTicks(ylo, yhi, 5);
    if (log) {
      var stepD = Math.max(1, Math.ceil((yhi - ylo) / 6)), dec = [];
      for (var e = Math.ceil(ylo); e <= Math.floor(yhi); e += stepD) dec.push(e);
      if (dec.length >= 2) ticks = dec;
    }
    function lab(t) {
      if (!log) return tickFmt(t, ticks);
      var r = Math.round(t);
      if (Math.abs(t - r) > 1e-9) return fmt(Math.pow(10, t), 2);
      return Math.abs(r) >= 4 ? '1e' + r : fmt(Math.pow(10, r), Math.max(0, -r));
    }
    ticks.forEach(function (t) {
      sv('line', { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), class: 'grid' }, svg);
      svText(m.l - 6, y(t) + 4, lab(t), { class: 'tick', 'text-anchor': 'end' }, svg);
    });
    if (log && yTitle) yTitle = yTitle + ' (log scale)';
    if (yTitle) svText(14, (m.t + h - m.b) / 2, String(yTitle), { class: 'axis-title', 'text-anchor': 'middle', transform: 'rotate(-90 14 ' + ((m.t + h - m.b) / 2) + ')' }, svg);
    return y;
  }
  function legend(svg, items, m) {
    if (items.length < 2) return;
    var x = m.l;
    items.forEach(function (it, i) {
      sv('rect', { x: x, y: 4, width: 11, height: 11, rx: 2, fill: seriesColor(i) }, svg);
      var t = svText(x + 15, 13.5, it, { class: 'tick' }, svg);
      x += 26 + Math.min(180, it.length * 6.4);
    });
  }

  var VISUALS = {
    readouts: function (v, body, out, st) {
      body.textContent = '';
      var wrap = el('div', { class: 'readouts' }, body);
      (v.items || []).forEach(function (it) {
        var r = el('div', { class: 'readout' }, wrap);
        rich(el('div', { class: 'rl' }, r), it.label || it.source);
        var line = el('div', null, r);
        var val = resolve(it.source, out, st);
        el('span', { class: 'rv' }, line, fmt(val, it.digits));
        if (it.unit) el('span', { class: 'ru' }, line, plain(it.unit));
      });
    },

    bar: function (v, body, out, st) {
      var ser = (v.series || []).map(function (s) { return { label: String(s.label || s.source), data: numArr(resolve(s.source, out, st), s.source) }; });
      if (!ser.length) throw new Error('bar chart has no series');
      var n = Math.max.apply(null, ser.map(function (s) { return s.data.length; }));
      if (!n) { body.textContent = ''; el('p', { class: 'caption' }, body, 'Nothing to plot for these inputs (the series is empty).'); return; }
      var h = 300, m = { l: 58, r: 14, t: ser.length > 1 ? 28 : 14, b: 52 };
      var svg = newSvg(body, h);
      legend(svg, ser.map(function (s) { return s.label; }), m);
      var dom = yDomain([].concat.apply([], ser.map(function (s) { return s.data; })), v.y_min, v.y_max);
      var y = axesY(svg, m, h, dom[0], dom[1], v.y_label || (ser.length === 1 ? ser[0].label : ''));
      var labels = labelsFor(v.x_labels, n, out, st);
      var band = (W - m.l - m.r) / Math.max(1, n);
      var gw = band * 0.78, bw = gw / ser.length;
      var showVals = n * ser.length <= 16;
      var every = Math.ceil(n / 16);
      for (var i = 0; i < n; i++) {
        var x0 = m.l + i * band + (band - gw) / 2;
        ser.forEach(function (s, k) {
          var val = s.data[i];
          var x = x0 + k * bw;
          if (!isNum(val)) {
            if (i < s.data.length) svText(x + bw / 2, y(0) - 4, fmt(val), { class: 'vlabel', 'text-anchor': 'middle', fill: 'var(--bad)' }, svg);
            return;
          }
          var y1 = y(Math.max(0, val)), y2 = y(Math.min(0, val));
          sv('rect', { x: x + 1, y: y1, width: Math.max(1, bw - 2), height: Math.max(val === 0 ? 0 : 1, y2 - y1), fill: seriesColor(k), rx: 2 }, svg)
            .appendChild(document.createElementNS(SVGNS, 'title')).textContent = s.label + ' [' + labels[i] + '] = ' + fmt(val, v.digits);
          if (showVals) svText(x + bw / 2, val >= 0 ? y1 - 4 : y2 + 12, fmt(val, v.digits == null ? 3 : v.digits), { class: 'vlabel', 'text-anchor': 'middle' }, svg);
        });
        if (i % every === 0) svText(m.l + i * band + band / 2, h - m.b + 16, labels[i], { class: 'tick', 'text-anchor': 'middle' }, svg);
      }
      sv('line', { x1: m.l, x2: W - m.r, y1: y(0), y2: y(0), class: 'zero' }, svg);
      if (v.x_label) svText((m.l + W - m.r) / 2, h - 10, String(v.x_label), { class: 'axis-title', 'text-anchor': 'middle' }, svg);
    },

    // Series: {source, label, x_source?, style?: line|points|line+points|stem|dashed|step}.
    // Fixed axes (x_min/x_max/y_min/y_max) clip instead of rescaling, so divergence stays visible.
    line: function (v, body, out, st) {
      var defStyle = v.type === 'scatter' ? 'points' : (v.points ? 'line+points' : 'line');
      v = Object.assign({}, v);
      ['x_min', 'x_max', 'y_min', 'y_max'].forEach(function (k) { if (typeof v[k] === 'string') v[k] = Number(resolve(v[k], out, st)); });
      var sharedX = v.x_source != null ? resolve(v.x_source, out, st) : null;
      var ser = (v.series || []).map(function (s) {
        var xsrc = s.x_source != null ? s.x_source : v.x_source;
        var xs = s.x_source != null ? resolve(s.x_source, out, st) : sharedX;
        var data = numArr(resolve(s.source, out, st), s.source);
        xs = xs == null ? data.map(function (_, i) { return i; }) : numArr(xs, xsrc);
        return { label: svgLabel(s.label || s.source), xs: xs, data: data, style: s.style || defStyle };
      });
      if (!ser.length) throw new Error('line chart has no series');
      var markers = asArr(v.markers).concat(v.marker ? [v.marker] : []).filter(isObj).map(function (mk) {
        return { x: mk.x != null ? Number(resolve(mk.x, out, st)) : NaN, y: mk.y != null ? Number(resolve(mk.y, out, st)) : NaN, label: mk.label };
      });
      var LOG = !!v.y_log;
      if (LOG) {
        var L = function (q) { return isNum(q) && q > 0 ? Math.log10(q) : NaN; };
        ser.forEach(function (s) { s.raw = s.data; s.data = s.data.map(L); });
        markers.forEach(function (mk) { mk.rawY = mk.y; mk.y = L(mk.y); });
        ['y_min', 'y_max'].forEach(function (k) { if (isNum(v[k])) v[k] = v[k] > 0 ? Math.log10(v[k]) : undefined; });
      }
      var h = 300, m = { l: 58, r: 16, t: ser.length > 1 ? 28 : 14, b: 52 };
      var svg = newSvg(body, h);
      legend(svg, ser.map(function (s) { return s.label; }), m);

      var allX = [], allY = [];
      ser.forEach(function (s) {
        for (var i = 0; i < Math.min(s.xs.length, s.data.length); i++) if (isNum(s.xs[i]) && isNum(s.data[i])) { allX.push(s.xs[i]); allY.push(s.data[i]); }
      });
      markers.forEach(function (mk) { if (isNum(mk.x)) allX.push(mk.x); if (isNum(mk.y)) allY.push(mk.y); });
      if (!allX.length && !isNum(v.x_min)) { body.textContent = ''; el('p', { class: 'caption' }, body, 'Nothing to plot for these inputs.'); return; }
      var xlo = isNum(v.x_min) ? v.x_min : Math.min.apply(null, allX), xhi = isNum(v.x_max) ? v.x_max : Math.max.apply(null, allX);
      if (!(xhi > xlo)) { xlo -= 0.5; xhi += 0.5; }
      var lo = isNum(v.y_min) ? v.y_min : Math.min.apply(null, allY.length ? allY : [0]);
      var hi = isNum(v.y_max) ? v.y_max : Math.max.apply(null, allY.length ? allY : [1]);
      if (!(hi > lo)) { hi = lo + 0.5; lo -= 0.5; }
      var pad = (hi - lo) * 0.06;
      if (!isNum(v.y_max)) hi += pad;
      if (!isNum(v.y_min) && lo !== 0) lo -= pad;

      var y = axesY(svg, m, h, lo, hi, v.y_label, LOG);
      var x = linScale(xlo, xhi, m.l, W - m.r);
      var xt = niceTicks(xlo, xhi, 6);
      xt.forEach(function (t) {
        sv('line', { x1: x(t), x2: x(t), y1: m.t, y2: h - m.b, class: 'grid' }, svg);
        svText(x(t), h - m.b + 16, tickFmt(t, xt), { class: 'tick', 'text-anchor': 'middle' }, svg);
      });
      if (!LOG && lo < 0 && hi > 0) sv('line', { x1: m.l, x2: W - m.r, y1: y(0), y2: y(0), class: 'zero' }, svg);
      sv('line', { x1: m.l, x2: m.l, y1: m.t, y2: h - m.b, class: 'axis' }, svg);
      sv('line', { x1: m.l, x2: W - m.r, y1: h - m.b, y2: h - m.b, class: 'axis' }, svg);

      var clipId = 'clip' + (++clipSeq);
      sv('rect', { x: m.l, y: m.t - 4, width: W - m.l - m.r, height: h - m.t - m.b + 8 }, sv('clipPath', { id: clipId }, sv('defs', null, svg)));
      var plot = sv('g', { 'clip-path': 'url(#' + clipId + ')' }, svg);
      var clipped = 0;
      ser.forEach(function (s, k) {
        var col = seriesColor(k), n = Math.min(s.xs.length, s.data.length), d = '', pen = false, px = null, py = null;
        var drawLine = /line|dashed|step/.test(s.style), dots = /points/.test(s.style) || s.style === 'stem';
        for (var i = 0; i < n; i++) {
          var xi = s.xs[i], yi = s.data[i];
          if (!(isNum(xi) && isNum(yi))) { pen = false; continue; }
          if (yi < lo || yi > hi || xi < xlo || xi > xhi) clipped++;
          var X = x(xi), Y = y(yi);
          if (drawLine) {
            if (s.style === 'step' && pen) d += 'L' + X.toFixed(1) + ' ' + py.toFixed(1);
            d += (pen ? 'L' : 'M') + X.toFixed(1) + ' ' + Y.toFixed(1);
            pen = true; px = X; py = Y;
          }
          if (s.style === 'stem') sv('line', { x1: X, x2: X, y1: y(LOG ? lo : clamp(0, lo, hi)), y2: Y, stroke: col, 'stroke-width': 1.6 }, plot);
          if (dots) sv('circle', { cx: X, cy: Y, r: s.style === 'points' ? 4 : 3.2, fill: col, stroke: 'var(--panel)', 'stroke-width': 1 }, plot)
            .appendChild(document.createElementNS(SVGNS, 'title')).textContent = s.label + ': (' + fmt(xi, 3) + ', ' + fmt(s.raw ? s.raw[i] : yi, 3) + ')';
        }
        if (drawLine && d) sv('path', { d: d, fill: 'none', stroke: col, 'stroke-width': 2.2, 'stroke-linejoin': 'round', 'stroke-dasharray': s.style === 'dashed' ? '6 4' : null }, plot);
      });
      markers.forEach(function (mk) {
        var hasX = isNum(mk.x), hasY = isNum(mk.y);
        if (!hasX && !hasY) return;
        var parts = [];
        if (hasX) parts.push(fmt(mk.x, 3));
        if (hasY) parts.push(fmt(LOG ? mk.rawY : mk.y, 3));
        var lab = (mk.label ? svgLabel(mk.label) + ': ' : '') + parts.join(', ');
        var offX = hasX && (mk.x < xlo || mk.x > xhi), offY = hasY && (mk.y < lo || mk.y > hi);
        if (offX || offY) {
          // Never draw a marker at a clamped position: say where it is instead.
          var dir = (offX ? (mk.x > xhi ? '→' : '←') : '') + (offY ? (mk.y > hi ? '↑' : '↓') : '');
          svText(W - m.r - 4, m.t + 12, lab + '  ' + dir + ' off-scale', { class: 'vlabel', 'text-anchor': 'end', style: 'fill:var(--bad)' }, svg);
        } else if (hasX && !hasY) {
          var X = x(clamp(mk.x, xlo, xhi));
          sv('line', { x1: X, x2: X, y1: m.t, y2: h - m.b, stroke: 'var(--bad)', 'stroke-dasharray': '4 3', 'stroke-width': 1.4 }, svg);
          svText(X > W * 0.7 ? X - 6 : X + 6, m.t + 12, lab, { class: 'vlabel', 'text-anchor': X > W * 0.7 ? 'end' : 'start', style: 'fill:var(--bad)' }, svg);
        } else if (hasY && !hasX) {
          var Yh = y(clamp(mk.y, lo, hi));
          sv('line', { x1: m.l, x2: W - m.r, y1: Yh, y2: Yh, stroke: 'var(--bad)', 'stroke-dasharray': '4 3', 'stroke-width': 1.4 }, svg);
          svText(W - m.r - 4, Yh - 5, lab, { class: 'vlabel', 'text-anchor': 'end', style: 'fill:var(--bad)' }, svg);
        } else {
          var Xp = x(clamp(mk.x, xlo, xhi)), Yp = y(clamp(mk.y, lo, hi));
          sv('line', { x1: Xp, x2: Xp, y1: Yp, y2: h - m.b, stroke: 'var(--bad)', 'stroke-dasharray': '3 3' }, svg);
          sv('circle', { cx: Xp, cy: Yp, r: 5.5, fill: 'var(--bad)', stroke: 'var(--panel)', 'stroke-width': 2 }, svg);
          svText(Xp > W * 0.7 ? Xp - 9 : Xp + 9, Math.max(m.t + 10, Yp - 9), lab, { class: 'vlabel', 'text-anchor': Xp > W * 0.7 ? 'end' : 'start', style: 'fill:var(--bad)' }, svg);
        }
      });
      if (clipped) svText(W - m.r, h - 4, clipped + ' point(s) outside the fixed axes', { class: 'tick', 'text-anchor': 'end' }, svg);
      if (v.x_label) svText((m.l + W - m.r) / 2, h - 10, String(v.x_label), { class: 'axis-title', 'text-anchor': 'middle' }, svg);
    },

    heatmap: function (v, body, out, st) {
      var mat = resolve(v.source, out, st);
      if (!Array.isArray(mat) || !mat.length) throw new Error('"' + v.source + '" is not a matrix in the compute output');
      mat = mat.map(function (r) { return numArr(Array.isArray(r) ? r : [r], v.source); });
      var rows = mat.length, cols = Math.max.apply(null, mat.map(function (r) { return r.length; }));
      var rl = labelsFor(v.row_labels, rows, out, st), cl = labelsFor(v.col_labels, cols, out, st);
      var flat = [].concat.apply([], mat).filter(isNum);
      var lo, hi;
      if (Array.isArray(v.domain) && v.domain.length === 2) { lo = v.domain[0]; hi = v.domain[1]; }
      else { lo = Math.min.apply(null, flat.concat([0])); hi = Math.max.apply(null, flat.concat([0])); }
      var diverging = lo < 0 && hi > 0;
      var amax = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
      var left = 16 + (v.row_title ? 18 : 0) + Math.min(90, 7 * Math.max.apply(null, rl.map(function (s) { return s.length; }).concat([2])));
      var extra = v.row_sums ? 70 : 0;
      var top = 22 + (v.col_title ? 18 : 0);
      var cell = Math.max(26, Math.min(72, (W - left - extra - 10) / cols));
      var gw = cell * cols, h = top + cell * rows + 10;
      var svg = newSvg(body, h);
      var x0 = left + Math.max(0, (W - left - extra - 10 - gw) / 2);
      var fs = Math.max(9, Math.min(13, cell / 4.2));
      var digits = v.digits == null ? 2 : v.digits;
      if (v.col_title) svText(x0 + gw / 2, 12, String(v.col_title), { class: 'axis-title', 'text-anchor': 'middle' }, svg);
      if (v.row_title) svText(10, top + cell * rows / 2, String(v.row_title), { class: 'axis-title', 'text-anchor': 'middle', transform: 'rotate(-90 10 ' + (top + cell * rows / 2) + ')' }, svg);
      cl.forEach(function (s, j) { svText(x0 + j * cell + cell / 2, top - 6, s, { class: 'tick', 'text-anchor': 'middle' }, svg); });
      if (v.row_sums) svText(x0 + gw + 36, top - 6, 'row Σ', { class: 'tick', 'text-anchor': 'middle' }, svg);
      mat.forEach(function (row, i) {
        svText(x0 - 6, top + i * cell + cell / 2 + 4, rl[i], { class: 'tick', 'text-anchor': 'end' }, svg);
        for (var j = 0; j < cols; j++) {
          var val = row[j];
          var t = isNum(val) ? (diverging ? val / amax : (hi === lo ? 0.5 : (val - lo) / (hi - lo))) : 0;
          var hc = isNum(val) ? heatColor(t) : { fill: 'var(--bad-soft)', op: 1 };
          var r = sv('rect', { x: x0 + j * cell, y: top + i * cell, width: cell - 2, height: cell - 2, rx: 3, fill: hc.fill, 'fill-opacity': hc.op, stroke: 'var(--line)', 'stroke-width': 0.6 }, svg);
          r.appendChild(document.createElementNS(SVGNS, 'title')).textContent = rl[i] + ', ' + cl[j] + ': ' + fmt(val, 4);
          var strong = Math.abs(t) > 0.55;
          svText(x0 + j * cell + cell / 2 - 1, top + i * cell + cell / 2 + fs / 3, fmt(val, digits),
            { class: 'vlabel', 'text-anchor': 'middle', 'font-size': fs, style: 'fill:' + (strong ? '#fff' : 'var(--ink)') }, svg);
        }
        if (v.row_sums) {
          var s = row.reduce(function (a, b) { return a + b; }, 0);
          svText(x0 + gw + 36, top + i * cell + cell / 2 + 4, fmt(s, 3), { class: 'vlabel', 'text-anchor': 'middle' }, svg);
        }
      });
    },

    table: function (v, body, out, st) {
      var t = resolve(v.source, out, st);
      if (!t || !Array.isArray(t.rows)) throw new Error('"' + v.source + '" is not a table {cols, rows}');
      body.textContent = '';
      var wrap = el('div', { class: 'table-wrap' }, body);
      var tbl = el('table', null, wrap);
      var cols = t.cols || [];
      var numericCol = cols.map(function (_, j) { return t.rows.every(function (r) { return typeof r[j] === 'number'; }); });
      var intCol = cols.map(function (_, j) { return t.rows.every(function (r) { return !Array.isArray(r) || typeof r[j] !== 'number' || (Number.isInteger(r[j]) && Math.abs(r[j]) < 1e6); }); });
      var hr = el('tr', null, el('thead', null, tbl));
      cols.forEach(function (c, j) { rich(el('th', { class: numericCol[j] ? 'num' : '' }, hr), c); });
      var tb = el('tbody', null, tbl);
      t.rows.forEach(function (r) {
        var tr = el('tr', null, tb);
        (Array.isArray(r) ? r : [r]).forEach(function (x, j) {
          var td = el('td', { class: typeof x === 'number' ? 'num' : '' }, tr);
          if (typeof x === 'number') td.textContent = fmt(x, intCol[j] ? 0 : v.digits); else rich(td, x);
        });
      });
    },

    // 2-D plane with equal axis scaling. items: [{kind: vector|points|segment|line, source, from?, label?, color?, dashed?}]
    vectors2d: function (v, body, out, st) {
      function pt(a) { return Array.isArray(a) && a.length >= 2 && isNum(+a[0]) && isNum(+a[1]) ? [+a[0], +a[1]] : null; }
      function ptList(a) { return Array.isArray(a) ? a.map(pt).filter(Boolean) : []; }
      var items = asArr(v.items).filter(isObj).map(function (it, idx) {
        var d = resolve(it.source, out, st), from = pt(resolve(it.from, out, st)) || [0, 0];
        var kind = it.kind || 'vector', r = { it: it, kind: kind, color: seriesColor(isNum(it.color) ? it.color : idx), label: svgLabel(it.label || '') };
        if (kind === 'vector' || kind === 'line') { r.p = pt(d); r.from = from; }
        else if (kind === 'points') r.pts = ptList(d);
        else if (kind === 'segment') r.segs = (Array.isArray(d) && Array.isArray(d[0]) && Array.isArray(d[0][0]) ? d : [d]).map(ptList).filter(function (s) { return s.length === 2; });
        return r;
      });
      if (!items.length) throw new Error('vectors2d has no items');
      var all = [];
      items.forEach(function (r) {
        if (r.kind === 'vector' && r.p) all.push(r.from, [r.from[0] + r.p[0], r.from[1] + r.p[1]]);
        if (r.kind === 'line') all.push(r.from);
        (r.pts || []).forEach(function (p) { all.push(p); });
        (r.segs || []).forEach(function (s) { all.push(s[0], s[1]); });
      });
      var R = Number(resolve(v.range, out, st));
      if (!isNum(R) || R <= 0) R = Math.max(1, Math.max.apply(null, all.map(function (p) { return Math.max(Math.abs(p[0]), Math.abs(p[1])); }).concat([0])) * 1.18);
      var top = 26, S = Math.min(W - 70, 430), h = S + top + 34, x0 = (W - S) / 2 + 14;
      var svg = newSvg(body, h);
      var X = linScale(-R, R, x0, x0 + S), Y = linScale(-R, R, top + S, top);
      var ticks = niceTicks(-R, R, 6);
      ticks.forEach(function (t) {
        sv('line', { x1: X(t), x2: X(t), y1: top, y2: top + S, class: 'grid' }, svg);
        sv('line', { x1: x0, x2: x0 + S, y1: Y(t), y2: Y(t), class: 'grid' }, svg);
        svText(X(t), top + S + 14, tickFmt(t, ticks), { class: 'tick', 'text-anchor': 'middle' }, svg);
        svText(x0 - 5, Y(t) + 4, tickFmt(t, ticks), { class: 'tick', 'text-anchor': 'end' }, svg);
      });
      sv('line', { x1: x0, x2: x0 + S, y1: Y(0), y2: Y(0), class: 'zero' }, svg);
      sv('line', { x1: X(0), x2: X(0), y1: top, y2: top + S, class: 'zero' }, svg);
      if (v.x_label) svText(x0 + S - 4, Y(0) - 7, String(v.x_label), { class: 'axis-title', 'text-anchor': 'end' }, svg);
      if (v.y_label) svText(X(0) + 7, top + 12, String(v.y_label), { class: 'axis-title' }, svg);
      var clipId = 'clip' + (++clipSeq);
      sv('rect', { x: x0, y: top, width: S, height: S }, sv('clipPath', { id: clipId }, sv('defs', null, svg)));
      var g = sv('g', { 'clip-path': 'url(#' + clipId + ')' }, svg);
      var legendItems = [], skipped = 0;
      items.forEach(function (r) {
        var dash = r.it.dashed ? '6 4' : null;
        if (r.kind === 'vector') {
          if (!r.p) { skipped++; return; }
          var ax = X(r.from[0]), ay = Y(r.from[1]), bx = X(r.from[0] + r.p[0]), by = Y(r.from[1] + r.p[1]);
          sv('line', { x1: ax, y1: ay, x2: bx, y2: by, stroke: r.color, 'stroke-width': 2.6, 'stroke-dasharray': dash }, g);
          arrowHead(g, ax, ay, bx, by, r.color);
          if (r.label) svText(bx + 6, by - 6, r.label + ' (' + fmt(r.p[0], 2) + ', ' + fmt(r.p[1], 2) + ')', { class: 'vlabel', style: 'fill:' + r.color + ';font-weight:600' }, svg);
        } else if (r.kind === 'line') {
          if (!r.p || (r.p[0] === 0 && r.p[1] === 0)) { skipped++; return; }
          var k = 4 * R / Math.hypot(r.p[0], r.p[1]);
          sv('line', { x1: X(r.from[0] - k * r.p[0]), y1: Y(r.from[1] - k * r.p[1]), x2: X(r.from[0] + k * r.p[0]), y2: Y(r.from[1] + k * r.p[1]), stroke: r.color, 'stroke-width': 1.6, 'stroke-dasharray': dash || '2 0' }, g);
          if (r.label) legendItems.push(r);
        } else if (r.kind === 'points') {
          var labs = r.it.point_labels != null ? labelsFor(r.it.point_labels, r.pts.length, out, st) : null;
          r.pts.forEach(function (p, i) {
            sv('circle', { cx: X(p[0]), cy: Y(p[1]), r: 4.5, fill: r.color, stroke: 'var(--panel)', 'stroke-width': 1.2 }, g)
              .appendChild(document.createElementNS(SVGNS, 'title')).textContent = (r.label || 'point') + ' ' + (i + 1) + ': (' + fmt(p[0], 3) + ', ' + fmt(p[1], 3) + ')';
            if (labs) svText(X(p[0]) + 6, Y(p[1]) - 6, labs[i], { class: 'tick' }, svg);
          });
          if (r.label) legendItems.push(r);
        } else if (r.kind === 'segment') {
          r.segs.forEach(function (s) { sv('line', { x1: X(s[0][0]), y1: Y(s[0][1]), x2: X(s[1][0]), y2: Y(s[1][1]), stroke: r.color, 'stroke-width': 1.6, 'stroke-dasharray': dash }, g); });
          if (r.label) legendItems.push(r);
        } else skipped++;
      });
      var lx = 8;
      legendItems.forEach(function (r) {
        sv('rect', { x: lx, y: 6, width: 11, height: 11, rx: 2, fill: r.color }, svg);
        svText(lx + 15, 15.5, r.label, { class: 'tick' }, svg);
        lx += 30 + r.label.length * 6.4;
      });
      if (skipped) svText(W - 6, h - 4, skipped + ' item(s) not drawn (invalid data)', { class: 'tick', 'text-anchor': 'end' }, svg);
    },

    // Nodes and (directed) edges. nodes: array | path | count; edges: [{from, to, weight?, label?}] | path;
    // or matrix: path to an n×n weight/transition matrix (non-zero entries become edges).
    graph: function (v, body, out, st) {
      var rawN = resolve(v.nodes, out, st), M = v.matrix != null ? resolve(v.matrix, out, st) : null;
      if (rawN == null && Array.isArray(M)) rawN = M.length;
      var nodes = (typeof rawN === 'number' ? range(Math.max(0, Math.round(rawN))) : asArr(rawN)).map(function (n, i) {
        if (isObj(n)) return { id: n.id != null ? String(n.id) : String(i + 1), label: n.label != null ? n.label : (n.id != null ? n.id : i + 1), x: n.x, y: n.y, layer: n.layer };
        return { id: typeof rawN === 'number' ? String(i + 1) : String(n), label: typeof rawN === 'number' ? i + 1 : n };
      });
      if (!nodes.length) throw new Error('graph has no nodes');
      var nl = v.node_labels != null ? labelsFor(v.node_labels, nodes.length, out, st) : null;
      if (nl) nodes.forEach(function (n, i) { n.label = nl[i]; });
      var index = {};
      nodes.forEach(function (n, i) { index[n.id] = i; index[String(n.label)] = index[String(n.label)] != null ? index[String(n.label)] : i; });
      function ref(x) { if (typeof x === 'number' && x >= 0 && x < nodes.length && index[String(x)] == null) return x; var k = index[String(x)]; return k != null ? k : (typeof x === 'number' ? x : -1); }
      var edges = [];
      if (Array.isArray(M)) M.forEach(function (row, i) { asArr(row).forEach(function (w, j) { w = Number(w); if (isNum(w) && w !== 0 && j < nodes.length) edges.push({ a: i, b: j, w: w }); }); });
      else asArr(resolve(v.edges, out, st)).forEach(function (e) {
        var a, b, w, lab;
        if (Array.isArray(e)) { a = e[0]; b = e[1]; w = e[2]; } else if (isObj(e)) { a = e.from; b = e.to; w = e.weight; lab = e.label; } else return;
        a = ref(a); b = ref(b);
        if (a >= 0 && b >= 0 && a < nodes.length && b < nodes.length) edges.push({ a: a, b: b, w: w == null ? null : Number(w), label: lab });
      });
      var vals = v.node_values != null ? asArr(resolve(v.node_values, out, st)).map(Number) : null;
      var hiNode = v.highlight != null ? ref(resolve(v.highlight, out, st)) : -1;
      var h = isNum(v.height) ? v.height : 340, n = nodes.length;
      var r = Math.max(13, Math.min(24, 120 / Math.sqrt(n)));
      var pos;
      if (nodes.every(function (q) { return isNum(q.x) && isNum(q.y); })) pos = nodes.map(function (q) { return [40 + q.x * (W - 80), 30 + q.y * (h - 70)]; });
      else if (nodes.some(function (q) { return isNum(q.layer); })) {
        var layers = {}, keys;
        nodes.forEach(function (q, i) { var L = isNum(q.layer) ? q.layer : 0; (layers[L] = layers[L] || []).push(i); });
        keys = Object.keys(layers).map(Number).sort(function (a, b) { return a - b; });
        pos = new Array(n);
        keys.forEach(function (L, li) {
          layers[L].forEach(function (i, k) { pos[i] = [60 + (W - 120) * (keys.length > 1 ? li / (keys.length - 1) : 0.5), 30 + (h - 70) * (k + 1) / (layers[L].length + 1)]; });
        });
      } else {
        var cx = W / 2, cy = (h - 20) / 2 + 6, rad = Math.min(W / 2 - 60, (h - 20) / 2 - r - 22);
        pos = nodes.map(function (_, i) { var t = -Math.PI / 2 + 2 * Math.PI * i / n; return n === 1 ? [cx, cy] : [cx + rad * Math.cos(t), cy + rad * Math.sin(t)]; });
        // centre the polygon vertically (odd n leaves it top-heavy); leave room for self-loops above
        var ys = pos.map(function (q) { return q[1]; }), shift = (h + 16) / 2 - (Math.min.apply(null, ys) + Math.max.apply(null, ys)) / 2;
        pos.forEach(function (q) { q[1] += shift; });
      }
      var svg = newSvg(body, h);
      var directed = v.directed !== false, digits = v.digits == null ? 2 : v.digits;
      var maxW = Math.max.apply(null, edges.map(function (e) { return isNum(e.w) ? Math.abs(e.w) : 0; }).concat([1e-12]));
      var has = {};
      edges.forEach(function (e) { has[e.a + ',' + e.b] = 1; });
      var labelStyle = 'paint-order:stroke;stroke:var(--panel);stroke-width:4px;stroke-linejoin:round';
      edges.forEach(function (e) {
        var col = 'var(--axis)', sw = isNum(e.w) && v.weight_width !== false ? 1.2 + 2.6 * Math.abs(e.w) / maxW : 1.6;
        var A = pos[e.a], B = pos[e.b], txt = e.label != null ? svgLabel(e.label) : (isNum(e.w) && v.show_weights !== false ? fmt(e.w, digits) : '');
        if (e.a === e.b) {
          var lx0 = A[0], ly0 = A[1] - r;
          sv('path', { d: 'M' + (lx0 - 8) + ' ' + ly0 + ' C ' + (lx0 - 26) + ' ' + (ly0 - 40) + ' ' + (lx0 + 26) + ' ' + (ly0 - 40) + ' ' + (lx0 + 8) + ' ' + ly0, fill: 'none', stroke: col, 'stroke-width': sw }, svg);
          if (directed) arrowHead(svg, lx0 + 16, ly0 - 14, lx0 + 8, ly0, col);
          if (txt) svText(lx0, ly0 - 34, txt, { class: 'vlabel', 'text-anchor': 'middle', style: labelStyle }, svg);
          return;
        }
        var dx = B[0] - A[0], dy = B[1] - A[1], L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L;
        var bend = has[e.b + ',' + e.a] ? 22 : 0, nx = -uy * bend, ny = ux * bend;
        var sx = A[0] + ux * r, sy = A[1] + uy * r, ex = B[0] - ux * (r + 2), ey = B[1] - uy * (r + 2);
        var qx = (sx + ex) / 2 + nx, qy = (sy + ey) / 2 + ny;
        sv('path', { d: 'M' + sx + ' ' + sy + ' Q ' + qx + ' ' + qy + ' ' + ex + ' ' + ey, fill: 'none', stroke: col, 'stroke-width': sw }, svg);
        if (directed) arrowHead(svg, qx, qy, ex, ey, col);
        if (txt) svText((sx + 2 * qx + ex) / 4, (sy + 2 * qy + ey) / 4 + 4, txt, { class: 'vlabel', 'text-anchor': 'middle', style: labelStyle }, svg);
      });
      var vmax = vals ? Math.max.apply(null, vals.filter(isNum).concat([1e-12])) : 0;
      nodes.forEach(function (q, i) {
        var P = pos[i], val = vals ? vals[i] : null;
        var op = vals && isNum(val) ? 0.12 + 0.8 * Math.max(0, val) / vmax : 0.14;
        sv('circle', { cx: P[0], cy: P[1], r: r, fill: 'rgb(36,89,214)', 'fill-opacity': op, stroke: i === hiNode ? 'var(--bad)' : 'rgb(36,89,214)', 'stroke-width': i === hiNode ? 3 : 1.6 }, svg);
        svText(P[0], P[1] + 4, svgLabel(q.label), { 'text-anchor': 'middle', style: 'font-size:12px;font-weight:600;' + (op > 0.55 ? 'fill:#fff' : '') }, svg);
        if (vals) svText(P[0], P[1] + r + 13, fmt(val, digits), { class: 'vlabel', 'text-anchor': 'middle', style: labelStyle }, svg);
      });
    },

    custom: function (v, body, out, st, drawFn) {
      var h = isNum(v.height) ? v.height : 300;
      var svg = newSvg(body, h);
      drawFn(svg, deepCopy(st), out, {
        el: sv, text: function (x, y, s, a, p) { return svText(x, y, s, a, p || svg); },
        scale: linScale, fmt: fmt, color: seriesColor, W: W, H: h
      });
    }
  };

  VISUALS.scatter = VISUALS.line;

  // Filled triangular arrowhead pointing from (x1,y1) to its tip (x2,y2).
  function arrowHead(parent, x1, y1, x2, y2, color) {
    var dx = x2 - x1, dy = y2 - y1, L = Math.hypot(dx, dy);
    if (!(L > 0)) return;
    var ux = dx / L, uy = dy / L, s = 9, w = 4.5;
    var pts = [[x2, y2], [x2 - ux * s - uy * w, y2 - uy * s + ux * w], [x2 - ux * s + uy * w, y2 - uy * s - ux * w]];
    sv('polygon', { points: pts.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' '), fill: color }, parent);
  }

  // Opacity ramp over the panel colour, so it reads in light and dark themes.
  // Positive → blue, negative → orange (diverging around 0).
  function heatColor(t) {
    var k = clamp(Math.abs(t), 0, 1);
    return { fill: t >= 0 ? 'rgb(36,89,214)' : 'rgb(214,104,26)', op: 0.06 + 0.94 * k };
  }

  // ---------- update loop ----------
  function update() {
    var banner = document.getElementById('calc-error');
    var out;
    try {
      out = runCompute(state);
      lastOut = out;
      var bad = Object.keys(out.values || {}).filter(function (k) { var x = out.values[k]; return typeof x === 'number' && !isFinite(x); });
      if (typeof out.warning === 'string' && out.warning) { banner.hidden = false; banner.className = 'banner warn'; banner.textContent = out.warning; }
      else if (bad.length) { banner.hidden = false; banner.className = 'banner warn'; banner.textContent = 'Note: these values are not finite for the current inputs: ' + bad.join(', ') + '. Check the inputs (e.g. all zeros).'; }
      else banner.hidden = true;
    } catch (e) {
      banner.hidden = false; banner.className = 'banner error';
      banner.textContent = computeFn
        ? 'Calculation error for these inputs: ' + e.message + (lastOut ? ' (showing the last valid result)' : '')
        : 'The calculation code could not be loaded, so the interactive parts are unavailable: ' + e.message;
      out = lastOut;
      if (!out) return;
    }
    renderers.forEach(function (r) { r(out); });
    refreshLive();
  }

  // ---------- expectations, explorations, checks ----------
  function evalExpect(ex, out, st) {
    var got = resolve(ex.source, out, st);
    var ok = true, parts = [];
    if (ex.equals != null) {
      var tol = isNum(ex.tol) ? ex.tol : 1e-6;
      ok = ok && (typeof ex.equals === 'number' ? isNum(got) && Math.abs(got - ex.equals) <= tol : got === ex.equals);
      parts.push('= ' + fmt(ex.equals, 4) + (typeof ex.equals === 'number' ? ' ± ' + fmt(tol, 6) : ''));
    }
    if (ex.min != null) { ok = ok && isNum(got) && got >= ex.min; parts.push('≥ ' + fmt(ex.min, 4)); }
    if (ex.max != null) { ok = ok && isNum(got) && got <= ex.max; parts.push('≤ ' + fmt(ex.max, 4)); }
    return { ok: ok, text: ex.source + ' = ' + fmt(got, 4) + '  (expected ' + parts.join(', ') + ')' };
  }

  var liveCards = [];
  function buildExplorations() {
    var host = section('explore');
    (spec.explorations || []).forEach(function (x, idx) {
      var lim = x.kind === 'limitation';
      var card = el('article', { class: 'card' + (lim ? ' limitation' : '') }, host);
      el('span', { class: 'tag' }, card, lim ? 'Limitation / misconception' : 'Exploration ' + (idx + 1));
      rich(el('h3', null, card), x.title || '');
      [['Change', x.change], ['Observe', x.observe], ['Why', x.why]].forEach(function (p) {
        if (!p[1]) return;
        var row = el('div', { class: 'step' }, card);
        el('div', { class: 'k' }, row, p[0]);
        rich(el('div', null, row), p[1]);
      });
      if (x.state && typeof x.state === 'object') {
        var live = el('div', { class: 'live', hidden: true }, card);
        var b = el('button', { type: 'button', class: 'primary' }, card, 'Try it: set these inputs');
        b.style.alignSelf = 'flex-start';
        var entry = { x: x, live: live, active: false };
        liveCards.push(entry);
        b.addEventListener('click', function () {
          liveCards.forEach(function (e) { e.active = false; e.live.hidden = true; });
          entry.active = true;
          setState(mergeState(defaultState(), x.state));
          document.getElementById('playground').scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
      }
    });
  }
  function refreshLive() {
    liveCards.forEach(function (e) {
      if (!e.active || !lastOut) return;
      var exps = Array.isArray(e.x.expect) ? e.x.expect : [];
      e.live.hidden = !exps.length;
      e.live.textContent = exps.map(function (ex) { var r = evalExpect(ex, lastOut, state); return (r.ok ? '✓ ' : '• now: ') + r.text; }).join('\n');
      e.live.style.whiteSpace = 'pre-wrap';
    });
  }

  function buildLimits() {
    var host = section('limits');
    var items = Array.isArray(spec.limitations) ? spec.limitations : [];
    if (!items.length) { el('p', null, host, 'No limitations were listed.'); return; }
    var ul = el('ul', { class: 'limits' }, host);
    items.forEach(function (s) { rich(el('li', null, ul), s); });
  }

  function buildSource() {
    var host = section('source'), s = spec.source || {};
    var cite = el('div', { class: 'cite' }, host);
    var line = el('div', null, cite);
    el('strong', null, line, plain(s.paper) || 'Source');
    if (s.authors || s.year) line.appendChild(document.createTextNode(' — ' + [s.authors, s.year].filter(Boolean).join(', ')));
    if (s.section) { line.appendChild(document.createTextNode(' · ')); rich(el('span', null, line), s.section); }
    if (s.url) { var u = el('div', null, cite); el('code', null, u, s.url); }
    if (s.equation) rich(el('div', { class: 'eq' }, cite), s.equation);
    if (s.context_note) el('div', { class: 'banner info' }, host, s.context_note);
    var g = el('div', { class: 'ground' }, host);
    var pc = el('div', { class: 'gcol paper' }, g);
    el('h3', null, pc, 'Supported by the source excerpt');
    var pu = el('ul', null, pc);
    (s.supported || []).forEach(function (it) {
      var li = el('li', null, pu);
      rich(el('div', null, li), typeof it === 'string' ? it : it.claim);
      if (it && it.quote) el('blockquote', null, li, '“' + it.quote + '”');
    });
    if (!(s.supported || []).length) el('li', null, pu, 'No verified quotes from the excerpt.');
    var oc = el('div', { class: 'gcol ours' }, g);
    el('h3', null, oc, 'Our examples & simplifications');
    var ou = el('ul', null, oc);
    (s.simplifications || []).forEach(function (t) { rich(el('li', null, ou), t); });
    el('p', { class: 'disclaimer' }, host,
      'This page is a small illustrative demonstration of the mechanism described in the source. The numbers come from the toy inputs above, computed live in your browser; they do not reproduce the paper’s experiments or reported results.');
  }

  function buildChecks() {
    var host = section('checks');
    el('p', null, host, 'These checks run the same calculation as the playground on fixed inputs when the page loads.');
    var ul = el('ul', { class: 'checklist' }, host);
    var tests = Array.isArray(spec.tests) ? spec.tests : [];
    (spec.explorations || []).forEach(function (x) { if (x.state && Array.isArray(x.expect) && x.expect.length) tests = tests.concat([{ name: 'Exploration: ' + plain(x.title || ''), state: x.state, expect: x.expect }]); });
    var pass = 0;
    tests.forEach(function (t) {
      var li = el('li', null, ul), ok = true, lines = [];
      try {
        var st = mergeState(defaultState(), t.state || {});
        var out = runCompute(st);
        (t.expect || []).forEach(function (ex) { var r = evalExpect(ex, out, st); ok = ok && r.ok; lines.push(r.text); });
      } catch (e) { ok = false; lines.push('error: ' + e.message); }
      if (ok) pass++;
      el('span', { class: ok ? 'ok' : 'no' }, li, ok ? '✓' : '✗');
      var d = el('div', null, li);
      el('div', null, d, t.name || 'check');
      el('div', { class: 'detail' }, d, lines.join(' · '));
    });
    if (!tests.length) el('li', null, ul, 'No checks defined.');
    document.body.setAttribute('data-checks', pass + '/' + tests.length);
  }

  function bootError(msg) {
    var d = document.createElement('p');
    d.className = 'boot-error'; d.textContent = msg;
    document.body.insertBefore(d, document.body.firstChild);
  }

  var lastWidth = window.innerWidth, resizeTimer = null;
  window.addEventListener('resize', function () {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (lastOut) renderers.forEach(function (r) { r(lastOut); }); }, 120);
  });

  // ---------- boot ----------
  guard('hero', buildHero);
  guard('intro', buildIntro);
  guard('playground', buildControls);
  guard('playground', buildVisuals);
  guard('explore', buildExplorations);
  guard('limits', buildLimits);
  guard('source', buildSource);
  guard('playground', update);
  guard('checks', buildChecks);
  document.body.setAttribute('data-ready', '1');
})();
