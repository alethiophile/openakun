/* global $, moment, is_author, chapter_id, csrf_token, Quill,
   fix_quill_html, post_url, channel_id, Alpine, make_random_token,
   ExpandingTextarea, io */
$(function () {
  function fix_dates($el) {
    $el.find('.server-date').each(function () {
      let $t = $(this);
      var m = moment($t.data('dateval'));
      var rd = m.format("MMM Do, YYYY h:mm A");
      $t.html(rd);
      $t.removeClass('server-date');
    });
  }
  fix_dates($('body'));

  ExpandingTextarea({
    id: 'chat-type',
    pixel_height: 21,
    before_resize: function () {
      let $cm = $('#chat-messages');
      this.$cm = $cm;
      this.scrollFrac = $cm[0].scrollTop / ($cm[0].scrollHeight - $cm.height());
    },
    after_resize: function () {
      let $cm = this.$cm;
      $cm[0].scrollTop = this.scrollFrac * ($cm[0].scrollHeight - $cm.height());
    },
  });

  $('#chat-type').keydown(function (ev) {
    if (ev.which == 13 && !ev.shiftKey) {
      // note: must use requestSubmit() here, not submit(), because
      // submit() doesn't raise the submit event and so doesn't
      // trigger htmx
      $(this).closest('form')[0].requestSubmit();
      return false;
    }
    return true;
  });
  htmx.on('form#chat-sender', "htmx:wsConfigSend", (ev) => {
    if (ev.detail.parameters['type'] == 'chat_message') {
      ev.detail.parameters['id_token'] = make_random_token();
    }
  });

  htmx.on('form#chat-sender', 'htmx:wsAfterSend', (ev) => {
    let $ct = $('#chat-type');
    $ct.val('');
    $ct.attr('rows', $ct.data('min-rows'));
  });

  // here we track whether the chat window is scrolled down to the
  // bottom -- if so, we're in "current mode", and new chat messages
  // should scroll the window to the bottom as well; if not, we're in
  // "backscroll mode", and new chat messages don't affect the scroll
  // position
  let msgs = document.querySelector('#chat-messages').querySelectorAll('.chatmsg');
  let last_chat_message = msgs[msgs.length - 1];
  let scroll_after_new = true;
  let cm = document.querySelector('#chat-messages');
  cm.scrollTop = cm.scrollHeight;
  function scroll_from_bottom(el) {
    return el.scrollHeight - (el.scrollTop + el.clientHeight);
  }
  htmx.on('#chat-messages', 'scrollend', (ev) => {
    scroll_after_new = scroll_from_bottom(ev.target) < last_chat_message.clientHeight;
  });

  // any new content gets server dates fixed
  htmx.on('htmx:load', (ev) => {
    fix_dates($(ev.detail.elt));
  });

  // only chat messages do the scroll stuff
  htmx.on('#chat-messages', 'htmx:load', (ev) => {
    if (scroll_after_new) {
      let cm = document.querySelector('#chat-messages');
      cm.scrollTop = cm.scrollHeight;
    }
    last_chat_message = ev.detail.elt;
  });

  htmx.on('htmx:wsOpen', (ev) => {
    window._websock = ev.detail.socketWrapper;
  });

  htmx.on('htmx:wsBeforeMessage', (ev) => {
    console.log(ev);
    let msg_obj;
    try {
      msg_obj = JSON.parse(ev.detail.message);
    } catch (error) {
      // not JSON, proceed as usual
      return;
    }
    // message was JSON, dispatch as event
    ev.preventDefault();
    let cev = new CustomEvent(msg_obj['type'], { detail: msg_obj });
    window.dispatchEvent(cev);
  });
});

function send_message(socket, type, message) {
  message['type'] = type;
  let data = JSON.stringify(message);
  socket.send(data);
}

