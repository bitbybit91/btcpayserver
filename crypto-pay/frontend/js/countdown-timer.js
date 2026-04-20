/**
 * countdown-timer.js — Payment window countdown timer
 * Part of the crypto-pay library. Can be loaded as a standalone module.
 *
 * Usage:
 *   CountdownTimer.start('timer-element-id', '2025-01-01T12:30:00Z', function() {
 *     console.log('Payment window expired');
 *   });
 */
(function (global) {
  'use strict';

  var CountdownTimer = global.CountdownTimer || {};

  /** Internal timer registry: elementId → intervalId */
  CountdownTimer._timers = CountdownTimer._timers || {};

  /**
   * Start a countdown in the element with the given ID.
   *
   * The element receives one of three CSS classes based on time remaining:
   *  - .crypto-pay-timer                 (> 2 minutes)
   *  - .crypto-pay-timer-warning         (≤ 2 minutes)
   *  - .crypto-pay-timer-expired         (0 seconds)
   *
   * @param {string}        elementId   - ID of the DOM element to update
   * @param {string|Date}   expiresAt   - ISO timestamp string or Date object
   * @param {Function}      [onExpired] - Callback invoked when timer reaches zero
   */
  CountdownTimer.start = function (elementId, expiresAt, onExpired) {
    var self = this;
    var el = document.getElementById(elementId);
    if (!el) return;

    var expiry = (expiresAt instanceof Date) ? expiresAt : new Date(expiresAt);

    function tick() {
      var remaining = Math.floor((expiry.getTime() - Date.now()) / 1000);

      if (remaining <= 0) {
        el.className = 'crypto-pay-timer crypto-pay-timer-expired';
        el.textContent = 'Payment window expired';
        clearInterval(self._timers[elementId]);
        delete self._timers[elementId];
        if (typeof onExpired === 'function') onExpired();
        return;
      }

      var mins = Math.floor(remaining / 60);
      var secs = remaining % 60;
      var formatted = mins + ':' + (secs < 10 ? '0' : '') + secs;
      var cssClass = remaining < 120
        ? 'crypto-pay-timer crypto-pay-timer-warning'
        : 'crypto-pay-timer';

      el.className = cssClass;
      el.innerHTML =
        '&#9201; Expires in <span class="crypto-pay-timer-value">' + formatted + '</span>';
    }

    tick();
    this._timers[elementId] = setInterval(tick, 1000);
  };

  /**
   * Stop the countdown for the given element.
   * @param {string} elementId
   */
  CountdownTimer.stop = function (elementId) {
    clearInterval(this._timers[elementId]);
    delete this._timers[elementId];
  };

  global.CountdownTimer = CountdownTimer;

}(typeof globalThis !== 'undefined' ? globalThis : typeof window !== 'undefined' ? window : this));
