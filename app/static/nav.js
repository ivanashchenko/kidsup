/* Скрипты общей шапки (app/nav.py, shapka). 13.11.2026, шаг 2: перенесены из base.html,
   чтобы меню, бейдж и поиск работали и на страницах со своим HTML (пульт, бланки, долги…).
   Каждый блок начинается с проверки, что его элементы на странице есть.
   Педагогу шапка скрипт не подключает вовсе; флаг KIDSUP_PED проверяем на всякий случай. */
(function () {
  'use strict';
  var koren = document.getElementById('kidsup-nav');
  if (!koren || window.__knGotovo) return;
  window.__knGotovo = true;
  var telefon = window.matchMedia ? window.matchMedia('(max-width: 919.98px)') : { matches: false };

  /* --- шапка во всю ширину окна ---------------------------------------------
     У части страниц со своим HTML у body поля или ограничена ширина (стоп-лист, контроль
     оплат, сверка честности): шапка, вставленная в такой body, вышла бы узкой и со сдвигом.
     Меряем и растягиваем до краёв окна; на страницах без полей ничего не меняется. */
  (function () {
    function rastyanut() {
      koren.style.marginLeft = koren.style.marginRight = koren.style.marginTop = koren.style.width = '';
      var r = koren.getBoundingClientRect();
      var w = document.documentElement.clientWidth;
      var sverhu = r.top + (window.pageYOffset || 0);
      if (r.left > 0.5 || Math.abs(r.width - w) > 1) {
        koren.style.marginLeft = (-r.left) + 'px';
        koren.style.marginRight = (r.right - w) + 'px';
        koren.style.width = w + 'px';
      }
      if (sverhu > 0.5 && sverhu < 80) koren.style.marginTop = (-sverhu) + 'px';
    }
    rastyanut();
    window.addEventListener('resize', rastyanut);
    window.addEventListener('load', rastyanut);
  })();

  /* --- место под нижнюю панель ------------------------------------------- */
  (function () {
    var panel = koren.querySelector('.kn-panel');
    if (!panel || !document.body) return;
    document.body.classList.add('kn-s-panelyu');
  })();

  /* --- лист «☰ Разделы» на телефоне: открыть, закрыть, гармошка ------------ */
  (function () {
    var knopka = document.getElementById('nav-toggle');
    var list = document.getElementById('nav-razdely');
    if (!knopka || !list) return;
    var zakryt = document.getElementById('kn-zakryt');
    var kto = koren.querySelector('.kn-kto');
    var mestoKto = document.getElementById('kn-list-kto');
    var ktoDom = kto ? kto.parentNode : null;   // 13.11.2026: полоска живёт в верхней строке (.kn-verh) — туда и возвращаем
    if (ktoDom === koren) ktoDom = null;
    var vIstorii = false;   // открытие листа положено в историю — «назад» его закроет

    function pokazat(da) {
      koren.classList.toggle('kn-otkryt', da);
      document.documentElement.classList.toggle('kn-zamok', da);
      knopka.setAttribute('aria-expanded', da ? 'true' : 'false');
      // «Вы: Лена · Выйти / сменить» на телефоне показываем в листе, а не над страницей
      if (kto && mestoKto && !kto.classList.contains('kn-kto-vazhno')) {
        if (da) mestoKto.appendChild(kto); else if (ktoDom) ktoDom.appendChild(kto); else koren.insertBefore(kto, koren.firstChild);
      }
      if (da && zakryt) { try { zakryt.focus({ preventScroll: true }); } catch (e) {} }
    }
    function otkryt() {
      if (koren.classList.contains('kn-otkryt')) return;
      pokazat(true);
      try { history.pushState({ knList: 1 }, ''); vIstorii = true; } catch (e) { vIstorii = false; }
    }
    function svernut() {
      if (!koren.classList.contains('kn-otkryt')) return;
      if (vIstorii) { vIstorii = false; try { history.back(); return; } catch (e) {} }
      pokazat(false);
    }
    knopka.addEventListener('click', function () {
      if (koren.classList.contains('kn-otkryt')) svernut(); else otkryt();
    });
    if (zakryt) zakryt.addEventListener('click', svernut);
    // жест «назад» закрывает лист, а не уводит со страницы
    window.addEventListener('popstate', function () {
      if (koren.classList.contains('kn-otkryt')) { vIstorii = false; pokazat(false); }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && koren.classList.contains('kn-otkryt')) svernut();
    });
    // переход по пункту: лист прячем сразу, запись в истории остаётся под новой страницей
    // 13.11.2026, проверка шага 2: запись листа оставалась в истории — после каждого перехода
    // через меню «назад» один раз не делал ничего. Обычный клик теперь заменяет запись листа
    // новой страницей (location.replace); Ctrl/Shift/средняя кнопка и target — как у браузера.
    list.addEventListener('click', function (e) {
      var a = e.target.closest ? e.target.closest('a') : null;
      if (!a || !koren.classList.contains('kn-otkryt')) return;
      var obychnyy = !e.defaultPrevented && !e.button && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey
        && (!a.target || a.target === '_self') && a.href && !a.hasAttribute('download');
      var byloVIstorii = vIstorii;
      vIstorii = false; pokazat(false);
      if (byloVIstorii && obychnyy) { e.preventDefault(); location.replace(a.href); }
    });
    // страницу перезагрузили при открытом листе (пульт обновляется сам каждые 5 минут): лист
    // закрыт, а его запись в истории осталась — убираем её, иначе «назад» один раз холостой
    try {
      if (history.state && history.state.knList && !koren.classList.contains('kn-otkryt')) history.back();
    } catch (e) {}
    // окно растянули до компьютерной ширины — лист не должен остаться поверх страницы
    if (telefon.addEventListener) telefon.addEventListener('change', function (e) {
      if (!e.matches && koren.classList.contains('kn-otkryt')) svernut();
    });
    // гармошка: на компьютере названия разделов — подписи, нажатия до них не доходят (nav.css)
    var imena = list.querySelectorAll('.kn-razdel-imya');
    Array.prototype.forEach.call(imena, function (b) {
      b.addEventListener('click', function () {
        var r = b.parentNode, da = !r.classList.contains('kn-raskryt');
        r.classList.toggle('kn-raskryt', da);
        b.setAttribute('aria-expanded', da ? 'true' : 'false');
      });
    });
  })();

  /* --- бейдж «ждут ответа» -------------------------------------------------
     Числа — из лёгкого счётчика /api/waiting/schet (кэш последнего расчёта), а не из
     /api/waiting: тот идёт в Mango до минуты, опрос с каждой вкладки тормозил бы сервер. */
  (function () {
    var b = document.getElementById('wait-badge');
    var url = koren.getAttribute('data-schet');
    if (!b || !url || window.KIDSUP_PED) return;
    var chislo = document.getElementById('kn-chislo');
    function tick() {
      if (document.hidden) return;
      fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (r) {
        if (!r.ok) throw 0;
        return r.json();
      }).then(function (w) {
        var p = w.perepiska || 0, n = w.bez_nomera || 0, z = w.zvonki || 0;
        if (chislo) { chislo.hidden = !(p + n); chislo.textContent = String(p + n); }
        if (!p && !n && !z) { b.hidden = true; return; }
        var chasti = [];
        if (p) chasti.push('💬 ' + p);
        // 14.10: чаты Telegram/MAX без номера — своей цифрой, отвечать в Wazzup
        if (n) chasti.push('📨 ' + n);
        if (z) chasti.push('☎ ' + z);
        b.textContent = (w.dengi ? '🔥 ' : '') + chasti.join(' · ') + ' ждут';
        b.classList.toggle('kn-goryacho', !!w.goryacho);
        b.hidden = false;
      }).catch(function () { /* сеть моргнула или вход истёк — попробуем в следующий раз */ });
    }
    tick();
    setInterval(tick, 180000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });
  })();

  /* --- поиск в шапке ------------------------------------------------------ */
  (function () {
    var inp = document.getElementById('gs-input'), drop = document.getElementById('gs-drop');
    if (!inp || !drop) return;
    var lupa = document.getElementById('kn-lupa');
    var t = null, sel = -1, items = [];
    function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML.replace(/"/g, '&quot;'); }
    /* 13.11.2026, вечер 1: поиск отдаёт ещё и страницы (тип «Раздел») — у них своя метка;
       карточка МойКласса (клиент без целого номера) открывается в новой вкладке, как на
       остальных страницах, а не вместо рабочей. */
    function vneshnyaya(u) { return /^https?:\/\//i.test(u || ''); }
    function narisovat() {
      if (!items.length) { drop.hidden = true; return; }
      drop.innerHTML = items.map(function (it, i) {
        return '<a href="' + esc(it.url) + '"' + (vneshnyaya(it.url) ? ' target="_blank" rel="noopener"' : '') +
          ' class="kn-gs-item' + (it.type === 'Раздел' ? ' kn-gs-razdel' : '') + (i === sel ? ' kn-vybran' : '') + '">' +
          '<span class="kn-gs-type">' + esc(it.type) + '</span>' +
          '<span class="kn-gs-title">' + esc(it.title) + '</span>' +
          '<span class="kn-gs-sub">' + esc(it.sub || '') + '</span></a>';
      }).join('');
      drop.hidden = false;
    }
    function iskat(q) {
      fetch('/api/search?q=' + encodeURIComponent(q)).then(function (r) { return r.json(); })
        .then(function (d) { items = d.items || []; sel = -1; narisovat(); })
        .catch(function () {});
    }
    function pole(da) {
      koren.classList.toggle('kn-poisk-otkryt', da);
      if (lupa) { lupa.setAttribute('aria-expanded', da ? 'true' : 'false'); lupa.textContent = da ? '✕' : '🔍'; }
      if (da) inp.focus(); else { drop.hidden = true; inp.blur(); }
    }
    if (lupa) lupa.addEventListener('click', function () { pole(!koren.classList.contains('kn-poisk-otkryt')); });
    inp.addEventListener('input', function () {
      clearTimeout(t);
      var q = inp.value.trim();
      if (q.length < 2) { drop.hidden = true; items = []; return; }
      t = setTimeout(function () { iskat(q); }, 220);
    });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { sel = Math.min(sel + 1, items.length - 1); narisovat(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { sel = Math.max(sel - 1, 0); narisovat(); e.preventDefault(); }
      else if (e.key === 'Enter' && sel >= 0 && items[sel]) {
        if (vneshnyaya(items[sel].url)) window.open(items[sel].url, '_blank', 'noopener'); else location.href = items[sel].url;
      }
      else if (e.key === 'Escape') { if (koren.classList.contains('kn-poisk-otkryt')) pole(false); else { drop.hidden = true; inp.blur(); } }
    });
    document.addEventListener('click', function (e) {
      if (!e.target.closest || !e.target.closest('.kn-poisk')) drop.hidden = true;
    });
    // «/» ставит курсор в поиск — как было в base.html; не мешаем вводу в поля страницы
    document.addEventListener('keydown', function (e) {
      var a = document.activeElement;
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
      if (a && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable)) return;
      e.preventDefault();
      if (telefon.matches) pole(true); else inp.focus();
    });
  })();
})();
