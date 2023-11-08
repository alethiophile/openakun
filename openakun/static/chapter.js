/* global $, moment, is_author, chapter_id, csrf_token, Quill,
   fix_quill_html, post_url, DisplayVote, channel_id, Alpine,
   nunjucks, anon_username, make_random_token, ExpandingTextarea,
   io */
$(function () {
  function fix_dates($el) {
    $el.find('.server-date').each(function () {
      var m = moment($(this).data('dateval'));
      var rd = m.format("MMM Do, YYYY h:mm A");
      $(this).html(rd);
    });
  }
  fix_dates($('body'));

  var socket = io();
  window._socketio_socket = socket;
  var msgs_recvd = new Set();
  socket.on('connect', function () {
    console.log('joining room');
    socket.emit('join', { channel: channel_id }, function (res) {
      console.log("join result", res)
      socket.emit('backlog', { channel: channel_id });
    });
  });
  socket.on('chat_msg', function (data) {
    console.log('got chat message', data);
    data.rendered_date = moment(data.date).format("MMM Do, YYYY h:mm A");
    if (!msgs_recvd.has(data.id_token)) {
      add_chat_msg(data);
      msgs_recvd.add(data.id_token);
    }
  });
  socket.on('new_post', function (data) {
    console.log('new post:', data);
    let $html = $(data.html);
    fix_dates($html);
    $('#story-content').append($html);
  });
  socket.on('rendered_vote', function (data) {
    let vote_id = data.vote;
    console.log("rendered_vote", vote_id);
    let el = $(`div.vote[db-id='${vote_id}']`)[0];
    Alpine.morph(el, data.html);
  });
  socket.on('user_vote', function (data) {
    let ev = new CustomEvent("user-vote", { detail: data });
    window.dispatchEvent(ev);
  });
  socket.on('disconnect', function (reason) {
    console.log('disconnected', reason);
    socket.connect();
  });
  socket.on('connect_error', function (error) {
    console.log('connection error');
    setTimeout(function () {
      console.log('trying reconnect');
      socket.connect();
    }, 1000);
  });
  function socket_err(error) {
    console.log('error', error);
  }
  socket.on('error', socket_err);
  socket.on('connect_timeout', socket_err);

  var chat_tmpl = nunjucks.compile($('#render_chatmsg').html());
  /* Adds a message to the chatbox, rendering it. Called
     for every message that comes in over the wire. */
  function add_chat_msg (msg) {
    var $cm = $('#chat-messages');
    var scrollBottomVal = $cm[0].scrollHeight - $cm.height();
    if (msg.is_anon) {
      msg.username = anon_username;
    }
    var $d = $(chat_tmpl.render({ c: msg}));
    $('#chat-messages').append($d);
    var msgHeight = $d.height();
    if (scrollBottomVal - $cm[0].scrollTop < msgHeight) {
      $cm.scrollTop($cm.height());
    }
  }
  /* Send a chat message. Takes a string message, arranges
     for it to be sent. */
  function chat_send (msg) {
    var mo = { channel: channel_id, msg: msg, id_token: make_random_token() };
    socket.emit('chat_msg', mo);
  }
  function chat_trigger_send () {
    var $ct = $('#chat-type');
    var chat_msg = $ct.val();
    if (chat_msg === '') {
      return;
    }
    chat_send(chat_msg);
    $ct.val('');
    $ct.attr('rows', $ct.data('min-rows'));
  }

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
      chat_trigger_send();
      return false;
    }
    return true;
  });
  $('#chat-send').click(function () {
    chat_trigger_send();
  });
  $('#newchapter').change(function () {
    if ($(this).is(':checked')) {
      $('#chaptertitle_inp').css('visibility', 'visible');
    } else {
      $('#chaptertitle_inp').css('visibility', 'hidden');
    }
  });

});

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
      let socket = window._socketio_socket;
      if (this.user_votes[id]) {
        socket.emit('remove_vote', msg);
      }
      else {
        socket.emit('add_vote', msg);
      }
    },

    handle_vote: function (data) {
      if (data.vote != this.vote_id) {
        return;
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
      let socket = window._socketio_socket;
      socket.emit('new_vote_entry', msg);

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
      let socket = window._socketio_socket;
      socket.emit('set_option_killed', msg);
    }
  }));

  Alpine.data('post_editor', function() { return {
    init() {
      this.quill_instance = new Quill(this.$refs.quill, {
        theme: 'snow',
      });
      let t = this;
      // this.quill_instance.clipboard.dangerouslyPasteHTML(0, this.post_text);
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
