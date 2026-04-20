/**
 * price-ticker.js — Live price update via API polling
 * Part of the crypto-pay library. Can be loaded as a standalone module.
 *
 * Usage:
 *   PriceTicker.start('usd', 30000);
 *   // Any element with data-cp-price="bitcoin" will be updated automatically.
 */
(function (global) {
  'use strict';

  var PriceTicker = global.PriceTicker || {};

  /** Internal timer registry: fiat → intervalId */
  PriceTicker._timers = PriceTicker._timers || {};

  /**
   * Start polling prices and updating DOM elements tagged with data-cp-price.
   *
   * @param {string} fiat      - Fiat currency code (e.g. 'usd')
   * @param {number} interval  - Polling interval in milliseconds (default 30 000)
   * @param {string} [apiBase] - API base URL (default '/api')
   */
  PriceTicker.start = function (fiat, interval, apiBase) {
    var self = this;
    fiat = (fiat || 'usd').toLowerCase();
    interval = interval || 30000;
    apiBase = apiBase || (global.CryptoPay ? global.CryptoPay._config.apiBase : '/api');

    function update() {
      fetch(apiBase + '/prices?fiat=' + encodeURIComponent(fiat), { credentials: 'omit' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data || !data.prices) return;
          Object.keys(data.prices).forEach(function (coinId) {
            var price = data.prices[coinId];
            document.querySelectorAll('[data-cp-price="' + coinId + '"]').forEach(function (el) {
              el.textContent = price.toLocaleString(undefined, { maximumFractionDigits: 8 });
            });
          });
        })
        .catch(function () { /* silent — network errors are non-fatal */ });
    }

    update();
    self._timers[fiat] = setInterval(update, interval);
  };

  /**
   * Stop price polling for a fiat currency.
   * @param {string} fiat
   */
  PriceTicker.stop = function (fiat) {
    clearInterval(this._timers[fiat]);
    delete this._timers[fiat];
  };

  global.PriceTicker = PriceTicker;

}(typeof globalThis !== 'undefined' ? globalThis : typeof window !== 'undefined' ? window : this));
