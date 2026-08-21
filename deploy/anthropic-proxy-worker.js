// Прокси к Anthropic API для KidsUP.
//
// Зачем он нужен. Сервер app.kidsup.ru стоит в России, и api.anthropic.com
// отвечает ему 403 по региону. Воркер живёт в сети Cloudflare за пределами
// РФ и просто пересылает запросы: сервер обращается к воркеру, воркер —
// к Anthropic, ответ идёт обратно тем же путём.
//
// Ключ Anthropic здесь НЕ хранится. Он остаётся в настройках сервера
// и проходит через воркер транзитом внутри TLS — воркер его не читает
// и не пишет в логи. Это важно: секрет живёт в одном месте, а не в двух.
//
// Защита от посторонних. Воркер отвечает только на запросы с правильным
// заголовком X-KidsUP-Auth. Без него это был бы открытый прокси, которым
// быстро начнут пользоваться чужие.

const UPSTREAM = "https://api.anthropic.com";

// Заголовки, которые Anthropic ждёт от клиента. Всё остальное не пересылаем:
// лишние заголовки Cloudflare (cf-connecting-ip и прочие) там не нужны.
const PASS_THROUGH = [
  "x-api-key",
  "authorization",
  "anthropic-version",
  "anthropic-beta",
  "content-type",
  "accept",
];

export default {
  async fetch(request, env) {
    // Общий секрет: задаётся командой wrangler secret put SHARED_SECRET
    // или в панели Cloudflare → Settings → Variables.
    const expected = env.SHARED_SECRET;
    if (!expected) {
      return json({ error: "SHARED_SECRET не задан в настройках воркера" }, 500);
    }
    if (request.headers.get("x-kidsup-auth") !== expected) {
      return json({ error: "forbidden" }, 403);
    }

    // Проверка живости: чтобы убедиться, что воркер работает,
    // не тратя запрос к модели и не имея ключа под рукой.
    const url = new URL(request.url);
    if (url.pathname === "/__ping") {
      return json({ ok: true, upstream: UPSTREAM });
    }

    const headers = new Headers();
    for (const name of PASS_THROUGH) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }

    let upstreamResponse;
    try {
      upstreamResponse = await fetch(UPSTREAM + url.pathname + url.search, {
        method: request.method,
        headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      });
    } catch (err) {
      // Сеть до Anthropic отвалилась. Отвечаем 502, а не молчанием:
      // сервер должен отличать «модель отказала» от «прокси не доехал».
      return json({ error: "upstream_unreachable", detail: String(err) }, 502);
    }

    // Ответ отдаём как есть, включая потоковый: тело не буферизуем.
    const out = new Headers(upstreamResponse.headers);
    out.delete("content-encoding");   // тело уже распаковано fetch-ем
    out.delete("content-length");
    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      headers: out,
    });
  },
};

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
