/* 25.09.2026 разбор рекламы (T1): метки рекламы и ClientID Метрики — в каждую заявку.
   До этого заявка несла только location.search текущей страницы: у обеих заявок
   с поиска Директа note обрезался на 250 символах из-за etext и yclid терялся,
   63% реальных номеров пришли вовсе без меток, а ClientID не передавался нигде —
   без него нет цены оплаты по кампаниям и офлайн-конверсий.

   При заходе с метками (utm_*, yclid, ysclid, roistat, rs) сохраняем касание
   в localStorage: ku_lt — последнее, ku_ft — первое (живёт 30 дней).
   Формы берут всё через window.__kuRef(). Любая ошибка хранилища — молча:
   заявка важнее аналитики.
   Подключают: site.html и english.html — тегом script, посадочные — через lp-lead.js.
   Имя файла нейтральное (не «track»), чтобы его не резали блокировщики рекламы;
   если файл всё же не загрузился, формы сами берут метки из адреса (kuT / __kuT). */
(function () {
  if (window.__kuRef) return;
  var COUNTER = 69569509, DAYS30 = 30 * 864e5;
  var KEY = /^(utm_[a-z_]+|yclid|ysclid|roistat|rs)$/;

  function parse(q) {
    var o = {}, n = 0;
    String(q || '').replace(/^\?/, '').split('&').forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf('='), k, v;
      try {
        k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i)).toLowerCase();
        v = i < 0 ? '' : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' '));
      } catch (_) { return; }
      if (!KEY.test(k) || !v) return;
      o[k] = v.replace(/[&|]/g, ' ').slice(0, 200); n++;
    });
    return n ? o : null;
  }
  function get(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (_) { return null; } }
  function put(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} }
  function fresh(t) { return t && t.ts && (Date.now() - t.ts) < DAYS30 ? t : null; }
  function cookie(n) {
    var m = document.cookie.match(new RegExp('(?:^|; )' + n + '=([^;]+)'));
    return m ? m[1] : '';
  }

  var cur = parse(location.search);
  try {
    if (cur) {
      var touch = {p: cur, landing: location.pathname, ts: Date.now()};
      put('ku_lt', touch);
      if (!fresh(get('ku_ft'))) put('ku_ft', touch);
    }
  } catch (_) {}

  // ClientID: ym(…,'getClientID') отвечает после загрузки тега, поэтому спрашиваем
  // заранее и держим ответ в window.__kuCid; запасной путь — cookie _ym_uid
  function askCid() {
    try {
      if (typeof window.ym === 'function')
        window.ym(COUNTER, 'getClientID', function (id) { if (id) window.__kuCid = String(id); });
    } catch (_) {}
  }
  askCid();
  window.addEventListener('load', function () { setTimeout(askCid, 1500); });

  // 25.09.2026 после ревью: раньше JSON резали .slice(0,1500) — при длинных метках
  // уходил битый JSON и сервер его не читал. Теперь значения укорачиваем до 100
  // символов, при превышении сначала отбрасываем первое касание, потом всё
  // (метки последнего касания всё равно едут в note).
  function short(t) {
    if (!t) return null;
    var p = {};
    Object.keys(t.p || {}).forEach(function (k) { p[k] = String(t.p[k]).slice(0, 100); });
    return {p: p, landing: String(t.landing || '').slice(0, 120), ts: t.ts};
  }
  function utmJson(lt, ft) {
    var s = JSON.stringify({lt: short(lt), ft: short(ft)});
    if (s.length > 1500) s = JSON.stringify({lt: short(lt), ft: null});
    return s.length > 1500 ? '' : s;
  }

  window.__kuRef = function () {
    askCid();
    var lt = fresh(get('ku_lt')), ft = fresh(get('ku_ft'));
    var p = cur || (lt && lt.p) || {};
    return {
      ym_cid: String(window.__kuCid || cookie('_ym_uid') || ''),
      yclid: p.yclid || '',
      utm: utmJson(lt, ft),
      landing: ((lt && lt.landing) || location.pathname).slice(0, 120),
      // для note: только метки, без etext и прочего хвоста адреса
      qs: Object.keys(p).map(function (k) { return k + '=' + p[k]; }).join('&').slice(0, 230)
    };
  };
})();
