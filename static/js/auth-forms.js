/* PaieGabon — interactions partagées : toggle mot de passe + jauge de robustesse.
   Chargé après le DOM (balise en fin de body). Compatible CSP (aucun eval). */
(function(){
  'use strict';
  var EYE = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18M10.6 10.6a3 3 0 0 0 4.2 4.2M9.9 5.1A9.6 9.6 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.1 3.9M6.1 6.1A17 17 0 0 0 2 12s3.6 7 10 7a9.5 9.5 0 0 0 3-.5"/></svg>';

  // Toggle affichage/masquage
  Array.prototype.forEach.call(document.querySelectorAll('.pw-toggle[data-toggle]'), function(btn){
    btn.addEventListener('click', function(){
      var input = document.getElementById(btn.getAttribute('data-toggle'));
      if (!input) return;
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.innerHTML = show ? EYE_OFF : EYE;
      btn.setAttribute('aria-label', show ? 'Masquer le mot de passe' : 'Afficher le mot de passe');
    });
  });

  // Jauge de robustesse — critères alignés sur validate_password() côté serveur
  function score(pw){
    var s = 0;
    if (pw.length >= 8) s++;
    if (/[A-Z]/.test(pw)) s++;
    if (/\d/.test(pw)) s++;
    if (/[^A-Za-z0-9]/.test(pw) || pw.length >= 12) s++;
    return Math.min(s, 4);
  }
  var LABELS = ['', 'Faible', 'Moyen', 'Bon', 'Excellent'];
  Array.prototype.forEach.call(document.querySelectorAll('[data-pw-strength]'), function(input){
    var meter = document.getElementById(input.getAttribute('data-pw-strength'));
    var label = meter ? meter.parentElement.querySelector('.pw-label') : null;
    input.addEventListener('input', function(){
      var sc = input.value ? score(input.value) : 0;
      if (meter) meter.setAttribute('data-score', sc);
      if (label) label.textContent = input.value ? 'Robustesse : ' + LABELS[sc] : '';
    });
  });
})();
