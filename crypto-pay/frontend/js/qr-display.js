/**
 * qr-display.js — QR code display and copy-address functionality
 * Part of the crypto-pay library. Can be loaded as a standalone module.
 *
 * Usage:
 *   QRDisplay.init('qr-container-id', orderId, address, '/api');
 */
(function (global) {
  'use strict';

  var QRDisplay = {};

  /**
   * Initialise a QR display container.
   *
   * @param {string} containerId  - ID of the container element
   * @param {string} orderId      - Payment order UUID
   * @param {string} address      - Wallet address
   * @param {string} [apiBase]    - API base URL (default '/api')
   */
  QRDisplay.init = function (containerId, orderId, address, apiBase) {
    apiBase = apiBase || '/api';
    var container = document.getElementById(containerId);
    if (!container) return;

    var qrUrl = apiBase + '/qr/' + encodeURIComponent(orderId);

    container.innerHTML =
      '<div class="crypto-pay-qr-section">' +
        '<img src="' + escHtml(qrUrl) + '" alt="Payment QR code" width="200" height="200">' +
      '</div>' +
      '<div class="crypto-pay-address-row">' +
        '<span class="crypto-pay-address-text" id="cp-qr-addr">' + escHtml(address) + '</span>' +
        '<button type="button" class="crypto-pay-copy-btn" id="cp-qr-copy-btn" aria-label="Copy address">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
               'stroke="currentColor" stroke-width="2" aria-hidden="true">' +
            '<rect x="9" y="9" width="13" height="13" rx="2"/>' +
            '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>' +
          '</svg>' +
        '</button>' +
      '</div>' +
      '<div class="crypto-pay-copy-toast" id="cp-qr-toast" aria-live="polite"></div>';

    document.getElementById('cp-qr-copy-btn').addEventListener('click', function () {
      QRDisplay.copyAddress(address, document.getElementById('cp-qr-toast'));
    });
  };

  /**
   * Copy address text to clipboard and display a toast message.
   *
   * @param {string} address   - Wallet address to copy
   * @param {HTMLElement} [toastEl] - Element to show "Copied!" confirmation
   */
  QRDisplay.copyAddress = function (address, toastEl) {
    function showToast() {
      if (toastEl) {
        toastEl.textContent = 'Copied!';
        setTimeout(function () { toastEl.textContent = ''; }, 2000);
      }
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(address).then(showToast).catch(function () {
        fallbackCopy(address);
        showToast();
      });
    } else {
      fallbackCopy(address);
      showToast();
    }
  };

  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    document.body.removeChild(ta);
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  global.QRDisplay = QRDisplay;

}(typeof globalThis !== 'undefined' ? globalThis : typeof window !== 'undefined' ? window : this));
