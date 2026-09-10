/* Отправка заявки с посадочных страниц курсов. Один файл на все страницы:
   курс и пометку страница передаёт через data-атрибуты тега script.
   Приём тот же, что на главной, — /api/public/lead с меткой Roistat. */
(function () {
  var self = document.currentScript;
  var COURSE = (self && self.dataset.course) || '';
  var NOTE = (self && self.dataset.note) || '';
  var form = document.getElementById('lead');
  if (!form) return;

  function normPhone(v) {
    var s = (v || '').replace(/\D/g, '');
    if (s.length === 11 && s[0] === '8') s = '7' + s.slice(1);
    return (s.length === 11 && s[0] === '7' && s[1] === '9') ? s : '';
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    var err = document.getElementById('f-phone-err');
    var p = normPhone(document.getElementById('f-phone').value);
    if (!p) { err.hidden = false; document.getElementById('f-phone').focus(); return; }
    err.hidden = true;
    var ok = document.getElementById('f-ok');
    if (ok && !ok.checked) { ok.focus(); return; }
    var btn = form.querySelector('button[type="submit"]');
    btn.disabled = true; btn.textContent = 'Отправляем…';
    var rv = (document.cookie.match(/roistat_visit=([^;]+)/) || [])[1] || '';
    try {
      var r = await fetch('https://app.kidsup.ru/api/public/lead', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: (document.getElementById('f-name') || {}).value || '',
          age: (document.getElementById('f-age') || {}).value || '',
          phone: p, course: COURSE, roistat: rv,
          note: (NOTE + ' | ' + (location.search || '').slice(1)).slice(0, 250),
          website: (document.getElementById('f-web') || {}).value || ''})});
      var j = await r.json();
      if (j.ok) {
        try { ym(69569509, 'reachGoal', 'lead', {course: COURSE}); } catch (_) {}
        try { window.roistat && window.roistat.event && window.roistat.event.send('lead'); } catch (_) {}
        form.innerHTML = '<div class="done"><h3 style="color:var(--green-ink)">Заявка принята</h3>' +
          '<p style="color:var(--muted)">Администратор перезвонит в течение 15 минут ' +
          'в рабочее время и подберёт время и группу.</p></div>';
        return;
      }
      throw new Error('bad');
    } catch (_) {
      btn.disabled = false; btn.textContent = 'Отправить заявку';
      err.textContent = 'Не получилось отправить. Позвоните нам: +7 495 120-90-24';
      err.hidden = false;
    }
  });
})();
