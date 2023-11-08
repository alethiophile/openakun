/* global $, Alpine */

/* exported ExpandingTextarea */

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

document.addEventListener('alpine:init', () => {
  // this duplicates the functionality of ExpandingTextarea for
  // elements in Alpine components, but does not support the
  // before_resize or after_resize callbacks

  // these are saved here to make them available to every Alpine
  // context; it might work just to make them closures, I dunno
  // Alpine.store('set_ta_size', {
  function setup($el) {
    if ($el.baseScrollHeight) {
      return;
    }
    let savedValue = $el.value;
    $el.value = '';
    $el.baseScrollHeight = $el.scrollHeight;
    $el.value = savedValue;
    console.log(savedValue, $el.baseScrollHeight);
  }

  function set($el) {
    let minRows = $el.getAttribute('data-min-rows')|0, rows;
    let pixel_height = $el.getAttribute('pixel-height') || 21;
    $el.rows = minRows;
    console.log($el.scrollHeight, $el.baseScrollHeight);
    rows = Math.ceil(($el.scrollHeight - $el.baseScrollHeight) / pixel_height);
    // TODO if necessary: reimplement before_resize and
    // after_resize using Alpine element functions
    // if (rv.before_resize !== undefined) {
    //   rv.before_resize();
    // }
    $el.rows = minRows + rows;
    console.log(minRows, pixel_height, $el.rows);
    // if (rv.after_resize !== undefined) {
    //   rv.after_resize();
    // }
  }
// });

  Alpine.bind('expanding_textarea', () => ({
    // we do this both on init, and on focus.once; the init version
    // handles the case where we've reloaded with saved content from
    // $persist, but it doesn't work properly for reload alone
    ['x-init']() {
      this.$nextTick(() => {
        //this.$store.set_ta_size.setup(this.$el);
        setup(this.$el);
        set(this.$el);
      });
    },

    ['x-on:focus.once']() {
      setup(this.$el);
      set(this.$el);
    },

    ['x-on:input']() {
      set(this.$el);
    },

    // This event is for use when an XTA is shown. (The setup/set
    // functions will not work if the XTA is not displayed, since in
    // that case the scrollHeight is always 0.) Components using XTAs
    // should send it with $dispatch on the action that shows them.
    ['x-on:xta-setup.window']() {
      setup(this.$el);
      set(this.$el);
    }
  }));
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
