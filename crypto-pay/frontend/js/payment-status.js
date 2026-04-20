/**
 * payment-status.js — Poll payment confirmation status
 * Part of the crypto-pay library. Can be loaded as a standalone module.
 *
 * Usage:
 *   PaymentStatus.poll('order-uuid', 5000, function(status) {
 *     console.log('Status changed to:', status);
 *   });
 *   PaymentStatus.stop('order-uuid');
 */
(function (global) {
  'use strict';

  var PaymentStatus = global.PaymentStatus || {};

  /** Internal timer registry: orderId → intervalId */
  PaymentStatus._timers = PaymentStatus._timers || {};

  /**
   * Begin polling an order's status from the API.
   *
   * The callback is invoked every time the status changes.
   * Polling automatically continues until stopped with .stop().
   *
   * @param {string}   orderId   - Payment order UUID
   * @param {number}   interval  - Polling interval in milliseconds
   * @param {Function} onChange  - Called with (status) when the status changes
   * @param {string}   [apiBase] - API base URL (default '/api')
   */
  PaymentStatus.poll = function (orderId, interval, onChange, apiBase) {
    var self = this;
    apiBase = apiBase || (global.CryptoPay ? global.CryptoPay._config.apiBase : '/api');
    var lastStatus = null;

    function check() {
      var url = apiBase + '/order/' + encodeURIComponent(orderId) + '/status';
      fetch(url, { credentials: 'omit' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data && data.status && data.status !== lastStatus) {
            lastStatus = data.status;
            try { onChange(data.status); } catch (e) { /* ignore */ }
          }
        })
        .catch(function () { /* silent — transient network errors are non-fatal */ });
    }

    check();
    this._timers[orderId] = setInterval(check, interval);
  };

  /**
   * Stop polling for an order.
   * @param {string} orderId
   */
  PaymentStatus.stop = function (orderId) {
    clearInterval(this._timers[orderId]);
    delete this._timers[orderId];
  };

  global.PaymentStatus = PaymentStatus;

}(typeof globalThis !== 'undefined' ? globalThis : typeof window !== 'undefined' ? window : this));
