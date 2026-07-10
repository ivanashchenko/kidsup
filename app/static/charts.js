/* Лёгкие SVG-графики без внешних зависимостей. */
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

  function svgEl(tag, attrs) {
    const el = document.createElementNS(NS, tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function makeSvg(w, h) {
    const svg = svgEl('svg', { viewBox: `0 0 ${w} ${h}`, class: 'chart-svg' });
    return svg;
  }

  function niceMax(v) {
    if (v <= 0) return 1;
    const pow = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / pow;
    const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return step * pow;
  }

  // сетка + подписи оси Y; возвращает функцию y(value)
  function drawAxes(svg, W, H, pad, maxV) {
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const v = maxV * i / ticks;
      const y = H - pad.b - (H - pad.t - pad.b) * i / ticks;
      svg.appendChild(svgEl('line', {
        x1: pad.l, y1: y, x2: W - pad.r, y2: y,
        stroke: '#e3e8f0', 'stroke-width': 1,
      }));
      const t = svgEl('text', {
        x: pad.l - 8, y: y + 4, 'text-anchor': 'end',
        'font-size': 11, fill: '#6b7487',
      });
      t.textContent = fmt.format(v);
      svg.appendChild(t);
    }
    return v => H - pad.b - (H - pad.t - pad.b) * (v / maxV);
  }

  function drawXLabels(svg, labels, xCenter, H, pad) {
    const step = Math.ceil(labels.length / 12);
    labels.forEach((lab, i) => {
      if (i % step !== 0) return;
      const t = svgEl('text', {
        x: xCenter(i), y: H - pad.b + 16, 'text-anchor': 'middle',
        'font-size': 11, fill: '#6b7487',
      });
      t.textContent = lab;
      svg.appendChild(t);
    });
  }

  function legend(el, series) {
    const box = document.createElement('div');
    box.className = 'legend';
    series.forEach(s => {
      const item = document.createElement('span');
      const dot = document.createElement('span');
      dot.className = 'dot';
      dot.style.background = s.color;
      item.appendChild(dot);
      item.appendChild(document.createTextNode(s.name));
      box.appendChild(item);
    });
    el.appendChild(box);
  }

  function bars(el, cfg) {
    const W = 800, H = 300, pad = { l: 56, r: 10, t: 12, b: 30 };
    const labels = cfg.labels, series = cfg.series;
    const maxV = niceMax(Math.max(1, ...series.flatMap(s => s.data.map(v => v || 0))));
    const svg = makeSvg(W, H);
    const y = drawAxes(svg, W, H, pad, maxV);
    const plotW = W - pad.l - pad.r;
    const slot = plotW / labels.length;
    const barW = Math.max(2, (slot * 0.7) / series.length);

    series.forEach((s, si) => {
      s.data.forEach((v, i) => {
        const val = v || 0;
        const x = pad.l + slot * i + (slot - barW * series.length) / 2 + barW * si;
        const rect = svgEl('rect', {
          x, y: y(val), width: barW,
          height: Math.max(0, (H - pad.b) - y(val)),
          fill: s.color, rx: 2,
        });
        const title = svgEl('title', {});
        title.textContent = `${labels[i]} · ${s.name}: ${fmt.format(val)}`;
        rect.appendChild(title);
        svg.appendChild(rect);
      });
    });
    drawXLabels(svg, labels, i => pad.l + slot * i + slot / 2, H, pad);
    el.appendChild(svg);
    if (series.length > 1) legend(el, series);
  }

  function line(el, cfg) {
    const W = 800, H = 300, pad = { l: 56, r: 10, t: 12, b: 30 };
    const labels = cfg.labels, series = cfg.series;
    const maxV = cfg.maxY || niceMax(Math.max(1, ...series.flatMap(s => s.data.map(v => v || 0))));
    const svg = makeSvg(W, H);
    const y = drawAxes(svg, W, H, pad, maxV);
    const plotW = W - pad.l - pad.r;
    const x = i => pad.l + (labels.length === 1 ? plotW / 2 : plotW * i / (labels.length - 1));

    series.forEach(s => {
      let d = '';
      s.data.forEach((v, i) => {
        if (v === null || v === undefined) return;
        d += (d ? ' L' : 'M') + `${x(i)} ${y(v)}`;
      });
      if (d) {
        svg.appendChild(svgEl('path', {
          d, fill: 'none', stroke: s.color, 'stroke-width': 2.5,
          'stroke-linejoin': 'round', 'stroke-linecap': 'round',
        }));
      }
      s.data.forEach((v, i) => {
        if (v === null || v === undefined) return;
        const c = svgEl('circle', { cx: x(i), cy: y(v), r: 3.5, fill: s.color });
        const title = svgEl('title', {});
        title.textContent = `${labels[i]} · ${s.name}: ${fmt.format(v)}`;
        c.appendChild(title);
        svg.appendChild(c);
      });
    });
    drawXLabels(svg, labels, x, H, pad);
    el.appendChild(svg);
    if (series.length > 1) legend(el, series);
  }

  function donut(el, cfg) {
    const W = 320, H = 220, cx = 110, cy = 110, r = 80, width = 34;
    const total = cfg.items.reduce((a, x) => a + (x.value || 0), 0) || 1;
    const svg = makeSvg(W, H);
    let angle = -Math.PI / 2;
    cfg.items.forEach(item => {
      const frac = (item.value || 0) / total;
      const a2 = angle + frac * 2 * Math.PI;
      const large = frac > 0.5 ? 1 : 0;
      const p1 = [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
      const p2 = [cx + r * Math.cos(a2), cy + r * Math.sin(a2)];
      const path = svgEl('path', {
        d: `M ${p1[0]} ${p1[1]} A ${r} ${r} 0 ${large} 1 ${p2[0]} ${p2[1]}`,
        fill: 'none', stroke: item.color, 'stroke-width': width,
      });
      const title = svgEl('title', {});
      title.textContent = `${item.label}: ${fmt.format(item.value)} (${(frac * 100).toFixed(1)}%)`;
      path.appendChild(title);
      svg.appendChild(path);
      angle = a2;
    });
    el.appendChild(svg);
    legend(el, cfg.items.map(i => ({ name: `${i.label} — ${fmt.format(i.value)}`, color: i.color })));
  }

  window.KCharts = { bars, line, donut };
})();
