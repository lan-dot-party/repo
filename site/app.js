(function () {
    'use strict';
    var root = document.documentElement;
    root.classList.add('js');

    // ---- Tabs -----------------------------------------------------------
    var wrap = document.querySelector('.tabs');
    if (wrap) {
        var panels = Array.prototype.slice.call(wrap.querySelectorAll(':scope > .tabpanel'));
        var list = document.createElement('div');
        list.className = 'tablist';
        list.setAttribute('role', 'tablist');
        list.setAttribute('aria-label', 'Distribution family');
        var tabs = panels.map(function (panel, i) {
            var tab = document.createElement('button');
            tab.type = 'button';
            tab.setAttribute('role', 'tab');
            tab.id = 'tabbtn-' + panel.id;
            tab.setAttribute('aria-controls', panel.id);
            tab.textContent = panel.getAttribute('data-tab');
            panel.setAttribute('role', 'tabpanel');
            panel.setAttribute('aria-labelledby', tab.id);
            tab.addEventListener('click', function () { select(i, false); });
            list.appendChild(tab);
            return tab;
        });
        wrap.insertBefore(list, wrap.firstChild);

        var select = function (idx, focus) {
            tabs.forEach(function (t, i) {
                var on = i === idx;
                t.setAttribute('aria-selected', on ? 'true' : 'false');
                t.tabIndex = on ? 0 : -1;
                panels[i].hidden = !on;
            });
            if (focus) tabs[idx].focus();
            try { sessionStorage.setItem('lp-tab', String(idx)); } catch (e) { /* ignore */ }
        };
        list.addEventListener('keydown', function (ev) {
            var cur = tabs.indexOf(document.activeElement);
            if (cur < 0) return;
            var n = tabs.length, next = null;
            if (ev.key === 'ArrowRight') next = (cur + 1) % n;
            else if (ev.key === 'ArrowLeft') next = (cur - 1 + n) % n;
            else if (ev.key === 'Home') next = 0;
            else if (ev.key === 'End') next = n - 1;
            if (next !== null) { ev.preventDefault(); select(next, true); }
        });

        var start = 0;
        try {
            var saved = parseInt(sessionStorage.getItem('lp-tab'), 10);
            if (saved >= 0 && saved < tabs.length) start = saved;
        } catch (e) { /* ignore */ }
        select(start, false);
    }

    // ---- Suite selector: swap @SUITE@ in the deb snippets ------------------
    var sel = document.getElementById('suite');
    var tpls = document.querySelectorAll('pre[data-tpl]');
    function applySuite() {
        var suite = sel ? sel.value : 'bookworm';
        Array.prototype.forEach.call(tpls, function (pre) {
            pre.querySelector('code').textContent = pre.getAttribute('data-tpl').replace(/@SUITE@/g, suite);
        });
        try { localStorage.setItem('lp-suite', suite); } catch (e) { /* ignore */ }
    }
    if (sel) {
        try {
            var s = localStorage.getItem('lp-suite');
            if (s) {
                for (var i = 0; i < sel.options.length; i++) {
                    if (sel.options[i].value === s) sel.value = s;
                }
            }
        } catch (e) { /* ignore */ }
        sel.addEventListener('change', applySuite);
        applySuite();
    }

    // ---- Copy buttons -------------------------------------------------------
    Array.prototype.forEach.call(document.querySelectorAll('.code'), function (box) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'copy-btn';
        btn.textContent = 'Copy';
        btn.addEventListener('click', function () {
            var text = box.querySelector('code').textContent;
            var done = function (ok) {
                btn.textContent = ok ? 'Copied' : 'Press Ctrl+C';
                setTimeout(function () { btn.textContent = 'Copy'; }, 1800);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
            } else {
                var r = document.createRange();
                r.selectNodeContents(box.querySelector('code'));
                var sl = window.getSelection();
                sl.removeAllRanges();
                sl.addRange(r);
                var ok = false;
                try { ok = document.execCommand('copy'); } catch (e) { /* ignore */ }
                done(ok);
            }
        });
        box.appendChild(btn);
    });
})();
