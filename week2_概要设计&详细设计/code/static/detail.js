const showImage = document.querySelector('#selected-image');
const showTime = document.querySelector('#selected-time');
const frames = document.querySelectorAll('.frame-choice');

for (const link of frames) {
  link.addEventListener('click', (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    showImage.src = link.href;
    showTime.textContent = `拍摄于 ${link.dataset.time}`;
    for (const frame of frames) frame.setAttribute('aria-current', String(frame === link));
  });
}

const canvas = document.querySelector('#trajectory');
const {track, events, regions} = JSON.parse(document.querySelector('#track-data').textContent);

function drawTrack() {
  if (track.length === 0) return;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);

  const xs = track.map((point) => point.x);
  const ys = track.map((point) => point.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const near = regions.filter((r) => r.min_x <= x1 && r.max_x >= x0 && r.min_y <= y1 && r.max_y >= y0);
  const left = Math.min(x0, ...near.map((r) => r.min_x));
  const right = Math.max(x1, ...near.map((r) => r.max_x));
  const bottom = Math.min(y0, ...near.map((r) => r.min_y));
  const top = Math.max(y1, ...near.map((r) => r.max_y));
  const scale = Math.min((width - 48) / Math.max(right - left, 10), (height - 56) / Math.max(top - bottom, 10));
  const X = (x) => width / 2 + (x - (left + right) / 2) * scale;
  const Y = (y) => (height - 14) / 2 - (y - (bottom + top) / 2) * scale;

  ctx.strokeStyle = '#e2ebe7';
  ctx.lineWidth = 1;
  for (let x = 20; x < width; x += 28) {
    ctx.beginPath(); ctx.moveTo(x, 10); ctx.lineTo(x, height - 26); ctx.stroke();
  }
  for (let y = 10; y < height - 26; y += 28) {
    ctx.beginPath(); ctx.moveTo(12, y); ctx.lineTo(width - 12, y); ctx.stroke();
  }
  for (const r of near) {
    const w = X(r.max_x) - X(r.min_x);
    const h = Y(r.min_y) - Y(r.max_y);
    ctx.fillStyle = '#d7e9e1'; ctx.strokeStyle = '#a3c4b8';
    ctx.fillRect(X(r.min_x), Y(r.max_y), w, h);
    ctx.strokeRect(X(r.min_x), Y(r.max_y), w, h);
  }
  ctx.beginPath();
  ctx.moveTo(X(track[0].x), Y(track[0].y));
  for (const point of track.slice(1)) ctx.lineTo(X(point.x), Y(point.y));
  ctx.strokeStyle = '#256f66'; ctx.lineWidth = 2.6; ctx.lineJoin = 'round'; ctx.stroke();

  function dot(point, radius, color) {
    ctx.beginPath(); ctx.arc(X(point.x), Y(point.y), radius, 0, 2 * Math.PI);
    ctx.fillStyle = color; ctx.fill();
  }
  dot(track[0], 4, '#5c8d86');
  dot(track[track.length - 1], 4, '#1e534d');
  for (const event of events) {
    dot(event, 7.5, '#fff');
    dot(event, 5.5, '#c05028');
  }
  ctx.fillStyle = '#5d6b7b'; ctx.font = '12px "Microsoft YaHei", sans-serif';
  ctx.fillText('地图平面坐标（米）', 12, height - 9);
  const scaleMeters = scale * 10 > width / 4 ? 1 : 10;
  const scaleStart = width - 24 - scale * scaleMeters;
  ctx.beginPath(); ctx.moveTo(scaleStart, height - 18); ctx.lineTo(width - 24, height - 18);
  ctx.strokeStyle = '#5d6b7b'; ctx.lineWidth = 2; ctx.stroke();
  ctx.fillText(`${scaleMeters} m`, scaleStart, height - 5);
}

drawTrack();
window.addEventListener('resize', drawTrack);
