/* global $ */
// this is written as an object, but there isn't much you can do with the return
// value (other than set before_resize and after_resize dynamically); all the
// work is done in the closure event handlers

// pixel_height is the height in pixels of one row of text, there should be a
// way to find this automatically but for now just trial and error
var ExpandingTextarea = (function (args) {
  let rv = {
    // id: args.id,
    pixel_height: args.pixel_height !== undefined ? args.pixel_height : 21,
    before_resize: args.before_resize,
    after_resize: args.after_resize,
  };
  let el;
  if (args.elem !== undefined) {
    el = args.elem;
    if (!(el instanceof $)) {
      el = $(el);
    }
  } else {
    el = $('#' + args.id);
  }
  /* basic algorithm copied from https://codepen.io/vsync/pen/frudD */
  el.one('focus', function () {
    let savedValue = this.value;
    this.value = '';
    this.baseScrollHeight = this.scrollHeight;
    this.value = savedValue;
  }).on('input', function () {
    let minRows = this.getAttribute('data-min-rows')|0, rows;
    this.rows = minRows;
    rows = Math.ceil((this.scrollHeight - this.baseScrollHeight) / rv.pixel_height);
    if (rv.before_resize !== undefined) {
      rv.before_resize();
    }
    this.rows = minRows + rows;
    if (rv.after_resize !== undefined) {
      rv.after_resize();
    }
  });
  return rv;
});

/* basically compatible with Python's secrets.token_urlsafe */
function make_random_token(bytes=32) {
  var arr = new Uint8Array(bytes);
  window.crypto.getRandomValues(arr);
  var b64 = btoa(String.fromCharCode.apply(null, arr));
  var tok = b64.replace(/\+/g, '_').replace(/\//g, '-').replace(/=/g, '');
  return tok;
}

function fix_quill_html (html_s) {
  let $h = $('<div/>').append(html_s);
  $h.find('span').remove();
  return $h.html();
}
