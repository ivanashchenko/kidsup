/* Отправка заявки с посадочных страниц курсов. Один файл на все страницы:
   курс и пометку страница передаёт через data-атрибуты тега script.
   Приём тот же, что на главной, — /api/public/lead с меткой Roistat. */
window.__LP_COURSE = (document.currentScript && document.currentScript.dataset.course) || '';
// 25.09.2026 разбор рекламы (T1): метки и ClientID Метрики для заявки — общий ku-ref.js,
// чтобы не править теги на семи посадочных. Пока он не загрузился — метки из адреса (__kuT ниже).
(function () {
  if (window.__kuRef || document.querySelector('script[src*="ku-ref.js"]')) return;
  var s = document.createElement('script'); s.src = '/static/ku-ref.js'; s.async = true;
  (document.head || document.documentElement).appendChild(s);
})();
function __kuT() {
  try { if (window.__kuRef) return window.__kuRef(); } catch (_) {}
  /* 25.09.2026 после ревью: ku-ref.js не успел загрузиться или его отрезал блокировщик —
     раньше метки тогда пропадали совсем. Берём их из адреса, но только utm_*, yclid,
     ysclid, roistat, rs — без etext, из-за которого note обрезался и терялся yclid */
  var o = {qs: '', yclid: '', landing: location.pathname.slice(0, 120), utm: '',
           ym_cid: ((document.cookie.match(/(?:^|; )_ym_uid=([^;]+)/) || [])[1] || '')}, q = [];
  (location.search || '').slice(1).split('&').forEach(function (kv) {
    var i = kv.indexOf('='), k, v;
    try { k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i)).toLowerCase();
          v = i < 0 ? '' : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); } catch (_) { return; }
    if (!v || !/^(utm_[a-z_]+|yclid|ysclid|roistat|rs)$/.test(k)) return;
    v = v.replace(/[&|]/g, ' ').slice(0, 200); q.push(k + '=' + v);
    if (k === 'yclid') o.yclid = v;
  });
  o.qs = q.join('&').slice(0, 230);
  return o;
}
// 25.09.2026 после ревью: та же проверка, что normPhone в полной форме (8…, +7…, 10 цифр с 9)
function __kuPhoneOk(v) { return /^[78]?9\d{9}$/.test(String(v || '').replace(/\D/g, '')); }
// 25.09.2026 разбор рекламы (T4): автоцель «отправка формы» срабатывала и на неудачных
// отправках (19 раз против 4 lead) — ошибки считаем отдельной целью только для анализа
function __kuLeadError(reason, form) {
  try { ym(69569509, 'reachGoal', 'lead_error', {reason: reason, form: form}); } catch (_) {}
}
(function () {
  var self = document.currentScript;
  var COURSE = (self && self.dataset.course) || '';
  var NOTE = (self && self.dataset.note) || '';
  var form = document.getElementById('lead');
  if (!form) return;

  function normPhone(v) {
    var s = (v || '').replace(/\D/g, '');
    if (s.length === 11 && s[0] === '8') s = '7' + s.slice(1);
    // 24.09.2026 разбор рекламы: «916 123 45 67» без +7 форма отвергала («Проверьте номер»),
    // хотя главная и сервер такой номер принимают — с /english и /podgotovka 0 заявок
    if (s.length === 10 && s[0] === '9') s = '7' + s;
    return (s.length === 11 && s[0] === '7' && s[1] === '9') ? s : '';
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    var err = document.getElementById('f-phone-err');
    var p = normPhone(document.getElementById('f-phone').value);
    if (!p) { err.hidden = false; document.getElementById('f-phone').focus(); __kuLeadError('phone', 'full'); return; }
    err.hidden = true;
      var btn = form.querySelector('button[type="submit"]');
    btn.disabled = true; btn.textContent = 'Отправляем…';
    var rv = (document.cookie.match(/roistat_visit=([^;]+)/) || [])[1] || '';
    var t = __kuT(), why = 'network';
    try {
      var r = await fetch('https://app.kidsup.ru/api/public/lead', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: (document.getElementById('f-name') || {}).value || '',
          age: (document.getElementById('f-age') || {}).value || '',
          phone: p, course: COURSE, roistat: rv,
          // 25.09.2026: в note только метки (без etext) — раньше хвост адреса резал yclid
          note: (NOTE + ' | ' + (t.qs || '')).slice(0, 250),
          ym_cid: t.ym_cid || '', yclid: t.yclid || '', utm: t.utm || '', landing: t.landing || '',
          website: (document.getElementById('f-web') || {}).value || ''})});
      var j = await r.json();
      if (j.ok) {
        try { ym(69569509, 'reachGoal', 'lead', {course: COURSE}); } catch (_) {}
        // тот же лид — в пиксель VK Рекламы: без события в счётчике 3355457 кампании
        // ВК нечего оптимизировать, и они неделю платили за «посещение сайта»
        try { (window._tmr = window._tmr || []).push({type: 'reachGoal', id: '3355457', goal: 'lead'}); } catch (_) {}
        try { window.VK && VK.Goal && VK.Goal('lead'); } catch (_) {}
        try { window.roistat && window.roistat.event && window.roistat.event.send('lead'); } catch (_) {}
        form.innerHTML = '<div class="done"><h3 style="color:var(--green-ink)">Заявка принята</h3>' +
          '<p style="color:var(--muted)">Администратор перезвонит в течение 15 минут ' +
          'в рабочее время и подберёт время и группу.</p></div>';
        return;
      }
      why = 'rejected';
      throw new Error('bad');
    } catch (_) {
      __kuLeadError(why, 'full');
      btn.disabled = false; btn.textContent = 'Отправить заявку';
      err.textContent = 'Не получилось отправить. Позвоните нам: +7 495 120-90-24';
      err.hidden = false;
    }
  });

})();

