// KidsUP — базовые сценарии сайта

// Мобильное меню
const burger = document.getElementById('burger');
const nav = document.getElementById('nav');

if (burger && nav) {
  burger.addEventListener('click', () => {
    nav.classList.toggle('open');
  });

  nav.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => nav.classList.remove('open'));
  });
}

// Форма заявки (демо: подключите свой бэкенд или CRM)
document.querySelectorAll('#lead-form').forEach((form) => {
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const name = form.querySelector('[name="name"]').value.trim();
    alert(
      (name ? name + ', спасибо' : 'Спасибо') +
        ' за заявку! Мы свяжемся с вами в ближайшее время.\n\nТелефон центра: +7 (495) 120-90-24'
    );
    form.reset();
  });
});
