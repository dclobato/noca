// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
  if (typeof confetti === 'undefined') return;

  var celebrated = new Set();
  var activeInterval = null;

  function randomInRange(min, max) {
    return Math.random() * (max - min) + min;
  }

  function startRunsConfetti(options) {
    var config = options || {};
    var duration = typeof config.duration === 'number' ? config.duration : 3 * 1000;
    var intervalMs = typeof config.intervalMs === 'number' ? config.intervalMs : 250;
    var animationEnd = Date.now() + duration;
    var defaults = {
      startVelocity: typeof config.startVelocity === 'number' ? config.startVelocity : 25,
      spread: typeof config.spread === 'number' ? config.spread : 360,
      ticks: typeof config.ticks === 'number' ? config.ticks : 90,
      zIndex: typeof config.zIndex === 'number' ? config.zIndex : 0
    };
    var leftMin = typeof config.leftMin === 'number' ? config.leftMin : 0.1;
    var leftMax = typeof config.leftMax === 'number' ? config.leftMax : 0.3;
    var rightMin = typeof config.rightMin === 'number' ? config.rightMin : 0.7;
    var rightMax = typeof config.rightMax === 'number' ? config.rightMax : 0.9;
    var particleBase = typeof config.particleBase === 'number' ? config.particleBase : 100;
    var yJitter = typeof config.yJitter === 'number' ? config.yJitter : 0.2;

    if (activeInterval !== null) {
      clearInterval(activeInterval);
      activeInterval = null;
    }

    activeInterval = setInterval(function () {
      var timeLeft = animationEnd - Date.now();
      if (timeLeft <= 0) {
        clearInterval(activeInterval);
        activeInterval = null;
        return;
      }
      var particleCount = particleBase * (timeLeft / duration);
      confetti(Object.assign({}, defaults, {
        particleCount: particleCount,
        origin: { x: randomInRange(leftMin, leftMax), y: Math.random() - yJitter }
      }));
      confetti(Object.assign({}, defaults, {
        particleCount: particleCount,
        origin: { x: randomInRange(rightMin, rightMax), y: Math.random() - yJitter }
      }));
    }, intervalMs);
  }

  window.testRunsConfetti = startRunsConfetti;

  document.addEventListener('runs:accepted-visible', function (evt) {
    var d = evt.detail;
    if (!d || !d.teamId || !d.problemId || !d.verdict) return;

    var key = d.submissionId || (d.teamId + ':' + d.problemId + ':' + d.verdict);
    if (celebrated.has(key)) return;
    celebrated.add(key);
    startRunsConfetti();
  });
})();