// Клик по кнопке мессенджера — для половины родителей это и есть заявка:
// они пишут в WhatsApp, а не заполняют форму. Кнопки ведут через
// app.kidsup.ru/go/, счётчики сами такой переход целью не считают.
// 25.09.2026 разбор рекламы (T5): 22% кликов по /go/ — ссылки в соцсети (vk, youtube,
// instagram, tgchannel), а считались «messenger». Теперь messenger — только WhatsApp,
// Telegram и MAX, соцсети — отдельная цель social. Слушатель один на странице
// (раньше липкая панель слала цель второй раз) и не зависит от наличия формы #lead.
document.addEventListener('click', function (e) {
  var a = e.target.closest && e.target.closest('a[href*="app.kidsup.ru/go/"]');
  if (!a) return;
  var ch = (a.getAttribute('href').match(/\/go\/([a-z]+)/) || [])[1] || 'msg';
  if (!/^(whatsapp|telegram|max)$/.test(ch)) {
    try { ym(69569509, 'reachGoal', 'social', {channel: ch}); } catch (_) {}
    return;
  }
  try { ym(69569509, 'reachGoal', 'messenger', {channel: ch, course: window.__LP_COURSE || ''}); } catch (_) {}
  try { (window._tmr = window._tmr || []).push({type: 'reachGoal', id: '3355457', goal: 'messenger'}); } catch (_) {}
  try { window.VK && VK.Goal && VK.Goal('contact'); } catch (_) {}
}, true);

