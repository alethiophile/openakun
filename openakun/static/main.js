/* global $, htmx */
/* exported ws_html_func */

function ws_html_func(f) {
  if (window._ws_html_funcs === undefined) {
    window._ws_html_funcs = [];
  }
  window._ws_html_funcs.push(f);
}

$(function () {
  htmx.on('htmx:wsOpen', (ev) => {
    window._websock = ev.detail.socketWrapper;
  });

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
});
