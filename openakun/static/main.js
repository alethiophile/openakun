/* global $, htmx */
/* exported ws_html_func */

function ws_html_func(f) {
  if (window._ws_html_funcs === undefined) {
    window._ws_html_funcs = [];
  }
  window._ws_html_funcs.push(f);
}

var all_rtes = new Map();
// This is a wrapper around a rich text editor (currently TinyMCE). It
// serves to keep a master list of active editors for later
// bookkeeping.
class RTEWrapper {
  constructor(target) {
    this.target = target;
    all_rtes.set(target, this);
    _clean_rtes();
  }

  // Initializes the RTE on the target passed at construction,
  // respecting the current state of dark mode. The RTE instance is
  // available later on the wrapper instance at property 'ed'.

  // This can take two config parameters: an `after` function, which
  // is called after initialization with the TinyMCE instance, and an
  // `add_cfg` object, which is merged into the config object passed
  // to tinymce.init(). Both parameters are saved on the wrapper
  // object, and the same values will be passed in on calls to
  // reinit().
  init(after = undefined, add_cfg = {}) {
    let dark_mode = (document.documentElement.dataset.theme === 'forest');
    let config = {
      target: this.target,
      menubar: false,
      statusbar: false,
    };
    if (dark_mode) {
      config.skin = 'oxide-dark';
      config.content_css = 'dark';
    }
    Object.assign(config, add_cfg);
    this._init_add_cfg = add_cfg;
    this._init_after = after;
    tinymce.init(config).then(([ed]) => {
      this.ed = ed;
      if (after !== undefined) {
        after(ed);
      }
      return ed;
    });
  }

  // Reinitializes an RTE while preserving its content. Used to switch
  // from light to dark mode.
  reinit() {
    this.ed.save();
    this.ed.remove();
    this.init(this._init_after, this._init_add_cfg);
  }
}

// Cleans the list of RTEs by deleting any whose target key is not in
// the DOM.
function _clean_rtes() {
  for (let [k, v] of all_rtes) {
    if (!k.isConnected) {
      all_rtes.delete(k);
    }
  }
}

// Go through the list of RTEs and reinit them all.
function _reinit_rtes() {
  for (let [k, v] of all_rtes) {
    v.reinit();
  }
}

$(function () {
  // this is called on every incoming WS message, before HTMX handles
  // it, if and only if that message is not JSON

  // it can cancel the event if desired
  function process_ws_html(ev) {
    let parser = new DOMParser();
    let doc = parser.parseFromString(ev.detail.message, "text/html");
    let node = doc.body.firstChild;
    console.log(node);

    if (window._ws_html_funcs === undefined) {
      return;
    }
    for (let f of window._ws_html_funcs) {
      f(node, ev);
    }
  }

  htmx.on('htmx:wsBeforeMessage', (ev) => {
    //console.log(ev);
    let msg_obj;
    try {
      msg_obj = JSON.parse(ev.detail.message);
    } catch (error) {
      // not JSON, proceed as usual
      process_ws_html(ev);
      return;
    }
    // message was JSON, dispatch as event
    ev.preventDefault();
    let cev = new CustomEvent(msg_obj['type'], { detail: msg_obj });
    window.dispatchEvent(cev);
  });

  htmx.on('set-dark-mode', (ev) => {
    if (ev.detail.value) {
      document.documentElement.dataset.theme = 'forest';
    } else {
      document.documentElement.dataset.theme = 'light';
    }
    _reinit_rtes();
  });
  htmx.on('htmx:afterSettle', () => {
    _clean_rtes();
  });
});
