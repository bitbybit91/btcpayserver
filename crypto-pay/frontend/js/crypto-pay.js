/**
 * crypto-pay.js — Core payment library (vanilla JS, no dependencies)
 * Self-hosted, no CDN calls, no cookies, privacy-friendly.
 *
 * @version 1.0.0
 */
(function (global) {
  'use strict';

  /* -----------------------------------------------------------------------
   * Configuration defaults
   * -------------------------------------------------------------------- */
  var DEFAULT_CONFIG = {
    apiBase: '/api',
    pollInterval: 5000,       // ms — status polling
    pricePollInterval: 30000, // ms — live price refresh
    lang: 'en',
  };

  /* -----------------------------------------------------------------------
   * CryptoPay namespace
   * -------------------------------------------------------------------- */
  var CryptoPay = {
    _config: Object.assign({}, DEFAULT_CONFIG),
    _orderListeners: [],

    /**
     * Initialise with user-supplied options.
     * @param {Object} opts
     */
    init: function (opts) {
      Object.assign(this._config, opts || {});
    },

    /**
     * Register a callback for order-status changes.
     * @param {Function} cb  called with (orderId, status)
     */
    onStatusChange: function (cb) {
      this._orderListeners.push(cb);
    },

    /**
     * Fire all status-change listeners.
     * @param {string} orderId
     * @param {string} status
     */
    _fireStatus: function (orderId, status) {
      this._orderListeners.forEach(function (cb) {
        try { cb(orderId, status); } catch (e) { /* ignore */ }
      });
    },

    /* -------------------------------------------------------------------
     * API helpers
     * ------------------------------------------------------------------ */

    /**
     * Fetch JSON from the payment API.
     * @param {string} path
     * @param {Object} [opts]  fetch options
     * @returns {Promise<Object>}
     */
    _api: function (path, opts) {
      var url = this._config.apiBase + path;
      return fetch(url, Object.assign({ credentials: 'omit' }, opts || {}))
        .then(function (r) { return r.json(); });
    },

    /**
     * Create a new payment order.
     * @param {Object} params
     * @returns {Promise<Object>} order
     */
    createOrder: function (params) {
      return this._api('/order/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
    },

    /**
     * Fetch order status.
     * @param {string} orderId
     * @returns {Promise<Object>}
     */
    getOrderStatus: function (orderId) {
      return this._api('/order/' + encodeURIComponent(orderId) + '/status');
    },

    /**
     * Submit a TX hash for manual verification.
     * @param {string} orderId
     * @param {string} txHash
     * @returns {Promise<Object>}
     */
    submitTxHash: function (orderId, txHash) {
      return this._api('/order/' + encodeURIComponent(orderId) + '/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tx_hash: txHash }),
      });
    },

    /**
     * Get price conversion.
     * @param {string} coin
     * @param {string} fiat
     * @param {number} amount
     * @returns {Promise<Object>}
     */
    getPrice: function (coin, fiat, amount) {
      var qs = '?coin=' + encodeURIComponent(coin) +
               '&fiat=' + encodeURIComponent(fiat) +
               (amount != null ? '&amount=' + encodeURIComponent(amount) : '');
      return this._api('/price' + qs);
    },

    /**
     * Get all prices for a fiat currency.
     * @param {string} fiat
     * @returns {Promise<Object>}
     */
    getAllPrices: function (fiat) {
      return this._api('/prices?fiat=' + encodeURIComponent(fiat || 'usd'));
    },

    /**
     * QR code URL for an order.
     * @param {string} orderId
     * @returns {string}
     */
    qrUrl: function (orderId) {
      return this._config.apiBase + '/qr/' + encodeURIComponent(orderId);
    },

    /* -------------------------------------------------------------------
     * Modal widget
     * ------------------------------------------------------------------ */

    /**
     * Open a payment modal for the given order parameters.
     * @param {Object} params  — same as createOrder()
     * @returns {Promise<void>}
     */
    openModal: function (params) {
      var self = this;
      return this.createOrder(params).then(function (order) {
        if (order.error) {
          console.error('[CryptoPay] Order creation failed:', order.error);
          return;
        }
        self._renderModal(order);
      });
    },

    /**
     * Close and remove the active payment modal.
     */
    closeModal: function () {
      var overlay = document.getElementById('crypto-pay-overlay');
      if (overlay) overlay.remove();
    },

    /* -------------------------------------------------------------------
     * Internal rendering
     * ------------------------------------------------------------------ */

    /**
     * Build and inject the payment modal into the DOM.
     * @param {Object} order
     */
    _renderModal: function (order) {
      var self = this;
      this.closeModal(); // remove any stale modal

      var overlay = document.createElement('div');
      overlay.id = 'crypto-pay-overlay';
      overlay.className = 'crypto-pay-overlay crypto-pay-active';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      overlay.setAttribute('aria-label', 'Cryptocurrency payment');

      overlay.innerHTML = self._modalHTML(order);
      document.body.appendChild(overlay);

      // Wire up close
      var closeBtn = document.getElementById('cp-close-btn');
      if (closeBtn) {
        closeBtn.addEventListener('click', function () { self.closeModal(); });
      }
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) self.closeModal();
      });

      // Wire up copy button
      var copyBtn = document.getElementById('cp-copy-btn');
      if (copyBtn) {
        copyBtn.addEventListener('click', function () {
          self._copyToClipboard(
            order.wallet_address,
            document.getElementById('cp-copy-toast')
          );
        });
      }

      // Wire up TX hash submission
      var txForm = document.getElementById('cp-tx-form');
      if (txForm) {
        txForm.addEventListener('submit', function (e) {
          e.preventDefault();
          var txInput = document.getElementById('cp-tx-input');
          var txHash = txInput ? txInput.value.trim() : '';
          if (!txHash) return;
          self.submitTxHash(order.order_id, txHash).then(function (res) {
            if (!res.error) {
              self._updateStatusBadge(res.status);
              self._fireStatus(order.order_id, res.status);
            }
          });
        });
      }

      // Load QR image
      var qrImg = document.getElementById('cp-qr-img');
      if (qrImg) {
        qrImg.src = self.qrUrl(order.order_id);
      }

      // Start countdown
      if (order.expires_at) {
        CountdownTimer.start('cp-timer', order.expires_at, function () {
          self._updateStatusBadge('expired');
          self._fireStatus(order.order_id, 'expired');
        });
      }

      // Start status polling
      PaymentStatus.poll(order.order_id, self._config.pollInterval, function (status) {
        self._updateStatusBadge(status);
        self._fireStatus(order.order_id, status);
        if (status === 'completed' || status === 'expired' || status === 'cancelled') {
          PaymentStatus.stop(order.order_id);
        }
      });
    },

    /**
     * Generate the inner HTML for the payment modal.
     * @param {Object} order
     * @returns {string}
     */
    _modalHTML: function (order) {
      var addr = this._esc(order.wallet_address || '');
      var coin = this._esc(order.crypto_currency || '');
      var amount = this._esc(order.crypto_amount || '');
      var fiat = this._esc(
        (order.fiat_amount != null ? order.fiat_amount.toFixed(2) : '0.00') +
        ' ' + (order.fiat_currency || 'USD').toUpperCase()
      );

      return (
        '<div class="crypto-pay-modal">' +
          '<div class="crypto-pay-header">' +
            '<h2>&#8383; Pay with ' + coin + '</h2>' +
            '<button id="cp-close-btn" class="crypto-pay-close-btn" aria-label="Close">&times;</button>' +
          '</div>' +
          '<div class="crypto-pay-body">' +
            '<div class="crypto-pay-amount-box">' +
              '<div class="crypto-pay-amount-fiat">' + fiat + '</div>' +
              '<div class="crypto-pay-amount-crypto">' + amount +
                '<span class="crypto-pay-amount-ticker">' + coin + '</span>' +
              '</div>' +
            '</div>' +
            '<div class="crypto-pay-qr-section">' +
              '<img id="cp-qr-img" src="" alt="Payment QR code" width="200" height="200">' +
            '</div>' +
            '<div class="crypto-pay-address-row">' +
              '<span class="crypto-pay-address-text" id="cp-address">' + addr + '</span>' +
              '<button id="cp-copy-btn" class="crypto-pay-copy-btn" aria-label="Copy address">' +
                '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                  '<rect x="9" y="9" width="13" height="13" rx="2"/>' +
                  '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>' +
                '</svg>' +
              '</button>' +
            '</div>' +
            '<div class="crypto-pay-copy-toast" id="cp-copy-toast" aria-live="polite"></div>' +
            '<div class="crypto-pay-timer" id="cp-timer" aria-live="polite"></div>' +
            '<div class="crypto-pay-status crypto-pay-status-pending" id="cp-status">Waiting for payment…</div>' +
            '<form id="cp-tx-form" class="crypto-pay-tx-section">' +
              '<label for="cp-tx-input">Transaction hash (optional — speeds up confirmation)</label>' +
              '<input id="cp-tx-input" class="crypto-pay-tx-input" type="text" ' +
                'placeholder="Paste your transaction hash here" autocomplete="off">' +
              '<button type="submit" class="crypto-pay-btn" style="margin-top:10px">Submit TX hash</button>' +
            '</form>' +
          '</div>' +
          '<div class="crypto-pay-footer">Powered by crypto-pay &bull; Self-hosted &bull; No tracking</div>' +
        '</div>'
      );
    },

    /**
     * Update the status badge in the modal.
     * @param {string} status
     */
    _updateStatusBadge: function (status) {
      var el = document.getElementById('cp-status');
      if (!el) return;
      var map = {
        pending: ['crypto-pay-status-pending', 'Waiting for payment…'],
        awaiting_confirmation: ['crypto-pay-status-awaiting', 'Payment detected — awaiting confirmation…'],
        completed: ['crypto-pay-status-completed', '&#10003; Payment confirmed!'],
        expired: ['crypto-pay-status-expired', 'Payment window expired'],
        cancelled: ['crypto-pay-status-cancelled', 'Order cancelled'],
      };
      var info = map[status] || ['', status];
      el.className = 'crypto-pay-status ' + info[0];
      el.innerHTML = info[1];
    },

    /**
     * HTML-escape a value for safe insertion.
     * @param {*} v
     * @returns {string}
     */
    _esc: function (v) {
      return String(v)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    },

    /**
     * Copy text to clipboard and show a toast message.
     * @param {string} text
     * @param {HTMLElement|null} toastEl
     */
    _copyToClipboard: function (text, toastEl) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          if (toastEl) { toastEl.textContent = 'Copied!'; setTimeout(function () { toastEl.textContent = ''; }, 2000); }
        });
      } else {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;opacity:0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); } catch (e) { /* ignore */ }
        document.body.removeChild(ta);
        if (toastEl) { toastEl.textContent = 'Copied!'; setTimeout(function () { toastEl.textContent = ''; }, 2000); }
      }
    },
  };

  /* -----------------------------------------------------------------------
   * PriceTicker — live price updates via polling
   * -------------------------------------------------------------------- */
  var PriceTicker = {
    _timers: {},

    /**
     * Start polling prices and updating DOM elements.
     * @param {string} fiat
     * @param {number} interval  milliseconds
     */
    start: function (fiat, interval) {
      var self = this;
      fiat = fiat || 'usd';
      interval = interval || 30000;

      function update() {
        CryptoPay.getAllPrices(fiat).then(function (data) {
          if (data && data.prices) {
            Object.keys(data.prices).forEach(function (coinId) {
              var price = data.prices[coinId];
              var els = document.querySelectorAll('[data-cp-price="' + coinId + '"]');
              els.forEach(function (el) {
                el.textContent = price.toLocaleString(undefined, { maximumFractionDigits: 8 });
              });
            });
          }
        }).catch(function (e) { /* silent */ });
      }

      update();
      self._timers[fiat] = setInterval(update, interval);
    },

    /**
     * Stop price polling for a fiat currency.
     * @param {string} fiat
     */
    stop: function (fiat) {
      clearInterval(this._timers[fiat]);
      delete this._timers[fiat];
    },
  };

  /* -----------------------------------------------------------------------
   * CountdownTimer — payment window countdown
   * -------------------------------------------------------------------- */
  var CountdownTimer = {
    _timers: {},

    /**
     * Start a countdown in the element with the given ID.
     * @param {string} elementId
     * @param {string|Date} expiresAt  ISO timestamp or Date
     * @param {Function} [onExpired]   called when timer reaches zero
     */
    start: function (elementId, expiresAt, onExpired) {
      var self = this;
      var el = document.getElementById(elementId);
      if (!el) return;

      var expiry = (expiresAt instanceof Date) ? expiresAt : new Date(expiresAt);

      function tick() {
        var remaining = Math.floor((expiry - Date.now()) / 1000);
        if (remaining <= 0) {
          el.className = 'crypto-pay-timer crypto-pay-timer-expired';
          el.textContent = 'Expired';
          clearInterval(self._timers[elementId]);
          delete self._timers[elementId];
          if (typeof onExpired === 'function') onExpired();
          return;
        }

        var mins = Math.floor(remaining / 60);
        var secs = remaining % 60;
        var formatted = mins + ':' + (secs < 10 ? '0' : '') + secs;

        el.className = 'crypto-pay-timer' + (remaining < 120 ? ' crypto-pay-timer-warning' : '');
        el.innerHTML = '&#9201; Expires in <span class="crypto-pay-timer-value">' + formatted + '</span>';
      }

      tick();
      this._timers[elementId] = setInterval(tick, 1000);
    },

    /**
     * Stop the countdown.
     * @param {string} elementId
     */
    stop: function (elementId) {
      clearInterval(this._timers[elementId]);
      delete this._timers[elementId];
    },
  };

  /* -----------------------------------------------------------------------
   * PaymentStatus — poll order status
   * -------------------------------------------------------------------- */
  var PaymentStatus = {
    _timers: {},

    /**
     * Begin polling order status.
     * @param {string} orderId
     * @param {number} interval  milliseconds
     * @param {Function} onChange  called with (status) on every change
     */
    poll: function (orderId, interval, onChange) {
      var self = this;
      var lastStatus = null;

      function check() {
        CryptoPay.getOrderStatus(orderId).then(function (data) {
          if (data && data.status && data.status !== lastStatus) {
            lastStatus = data.status;
            onChange(data.status);
          }
        }).catch(function (e) { /* silent */ });
      }

      check();
      this._timers[orderId] = setInterval(check, interval);
    },

    /**
     * Stop polling for an order.
     * @param {string} orderId
     */
    stop: function (orderId) {
      clearInterval(this._timers[orderId]);
      delete this._timers[orderId];
    },
  };

  /* -----------------------------------------------------------------------
   * Exports
   * -------------------------------------------------------------------- */
  global.CryptoPay = CryptoPay;
  global.PriceTicker = PriceTicker;
  global.CountdownTimer = CountdownTimer;
  global.PaymentStatus = PaymentStatus;

}(typeof globalThis !== 'undefined' ? globalThis : typeof window !== 'undefined' ? window : this));