document.addEventListener('alpine:init', () => {
  Alpine.data('active_vote', () => ({
    init() {
      // The server sets the .voted-for class on all the votes the
      // user voted for; this saves those to the user_votes key so
      // Alpine can manage them
      let t = this;
      this.vote_id = $(this.$el).attr('db-id');
      $(this.$el).find('.voted-for').each(function () {
        t.user_votes[$(this).attr('db-id')] = true;
      });
      this.admin = is_author == true;
      ExpandingTextarea({ elem: this.$refs.edit, pixel_height: 28 });
    },

    user_votes: {},
    editing: false,
    admin: false,

    toggle_vote: function (id) {
      let msg = { channel: channel_id,
                  vote: this.vote_id,
                  option: id };
      let socket = window._websock;
      if (this.user_votes[id]) {
        send_message(socket, 'remove_vote', msg)
      }
      else {
        send_message(socket, 'add_vote', msg);
      }
    },

    handle_vote: function (data) {
      if (data.vote != this.vote_id) {
        return;
      }
      if (data.clear) {
        this.user_votes = {};
      }
      this.user_votes[data.option] = data.value;
    },

    submit_new: function (ev) {
      if (ev && ev.shiftKey) {
        return;
      }
      if (ev) { ev.preventDefault(); }
      let vt = this.$refs.edit.value;
      let msg = { channel: channel_id,
                  vote: this.vote_id,
                  vote_info: { text: vt } };
      let socket = window._websock;
      send_message(socket, 'new_vote_entry', msg);

      this.$refs.edit.value = '';
      this.editing = false;
    },

    delete_option: function (option_id) {
      let msg = {
        channel: channel_id,
        vote: this.vote_id,
        option: option_id,
        killed: true
      };
      let socket = window._websock;
      send_message(socket, 'set_option_killed', msg);
    },

    close_vote: function () {
      let msg = {
        channel: channel_id,
        vote: this.vote_id,
        active: false,
      };
      let socket = window._websock;
      send_message(socket, 'set_vote_active', msg);
    },
  }));

  Alpine.data('post_editor', function() { return {
    init() {
      this.quill_instance = new Quill(this.$refs.quill, {
        theme: 'snow',
      });
      let t = this;
      this.quill_instance.root.innerHTML = this.post_text;
      this.quill_instance.on('text-change', function () {
        let html = t.quill_instance.root.innerHTML;
        t.post_text = fix_quill_html(html);
      });
    },

    post_type: this.$persist('Text'),
    make_new_chapter: this.$persist(false),
    new_chapter_title: this.$persist(''),
    post_text: this.$persist(''),
    vote_question: this.$persist(''),
    vote_multivote: this.$persist(true),
    vote_writein: this.$persist(true),
    vote_hidden: this.$persist(false),
    vote_options: this.$persist([]),

    add_option() {
      this.vote_options.push({ text: '' });
    },

    delete_option(ind) {
      this.vote_options.splice(ind, 1);
    },

    reset() {
      this.post_type = 'Text';
      this.make_new_chapter = false;
      this.new_chapter_title = '';
      this.post_text = '';
      this.vote_question = '';
      this.vote_multivote = true;
      this.vote_writein = true;
      this.vote_hidden = false;
      this.vote_options = [];
      this.quill_instance.setText('');
    },

    submit() {
      let msg = {
        chapter_id: chapter_id,
        _csrf_token: csrf_token,
        post_type: this.post_type,
        new_chapter: this.make_new_chapter,
        chapter_title: this.new_chapter_title,
        post_text: this.post_text,
        vote_data: {
          question: this.vote_question,
          multivote: this.vote_multivote,
          writein_allowed: this.vote_writein,
          votes_hidden: this.vote_hidden,
          votes: this.vote_options.map((v) => ({ ...v })),
        },
      };
      console.log(msg);
      fetch(post_url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(msg)
      }).then(
        () => { this.reset(); },
        (err) => {
          var errstr = "Error: " + err;
          console.log(errstr);
          alert(errstr);
        });
    },
  };});
});