// Липкая панель на телефоне (владелец 15.09: форма на лендингах стоит в самом низу,
// 72% визитов — со смартфонов). Ссылки идут через /go/, чтобы клики по мессенджерам
// считались как цель messenger и получали визит Roistat.
(function () {
  if (document.querySelector('.stickybar')) return;
  var page = (location.pathname.replace(/^\//, '') || 'main').replace(/[^a-z0-9_-]/gi, '');
  var src = page + '_bar';
  var bar = document.createElement('div');
  bar.className = 'stickybar'; bar.setAttribute('role', 'navigation'); bar.setAttribute('aria-label', 'Быстрые действия');
  bar.innerHTML = '<a href="tel:+74951209024">📞 Позвонить</a><a href="#" id="sb-msg">💬 Написать</a>' +
                  '<a class="sb-main" href="#signup">Записаться</a>';
  var sheet = document.createElement('div');
  sheet.className = 'sb-sheet'; sheet.id = 'sb-sheet'; sheet.hidden = true;
  sheet.innerHTML =
    '<a href="https://app.kidsup.ru/go/whatsapp?src=' + src + '" target="_blank" rel="noopener"><i style="background:#25D366"></i>WhatsApp</a>' +
    '<a href="https://app.kidsup.ru/go/telegram?src=' + src + '" target="_blank" rel="noopener"><i style="background:#2AABEE"></i>Telegram</a>' +
    '<a href="https://app.kidsup.ru/go/max?src=' + src + '" target="_blank" rel="noopener"><i style="background:#8A2BE2"></i>MAX</a>';
  document.body.appendChild(bar); document.body.appendChild(sheet);
  bar.querySelector('#sb-msg').addEventListener('click', function (e) { e.preventDefault(); sheet.hidden = !sheet.hidden; });
  sheet.addEventListener('click', function (e) {
    var a = e.target.closest('a'); if (!a) return;
    // 25.09.2026 (T5): цель messenger шлёт общий слушатель /go/ выше — здесь был второй вызов
    sheet.hidden = true;
  });
  document.addEventListener('click', function (e) {
    if (!sheet.hidden && !sheet.contains(e.target) && e.target.id !== 'sb-msg') sheet.hidden = true;
  });
})();


/* 17.09: sticky «Записаться» открывает форму из одного поля на месте, а не уводит на форму
   в 14–18 тыс. px ниже (замер офлайн-рендера 390×844). */
(function(){
  var main=document.querySelector('.stickybar .sb-main'); if(!main) return;
  main.setAttribute('href','#'); main.id='sb-lead';
  var course=(window.__LP_COURSE||'') || 'мини-форма-sticky';
  var page=(location.pathname.replace(/^\//,'')||'main').replace(/[^a-z0-9_-]/gi,'');
  var fs=document.createElement('div'); fs.className='sb-sheet'; fs.id='sb-form'; fs.hidden=true;
  fs.innerHTML='<form class="mini-lead" data-mini="sticky" action="https://app.kidsup.ru/api/public/lead" method="post">'+
    '<b>Оставьте номер — перезвоним и подберём группу</b>'+
    '<input name="phone" type="tel" inputmode="tel" required placeholder="+7 916 000-00-00" autocomplete="tel" aria-label="Ваш телефон">'+
    '<input type="hidden" name="course" value="'+course.replace(/"/g,'')+'">'+
    '<input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" style="display:none">'+
    '<button type="submit">Перезвоните мне</button>'+
    '<p class="consent-note">Нажимая «Перезвоните мне», вы соглашаетесь с <a href="https://kidsup.ru/privacy" target="_blank" rel="noopener">политикой обработки персональных данных</a>.</p>'+
    '<span class="ok-msg" hidden>Принято! Позвоним в течение 15 минут ✅ Хотите быстрее — <a href="https://app.kidsup.ru/go/whatsapp?src='+page+'_bar_ok&t=%D0%97%D0%B4%D1%80%D0%B0%D0%B2%D1%81%D1%82%D0%B2%D1%83%D0%B9%D1%82%D0%B5!%20%D0%A5%D0%BE%D1%87%D1%83%20%D0%B7%D0%B0%D0%BF%D0%B8%D1%81%D0%B0%D1%82%D1%8C%D1%81%D1%8F%20%D0%BD%D0%B0%20%D0%BF%D0%B5%D1%80%D0%B2%D0%BE%D0%B5%20%D0%B7%D0%B0%D0%BD%D1%8F%D1%82%D0%B8%D0%B5" target="_blank" rel="noopener">напишите в WhatsApp</a></span></form>';
  var bar=document.querySelector('.stickybar'); bar.parentNode.insertBefore(fs, bar);
  function toggle(e){ e.preventDefault(); var msg=document.getElementById('sb-sheet'); if(msg) msg.hidden=true; fs.hidden=!fs.hidden; if(!fs.hidden){ var i=fs.querySelector('input[name=phone]'); try{i.focus({preventScroll:true});}catch(_){} } }
  main.addEventListener('click', toggle);
  var hb=document.querySelector('header .btn.btn-green[href="#signup"], header a.nav-cta[href="#signup"]');
  if(hb) hb.addEventListener('click', function(e){ if(window.matchMedia('(max-width:760px)').matches) toggle(e); });
  document.addEventListener('click', function(e){ if(!fs.hidden && !fs.contains(e.target) && e.target!==main && !main.contains(e.target)) fs.hidden=true; });
})();
/* кнопка «Записаться» в hero ведёт в мини-форму рядом, а не на форму в конце страницы */
document.querySelectorAll('a[href="#hero-phone"]').forEach(function(a){
  a.addEventListener('click', function(e){ e.preventDefault(); var i=document.getElementById('hero-phone');
    if(i){ i.scrollIntoView({block:'center',behavior:'smooth'}); setTimeout(function(){ try{i.focus({preventScroll:true});}catch(_){ i.focus(); } },350); } });
});
/* номер визита Roistat и метки — во все ссылки мессенджеров (иначе заявка из WhatsApp
   не привязывается к кампании) */
window.addEventListener('DOMContentLoaded', function(){
  setTimeout(function(){
    var rv=(document.cookie.match(/roistat_visit=([^;]+)/)||[])[1]||'';
    var utm=(location.search||'').replace(/^\?/,'');
    document.querySelectorAll('a[href*="app.kidsup.ru/go/"]').forEach(function(a){
      if(/[?&]rv=/.test(a.href)) return;
      var extra=(rv?'&rv='+encodeURIComponent(rv):'')+(utm?'&'+utm:'');
      if(extra) a.href=a.href+extra;
    });
  },1200);
});

// Мини-форма «только телефон» на первом экране (15.09): форма внизу страницы
// теряет 72% мобильных визитов, которые до неё не доходят.
document.querySelectorAll('.mini-lead').forEach(function (f) {
  if (f.dataset.bound) return; f.dataset.bound = '1';
  f.addEventListener('submit', function (e) {
    e.preventDefault();
    var btn = f.querySelector('button'), ok = f.querySelector('.ok-msg');
    var rv = (document.cookie.match(/roistat_visit=([^;]+)/) || [])[1] || '';
    var kind = f.dataset.mini === '0' ? 'hero' : (f.dataset.mini === 'sticky' ? 'sticky' : 'mini');
    /* 25.09.2026 после ревью: мини-форма уходила на сервер с любым вводом («abc», «12345») —
       проверяем номер в браузере так же, как полная форма */
    if (!__kuPhoneOk(f.phone.value)) { __kuLeadError('phone', kind); alert('Проверьте номер телефона: 10 цифр после +7'); f.phone.focus(); return; }
    var t = __kuT();
    btn.disabled = true; btn.textContent = 'Отправляем…';
    fetch(f.action, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({phone: f.phone.value, course: f.course.value,
                            website: f.website.value, roistat: rv,
                            ym_cid: t.ym_cid || '', yclid: t.yclid || '', utm: t.utm || '', landing: t.landing || '',
                            note: ('мини-форма ' + kind + ' ' + location.pathname + ' | ' + (t.qs || '')).slice(0, 250)})})
      .then(function (r) { return r.json() })
      .then(function (d) {
        if (d.ok) {
          btn.hidden = true; f.phone.hidden = true; ok.hidden = false;
          var wa = f.querySelector('.alt-contacts') || f.querySelector('.wa-inline'); if (wa) wa.hidden = true;
          try { ym(69569509, 'reachGoal', 'lead', {form: kind}); } catch (_) {}
          try { (window._tmr = window._tmr || []).push({type: 'reachGoal', id: '3355457', goal: 'lead'}); } catch (_) {}
          try { window.VK && VK.Goal && VK.Goal('lead'); } catch (_) {}
          try { window.roistat && window.roistat.event && window.roistat.event.send('lead'); } catch (_) {}
        } else {
          __kuLeadError('rejected', kind);
          btn.disabled = false; btn.textContent = 'Перезвоните мне';
          alert(d.error || 'Проверьте номер');
        }
      }).catch(function () {
        __kuLeadError('network', kind);
        btn.disabled = false; btn.textContent = 'Перезвоните мне';
        alert('Не получилось — позвоните нам: +7 495 120-90-24');
      });
  });
});
