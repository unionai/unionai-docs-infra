/* ==========================================================================
   Docs search -- InstantSearch modal + Ask AI panel (DOC-1423)

   Replaces @docsearch/js. We own the UI now; Algolia supplies the data
   (InstantSearch for keyword search, Agent Studio for Ask AI).

   WHY WE LEFT DocSearch. Two of its Ask AI defects are client state-management
   bugs we cannot fix from configuration, both filed upstream:
     - algolia/docsearch#3010 -- clicking the "Ask AI" row sends the question and
       receives the whole answer, then discards both along with the chat instance
       that requested them. The reader sees an empty conversation.
     - algolia/docsearch#3011 -- pressing stop mid-search leaves a dangling
       tool call that is never pruned, so every later question in that
       conversation is rejected and the conversation is permanently dead.
   Owning the client removes both by construction: we send on the instance that
   renders, and we prune non-terminal tool parts before any resend.

   The markup deliberately uses the DocSearch class names. assets/css/
   search-modal.css is a fork of their stylesheet, so the cutover is invisible
   to readers; the layout is ours to change from here.

   Config arrives as window.__SEARCH_CONFIG from layouts/partials/search.html.
   ========================================================================== */
(function () {
  'use strict';

  var CFG = window.__SEARCH_CONFIG;
  if (!CFG || !CFG.appId || !CFG.apiKey || !CFG.indexName) return;

  var HAS_ASK_AI = !!(CFG.askAi && CFG.askAi.agentId && CFG.askAi.apiKey);

  // --------------------------------------------------------------------------
  // Icons (inline; the stylesheet expects an svg in these slots)
  // --------------------------------------------------------------------------
  var ICON = {
    magnifier:
      '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="8.5" cy="8.5" r="6"/><line x1="13" y1="13" x2="18" y2="18"/></svg>',
    hash: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><line x1="7" y1="3" x2="5" y2="17"/><line x1="15" y1="3" x2="13" y2="17"/><line x1="3" y1="7" x2="17" y2="7"/><line x1="3" y1="13" x2="17" y2="13"/></svg>',
    page: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 2.5h6l4 4V17a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 5 17V3a.5.5 0 0 1 .5-.5z"/><path d="M11 2.5V7h4"/></svg>',
    enter:
      '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5v4a2 2 0 0 1-2 2H5"/><path d="M8 8l-3 3 3 3"/></svg>',
    reset:
      '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><line x1="6" y1="6" x2="14" y2="14"/><line x1="14" y1="6" x2="6" y2="14"/></svg>',
    sparkle:
      '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 1.6l1.6 4.5 4.5 1.6-4.5 1.6L10 13.8 8.4 9.3 3.9 7.7l4.5-1.6L10 1.6zM15.4 12.2l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2z"/></svg>'
  };

  // --------------------------------------------------------------------------
  // DOM
  // --------------------------------------------------------------------------
  var el = {};
  var built = false;

  function h(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function build() {
    if (built) return;
    built = true;

    el.container = h('div', 'DocSearch-Container');

    // A dialog, not a button. DocSearch marks the overlay role="button" so a
    // click dismisses it; screen readers then announce the whole search
    // experience as a button. Keep click-to-dismiss (handled below) and
    // describe what this actually is.
    el.modal = h('div', 'DocSearch-Modal');
    el.modal.setAttribute('role', 'dialog');
    el.modal.setAttribute('aria-modal', 'true');
    el.modal.setAttribute('aria-label', 'Search the documentation');

    // -- search bar
    //
    // A DIV, not <header>. The docs site styles bare `header` and `footer`
    // elements globally (header.css sets height/background/border/padding,
    // footer.css and base.css set display and min-height), and those rules
    // matched our modal's chrome -- the search bar inherited the SITE header's
    // height and padding, so the modal rendered differently on the real site
    // than in a harness that had not loaded those stylesheets. Using neutral
    // elements removes the collision by construction rather than by fighting
    // specificity, which would break again the next time header.css changes.
    var bar = h('div', 'DocSearch-SearchBar');
    el.form = h('form', 'DocSearch-Form');
    el.form.appendChild(h('label', 'DocSearch-MagnifierLabel', ICON.magnifier));
    el.form.appendChild(h('div', 'DocSearch-LoadingIndicator'));

    el.input = document.createElement('input');
    el.input.className = 'DocSearch-Input';
    el.input.setAttribute('aria-autocomplete', 'both');
    el.input.setAttribute('autocomplete', 'off');
    el.input.setAttribute('autocorrect', 'off');
    el.input.setAttribute('autocapitalize', 'off');
    el.input.setAttribute('spellcheck', 'false');
    el.input.setAttribute('placeholder', CFG.placeholder || 'Search docs');
    el.form.appendChild(el.input);

    bar.appendChild(el.form);

    el.stopBtn = h('button', 'DocSearch-StopStreaming', 'Stop');
    el.stopBtn.type = 'button';
    el.stopBtn.hidden = true;
    el.stopBtn.setAttribute('aria-label', 'Stop generating the answer');
    bar.appendChild(el.stopBtn);

    // Clear (text) empties the query; Close (x) dismisses the modal. That is
    // v5's arrangement -- `.DocSearch-Reset` / `.DocSearch-Cancel` are v3 names
    // with NO rules in the v5 stylesheet, so they rendered as unstyled default
    // buttons, and putting "clear" behind the x made it look inert whenever the
    // input was already empty.
    el.clearBtn = h('button', 'DocSearch-Clear', 'Clear');
    el.clearBtn.type = 'button';
    el.clearBtn.hidden = true;
    bar.appendChild(el.clearBtn);

    el.closeBtn = h('button', 'DocSearch-Close', ICON.reset);
    el.closeBtn.type = 'button';
    el.closeBtn.setAttribute('aria-label', 'Close search');
    bar.appendChild(el.closeBtn);

    el.modal.appendChild(bar);

    // -- scope bar
    //
    // Every query in this modal -- keyword AND Ask AI -- is filtered to one
    // version/variant slice, and nothing in the UI said which. Two things made
    // that worth stating rather than implying:
    //
    //   * the tint on the controls says "Union" or "Flyte" only to someone who
    //     already knows the two brand colours apart;
    //   * DOC-1408 means a reader on a PINNED tree is answered from that line's
    //     STABLE instead, because the pins are not indexed. That substitution
    //     was completely invisible, and it is exactly the case where a reader
    //     needs to know what they are actually reading.
    //
    // Sits directly under the search bar's hairline, above the results, so it
    // reads as a subtitle to the query rather than as a banner over the modal.
    var scope = scopeParts();
    if (scope) {
      el.scope = h('div', 'DocSearch-Scope');
      el.scope.innerHTML =
        '<span class="DocSearch-Scope-Dot" aria-hidden="true"></span>' +
        '<span class="DocSearch-Scope-Label">Searching</span>' +
        '<span class="DocSearch-Scope-Product">' + escapeHtml(scope.product) + '</span>' +
        '<span class="DocSearch-Scope-Version">' +
          '<span class="DocSearch-Scope-Line">' + escapeHtml(scope.line) + '</span>' +
          (scope.badge ? '<span class="DocSearch-Scope-Badge">' + escapeHtml(scope.badge) + '</span>' : '') +
          (scope.release ? '<span class="DocSearch-Scope-Release">' + escapeHtml(scope.release) + '</span>' : '') +
        '</span>';
      el.modal.appendChild(el.scope);
    }

    // -- results
    el.dropdown = h('div', 'DocSearch-Dropdown');
    // The Ask AI row lives in its own host ABOVE the hits. renderHits() clears
    // its container on every result set, so a row rendered into that container
    // is wiped the moment the (asynchronous) hits response lands.
    el.askAiHost = h('div', 'DocSearch-AskAi-Host');
    el.dropdown.appendChild(el.askAiHost);
    el.dropdownContainer = h('div', 'DocSearch-Dropdown-Container');
    el.dropdown.appendChild(el.dropdownContainer);
    el.modal.appendChild(el.dropdown);

    // -- Ask AI panel
    el.askAiScreen = h('div', 'DocSearch-AskAiScreen');
    el.askAiScroll = h('div', 'DocSearch-AskAiScreen-Container');
    var askAiInner = h('div', 'DocSearch-AskAiScreen-Body');
    askAiInner.appendChild(
      h('div', 'DocSearch-AskAiScreen-Disclaimer', 'Answers are generated with AI which can make mistakes.')
    );
    el.askAiBody = h('ul', 'DocSearch-AskAiScreen-ExchangesList');
    askAiInner.appendChild(el.askAiBody);

    // Announce STATE, never the answer text.
    //
    // A live region on the exchanges list is actively harmful here: the answer
    // is re-rendered from scratch on every streamed token, so a polite region
    // would re-announce the whole growing answer dozens of times, each pass
    // longer than the last. Screen-reader users would be unable to get through
    // it. Instead the response carries aria-busy while it streams, and this
    // region says only what changed.
    el.status = h('div', 'DocSearch-VisuallyHidden');
    el.status.setAttribute('role', 'status');
    el.status.setAttribute('aria-live', 'polite');
    askAiInner.appendChild(el.status);
    el.askAiScroll.appendChild(askAiInner);
    el.askAiScreen.appendChild(el.askAiScroll);
    el.askAiScreen.hidden = true;
    el.modal.appendChild(el.askAiScreen);

    // -- footer
    el.footer = h('div', 'DocSearch-Footer');   // div, not <footer> -- see the search bar above
    el.modal.appendChild(el.footer);
    paintFooter();

    el.container.appendChild(el.modal);
    document.body.appendChild(el.container);

    // -- events
    el.form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (mode === 'askai') {
        // Clear ONLY if the send was accepted. Clearing first meant a question
        // typed while an answer was still streaming vanished with no feedback
        // at all -- the reader's words silently discarded, which is the very
        // failure we left DocSearch over.
        if (askAiSend(el.input.value)) el.input.value = '';
        return;
      }
      var sel = currentSelection();
      if (sel) activate(sel);
    });
    el.stopBtn.addEventListener('click', function () {
      if (askai.abort) askai.abort();
    });
    el.clearBtn.addEventListener('click', function (e) {
      e.preventDefault();
      if (mode === 'askai') setMode('search');
      setQuery('');
      el.input.focus();
    });
    el.closeBtn.addEventListener('click', close);
    el.container.addEventListener('mousedown', function (e) {
      if (e.target === el.container) close();
    });
    el.input.addEventListener('input', function () {
      onQuery(el.input.value);
    });
  }

  // --------------------------------------------------------------------------
  // Open / close
  // --------------------------------------------------------------------------
  var isOpen = false;
  var mode = 'search'; // 'search' | 'askai'
  var scrollY = 0;
  var lastFocused = null;

  function open(initialQuery) {
    build();
    if (isOpen) return;
    isOpen = true;
    lastFocused = document.activeElement;
    scrollY = window.scrollY;
    document.body.classList.add('DocSearch--active');
    el.container.style.display = '';
    setMode('search');
    if (initialQuery != null) setQuery(initialQuery);
    el.input.focus();
    startSearch();
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    // Closing ends the conversation. Without this the next question is appended
    // to the previous exchange, so reopening the modal and asking something new
    // shows the OLD question and answer first -- which reads as the widget
    // ignoring you. Conversation history is deliberately not a v1 feature, so
    // "one modal session, one conversation" is the coherent rule.
    if (askai.streaming && askai.abort) askai.abort();
    resetAskAi();
    document.body.classList.remove('DocSearch--active');
    el.container.style.display = 'none';
    window.scrollTo(0, scrollY);
    // Return focus where the reader left it. Without this, closing drops focus
    // to <body> and a keyboard user restarts from the top of the page.
    if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
    lastFocused = null;
  }

  function resetAskAi() {
    askai.conv = null;
    askai.suppressedErrors = 0;
    if (el.askAiBody) el.askAiBody.innerHTML = '';
  }

  function setMode(next) {
    mode = next;
    var searching = next === 'search';
    el.dropdown.hidden = !searching;
    el.askAiScreen.hidden = searching;
    el.input.placeholder = searching
      ? (CFG.placeholder || 'Search docs')
      // "another" only once there IS a previous one. Opening Ask AI from the
      // header starts an empty conversation, where "Ask another question"
      // implies a first one the reader never asked.
      : (el.askAiBody && el.askAiBody.children.length
          ? 'Ask another question...'
          : 'Ask a question about the docs...');
    if (searching) el.stopBtn.hidden = true;
    paintFooter();
  }

  // The footer advertises the keys, so it has to follow the mode. In Ask AI,
  // Enter submits a question rather than opening a result, and Esc returns to
  // search rather than closing -- a static footer describes the wrong widget.
  function paintFooter() {
    // Each key (or key pair) and the label describing it are ONE group. A flat
    // row put "Navigate" the same distance from the arrows it describes as from
    // the Enter key it does not, so the eye had to guess which label belonged
    // to which control. Grouping tightens that within a pair and widens it
    // between pairs, which is what makes the association readable.
    var K = function (k) { return '<span class="DocSearch-Commands-Key">' + k + '</span>'; };
    var cmd = function (keys, label) {
      return '<span class="DocSearch-Command">' + keys +
             '<span class="DocSearch-Label">' + label + '</span></span>';
    };
    el.footer.innerHTML =
      '<div class="DocSearch-Commands">' +
      (mode === 'askai'
        ? cmd(K('&#8629;'), 'Submit question') + cmd(K('esc'), 'Back to search')
        : cmd('<span class="DocSearch-Command-Keys">' + K('&#8595;') + K('&#8593;') + '</span>', 'Navigate') +
          cmd(K('&#8629;'), 'Select') +
          cmd(K('esc'), 'Close')) +
      '</div>';
  }


  // --------------------------------------------------------------------------
  // InstantSearch
  // --------------------------------------------------------------------------
  var search = null;
  var setISQuery = function () {};
  var pendingQuery = '';
  var lastHits = [];

  function startSearch() {
    if (search) return;

    var lite = window['algoliasearch/lite'];
    var client = (lite.liteClient || lite.default || lite)(CFG.appId, CFG.apiKey);

    search = window.instantsearch({
      indexName: CFG.indexName,
      searchClient: client,
      // The modal drives the query; InstantSearch should not read the URL or
      // fire a request for an empty query.
      searchFunction: function (helper) {
        if (helper.state.query) helper.search();
      }
    });

    search.addWidgets([
      window.instantsearch.widgets.configure({
        // Scope. facetFilters is an AND of an array here; Ask AI needs the same
        // scope expressed as a `filters` STRING (see search.html) -- Agent
        // Studio ignores facetFilters in that position, so they cannot share
        // one expression.
        facetFilters: CFG.facetFilters || [],
        hitsPerPage: CFG.hitsPerPage || 20,
        // <mark> so the forked stylesheet's `.DocSearch-Hits mark` applies. The
        // index's own default tags are the legacy docsearch-suggestion spans.
        highlightPreTag: '<mark>',
        highlightPostTag: '</mark>',
        attributesToSnippet: ['content:25'],
        snippetEllipsisText: '...'
      }),

      customWidget(window.instantsearch.connectors.connectSearchBox, function (opts) {
        setISQuery = opts.refine;
        // InstantSearch renders for the first time ASYNCHRONOUSLY, so a query
        // typed (or passed to open()) before that first pass lands while
        // `refine` is still the no-op below. Reconcile here instead of assuming
        // the widget is wired the moment start() returns -- otherwise the very
        // first query silently never reaches Algolia.
        if (pendingQuery !== opts.query) opts.refine(pendingQuery);
      }),

      customWidget(window.instantsearch.connectors.connectHits, function (opts) {
        lastHits = opts.hits || [];
        renderHits(lastHits);
      })
    ]);

    search.start();
  }

  // connectX(render)(widgetParams) with a no-op unmount.
  //
  // Render on EVERY pass, including the first. The first render is where a
  // connector hands over its `refine` function, so skipping it leaves the
  // search box permanently unwired -- the query never reaches InstantSearch and
  // no request is ever sent.
  function customWidget(connector, render) {
    return connector(function (opts) {
      render(opts);
    }, function () {})({});
  }

  function onQuery(q) {
    pendingQuery = q;
    if (el.clearBtn) el.clearBtn.hidden = !q;
    if (!search) startSearch();
    setISQuery(q);
    if (!q) renderHits([]);
    renderAskAiRow(q);
  }

  function setQuery(q) {
    el.input.value = q;
    onQuery(q);
  }

  // --------------------------------------------------------------------------
  // Hit rendering -- grouped by hierarchy.lvl0, DocSearch-equivalent markup
  // --------------------------------------------------------------------------
  // Headings in the corpus carry raw markdown: a section called `ReusePolicy`
  // parameters arrives with literal backticks and rendered as
  // "`ReusePolicy` parameters". Render the span as code, matching how the same
  // heading looks on the page.
  //
  // The inner text MUST be allowed to contain markup. The search highlighter
  // wraps the match first, so by the time this runs the backticked span is
  // usually `<mark>ReusePolicy</mark>` -- excluding angle brackets, as a first
  // attempt did, meant the pattern missed precisely the titles that matter.
  // Length-capped so an unpaired backtick cannot swallow a whole title.
  function codeSpans(html) {
    return html.replace(/`([^`]{1,80})`/g, '<code>$1</code>');
  }

  function hitTitle(hit) {
    var hr = hit._highlightResult || {};
    if (hit.type === 'content') {
      var sn = (hit._snippetResult && hit._snippetResult.content) || null;
      return (sn && sn.value) || escapeHtml(hit.content || '');
    }
    var lvl = hit.type; // lvl1..lvl6
    var hh = (hr.hierarchy && hr.hierarchy[lvl]) || null;
    if (hh && hh.value) return hh.value;
    return escapeHtml((hit.hierarchy && hit.hierarchy[lvl]) || '');
  }

  // Where a result lives, from its URL -- the SAME derivation the Ask AI
  // sources use, so both halves of the modal answer "where is this?" the same
  // way.
  //
  // Was built from hierarchy levels, which produced a breadcrumb on only 12 of
  // 20 rows and, on one, the raw anchor slug "container-tasks". Most results
  // carried no location at all and one carried something meaningless. The URL
  // is always present and always structural.
  function hitPath(hit) {
    return escapeHtml(breadcrumbFor(hit.url || ''));
  }

  function renderHits(hits) {
    el.dropdownContainer.innerHTML = '';

    if (!hits.length) {
      if (el.input.value) {
        el.dropdownContainer.appendChild(
          h('div', 'DocSearch-NoResults', '<p class="DocSearch-Help">No results for "' + escapeHtml(el.input.value) + '"</p>')
        );
      }
      indexSelectables();
      return;
    }

    // group in first-seen order so the ranking decides section order
    var groups = [];
    var byName = {};
    hits.forEach(function (hit) {
      var name = (hit.hierarchy && hit.hierarchy.lvl0) || 'Documentation';
      if (!byName[name]) {
        byName[name] = { name: name, hits: [] };
        groups.push(byName[name]);
      }
      byName[name].hits.push(hit);
    });

    groups.forEach(function (g) {
      var section = h('section', 'DocSearch-Hits');
      section.appendChild(h('div', 'DocSearch-Hit-source', escapeHtml(g.name)));
      var ul = document.createElement('ul');
      g.hits.forEach(function (hit) {
        var li = h('li', 'DocSearch-Hit');
        var a = document.createElement('a');
        a.href = hit.url;
        a.innerHTML =
          '<div class="DocSearch-Hit-Container">' +
          '<div class="DocSearch-Hit-icon">' + (hit.type === 'lvl1' ? ICON.page : ICON.hash) + '</div>' +
          '<div class="DocSearch-Hit-content-wrapper">' +
          '<span class="DocSearch-Hit-title">' + codeSpans(hitTitle(hit)) + '</span>' +
          '<span class="DocSearch-Hit-path">' + hitPath(hit) + '</span>' +
          '</div>' +
          '<div class="DocSearch-Hit-action">' + ICON.enter + '</div>' +
          '</div>';
        li.appendChild(a);
        ul.appendChild(li);
      });
      section.appendChild(ul);
      el.dropdownContainer.appendChild(section);
    });

    indexSelectables();
  }

  // --------------------------------------------------------------------------
  // Ask AI entry row (panel wired separately)
  // --------------------------------------------------------------------------
  function renderAskAiRow(q) {
    if (!HAS_ASK_AI) return;
    el.askAiHost.innerHTML = '';
    if (!q) return;

    var section = h('section', 'DocSearch-Hits DocSearch-AskAi-Section');
    section.appendChild(h('div', 'DocSearch-Hit-source', 'Ask AI Assistant'));
    var ul = document.createElement('ul');
    var li = h('li', 'DocSearch-Hit DocSearch-Hit--AskAI');
    var a = document.createElement('a');
    a.href = '#';
    a.dataset.askai = q;
    a.innerHTML =
      '<div class="DocSearch-Hit-Container">' +
      '<div class="DocSearch-Hit-icon">' + ICON.sparkle + '</div>' +
      '<div class="DocSearch-Hit-content-wrapper">' +
      '<span class="DocSearch-Hit-title">' + escapeHtml(q) + '</span>' +
      '</div>' +
      '</div>';
    li.appendChild(a);
    ul.appendChild(li);
    section.appendChild(ul);
    el.askAiHost.appendChild(section);
    indexSelectables();
  }

  // --------------------------------------------------------------------------
  // Keyboard navigation
  // --------------------------------------------------------------------------
  var selectables = [];
  var selectedIndex = 0;

  function indexSelectables() {
    // across BOTH hosts, so the Ask AI row is reachable by keyboard
    selectables = Array.prototype.slice.call(el.dropdown.querySelectorAll('.DocSearch-Hit a'));
    if (selectedIndex >= selectables.length) selectedIndex = 0;
    paintSelection();
  }

  function paintSelection() {
    selectables.forEach(function (a, i) {
      a.parentNode.setAttribute('aria-selected', i === selectedIndex ? 'true' : 'false');
      a.parentNode.classList.toggle('DocSearch-Hit--Selected', i === selectedIndex);
    });
    var cur = selectables[selectedIndex];
    if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
  }

  // Visible, focusable elements inside the dialog, in DOM order.
  function focusable() {
    var sel = 'a[href], button:not([disabled]), input, [tabindex]:not([tabindex="-1"])';
    return Array.prototype.slice.call(el.modal.querySelectorAll(sel))
      .filter(function (n) { return !n.hidden && n.offsetParent !== null; });
  }

  function currentSelection() {
    return selectables[selectedIndex] || null;
  }

  function activate(a) {
    if (a.dataset.askai != null) {
      openAskAi(a.dataset.askai);
      return;
    }
    window.location.assign(a.href);
  }

  function onKeydown(e) {
    // global open shortcut
    var isK = e.key === 'k' || e.key === 'K';
    if (isK && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      isOpen ? close() : open('');
      return;
    }
    // Shift-Cmd-L opens straight into Ask AI. Inherited from the RunLLM button
    // that used to sit beside the search box; the button is gone -- Ask AI is
    // reached from inside the modal now -- but the shortcut costs nothing and
    // anyone who had it in their fingers keeps it. Uses e.code so it is layout
    // independent, and because e.key is 'L' once Shift is held.
    if (e.code === 'KeyL' && e.shiftKey && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      openAsk();
      return;
    }
    if (!isOpen) return;

    if (e.key === 'Escape') {
      e.preventDefault();
      if (mode === 'askai') { setMode('search'); el.input.focus(); }
      else close();
      return;
    }

    // Keep Tab inside the dialog. aria-modal tells assistive tech the rest of
    // the page is inert, but it does not move focus -- without a trap, Tab
    // walks out into the page behind the overlay, where the reader cannot see
    // what is focused.
    if (e.key === 'Tab') {
      var f = focusable();
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      var here = document.activeElement;
      if (e.shiftKey && (here === first || !el.modal.contains(here))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && here === last) {
        e.preventDefault();
        first.focus();
      }
      return;
    }
    if (mode !== 'search') return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (selectables.length) { selectedIndex = (selectedIndex + 1) % selectables.length; paintSelection(); }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (selectables.length) { selectedIndex = (selectedIndex - 1 + selectables.length) % selectables.length; paintSelection(); }
    }
  }

  // click-through on hits
  document.addEventListener('click', function (e) {
    if (!isOpen) return;
    var a = e.target.closest && e.target.closest('.DocSearch-Hit a');
    if (a && el.container.contains(a)) {
      if (a.dataset.askai != null) { e.preventDefault(); activate(a); }
    }
  });

  // ==========================================================================
  // Ask AI panel
  //
  // Plain DOM over one conversation object. That is the whole fix for
  // algolia/docsearch#3010: there is no second chat instance to lose the send
  // to, because entering the panel and sending the question act on the same
  // state. The stop path is the fix for #3011 -- see pruneForSend().
  // ==========================================================================
  var askai = {
    conv: null,      // { id, messages: [] }
    streaming: false,
    stopped: false,
    controller: null,
    suppressedErrors: 0
  };

  // -- transport --------------------------------------------------------------
  // We talk to the completions endpoint directly rather than through
  // @algolia/agent-studio. The official client CANNOT stream in a browser
  // without a bundler: its UMD build ships the XHR requester, which throws
  // "This requester does not support streaming", and the fetch requester is
  // published as ESM only. Since the no-bundler script-tag model is what makes
  // this approach viable at all, we own the ~30 lines instead -- which also
  // gives us a real AbortController, and a proper abort is the whole mechanism
  // behind stopping cleanly (see pruneForSend).
  //
  // The contract below was verified exhaustively against the live endpoint:
  // errors arrive INSIDE a 200 response as `{"type":"error"}` parts, so a
  // non-ok status is the exception, not the error path.
  async function* askAiStream(body, signal) {
    var url =
      'https://' + CFG.appId + '.algolia.net/agent-studio/1/agents/' +
      encodeURIComponent(CFG.askAi.agentId) +
      '/completions?stream=true&compatibilityMode=ai-sdk-5';

    var res = await fetch(url, {
      method: 'POST',
      signal: signal,
      headers: {
        'content-type': 'application/json',
        'x-algolia-application-id': CFG.appId,
        'x-algolia-api-key': CFG.askAi.apiKey
      },
      body: JSON.stringify(body)
    });

    if (!res.ok || !res.body) {
      var t = '';
      try { t = await res.text(); } catch (e) {}
      throw new Error(extractErrorMessage({ errorText: t }) || ('HTTP ' + res.status));
    }

    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var buf = '';
    try {
      while (true) {
        var step = await reader.read();
        if (step.done) break;
        buf += decoder.decode(step.value, { stream: true });
        var lines = buf.split('\n');
        buf = lines.pop();
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (line.indexOf('data:') !== 0) continue;
          var payload = line.slice(5).trim();
          if (payload === '[DONE]') return;
          try { yield JSON.parse(payload); } catch (e) { /* skip partial */ }
        }
      }
    } finally {
      try { reader.cancel(); } catch (e) {}
    }
  }

  function newConversation() {
    askai.conv = { id: randomId(), messages: [] };
    askai.suppressedErrors = 0;
    el.askAiBody.innerHTML = '';
  }

  function randomId() {
    var s = '';
    var a = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    var r = new Uint32Array(16);
    (window.crypto || window.msCrypto).getRandomValues(r);
    for (var i = 0; i < 16; i++) s += a[r[i] % a.length];
    return s;
  }

  // -- markdown ---------------------------------------------------------------
  // The answer is model-generated text. marked emits raw HTML, so scrub the
  // result before it reaches the DOM rather than trusting the model not to emit
  // markup. Allowlist, not blocklist.
  var ALLOWED_TAGS = {
    P: 1, BR: 1, STRONG: 1, EM: 1, CODE: 1, PRE: 1, UL: 1, OL: 1, LI: 1,
    BLOCKQUOTE: 1, H1: 1, H2: 1, H3: 1, H4: 1, H5: 1, H6: 1, A: 1, HR: 1,
    TABLE: 1, THEAD: 1, TBODY: 1, TR: 1, TH: 1, TD: 1, DEL: 1, SPAN: 1, DIV: 1
  };

  // Highlight AFTER the stream ends, never per token: half-written code
  // tokenises wrongly (an unterminated string swallows the rest of the block),
  // and re-parsing on every delta is wasted work the reader would see flicker.
  function highlightCode(ex) {
    if (!window.Prism) return;
    var blocks = ex.querySelectorAll('pre code[class*="language-"]');
    for (var i = 0; i < blocks.length; i++) {
      try { window.Prism.highlightElement(blocks[i]); } catch (e) { /* leave it plain */ }
    }
  }

  function renderMarkdown(text) {
    var html;
    try {
      html = window.marked ? window.marked.parse(text, { breaks: true }) : escapeHtml(text);
    } catch (e) {
      html = escapeHtml(text);
    }
    var tpl = document.createElement('div');
    tpl.innerHTML = html;
    scrub(tpl);
    return tpl.innerHTML;
  }

  function scrub(root) {
    var nodes = root.querySelectorAll('*');
    for (var i = nodes.length - 1; i >= 0; i--) {
      var n = nodes[i];
      if (!ALLOWED_TAGS[n.tagName]) {
        n.parentNode.replaceChild(document.createTextNode(n.textContent || ''), n);
        continue;
      }
      for (var j = n.attributes.length - 1; j >= 0; j--) {
        var attr = n.attributes[j].name;
        var val = n.attributes[j].value;
        var ok =
          (n.tagName === 'A' && attr === 'href' && /^(https?:|\/|#)/i.test(val)) ||
          attr === 'class';
        if (!ok) n.removeAttribute(attr);
      }
      if (n.tagName === 'A') {
        n.setAttribute('rel', 'noopener noreferrer');
        n.setAttribute('target', '_blank');
      }
    }
  }

  // -- send -------------------------------------------------------------------
  // Drop anything the backend will reject before it is ever sent. A tool call
  // that never reached a terminal state -- which is exactly what pressing stop
  // mid-search leaves behind -- serialises to an assistant `tool_use` with no
  // matching `tool_result`, and the provider rejects EVERY later request in the
  // conversation. That is algolia/docsearch#3011: their sanitiser strips only
  // data-* parts, so one press of stop kills the conversation permanently.
  function pruneForSend(messages) {
    var out = [];
    messages.forEach(function (m) {
      if (m.role !== 'assistant') { out.push(m); return; }
      var parts = (m.parts || []).filter(function (p) {
        if (p.type.indexOf('tool-') !== 0) return true;
        return p.state === 'output-available' || p.state === 'output-error';
      });
      if (parts.length) out.push({ id: m.id, role: m.role, parts: parts });
      // an assistant message left with no parts is dropped entirely
    });
    return out;
  }

  function askAiSend(question) {
    var q = (question || '').trim();
    if (!q) return false;
    if (askai.streaming) {
      // Keep the text in the box and say why nothing happened.
      announce('Still answering the previous question. Your question was not sent yet.');
      flashBusy();
      return false;
    }
    if (!askai.conv) newConversation();

    // What we SEND carries the scope; what we DISPLAY is the reader's question
    // verbatim. Only values we resolved ourselves go in here -- never raw URL
    // or query-string input, which would turn a crafted link into a
    // prompt-injection path into our own agent.
    askai.conv.messages.push({
      id: randomId(),
      role: 'user',
      parts: [{ type: 'text', text: scopePreamble() + q }]
    });

    var exchange = appendExchange(q);
    announce('Searching the documentation.');
    streamAnswer(exchange);
    return true;
  }

  // Visible counterpart to the announcement: briefly mark the bar as busy so a
  // sighted reader sees why their Enter did nothing.
  function flashBusy() {
    var bar = el.stopBtn && el.stopBtn.parentNode;
    if (!bar) return;
    bar.classList.add('DocSearch-SearchBar--busy');
    setTimeout(function () { bar.classList.remove('DocSearch-SearchBar--busy'); }, 900);
  }

  function streamAnswer(ex) {
    askai.streaming = true;
    askai.stopped = false;
    setStreamingUI(true);

    var assistant = { id: randomId(), role: 'assistant', parts: [] };
    askai.conv.messages.push(assistant);

    var text = '';
    var gotText = false;
    var sources = [];
    var toolQueries = [];
    var guardrailed = false;

    askai.controller = new AbortController();
    askai.abort = function () {
      askai.stopped = true;
      if (askai.controller) askai.controller.abort();
    };

    (async function () {
      try {
        var stream = askAiStream(
          {
            id: askai.conv.id,
            messages: pruneForSend(askai.conv.messages),
            algolia: buildAskAiAlgoliaParams()
          },
          askai.controller.signal
        );

        for await (var p of stream) {
          if (askai.stopped) break;
          if (!p || !p.type) continue;

          if (p.type === 'text-delta' && p.delta) {
            text += p.delta;
            gotText = true;
            paintAnswer(ex, text);
          } else if (p.type === 'tool-input-start') {
            assistant.parts.push({
              type: 'tool-' + (p.toolName || 'search'),
              toolCallId: p.toolCallId,
              state: 'input-streaming'
            });
            paintTool(ex, toolQueries);
          } else if (p.type === 'tool-input-available') {
            markTool(assistant, p.toolCallId, 'input-available');
            if (p.input && p.input.query) { toolQueries.push(p.input.query); paintTool(ex, toolQueries); }
          } else if (p.type === 'tool-output-available') {
            markTool(assistant, p.toolCallId, 'output-available', p.output);
            collectSources(sources, p.output);
          } else if (p.type === 'data-guardrail-violation') {
            // REPLACE whatever text arrived, do not merely fill in for it.
            // A guardrail truncates the model mid-sentence, so the stream is
            // literally: text-delta("This") -> text-end -> finish-step ->
            // data-guardrail-violation. That single stray token is guarded
            // output, not an answer -- gating the fallback on "no text yet"
            // leaves the reader looking at one word, which is upstream #2941's
            // symptom reproduced in our own client.
            if (p.data && p.data.fallbackResponse) {
              text = p.data.fallbackResponse;
              gotText = true;
              guardrailed = true;
              paintAnswer(ex, text);
            }
          } else if (p.type === 'error') {
            handleStreamError(ex, p, gotText);
          }
        }

        if (text) assistant.parts.push({ type: 'text', text: text, state: 'done' });
        if (sources.length && !guardrailed) paintSources(ex, sources);
      } catch (e) {
        var aborted = askai.stopped || (e && e.name === 'AbortError');
        if (!aborted) handleStreamError(ex, { errorText: String((e && e.message) || e) }, gotText);
      } finally {
        // In `finally`, not after the loop: aborting REJECTS the in-flight
        // fetch, so the stopped path arrives via catch and would skip anything
        // placed at the end of the try -- leaving the exchange stuck on
        // "Searching..." with no indication the reader stopped it.
        if (askai.stopped) paintStopped(ex);
        highlightCode(ex);
        var resp = ex.querySelector('.DocSearch-AskAiScreen-Response');
        if (resp) resp.setAttribute('aria-busy', 'false');
        announce(askai.stopped ? 'Answer stopped.'
                               : (gotText ? 'Answer ready.' : 'No answer returned.'));
        askai.controller = null;
        askai.streaming = false;
        setStreamingUI(false);
        // Pruning happens on send, but do it here too so the conversation in
        // memory never carries a half-finished tool call between turns.
        askai.conv.messages = pruneForSend(askai.conv.messages);
      }
    })();
  }

  // Prepended to every user turn, not just the first: the clarifying question
  // is triggered by SHORT queries, and follow-ups ("what about caching?") are
  // the shortest of all. Costs ~25 tokens a turn.
  function scopePreamble() {
    var a = CFG.askAi || {};
    if (!a.version && !a.variant) return '';
    var product = a.variant === 'flyte' ? 'Flyte (open source)' : 'Union.ai';
    var scope = a.version ? product + ', documentation version ' + a.version : product;
    return '[Context: the documentation search is already restricted to ' + scope +
      '. Do not ask which product or version the reader means; answer from the retrieved documents. ' +
      'Do not end the answer with a "Sources:" list -- the interface renders the sources itself, ' +
      'so one would appear twice. Inline links within a sentence are fine.]\n\n';
  }

  function buildAskAiAlgoliaParams() {
    var o = {};
    if (CFG.askAi.filters) {
      o.searchParameters = {};
      o.searchParameters[CFG.askAi.indexName] = { filters: CFG.askAi.filters };
    }
    // deliberately NO `indices` -- the agent's tool is mode='static' and
    // rejects a per-request index override outright, failing the question
    return o;
  }

  function markTool(assistant, id, state, output) {
    for (var i = 0; i < assistant.parts.length; i++) {
      if (assistant.parts[i].toolCallId === id) {
        assistant.parts[i].state = state;
        if (output !== undefined) assistant.parts[i].output = output;
        return;
      }
    }
  }

  function collectSources(sources, output) {
    var hits = (output && output.hits) || [];
    var seen = {};
    sources.forEach(function (s) { seen[s.url] = 1; });
    hits.forEach(function (hit) {
      if (!hit || !hit.url || seen[hit.url]) return;
      seen[hit.url] = 1;
      sources.push({ url: hit.url, title: hit.title || hit.url });
    });
  }

  // -- error triage -----------------------------------------------------------
  // An error part can arrive AFTER a complete answer has streamed. DocSearch
  // paints its red banner either way, so a correct answer gets a failure notice
  // over it. Only surface an error the reader actually needs to act on.
  function handleStreamError(ex, part, gotText) {
    var msg = extractErrorMessage(part);
    if (gotText) {
      askai.suppressedErrors++;
      if (window.console && console.warn) {
        console.warn('[search] Ask AI: post-answer stream error suppressed:', msg);
      }
      return;
    }
    var box = ex.querySelector('.DocSearch-AskAiScreen-Error');
    if (!box) {
      box = h('div', 'DocSearch-AskAiScreen-Error');
      ex.querySelector('.DocSearch-AskAiScreen-Response').appendChild(box);
    }
    box.innerHTML =
      '<div class="DocSearch-AskAiScreen-Error-Title">Chat error</div>' +
      '<div class="DocSearch-AskAiScreen-Error-Content">' + escapeHtml(msg) + '</div>';
  }

  function extractErrorMessage(part) {
    var raw = part.errorText || part.error || '';
    try {
      var m = /"message"\s*:\s*"((?:[^"\\]|\\.)*)"/.exec(raw);
      if (m) return m[1].replace(/\\"/g, '"');
    } catch (e) {}
    return String(raw) || 'Something went wrong.';
  }

  function renderAskAiFatal(msg) {
    el.askAiBody.innerHTML =
      '<div class="DocSearch-AskAiScreen-Error">' +
      '<div class="DocSearch-AskAiScreen-Error-Title">Chat error</div>' +
      '<div class="DocSearch-AskAiScreen-Error-Content">' + escapeHtml(msg) + '</div></div>';
  }

  // -- panel rendering --------------------------------------------------------
  function appendExchange(question) {
    var li = h('li', 'DocSearch-AskAiScreen-Exchange');
    li.innerHTML =
      '<div class="DocSearch-AskAiScreen-Message--user">' + escapeHtml(question) + '</div>' +
      '<div class="DocSearch-AskAiScreen-Response">' +
      '<div class="DocSearch-AskAiScreen-MessageContent-Tool" hidden></div>' +
      '<div class="DocSearch-Markdown-Content DocSearch-Markdown-Content--streaming"></div>' +
      '</div>';
    li.querySelector('.DocSearch-AskAiScreen-Response').setAttribute('aria-busy', 'true');
    el.askAiBody.appendChild(li);
    // Bring the new QUESTION to the top of the view, then leave the scroll
    // position alone. Pinning to the bottom as text streams (chat convention)
    // is wrong for a docs answer: the reader wants to start at the beginning of
    // the answer, not chase its tail.
    el.askAiScroll.scrollTop = Math.max(0, li.offsetTop - 8);
    return li;
  }

  function paintAnswer(ex, text) {
    var target = ex.querySelector('.DocSearch-Markdown-Content');
    target.innerHTML = renderMarkdown(text);
  }

  function paintTool(ex, queries) {
    var t = ex.querySelector('.DocSearch-AskAiScreen-MessageContent-Tool');
    t.hidden = false;
    t.textContent = queries.length
      ? 'Searched for ' + queries.map(function (q) { return '"' + q + '"'; }).join(' and ')
      : 'Searching...';
  }

  // The product names the site itself uses (hugo.site.toml, params.key.product_name),
  // so the modal names the variant the same way every page does.
  var VARIANT_LABELS = { union: 'Union.ai', flyte: 'Flyte' };

  // null when there is nothing true to say -- an unversioned local build, or a
  // path the version resolver could not place. An empty bar beats a wrong one,
  // and a bar reading "Searching  " would be worse than no bar at all.
  function scopeParts() {
    var variant = CFG.variant;
    var version = CFG.version;
    if (!variant && !version) return null;

    // Keyed on the RESOLVED version, not the served segment. They are the same
    // on every tree the index carries, including the pins; they differ only for
    // a pin outside the indexers' keep-pins window, which falls back to its
    // line -- and there the bar must name the line, because that is what is
    // actually being searched.
    //
    // A version the menu does not describe degrades to naming itself, which is
    // still true, rather than to nothing.
    var v = (version && (CFG.versionLabels || {})[version]) ||
            { line: version || 'all versions', badge: '', num: '' };
    return {
      product: variant ? (VARIANT_LABELS[variant] || variant) : 'all products',
      line: v.line,
      // LATEST has no release number -- it is the branch tip, not a cut -- so
      // its badge is the whole of what there is to say. A pin is the inverse:
      // a number with no badge. Neither is a missing value to paper over.
      badge: v.badge || '',
      release: v.num || ''
    };
  }

  // Segments whose sentence-case form is not just "capitalise the first letter".
  var CRUMB_LABELS = {
    'api-reference': 'API reference',
    'flyte-sdk': 'Flyte SDK',
    'flyte-cli': 'Flyte CLI',
    'user-guide': 'User guide',
    'byoc': 'BYOC',
    'llms': 'LLMs'
  };

  // Where a source lives, derived from its URL. Page titles repeat across the
  // corpus -- "Resources" is both a user-guide page and an API reference page --
  // so a bare list of titles gives the reader two identical-looking links and no
  // way to tell which is which.
  function breadcrumbFor(url) {
    var path;
    try { path = new URL(url, location.href).pathname; } catch (e) { return ''; }
    var parts = path.split('/').filter(Boolean);
    var i = parts.indexOf('docs');
    if (i >= 0) parts = parts.slice(i + 3);   // drop docs/<version>/<variant>
    parts.pop();                              // drop the page slug -- that is the title
    return parts.map(function (p) {
      return CRUMB_LABELS[p] || (p.charAt(0).toUpperCase() + p.slice(1).replace(/-/g, ' '));
    }).join(' › ');
  }

  function paintSources(ex, sources) {
    var wrap = h('div', 'DocSearch-Sources');
    var html = '<div class="DocSearch-Sources-Title">Sources</div><ul class="DocSearch-Sources-List">';
    sources.slice(0, 6).forEach(function (s) {
      var crumb = breadcrumbFor(s.url);
      html += '<li class="DocSearch-Sources-Item">' +
        '<a class="DocSearch-Sources-Link" href="' + escapeHtml(s.url) + '">' +
        escapeHtml(s.title) + '</a>' +
        (crumb ? '<span class="DocSearch-Sources-Crumb">' + escapeHtml(crumb) + '</span>' : '') +
        '</li>';
    });
    wrap.innerHTML = html + '</ul>';
    ex.querySelector('.DocSearch-AskAiScreen-Response').appendChild(wrap);
  }

  function paintStopped(ex) {
    var c = ex.querySelector('.DocSearch-Markdown-Content');
    c.classList.remove('DocSearch-Markdown-Content--streaming');
    var tool = ex.querySelector('.DocSearch-AskAiScreen-MessageContent-Tool');
    if (tool && /^Searching/.test(tool.textContent)) tool.hidden = true;
    if (ex.querySelector('.DocSearch-AskAiScreen-MessageContent-Stopped')) return;
    var note = h('div', 'DocSearch-AskAiScreen-MessageContent-Stopped', 'Stopped.');
    ex.querySelector('.DocSearch-AskAiScreen-Response').appendChild(note);
  }

  function announce(msg) {
    if (el.status) el.status.textContent = msg;
  }

  function setStreamingUI(on) {
    el.stopBtn.hidden = !on;
    el.input.placeholder = on ? 'Answering...' : 'Ask another question...';
    if (!on) {
      var s = el.askAiBody.querySelectorAll('.DocSearch-Markdown-Content--streaming');
      for (var i = 0; i < s.length; i++) s[i].classList.remove('DocSearch-Markdown-Content--streaming');
    }
  }

  function openAskAi(query) {
    setMode('askai');
    if (!askai.conv) newConversation();
    askAiSend(query);
    el.input.value = '';
    el.input.focus();
  }

  // --------------------------------------------------------------------------
  // Wiring
  // --------------------------------------------------------------------------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // Open straight into Ask AI, with an empty conversation ready for a question.
  // This is the header button's entry point -- distinct from openAskAi(), which
  // SENDS a question the reader has already typed into the search box.
  //
  // Degrades to plain search when Ask AI is not configured (no agent id or key,
  // e.g. a local build without the secrets). Better a search box than a button
  // that appears to do nothing.
  function openAsk() {
    open('');
    if (!HAS_ASK_AI) return;
    setMode('askai');
    if (!askai.conv) newConversation();
    el.input.focus();
  }

  // Bind the keyboard shortcut at load, NOT from build(). build() only runs on
  // the first open, so registering there means Cmd/Ctrl-K does nothing until the
  // reader has already opened the modal some other way -- while the trigger
  // button and the footer both advertise the shortcut.
  document.addEventListener('keydown', onKeydown);

  function attachTrigger() {
    var btn = document.getElementById('search-trigger');
    if (btn) btn.addEventListener('click', function () { open(''); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attachTrigger);
  } else {
    attachTrigger();
  }

  // Exposed for debugging and for the regression checks in the PR description.
  // `askai.conv.messages` is the conversation exactly as it will be sent, so a
  // reviewer can confirm the pruning invariant after pressing stop.
  window.__search = {
    open: open,
    // Shift-Cmd-L calls this, and it stays exposed so anything else that wants
    // to open Ask AI directly has one entry point rather than reaching into
    // open() + setMode().
    openAsk: openAsk,
    close: close,
    setMode: setMode,
    el: el,
    cfg: CFG,
    askai: askai,
    pruneForSend: pruneForSend
  };
})();
