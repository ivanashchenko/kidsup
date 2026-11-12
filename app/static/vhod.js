/* Вход истёк посреди работы (12.11.2026, личные входы, app/vhod.py).
 *
 * Сервер на запрос со страницы без входа отвечает 401 с заголовком X-Vhod: nuzhen.
 * Тогда уводим на страницу входа и возвращаемся на эту же страницу после входа.
 * Обещание fetch при этом не завершается: страница не успеет показать «Не сохранилось».
 * Ответ 401 без X-Vhod (браузер, где через форму ещё не входили) — как раньше, не трогаем.
 *
 * После удачного входа запоминает в этом браузере имя вошедшего: страница входа
 * покажет его кнопкой. Пароль не хранится нигде.
 */
(function () {
  if (window.__kidsupVhod) return;
  window.__kidsupVhod = true;
  var f = window.fetch;
  if (!f) return;
  var ushli = false;

  window.fetch = function () {
    return f.apply(this, arguments).then(function (r) {
      if (r && r.status === 401 && r.headers && r.headers.get('X-Vhod')) {
        if (!ushli) {
          ushli = true;
          location.href = '/vhod?next=' + encodeURIComponent(location.pathname + location.search);
        }
        return new Promise(function () {});
      }
      return r;
    });
  };

  // имя вошедшего — для кнопок на странице входа
  var KL = 'kidsup_vhod_imena', ZH = 'kidsup_vhod_zhdu';
  try {
    if (location.pathname !== '/vhod' && sessionStorage.getItem(ZH)) {
      sessionStorage.removeItem(ZH);
      f('/api/kto', {credentials: 'same-origin'}).then(function (r) {
        return r.ok ? r.json() : null;
      }).then(function (d) {
        if (!d || !d.imya || d.sposob !== 'forma') return;
        var s = [];
        try { s = JSON.parse(localStorage.getItem(KL) || '[]'); } catch (e) {}
        if (!Array.isArray(s)) s = [];
        s = [d.imya].concat(s.filter(function (x) { return x !== d.imya; })).slice(0, 6);
        localStorage.setItem(KL, JSON.stringify(s));
      }).catch(function () {});
    }
  } catch (e) {}
})();
